"""创作蓝图 AI 服务：整理、补全、战力体系与卷章大纲生成。

统一约定：
- 蓝图整理/补全/大纲生成都只产出候选，写入 generation_records；
- 确认前的校验（必填字段、blocking 冲突、实体 ID 归属、状态字段白名单）一律在
  本服务完成，不让非法候选进入正式流程；
- 战力体系只产出候选，正式写入由 PowerSystemService 完成。
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from uuid import uuid4

from bson import ObjectId

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.db.repositories.chapter_repository import ChapterRepository
from backend.db.repositories.character_repository import character_repo
from backend.db.repositories.generation_record_repository import generation_record_repo
from backend.db.repositories.novel_blueprint_repository import novel_blueprint_repo
from backend.db.repositories.novel_repository import novel_repo
from backend.db.repositories.setting_card_repository import SettingCardRepository
from backend.db.repositories.volume_repository import volume_repo
from backend.llm.prompts.prompt_selector import get_prompt_section
from backend.llm.schemas.novel_blueprint_pydantic import (
    BlueprintPrepareResultSchema,
    NovelOutlineResultSchema,
)
from backend.llm.schemas.power_system_pydantic import PowerSystemGenerateResultSchema
from backend.services.llm.generation_support import (
    resolve_runtime,
    reuse_record,
    save_record,
)
from backend.services.llm.workflow_service import (
    build_generation_kwargs,
    generate_structured_result,
)

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "novel_blueprint_ai"

# 蓝图生成记录的 kind（与技术指引 19.3 保持一致的命名风格）
BLUEPRINT_KINDS = {
    "prepare": "blueprint_prepare",
    "complete": "blueprint_complete",
    "power_system": "generate_power_system",
    "outline": "generate_outline",
    "confirm_outline": "confirm_outline",
    "chapters": "generate_chapters",
}

# 蓝图确认前的必填字段
BLUEPRINT_REQUIRED_FIELDS = ("plot_summary", "worldview")

# 大纲中允许出现的实体字段 -> 校验来源
OUTLINE_ENTITY_FIELDS = (
    "character_ids",
    "faction_ids",
    "setting_card_ids",
)


def _dump_json(value: Any) -> str:
    """把任意结构转成紧凑 JSON 字符串，供提示词引用。"""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


def _record_result(record: Dict[str, Any]) -> Dict[str, Any]:
    """读取生成记录中的业务结果，兼容新旧两种存储路径。"""
    if isinstance(record.get("result"), dict) and record.get("result"):
        return dict(record["result"])
    payload = record.get("payload") or {}
    result = payload.get("result") or {}
    return dict(result) if isinstance(result, dict) else {}


def _request_id(request: Any) -> str:
    """读取客户端幂等 ID，保证相同 request_id 重试可复用候选。"""
    return str(getattr(request, "request_id", "") or "").strip()


def _candidate_response(record: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    """把生成记录封装成接口文档约定的候选响应。

    Args:
        record: save_record / reuse_record 返回的记录响应。
        **extra: 各接口额外的业务字段。

    Returns:
        统一包含 generation_id、warnings、conflicts 的候选响应。
    """
    return {
        "generation_id": str(record.get("candidate_id") or ""),
        "warnings": list(record.get("warnings") or []),
        "conflicts": list(record.get("conflicts") or []),
        "provider": str(record.get("provider") or ""),
        "model": str(record.get("model") or ""),
        "context_snapshot": record.get("context_snapshot") or {},
        "reused": bool(record.get("reused")),
        **extra,
    }


def _empty_chapter() -> Dict[str, Any]:
    """返回蓝图场景使用的空章节，让上下文组装器复用同一套逻辑。"""
    return {
        "chapter_id": "",
        "number": 0,
        "title": "",
        "content": "",
        "summary": "",
        "blueprint_snapshot": {},
        "unresolved_threads": [],
        "linked_character_ids": [],
        "linked_location_ids": [],
        "linked_item_ids": [],
        "linked_rule_ids": [],
        "version": 1,
    }


class NovelBlueprintService:
    """创作蓝图 AI 服务。"""

    def __init__(self) -> None:
        self.card_repo = SettingCardRepository()
        self.chapter_repo = ChapterRepository()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_blueprint_status(status: str) -> str:
        """校验蓝图状态取值。"""
        text = str(status or "").strip()
        if text not in ("draft", "pending_confirmation", "confirmed", "superseded"):
            raise InvalidIdError(f"非法的蓝图状态: {status}")
        return text

    async def _load_available_entity_ids(self, novel_id: str) -> Dict[str, set]:
        """加载本小说可用的角色、势力和设定卡业务 ID 集合。"""
        characters = await character_repo.get_characters_by_novel(novel_id)
        cards = await self.card_repo.list_cards(novel_id)
        return {
            "character_ids": {
                str(item.get("character_id") or "").strip()
                for item in characters
                if str(item.get("character_id") or "").strip()
            },
            "faction_ids": {
                str(item.get("card_id") or "").strip()
                for item in cards
                if str(item.get("card_id") or "").strip()
            },
            "setting_card_ids": {
                str(item.get("card_id") or "").strip()
                for item in cards
                if str(item.get("card_id") or "").strip()
            },
        }

    @staticmethod
    def _filter_unknown_ids(
        chapters: List[Dict[str, Any]],
        available: Dict[str, set],
        warnings: List[str],
    ) -> None:
        """把大纲中引用了未知业务 ID 的条目剔除并记录告警。

        Args:
            chapters: 已归一化的章节列表。
            available: 各实体类型可用的业务 ID 集合。
            warnings: 告警收集列表，会被就地追加。
        """
        for chapter in chapters:
            for field in OUTLINE_ENTITY_FIELDS:
                allowed = available.get(field) or set()
                kept: List[str] = []
                for value in chapter.get(field) or []:
                    text = str(value or "").strip()
                    if not text:
                        continue
                    if text in allowed:
                        kept.append(text)
                    else:
                        warnings.append(
                            f"章节「{chapter.get('title') or '未命名'}」引用了未知的 {field} 业务 ID: {text}，已忽略"
                        )
                chapter[field] = kept

    # ------------------------------------------------------------------
    # 蓝图整理
    # ------------------------------------------------------------------
    async def prepare_blueprint(self, request: Any) -> Dict[str, Any]:
        """整理用户已选素材，产出蓝图候选（含建议、冲突与追问）。"""
        novel_id = request.novel_id
        kind = BLUEPRINT_KINDS["prepare"]
        cached = await reuse_record(novel_id, kind, _request_id(request))
        if cached:
            return _candidate_response(
                cached,
                data=cached.get("data") or {},
                blueprint_id=str(getattr(request, "blueprint_id", "") or ""),
                version=int(getattr(request, "blueprint_version", 0) or 0) or 1,
                status="pending_confirmation",
            )

        # 增量新增：新建小说场景还没有 novel_id，此时只做无持久化整理
        has_novel = bool(str(novel_id or "").strip())
        novel: Dict[str, Any] = {}
        if has_novel:
            novel = await novel_repo.get_novel_by_id(novel_id)
        from backend.services.llm.chapter_context_service import chapter_context_service

        context = None
        context_text = ""
        if has_novel:
            context = await chapter_context_service.build(
                novel, _empty_chapter(), user_prompt=getattr(request, "user_prompt", "") or ""
            )
            context_text = context.text
        prompts = get_prompt_section("prepare_blueprint")
        prompt = (
            prompts["prepare_blueprint_prompt_base"].format(
                novel_title=str(novel.get("title") or ""),
                generation_mode=str(getattr(request, "generation_mode", "") or "guided"),
                plot_summary=str(getattr(request, "plot_summary", "") or "").strip() or "（未填写）",
                worldview=str(getattr(request, "worldview", "") or "").strip() or "（未填写）",
                power_system_json=_dump_json(getattr(request, "power_system", None)),
                selected_entities_json=_dump_json(
                    getattr(request, "selected_entities", None) or {}
                ),
                user_prompt=str(getattr(request, "user_prompt", "") or "").strip() or "（无）",
            )
            + "\n"
            + prompts["prepare_blueprint_prompt_without_schema_suffix"]
        ).strip()

        service, provider, use_json_schema, _stream = resolve_runtime(
            request, WORKFLOW_NAME, "prepare_blueprint"
        )
        result = await generate_structured_result(
            service,
            prompt,
            BlueprintPrepareResultSchema,
            "prepare_blueprint",
            gen_kwargs=build_generation_kwargs(request),
            use_json_schema=use_json_schema,
        )

        warnings: List[str] = []
        conflicts = [
            {
                "severity": str(item.severity or "notice"),
                "category": str(item.category or ""),
                "entity_type": str(item.entity_type or ""),
                "entity_id": str(item.entity_id or ""),
                "message": str(item.message or ""),
                "suggestion": str(item.suggestion or ""),
                "can_ignore": bool(item.can_ignore),
            }
            for item in result.conflicts
            if str(item.message or "").strip()
        ]
        data = {
            "plot_summary": str(result.plot_summary or ""),
            "worldview": str(result.worldview or ""),
            "power_system": result.power_system.model_dump() if result.power_system else None,
            "suggestions": [item.model_dump() for item in result.suggestions],
            "conflicts": conflicts,
            "questions": [item.model_dump() for item in result.questions],
            "notes": str(result.notes or ""),
        }
        if not data["plot_summary"]:
            warnings.append("AI 未返回剧情摘要，请手动补全后再确认")
        if not data["worldview"]:
            warnings.append("AI 未返回世界观，请手动补全后再确认")

        if not has_novel:
            # 未创建小说时不落生成记录，返回一次性候选，避免无归属的脏数据
            return _candidate_response(
                {
                    "candidate_id": f"draft-{uuid4().hex[:12]}",
                    "provider": provider,
                    "model": "",
                    "context_snapshot": {},
                },
                data=data,
                blueprint_id=str(getattr(request, "blueprint_id", "") or ""),
                version=int(getattr(request, "blueprint_version", 0) or 0) or 1,
                status="pending_confirmation",
                warnings=warnings,
                conflicts=[str(item.get("message") or "") for item in conflicts],
            )

        record = await save_record(
            novel_id,
            kind,
            request_id=_request_id(request),
            snapshot=context.snapshot if context else {},
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[str(item.get("message") or "") for item in conflicts],
            blueprint_version=int(getattr(request, "blueprint_version", 0) or 0) or None,
        )
        return _candidate_response(
            record,
            data=data,
            blueprint_id=str(getattr(request, "blueprint_id", "") or ""),
            version=int(getattr(request, "blueprint_version", 0) or 0) or 1,
            status="pending_confirmation",
        )

    # ------------------------------------------------------------------
    # 蓝图补全
    # ------------------------------------------------------------------
    async def complete_blueprint(self, request: Any) -> Dict[str, Any]:
        """补全蓝图中为空的字段，只产出候选。"""
        novel_id = request.novel_id
        kind = BLUEPRINT_KINDS["complete"]
        cached = await reuse_record(novel_id, kind, _request_id(request))
        if cached:
            return _candidate_response(
                cached,
                data=cached.get("data") or {},
                blueprint_id=str(getattr(request, "blueprint_id", "") or ""),
                version=int(getattr(request, "blueprint_version", 0) or 0) or 1,
                status="pending_confirmation",
            )

        blueprint = getattr(request, "blueprint", None) or {}
        missing_fields = [
            str(item).strip()
            for item in (getattr(request, "missing_fields", None) or [])
            if str(item).strip()
        ]
        if not missing_fields:
            missing_fields = [
                field
                for field in BLUEPRINT_REQUIRED_FIELDS
                if not str(blueprint.get(field) or "").strip()
            ]
        if not missing_fields:
            missing_fields = list(BLUEPRINT_REQUIRED_FIELDS)

        from backend.services.llm.chapter_context_service import chapter_context_service

        novel = await novel_repo.get_novel_by_id(novel_id)
        context = await chapter_context_service.build(
            novel, _empty_chapter(), user_prompt=getattr(request, "user_prompt", "") or ""
        )
        prompts = get_prompt_section("complete_blueprint")
        prompt = (
            prompts["complete_blueprint_prompt_base"].format(
                blueprint_json=_dump_json(blueprint),
                missing_fields="、".join(missing_fields),
                context_text=context.text,
                user_prompt=str(getattr(request, "user_prompt", "") or "").strip() or "（无）",
            )
            + "\n"
            + prompts["complete_blueprint_prompt_without_schema_suffix"]
        ).strip()

        service, provider, use_json_schema, _stream = resolve_runtime(
            request, WORKFLOW_NAME, "complete_blueprint"
        )
        result = await generate_structured_result(
            service,
            prompt,
            BlueprintPrepareResultSchema,
            "complete_blueprint",
            gen_kwargs=build_generation_kwargs(request),
            use_json_schema=use_json_schema,
        )

        warnings: List[str] = []
        # 补全只回填请求中声明为空的字段，其余字段一律忽略，防止覆盖用户已填内容
        data: Dict[str, Any] = {}
        for field in missing_fields:
            value = str(getattr(result, field, "") or "").strip()
            if value:
                data[field] = value
            else:
                warnings.append(f"AI 未能补全字段 {field}，请手动填写")
        if result.notes:
            data["notes"] = str(result.notes or "")

        record = await save_record(
            novel_id,
            kind,
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[],
            blueprint_version=int(getattr(request, "blueprint_version", 0) or 0) or None,
            request_id=_request_id(request),
        )
        return _candidate_response(
            record,
            data=data,
            blueprint_id=str(getattr(request, "blueprint_id", "") or ""),
            version=int(getattr(request, "blueprint_version", 0) or 0) or 1,
            status="pending_confirmation",
        )

    # ------------------------------------------------------------------
    # 战力体系
    # ------------------------------------------------------------------
    async def generate_power_system(self, request: Any) -> Dict[str, Any]:
        """生成或补全一套结构化战力体系候选。"""
        novel_id = request.novel_id
        kind = BLUEPRINT_KINDS["power_system"]
        cached = await reuse_record(novel_id, kind, _request_id(request))
        if cached:
            return _candidate_response(cached, data=cached.get("data") or {})

        novel = await novel_repo.get_novel_by_id(novel_id)
        prompts = get_prompt_section("generate_power_system")
        prompt = (
            prompts["generate_power_system_prompt_base"].format(
                novel_title=str(novel.get("title") or ""),
                genre=str(novel.get("genre") or "").strip() or "（未指定）",
                worldview=str(getattr(request, "worldview", "") or "").strip() or "（未填写）",
                existing_power_system_json=_dump_json(getattr(request, "power_system", None)),
                user_prompt=str(getattr(request, "user_prompt", "") or "").strip() or "（无）",
            )
            + "\n"
            + prompts["generate_power_system_prompt_without_schema_suffix"]
        ).strip()

        service, provider, use_json_schema, _stream = resolve_runtime(
            request, WORKFLOW_NAME, "generate_power_system"
        )
        result = await generate_structured_result(
            service,
            prompt,
            PowerSystemGenerateResultSchema,
            "generate_power_system",
            gen_kwargs=build_generation_kwargs(request),
            use_json_schema=use_json_schema,
        )

        warnings: List[str] = [str(item) for item in result.warnings]
        levels = result.power_system.normalized_levels()
        if not levels:
            warnings.append("AI 未返回任何战力等级，请补充后重试")

        data = {
            "power_system": {
                **result.power_system.model_dump(),
                "levels": levels,
            },
            "notes": str(result.notes or ""),
        }
        record = await save_record(
            novel_id,
            kind,
            snapshot={"generation_mode": "guided"},
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[],
            blueprint_version=int(getattr(request, "blueprint_version", 0) or 0) or None,
            request_id=_request_id(request),
        )
        return _candidate_response(record, data=data)

    # ------------------------------------------------------------------
    # 卷章大纲
    # ------------------------------------------------------------------
    async def generate_outline(self, request: Any) -> Dict[str, Any]:
        """根据已确认蓝图生成卷章大纲候选。"""
        novel_id = request.novel_id
        kind = BLUEPRINT_KINDS["outline"]
        # 增量新增：大纲必须基于当前小说的已确认蓝图，且版本必须一致
        # 先校验蓝图，避免带过期版本的 request_id 直接复用历史候选。
        blueprint = await self._load_confirmed_blueprint(request)
        blueprint_version = int(blueprint.get("version") or 1)
        cached = await reuse_record(novel_id, kind, _request_id(request))
        if cached:
            data = cached.get("data") or {}
            return _candidate_response(
                cached,
                volumes=data.get("volumes") or [],
                blueprint_version=blueprint_version,
            )

        novel = await novel_repo.get_novel_by_id(novel_id)
        available = await self._load_available_entity_ids(novel_id)
        prompts = get_prompt_section("generate_outline")
        prompt = (
            prompts["generate_outline_prompt_base"].format(
                novel_title=str(novel.get("title") or ""),
                blueprint_json=_dump_json(blueprint or getattr(request, "blueprint", None) or {}),
                target_chapter_count=int(getattr(request, "target_chapter_count", 0) or 0) or "未指定",
                target_word_count=int(getattr(request, "target_word_count", 0) or 0) or "未指定",
                available_entities_json=_dump_json(
                    {key: sorted(value) for key, value in available.items()}
                ),
                user_prompt=str(getattr(request, "user_prompt", "") or "").strip() or "（无）",
            )
            + "\n"
            + prompts["generate_outline_prompt_without_schema_suffix"]
        ).strip()

        service, provider, use_json_schema, _stream = resolve_runtime(
            request, WORKFLOW_NAME, "generate_outline"
        )
        result = await generate_structured_result(
            service,
            prompt,
            NovelOutlineResultSchema,
            "generate_outline",
            gen_kwargs=build_generation_kwargs(request),
            use_json_schema=use_json_schema,
        )

        warnings: List[str] = [str(item) for item in result.warnings]
        chapters: List[Dict[str, Any]] = []
        volumes: List[Dict[str, Any]] = []
        for volume in result.volumes:
            items: List[Dict[str, Any]] = [item.model_dump() for item in volume.chapters]
            self._filter_unknown_ids(items, available, warnings)
            if not items:
                warnings.append(f"卷「{volume.title or '未命名'}」没有可用章节，已忽略")
                continue
            volumes.append(
                {
                    "title": str(volume.title or ""),
                    "summary": str(volume.summary or ""),
                    "chapters": items,
                }
            )
            chapters.extend(items)
        if not chapters:
            warnings.append("AI 未返回任何章节，请调整蓝图或补充要求后重试")

        conflicts = [
            {
                "severity": str(item.severity or "notice"),
                "category": str(item.category or ""),
                "entity_type": str(item.entity_type or ""),
                "entity_id": str(item.entity_id or ""),
                "message": str(item.message or ""),
                "suggestion": str(item.suggestion or ""),
                "can_ignore": bool(item.can_ignore),
            }
            for item in result.conflicts
            if str(item.message or "").strip()
        ]
        data = {
            "volumes": volumes,
            "chapter_count": len(chapters),
            "conflicts": conflicts,
        }
        record = await save_record(
            novel_id,
            kind,
            snapshot={
                "chapter_count": len(chapters),
                "blueprint_id": str(blueprint.get("blueprint_id") or ""),
            },
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[str(item.get("message") or "") for item in conflicts],
            blueprint_version=blueprint_version,
            request_id=_request_id(request),
        )
        return _candidate_response(
            record,
            volumes=volumes,
            blueprint_version=blueprint_version,
            conflicts=conflicts,
        )

    # ------------------------------------------------------------------
    # 大纲确认与分章生成
    # ------------------------------------------------------------------
    @staticmethod
    def _outline_blueprint_version(request: Any) -> int:
        """读取请求声明的蓝图版本，缺省按 1 处理。"""
        return int(getattr(request, "blueprint_version", 0) or 0) or 1

    async def _load_confirmed_blueprint(self, request: Any) -> Dict[str, Any]:
        """读取并校验本次生成依赖的已确认蓝图。

        Args:
            request: 含 novel_id、可选 blueprint_id 与 blueprint_version 的请求对象。

        Returns:
            已确认的蓝图文档。

        Raises:
            NotFoundError: 蓝图不存在时抛出。
            DuplicateKeyError: 蓝图未确认或版本不匹配时抛出。
        """
        novel_id = str(request.novel_id)
        blueprint_id = str(getattr(request, "blueprint_id", "") or "").strip()
        expected_version = int(getattr(request, "blueprint_version", 0) or 0)
        blueprint: Optional[Dict[str, Any]] = None
        if blueprint_id:
            try:
                blueprint = await novel_blueprint_repo.get_blueprint(novel_id, blueprint_id)
            except NotFoundError:
                blueprint = None
        else:
            blueprint = await novel_blueprint_repo.find_confirmed(novel_id)
        if not blueprint:
            raise NotFoundError("未找到可用的已确认蓝图，请先确认创作蓝图")
        status = str(blueprint.get("status") or "").strip()
        if status != "confirmed":
            raise DuplicateKeyError("蓝图尚未确认，请先确认创作蓝图")
        current_version = int(blueprint.get("version") or 0)
        if expected_version and current_version != expected_version:
            raise DuplicateKeyError(
                f"蓝图版本已变化（当前 v{current_version}），请刷新后重试"
            )
        return blueprint

    def _normalize_submitted_outline(
        self,
        volumes: Any,
        available: Dict[str, set],
        warnings: List[str],
    ) -> List[Dict[str, Any]]:
        """归一化用户提交的大纲：校验卷章结构、章节顺序与实体 ID 归属。

        Args:
            volumes: 用户提交的卷列表。
            available: 各实体类型可用的业务 ID 集合。
            warnings: 告警收集列表，会被就地追加。

        Returns:
            归一化后的卷列表，章节序号已连续重排。

        Raises:
            InvalidIdError: 卷缺少标题、没有章节或章节缺少标题时抛出。
        """
        normalized: List[Dict[str, Any]] = []
        order = 0
        for index, volume in enumerate(volumes or [], start=1):
            if not isinstance(volume, dict):
                raise InvalidIdError(f"第 {index} 卷格式非法")
            title = str(volume.get("title") or "").strip()
            if not title:
                raise InvalidIdError(f"第 {index} 卷缺少标题")
            raw_chapters = list(volume.get("chapters") or [])
            if not raw_chapters:
                raise InvalidIdError(f"第 {index} 卷「{title}」没有章节")
            chapters: List[Dict[str, Any]] = []
            for position, chapter in enumerate(raw_chapters, start=1):
                if not isinstance(chapter, dict):
                    raise InvalidIdError(f"第 {index} 卷第 {position} 章格式非法")
                chapter_title = str(chapter.get("title") or "").strip()
                if not chapter_title:
                    raise InvalidIdError(f"第 {index} 卷第 {position} 章缺少标题")
                order += 1
                chapters.append(
                    {
                        "index": order,
                        "title": chapter_title,
                        "summary": str(chapter.get("summary") or "").strip(),
                        "character_ids": chapter.get("character_ids") or [],
                        "faction_ids": chapter.get("faction_ids") or [],
                        "setting_card_ids": chapter.get("setting_card_ids") or [],
                    }
                )
            self._filter_unknown_ids(chapters, available, warnings)
            normalized.append(
                {
                    "order": index,
                    "title": title,
                    "summary": str(volume.get("summary") or "").strip(),
                    "chapters": chapters,
                }
            )
        return normalized

    async def _load_confirmed_outline(
        self,
        novel_id: str,
        outline_generation_id: str = "",
    ) -> Dict[str, Any]:
        """读取最近一次已确认的大纲生成记录。

        Args:
            novel_id: 小说 ObjectId 字符串。
            outline_generation_id: 可选的大纲生成记录业务 ID。

        Returns:
            已确认的大纲确认记录文档。

        Raises:
            NotFoundError: 尚未确认任何大纲时抛出。
        """
        query: Dict[str, Any] = {
            "novel_id": ObjectId(novel_id),
            "kind": BLUEPRINT_KINDS["confirm_outline"],
            "status": "completed",
            "input_snapshot.confirmed": True,
        }
        if outline_generation_id:
            query["input_snapshot.outline_generation_id"] = outline_generation_id
        docs = await generation_record_repo.find_many(
            query, sort=[("created_at", -1)], limit=1
        )
        if not docs:
            raise NotFoundError("尚未确认任何大纲，请先确认卷章大纲")
        return docs[0]

    @staticmethod
    def _flatten_outline_chapters(record: Dict[str, Any]) -> List[Dict[str, Any]]:
        """把已确认大纲摊平成带章节 ID 的列表。"""
        result = _record_result(record)
        flat: List[Dict[str, Any]] = []
        for volume in result.get("volumes") or []:
            volume_title = str(volume.get("title") or "")
            for chapter in volume.get("chapters") or []:
                flat.append(
                    {
                        "chapter_id": str(chapter.get("chapter_id") or ""),
                        "volume_title": volume_title,
                        "title": str(chapter.get("title") or ""),
                        "summary": str(chapter.get("summary") or ""),
                        "index": int(chapter.get("index") or 0),
                    }
                )
        return [item for item in flat if item["chapter_id"]]

    async def confirm_outline(self, request: Any) -> Dict[str, Any]:
        """确认（或暂存）卷章大纲，确认后创建正式卷章。

        Args:
            request: 含 novel_id、generation_id、outline、outline_version、confirm 的请求对象。

        Returns:
            含 generation_id、outline_version、status、volumes 的响应。

        Raises:
            InvalidIdError: 生成记录类型不符或提交的大纲结构非法时抛出。
            DuplicateKeyError: 记录未完成、已确认或蓝图版本不一致时抛出。
        """
        novel_id = str(request.novel_id)
        generation_id = str(getattr(request, "generation_id", "") or "").strip()
        if not generation_id:
            raise InvalidIdError("缺少大纲生成记录 ID")
        record = await generation_record_repo.get_owned_record(novel_id, generation_id)
        if str(record.get("kind") or "") != BLUEPRINT_KINDS["outline"]:
            raise InvalidIdError("该生成记录不是大纲生成记录")
        if str(record.get("status") or "") != "completed":
            raise DuplicateKeyError("该大纲生成记录尚未完成，无法确认")
        if str(record.get("action_type") or "") == "rejected":
            raise DuplicateKeyError("该大纲已被拒绝，请重新生成")

        confirmed_before = await generation_record_repo.find_one(
            {
                "novel_id": ObjectId(novel_id),
                "kind": BLUEPRINT_KINDS["confirm_outline"],
                "status": "completed",
                "input_snapshot.outline_generation_id": generation_id,
                "input_snapshot.confirmed": True,
            }
        )
        if confirmed_before:
            raise DuplicateKeyError("该大纲已确认，不能重复确认")

        blueprint = await self._load_confirmed_blueprint(request)
        blueprint_version = int(blueprint.get("version") or 1)
        generated_blueprint_version = int(record.get("blueprint_version") or 0)
        generated_blueprint_id = str(
            (record.get("input_snapshot") or {}).get("blueprint_id") or ""
        )
        if generated_blueprint_version and generated_blueprint_version != blueprint_version:
            raise DuplicateKeyError("大纲基于过期蓝图生成，请按当前蓝图重新生成大纲")
        if generated_blueprint_id and generated_blueprint_id != str(blueprint.get("blueprint_id") or ""):
            raise DuplicateKeyError("大纲引用的蓝图已不是当前确认版本，请重新生成大纲")
        available = await self._load_available_entity_ids(novel_id)
        warnings: List[str] = []
        submitted = getattr(request, "outline", None) or {}
        volumes = self._normalize_submitted_outline(
            submitted.get("volumes") or [], available, warnings
        )
        if not volumes:
            raise InvalidIdError("提交的大纲没有可用章节")

        original = (_record_result(record).get("volumes")) or []
        changed = _dump_json(volumes) != _dump_json(original)
        base_version = int(getattr(request, "outline_version", 0) or 0) or 1
        version = base_version + 1 if changed else base_version

        confirm = bool(getattr(request, "confirm", False))
        chapter_map: List[Dict[str, Any]] = []
        if confirm:
            for volume in volumes:
                created_volume = await volume_repo.create_volume(
                    {
                        "novel_id": ObjectId(novel_id),
                        "title": volume["title"],
                        "summary": volume.get("summary") or "",
                        "order_index": volume["order"],
                    }
                )
                volume_id = (
                    str(created_volume.get("_id") or "")
                    if isinstance(created_volume, dict)
                    else str(created_volume or "")
                )
                volume["volume_id"] = volume_id
                for chapter in volume.get("chapters") or []:
                    snapshot = {
                        "blueprint_version": blueprint_version,
                        "outline_version": version,
                        "outline_index": chapter["index"],
                        "outline_character_ids": list(chapter.get("character_ids") or []),
                        "outline_setting_card_ids": list(chapter.get("setting_card_ids") or []),
                    }
                    created_chapter = await self.chapter_repo.create_chapter(
                        novel_id,
                        {
                            "volume_id": volume_id,
                            "title": chapter["title"],
                            "summary": chapter.get("summary") or "",
                            "status": "draft",
                            "blueprint_snapshot": snapshot,
                        },
                    )
                    chapter_id = str(created_chapter.get("_id") or "")
                    chapter["chapter_id"] = chapter_id
                    chapter_map.append(
                        {
                            "outline_index": chapter["index"],
                            "chapter_id": chapter_id,
                            "volume_id": volume_id,
                            "title": chapter["title"],
                        }
                    )

        confirm_record = await save_record(
            novel_id,
            BLUEPRINT_KINDS["confirm_outline"],
            request_id=_request_id(request),
            snapshot={
                "outline_generation_id": generation_id,
                "blueprint_id": str(blueprint.get("blueprint_id") or ""),
                "blueprint_version": blueprint_version,
                "confirmed": confirm,
                "changed": changed,
            },
            data={
                "volumes": volumes,
                "version": version,
                "chapter_map": chapter_map,
            },
            provider="",
            warnings=warnings,
            conflicts=[],
            blueprint_version=blueprint_version,
            outline_version=version,
        )
        return {
            "generation_id": str(confirm_record.get("candidate_id") or ""),
            "outline_version": version,
            "status": "confirmed" if confirm else "draft",
            "volumes": volumes,
            "message": (
                f"大纲已确认，已创建 {len(chapter_map)} 个章节草稿"
                if confirm
                else "大纲草稿已保存为 v" + str(version)
            ),
        }

    async def generate_chapters(self, request: Any) -> Dict[str, Any]:
        """按已确认大纲批量生成章节正文候选。

        Args:
            request: 含 novel_id、outline_generation_id、outline_version、chapter_ids、
                start_index、count、allow_overwrite 的请求对象。

        Returns:
            含 generation_id、blueprint_version、outline_version、chapters、warnings、failed 的响应。

        Raises:
            NotFoundError: 大纲未确认或章节不存在时抛出。
            DuplicateKeyError: 版本不一致时抛出。
        """
        novel_id = str(request.novel_id)
        blueprint = await self._load_confirmed_blueprint(request)
        blueprint_version = int(blueprint.get("version") or 1)
        outline_generation_id = str(getattr(request, "outline_generation_id", "") or "").strip()
        confirmed = await self._load_confirmed_outline(novel_id, outline_generation_id)
        outline_version = int(_record_result(confirmed).get("version") or 1)
        expected_version = int(getattr(request, "outline_version", 0) or 0)
        if expected_version and expected_version != outline_version:
            raise DuplicateKeyError(
                f"大纲版本已变化（当前 v{outline_version}），请刷新后重试"
            )

        flat = self._flatten_outline_chapters(confirmed)
        if not flat:
            raise InvalidIdError("已确认大纲中没有可生成的章节，请重新确认大纲")

        requested_ids = [
            str(item).strip() for item in (getattr(request, "chapter_ids", None) or []) if str(item).strip()
        ]
        if requested_ids:
            wanted = set(requested_ids)
            targets = [item for item in flat if item["chapter_id"] in wanted]
            if len(targets) != len(wanted):
                raise NotFoundError("存在不属于当前小说或大纲的章节 ID")
        else:
            start = max(0, int(getattr(request, "start_index", 0) or 0))
            count = max(1, int(getattr(request, "count", 0) or 1))
            targets = flat[start : start + count]
        if not targets:
            raise InvalidIdError("没有可生成的章节")

        # 延迟导入，避免与章节生成服务形成循环依赖
        from backend.services.llm.chapter_generation_service import (
            chapter_generation_service,
        )

        allow_overwrite = bool(getattr(request, "allow_overwrite", False))
        novel = await novel_repo.get_novel_by_id(novel_id)
        chapters: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        warnings: List[str] = []
        for item in targets:
            try:
                chapter = await self.chapter_repo.get_chapter(item["chapter_id"])
            except (NotFoundError, InvalidIdError):
                failed.append(
                    {"chapter_id": item["chapter_id"], "message": "章节不存在或不属于当前小说"}
                )
                continue
            if str(chapter.get("novel_id")) != str(novel.get("_id")):
                failed.append(
                    {"chapter_id": item["chapter_id"], "message": "章节不属于当前小说"}
                )
                continue
            if str(chapter.get("content") or "").strip() and not allow_overwrite:
                failed.append(
                    {
                        "chapter_id": item["chapter_id"],
                        "message": "章节已有正文，未覆盖（如需覆盖请传 allow_overwrite=true）",
                    }
                )
                continue
            draft_request = SimpleNamespace(
                novel_id=novel_id,
                chapter_id=str(chapter.get("_id")),
                request_id=(
                    f"{_request_id(request)}:{item['chapter_id']}"
                    if _request_id(request)
                    else ""
                ),
                user_prompt=" ".join(
                    part
                    for part in (
                        getattr(request, "user_prompt", "") or "",
                        item["title"],
                        item["summary"],
                    )
                    if part
                ).strip(),
                plan={"title": item["title"], "summary": item["summary"]},
                target_words=int(getattr(request, "target_words", 0) or 0),
                provider=getattr(request, "provider", None),
                model=getattr(request, "model", None),
                temperature=getattr(request, "temperature", None),
                max_tokens=getattr(request, "max_tokens", None),
                use_stream=getattr(request, "use_stream", None),
            )
            try:
                candidate = await chapter_generation_service.generate_draft(draft_request)
            except Exception as exc:  # 单章失败不影响整批
                logger.warning("按大纲生成章节失败: %s", exc)
                failed.append({"chapter_id": item["chapter_id"], "message": str(exc)})
                continue
            candidate_data = candidate.get("data") or {}
            chapters.append(
                {
                    "chapter_id": item["chapter_id"],
                    "title": str(candidate_data.get("title") or item["title"]),
                    "content": str(candidate_data.get("content") or ""),
                    "summary": str(candidate_data.get("summary") or item["summary"]),
                    "index": int(item.get("index") or 0),
                    "status": "candidate",
                    "generation_id": str(candidate.get("candidate_id") or ""),
                    "warnings": list(candidate.get("warnings") or []),
                }
            )
            for warning in candidate.get("warnings") or []:
                warnings.append(f"「{item['title']}」{warning}")

        return {
            "generation_id": chapters[0]["generation_id"] if chapters else "",
            "blueprint_version": blueprint_version,
            "outline_version": outline_version,
            "chapters": chapters,
            "warnings": warnings,
            "failed": failed,
        }

    # ------------------------------------------------------------------
    # 确认前校验
    # ------------------------------------------------------------------
    @staticmethod
    def validate_before_confirm(blueprint: Dict[str, Any]) -> List[str]:
        """执行蓝图确认前的校验，返回阻塞问题列表。

        确认前必须校验：必填字段、blocking 冲突、实体 ID 归属、状态字段白名单。

        Args:
            blueprint: 待确认的蓝图内容。

        Returns:
            阻塞问题列表；为空表示可以确认。
        """
        problems: List[str] = []
        for field in BLUEPRINT_REQUIRED_FIELDS:
            if not str(blueprint.get(field) or "").strip():
                problems.append(f"必填字段 {field} 为空")
        conflicts = blueprint.get("conflicts") or []
        blocking = [
            item
            for item in conflicts
            if isinstance(item, dict)
            and str(item.get("severity") or "").strip() == "blocking"
        ]
        if blocking:
            problems.append(f"存在 {len(blocking)} 个 blocking 冲突，需先处理")
        status = str(blueprint.get("status") or "").strip()
        allowed_status = ("draft", "pending_confirmation", "confirmed", "superseded")
        if status and status not in allowed_status:
            problems.append(f"蓝图状态 {status} 不在白名单内")
        return problems


novel_blueprint_service = NovelBlueprintService()
