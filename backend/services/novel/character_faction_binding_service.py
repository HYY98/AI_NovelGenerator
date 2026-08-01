"""全书级角色—势力绑定领域服务。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pymongo.asynchronous.client_session import AsyncClientSession

from backend.db.errors import DuplicateKeyError
from backend.db.endpoint_guard import (
    DependencyGuardContribution,
    rollback_dependency_endpoint_guards,
    touch_dependency_endpoint_guards,
)
from backend.db.repositories.character_faction_binding_repository import character_faction_binding_repo
from backend.db.repositories.character_repository import character_repo
from backend.db.repositories.faction_repository import faction_repo
from backend.db.repositories.id_sequence_repository import id_sequence_repo
from backend.db.repositories.novel_repository import novel_repo
from backend.db.transaction import run_mongo_write_unit
from backend.services.novel.character_name import (
    GenerationDomainError,
    checksum,
    hash_idempotency_key,
)


INITIAL_MEMBERSHIP_TYPES = frozenset({"primary", "secondary", "covert"})


def _payload_to_dict(payload: Any) -> dict[str, Any]:
    """将 Pydantic 模型或普通映射转换为独立字典。

    Args:
        payload: Pydantic V2 模型或可转换为 dict 的绑定载荷。

    Returns:
        不与调用方共享可变状态的字典。
    """
    if hasattr(payload, "model_dump"):
        return dict(payload.model_dump(mode="json"))
    return dict(payload)


def _normalize_scalar(value: Any) -> Any:
    """把枚举值转换为其公开字符串值。

    Args:
        value: 任意字段值。

    Returns:
        Enum 的 value 或原始值。
    """
    return value.value if isinstance(value, Enum) else value


async def _load_usable_binding_endpoint_signatures(
    novel_id: str,
    payloads: list[dict[str, Any]],
    *,
    session: AsyncClientSession | None,
) -> tuple[
    dict[str, tuple[Any, Any, str, bool, bool]],
    dict[str, tuple[Any, Any, str, str, bool]],
]:
    """读取绑定端点可用性并生成角色、势力并发比较签名。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        payloads: 本次待创建绑定的规范载荷。
        session: 可选 MongoDB 会话。

    Returns:
        角色签名映射与势力签名映射组成的二元组。
    """
    character_ids = sorted({str(item["character_id"]) for item in payloads})
    faction_ids = sorted({str(item["faction_id"]) for item in payloads})
    characters = await character_repo.get_characters_by_ids(
        novel_id,
        character_ids,
        session=session,
    )
    factions = await faction_repo.get_factions_by_novel(novel_id, session=session)
    characters_by_id = {
        str(character["character_id"]): character for character in characters
    }
    factions_by_id = {
        str(faction["faction_id"]): faction
        for faction in factions
        if str(faction.get("faction_id") or "") in set(faction_ids)
    }
    invalid_characters = [
        character_id
        for character_id in character_ids
        if character_id not in characters_by_id
        or characters_by_id[character_id].get("status") != "active"
        or characters_by_id[character_id].get("is_core_character") is not True
        or characters_by_id[character_id].get("is_deleted") is True
    ]
    invalid_factions = [
        faction_id
        for faction_id in faction_ids
        if faction_id not in factions_by_id
        or factions_by_id[faction_id].get("active_status") != "active"
        or factions_by_id[faction_id].get("level_type") != "core"
        or factions_by_id[faction_id].get("is_deleted") is True
    ]
    if invalid_characters or invalid_factions:
        raise ValueError(
            "角色—势力绑定端点当前不可用: "
            f"characters={invalid_characters}, factions={invalid_factions}"
        )
    character_signatures = {
        character_id: (
            characters_by_id[character_id].get("_id"),
            characters_by_id[character_id].get("version"),
            str(characters_by_id[character_id].get("status")),
            characters_by_id[character_id].get("is_core_character") is True,
            characters_by_id[character_id].get("is_deleted") is True,
        )
        for character_id in character_ids
    }
    faction_signatures = {
        faction_id: (
            factions_by_id[faction_id].get("_id"),
            factions_by_id[faction_id].get("version"),
            str(factions_by_id[faction_id].get("active_status")),
            str(factions_by_id[faction_id].get("level_type")),
            factions_by_id[faction_id].get("is_deleted") is True,
        )
        for faction_id in faction_ids
    }
    return character_signatures, faction_signatures


async def _rollback_binding_endpoint_touches(
    character_touches: list[DependencyGuardContribution],
    faction_touches: list[DependencyGuardContribution],
) -> None:
    """回退未落库绑定自己的两类端点 guard，并完整尝试两侧。

    Args:
        character_touches: 已推进的角色端点一次性贡献记录。
        faction_touches: 已推进的势力端点一次性贡献记录。

    Returns:
        无；逐条减回尚未消费的本请求贡献。
    """
    if not character_touches and not faction_touches:
        return
    failures: list[Exception] = []
    for collection, touches in (
        (faction_repo.collection, faction_touches),
        (character_repo.collection, character_touches),
    ):
        if not touches:
            continue
        try:
            await rollback_dependency_endpoint_guards(collection, touches)
        except Exception as exc:  # 补偿必须继续尝试另一类端点，最后统一显式失败。
            failures.append(exc)
    if failures:
        raise RuntimeError("绑定失败后的端点 guard 补偿未完整完成") from failures[0]


async def _touch_binding_endpoint_signatures(
    signatures: tuple[
        dict[str, tuple[Any, Any, str, bool, bool]],
        dict[str, tuple[Any, Any, str, str, bool]],
    ],
    *,
    session: AsyncClientSession | None,
) -> tuple[
    list[DependencyGuardContribution],
    list[DependencyGuardContribution],
]:
    """按两类端点签名推进绑定写入所需的内部护栏。

    Args:
        signatures: 角色、势力端点完整签名。
        session: 可选 MongoDB 会话。

    Returns:
        已推进的角色和势力一次性 guard 贡献记录。
    """
    character_signatures, faction_signatures = signatures
    character_queries = [
        {
            "_id": signature[0],
            "character_id": character_id,
            "version": (
                signature[1]
                if signature[1] is not None
                else {"$exists": False}
            ),
            "status": "active",
            "is_core_character": True,
            "is_deleted": {"$ne": True},
        }
        for character_id, signature in character_signatures.items()
    ]
    faction_queries = [
        {
            "_id": signature[0],
            "faction_id": faction_id,
            "version": (
                signature[1]
                if signature[1] is not None
                else {"$exists": False}
            ),
            "active_status": "active",
            "level_type": "core",
            "is_deleted": {"$ne": True},
        }
        for faction_id, signature in faction_signatures.items()
    ]
    completed_characters: list[DependencyGuardContribution] = []
    completed_factions: list[DependencyGuardContribution] = []
    try:
        completed_characters = await touch_dependency_endpoint_guards(
            character_repo.collection,
            character_queries,
            session=session,
        )
        completed_factions = await touch_dependency_endpoint_guards(
            faction_repo.collection,
            faction_queries,
            session=session,
        )
    except Exception:
        if session is None:
            await _rollback_binding_endpoint_touches(
                completed_characters,
                completed_factions,
            )
        raise
    return completed_characters, completed_factions


class CharacterFactionBindingService:
    """维护 active 核心角色与 active 核心势力之间的全书级初始绑定。"""

    @staticmethod
    async def _validate_binding_payloads(
        novel_id: str,
        payloads: list[dict[str, Any]],
        session: AsyncClientSession | None = None,
    ) -> list[str]:
        """批量校验绑定端点、初始状态、主归属唯一性和语义重复。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            payloads: 已转换为字典的绑定创建载荷。
            session: 可选 MongoDB 会话。

        Returns:
            稳定顺序的业务校验错误列表。
        """
        await novel_repo.get_novel_by_id(novel_id, session=session)
        character_ids = sorted(
            {str(payload.get("character_id") or "") for payload in payloads if payload.get("character_id")}
        )
        faction_ids = sorted(
            {str(payload.get("faction_id") or "") for payload in payloads if payload.get("faction_id")}
        )
        characters = await character_repo.get_characters_by_ids(
            novel_id,
            character_ids,
            session=session,
        )
        all_factions = await faction_repo.get_factions_by_novel(novel_id, session=session)
        characters_by_id = {str(character["character_id"]): character for character in characters}
        factions_by_id = {
            str(faction["faction_id"]): faction
            for faction in all_factions
            if str(faction.get("faction_id") or "") in set(faction_ids)
        }

        errors: list[str] = []
        semantic_owner: dict[tuple[str, str, str], str] = {}
        primary_owner: dict[str, str] = {}
        semantic_bindings: list[tuple[str, str, str]] = []

        for index, payload in enumerate(payloads):
            label = str(payload.get("binding_ref") or f"bindings[{index}]")
            character_id = str(payload.get("character_id") or "")
            faction_id = str(payload.get("faction_id") or "")
            membership_type = str(_normalize_scalar(payload.get("membership_type")) or "")
            loyalty_level = payload.get("loyalty_level")

            if not character_id or not faction_id:
                errors.append(f"{label}: character_id 与 faction_id 均为必填")
                continue
            if membership_type not in INITIAL_MEMBERSHIP_TYPES:
                errors.append(f"{label}: 初始 membership_type 仅允许 primary|secondary|covert")
            if payload.get("is_active", True) is not True:
                errors.append(f"{label}: 初始绑定的 is_active 必须为 true")
            if payload.get("joined_volume_id") is not None or payload.get("left_volume_id") is not None:
                errors.append(f"{label}: 本轮全书级绑定不接受 joined/left volume ID")
            if loyalty_level is not None and (
                isinstance(loyalty_level, bool)
                or not isinstance(loyalty_level, int)
                or not 1 <= loyalty_level <= 5
            ):
                errors.append(f"{label}: loyalty_level 必须为空或为 1 至 5 的整数")

            character = characters_by_id.get(character_id)
            if character is None:
                errors.append(f"{label}: 角色 {character_id} 不存在于当前小说")
            elif character.get("status") != "active" or character.get("is_core_character") is not True:
                errors.append(f"{label}: 角色 {character_id} 必须是 active 核心角色")

            faction = factions_by_id.get(faction_id)
            if faction is None:
                errors.append(f"{label}: 势力 {faction_id} 不存在于当前小说")
            elif faction.get("level_type") != "core" or faction.get("active_status") != "active":
                errors.append(f"{label}: 势力 {faction_id} 必须是 active 核心势力")

            if membership_type in INITIAL_MEMBERSHIP_TYPES:
                semantic = (character_id, faction_id, membership_type)
                previous_label = semantic_owner.get(semantic)
                if previous_label is not None:
                    errors.append(f"{label}: 与 {previous_label} 构成重复活动绑定")
                else:
                    semantic_owner[semantic] = label
                semantic_bindings.append(semantic)

            if membership_type == "primary":
                previous_primary = primary_owner.get(character_id)
                if previous_primary is not None:
                    errors.append(f"{label}: 角色 {character_id} 已在 {previous_primary} 声明主归属")
                else:
                    primary_owner[character_id] = label

        existing_semantic = await character_faction_binding_repo.find_active_semantic_bindings(
            novel_id,
            semantic_bindings,
            session=session,
        )
        for binding in existing_semantic:
            errors.append(
                "活动绑定已存在: "
                f"{binding['character_id']}->{binding['faction_id']}({binding['membership_type']})"
            )

        primary_character_ids = [
            character_id
            for character_id, _ in primary_owner.items()
            if character_id
        ]
        existing_primary = await character_faction_binding_repo.find_active_primary_bindings(
            novel_id,
            primary_character_ids,
            session=session,
        )
        for binding in existing_primary:
            errors.append(
                f"角色 {binding['character_id']} 已有活动主归属绑定 {binding['binding_id']}"
            )
        return errors

    @staticmethod
    async def bulk_create_bindings(
        novel_id: str,
        payloads: list[Any],
        session: AsyncClientSession | None = None,
    ) -> list[dict[str, Any]]:
        """批量创建全书级角色—势力绑定。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            payloads: 正式绑定创建载荷或已映射 character_id 的生成候选。
            session: 可选 MongoDB 会话，用于纳入上层聚合写入。

        Returns:
            按输入顺序返回正式绑定文档。
        """
        normalized_payloads = [_payload_to_dict(payload) for payload in payloads]
        if not normalized_payloads:
            return []
        errors = await CharacterFactionBindingService._validate_binding_payloads(
            novel_id,
            normalized_payloads,
            session=session,
        )
        if errors:
            raise ValueError("角色—势力绑定校验失败: " + "; ".join(errors))
        endpoint_signatures = await _load_usable_binding_endpoint_signatures(
            novel_id,
            normalized_payloads,
            session=session,
        )

        binding_ids = await id_sequence_repo.allocate_many(
            novel_id,
            "character_faction_binding",
            "cfb",
            len(normalized_payloads),
            session=session,
        )
        documents: list[dict[str, Any]] = []
        for payload, binding_id in zip(normalized_payloads, binding_ids):
            suffix = int(binding_id.rsplit("_", 1)[-1])
            notes_value = payload.get("notes")
            if notes_value is None:
                notes_value = payload.get("reason")
            document = {
                "novel_id": novel_id,
                "binding_id": binding_id,
                "character_id": str(payload["character_id"]),
                "faction_id": str(payload["faction_id"]),
                "membership_type": str(_normalize_scalar(payload["membership_type"])),
                "role_title": (
                    str(payload["role_title"]).strip()
                    if payload.get("role_title") is not None
                    else None
                ),
                "public_status": (
                    str(payload["public_status"]).strip()
                    if payload.get("public_status") is not None
                    else None
                ),
                "loyalty_level": payload.get("loyalty_level"),
                "joined_volume_id": None,
                "left_volume_id": None,
                "is_active": True,
                "notes": str(notes_value).strip() if notes_value is not None else None,
                "sort_order": suffix * 10,
                "version": 1,
            }
            # 只有人工单条创建需要持久化幂等摘要；AI 批量写入不制造空审计字段。
            for field in ("creation_idempotency_key_hash", "creation_request_hash"):
                if payload.get(field) is not None:
                    document[field] = payload[field]
            documents.append(document)

        # 两类端点均先推进内部护栏，避免聚合补偿删除已被绑定引用的新主档。
        character_touches, faction_touches = await _touch_binding_endpoint_signatures(
            endpoint_signatures,
            session=session,
        )
        try:
            created = await character_faction_binding_repo.insert_bindings(
                documents,
                session=session,
            )
            current_signatures = await _load_usable_binding_endpoint_signatures(
                novel_id,
                normalized_payloads,
                session=session,
            )
            if current_signatures != endpoint_signatures:
                raise DuplicateKeyError("角色—势力绑定创建期间端点已被并发修改，请重试")
        except Exception:
            if session is None:
                await character_faction_binding_repo.delete_bindings_exact(
                    novel_id,
                    binding_ids,
                )
                await _rollback_binding_endpoint_touches(
                    character_touches,
                    faction_touches,
                )
            raise
        return created

    @staticmethod
    async def create_binding(
        novel_id: str,
        payload: Any,
        *,
        idempotency_key: str,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any]:
        """创建一条人工全书级角色—势力绑定。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            payload: 绑定创建 DTO 或等价映射。
            idempotency_key: 调用方提供的创建幂等键。
            session: 可选 MongoDB 会话。

        Returns:
            新创建的正式绑定文档。
        """
        source = _payload_to_dict(payload)
        key_hash = hash_idempotency_key(idempotency_key)
        request_hash = checksum({"novel_id": novel_id, "payload": source})

        async def _find_replay(
            active_session: AsyncClientSession | None,
        ) -> dict[str, Any] | None:
            """读取并校验同一幂等键的原绑定创建结果。"""
            existing = await character_faction_binding_repo.find_by_creation_idempotency(
                novel_id,
                key_hash,
                session=active_session,
            )
            if existing is None:
                return None
            if existing.get("creation_request_hash") != request_hash:
                raise GenerationDomainError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "同一 Idempotency-Key 已用于不同的角色阵营绑定创建请求",
                    status_code=409,
                )
            return existing

        async def _create(
            active_session: AsyncClientSession | None,
        ) -> dict[str, Any]:
            """在一个写入单元内完成幂等回放与绑定创建。"""
            await novel_repo.get_novel_by_id(novel_id, session=active_session)
            replay = await _find_replay(active_session)
            if replay is not None:
                return replay
            source["creation_idempotency_key_hash"] = key_hash
            source["creation_request_hash"] = request_hash
            created = await CharacterFactionBindingService.bulk_create_bindings(
                novel_id,
                [source],
                session=active_session,
            )
            return created[0]

        if session is not None:
            return await _create(session)
        try:
            return await run_mongo_write_unit(_create, "create_character_faction_binding")
        except DuplicateKeyError:
            # 唯一索引解决并发抢占，输家回放首次成功写入的业务 ID。
            replay = await _find_replay(None)
            if replay is not None:
                return replay
            raise

    @staticmethod
    async def get_binding(
        novel_id: str,
        binding_id: str,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any]:
        """读取指定小说内的一条角色—势力绑定。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            binding_id: 稳定绑定业务 ID。
            session: 可选 MongoDB 会话。

        Returns:
            角色—势力绑定文档。
        """
        await novel_repo.get_novel_by_id(novel_id, session=session)
        return await character_faction_binding_repo.get_binding(novel_id, binding_id, session=session)

    @staticmethod
    async def list_bindings(
        novel_id: str,
        *,
        character_id: str | None = None,
        faction_id: str | None = None,
        membership_type: str | None = None,
        is_active: bool | None = True,
        session: AsyncClientSession | None = None,
    ) -> list[dict[str, Any]]:
        """列出小说内角色—势力绑定。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            character_id: 可选角色业务 ID。
            faction_id: 可选势力业务 ID。
            membership_type: 可选成员类型。
            is_active: 可选活动状态过滤。
            session: 可选 MongoDB 会话。

        Returns:
            稳定排序的绑定文档列表。
        """
        await novel_repo.get_novel_by_id(novel_id, session=session)
        if membership_type is not None and membership_type not in INITIAL_MEMBERSHIP_TYPES:
            raise ValueError(f"本轮不支持 membership_type={membership_type}")
        if character_id:
            character = await character_repo.get_character(novel_id, character_id, session=session)
            if character.get("status") != "active" or character.get("is_core_character") is not True:
                raise ValueError(f"角色 {character_id} 必须是 active 核心角色")
        if faction_id:
            faction = await faction_repo.get_faction(novel_id, faction_id, session=session)
            if faction.get("level_type") != "core" or faction.get("active_status") != "active":
                raise ValueError(f"势力 {faction_id} 必须是 active 核心势力")
        return await character_faction_binding_repo.list_bindings(
            novel_id,
            character_id=character_id,
            faction_id=faction_id,
            membership_type=membership_type,
            is_active=is_active,
            session=session,
        )
