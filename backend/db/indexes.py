import pymongo
import logging
import re
from collections import defaultdict
from typing import Any

from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import OperationFailure

from backend.db.mongo import get_database
from backend.db.utils import get_utc_now
from backend.llm.schemas.character_pydantic import FACTION_BUSINESS_ID_PATTERN
from backend.services.novel.character_name import normalize_character_name

logger = logging.getLogger(__name__)


CHARACTER_NORMALIZED_NAME_INDEX = "characters_active_novel_normalized_name_unique"
LEGACY_CHARACTER_NAME_INDEX = "characters_active_novel_name_unique"
LEGACY_CHARACTER_ID_INDEX = "characters_active_novel_character_unique"
CHARACTER_FACTION_PRIMARY_INDEX = (
    "character_faction_bindings_active_membership_primary_unique"
)
LEGACY_CHARACTER_FACTION_PRIMARY_INDEX = (
    "character_faction_bindings_active_primary_unique"
)
LEGACY_CHARACTER_FACTION_ID_INDEX = (
    "character_faction_bindings_active_novel_binding_unique"
)

CHARACTER_ID_PATTERN = re.compile(r"^char_[0-9]{6,}$")
CHARACTER_RELATION_ID_PATTERN = re.compile(r"^cr_[0-9]{6,}$")
CHARACTER_FACTION_BINDING_ID_PATTERN = re.compile(r"^cfb_[0-9]{6,}$")
FACTION_ID_PATTERN = re.compile(FACTION_BUSINESS_ID_PATTERN)
FACTION_RELATION_ID_PATTERN = re.compile(r"^fr_[0-9]{6,}$")

def _canonical_positive_int(value: Any, *, default: int = 1) -> int:
    """把历史值规范为排除布尔值的正整数。

    Args:
        value: 数据库中的历史标量值。
        default: 无法转换或小于 1 时采用的默认值。

    Returns:
        大于等于 1 的 Python 整数。
    """
    if isinstance(value, bool):
        return default
    try:
        converted = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return converted if converted >= 1 else default


def _canonical_non_negative_int(value: Any, *, default: int = 0) -> int:
    """把历史值规范为排除布尔值的非负整数。

    Args:
        value: 数据库中的历史标量值。
        default: 无法转换时采用的默认值。

    Returns:
        大于等于 0 的 Python 整数。
    """
    if isinstance(value, bool):
        return default
    try:
        converted = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, converted)


def _migration_values_equal(current: Any, canonical: Any) -> bool:
    """比较迁移前后值，并对布尔值与整数执行类型敏感判断。

    Args:
        current: 文档当前值。
        canonical: 迁移期望写入的规范值。

    Returns:
        值与关键标量类型均一致时返回 True。
    """
    if isinstance(canonical, int) and not isinstance(canonical, bool):
        return (
            isinstance(current, int)
            and not isinstance(current, bool)
            and current == canonical
        )
    return current == canonical


async def _drop_legacy_unique_index(
    collection: AsyncCollection,
    expected_key: dict[str, int],
    new_index_name: str,
) -> None:
    """删除软删除改造前遗留的全量唯一索引。

    Args:
        collection: 需要检查的 MongoDB 集合。
        expected_key: 旧索引的 key 定义。
        new_index_name: 新 partial unique 索引名称，用于避免误删。

    Returns:
        无。
    """
    # PyMongo Async 的 list_indexes() 是协程，必须先 await 得到异步游标。
    async with await collection.list_indexes() as cursor:
        async for index in cursor:
            index_name = index.get("name")
            if index_name in {"_id_", new_index_name}:
                continue

            # 只删除 key 完全一致、unique=true 且没有 partialFilterExpression 的旧索引。
            if (
                list(index.get("key", {}).items()) == list(expected_key.items())
                and index.get("unique") is True
                and "partialFilterExpression" not in index
            ):
                await collection.drop_index(index_name)
                logger.info("已删除旧全量唯一索引 %s.%s", collection.name, index_name)


def _format_character_conflict_sample(
    conflicts: dict[tuple[Any, str], list[Any]],
) -> str:
    """生成不暴露角色正文的有限冲突摘要。

    Args:
        conflicts: 以小说 ID 和规范键分组的冲突文档 ID。

    Returns:
        最多五组小说 ID、规范键与文档 ID 摘要。
    """
    samples: list[str] = []
    for (novel_id, normalized_name), document_ids in list(conflicts.items())[:5]:
        ids = ",".join(str(item) for item in document_ids[:5])
        samples.append(
            f"novel_id={novel_id}, normalized_name={normalized_name!r}, _id=[{ids}]"
        )
    return "; ".join(samples)


async def _backfill_character_normalized_names(collection: AsyncCollection) -> int:
    """预检历史角色名称并幂等回填 ``normalized_name``。

    Args:
        collection: characters 集合。

    Returns:
        本次实际需要回填或校正的文档数量。

    Raises:
        RuntimeError: 活动角色名称非法、规范化后重复，或业务 ID 无法满足全量唯一约束。
    """
    updates: list[tuple[Any, str]] = []
    invalid_active_documents: list[Any] = []
    invalid_character_id_documents: list[tuple[Any, Any]] = []
    active_names: dict[tuple[Any, str], list[Any]] = defaultdict(list)
    character_ids: dict[tuple[Any, str], list[Any]] = defaultdict(list)

    projection = {
        "_id": 1,
        "novel_id": 1,
        "character_id": 1,
        "name": 1,
        "normalized_name": 1,
        "is_deleted": 1,
    }
    async for document in collection.find({}, projection=projection):
        document_id = document.get("_id")
        novel_id = document.get("novel_id")
        character_id = document.get("character_id")
        if (
            novel_id is None
            or not isinstance(character_id, str)
            or CHARACTER_ID_PATTERN.fullmatch(character_id) is None
        ):
            invalid_character_id_documents.append((character_id, document_id))
        else:
            character_ids[(novel_id, character_id)].append(document_id)

        raw_name = document.get("name")
        try:
            if not isinstance(raw_name, str):
                raise ValueError("历史角色名称必须是字符串")
            _, normalized_name = normalize_character_name(raw_name)
        except (TypeError, ValueError):
            # 新名称索引只覆盖活动记录；回收站里的旧脏数据不应阻断当前启动。
            if document.get("is_deleted") is False:
                invalid_active_documents.append(document_id)
            continue

        if document.get("is_deleted") is False:
            active_names[(novel_id, normalized_name)].append(document_id)
        if document.get("normalized_name") != normalized_name:
            updates.append((document_id, normalized_name))

    duplicate_names = {
        key: document_ids
        for key, document_ids in active_names.items()
        if len(document_ids) > 1
    }
    duplicate_character_ids = {
        key: document_ids
        for key, document_ids in character_ids.items()
        if len(document_ids) > 1
    }
    if invalid_active_documents:
        sample = ",".join(str(item) for item in invalid_active_documents[:10])
        raise RuntimeError(
            "characters 索引迁移失败：存在名称为空或规范化后超过 80 字符的活动角色；"
            f"请先修正这些文档后重启，_id 样本=[{sample}]"
        )
    if duplicate_names:
        raise RuntimeError(
            "characters 索引迁移失败：活动角色名称经 NFKC、trim、casefold 后发生冲突；"
            "不会自动覆盖或删除角色，请人工修改重名记录后重启。冲突样本："
            f"{_format_character_conflict_sample(duplicate_names)}"
        )
    if invalid_character_id_documents or duplicate_character_ids:
        invalid_sample = ",".join(
            f"character_id={business_id!r}/_id={document_id}"
            for business_id, document_id in invalid_character_id_documents[:10]
        )
        duplicate_sample = _format_character_conflict_sample(duplicate_character_ids)
        raise RuntimeError(
            "characters 索引迁移失败：character_id 缺失或在同一小说中被历史记录复用，"
            "无法建立软删除后也不可复用的全量唯一索引；"
            f"缺失 _id 样本=[{invalid_sample}]，重复样本={duplicate_sample}"
        )

    # 预检全部通过后才写派生键；中断后重跑只会继续更新尚未完成的文档。
    for document_id, normalized_name in updates:
        await collection.update_one(
            {"_id": document_id},
            {"$set": {"normalized_name": normalized_name}},
        )
    if updates:
        logger.info("已回填或校正 characters.normalized_name：%s 条", len(updates))
    return len(updates)


async def _drop_exact_legacy_index(
    collection: AsyncCollection,
    *,
    index_name: str,
    expected_key: list[tuple[str, int]],
    expected_partial_filter: dict[str, Any],
) -> bool:
    """在替代索引成功后精确删除一个已知旧索引。

    Args:
        collection: 需要迁移索引的 MongoDB 集合。
        index_name: 只允许删除的旧索引名称。
        expected_key: 旧索引必须完全匹配的有序键定义。
        expected_partial_filter: 旧索引必须完全匹配的 partial 条件。

    Returns:
        本次确实删除旧索引时返回 True，否则返回 False。
    """
    async with await collection.list_indexes() as cursor:
        async for index in cursor:
            if index.get("name") != index_name:
                continue
            is_exact_legacy = (
                list(index.get("key", {}).items()) == expected_key
                and index.get("unique") is True
                and dict(index.get("partialFilterExpression") or {})
                == expected_partial_filter
            )
            if not is_exact_legacy:
                logger.warning(
                    "保留未知定义的同名索引 %s.%s，未执行自动删除",
                    collection.name,
                    index_name,
                )
                return False
            try:
                await collection.drop_index(index_name)
            except OperationFailure as exc:
                # 多实例同时启动时，另一实例可能已经完成同一个精确删除。
                if exc.code == 27:
                    return False
                raise
            logger.info("已删除已被新索引替代的旧索引 %s.%s", collection.name, index_name)
            return True
    return False


async def _resolve_compatible_index_name(
    collection: AsyncCollection,
    *,
    preferred_name: str,
    expected_key: list[tuple[str, int]],
    expected_partial_filter: dict[str, Any] | None = None,
) -> str:
    """复用任意已存在的等价索引，避免仅名称不同导致 code 85。

    Args:
        collection: 需要创建索引的 MongoDB 集合。
        preferred_name: 新安装默认使用的稳定索引名称。
        expected_key: 目标索引的有序键定义。
        expected_partial_filter: 可选 partialFilterExpression；全量索引传 None。

    Returns:
        可安全交给 IndexModel 的现有等价索引名或首选新名称。

    Raises:
        RuntimeError: 首选名称已存在，但定义并非当前目标规格。
    """
    equivalent_names: list[str] = []
    expected_partial = expected_partial_filter or {}
    async with await collection.list_indexes() as cursor:
        async for index in cursor:
            index_name = str(index.get("name") or "")
            is_equivalent = (
                list(index.get("key", {}).items()) == expected_key
                and index.get("unique") is True
                and dict(index.get("partialFilterExpression") or {}) == expected_partial
                and index.get("sparse") is not True
                and "collation" not in index
            )
            if index_name == preferred_name:
                if not is_equivalent:
                    raise RuntimeError(
                        f"索引 {collection.name}.{preferred_name} 已存在但定义不兼容，"
                        "为避免误删未知索引，启动已停止"
                    )
                return preferred_name
            if is_equivalent and index_name:
                equivalent_names.append(index_name)

    if equivalent_names:
        selected_name = sorted(equivalent_names)[0]
        logger.info(
            "复用已存在的等价索引 %s.%s（首选名称 %s）",
            collection.name,
            selected_name,
            preferred_name,
        )
        return selected_name
    return preferred_name


def _infer_legacy_membership_type(document: dict[str, Any]) -> str:
    """把旧版主归属/隐藏标记转换为当前成员类型。

    Args:
        document: 历史 character_faction_bindings 文档。

    Returns:
        ``primary``、``secondary`` 或 ``covert``。

    Raises:
        ValueError: 文档既无当前成员类型，也无可判定的旧布尔标记。
    """
    membership_type = document.get("membership_type")
    if membership_type in {"primary", "secondary", "covert"}:
        return str(membership_type)
    if document.get("is_primary") is True:
        return "primary"
    if document.get("is_primary") is False and document.get("is_hidden") is True:
        return "covert"
    if document.get("is_primary") is False and document.get("is_hidden") is False:
        return "secondary"
    raise ValueError("缺少可判定的 membership_type 或旧版 is_primary/is_hidden 标记")


async def _backfill_character_faction_binding_schema(
    collection: AsyncCollection,
    characters_collection: AsyncCollection,
    factions_collection: AsyncCollection,
) -> int:
    """预检旧绑定约束并幂等补齐当前全书级绑定字段。

    Args:
        collection: character_faction_bindings 集合。
        characters_collection: 用于复核角色状态的 characters 集合。
        factions_collection: 用于复核势力状态的 factions 集合。

    Returns:
        本次实际需要补齐的历史绑定数量。

    Raises:
        RuntimeError: 历史绑定无法确定成员类型，或违反当前唯一约束。
    """
    character_lookup: dict[tuple[Any, str], dict[str, Any]] = {}
    async for character in characters_collection.find(
        {},
        projection={
            "novel_id": 1,
            "character_id": 1,
            "is_deleted": 1,
            "status": 1,
            "is_core_character": 1,
        },
    ):
        novel_id = character.get("novel_id")
        character_id = character.get("character_id")
        if (
            novel_id is not None
            and isinstance(character_id, str)
            and CHARACTER_ID_PATTERN.fullmatch(character_id) is not None
        ):
            character_lookup[(novel_id, character_id)] = character

    faction_lookup: dict[tuple[Any, str], dict[str, Any]] = {}
    async for faction in factions_collection.find(
        {},
        projection={
            "novel_id": 1,
            "faction_id": 1,
            "is_deleted": 1,
            "active_status": 1,
            "level_type": 1,
        },
    ):
        novel_id = faction.get("novel_id")
        faction_id = faction.get("faction_id")
        if (
            novel_id is not None
            and isinstance(faction_id, str)
            and FACTION_ID_PATTERN.fullmatch(faction_id) is not None
        ):
            faction_lookup[(novel_id, faction_id)] = faction

    updates: list[tuple[Any, dict[str, Any], bool]] = []
    invalid_documents: list[tuple[Any, Any, Any, Any, str]] = []
    binding_ids: dict[tuple[Any, str], list[Any]] = defaultdict(list)
    active_primary: dict[tuple[Any, str], list[Any]] = defaultdict(list)
    active_semantic: dict[tuple[Any, str, str, str], list[Any]] = defaultdict(list)

    async for document in collection.find({}):
        document_id = document.get("_id")
        novel_id = document.get("novel_id")
        binding_id = document.get("binding_id")
        character_id = document.get("character_id")
        faction_id = document.get("faction_id")
        invalid_reasons: list[str] = []
        if novel_id is None:
            invalid_reasons.append("novel_id 缺失")
        if (
            not isinstance(binding_id, str)
            or CHARACTER_FACTION_BINDING_ID_PATTERN.fullmatch(binding_id) is None
        ):
            invalid_reasons.append("binding_id 格式非法")
        character_id_valid = bool(
            isinstance(character_id, str)
            and CHARACTER_ID_PATTERN.fullmatch(character_id) is not None
        )
        if not character_id_valid:
            invalid_reasons.append("character_id 格式非法")
        faction_id_valid = bool(
            isinstance(faction_id, str)
            and FACTION_ID_PATTERN.fullmatch(faction_id) is not None
        )
        if not faction_id_valid:
            invalid_reasons.append("faction_id 格式非法")
        if novel_id is not None and character_id_valid:
            if (novel_id, character_id) not in character_lookup:
                invalid_reasons.append("角色端点不存在")
        if novel_id is not None and faction_id_valid:
            if (novel_id, faction_id) not in faction_lookup:
                invalid_reasons.append("势力端点不存在")
        if invalid_reasons:
            invalid_documents.append(
                (
                    binding_id,
                    character_id,
                    faction_id,
                    document_id,
                    ";".join(invalid_reasons),
                )
            )
            continue

        try:
            membership_type = _infer_legacy_membership_type(document)
        except ValueError:
            invalid_documents.append(
                (
                    binding_id,
                    character_id,
                    faction_id,
                    document_id,
                    "membership_type 无法判定",
                )
            )
            continue

        persisted_character_blockers = _clean_blocker_ids(
            document.get("disabled_by_character_ids")
        )
        persisted_faction_blockers = list(
            dict.fromkeys(
                [
                    *_clean_blocker_ids(document.get("disabled_by_faction_ids")),
                    *_clean_blocker_ids(
                        document.get("disabled_by_faction_delete_ids")
                    ),
                ]
            )
        )
        # 历史 blocker 只用于推断旧 is_active=false 的原因；当前 blocker 必须按实体事实重建。
        character_blockers: list[str] = []
        faction_blockers: list[str] = []
        character = character_lookup[(novel_id, character_id)]
        faction = faction_lookup[(novel_id, faction_id)]
        if (
            character.get("is_deleted") is True
            or character.get("status") != "active"
            or character.get("is_core_character") is not True
        ) and character_id not in character_blockers:
            character_blockers.append(character_id)
        if (
            faction.get("is_deleted") is True
            or faction.get("active_status") != "active"
            or faction.get("level_type") != "core"
        ) and faction_id not in faction_blockers:
            faction_blockers.append(faction_id)
        if isinstance(document.get("user_is_active"), bool):
            user_is_active = bool(document["user_is_active"])
        elif persisted_character_blockers or persisted_faction_blockers:
            # 历史自动停用没有独立用户意图字段时，阻断来源优先解释 is_active=false。
            user_is_active = True
        else:
            user_is_active = bool(document.get("is_active", True))
        is_deleted = bool(document.get("is_deleted", False))
        effective_active = bool(
            user_is_active
            and not character_blockers
            and not faction_blockers
            and not is_deleted
        )

        binding_ids[(novel_id, binding_id)].append(document_id)
        if effective_active:
            active_semantic[
                (novel_id, character_id, faction_id, membership_type)
            ].append(document_id)
            if membership_type == "primary":
                active_primary[(novel_id, character_id)].append(document_id)

        set_fields: dict[str, Any] = {}
        if document.get("membership_type") != membership_type:
            set_fields["membership_type"] = membership_type
        # 先保留旧业务内容，再补齐当前响应、CAS 与自动阻断所需字段。
        legacy_defaults = {
            "role_title": document.get("role_in_faction"),
            "public_status": document.get("faction_stance"),
            "loyalty_level": None,
            "joined_volume_id": None,
            "left_volume_id": None,
            "sort_order": _canonical_non_negative_int(document.get("sort_order")),
            "version": _canonical_positive_int(document.get("version")),
            "deletion_sources": [],
        }
        for field, value in legacy_defaults.items():
            if field not in document or (
                field in {"version", "sort_order"}
                and not _migration_values_equal(document.get(field), value)
            ):
                set_fields[field] = value
        current_fields = {
            "user_is_active": user_is_active,
            "disabled_by_character_ids": character_blockers,
            "disabled_by_faction_ids": faction_blockers,
            "is_active": effective_active,
        }
        set_fields.update(
            {
                field: value
                for field, value in current_fields.items()
                if not _migration_values_equal(document.get(field), value)
            }
        )
        if set_fields or "disabled_by_faction_delete_ids" in document:
            updates.append(
                (
                    document_id,
                    set_fields,
                    "disabled_by_faction_delete_ids" in document,
                )
            )

    duplicate_binding_ids = {
        key: document_ids
        for key, document_ids in binding_ids.items()
        if len(document_ids) > 1
    }
    duplicate_primary = {
        key: document_ids
        for key, document_ids in active_primary.items()
        if len(document_ids) > 1
    }
    duplicate_semantic = {
        key: document_ids
        for key, document_ids in active_semantic.items()
        if len(document_ids) > 1
    }
    if invalid_documents or duplicate_binding_ids or duplicate_primary or duplicate_semantic:
        invalid_sample = ";".join(
            f"binding_id={binding_id!r},character_id={character_id!r},"
            f"faction_id={faction_id!r},_id={document_id},reason={reason}"
            for binding_id, character_id, faction_id, document_id, reason in invalid_documents[:10]
        )
        conflict_ids = [
            str(document_id)
            for groups in (duplicate_binding_ids, duplicate_primary, duplicate_semantic)
            for document_ids in groups.values()
            for document_id in document_ids
        ][:10]
        raise RuntimeError(
            "character_faction_bindings 索引迁移失败：历史绑定缺少必要标识、"
            "无法判定成员类型，或违反当前业务 ID/主归属/语义唯一约束；"
            "不会自动删除或合并绑定，请人工处理后重启。"
            f"无效 _id 样本=[{invalid_sample}]，冲突 _id 样本=[{','.join(conflict_ids)}]"
        )

    # 与角色名称迁移相同，完整预检后才执行可重入的字段补齐。
    for document_id, set_fields, remove_legacy_blockers in updates:
        update_document: dict[str, Any] = {}
        if set_fields:
            update_document["$set"] = set_fields
        if remove_legacy_blockers:
            update_document["$unset"] = {"disabled_by_faction_delete_ids": ""}
        await collection.update_one({"_id": document_id}, update_document)
    if updates:
        logger.info("已补齐历史 character_faction_bindings：%s 条", len(updates))
    return len(updates)


CHARACTER_RELATION_TYPES = frozenset(
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
SYMMETRIC_CHARACTER_RELATION_TYPES = frozenset(
    {"friend", "romantic", "ally", "rival", "enemy"}
)
FACTION_RELATION_TYPES = frozenset(
    {
        "hostile",
        "allied",
        "cold_war",
        "dependent",
        "subordinate",
        "trade_partner",
        "secret_cooperation",
        "historical_enemy",
    }
)
SYMMETRIC_FACTION_RELATION_TYPES = frozenset(
    {
        "hostile",
        "allied",
        "cold_war",
        "trade_partner",
        "secret_cooperation",
        "historical_enemy",
    }
)


def _clean_blocker_ids(value: Any) -> list[str]:
    """规范化关系自动阻断来源数组。

    Args:
        value: 历史文档中的原始阻断来源字段。

    Returns:
        去空、去重且保持首次出现顺序的字符串列表。
    """
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        )
    )


def _normalize_relation_endpoints(
    source_id: str,
    target_id: str,
    relation_type: str,
    symmetric_types: frozenset[str],
) -> tuple[str, str]:
    """按关系方向矩阵计算稳定语义端点。

    Args:
        source_id: 来源实体业务 ID。
        target_id: 目标实体业务 ID。
        relation_type: 关系类型。
        symmetric_types: 需要忽略方向的关系类型集合。

    Returns:
        规范化来源与目标业务 ID。
    """
    if relation_type in symmetric_types:
        return tuple(sorted((source_id, target_id)))
    return source_id, target_id


def _format_business_conflicts(
    conflicts: dict[tuple[Any, ...], list[Any]],
) -> str:
    """格式化有限数量的迁移冲突业务键和文档 ID。

    Args:
        conflicts: 业务键到冲突 MongoDB 文档 ID 的映射。

    Returns:
        最多十组冲突的单行摘要。
    """
    samples: list[str] = []
    for key, document_ids in list(conflicts.items())[:10]:
        ids = ",".join(str(item) for item in document_ids[:10])
        samples.append(f"key={key}, _id=[{ids}]")
    return "; ".join(samples)


async def _preflight_full_business_id(
    collection: AsyncCollection,
    *,
    id_field: str,
    label: str,
    id_pattern: re.Pattern[str],
) -> int:
    """预检同小说全历史业务 ID 唯一性。

    Args:
        collection: 待迁移的实体集合。
        id_field: 业务 ID 字段名。
        label: 错误消息中的集合标签。
        id_pattern: 与公开 Schema 等价的业务 ID 正则。

    Returns:
        扫描到的合法文档数量。

    Raises:
        RuntimeError: 业务 ID 缺失或被历史记录复用时抛出。
    """
    groups: dict[tuple[Any, str], list[Any]] = defaultdict(list)
    invalid: list[tuple[Any, Any]] = []
    async for document in collection.find(
        {}, projection={"_id": 1, "novel_id": 1, id_field: 1}
    ):
        novel_id = document.get("novel_id")
        business_id = document.get(id_field)
        if (
            novel_id is None
            or not isinstance(business_id, str)
            or id_pattern.fullmatch(business_id) is None
        ):
            invalid.append((business_id, document.get("_id")))
            continue
        groups[(novel_id, business_id)].append(document.get("_id"))
    duplicates = {key: ids for key, ids in groups.items() if len(ids) > 1}
    if invalid or duplicates:
        raise RuntimeError(
            f"{label} 索引迁移失败：{id_field} 不符合公开格式或在同一小说全历史中被复用；"
            "不会自动删除或合并记录。"
            "无效=["
            + ",".join(
                f"business_id={business_id!r}/_id={document_id}"
                for business_id, document_id in invalid[:10]
            )
            + "]，"
            f"冲突={_format_business_conflicts(duplicates)}"
        )
    return sum(len(ids) for ids in groups.values())


async def _backfill_faction_versions(collection: AsyncCollection) -> int:
    """幂等补齐势力主档内部 CAS 版本。

    Args:
        collection: factions 集合。

    Returns:
        本次实际补齐或校正版本的势力数量。
    """
    updates: list[Any] = []
    async for document in collection.find({}, projection={"_id": 1, "version": 1}):
        version = document.get("version")
        if isinstance(version, int) and not isinstance(version, bool) and version >= 1:
            continue
        updates.append(document["_id"])
    for document_id in updates:
        await collection.update_one(
            {"_id": document_id},
            {"$set": {"version": 1}},
        )
    if updates:
        logger.info("已补齐 factions 内部 CAS version：%s 条", len(updates))
    return len(updates)


async def _backfill_endpoint_guard_revisions(collection: AsyncCollection) -> int:
    """幂等回填角色或势力端点的严格非负整数贡献计数。

    Args:
        collection: characters 或 factions 集合。

    Returns:
        本次补齐缺失值或修正非法类型的文档数量。
    """
    updates: list[Any] = []
    async for document in collection.find(
        {},
        projection={"_id": 1, "dependency_guard_revision": 1},
    ):
        current = document.get("dependency_guard_revision")
        canonical = _canonical_non_negative_int(current)
        if _migration_values_equal(current, canonical):
            continue
        updates.append((document["_id"], canonical))

    # 所有身份预检由调用方先完成；逐条幂等写使中断后的重跑只处理剩余文档。
    for document_id, canonical in updates:
        await collection.update_one(
            {"_id": document_id},
            {"$set": {"dependency_guard_revision": canonical}},
        )
    if updates:
        logger.info(
            "已补齐 %s 端点 dependency_guard_revision：%s 条",
            getattr(collection, "name", "endpoint"),
            len(updates),
        )
    return len(updates)


async def _backfill_character_relation_schema(
    collection: AsyncCollection,
    characters_collection: AsyncCollection,
) -> int:
    """预检并幂等补齐历史角色关系的派生字段和用户启停意图。

    Args:
        collection: character_relations 集合。
        characters_collection: 用于复核当前端点状态的 characters 集合。

    Returns:
        本次需要回填或校正的关系数量。

    Raises:
        RuntimeError: 业务 ID、端点或活动语义唯一约束冲突时抛出。
    """
    character_lookup: dict[tuple[Any, str], dict[str, Any]] = {}
    async for character in characters_collection.find(
        {},
        projection={
            "novel_id": 1,
            "character_id": 1,
            "is_deleted": 1,
            "status": 1,
            "is_core_character": 1,
        },
    ):
        novel_id = character.get("novel_id")
        character_id = character.get("character_id")
        if (
            novel_id is not None
            and isinstance(character_id, str)
            and CHARACTER_ID_PATTERN.fullmatch(character_id) is not None
        ):
            character_lookup[(novel_id, character_id)] = character

    updates: list[tuple[Any, dict[str, Any]]] = []
    invalid: list[tuple[Any, Any]] = []
    relation_ids: dict[tuple[Any, str], list[Any]] = defaultdict(list)
    active_semantics: dict[tuple[Any, str, str, str], list[Any]] = defaultdict(list)

    async for document in collection.find({}):
        document_id = document.get("_id")
        novel_id = document.get("novel_id")
        relation_id = document.get("relation_id")
        source_id = document.get("source_character_id")
        target_id = document.get("target_character_id")
        relation_type = document.get("relation_type")
        if (
            novel_id is None
            or not isinstance(relation_id, str)
            or CHARACTER_RELATION_ID_PATTERN.fullmatch(relation_id) is None
            or not isinstance(source_id, str)
            or CHARACTER_ID_PATTERN.fullmatch(source_id) is None
            or not isinstance(target_id, str)
            or CHARACTER_ID_PATTERN.fullmatch(target_id) is None
            or source_id == target_id
            or relation_type not in CHARACTER_RELATION_TYPES
            or (novel_id, source_id) not in character_lookup
            or (novel_id, target_id) not in character_lookup
        ):
            invalid.append((relation_id, source_id, target_id, document_id))
            continue

        relation_ids[(novel_id, relation_id)].append(document_id)
        persisted_blockers = _clean_blocker_ids(
            document.get("disabled_by_character_ids")
        )
        if isinstance(document.get("user_is_active"), bool):
            user_is_active = bool(document["user_is_active"])
        elif persisted_blockers:
            # 已存在自动阻断来源时，旧 is_active=false 不是用户主动停用。
            user_is_active = True
        else:
            user_is_active = bool(document.get("is_active", True))
        # 旧 blocker 仅参与用户意图推断，不能让已经恢复的端点永久保持失效。
        blockers: list[str] = []
        for endpoint_id in (source_id, target_id):
            endpoint = character_lookup[(novel_id, endpoint_id)]
            if (
                endpoint.get("is_deleted") is True
                or endpoint.get("status") != "active"
                or endpoint.get("is_core_character") is not True
            ) and endpoint_id not in blockers:
                blockers.append(endpoint_id)
        is_deleted = bool(document.get("is_deleted", False))
        effective_active = user_is_active and not blockers and not is_deleted
        normalized_source, normalized_target = _normalize_relation_endpoints(
            source_id,
            target_id,
            str(relation_type),
            SYMMETRIC_CHARACTER_RELATION_TYPES,
        )
        if effective_active:
            active_semantics[
                (novel_id, normalized_source, normalized_target, str(relation_type))
            ].append(document_id)

        set_fields = {
            "normalized_source_character_id": normalized_source,
            "normalized_target_character_id": normalized_target,
            "semantic_key": f"{normalized_source}:{normalized_target}:{relation_type}",
            "version": _canonical_positive_int(document.get("version")),
            "user_is_active": user_is_active,
            "disabled_by_character_ids": blockers,
            "is_active": effective_active,
            "is_deleted": is_deleted,
            "deletion_sources": _clean_blocker_ids(document.get("deletion_sources")),
            "sort_order": _canonical_non_negative_int(document.get("sort_order")),
        }
        changed = {
            key: value
            for key, value in set_fields.items()
            if not _migration_values_equal(document.get(key), value)
        }
        if changed:
            updates.append((document_id, changed))

    duplicate_ids = {key: ids for key, ids in relation_ids.items() if len(ids) > 1}
    duplicate_semantics = {
        key: ids for key, ids in active_semantics.items() if len(ids) > 1
    }
    if invalid or duplicate_ids or duplicate_semantics:
        invalid_summary = ",".join(
            f"relation_id={business_id!r},source={source_id!r},target={target_id!r},"
            f"_id={document_id}"
            for business_id, source_id, target_id, document_id in invalid[:10]
        )
        raise RuntimeError(
            "character_relations 索引迁移失败：存在非法端点/类型、全历史 relation_id "
            "复用或活动语义冲突；不会自动修改冲突记录。"
            f"无效=[{invalid_summary}]，ID 冲突={_format_business_conflicts(duplicate_ids)}，"
            f"语义冲突={_format_business_conflicts(duplicate_semantics)}"
        )

    # 完整预检后逐条幂等回填；进程中断后可安全重跑。
    for document_id, set_fields in updates:
        await collection.update_one({"_id": document_id}, {"$set": set_fields})
    if updates:
        logger.info("已补齐或校正历史 character_relations：%s 条", len(updates))
    return len(updates)


async def _backfill_faction_relation_schema(
    collection: AsyncCollection,
    factions_collection: AsyncCollection,
) -> int:
    """预检并幂等补齐历史阵营关系的版本、方向键和阻断来源。

    Args:
        collection: faction_relations 集合。
        factions_collection: 用于校验端点作用域和补显示名称的 factions 集合。

    Returns:
        本次需要回填或校正的关系数量。

    Raises:
        RuntimeError: 存在悬空端点、ID 复用或活动语义冲突时抛出。
    """
    faction_lookup: dict[tuple[Any, str], dict[str, Any]] = {}
    async for faction in factions_collection.find(
        {},
        projection={
            "novel_id": 1,
            "faction_id": 1,
            "name": 1,
            "is_deleted": 1,
            "active_status": 1,
        },
    ):
        novel_id = faction.get("novel_id")
        faction_id = faction.get("faction_id")
        if (
            novel_id is not None
            and isinstance(faction_id, str)
            and FACTION_ID_PATTERN.fullmatch(faction_id) is not None
        ):
            faction_lookup[(novel_id, faction_id)] = faction

    updates: list[tuple[Any, dict[str, Any], bool]] = []
    invalid: list[tuple[Any, Any, Any, Any, str]] = []
    relation_ids: dict[tuple[Any, str], list[Any]] = defaultdict(list)
    active_semantics: dict[tuple[Any, str, str, str], list[Any]] = defaultdict(list)
    async for document in collection.find({}):
        document_id = document.get("_id")
        novel_id = document.get("novel_id")
        relation_id = document.get("relation_id")
        source_id = document.get("source_faction_id")
        target_id = document.get("target_faction_id")
        relation_type = document.get("relation_type")
        reason = ""
        if (
            novel_id is None
            or not isinstance(relation_id, str)
            or FACTION_RELATION_ID_PATTERN.fullmatch(relation_id) is None
            or not isinstance(source_id, str)
            or FACTION_ID_PATTERN.fullmatch(source_id) is None
            or not isinstance(target_id, str)
            or FACTION_ID_PATTERN.fullmatch(target_id) is None
            or source_id == target_id
            or relation_type not in FACTION_RELATION_TYPES
        ):
            reason = "缺少标识、自引用或关系类型非法"
        elif (novel_id, source_id) not in faction_lookup or (novel_id, target_id) not in faction_lookup:
            reason = "端点不存在于同一小说（悬空或跨小说引用）"
        elif any(
            not isinstance(faction_lookup[(novel_id, endpoint_id)].get("name"), str)
            or not str(faction_lookup[(novel_id, endpoint_id)].get("name")).strip()
            for endpoint_id in (source_id, target_id)
        ):
            reason = "端点阵营名称为空，无法生成非空名称投影"
        if reason:
            invalid.append((relation_id, source_id, target_id, document_id, reason))
            continue

        relation_ids[(novel_id, relation_id)].append(document_id)
        legacy_blockers = _clean_blocker_ids(
            document.get("disabled_by_faction_delete_ids")
        )
        persisted_blockers = list(
            dict.fromkeys(
                [
                    *_clean_blocker_ids(document.get("disabled_by_faction_ids")),
                    *legacy_blockers,
                ]
            )
        )
        if isinstance(document.get("user_is_active"), bool):
            user_is_active = bool(document["user_is_active"])
        elif persisted_blockers:
            user_is_active = True
        else:
            user_is_active = bool(document.get("is_active", True))
        # 旧 blocker 仅参与用户意图推断，最终集合完全反映当前阵营状态。
        blockers: list[str] = []
        for endpoint_id in (source_id, target_id):
            endpoint = faction_lookup[(novel_id, endpoint_id)]
            if (
                endpoint.get("is_deleted") is True
                or endpoint.get("active_status") != "active"
            ) and endpoint_id not in blockers:
                # 迁移以当前实体事实重建自动阻断，不能信任旧关系是否曾正确级联。
                blockers.append(endpoint_id)
        is_deleted = bool(document.get("is_deleted", False))
        effective_active = user_is_active and not blockers and not is_deleted
        normalized_source, normalized_target = _normalize_relation_endpoints(
            source_id,
            target_id,
            str(relation_type),
            SYMMETRIC_FACTION_RELATION_TYPES,
        )
        if effective_active:
            active_semantics[
                (novel_id, normalized_source, normalized_target, str(relation_type))
            ].append(document_id)

        source_faction = faction_lookup[(novel_id, source_id)]
        target_faction = faction_lookup[(novel_id, target_id)]
        set_fields = {
            "source_faction_name": str(source_faction["name"]),
            "target_faction_name": str(target_faction["name"]),
            "normalized_source_faction_id": normalized_source,
            "normalized_target_faction_id": normalized_target,
            "semantic_key": f"{normalized_source}:{normalized_target}:{relation_type}",
            "version": _canonical_positive_int(document.get("version")),
            "user_is_active": user_is_active,
            "disabled_by_faction_ids": blockers,
            "is_active": effective_active,
            "is_deleted": is_deleted,
            "deletion_sources": _clean_blocker_ids(document.get("deletion_sources")),
            "sort_order": _canonical_non_negative_int(document.get("sort_order")),
        }
        changed = {
            key: value
            for key, value in set_fields.items()
            if not _migration_values_equal(document.get(key), value)
        }
        if changed or "disabled_by_faction_delete_ids" in document:
            updates.append(
                (document_id, changed, "disabled_by_faction_delete_ids" in document)
            )

    duplicate_ids = {key: ids for key, ids in relation_ids.items() if len(ids) > 1}
    duplicate_semantics = {
        key: ids for key, ids in active_semantics.items() if len(ids) > 1
    }
    if invalid or duplicate_ids or duplicate_semantics:
        invalid_summary = "; ".join(
            f"relation_id={business_id!r}, source={source_id!r}, target={target_id!r}, "
            f"_id={document_id}, reason={reason}"
            for business_id, source_id, target_id, document_id, reason in invalid[:10]
        )
        raise RuntimeError(
            "faction_relations 索引迁移失败：存在非法/悬空端点、全历史 relation_id "
            "复用或活动语义冲突；不会自动修改冲突记录。"
            f"无效=[{invalid_summary}]，ID 冲突={_format_business_conflicts(duplicate_ids)}，"
            f"语义冲突={_format_business_conflicts(duplicate_semantics)}"
        )

    for document_id, set_fields, remove_legacy_blockers in updates:
        update_document: dict[str, Any] = {"$set": set_fields}
        if remove_legacy_blockers:
            update_document["$unset"] = {"disabled_by_faction_delete_ids": ""}
        await collection.update_one({"_id": document_id}, update_document)
    if updates:
        logger.info("已补齐或校正历史 faction_relations：%s 条", len(updates))
    return len(updates)


async def init_novel_indexes():
    """初始化novels集合的索引。"""
    try:
        db = get_database()
        novels_collection = db["novels"]
        
        logger.info("正在初始化'novels'集合的索引...")
        
        indexes = [
            # 单字段索引
            pymongo.IndexModel([("title", pymongo.ASCENDING)]),
            pymongo.IndexModel([("status", pymongo.ASCENDING)]),
            pymongo.IndexModel([("tags", pymongo.ASCENDING)]),
            pymongo.IndexModel([("updated_at", pymongo.DESCENDING)]),
            pymongo.IndexModel([("is_deleted", pymongo.ASCENDING)]),
            
            # 书架列表：按is_deleted过滤，按updated_at降序排序
            pymongo.IndexModel([
                ("is_deleted", pymongo.ASCENDING),
                ("updated_at", pymongo.DESCENDING)
            ]),
            
            # 标题搜索：按is_deleted过滤，按标题排序或查询
            pymongo.IndexModel([
                ("is_deleted", pymongo.ASCENDING),
                ("title", pymongo.ASCENDING)
            ]),
        ]
        
        await novels_collection.create_indexes(indexes)
        logger.info("成功初始化'novels'集合的索引。")
    except Exception as e:
        logger.error(f"初始化novel索引失败：{e}")


async def init_volume_indexes():
    """初始化volumes集合的索引。"""
    try:
        db = get_database()
        volumes_collection = db["volumes"]

        logger.info("正在初始化'volumes'集合的索引...")

        await _drop_legacy_unique_index(
            volumes_collection,
            {"novel_id": 1, "order_index": 1},
            "volumes_active_novel_order_unique",
        )

        indexes = [
            # 单字段索引：按小说过滤拉取全书卷列表
            pymongo.IndexModel([("novel_id", pymongo.ASCENDING)]),

            # 只约束未软删除卷，允许回收站里保留历史序号。
            pymongo.IndexModel(
                [("novel_id", pymongo.ASCENDING), ("order_index", pymongo.ASCENDING)],
                unique=True,
                partialFilterExpression={"is_deleted": False},
                name="volumes_active_novel_order_unique",
            ),

            # 读优化组合索引：未删除卷按序号排列的常用查询
            pymongo.IndexModel([
                ("novel_id", pymongo.ASCENDING),
                ("is_deleted", pymongo.ASCENDING),
                ("order_index", pymongo.ASCENDING)
            ]),

            # 按最近更新时间检索
            pymongo.IndexModel([("updated_at", pymongo.DESCENDING)]),
        ]

        await volumes_collection.create_indexes(indexes)
        logger.info("成功初始化'volumes'集合的索引。")
    except Exception as e:
        logger.error(f"初始化volume索引失败：{e}")


async def init_faction_indexes():
    """初始化factions集合的索引。"""
    try:
        db = get_database()
        factions_collection = db["factions"]

        logger.info("正在初始化'factions'集合的索引...")

        await _preflight_full_business_id(
            factions_collection,
            id_field="faction_id",
            label="factions",
            id_pattern=FACTION_ID_PATTERN,
        )
        await _backfill_faction_versions(factions_collection)
        await _backfill_endpoint_guard_revisions(factions_collection)
        faction_id_index_name = await _resolve_compatible_index_name(
            factions_collection,
            preferred_name="factions_novel_faction_unique",
            expected_key=[("novel_id", 1), ("faction_id", 1)],
        )

        indexes = [
            # 业务 ID 在同一小说全历史中唯一，软删除后也禁止复用。
            pymongo.IndexModel(
                [("novel_id", pymongo.ASCENDING), ("faction_id", pymongo.ASCENDING)],
                unique=True,
                name=faction_id_index_name,
            ),

            # 按层级类型过滤（用于按 core / major_volume 等召回）
            pymongo.IndexModel(
                [("novel_id", pymongo.ASCENDING), ("level_type", pymongo.ASCENDING)],
            ),

            # 按父级阵营查子阵营
            pymongo.IndexModel(
                [("novel_id", pymongo.ASCENDING), ("parent_faction_id", pymongo.ASCENDING)],
            ),

            # 按名称检索阵营
            pymongo.IndexModel(
                [("novel_id", pymongo.ASCENDING), ("name", pymongo.ASCENDING)],
            ),

            # 读优化：未删除阵营按排序权重排列
            pymongo.IndexModel([
                ("novel_id", pymongo.ASCENDING),
                ("is_deleted", pymongo.ASCENDING),
                ("sort_order", pymongo.ASCENDING)
            ]),

            # 按最近更新时间检索
            pymongo.IndexModel([("updated_at", pymongo.DESCENDING)]),
        ]

        await factions_collection.create_indexes(indexes)
        if faction_id_index_name != "factions_active_novel_faction_unique":
            await _drop_exact_legacy_index(
                factions_collection,
                index_name="factions_active_novel_faction_unique",
                expected_key=[("novel_id", 1), ("faction_id", 1)],
                expected_partial_filter={"is_deleted": False},
            )
        logger.info("成功初始化'factions'集合的索引。")
    except Exception as exc:
        logger.error("初始化faction索引失败：%s", exc)
        raise


async def init_faction_relation_indexes():
    """初始化faction_relations集合的索引。"""
    try:
        db = get_database()
        relations_collection = db["faction_relations"]

        logger.info("正在初始化'faction_relations'集合的索引...")
        await _backfill_faction_relation_schema(relations_collection, db["factions"])
        relation_id_index_name = await _resolve_compatible_index_name(
            relations_collection,
            preferred_name="faction_relations_novel_relation_unique",
            expected_key=[("novel_id", 1), ("relation_id", 1)],
        )

        indexes = [
            # 稳定关系 ID 在同一小说全历史中唯一，软删除后不可复用。
            pymongo.IndexModel(
                [("novel_id", pymongo.ASCENDING), ("relation_id", pymongo.ASCENDING)],
                unique=True,
                name=relation_id_index_name,
            ),

            pymongo.IndexModel(
                [
                    ("novel_id", pymongo.ASCENDING),
                    ("normalized_source_faction_id", pymongo.ASCENDING),
                    ("normalized_target_faction_id", pymongo.ASCENDING),
                    ("relation_type", pymongo.ASCENDING),
                ],
                unique=True,
                partialFilterExpression={"is_deleted": False, "is_active": True},
                name="faction_relations_active_semantic_unique",
            ),

            # 按来源阵营召回关系
            pymongo.IndexModel(
                [("novel_id", pymongo.ASCENDING), ("source_faction_id", pymongo.ASCENDING)],
            ),

            # 按目标阵营召回关系
            pymongo.IndexModel(
                [("novel_id", pymongo.ASCENDING), ("target_faction_id", pymongo.ASCENDING)],
            ),

            # 常用列表读取：只读未删除、启用关系，并按强度排序
            pymongo.IndexModel([
                ("novel_id", pymongo.ASCENDING),
                ("is_deleted", pymongo.ASCENDING),
                ("is_active", pymongo.ASCENDING),
                ("intensity", pymongo.DESCENDING),
            ]),

            # 按最近更新时间检索
            pymongo.IndexModel([("updated_at", pymongo.DESCENDING)]),

            pymongo.IndexModel(
                [("novel_id", 1), ("creation_idempotency_key_hash", 1)],
                unique=True,
                partialFilterExpression={
                    "creation_idempotency_key_hash": {"$type": "string"},
                },
                name="faction_relations_creation_idempotency_unique",
            ),
        ]

        await relations_collection.create_indexes(indexes)
        if relation_id_index_name != "faction_relations_active_novel_relation_unique":
            await _drop_exact_legacy_index(
                relations_collection,
                index_name="faction_relations_active_novel_relation_unique",
                expected_key=[("novel_id", 1), ("relation_id", 1)],
                expected_partial_filter={"is_deleted": False},
            )
        logger.info("成功初始化'faction_relations'集合的索引。")
    except Exception as exc:
        logger.error("初始化faction_relations索引失败：%s", exc)
        raise


async def init_character_indexes() -> None:
    """初始化全书级角色集合索引。

    Args:
        无。

    Returns:
        无。
    """
    try:
        collection = get_database()["characters"]
        # 先校验并回填历史派生键；旧唯一索引此时仍在，迁移失败不会留下保护空窗。
        await _backfill_character_normalized_names(collection)
        await _backfill_endpoint_guard_revisions(collection)
        character_id_index_name = await _resolve_compatible_index_name(
            collection,
            preferred_name="characters_novel_character_unique",
            expected_key=[("novel_id", 1), ("character_id", 1)],
        )
        normalized_name_index_name = await _resolve_compatible_index_name(
            collection,
            preferred_name=CHARACTER_NORMALIZED_NAME_INDEX,
            expected_key=[("novel_id", 1), ("normalized_name", 1)],
            expected_partial_filter={"is_deleted": False},
        )
        indexes = [
            # 业务 ID 终身唯一，软删除后也不得复用。
            pymongo.IndexModel(
                [("novel_id", 1), ("character_id", 1)],
                unique=True,
                name=character_id_index_name,
            ),
            pymongo.IndexModel(
                [("novel_id", 1), ("normalized_name", 1)],
                unique=True,
                partialFilterExpression={"is_deleted": False},
                name=normalized_name_index_name,
            ),
            pymongo.IndexModel(
                [
                    ("novel_id", 1),
                    ("is_deleted", 1),
                    ("is_core_character", 1),
                    ("status", 1),
                    ("sort_order", 1),
                    ("character_id", 1),
                ],
                name="characters_novel_core_sort",
            ),
            pymongo.IndexModel(
                [("novel_id", 1), ("role_type", 1), ("importance_level", 1)],
                name="characters_novel_role_importance",
            ),
            pymongo.IndexModel(
                [("novel_id", 1), ("creation_idempotency_key_hash", 1)],
                unique=True,
                partialFilterExpression={
                    "creation_idempotency_key_hash": {"$type": "string"},
                },
                name="characters_creation_idempotency_unique",
            ),
        ]
        await collection.create_indexes(indexes)
        # 替代索引全部成功后才精确清理已知旧定义，避免构建失败期间失去唯一约束。
        if normalized_name_index_name != LEGACY_CHARACTER_NAME_INDEX:
            await _drop_exact_legacy_index(
                collection,
                index_name=LEGACY_CHARACTER_NAME_INDEX,
                expected_key=[("novel_id", 1), ("name", 1)],
                expected_partial_filter={"is_deleted": False},
            )
        if character_id_index_name != LEGACY_CHARACTER_ID_INDEX:
            await _drop_exact_legacy_index(
                collection,
                index_name=LEGACY_CHARACTER_ID_INDEX,
                expected_key=[("novel_id", 1), ("character_id", 1)],
                expected_partial_filter={"is_deleted": False},
            )
        logger.info("成功初始化'characters'集合的索引。")
    except Exception as exc:
        logger.error("初始化characters索引失败：%s", exc)
        raise


async def init_character_relation_indexes() -> None:
    """初始化全书级角色关系索引。

    Args:
        无。

    Returns:
        无。
    """
    try:
        collection = get_database()["character_relations"]
        await _backfill_character_relation_schema(
            collection,
            get_database()["characters"],
        )
        indexes = [
            pymongo.IndexModel(
                [("novel_id", 1), ("relation_id", 1)],
                unique=True,
                name="character_relations_novel_relation_unique",
            ),
            # 同一小说内只允许一条活动语义边，追加生成不能覆盖既有关系。
            pymongo.IndexModel(
                [
                    ("novel_id", 1),
                    ("normalized_source_character_id", 1),
                    ("normalized_target_character_id", 1),
                    ("relation_type", 1),
                ],
                unique=True,
                partialFilterExpression={"is_deleted": False, "is_active": True},
                name="character_relations_active_semantic_unique",
            ),
            pymongo.IndexModel(
                [("novel_id", 1), ("source_character_id", 1), ("is_active", 1)],
                name="character_relations_source_lookup",
            ),
            pymongo.IndexModel(
                [("novel_id", 1), ("target_character_id", 1), ("is_active", 1)],
                name="character_relations_target_lookup",
            ),
            pymongo.IndexModel(
                [("novel_id", 1), ("creation_idempotency_key_hash", 1)],
                unique=True,
                partialFilterExpression={
                    "creation_idempotency_key_hash": {"$type": "string"},
                },
                name="character_relations_creation_idempotency_unique",
            ),
        ]
        await collection.create_indexes(indexes)
        logger.info("成功初始化'character_relations'集合的索引。")
    except Exception as exc:
        logger.error("初始化character_relations索引失败：%s", exc)
        raise


async def init_character_faction_binding_indexes() -> None:
    """初始化全书级角色势力绑定索引。

    Args:
        无。

    Returns:
        无。
    """
    try:
        collection = get_database()["character_faction_bindings"]
        database = get_database()
        await _backfill_character_faction_binding_schema(
            collection,
            database["characters"],
            database["factions"],
        )
        binding_id_index_name = await _resolve_compatible_index_name(
            collection,
            preferred_name="character_faction_bindings_novel_binding_unique",
            expected_key=[("novel_id", 1), ("binding_id", 1)],
        )
        primary_index_name = await _resolve_compatible_index_name(
            collection,
            preferred_name=CHARACTER_FACTION_PRIMARY_INDEX,
            expected_key=[("novel_id", 1), ("character_id", 1)],
            expected_partial_filter={
                "membership_type": "primary",
                "is_active": True,
                "is_deleted": False,
            },
        )
        indexes = [
            pymongo.IndexModel(
                [("novel_id", 1), ("binding_id", 1)],
                unique=True,
                name=binding_id_index_name,
            ),
            pymongo.IndexModel(
                [("novel_id", 1), ("character_id", 1)],
                unique=True,
                partialFilterExpression={
                    "membership_type": "primary",
                    "is_active": True,
                    "is_deleted": False,
                },
                name=primary_index_name,
            ),
            # 防止并发创建 secondary/covert 时绕过 service 的先查后写校验。
            pymongo.IndexModel(
                [
                    ("novel_id", 1),
                    ("character_id", 1),
                    ("faction_id", 1),
                    ("membership_type", 1),
                ],
                unique=True,
                partialFilterExpression={"is_deleted": False, "is_active": True},
                name="character_faction_bindings_active_semantic_unique",
            ),
            pymongo.IndexModel(
                [("novel_id", 1), ("character_id", 1), ("is_active", 1)],
                name="character_faction_bindings_character_lookup",
            ),
            pymongo.IndexModel(
                [("novel_id", 1), ("faction_id", 1), ("is_active", 1)],
                name="character_faction_bindings_faction_lookup",
            ),
            pymongo.IndexModel(
                [("novel_id", 1), ("creation_idempotency_key_hash", 1)],
                unique=True,
                partialFilterExpression={
                    "creation_idempotency_key_hash": {"$type": "string"},
                },
                name="character_faction_bindings_creation_idempotency_unique",
            ),
        ]
        await collection.create_indexes(indexes)
        if primary_index_name != LEGACY_CHARACTER_FACTION_PRIMARY_INDEX:
            await _drop_exact_legacy_index(
                collection,
                index_name=LEGACY_CHARACTER_FACTION_PRIMARY_INDEX,
                expected_key=[("novel_id", 1), ("character_id", 1), ("is_primary", 1)],
                expected_partial_filter={
                    "is_deleted": False,
                    "is_active": True,
                    "is_primary": True,
                },
            )
        if binding_id_index_name != LEGACY_CHARACTER_FACTION_ID_INDEX:
            await _drop_exact_legacy_index(
                collection,
                index_name=LEGACY_CHARACTER_FACTION_ID_INDEX,
                expected_key=[("novel_id", 1), ("binding_id", 1)],
                expected_partial_filter={"is_deleted": False},
            )
        logger.info("成功初始化'character_faction_bindings'集合的索引。")
    except Exception as exc:
        logger.error("初始化character_faction_bindings索引失败：%s", exc)
        raise


async def _backfill_id_sequences(db: Any) -> int:
    """按历史正式文档的最大业务 ID 幂等抬升各实体序列。

    Args:
        db: 当前 MongoDB 数据库实例。

    Returns:
        找到并用于抬升序列的小说与实体组合数量。
    """
    sequence_specs = (
        ("factions", "faction_id", "faction", "fac"),
        ("faction_relations", "relation_id", "faction_relation", "fr"),
        ("characters", "character_id", "character", "char"),
        ("character_relations", "relation_id", "character_relation", "cr"),
        (
            "character_faction_bindings",
            "binding_id",
            "character_faction_binding",
            "cfb",
        ),
    )
    maximums: dict[tuple[Any, str], int] = {}
    for collection_name, id_field, entity_type, prefix in sequence_specs:
        prefix_token = f"{prefix}_"
        projection = {"novel_id": 1, id_field: 1}
        async for document in db[collection_name].find({}, projection=projection):
            novel_id = document.get("novel_id")
            business_id = document.get(id_field)
            if novel_id is None or not isinstance(business_id, str):
                continue
            if not business_id.startswith(prefix_token):
                continue
            suffix = business_id[len(prefix_token) :]
            if not suffix.isdigit():
                continue
            key = (novel_id, entity_type)
            maximums[key] = max(maximums.get(key, 0), int(suffix))

    now = get_utc_now()
    for (novel_id, entity_type), maximum in maximums.items():
        # $max 让迁移可重复、可并发执行，且绝不会把已经前进的序列回退。
        await db["id_sequences"].update_one(
            {"novel_id": novel_id, "entity_type": entity_type},
            {
                "$max": {"value": maximum},
                "$set": {"updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    if maximums:
        logger.info("已按历史业务 ID 校正 id_sequences：%s 组", len(maximums))
    return len(maximums)


async def init_id_sequence_indexes() -> None:
    """初始化小说内业务 ID 序列索引。

    Args:
        无。

    Returns:
        无。
    """
    try:
        db = get_database()
        await db["id_sequences"].create_indexes(
            [
                pymongo.IndexModel(
                    [("novel_id", 1), ("entity_type", 1)],
                    unique=True,
                    name="id_sequences_novel_entity_unique",
                )
            ]
        )
        await _backfill_id_sequences(db)
        logger.info("成功初始化'id_sequences'集合的索引。")
    except Exception as exc:
        logger.error("初始化id_sequences索引失败：%s", exc)
        raise


async def init_all_indexes():
    """初始化所有数据库索引。"""
    await init_novel_indexes()
    await init_volume_indexes()
    await init_faction_indexes()
    await init_faction_relation_indexes()
    await init_character_indexes()
    await init_character_relation_indexes()
    await init_character_faction_binding_indexes()
    await init_id_sequence_indexes()
