"""创作蓝图仓储，异步 MongoDB。

蓝图采用独立集合存储（模块3.2 决策：独立集合而非嵌套到 novels），
以支持确认状态、版本化和生成审计。
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

# 蓝图生命周期状态
BLUEPRINT_STATUS = ("draft", "pending_confirmation", "confirmed", "superseded")

# 蓝图内容来源
BLUEPRINT_SOURCES = ("user", "ai", "mixed")

# 生成模式
GENERATION_MODES = ("quick", "guided")

# 可编辑字段白名单
BLUEPRINT_EDITABLE_FIELDS = (
    "plot_summary",
    "worldview",
    "power_system",
    "selected_character_ids",
    "selected_faction_ids",
    "selected_setting_card_ids",
    "selected_relation_ids",
    "ai_suggestions",
    "conflicts",
    "status",
    "source",
    "confirmed_at",
)


def serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把文档中的 ObjectId 转成字符串，便于 JSON 序列化。"""
    if not doc:
        return doc
    for key in ("_id", "novel_id"):
        if key in doc and isinstance(doc[key], ObjectId):
            doc[key] = str(doc[key])
    return doc


class NovelBlueprintRepository(BaseRepository):
    """创作蓝图数据访问层。"""

    def __init__(self) -> None:
        super().__init__("novel_blueprints")

    async def _next_blueprint_id(self, novel_id, session=None) -> str:
        allocated = await id_sequence_repo.allocate_many(
            novel_id, "novel_blueprint", "bp", 1, session=session
        )
        return allocated[0]

    def _build_doc(self, novel_id, blueprint_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """按白名单构造蓝图文档，并校验枚举取值。"""
        mode = str(data.get("generation_mode") or "guided").strip()
        if mode not in GENERATION_MODES:
            raise DuplicateKeyError(f"非法的生成模式: {mode}")
        status = str(data.get("status") or "draft").strip()
        if status not in BLUEPRINT_STATUS:
            raise DuplicateKeyError(f"非法的蓝图状态: {status}")
        source = str(data.get("source") or "user").strip()
        if source not in BLUEPRINT_SOURCES:
            raise DuplicateKeyError(f"非法的蓝图来源: {source}")
        return {
            "blueprint_id": blueprint_id,
            "novel_id": to_object_id(novel_id),
            "version": max(1, int(data.get("version") or 1)),
            "generation_mode": mode,
            "plot_summary": str(data.get("plot_summary") or ""),
            "worldview": str(data.get("worldview") or ""),
            "power_system": data.get("power_system"),
            "selected_character_ids": list(data.get("selected_character_ids") or []),
            "selected_faction_ids": list(data.get("selected_faction_ids") or []),
            "selected_setting_card_ids": list(data.get("selected_setting_card_ids") or []),
            "selected_relation_ids": list(data.get("selected_relation_ids") or []),
            "ai_suggestions": list(data.get("ai_suggestions") or []),
            "conflicts": list(data.get("conflicts") or []),
            "status": status,
            "source": source,
            "confirmed_at": data.get("confirmed_at"),
        }

    async def create_blueprint(
        self,
        novel_id,
        data: Dict[str, Any],
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """创建一条蓝图记录。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            data: 蓝图业务字段。
            session: 可选 MongoDB 会话。

        Returns:
            已落库的蓝图文档。

        Raises:
            DuplicateKeyError: 生成模式、状态或来源非法。
            NotFoundError: 写入后无法回读。
        """
        blueprint_id = await self._next_blueprint_id(novel_id, session=session)
        doc = self._build_doc(novel_id, blueprint_id, data)
        inserted = await self.insert_one(doc, session=session)
        # 回读必须按 Mongo _id 查，业务 ID 与 _id 不是同一个值
        found = await self.find_one({"_id": ObjectId(inserted)}, session=session)
        if not found:
            raise NotFoundError(f"蓝图写入后无法回读: {inserted}")
        return serialize(found)

    async def get_blueprint(
        self,
        novel_id,
        blueprint_id: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """校验归属后按业务 ID 读取蓝图。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            blueprint_id: 蓝图业务 ID。
            session: 可选 MongoDB 会话。

        Returns:
            命中的蓝图文档。

        Raises:
            NotFoundError: 蓝图不存在或不属于该小说。
        """
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "blueprint_id": blueprint_id},
            session=session,
        )
        if not doc:
            raise NotFoundError(f"蓝图不存在: {blueprint_id}")
        return serialize(doc)

    async def find_by_novel(
        self,
        novel_id,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """按小说查询蓝图，按版本号倒序。"""
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
        """读取当前小说的已确认蓝图，不存在时返回 None。"""
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "status": "confirmed"},
            sort=[("version", -1)],
            session=session,
        )
        return serialize(doc)

    async def update_blueprint(
        self,
        novel_id,
        blueprint_id: str,
        update_data: Dict[str, Any],
        *,
        expected_version: int,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """按乐观锁更新蓝图可编辑字段。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            blueprint_id: 蓝图业务 ID。
            update_data: 待更新字段，会被白名单过滤。
            expected_version: 客户端基于的蓝图版本。
            session: 可选 MongoDB 会话。

        Returns:
            更新后的蓝图文档。

        Raises:
            DuplicateKeyError: 无字段、状态非法或版本冲突。
            NotFoundError: 蓝图不存在。
        """
        filtered = {
            k: v for k, v in update_data.items() if k in BLUEPRINT_EDITABLE_FIELDS
        }
        if not filtered:
            raise DuplicateKeyError("蓝图更新至少需要提供一个可编辑字段")
        status = str(filtered.get("status") or "").strip()
        if status and status not in BLUEPRINT_STATUS:
            raise DuplicateKeyError(f"非法的蓝图状态: {status}")

        result = await self.collection.update_one(
            {
                "novel_id": to_object_id(novel_id),
                "blueprint_id": blueprint_id,
                "is_deleted": False,
                "version": expected_version,
            },
            {"$set": {**filtered, "updated_at": get_utc_now()}},
            session=session,
        )
        if result.matched_count == 0:
            latest = await self.collection.find_one(
                {"novel_id": to_object_id(novel_id), "blueprint_id": blueprint_id},
                session=session,
            )
            if latest is None:
                raise NotFoundError(f"蓝图不存在: {blueprint_id}")
            raise DuplicateKeyError(
                f"蓝图已被修改（服务器版本 {latest.get('version')}，"
                f"当前基于版本 {expected_version}），请刷新后重试"
            )
        return await self.get_blueprint(novel_id, blueprint_id, session=session)

    async def create_new_version(
        self,
        novel_id,
        blueprint_id: str,
        update_data: Dict[str, Any],
        *,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """基于已有蓝图创建新版本，旧版本标记为 superseded。

        已确认蓝图不可原地修改，任何变更都必须生成新版本。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            blueprint_id: 源蓝图业务 ID。
            update_data: 新版本需要覆盖的字段。
            session: 可选 MongoDB 会话。

        Returns:
            新版本蓝图文档。

        Raises:
            NotFoundError: 源蓝图不存在。
        """
        source_doc = await self.get_blueprint(novel_id, blueprint_id, session=session)
        # 先把当前已确认版本降级，保证同一时刻只有一个确认版本
        await self.collection.update_many(
            {
                "novel_id": to_object_id(novel_id),
                "blueprint_id": blueprint_id,
                "status": "confirmed",
            },
            {"$set": {"status": "superseded", "updated_at": get_utc_now()}},
            session=session,
        )
        payload: Dict[str, Any] = {
            "generation_mode": source_doc.get("generation_mode", "guided"),
            "plot_summary": source_doc.get("plot_summary", ""),
            "worldview": source_doc.get("worldview", ""),
            "power_system": source_doc.get("power_system"),
            "selected_character_ids": source_doc.get("selected_character_ids", []),
            "selected_faction_ids": source_doc.get("selected_faction_ids", []),
            "selected_setting_card_ids": source_doc.get("selected_setting_card_ids", []),
            "selected_relation_ids": source_doc.get("selected_relation_ids", []),
            "ai_suggestions": source_doc.get("ai_suggestions", []),
            "conflicts": source_doc.get("conflicts", []),
            "source": source_doc.get("source", "user"),
        }
        payload.update(
            {k: v for k, v in update_data.items() if k in BLUEPRINT_EDITABLE_FIELDS}
        )
        payload["version"] = int(source_doc.get("version") or 1) + 1
        payload["status"] = "draft"
        payload["confirmed_at"] = None
        return await self.create_blueprint(novel_id, payload, session=session)


novel_blueprint_repo = NovelBlueprintRepository()
