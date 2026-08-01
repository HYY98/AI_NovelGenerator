"""全书级角色关系主档与直接生成预览的 Pydantic 契约。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from backend.llm.schemas.character_pydantic import StrictSchemaModel


CHARACTER_RELATIONS_RESULT_SCHEMA_ID = "character.relation_generation.result"
CHARACTER_RELATIONS_RESULT_SCHEMA_VERSION = "1.0.0"

_OBJECT_ID_PATTERN = r"^[0-9a-f]{24}$"
_CHARACTER_ID_PATTERN = r"^char_[0-9]{6,}$"
_RELATION_ID_PATTERN = r"^cr_[0-9]{6,}$"
_RELATION_REF_PATTERN = r"^relation_[0-9]{3}$"
_SORT_ORDER_MAX = 2_147_483_647


CharacterRelationType: TypeAlias = Literal[
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
]
SymmetricCharacterRelationType: TypeAlias = Literal[
    "friend",
    "romantic",
    "ally",
    "rival",
    "enemy",
]

SYMMETRIC_CHARACTER_RELATION_TYPES: frozenset[str] = frozenset(
    {"friend", "romantic", "ally", "rival", "enemy"}
)


def _normalize_identifier_list(value: object) -> object:
    """清理 ID 数组并按首次出现顺序去重。

    Args:
        value: Pydantic 字段收到的原始 ID 数组。

    Returns:
        清理空白、移除空项和重复项后的数组。
    """
    if not isinstance(value, list):
        return value

    normalized: list[object] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            normalized.append(item)
            continue
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def _normalize_optional_guidance(value: object) -> object:
    """规范化可空的关系生成指导文本。

    Args:
        value: 原始用户指导文本。

    Returns:
        去除首尾空白后的文本、null 或原始非字符串值。
    """
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    return cleaned or None


def build_relation_semantic_key(
    source_character_id: str,
    target_character_id: str,
    relation_type: CharacterRelationType,
) -> tuple[str, str, str]:
    """构建可用于检测反向对称重复的关系语义键。

    Args:
        source_character_id: 关系源角色业务 ID。
        target_character_id: 关系目标角色业务 ID。
        relation_type: 角色关系类型。

    Returns:
        规范化源、规范化目标和关系类型组成的语义键。
    """
    if relation_type in SYMMETRIC_CHARACTER_RELATION_TYPES:
        source_character_id, target_character_id = sorted(
            (source_character_id, target_character_id)
        )
    return source_character_id, target_character_id, relation_type


class CharacterRelationCreateRequestV1(StrictSchemaModel):
    """人工创建全书级角色关系时允许写入的字段。"""

    source_character_id: str = Field(pattern=_CHARACTER_ID_PATTERN)
    target_character_id: str = Field(pattern=_CHARACTER_ID_PATTERN)
    relation_type: CharacterRelationType
    current_state: str = Field(min_length=1, max_length=2000)
    core_conflict: str = Field(min_length=1, max_length=2000)
    hidden_tension: str = Field(min_length=1, max_length=2000)
    possible_change: str = Field(min_length=1, max_length=2000)
    story_value: str = Field(min_length=1, max_length=2000)
    intensity: int = Field(ge=1, le=5, strict=True)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=_SORT_ORDER_MAX, strict=True)

    @model_validator(mode="after")
    def validate_distinct_endpoints(self) -> "CharacterRelationCreateRequestV1":
        """拒绝人工创建的角色关系自引用。

        Args:
            无。

        Returns:
            校验通过后的创建请求。
        """
        if self.source_character_id == self.target_character_id:
            raise ValueError("角色关系不能指向自身")
        return self


class CharacterRelationUpdateRequestV1(StrictSchemaModel):
    """更新角色关系内容时允许写入的完整字段集合。"""

    expected_version: int = Field(ge=1, strict=True)
    relation_type: CharacterRelationType
    current_state: str = Field(min_length=1, max_length=2000)
    core_conflict: str = Field(min_length=1, max_length=2000)
    hidden_tension: str = Field(min_length=1, max_length=2000)
    possible_change: str = Field(min_length=1, max_length=2000)
    story_value: str = Field(min_length=1, max_length=2000)
    intensity: int = Field(ge=1, le=5, strict=True)


class CharacterRelationActiveRequestV1(StrictSchemaModel):
    """显式变更角色关系用户启停意图的请求。"""

    expected_version: int = Field(ge=1, strict=True)
    is_active: bool


class CharacterRelationVersionRequestV1(StrictSchemaModel):
    """仅携带乐观锁版本的关系生命周期请求。"""

    expected_version: int = Field(ge=1, strict=True)


class CharacterRelationResponseV1(StrictSchemaModel):
    """角色关系主档的完整公开响应。"""

    novel_id: str = Field(pattern=_OBJECT_ID_PATTERN)
    relation_id: str = Field(pattern=_RELATION_ID_PATTERN)
    source_character_id: str = Field(pattern=_CHARACTER_ID_PATTERN)
    target_character_id: str = Field(pattern=_CHARACTER_ID_PATTERN)
    source_character_name: str | None = Field(default=None, min_length=1, max_length=80)
    target_character_name: str | None = Field(default=None, min_length=1, max_length=80)
    relation_type: CharacterRelationType
    current_state: str = Field(min_length=1, max_length=2000)
    core_conflict: str = Field(min_length=1, max_length=2000)
    hidden_tension: str = Field(min_length=1, max_length=2000)
    possible_change: str = Field(min_length=1, max_length=2000)
    story_value: str = Field(min_length=1, max_length=2000)
    intensity: int = Field(ge=1, le=5, strict=True)
    user_is_active: bool = True
    is_active: bool
    sort_order: int = Field(ge=0, strict=True)
    version: int = Field(ge=1, strict=True)
    is_deleted: bool
    deleted_at: datetime | None = None
    deletion_sources: list[str] = Field(default_factory=list)
    disabled_by_character_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_validator("deletion_sources", "disabled_by_character_ids", mode="before")
    @classmethod
    def normalize_deletion_sources(cls, value: object) -> object:
        """清理关系删除来源数组。

        Args:
            value: Repository 返回的删除来源数组。

        Returns:
            去空、去重并保持原顺序的数组。
        """
        return _normalize_identifier_list(value)

    @model_validator(mode="after")
    def validate_distinct_endpoints(self) -> "CharacterRelationResponseV1":
        """保证持久化关系响应不包含自引用。

        Args:
            无。

        Returns:
            校验通过后的关系响应。
        """
        if self.source_character_id == self.target_character_id:
            raise ValueError("角色关系不能指向自身")
        return self


class CharacterRelationsGenerateRequestV1(StrictSchemaModel):
    """直接生成全书级角色关系预览的请求。"""

    novel_id: str = Field(pattern=_OBJECT_ID_PATTERN)
    character_ids: list[str] = Field(min_length=2, max_length=100)
    allow_isolated_characters: bool = False
    relation_count_limit: int | None = Field(default=None, ge=1, le=500, strict=True)
    user_guidance: str | None = Field(default=None, max_length=2000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0, strict=True)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    system_prompt: str | None = None
    use_stream: bool = True

    @field_validator("character_ids", mode="before")
    @classmethod
    def normalize_character_ids(cls, value: object) -> object:
        """清理并去重关系生成请求中的角色业务 ID。

        Args:
            value: 原始角色 ID 数组。

        Returns:
            规范化后保持首次出现顺序的角色 ID 数组。
        """
        return _normalize_identifier_list(value)

    @field_validator("character_ids")
    @classmethod
    def validate_character_ids(cls, value: list[str]) -> list[str]:
        """校验关系生成只引用稳定角色业务 ID。

        Args:
            value: 去重后的角色 ID 数组。

        Returns:
            校验通过的原数组。
        """
        if any(re.fullmatch(_CHARACTER_ID_PATTERN, item) is None for item in value):
            raise ValueError("character_ids 只能包含 char_ 开头的稳定角色业务 ID")
        return value

    @field_validator("user_guidance", mode="before")
    @classmethod
    def normalize_user_guidance(cls, value: object) -> object:
        """清理指导文本并把纯空白转换为 null。

        Args:
            value: 原始用户指导文本。

        Returns:
            规范化后的用户指导文本。
        """
        return _normalize_optional_guidance(value)

    @model_validator(mode="after")
    def validate_relation_count_limit(self) -> "CharacterRelationsGenerateRequestV1":
        """校验关系数量不超过所选角色可形成的非自引用边。

        Args:
            无。

        Returns:
            校验通过后的关系生成请求。
        """
        if self.relation_count_limit is None:
            return self

        character_count = len(self.character_ids)
        maximum_edge_count = character_count * (character_count - 1)
        if self.relation_count_limit > maximum_edge_count:
            raise ValueError("relation_count_limit 不能超过所选角色可形成的有向边数量")

        minimum_cover_count = (character_count + 1) // 2
        if (
            not self.allow_isolated_characters
            and self.relation_count_limit < minimum_cover_count
        ):
            raise ValueError("relation_count_limit 不足以覆盖全部所选角色")
        return self


class CharacterRelationCandidateV1(StrictSchemaModel):
    """模型输出或用户编辑后的单条角色关系候选。"""

    relation_ref: str = Field(
        pattern=_RELATION_REF_PATTERN,
        description="仅在本次候选中使用的稳定引用，如 relation_001。",
    )
    source_character_id: str = Field(
        pattern=_CHARACTER_ID_PATTERN,
        description="关系源角色业务 ID，必须来自本次输入角色。",
    )
    target_character_id: str = Field(
        pattern=_CHARACTER_ID_PATTERN,
        description="关系目标角色业务 ID，必须来自本次输入角色。",
    )
    relation_type: CharacterRelationType = Field(description="关系类型。")
    current_state: str = Field(
        min_length=1,
        max_length=2000,
        description="故事开始时双方关系的当前状态。",
    )
    core_conflict: str = Field(
        min_length=1,
        max_length=2000,
        description="关系中的核心矛盾。",
    )
    hidden_tension: str = Field(
        min_length=1,
        max_length=2000,
        description="未公开或尚未爆发的深层张力。",
    )
    possible_change: str = Field(
        min_length=1,
        max_length=2000,
        description="跨全书可能发生的关系变化。",
    )
    story_value: str = Field(
        min_length=1,
        max_length=2000,
        description="该关系推动主线的叙事价值。",
    )
    intensity: int = Field(ge=1, le=5, strict=True, description="关系强度，1 至 5。")
    # google-genai 1.69.0 无法转换布尔 const；使用必填严格布尔值并在 Pydantic 层固定为 true。
    is_active: bool = Field(
        ...,
        strict=True,
        description="新生成关系固定为 true。",
    )

    @field_validator("is_active")
    @classmethod
    def validate_is_active_true(cls, value: bool) -> bool:
        """强制新生成的角色关系处于活动状态。

        Args:
            value: Provider 返回的严格布尔值。

        Returns:
            值为 true 时返回原值。
        """
        if value is not True:
            raise ValueError("新生成关系的 is_active 必须为 true")
        return value

    @model_validator(mode="after")
    def validate_distinct_endpoints(self) -> "CharacterRelationCandidateV1":
        """拒绝模型候选中的角色自引用。

        Args:
            无。

        Returns:
            校验通过后的关系候选。
        """
        if self.source_character_id == self.target_character_id:
            raise ValueError("角色关系候选不能指向自身")
        return self


class CharacterRelationsResultSchemaV1(StrictSchemaModel):
    """Provider 返回的角色关系候选数组。"""

    relations: list[CharacterRelationCandidateV1] = Field(
        min_length=1,
        max_length=500,
        description="仅追加到当前小说的全书级角色关系候选列表。",
    )

    @model_validator(mode="after")
    def validate_unique_relations(self) -> "CharacterRelationsResultSchemaV1":
        """校验局部引用和规范化语义边唯一性。

        Args:
            无。

        Returns:
            校验通过后的关系生成结果。
        """
        relation_refs = [relation.relation_ref for relation in self.relations]
        if len(relation_refs) != len(set(relation_refs)):
            raise ValueError("relations 中的 relation_ref 不能重复")

        semantic_keys = [
            build_relation_semantic_key(
                relation.source_character_id,
                relation.target_character_id,
                relation.relation_type,
            )
            for relation in self.relations
        ]
        if len(semantic_keys) != len(set(semantic_keys)):
            raise ValueError("relations 中不能包含重复或反向重复的同类型语义边")
        return self


__all__ = [
    "CHARACTER_RELATIONS_RESULT_SCHEMA_ID",
    "CHARACTER_RELATIONS_RESULT_SCHEMA_VERSION",
    "CharacterRelationActiveRequestV1",
    "CharacterRelationCandidateV1",
    "CharacterRelationCreateRequestV1",
    "CharacterRelationResponseV1",
    "CharacterRelationType",
    "CharacterRelationUpdateRequestV1",
    "CharacterRelationVersionRequestV1",
    "CharacterRelationsGenerateRequestV1",
    "CharacterRelationsResultSchemaV1",
    "SYMMETRIC_CHARACTER_RELATION_TYPES",
    "SymmetricCharacterRelationType",
    "build_relation_semantic_key",
]
