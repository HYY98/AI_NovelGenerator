"""章节 AI 生成与审校服务。

统一约定（技术指引第 19 节）：
1. 所有生成只产出候选，不修改正式章节内容；
2. 每次生成写入 generation_records，保存输入上下文版本快照；
3. 客户端 request_id 在相同小说与生成类型下幂等，重复提交直接返回既有候选；
4. AI 返回的实体 ID 必须能在当前小说中解析，否则降级为告警而不是直接写入。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.db.repositories.chapter_repository import ChapterRepository
from backend.db.repositories.novel_repository import novel_repo
from backend.llm.prompts.prompt_selector import get_prompt_section
from backend.llm.schemas.chapter_pydantic import (
    REVIEW_CATEGORIES,
    REVIEW_SEVERITIES,
    STATE_CHANGE_ENTITY_TYPES,
    ChapterCompressSchema,
    ChapterContinueSchema,
    ChapterDraftSchema,
    ChapterExpandSchema,
    ChapterPlanSchema,
    ChapterRewriteSchema,
    ConsistencyReviewSchema,
    StateChangeProposalResultSchema,
)
from backend.services.llm.chapter_context_service import ChapterContext, chapter_context_service
from backend.services.llm.generation_support import (
    provider_model,
    resolve_runtime,
    reuse_record,
    save_record,
)
from backend.db.errors import InvalidIdError, NotFoundError
from backend.services.llm.workflow_service import (
    build_generation_kwargs,
    generate_structured_result,
)

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "chapter_ai"

# 生成类型 -> 工作流步骤名 / 提示词分组名
KIND_TO_STEP: Dict[str, str] = {
    "chapter_plan": "chapter_plan",
    "chapter_draft": "chapter_draft",
    "chapter_continue": "chapter_continue",
    "chapter_rewrite": "chapter_rewrite",
    "chapter_expand": "chapter_expand",
    "chapter_compress": "chapter_compress",
    "consistency_review": "consistency_review",
    "state_change_proposal": "propose_state_changes",
}

# 生成类型 -> 结构化 Schema
KIND_TO_SCHEMA = {
    "chapter_plan": ChapterPlanSchema,
    "chapter_draft": ChapterDraftSchema,
    "chapter_continue": ChapterContinueSchema,
    "chapter_rewrite": ChapterRewriteSchema,
    "chapter_expand": ChapterExpandSchema,
    "chapter_compress": ChapterCompressSchema,
    "consistency_review": ConsistencyReviewSchema,
    "state_change_proposal": StateChangeProposalResultSchema,
}

# 正文类生成的默认目标字数
DEFAULT_TARGET_WORDS = 2500


def _dump(value: Any) -> str:
    """把任意结构序列化为可读 JSON 文本。"""
    return json.dumps(value, ensure_ascii=False, indent=2)


def _selection_text(selection: Any) -> str:
    """从选区载荷中取出待处理文本。"""
    if isinstance(selection, dict):
        return str(selection.get("text") or "")
    return ""


class ChapterGenerationService:
    """章节 AI 生成、审校与状态变更候选服务。"""

    def __init__(self) -> None:
        self.chapter_repo = ChapterRepository()

    # ------------------------------------------------------------------
    # 公共准备
    # ------------------------------------------------------------------
    async def load_chapter(self, chapter_id: str) -> Dict[str, Any]:
        """按章节 ObjectId 字符串读取章节。"""
        return await self.chapter_repo.get_chapter(chapter_id)

    async def load_novel(self, novel_id: str) -> Dict[str, Any]:
        """读取小说文档。"""
        return await novel_repo.get_novel_by_id(novel_id)

    async def prepare_context(
        self,
        request: Any,
        *,
        chapter: Optional[Dict[str, Any]] = None,
        include_core_cards: bool = True,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], ChapterContext]:
        """准备小说、章节与 AI 上下文。

        Args:
            request: 含 novel_id、chapter_id、user_prompt 的请求对象。
            chapter: 已读取的章节文档，避免重复查询。
            include_core_cards: 是否注入全书核心设定。

        Returns:
            小说文档、章节文档与上下文对象的元组。

        Raises:
            NotFoundError: 小说或章节不存在时抛出。
        """
        novel = await self.load_novel(request.novel_id)
        chapter = chapter or await self.load_chapter(request.chapter_id)
        if str(chapter.get("novel_id")) != str(novel.get("_id")):
            raise NotFoundError("章节不属于该小说")
        context = await chapter_context_service.build(
            novel,
            chapter,
            user_prompt=getattr(request, "user_prompt", "") or "",
            include_core_cards=include_core_cards,
        )
        return novel, chapter, context

    def _resolve_runtime(
        self,
        request: Any,
        kind: str,
    ) -> Tuple[LLMService, str, bool, bool]:
        """解析本次生成使用的 Provider、JSON Schema 与流式能力。

        Args:
            request: 含可选 provider 覆盖与 use_stream 的请求对象。
            kind: 生成类型。

        Returns:
            元组：LLMService、Provider 别名、是否使用原生 JSON Schema、是否流式。

        Raises:
            InvalidIdError: 指定的 Provider 不存在或未启用时抛出。
        """
        return resolve_runtime(request, WORKFLOW_NAME, KIND_TO_STEP[kind])

    @staticmethod
    def _provider_model(provider: str) -> str:
        """读取 Provider 的默认模型名，仅用于生成记录审计。"""
        return provider_model(provider)

    # ------------------------------------------------------------------
    # 生成记录
    # ------------------------------------------------------------------
    async def _reuse_record(
        self,
        novel_id: str,
        kind: str,
        request_id: str,
    ) -> Optional[Dict[str, Any]]:
        """相同 request_id 的生成已完成时直接复用候选，避免重复计费。"""
        return await reuse_record(novel_id, kind, request_id)

    async def _save_record(
        self,
        novel_id: str,
        kind: str,
        *,
        chapter_id: str,
        request_id: str,
        snapshot: Dict[str, Any],
        data: Any,
        provider: str,
        warnings: List[str],
        conflicts: List[str],
    ) -> Dict[str, Any]:
        """写入生成记录并返回候选响应体。"""
        return await save_record(
            novel_id,
            kind,
            chapter_id=chapter_id,
            request_id=request_id,
            snapshot=snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=conflicts,
        )

    # ------------------------------------------------------------------
    # 候选校验
    # ------------------------------------------------------------------
    @staticmethod
    def _filter_ids(ids: List[str], known: set, label: str, warnings: List[str]) -> List[str]:
        """过滤掉小说中不存在的业务 ID，并记录告警。"""
        valid: List[str] = []
        unknown: List[str] = []
        for item in ids:
            text = str(item).strip()
            if not text or text in valid:
                continue
            if text in known:
                valid.append(text)
            else:
                unknown.append(text)
        if unknown:
            warnings.append(f"{label} 中存在无法解析的业务 ID，已忽略: {', '.join(unknown)}")
        return valid

    def _normalize_draft(
        self,
        result: ChapterDraftSchema,
        context: ChapterContext,
        warnings: List[str],
    ) -> Dict[str, Any]:
        """校验并归一化章节正文候选。"""
        entities = result.used_entities
        used = {
            "characters": self._filter_ids(entities.characters, context.known_character_ids, "used_entities.characters", warnings),
            "locations": self._filter_ids(entities.locations, context.known_card_ids, "used_entities.locations", warnings),
            "items": self._filter_ids(entities.items, context.known_card_ids, "used_entities.items", warnings),
            "rules": self._filter_ids(entities.rules, context.known_card_ids, "used_entities.rules", warnings),
        }
        return {
            "title": result.title,
            "content": result.content,
            "summary": result.summary,
            "unresolved_threads": result.unresolved_threads,
            "used_entities": used,
            "state_change_proposals": [
                self._normalize_proposal(item.model_dump(), context, warnings)
                for item in result.state_change_proposals
            ],
        }

    @staticmethod
    def _normalize_proposal(
        proposal: Dict[str, Any],
        context: ChapterContext,
        warnings: List[str],
    ) -> Dict[str, Any]:
        """归一化单条状态变更候选。"""
        entity_type = str(proposal.get("entity_type") or "").strip().lower()
        if entity_type not in STATE_CHANGE_ENTITY_TYPES:
            warnings.append(f"忽略无法识别的状态变更实体类型: {entity_type or '空'}")
            entity_type = "other"
        entity_id = str(proposal.get("entity_id") or "").strip()
        if entity_id and entity_type == "character" and entity_id not in context.known_character_ids:
            warnings.append(f"状态变更引用了不存在的角色: {entity_id}")
        if entity_id and entity_type in {"location", "item", "rule"} and entity_id not in context.known_card_ids:
            warnings.append(f"状态变更引用了不存在的卡片: {entity_id}")
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "change_type": str(proposal.get("change_type") or "other").strip() or "other",
            "before": proposal.get("before") or {},
            "after": proposal.get("after") or {},
            "evidence": str(proposal.get("evidence") or ""),
            "confidence": float(proposal.get("confidence") or 0.0),
            "note": str(proposal.get("note") or ""),
        }

    @staticmethod
    def _normalize_review(
        result: ConsistencyReviewSchema,
        warnings: List[str],
    ) -> Dict[str, Any]:
        """归一化审校结果的严重级别与类别。"""
        issues: List[Dict[str, Any]] = []
        for issue in result.issues:
            severity = str(issue.severity or "").strip().lower()
            if severity not in REVIEW_SEVERITIES:
                warnings.append(f"未知的审校级别「{issue.severity or '空'}」已按 warning 处理")
                severity = "warning"
            category = str(issue.category or "").strip().lower()
            if category not in REVIEW_CATEGORIES:
                warnings.append(f"未知的审校类别「{issue.category or '空'}」已按 style 处理")
                category = "style"
            issues.append(
                {
                    "severity": severity,
                    "category": category,
                    "entity_type": str(issue.entity_type or ""),
                    "entity_id": str(issue.entity_id or ""),
                    "message": str(issue.message or ""),
                    "evidence": str(issue.evidence or ""),
                    "suggestion": str(issue.suggestion or ""),
                    # 阻断级问题不允许被用户忽略，否则「确认后定稿」会失去意义
                    "can_ignore": False if severity == "blocking" else bool(issue.can_ignore),
                }
            )
        return {
            "summary": result.summary,
            "issues": issues,
            "blocking_count": sum(1 for item in issues if item["severity"] == "blocking"),
        }

    # ------------------------------------------------------------------
    # 提示词构造
    # ------------------------------------------------------------------
    @staticmethod
    def _build_prompt(kind: str, context: ChapterContext, **extra: Any) -> str:
        """按生成类型组装完整提示词（base 格式化 + suffix 原文追加）。"""
        step = KIND_TO_STEP[kind]
        prompts = get_prompt_section(step)
        kwargs: Dict[str, Any] = {"context_text": context.text}
        kwargs.update(extra)
        base = prompts[f"{step}_prompt_base"].format(**kwargs)
        suffix = prompts[f"{step}_prompt_without_schema_suffix"]
        return f"{base}\n{suffix}".strip()

    # ------------------------------------------------------------------
    # 各生成类型
    # ------------------------------------------------------------------
    async def generate_plan(self, request: Any) -> Dict[str, Any]:
        """生成章节剧情方案候选。"""
        novel, chapter, context = await self.prepare_context(request)
        kind = "chapter_plan"
        cached = await self._reuse_record(request.novel_id, kind, getattr(request, "request_id", ""))
        if cached:
            return cached

        prompt = self._build_prompt(kind, context)
        data, provider, warnings = await self._run(kind, request, prompt, ChapterPlanSchema)
        return await self._save_record(
            request.novel_id,
            kind,
            chapter_id=context.chapter_id,
            request_id=getattr(request, "request_id", "") or "",
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[],
        )

    async def generate_draft(self, request: Any) -> Dict[str, Any]:
        """生成章节正文候选（不写入章节）。"""
        novel, chapter, context = await self.prepare_context(request)
        kind = "chapter_draft"
        cached = await self._reuse_record(request.novel_id, kind, getattr(request, "request_id", ""))
        if cached:
            return cached

        plan = getattr(request, "plan", None) or {}
        target_words = int(getattr(request, "target_words", 0) or 0) or self._default_words(novel)
        prompt = self._build_prompt(
            kind, context, plan_json=_dump(plan), target_words=target_words
        )
        data, provider, warnings = await self._run(kind, request, prompt, ChapterDraftSchema, context=context)
        return await self._save_record(
            request.novel_id,
            kind,
            chapter_id=context.chapter_id,
            request_id=getattr(request, "request_id", "") or "",
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[],
        )

    async def generate_continue(self, request: Any) -> Dict[str, Any]:
        """生成续写候选，只携带当前章节正文尾部。"""
        novel, chapter, context = await self.prepare_context(request)
        kind = "chapter_continue"
        cached = await self._reuse_record(request.novel_id, kind, getattr(request, "request_id", ""))
        if cached:
            return cached

        tail = (getattr(request, "tail_text", "") or "").strip() or context.content_tail
        if not tail:
            raise InvalidIdError("当前章节还没有正文，无法续写，请先使用「AI 生成本章」")
        target_words = int(getattr(request, "target_words", 0) or 0) or 1000
        prompt = self._build_prompt(
            kind,
            context,
            plan_json=_dump(getattr(request, "plan", None) or {}),
            tail_text=tail,
            target_words=target_words,
        )
        data, provider, warnings = await self._run(kind, request, prompt, ChapterContinueSchema)
        return await self._save_record(
            request.novel_id,
            kind,
            chapter_id=context.chapter_id,
            request_id=getattr(request, "request_id", "") or "",
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[],
        )

    async def rewrite_selection(self, request: Any) -> Dict[str, Any]:
        """改写选中片段候选。"""
        return await self._selection_kind(request, "chapter_rewrite", ChapterRewriteSchema, {})

    async def expand_selection(self, request: Any) -> Dict[str, Any]:
        """扩写选中片段候选。"""
        return await self._selection_kind(request, "chapter_expand", ChapterExpandSchema, {})

    async def compress_selection(self, request: Any) -> Dict[str, Any]:
        """压缩选中片段候选。"""
        return await self._selection_kind(request, "chapter_compress", ChapterCompressSchema, {})

    async def _selection_kind(
        self,
        request: Any,
        kind: str,
        schema: Any,
        extra: Dict[str, Any],
    ) -> Dict[str, Any]:
        """处理改写/扩写/压缩这类必须携带选区的生成类型。"""
        novel, chapter, context = await self.prepare_context(request)
        cached = await self._reuse_record(request.novel_id, kind, getattr(request, "request_id", ""))
        if cached:
            return cached

        selection = getattr(request, "selection", None) or {}
        text = _selection_text(selection).strip()
        if not text:
            raise InvalidIdError("缺少选中文本，无法执行该操作")
        locked = getattr(request, "locked_facts", None) or []
        prompt = self._build_prompt(
            kind,
            context,
            instruction=(getattr(request, "instruction", "") or "").strip() or "按最佳编辑判断处理",
            selection_text=text,
            locked_facts="、".join(locked) if locked else "无",
        )
        data, provider, warnings = await self._run(kind, request, prompt, schema)
        return await self._save_record(
            request.novel_id,
            kind,
            chapter_id=context.chapter_id,
            request_id=getattr(request, "request_id", "") or "",
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[],
        )

    async def review_chapter(self, request: Any) -> Dict[str, Any]:
        """一致性审校，返回结构化问题列表。"""
        novel, chapter, context = await self.prepare_context(request)
        kind = "consistency_review"
        content = (getattr(request, "content", "") or "").strip() or str(chapter.get("content") or "").strip()
        if not content:
            raise InvalidIdError("章节正文为空，无法审校")

        prompt = self._build_prompt(kind, context, content=content)
        data, provider, warnings = await self._run(
            kind, request, prompt, ConsistencyReviewSchema, context=context
        )
        return await self._save_record(
            request.novel_id,
            kind,
            chapter_id=context.chapter_id,
            request_id=getattr(request, "request_id", "") or "",
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[],
        )

    async def propose_state_changes(self, request: Any) -> Dict[str, Any]:
        """从正文中提取状态变更候选。"""
        novel, chapter, context = await self.prepare_context(request)
        kind = "state_change_proposal"
        content = (getattr(request, "content", "") or "").strip() or str(chapter.get("content") or "").strip()
        if not content:
            raise InvalidIdError("章节正文为空，无法提取状态变更")

        prompt = self._build_prompt(kind, context, content=content)
        data, provider, warnings = await self._run(
            kind, request, prompt, StateChangeProposalResultSchema, context=context
        )
        proposals = [
            self._normalize_proposal(item.model_dump(), context, warnings)
            for item in StateChangeProposalResultSchema.model_validate(data).proposals
        ]
        data = {"proposals": proposals, "summary": data.get("summary", "")}
        return await self._save_record(
            request.novel_id,
            kind,
            chapter_id=context.chapter_id,
            request_id=getattr(request, "request_id", "") or "",
            snapshot=context.snapshot,
            data=data,
            provider=provider,
            warnings=warnings,
            conflicts=[],
        )

    # ------------------------------------------------------------------
    # 执行与流式
    # ------------------------------------------------------------------
    async def _run(
        self,
        kind: str,
        request: Any,
        prompt: str,
        schema: Any,
        context: Optional[ChapterContext] = None,
    ) -> Tuple[Dict[str, Any], str, List[str]]:
        """执行一次结构化生成并归一化结果。"""
        service, provider, use_json_schema, _use_stream = self._resolve_runtime(request, kind)
        gen_kwargs = build_generation_kwargs(request)
        step = KIND_TO_STEP[kind]
        result = await generate_structured_result(
            service,
            prompt,
            schema,
            step,
            gen_kwargs=gen_kwargs,
            use_json_schema=use_json_schema,
        )
        warnings: List[str] = []
        data = self._normalize_result(kind, result, context, warnings)
        return data, provider, warnings

    def _normalize_result(
        self,
        kind: str,
        result: Any,
        context: Optional[ChapterContext],
        warnings: List[str],
    ) -> Dict[str, Any]:
        """按生成类型归一化结构化结果。"""
        if kind == "chapter_draft" and context is not None:
            return self._normalize_draft(result, context, warnings)
        if kind == "state_change_proposal" and context is not None:
            return {
                "proposals": [
                    self._normalize_proposal(item.model_dump(), context, warnings)
                    for item in result.proposals
                ],
                "summary": result.summary,
            }
        if kind == "consistency_review":
            return self._normalize_review(result, warnings)
        return result.model_dump()

    @staticmethod
    def _default_words(novel: Dict[str, Any]) -> int:
        """读取小说配置的每章字数，缺失时使用默认值。"""
        try:
            words = int(novel.get("words_per_chapter") or 0)
        except (TypeError, ValueError):
            words = 0
        return words if words > 0 else DEFAULT_TARGET_WORDS

chapter_generation_service = ChapterGenerationService()
