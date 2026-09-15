"""章节 AI 生成与审校的结构化 Schema。

所有 Schema 都刻意保持宽松：AI 输出多出无关字段时忽略即可，
不做"格式不合就重试"的强约束，未知枚举值在服务层归一化。
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 审校严重级别：blocking 阻断定稿，warning 允许定稿，notice 仅提示
REVIEW_SEVERITIES = ("blocking", "warning", "notice")

# 审校类别
REVIEW_CATEGORIES = (
    "character_state",
    "location_state",
    "item_owner",
    "item_ability",
    "rule_conflict",
    "timeline",
    "foreshadowing",
    "duplicate_plot",
    "style",
)

# 状态变更可作用的实体类型
STATE_CHANGE_ENTITY_TYPES = ("character", "location", "item", "rule")

# 状态变更类型
STATE_CHANGE_TYPES = (
    "state_change",
    "transfer",
    "discover",
    "use",
    "damage",
    "repair",
    "destroy",
    "seal",
    "unseal",
    "lose",
    "recover",
    "move",
    "relationship",
    "other",
)


def _to_text_list(value: Any) -> Any:
    """把字符串或数组统一成去空、去重、保序的字符串数组。"""
    if value is None:
        return []
    if isinstance(value, str):
        parts = [seg.strip() for seg in value.replace("；", "\n").replace(";", "\n").split("\n")]
        return [seg for seg in parts if seg]
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
        return out
    return [str(value).strip()] if str(value).strip() else []


class AISchemaModel(BaseModel):
    """允许额外字段的 AI 输出基础模型。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class UsedEntities(AISchemaModel):
    """本章正文实际使用到的实体业务 ID。"""

    characters: List[str] = Field(default_factory=list)
    locations: List[str] = Field(default_factory=list)
    items: List[str] = Field(default_factory=list)
    rules: List[str] = Field(default_factory=list)

    @field_validator("characters", "locations", "items", "rules", mode="before")
    @classmethod
    def normalize_ids(cls, value: Any) -> Any:
        """规范化实体 ID 数组。"""
        return _to_text_list(value)


class StateChangeProposalSchema(AISchemaModel):
    """AI 识别的实体事实变化候选，必须经用户确认才可写入正式数据。"""

    entity_type: str = Field(default="item")
    entity_id: str = Field(default="")
    change_type: str = Field(default="other")
    before: Dict[str, str] = Field(default_factory=dict)
    after: Dict[str, str] = Field(default_factory=dict)
    evidence: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    note: str = Field(default="")

    @field_validator("before", "after", mode="before")
    @classmethod
    def normalize_pairs(cls, value: Any) -> Any:
        """把任意键值映射转为字符串映射，非映射输入视为空。"""
        if not isinstance(value, dict):
            return {}
        return {str(k): str(v) for k, v in value.items() if str(v).strip()}


class ChapterPlanSchema(AISchemaModel):
    """章节剧情方案（第一步输出，确认后才生成正文）。"""

    title: str = Field(default="")
    goal: str = Field(default="")
    conflict: str = Field(default="")
    beats: List[str] = Field(default_factory=list)
    character_changes: List[str] = Field(default_factory=list)
    location_usage: List[str] = Field(default_factory=list)
    item_usage: List[str] = Field(default_factory=list)
    rule_constraints: List[str] = Field(default_factory=list)
    foreshadowing: List[str] = Field(default_factory=list)
    ending_hook: str = Field(default="")

    @field_validator(
        "beats",
        "character_changes",
        "location_usage",
        "item_usage",
        "rule_constraints",
        "foreshadowing",
        mode="before",
    )
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化方案中的字符串数组。"""
        return _to_text_list(value)


class ChapterDraftSchema(AISchemaModel):
    """章节正文候选，不直接覆盖 chapters.content。"""

    title: str = Field(default="")
    content: str = Field(default="")
    summary: str = Field(default="")
    unresolved_threads: List[str] = Field(default_factory=list)
    used_entities: UsedEntities = Field(default_factory=UsedEntities)
    state_change_proposals: List[StateChangeProposalSchema] = Field(default_factory=list)

    @field_validator("unresolved_threads", mode="before")
    @classmethod
    def normalize_threads(cls, value: Any) -> Any:
        """规范化伏笔数组。"""
        return _to_text_list(value)


class ChapterContinueSchema(AISchemaModel):
    """续写候选片段。"""

    content: str = Field(default="")
    summary: str = Field(default="")
    unresolved_threads: List[str] = Field(default_factory=list)

    @field_validator("unresolved_threads", mode="before")
    @classmethod
    def normalize_threads(cls, value: Any) -> Any:
        """规范化伏笔数组。"""
        return _to_text_list(value)


class ChapterRewriteSchema(AISchemaModel):
    """选段改写候选。"""

    rewritten_text: str = Field(default="")
    change_notes: List[str] = Field(default_factory=list)
    preserved_facts: List[str] = Field(default_factory=list)

    @field_validator("change_notes", "preserved_facts", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化说明数组。"""
        return _to_text_list(value)


class ChapterExpandSchema(AISchemaModel):
    """选段扩写候选。"""

    expanded_text: str = Field(default="")
    added_details: List[str] = Field(default_factory=list)

    @field_validator("added_details", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化说明数组。"""
        return _to_text_list(value)


class ChapterCompressSchema(AISchemaModel):
    """选段压缩候选。"""

    compressed_text: str = Field(default="")
    removed_notes: List[str] = Field(default_factory=list)

    @field_validator("removed_notes", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化说明数组。"""
        return _to_text_list(value)


class ReviewIssueSchema(AISchemaModel):
    """单条一致性问题。"""

    severity: str = Field(default="notice")
    category: str = Field(default="style")
    entity_type: str = Field(default="")
    entity_id: str = Field(default="")
    message: str = Field(default="")
    evidence: str = Field(default="")
    suggestion: str = Field(default="")
    can_ignore: bool = Field(default=True)


class ConsistencyReviewSchema(AISchemaModel):
    """一致性审校结果。"""

    summary: str = Field(default="")
    issues: List[ReviewIssueSchema] = Field(default_factory=list)


class StateChangeProposalResultSchema(AISchemaModel):
    """状态变更候选集合。"""

    proposals: List[StateChangeProposalSchema] = Field(default_factory=list)
    summary: str = Field(default="")


__all__ = [
    "AISchemaModel",
    "UsedEntities",
    "StateChangeProposalSchema",
    "ChapterPlanSchema",
    "ChapterDraftSchema",
    "ChapterContinueSchema",
    "ChapterRewriteSchema",
    "ChapterExpandSchema",
    "ChapterCompressSchema",
    "ReviewIssueSchema",
    "ConsistencyReviewSchema",
    "StateChangeProposalResultSchema",
    "REVIEW_SEVERITIES",
    "REVIEW_CATEGORIES",
    "STATE_CHANGE_ENTITY_TYPES",
    "STATE_CHANGE_TYPES",
]
