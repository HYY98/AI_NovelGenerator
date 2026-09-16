"""设定卡 AI 服务：生成、提取、补全与冲突检查。

统一约定：
- 三类卡（地点/物品/规则）共用同一套结构化候选；
- 只产出候选，正式写入仍走 SettingCardService；
- 补全只填空白字段，锁定字段一律不返回；
- 卡片生成/提取/补全都写入 generation_records，便于追溯与幂等。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from backend.db.errors import InvalidIdError, NotFoundError
from backend.db.repositories.chapter_repository import ChapterRepository
from backend.db.repositories.novel_repository import novel_repo
from backend.db.repositories.setting_card_repository import CARD_TYPES, SettingCardRepository
from backend.llm.prompts.prompt_selector import get_prompt_section
from backend.llm.schemas.setting_card_pydantic import (
    CONFLICT_SEVERITIES,
    CardCandidateSchema,
    CardCompleteResultSchema,
    CardConflictResultSchema,
    CardExtractResultSchema,
    CardGenerateResultSchema,
    CardRewriteResultSchema,
)
from backend.services.llm.chapter_context_service import ChapterContext, chapter_context_service
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

WORKFLOW_NAME = "setting_card_ai"

# 默认保护字段：AI 改写时不允许直接改，用户显式解锁后才可改
DEFAULT_PROTECTED_FIELDS = ("name", "current_state")

# 规则卡额外保护字段：is_hard_rule 既不能由 AI 单独置为 true，也不能默认进入可改列表
RULE_PROTECTED_FIELDS = ("is_hard_rule",)

# 卡片类型 -> 生成记录的 kind（技术指引 19.3）
CARD_KIND = {
    "location": "location_card",
    "item": "item_card",
    "rule": "rule_card",
}

# 补全默认锁定的字段：这些字段由用户明确决定，AI 不得自作主张
DEFAULT_LOCKED_FIELDS = ("name",)

# 没有指定章节时使用的空章节，让上下文组装器复用同一套逻辑
EMPTY_CHAPTER: Dict[str, Any] = {
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


class SettingCardGenerationService:
    """设定卡 AI 生成与检查服务。"""

    def __init__(self) -> None:
        self.card_repo = SettingCardRepository()
        self.chapter_repo = ChapterRepository()

    # ------------------------------------------------------------------
    # 准备
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_type(card_type: str) -> str:
        """校验卡片类型。"""
        text = str(card_type or "").strip().lower()
        if text not in CARD_TYPES:
            raise InvalidIdError(f"不支持的卡片类型: {card_type}，支持 location/item/rule")
        return text

    async def _build_context(
        self,
        novel_id: str,
        chapter_id: str,
        user_prompt: str,
    ) -> ChapterContext:
        """组装卡片 AI 使用的上下文（无章节时退化为全书上下文）。"""
        novel = await novel_repo.get_novel_by_id(novel_id)
        chapter = EMPTY_CHAPTER
        if chapter_id:
            chapter = await self.chapter_repo.get_chapter(chapter_id)
        return await chapter_context_service.build(
            novel,
            chapter,
            user_prompt=user_prompt,
            include_core_cards=True,
        )

    @staticmethod
    def _existing_cards_brief(cards: List[Dict[str, Any]]) -> str:
        """把已有卡片压缩成"名称 + 别名 + 业务ID"的紧凑清单。"""
        lines: List[str] = []
        for card in cards:
            aliases = card.get("aliases") or []
            alias_text = f"（别名：{'、'.join(str(a) for a in aliases if str(a).strip())}）" if aliases else ""
            lines.append(
                f"- [{card.get('type')}] {card.get('name')}{alias_text} card_id={card.get('card_id')}"
            )
        return "\n".join(lines) if lines else "（暂无）"

    @staticmethod
    def _field_spec(card_type: str) -> str:
        """渲染该卡类型的字段说明。"""
        fields = CARD_TYPES[card_type]["fields"]
        states = CARD_TYPES[card_type].get("state_options") or []
        lines = [f"- {key}：{label}" for key, label in fields.items()]
        lines.append(f"允许的 current_state 取值：{'、'.join(states) if states else '不限'}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # AI 生成卡片
    # ------------------------------------------------------------------
    async def generate_card(self, request: Any) -> Dict[str, Any]:
        """生成卡片候选（不写正式卡片）。"""
        card_type = self._validate_type(getattr(request, "type", ""))
        kind = CARD_KIND[card_type]
        novel_id = request.novel_id
        cached = await reuse_record(novel_id, kind, getattr(request, "request_id", "") or "")
        if cached:
            return cached

        context = await self._build_context(
            novel_id,
            getattr(request, "chapter_id", "") or "",
            getattr(request, "user_prompt", "") or "",
        )
        existing = await self.card_repo.list_cards(novel_id, card_type=card_type)
        existing_names = {str(card.get("name") or "").strip() for card in existing}
        existing_aliases = {
            str(alias).strip()
            for card in existing
            for alias in (card.get("aliases") or [])
            if str(alias).strip()
        }

        prompts = get_prompt_section("generate_setting_card")
        prompt = (
            prompts["generate_setting_card_prompt_base"].format(
                card_type_label=CARD_TYPES[card_type]["label"],
                context_text=context.text,
                existing_cards_json=self._existing_cards_brief(existing),
                field_spec=self._field_spec(card_type),
                user_prompt=(getattr(request, "user_prompt", "") or "").strip() or "按当前章节需要补一张必要的设定卡",
            )
            + "\n"
            + prompts["generate_setting_card_prompt_without_schema_suffix"]
        ).strip()

        service, provider, use_json_schema, _stream = resolve_runtime(
            request, WORKFLOW_NAME, "generate_setting_card"
        )
        result = await generate_structured_result(
            service,
            prompt,
            CardGenerateResultSchema,
            "generate_setting_card",
            gen_kwargs=build_generation_kwargs(request),
            use_json_schema=use_json_schema,
        )

        warnings: List[str] = []
        candidates: List[Dict[str, Any]] = []
        for candidate in result.candidates:
            normalized = self._normalize_candidate(
                candidate, card_type, existing_names, existing_aliases, warnings
            )
            if normalized:
                candidates.append(normalized)
        data = {"candidates": candidates, "notes": result.notes}
        return await save_record(
            novel_id,
            kind,
            chapter_id=getattr(request, "chapter_id", "") or "",
            request_id=getattr(request, "request_id", "") or "",
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[],
        )

    def _normalize_candidate(
        self,
        candidate: CardCandidateSchema,
        card_type: str,
        existing_names: set,
        existing_aliases: set,
        warnings: List[str],
    ) -> Optional[Dict[str, Any]]:
        """校验并归一化单张卡片候选。"""
        name = str(candidate.name or "").strip()
        if not name:
            warnings.append("忽略了一张没有名称的候选卡片")
            return None
        fields = {
            key: str(value)
            for key, value in (candidate.fields or {}).items()
            if key in CARD_TYPES[card_type]["fields"] and str(value).strip()
        }
        dropped = [
            key for key in (candidate.fields or {}) if key not in CARD_TYPES[card_type]["fields"]
        ]
        if dropped:
            warnings.append(f"候选卡片字段 {', '.join(dropped)} 不属于该类型，已忽略")
        conflicts = list(candidate.conflicts or [])
        if name in existing_names:
            conflicts.append(f"名称「{name}」与已有同类卡片重复，采纳前需要改名或改为合并")
        if name in existing_aliases:
            conflicts.append(f"名称「{name}」与已有卡片的别名重复")
        states = CARD_TYPES[card_type].get("state_options") or []
        current_state = str(candidate.current_state or "").strip()
        if current_state and states and current_state not in states:
            warnings.append(f"候选状态「{current_state}」不在允许取值内，已保留原值")
        return {
            "type": card_type,
            "name": name,
            "aliases": [str(item) for item in (candidate.aliases or []) if str(item).strip()],
            "fields": fields,
            "current_state": current_state,
            "importance": int(candidate.importance or 3),
            "reason": str(candidate.reason or ""),
            "conflicts": conflicts,
            "source": "ai_generated",
        }

    # ------------------------------------------------------------------
    # AI 补全卡片
    # ------------------------------------------------------------------
    async def complete_card(self, request: Any) -> Dict[str, Any]:
        """补全一张已有卡片的空白字段。"""
        novel_id = request.novel_id
        card_id = str(getattr(request, "card_id", "") or "").strip()
        if not card_id:
            raise InvalidIdError("缺少 card_id")
        card = await self.card_repo.get_card_by_business_id(novel_id, card_id)
        card_type = self._validate_type(card.get("type", ""))

        kind = "complete_card"
        cached = await reuse_record(novel_id, kind, getattr(request, "request_id", "") or "")
        if cached:
            return cached

        context = await self._build_context(
            novel_id,
            getattr(request, "chapter_id", "") or "",
            getattr(request, "user_prompt", "") or "",
        )

        fields = card.get("fields") or {}
        empty_fields = [key for key, value in fields.items() if not str(value or "").strip()]
        locked = list(
            dict.fromkeys(
                list(DEFAULT_LOCKED_FIELDS) + list(getattr(request, "locked_fields", None) or [])
            )
        )

        if not empty_fields:
            # 没有空字段时不再消耗模型调用，直接给出明确结论
            return await save_record(
                novel_id,
                kind,
                chapter_id=getattr(request, "chapter_id", "") or "",
                card_id=card_id,
                request_id=getattr(request, "request_id", "") or "",
                snapshot=context.snapshot,
                data={"filled_fields": {}, "notes": "该卡片没有待补全的空字段", "conflicts": []},
                provider="",
                warnings=["该卡片没有待补全的空字段"],
                conflicts=[],
            )

        prompts = get_prompt_section("complete_setting_card")
        prompt = (
            prompts["complete_setting_card_prompt_base"].format(
                card_type_label=CARD_TYPES[card_type]["label"],
                card_type=card_type,
                context_text=context.text,
                card_json=self._card_json(card),
                empty_fields="\n".join(
                    f"- {key}：{CARD_TYPES[card_type]['fields'].get(key, key)}"
                    for key in empty_fields
                ),
                locked_fields="、".join(locked) if locked else "无",
                user_prompt=(getattr(request, "user_prompt", "") or "").strip() or "按已有设定合理补全",
            )
            + "\n"
            + prompts["complete_setting_card_prompt_without_schema_suffix"]
        ).strip()

        service, provider, use_json_schema, _stream = resolve_runtime(
            request, WORKFLOW_NAME, "complete_setting_card"
        )
        result = await generate_structured_result(
            service,
            prompt,
            CardCompleteResultSchema,
            "complete_setting_card",
            gen_kwargs=build_generation_kwargs(request),
            use_json_schema=use_json_schema,
        )

        warnings: List[str] = []
        filled: Dict[str, str] = {}
        for key, value in result.filled_fields.items():
            if key in locked:
                warnings.append(f"AI 返回了锁定字段 {key}，已忽略")
                continue
            if key not in empty_fields:
                warnings.append(f"字段 {key} 不是空字段，已忽略以免覆盖已有内容")
                continue
            text = str(value).strip()
            if text:
                filled[key] = text

        data = {"filled_fields": filled, "notes": result.notes, "conflicts": result.conflicts}
        return await save_record(
            novel_id,
            kind,
            chapter_id=getattr(request, "chapter_id", "") or "",
            card_id=card_id,
            request_id=getattr(request, "request_id", "") or "",
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=list(result.conflicts or []),
        )

    @staticmethod
    def _card_json(card: Dict[str, Any]) -> str:
        """渲染卡片当前内容为提示词可读文本。"""
        payload = {
            "card_id": card.get("card_id"),
            "type": card.get("type"),
            "name": card.get("name"),
            "aliases": card.get("aliases") or [],
            "current_state": card.get("current_state") or "",
            "importance": card.get("importance", 3),
            "fields": card.get("fields") or {},
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # 从小说内容提取卡片
    # ------------------------------------------------------------------
    async def extract_cards(self, request: Any) -> Dict[str, Any]:
        """从世界观、小说信息或章节正文中提取卡片候选。"""
        novel_id = request.novel_id
        kind = "extract_cards"
        cached = await reuse_record(novel_id, kind, getattr(request, "request_id", "") or "")
        if cached:
            return cached

        chapter_id = getattr(request, "chapter_id", "") or ""
        source_document = str(getattr(request, "source_document", "chapter") or "chapter")
        source_text = str(getattr(request, "source_text", "") or "").strip()
        if not source_text:
            if not chapter_id:
                raise InvalidIdError("请提供待提取的文本，或指定章节")
            chapter = await self.chapter_repo.get_chapter(chapter_id)
            source_text = str(chapter.get("content") or "").strip()
        if not source_text:
            raise InvalidIdError("待提取文本为空")

        context = await self._build_context(novel_id, chapter_id, getattr(request, "user_prompt", "") or "")
        existing = await self.card_repo.list_cards(novel_id)

        document_labels = {
            "worldview": "世界观",
            "novel_info": "小说基础信息",
            "chapter": "章节正文",
            "card": "已有设定卡",
        }
        prompts = get_prompt_section("extract_setting_cards")
        prompt = (
            prompts["extract_setting_cards_prompt_base"].format(
                source_document=document_labels.get(source_document, source_document),
                source_text=source_text[:60000],
                existing_cards_json=self._existing_cards_brief(existing),
            )
            + "\n"
            + prompts["extract_setting_cards_prompt_without_schema_suffix"]
        ).strip()

        service, provider, use_json_schema, _stream = resolve_runtime(
            request, WORKFLOW_NAME, "extract_setting_cards"
        )
        result = await generate_structured_result(
            service,
            prompt,
            CardExtractResultSchema,
            "extract_setting_cards",
            gen_kwargs=build_generation_kwargs(request),
            use_json_schema=use_json_schema,
        )

        warnings: List[str] = []
        known_card_ids = {str(card.get("card_id")) for card in existing}
        items: List[Dict[str, Any]] = []
        for item in result.items:
            card_type = str(item.type or "").strip().lower()
            if card_type not in CARD_TYPES:
                warnings.append(f"忽略了类型无法识别的提取项: {item.name or '未命名'}")
                continue
            if not str(item.name or "").strip():
                continue
            action = str(item.suggested_action or "create").strip().lower()
            if action not in {"create", "merge", "skip"}:
                action = "create"
            duplicate_of = str(item.duplicate_of or "").strip()
            if duplicate_of and duplicate_of not in known_card_ids:
                warnings.append(f"提取项「{item.name}」指向的合并目标不存在: {duplicate_of}")
                duplicate_of = ""
            if duplicate_of and action == "create":
                action = "merge"
            items.append(
                {
                    "type": card_type,
                    "name": str(item.name).strip(),
                    "aliases": [str(a) for a in (item.aliases or []) if str(a).strip()],
                    "fields": {
                        key: str(value)
                        for key, value in (item.fields or {}).items()
                        if key in CARD_TYPES[card_type]["fields"] and str(value).strip()
                    },
                    "evidence": str(item.evidence or ""),
                    "duplicate_of": duplicate_of,
                    "new_fields": [str(x) for x in (item.new_fields or []) if str(x).strip()],
                    "state_change": str(item.state_change or ""),
                    "confidence": float(item.confidence or 0.0),
                    "suggested_action": action,
                }
            )

        data = {"items": items, "notes": result.notes}
        return await save_record(
            novel_id,
            kind,
            chapter_id=chapter_id,
            request_id=getattr(request, "request_id", "") or "",
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[],
        )

    # ------------------------------------------------------------------
    # 冲突检查
    # ------------------------------------------------------------------
    async def check_conflicts(self, request: Any) -> Dict[str, Any]:
        """检查一张卡片与当前全书设定是否存在冲突。"""
        novel_id = request.novel_id
        card_id = str(getattr(request, "card_id", "") or "").strip()
        if not card_id:
            raise InvalidIdError("缺少 card_id")
        card = await self.card_repo.get_card_by_business_id(novel_id, card_id)
        card_type = self._validate_type(card.get("type", ""))

        kind = "check_card_conflicts"
        cached = await reuse_record(novel_id, kind, getattr(request, "request_id", "") or "")
        if cached:
            return cached

        context = await self._build_context(
            novel_id,
            getattr(request, "chapter_id", "") or "",
            getattr(request, "user_prompt", "") or "",
        )
        # 相关设定：同类型卡片 + 全部规则卡，避免能力越权与规则冲突
        related = [
            item
            for item in await self.card_repo.list_cards(novel_id)
            if item.get("card_id") != card_id
            and (item.get("type") == card_type or item.get("type") == "rule")
        ]

        prompts = get_prompt_section("check_card_conflicts")
        prompt = (
            prompts["check_card_conflicts_prompt_base"].format(
                card_type=card_type,
                card_json=self._card_json(card),
                related_cards_json=self._existing_cards_brief(related),
            )
            + "\n"
            + prompts["check_card_conflicts_prompt_without_schema_suffix"]
        ).strip()

        service, provider, use_json_schema, _stream = resolve_runtime(
            request, WORKFLOW_NAME, "check_card_conflicts"
        )
        result = await generate_structured_result(
            service,
            prompt,
            CardConflictResultSchema,
            "check_card_conflicts",
            gen_kwargs=build_generation_kwargs(request),
            use_json_schema=use_json_schema,
        )

        warnings: List[str] = []
        conflicts: List[Dict[str, Any]] = []
        known_card_ids = {str(c.get("card_id")) for c in await self.card_repo.list_cards(novel_id)}
        for item in result.conflicts:
            severity = str(item.severity or "").strip().lower()
            if severity not in CONFLICT_SEVERITIES:
                warnings.append(f"未知的冲突级别「{item.severity or '空'}」已按 warning 处理")
                severity = "warning"
            conflicts.append(
                {
                    "severity": severity,
                    "message": str(item.message or ""),
                    "related_card_ids": [
                        cid for cid in (item.related_card_ids or []) if cid in known_card_ids
                    ],
                    "suggestion": str(item.suggestion or ""),
                }
            )

        data = {"conflicts": conflicts, "notes": result.notes}
        return await save_record(
            novel_id,
            kind,
            chapter_id=getattr(request, "chapter_id", "") or "",
            card_id=card_id,
            request_id=getattr(request, "request_id", "") or "",
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[item["message"] for item in conflicts],
        )

    # ------------------------------------------------------------------
    # AI 改写卡片（模块2 新增）
    # ------------------------------------------------------------------
    async def rewrite_card(self, request: Any) -> Dict[str, Any]:
        """按用户指令改写一张已有卡片的指定字段，只产出字段级候选补丁。

        未指定的字段一律视为锁定字段；AI 返回的补丁中若出现锁定字段或未知字段，
        会在服务端被丢弃并记录告警，避免污染正式卡片。

        Args:
            request: 请求模型，需包含 novel_id、card_id、instruction、
                target_fields、chapter_id、request_id 等字段。

        Returns:
            包含 changed_fields / preserved_fields 的候选响应体。

        Raises:
            InvalidIdError: 缺少 card_id、卡片类型非法或没有可改写字段。
            NotFoundError: 卡片不存在或不属于该小说。
        """
        novel_id = request.novel_id
        card_id = str(getattr(request, "card_id", "") or "").strip()
        if not card_id:
            raise InvalidIdError("缺少 card_id")
        card = await self.card_repo.get_card_by_business_id(novel_id, card_id)
        card_type = self._validate_type(card.get("type", ""))

        allowed_fields = set(CARD_TYPES[card_type]["fields"].keys())
        target_fields = [
            str(item).strip()
            for item in (getattr(request, "target_fields", None) or [])
            if str(item).strip()
        ]
        unknown = [item for item in target_fields if item not in allowed_fields]
        if unknown:
            raise InvalidIdError(
                f"字段 {', '.join(unknown)} 不属于 {card_type} 卡的字段，无法改写"
            )

        # 默认保护 name / current_state，规则卡额外保护 is_hard_rule；
        # 只有用户显式声明 allow_locked_fields 时才放开，AI 无法自行解锁
        protected = set(DEFAULT_PROTECTED_FIELDS)
        if card_type == "rule":
            protected |= set(RULE_PROTECTED_FIELDS)
        if not bool(getattr(request, "allow_locked_fields", False)):
            blocked = [item for item in target_fields if item in protected]
            if blocked:
                raise InvalidIdError(
                    f"字段 {', '.join(blocked)} 受保护，需显式允许后才能改写"
                )
        target_fields = [item for item in target_fields if item not in protected or bool(getattr(request, "allow_locked_fields", False))]
        if not target_fields:
            raise InvalidIdError("改写卡片至少需要指定一个目标字段")

        kind = "rewrite_setting_card"
        cached = await reuse_record(novel_id, kind, getattr(request, "request_id", "") or "")
        if cached:
            return cached

        context = await self._build_context(
            novel_id,
            getattr(request, "chapter_id", "") or "",
            getattr(request, "instruction", "") or "",
        )
        locked_fields = sorted(allowed_fields - set(target_fields))
        prompts = get_prompt_section("rewrite_setting_card")
        prompt = (
            prompts["rewrite_setting_card_prompt_base"].format(
                card_type_label=CARD_TYPES[card_type]["label"],
                card_type=card_type,
                card_json=self._card_json(card),
                target_fields="、".join(target_fields),
                locked_fields="、".join(locked_fields) or "（无）",
                instruction=str(getattr(request, "instruction", "") or "").strip()
                or "请按当前设定补全并优化这些字段",
                context_text=context.text,
            )
            + "\n"
            + prompts["rewrite_setting_card_prompt_without_schema_suffix"]
        ).strip()

        service, provider, use_json_schema, _stream = resolve_runtime(
            request, WORKFLOW_NAME, "rewrite_setting_card"
        )
        result = await generate_structured_result(
            service,
            prompt,
            CardRewriteResultSchema,
            "rewrite_setting_card",
            gen_kwargs=build_generation_kwargs(request),
            use_json_schema=use_json_schema,
        )

        warnings: List[str] = []
        current_fields = card.get("fields") or {}
        changed_fields: List[Dict[str, Any]] = []
        for item in result.changed_fields:
            field = str(item.field or "").strip()
            if field not in target_fields:
                warnings.append(f"AI 改写了非目标字段 {field or '空'}，已忽略")
                continue
            new_value = str(item.new_value or "").strip()
            if not new_value:
                warnings.append(f"字段 {field} 的候选值为空，已忽略")
                continue
            changed_fields.append(
                {
                    "field": field,
                    "old_value": str(current_fields.get(field) or ""),
                    "new_value": new_value,
                    "reason": str(item.reason or ""),
                    "warnings": list(item.warnings or []),
                }
            )
        if not changed_fields:
            warnings.append("AI 未产出任何可应用的字段改写，请调整指令后重试")

        data = {
            "target_card_id": card_id,
            "target_card_type": card_type,
            "changed_fields": changed_fields,
            "preserved_fields": [
                str(item)
                for item in (result.preserved_fields or [])
                if str(item) in allowed_fields
            ],
            "notes": str(result.notes or ""),
        }
        return await save_record(
            novel_id,
            kind,
            chapter_id=getattr(request, "chapter_id", "") or "",
            card_id=card_id,
            request_id=getattr(request, "request_id", "") or "",
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[str(item) for item in (result.conflicts or [])],
            target_card_id=card_id,
            chapter_version=int(getattr(request, "chapter_version", 0) or 0) or None,
        )

    # ------------------------------------------------------------------
    # 合并预览（模块2 新增）
    # ------------------------------------------------------------------
    async def merge_preview(self, request: Any) -> Dict[str, Any]:
        """把抽取候选合并进已有卡片时，产出字段级差异预览。

        合并预览是确定性计算，不调用大模型：候选字段与卡片当前值逐字段比对，
        区分「字段有差异」与「新增字段」，供用户逐字段勾选后再正式合并。

        Args:
            request: 请求模型，需包含 novel_id、card_id 与 candidate 字段。

        Returns:
            包含 field_diffs / new_fields 的预览结果。

        Raises:
            InvalidIdError: 缺少 card_id 或卡片类型非法。
            NotFoundError: 卡片不存在或不属于该小说。
        """
        novel_id = request.novel_id
        card_id = str(getattr(request, "card_id", "") or "").strip()
        if not card_id:
            raise InvalidIdError("缺少 card_id")
        card = await self.card_repo.get_card_by_business_id(novel_id, card_id)
        card_type = self._validate_type(card.get("type", ""))
        allowed_fields = set(CARD_TYPES[card_type]["fields"].keys())

        candidate = getattr(request, "candidate", None) or {}
        candidate_fields = {
            str(key): str(value)
            for key, value in (candidate.get("fields") or {}).items()
            if str(key).strip() and str(value).strip() and key in allowed_fields
        }

        current_fields = card.get("fields") or {}
        field_diffs: List[Dict[str, Any]] = []
        new_fields: List[str] = []
        warnings: List[str] = []
        for field, value in candidate_fields.items():
            current_value = str(current_fields.get(field) or "").strip()
            if not current_value:
                new_fields.append(field)
                field_diffs.append(
                    {
                        "field": field,
                        "current_value": "",
                        "incoming_value": value,
                        # 空白字段默认勾选，有值字段默认不覆盖，避免误改用户已确认内容
                        "selected": True,
                    }
                )
            elif current_value != value:
                field_diffs.append(
                    {
                        "field": field,
                        "current_value": current_value,
                        "incoming_value": value,
                        "selected": False,
                    }
                )

        dropped = [
            str(key)
            for key in (candidate.get("fields") or {})
            if str(key).strip() and key not in allowed_fields
        ]
        if dropped:
            warnings.append(f"候选字段 {', '.join(dropped)} 不属于该类型，已忽略")

        return {
            "candidate_name": str(candidate.get("name") or ""),
            "target_card_id": card_id,
            "target_card_name": str(card.get("name") or ""),
            "field_diffs": field_diffs,
            "new_fields": new_fields,
            "warnings": warnings,
        }


setting_card_generation_service = SettingCardGenerationService()
