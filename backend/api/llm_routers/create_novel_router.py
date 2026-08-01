"""AI 创建小说路由：通过 4 步 LLM 管道从用户创意生成完整小说设定（SSE 流式状态推送）。"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, AsyncGenerator, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from backend.db.errors import InvalidIdError, NotFoundError
from backend.db.repositories.novel_repository import novel_repo
from backend.llm.config import get_llm_config, get_provider_config
from backend.services.llm.workflow_service import (
    build_generation_kwargs,
    generate_structured_result,
    get_llm_service_for_step,
    provider_supports_json_schema,
    resolve_provider_for_step,
    resolve_timeout_for_step,
    should_use_streaming,
    stream_structured_result_events,
)
from backend.services.llm.llm_service import LLMService
from backend.services.novel.faction_service import FactionService
from backend.llm.prompts.prompt_selector import (
    CORE_FACTIONS_PROMPT_NAME,
    REWRITE_NOVEL_FIELD_PROMPT_NAME,
    load_prompt_config,
)
from backend.llm.schemas.novel_pydantic import (
    ExpandIdeaSchema,
    ExtractIdeaSchema,
    CoreSeedSchema,
    CoreFactionsResultSchema,
    NovelMetaSchema,
)

router = APIRouter(prefix="/api/llm", tags=["llm"])

WORKFLOW_NAME = "create_novel_by_ai"
FACTIONS_WORKFLOW_NAME = "create_factions_by_ai"
CREATE_CORE_FACTIONS_STEP_NAME = "create_core_factions"
logger = logging.getLogger(__name__)

NovelRewriteFieldKey = Literal[
    "title",
    "subtitle",
    "genre",
    "tags",
    "plot",
    "core_idea",
    "tone",
    "target_audience",
    "introduction",
    "summary",
    "core_seed",
    "worldview",
    "writing_style",
    "narrative_pov",
    "era_background",
]

REWRITABLE_NOVEL_FIELDS: set[str] = {
    "title",
    "subtitle",
    "genre",
    "tags",
    "plot",
    "core_idea",
    "tone",
    "target_audience",
    "introduction",
    "summary",
    "core_seed",
    "worldview",
    "writing_style",
    "narrative_pov",
    "era_background",
}

REWRITE_CONTEXT_FIELDS: tuple[str, ...] = (
    "title",
    "subtitle",
    "genre",
    "tags",
    "tone",
    "target_audience",
    "core_idea",
    "core_seed",
    "writing_style",
    "narrative_pov",
    "era_background",
    "number_of_chapters",
    "words_per_chapter",
)

FIELD_LABELS: dict[str, str] = {
    "title": "标题",
    "subtitle": "副标题",
    "genre": "类型",
    "tags": "标签",
    "plot": "主线剧情",
    "core_idea": "核心创意",
    "tone": "基调",
    "target_audience": "目标读者",
    "introduction": "引言",
    "summary": "简介",
    "core_seed": "核心种子",
    "worldview": "世界观",
    "writing_style": "写作风格",
    "narrative_pov": "叙事视角",
    "era_background": "时代背景",
}

NARRATIVE_POV_VALUES: set[str] = {"第一人称", "第三人称有限视角", "全知视角"}
TAG_SPLIT_RE = re.compile(r"[\n,，、;；]+")


def _load_prompts() -> dict:
    """读取当前生效的 prompt 定义文件。"""
    return load_prompt_config()


def _check_json_schema_support(step_name: str, workflow_name: str = WORKFLOW_NAME) -> bool:
    """检查指定步骤对应的 Provider 是否支持 JSON Schema 输出。"""
    provider = resolve_provider_for_step(workflow_name, step_name) or ""
    return provider_supports_json_schema(provider)


def _sse_event(event: str, data: dict) -> str:
    """格式化一条 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_response(event_stream: AsyncGenerator[str, None]) -> StreamingResponse:
    """构造统一的 SSE 响应。
    Args:
        event_stream: 已格式化 SSE 文本的异步生成器。
    Returns:
        带禁用缓冲响应头的 StreamingResponse。
    """
    return StreamingResponse(
        event_stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _log_workflow_event(request_id: str, step: str, status: str, **details: object) -> None:
    logger.info(
        "[create_novel_by_ai] request_id=%s step=%s status=%s details=%s",
        request_id,
        step,
        status,
        details or {},
    )


AI_CREATE_STEP_ORDER: tuple[str, ...] = ("expand_idea", "extract_idea", "core_seed", "novel_meta")


class AICreateCachedSteps(BaseModel):
    """AI 创建小说流程的可复用步骤缓存。

    Args:
        expand_idea: 已完成的扩写完整剧情结果。
        extract_idea: 已完成的提炼创意结果。
        core_seed: 已完成的故事核心结果。
        novel_meta: 已完成的小说设定结果。

    Returns:
        请求体中的缓存步骤会被 Pydantic 校验为对应 schema 实例。
    """

    expand_idea: ExpandIdeaSchema | None = None
    extract_idea: ExtractIdeaSchema | None = None
    core_seed: CoreSeedSchema | None = None
    novel_meta: NovelMetaSchema | None = None


def _model_dump_or_none(model: BaseModel | None) -> dict[str, Any] | None:
    """把 Pydantic 模型转换为可序列化字典。

    Args:
        model: 可能为空的 Pydantic 模型实例。

    Returns:
        模型字典；为空时返回 None。
    """
    return model.model_dump() if model is not None else None


def _build_ai_create_result(
    expanded: ExpandIdeaSchema | None = None,
    idea: ExtractIdeaSchema | None = None,
    seed: CoreSeedSchema | None = None,
    meta: NovelMetaSchema | None = None,
) -> dict[str, Any]:
    """汇总 AI 创建小说流程中已经完成的步骤结果。

    Args:
        expanded: 扩写完整剧情步骤结果。
        idea: 提炼创意步骤结果。
        seed: 生成故事核心步骤结果。
        meta: 生成小说设定步骤结果。

    Returns:
        只包含非空步骤的结果字典，可作为 partial_result 或最终 result。
    """
    result: dict[str, Any] = {}
    if dumped := _model_dump_or_none(expanded):
        result["expand_idea"] = dumped
    if dumped := _model_dump_or_none(idea):
        result["extract_idea"] = dumped
    if dumped := _model_dump_or_none(seed):
        result["core_seed"] = dumped
    if dumped := _model_dump_or_none(meta):
        result["novel_meta"] = dumped
    return result


def _get_contiguous_cached_steps(cached_steps: AICreateCachedSteps | None) -> dict[str, BaseModel]:
    """读取从第一步开始连续存在的缓存步骤。

    Args:
        cached_steps: 前端传入的可选缓存步骤。

    Returns:
        只包含连续前缀的缓存字典；中间断档后的缓存会被忽略，避免错误续跑。
    """
    if cached_steps is None:
        return {}

    prefix: dict[str, BaseModel] = {}
    for step_name in AI_CREATE_STEP_ORDER:
        cached_value = getattr(cached_steps, step_name)
        if cached_value is None:
            break
        # 只信任连续前缀，后续步骤即使传入也会从断点重新生成。
        prefix[step_name] = cached_value
    return prefix


class AICreateNovelRequest(BaseModel):
    user_idea: str
    number_of_chapters: int = 100
    words_per_chapter: int = 3000
    cached_steps: AICreateCachedSteps | None = None
    use_stream: bool = True
    # 可选生成参数，前端传入时覆盖 provider 级别默认值
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    system_prompt: str | None = Field(default=None)


class NovelRewriteChatMessage(BaseModel):
    """单条创建态字段改写对话消息。"""

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=8000)


class NovelFieldRewriteRequest(BaseModel):
    """创建态字段改写请求。"""

    provider: str = Field(..., min_length=1)
    target_field: NovelRewriteFieldKey
    instruction: str = Field(..., min_length=1, max_length=4000)
    current_value: str | list[str] = ""
    context: dict[str, Any] = Field(default_factory=dict)
    chat_history: list[NovelRewriteChatMessage] = Field(default_factory=list)
    use_stream: bool = True


class NovelFieldRewriteResult(BaseModel):
    """创建态字段改写结果。"""

    target_field: NovelRewriteFieldKey
    value: str | list[str]


class ExistingFactionRelationTarget(BaseModel):
    """单个已有阵营关系目标配置。"""

    faction_name: str = Field(..., min_length=1, max_length=80)
    relation_score: int = Field(..., ge=0, le=10)


class GenerateCoreFactionsRequest(BaseModel):
    """基于已保存小说生成核心阵营预览的请求。"""

    novel_id: str = Field(..., min_length=1)
    faction_count: int = Field(default=3, ge=1, le=6)
    independent_faction_count: int = Field(default=0, ge=0, le=6)
    user_guidance: str = Field(default="", max_length=2000)
    single_faction_integrity_score: int | None = Field(default=None, ge=0, le=10)
    connect_to_existing: bool = Field(default=False)
    existing_relation_targets: list[ExistingFactionRelationTarget] = Field(default_factory=list, max_length=12)
    existing_relation_score: int | None = Field(default=None, ge=0, le=10)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    system_prompt: str | None = Field(default=None)
    use_stream: bool = True

    @model_validator(mode="after")
    def validate_generation_counts(self) -> "GenerateCoreFactionsRequest":
        """校验核心阵营生成数量之间的约束。

        Args:
            无。

        Returns:
            校验通过后的请求模型。

        Raises:
            ValueError: 完全独立势力数量大于本次生成总数时抛出。
        """
        if self.independent_faction_count > self.faction_count:
            raise ValueError("完全独立势力数量不能大于本次创建势力数量")
        target_names = [target.faction_name.strip() for target in self.existing_relation_targets if target.faction_name.strip()]
        if len(target_names) != len(set(target_names)):
            raise ValueError("需要产生联系的已有阵营不能重复")
        return self


def _validate_rewrite_provider(provider: str):
    """校验指定 Provider 能否用于创建态字段改写。

    Args:
        provider: 前端选择的 Provider 别名。

    Returns:
        已解析的 Provider 配置。

    Raises:
        HTTPException: Provider 不存在或未启用时抛出 400。
    """
    alias = provider.strip()
    llm_cfg = get_llm_config()
    if alias not in llm_cfg.providers:
        raise HTTPException(status_code=400, detail=f"Provider 不存在: {alias}")

    provider_config = get_provider_config(alias)
    if not provider_config.enabled:
        raise HTTPException(status_code=400, detail=f"Provider 未启用: {alias}")

    return provider_config


def _format_rewrite_value(value: str | list[str]) -> str:
    """将字段值格式化为提示词中的可读文本。

    Args:
        value: 当前字段值，标签字段可能是字符串列表。

    Returns:
        可直接放入提示词的文本。
    """
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _compact_rewrite_context(context: dict[str, Any], target_field: str) -> dict[str, Any]:
    """过滤创建草稿上下文，只保留用户可见且非目标字段的小说创建字段。

    Args:
        context: 前端提交的完整创建草稿上下文。
        target_field: 当前正在改写的目标字段。

    Returns:
        供 LLM 参考的上下文字典。
    """
    compact: dict[str, Any] = {}
    for field in REWRITE_CONTEXT_FIELDS:
        # 目标字段已经通过 current_value 独立传入，避免同一长文本在 prompt 中重复出现。
        if field != target_field and field in context:
            compact[field] = context[field]
    return compact


def _format_rewrite_history(history: list[NovelRewriteChatMessage]) -> str:
    """将字段历史对话压缩成提示词片段。

    Args:
        history: 当前目标字段的历史消息列表。

    Returns:
        可读的历史对话文本；无历史时返回占位说明。
    """
    if not history:
        return "无"

    lines: list[str] = []
    for message in history[-12:]:
        role_label = "用户" if message.role == "user" else "AI"
        lines.append(f"{role_label}: {message.content.strip()}")
    return "\n".join(lines)


def _build_rewrite_prompt(req: NovelFieldRewriteRequest, *, use_json_schema: bool) -> str:
    """构造创建态字段改写提示词。

    Args:
        req: 字段改写请求模型。
        use_json_schema: 当前 Provider 是否支持结构化输出。

    Returns:
        发送给 LLM 的完整提示词。
    """
    prompts = _load_prompts().get(REWRITE_NOVEL_FIELD_PROMPT_NAME, {})
    field_label = FIELD_LABELS[req.target_field]
    context_json = json.dumps(
        _compact_rewrite_context(req.context, req.target_field),
        ensure_ascii=False,
        indent=2,
    )
    current_value = _format_rewrite_value(req.current_value)
    history_text = _format_rewrite_history(req.chat_history)
    suffix_key = (
        "rewrite_novel_field_prompt_with_schema_suffix"
        if use_json_schema
        else "rewrite_novel_field_prompt_without_schema_suffix"
    )

    prompt_base = prompts["rewrite_novel_field_prompt_base"].format(
        target_field_label=field_label,
        target_field=req.target_field,
        instruction=req.instruction.strip(),
        current_value=current_value or "无",
        context_json=context_json,
        history_text=history_text,
    )
    prompt_suffix = prompts[suffix_key].format(target_field=req.target_field)
    return f"{prompt_base}\n{prompt_suffix}".strip()


def _normalize_rewrite_value(target_field: NovelRewriteFieldKey, value: str | list[str]) -> str | list[str]:
    """归一化 LLM 返回的字段值。

    Args:
        target_field: 当前改写目标字段。
        value: LLM 返回的原始字段值。

    Returns:
        可直接返回给前端并写入表单的字段值。

    Raises:
        ValueError: 返回值为空或不满足字段约束时抛出。
    """
    if target_field not in REWRITABLE_NOVEL_FIELDS:
        raise ValueError(f"不支持改写字段: {target_field}")

    if target_field == "tags":
        raw_items = value if isinstance(value, list) else TAG_SPLIT_RE.split(str(value))
        tags: list[str] = []
        for item in raw_items:
            tag = str(item).strip()
            if tag and tag not in tags:
                tags.append(tag)
        if not tags:
            raise ValueError("标签改写结果不能为空")
        return tags[:8]

    if isinstance(value, list):
        text = "\n".join(str(item).strip() for item in value if str(item).strip())
    else:
        text = str(value).strip()

    if not text:
        raise ValueError(f"{FIELD_LABELS[target_field]}改写结果不能为空")

    if target_field == "narrative_pov" and text not in NARRATIVE_POV_VALUES:
        allowed = "、".join(sorted(NARRATIVE_POV_VALUES))
        raise ValueError(f"叙事视角只能为: {allowed}")

    return text


def _normalize_rewrite_result(
    target_field: NovelRewriteFieldKey,
    result: NovelFieldRewriteResult,
) -> NovelFieldRewriteResult:
    """校验并归一化完整改写结果。

    Args:
        target_field: 请求中的目标字段。
        result: LLM 返回并解析后的改写结果。

    Returns:
        字段一致且值已归一化的改写结果。

    Raises:
        ValueError: 字段不一致或字段值非法时抛出。
    """
    if result.target_field != target_field:
        raise ValueError(f"AI 返回字段不一致: {result.target_field}")

    return NovelFieldRewriteResult(
        target_field=target_field,
        value=_normalize_rewrite_value(target_field, result.value),
    )


def _safe_novel_text(novel: dict[str, Any], field: str, fallback: str = "未提供") -> str:
    """读取小说字段并转换为适合提示词的文本。

    Args:
        novel: 已落库小说文档。
        field: 字段名。
        fallback: 字段为空时使用的占位文本。

    Returns:
        可放入提示词的字符串。
    """
    value = novel.get(field)
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip()) or fallback
    text = str(value or "").strip()
    return text or fallback


def _compact_existing_core_factions(factions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """压缩已有核心阵营为提示词可读的低噪声结构。

    Args:
        factions: 数据库中已保存且未删除的核心阵营列表。

    Returns:
        只保留名称、类型和主线相关信息的阵营摘要列表。
    """
    compacted: list[dict[str, Any]] = []
    for faction in factions:
        compacted.append(
            {
                "name": faction.get("name", ""),
                "faction_type": faction.get("faction_type", ""),
                "positioning": faction.get("positioning", ""),
                "public_stance": faction.get("public_stance", ""),
                "core_goal": faction.get("core_goal", ""),
                "hidden_goal": faction.get("hidden_goal", ""),
                "influence_scope": faction.get("influence_scope", ""),
                "tags": faction.get("tags", []),
            }
        )
    return compacted


def _describe_integrity_score(score: int) -> str:
    """解释单个阵营正直度滑块的生成含义。

    Args:
        score: 0 到 10 的正直度分值。

    Returns:
        可直接放入提示词的正直度说明。
    """
    negative_feature_count = max(0, 10 - score)
    if score == 0:
        stance = "绝对负面，可设计为扁平化反派势力，核心特征全部服务压迫、掠夺或破坏。"
    elif score == 10:
        stance = "绝对正面，可设计为扁平化正派势力，不安排明显负面特征。"
    elif score < 5:
        stance = "整体偏负面，但需要保留少量可理解动机或秩序逻辑。"
    elif score > 5:
        stance = "整体偏正面，但需要安排相对负面的代价、盲区或内部问题。"
    else:
        stance = "正负面相对均衡，需要同时具备建设性目标和明显问题。"
    return f"正直度为 {score}/10。{stance} 相对负面特征数量参考 {negative_feature_count} 项。"


def _describe_relation_score(score: int) -> str:
    """解释单个阵营与已有阵营关系滑块的生成含义。

    Args:
        score: 0 到 10 的关系量化分值。

    Returns:
        可直接放入提示词的关系倾向说明。
    """
    if score == 0:
        return "关系量化为 0/10：应设计为绝对决裂或不可调和敌对。"
    if score == 10:
        return "关系量化为 10/10：应设计为高度互信、稳定伙伴或同盟。"
    if score < 5:
        return f"关系量化为 {score}/10：关系偏差，越接近 0 越应体现敌意、竞争、背叛风险或旧怨。"
    if score > 5:
        return f"关系量化为 {score}/10：关系偏好，越接近 10 越应体现合作、利益绑定或共同目标。"
    return "关系量化为 5/10：关系中性或摇摆，合作与冲突并存。"


def _format_existing_relation_targets(targets: list[ExistingFactionRelationTarget]) -> str:
    """格式化用户手动选择的已有阵营关系目标。

    Args:
        targets: 已有阵营名称与独立关系分值配置列表。

    Returns:
        可放入提示词的多行目标说明。
    """
    lines: list[str] = []
    for target in targets:
        faction_name = target.faction_name.strip()
        if faction_name:
            lines.append(f"{faction_name}: {_describe_relation_score(target.relation_score)}")
    return "\n".join(lines)


def _build_core_factions_generation_strategy(
    req: GenerateCoreFactionsRequest,
    existing_core_factions: list[dict[str, Any]],
) -> str:
    """构造核心阵营追加生成策略说明。

    Args:
        req: 前端提交的核心阵营生成参数。
        existing_core_factions: 数据库中已保存且未删除的核心阵营列表。

    Returns:
        可拼接进提示词的生成约束说明。
    """
    independent_count = req.independent_faction_count
    user_guidance = req.user_guidance.strip() or "无"
    has_existing = len(existing_core_factions) > 0
    lines = [
        f"本次必须只生成 {req.faction_count} 个全新的核心阵营。",
        "已有核心阵营只作为上下文和关系端点参考，禁止把已有阵营重复放入 core_factions。",
        "新阵营名称不得与已有核心阵营重复。",
        "每条新关系至少要连接一个本次生成的新阵营；可以连接新阵营和已有阵营，也可以连接两个本次新阵营。",
        f"用户指导: {user_guidance}",
    ]

    if independent_count > 0:
        lines.append(
            f"其中 {independent_count} 个新阵营必须是完全独立势力，不与本次其他阵营或已有阵营产生 faction_relations。"
        )
    else:
        lines.append("本次没有强制完全独立势力，非独立新阵营应优先设计可写入的阵营关系。")

    if req.faction_count == 1:
        integrity_score = req.single_faction_integrity_score if req.single_faction_integrity_score is not None else 5
        lines.append(_describe_integrity_score(integrity_score))
        can_connect_to_existing = independent_count == 0 and req.connect_to_existing and has_existing
        if can_connect_to_existing:
            formatted_targets = _format_existing_relation_targets(req.existing_relation_targets)
            if formatted_targets:
                lines.append("这个单个新阵营需要分别与以下用户选定的已有核心阵营产生关系，并按每个阵营的独立分值设计关系。")
                lines.append(formatted_targets)
            else:
                relation_score = req.existing_relation_score if req.existing_relation_score is not None else 5
                lines.append("这个单个新阵营需要与至少一个已有核心阵营产生关系。")
                lines.append(_describe_relation_score(relation_score))
        else:
            lines.append("这个单个新阵营不强制与已有阵营建立关系，必要时 faction_relations 可以为空数组。")
    elif has_existing:
        lines.append("已有核心阵营存在时，非独立新阵营可以优先与已有核心阵营建立关系，补足追加生成的连贯性。")
    else:
        lines.append("当前没有已有核心阵营，关系只能在本次新生成阵营之间建立。")

    return "\n".join(f"- {line}" for line in lines)


def _build_core_factions_prompt(
    novel: dict[str, Any],
    req: GenerateCoreFactionsRequest,
    existing_core_factions: list[dict[str, Any]],
    *,
    use_json_schema: bool,
) -> str:
    """构造全书核心阵营生成提示词。

    Args:
        novel: 已落库小说文档。
        req: 前端提交的核心阵营生成参数。
        existing_core_factions: 数据库中已保存且未删除的核心阵营列表。
        use_json_schema: 当前 Provider 是否支持结构化输出。

    Returns:
        发送给 LLM 的完整提示词。
    """
    prompts = _load_prompts().get(CORE_FACTIONS_PROMPT_NAME, {})
    suffix_key = (
        "create_core_factions_prompt_with_schema_suffix"
        if use_json_schema
        else "create_core_factions_prompt_without_schema_suffix"
    )
    tags = novel.get("tags") if isinstance(novel.get("tags"), list) else []
    prompt_base = prompts["create_core_factions_prompt_base"].format(
        plot=_safe_novel_text(novel, "plot"),
        genre=_safe_novel_text(novel, "genre", "未分类"),
        tone=_safe_novel_text(novel, "tone"),
        target_audience=_safe_novel_text(novel, "target_audience"),
        core_idea=_safe_novel_text(novel, "core_idea"),
        number_of_chapters=novel.get("number_of_chapters") or 100,
        words_per_chapter=novel.get("words_per_chapter") or 3000,
        core_seed=_safe_novel_text(novel, "core_seed"),
        title=_safe_novel_text(novel, "title"),
        summary=_safe_novel_text(novel, "summary"),
        worldview=_safe_novel_text(novel, "worldview"),
        writing_style=_safe_novel_text(novel, "writing_style"),
        narrative_pov=_safe_novel_text(novel, "narrative_pov"),
        era_background=_safe_novel_text(novel, "era_background"),
        tags_json=json.dumps(tags, ensure_ascii=False),
        existing_core_factions_json=json.dumps(
            _compact_existing_core_factions(existing_core_factions),
            ensure_ascii=False,
            indent=2,
        ),
        faction_count=req.faction_count,
        generation_strategy=_build_core_factions_generation_strategy(req, existing_core_factions),
    )
    return f"{prompt_base}\n{prompts[suffix_key]}".strip()


async def _prepare_core_factions_generation(
    req: GenerateCoreFactionsRequest,
) -> tuple[dict[str, Any], list[dict[str, Any]], str, int | None, bool, dict[str, Any]]:
    """准备核心阵营生成所需的小说、Provider 与提示词参数。
    Args:
        req: 前端提交的核心阵营生成请求。
    Returns:
        小说文档、已有核心阵营、Provider 别名、步骤超时、JSON Schema 能力和生成参数。
    Raises:
        HTTPException: 小说不存在、ID 非法或指定的已有阵营不存在时抛出。
    """
    try:
        novel = await novel_repo.get_novel_by_id(req.novel_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    existing_core_factions = await FactionService.get_factions_by_level_type(req.novel_id, "core")
    existing_names = {str(faction.get("name", "")).strip() for faction in existing_core_factions}
    unknown_targets = [
        target.faction_name.strip()
        for target in req.existing_relation_targets
        if target.faction_name.strip() and target.faction_name.strip() not in existing_names
    ]
    if unknown_targets:
        raise HTTPException(status_code=400, detail=f"指定的已有阵营不存在: {', '.join(unknown_targets)}")

    step_name = CREATE_CORE_FACTIONS_STEP_NAME
    return (
        novel,
        existing_core_factions,
        resolve_provider_for_step(FACTIONS_WORKFLOW_NAME, step_name) or "",
        resolve_timeout_for_step(FACTIONS_WORKFLOW_NAME, step_name),
        _check_json_schema_support(step_name, FACTIONS_WORKFLOW_NAME),
        build_generation_kwargs(req),
    )


async def _generate_core_factions_result(req: GenerateCoreFactionsRequest) -> CoreFactionsResultSchema:
    """执行非流式核心阵营生成并返回已校验结果。
    Args:
        req: 核心阵营生成请求。
    Returns:
        已通过 CoreFactionsResultSchema 校验的生成结果。
    """
    (
        novel,
        existing_core_factions,
        _provider,
        _timeout_seconds,
        use_json_schema,
        gen_kwargs,
    ) = await _prepare_core_factions_generation(req)
    prompt = _build_core_factions_prompt(
        novel,
        req,
        existing_core_factions,
        use_json_schema=use_json_schema,
    )
    service = get_llm_service_for_step(FACTIONS_WORKFLOW_NAME, CREATE_CORE_FACTIONS_STEP_NAME)
    return await generate_structured_result(
        service,
        prompt,
        CoreFactionsResultSchema,
        CREATE_CORE_FACTIONS_STEP_NAME,
        gen_kwargs=gen_kwargs,
        use_json_schema=use_json_schema,
    )


async def _rewrite_novel_field_result(req: NovelFieldRewriteRequest) -> NovelFieldRewriteResult:
    """执行非流式小说字段改写并返回归一化结果。
    Args:
        req: 前端提交的字段改写请求。
    Returns:
        字段一致且字段值已归一化的改写结果。
    """
    provider_config = _validate_rewrite_provider(req.provider)
    prompt = _build_rewrite_prompt(req, use_json_schema=provider_config.supports_json_schema)
    service = LLMService(provider_name=req.provider)
    raw_result = await generate_structured_result(
        service,
        prompt,
        NovelFieldRewriteResult,
        "rewrite_novel_field",
        use_json_schema=provider_config.supports_json_schema,
    )
    return _normalize_rewrite_result(req.target_field, raw_result)


@router.post("/generate-core-factions")
async def generate_core_factions(req: GenerateCoreFactionsRequest):
    """基于已保存小说信息生成核心阵营预览，不写入数据库。

    Args:
        req: 生成核心阵营的请求参数。

    Returns:
        包含 core_factions 与 faction_relations 的预览结果。
    """
    request_id = uuid4().hex[:8]
    step_name = CREATE_CORE_FACTIONS_STEP_NAME
    provider = resolve_provider_for_step(FACTIONS_WORKFLOW_NAME, step_name) or ""
    timeout_seconds = resolve_timeout_for_step(FACTIONS_WORKFLOW_NAME, step_name)

    logger.info(
        "[generate_core_factions] request_id=%s novel_id=%s provider=%s count=%s",
        request_id,
        req.novel_id,
        provider or "unresolved",
        req.faction_count,
    )

    try:
        result = await _generate_core_factions_result(req)
        return result.model_dump()
    except HTTPException:
        # 准备阶段已将小说不存在、非法 ID 和未知阵营映射为明确的客户端错误。
        raise
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning(
            "[generate_core_factions] request_id=%s invalid_result=%s",
            request_id,
            exc,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "[generate_core_factions] request_id=%s failed provider=%s timeout=%s",
            request_id,
            provider or "unresolved",
            timeout_seconds,
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/generate-core-factions/stream")
async def generate_core_factions_stream(req: GenerateCoreFactionsRequest):
    """通过 SSE 生成核心阵营预览，支持 Provider 流式保活。
    Args:
        req: 核心阵营生成请求，use_stream 为 true 且 Provider 支持时启用流式接口。
    Returns:
        SSE 响应；progress 事件只表示保活进度，done 事件返回最终结构化结果。
    """
    (
        novel,
        existing_core_factions,
        provider,
        timeout_seconds,
        use_json_schema,
        gen_kwargs,
    ) = await _prepare_core_factions_generation(req)
    request_id = uuid4().hex[:8]
    should_stream = should_use_streaming(req, provider)
    service = get_llm_service_for_step(FACTIONS_WORKFLOW_NAME, CREATE_CORE_FACTIONS_STEP_NAME)
    prompt = _build_core_factions_prompt(
        novel,
        req,
        existing_core_factions,
        use_json_schema=False if should_stream else use_json_schema,
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        logger.info(
            "[generate_core_factions_stream] request_id=%s provider=%s stream=%s timeout=%s",
            request_id,
            provider or "unresolved",
            should_stream,
            timeout_seconds,
        )
        try:
            if should_stream:
                async for event in stream_structured_result_events(
                    service,
                    prompt,
                    CoreFactionsResultSchema,
                    CREATE_CORE_FACTIONS_STEP_NAME,
                    gen_kwargs=gen_kwargs,
                ):
                    if event["status"] == "progress":
                        yield _sse_event(
                            "progress",
                            {
                                "status": "progress",
                                "step": CREATE_CORE_FACTIONS_STEP_NAME,
                                "chunk_count": event["chunk_count"],
                                "characters": event["characters"],
                            },
                        )
                    else:
                        parsed_result = CoreFactionsResultSchema.model_validate(event["result"].model_dump())
                        yield _sse_event("done", {"success": True, "result": parsed_result.model_dump()})
                return

            raw_result = await generate_structured_result(
                service,
                prompt,
                CoreFactionsResultSchema,
                CREATE_CORE_FACTIONS_STEP_NAME,
                gen_kwargs=gen_kwargs,
                use_json_schema=use_json_schema,
            )
            parsed_result = CoreFactionsResultSchema.model_validate(raw_result.model_dump())
            yield _sse_event("done", {"success": True, "result": parsed_result.model_dump()})
        except ValueError as exc:
            logger.warning(
                "[generate_core_factions_stream] request_id=%s invalid_result=%s",
                request_id,
                exc,
            )
            yield _sse_event("error", {"success": False, "status_code": 502, "error": str(exc)})
        except Exception as exc:
            logger.exception(
                "[generate_core_factions_stream] request_id=%s failed provider=%s",
                request_id,
                provider or "unresolved",
            )
            yield _sse_event("error", {"success": False, "status_code": 500, "error": str(exc)})

    return _sse_response(event_stream())


@router.post("/rewrite-novel-field")
async def rewrite_novel_field(req: NovelFieldRewriteRequest):
    """使用指定 Provider 改写创建态小说信息中的单个字段。

    Args:
        req: 前端提交的字段改写请求。

    Returns:
        包含目标字段和改写后字段值的响应字典。
    """
    provider_config = _validate_rewrite_provider(req.provider)
    request_id = uuid4().hex[:8]
    logger.info(
        "[rewrite_novel_field] request_id=%s provider=%s field=%s json_schema=%s",
        request_id,
        req.provider,
        req.target_field,
        provider_config.supports_json_schema,
    )

    try:
        normalized_result = await _rewrite_novel_field_result(req)
        return normalized_result.model_dump()
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning(
            "[rewrite_novel_field] request_id=%s invalid_result=%s",
            request_id,
            exc,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "[rewrite_novel_field] request_id=%s failed provider=%s field=%s",
            request_id,
            req.provider,
            req.target_field,
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/rewrite-novel-field/stream")
async def rewrite_novel_field_stream(req: NovelFieldRewriteRequest):
    """通过 SSE 改写创建态小说字段，支持 Provider 流式保活。
    Args:
        req: 字段改写请求，use_stream 为 true 且 Provider 支持时启用流式接口。
    Returns:
        SSE 响应；progress 事件只表示保活进度，done 事件返回最终字段改写结果。
    """
    provider_config = _validate_rewrite_provider(req.provider)
    request_id = uuid4().hex[:8]
    should_stream = should_use_streaming(req, req.provider)
    prompt = _build_rewrite_prompt(
        req,
        use_json_schema=False if should_stream else provider_config.supports_json_schema,
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        logger.info(
            "[rewrite_novel_field_stream] request_id=%s provider=%s field=%s stream=%s",
            request_id,
            req.provider,
            req.target_field,
            should_stream,
        )
        try:
            service = LLMService(provider_name=req.provider)
            if should_stream:
                async for event in stream_structured_result_events(
                    service,
                    prompt,
                    NovelFieldRewriteResult,
                    "rewrite_novel_field",
                ):
                    if event["status"] == "progress":
                        yield _sse_event(
                            "progress",
                            {
                                "status": "progress",
                                "field": req.target_field,
                                "chunk_count": event["chunk_count"],
                                "characters": event["characters"],
                            },
                        )
                    else:
                        parsed_result = NovelFieldRewriteResult.model_validate(event["result"].model_dump())
                        normalized_result = _normalize_rewrite_result(req.target_field, parsed_result)
                        yield _sse_event("done", {"success": True, "result": normalized_result.model_dump()})
                return

            raw_result = await generate_structured_result(
                service,
                prompt,
                NovelFieldRewriteResult,
                "rewrite_novel_field",
                use_json_schema=provider_config.supports_json_schema,
            )
            normalized_result = _normalize_rewrite_result(req.target_field, raw_result)
            yield _sse_event("done", {"success": True, "result": normalized_result.model_dump()})
        except ValueError as exc:
            logger.warning(
                "[rewrite_novel_field_stream] request_id=%s invalid_result=%s",
                request_id,
                exc,
            )
            yield _sse_event("error", {"success": False, "status_code": 502, "error": str(exc)})
        except Exception as exc:
            logger.exception(
                "[rewrite_novel_field_stream] request_id=%s failed provider=%s field=%s",
                request_id,
                req.provider,
                req.target_field,
            )
            yield _sse_event("error", {"success": False, "status_code": 500, "error": str(exc)})

    return _sse_response(event_stream())


@router.post("/create-novel-by-ai")
async def create_novel_by_ai(req: AICreateNovelRequest):
    """4 步 LLM 管道（SSE 流式）：expand_idea → extract_idea → core_seed → novel_meta。

    Args:
        req: AI 创建小说请求，允许携带从第一步开始连续完成的 cached_steps。

    Returns:
        SSE 响应；每一步通过 step 事件推送，结束时通过 done 事件返回结果或部分结果。
    """

    async def event_stream() -> AsyncGenerator[str, None]:
        request_id = uuid4().hex[:8]
        workflow_start = time.perf_counter()
        prompts = _load_prompts().get("create_novel_by_ai", {})
        gen_kwargs = build_generation_kwargs(req)
        cached_prefix = _get_contiguous_cached_steps(req.cached_steps)
        expanded: ExpandIdeaSchema | None = None
        idea: ExtractIdeaSchema | None = None
        seed: CoreSeedSchema | None = None
        meta: NovelMetaSchema | None = None
        _log_workflow_event(
            request_id,
            "workflow",
            "started",
            idea_chars=len(req.user_idea),
            number_of_chapters=req.number_of_chapters,
            words_per_chapter=req.words_per_chapter,
            cached_steps=list(cached_prefix.keys()),
            overrides=sorted(gen_kwargs.keys()),
        )

        # Step 1: Expand Idea to Full Novel Story
        if cached_expanded := cached_prefix.get("expand_idea"):
            expanded = cached_expanded  # type: ignore[assignment]
            _log_workflow_event(request_id, "expand_idea", "cached")
            yield _sse_event("step", {"step": "expand_idea", "status": "done", "cached": True, "data": expanded.model_dump()})
        else:
            yield _sse_event("step", {"step": "expand_idea", "status": "running"})
            step_started_at = time.perf_counter()
            provider0 = resolve_provider_for_step(WORKFLOW_NAME, "expand_idea_to_full_novel_story") or ""
            timeout0 = resolve_timeout_for_step(WORKFLOW_NAME, "expand_idea_to_full_novel_story")
            use_schema0 = _check_json_schema_support("expand_idea_to_full_novel_story")
            use_stream0 = should_use_streaming(req, provider0)
            _log_workflow_event(
                request_id,
                "expand_idea",
                "running",
                provider=provider0 or "unresolved",
                timeout_seconds=timeout0,
                json_schema=use_schema0,
                stream=use_stream0,
            )
            try:
                svc0 = get_llm_service_for_step(WORKFLOW_NAME, "expand_idea_to_full_novel_story")
                suffix0 = "expand_idea_to_full_novel_story_prompt_without_schema_suffix" if use_stream0 else ("expand_idea_to_full_novel_story_prompt_with_schema_suffix" if use_schema0 else "expand_idea_to_full_novel_story_prompt_without_schema_suffix")
                prompt0 = (
                    prompts["expand_idea_to_full_novel_story_prompt_base"].format(
                        user_idea=req.user_idea,
                    )
                    + "\n"
                    + prompts[suffix0]
                )
                if use_stream0:
                    async for event in stream_structured_result_events(
                        svc0,
                        prompt0,
                        ExpandIdeaSchema,
                        "expand_idea_to_full_novel_story",
                        gen_kwargs=gen_kwargs,
                    ):
                        if event["status"] == "progress":
                            yield _sse_event(
                                "progress",
                                {
                                    "step": "expand_idea",
                                    "status": "progress",
                                    "chunk_count": event["chunk_count"],
                                    "characters": event["characters"],
                                },
                            )
                        else:
                            expanded = event["result"]
                else:
                    expanded = await generate_structured_result(
                        svc0,
                        prompt0,
                        ExpandIdeaSchema,
                        "expand_idea_to_full_novel_story",
                        gen_kwargs=gen_kwargs,
                        use_json_schema=use_schema0,
                    )
                _log_workflow_event(
                    request_id,
                    "expand_idea",
                    "done",
                    provider=provider0 or "unresolved",
                    elapsed_ms=int((time.perf_counter() - step_started_at) * 1000),
                )
                yield _sse_event("step", {"step": "expand_idea", "status": "done", "data": expanded.model_dump()})
            except Exception as e:
                logger.exception(
                    "[create_novel_by_ai] request_id=%s step=%s failed provider=%s",
                    request_id,
                    "expand_idea",
                    provider0 or "unresolved",
                )
                yield _sse_event("step", {"step": "expand_idea", "status": "error", "error": str(e)})
                yield _sse_event("done", {"success": False, "failed_step": "expand_idea", "partial_result": _build_ai_create_result(expanded, idea, seed, meta)})
                return

        # Step 2: Extract Idea
        if cached_idea := cached_prefix.get("extract_idea"):
            idea = cached_idea  # type: ignore[assignment]
            _log_workflow_event(request_id, "extract_idea", "cached")
            yield _sse_event("step", {"step": "extract_idea", "status": "done", "cached": True, "data": idea.model_dump()})
        else:
            yield _sse_event("step", {"step": "extract_idea", "status": "running"})
            step_started_at = time.perf_counter()
            provider1 = resolve_provider_for_step(WORKFLOW_NAME, "extract_idea") or ""
            timeout1 = resolve_timeout_for_step(WORKFLOW_NAME, "extract_idea")
            use_schema1 = _check_json_schema_support("extract_idea")
            use_stream1 = should_use_streaming(req, provider1)
            _log_workflow_event(
                request_id,
                "extract_idea",
                "running",
                provider=provider1 or "unresolved",
                timeout_seconds=timeout1,
                json_schema=use_schema1,
                stream=use_stream1,
            )
            try:
                svc1 = get_llm_service_for_step(WORKFLOW_NAME, "extract_idea")
                suffix1 = "extract_idea_prompt_without_schema_suffix" if use_stream1 else ("extract_idea_prompt_with_schema_suffix" if use_schema1 else "extract_idea_prompt_without_schema_suffix")
                prompt1 = (
                    prompts["extract_idea_prompt_base"].format(
                        plot=expanded.plot,
                    )
                    + "\n"
                    + prompts[suffix1]
                )
                if use_stream1:
                    async for event in stream_structured_result_events(
                        svc1,
                        prompt1,
                        ExtractIdeaSchema,
                        "extract_idea",
                        gen_kwargs=gen_kwargs,
                    ):
                        if event["status"] == "progress":
                            yield _sse_event(
                                "progress",
                                {
                                    "step": "extract_idea",
                                    "status": "progress",
                                    "chunk_count": event["chunk_count"],
                                    "characters": event["characters"],
                                },
                            )
                        else:
                            idea = event["result"]
                else:
                    idea = await generate_structured_result(
                        svc1,
                        prompt1,
                        ExtractIdeaSchema,
                        "extract_idea",
                        gen_kwargs=gen_kwargs,
                        use_json_schema=use_schema1,
                    )
                _log_workflow_event(
                    request_id,
                    "extract_idea",
                    "done",
                    provider=provider1 or "unresolved",
                    elapsed_ms=int((time.perf_counter() - step_started_at) * 1000),
                )
                yield _sse_event("step", {"step": "extract_idea", "status": "done", "data": idea.model_dump()})
            except Exception as e:
                logger.exception(
                    "[create_novel_by_ai] request_id=%s step=%s failed provider=%s",
                    request_id,
                    "extract_idea",
                    provider1 or "unresolved",
                )
                yield _sse_event("step", {"step": "extract_idea", "status": "error", "error": str(e)})
                yield _sse_event("done", {"success": False, "failed_step": "extract_idea", "partial_result": _build_ai_create_result(expanded, idea, seed, meta)})
                return

        # Step 3: Core Seed
        if cached_seed := cached_prefix.get("core_seed"):
            seed = cached_seed  # type: ignore[assignment]
            _log_workflow_event(request_id, "core_seed", "cached")
            yield _sse_event("step", {"step": "core_seed", "status": "done", "cached": True, "data": seed.model_dump()})
        else:
            yield _sse_event("step", {"step": "core_seed", "status": "running"})
            step_started_at = time.perf_counter()
            provider2 = resolve_provider_for_step(WORKFLOW_NAME, "core_seed") or ""
            timeout2 = resolve_timeout_for_step(WORKFLOW_NAME, "core_seed")
            use_schema2 = _check_json_schema_support("core_seed")
            use_stream2 = should_use_streaming(req, provider2)
            _log_workflow_event(
                request_id,
                "core_seed",
                "running",
                provider=provider2 or "unresolved",
                timeout_seconds=timeout2,
                json_schema=use_schema2,
                stream=use_stream2,
            )
            try:
                svc2 = get_llm_service_for_step(WORKFLOW_NAME, "core_seed")
                suffix2 = "core_seed_prompt_without_schema_suffix" if use_stream2 else ("core_seed_prompt_with_schema_suffix" if use_schema2 else "core_seed_prompt_without_schema_suffix")
                prompt2 = (
                    prompts["core_seed_prompt_base"].format(
                        plot=expanded.plot,
                        genre=idea.genre,
                        tone=idea.tone,
                        target_audience=idea.target_audience,
                        core_idea=idea.core_idea,
                        number_of_chapters=req.number_of_chapters,
                        words_per_chapter=req.words_per_chapter,
                    )
                    + "\n"
                    + prompts[suffix2]
                )
                if use_stream2:
                    async for event in stream_structured_result_events(
                        svc2,
                        prompt2,
                        CoreSeedSchema,
                        "core_seed",
                        gen_kwargs=gen_kwargs,
                    ):
                        if event["status"] == "progress":
                            yield _sse_event(
                                "progress",
                                {
                                    "step": "core_seed",
                                    "status": "progress",
                                    "chunk_count": event["chunk_count"],
                                    "characters": event["characters"],
                                },
                            )
                        else:
                            seed = event["result"]
                else:
                    seed = await generate_structured_result(
                        svc2,
                        prompt2,
                        CoreSeedSchema,
                        "core_seed",
                        gen_kwargs=gen_kwargs,
                        use_json_schema=use_schema2,
                    )
                _log_workflow_event(
                    request_id,
                    "core_seed",
                    "done",
                    provider=provider2 or "unresolved",
                    elapsed_ms=int((time.perf_counter() - step_started_at) * 1000),
                )
                yield _sse_event("step", {"step": "core_seed", "status": "done", "data": seed.model_dump()})
            except Exception as e:
                logger.exception(
                    "[create_novel_by_ai] request_id=%s step=%s failed provider=%s",
                    request_id,
                    "core_seed",
                    provider2 or "unresolved",
                )
                yield _sse_event("step", {"step": "core_seed", "status": "error", "error": str(e)})
                yield _sse_event("done", {"success": False, "failed_step": "core_seed", "partial_result": _build_ai_create_result(expanded, idea, seed, meta)})
                return

        # Step 4: Novel Meta
        if cached_meta := cached_prefix.get("novel_meta"):
            meta = cached_meta  # type: ignore[assignment]
            _log_workflow_event(request_id, "novel_meta", "cached")
            yield _sse_event("step", {"step": "novel_meta", "status": "done", "cached": True, "data": meta.model_dump()})
        else:
            yield _sse_event("step", {"step": "novel_meta", "status": "running"})
            step_started_at = time.perf_counter()
            provider3 = resolve_provider_for_step(WORKFLOW_NAME, "novel_meta") or ""
            timeout3 = resolve_timeout_for_step(WORKFLOW_NAME, "novel_meta")
            use_schema3 = _check_json_schema_support("novel_meta")
            use_stream3 = should_use_streaming(req, provider3)
            _log_workflow_event(
                request_id,
                "novel_meta",
                "running",
                provider=provider3 or "unresolved",
                timeout_seconds=timeout3,
                json_schema=use_schema3,
                stream=use_stream3,
            )
            try:
                svc3 = get_llm_service_for_step(WORKFLOW_NAME, "novel_meta")
                suffix3 = "novel_meta_prompt_without_schema_suffix" if use_stream3 else ("novel_meta_prompt_with_schema_suffix" if use_schema3 else "novel_meta_prompt_without_schema_suffix")
                prompt3 = (
                    prompts["novel_meta_prompt_base"].format(
                        plot=expanded.plot,
                        genre=idea.genre,
                        tone=idea.tone,
                        target_audience=idea.target_audience,
                        core_idea=idea.core_idea,
                        number_of_chapters=req.number_of_chapters,
                        words_per_chapter=req.words_per_chapter,
                        core_seed=seed.core_seed,
                    )
                    + "\n"
                    + prompts[suffix3]
                )
                if use_stream3:
                    async for event in stream_structured_result_events(
                        svc3,
                        prompt3,
                        NovelMetaSchema,
                        "novel_meta",
                        gen_kwargs=gen_kwargs,
                    ):
                        if event["status"] == "progress":
                            yield _sse_event(
                                "progress",
                                {
                                    "step": "novel_meta",
                                    "status": "progress",
                                    "chunk_count": event["chunk_count"],
                                    "characters": event["characters"],
                                },
                            )
                        else:
                            meta = event["result"]
                else:
                    meta = await generate_structured_result(
                        svc3,
                        prompt3,
                        NovelMetaSchema,
                        "novel_meta",
                        gen_kwargs=gen_kwargs,
                        use_json_schema=use_schema3,
                    )
                _log_workflow_event(
                    request_id,
                    "novel_meta",
                    "done",
                    provider=provider3 or "unresolved",
                    elapsed_ms=int((time.perf_counter() - step_started_at) * 1000),
                )
                yield _sse_event("step", {"step": "novel_meta", "status": "done", "data": meta.model_dump()})
            except Exception as e:
                logger.exception(
                    "[create_novel_by_ai] request_id=%s step=%s failed provider=%s",
                    request_id,
                    "novel_meta",
                    provider3 or "unresolved",
                )
                yield _sse_event("step", {"step": "novel_meta", "status": "error", "error": str(e)})
                yield _sse_event("done", {"success": False, "failed_step": "novel_meta", "partial_result": _build_ai_create_result(expanded, idea, seed, meta)})
                return

        # All steps completed
        final_result = _build_ai_create_result(expanded, idea, seed, meta)
        _log_workflow_event(
            request_id,
            "workflow",
            "done",
            total_elapsed_ms=int((time.perf_counter() - workflow_start) * 1000),
        )
        yield _sse_event("done", {
            "success": True,
            "result": final_result,
        })

    return _sse_response(event_stream())
