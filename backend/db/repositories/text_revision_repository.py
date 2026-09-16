"""正文修改建议仓储，异步 MongoDB。

AI 生成的正文修改建议只落在这里作为候选，用户确认后才由章节服务写回正文。
建议记录生成时的章节版本与目标范围，供确认时做精确匹配校验。
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

# 修改类型：本次只实现补充、事实修正和受控术语统一，不做大范围整改
REVISION_TYPES = ("supplement", "correction", "terminology")

# 建议状态：正文版本变化后旧建议会被标记为 conflicted
REVISION_STATUS = ("pending", "accepted", "rejected", "conflicted")

# 单条建议文本长度上限，防止 AI 返回整本正文
MAX_TEXT_LENGTH = 20000


def serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把文档中的 ObjectId 转成字符串，便于 JSON 序列化。"""
    if not doc:
        return doc
    for key in ("_id", "novel_id"):
        if key in doc and isinstance(doc[key], ObjectId):
            doc[key] = str(doc[key])
    return doc


class TextRevisionRepository(BaseRepository):
    """正文修改建议数据访问层。"""

    def __init__(self) -> None:
        super().__init__("text_revisions")

    async def _next_revision_id(self, novel_id, session=None) -> str:
        allocated = await id_sequence_repo.allocate_many(
            novel_id, "text_revision", "rev", 1, session=session
        )
        return allocated[0]

    async def create_revision(
        self,
        novel_id,
        data: Dict[str, Any],
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """写入一条正文修改建议候选。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            data: 建议业务字段。
            session: 可选 MongoDB 会话。

        Returns:
            已落库的建议文档。

        Raises:
            DuplicateKeyError: 修改类型或状态非法、缺少章节或文本超限。
            NotFoundError: 写入后无法回读。
        """
        revision_type = str(data.get("revision_type") or "supplement").strip()
        if revision_type not in REVISION_TYPES:
            raise DuplicateKeyError(f"非法的修改类型: {revision_type}")
        status = str(data.get("status") or "pending").strip()
        if status not in REVISION_STATUS:
            raise DuplicateKeyError(f"非法的建议状态: {status}")
        chapter_id = str(data.get("chapter_id") or "").strip()
        if not chapter_id:
            raise DuplicateKeyError("正文修改建议必须关联章节业务 ID")

        before_text = str(data.get("before_text") or "")
        after_text = str(data.get("after_text") or "")
        if len(after_text) > MAX_TEXT_LENGTH:
            raise DuplicateKeyError("建议文本超出长度上限，请缩小修改范围")

        target_range = data.get("target_range") or {}
        revision_id = await self._next_revision_id(novel_id, session=session)
        doc: Dict[str, Any] = {
            "revision_id": revision_id,
            "novel_id": to_object_id(novel_id),
            "chapter_id": chapter_id,
            "generation_id": str(data.get("generation_id") or "") or None,
            "chapter_version": int(data.get("chapter_version") or 1),
            "target_range": {
                "start": int(target_range.get("start") or 0),
                "end": int(target_range.get("end") or 0),
            },
            "before_text": before_text,
            "after_text": after_text,
            "revision_type": revision_type,
            "reason": str(data.get("reason") or ""),
            "card_ids": [str(v) for v in data.get("card_ids") or [] if str(v).strip()],
            "status": status,
            "accepted_at": None,
        }
        inserted = await self.insert_one(doc, session=session)
        # 回读必须按 Mongo _id 查，业务 ID 与 _id 不是同一个值
        found = await self.find_one({"_id": ObjectId(inserted)}, session=session)
        if not found:
            raise NotFoundError(f"正文修改建议写入后无法回读: {inserted}")
        return serialize(found)

    async def get_revision(
        self,
        novel_id,
        revision_id: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """校验归属后按业务 ID 读取建议。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            revision_id: 建议业务 ID。
            session: 可选 MongoDB 会话。

        Returns:
            命中的建议文档。

        Raises:
            NotFoundError: 建议不存在或不属于该小说。
        """
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "revision_id": revision_id},
            session=session,
        )
        if not doc:
            raise NotFoundError(f"正文修改建议不存在: {revision_id}")
        return serialize(doc)

    async def find_by_chapter(
        self,
        novel_id,
        chapter_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 100,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """按章节查询建议，按创建时间正序。"""
        flt: Dict[str, Any] = {
            "novel_id": to_object_id(novel_id),
            "chapter_id": chapter_id,
        }
        if status:
            flt["status"] = status
        docs = await self.find_many(
            flt, limit=max(1, min(500, limit)), sort=[("created_at", 1)], session=session
        )
        return [serialize(d) for d in docs]

    async def mark_conflicted_by_chapter(
        self,
        novel_id,
        chapter_id: str,
        chapter_version: int,
        *,
        session: AsyncClientSession | None = None,
    ) -> int:
        """把章节版本已变化的待处理建议标记为 conflicted。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            chapter_id: 章节业务 ID。
            chapter_version: 当前章节版本；低于该版本的待处理建议会被标记。
            session: 可选 MongoDB 会话。

        Returns:
            被标记的建议数量。
        """
        result = await self.collection.update_many(
            {
                "novel_id": to_object_id(novel_id),
                "chapter_id": chapter_id,
                "status": "pending",
                "chapter_version": {"$lt": chapter_version},
            },
            {"$set": {"status": "conflicted", "updated_at": get_utc_now()}},
            session=session,
        )
        return result.modified_count

    async def update_status(
        self,
        novel_id,
        revision_id: str,
        status: str,
        *,
        after_text: Optional[str] = None,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """更新建议状态，采纳时可同时写入用户编辑后的文本。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            revision_id: 建议业务 ID。
            status: 目标状态，必须属于 REVISION_STATUS。
            after_text: 用户编辑后的文本，仅采纳时需要。
            session: 可选 MongoDB 会话。

        Returns:
            实际更新成功时返回 True。

        Raises:
            DuplicateKeyError: 状态非法或文本超出长度上限。
        """
        if status not in REVISION_STATUS:
            raise DuplicateKeyError(f"非法的建议状态: {status}")
        fields: Dict[str, Any] = {"status": status}
        if status == "accepted":
            fields["accepted_at"] = get_utc_now()
        if after_text is not None:
            if len(after_text) > MAX_TEXT_LENGTH:
                raise DuplicateKeyError("建议文本超出长度上限，请缩小修改范围")
            fields["after_text"] = after_text
        return await self.update_one(
            {"novel_id": to_object_id(novel_id), "revision_id": revision_id},
            fields,
            session=session,
        )


text_revision_repo = TextRevisionRepository()
