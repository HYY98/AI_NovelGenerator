"""角色领域共享的名称、错误、预算与幂等工具。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from typing import Any

from bson import ObjectId


IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{16,128}$")


class GenerationDomainError(Exception):
    """携带稳定错误码与 HTTP 状态的角色领域异常。"""

    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        """初始化角色领域异常。

        Args:
            code: 供 API 与前端稳定判断的错误码。
            message: 可安全返回给用户的错误说明。
            status_code: 建议 HTTP 状态码。

        Returns:
            无。
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def normalize_character_name(name: str) -> tuple[str, str]:
    """规范化角色显示名和唯一性比较键。

    Args:
        name: 用户、模型或历史角色文档提供的角色名称。

    Returns:
        NFKC 规范化后的显示名，以及继续执行 casefold 的唯一性比较键。

    Raises:
        ValueError: 名称为空，或 NFKC 规范化后超过 80 个字符。
    """
    display_name = unicodedata.normalize("NFKC", str(name or "")).strip()
    normalized_name = display_name.casefold()
    if not normalized_name:
        raise ValueError("角色名称不能为空")
    if len(display_name) > 80:
        # Schema 校验发生在 NFKC 之前；兼容字符展开后必须再次执行持久化长度约束。
        raise ValueError("角色名称经 Unicode NFKC 规范化后不能超过 80 个字符")
    return display_name, normalized_name


def _json_safe(value: Any) -> Any:
    """把 BSON 和时间对象递归转换成规范 JSON 可编码值。

    Args:
        value: 任意待规范化或哈希的数据。

    Returns:
        只包含 JSON 基础类型的等价数据。
    """
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """使用固定参数生成可稳定比较的 JSON 文本。

    Args:
        value: 待规范化对象。

    Returns:
        键排序、无多余空白的 UTF-8 JSON 文本。
    """
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def checksum(value: Any) -> str:
    """计算规范化对象的 SHA-256。

    Args:
        value: 待哈希对象。

    Returns:
        十六进制 SHA-256 字符串。
    """
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def hash_idempotency_key(raw_key: str) -> str:
    """校验并哈希原始 Idempotency-Key，避免原文落库。

    Args:
        raw_key: HTTP 请求头中的原始幂等键。

    Returns:
        原始键的 SHA-256。
    """
    if not IDEMPOTENCY_KEY_RE.fullmatch(raw_key or ""):
        raise GenerationDomainError(
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key 必须为 16 到 128 位，只能包含 ASCII 字母、数字和 -_.:",
            status_code=400,
        )
    return hashlib.sha256(raw_key.encode("ascii")).hexdigest()


def ensure_text_budget(
    value: Any,
    *,
    max_chars: int,
    code: str,
    label: str,
) -> None:
    """在调用模型前执行不可截断的字符预算检查。

    Args:
        value: 将进入提示词的规范化对象。
        max_chars: 允许的规范 JSON 最大字符数。
        code: 超限时返回的稳定错误码。
        label: 用户可读的输入名称。

    Returns:
        校验通过时不返回内容。
    """
    actual_chars = len(canonical_json(value))
    if actual_chars > max_chars:
        raise GenerationDomainError(
            code,
            f"{label}超出输入预算：{actual_chars} > {max_chars} 字符",
            status_code=422,
        )


__all__ = [
    "GenerationDomainError",
    "canonical_json",
    "checksum",
    "ensure_text_budget",
    "hash_idempotency_key",
    "normalize_character_name",
]
