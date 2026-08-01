"""LLM 工作流服务：支持多步骤 LLM 管道调用，具备 Provider 与超时回退逻辑。"""

from __future__ import annotations

import time
from typing import Any, AsyncGenerator, TypeVar

from pydantic import BaseModel

from backend.config import get_config_value
from backend.llm.config import get_llm_config, get_provider_config
from backend.services.llm.format_review_service import validate_and_fix_format
from backend.services.llm.llm_service import LLMService


TStructuredModel = TypeVar("TStructuredModel", bound=BaseModel)


def _get_workflow_step_config(workflow_name: str, step_name: str) -> dict:
    """读取工作流步骤配置，缺失或结构异常时返回空配置。"""
    workflows = get_config_value("llm", {}).get("workflows", {})
    workflow = workflows.get(workflow_name, {})
    steps = workflow.get("steps", {}) if isinstance(workflow, dict) else {}
    step_cfg = steps.get(step_name, {}) if isinstance(steps, dict) else {}
    return step_cfg if isinstance(step_cfg, dict) else {}


def _coerce_positive_timeout(value: object) -> int | None:
    """将步骤级超时配置转成正整数秒，空值表示继承 Provider 默认值。"""
    if value in (None, ""):
        return None
    try:
        timeout_seconds = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return timeout_seconds if timeout_seconds > 0 else None


def resolve_provider_for_step(workflow_name: str, step_name: str) -> str | None:
    """按优先级解析步骤应使用的 Provider 别名。

    Args:
        workflow_name: 工作流配置名称。
        step_name: 工作流步骤名称。

    Returns:
        可用 Provider 别名；所有回退均不可用时返回 None。

    回退链：step.provider → workflow.default_provider → llm.default_provider。
    """
    llm_cfg = get_llm_config()
    workflows = get_config_value("llm", {}).get("workflows", {})
    workflow = workflows.get(workflow_name, {})

    # 1. 步骤级别
    step_cfg = _get_workflow_step_config(workflow_name, step_name)
    step_provider = step_cfg.get("provider", "")
    if step_provider:
        # 检查是否存在且启用
        if step_provider in llm_cfg.providers and llm_cfg.providers[step_provider].enabled:
            return step_provider

    # 2. 工作流默认
    wf_default = workflow.get("default_provider", "")
    if wf_default:
        if wf_default in llm_cfg.providers and llm_cfg.providers[wf_default].enabled:
            return wf_default

    # 3. 全局默认
    global_default = llm_cfg.default_provider
    if global_default in llm_cfg.providers and llm_cfg.providers[global_default].enabled:
        return global_default

    return None


def resolve_timeout_for_step(workflow_name: str, step_name: str) -> int | None:
    """解析步骤级 LLM 请求超时。

    Args:
        workflow_name: 工作流配置名称。
        step_name: 工作流步骤名称。

    Returns:
        正整数超时秒数；未配置时返回 None 以继承 Provider 默认值。
    """
    step_cfg = _get_workflow_step_config(workflow_name, step_name)
    return _coerce_positive_timeout(step_cfg.get("timeout_seconds"))


def get_llm_service_for_step(workflow_name: str, step_name: str) -> LLMService:
    """创建用于指定工作流步骤的 LLMService 实例。

    Args:
        workflow_name: 工作流配置名称。
        step_name: 工作流步骤名称。

    Returns:
        已绑定 Provider 和步骤超时的 LLMService。
    """
    provider = resolve_provider_for_step(workflow_name, step_name)
    if not provider:
        raise ValueError(
            f"无法为工作流 '{workflow_name}' 的步骤 '{step_name}' 找到可用的 Provider，"
            "请检查配置中是否有已启用的 Provider。"
        )
    return LLMService(
        provider_name=provider,
        timeout_seconds=resolve_timeout_for_step(workflow_name, step_name),
    )


def build_generation_kwargs(request: Any) -> dict[str, Any]:
    """从扁平业务请求中提取非空的公共文本生成参数。

    Args:
        request: 包含温度、采样、Token、惩罚和系统提示词字段的请求模型。

    Returns:
        可直接传给 LLMService 的请求级参数；不包含仅用于选择入口的 use_stream。
    """
    kwargs: dict[str, Any] = {}
    for key in (
        "temperature",
        "top_p",
        "max_tokens",
        "presence_penalty",
        "frequency_penalty",
        "system_prompt",
    ):
        value = getattr(request, key, None)
        if value is not None:
            kwargs[key] = value
    return kwargs


def provider_supports_streaming(provider: str) -> bool:
    """判断指定 Provider 是否支持流式文本输出。

    Args:
        provider: Provider 配置别名。

    Returns:
        Provider 存在且启用流式能力时返回 True，否则返回 False。
    """
    alias = provider.strip()
    if not alias:
        return False
    try:
        return bool(get_provider_config(alias).supports_streaming)
    except ValueError:
        return False


def provider_supports_json_schema(provider: str) -> bool:
    """判断指定 Provider 是否支持原生 JSON Schema 输出。

    Args:
        provider: Provider 配置别名。

    Returns:
        Provider 存在且启用 JSON Schema 能力时返回 True，否则返回 False。
    """
    alias = provider.strip()
    if not alias:
        return False
    try:
        return bool(get_provider_config(alias).supports_json_schema)
    except ValueError:
        return False


def should_use_streaming(request: Any, provider: str) -> bool:
    """按请求开关与 Provider 能力决定是否调用流式接口。

    Args:
        request: 包含 use_stream 字段的业务请求模型。
        provider: 当前步骤解析出的 Provider 别名。

    Returns:
        请求开启流式且 Provider 支持时返回 True。
    """
    return bool(
        getattr(request, "use_stream", True)
        and provider_supports_streaming(provider)
    )


async def generate_structured_result(
    service: LLMService,
    prompt: str,
    schema: type[TStructuredModel],
    step_label: str,
    *,
    gen_kwargs: dict[str, Any] | None = None,
    use_json_schema: bool,
) -> TStructuredModel:
    """执行一次非流式结构化生成并返回严格校验结果。

    Args:
        service: 已绑定当前工作流 Provider 的 LLMService。
        prompt: 完整业务提示词。
        schema: Provider 输出目标 Schema。
        step_label: 日志与格式审校使用的步骤名。
        gen_kwargs: 可选请求级生成参数。
        use_json_schema: 是否调用 Provider 原生 JSON Schema 接口。

    Returns:
        已通过目标 Schema 校验的结果。
    """
    params = gen_kwargs or {}
    if use_json_schema:
        result = await service.generate_structured(prompt, schema, **params)
        return schema.model_validate(result.model_dump())

    raw_text = await service.generate_text(prompt, **params)
    return await validate_and_fix_format(raw_text, schema, step_label)


async def stream_structured_result_events(
    service: LLMService,
    prompt: str,
    schema: type[TStructuredModel],
    step_label: str,
    *,
    gen_kwargs: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """收集 Provider 文本流并产出不暴露 JSON 分片的进度与完成事件。

    Args:
        service: 已绑定当前工作流 Provider 的 LLMService。
        prompt: 使用普通 JSON 输出约束的完整提示词。
        schema: 最终结构化结果 Schema。
        step_label: 日志与格式审校使用的步骤名。
        gen_kwargs: 可选请求级生成参数。

    Yields:
        progress 事件包含累计 chunk 和字符数；done 事件包含严格校验结果。
    """
    chunks: list[str] = []
    chunk_count = 0
    character_count = 0
    last_emit_at = 0.0

    async for chunk in service.stream_text(prompt, **(gen_kwargs or {})):
        chunks.append(chunk)
        chunk_count += 1
        character_count += len(chunk)
        now = time.perf_counter()
        # 首块立即回报，后续按秒节流；原始 JSON 始终只在服务端累积。
        if chunk_count == 1 or now - last_emit_at >= 1.0:
            last_emit_at = now
            yield {
                "status": "progress",
                "chunk_count": chunk_count,
                "characters": character_count,
            }

    raw_text = "".join(chunks).strip()
    if not raw_text:
        raise ValueError("流式响应为空，无法解析为结构化结果")

    result = await validate_and_fix_format(raw_text, schema, step_label)
    yield {
        "status": "done",
        "result": result,
        "chunk_count": chunk_count,
        "characters": character_count,
    }
