import logging
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Tuple

from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import BulkWriteError, DuplicateKeyError as PyMongoDuplicateKeyError

from backend.db.base import BaseRepository
from backend.db.errors import DuplicateKeyError, NotFoundError
from backend.db.repositories.character_faction_binding_repository import (
    character_faction_binding_repo,
)
from backend.db.repositories.id_sequence_repository import id_sequence_repo
from backend.db.repositories.faction_relation_repository import faction_relation_repo
from backend.db.repositories.faction_repository import faction_repo
from backend.db.repositories.novel_repository import novel_repo
from backend.db.transaction import run_mongo_write_unit
from backend.db.utils import get_utc_now, to_object_id
from backend.services.novel.character_service import (
    _CompensationRecord,
    _DependencyReconcileCasMiss,
    _delete_snapshots_exact,
    _deleted_compensation_record,
    _normalized_blockers,
    _restore_snapshots,
    _run_bounded_dependency_reconcile,
    _snapshot_version_query,
    _updated_compensation_record,
)
from backend.services.novel.faction_relation_service import FactionRelationService

logger = logging.getLogger(__name__)

FACTION_EDITABLE_FIELDS = frozenset(
    {
        "name",
        "alias",
        "faction_type",
        "level_type",
        "parent_faction_id",
        "positioning",
        "public_stance",
        "core_goal",
        "hidden_goal",
        "resources_and_advantages",
        "organization_style",
        "core_values",
        "conflict_with_mainline",
        "is_public",
        "influence_scope",
        "active_status",
        "expandability",
        "tags",
        "first_appearance_volume_id",
        "first_appearance_chapter_id",
        "sort_order",
        "extra",
    }
)

_faction_relation_base_repo = BaseRepository("faction_relations")
_faction_binding_base_repo = BaseRepository("character_faction_bindings")


def _snapshot_deleted_query(snapshot: Mapping[str, Any]) -> object:
    """构造预镜像删除态的精确 CAS 条件。

    Args:
        snapshot: 写入前的势力、关系、绑定或子势力文档。

    Returns:
        精确布尔删除态；旧文档缺字段时返回 `$exists=false` 条件。
    """
    if "is_deleted" not in snapshot:
        return {"$exists": False}
    return bool(snapshot["is_deleted"])


def _dependency_user_intent(snapshot: Mapping[str, Any]) -> bool:
    """读取依赖对象用户启停意图并兼容迁移前自动阻断记录。

    Args:
        snapshot: 人物—势力绑定或阵营关系预镜像。

    Returns:
        独立于实体 blocker 的用户启停意图。
    """
    raw_intent = snapshot.get("user_is_active")
    if isinstance(raw_intent, bool):
        return raw_intent
    if _normalized_blockers(snapshot, "disabled_by_faction_ids"):
        return True
    return bool(snapshot.get("is_active", True))


async def _load_faction_relations(
    novel_id: str,
    faction_id: str,
    *,
    include_deleted: bool,
    session: AsyncClientSession | None,
) -> list[dict[str, Any]]:
    """读取势力参与的全部阵营关系预镜像。

    Args:
        novel_id: 小说 ObjectId 字符串。
        faction_id: 稳定势力业务 ID。
        include_deleted: 是否包含关系回收站记录。
        session: 可选 MongoDB 会话。

    Returns:
        当前势力作为任一端点的阵营关系预镜像。
    """
    return await _faction_relation_base_repo.find_many(
        {
            "novel_id": to_object_id(novel_id),
            "$or": [
                {"source_faction_id": faction_id},
                {"target_faction_id": faction_id},
            ],
        },
        include_deleted=include_deleted,
        session=session,
    )


async def _load_faction_bindings(
    novel_id: str,
    faction_id: str,
    *,
    include_deleted: bool,
    session: AsyncClientSession | None,
) -> list[dict[str, Any]]:
    """读取引用势力的角色—势力绑定预镜像。

    Args:
        novel_id: 小说 ObjectId 字符串。
        faction_id: 稳定势力业务 ID。
        include_deleted: 是否包含绑定回收站记录。
        session: 可选 MongoDB 会话。

    Returns:
        当前势力下的角色—势力绑定预镜像。
    """
    return await _faction_binding_base_repo.find_many(
        {"novel_id": to_object_id(novel_id), "faction_id": faction_id},
        include_deleted=include_deleted,
        session=session,
    )


async def _load_child_factions(
    novel_id: str,
    faction_id: str,
    *,
    session: AsyncClientSession | None,
) -> list[dict[str, Any]]:
    """读取软删或硬删时需要解除挂靠的活动子势力预镜像。

    Args:
        novel_id: 小说 ObjectId 字符串。
        faction_id: 父势力业务 ID。
        session: 可选 MongoDB 会话。

    Returns:
        当前仍挂靠在该势力下的未删除子势力。
    """
    return await faction_repo.find_many(
        {
            "novel_id": to_object_id(novel_id),
            "parent_faction_id": faction_id,
        },
        session=session,
    )


def _validate_expected_faction_version(expected_version: Any) -> int:
    """校验并返回客户端提交的势力乐观锁版本。

    Args:
        expected_version: 客户端基于的势力版本。

    Returns:
        大于等于 1 的严格整数版本。
    """
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise ValueError("expected_version 必须为正整数")
    if expected_version < 1:
        raise ValueError("expected_version 必须大于等于 1")
    return expected_version


async def _get_versioned_faction(
    novel_id: str,
    faction_id: str,
    expected_version: int,
    *,
    deleted: bool,
    session: AsyncClientSession | None,
) -> dict[str, Any]:
    """读取指定生命周期状态的势力并校验客户端版本。

    Args:
        novel_id: 小说 ObjectId 字符串。
        faction_id: 稳定势力业务 ID。
        expected_version: 客户端基于的势力版本。
        deleted: True 时只读取回收站，否则读取未删除势力。
        session: 可选 MongoDB 会话。

    Returns:
        与客户端版本一致的势力预镜像。
    """
    normalized_version = _validate_expected_faction_version(expected_version)
    if deleted:
        faction = await faction_repo.get_deleted_faction(
            novel_id,
            faction_id,
            session=session,
        )
    else:
        faction = await faction_repo.get_faction(
            novel_id,
            faction_id,
            session=session,
        )
    current_version = int(faction.get("version") or 1)
    if current_version != normalized_version:
        raise DuplicateKeyError(
            "势力版本冲突："
            f"当前版本为 {current_version}，请求版本为 {normalized_version}"
        )
    return faction


async def _cas_update_faction_snapshot(
    snapshot: Mapping[str, Any],
    update_fields: Mapping[str, Any],
    *,
    session: AsyncClientSession | None,
) -> dict[str, Any]:
    """按预镜像版本与删除态更新势力并返回本次精确写后文档。

    Args:
        snapshot: 势力写入前预镜像。
        update_fields: 已经过势力编辑白名单过滤的字段。
        session: 可选 MongoDB 会话。

    Returns:
        递增内部版本后的势力文档。
    """
    current_version = int(snapshot.get("version") or 1)
    try:
        updated = await faction_repo.collection.find_one_and_update(
            {
                "_id": snapshot["_id"],
                **_snapshot_version_query(snapshot),
                "is_deleted": _snapshot_deleted_query(snapshot),
            },
            {
                "$set": {
                    **dict(update_fields),
                    "version": current_version + 1,
                    "updated_at": get_utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
    except (PyMongoDuplicateKeyError, BulkWriteError) as exc:
        raise DuplicateKeyError("势力更新与现有业务约束冲突") from exc
    if updated is None:
        raise DuplicateKeyError("势力已被其他请求修改，请刷新后重试")
    return updated


async def _apply_faction_relation_changes(
    snapshots: Sequence[dict[str, Any]],
    faction_id: str,
    *,
    projected_name: str | None,
    available: bool | None,
    applied_snapshots: list[_CompensationRecord],
    session: AsyncClientSession | None,
) -> int:
    """逐条 CAS 更新阵营关系名称投影与势力 blocker。

    Args:
        snapshots: 本次操作前读取的阵营关系预镜像。
        faction_id: 名称或可用性发生变化的势力 ID。
        projected_name: 新显示名；None 表示不更新名称投影。
        available: 新可用性；None 表示不改 blocker。
        applied_snapshots: 记录已经成功写入的补偿记录。
        session: 可选 MongoDB 会话。

    Returns:
        实际修改的阵营关系数量。
    """
    updated_count = 0
    for snapshot in snapshots:
        updates: dict[str, Any] = {}
        if projected_name is not None:
            if (
                snapshot.get("source_faction_id") == faction_id
                and snapshot.get("source_faction_name") != projected_name
            ):
                updates["source_faction_name"] = projected_name
            if (
                snapshot.get("target_faction_id") == faction_id
                and snapshot.get("target_faction_name") != projected_name
            ):
                updates["target_faction_name"] = projected_name

        if available is not None and snapshot.get("is_deleted") is not True:
            blockers = _normalized_blockers(snapshot, "disabled_by_faction_ids")
            next_blockers = [item for item in blockers if item != faction_id]
            if not available and faction_id not in next_blockers:
                next_blockers.append(faction_id)
            user_is_active = _dependency_user_intent(snapshot)
            next_is_active = bool(user_is_active and not next_blockers)
            updates.update(
                {
                    "user_is_active": user_is_active,
                    "disabled_by_faction_ids": next_blockers,
                    "is_active": next_is_active,
                }
            )

        changed = {
            field: value for field, value in updates.items() if snapshot.get(field) != value
        }
        if not changed:
            continue
        current_version = int(snapshot.get("version") or 1)
        changed.update({"version": current_version + 1, "updated_at": get_utc_now()})
        try:
            result = await faction_relation_repo.collection.update_one(
                {
                    "_id": snapshot["_id"],
                    **_snapshot_version_query(snapshot),
                    "is_deleted": _snapshot_deleted_query(snapshot),
                },
                {"$set": changed},
                session=session,
            )
        except (PyMongoDuplicateKeyError, BulkWriteError) as exc:
            raise DuplicateKeyError("势力恢复后阵营关系发生活动语义冲突") from exc
        if result.matched_count != 1:
            raise _DependencyReconcileCasMiss(
                "阵营关系已被其他请求修改，请重新读取后重试"
            )
        applied_snapshots.append(
            _updated_compensation_record(
                snapshot,
                {
                    "version": current_version + 1,
                    **(
                        {"is_deleted": bool(snapshot["is_deleted"])}
                        if "is_deleted" in snapshot
                        else {}
                    ),
                },
            )
        )
        updated_count += 1
    return updated_count


async def _apply_faction_binding_availability(
    snapshots: Sequence[dict[str, Any]],
    faction_id: str,
    *,
    available: bool,
    applied_snapshots: list[_CompensationRecord],
    session: AsyncClientSession | None,
) -> int:
    """逐条 CAS 更新角色—势力绑定的势力 blocker 与有效态。

    Args:
        snapshots: 本次操作前读取的绑定预镜像。
        faction_id: 可用性发生变化的势力 ID。
        available: 势力操作后的可用性。
        applied_snapshots: 记录已经成功写入的补偿记录。
        session: 可选 MongoDB 会话。

    Returns:
        实际修改的绑定数量。
    """
    updated_count = 0
    for snapshot in snapshots:
        if snapshot.get("is_deleted") is True:
            continue
        blockers = _normalized_blockers(snapshot, "disabled_by_faction_ids")
        next_blockers = [item for item in blockers if item != faction_id]
        if not available and faction_id not in next_blockers:
            next_blockers.append(faction_id)
        user_is_active = _dependency_user_intent(snapshot)
        next_is_active = bool(
            user_is_active
            and not next_blockers
            and not _normalized_blockers(snapshot, "disabled_by_character_ids")
        )
        updates = {
            "user_is_active": user_is_active,
            "disabled_by_faction_ids": next_blockers,
            "is_active": next_is_active,
        }
        if all(snapshot.get(field) == value for field, value in updates.items()):
            continue
        current_version = int(snapshot.get("version") or 1)
        updates.update({"version": current_version + 1, "updated_at": get_utc_now()})
        try:
            result = await character_faction_binding_repo.collection.update_one(
                {
                    "_id": snapshot["_id"],
                    **_snapshot_version_query(snapshot),
                    "is_deleted": _snapshot_deleted_query(snapshot),
                },
                {"$set": updates},
                session=session,
            )
        except (PyMongoDuplicateKeyError, BulkWriteError) as exc:
            raise DuplicateKeyError("势力恢复后角色—势力绑定发生活动唯一冲突") from exc
        if result.matched_count != 1:
            raise _DependencyReconcileCasMiss(
                "角色—势力绑定已被其他请求修改，请重新读取后重试"
            )
        applied_snapshots.append(
            _updated_compensation_record(
                snapshot,
                {
                    "version": current_version + 1,
                    **(
                        {"is_deleted": bool(snapshot["is_deleted"])}
                        if "is_deleted" in snapshot
                        else {}
                    ),
                },
            )
        )
        updated_count += 1
    return updated_count


async def _apply_child_unlink(
    snapshots: Sequence[dict[str, Any]],
    faction_id: str,
    *,
    applied_snapshots: list[_CompensationRecord],
    session: AsyncClientSession | None,
) -> int:
    """逐条 CAS 解除子势力挂靠并记录可安全补偿的预镜像。

    Args:
        snapshots: 当前父势力的未删除子势力预镜像。
        faction_id: 即将删除的父势力业务 ID。
        applied_snapshots: 记录已经成功写入的补偿记录。
        session: 可选 MongoDB 会话。

    Returns:
        实际解除挂靠的子势力数量。
    """
    updated_count = 0
    for snapshot in snapshots:
        if snapshot.get("parent_faction_id") != faction_id:
            continue
        current_version = int(snapshot.get("version") or 1)
        result = await faction_repo.collection.update_one(
            {
                "_id": snapshot["_id"],
                **_snapshot_version_query(snapshot),
                "is_deleted": _snapshot_deleted_query(snapshot),
                "parent_faction_id": faction_id,
            },
            {
                "$set": {
                    "parent_faction_id": None,
                    "version": current_version + 1,
                    "updated_at": get_utc_now(),
                }
            },
            session=session,
        )
        if result.matched_count != 1:
            raise _DependencyReconcileCasMiss(
                "子势力已被其他请求修改，请重新读取后重试"
            )
        applied_snapshots.append(
            _updated_compensation_record(
                snapshot,
                {
                    "version": current_version + 1,
                    **(
                        {"is_deleted": bool(snapshot["is_deleted"])}
                        if "is_deleted" in snapshot
                        else {}
                    ),
                },
            )
        )
        updated_count += 1
    return updated_count


async def _validate_faction_binding_unblock(
    novel_id: str,
    faction_id: str,
    *,
    session: AsyncClientSession | None,
) -> int:
    """预检势力恢复可用后将重新启用的角色—势力绑定唯一性。

    Args:
        novel_id: 小说 ObjectId 字符串。
        faction_id: 即将恢复可用的势力 ID。
        session: 可选 MongoDB 会话。

    Returns:
        预检通过后将重新启用的绑定数量。
    """
    bindings = await _load_faction_bindings(
        novel_id,
        faction_id,
        include_deleted=False,
        session=session,
    )
    semantic_candidates: dict[tuple[str, str, str], str] = {}
    primary_candidates: dict[str, str] = {}
    for binding in bindings:
        blockers = _normalized_blockers(binding, "disabled_by_faction_ids")
        if faction_id not in blockers or _dependency_user_intent(binding) is not True:
            continue
        remaining = [item for item in blockers if item != faction_id]
        if remaining or _normalized_blockers(binding, "disabled_by_character_ids"):
            continue
        semantic = (
            str(binding["character_id"]),
            str(binding["faction_id"]),
            str(binding["membership_type"]),
        )
        if semantic in semantic_candidates:
            raise DuplicateKeyError("势力恢复后角色—势力绑定语义重复")
        semantic_candidates[semantic] = str(binding["binding_id"])
        if binding.get("membership_type") == "primary":
            character_id = str(binding["character_id"])
            if character_id in primary_candidates:
                raise DuplicateKeyError("势力恢复后同一角色出现多个主归属")
            primary_candidates[character_id] = str(binding["binding_id"])

    semantic_conflicts = await character_faction_binding_repo.find_active_semantic_bindings(
        novel_id,
        list(semantic_candidates),
        session=session,
    )
    if semantic_conflicts:
        raise DuplicateKeyError("势力恢复会与现有活动角色—势力绑定语义冲突")
    primary_conflicts = await character_faction_binding_repo.find_active_primary_bindings(
        novel_id,
        list(primary_candidates),
        session=session,
    )
    if primary_conflicts:
        raise DuplicateKeyError("势力恢复会导致同一角色存在多个活动主归属")
    return len(semantic_candidates)


async def _reconcile_faction_dependents(faction: Mapping[str, Any]) -> None:
    """按当前势力主档重扫并调和阵营关系、角色绑定与晚到子势力。

    Args:
        faction: 已确认保留或恢复的当前势力主档。

    Returns:
        无。
    """
    novel_id = str(faction["novel_id"])
    faction_id = str(faction["faction_id"])
    relation_available = bool(
        faction.get("is_deleted") is not True
        and faction.get("active_status") == "active"
    )
    binding_available = bool(
        relation_available and faction.get("level_type", "core") == "core"
    )
    projected_name = str(faction.get("name") or "").strip()

    async def _reconcile_once() -> None:
        """重新读取当前全部势力依赖并执行一轮版本化调和。"""
        latest_relations = await _load_faction_relations(
            novel_id,
            faction_id,
            include_deleted=True,
            session=None,
        )
        latest_bindings = await _load_faction_bindings(
            novel_id,
            faction_id,
            include_deleted=False,
            session=None,
        )
        # 关系名称投影与 blocker 共用同一轮最新版本 CAS。
        await _apply_faction_relation_changes(
            latest_relations,
            faction_id,
            projected_name=projected_name or None,
            available=relation_available,
            applied_snapshots=[],
            session=None,
        )
        await _apply_faction_binding_availability(
            latest_bindings,
            faction_id,
            available=binding_available,
            applied_snapshots=[],
            session=None,
        )
        if not relation_available:
            latest_children = await _load_child_factions(
                novel_id,
                faction_id,
                session=None,
            )
            await _apply_child_unlink(
                latest_children,
                faction_id,
                applied_snapshots=[],
                session=None,
            )

    await _run_bounded_dependency_reconcile(
        _reconcile_once,
        label="势力依赖调和",
    )


async def _compensate_faction_write(
    faction_record: _CompensationRecord | None,
    relation_records: Sequence[_CompensationRecord],
    binding_records: Sequence[_CompensationRecord],
    child_records: Sequence[_CompensationRecord],
) -> None:
    """恢复势力生命周期 standalone 顺序写已经应用的全部预镜像。

    Args:
        faction_record: 势力主档更新或删除补偿记录。
        relation_records: 已修改或删除的阵营关系补偿记录。
        binding_records: 已修改或删除的角色—势力绑定补偿记录。
        child_records: 已解除挂靠的子势力补偿记录。

    Returns:
        无。
    """
    if faction_record is None:
        await _restore_snapshots(faction_repo, child_records)
        await _restore_snapshots(_faction_relation_base_repo, relation_records)
        await _restore_snapshots(_faction_binding_base_repo, binding_records)
        return

    try:
        await _restore_snapshots(faction_repo, [faction_record])
    except Exception as master_exc:
        # 公开版本已推进时保留较新势力，仅按当前真相调和依赖并明确报告补偿未完成。
        current = await faction_repo.collection.find_one(
            {
                "_id": faction_record.snapshot["_id"],
                "novel_id": faction_record.snapshot["novel_id"],
                "faction_id": faction_record.snapshot["faction_id"],
            }
        )
        if current is not None:
            await _reconcile_faction_dependents(current)
            raise RuntimeError(
                "standalone 势力主档补偿未完成：公开版本或删除态已推进；"
                "已保留较新主档并按当前状态调和依赖"
            ) from master_exc
        raise RuntimeError(
            "standalone 势力主档补偿未完成：原业务身份已不存在或被重建"
        ) from master_exc

    dependency_error: Exception | None = None
    try:
        await _restore_snapshots(faction_repo, child_records)
        await _restore_snapshots(_faction_relation_base_repo, relation_records)
        await _restore_snapshots(_faction_binding_base_repo, binding_records)
    except Exception as exc:
        dependency_error = exc

    await _reconcile_faction_dependents(faction_record.snapshot)
    if dependency_error is not None:
        raise RuntimeError(
            "standalone 势力依赖预镜像补偿未完整命中；已按恢复后的主档调和最新依赖"
        ) from dependency_error


class FactionService:
    """
    阵营（Faction）服务层，编排跨集合的业务逻辑。
    纯集合内操作由 FactionRepository 负责，跨集合联动由此处编排。
    """

    @staticmethod
    def _normalize_generated_core_faction(data: Dict[str, Any], *, faction_id: str, sort_order: int) -> Dict[str, Any]:
        """将 AI 生成的核心阵营转换为 factions 集合可写入文档。

        Args:
            data: 单个核心阵营生成结果。
            faction_id: 后端分配的业务阵营 ID。
            sort_order: 阵营排序权重。

        Returns:
            可写入 factions 集合的阵营文档片段。
        """
        payload = dict(data)
        payload["name"] = str(payload.get("name", "")).strip()
        payload["faction_id"] = faction_id
        payload["level_type"] = "core"
        payload["parent_faction_id"] = None
        payload["active_status"] = "active"
        payload["sort_order"] = sort_order
        payload.setdefault("alias", [])
        payload.setdefault("first_appearance_volume_id", None)
        payload.setdefault("first_appearance_chapter_id", None)
        payload.setdefault("extra", {})
        return payload

    @staticmethod
    def _normalize_generated_relation(
        data: Dict[str, Any],
        *,
        name_to_faction_id: Dict[str, str],
        new_faction_names: set[str],
    ) -> Dict[str, Any]:
        """将 AI 生成的阵营名称关系映射为 faction_id 关系。

        Args:
            data: 单条 AI 生成关系，使用阵营名称引用端点。
            name_to_faction_id: 新阵营与已有阵营名称到业务阵营 ID 的映射。
            new_faction_names: 本次新生成阵营名称集合。

        Returns:
            可写入 faction_relations 集合的关系文档片段。
        """
        source_name = str(data.get("source_faction_name", "")).strip()
        target_name = str(data.get("target_faction_name", "")).strip()
        if source_name not in name_to_faction_id:
            raise ValueError(f"关系发起方阵营不存在: {source_name}")
        if target_name not in name_to_faction_id:
            raise ValueError(f"关系目标方阵营不存在: {target_name}")
        if source_name not in new_faction_names and target_name not in new_faction_names:
            raise ValueError("新增关系至少需要连接一个本次生成的核心阵营")

        # AI 阶段用名称便于阅读，落库阶段必须统一转换为稳定业务 ID。
        payload = {
            "source_faction_id": name_to_faction_id[source_name],
            "target_faction_id": name_to_faction_id[target_name],
            "relation_type": data.get("relation_type"),
            "current_state": data.get("current_state", ""),
            "core_conflict": data.get("core_conflict", ""),
            "hidden_tension": data.get("hidden_tension", ""),
            "possible_change": data.get("possible_change", ""),
            "intensity": data.get("intensity", 3),
            "is_active": data.get("is_active", True),
        }
        return payload

    @staticmethod
    async def _ensure_unique_active_name(
        novel_id: str,
        level_type: str,
        name: str,
        *,
        exclude_faction_id: str | None = None,
        session=None,
    ) -> None:
        """校验同一小说同一层级下未删除阵营名称不重复。

        Args:
            novel_id: 小说 ObjectId 字符串。
            level_type: 阵营层级类型。
            name: 待校验阵营名称。
            exclude_faction_id: 更新时需要排除的当前阵营 ID。
            session: 可选 MongoDB 会话，用于事务读取。

        Returns:
            无。

        Raises:
            DuplicateKeyError: 已存在同名未删除阵营时抛出。
        """
        query: Dict[str, Any] = {
            "novel_id": to_object_id(novel_id),
            "level_type": level_type,
            "name": name,
        }
        if exclude_faction_id:
            query["faction_id"] = {"$ne": exclude_faction_id}
        existing = await faction_repo.find_one(query, session=session)
        if existing:
            raise DuplicateKeyError(f"同一小说同一势力层级下已存在同名阵营: {name}")

    @staticmethod
    async def has_core_factions_initialized(novel_id: str, *, session=None) -> bool:
        """判断小说是否已经存在核心阵营初始化记录。

        Args:
            novel_id: 小说 ObjectId 字符串。
            session: 可选 MongoDB 会话，用于事务读取。

        Returns:
            存在未删除或已软删除核心阵营时返回 True。
        """
        count = await faction_repo.count_factions_by_level_type(
            novel_id,
            "core",
            include_deleted=True,
            session=session,
        )
        return count > 0

    @staticmethod
    async def create_faction(novel_id: str, data: Dict[str, Any]) -> Tuple[str, str]:
        """创建新阵营。

        Args:
            novel_id: 小说 ObjectId 字符串。
            data: 阵营基础字段，不包含 novel_id。

        Returns:
            (MongoDB ObjectId 字符串, 业务 faction_id)。
        """
        async def _create(session):
            """在同一个写入单元内校验小说、生成业务 ID 并插入阵营。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            payload = dict(data)
            payload["name"] = str(payload.get("name", "")).strip()
            payload["level_type"] = str(payload.get("level_type") or "core").strip() or "core"
            await FactionService._ensure_unique_active_name(
                novel_id,
                payload["level_type"],
                payload["name"],
                session=session,
            )
            payload["novel_id"] = novel_id
            # 正式业务 ID 只能由可靠序列分配，禁止客户端显式 ID 绕过终身不复用约束。
            payload["faction_id"] = await faction_repo._get_next_faction_id(
                novel_id,
                session=session,
            )
            if "sort_order" not in payload:
                # 手动创建不限制核心阵营数量，但仍按当前层级尾部追加，保证列表稳定可读。
                sibling_count = await faction_repo.count_factions_by_level_type(
                    novel_id,
                    payload["level_type"],
                    session=session,
                )
                payload["sort_order"] = (sibling_count + 1) * 10
            faction_oid = await faction_repo.create_faction(payload, session=session)
            return faction_oid, payload["faction_id"]

        try:
            return await run_mongo_write_unit(_create, "create_faction")
        except DuplicateKeyError:
            # 迁移前遗留序列若短暂落后，重新原子预留一个编号并仅重试一次。
            return await run_mongo_write_unit(_create, "create_faction_retry")

    @staticmethod
    async def bulk_create_core_factions_with_relations(
        novel_id: str,
        data: Dict[str, Any],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """批量创建全书核心阵营及其阵营关系。

        Args:
            novel_id: 小说 ObjectId 字符串。
            data: 包含 core_factions 与 faction_relations 的生成结果。

        Returns:
            已创建的阵营和阵营关系文档列表。
        """
        core_factions = list(data.get("core_factions") or [])
        faction_relations = list(data.get("faction_relations") or [])
        if len(core_factions) < 1 or len(core_factions) > 6:
            raise ValueError("单次 AI 核心阵营数量必须为 1 到 6 个")

        incoming_names = [str(item.get("name", "")).strip() for item in core_factions]
        if len(incoming_names) != len(set(incoming_names)):
            raise ValueError("核心阵营名称不能重复")

        async def _create(session):
            """在同一个写入单元内保存核心阵营，并把关系名称映射为业务 ID。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            existing_core_factions = await faction_repo.get_factions_by_level_type(
                novel_id,
                "core",
                session=session,
            )

            existing_names = {str(item.get("name", "")).strip() for item in existing_core_factions}
            duplicated_names = sorted(name for name in incoming_names if name in existing_names)
            if duplicated_names:
                raise ValueError(f"同一小说下已存在同名核心阵营: {', '.join(duplicated_names)}")

            # 一次原子预留完整 ID 区间，避免并发确认或后续请求复用批次中的尾部编号。
            faction_ids = await id_sequence_repo.allocate_many(
                novel_id,
                "faction",
                "fac",
                len(core_factions),
                session=session,
            )
            name_to_faction_id = {
                str(faction.get("name", "")).strip(): faction_id
                for faction, faction_id in zip(core_factions, faction_ids)
            }
            # 追加生成关系时允许引用已有核心阵营，但不能重复保存已有阵营本体。
            existing_name_to_faction_id = {
                str(faction.get("name", "")).strip(): str(faction.get("faction_id", "")).strip()
                for faction in existing_core_factions
                if str(faction.get("name", "")).strip() and str(faction.get("faction_id", "")).strip()
            }
            relation_name_to_faction_id = {**existing_name_to_faction_id, **name_to_faction_id}
            new_faction_names = set(name_to_faction_id.keys())

            created_factions: list[Dict[str, Any]] = []
            created_faction_documents: list[dict[str, Any]] = []
            try:
                for index, (faction, faction_id) in enumerate(zip(core_factions, faction_ids), start=1):
                    payload = FactionService._normalize_generated_core_faction(
                        faction,
                        faction_id=faction_id,
                        sort_order=(len(existing_core_factions) + index) * 10,
                    )
                    payload["novel_id"] = novel_id
                    faction_oid = await faction_repo.create_faction(payload, session=session)
                    created_faction_documents.append(
                        {
                            "_id": to_object_id(faction_oid),
                            "novel_id": to_object_id(novel_id),
                            "faction_id": faction_id,
                            "version": 1,
                        }
                    )
                    created_factions.append(
                        await faction_repo.get_faction(novel_id, faction_id, session=session)
                    )

                relation_payloads = [
                    FactionService._normalize_generated_relation(
                        relation,
                        name_to_faction_id=relation_name_to_faction_id,
                        new_faction_names=new_faction_names,
                    )
                    for relation in faction_relations
                ]
                # 人工创建与 AI 确认共用同一端点、语义唯一和字段校验入口。
                created_relations = await FactionRelationService.bulk_create_relations(
                    novel_id,
                    relation_payloads,
                    session=session,
                )
            except Exception:
                if session is None:
                    await faction_repo.delete_factions_created_exact(
                        list(reversed(created_faction_documents)),
                    )
                raise

            return {
                "factions": created_factions,
                "faction_relations": created_relations,
            }

        return await run_mongo_write_unit(_create, "bulk_create_core_factions_with_relations")

    @staticmethod
    async def get_factions_by_novel(novel_id: str) -> List[Dict[str, Any]]:
        """获取指定小说下所有阵营列表。

        Args:
            novel_id: 小说 ObjectId 字符串。

        Returns:
            阵营文档列表。
        """
        await novel_repo.get_novel_by_id(novel_id)
        return await faction_repo.get_factions_by_novel(novel_id)

    @staticmethod
    async def get_faction(novel_id: str, faction_id: str) -> Dict[str, Any]:
        """获取指定小说下的单个阵营详情。

        Args:
            novel_id: 小说 ObjectId 字符串。
            faction_id: 业务层阵营 ID。

        Returns:
            阵营文档。
        """
        await novel_repo.get_novel_by_id(novel_id)
        return await faction_repo.get_faction(novel_id, faction_id)

    @staticmethod
    async def get_factions_by_level_type(novel_id: str, level_type: str) -> List[Dict[str, Any]]:
        """获取指定小说下特定层级类型的阵营列表。

        Args:
            novel_id: 小说 ObjectId 字符串。
            level_type: 阵营层级类型。

        Returns:
            阵营文档列表。
        """
        await novel_repo.get_novel_by_id(novel_id)
        return await faction_repo.get_factions_by_level_type(novel_id, level_type)

    @staticmethod
    async def get_deleted_factions_by_level_type(
        novel_id: str,
        level_type: str | None = None,
    ) -> List[Dict[str, Any]]:
        """获取指定小说下已软删除阵营列表，可按层级过滤。

        Args:
            novel_id: 小说 ObjectId 字符串。
            level_type: 可选阵营层级类型。

        Returns:
            已软删除阵营文档列表。
        """
        await novel_repo.get_novel_by_id(novel_id)
        return await faction_repo.get_deleted_factions_by_level_type(novel_id, level_type)

    @staticmethod
    async def get_child_factions(novel_id: str, parent_faction_id: str) -> List[Dict[str, Any]]:
        """获取指定父级阵营的所有直接子阵营。

        Args:
            novel_id: 小说 ObjectId 字符串。
            parent_faction_id: 父级业务阵营 ID。

        Returns:
            子阵营文档列表。
        """
        await novel_repo.get_novel_by_id(novel_id)
        return await faction_repo.get_child_factions(novel_id, parent_faction_id)

    @staticmethod
    async def update_faction_info(novel_id: str, faction_id: str, update_data: Dict[str, Any]) -> bool:
        """更新阵营基础信息。

        Args:
            novel_id: 小说 ObjectId 字符串。
            faction_id: 业务层阵营 ID。
            update_data: 含 expected_version 的待更新字段。

        Returns:
            实际修改成功时返回 True。
        """
        source = dict(update_data)
        expected_version = _validate_expected_faction_version(
            source.pop("expected_version", None)
        )
        filtered_update = {
            key: value for key, value in source.items() if key in FACTION_EDITABLE_FIELDS
        }
        if not filtered_update:
            return False
        if "name" in filtered_update:
            normalized_name = str(filtered_update["name"] or "").strip()
            if not normalized_name:
                raise ValueError("势力名称不能为空")
            filtered_update["name"] = normalized_name

        async def _update(session: AsyncClientSession | None) -> bool:
            """在同一个写入单元内更新阵营及其关系、绑定派生状态。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            current = await _get_versioned_faction(
                novel_id,
                faction_id,
                expected_version,
                deleted=False,
                session=session,
            )
            next_data = dict(filtered_update)
            next_name = str(next_data.get("name", current.get("name", ""))).strip()
            next_level_type = str(next_data.get("level_type", current.get("level_type") or "core")).strip() or "core"
            if "name" in next_data:
                next_data["name"] = next_name
            if "level_type" in next_data:
                next_data["level_type"] = next_level_type
            current_active_status = str(current.get("active_status") or "active")
            current_level_type = str(current.get("level_type") or "core")
            next_active_status = str(
                next_data.get("active_status", current_active_status)
            )
            name_changed = "name" in next_data and next_name != current.get("name")
            current_relation_available = current_active_status == "active"
            next_relation_available = next_active_status == "active"
            current_binding_available = (
                current_relation_available and current_level_type == "core"
            )
            next_binding_available = (
                next_relation_available and next_level_type == "core"
            )
            relation_availability_changed = (
                current_relation_available != next_relation_available
            )
            binding_availability_changed = (
                current_binding_available != next_binding_available
            )
            if next_name:
                await FactionService._ensure_unique_active_name(
                    novel_id,
                    next_level_type,
                    next_name,
                    exclude_faction_id=faction_id,
                    session=session,
                )

            if not current_relation_available and next_relation_available:
                # 在实体状态写入前预检所有将解除 blocker 的关系，避免恢复时才触发唯一索引冲突。
                await FactionRelationService.validate_faction_unblock(
                    novel_id,
                    faction_id,
                    session=session,
                )
            if not current_binding_available and next_binding_available:
                await _validate_faction_binding_unblock(
                    novel_id,
                    faction_id,
                    session=session,
                )

            relation_snapshots = (
                await _load_faction_relations(
                    novel_id,
                    faction_id,
                    # 改名需要同步回收站关系；纯状态切换只处理未删除关系。
                    include_deleted=name_changed,
                    session=session,
                )
                if name_changed or relation_availability_changed
                else []
            )
            binding_snapshots = (
                await _load_faction_bindings(
                    novel_id,
                    faction_id,
                    include_deleted=False,
                    session=session,
                )
                if binding_availability_changed
                else []
            )
            faction_record: _CompensationRecord | None = None
            relation_records: list[_CompensationRecord] = []
            binding_records: list[_CompensationRecord] = []
            try:
                updated = await _cas_update_faction_snapshot(
                    current,
                    next_data,
                    session=session,
                )
                faction_record = _updated_compensation_record(current, updated)
                if relation_snapshots:
                    await _apply_faction_relation_changes(
                        relation_snapshots,
                        faction_id,
                        projected_name=next_name if name_changed else None,
                        available=(
                            next_relation_available
                            if relation_availability_changed
                            else None
                        ),
                        applied_snapshots=relation_records,
                        session=session,
                    )
                if binding_snapshots:
                    await _apply_faction_binding_availability(
                        binding_snapshots,
                        faction_id,
                        available=next_binding_available,
                        applied_snapshots=binding_records,
                        session=session,
                    )
                # 主档 CAS 后再次扫描，覆盖“首次快照之后才插入”的并发依赖。
                if name_changed or relation_availability_changed:
                    latest_relations = await _load_faction_relations(
                        novel_id,
                        faction_id,
                        include_deleted=name_changed,
                        session=session,
                    )
                    await _apply_faction_relation_changes(
                        latest_relations,
                        faction_id,
                        projected_name=next_name if name_changed else None,
                        available=(
                            next_relation_available
                            if relation_availability_changed
                            else None
                        ),
                        applied_snapshots=relation_records,
                        session=session,
                    )
                if binding_availability_changed:
                    latest_bindings = await _load_faction_bindings(
                        novel_id,
                        faction_id,
                        include_deleted=False,
                        session=session,
                    )
                    await _apply_faction_binding_availability(
                        latest_bindings,
                        faction_id,
                        available=next_binding_available,
                        applied_snapshots=binding_records,
                        session=session,
                    )
                return True
            except Exception:
                if session is None and faction_record is not None:
                    try:
                        await _compensate_faction_write(
                            faction_record,
                            relation_records,
                            binding_records,
                            [],
                        )
                    except Exception as compensation_exc:
                        logger.exception("势力更新失败且预镜像补偿失败")
                        raise RuntimeError(
                            "势力更新失败，且 standalone 补偿未完成"
                        ) from compensation_exc
                raise

        return await run_mongo_write_unit(_update, "update_faction_info")

    @staticmethod
    async def batch_update_sort_order(novel_id: str, sort_map: Dict[str, int]) -> int:
        """批量更新阵营排序权重。

        Args:
            novel_id: 小说 ObjectId 字符串。
            sort_map: {faction_id: new_sort_order} 映射。

        Returns:
            被实际修改的阵营数量。
        """
        async def _update(session):
            """在同一个写入单元内批量更新同一小说下的阵营排序。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            return await faction_repo.batch_update_sort_order(novel_id, sort_map, session=session)

        return await run_mongo_write_unit(_update, "batch_update_faction_sort_order")

    @staticmethod
    async def soft_delete_faction(
        novel_id: str,
        faction_id: str,
        *,
        expected_version: int,
    ) -> bool:
        """软删除阵营，并解除同小说下子阵营挂靠。

        Args:
            novel_id: 小说 ObjectId 字符串。
            faction_id: 业务层阵营 ID。
            expected_version: 客户端基于的势力版本。

        Returns:
            实际软删除成功时返回 True。
        """
        async def _delete(session: AsyncClientSession | None) -> bool:
            """在同一个写入单元内软删除势力并阻断全部未删除依赖。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            current = await _get_versioned_faction(
                novel_id,
                faction_id,
                expected_version,
                deleted=False,
                session=session,
            )
            relation_snapshots = await _load_faction_relations(
                novel_id,
                faction_id,
                include_deleted=False,
                session=session,
            )
            binding_snapshots = await _load_faction_bindings(
                novel_id,
                faction_id,
                include_deleted=False,
                session=session,
            )
            child_snapshots = await _load_child_factions(
                novel_id,
                faction_id,
                session=session,
            )
            faction_record: _CompensationRecord | None = None
            relation_records: list[_CompensationRecord] = []
            binding_records: list[_CompensationRecord] = []
            child_records: list[_CompensationRecord] = []
            try:
                now = get_utc_now()
                deleted = await _cas_update_faction_snapshot(
                    current,
                    {"is_deleted": True, "deleted_at": now},
                    session=session,
                )
                faction_record = _updated_compensation_record(current, deleted)
                await _apply_faction_relation_changes(
                    relation_snapshots,
                    faction_id,
                    projected_name=None,
                    available=False,
                    applied_snapshots=relation_records,
                    session=session,
                )
                await _apply_faction_binding_availability(
                    binding_snapshots,
                    faction_id,
                    available=False,
                    applied_snapshots=binding_records,
                    session=session,
                )
                latest_relations = await _load_faction_relations(
                    novel_id,
                    faction_id,
                    include_deleted=False,
                    session=session,
                )
                latest_bindings = await _load_faction_bindings(
                    novel_id,
                    faction_id,
                    include_deleted=False,
                    session=session,
                )
                await _apply_faction_relation_changes(
                    latest_relations,
                    faction_id,
                    projected_name=None,
                    available=False,
                    applied_snapshots=relation_records,
                    session=session,
                )
                await _apply_faction_binding_availability(
                    latest_bindings,
                    faction_id,
                    available=False,
                    applied_snapshots=binding_records,
                    session=session,
                )
                await _apply_child_unlink(
                    child_snapshots,
                    faction_id,
                    applied_snapshots=child_records,
                    session=session,
                )
                logger.info("软删除势力 %s 完成", faction_id)
                return True
            except Exception:
                if session is None and faction_record is not None:
                    try:
                        await _compensate_faction_write(
                            faction_record,
                            relation_records,
                            binding_records,
                            child_records,
                        )
                    except Exception as compensation_exc:
                        logger.exception("势力软删除失败且预镜像补偿失败")
                        raise RuntimeError(
                            "势力软删除失败，且 standalone 补偿未完成"
                        ) from compensation_exc
                raise

        return await run_mongo_write_unit(_delete, "soft_delete_faction")

    @staticmethod
    async def restore_faction(
        novel_id: str,
        faction_id: str,
        *,
        expected_version: int,
    ) -> bool:
        """恢复已软删除的阵营。

        Args:
            novel_id: 小说 ObjectId 字符串。
            faction_id: 业务层阵营 ID。
            expected_version: 客户端基于的势力版本。

        Returns:
            实际恢复成功时返回 True。
        """
        async def _restore(session: AsyncClientSession | None) -> bool:
            """在同一个写入单元内恢复势力并重算全部依赖有效态。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            deleted_faction = await _get_versioned_faction(
                novel_id,
                faction_id,
                expected_version,
                deleted=True,
                session=session,
            )
            await FactionService._ensure_unique_active_name(
                novel_id,
                str(deleted_faction.get("level_type") or "core"),
                str(deleted_faction.get("name", "")).strip(),
                session=session,
            )
            relation_should_unblock = deleted_faction.get("active_status") == "active"
            binding_should_unblock = bool(
                relation_should_unblock
                and deleted_faction.get("level_type", "core") == "core"
            )
            if relation_should_unblock:
                await FactionRelationService.validate_faction_unblock(
                    novel_id,
                    faction_id,
                    session=session,
                )
            if binding_should_unblock:
                await _validate_faction_binding_unblock(
                    novel_id,
                    faction_id,
                    session=session,
                )
            relation_snapshots = await _load_faction_relations(
                novel_id,
                faction_id,
                include_deleted=False,
                session=session,
            )
            binding_snapshots = await _load_faction_bindings(
                novel_id,
                faction_id,
                include_deleted=False,
                session=session,
            )
            faction_record: _CompensationRecord | None = None
            relation_records: list[_CompensationRecord] = []
            binding_records: list[_CompensationRecord] = []
            try:
                restored = await _cas_update_faction_snapshot(
                    deleted_faction,
                    {"is_deleted": False, "deleted_at": None},
                    session=session,
                )
                faction_record = _updated_compensation_record(deleted_faction, restored)
                await _apply_faction_relation_changes(
                    relation_snapshots,
                    faction_id,
                    projected_name=None,
                    available=relation_should_unblock,
                    applied_snapshots=relation_records,
                    session=session,
                )
                await _apply_faction_binding_availability(
                    binding_snapshots,
                    faction_id,
                    available=binding_should_unblock,
                    applied_snapshots=binding_records,
                    session=session,
                )
                latest_relations = await _load_faction_relations(
                    novel_id,
                    faction_id,
                    include_deleted=False,
                    session=session,
                )
                latest_bindings = await _load_faction_bindings(
                    novel_id,
                    faction_id,
                    include_deleted=False,
                    session=session,
                )
                await _apply_faction_relation_changes(
                    latest_relations,
                    faction_id,
                    projected_name=None,
                    available=relation_should_unblock,
                    applied_snapshots=relation_records,
                    session=session,
                )
                await _apply_faction_binding_availability(
                    latest_bindings,
                    faction_id,
                    available=binding_should_unblock,
                    applied_snapshots=binding_records,
                    session=session,
                )
                logger.info("恢复势力 %s 完成", faction_id)
                return True
            except Exception:
                if session is None and faction_record is not None:
                    try:
                        await _compensate_faction_write(
                            faction_record,
                            relation_records,
                            binding_records,
                            [],
                        )
                    except Exception as compensation_exc:
                        logger.exception("势力恢复失败且预镜像补偿失败")
                        raise RuntimeError(
                            "势力恢复失败，且 standalone 补偿未完成"
                        ) from compensation_exc
                raise

        return await run_mongo_write_unit(_restore, "restore_faction")

    @staticmethod
    async def hard_delete_faction(
        novel_id: str,
        faction_id: str,
        *,
        expected_version: int,
    ) -> Dict[str, Any]:
        """物理删除已软删除的阵营。

        Args:
            novel_id: 小说 ObjectId 字符串。
            faction_id: 业务层阵营 ID。
            expected_version: 客户端基于的势力版本。

        Returns:
            删除统计。
        """
        async def _delete(session: AsyncClientSession | None) -> Dict[str, Any]:
            """在同一个写入单元内按预镜像版本物理删除势力及全部引用。"""
            await novel_repo.get_novel_by_id(novel_id, session=session)
            deleted_faction = await _get_versioned_faction(
                novel_id,
                faction_id,
                expected_version,
                deleted=True,
                session=session,
            )
            relation_snapshots = await _load_faction_relations(
                novel_id,
                faction_id,
                include_deleted=True,
                session=session,
            )
            binding_snapshots = await _load_faction_bindings(
                novel_id,
                faction_id,
                include_deleted=True,
                session=session,
            )
            child_snapshots = await _load_child_factions(
                novel_id,
                faction_id,
                session=session,
            )
            deleted_relations: list[_CompensationRecord] = []
            deleted_bindings: list[_CompensationRecord] = []
            child_records: list[_CompensationRecord] = []
            faction_record: _CompensationRecord | None = None
            try:
                relations_deleted = await _delete_snapshots_exact(
                    _faction_relation_base_repo,
                    relation_snapshots,
                    deleted_snapshots=deleted_relations,
                    session=session,
                )
                bindings_deleted = await _delete_snapshots_exact(
                    _faction_binding_base_repo,
                    binding_snapshots,
                    deleted_snapshots=deleted_bindings,
                    session=session,
                )
                children_count = await _apply_child_unlink(
                    child_snapshots,
                    faction_id,
                    applied_snapshots=child_records,
                    session=session,
                )
                delete_result = await faction_repo.collection.delete_one(
                    {
                        "_id": deleted_faction["_id"],
                        **_snapshot_version_query(deleted_faction),
                        "is_deleted": True,
                    },
                    session=session,
                )
                if delete_result.deleted_count != 1:
                    raise DuplicateKeyError("势力已被其他请求修改，请刷新后重试")
                faction_record = _deleted_compensation_record(deleted_faction)
                stats = {
                    "faction_deleted": 1,
                    "children_unlinked": children_count,
                    "relations_deleted": relations_deleted,
                    "bindings_deleted": bindings_deleted,
                }
                logger.info("硬删除势力 %s 完成: %s", faction_id, stats)
                return stats
            except Exception:
                if session is None and (
                    faction_record is not None
                    or deleted_relations
                    or deleted_bindings
                    or child_records
                ):
                    try:
                        await _compensate_faction_write(
                            faction_record,
                            deleted_relations,
                            deleted_bindings,
                            child_records,
                        )
                    except Exception as compensation_exc:
                        logger.exception("势力硬删除失败且预镜像补偿失败")
                        raise RuntimeError(
                            "势力硬删除失败，且 standalone 补偿未完成"
                        ) from compensation_exc
                raise

        return await run_mongo_write_unit(_delete, "hard_delete_faction")
