"""章节 API 路由：章节 CRUD、状态流转、自动保存（乐观锁）、上下章导航。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.services.novel.chapter_service import ChapterService

router = APIRouter(prefix="/api/chapters", tags=["chapters"])
service = ChapterService()


class CreateChapterRequest(BaseModel):
    volume_id: Optional[str] = None
    number: Optional[int] = None
    title: Optional[str] = None
    content: Optional[str] = ""
    status: Optional[str] = "draft"
    blueprint_snapshot: Optional[Dict[str, Any]] = None
    linked_character_ids: Optional[List[str]] = None
    linked_location_ids: Optional[List[str]] = None
    linked_item_ids: Optional[List[str]] = None
    linked_rule_ids: Optional[List[str]] = None
    summary: Optional[str] = ""
    unresolved_threads: Optional[List[str]] = None


class SaveChapterRequest(BaseModel):
    volume_id: Optional[str] = None
    number: Optional[int] = None
    title: Optional[str] = None
    content: Optional[str] = None
    status: Optional[str] = None
    blueprint_snapshot: Optional[Dict[str, Any]] = None
    linked_character_ids: Optional[List[str]] = None
    linked_location_ids: Optional[List[str]] = None
    linked_item_ids: Optional[List[str]] = None
    linked_rule_ids: Optional[List[str]] = None
    summary: Optional[str] = None
    unresolved_threads: Optional[List[str]] = None


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


@router.get("/status-meta")
async def status_meta():
    return service.status_meta()


@router.get("/novel/{novel_id}")
async def list_chapters(novel_id: str, volume_id: Optional[str] = None, include_deleted: bool = False):
    try:
        items = await service.list_chapters(novel_id, volume_id, include_deleted)
        return {"data": items, "total": len(items)}
    except Exception as e:
        _handle(e)


@router.post("/novel/{novel_id}/create")
async def create_chapter(novel_id: str, req: CreateChapterRequest):
    try:
        return await service.create_chapter(novel_id, req.model_dump(exclude_unset=True))
    except Exception as e:
        _handle(e)


@router.get("/{chapter_id}")
async def get_chapter(chapter_id: str, include_deleted: bool = False):
    try:
        return await service.get_chapter(chapter_id, include_deleted)
    except Exception as e:
        _handle(e)


@router.put("/{chapter_id}")
async def save_chapter(chapter_id: str, req: SaveChapterRequest, expected_version: Optional[int] = None):
    try:
        data = req.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(400, "没有要保存的字段")
        return await service.save_chapter(chapter_id, data, expected_version)
    except Exception as e:
        _handle(e)


@router.post("/{chapter_id}/finalize")
async def finalize_chapter(chapter_id: str):
    try:
        return await service.finalize_chapter(chapter_id)
    except Exception as e:
        _handle(e)


@router.post("/{chapter_id}/reopen")
async def reopen_chapter(chapter_id: str):
    try:
        return await service.reopen_chapter(chapter_id)
    except Exception as e:
        _handle(e)


@router.delete("/{chapter_id}")
async def delete_chapter(chapter_id: str):
    try:
        await service.delete_chapter(chapter_id)
        return {"success": True, "message": "章节已移入回收站"}
    except Exception as e:
        _handle(e)


@router.post("/{chapter_id}/restore")
async def restore_chapter(chapter_id: str):
    try:
        return await service.restore_chapter(chapter_id)
    except Exception as e:
        _handle(e)


@router.get("/novel/{novel_id}/navigation/{number}")
async def navigation(novel_id: str, number: int):
    try:
        return await service.get_navigation(novel_id, number)
    except Exception as e:
        _handle(e)
