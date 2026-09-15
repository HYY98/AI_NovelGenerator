"""故事事件仓储，异步 MongoDB。

地点、物品、规则和角色的事实变化统一写入 story_events，
不直接覆盖卡片当前字段。只有 accepted 事件才会被业务层用于更新正式实体。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId

from backend.db.base import BaseRepository
from backend.db.errors import DuplicateKeyError, NotFoundError
from backend.db.repositories.id_sequence_repository import id_sequence_repo
from backend.db.utils import get_utc_now, to_object_id

logger = logging.getLogger(__name__)

# 事件类型：覆盖物品流转、地点状态与能力变化等长篇连续性场景
EVENT_TYPES = (
    "create",
    "discover",
    "transfer",
    "use",
    "damage",
    "repair",
    "destroy",
    "seal",
    "unseal",
    "lose",
    "recover",
    "move",
    "state_change",
    "relationship",
    "other",
)

# 事件确认状态：只有 accepted 才能更新正式实体
EVENT_STATUS = ("pending", "accepted", "rejected", "edited")

# 事件作用实体类型
EVENT_ENTITY_TYPES = ("character", "location", "item", "rule")


def serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把文档中的 ObjectId 转成字符串，便于 JSON 序列化。"""
    if not doc:
        return doc
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    if "novel_id" in doc and isinstance(doc["novel_id"], ObjectId):
        doc["novel_id"] = str(doc["novel_id"])
    return doc


class StoryEventRepository(BaseRepository):
    """故事事件数据访问层。"""

    def __init__(self) -> None:
        super().__init__("story_events")

    async def _next_event_ids(self, novel_id, count: int, session=None) -> List[str]:
        if count <= 0:
            return []
        return await id_sequence_repo.allocate_many(
            novel_id, "story_event", "evt", count, session=session
        )

    async def create_event(
        self,
        novel_id,
        data: Dict[str, Any],
        session=None,
    ) -> Dict[str, Any]:
        """写入单条事件。"""
        events = await self.create_events(novel_id, [data], session=session)
        return events[0]

    async def create_events(
        self,
        novel_id,
        items: List[Dict[str, Any]],
        session=None,
    ) -> List[Dict[str, Any]]:
        """批量写入事件，并一次分配连续业务 ID。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            items: 事件字段列表。
            session: 可选 MongoDB 会话，用于事务写入。

        Returns:
            已写入的事件列表，顺序与入参一致。
        """
        if not items:
            return []
        event_ids = await self._next_event_ids(novel_id, len(items), session=session)
        docs: List[Dict[str, Any]] = []
        for event_id, item in zip(event_ids, items):
            entity_type = str(item.get("entity_type") or "").strip()
            if entity_type not in EVENT_ENTITY_TYPES:
                raise DuplicateKeyError(f"非法的事件实体类型: {entity_type}")
            event_type = str(item.get("event_type") or "").strip() or "other"
            if event_type not in EVENT_TYPES:
                raise DuplicateKeyError(f"非法的事件类型: {event_type}")
            status = str(item.get("status") or "pending")
            if status not in EVENT_STATUS:
                raise DuplicateKeyError(f"非法的事件状态: {status}")
            docs.append(
                {
                    "event_id": event_id,
                    "novel_id": to_object_id(novel_id),
                    "chapter_id": str(item.get("chapter_id") or ""),
                    "entity_type": entity_type,
                    "entity_id": str(item.get("entity_id") or ""),
                    "event_type": event_type,
                    "before": item.get("before", {}),
                    "after": item.get("after", {}),
                    "evidence": item.get("evidence", {}),
                    "confidence": float(item.get("confidence") or 0.0),
                    "status": status,
                    "source": str(item.get("source") or "ai"),
                    "generation_id": str(item.get("generation_id") or ""),
                    "confirmed_by": item.get("confirmed_by"),
                    "confirmed_at": item.get("confirmed_at"),
                }
            )
        await self.insert_many(docs, session=session)
        return [serialize(d) for d in docs]

    async def get_event(
        self,
        event_id: str,
        include_deleted: bool = False,
        session=None,
    ) -> Dict[str, Any]:
        """按业务 ID 读取事件。"""
        doc = await self.find_one(
            {"event_id": event_id}, include_deleted=include_deleted, session=session
        )
        if not doc:
            raise NotFoundError(f"事件不存在: {event_id}")
        return serialize(doc)

    async def list_events(
        self,
        novel_id,
        chapter_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
        include_deleted: bool = False,
        session=None,
    ) -> List[Dict[str, Any]]:
        """按小说与可选条件列出事件，按创建时间正序。"""
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if chapter_id:
            flt["chapter_id"] = chapter_id
        if entity_type:
            flt["entity_type"] = entity_type
        if entity_id:
            flt["entity_id"] = entity_id
        if status:
            flt["status"] = status
        docs = await self.find_many(
            flt,
            include_deleted=include_deleted,
            limit=max(1, min(500, limit)),
            sort=[("created_at", 1)],
            session=session,
        )
        return [serialize(d) for d in docs]

    async def list_events_by_ids(
        self,
        novel_id,
        event_ids: List[str],
        session=None,
    ) -> List[Dict[str, Any]]:
        """按业务 ID 列表批量读取事件。"""
        cleaned = [e for e in dict.fromkeys(event_ids) if e]
        if not cleaned:
            return []
        docs = await self.find_many(
            {"novel_id": to_object_id(novel_id), "event_id": {"$in": cleaned}},
            session=session,
        )
        return [serialize(d) for d in docs]

    async def update_event_status(
        self,
        novel_id,
        event_id: str,
        status: str,
        after: Optional[Dict[str, Any]] = None,
        note: Optional[str] = None,
        session=None,
    ) -> bool:
        """更新事件确认状态，可同时写入用户编辑后的 after 值与备注。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            event_id: 事件业务 ID。
            status: pending / accepted / rejected / edited。
            after: 用户编辑后接受时的新事实值。
            note: 处理说明，例如被新的审校结果取代。
            session: 可选 MongoDB 会话。

        Returns:
            实际更新成功时返回 True。
        """
        if status not in EVENT_STATUS:
            raise DuplicateKeyError(f"非法的事件状态: {status}")
        fields: Dict[str, Any] = {"status": status}
        if status == "accepted":
            fields["confirmed_at"] = get_utc_now()
        if after is not None:
            fields["after"] = after
        if note is not None:
            fields["note"] = note
        return await self.update_one(
            {"novel_id": to_object_id(novel_id), "event_id": event_id},
            fields,
            session=session,
        )

    async def count_pending(self, novel_id, chapter_id: Optional[str] = None) -> int:
        """统计待确认事件数量。"""
        flt: Dict[str, Any] = {
            "novel_id": to_object_id(novel_id),
            "status": "pending",
        }
        if chapter_id:
            flt["chapter_id"] = chapter_id
        return await self.count_documents(flt)


story_event_repo = StoryEventRepository()
