import logging
from typing import Any, Dict, List

import pymongo.errors
from bson import ObjectId
from pymongo import UpdateOne
from pymongo.asynchronous.client_session import AsyncClientSession

from backend.db.base import BaseRepository
from backend.db.created_write_compensation import delete_created_documents_cas
from backend.db.errors import DuplicateKeyError, NotFoundError
from backend.db.repositories.id_sequence_repository import id_sequence_repo
from backend.db.utils import get_utc_now, to_object_id

logger = logging.getLogger(__name__)


class FactionRepository(BaseRepository):
    def __init__(self):
        """初始化阵营仓储，指定集合为'factions'。"""
        super().__init__("factions")

    async def _get_next_faction_id(
        self,
        novel_id: str | ObjectId,
        session: AsyncClientSession | None = None,
    ) -> str:
        """通过原子序列预留下一个阵营业务 ID。

        Args:
            novel_id: 小说 ObjectId 或可转换字符串。
            session: 可选 MongoDB 会话，用于事务读取。

        Returns:
            下一个可用业务阵营 ID，格式为 fac_000001。
        """
        allocated = await id_sequence_repo.allocate_many(
            novel_id,
            "faction",
            "fac",
            1,
            session=session,
        )
        return allocated[0]

    async def create_faction(
        self,
        data: Dict[str, Any],
        session: AsyncClientSession | None = None,
    ) -> str:
        """创建一个新阵营。

        Args:
            data: 阵营文档数据，必须包含 novel_id、faction_id 和 name。
            session: 可选 MongoDB 会话，用于事务写入。

        Returns:
            新阵营文档的 ObjectId 字符串。
        """
        if "novel_id" not in data:
            raise ValueError("novel_id is required")
        if "faction_id" not in data or not data["faction_id"]:
            raise ValueError("faction_id is required")
        if "name" not in data or not str(data["name"]).strip():
            raise ValueError("Faction name cannot be empty")

        prepared = dict(data)
        prepared["novel_id"] = to_object_id(prepared["novel_id"])
        prepared["name"] = str(prepared["name"]).strip()

        prepared.setdefault("alias", [])
        prepared.setdefault("faction_type", "")
        prepared.setdefault("level_type", "core")
        prepared.setdefault("parent_faction_id", None)
        prepared.setdefault("positioning", "")
        prepared.setdefault("public_stance", "")
        prepared.setdefault("core_goal", "")
        prepared.setdefault("hidden_goal", "")
        prepared.setdefault("resources_and_advantages", [])
        prepared.setdefault("organization_style", "")
        prepared.setdefault("core_values", [])
        prepared.setdefault("conflict_with_mainline", "")
        prepared.setdefault("is_public", True)
        prepared.setdefault("influence_scope", "")
        prepared.setdefault("active_status", "active")
        prepared.setdefault("expandability", "")
        prepared.setdefault("tags", [])
        prepared.setdefault("first_appearance_volume_id", None)
        prepared.setdefault("first_appearance_chapter_id", None)
        prepared.setdefault("sort_order", 0)
        prepared.setdefault("extra", {})
        # 势力生命周期跨集合补偿使用内部版本做 CAS，不改变现有公开表单契约。
        prepared.setdefault("version", 1)
        # 势力作为依赖端点时，以严格整数 0 初始化贡献计数。
        prepared.setdefault("dependency_guard_revision", 0)

        try:
            return await self.insert_one(prepared, session=session)
        except pymongo.errors.DuplicateKeyError:
            raise DuplicateKeyError(
                f"同一小说下 faction_id={prepared['faction_id']} 已存在，请使用其他阵营ID"
            )

    async def delete_factions_created_exact(
        self,
        documents: list[dict[str, Any]],
        *,
        session: AsyncClientSession | None = None,
    ) -> int:
        """逐文档补偿仍停留在 v1 的本批新建势力。

        Args:
            documents: 带 Mongo `_id`、小说 ID、势力业务 ID 和版本的本批记录。
            session: 可选 MongoDB 会话。

        Returns:
            实际安全删除的本批势力数量。
        """
        return await delete_created_documents_cas(
            self.collection,
            documents,
            business_key_field="faction_id",
            expected_dependency_guard_revision=0,
            session=session,
        )

    async def get_factions_by_novel(
        self,
        novel_id: str,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """获取指定小说下所有未软删除阵营，按 sort_order 升序排列。

        Args:
            novel_id: 小说 ObjectId 字符串。
            session: 可选 MongoDB 会话，用于事务读取。

        Returns:
            阵营文档列表。
        """
        obj_id = to_object_id(novel_id)
        return await self.find_many(
            {"novel_id": obj_id},
            sort=[("sort_order", 1)],
            session=session,
        )

    async def get_faction(
        self,
        novel_id: str,
        faction_id: str,
        include_deleted: bool = False,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """根据 novel_id + faction_id 获取单个阵营。

        Args:
            novel_id: 小说 ObjectId 字符串。
            faction_id: 业务层阵营 ID。
            include_deleted: 是否允许读取软删除阵营。
            session: 可选 MongoDB 会话，用于事务读取。

        Returns:
            阵营文档。
        """
        obj_id = to_object_id(novel_id)
        faction = await self.find_one(
            {"novel_id": obj_id, "faction_id": faction_id},
            include_deleted=include_deleted,
            session=session,
        )
        if not faction:
            raise NotFoundError(f"Faction with faction_id '{faction_id}' not found in novel {novel_id}")
        return faction

    async def get_deleted_faction(
        self,
        novel_id: str,
        faction_id: str,
        session: AsyncClientSession | None = None,
    ) -> Dict[str, Any]:
        """获取指定小说下最近一次软删除的阵营。

        Args:
            novel_id: 小说 ObjectId 字符串。
            faction_id: 业务层阵营 ID。
            session: 可选 MongoDB 会话，用于事务读取。

        Returns:
            已软删除的阵营文档。
        """
        obj_id = to_object_id(novel_id)
        cursor = self.collection.find(
            {"novel_id": obj_id, "faction_id": faction_id, "is_deleted": True},
            session=session,
        ).sort("updated_at", -1).limit(1)
        docs = await cursor.to_list(length=1)
        if not docs:
            raise NotFoundError(f"Deleted faction with faction_id '{faction_id}' not found in novel {novel_id}")
        return docs[0]

    async def get_deleted_factions_by_level_type(
        self,
        novel_id: str,
        level_type: str | None = None,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """拉取指定小说下已软删除的阵营，可按层级过滤。

        Args:
            novel_id: 小说 ObjectId 字符串。
            level_type: 可选阵营层级类型。
            session: 可选 MongoDB 会话，用于事务读取。

        Returns:
            已软删除阵营文档列表。
        """
        query: Dict[str, Any] = {"novel_id": to_object_id(novel_id), "is_deleted": True}
        if level_type:
            query["level_type"] = level_type
        cursor = self.collection.find(query, session=session).sort("updated_at", -1)
        return await cursor.to_list(length=None)

    async def get_factions_by_level_type(
        self,
        novel_id: str,
        level_type: str,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """拉取指定小说下特定 level_type 的阵营列表，按 sort_order 升序。

        Args:
            novel_id: 小说 ObjectId 字符串。
            level_type: 阵营层级类型。
            session: 可选 MongoDB 会话，用于事务读取。

        Returns:
            阵营文档列表。
        """
        obj_id = to_object_id(novel_id)
        return await self.find_many(
            {"novel_id": obj_id, "level_type": level_type},
            sort=[("sort_order", 1)],
            session=session,
        )

    async def count_factions_by_level_type(
        self,
        novel_id: str,
        level_type: str,
        include_deleted: bool = False,
        session: AsyncClientSession | None = None,
    ) -> int:
        """统计指定小说下某个层级类型的阵营数量。

        Args:
            novel_id: 小说 ObjectId 字符串。
            level_type: 阵营层级类型。
            include_deleted: 是否包含软删除阵营。
            session: 可选 MongoDB 会话，用于事务读取。

        Returns:
            匹配的阵营数量。
        """
        return await self.count_documents(
            {"novel_id": to_object_id(novel_id), "level_type": level_type},
            include_deleted=include_deleted,
            session=session,
        )

    async def get_child_factions(
        self,
        novel_id: str,
        parent_faction_id: str,
        session: AsyncClientSession | None = None,
    ) -> List[Dict[str, Any]]:
        """拉取指定小说下某个父级阵营的所有直接子阵营。

        Args:
            novel_id: 小说 ObjectId 字符串。
            parent_faction_id: 父级业务阵营 ID。
            session: 可选 MongoDB 会话，用于事务读取。

        Returns:
            子阵营文档列表。
        """
        obj_id = to_object_id(novel_id)
        return await self.find_many(
            {"novel_id": obj_id, "parent_faction_id": parent_faction_id},
            sort=[("sort_order", 1)],
            session=session,
        )

    async def update_faction_info(
        self,
        novel_id: str,
        faction_id: str,
        update_data: Dict[str, Any],
        session: AsyncClientSession | None = None,
    ) -> bool:
        """更新阵营基础信息。

        Args:
            novel_id: 小说 ObjectId 字符串。
            faction_id: 业务层阵营 ID。
            update_data: 待更新字段。
            session: 可选 MongoDB 会话，用于事务写入。

        Returns:
            实际修改成功时返回 True。
        """
        allowed_fields = {
            "name", "alias", "faction_type", "level_type", "parent_faction_id",
            "positioning", "public_stance", "core_goal", "hidden_goal",
            "resources_and_advantages", "organization_style", "core_values",
            "conflict_with_mainline", "is_public", "influence_scope",
            "active_status", "expandability", "tags",
            "first_appearance_volume_id", "first_appearance_chapter_id",
            "sort_order", "extra",
        }
        filtered = {k: v for k, v in update_data.items() if k in allowed_fields}
        if not filtered:
            return False

        return await self.update_one(
            {"novel_id": to_object_id(novel_id), "faction_id": faction_id},
            filtered,
            session=session,
        )

    async def batch_update_sort_order(
        self,
        novel_id: str,
        sort_map: Dict[str, int],
        session: AsyncClientSession | None = None,
    ) -> int:
        """批量更新阵营排序权重。

        Args:
            novel_id: 小说 ObjectId 字符串。
            sort_map: {faction_id: new_sort_order} 映射。
            session: 可选 MongoDB 会话，用于事务写入。

        Returns:
            被实际修改的阵营数量。
        """
        obj_id = to_object_id(novel_id)
        # 排序也是主档并发写，必须推进内部版本，避免生命周期补偿覆盖刚完成的拖拽排序。
        operations = [
            UpdateOne(
                {
                    "novel_id": obj_id,
                    "faction_id": faction_id,
                    "is_deleted": False,
                },
                {
                    "$set": {
                        "sort_order": new_order,
                        "updated_at": get_utc_now(),
                    },
                    "$inc": {"version": 1},
                },
            )
            for faction_id, new_order in sort_map.items()
        ]
        result = await self.bulk_write(operations, session=session)
        return 0 if result is None else result.modified_count

    async def soft_delete_faction(
        self,
        novel_id: str,
        faction_id: str,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """软删除指定阵营，并解除同小说下子阵营挂靠。

        Args:
            novel_id: 小说 ObjectId 字符串。
            faction_id: 业务层阵营 ID。
            session: 可选 MongoDB 会话，用于事务写入。

        Returns:
            实际软删除成功时返回 True。
        """
        faction = await self.get_faction(novel_id, faction_id, session=session)
        success = await self.soft_delete_one({"_id": faction["_id"]}, session=session)
        if not success:
            return False

        # 父子关系只在同一小说内解除，避免影响其他小说同名 faction_id。
        children_updated = await self.update_many(
            {"novel_id": faction["novel_id"], "parent_faction_id": faction_id},
            {"parent_faction_id": None},
            session=session,
        )
        if children_updated > 0:
            logger.info(f"软删除阵营 {faction_id}，解除 {children_updated} 个子阵营挂靠")
        return True

    async def restore_faction(
        self,
        novel_id: str,
        faction_id: str,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """恢复已软删除的指定阵营。

        Args:
            novel_id: 小说 ObjectId 字符串。
            faction_id: 业务层阵营 ID。
            session: 可选 MongoDB 会话，用于事务写入。

        Returns:
            实际恢复成功时返回 True。
        """
        active_exists = await self.exists(
            {"novel_id": to_object_id(novel_id), "faction_id": faction_id},
            session=session,
        )
        if active_exists:
            raise DuplicateKeyError("同一小说下已存在相同 faction_id 的未删除阵营，无法恢复")

        faction = await self.get_deleted_faction(novel_id, faction_id, session=session)
        try:
            return await self.restore_one({"_id": faction["_id"]}, session=session)
        except pymongo.errors.DuplicateKeyError:
            raise DuplicateKeyError("同一小说下已存在相同 faction_id 的未删除阵营，无法恢复")

    async def hard_delete_faction(
        self,
        novel_id: str,
        faction_id: str,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """物理删除已软删除的指定阵营。

        Args:
            novel_id: 小说 ObjectId 字符串。
            faction_id: 业务层阵营 ID。
            session: 可选 MongoDB 会话，用于事务写入。

        Returns:
            实际删除成功时返回 True。
        """
        faction = await self.get_deleted_faction(novel_id, faction_id, session=session)
        active_exists = await self.exists(
            {"novel_id": faction["novel_id"], "faction_id": faction_id},
            session=session,
        )
        if not active_exists:
            # 没有同 ID 的 active 阵营时，兼容旧数据：硬删前再解除可能残留的子挂靠。
            children_updated = await self.update_many(
                {"novel_id": faction["novel_id"], "parent_faction_id": faction_id},
                {"parent_faction_id": None},
                session=session,
            )
            if children_updated > 0:
                logger.info(f"硬删除阵营 {faction_id} 前，解除 {children_updated} 个子阵营挂靠")

        return await self.hard_delete_one({"_id": faction["_id"]}, session=session)


faction_repo = FactionRepository()
