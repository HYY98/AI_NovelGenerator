"""章节数据访问层，异步 MongoDB。章节是实际写作内容的主存储。"""
import re
import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId

from backend.db.base import BaseRepository
from backend.db.utils import to_object_id, get_utc_now
from backend.db.errors import NotFoundError, DuplicateKeyError
from backend.db.repositories.id_sequence_repository import id_sequence_repo

logger = logging.getLogger(__name__)

# 章节状态：草稿 / 修改中 / 已定稿
CHAPTER_STATUS = ("draft", "editing", "finalized")
STATUS_LABEL = {"draft": "草稿", "editing": "修改中", "finalized": "已定稿"}

# 合法状态流转：已定稿只能重开为修改中，不能直接回草稿
STATUS_TRANSITIONS = {
    "draft": {"editing", "finalized"},
    "editing": {"draft", "finalized"},
    "finalized": {"editing"},
}


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
        # 普通保存同样不允许绕过状态机非法跳转（如已定稿直接回草稿）
        if "status" in data and data["status"] != existing.get("status"):
            tgt = data["status"]
            cur = existing.get("status", "draft")
            if tgt not in CHAPTER_STATUS:
                raise DuplicateKeyError(f"非法章节状态: {tgt}")
            if tgt not in STATUS_TRANSITIONS.get(cur, set()):
                raise DuplicateKeyError(
                    f"不允许从「{STATUS_LABEL.get(cur, cur)}」直接改为「{STATUS_LABEL.get(tgt, tgt)}」"
                )
        # 乐观锁基准：显式 expected_version 优先，否则以刚读到的版本为基准（仍走原子条件更新）
        base_version = expected_version if expected_version is not None else existing.get("version", 1)
        # 改章节号时检查唯一
        if "number" in data and data["number"] != existing["number"]:
            conflict = await self.find_one(
                {"novel_id": existing["novel_id"], "number": data["number"], "_id": {"$ne": ObjectId(chapter_id)}},
                session=session,
            )
            if conflict:
                raise DuplicateKeyError(f"第 {data['number']} 章已存在")
        # 待写入字段（version 一律由 $inc 原子自增，不允许外部直接写）
        set_fields: Dict[str, Any] = {k: v for k, v in data.items() if k != "version"}
        if "content" in set_fields:
            set_fields["word_count"] = len(re.sub(r"\s", "", set_fields["content"] or ""))
        if "volume_id" in set_fields and set_fields["volume_id"]:
            set_fields["volume_id"] = to_object_id(set_fields["volume_id"])
        # 原子条件更新：版本号匹配才写入并 +1，两个窗口并发时后写不会覆盖先写
        flt = {"_id": ObjectId(chapter_id), "is_deleted": False, "version": base_version}
        update = {
            "$set": {**set_fields, "updated_at": get_utc_now()},
            "$inc": {"version": 1},
        }
        result = await self.collection.update_one(flt, update, session=session)
        if result.matched_count == 0:
            latest = await self.collection.find_one({"_id": ObjectId(chapter_id)}, session=session)
            if not latest:
                raise NotFoundError(f"章节不存在: {chapter_id}")
            raise DuplicateKeyError(
                f"章节已在其他页面被修改（服务器版本 {latest.get('version')}，当前基于版本 {base_version}），请刷新后重试"
            )
        return await self.get_chapter(chapter_id, session=session)

    async def change_status(self, chapter_id, status: str, session=None):
        if status not in CHAPTER_STATUS:
            raise DuplicateKeyError(f"非法章节状态: {status}")
        existing = await self.get_chapter(chapter_id, session=session)
        cur = existing.get("status", "draft")
        if status == cur:
            return existing
        if status not in STATUS_TRANSITIONS.get(cur, set()):
            raise DuplicateKeyError(
                f"不允许从「{STATUS_LABEL.get(cur, cur)}」直接改为「{STATUS_LABEL.get(status, status)}」"
            )
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
