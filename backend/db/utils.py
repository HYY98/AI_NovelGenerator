import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from bson.errors import InvalidId
from .errors import InvalidIdError

logger = logging.getLogger(__name__)

def get_utc_now() -> datetime:
    """返回当前的UTC时间。"""
    return datetime.now(timezone.utc)

def canonicalize_extras(extras: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """对扩展字段做轻量序列化，防止把模型对象或不可序列化值写进 MongoDB。

    用于 generation_records 与 story_events 的 extras 字段，
    保证写入前统一转成可通过 BSON 编码的普通字典。

    Args:
        extras: 调用方传入的扩展字段；None 或空字典都返回空字典。

    Returns:
        可安全写入 MongoDB 的字典；序列化失败时降级为空字典并记警告日志。
    """
    if not extras:
        return {}
    try:
        return json.loads(json.dumps(extras, default=str, ensure_ascii=False))
    except (TypeError, ValueError):
        logger.warning("extras 序列化失败，已降级为空字典")
        return {}

def as_plain_dict(value: Any) -> Dict[str, Any]:
    """把 Pydantic 模型或映射统一转成普通字典。

    Args:
        value: 映射、Pydantic 模型或 None。

    Returns:
        普通字典；None 返回空字典。

    Raises:
        TypeError: 入参既不是映射也不是 Pydantic 模型。
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    raise TypeError("参数必须是映射或 Pydantic 模型")

def to_object_id(id_val: str | ObjectId) -> ObjectId:
    """将字符串转换为ObjectId，如果无效则抛出InvalidIdError。"""
    if isinstance(id_val, ObjectId):
        return id_val
    try:
        return ObjectId(id_val)
    except (InvalidId, TypeError):
        raise InvalidIdError(f"'{id_val}' is not a valid ObjectId.")

def to_str_id(id_val: ObjectId | str) -> str:
    """安全地将ObjectId转换为字符串。"""
    if id_val is None:
        return None
    return str(id_val)
