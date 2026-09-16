"""统一提交协议的操作记录仓储。

任何"采纳 / 定稿 / 写回 / 还原"这类会改变正式数据的动作，都先在本集合认领一次操作：
- 相同 request_id + 相同请求体：只生效一次，重复提交回放首次结果；
- 相同 request_id + 不同请求体：拒绝，避免复用幂等键造成错写；
- 处理中的重复提交：返回冲突，调用方应稍后重试而不是再写一遍；
- 失败的操作允许用同一个 request_id 重试，重试记录会累积在 attempts 中。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError as PyMongoDuplicateKeyError

from backend.api.error_contract import ServiceError
from backend.db.base import BaseRepository
from backend.db.repositories.id_sequence_repository import id_sequence_repo
from backend.db.utils import canonicalize_extras, get_utc_now, to_object_id

logger = logging.getLogger(__name__)

# 操作状态：processing 进行中 / completed 已完成 / failed 已失败（可重试）
OPERATION_STATUS = ("processing", "completed", "failed")


def build_request_hash(payload: Any) -> str:
    """计算请求体指纹，用于识别同一幂等键下的不同请求。

    Args:
        payload: 参与幂等判定的请求内容，通常是请求体字典。

    Returns:
        SHA-256 十六进制字符串。
    """
    canonical = json.dumps(
        payload if payload is not None else {},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OperationRecordRepository(BaseRepository):
    """操作记录数据访问层。"""

    def __init__(self) -> None:
        super().__init__("operation_records")

    async def _next_operation_id(
        self, novel_id, session: AsyncClientSession | None = None
    ) -> str:
        allocated = await id_sequence_repo.allocate_many(
            novel_id, "operation_record", "op", 1, session=session
        )
        return allocated[0]

    async def claim(
        self,
        novel_id,
        *,
        operation_type: str,
        request_id: str,
        request_hash: str,
        target: Optional[Dict[str, Any]] = None,
        actor: str = "user",
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """认领一次操作，保证同一幂等键只生效一次。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            operation_type: 操作类型，例如 setting_card_accept。
            request_id: 客户端幂等键。
            request_hash: 请求体指纹。
            target: 操作目标描述，便于排障与审计。
            actor: 操作发起者。
            session: 可选 MongoDB 会话。

        Returns:
            ``{"operation_id", "status", "replayed", "result_refs", "attempt"}``。

        Raises:
            ServiceError: 幂等键冲突（处理中或请求体不一致）。
        """
        if not request_id:
            raise ServiceError("REQUEST_ID_REQUIRED", "缺少幂等键 request_id")
        if not operation_type:
            raise ServiceError("INTERNAL_ERROR", "operation_type 不能为空")

        now = get_utc_now()
        operation_id = await self._next_operation_id(novel_id, session=session)
        doc: Dict[str, Any] = {
            "operation_id": operation_id,
            "novel_id": to_object_id(novel_id),
            "operation_type": operation_type,
            "request_id": str(request_id),
            "request_hash": request_hash,
            "target": canonicalize_extras(target or {}),
            "status": "processing",
            "actor": str(actor or "user"),
            "result_refs": {},
            "attempt": 1,
            "error": {},
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self.insert_one(dict(doc), session=session)
        except PyMongoDuplicateKeyError:
            return await self._handle_duplicate(
                novel_id,
                operation_type=operation_type,
                request_id=request_id,
                request_hash=request_hash,
                session=session,
            )
        return {
            "operation_id": operation_id,
            "status": "processing",
            "replayed": False,
            "result_refs": {},
            "attempt": 1,
        }

    async def _handle_duplicate(
        self,
        novel_id,
        *,
        operation_type: str,
        request_id: str,
        request_hash: str,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """处理幂等键冲突：回放、拒绝或接管重试。"""
        existing = await self.find_one(
            {
                "novel_id": to_object_id(novel_id),
                "operation_type": operation_type,
                "request_id": request_id,
            },
            session=session,
        )
        if not existing:
            # 极端并发下记录已被清理，交给调用方重试一次
            raise ServiceError(
                "OPERATION_IN_PROGRESS",
                "同一 request_id 的操作正在处理中，请稍后重试",
                retryable=True,
            )
        if str(existing.get("request_hash") or "") != request_hash:
            raise ServiceError(
                "REQUEST_ID_REUSED",
                "request_id 已被其他请求使用，请更换幂等键后重试",
            )
        status = str(existing.get("status") or "")
        if status == "completed":
            return {
                "operation_id": existing.get("operation_id"),
                "status": "completed",
                "replayed": True,
                "result_refs": existing.get("result_refs") or {},
                "attempt": int(existing.get("attempt") or 1),
            }
        if status == "processing":
            raise ServiceError(
                "OPERATION_IN_PROGRESS",
                "同一 request_id 的操作正在处理中，请稍后重试",
                retryable=True,
            )
        # failed：允许用同一幂等键重试，原子接管并累加尝试次数
        taken = await self.collection.find_one_and_update(
            {
                "novel_id": to_object_id(novel_id),
                "operation_type": operation_type,
                "request_id": request_id,
                "status": "failed",
            },
            {
                "$set": {"status": "processing", "updated_at": get_utc_now()},
                "$inc": {"attempt": 1},
                "$push": {
                    "attempts": {
                        "retried_at": get_utc_now(),
                        "previous_error": existing.get("error") or {},
                    }
                },
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if not taken:
            raise ServiceError(
                "OPERATION_IN_PROGRESS",
                "同一 request_id 的操作正在处理中，请稍后重试",
                retryable=True,
            )
        return {
            "operation_id": taken.get("operation_id"),
            "status": "processing",
            "replayed": False,
            "result_refs": {},
            "attempt": int(taken.get("attempt") or 1),
        }

    async def complete(
        self,
        novel_id,
        operation_id: str,
        *,
        result_refs: Optional[Dict[str, Any]] = None,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """标记操作成功并固化结果引用，供后续重放使用。"""
        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "operation_id": operation_id,
                "status": "processing",
            },
            {
                "$set": {
                    "status": "completed",
                    "result_refs": canonicalize_extras(result_refs or {}),
                    "error": {},
                    "updated_at": get_utc_now(),
                    "finished_at": get_utc_now(),
                }
            },
            session=session,
        )
        return result.modified_count > 0

    async def fail(
        self,
        novel_id,
        operation_id: str,
        *,
        error_code: str = "INTERNAL_ERROR",
        message: str = "",
        session: AsyncClientSession | None = None,
    ) -> bool:
        """标记操作失败，保留错误码以便重试与排障。"""
        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "operation_id": operation_id,
                "status": "processing",
            },
            {
                "$set": {
                    "status": "failed",
                    "error": {"code": error_code, "message": str(message or "")},
                    "updated_at": get_utc_now(),
                    "finished_at": get_utc_now(),
                }
            },
            session=session,
        )
        return result.modified_count > 0

    async def get_operation(
        self,
        novel_id,
        operation_id: str,
        session: AsyncClientSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """读取一条操作记录。"""
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "operation_id": operation_id},
            session=session,
        )
        return self._serialize(doc)

    async def list_operations(
        self,
        novel_id,
        *,
        operation_type: str = "",
        status: str = "",
        request_id: str = "",
        limit: int = 50,
        skip: int = 0,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """列出小说下的操作记录，按创建时间倒序。"""
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if operation_type:
            flt["operation_type"] = operation_type
        if status:
            flt["status"] = status
        if request_id:
            flt["request_id"] = request_id
        docs = await self.find_many(
            flt,
            limit=max(1, min(200, limit)),
            skip=max(0, skip),
            sort=[("created_at", -1)],
            session=session,
        )
        return [self._serialize(d) for d in docs]

    @staticmethod
    def _serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """把 ObjectId 转成字符串。"""
        if not doc:
            return doc
        for key in ("_id", "novel_id"):
            if key in doc and isinstance(doc[key], ObjectId):
                doc[key] = str(doc[key])
        return doc


operation_record_repo = OperationRecordRepository()
