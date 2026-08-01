"""全书级角色关系领域服务。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pymongo.asynchronous.client_session import AsyncClientSession

from backend.db.errors import DuplicateKeyError, NotFoundError
from backend.db.endpoint_guard import (
    DependencyGuardContribution,
    rollback_dependency_endpoint_guards,
    touch_dependency_endpoint_guards,
)
from backend.db.repositories.character_relation_repository import character_relation_repo
from backend.db.repositories.character_repository import character_repo
from backend.db.repositories.id_sequence_repository import id_sequence_repo
from backend.db.repositories.novel_repository import novel_repo
from backend.db.transaction import run_mongo_write_unit
from backend.services.novel.character_name import (
    GenerationDomainError,
    checksum,
    hash_idempotency_key,
)
from backend.services.novel.character_service import (
    _restore_snapshots,
    _updated_compensation_record,
)


RELATION_TYPES = frozenset(
    {
        "friend",
        "romantic",
        "ally",
        "rival",
        "enemy",
        "parent_of",
        "mentor_of",
        "superior_of",
        "protector_of",
        "debtor_to",
    }
)
SYMMETRIC_RELATION_TYPES = frozenset({"friend", "romantic", "ally", "rival", "enemy"})
RELATION_CONTENT_FIELDS = (
    "relation_type",
    "current_state",
    "core_conflict",
    "hidden_tension",
    "possible_change",
    "story_value",
    "intensity",
)


def _payload_to_dict(payload: Any) -> dict[str, Any]:
    """将 Pydantic 模型或普通映射转换为独立字典。

    Args:
        payload: Pydantic V2 模型或可转换为 dict 的关系载荷。

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


def normalize_relation_endpoints(
    source_character_id: str,
    target_character_id: str,
    relation_type: str,
) -> tuple[str, str]:
    """按关系方向规则生成稳定语义端点。

    Args:
        source_character_id: 来源角色业务 ID。
        target_character_id: 目标角色业务 ID。
        relation_type: 角色关系类型。

    Returns:
        用于去重与唯一索引的规范化 source、target 元组。
    """
    if relation_type in SYMMETRIC_RELATION_TYPES:
        return tuple(sorted((source_character_id, target_character_id)))
    return source_character_id, target_character_id


def _character_endpoint_signature(
    character: dict[str, Any],
) -> tuple[Any, Any, str, bool, bool]:
    """生成不含内部 guard revision 的角色端点业务签名。

    Args:
        character: 同小说作用域内的角色主档。

    Returns:
        身份、公开版本、状态、核心标记和删除态组成的签名。
    """
    return (
        character.get("_id"),
        character.get("version"),
        str(character.get("status")),
        character.get("is_core_character") is True,
        character.get("is_deleted") is True,
    )


async def _load_character_endpoint_signatures(
    novel_id: str,
    character_ids: list[str],
    *,
    require_usable: bool,
    session: AsyncClientSession | None,
) -> dict[str, tuple[Any, Any, str, bool, bool]]:
    """读取人物关系端点并生成可用于写后复核的业务签名。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        character_ids: 稳定角色业务 ID。
        require_usable: 是否要求端点为未删除 active 核心角色。
        session: 可选 MongoDB 会话。

    Returns:
        角色 ID 到业务签名的映射。
    """
    by_id: dict[str, dict[str, Any]] = {}
    for character_id in character_ids:
        try:
            by_id[character_id] = await character_repo.get_character(
                novel_id,
                character_id,
                include_deleted=True,
                session=session,
            )
        except NotFoundError as exc:
            raise ValueError(f"角色端点 {character_id} 不存在于当前小说") from exc
    invalid = [
        character_id
        for character_id, character in by_id.items()
        if require_usable
        and (
            character.get("status") != "active"
            or character.get("is_core_character") is not True
            or character.get("is_deleted") is True
        )
    ]
    if invalid:
        raise ValueError("角色关系端点当前不可用: " + ", ".join(invalid))
    return {
        character_id: _character_endpoint_signature(by_id[character_id])
        for character_id in character_ids
    }


async def _touch_character_endpoint_signatures(
    signatures: dict[
        str,
        tuple[Any, Any, str, bool, bool],
    ],
    *,
    session: AsyncClientSession | None,
) -> list[DependencyGuardContribution]:
    """按角色端点业务签名推进内部依赖护栏。

    Args:
        signatures: 写入前读取的角色端点业务签名。
        session: 可选 MongoDB 会话。

    Returns:
        每个端点业务查询及其 touch 前 guard 基线，供失败补偿使用。
    """
    queries = [
        {
            "_id": signature[0],
            "character_id": character_id,
            "version": (
                signature[1]
                if signature[1] is not None
                else {"$exists": False}
            ),
            "status": signature[2],
            "is_core_character": True if signature[3] else {"$ne": True},
            "is_deleted": True if signature[4] else {"$ne": True},
        }
        for character_id, signature in signatures.items()
    ]
    return await touch_dependency_endpoint_guards(
        character_repo.collection,
        queries,
        session=session,
    )


async def _compensate_character_relation_update(
    snapshot: dict[str, Any],
    written: dict[str, Any],
) -> None:
    """精确回滚 standalone 人物关系写入且不覆盖并发新版本。

    Args:
        snapshot: 人物关系写入前预镜像。
        written: 本次 CAS 写入返回的写后文档。

    Returns:
        无；仍处于本次写后版本时恢复预镜像。
    """
    await _restore_snapshots(
        character_relation_repo,
        [_updated_compensation_record(snapshot, written)],
    )


async def _load_usable_character_endpoint_signatures(
    novel_id: str,
    character_ids: list[str],
    *,
    session: AsyncClientSession | None,
) -> dict[str, tuple[Any, Any, str, bool, bool]]:
    """读取可用于新建关系的角色端点并生成并发比较签名。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        character_ids: 待校验的稳定角色业务 ID。
        session: 可选 MongoDB 会话。

    Returns:
        角色 ID 到身份、版本与可用性字段签名的映射。
    """
    characters = await character_repo.get_characters_by_ids(
        novel_id,
        character_ids,
        session=session,
    )
    by_id = {str(character["character_id"]): character for character in characters}
    invalid = [
        character_id
        for character_id in character_ids
        if character_id not in by_id
        or by_id[character_id].get("status") != "active"
        or by_id[character_id].get("is_core_character") is not True
        or by_id[character_id].get("is_deleted") is True
    ]
    if invalid:
        raise ValueError("角色关系端点当前不可用: " + ", ".join(invalid))
    return {
        character_id: _character_endpoint_signature(by_id[character_id])
        for character_id in character_ids
    }


class CharacterRelationService:
    """校验全书级核心角色端点并维护规范化关系边。"""

    @staticmethod
    def _ensure_expected_version(relation: dict[str, Any], expected_version: int) -> None:
        """在执行引用校验前快速拒绝明显过期的关系版本。

        Args:
            relation: 当前数据库关系文档。
            expected_version: 调用方提交的版本。

        Returns:
            无；版本一致时正常返回。
        """
        actual_version = int(relation.get("version") or 1)
        if actual_version != expected_version:
            raise DuplicateKeyError(
                f"角色关系 '{relation.get('relation_id')}' 版本冲突："
                f"expected={expected_version}, actual={actual_version}"
            )

    @staticmethod
    async def validate_generation_candidates(
        novel_id: str,
        candidates: list[Any],
        selected_character_ids: list[str] | tuple[str, ...],
        *,
        raise_on_conflict: bool = False,
        require_active: bool = True,
        check_active_semantic: bool = True,
        exclude_relation_id: str | None = None,
        session: AsyncClientSession | None = None,
    ) -> list[str]:
        """执行生成关系候选的端点、枚举、覆盖范围和语义冲突校验。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            candidates: 待校验的关系候选或正式创建载荷。
            selected_character_ids: 本次生成锁定的核心角色 ID 白名单。
            raise_on_conflict: 是否把重复语义边立即作为 409 领域冲突抛出。
            require_active: 是否要求每个载荷的用户启停意图必须为 true。
            check_active_semantic: 是否检查与现有活动语义边的冲突。
            exclude_relation_id: 更新时从冲突查询中排除的当前关系 ID。
            session: 可选 MongoDB 会话。

        Returns:
            稳定顺序的业务校验错误列表；空列表表示全部通过。
        """
        await novel_repo.get_novel_by_id(novel_id, session=session)
        payloads = [_payload_to_dict(candidate) for candidate in candidates]
        selected_ids = set(dict.fromkeys(selected_character_ids))
        endpoint_ids = {
            str(payload.get(field) or "")
            for payload in payloads
            for field in ("source_character_id", "target_character_id")
            if payload.get(field)
        }
        characters = await character_repo.get_characters_by_ids(
            novel_id,
            sorted(endpoint_ids),
            session=session,
        )
        characters_by_id = {str(character["character_id"]): character for character in characters}

        errors: list[str] = []
        conflict_errors: list[str] = []
        semantic_owner: dict[tuple[str, str, str], str] = {}
        candidate_semantics: list[tuple[str, str, str]] = []
        semantic_labels: list[str] = []

        for index, payload in enumerate(payloads):
            label = str(payload.get("relation_ref") or f"relations[{index}]")
            source_id = str(payload.get("source_character_id") or "")
            target_id = str(payload.get("target_character_id") or "")
            relation_type = str(_normalize_scalar(payload.get("relation_type")) or "")
            intensity = payload.get("intensity")

            if not source_id or not target_id:
                errors.append(f"{label}: source_character_id 与 target_character_id 均为必填")
                continue
            if source_id == target_id:
                errors.append(f"{label}: 角色关系不允许自引用 {source_id}")
            if source_id not in selected_ids or target_id not in selected_ids:
                errors.append(f"{label}: 关系端点必须来自本次锁定的角色白名单")
            if relation_type not in RELATION_TYPES:
                errors.append(f"{label}: 不支持的 relation_type={relation_type}")
            if isinstance(intensity, bool) or not isinstance(intensity, int) or not 1 <= intensity <= 5:
                errors.append(f"{label}: intensity 必须为 1 至 5 的整数")
            candidate_is_active = payload.get("is_active", True) is True
            if require_active and not candidate_is_active:
                errors.append(f"{label}: 新生成关系的 is_active 必须为 true")
            for field_name in (
                "current_state",
                "core_conflict",
                "hidden_tension",
                "possible_change",
                "story_value",
            ):
                text_value = payload.get(field_name)
                if not isinstance(text_value, str) or not 1 <= len(text_value.strip()) <= 2000:
                    errors.append(f"{label}: {field_name} 必须为 1 至 2000 字符的非空文本")

            for endpoint_id in (source_id, target_id):
                character = characters_by_id.get(endpoint_id)
                if character is None:
                    errors.append(f"{label}: 角色端点 {endpoint_id} 不存在于当前小说")
                    continue
                if character.get("status") != "active" or character.get("is_core_character") is not True:
                    errors.append(f"{label}: 角色端点 {endpoint_id} 必须是 active 核心角色")

            if (
                relation_type in RELATION_TYPES
                and source_id
                and target_id
                and source_id != target_id
                and candidate_is_active
                and check_active_semantic
            ):
                normalized_source, normalized_target = normalize_relation_endpoints(
                    source_id,
                    target_id,
                    relation_type,
                )
                semantic = (normalized_source, normalized_target, relation_type)
                previous_label = semantic_owner.get(semantic)
                if previous_label is not None:
                    message = f"{label}: 与 {previous_label} 构成重复语义关系"
                    errors.append(message)
                    conflict_errors.append(message)
                else:
                    semantic_owner[semantic] = label
                candidate_semantics.append(semantic)
                semantic_labels.append(label)

        existing_relations = await character_relation_repo.find_active_semantic_relations(
            novel_id,
            candidate_semantics,
            exclude_relation_id=exclude_relation_id,
            session=session,
        )
        existing_semantics = {
            (
                str(relation["normalized_source_character_id"]),
                str(relation["normalized_target_character_id"]),
                str(relation["relation_type"]),
            ): str(relation["relation_id"])
            for relation in existing_relations
        }
        for label, semantic in zip(semantic_labels, candidate_semantics):
            conflicting_relation_id = existing_semantics.get(semantic)
            if conflicting_relation_id:
                message = f"{label}: 与既有活动关系 {conflicting_relation_id} 语义重复"
                errors.append(message)
                conflict_errors.append(message)

        if raise_on_conflict and conflict_errors:
            raise DuplicateKeyError("角色关系语义冲突: " + "; ".join(conflict_errors))
        return errors

    @staticmethod
    async def bulk_create_relations(
        novel_id: str,
        payloads: list[Any],
        *,
        force_active: bool = False,
        session: AsyncClientSession | None = None,
    ) -> list[dict[str, Any]]:
        """批量创建已确认的全书级角色关系。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            payloads: 关系创建载荷或已验证生成候选。
            force_active: 是否强制 AI 生成载荷全部保持启用。
            session: 可选 MongoDB 会话，用于纳入上层聚合写入。

        Returns:
            按输入顺序返回正式角色关系文档。
        """
        normalized_payloads = [_payload_to_dict(payload) for payload in payloads]
        if not normalized_payloads:
            return []
        selected_ids = sorted(
            {
                str(payload.get(field) or "")
                for payload in normalized_payloads
                for field in ("source_character_id", "target_character_id")
                if payload.get(field)
            }
        )
        errors = await CharacterRelationService.validate_generation_candidates(
            novel_id,
            normalized_payloads,
            selected_ids,
            raise_on_conflict=True,
            require_active=force_active,
            session=session,
        )
        if errors:
            raise ValueError("角色关系校验失败: " + "; ".join(errors))
        endpoint_signatures = await _load_usable_character_endpoint_signatures(
            novel_id,
            selected_ids,
            session=session,
        )

        relation_ids = await id_sequence_repo.allocate_many(
            novel_id,
            "character_relation",
            "cr",
            len(normalized_payloads),
            session=session,
        )
        documents: list[dict[str, Any]] = []
        for payload, relation_id in zip(normalized_payloads, relation_ids):
            source_id = str(payload["source_character_id"])
            target_id = str(payload["target_character_id"])
            relation_type = str(_normalize_scalar(payload["relation_type"]))
            normalized_source, normalized_target = normalize_relation_endpoints(
                source_id,
                target_id,
                relation_type,
            )
            if relation_type in SYMMETRIC_RELATION_TYPES:
                # 对称关系的正式端点也使用稳定顺序，彻底消除反向重复表示。
                source_id, target_id = normalized_source, normalized_target

            suffix = int(relation_id.rsplit("_", 1)[-1])
            document = {
                "novel_id": novel_id,
                "relation_id": relation_id,
                "source_character_id": source_id,
                "target_character_id": target_id,
                "normalized_source_character_id": normalized_source,
                "normalized_target_character_id": normalized_target,
                "relation_type": relation_type,
                "semantic_key": f"{normalized_source}:{normalized_target}:{relation_type}",
                "current_state": str(payload.get("current_state") or "").strip(),
                "core_conflict": str(payload.get("core_conflict") or "").strip(),
                "hidden_tension": str(payload.get("hidden_tension") or "").strip(),
                "possible_change": str(payload.get("possible_change") or "").strip(),
                "story_value": str(payload.get("story_value") or "").strip(),
                "intensity": int(payload["intensity"]),
                "user_is_active": bool(payload.get("is_active", True)),
                "is_active": bool(payload.get("is_active", True)),
                "disabled_by_character_ids": [],
                "sort_order": int(payload.get("sort_order", suffix * 10)),
                "version": 1,
            }
            # 只有人工单条创建需要持久化幂等摘要；AI 批量写入不保留候选局部引用。
            for field in ("creation_idempotency_key_hash", "creation_request_hash"):
                if payload.get(field) is not None:
                    document[field] = payload[field]
            documents.append(document)

        # 先推进端点内部护栏，再写依赖；进程若在两步之间退出，只留下无害的内部 revision。
        guard_touches = await _touch_character_endpoint_signatures(
            endpoint_signatures,
            session=session,
        )
        try:
            created = await character_relation_repo.insert_relations(
                documents,
                session=session,
            )
            current_signatures = await _load_usable_character_endpoint_signatures(
                novel_id,
                selected_ids,
                session=session,
            )
            if current_signatures != endpoint_signatures:
                raise DuplicateKeyError("角色关系创建期间端点已被并发修改，请重试")
        except Exception:
            # 写后复核失败时只补偿仍停留在 v1 的本批关系，绝不删除并发推进后的记录。
            if session is None:
                await character_relation_repo.delete_relations_exact(
                    novel_id,
                    relation_ids,
                )
                if guard_touches:
                    await rollback_dependency_endpoint_guards(
                        character_repo.collection,
                        guard_touches,
                    )
            raise
        return created

    @staticmethod
    async def bulk_append_relations(
        novel_id: str,
        result: Any,
    ) -> dict[str, Any]:
        """把已选择的全书级关系候选追加为正式关系。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            result: 已通过 CharacterRelationsResultSchemaV1 校验的生成结果。

        Returns:
            包含新建正式关系列表的字典。
        """
        source = _payload_to_dict(result)
        candidates = list(source.get("relations") or [])
        if not candidates:
            raise ValueError("至少需要一条角色关系候选")

        normalized_candidates = [_payload_to_dict(candidate) for candidate in candidates]
        relation_refs = [
            str(candidate.get("relation_ref") or "").strip()
            for candidate in normalized_candidates
        ]
        if any(not relation_ref for relation_ref in relation_refs):
            raise ValueError("角色关系候选缺少 relation_ref")
        if len(relation_refs) != len(set(relation_refs)):
            raise ValueError("角色关系候选 relation_ref 不能重复")

        selected_ids = sorted(
            {
                str(candidate.get(field) or "")
                for candidate in normalized_candidates
                for field in ("source_character_id", "target_character_id")
                if candidate.get(field)
            }
        )
        # 在进入写入单元前完成端点、活动核心角色和全部语义冲突校验。
        errors = await CharacterRelationService.validate_generation_candidates(
            novel_id,
            normalized_candidates,
            selected_ids,
            raise_on_conflict=True,
        )
        if errors:
            raise ValueError("角色关系校验失败: " + "; ".join(errors))

        async def _append(active_session: AsyncClientSession | None) -> dict[str, Any]:
            """在一个写入单元内校验并追加正式关系。"""
            await novel_repo.get_novel_by_id(novel_id, session=active_session)
            created = await CharacterRelationService.bulk_create_relations(
                novel_id,
                normalized_candidates,
                force_active=True,
                session=active_session,
            )
            return {
                "character_relations": created,
            }

        return await run_mongo_write_unit(_append, "bulk_append_character_relations")

    @staticmethod
    async def create_relation(
        novel_id: str,
        payload: Any,
        *,
        idempotency_key: str,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any]:
        """创建一条人工全书级角色关系。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            payload: 角色关系创建 DTO 或等价映射。
            idempotency_key: 调用方提供的创建幂等键。
            session: 可选 MongoDB 会话。

        Returns:
            新创建的正式角色关系文档。
        """
        source = _payload_to_dict(payload)
        key_hash = hash_idempotency_key(idempotency_key)
        request_hash = checksum({"novel_id": novel_id, "payload": source})

        async def _find_replay(
            active_session: AsyncClientSession | None,
        ) -> dict[str, Any] | None:
            """读取并校验同一幂等键的原关系创建结果。"""
            existing = await character_relation_repo.find_by_creation_idempotency(
                novel_id,
                key_hash,
                session=active_session,
            )
            if existing is None:
                return None
            if existing.get("creation_request_hash") != request_hash:
                raise GenerationDomainError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "同一 Idempotency-Key 已用于不同的角色关系创建请求",
                    status_code=409,
                )
            return existing

        async def _create(
            active_session: AsyncClientSession | None,
        ) -> dict[str, Any]:
            """在一个写入单元内完成幂等回放与关系创建。"""
            await novel_repo.get_novel_by_id(novel_id, session=active_session)
            replay = await _find_replay(active_session)
            if replay is not None:
                return replay
            source["creation_idempotency_key_hash"] = key_hash
            source["creation_request_hash"] = request_hash
            created = await CharacterRelationService.bulk_create_relations(
                novel_id,
                [source],
                session=active_session,
            )
            return created[0]

        if session is not None:
            return await _create(session)
        try:
            return await run_mongo_write_unit(_create, "create_character_relation")
        except DuplicateKeyError:
            # 唯一索引解决并发抢占，输家回放首次成功写入的业务 ID。
            replay = await _find_replay(None)
            if replay is not None:
                return replay
            raise

    @staticmethod
    async def get_relation(
        novel_id: str,
        relation_id: str,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any]:
        """读取指定小说内的一条角色关系。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            relation_id: 稳定角色关系业务 ID。
            session: 可选 MongoDB 会话。

        Returns:
            角色关系文档。
        """
        await novel_repo.get_novel_by_id(novel_id, session=session)
        return await character_relation_repo.get_relation(novel_id, relation_id, session=session)

    @staticmethod
    async def list_relations(
        novel_id: str,
        *,
        character_id: str | None = None,
        relation_type: str | None = None,
        is_active: bool | None = None,
        session: AsyncClientSession | None = None,
    ) -> list[dict[str, Any]]:
        """列出小说内角色关系。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            character_id: 可选角色端点业务 ID。
            relation_type: 可选关系类型。
            is_active: 可选活动状态过滤。
            session: 可选 MongoDB 会话。

        Returns:
            稳定排序的角色关系文档列表。
        """
        await novel_repo.get_novel_by_id(novel_id, session=session)
        if relation_type is not None and relation_type not in RELATION_TYPES:
            raise ValueError(f"不支持的 relation_type={relation_type}")
        if character_id:
            character = await character_repo.get_character(novel_id, character_id, session=session)
            if character.get("status") != "active" or character.get("is_core_character") is not True:
                raise ValueError(f"角色 {character_id} 必须是 active 核心角色")
        return await character_relation_repo.list_relations(
            novel_id,
            character_id=character_id,
            relation_type=relation_type,
            is_active=is_active,
            session=session,
        )

    @staticmethod
    async def _validate_persisted_relation(
        novel_id: str,
        relation: dict[str, Any],
        *,
        relation_type: str,
        should_be_active: bool,
        require_active_endpoints: bool,
        session: AsyncClientSession | None,
    ) -> tuple[str, str]:
        """复核既有关系端点并计算新的规范化端点。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            relation: 当前关系文档。
            relation_type: 待保存的关系类型。
            should_be_active: 保存后是否会成为活动关系。
            require_active_endpoints: 是否要求两端当前均为 active。
            session: 可选 MongoDB 会话。

        Returns:
            规范化来源与目标角色业务 ID。
        """
        source_id = str(relation["source_character_id"])
        target_id = str(relation["target_character_id"])
        if source_id == target_id:
            raise ValueError("角色关系不能指向自身")
        if relation_type not in RELATION_TYPES:
            raise ValueError(f"不支持的 relation_type={relation_type}")
        for endpoint_id in (source_id, target_id):
            try:
                character = await character_repo.get_character(
                    novel_id,
                    endpoint_id,
                    include_deleted=True,
                    session=session,
                )
            except NotFoundError as exc:
                raise ValueError(f"角色端点 {endpoint_id} 不存在于当前小说") from exc
            if character.get("is_core_character") is not True:
                raise ValueError(f"角色端点 {endpoint_id} 必须是核心角色")
            if require_active_endpoints and (
                character.get("is_deleted") is True
                or character.get("status") != "active"
            ):
                raise ValueError(f"角色端点 {endpoint_id} 当前不可用，关系无法启用")

        normalized = normalize_relation_endpoints(source_id, target_id, relation_type)
        if should_be_active:
            conflicts = await character_relation_repo.find_active_semantic_relations(
                novel_id,
                [(normalized[0], normalized[1], relation_type)],
                exclude_relation_id=str(relation["relation_id"]),
                session=session,
            )
            if conflicts:
                raise DuplicateKeyError(
                    f"角色关系与既有活动关系 {conflicts[0]['relation_id']} 语义重复"
                )
        return normalized

    @staticmethod
    async def update_relation(
        novel_id: str,
        relation_id: str,
        payload: Any,
    ) -> dict[str, Any]:
        """更新角色关系内容，端点与稳定业务 ID 保持不可变。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            relation_id: 稳定角色关系业务 ID。
            payload: 已通过严格 Schema 校验的内容更新请求。

        Returns:
            递增版本后的完整关系文档。
        """
        source = _payload_to_dict(payload)
        expected_version = int(source.pop("expected_version"))

        async def _update(session: AsyncClientSession | None) -> dict[str, Any]:
            """在一个写入单元内复核引用并执行 CAS 更新。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            current = await character_relation_repo.get_relation(
                novel_id,
                relation_id,
                session=session,
            )
            CharacterRelationService._ensure_expected_version(current, expected_version)
            update_fields = {field: source[field] for field in RELATION_CONTENT_FIELDS}
            for field in (
                "current_state",
                "core_conflict",
                "hidden_tension",
                "possible_change",
                "story_value",
            ):
                update_fields[field] = str(update_fields[field]).strip()
            relation_type = str(_normalize_scalar(update_fields["relation_type"]))
            update_fields["relation_type"] = relation_type

            validation_view = {**current, **update_fields}
            remains_active = bool(current.get("is_active"))
            normalized_source, normalized_target = (
                await CharacterRelationService._validate_persisted_relation(
                    novel_id,
                    validation_view,
                    relation_type=relation_type,
                    should_be_active=remains_active,
                    require_active_endpoints=remains_active,
                    session=session,
                )
            )
            update_fields.update(
                {
                    "normalized_source_character_id": normalized_source,
                    "normalized_target_character_id": normalized_target,
                    "semantic_key": (
                        f"{normalized_source}:{normalized_target}:{relation_type}"
                    ),
                }
            )
            endpoint_ids = [
                str(current["source_character_id"]),
                str(current["target_character_id"]),
            ]
            endpoint_signatures = None
            guard_touches: list[DependencyGuardContribution] = []
            if remains_active:
                endpoint_signatures = await _load_character_endpoint_signatures(
                    novel_id,
                    endpoint_ids,
                    require_usable=True,
                    session=session,
                )
                guard_touches = await _touch_character_endpoint_signatures(
                    endpoint_signatures,
                    session=session,
                )
            written: dict[str, Any] | None = None
            try:
                written = await character_relation_repo.update_relation(
                    novel_id,
                    relation_id,
                    update_fields,
                    expected_version,
                    session=session,
                )
                if endpoint_signatures is not None:
                    current_signatures = await _load_character_endpoint_signatures(
                        novel_id,
                        endpoint_ids,
                        require_usable=True,
                        session=session,
                    )
                    if current_signatures != endpoint_signatures:
                        raise DuplicateKeyError(
                            "角色关系更新期间端点已被并发修改，请重试"
                        )
                return written
            except Exception:
                if session is None and written is not None:
                    try:
                        await _compensate_character_relation_update(current, written)
                    except Exception as compensation_exc:
                        raise RuntimeError(
                            "角色关系更新失败，且 standalone 补偿未完成"
                        ) from compensation_exc
                if session is None and guard_touches:
                    await rollback_dependency_endpoint_guards(
                        character_repo.collection,
                        guard_touches,
                    )
                raise

        return await run_mongo_write_unit(_update, "update_character_relation")

    @staticmethod
    async def _current_blockers(
        novel_id: str,
        relation: dict[str, Any],
        *,
        session: AsyncClientSession | None,
    ) -> list[str]:
        """按当前两个角色端点事实重算关系自动阻断来源。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            relation: 当前人物关系文档。
            session: 可选 MongoDB 会话。

        Returns:
            当前已删除、非 active 或非核心角色的端点业务 ID 列表。
        """
        blockers: list[str] = []
        for character_id in (
            str(relation["source_character_id"]),
            str(relation["target_character_id"]),
        ):
            try:
                character = await character_repo.get_character(
                    novel_id,
                    character_id,
                    include_deleted=True,
                    session=session,
                )
            except NotFoundError as exc:
                raise ValueError(f"角色端点 {character_id} 已被彻底删除") from exc
            if (
                character.get("is_deleted") is True
                or character.get("status") != "active"
                or character.get("is_core_character") is not True
            ):
                blockers.append(character_id)
        return blockers

    @staticmethod
    async def set_relation_active(
        novel_id: str,
        relation_id: str,
        *,
        expected_version: int,
        is_active: bool,
    ) -> dict[str, Any]:
        """修改用户启停意图并计算角色关系实际有效态。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            relation_id: 稳定角色关系业务 ID。
            expected_version: 调用方持有的乐观锁版本。
            is_active: 用户期望的启用状态。

        Returns:
            递增版本后的完整关系文档。
        """

        async def _set_active(session: AsyncClientSession | None) -> dict[str, Any]:
            """在一个写入单元内复核端点并执行启停 CAS。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            current = await character_relation_repo.get_relation(
                novel_id,
                relation_id,
                session=session,
            )
            CharacterRelationService._ensure_expected_version(current, expected_version)
            # 每次以端点当前事实重算，避免恢复角色后残留 blocker 或漏记新失效端点。
            blockers = await CharacterRelationService._current_blockers(
                novel_id,
                current,
                session=session,
            )
            if is_active:
                if blockers:
                    raise ValueError(
                        "角色关系仍受失效端点阻断，无法启用: " + ", ".join(blockers)
                    )
                await CharacterRelationService._validate_persisted_relation(
                    novel_id,
                    current,
                    relation_type=str(current["relation_type"]),
                    should_be_active=True,
                    require_active_endpoints=True,
                    session=session,
                )
            endpoint_ids = [
                str(current["source_character_id"]),
                str(current["target_character_id"]),
            ]
            endpoint_signatures = None
            guard_touches: list[DependencyGuardContribution] = []
            if is_active:
                endpoint_signatures = await _load_character_endpoint_signatures(
                    novel_id,
                    endpoint_ids,
                    require_usable=True,
                    session=session,
                )
                guard_touches = await _touch_character_endpoint_signatures(
                    endpoint_signatures,
                    session=session,
                )
            written: dict[str, Any] | None = None
            try:
                written = await character_relation_repo.update_relation(
                    novel_id,
                    relation_id,
                    {
                        "user_is_active": is_active,
                        "disabled_by_character_ids": blockers,
                        "is_active": is_active and not blockers,
                    },
                    expected_version,
                    session=session,
                )
                if endpoint_signatures is not None:
                    current_signatures = await _load_character_endpoint_signatures(
                        novel_id,
                        endpoint_ids,
                        require_usable=True,
                        session=session,
                    )
                    if current_signatures != endpoint_signatures:
                        raise DuplicateKeyError(
                            "角色关系启用期间端点已被并发修改，请重试"
                        )
                return written
            except Exception:
                if session is None and written is not None:
                    try:
                        await _compensate_character_relation_update(current, written)
                    except Exception as compensation_exc:
                        raise RuntimeError(
                            "角色关系启停失败，且 standalone 补偿未完成"
                        ) from compensation_exc
                if session is None and guard_touches:
                    await rollback_dependency_endpoint_guards(
                        character_repo.collection,
                        guard_touches,
                    )
                raise

        return await run_mongo_write_unit(_set_active, "set_character_relation_active")

    @staticmethod
    async def soft_delete_relation(
        novel_id: str,
        relation_id: str,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        """把角色关系移入回收站。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            relation_id: 稳定角色关系业务 ID。
            expected_version: 调用方持有的乐观锁版本。

        Returns:
            软删除后的完整关系文档。
        """

        async def _delete(session: AsyncClientSession | None) -> dict[str, Any]:
            """在一个写入单元内执行软删除 CAS。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            return await character_relation_repo.soft_delete_relation(
                novel_id,
                relation_id,
                expected_version,
                session=session,
            )

        return await run_mongo_write_unit(_delete, "soft_delete_character_relation")

    @staticmethod
    async def list_deleted_relations(novel_id: str) -> list[dict[str, Any]]:
        """读取小说角色关系回收站。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。

        Returns:
            已软删除角色关系列表。
        """
        await novel_repo.get_novel_by_id(novel_id)
        return await character_relation_repo.list_deleted_relations(novel_id)

    @staticmethod
    async def restore_relation(
        novel_id: str,
        relation_id: str,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        """从回收站恢复角色关系并重新校验端点与语义唯一性。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            relation_id: 稳定角色关系业务 ID。
            expected_version: 调用方持有的乐观锁版本。

        Returns:
            恢复后的完整关系文档。
        """

        async def _restore(session: AsyncClientSession | None) -> dict[str, Any]:
            """在一个写入单元内复核并恢复关系。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            current = await character_relation_repo.get_relation(
                novel_id,
                relation_id,
                include_deleted=True,
                session=session,
            )
            if not current.get("is_deleted"):
                raise NotFoundError(f"角色关系 '{relation_id}' 不在回收站")
            CharacterRelationService._ensure_expected_version(current, expected_version)
            endpoint_ids = [
                str(current["source_character_id"]),
                str(current["target_character_id"]),
            ]
            endpoint_signatures = await _load_character_endpoint_signatures(
                novel_id,
                endpoint_ids,
                require_usable=False,
                session=session,
            )
            blockers = await CharacterRelationService._current_blockers(
                novel_id,
                current,
                session=session,
            )
            should_be_active = bool(current.get("user_is_active", True))
            effective_active = should_be_active and not blockers
            if effective_active:
                await CharacterRelationService._validate_persisted_relation(
                    novel_id,
                    current,
                    relation_type=str(current["relation_type"]),
                    should_be_active=True,
                    require_active_endpoints=True,
                    session=session,
                )
            guard_touches = await _touch_character_endpoint_signatures(
                endpoint_signatures,
                session=session,
            )
            restored: dict[str, Any] | None = None
            try:
                restored = await character_relation_repo.restore_relation(
                    novel_id,
                    relation_id,
                    expected_version,
                    effective_active=effective_active,
                    disabled_by_character_ids=blockers,
                    session=session,
                )
                current_signatures = await _load_character_endpoint_signatures(
                    novel_id,
                    endpoint_ids,
                    require_usable=False,
                    session=session,
                )
                if current_signatures != endpoint_signatures:
                    raise DuplicateKeyError(
                        "角色关系恢复期间端点已被并发修改，请重试"
                    )
                return restored
            except Exception:
                if session is None and restored is not None:
                    try:
                        await _compensate_character_relation_update(current, restored)
                    except Exception as compensation_exc:
                        raise RuntimeError(
                            "角色关系恢复失败，且 standalone 补偿未完成"
                        ) from compensation_exc
                if session is None and guard_touches:
                    await rollback_dependency_endpoint_guards(
                        character_repo.collection,
                        guard_touches,
                    )
                raise

        return await run_mongo_write_unit(_restore, "restore_character_relation")

    @staticmethod
    async def hard_delete_relation(
        novel_id: str,
        relation_id: str,
        *,
        expected_version: int,
    ) -> bool:
        """物理删除回收站中的角色关系。

        Args:
            novel_id: 小说 MongoDB ObjectId 字符串。
            relation_id: 稳定角色关系业务 ID。
            expected_version: 调用方持有的乐观锁版本。

        Returns:
            删除成功时返回 True。
        """

        async def _hard_delete(session: AsyncClientSession | None) -> bool:
            """在一个写入单元内执行硬删除 CAS。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            return await character_relation_repo.hard_delete_relation(
                novel_id,
                relation_id,
                expected_version,
                session=session,
            )

        return await run_mongo_write_unit(_hard_delete, "hard_delete_character_relation")
