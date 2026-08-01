"""格式审校服务：当 LLM 原始响应无法通过 Schema 校验时，调用支持 json_schema 的 Provider 进行修正。"""

from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from backend.llm.config import get_llm_config, get_provider_config
from backend.services.llm.llm_service import LLMService

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
FORMAT_REVIEW_PROMPT_TEMPLATE = (
    "以下是另一个 AI 的原始输出，但格式不符合要求。\n"
    "请从中提取有效信息，严格按目标 Schema 输出合法 JSON，不要输出任何额外内容。\n\n"
    "原始输出:\n{raw_text}"
)


def resolve_format_review_provider() -> str:
    """解析当前可用的结构化格式审校 Provider。

    Args:
        无。

    Returns:
        已启用且支持 JSON Schema 的 Provider 别名。
    """
    llm_cfg = get_llm_config()

    # 1. 配置显式指定
    configured = llm_cfg.format_review_provider
    if configured:
        if configured in llm_cfg.providers:
            prov = llm_cfg.providers[configured]
            if prov.enabled and prov.supports_json_schema:
                return configured
        logger.warning(
            "配置的 format_review_provider '%s' 不可用（不存在 / 未启用 / 不支持 json_schema），尝试自动查找",
            configured,
        )

    # 2. 自动查找
    for name, cfg in llm_cfg.providers.items():
        if cfg.enabled and cfg.supports_json_schema:
            return name

    raise ValueError("无可用的支持 json_schema 的 Provider 进行格式审校，请在配置中设置 format_review_provider")


async def validate_and_fix_format(
    raw_text: str,
    schema: type[T],
    step_label: str = "",
) -> T:
    """解析原始文本，并在结构失败时执行一次通用格式审校。

    Args:
        raw_text: LLM 返回的原始文本。
        schema: 目标 Pydantic Schema 类型。
        step_label: 用于日志的步骤标识。
    Returns:
        直接解析或经一次审校后的 Schema 实例。
    """
    # 先尝试直接解析
    try:
        return schema.model_validate_json(raw_text)
    except (ValidationError, ValueError) as parse_err:
        logger.warning("%s 响应格式校验失败，尝试格式审校: %s", step_label or "unknown", parse_err)

    # 直接使用当前通用配置，不保存跨请求审校绑定或任务级重试状态。
    reviewer_provider = resolve_format_review_provider()
    logger.info(
        "%s 启用格式审校 provider=%s schema=%s",
        step_label or "unknown",
        reviewer_provider,
        schema.__name__,
    )
    reviewer = LLMService(provider_name=reviewer_provider)
    review_prompt = FORMAT_REVIEW_PROMPT_TEMPLATE.format(raw_text=raw_text)
    return await reviewer.generate_structured(review_prompt, schema)
