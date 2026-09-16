"""候选统一提交服务（v2.0 第一批）。

把「认领操作 → 校验 → 正式写入 → 逐候选落状态 → 固化结果」这条链路收敛到一处，
保证设定卡采纳、正文修正、定稿、还原等动作在并发、重试与失败场景下的行为一致：

- 幂等：同一 request_id + 相同请求体只生效一次，重复提交回放首次结果；
- 并发：同一候选的采纳通过条件更新认领，只有第一个请求能成功；
- 恢复：正式写入失败时释放候选状态并把操作标记为 failed，可用同一 request_id 重试。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from backend.api.error_contract import ServiceError
from backend.db.repositories.generation_record_repository import generation_record_repo
from backend.db.repositories.operation_record_repository import (
    build_request_hash,
    operation_record_repo,
)

logger = logging.getLogger(__name__)

CANDIDATE_STATUS = ("pending", "accepted", "rejected", "ignored")
# 终态：进入这些状态后不再允许接受
CANDIDATE_TERMINAL_STATUS = ("accepted", "rejected", "ignored")


class OperationHandle:
    """一次已认领操作的句柄。"""

    def __init__(
        self,
        *,
        novel_id: str,
        operation_id: str,
        operation_type: str,
        replayed: bool,
        result_refs: Dict[str, Any],
        attempt: int,
    ) -> None:
        self.novel_id = novel_id
        self.operation_id = operation_id
        self.operation_type = operation_type
        self.replayed = replayed
        self.result_refs = result_refs or {}
        self.attempt = attempt

    async def succeed(self, result_refs: Optional[Dict[str, Any]] = None) -> None:
        """标记操作成功并固化结果引用。"""
        await operation_record_repo.complete(
            self.novel_id, self.operation_id, result_refs=result_refs or {}
        )

    async def fail(self, error_code: str, message: str) -> None:
        """标记操作失败，保留错误码以便重试。"""
        await operation_record_repo.fail(
            self.novel_id, self.operation_id, error_code=error_code, message=message
        )


async def begin_operation(
    *,
    novel_id: str,
    operation_type: str,
    request_id: str,
    request_payload: Optional[Dict[str, Any]] = None,
    target: Optional[Dict[str, Any]] = None,
    actor: str = "user",
) -> OperationHandle:
    """认领一次操作，返回句柄；命中已完成记录时返回 replayed 句柄。"""
    request_hash = build_request_hash(request_payload or {})
    claimed = await operation_record_repo.claim(
        novel_id,
        operation_type=operation_type,
        request_id=request_id,
        request_hash=request_hash,
        target=target,
        actor=actor,
    )
    return OperationHandle(
        novel_id=novel_id,
        operation_id=str(claimed.get("operation_id") or ""),
        operation_type=operation_type,
        replayed=bool(claimed.get("replayed")),
        result_refs=claimed.get("result_refs") or {},
        attempt=int(claimed.get("attempt") or 1),
    )


async def run_operation(
    *,
    novel_id: str,
    operation_type: str,
    request_id: str,
    request_payload: Optional[Dict[str, Any]] = None,
    target: Optional[Dict[str, Any]] = None,
    executor: Callable[[OperationHandle], Awaitable[Dict[str, Any]]],
    actor: str = "user",
) -> Dict[str, Any]:
    """以统一协议执行一次会改变正式数据的动作。

    Args:
        novel_id: 小说 ObjectId 字符串。
        operation_type: 操作类型。
        request_id: 客户端幂等键。
        request_payload: 参与幂等判定的请求体。
        target: 操作目标描述。
        executor: 真正的写入逻辑，接收操作句柄，返回结果字典。
        actor: 操作发起者。

    Returns:
        ``{"operation_id", "replayed", "result"}``；命中重放时 result 为首次结果引用。
    """
    handle = await begin_operation(
        novel_id=novel_id,
        operation_type=operation_type,
        request_id=request_id,
        request_payload=request_payload,
        target=target,
        actor=actor,
    )
    if handle.replayed:
        logger.info(
            "操作命中幂等重放 operation_type=%s operation_id=%s",
            operation_type,
            handle.operation_id,
        )
        return {
            "operation_id": handle.operation_id,
            "replayed": True,
            "result": handle.result_refs,
        }

    try:
        result = await executor(handle)
    except ServiceError as exc:
        await handle.fail(exc.code, exc.message)
        raise
    except Exception as exc:  # noqa: BLE001 - 统一兜底，保证操作状态可恢复
        logger.exception(
            "操作执行失败 operation_type=%s operation_id=%s",
            operation_type,
            handle.operation_id,
        )
        await handle.fail("INTERNAL_ERROR", str(exc))
        raise

    result_refs = {}
    if isinstance(result, dict):
        result_refs = dict(result.get("result_refs") or result)
    await handle.succeed(result_refs)
    return {
        "operation_id": handle.operation_id,
        "replayed": False,
        "result": result,
    }


def _candidate_container(record: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """定位生成记录中的候选数组。

    Returns:
        (字段路径, 候选列表)；无候选数组时路径为空字符串。
    """
    payload = record.get("payload") or {}
    if isinstance(payload, dict):
        data = (payload.get("result") or {}).get("data") or {}
        if isinstance(data, dict) and isinstance(data.get("candidates"), list):
            return "payload.result.data.candidates", list(data["candidates"])
    data = (record.get("result") or {}).get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("candidates"), list):
        return "result.data.candidates", list(data["candidates"])
    return "", []


async def ensure_candidate_ids(record: Dict[str, Any]) -> Dict[str, Any]:
    """为缺失稳定 ID 的候选补齐 candidate_id，并回读最新记录。

    Args:
        record: 生成记录。

    Returns:
        补齐候选 ID 后的生成记录。
    """
    path, candidates = _candidate_container(record)
    if not path or not candidates:
        return record
    missing = [
        index
        for index, item in enumerate(candidates)
        if isinstance(item, dict) and not str(item.get("candidate_id") or "").strip()
    ]
    if not missing:
        return record
    generation_id = str(record.get("generation_id") or "")
    updates: Dict[str, Any] = {}
    for index in missing:
        item = candidates[index] or {}
        version = str(item.get("version") or "").strip() or str(index + 1)
        updates[f"{path}.{index}.candidate_id"] = f"{generation_id}#c{version}"
        updates[f"{path}.{index}.status"] = str(item.get("status") or "pending")
    await generation_record_repo.update_one(
        {"generation_id": generation_id}, updates
    )
    return await generation_record_repo.get_owned_record(
        record.get("novel_id"), generation_id
    )


async def resolve_candidate(
    novel_id: str,
    generation_id: str,
    candidate_id: str = "",
) -> Dict[str, Any]:
    """读取一条候选，兼容数组候选与整记录单候选。

    Args:
        novel_id: 小说 ObjectId 字符串。
        generation_id: 生成记录业务 ID。
        candidate_id: 候选 ID；为空表示整记录即候选。

    Returns:
        ``{"candidate_id", "status", "data", "version", "record"}``。

    Raises:
        ServiceError: 候选不存在。
    """
    record = await generation_record_repo.get_owned_record(novel_id, generation_id)
    record = await ensure_candidate_ids(record)
    path, candidates = _candidate_container(record)
    wanted = str(candidate_id or "").strip()

    if not candidates:
        if wanted and wanted not in ("", f"{generation_id}#c1"):
            raise ServiceError(
                "CANDIDATE_NOT_FOUND",
                f"生成记录 {generation_id} 不包含候选 {candidate_id}",
            )
        data = ((record.get("result") or {}).get("data")) or {}
        if not data:
            payload_data = (record.get("payload") or {}).get("result") or {}
            data = payload_data.get("data") or {}
        return {
            "candidate_id": "",
            "status": str(record.get("accept_status") or "pending"),
            "data": data,
            "version": "",
            "record": record,
        }

    for index, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("candidate_id") or "").strip()
        if wanted == item_id:
            return {
                "candidate_id": item_id,
                "status": str(item.get("status") or "pending"),
                "data": item,
                "version": str(item.get("version") or index + 1),
                "record": record,
            }
    # 兼容：允许用 version 或下标定位
    for index, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        if wanted in (str(item.get("version") or ""), str(index + 1), f"c{index + 1}"):
            return {
                "candidate_id": str(item.get("candidate_id") or ""),
                "status": str(item.get("status") or "pending"),
                "data": item,
                "version": str(item.get("version") or index + 1),
                "record": record,
            }
    raise ServiceError(
        "CANDIDATE_NOT_FOUND",
        f"生成记录 {generation_id} 不包含候选 {candidate_id}",
    )


async def claim_candidate(
    novel_id: str,
    generation_id: str,
    candidate_id: str,
    operation_id: str,
) -> None:
    """原子认领一条候选，保证并发下只有一个请求能采纳它。

    Args:
        novel_id: 小说 ObjectId 字符串。
        generation_id: 生成记录业务 ID。
        candidate_id: 候选业务 ID；单候选传空字符串。
        operation_id: 关联的幂等操作 ID。

    Raises:
        ServiceError: 候选不存在（404）、已被采纳（409）或已进入终态（409）。
    """
    resolved = await resolve_candidate(novel_id, generation_id, candidate_id)
    # 数组候选返回稳定 candidate_id；单候选返回空字符串（整记录即候选）
    target_id = str(resolved.get("candidate_id") or "")

    claimed = await generation_record_repo.set_candidate_status(
        novel_id,
        generation_id,
        target_id,
        "accepted",
        operation_id=operation_id,
    )
    if claimed:
        return

    latest = await resolve_candidate(novel_id, generation_id, target_id or candidate_id)
    status = str(latest.get("status") or "")
    if status == "accepted":
        raise ServiceError(
            "CANDIDATE_ALREADY_ACCEPTED",
            f"候选 {target_id or generation_id} 已被采纳，请勿重复提交",
        )
    if status in ("rejected", "ignored"):
        raise ServiceError(
            "CANDIDATE_ALREADY_RESOLVED",
            f"候选 {target_id or generation_id} 已被{('拒绝' if status == 'rejected' else '忽略')}，不可再采纳",
        )
    raise ServiceError("CANDIDATE_NOT_FOUND", f"候选 {candidate_id} 不存在")


async def release_candidate(
    novel_id: str,
    generation_id: str,
    candidate_id: str,
) -> None:
    """释放候选的采纳认领，用于正式写入失败后的回滚。"""
    await generation_record_repo.set_candidate_status(
        novel_id,
        generation_id,
        str(candidate_id or ""),
        "pending",
        expected="accepted",
    )


async def mark_candidate_rejected(
    novel_id: str,
    generation_id: str,
    candidate_id: str,
    *,
    reason: str = "",
    operation_id: str = "",
) -> None:
    """把一条候选标记为已拒绝。"""
    await generation_record_repo.set_candidate_status(
        novel_id,
        generation_id,
        str(candidate_id or ""),
        "rejected",
        reason=reason,
        operation_id=operation_id,
    )


async def mark_candidate_ignored(
    novel_id: str,
    generation_id: str,
    candidate_id: str,
    *,
    reason: str = "",
    operation_id: str = "",
) -> None:
    """把一条候选标记为已忽略（用户明确不采纳也不拒绝）。"""
    await generation_record_repo.set_candidate_status(
        novel_id,
        generation_id,
        str(candidate_id or ""),
        "ignored",
        reason=reason,
        operation_id=operation_id,
    )


def summarize_candidate(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把一条生成记录展开成候选列表项。"""
    path, candidates = _candidate_container(record)
    generation_id = str(record.get("generation_id") or "")
    base = {
        "generation_id": generation_id,
        "kind": str(record.get("kind") or ""),
        "action_type": str(record.get("action_type") or ""),
        "record_type": str(record.get("record_type") or ""),
        "status": str(record.get("status") or ""),
        "scope": record.get("scope") or {},
        "provider": str(record.get("provider") or ""),
        "model": str(record.get("model") or ""),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "is_latest": bool(record.get("is_latest", False)),
    }
    items: List[Dict[str, Any]] = []
    if path and candidates:
        for index, item in enumerate(candidates):
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    **base,
                    "candidate_id": str(item.get("candidate_id") or ""),
                    "version": str(item.get("version") or index + 1),
                    "candidate_status": str(item.get("status") or "pending"),
                    "summary": str(item.get("name") or item.get("title") or item.get("summary") or ""),
                    "data": item,
                }
            )
        return items
    data = ((record.get("result") or {}).get("data")) or {}
    if not data:
        data = ((record.get("payload") or {}).get("result") or {}).get("data") or {}
    return [
        {
            **base,
            "candidate_id": "",
            "version": "",
            "candidate_status": str(record.get("accept_status") or "pending"),
            "summary": str(data.get("title") or data.get("name") or ""),
            "data": data,
        }
    ]
