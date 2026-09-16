"""候选采纳通用请求/响应 DTO（v2.0 T02）。

统一采纳入口（卡片 / 正文 / 角色）共用同一套结构，路由层按记录类型分发到
对应的业务写入实现；这里只定义契约，不做实体写入。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 候选决定：接受 / 拒绝 / 忽略
CANDIDATE_DECISIONS = ("accept", "reject", "ignore")
# 采纳动作：新建 / 合并 / 改写
CANDIDATE_ACTIONS = ("create", "merge", "rewrite")
# 正文操作：替换 / 插入 / 删除
TEXT_OPERATIONS = ("replace", "insert", "delete")


class CandidateAcceptRequest(BaseModel):
    """候选采纳统一请求。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    novel_id: str = Field(min_length=1, max_length=80)
    generation_id: str = Field(min_length=1, max_length=80)
    # 多候选记录必填；单候选记录可为空，表示整记录即候选
    candidate_id: str = Field(default="", max_length=120)
    decision: str = Field(default="accept")
    request_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)

    # accept 时的动作；reject/ignore 时忽略
    action: Optional[str] = Field(default=None, max_length=20)

    # 设定卡采纳
    target_card_id: str = Field(default="", max_length=80)
    accepted_fields: Dict[str, Any] = Field(default_factory=dict)
    expected_version: Optional[int] = None
    confirm_hard_rule: bool = Field(default=False)

    # 正文修正
    after_text: Optional[str] = Field(default=None, max_length=20000)
    expected_chapter_version: Optional[int] = None
    expected_content_hash: str = Field(default="", max_length=128)
    operation: str = Field(default="replace", max_length=20)
    occurrence: Optional[int] = None

    @field_validator("decision")
    @classmethod
    def _check_decision(cls, value: str) -> str:
        text = str(value or "accept").strip().lower()
        if text not in CANDIDATE_DECISIONS:
            raise ValueError(f"非法的候选决定: {value}，支持 accept/reject/ignore")
        return text

    @field_validator("action")
    @classmethod
    def _check_action(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        text = str(value).strip().lower()
        if text not in CANDIDATE_ACTIONS:
            raise ValueError(f"非法的采纳动作: {value}，支持 create/merge/rewrite")
        return text

    @field_validator("operation")
    @classmethod
    def _check_operation(cls, value: str) -> str:
        text = str(value or "replace").strip().lower()
        if text not in TEXT_OPERATIONS:
            raise ValueError(f"非法的正文操作: {value}，支持 replace/insert/delete")
        return text


class CandidateAcceptResponse(BaseModel):
    """候选采纳统一响应。"""

    model_config = ConfigDict(extra="allow")

    generation_id: str
    candidate_id: str = ""
    status: str = "accepted"          # 候选终态：accepted/rejected/ignored
    operation_id: str = ""
    replayed: bool = False            # true=命中幂等重放
    action: Optional[str] = None
    card: Optional[Dict[str, Any]] = None
    chapter: Optional[Dict[str, Any]] = None
    events_created: int = 0
    warnings: List[str] = Field(default_factory=list)


__all__ = [
    "CANDIDATE_DECISIONS",
    "CANDIDATE_ACTIONS",
    "TEXT_OPERATIONS",
    "CandidateAcceptRequest",
    "CandidateAcceptResponse",
]
