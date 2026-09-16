"""实体内容版本路由（v2.0 T10 版本基础）。

第一批提供章节与设定卡的版本列表和还原；还原是写新快照，不删除历史。
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

from backend.api.error_contract import to_http_exception
from backend.db.repositories.chapter_repository import ChapterRepository
from backend.services.novel.entity_version_service import entity_version_service

novel_version_router = APIRouter(prefix="/api/novels", tags=["entity-versions"])
chapter_version_router = APIRouter(prefix="/api/chapters", tags=["entity-versions"])

chapter_repo = ChapterRepository()


class RestoreRequest(BaseModel):
    """还原请求。"""

    request_id: str = Field(min_length=1, max_length=120)
    reason: Optional[str] = Field(default="", max_length=2000)


class VersionedEntity(BaseModel):
    """内容版本 DTO（对齐 v2.0 接口契约 6.7）。

    后端 1 号未最终交付前，先按契约字段 mock；列表与 diff 都据此形态返回。
    """

    revision_id: str
    entity_type: str
    entity_id: str
    chapter_id: str = ""
    operation_type: str = ""
    source: str = "system"
    summary: str = ""
    content_snapshot: Dict[str, Any] = Field(default_factory=dict)
    parent_revision_id: str = ""
    created_at: Optional[str] = None
    is_current: bool = False
    version: int = 1
    content_hash: str = ""


@novel_version_router.get("/{novel_id}/entities/{entity_type}/{entity_id}/versions")
async def list_entity_versions(
    novel_id: str,
    entity_type: str,
    entity_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
):
    """列出实体内容版本。"""
    try:
        return await entity_version_service.list_versions(
            novel_id, entity_type, entity_id, limit=limit, skip=skip
        )
    except Exception as exc:
        raise to_http_exception(exc)


@novel_version_router.post(
    "/{novel_id}/entities/{entity_type}/{entity_id}/versions/{revision_id}/restore"
)
async def restore_entity_version(
    novel_id: str,
    entity_type: str,
    entity_id: str,
    revision_id: str,
    req: RestoreRequest,
):
    """还原实体到指定版本（写新快照）。"""
    try:
        return await entity_version_service.restore_version(
            novel_id,
            entity_type,
            entity_id,
            revision_id,
            request_id=req.request_id,
            reason=req.reason or "",
        )
    except Exception as exc:
        raise to_http_exception(exc)


@chapter_version_router.get("/{chapter_id}/versions")
async def list_chapter_versions(
    chapter_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
):
    """列出章节内容版本。"""
    try:
        chapter = await chapter_repo.get_chapter(chapter_id)
        novel_id = str(chapter.get("novel_id") or "")
        entity_id = str(chapter.get("chapter_id") or chapter.get("_id") or "")
        return await entity_version_service.list_versions(
            novel_id, "chapter", entity_id, limit=limit, skip=skip
        )
    except Exception as exc:
        raise to_http_exception(exc)


@chapter_version_router.post("/{chapter_id}/versions/{revision_id}/restore")
async def restore_chapter_version(chapter_id: str, revision_id: str, req: RestoreRequest):
    """还原章节正文到指定版本（写新快照）。"""
    try:
        chapter = await chapter_repo.get_chapter(chapter_id)
        novel_id = str(chapter.get("novel_id") or "")
        entity_id = str(chapter.get("chapter_id") or chapter.get("_id") or "")
        return await entity_version_service.restore_version(
            novel_id,
            "chapter",
            entity_id,
            revision_id,
            request_id=req.request_id,
            reason=req.reason or "",
        )
    except Exception as exc:
        raise to_http_exception(exc)


@chapter_version_router.get("/{chapter_id}/versions/diff")
async def diff_chapter_versions(
    chapter_id: str,
    from_revision_id: str = Query(..., description="基线版本业务 ID"),
    to_revision_id: str = Query(..., description="目标版本业务 ID"),
):
    """对比章节两个版本的内容差异（字段级 + 正文行级）。"""
    try:
        chapter = await chapter_repo.get_chapter(chapter_id)
        novel_id = str(chapter.get("novel_id") or "")
        entity_id = str(chapter.get("chapter_id") or chapter.get("_id") or "")
        return await entity_version_service.diff_versions(
            novel_id, "chapter", entity_id, from_revision_id, to_revision_id
        )
    except Exception as exc:
        raise to_http_exception(exc)


@novel_version_router.get(
    "/{novel_id}/entities/{entity_type}/{entity_id}/versions/diff"
)
async def diff_entity_versions(
    novel_id: str,
    entity_type: str,
    entity_id: str,
    from_revision_id: str = Query(..., description="基线版本业务 ID"),
    to_revision_id: str = Query(..., description="目标版本业务 ID"),
):
    """对比任意受支持实体两个版本的内容差异。"""
    try:
        return await entity_version_service.diff_versions(
            novel_id, entity_type, entity_id, from_revision_id, to_revision_id
        )
    except Exception as exc:
        raise to_http_exception(exc)
