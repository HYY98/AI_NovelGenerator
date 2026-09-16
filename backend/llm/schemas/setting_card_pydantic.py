"""设定卡 AI 能力的结构化 Schema。

三类卡（地点/物品/规则）共用同一套候选结构，靠 type 区分；
所有结果都只是候选，正式写入仍走 SettingCardService。
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 卡片类型
CARD_KINDS = ("location", "item", "rule")

# 提取建议动作：新建 / 合并到已有卡 / 跳过
CARD_ACTIONS = ("create", "merge", "skip")

# 冲突严重级别
CONFLICT_SEVERITIES = ("blocking", "warning", "notice")


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


def _to_text_map(value: Any) -> Any:
    """把任意键值映射规范为字符串映射。"""
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item).strip()
        for key, item in value.items()
        if item is not None and str(item).strip()
    }


class CardAISchemaModel(BaseModel):
    """允许额外字段的卡片 AI 输出基础模型。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class CardCandidateSchema(CardAISchemaModel):
    """单张卡片候选。"""

    type: str = Field(default="location")
    name: str = Field(default="")
    aliases: List[str] = Field(default_factory=list)
    fields: Dict[str, str] = Field(default_factory=dict)
    current_state: str = Field(default="")
    importance: int = Field(default=3, ge=1, le=5)
    reason: str = Field(default="")
    conflicts: List[str] = Field(default_factory=list)
    source: str = Field(default="ai_generated")

    @field_validator("aliases", "conflicts", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化字符串数组。"""
        return _to_text_list(value)

    @field_validator("fields", mode="before")
    @classmethod
    def normalize_fields(cls, value: Any) -> Any:
        """规范化扩展字段映射。"""
        return _to_text_map(value)


class CardGenerateResultSchema(CardAISchemaModel):
    """卡片生成候选集合。"""

    candidates: List[CardCandidateSchema] = Field(default_factory=list)
    notes: str = Field(default="")


class CardCompleteResultSchema(CardAISchemaModel):
    """卡片补全候选：只包含待写入的空字段。"""

    filled_fields: Dict[str, str] = Field(default_factory=dict)
    notes: str = Field(default="")
    conflicts: List[str] = Field(default_factory=list)

    @field_validator("filled_fields", mode="before")
    @classmethod
    def normalize_fields(cls, value: Any) -> Any:
        """规范化补全字段映射。"""
        return _to_text_map(value)

    @field_validator("conflicts", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化冲突说明数组。"""
        return _to_text_list(value)


class CardExtractItemSchema(CardAISchemaModel):
    """从小说内容中提取到的卡片候选。"""

    type: str = Field(default="location")
    name: str = Field(default="")
    aliases: List[str] = Field(default_factory=list)
    fields: Dict[str, str] = Field(default_factory=dict)
    evidence: str = Field(default="")
    duplicate_of: str = Field(default="")
    new_fields: List[str] = Field(default_factory=list)
    state_change: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    suggested_action: str = Field(default="create")

    @field_validator("aliases", "new_fields", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化字符串数组。"""
        return _to_text_list(value)

    @field_validator("fields", mode="before")
    @classmethod
    def normalize_fields(cls, value: Any) -> Any:
        """规范化扩展字段映射。"""
        return _to_text_map(value)


class CardExtractResultSchema(CardAISchemaModel):
    """卡片提取候选集合。"""

    items: List[CardExtractItemSchema] = Field(default_factory=list)
    notes: str = Field(default="")


class CardConflictItemSchema(CardAISchemaModel):
    """卡片能力/状态冲突项。"""

    severity: str = Field(default="notice")
    message: str = Field(default="")
    related_card_ids: List[str] = Field(default_factory=list)
    suggestion: str = Field(default="")

    @field_validator("related_card_ids", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化关联卡片 ID 数组。"""
        return _to_text_list(value)


class CardConflictResultSchema(CardAISchemaModel):
    """卡片冲突检查结果。"""

    conflicts: List[CardConflictItemSchema] = Field(default_factory=list)
    notes: str = Field(default="")


class CardRewriteFieldSchema(CardAISchemaModel):
    """单个字段的改写候选，用于字段级 old/new 差异展示。"""

    field: str = Field(default="")
    old_value: str = Field(default="")
    new_value: str = Field(default="")
    reason: str = Field(default="")
    warnings: List[str] = Field(default_factory=list)

    @field_validator("warnings", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化告警数组。"""
        return _to_text_list(value)


class CardRewriteResultSchema(CardAISchemaModel):
    """卡片改写候选补丁。

    只描述"哪些字段准备怎么改"，不携带整卡覆盖结果，
    正式写入必须经过统一采纳接口的白名单与版本校验。
    """

    target_card_id: str = Field(default="")
    target_card_type: str = Field(default="")
    changed_fields: List[CardRewriteFieldSchema] = Field(default_factory=list)
    preserved_fields: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    notes: str = Field(default="")

    @field_validator("preserved_fields", "warnings", "conflicts", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化字符串数组。"""
        return _to_text_list(value)


class CardMergeFieldDiffSchema(CardAISchemaModel):
    """合并预览中的单个字段差异。"""

    field: str = Field(default="")
    current_value: str = Field(default="")
    incoming_value: str = Field(default="")
    selected: bool = Field(default=False)


class CardMergePreviewSchema(CardAISchemaModel):
    """把抽取候选合并进已有卡片时的字段级差异预览。"""

    candidate_name: str = Field(default="")
    target_card_id: str = Field(default="")
    target_card_name: str = Field(default="")
    field_diffs: List[CardMergeFieldDiffSchema] = Field(default_factory=list)
    new_fields: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    @field_validator("new_fields", "warnings", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化字符串数组。"""
        return _to_text_list(value)


class EvidenceRangeSchema(CardAISchemaModel):
    """正文证据定位，供前端高亮并跳转到具体位置。"""

    text: str = Field(default="")
    start: int | None = Field(default=None)
    end: int | None = Field(default=None)


class CardStateChangeSchema(CardAISchemaModel):
    """实体事实变化候选，统一进入 story_events 待确认。"""

    entity_type: str = Field(default="")
    entity_id: str = Field(default="")
    entity_name: str = Field(default="")
    change_type: str = Field(default="state_change")
    before: Dict[str, str] = Field(default_factory=dict)
    after: Dict[str, str] = Field(default_factory=dict)
    evidence: EvidenceRangeSchema = Field(default_factory=EvidenceRangeSchema)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    note: str = Field(default="")

    @field_validator("before", "after", mode="before")
    @classmethod
    def normalize_fields(cls, value: Any) -> Any:
        """规范化前后事实映射。"""
        return _to_text_map(value)


class NewCardDiscoverySchema(CardAISchemaModel):
    """正文中新发现的卡片候选。"""

    type: str = Field(default="location")
    name: str = Field(default="")
    aliases: List[str] = Field(default_factory=list)
    fields: Dict[str, str] = Field(default_factory=dict)
    evidence: EvidenceRangeSchema = Field(default_factory=EvidenceRangeSchema)
    duplicate_of: str = Field(default="")
    suggested_action: str = Field(default="create")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化别名数组。"""
        return _to_text_list(value)

    @field_validator("fields", mode="before")
    @classmethod
    def normalize_fields(cls, value: Any) -> Any:
        """规范化扩展字段映射。"""
        return _to_text_map(value)


class CardSupplementSchema(CardAISchemaModel):
    """已有卡片的补充候选。"""

    card_id: str = Field(default="")
    card_type: str = Field(default="")
    card_name: str = Field(default="")
    added_fields: Dict[str, str] = Field(default_factory=dict)
    evidence: EvidenceRangeSchema = Field(default_factory=EvidenceRangeSchema)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="")

    @field_validator("added_fields", mode="before")
    @classmethod
    def normalize_fields(cls, value: Any) -> Any:
        """规范化补充字段映射。"""
        return _to_text_map(value)


class ChapterSettingAnalysisSchema(CardAISchemaModel):
    """章节正文设定分析结果。

    全部内容都是候选，后端只写入生成记录与待确认事件，不直接改正式实体。
    """

    new_cards: List[NewCardDiscoverySchema] = Field(default_factory=list)
    supplements: List[CardSupplementSchema] = Field(default_factory=list)
    conflicts: List[CardConflictItemSchema] = Field(default_factory=list)
    state_changes: List[CardStateChangeSchema] = Field(default_factory=list)
    summary: str = Field(default="")


class TextRevisionItemSchema(CardAISchemaModel):
    """单条正文修改建议候选。"""

    before_text: str = Field(default="")
    after_text: str = Field(default="")
    reason: str = Field(default="")
    revision_type: str = Field(default="supplement")
    warnings: List[str] = Field(default_factory=list)

    @field_validator("warnings", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化告警数组。"""
        return _to_text_list(value)


class TextRevisionResultSchema(CardAISchemaModel):
    """正文修改建议生成结果。"""

    revisions: List[TextRevisionItemSchema] = Field(default_factory=list)


__all__ = [
    "CARD_KINDS",
    "CARD_ACTIONS",
    "CONFLICT_SEVERITIES",
    "CardAISchemaModel",
    "CardCandidateSchema",
    "CardGenerateResultSchema",
    "CardCompleteResultSchema",
    "CardExtractItemSchema",
    "CardExtractResultSchema",
    "CardConflictItemSchema",
    "CardConflictResultSchema",
    "CardRewriteFieldSchema",
    "CardRewriteResultSchema",
    "CardMergeFieldDiffSchema",
    "CardMergePreviewSchema",
    "EvidenceRangeSchema",
    "CardStateChangeSchema",
    "NewCardDiscoverySchema",
    "CardSupplementSchema",
    "ChapterSettingAnalysisSchema",
    "TextRevisionItemSchema",
    "TextRevisionResultSchema",
]
