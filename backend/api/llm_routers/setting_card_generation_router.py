"""设定卡 AI 路由：生成、提取、补全与冲突检查。

所有接口只返回候选，用户采纳后仍需调用 /api/setting-cards 正式保存。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.services.llm.setting_card_generation_service import (
    setting_card_generation_service,
)

router = APIRouter(prefix="/api/llm/setting-cards", tags=["setting-card-ai"])
logger = logging.getLogger(__name__)

CardType = Literal["location", "item", "rule"]


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


class CardAIRequestBase(BaseModel):
    """设定卡 AI 请求公共字段。"""

    novel_id: str = Field(min_length=1)
    # 由路由按路径参数覆盖
    card_id: str = Field(default="")
    chapter_id: str = Field(default="")
    request_id: str = Field(default="", max_length=80)
    user_prompt: str = Field(default="", max_length=4000)
    provider: Optional[str] = Field(default=None)
    use_stream: bool = False
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    top_p: Optional[float] = Field(default=None, ge=0, le=1)
    max_tokens: Optional[int] = Field(default=None, gt=0)
    presence_penalty: Optional[float] = Field(default=None, ge=-2, le=2)
    frequency_penalty: Optional[float] = Field(default=None, ge=-2, le=2)
    system_prompt: Optional[str] = Field(default=None)


class CardGenerateRequest(CardAIRequestBase):
    """AI 生成卡片请求。"""

    type: CardType


class CardCompleteRequest(CardAIRequestBase):
    """AI 补全卡片请求：默认只补空字段。"""

    card_id: str = Field(min_length=1)
    fill_empty_only: bool = True
    locked_fields: List[str] = Field(default_factory=list, max_length=40)


class CardExtractRequest(CardAIRequestBase):
    """从小说内容提取卡片请求。"""

    source_document: Literal["worldview", "novel_info", "chapter", "card"] = "chapter"
    source_text: str = Field(default="", max_length=100000)


class CardConflictRequest(CardAIRequestBase):
    """卡片冲突检查请求。"""

    card_id: str = Field(min_length=1)


@router.post("/generate")
async def generate_setting_card(request: CardGenerateRequest):
    """生成卡片候选（不写入正式卡片）。"""
    try:
        return await setting_card_generation_service.generate_card(request)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/{card_id}/complete")
async def complete_setting_card(card_id: str, request: CardCompleteRequest):
    """补全指定卡片的空白字段，只返回可写入的字段候选。"""
    payload = request.model_copy(update={"card_id": card_id})
    try:
        return await setting_card_generation_service.complete_card(payload)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/extract")
async def extract_setting_cards(request: CardExtractRequest):
    """从世界观、小说信息或章节正文中提取卡片候选。"""
    try:
        return await setting_card_generation_service.extract_cards(request)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/check-conflicts")
async def check_setting_card_conflicts(request: CardConflictRequest):
    """检查卡片与全书设定是否存在冲突。"""
    try:
        return await setting_card_generation_service.check_conflicts(request)
    except Exception as exc:
        raise _http_exception(exc) from exc


__all__ = ["router"]
