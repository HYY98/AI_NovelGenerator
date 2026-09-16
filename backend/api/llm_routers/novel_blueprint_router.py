"""创作蓝图 AI 路由：整理、补全、战力体系生成与卷章大纲生成。

所有接口只返回候选，蓝图确认、战力体系正式写入与章节创建都由各自的服务完成。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.services.llm.novel_blueprint_service import novel_blueprint_service
from backend.services.llm.text_revision_service import text_revision_service

router = APIRouter(prefix="/api/llm/novels", tags=["novel-blueprint-ai"])
logger = logging.getLogger(__name__)


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


class BlueprintRequestBase(BaseModel):
    """蓝图 AI 请求公共字段。"""

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


class BlueprintPrepareRequest(BlueprintRequestBase):
    """蓝图整理请求。"""

    # 增量新增：新建小说流程还没有 novel_id，允许为空
    novel_id: Optional[str] = Field(default=None)
    generation_mode: str = Field(default="guided")
    plot_summary: str = Field(default="", max_length=20000)
    worldview: str = Field(default="", max_length=20000)
    power_system: Optional[Dict[str, Any]] = None
    selected_entities: Dict[str, Any] = Field(default_factory=dict)
    blueprint_version: Optional[int] = Field(default=None, ge=1)


class BlueprintCompleteRequest(BlueprintRequestBase):
    """蓝图补全请求。"""

    blueprint: Dict[str, Any] = Field(default_factory=dict)
    missing_fields: List[str] = Field(default_factory=list, max_length=40)
    blueprint_version: Optional[int] = Field(default=None, ge=1)


class PowerSystemGenerateRequest(BlueprintRequestBase):
    """战力体系生成请求。"""

    worldview: str = Field(default="", max_length=20000)
    power_system: Optional[Dict[str, Any]] = None
    blueprint_version: Optional[int] = Field(default=None, ge=1)


class OutlineGenerateRequest(BlueprintRequestBase):
    """卷章大纲生成请求。"""

    blueprint: Dict[str, Any] = Field(default_factory=dict)
    target_chapter_count: Optional[int] = Field(default=None, ge=1)
    target_word_count: Optional[int] = Field(default=None, ge=1)
    blueprint_version: Optional[int] = Field(default=None, ge=1)
    # 增量新增：用于校验本次生成基于哪一份已确认蓝图
    blueprint_id: Optional[str] = Field(default=None, max_length=80)


class OutlineConfirmRequest(BlueprintRequestBase):
    """卷章大纲确认请求。"""

    outline: Dict[str, Any] = Field(default_factory=dict)
    outline_version: Optional[int] = Field(default=None, ge=1)
    confirm: bool = Field(default=True)
    blueprint_id: Optional[str] = Field(default=None, max_length=80)
    blueprint_version: Optional[int] = Field(default=None, ge=1)


class ChaptersGenerateRequest(BlueprintRequestBase):
    """按已确认大纲批量生成章节正文请求。"""

    outline_generation_id: str = Field(default="", max_length=80)
    outline_version: Optional[int] = Field(default=None, ge=1)
    chapter_ids: List[str] = Field(default_factory=list, max_length=200)
    start_index: Optional[int] = Field(default=None, ge=0)
    count: Optional[int] = Field(default=None, ge=1, le=20)
    allow_overwrite: bool = Field(default=False)
    target_words: Optional[int] = Field(default=None, ge=1)
    blueprint_id: Optional[str] = Field(default=None, max_length=80)
    blueprint_version: Optional[int] = Field(default=None, ge=1)


class TextRevisionGenerateRequest(BlueprintRequestBase):
    """正文修改建议生成请求。"""

    chapter_id: str = Field(min_length=1, max_length=80)
    revision_type: str = Field(default="supplement")
    target_range: Dict[str, Any] = Field(default_factory=dict)
    target_text: str = Field(default="", max_length=20000)
    instruction: str = Field(default="", max_length=4000)
    card_changes: List[Dict[str, Any]] = Field(default_factory=list)
    card_ids: List[str] = Field(default_factory=list, max_length=60)


class TextRevisionImpactRequest(BlueprintRequestBase):
    """正文影响分析请求。"""

    card_ids: List[str] = Field(min_length=1, max_length=60)


@router.post("/prepare-blueprint")
async def prepare_blueprint(request: BlueprintPrepareRequest):
    """整理用户已选素材，产出蓝图候选（建议、冲突与追问）。

    新建小说场景还没有小说 ID，此时 novel_id 允许为空，只做无持久化整理。
    """
    try:
        return await novel_blueprint_service.prepare_blueprint(request)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/{novel_id}/blueprint/complete")
async def complete_blueprint(novel_id: str, request: BlueprintCompleteRequest):
    """补全蓝图中的空字段，只产出候选。"""
    payload = request.model_copy(update={"novel_id": novel_id})
    try:
        return await novel_blueprint_service.complete_blueprint(payload)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/{novel_id}/power-system/generate")
async def generate_power_system(novel_id: str, request: PowerSystemGenerateRequest):
    """生成或补全一套结构化战力体系候选。"""
    payload = request.model_copy(update={"novel_id": novel_id})
    try:
        return await novel_blueprint_service.generate_power_system(payload)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/{novel_id}/generate-outline")
async def generate_outline(novel_id: str, request: OutlineGenerateRequest):
    """根据已确认蓝图生成卷章大纲候选。"""
    payload = request.model_copy(update={"novel_id": novel_id})
    try:
        return await novel_blueprint_service.generate_outline(payload)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/{novel_id}/outlines/{generation_id}/confirm")
async def confirm_outline(novel_id: str, generation_id: str, request: OutlineConfirmRequest):
    """确认或暂存卷章大纲；确认后按大纲创建正式卷章。"""
    payload = request.model_copy(
        update={"novel_id": novel_id, "generation_id": generation_id}
    )
    try:
        return await novel_blueprint_service.confirm_outline(payload)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/{novel_id}/generate-chapters")
async def generate_chapters(novel_id: str, request: ChaptersGenerateRequest):
    """按已确认大纲批量生成章节正文候选，默认不覆盖已有正文。"""
    payload = request.model_copy(update={"novel_id": novel_id})
    try:
        return await novel_blueprint_service.generate_chapters(payload)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/chapters/text-revision/generate")
async def generate_text_revision(novel_id: str, request: TextRevisionGenerateRequest):
    """为指定章节生成正文修改建议候选，只做补充、事实修正与术语统一。"""
    payload = request.model_copy(update={"novel_id": novel_id})
    try:
        return await text_revision_service.generate(payload)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/{novel_id}/text-revision/impact")
async def analyze_text_revision_impact(novel_id: str, request: TextRevisionImpactRequest):
    """按卡片定位受影响章节与正文范围，供逐处生成正文修改建议。"""
    payload = request.model_copy(update={"novel_id": novel_id})
    try:
        return await text_revision_service.analyze_impact(payload)
    except Exception as exc:
        raise _http_exception(exc) from exc
