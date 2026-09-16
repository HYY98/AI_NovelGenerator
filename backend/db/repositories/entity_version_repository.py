"""实体内容版本仓储（v2.0 T10 版本基础）。

每次内容写入（保存 / 采纳 / 修正 / 定稿 / 还原）都会写一条快照：
- 还原是"写新快照"，历史只增不删；
- 内容未变化时复用上一条快照，避免版本噪声；
- 每条快照记录 parent_revision_id，可回溯版本链。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from backend.db.base import BaseRepository
from backend.db.repositories.id_sequence_repository import id_sequence_repo
from backend.db.utils import canonicalize_extras, content_hash, get_utc_now, to_object_id

logger = logging.getLogger(__name__)

# 第一批支持的实体类型
ENTITY_TYPES = ("chapter", "setting_card")
# 版本来源操作类型
VERSION_OPERATION_TYPES = (
    "create",
    "update",
    "accept",
    "text_revision_replace",
    "text_revision_insert",
    "text_revision_delete",
    "finalize",
    "restore",
)


class EntityVersionRepository(BaseRepository):
    """实体内容版本数据访问层。"""

    def __init__(self) -> None:
        super().__init__("entity_versions")

    async def _next_revision_id(
        self, novel_id, session: AsyncClientSession | None = None
    ) -> str:
        allocated = await id_sequence_repo.allocate_many(
            novel_id, "entity_version", "rev", 1, session=session
        )
        return allocated[0]

    async def create_snapshot(
        self,
        novel_id,
        *,
        entity_type: str,
        entity_id: str,
        content_snapshot: Any,
        operation_type: str,
        source: str = "system",
        summary: str = "",
        chapter_id: str = "",
        parent_revision_id: str = "",
        operation_id: str = "",
        skip_if_unchanged: bool = True,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """写入一条内容快照。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            entity_type: 实体类型，第一批支持 chapter / setting_card。
            entity_id: 实体业务 ID。
            content_snapshot: 内容快照，通常是内容字典或字符串。
            operation_type: 产生该版本的操作类型。
            source: 来源标识。
            summary: 变更摘要。
            chapter_id: 关联章节业务 ID。
            parent_revision_id: 上一个版本 ID。
            operation_id: 关联的幂等操作 ID。
            skip_if_unchanged: 内容未变化时是否复用上一条快照。
            session: 可选 MongoDB 会话。

        Returns:
            版本文档（复用时不产生新文档，返回上一条）。

        Raises:
            ValueError: 实体类型或操作类型非法。
        """
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"不支持的实体类型: {entity_type}")
        if operation_type not in VERSION_OPERATION_TYPES:
            raise ValueError(f"不支持的版本操作类型: {operation_type}")

        snapshot = canonicalize_extras(
            content_snapshot if isinstance(content_snapshot, dict) else {"content": content_snapshot}
        )
        digest = content_hash(snapshot)
        latest = await self.get_current(novel_id, entity_type, entity_id, session=session)
        if skip_if_unchanged and latest and str(latest.get("content_hash") or "") == digest:
            return latest

        revision_id = await self._next_revision_id(novel_id, session=session)
        now = get_utc_now()
        doc: Dict[str, Any] = {
            "revision_id": revision_id,
            "novel_id": to_object_id(novel_id),
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "chapter_id": str(chapter_id or ""),
            "operation_type": operation_type,
            "source": str(source or "system"),
            "summary": str(summary or ""),
            "content_snapshot": snapshot,
            "content_hash": digest,
            "parent_revision_id": str(parent_revision_id or (latest or {}).get("revision_id") or ""),
            "operation_id": str(operation_id or ""),
            "is_current": False,
            "created_at": now,
            "updated_at": now,
        }
        inserted = await self.insert_one(doc, session=session)
        found = await self.find_one({"_id": ObjectId(inserted)}, session=session)
        if not found:
            raise RuntimeError(f"版本快照写入后无法回读: {inserted}")
        await self.set_current(novel_id, entity_type, entity_id, revision_id, session=session)
        return self._serialize(found)

    async def set_current(
        self,
        novel_id,
        entity_type: str,
        entity_id: str,
        revision_id: str,
        session: AsyncClientSession | None = None,
    ) -> None:
        """把某条版本标记为当前版本，同实体其它版本置为非当前。"""
        await self.collection.update_many(
            {
                "novel_id": to_object_id(novel_id),
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "is_deleted": False,
            },
            {"$set": {"is_current": False, "updated_at": get_utc_now()}},
            session=session,
        )
        await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "revision_id": revision_id,
            },
            {"$set": {"is_current": True, "updated_at": get_utc_now()}},
            session=session,
        )

    async def get_current(
        self,
        novel_id,
        entity_type: str,
        entity_id: str,
        session: AsyncClientSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """读取实体的当前版本。"""
        doc = await self.find_one(
            {
                "novel_id": to_object_id(novel_id),
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "is_current": True,
            },
            session=session,
        )
        return self._serialize(doc)

    async def get_version(
        self,
        novel_id,
        revision_id: str,
        session: AsyncClientSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """按版本业务 ID 读取快照。"""
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "revision_id": revision_id},
            session=session,
        )
        return self._serialize(doc)

    async def list_versions(
        self,
        novel_id,
        entity_type: str,
        entity_id: str,
        *,
        limit: int = 50,
        skip: int = 0,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """列出实体版本，按创建时间倒序。"""
        docs = await self.find_many(
            {
                "novel_id": to_object_id(novel_id),
                "entity_type": entity_type,
                "entity_id": str(entity_id),
            },
            limit=max(1, min(200, limit)),
            skip=max(0, skip),
            sort=[("created_at", -1)],
            session=session,
        )
        return [self._serialize(d) for d in docs]

    async def count_versions(
        self,
        novel_id,
        entity_type: str,
        entity_id: str,
        session: AsyncClientSession | None = None,
    ) -> int:
        """统计实体版本数量。"""
        return await self.count_documents(
            {
                "novel_id": to_object_id(novel_id),
                "entity_type": entity_type,
                "entity_id": str(entity_id),
            },
            session=session,
        )

    @staticmethod
    def _serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """把 ObjectId 转成字符串。"""
        if not doc:
            return doc
        for key in ("_id", "novel_id"):
            if key in doc and isinstance(doc[key], ObjectId):
                doc[key] = str(doc[key])
        return doc


entity_version_repo = EntityVersionRepository()
