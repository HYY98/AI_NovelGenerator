"""设定卡候选统一采纳路由。

所有 AI 候选（新建、合并、改写）的正式写入都收敛到这一个入口，
保证归属校验、版本校验、字段白名单与采纳标记的执行顺序一致。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.services.novel.setting_card_accept_service import (
    setting_card_accept_service,
)

router = APIRouter(prefix="/api/llm/setting-cards", tags=["setting-card-accept"])
logger = logging.getLogger(__name__)

AcceptAction = Literal["create", "merge", "rewrite", "reject"]


def _http_exception(exc: Exception) -> HTTPException:
    """把服务层异常转换为稳定的 HTTP 错误。"""
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, InvalidIdError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, DuplicateKeyError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


class SettingCardAcceptRequest(BaseModel):
    """统一采纳请求。"""

    # 路径不再携带 novel_id，改由请求体声明并做归属校验
    novel_id: str = Field(min_length=1, max_length=80)
    generation_id: str = Field(min_length=1, max_length=80)
    action: AcceptAction
    target_card_id: str = Field(default="", max_length=80)
    accepted_fields: Dict[str, Any] = Field(default_factory=dict)
    blueprint_version: Optional[int] = Field(default=None, ge=1)
    chapter_version: Optional[int] = Field(default=None, ge=1)
    expected_version: Optional[int] = Field(default=None, ge=1)
    create_state_change_events: bool = True
    # 显式确认把规则卡设为硬规则；不确认时 is_hard_rule 一律回落为 false
    confirm_hard_rule: bool = False
    outline_version: Optional[int] = Field(default=None, ge=1)


@router.post("/accept")
async def accept_setting_card_candidate(request: SettingCardAcceptRequest):
    """采纳一条设定卡候选，正式写入卡片并记录采纳状态。

    写入失败不会把候选标记为已采纳，避免产生不一致状态。
    """
    try:
        return await setting_card_accept_service.accept(
            request.novel_id,
            generation_id=request.generation_id,
            action=request.action,
            target_card_id=request.target_card_id,
            accepted_fields=request.accepted_fields,
            blueprint_version=request.blueprint_version,
            chapter_version=request.chapter_version,
            outline_version=request.outline_version,
            expected_version=request.expected_version,
            create_state_change_events=request.create_state_change_events,
            confirm_hard_rule=request.confirm_hard_rule,
        )
    except Exception as exc:
        raise _http_exception(exc) from exc
