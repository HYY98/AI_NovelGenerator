"""AI 生成服务的公共支撑：Provider 解析、生成记录幂等与候选响应封装。

章节 AI 与设定卡 AI 共用这套逻辑，保证两条链路在
"候选 → 确认 → 正式保存" 上的行为完全一致。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.db.errors import InvalidIdError
from backend.db.repositories.generation_record_repository import generation_record_repo
from backend.llm.config import get_provider_config
from backend.services.llm.llm_service import LLMService
from backend.services.llm.workflow_service import (
    get_llm_service_for_step,
    provider_supports_json_schema,
    resolve_provider_for_step,
    should_use_streaming,
)

logger = logging.getLogger(__name__)


def provider_model(provider: str) -> str:
    """读取 Provider 的默认模型名，仅用于生成记录审计。

    Args:
        provider: Provider 别名。

    Returns:
        默认模型名；Provider 不存在时返回空字符串。
    """
    try:
        return get_provider_config(provider).default_model
    except Exception:
        return ""


def resolve_runtime(
    request: Any,
    workflow: str,
    step: str,
) -> Tuple[LLMService, str, bool, bool]:
    """解析本次生成使用的 Provider、JSON Schema 与流式能力。

    Args:
        request: 含可选 provider 覆盖与 use_stream 的请求对象。
        workflow: 工作流名称。
        step: 工作流步骤名称。

    Returns:
        元组：LLMService、Provider 别名、是否使用原生 JSON Schema、是否流式。

    Raises:
        InvalidIdError: 显式指定的 Provider 不存在或未启用时抛出。
        ValueError: 未配置任何可用 Provider 时抛出。
    """
    override = (getattr(request, "provider", "") or "").strip()
    if override:
        try:
            config = get_provider_config(override)
        except ValueError as exc:
            raise InvalidIdError(f"Provider 不存在: {override}") from exc
        if not config.enabled:
            raise InvalidIdError(f"Provider 未启用: {override}")
        return (
            LLMService(provider_name=override),
            override,
            bool(config.supports_json_schema),
            should_use_streaming(request, override),
        )

    provider = resolve_provider_for_step(workflow, step)
    if not provider:
        raise ValueError(
            f"无法为工作流 '{workflow}' 的步骤 '{step}' 找到可用的 Provider，"
            "请检查配置中是否有已启用的 Provider。"
        )
    return (
        get_llm_service_for_step(workflow, step),
        provider,
        provider_supports_json_schema(provider),
        should_use_streaming(request, provider),
    )


async def reuse_record(
    novel_id: str,
    kind: str,
    request_id: str,
) -> Optional[Dict[str, Any]]:
    """相同 request_id 的生成已完成时复用候选，避免重复计费。

    Args:
        novel_id: 小说 ObjectId 字符串。
        kind: 生成类型。
        request_id: 客户端幂等 ID。

    Returns:
        已完成的生成记录响应体；不存在或未完成时返回 None。
    """
    if not request_id:
        return None
    existing = await generation_record_repo.find_by_request_id(novel_id, kind, request_id)
    if existing and existing.get("status") == "completed":
        logger.info(
            "复用已完成的生成记录 generation_id=%s kind=%s request_id=%s",
            existing.get("generation_id"),
            kind,
            request_id,
        )
        return record_to_response(existing)
    return None


def record_to_response(record: Dict[str, Any]) -> Dict[str, Any]:
    """把生成记录转成与新建候选一致的响应结构。"""
    payload = record.get("result") or {}
    return {
        "kind": record.get("kind"),
        "candidate_id": record.get("generation_id"),
        "data": payload.get("data", {}),
        "source": {
            "type": "ai_generated",
            "chapter_id": record.get("chapter_id", ""),
            "card_id": record.get("card_id", ""),
            "evidence": "复用相同 request_id 的既有候选",
        },
        "warnings": payload.get("warnings", []),
        "conflicts": payload.get("conflicts", []),
        "provider": record.get("provider", ""),
        "model": record.get("model", ""),
        "context_snapshot": record.get("input_snapshot") or {},
        "reused": True,
    }


async def save_record(
    novel_id: str,
    kind: str,
    *,
    chapter_id: str = "",
    card_id: str = "",
    request_id: str = "",
    snapshot: Dict[str, Any],
    data: Any,
    provider: str,
    warnings: List[str],
    conflicts: List[str],
) -> Dict[str, Any]:
    """写入生成记录并返回候选响应体。

    Args:
        novel_id: 小说 ObjectId 字符串。
        kind: 生成类型。
        chapter_id: 关联章节业务 ID。
        card_id: 关联卡片业务 ID。
        request_id: 客户端幂等 ID。
        snapshot: 输入上下文快照。
        data: 候选数据。
        provider: Provider 别名。
        warnings: 生成告警。
        conflicts: 冲突说明。

    Returns:
        统一的候选响应体。
    """
    payload = {"data": data, "warnings": warnings, "conflicts": conflicts}
    record = await generation_record_repo.create_record(
        novel_id,
        {
            "kind": kind,
            "chapter_id": chapter_id,
            "card_id": card_id,
            "request_id": request_id,
            "input_snapshot": snapshot,
            "result": payload,
            "provider": provider,
            "model": provider_model(provider),
            "status": "completed",
        },
    )
    return {
        "kind": kind,
        "candidate_id": record.get("generation_id"),
        "data": data,
        "source": {
            "type": "ai_generated",
            "chapter_id": chapter_id,
            "card_id": card_id,
            "evidence": f"基于 {kind} 生成，上下文摘要 {str(snapshot.get('context_hash', ''))[:12]}",
        },
        "warnings": warnings,
        "conflicts": conflicts,
        "provider": provider,
        "model": record.get("model"),
        "context_snapshot": snapshot,
    }
