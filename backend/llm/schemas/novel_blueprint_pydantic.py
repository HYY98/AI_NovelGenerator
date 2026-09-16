"""创作蓝图与大纲的 Pydantic Schema。

蓝图整理、补全与大纲生成的结果都必须先经过 Schema 校验，
非法实体 ID、未知卡片类型和非法状态一律转为告警并丢弃，不写入正式数据。
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.llm.schemas.power_system_pydantic import PowerSystemSchema

# 蓝图生命周期状态
BLUEPRINT_STATUSES = ("draft", "pending_confirmation", "confirmed", "superseded")


class BlueprintSchemaModel(BaseModel):
    """允许额外字段的蓝图 AI 输出基础模型。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _to_text_list(value: Any) -> Any:
    """把字符串或数组统一成去空、去重、保序的字符串数组。"""
    if value is None:
        return []
    if isinstance(value, str):
        parts = [
            seg.strip()
            for seg in value.replace("；", "\n").replace(";", "\n").split("\n")
        ]
        return [seg for seg in parts if seg]
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
        return out
    return [str(value).strip()] if str(value).strip() else []


class BlueprintSuggestionSchema(BlueprintSchemaModel):
    """AI 对蓝图的补全候选，不视为已确认事实。"""

    target: str = Field(default="")
    field: str = Field(default="")
    value: str = Field(default="")
    reason: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class BlueprintConflictSchema(BlueprintSchemaModel):
    """蓝图整理阶段发现的冲突。"""

    severity: str = Field(default="notice")
    category: str = Field(default="")
    entity_type: str = Field(default="")
    entity_id: str = Field(default="")
    message: str = Field(default="")
    suggestion: str = Field(default="")
    can_ignore: bool = Field(default=True)


class BlueprintQuestionSchema(BlueprintSchemaModel):
    """需要用户补充确认的问题。"""

    question: str = Field(default="")
    target: str = Field(default="")
    options: List[str] = Field(default_factory=list)

    @field_validator("options", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化选项数组。"""
        return _to_text_list(value)


class BlueprintPrepareResultSchema(BlueprintSchemaModel):
    """蓝图整理结果候选。"""

    plot_summary: str = Field(default="")
    worldview: str = Field(default="")
    power_system: PowerSystemSchema | None = Field(default=None)
    suggestions: List[BlueprintSuggestionSchema] = Field(default_factory=list)
    conflicts: List[BlueprintConflictSchema] = Field(default_factory=list)
    questions: List[BlueprintQuestionSchema] = Field(default_factory=list)
    notes: str = Field(default="")


class OutlineChapterSchema(BlueprintSchemaModel):
    """大纲中的单个章节。"""

    title: str = Field(default="")
    summary: str = Field(default="")
    goals: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    character_ids: List[str] = Field(default_factory=list)
    faction_ids: List[str] = Field(default_factory=list)
    setting_card_ids: List[str] = Field(default_factory=list)
    power_changes: List[str] = Field(default_factory=list)
    foreshadowing: List[str] = Field(default_factory=list)

    @field_validator(
        "goals",
        "conflicts",
        "character_ids",
        "faction_ids",
        "setting_card_ids",
        "power_changes",
        "foreshadowing",
        mode="before",
    )
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化字符串数组。"""
        return _to_text_list(value)


class OutlineVolumeSchema(BlueprintSchemaModel):
    """大纲中的单个卷。"""

    title: str = Field(default="")
    summary: str = Field(default="")
    chapters: List[OutlineChapterSchema] = Field(default_factory=list)


class NovelOutlineResultSchema(BlueprintSchemaModel):
    """卷章大纲生成结果候选。"""

    volumes: List[OutlineVolumeSchema] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    conflicts: List[BlueprintConflictSchema] = Field(default_factory=list)

    @field_validator("warnings", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化告警数组。"""
        return _to_text_list(value)

    def to_plain_dict(self) -> Dict[str, Any]:
        """转成可写入生成记录的普通字典。"""
        return self.model_dump()


__all__ = [
    "BLUEPRINT_STATUSES",
    "BlueprintSchemaModel",
    "BlueprintSuggestionSchema",
    "BlueprintConflictSchema",
    "BlueprintQuestionSchema",
    "BlueprintPrepareResultSchema",
    "OutlineChapterSchema",
    "OutlineVolumeSchema",
    "NovelOutlineResultSchema",
]
