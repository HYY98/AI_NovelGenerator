"""章节数据访问层，异步 MongoDB。章节是实际写作内容的主存储。"""
import re
import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId

from backend.db.base import BaseRepository
from backend.db.utils import to_object_id
from backend.db.errors import NotFoundError, DuplicateKeyError
from backend.db.repositories.id_sequence_repository import id_sequence_repo

logger = logging.getLogger(__name__)

# 章节状态：草稿 / 修改中 / 已定稿
CHAPTER_STATUS = ("draft", "editing", "finalized")
STATUS_LABEL = {"draft": "草稿", "editing": "修改中", "finalized": "已定稿"}


def serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return doc
    for key in ("_id", "novel_id", "volume_id"):
        if key in doc and isinstance(doc[key], ObjectId):
            doc[key] = str(doc[key])
    return doc


class ChapterRepository(BaseRepository):
    def __init__(self):
        super().__init__("chapters")

    async def _next_chapter_id(self, novel_id, session=None) -> str:
        allocated = await id_sequence_repo.allocate_many(novel_id, "chapter", "chap", 1, session=session)
        return allocated[0]

    async def _next_number(self, novel_id, session=None) -> int:
        cursor = self.collection.find(
            {"novel_id": to_object_id(novel_id), "is_deleted": False},
            projection={"number": 1}, session=session,
        ).sort("number", -1).limit(1)
        docs = await cursor.to_list(length=1)
        return (docs[0].get("number", 0) + 1) if docs else 1

    async def create_chapter(self, novel_id, data: Dict[str, Any], session=None) -> Dict[str, Any]:
        chapter_id = await self._next_chapter_id(novel_id, session=session)
        number = data.get("number")
        if number is None:
            number = await self._next_number(novel_id, session=session)
        # 同小说章节号唯一
        exists = await self.find_one(
            {"novel_id": to_object_id(novel_id), "number": number}, session=session
        )
        if exists:
            raise DuplicateKeyError(f"第 {number} 章已存在")
        content = data.get("content", "")
        doc = {
            "chapter_id": chapter_id,
            "novel_id": to_object_id(novel_id),
            "volume_id": to_object_id(data["volume_id"]) if data.get("volume_id") else None,
            "number": number,
            "title": data.get("title", f"第{number}章") or f"第{number}章",
            "content": content,
            "status": data.get("status", "draft"),
            "word_count": len(re.sub(r"\s", "", content)),
            "blueprint_snapshot": data.get("blueprint_snapshot", {}),
            "linked_character_ids": data.get("linked_character_ids", []),
            "linked_location_ids": data.get("linked_location_ids", []),
            "linked_item_ids": data.get("linked_item_ids", []),
            "linked_rule_ids": data.get("linked_rule_ids", []),
            "summary": data.get("summary", ""),
            "unresolved_threads": data.get("unresolved_threads", []),
            "version": 1,
        }
        oid = await self.insert_one(doc, session=session)
        return await self.get_chapter(oid, session=session)

    async def list_chapters(self, novel_id, volume_id=None, include_deleted=False, session=None):
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if volume_id:
            flt["volume_id"] = to_object_id(volume_id)
        docs = await self.find_many(
            flt, include_deleted=include_deleted,
            sort=[("number", 1)], session=session,
        )
        # 列表不返回正文，减小体积
        out = []
        for d in docs:
            d.pop("content", None)
            out.append(serialize(d))
        return out

    async def get_chapter(self, chapter_id, include_deleted=False, session=None) -> Dict[str, Any]:
        try:
            oid = ObjectId(chapter_id)
        except Exception:
            raise NotFoundError(f"章节ID无效: {chapter_id}")
        doc = await self.find_one({"_id": oid}, include_deleted=include_deleted, session=session)
        if not doc:
            raise NotFoundError(f"章节不存在: {chapter_id}")
        return serialize(doc)

    async def update_chapter(self, chapter_id, data: Dict[str, Any], expected_version=None, session=None):
        existing = await self.get_chapter(chapter_id, session=session)
        if existing.get("status") == "finalized" and data.get("content") is not None:
            raise DuplicateKeyError("章节已定稿，如需修改请先转回修改中状态")
        if expected_version is not None and existing.get("version", 1) != expected_version:
            raise DuplicateKeyError(
                f"章节已在其他页面被修改（服务器版本 {existing.get('version')}，当前基于版本 {expected_version}），请刷新"
            )
        # 改章节号时检查唯一
        if "number" in data and data["number"] != existing["number"]:
            conflict = await self.find_one(
                {"novel_id": existing["novel_id"], "number": data["number"], "_id": {"$ne": ObjectId(chapter_id)}},
                session=session,
            )
            if conflict:
                raise DuplicateKeyError(f"第 {data['number']} 章已存在")
        if "content" in data:
            data["word_count"] = len(re.sub(r"\s", "", data["content"] or ""))
        if "volume_id" in data and data["volume_id"]:
            data["volume_id"] = to_object_id(data["volume_id"])
        data["version"] = existing.get("version", 1) + 1
        await self.update_one({"_id": ObjectId(chapter_id)}, data, session=session)
        return await self.get_chapter(chapter_id, session=session)

    async def change_status(self, chapter_id, status: str, session=None):
        if status not in CHAPTER_STATUS:
            raise DuplicateKeyError(f"非法章节状态: {status}")
        return await self.update_chapter(chapter_id, {"status": status}, session=session)

    async def soft_delete_chapter(self, chapter_id, session=None) -> bool:
        await self.get_chapter(chapter_id, session=session)
        return await self.soft_delete_one({"_id": ObjectId(chapter_id)}, session=session)

    async def restore_chapter(self, chapter_id, session=None):
        ok = await self.restore_one({"_id": ObjectId(chapter_id)}, session=session)
        if not ok:
            raise NotFoundError(f"待恢复章节不存在: {chapter_id}")
        return await self.get_chapter(chapter_id, include_deleted=True, session=session)

    async def get_navigation(self, novel_id, number: int, session=None):
        """返回上一章/下一章。"""
        flt = {"novel_id": to_object_id(novel_id), "is_deleted": False}
        prev_cur = self.collection.find({**flt, "number": {"$lt": number}}, session=session).sort("number", -1).limit(1)
        next_cur = self.collection.find({**flt, "number": {"$gt": number}}, session=session).sort("number", 1).limit(1)
        prev_docs = await prev_cur.to_list(length=1)
        next_docs = await next_cur.to_list(length=1)
        prev = serialize(prev_docs[0]) if prev_docs else None
        nxt = serialize(next_docs[0]) if next_docs else None
        for d in (prev, nxt):
            if d:
                d.pop("content", None)
        return {"prev": prev, "next": nxt}
