"""长期上下文记忆仓储，异步 MongoDB。

用于存放跨章节复用的压缩记忆，支撑长篇生成时的上下文控制：
- scope=chapter / volume / novel 三种粒度，每个粒度在小说内只保留一条"当前记忆"；
- 重复写入同一 scope 时原地更新并递增 version，避免历史碎片无限增长；
- 记忆只作为 AI 上下文素材，不承载任何正式设定事实（正式事实仍在卡片与 story_events）。
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

# 记忆粒度
MEMORY_SCOPES = ("chapter", "volume", "novel")
# 允许写入的业务字段
MEMORY_EDITABLE_FIELDS = ("content", "facts", "source_chapter_ids", "importance")
# 单条记忆正文长度上限，避免把整章正文塞进记忆
MEMORY_CONTENT_LIMIT = 4000


def serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把文档中的 ObjectId 转成字符串，便于 JSON 序列化。"""
    if not doc:
        return doc
    for key in ("_id", "novel_id"):
        if key in doc and isinstance(doc[key], ObjectId):
            doc[key] = str(doc[key])
    return doc


def _normalize_str_list(value: Any) -> List[str]:
    """把任意序列规范成去空去重的字符串列表，保持首次出现顺序。"""
    if isinstance(value, str):
        parts = [item.strip() for item in value.replace("，", ",").split(",")]
        return list(dict.fromkeys(item for item in parts if item))
    if not isinstance(value, (list, tuple)):
        return []
    return list(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def _normalize_scope(scope: Any, scope_id: Any) -> tuple[str, str]:
    """校验并规范化记忆粒度与粒度 ID。

    Raises:
        DuplicateKeyError: 粒度非法，或非 novel 粒度缺少 scope_id。
    """
    normalized_scope = str(scope or "").strip()
    if normalized_scope not in MEMORY_SCOPES:
        raise DuplicateKeyError(f"非法的记忆粒度: {scope}")
    normalized_scope_id = str(scope_id or "").strip()
    if normalized_scope != "novel" and not normalized_scope_id:
        raise DuplicateKeyError(f"{normalized_scope} 粒度必须提供 scope_id")
    return normalized_scope, normalized_scope_id


def _normalize_importance(value: Any) -> int:
    """把重要度规范为 1-5 的整数。"""
    try:
        importance = int(value)
    except (TypeError, ValueError):
        importance = 3
    return max(1, min(5, importance))


class ContextMemoryRepository(BaseRepository):
    """长期上下文记忆数据访问层。"""

    def __init__(self) -> None:
        super().__init__("context_memories")

    async def _next_memory_id(self, novel_id, session=None) -> str:
        """分配小说作用域内的记忆业务 ID。"""
        allocated = await id_sequence_repo.allocate_many(
            novel_id, "context_memory", "mem", 1, session=session
        )
        return allocated[0]

    async def create_memory(
        self,
        novel_id,
        data: Dict[str, Any],
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """创建一条上下文记忆。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            data: 记忆业务字段（scope / scope_id / content / facts 等）。
            session: 可选 MongoDB 会话。

        Returns:
            已落库的记忆文档。

        Raises:
            DuplicateKeyError: 粒度非法、缺少 scope_id 或内容为空；同一 scope 已存在。
            NotFoundError: 写入后无法回读。
        """
        scope, scope_id = _normalize_scope(data.get("scope"), data.get("scope_id"))
        content = str(data.get("content") or "").strip()
        if not content:
            raise DuplicateKeyError("上下文记忆内容不能为空")

        existing = await self.find_for_scope(novel_id, scope, scope_id, session=session)
        if existing:
            raise DuplicateKeyError(
                f"该粒度已存在上下文记忆: scope={scope}, scope_id={scope_id}；请使用 upsert_memory"
            )

        memory_id = await self._next_memory_id(novel_id, session=session)
        doc: Dict[str, Any] = {
            "memory_id": memory_id,
            "novel_id": to_object_id(novel_id),
            "version": 1,
            "scope": scope,
            "scope_id": scope_id,
            "content": content[:MEMORY_CONTENT_LIMIT],
            "facts": _normalize_str_list(data.get("facts")),
            "source_chapter_ids": _normalize_str_list(data.get("source_chapter_ids")),
            "importance": _normalize_importance(data.get("importance")),
        }
        inserted = await self.insert_one(doc, session=session)
        # 回读必须按 Mongo _id 查，业务 ID 与 _id 不是同一个值
        found = await self.find_one({"_id": ObjectId(inserted)}, session=session)
        if not found:
            raise NotFoundError(f"上下文记忆写入后无法回读: {inserted}")
        return serialize(found)

    async def get_memory(
        self,
        novel_id,
        memory_id: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """校验归属后按业务 ID 读取记忆。

        Raises:
            NotFoundError: 记忆不存在或不属于该小说。
        """
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "memory_id": memory_id},
            session=session,
        )
        if not doc:
            raise NotFoundError(f"上下文记忆不存在: {memory_id}")
        return serialize(doc)

    async def find_for_scope(
        self,
        novel_id,
        scope: str,
        scope_id: str = "",
        *,
        session: AsyncClientSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """读取指定粒度的当前记忆，不存在时返回 None。"""
        normalized_scope, normalized_scope_id = _normalize_scope(scope, scope_id)
        doc = await self.find_one(
            {
                "novel_id": to_object_id(novel_id),
                "scope": normalized_scope,
                "scope_id": normalized_scope_id,
            },
            session=session,
        )
        return serialize(doc)

    async def list_memories(
        self,
        novel_id,
        *,
        scope: Optional[str] = None,
        scope_id: Optional[str] = None,
        limit: int = 50,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """按小说列出记忆，按更新时间倒序。"""
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if scope:
            flt["scope"] = str(scope).strip()
        if scope_id is not None:
            flt["scope_id"] = str(scope_id).strip()
        docs = await self.find_many(
            flt,
            limit=max(1, min(200, limit)),
            sort=[("updated_at", -1)],
            session=session,
        )
        return [serialize(doc) for doc in docs]

    async def upsert_memory(
        self,
        novel_id,
        *,
        scope: str,
        scope_id: str = "",
        content: str,
        facts: Any = None,
        source_chapter_ids: Any = None,
        importance: Any = None,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """写入指定粒度的当前记忆：已存在则原地更新并递增版本。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            scope: 记忆粒度（chapter / volume / novel）。
            scope_id: 章节或卷业务 ID；novel 粒度可为空。
            content: 记忆正文，超长会被截断。
            facts: 关键事实列表。
            source_chapter_ids: 来源章节业务 ID 列表。
            importance: 重要度 1-5。
            session: 可选 MongoDB 会话。

        Returns:
            更新后（或新建）的记忆文档。

        Raises:
            DuplicateKeyError: 粒度非法或内容为空。
            NotFoundError: 更新后无法回读。
        """
        normalized_scope, normalized_scope_id = _normalize_scope(scope, scope_id)
        text = str(content or "").strip()
        if not text:
            raise DuplicateKeyError("上下文记忆内容不能为空")

        existing = await self.find_for_scope(
            novel_id, normalized_scope, normalized_scope_id, session=session
        )
        if existing:
            update_fields: Dict[str, Any] = {
                "content": text[:MEMORY_CONTENT_LIMIT],
            }
            if facts is not None:
                update_fields["facts"] = _normalize_str_list(facts)
            if source_chapter_ids is not None:
                update_fields["source_chapter_ids"] = _normalize_str_list(
                    source_chapter_ids
                )
            if importance is not None:
                update_fields["importance"] = _normalize_importance(importance)
            result = await self.collection.update_one(
                {
                    "novel_id": to_object_id(novel_id),
                    "memory_id": existing.get("memory_id"),
                    "is_deleted": False,
                },
                {
                    "$set": {**update_fields, "updated_at": get_utc_now()},
                    "$inc": {"version": 1},
                },
                session=session,
            )
            if result.matched_count == 0:
                raise NotFoundError(
                    f"上下文记忆更新失败: {existing.get('memory_id')}"
                )
            return await self.get_memory(
                novel_id, str(existing.get("memory_id")), session=session
            )
        return await self.create_memory(
            novel_id,
            {
                "scope": normalized_scope,
                "scope_id": normalized_scope_id,
                "content": text,
                "facts": facts,
                "source_chapter_ids": source_chapter_ids,
                "importance": importance,
            },
            session=session,
        )

    async def delete_memory(
        self,
        novel_id,
        memory_id: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """软删除记忆，可在回收站恢复。"""
        removed = await self.soft_delete_one(
            {"novel_id": to_object_id(novel_id), "memory_id": memory_id},
            session=session,
        )
        if not removed:
            raise NotFoundError(f"上下文记忆不存在: {memory_id}")
        return removed

    async def restore_memory(
        self,
        novel_id,
        memory_id: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """从回收站恢复记忆。"""
        restored = await self.restore_one(
            {"novel_id": to_object_id(novel_id), "memory_id": memory_id},
            session=session,
        )
        if not restored:
            raise NotFoundError(f"回收站中不存在该记忆: {memory_id}")
        return await self.get_memory(novel_id, memory_id, session=session)


context_memory_repo = ContextMemoryRepository()
