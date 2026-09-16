"""世界书仓储，异步 MongoDB。

世界书用于存放"按关键词触发"的补充设定：正文或提示词中出现触发词时，
才把这些条目注入 AI 上下文，避免把全书设定一次性塞进 prompt。
- 条目与设定卡相互独立，可通过 linked_card_id 关联到地点/物品/规则卡；
- constant 条目表示常驻注入，不依赖关键词命中；
- 只做候选与上下文素材，不会自动改写任何正式卡片。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from backend.db.base import BaseRepository
from backend.db.errors import DuplicateKeyError, NotFoundError
from backend.db.repositories.id_sequence_repository import id_sequence_repo
from backend.db.utils import get_utc_now, to_object_id

logger = logging.getLogger(__name__)

# 条目分类
WORLDBOOK_CATEGORIES = (
    "worldview",
    "rule",
    "location",
    "item",
    "character",
    "faction",
    "other",
)
# 允许通过更新接口写入的字段
WORLDBOOK_EDITABLE_FIELDS = (
    "title",
    "keys",
    "secondary_keys",
    "content",
    "category",
    "priority",
    "enabled",
    "constant",
    "case_sensitive",
    "linked_card_id",
    "tags",
)
# 关键词匹配时的候选上限，避免超长正文全量扫描
MATCH_CANDIDATE_LIMIT = 200


def serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把文档中的 ObjectId 转成字符串，便于 JSON 序列化。"""
    if not doc:
        return doc
    for key in ("_id", "novel_id"):
        if key in doc and isinstance(doc[key], ObjectId):
            doc[key] = str(doc[key])
    return doc


def normalize_keys(value: Any) -> List[str]:
    """把触发词序列规范成去空去重的字符串列表。"""
    if isinstance(value, str):
        parts = [item.strip() for item in value.replace("，", ",").split(",")]
        return list(dict.fromkeys(item for item in parts if item))
    if not isinstance(value, (list, tuple)):
        return []
    return list(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def _normalize_priority(value: Any) -> int:
    """把优先级规范为 0-100 的整数，越大越先注入。"""
    try:
        priority = int(value)
    except (TypeError, ValueError):
        priority = 50
    return max(0, min(100, priority))


class WorldbookRepository(BaseRepository):
    """世界书条目数据访问层。"""

    def __init__(self) -> None:
        super().__init__("worldbook_entries")

    async def _next_entry_id(self, novel_id, session=None) -> str:
        """分配小说作用域内的世界书条目业务 ID。"""
        allocated = await id_sequence_repo.allocate_many(
            novel_id, "worldbook_entry", "wb", 1, session=session
        )
        return allocated[0]

    async def create_entry(
        self,
        novel_id,
        data: Dict[str, Any],
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """创建一条世界书条目。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            data: 条目业务字段（keys / content / category 等）。
            session: 可选 MongoDB 会话。

        Returns:
            已落库的条目文档。

        Raises:
            DuplicateKeyError: 分类非法或内容为空。
            NotFoundError: 写入后无法回读。
        """
        category = str(data.get("category") or "other").strip()
        if category not in WORLDBOOK_CATEGORIES:
            raise DuplicateKeyError(f"非法的世界书分类: {category}")
        content = str(data.get("content") or "").strip()
        if not content:
            raise DuplicateKeyError("世界书条目内容不能为空")

        entry_id = await self._next_entry_id(novel_id, session=session)
        doc: Dict[str, Any] = {
            "entry_id": entry_id,
            "novel_id": to_object_id(novel_id),
            "version": 1,
            "title": str(data.get("title") or "").strip(),
            "keys": normalize_keys(data.get("keys")),
            "secondary_keys": normalize_keys(data.get("secondary_keys")),
            "content": content,
            "category": category,
            "priority": _normalize_priority(data.get("priority")),
            "enabled": bool(data.get("enabled", True)),
            "constant": bool(data.get("constant", False)),
            "case_sensitive": bool(data.get("case_sensitive", False)),
            "linked_card_id": str(data.get("linked_card_id") or ""),
            "tags": normalize_keys(data.get("tags")),
        }
        inserted = await self.insert_one(doc, session=session)
        # 回读必须按 Mongo _id 查，业务 ID 与 _id 不是同一个值
        found = await self.find_one({"_id": ObjectId(inserted)}, session=session)
        if not found:
            raise NotFoundError(f"世界书条目写入后无法回读: {inserted}")
        return serialize(found)

    async def get_entry(
        self,
        novel_id,
        entry_id: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """校验归属后按业务 ID 读取条目。

        Raises:
            NotFoundError: 条目不存在或不属于该小说。
        """
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "entry_id": entry_id},
            session=session,
        )
        if not doc:
            raise NotFoundError(f"世界书条目不存在: {entry_id}")
        return serialize(doc)

    async def list_entries(
        self,
        novel_id,
        *,
        enabled_only: bool = False,
        category: Optional[str] = None,
        limit: int = 200,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """按小说列出条目，按优先级倒序、创建时间正序。"""
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if enabled_only:
            flt["enabled"] = True
        if category:
            flt["category"] = category
        docs = await self.find_many(
            flt,
            limit=max(1, min(500, limit)),
            sort=[("priority", -1), ("entry_id", 1)],
            session=session,
        )
        return [serialize(doc) for doc in docs]

    async def update_entry(
        self,
        novel_id,
        entry_id: str,
        update_data: Dict[str, Any],
        *,
        expected_version: int,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """按乐观锁更新条目可编辑字段。

        Raises:
            DuplicateKeyError: 无字段、分类非法、内容为空或版本冲突。
            NotFoundError: 条目不存在。
        """
        filtered = {
            key: value for key, value in update_data.items() if key in WORLDBOOK_EDITABLE_FIELDS
        }
        if not filtered:
            raise DuplicateKeyError("世界书条目更新至少需要提供一个可编辑字段")
        if "category" in filtered:
            category = str(filtered["category"] or "").strip()
            if category not in WORLDBOOK_CATEGORIES:
                raise DuplicateKeyError(f"非法的世界书分类: {category}")
            filtered["category"] = category
        if "content" in filtered:
            content = str(filtered["content"] or "").strip()
            if not content:
                raise DuplicateKeyError("世界书条目内容不能为空")
            filtered["content"] = content
        for key in ("keys", "secondary_keys", "tags"):
            if key in filtered:
                filtered[key] = normalize_keys(filtered[key])
        if "priority" in filtered:
            filtered["priority"] = _normalize_priority(filtered["priority"])
        for key in ("enabled", "constant", "case_sensitive"):
            if key in filtered:
                filtered[key] = bool(filtered[key])
        if "linked_card_id" in filtered:
            filtered["linked_card_id"] = str(filtered["linked_card_id"] or "")

        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "entry_id": entry_id,
                "is_deleted": False,
                "version": expected_version,
            },
            {
                "$set": {**filtered, "updated_at": get_utc_now()},
                "$inc": {"version": 1},
            },
            session=session,
        )
        if result.matched_count == 0:
            latest = await self.collection.find_one(
                {"novel_id": to_object_id(novel_id), "entry_id": entry_id},
                session=session,
            )
            if latest is None:
                raise NotFoundError(f"世界书条目不存在: {entry_id}")
            raise DuplicateKeyError(
                f"世界书条目已被修改（服务器版本 {latest.get('version')}，"
                f"当前基于版本 {expected_version}），请刷新后重试"
            )
        return await self.get_entry(novel_id, entry_id, session=session)

    async def delete_entry(
        self,
        novel_id,
        entry_id: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """软删除条目，可在回收站恢复。"""
        removed = await self.soft_delete_one(
            {"novel_id": to_object_id(novel_id), "entry_id": entry_id},
            session=session,
        )
        if not removed:
            raise NotFoundError(f"世界书条目不存在: {entry_id}")
        return removed

    async def restore_entry(
        self,
        novel_id,
        entry_id: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """从回收站恢复条目。"""
        restored = await self.restore_one(
            {"novel_id": to_object_id(novel_id), "entry_id": entry_id},
            session=session,
        )
        if not restored:
            raise NotFoundError(f"回收站中不存在该条目: {entry_id}")
        return await self.get_entry(novel_id, entry_id, session=session)

    async def hard_delete_entry(
        self,
        novel_id,
        entry_id: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """物理删除条目（仅回收站内记录），不可恢复。"""
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "entry_id": entry_id},
            include_deleted=True,
            session=session,
        )
        if not doc:
            raise NotFoundError(f"世界书条目不存在: {entry_id}")
        if not doc.get("is_deleted"):
            raise DuplicateKeyError("请先移入回收站后再彻底删除")
        return await self.hard_delete_one({"_id": doc["_id"]}, session=session)

    async def match_entries(
        self,
        novel_id,
        text: str,
        *,
        limit: int = 20,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """按关键词匹配应当注入上下文的世界书条目。

        匹配规则：
        - constant 条目常驻命中；
        - 其余条目在 text 中出现任一主触发词时命中；
        - case_sensitive=false 时忽略大小写；secondary_keys 仅作补充命中来源。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            text: 待匹配文本（例如当前章节正文或提示词）。
            limit: 返回条目的最大数量。
            session: 可选 MongoDB 会话。

        Returns:
            按优先级倒序排列的命中条目；无命中时返回空列表。
        """
        entries = await self.list_entries(
            novel_id, enabled_only=True, limit=MATCH_CANDIDATE_LIMIT, session=session
        )
        haystack = str(text or "")
        lowered = haystack.lower()
        matched: List[Dict[str, Any]] = []
        for entry in entries:
            if entry.get("constant"):
                matched.append(entry)
                continue
            case_sensitive = bool(entry.get("case_sensitive"))
            needle = haystack if case_sensitive else lowered
            tokens = list(entry.get("keys") or []) + list(entry.get("secondary_keys") or [])
            hit = False
            for token in tokens:
                candidate = str(token)
                if not candidate:
                    continue
                probe = candidate if case_sensitive else candidate.lower()
                if probe and probe in needle:
                    hit = True
                    break
            if hit:
                matched.append(entry)
        matched.sort(
            key=lambda item: (-_normalize_priority(item.get("priority")), str(item.get("entry_id") or ""))
        )
        return matched[: max(1, min(100, limit))]


worldbook_repo = WorldbookRepository()
