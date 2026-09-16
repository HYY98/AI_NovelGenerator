"""卷章大纲仓储，异步 MongoDB。

确认后的卷章大纲以独立集合 `novel_outlines` 存储：
- 保留版本号、来源蓝图与生成记录引用，便于追溯大纲依据；
- 大纲确认后才允许分章生成，旧确认版本自动标记为 superseded；
- 章节上下文可按 chapter_id / 章节序号读取当前章大纲，不必依赖生成记录。
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

# 大纲状态：草稿 / 已确认 / 已被新版本取代
OUTLINE_STATUS = ("draft", "confirmed", "superseded")
# 大纲内容来源
OUTLINE_SOURCES = ("ai", "user", "mixed")
# 允许通过更新接口写入的字段
OUTLINE_EDITABLE_FIELDS = ("volumes", "status", "source")


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
    if not isinstance(value, (list, tuple)):
        return []
    return list(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def _normalize_outline_chapter(chapter: Any, fallback_index: int) -> Optional[Dict[str, Any]]:
    """规范化单个章节大纲条目，无法识别的元素返回 None。"""
    if not isinstance(chapter, dict):
        return None
    title = str(chapter.get("title") or "").strip()
    summary = str(chapter.get("summary") or "").strip()
    if not title and not summary:
        return None
    index_value = chapter.get("index")
    try:
        index = int(index_value) if index_value is not None else fallback_index
    except (TypeError, ValueError):
        index = fallback_index
    return {
        "index": max(0, index),
        "title": title,
        "summary": summary,
        "goals": _normalize_str_list(chapter.get("goals")),
        "conflicts": _normalize_str_list(chapter.get("conflicts")),
        "character_ids": _normalize_str_list(chapter.get("character_ids")),
        "faction_ids": _normalize_str_list(chapter.get("faction_ids")),
        "setting_card_ids": _normalize_str_list(chapter.get("setting_card_ids")),
        "power_changes": _normalize_str_list(chapter.get("power_changes")),
        "foreshadowing": _normalize_str_list(chapter.get("foreshadowing")),
    }


def normalize_outline_volumes(volumes: Any) -> List[Dict[str, Any]]:
    """规范化卷章大纲结构。

    Args:
        volumes: 原始卷列表，元素应为包含 chapters 的映射。

    Returns:
        规范化后的卷列表；空卷会被丢弃，章节序号不可解析时按位置补齐。
    """
    normalized: List[Dict[str, Any]] = []
    if not isinstance(volumes, (list, tuple)):
        return normalized
    for volume_index, volume in enumerate(volumes):
        if not isinstance(volume, dict):
            continue
        chapters: List[Dict[str, Any]] = []
        raw_chapters = volume.get("chapters")
        if isinstance(raw_chapters, (list, tuple)):
            for chapter_index, chapter in enumerate(raw_chapters):
                item = _normalize_outline_chapter(chapter, chapter_index + 1)
                if item is not None:
                    chapters.append(item)
        title = str(volume.get("title") or "").strip()
        summary = str(volume.get("summary") or "").strip()
        if not chapters and not title and not summary:
            continue
        order_value = volume.get("order")
        try:
            order = int(order_value) if order_value is not None else volume_index + 1
        except (TypeError, ValueError):
            order = volume_index + 1
        normalized.append(
            {
                "order": max(1, order),
                "title": title,
                "summary": summary,
                "chapters": chapters,
            }
        )
    return normalized


def count_outline_chapters(volumes: List[Dict[str, Any]]) -> int:
    """统计大纲中的章节总数。"""
    return sum(len(volume.get("chapters") or []) for volume in volumes or [])


class OutlineRepository(BaseRepository):
    """卷章大纲数据访问层。"""

    def __init__(self) -> None:
        super().__init__("novel_outlines")

    async def _next_outline_id(self, novel_id, session=None) -> str:
        """分配小说作用域内的大纲业务 ID。"""
        allocated = await id_sequence_repo.allocate_many(
            novel_id, "outline", "outl", 1, session=session
        )
        return allocated[0]

    async def _next_version(self, novel_id, session=None) -> int:
        """读取当前小说最大版本号并返回下一个版本号。"""
        latest = await self.find_one(
            {"novel_id": to_object_id(novel_id)},
            include_deleted=True,
            sort=[("version", -1)],
            session=session,
        )
        return int((latest or {}).get("version") or 0) + 1

    async def create_outline(
        self,
        novel_id,
        data: Dict[str, Any],
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """创建一份卷章大纲（默认是草稿版本）。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            data: 大纲业务字段（volumes / status / source / blueprint 引用等）。
            session: 可选 MongoDB 会话。

        Returns:
            已落库的大纲文档。

        Raises:
            DuplicateKeyError: 状态或来源非法。
            NotFoundError: 写入后无法回读。
        """
        status = str(data.get("status") or "draft").strip()
        if status not in OUTLINE_STATUS:
            raise DuplicateKeyError(f"非法的大纲状态: {status}")
        source = str(data.get("source") or "ai").strip()
        if source not in OUTLINE_SOURCES:
            raise DuplicateKeyError(f"非法的大纲来源: {source}")

        volumes = normalize_outline_volumes(data.get("volumes"))
        outline_id = await self._next_outline_id(novel_id, session=session)
        doc: Dict[str, Any] = {
            "outline_id": outline_id,
            "novel_id": to_object_id(novel_id),
            "version": await self._next_version(novel_id, session=session),
            "blueprint_id": str(data.get("blueprint_id") or ""),
            "blueprint_version": int(data.get("blueprint_version") or 0) or None,
            "generation_id": str(data.get("generation_id") or ""),
            "volumes": volumes,
            "chapter_count": count_outline_chapters(volumes),
            "status": status,
            "source": source,
            "confirmed_at": None,
        }
        inserted = await self.insert_one(doc, session=session)
        # 回读必须按 Mongo _id 查，业务 ID 与 _id 不是同一个值
        found = await self.find_one({"_id": ObjectId(inserted)}, session=session)
        if not found:
            raise NotFoundError(f"大纲写入后无法回读: {inserted}")
        return serialize(found)

    async def get_outline(
        self,
        novel_id,
        outline_id: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """校验归属后按业务 ID 读取大纲。

        Raises:
            NotFoundError: 大纲不存在或不属于该小说。
        """
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "outline_id": outline_id},
            session=session,
        )
        if not doc:
            raise NotFoundError(f"大纲不存在: {outline_id}")
        return serialize(doc)

    async def find_by_novel(
        self,
        novel_id,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """按小说查询大纲，按版本号倒序。"""
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if status:
            flt["status"] = status
        docs = await self.find_many(
            flt, limit=max(1, min(200, limit)), sort=[("version", -1)], session=session
        )
        return [serialize(doc) for doc in docs]

    async def find_confirmed(
        self,
        novel_id,
        *,
        session: AsyncClientSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """读取当前确认版本的大纲，不存在时返回 None。"""
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "status": "confirmed"},
            sort=[("version", -1)],
            session=session,
        )
        return serialize(doc)

    async def find_latest(
        self,
        novel_id,
        *,
        session: AsyncClientSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """读取最新版本的大纲（草稿或已确认），不存在时返回 None。"""
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id)},
            sort=[("version", -1)],
            session=session,
        )
        return serialize(doc)

    async def update_outline(
        self,
        novel_id,
        outline_id: str,
        update_data: Dict[str, Any],
        *,
        expected_version: int,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """按乐观锁更新大纲可编辑字段。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            outline_id: 大纲业务 ID。
            update_data: 待更新字段，会被白名单过滤。
            expected_version: 客户端基于的大纲版本。
            session: 可选 MongoDB 会话。

        Returns:
            更新后的大纲文档。

        Raises:
            DuplicateKeyError: 无字段、状态非法或版本冲突。
            NotFoundError: 大纲不存在。
        """
        filtered = {
            key: value for key, value in update_data.items() if key in OUTLINE_EDITABLE_FIELDS
        }
        if not filtered:
            raise DuplicateKeyError("大纲更新至少需要提供一个可编辑字段")
        status = str(filtered.get("status") or "").strip()
        if status and status not in OUTLINE_STATUS:
            raise DuplicateKeyError(f"非法的大纲状态: {status}")
        if "volumes" in filtered:
            volumes = normalize_outline_volumes(filtered["volumes"])
            filtered["volumes"] = volumes
            filtered["chapter_count"] = count_outline_chapters(volumes)

        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "outline_id": outline_id,
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
                {"novel_id": to_object_id(novel_id), "outline_id": outline_id},
                session=session,
            )
            if latest is None:
                raise NotFoundError(f"大纲不存在: {outline_id}")
            raise DuplicateKeyError(
                f"大纲已被修改（服务器版本 {latest.get('version')}，"
                f"当前基于版本 {expected_version}），请刷新后重试"
            )
        return await self.get_outline(novel_id, outline_id, session=session)

    async def confirm_outline(
        self,
        novel_id,
        outline_id: str,
        *,
        expected_version: int,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """确认大纲：当前版本置为 confirmed，旧的确认版本标记为 superseded。

        Raises:
            DuplicateKeyError: 版本冲突或状态已确认。
            NotFoundError: 大纲不存在。
        """
        target = await self.get_outline(novel_id, outline_id, session=session)
        if target.get("status") == "confirmed":
            raise DuplicateKeyError("该大纲已确认，请勿重复确认")
        if int(target.get("version") or 0) != int(expected_version):
            raise DuplicateKeyError(
                f"大纲版本已变化（当前 v{target.get('version')}），请刷新后重试"
            )
        await self.update_many(
            {
                "novel_id": to_object_id(novel_id),
                "status": "confirmed",
                "_id": {"$ne": ObjectId(str(target["_id"]))},
            },
            {"status": "superseded"},
            session=session,
        )
        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "outline_id": outline_id,
                "is_deleted": False,
                "version": expected_version,
            },
            {
                "$set": {
                    "status": "confirmed",
                    "confirmed_at": get_utc_now(),
                    "updated_at": get_utc_now(),
                },
                "$inc": {"version": 1},
            },
            session=session,
        )
        if result.matched_count == 0:
            raise DuplicateKeyError("大纲已被修改，请刷新后重新确认")
        return await self.get_outline(novel_id, outline_id, session=session)

    async def create_new_version(
        self,
        novel_id,
        outline_id: str,
        updates: Dict[str, Any],
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """基于现有大纲创建新版本；已确认的旧版本标记为 superseded。"""
        current = await self.get_outline(novel_id, outline_id, session=session)
        allowed = {
            key: value
            for key, value in updates.items()
            if key in ("volumes", "source", "blueprint_id", "blueprint_version", "generation_id")
        }
        volumes = normalize_outline_volumes(
            allowed.get("volumes", current.get("volumes"))
        )
        doc: Dict[str, Any] = {
            "outline_id": await self._next_outline_id(novel_id, session=session),
            "novel_id": to_object_id(novel_id),
            "version": await self._next_version(novel_id, session=session),
            "blueprint_id": str(
                allowed.get("blueprint_id", current.get("blueprint_id")) or ""
            ),
            "blueprint_version": int(
                allowed.get("blueprint_version", current.get("blueprint_version")) or 0
            )
            or None,
            "generation_id": str(
                allowed.get("generation_id", current.get("generation_id")) or ""
            ),
            "volumes": volumes,
            "chapter_count": count_outline_chapters(volumes),
            "status": "draft",
            "source": str(allowed.get("source") or current.get("source") or "user"),
            "confirmed_at": None,
        }
        inserted = await self.insert_one(doc, session=session)
        if current.get("status") == "confirmed":
            await self.update_many(
                {
                    "novel_id": to_object_id(novel_id),
                    "outline_id": outline_id,
                    "status": "confirmed",
                },
                {"status": "superseded"},
                session=session,
            )
        found = await self.find_one({"_id": ObjectId(inserted)}, session=session)
        if not found:
            raise NotFoundError(f"大纲新版本写入后无法回读: {inserted}")
        return serialize(found)

    async def find_chapter_entry(
        self,
        novel_id,
        *,
        chapter_id: Optional[str] = None,
        chapter_index: Optional[int] = None,
        session: AsyncClientSession | None = None,
    ) -> Optional[Dict[str, Any]]:
        """在已确认大纲（缺失时回退最新版本）中定位单章大纲条目。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            chapter_id: 章节业务 ID；与大纲记录过的一致时命中。
            chapter_index: 章节序号，用于未记录章节 ID 的场景。
            session: 可选 MongoDB 会话。

        Returns:
            含 outline_id、version、volume 与 chapter 的字典；未命中返回 None。
        """
        outline = await self.find_confirmed(novel_id, session=session) or await self.find_latest(
            novel_id, session=session
        )
        if not outline:
            return None
        wanted_id = str(chapter_id or "").strip()
        wanted_index = chapter_index if isinstance(chapter_index, int) else None
        for volume in outline.get("volumes") or []:
            for chapter in volume.get("chapters") or []:
                if wanted_id and str(chapter.get("chapter_id") or "") == wanted_id:
                    return {
                        "outline_id": outline.get("outline_id"),
                        "version": outline.get("version"),
                        "status": outline.get("status"),
                        "volume": {
                            "order": volume.get("order"),
                            "title": volume.get("title"),
                            "summary": volume.get("summary"),
                        },
                        "chapter": chapter,
                    }
                if not wanted_id and wanted_index is not None and int(chapter.get("index") or 0) == wanted_index:
                    return {
                        "outline_id": outline.get("outline_id"),
                        "version": outline.get("version"),
                        "status": outline.get("status"),
                        "volume": {
                            "order": volume.get("order"),
                            "title": volume.get("title"),
                            "summary": volume.get("summary"),
                        },
                        "chapter": chapter,
                    }
        return None


outline_repo = OutlineRepository()
