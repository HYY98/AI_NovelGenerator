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
from backend.db.utils import as_plain_dict, canonicalize_extras, get_utc_now, to_object_id

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

# 事件确认状态：只有 accepted / confirmed 才能更新正式实体
# 沿用历史取值 accepted / rejected / edited，并补充模块3.7 的 confirmed / applied / reverted
EVENT_STATUS = (
    "pending",
    "accepted",
    "rejected",
    "edited",
    "confirmed",
    "applied",
    "reverted",
)

# 事件作用实体类型
EVENT_ENTITY_TYPES = ("character", "location", "item", "rule")

# 事件功能分类：由 event_type 派生，供上层按语义聚合
EVENT_FUNCTIONS = ("state_change", "relationship", "ownership", "other")

# event_type -> function 映射
_EVENT_TYPE_TO_FUNCTION: Dict[str, str] = {
    "create": "state_change",
    "discover": "state_change",
    "transfer": "ownership",
    "use": "state_change",
    "damage": "state_change",
    "repair": "state_change",
    "destroy": "state_change",
    "seal": "state_change",
    "unseal": "state_change",
    "lose": "ownership",
    "recover": "ownership",
    "move": "state_change",
    "state_change": "state_change",
    "relationship": "relationship",
    "other": "other",
}


def _derive_function(event_type: str) -> str:
    """由 event_type 派生事件功能分类。

    Args:
        event_type: 事件类型。

    Returns:
        state_change / relationship / ownership / other；未登记类型归为 other。
    """
    return _EVENT_TYPE_TO_FUNCTION.get(event_type, "other")


def _build_status_history_entry(
    status: str,
    *,
    actor: str = "system",
    note: str = "",
) -> Dict[str, Any]:
    """构造一条事件状态变更历史条目。

    Args:
        status: 变更后的状态。
        actor: 变更发起者，ai / user / system。
        note: 变更说明。

    Returns:
        状态历史条目字典。
    """
    return {
        "status": status,
        "changed_at": get_utc_now(),
        "actor": actor or "system",
        "note": str(note or ""),
    }


def _build_impacted_entities(
    entity_type: str,
    entity_id: str,
    after: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """构造受影响的实体引用列表。

    Args:
        entity_type: 主实体类型。
        entity_id: 主实体业务 ID。
        after: 事件后的事实值，用于补充关联实体引用。

    Returns:
        受影响实体列表，至少包含主实体。
    """
    impacted: List[Dict[str, Any]] = []
    if entity_id:
        impacted.append({"entity_type": entity_type, "entity_id": entity_id})
    if not after:
        return impacted
    # after 中出现 owner / target / holder 等关联字段时，一并登记为受影响实体
    for key in ("owner", "target", "holder", "target_entity_id", "owner_entity_id"):
        value = after.get(key)
        if isinstance(value, str) and value.strip():
            impacted.append({"entity_type": "character", "entity_id": value.strip()})
        elif isinstance(value, dict):
            ref_type = str(value.get("entity_type") or "").strip()
            ref_id = str(value.get("entity_id") or "").strip()
            if ref_type and ref_id:
                impacted.append({"entity_type": ref_type, "entity_id": ref_id})
    return impacted


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

    async def _derive_is_latest(
        self,
        novel_id,
        entity_type: str,
        entity_id: str,
        event_type: str,
        session=None,
    ) -> bool:
        """把同实体同事件类型的旧事件置为非最新，保证 is_latest 语义唯一。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            entity_type: 实体类型。
            entity_id: 实体业务 ID。
            event_type: 事件类型。
            session: 可选 MongoDB 会话。

        Returns:
            当前新事件是否为最新，恒为 True。
        """
        await self.collection.update_many(
            {
                "novel_id": to_object_id(novel_id),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "event_type": event_type,
                "is_latest": True,
            },
            {"$set": {"is_latest": False}},
            session=session,
        )
        return True

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
            after = as_plain_dict(item.get("after") or {})
            docs.append(
                {
                    "event_id": event_id,
                    "novel_id": to_object_id(novel_id),
                    "chapter_id": str(item.get("chapter_id") or ""),
                    "entity_type": entity_type,
                    "entity_id": str(item.get("entity_id") or ""),
                    "event_type": event_type,
                    "function": _derive_function(event_type),
                    "before": as_plain_dict(item.get("before") or {}),
                    "after": after,
                    "evidence": item.get("evidence", {}),
                    "confidence": float(item.get("confidence") or 0.0),
                    "status": status,
                    "source": str(item.get("source") or "ai"),
                    "generation_id": str(item.get("generation_id") or "") or None,
                    # 模块3.7：正文证据定位与版本快照
                    "evidence_text": str(item.get("evidence_text") or ""),
                    "evidence_start": item.get("evidence_start"),
                    "evidence_end": item.get("evidence_end"),
                    "target_version": item.get("target_version"),
                    "proposal_id": str(item.get("proposal_id") or ""),
                    "conflict_id": str(item.get("conflict_id") or ""),
                    "impacted_entities": _build_impacted_entities(
                        entity_type, str(item.get("entity_id") or ""), after
                    ),
                    "extras": canonicalize_extras(item.get("extras")),
                    "is_latest": True,
                    "status_history": [
                        _build_status_history_entry(
                            status, actor=str(item.get("source") or "ai")
                        )
                    ],
                    "confirmed_by": item.get("confirmed_by"),
                    "confirmed_at": item.get("confirmed_at"),
                }
            )
        # 必须先让旧事件失效再插入，否则会把本次新写入的事件也一起置为非最新
        for doc in docs:
            await self._derive_is_latest(
                novel_id,
                doc["entity_type"],
                doc["entity_id"],
                doc["event_type"],
                session=session,
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

    async def find_by_card(
        self,
        novel_id,
        card_id: str,
        *,
        limit: int = 200,
        session=None,
    ) -> List[Dict[str, Any]]:
        """按设定卡业务 ID 查询其关联事件。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            card_id: 设定卡业务 ID。
            limit: 返回条数上限，会被裁剪到 [1, 500]。
            session: 可选 MongoDB 会话。

        Returns:
            按创建时间正序的事件列表。
        """
        docs = await self.find_many(
            {"novel_id": to_object_id(novel_id), "entity_id": card_id},
            limit=max(1, min(500, limit)),
            sort=[("created_at", 1)],
            session=session,
        )
        return [serialize(d) for d in docs]

    async def find_by_conflict(
        self,
        novel_id,
        conflict_id: str,
        *,
        limit: int = 200,
        session=None,
    ) -> List[Dict[str, Any]]:
        """按冲突项 ID 查询其关联事件。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            conflict_id: 一致性审校产生的冲突项 ID。
            limit: 返回条数上限，会被裁剪到 [1, 500]。
            session: 可选 MongoDB 会话。

        Returns:
            按创建时间正序的事件列表。
        """
        docs = await self.find_many(
            {"novel_id": to_object_id(novel_id), "conflict_id": conflict_id},
            limit=max(1, min(500, limit)),
            sort=[("created_at", 1)],
            session=session,
        )
        return [serialize(d) for d in docs]

    async def find_by_proposal(
        self,
        novel_id,
        proposal_id: str,
        *,
        limit: int = 200,
        session=None,
    ) -> List[Dict[str, Any]]:
        """按状态变化建议 ID 查询其关联事件。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            proposal_id: 状态变化建议 ID。
            limit: 返回条数上限，会被裁剪到 [1, 500]。
            session: 可选 MongoDB 会话。

        Returns:
            按创建时间正序的事件列表。
        """
        docs = await self.find_many(
            {"novel_id": to_object_id(novel_id), "proposal_id": proposal_id},
            limit=max(1, min(500, limit)),
            sort=[("created_at", 1)],
            session=session,
        )
        return [serialize(d) for d in docs]

    async def find_state_changes(
        self,
        novel_id,
        *,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        chapter_id: Optional[str] = None,
        only_latest: bool = False,
        status: Optional[str] = None,
        limit: int = 200,
        session=None,
    ) -> List[Dict[str, Any]]:
        """查询事实变化类事件，支持按实体、章节、最新标记与状态过滤。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            entity_type: 可选实体类型过滤。
            entity_id: 可选实体业务 ID 过滤。
            chapter_id: 可选章节业务 ID 过滤。
            only_latest: 是否只返回每个实体-事件类型组合的最新一条。
            status: 可选事件状态过滤。
            limit: 返回条数上限，会被裁剪到 [1, 500]。
            session: 可选 MongoDB 会话。

        Returns:
            按创建时间正序的事件列表。
        """
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if entity_type:
            flt["entity_type"] = entity_type
        if entity_id:
            flt["entity_id"] = entity_id
        if chapter_id:
            flt["chapter_id"] = chapter_id
        if status:
            flt["status"] = status
        if only_latest:
            flt["is_latest"] = True
        docs = await self.find_many(
            flt,
            limit=max(1, min(500, limit)),
            sort=[("created_at", 1)],
            session=session,
        )
        return [serialize(d) for d in docs]

    async def update_status(
        self,
        novel_id,
        event_id: str,
        status: str,
        *,
        actor: str = "user",
        note: str = "",
        session=None,
    ) -> bool:
        """更新事件状态并追加状态变更历史。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            event_id: 事件业务 ID。
            status: 目标状态，必须属于 EVENT_STATUS。
            actor: 变更发起者。
            note: 变更说明。
            session: 可选 MongoDB 会话。

        Returns:
            实际更新成功时返回 True。

        Raises:
            DuplicateKeyError: 状态非法。
        """
        if status not in EVENT_STATUS:
            raise DuplicateKeyError(f"非法的事件状态: {status}")
        fields: Dict[str, Any] = {"status": status}
        if status == "accepted":
            fields["confirmed_at"] = get_utc_now()
        # 基类 update_one 会把入参整体包进 $set，无法表达 $push，这里直接使用集合句柄
        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "event_id": event_id,
                "is_deleted": False,
            },
            {
                "$set": fields,
                "$push": {
                    "status_history": _build_status_history_entry(
                        status, actor=actor, note=note
                    )
                },
            },
            session=session,
        )
        return result.modified_count > 0

    async def update_event(
        self,
        novel_id,
        event_id: str,
        update_data: Dict[str, Any],
        *,
        session=None,
    ) -> bool:
        """更新事件的可编辑字段，并记录状态变更历史。

        允许更新 before / after / evidence / confidence / note 以及状态相关字段，
        不接受 event_id、novel_id 等受控字段。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            event_id: 事件业务 ID。
            update_data: 待更新字段。
            session: 可选 MongoDB 会话。

        Returns:
            实际更新成功时返回 True。

        Raises:
            DuplicateKeyError: 状态非法或没有可更新字段。
        """
        allowed = {"before", "after", "evidence", "confidence", "status", "note"}
        fields = {
            key: value
            for key, value in as_plain_dict(update_data).items()
            if key in allowed
        }
        if not fields:
            raise DuplicateKeyError("事件更新至少需要提供一个可编辑字段")
        status = str(fields.get("status") or "").strip()
        if status and status not in EVENT_STATUS:
            raise DuplicateKeyError(f"非法的事件状态: {status}")
        history_entry = None
        if status:
            fields["status"] = status
            history_entry = _build_status_history_entry(
                status, actor="user", note=str(fields.pop("note", "") or "")
            )
            if status == "accepted":
                fields["confirmed_at"] = get_utc_now()
        update_doc: Dict[str, Any] = {"$set": fields}
        if history_entry is not None:
            update_doc["$push"] = {"status_history": history_entry}
        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "event_id": event_id,
                "is_deleted": False,
            },
            update_doc,
            session=session,
        )
        return result.modified_count > 0

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
        # 基类 update_one 会把入参整体包进 $set，无法表达 $push，这里直接使用集合句柄
        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "event_id": event_id,
                "is_deleted": False,
            },
            {
                "$set": {**fields, "updated_at": get_utc_now()},
                "$push": {
                    "status_history": _build_status_history_entry(
                        status, actor="user", note=str(note or "")
                    )
                },
            },
            session=session,
        )
        return result.modified_count > 0

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
