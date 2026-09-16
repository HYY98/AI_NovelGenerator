"""v2.0 统一错误契约。

业务层抛出的错误统一带稳定错误码，路由层转换成 HTTPException 时：
- 响应体 ``detail`` 仍是字符串，保持与既有前端解析方式一致；
- 错误码通过响应头 ``X-NG-Error-Code`` 暴露，供前端做分支处理。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from backend.db.errors import (
    DatabaseError,
    DuplicateKeyError,
    InvalidIdError,
    NotFoundError,
)

ERROR_CODE_HEADER = "X-NG-Error-Code"

# 错误码 -> HTTP 状态码
_STATUS_BY_CODE: Dict[str, int] = {
    "REQUEST_ID_REQUIRED": 400,
    "OPERATION_IN_PROGRESS": 409,
    "REQUEST_ID_REUSED": 409,
    "CANDIDATE_NOT_FOUND": 404,
    "CANDIDATE_ALREADY_ACCEPTED": 409,
    "CANDIDATE_SCOPE_MISMATCH": 400,
    "CANDIDATE_KIND_UNSUPPORTED": 422,
    "VERSION_STALE": 409,
    "REVIEW_REQUIRED": 422,
    "REVIEW_INVALID": 422,
    "FINALIZE_BLOCKED": 422,
    "FINALIZE_FORCE_REMOVED": 422,
    "FINALIZE_NO_RULES": 422,
    "CHAPTER_FINALIZED": 409,
    "TEXT_ANCHOR_INVALID": 422,
    "TEXT_ANCHOR_NOT_UNIQUE": 409,
    "TEXT_OPERATION_INVALID": 400,
    "JOB_NOT_RESUMABLE": 409,
    "JOB_SCOPE_MISMATCH": 400,
    "VERSION_NOT_FOUND": 404,
    "INTERNAL_ERROR": 500,
}


class ServiceError(Exception):
    """带稳定错误码的业务异常。"""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        status: Optional[int] = None,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        code = str(code or "INTERNAL_ERROR").strip()
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = int(status or _STATUS_BY_CODE.get(code, 400))
        self.retryable = bool(retryable)
        self.details = details or {}

    def to_http_exception(self) -> HTTPException:
        """转换成带错误码响应头的 HTTPException。"""
        return HTTPException(
            status_code=self.status,
            detail=self.message,
            headers={ERROR_CODE_HEADER: self.code},
        )


def to_http_exception(exc: Exception) -> HTTPException:
    """把任意异常转换成符合统一错误契约的 HTTPException。

    Args:
        exc: 业务层抛出的异常。

    Returns:
        带错误码响应头的 HTTPException。
    """
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ServiceError):
        return exc.to_http_exception()
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, InvalidIdError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, DuplicateKeyError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, DatabaseError):
        return HTTPException(status_code=500, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def service_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
) -> ServiceError:
    """构造统一业务异常的快捷方法。"""
    return ServiceError(code, message, retryable=retryable, details=details)
