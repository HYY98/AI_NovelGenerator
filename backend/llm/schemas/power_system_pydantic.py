"""结构化战力体系的 Pydantic Schema。

既用于 AI 结构化输出校验，也用于正式保存前的字段校验。
AI 只产出候选，是否写入正式体系由服务层决定。
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PowerSystemSchemaModel(BaseModel):
    """允许额外字段的战力体系 AI 输出基础模型。"""

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


class PowerLevelSchema(PowerSystemSchemaModel):
    """战力体系中的一个等级或境界。"""

    name: str = Field(default="")
    order: int = Field(default=0, ge=0)
    description: str = Field(default="")
    requirements: List[str] = Field(default_factory=list)
    abilities: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)

    @field_validator("requirements", "abilities", "limitations", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化字符串数组。"""
        return _to_text_list(value)


class PowerResourceSchema(PowerSystemSchemaModel):
    """能量资源定义：名称、来源、消耗与恢复。"""

    name: str = Field(default="")
    source: str = Field(default="")
    consumption: str = Field(default="")
    recovery: str = Field(default="")


class PowerCounterSchema(PowerSystemSchemaModel):
    """克制关系定义。"""

    source: str = Field(default="")
    target: str = Field(default="")
    description: str = Field(default="")


class PowerSystemSchema(PowerSystemSchemaModel):
    """完整战力体系定义。"""

    name: str = Field(default="")
    description: str = Field(default="")
    levels: List[PowerLevelSchema] = Field(default_factory=list)
    power_dimensions: List[str] = Field(default_factory=list)
    resource: PowerResourceSchema | None = Field(default=None)
    restrictions: List[str] = Field(default_factory=list)
    special_rules: List[str] = Field(default_factory=list)
    counters: List[PowerCounterSchema] = Field(default_factory=list)

    @field_validator(
        "power_dimensions", "restrictions", "special_rules", mode="before"
    )
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化字符串数组。"""
        return _to_text_list(value)

    def normalized_levels(self) -> List[Dict[str, Any]]:
        """返回按 order 升序、order 缺失时按下标补齐的等级列表。"""
        levels = [level.model_dump() for level in self.levels]
        for index, level in enumerate(levels):
            if not level.get("order"):
                level["order"] = index
        levels.sort(key=lambda item: item["order"])
        return levels


class PowerSystemGenerateResultSchema(PowerSystemSchemaModel):
    """AI 生成的战力体系候选。"""

    power_system: PowerSystemSchema = Field(default_factory=PowerSystemSchema)
    notes: str = Field(default="")
    warnings: List[str] = Field(default_factory=list)

    @field_validator("warnings", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any) -> Any:
        """规范化告警数组。"""
        return _to_text_list(value)


__all__ = [
    "PowerSystemSchemaModel",
    "PowerLevelSchema",
    "PowerResourceSchema",
    "PowerCounterSchema",
    "PowerSystemSchema",
    "PowerSystemGenerateResultSchema",
]
