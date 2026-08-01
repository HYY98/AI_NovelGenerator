"""阵营关系人工维护与正式响应的严格 Pydantic 契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from backend.llm.schemas.character_pydantic import (
    FACTION_BUSINESS_ID_PATTERN,
    StrictSchemaModel,
)


_OBJECT_ID_PATTERN = r"^[0-9a-f]{24}$"
_RELATION_ID_PATTERN = r"^fr_[0-9]{6,}$"


FactionRelationType: TypeAlias = Literal[
    "hostile",
    "allied",
    "cold_war",
    "dependent",
    "subordinate",
    "trade_partner",
    "secret_cooperation",
    "historical_enemy",
]

SYMMETRIC_FACTION_RELATION_TYPES: frozenset[str] = frozenset(
    {
        "hostile",
        "allied",
        "cold_war",
        "trade_partner",
        "secret_cooperation",
        "historical_enemy",
    }
)


def build_faction_relation_semantic_key(
    source_faction_id: str,
    target_faction_id: str,
    relation_type: FactionRelationType | str,
) -> tuple[str, str, str]:
    """按关系方向规则构造稳定的阵营关系语义键。

    Args:
        source_faction_id: 来源阵营业务 ID。
        target_faction_id: 目标阵营业务 ID。
        relation_type: 阵营关系类型。

    Returns:
        规范化来源、规范化目标和关系类型组成的三元组。
    """
    if relation_type in SYMMETRIC_FACTION_RELATION_TYPES:
        source_faction_id, target_faction_id = sorted(
            (source_faction_id, target_faction_id)
        )
    return source_faction_id, target_faction_id, str(relation_type)


class FactionRelationCreateRequestV1(StrictSchemaModel):
    """人工创建阵营关系时允许写入的字段。"""

    source_faction_id: str = Field(pattern=FACTION_BUSINESS_ID_PATTERN)
    target_faction_id: str = Field(pattern=FACTION_BUSINESS_ID_PATTERN)
    relation_type: FactionRelationType
    current_state: str = Field(min_length=1, max_length=2000)
    core_conflict: str = Field(min_length=1, max_length=2000)
    hidden_tension: str = Field(default="", max_length=2000)
    possible_change: str = Field(min_length=1, max_length=2000)
    intensity: int = Field(ge=1, le=5, strict=True)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_distinct_endpoints(self) -> "FactionRelationCreateRequestV1":
        """拒绝阵营关系自引用。

        Args:
            无。

        Returns:
            校验通过后的创建请求。
        """
        if self.source_faction_id == self.target_faction_id:
            raise ValueError("阵营关系不能指向自身")
        return self


class FactionRelationUpdateRequestV1(StrictSchemaModel):
    """更新阵营关系内容时允许写入的完整字段集合。"""

    expected_version: int = Field(ge=1, strict=True)
    relation_type: FactionRelationType
    current_state: str = Field(min_length=1, max_length=2000)
    core_conflict: str = Field(min_length=1, max_length=2000)
    hidden_tension: str = Field(default="", max_length=2000)
    possible_change: str = Field(min_length=1, max_length=2000)
    intensity: int = Field(ge=1, le=5, strict=True)


class FactionRelationActiveRequestV1(StrictSchemaModel):
    """显式变更阵营关系用户启停意图的请求。"""

    expected_version: int = Field(ge=1, strict=True)
    is_active: bool


class FactionRelationVersionRequestV1(StrictSchemaModel):
    """仅携带乐观锁版本的阵营关系生命周期请求。"""

    expected_version: int = Field(ge=1, strict=True)


class FactionRelationResponseV1(StrictSchemaModel):
    """阵营关系当前事实的完整公开响应。"""

    novel_id: str = Field(pattern=_OBJECT_ID_PATTERN)
    relation_id: str = Field(pattern=_RELATION_ID_PATTERN)
    source_faction_id: str = Field(pattern=FACTION_BUSINESS_ID_PATTERN)
    target_faction_id: str = Field(pattern=FACTION_BUSINESS_ID_PATTERN)
    source_faction_name: str | None = Field(default=None, min_length=1)
    target_faction_name: str | None = Field(default=None, min_length=1)
    relation_type: FactionRelationType
    current_state: str = Field(default="", max_length=2000)
    core_conflict: str = Field(default="", max_length=2000)
    hidden_tension: str = Field(default="", max_length=2000)
    possible_change: str = Field(default="", max_length=2000)
    intensity: int = Field(ge=1, le=5, strict=True)
    user_is_active: bool = True
    is_active: bool
    sort_order: int = Field(default=0, ge=0, strict=True)
    version: int = Field(ge=1, strict=True)
    is_deleted: bool
    deleted_at: datetime | None = None
    deletion_sources: list[str] = Field(default_factory=list)
    disabled_by_faction_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_validator("deletion_sources", "disabled_by_faction_ids", mode="before")
    @classmethod
    def normalize_source_lists(cls, value: object) -> object:
        """清理删除来源和自动阻断来源数组。

        Args:
            value: Repository 返回的原始数组。

        Returns:
            去空、去重并保持首次出现顺序的数组。
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
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        return normalized

    @model_validator(mode="after")
    def validate_distinct_endpoints(self) -> "FactionRelationResponseV1":
        """保证正式响应不包含自引用关系。

        Args:
            无。

        Returns:
            校验通过后的响应。
        """
        if self.source_faction_id == self.target_faction_id:
            raise ValueError("阵营关系不能指向自身")
        return self


__all__ = [
    "FactionRelationActiveRequestV1",
    "FactionRelationCreateRequestV1",
    "FactionRelationResponseV1",
    "FactionRelationType",
    "FactionRelationUpdateRequestV1",
    "FactionRelationVersionRequestV1",
    "SYMMETRIC_FACTION_RELATION_TYPES",
    "build_faction_relation_semantic_key",
]
