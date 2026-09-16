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


class TextRevisionActionRequest(BaseModel):
    """正文修改建议确认或拒绝请求。"""

    novel_id: str = Field(min_length=1)
    after_text: Optional[str] = Field(default=None, max_length=20000)
    reason: Optional[str] = Field(default="", max_length=2000)


@router.get("/{chapter_id}/text-revisions")
async def list_text_revisions(chapter_id: str, novel_id: str, status: Optional[str] = None):
    """列出章节当前的正文修改建议候选。"""
    from backend.db.repositories.text_revision_repository import text_revision_repo

    try:
        return await text_revision_repo.find_by_chapter(novel_id, chapter_id, status=status)
    except Exception as e:
        _handle(e)


@router.post("/{chapter_id}/text-revisions/{revision_id}/accept")
async def accept_text_revision(chapter_id: str, revision_id: str, request: TextRevisionActionRequest):
    """确认一条修改建议并写回正文。

    写回前会校验建议归属、状态、章节版本与原文可定位性，任一失败都不改正文。
    """
    from backend.services.llm.text_revision_service import text_revision_service

    try:
        return await text_revision_service.apply(
            request.novel_id,
            revision_id,
            after_text=request.after_text,
        )
    except Exception as e:
        _handle(e)


@router.post("/{chapter_id}/text-revisions/{revision_id}/reject")
async def reject_text_revision(chapter_id: str, revision_id: str, request: TextRevisionActionRequest):
    """拒绝一条修改建议，不改动正文。"""
    from backend.db.repositories.text_revision_repository import text_revision_repo

    try:
        revision = await text_revision_repo.get_revision(request.novel_id, revision_id)
        if str(revision.get("chapter_id") or "") != chapter_id:
            raise NotFoundError(f"修改建议 {revision_id} 不属于章节 {chapter_id}")
        await text_revision_repo.update_status(request.novel_id, revision_id, "rejected")
        return await text_revision_repo.get_revision(request.novel_id, revision_id)
    except Exception as e:
        _handle(e)


@router.get("/novel/{novel_id}/navigation/{number}")
async def navigation(novel_id: str, number: int):
    try:
        return await service.get_navigation(novel_id, number)
    except Exception as e:
        _handle(e)
