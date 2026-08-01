"""全书级角色—势力绑定 MongoDB 仓储。"""

from __future__ import annotations

from typing import Any

import pymongo.errors
from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from backend.db.base import BaseRepository
from backend.db.created_write_compensation import delete_created_documents_cas
from backend.db.errors import DuplicateKeyError, NotFoundError
from backend.db.utils import to_object_id


class CharacterFactionBindingRepository(BaseRepository):
    """封装角色—势力绑定的创建、查询与精确失败补偿。"""

    def __init__(self) -> None:
        """初始化角色—势力绑定仓储。

        Args:
            无。

        Returns:
            无。
        """
        super().__init__("character_faction_bindings")

    async def insert_bindings(
        self,
        documents: list[dict[str, Any]],
        session: AsyncClientSession | None = None,
    ) -> list[dict[str, Any]]:
        """按已分配业务 ID 批量插入角色—势力绑定。

        Args:
            documents: 已完成端点和唯一性校验的绑定文档。
            session: 可选 MongoDB 会话，用于纳入生成确认事务。

        Returns:
            按输入顺序返回刚写入的正式绑定文档。
        """
        if not documents:
            return []

        prepared_documents: list[dict[str, Any]] = []
        binding_ids: list[str] = []
        for document in documents:
            prepared = dict(document)
            required_fields = (
                "novel_id",
                "binding_id",
                "character_id",
                "faction_id",
                "membership_type",
            )
            missing_fields = [field for field in required_fields if not prepared.get(field)]
            if missing_fields:
                raise ValueError(f"角色—势力绑定缺少必填字段: {', '.join(missing_fields)}")

            prepared.pop("_id", None)
            # 预分配 Mongo _id，standalone 部分写入时可只删除本次文档。
            prepared["_id"] = ObjectId()
            prepared["novel_id"] = to_object_id(prepared["novel_id"])
            prepared.setdefault("role_title", None)
            prepared.setdefault("public_status", None)
            prepared.setdefault("loyalty_level", None)
            # 本轮只创建全书级初始绑定，不允许写入任何卷范围。
            prepared["joined_volume_id"] = None
            prepared["left_volume_id"] = None
            # 用户启停意图与角色/势力不可用造成的自动阻断分离，恢复实体时不得误启用用户停用绑定。
            prepared.setdefault("user_is_active", bool(prepared.get("is_active", True)))
            prepared.setdefault("disabled_by_character_ids", [])
            prepared.setdefault("disabled_by_faction_ids", [])
            prepared["is_active"] = bool(
                prepared["user_is_active"]
                and not prepared["disabled_by_character_ids"]
                and not prepared["disabled_by_faction_ids"]
                and not prepared.get("is_deleted", False)
            )
            prepared.setdefault("notes", None)
            prepared.setdefault("sort_order", 0)
            prepared.setdefault("version", 1)
            prepared.setdefault("is_deleted", False)
            prepared.setdefault("deleted_at", None)
            prepared.setdefault("deletion_sources", [])
            binding_ids.append(str(prepared["binding_id"]))
            prepared_documents.append(prepared)

        if len(binding_ids) != len(set(binding_ids)):
            raise DuplicateKeyError("同一批次不能重复使用 binding_id")

        try:
            await self.insert_many(prepared_documents, session=session)
            created = await self.get_bindings_by_ids(
                str(prepared_documents[0]["novel_id"]),
                binding_ids,
                session=session,
            )
            if len(created) != len(prepared_documents):
                raise RuntimeError("角色—势力绑定批量写入后未能完整回读")
            return created
        except (pymongo.errors.DuplicateKeyError, pymongo.errors.BulkWriteError) as exc:
            if session is None:
                await delete_created_documents_cas(
                    self.collection,
                    prepared_documents,
                    business_key_field="binding_id",
                )
            raise DuplicateKeyError(
                "角色—势力绑定业务 ID、主归属或活动语义绑定已存在"
            ) from exc
        except Exception:
            if session is None:
                await delete_created_documents_cas(
                    self.collection,
                    prepared_documents,
                    business_key_field="binding_id",
                )
            raise

    async def get_binding(
        self,
        novel_id: str,
        binding_id: str,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any]:
        """按小说与稳定业务 ID 获取一条未删除绑定。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            binding_id: 稳定绑定业务 ID。
            session: 可选 MongoDB 会话。

        Returns:
            命中的角色—势力绑定文档。
        """
        binding = await self.find_one(
            {"novel_id": to_object_id(novel_id), "binding_id": binding_id},
            session=session,
        )
        if binding is None:
            raise NotFoundError(f"角色—势力绑定 '{binding_id}' 不存在于小说 {novel_id}")
        return binding

    async def find_by_creation_idempotency(
        self,
        novel_id: str,
        key_hash: str,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any] | None:
        """按小说与创建幂等键哈希读取原绑定结果。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            key_hash: 已校验原始 Idempotency-Key 的 SHA-256。
            session: 可选 MongoDB 会话。

        Returns:
            首次创建的绑定文档；尚未使用该键时返回 None。
        """
        return await self.find_one(
            {
                "novel_id": to_object_id(novel_id),
                "creation_idempotency_key_hash": key_hash,
            },
            include_deleted=True,
            session=session,
        )

    async def list_bindings(
        self,
        novel_id: str,
        *,
        character_id: str | None = None,
        faction_id: str | None = None,
        membership_type: str | None = None,
        is_active: bool | None = True,
        session: AsyncClientSession | None = None,
    ) -> list[dict[str, Any]]:
        """列出小说内绑定，可按角色、势力、类型和有效状态过滤。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            character_id: 可选角色业务 ID。
            faction_id: 可选势力业务 ID。
            membership_type: 可选成员类型。
            is_active: 是否按活动状态过滤；None 表示不筛选。
            session: 可选 MongoDB 会话。

        Returns:
            按 sort_order 与 binding_id 稳定排序的绑定文档列表。
        """
        query: dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if character_id:
            query["character_id"] = character_id
        if faction_id:
            query["faction_id"] = faction_id
        if membership_type:
            query["membership_type"] = membership_type
        if is_active is not None:
            query["is_active"] = is_active
        return await self.find_many(
            query,
            sort=[("sort_order", 1), ("binding_id", 1)],
            session=session,
        )

    async def get_bindings_by_ids(
        self,
        novel_id: str,
        binding_ids: list[str] | tuple[str, ...],
        session: AsyncClientSession | None = None,
    ) -> list[dict[str, Any]]:
        """按显式业务 ID 集合读取未删除绑定。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            binding_ids: 需要读取的稳定绑定业务 ID。
            session: 可选 MongoDB 会话。

        Returns:
            按调用方 ID 顺序排列的绑定文档列表。
        """
        ordered_ids = list(dict.fromkeys(binding_ids))
        if not ordered_ids:
            return []
        documents = await self.find_many(
            {
                "novel_id": to_object_id(novel_id),
                "binding_id": {"$in": ordered_ids},
            },
            session=session,
        )
        by_id = {str(document["binding_id"]): document for document in documents}
        return [by_id[binding_id] for binding_id in ordered_ids if binding_id in by_id]

    async def find_active_primary_bindings(
        self,
        novel_id: str,
        character_ids: list[str] | tuple[str, ...],
        session: AsyncClientSession | None = None,
    ) -> list[dict[str, Any]]:
        """查询指定角色当前存在的活动主归属。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            character_ids: 需要检查的角色业务 ID。
            session: 可选 MongoDB 会话。

        Returns:
            命中的活动 primary 绑定文档。
        """
        unique_ids = list(dict.fromkeys(character_ids))
        if not unique_ids:
            return []
        return await self.find_many(
            {
                "novel_id": to_object_id(novel_id),
                "character_id": {"$in": unique_ids},
                "membership_type": "primary",
                "is_active": True,
            },
            session=session,
        )

    async def find_active_semantic_bindings(
        self,
        novel_id: str,
        semantic_bindings: list[tuple[str, str, str]],
        session: AsyncClientSession | None = None,
    ) -> list[dict[str, Any]]:
        """查找重复的活动角色—势力—成员类型组合。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            semantic_bindings: character_id、faction_id、membership_type 三元组。
            session: 可选 MongoDB 会话。

        Returns:
            与任一三元组匹配的活动绑定文档。
        """
        unique_bindings = list(dict.fromkeys(semantic_bindings))
        if not unique_bindings:
            return []
        return await self.find_many(
            {
                "novel_id": to_object_id(novel_id),
                "is_active": True,
                "$or": [
                    {
                        "character_id": character_id,
                        "faction_id": faction_id,
                        "membership_type": membership_type,
                    }
                    for character_id, faction_id, membership_type in unique_bindings
                ],
            },
            session=session,
        )

    async def delete_bindings_exact(
        self,
        novel_id: str,
        binding_ids: list[str] | tuple[str, ...],
        session: AsyncClientSession | None = None,
    ) -> int:
        """物理删除本次命令明确创建的绑定 ID，用于失败补偿。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            binding_ids: 本次命令分配并可能已写入的绑定业务 ID。
            session: 可选 MongoDB 会话。

        Returns:
            实际删除的文档数量。
        """
        exact_ids = list(dict.fromkeys(binding_ids))
        if not exact_ids:
            return 0
        obj_id = to_object_id(novel_id)
        documents = await self.collection.find(
            {"novel_id": obj_id, "binding_id": {"$in": exact_ids}},
            projection={"_id": 1, "novel_id": 1, "binding_id": 1, "version": 1},
            session=session,
        ).to_list(length=None)
        return await delete_created_documents_cas(
            self.collection,
            documents,
            business_key_field="binding_id",
            session=session,
        )


character_faction_binding_repo = CharacterFactionBindingRepository()
