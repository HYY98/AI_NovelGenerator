"""设定卡 API 路由：CRUD + 回收站 + 乐观锁。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.services.novel.setting_card_service import SettingCardService

router = APIRouter(prefix="/api/setting-cards", tags=["setting-cards"])
service = SettingCardService()


class CreateCardRequest(BaseModel):
    type: str
    name: str = Field(..., min_length=1, max_length=200)
    aliases: Optional[List[str]] = None
    fields: Optional[Dict[str, Any]] = Field(default_factory=dict)
    enabled: Optional[bool] = True
    importance: Optional[int] = 3
    first_appearance_chapter: Optional[str] = ""
    current_state: Optional[str] = ""
    tags: Optional[List[str]] = None
    sort_order: Optional[int] = 0


class UpdateCardRequest(BaseModel):
    name: Optional[str] = None
    aliases: Optional[List[str]] = None
    fields: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    importance: Optional[int] = None
    first_appearance_chapter: Optional[str] = None
    current_state: Optional[str] = None
    tags: Optional[List[str]] = None
    sort_order: Optional[int] = None


def _handle(e):
    if isinstance(e, HTTPException):
        raise e
    if isinstance(e, NotFoundError):
        raise HTTPException(404, str(e))
    if isinstance(e, InvalidIdError):
        raise HTTPException(400, str(e))
    if isinstance(e, DuplicateKeyError):
        raise HTTPException(409, str(e))
    raise HTTPException(500, str(e))


@router.get("/types")
async def get_types():
    return service.get_card_types()


@router.get("/stats/{novel_id}")
async def get_stats(novel_id: str):
    try:
        return await service.get_stats(novel_id)
    except Exception as e:
        _handle(e)


@router.get("/{novel_id}")
async def list_cards(
    novel_id: str,
    type: Optional[str] = None,
    query: Optional[str] = "",
    include_deleted: bool = False,
    enabled_only: bool = False,
):
    try:
        return await service.list_cards(novel_id, type, query, include_deleted, enabled_only)
    except Exception as e:
        _handle(e)


@router.post("/{novel_id}")
async def create_card(novel_id: str, req: CreateCardRequest):
    try:
        return await service.create_card(novel_id, req.model_dump(exclude_unset=True))
    except Exception as e:
        _handle(e)


@router.get("/{novel_id}/{card_id}")
async def get_card(novel_id: str, card_id: str, include_deleted: bool = False):
    try:
        return await service.get_card(novel_id, card_id, include_deleted)
    except Exception as e:
        _handle(e)


@router.put("/{novel_id}/{card_id}")
async def update_card(
    novel_id: str,
    card_id: str,
    req: UpdateCardRequest,
    expected_version: Optional[int] = None,
    confirm_locked_fields: bool = False,
):
    """更新卡片；修改 name / current_state / is_hard_rule 需显式确认。"""
    try:
        data = req.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(400, "没有要更新的字段")
        return await service.update_card(
            novel_id,
            card_id,
            data,
            expected_version,
            allow_protected_fields=confirm_locked_fields,
        )
    except Exception as e:
        _handle(e)


@router.delete("/{novel_id}/{card_id}")
async def delete_card(novel_id: str, card_id: str):
    try:
        await service.delete_card(novel_id, card_id)
        return {"success": True, "message": "已移入回收站"}
    except Exception as e:
        _handle(e)


@router.post("/{novel_id}/{card_id}/restore")
async def restore_card(novel_id: str, card_id: str):
    try:
        return await service.restore_card(novel_id, card_id)
    except Exception as e:
        _handle(e)


@router.delete("/{novel_id}/{card_id}/hard")
async def hard_delete_card(novel_id: str, card_id: str):
    try:
        await service.hard_delete_card(novel_id, card_id)
        return {"success": True, "message": "已彻底删除"}
    except Exception as e:
        _handle(e)
