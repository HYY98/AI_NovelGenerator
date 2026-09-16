"""AI 生成记录仓储，异步 MongoDB。

每次 AI 生成都会写入一条记录，保存输入上下文快照、候选结果与采纳状态。
候选结果只用于预览，用户点击「采用」后由业务层调用正式保存接口，
本仓储不负责把候选写入正式章节或卡片。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId

from backend.db.base import BaseRepository
from backend.db.errors import DuplicateKeyError, NotFoundError
from backend.db.repositories.id_sequence_repository import id_sequence_repo
from backend.db.utils import as_plain_dict, canonicalize_extras, get_utc_now, to_object_id

logger = logging.getLogger(__name__)

# 生成类型（与技术指引 19.3 保持一致）
GENERATION_KINDS = (
    "location_card",
    "item_card",
    "rule_card",
    "extract_cards",
    "complete_card",
    "check_card_conflicts",
    "chapter_plan",
    "chapter_draft",
    "chapter_continue",
    "chapter_rewrite",
    "chapter_expand",
    "chapter_compress",
    "consistency_review",
    "state_change_proposal",
    # 增量新增：设定卡改写、正文设定分析、正文修改建议
    "rewrite_setting_card",
    "analyze_chapter_settings",
    "generate_text_revision",
    # 增量新增：创作蓝图与大纲、分章生成
    "blueprint_prepare",
    "blueprint_complete",
    "generate_power_system",
    "generate_outline",
    "confirm_outline",
    "generate_chapters",
)

# 生成记录状态（新增 draft/adopted/discarded，保留 pending/completed/failed 兼容老数据）
GENERATION_STATUS = ("draft", "pending", "completed", "failed", "adopted", "discarded")

# 生成记录来源类型
GENERATION_SOURCE_TYPES = ("ai", "user")

# 统一候选行为类型：与前端 generationKind 对齐，供前端直接用作列表筛选
GENERATION_ACTION_TYPES = (
    "worldview_preset_generate",   # 世界观预设生成
    "character_batch_generate",    # 角色批量生成
    "character_single_generate",   # 单个角色生成
    "setting_card_generate",       # 设定卡生成
    "setting_card_complete",       # 设定卡补全
    "setting_card_extract",        # 设定卡抽取
    "setting_card_conflict_check", # 设定卡冲突检查
    "chapter_plan_generate",       # 章节计划生成
    "chapter_draft_generate",      # 章节草稿生成
    "chapter_expand",              # 章节扩写
    "chapter_compress",            # 章节缩写
    "chapter_rewrite",             # 章节改写
    "chapter_continue",            # 章节续写
    "consistency_review",          # 一致性审校
    "state_change_proposal",       # 状态变化建议
    # 增量新增：卡片改写、正文分析、正文修改建议
    "setting_card_rewrite",        # 卡片字段改写
    "chapter_setting_analyze",     # 本章设定分析
    "text_revision_generate",      # 正文修改建议生成
    # 增量新增：创作蓝图、大纲与分章生成
    "blueprint_prepare",           # 创作蓝图整理
    "blueprint_complete",          # 创作蓝图补全
    "power_system_generate",       # 战力体系生成
    "outline_generate",            # 卷章大纲生成
    "outline_confirm",             # 卷章大纲确认
    "chapter_batch_generate",      # 按大纲批量生成章节正文
)

# canonical 动作：生成记录的检索主轴，前端可用 action_type 直接筛列表
GENERATION_CANONICAL_ACTIONS = ("generate", "complete", "extract", "review", "check")

# 记录类型：由 canonical 派生，与 action_type 一一对应
GENERATION_RECORD_TYPES = (
    "generation",
    "completion",
    "extraction",
    "review_result",
    "conflict_check",
)

# action_type -> (canonical, record_type) 映射
_ACTION_TYPE_MAP: Dict[str, tuple[str, str]] = {
    "worldview_preset_generate": ("generate", "generation"),
    "character_batch_generate": ("generate", "generation"),
    "character_single_generate": ("generate", "generation"),
    "setting_card_generate": ("generate", "generation"),
    "chapter_plan_generate": ("generate", "generation"),
    "chapter_draft_generate": ("generate", "generation"),
    "chapter_expand": ("generate", "generation"),
    "chapter_compress": ("generate", "generation"),
    "chapter_rewrite": ("generate", "generation"),
    "chapter_continue": ("generate", "generation"),
    "setting_card_complete": ("complete", "completion"),
    "setting_card_extract": ("extract", "extraction"),
    "consistency_review": ("review", "review_result"),
    "state_change_proposal": ("review", "review_result"),
    "setting_card_conflict_check": ("check", "conflict_check"),
    # 增量新增：新增动作类型的 canonical 与 record_type 派生
    "setting_card_rewrite": ("complete", "completion"),
    "chapter_setting_analyze": ("review", "review_result"),
    "text_revision_generate": ("generate", "generation"),
    "blueprint_prepare": ("generate", "generation"),
    "blueprint_complete": ("complete", "completion"),
    "power_system_generate": ("generate", "generation"),
    "outline_generate": ("generate", "generation"),
    "outline_confirm": ("complete", "completion"),
    "chapter_batch_generate": ("generate", "generation"),
}

# 作用范围字段取值优先级：越靠前优先级越高
_SCOPE_CHAPTER_FIELDS = ("chapter_id", "chapterId", "chapter")
_SCOPE_CARD_FIELDS = ("card_id", "cardId", "card", "entity_id", "entityId")
_SCOPE_CHARACTER_FIELDS = ("character_id", "characterId", "character")
_SCOPE_CANDIDATE_FIELDS = ("candidate_id", "candidateId", "candidate")
_SCOPE_EVENT_FIELDS = ("event_id", "eventId", "event")


def serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把文档中的 ObjectId 转成字符串，便于 JSON 序列化。"""
    if not doc:
        return doc
    for key in ("_id", "novel_id"):
        if key in doc and isinstance(doc[key], ObjectId):
            doc[key] = str(doc[key])
    return doc


def _derive_record_type(action_type: str) -> tuple[str, str]:
    """由 action_type 派生 canonical 与 record_type。

    Args:
        action_type: 统一候选行为类型。

    Returns:
        (canonical, record_type)；未登记的 action_type 默认按 generate/generation 处理。
    """
    return _ACTION_TYPE_MAP.get(action_type, ("generate", "generation"))


def _pick_first_str(payload: Dict[str, Any], fields: tuple[str, ...]) -> str:
    """按字段优先级从业务负载中取第一个非空字符串值。"""
    for field in fields:
        value = payload.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _derive_scope_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    """按优先级从业务负载派生统一作用范围 scope。

    优先级：chapter > card > character > candidate > event。
    所有取值均为业务 ID 字符串，未命中时留空字符串。

    Args:
        payload: 调用方传入的业务负载字典。

    Returns:
        scope 字典，固定包含全部五个键。
    """
    return {
        "chapter_id": _pick_first_str(payload, _SCOPE_CHAPTER_FIELDS),
        "card_id": _pick_first_str(payload, _SCOPE_CARD_FIELDS),
        "character_id": _pick_first_str(payload, _SCOPE_CHARACTER_FIELDS),
        "candidate_id": _pick_first_str(payload, _SCOPE_CANDIDATE_FIELDS),
        "event_id": _pick_first_str(payload, _SCOPE_EVENT_FIELDS),
    }


def _build_status_history_entry(
    status: str,
    *,
    actor: str = "system",
    note: str = "",
) -> Dict[str, Any]:
    """构造一条状态变更历史条目。

    Args:
        status: 变更后的状态。
        actor: 变更发起者，ai / user / system。
        note: 变更说明。

    Returns:
        状态历史条目字典。
    """
    return {
        "status": status,
        "changed_at": get_utc_now(),
        "actor": actor or "system",
        "note": str(note or ""),
    }


# 旧 kind -> 新 action_type 映射，用于让历史写入链路同样产出模块3 字段
_LEGACY_KIND_TO_ACTION_TYPE: Dict[str, str] = {
    "location_card": "setting_card_generate",
    "item_card": "setting_card_generate",
    "rule_card": "setting_card_generate",
    "extract_cards": "setting_card_extract",
    "complete_card": "setting_card_complete",
    "check_card_conflicts": "setting_card_conflict_check",
    "chapter_plan": "chapter_plan_generate",
    "chapter_draft": "chapter_draft_generate",
    "chapter_continue": "chapter_continue",
    "chapter_rewrite": "chapter_rewrite",
    "chapter_expand": "chapter_expand",
    "chapter_compress": "chapter_compress",
    "consistency_review": "consistency_review",
    "state_change_proposal": "state_change_proposal",
    # 增量新增：新增 kind 与统一 action_type 的映射
    "rewrite_setting_card": "setting_card_rewrite",
    "analyze_chapter_settings": "chapter_setting_analyze",
    "generate_text_revision": "text_revision_generate",
    "blueprint_prepare": "blueprint_prepare",
    "blueprint_complete": "blueprint_complete",
    "generate_power_system": "power_system_generate",
    "generate_outline": "outline_generate",
    "confirm_outline": "outline_confirm",
    "generate_chapters": "chapter_batch_generate",
}


def _build_compatibility_fields(data: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """为旧版 create_record 补齐模块3 的统一字段。

    旧链路继续保留 kind 语义，同时写入 action_type / record_type / scope 等字段，
    使新旧记录可以被同一套 find_by_novel / find_by_chapter 查询检索。
    该路径不重置同 scope 旧记录的 is_latest，避免改变既有写入行为。

    Args:
        data: 旧版记录字段。
        kind: 旧版生成类型。

    Returns:
        需要合并进文档的模块3 字段字典。
    """
    action_type = str(data.get("action_type") or "").strip()
    if not action_type:
        action_type = _LEGACY_KIND_TO_ACTION_TYPE.get(kind, "setting_card_generate")
    canonical, record_type = _derive_record_type(action_type)
    scope = _derive_scope_payload(
        {
            "chapter_id": data.get("chapter_id") or "",
            "card_id": data.get("card_id") or "",
        }
    )
    return {
        "action_type": action_type,
        "canonical": canonical,
        "record_type": record_type,
        "scope": scope,
        "payload": {
            "input_snapshot": data.get("input_snapshot", {}),
            "result": data.get("result", {}),
        },
        "source": str(data.get("source") or "ai"),
        "extras": canonicalize_extras(data.get("extras") or {}),
        "is_latest": True,
        "status_history": [
            _build_status_history_entry(
                str(data.get("status") or "completed"),
                actor=str(data.get("source") or "ai"),
            )
        ],
    }


class GenerationRecordRepository(BaseRepository):
    """生成记录数据访问层。"""

    def __init__(self) -> None:
        super().__init__("generation_records")

    async def _next_generation_id(self, novel_id, session=None) -> str:
        allocated = await id_sequence_repo.allocate_many(
            novel_id, "generation_record", "gen", 1, session=session
        )
        return allocated[0]

    # 对外暴露：部分上层流程需要预分配 generation_id 后再异步回写结果
    generate_generation_record_id = _next_generation_id

    async def _derive_is_latest(
        self,
        novel_id,
        scope: Dict[str, str],
        action_type: str,
        session=None,
    ) -> bool:
        """判断新记录是否应作为该 scope + action_type 下的最新记录。

        写入前把同 scope 同 action_type 的旧记录置为 False，
        保证「最新一条」语义唯一。查找 scope 时只比对非空维度，
        避免空字符串把不同层级的记录错误归为同一组。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            scope: 统一作用范围。
            action_type: 统一候选行为类型。
            session: 可选 MongoDB 会话。

        Returns:
            当前新记录是否为最新，恒为 True。
        """
        flt: Dict[str, Any] = {
            "novel_id": to_object_id(novel_id),
            "action_type": action_type,
            "is_latest": True,
        }
        for key, value in scope.items():
            if value:
                flt[f"scope.{key}"] = value
            else:
                flt[f"scope.{key}"] = ""
        await self.collection.update_many(
            flt,
            {"$set": {"is_latest": False}},
            session=session,
        )
        return True

    async def create_generation_record(
        self,
        novel_id,
        *,
        action_type: str,
        payload: Dict[str, Any] | None = None,
        status: str = "draft",
        source: str = "ai",
        extras: Dict[str, Any] | None = None,
        session=None,
    ) -> Dict[str, Any]:
        """新建一条统一生成记录（模块3 规范字段）。

        服务端负责分配 generation_id、派生 record_type、固化 scope 与 is_latest，
        调用方不得自行传入这些受控字段。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            action_type: 统一候选行为类型，必须属于 GENERATION_ACTION_TYPES。
            payload: 业务负载，包含输入快照与候选结果。
            status: 初始状态，默认 draft。
            source: 来源，ai / user。
            extras: 扩展字段，会被序列化后原样保存。
            session: 可选 MongoDB 会话。

        Returns:
            已落库的生成记录文档。

        Raises:
            DuplicateKeyError: action_type、status 或 source 非法。
            NotFoundError: 写入后无法回读。
        """
        if action_type not in GENERATION_ACTION_TYPES:
            raise DuplicateKeyError(f"不支持的生成行为类型: {action_type}")
        if status not in GENERATION_STATUS:
            raise DuplicateKeyError(f"非法的生成状态: {status}")
        source = str(source or "ai")
        if source not in GENERATION_SOURCE_TYPES:
            raise DuplicateKeyError(f"非法的生成来源: {source}")

        payload_dict = as_plain_dict(payload)
        canonical, record_type = _derive_record_type(action_type)
        scope = _derive_scope_payload(payload_dict)
        generation_id = await self._next_generation_id(novel_id, session=session)
        now = get_utc_now()

        doc: Dict[str, Any] = {
            "generation_id": generation_id,
            "novel_id": to_object_id(novel_id),
            "record_type": record_type,
            "action_type": action_type,
            "canonical": canonical,
            "scope": scope,
            "payload": payload_dict,
            "status": status,
            "source": source,
            "extras": canonicalize_extras(extras or {}),
            "is_latest": True,
            "status_history": [_build_status_history_entry(status, actor=source)],
            "created_at": now,
            "updated_at": now,
        }
        # 必须先让旧记录失效再插入，否则会把本次新写入的记录也一起置为非最新
        await self._derive_is_latest(novel_id, scope, action_type, session=session)
        inserted = await self.insert_one(doc, session=session)
        # 回读必须按 Mongo _id 查，业务 ID 与 _id 不是同一个值
        found = await self.find_one({"_id": ObjectId(inserted)}, session=session)
        if not found:
            raise NotFoundError(f"生成记录写入后无法回读: {inserted}")
        return serialize(found)

    async def find_by_novel(
        self,
        novel_id,
        *,
        action_types: Optional[List[str]] = None,
        limit: int = 50,
        session=None,
    ) -> List[Dict[str, Any]]:
        """按小说查询生成记录，支持按 action_type 批量过滤。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            action_types: 需要过滤的行为类型列表；为空时不过滤。
            limit: 返回条数上限，会被裁剪到 [1, 200]。
            session: 可选 MongoDB 会话。

        Returns:
            按创建时间倒序的生成记录列表。
        """
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if action_types:
            flt["action_type"] = {"$in": list(action_types)}
        docs = await self.find_many(
            flt,
            limit=max(1, min(200, limit)),
            sort=[("created_at", -1)],
            session=session,
        )
        return [serialize(d) for d in docs]

    async def find_by_chapter(
        self,
        novel_id,
        chapter_id: str,
        *,
        action_types: Optional[List[str]] = None,
        limit: int = 50,
        session=None,
    ) -> List[Dict[str, Any]]:
        """按小说 + 章节查询生成记录，支持按 action_type 批量过滤。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            chapter_id: 章节业务 ID。
            action_types: 需要过滤的行为类型列表；为空时不过滤。
            limit: 返回条数上限，会被裁剪到 [1, 200]。
            session: 可选 MongoDB 会话。

        Returns:
            按创建时间倒序的生成记录列表。
        """
        flt: Dict[str, Any] = {
            "novel_id": to_object_id(novel_id),
            "scope.chapter_id": chapter_id,
        }
        if action_types:
            flt["action_type"] = {"$in": list(action_types)}
        docs = await self.find_many(
            flt,
            limit=max(1, min(200, limit)),
            sort=[("created_at", -1)],
            session=session,
        )
        return [serialize(d) for d in docs]

    async def update_status(
        self,
        novel_id,
        generation_id: str,
        status: str,
        *,
        patch: Optional[Dict[str, Any]] = None,
        actor: str = "user",
        note: str = "",
        session=None,
    ) -> bool:
        """更新生成记录状态，并追加一条状态变更历史。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            generation_id: 生成记录业务 ID。
            status: 目标状态，必须属于 GENERATION_STATUS。
            patch: 需要同时合并进 payload 的字段。
            actor: 变更发起者。
            note: 变更说明。
            session: 可选 MongoDB 会话。

        Returns:
            实际更新成功时返回 True。

        Raises:
            DuplicateKeyError: 状态非法。
        """
        if status not in GENERATION_STATUS:
            raise DuplicateKeyError(f"非法的生成状态: {status}")
        fields: Dict[str, Any] = {
            "status": status,
            "updated_at": get_utc_now(),
        }
        patch_dict = as_plain_dict(patch)
        if patch_dict:
            merged = {f"payload.{key}": value for key, value in patch_dict.items()}
            fields.update(merged)
        # 基类 update_one 会把入参整体包进 $set，无法表达 $push，这里直接使用集合句柄
        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "generation_id": generation_id,
                "is_deleted": False,
            },
            {
                "$set": fields,
                "$push": {
                    "status_history": _build_status_history_entry(
                        status, actor=actor, note=note
                    )
                },
            },
            session=session,
        )
        return result.modified_count > 0

    async def find_by_request_id(
        self,
        novel_id,
        kind: str,
        request_id: str,
        session=None,
    ) -> Optional[Dict[str, Any]]:
        """按小说 + 生成类型 + 客户端幂等 ID 查找已存在的生成记录。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            kind: 生成类型。
            request_id: 客户端生成的幂等 ID。
            session: 可选 MongoDB 会话。

        Returns:
            命中的生成记录；不存在时返回 None。
        """
        if not request_id:
            return None
        doc = await self.find_one(
            {
                "novel_id": to_object_id(novel_id),
                "kind": kind,
                "request_id": request_id,
            },
            session=session,
        )
        return serialize(doc)

    async def create_record(
        self,
        novel_id,
        data: Dict[str, Any],
        session=None,
    ) -> Dict[str, Any]:
        """新建一条生成记录。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            data: 记录字段，至少包含 kind。
            session: 可选 MongoDB 会话。

        Returns:
            已落库的生成记录。
        """
        kind = str(data.get("kind", "")).strip()
        if kind not in GENERATION_KINDS:
            raise DuplicateKeyError(f"不支持的生成类型: {kind}")
        generation_id = await self._next_generation_id(novel_id, session=session)
        doc = {
            "generation_id": generation_id,
            "novel_id": to_object_id(novel_id),
            "chapter_id": str(data.get("chapter_id") or ""),
            "card_id": str(data.get("card_id") or ""),
            "kind": kind,
            "status": data.get("status", "completed"),
            "input_snapshot": data.get("input_snapshot", {}),
            "result": data.get("result", {}),
            "accepted": bool(data.get("accepted", False)),
            "provider": str(data.get("provider") or ""),
            "model": str(data.get("model") or ""),
            "error": str(data.get("error") or ""),
            # 模块3.6：生成时的版本快照与采纳闭环字段
            "blueprint_version": data.get("blueprint_version"),
            "chapter_version": data.get("chapter_version"),
            "outline_version": data.get("outline_version"),
            "target_card_id": str(data.get("target_card_id") or "") or None,
            "accepted_action": data.get("accepted_action"),
            "accepted_fields": data.get("accepted_fields"),
            "accepted_at": data.get("accepted_at"),
            "text_revision_id": str(data.get("text_revision_id") or "") or None,
        }
        # 兼容模块3：旧链路同样补齐统一字段，保证新旧记录可被同一套查询检索
        doc.update(_build_compatibility_fields(data, kind))
        # request_id 为空时不落该字段，让幂等唯一索引只覆盖真正带幂等键的记录
        request_id = str(data.get("request_id") or "").strip()
        if request_id:
            doc["request_id"] = request_id
        inserted = await self.insert_one(doc, session=session)
        # 回读必须按 Mongo _id 查，业务 ID 与 _id 不是同一个值
        found = await self.find_one({"_id": ObjectId(inserted)}, session=session)
        if not found:
            raise NotFoundError(f"生成记录写入后无法回读: {inserted}")
        return serialize(found)

    async def get_record(
        self,
        generation_id: str,
        include_deleted: bool = False,
        session=None,
    ) -> Dict[str, Any]:
        """按业务 ID 读取生成记录。"""
        doc = await self.find_one(
            {"generation_id": generation_id},
            include_deleted=include_deleted,
            session=session,
        )
        if not doc:
            raise NotFoundError(f"生成记录不存在: {generation_id}")
        return serialize(doc)

    async def list_records(
        self,
        novel_id,
        chapter_id: Optional[str] = None,
        card_id: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 50,
        include_deleted: bool = False,
        session=None,
    ) -> List[Dict[str, Any]]:
        """列出小说下的生成记录，按创建时间倒序。"""
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if chapter_id:
            flt["chapter_id"] = chapter_id
        if card_id:
            flt["card_id"] = card_id
        if kind:
            flt["kind"] = kind
        docs = await self.find_many(
            flt,
            include_deleted=include_deleted,
            limit=max(1, min(200, limit)),
            sort=[("created_at", -1)],
            session=session,
        )
        return [serialize(d) for d in docs]

    async def get_owned_record(
        self,
        novel_id,
        generation_id: str,
        *,
        session=None,
    ) -> Dict[str, Any]:
        """校验生成记录归属后再读取，避免跨小说访问。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            generation_id: 生成记录业务 ID。
            session: 可选 MongoDB 会话。

        Returns:
            命中的生成记录。

        Raises:
            NotFoundError: 记录不存在或不属于该小说。
        """
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "generation_id": generation_id},
            session=session,
        )
        if not doc:
            raise NotFoundError(f"生成记录不存在: {generation_id}")
        return serialize(doc)

    async def mark_accepted_action(
        self,
        novel_id,
        generation_id: str,
        action: str,
        *,
        accepted_fields: Optional[Dict[str, Any]] = None,
        text_revision_id: Optional[str] = None,
        session=None,
    ) -> bool:
        """原子标记生成记录被采纳，并防止同一候选重复采纳。

        使用 `accepted: False` 作为更新条件，保证并发下只有一个请求能成功，
        正式写入失败时不应调用本方法，避免出现"标记已采纳但未写入"的状态。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            generation_id: 生成记录业务 ID。
            action: create / merge / rewrite / reject。
            accepted_fields: 实际采纳的字段差异。
            text_revision_id: 关联的正文修改建议业务 ID。
            session: 可选 MongoDB 会话。

        Returns:
            首次采纳成功返回 True；已被采纳或记录不存在返回 False。

        Raises:
            DuplicateKeyError: 采纳动作非法。
        """
        valid_actions = ("create", "merge", "rewrite", "reject")
        if action not in valid_actions:
            raise DuplicateKeyError(f"非法的采纳动作: {action}")
        fields: Dict[str, Any] = {
            "accepted": True,
            "accepted_action": action,
            "accepted_fields": accepted_fields or {},
            "accepted_at": get_utc_now(),
            "status": "adopted" if action != "reject" else "discarded",
        }
        if text_revision_id:
            fields["text_revision_id"] = text_revision_id
        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "generation_id": generation_id,
                "is_deleted": False,
                "accepted": {"$ne": True},
            },
            {
                "$set": {**fields, "updated_at": get_utc_now()},
                "$push": {
                    "status_history": _build_status_history_entry(
                        fields["status"], actor="user", note=f"采纳动作: {action}"
                    )
                },
            },
            session=session,
        )
        return result.modified_count > 0

    async def mark_accepted(self, generation_id: str, session=None) -> bool:
        """标记候选结果已被用户采用。"""
        return await self.update_one(
            {"generation_id": generation_id},
            {"accepted": True, "accepted_at": get_utc_now()},
            session=session,
        )

    async def update_result(
        self,
        generation_id: str,
        result: Dict[str, Any],
        status: str = "completed",
        session=None,
    ) -> bool:
        """回写候选结果或失败状态。

        Args:
            generation_id: 生成记录业务 ID。
            result: 候选结果字典。
            status: pending / completed / failed。
            session: 可选 MongoDB 会话。

        Returns:
            实际更新成功时返回 True。
        """
        if status not in GENERATION_STATUS:
            raise DuplicateKeyError(f"非法的生成状态: {status}")
        return await self.update_one(
            {"generation_id": generation_id},
            {"result": result, "status": status},
            session=session,
        )


generation_record_repo = GenerationRecordRepository()
