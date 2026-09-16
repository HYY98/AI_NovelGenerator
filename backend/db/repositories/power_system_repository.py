"""结构化战力体系仓储，异步 MongoDB。

战力体系采用独立集合存储（模块3.3 决策：独立集合），
以支持体系版本、状态流转和角色关联引用。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from backend.db.base import BaseRepository
from backend.db.errors import DuplicateKeyError, NotFoundError
from backend.db.repositories.id_sequence_repository import id_sequence_repo
from backend.db.utils import get_utc_now, to_object_id

logger = logging.getLogger(__name__)

# 体系状态
POWER_SYSTEM_STATUS = ("draft", "confirmed", "archived")

# 可编辑字段白名单
POWER_SYSTEM_EDITABLE_FIELDS = (
    "name",
    "description",
    "levels",
    "power_dimensions",
    "resource",
    "restrictions",
    "special_rules",
    "counters",
    "status",
)


def serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把文档中的 ObjectId 转成字符串，便于 JSON 序列化。"""
    if not doc:
        return doc
    for key in ("_id", "novel_id"):
        if key in doc and isinstance(doc[key], ObjectId):
            doc[key] = str(doc[key])
    return doc


def normalize_levels(levels: Any) -> List[Dict[str, Any]]:
    """规范化等级列表：补齐默认字段并按 order 升序排序。

    Args:
        levels: 原始等级序列，元素应为映射。

    Returns:
        规范化后的等级列表；非法元素被丢弃。
    """
    normalized: List[Dict[str, Any]] = []
    if not isinstance(levels, (list, tuple)):
        return normalized
    for index, item in enumerate(levels):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "order": int(item.get("order") if item.get("order") is not None else index),
                "description": str(item.get("description") or ""),
                "requirements": [str(v) for v in item.get("requirements") or [] if str(v).strip()],
                "abilities": [str(v) for v in item.get("abilities") or [] if str(v).strip()],
                "limitations": [str(v) for v in item.get("limitations") or [] if str(v).strip()],
            }
        )
    normalized.sort(key=lambda item: item["order"])
    return normalized


class PowerSystemRepository(BaseRepository):
    """战力体系数据访问层。"""

    def __init__(self) -> None:
        super().__init__("power_systems")

    async def _next_power_system_id(self, novel_id, session=None) -> str:
        allocated = await id_sequence_repo.allocate_many(
            novel_id, "power_system", "pwr", 1, session=session
        )
        return allocated[0]

    async def create_power_system(
        self,
        novel_id,
        data: Dict[str, Any],
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """创建一套战力体系。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            data: 体系业务字段。
            session: 可选 MongoDB 会话。

        Returns:
            已落库的体系文档。

        Raises:
            DuplicateKeyError: 状态非法。
            NotFoundError: 写入后无法回读。
        """
        status = str(data.get("status") or "draft").strip()
        if status not in POWER_SYSTEM_STATUS:
            raise DuplicateKeyError(f"非法的战力体系状态: {status}")
        power_system_id = await self._next_power_system_id(novel_id, session=session)
        doc: Dict[str, Any] = {
            "power_system_id": power_system_id,
            "novel_id": to_object_id(novel_id),
            "version": max(1, int(data.get("version") or 1)),
            "name": str(data.get("name") or "").strip(),
            "description": str(data.get("description") or ""),
            "levels": normalize_levels(data.get("levels")),
            "power_dimensions": [str(v) for v in data.get("power_dimensions") or [] if str(v).strip()],
            "resource": data.get("resource"),
            "restrictions": [str(v) for v in data.get("restrictions") or [] if str(v).strip()],
            "special_rules": [str(v) for v in data.get("special_rules") or [] if str(v).strip()],
            "counters": [item for item in data.get("counters") or [] if isinstance(item, dict)],
            "status": status,
        }
        inserted = await self.insert_one(doc, session=session)
        # 回读必须按 Mongo _id 查，业务 ID 与 _id 不是同一个值
        found = await self.find_one({"_id": ObjectId(inserted)}, session=session)
        if not found:
            raise NotFoundError(f"战力体系写入后无法回读: {inserted}")
        return serialize(found)

    async def get_power_system(
        self,
        novel_id,
        power_system_id: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """校验归属后按业务 ID 读取体系。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            power_system_id: 体系业务 ID。
            session: 可选 MongoDB 会话。

        Returns:
            命中的体系文档。

        Raises:
            NotFoundError: 体系不存在或不属于该小说。
        """
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "power_system_id": power_system_id},
            session=session,
        )
        if not doc:
            raise NotFoundError(f"战力体系不存在: {power_system_id}")
        return serialize(doc)

    async def find_by_novel(
        self,
        novel_id,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """按小说查询体系，按版本号倒序。"""
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if status:
            flt["status"] = status
        docs = await self.find_many(
            flt, limit=max(1, min(200, limit)), sort=[("version", -1)], session=session
        )
        return [serialize(d) for d in docs]

    async def find_confirmed(
        self,
        novel_id,
        *,
        session: AsyncClientSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """读取当前小说的已确认体系，不存在时返回 None。"""
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "status": "confirmed"},
            sort=[("version", -1)],
            session=session,
        )
        return serialize(doc)

    async def update_power_system(
        self,
        novel_id,
        power_system_id: str,
        update_data: Dict[str, Any],
        *,
        expected_version: int,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """按乐观锁更新体系可编辑字段。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            power_system_id: 体系业务 ID。
            update_data: 待更新字段，会被白名单过滤。
            expected_version: 客户端基于的体系版本。
            session: 可选 MongoDB 会话。

        Returns:
            更新后的体系文档。

        Raises:
            DuplicateKeyError: 无字段、状态非法或版本冲突。
            NotFoundError: 体系不存在。
        """
        filtered = {
            k: v for k, v in update_data.items() if k in POWER_SYSTEM_EDITABLE_FIELDS
        }
        if not filtered:
            raise DuplicateKeyError("战力体系更新至少需要提供一个可编辑字段")
        status = str(filtered.get("status") or "").strip()
        if status and status not in POWER_SYSTEM_STATUS:
            raise DuplicateKeyError(f"非法的战力体系状态: {status}")
        if "levels" in filtered:
            filtered["levels"] = normalize_levels(filtered["levels"])

        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "power_system_id": power_system_id,
                "is_deleted": False,
                "version": expected_version,
            },
            {
                "$set": {**filtered, "updated_at": get_utc_now()},
                "$inc": {"version": 1},
            },
            session=session,
        )
        if result.matched_count == 0:
            latest = await self.collection.find_one(
                {"novel_id": to_object_id(novel_id), "power_system_id": power_system_id},
                session=session,
            )
            if latest is None:
                raise NotFoundError(f"战力体系不存在: {power_system_id}")
            raise DuplicateKeyError(
                f"战力体系已被修改（服务器版本 {latest.get('version')}，"
                f"当前基于版本 {expected_version}），请刷新后重试"
            )
        return await self.get_power_system(novel_id, power_system_id, session=session)


power_system_repo = PowerSystemRepository()
