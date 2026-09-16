"""章节 AI 路由：方案、正文、续写、改写、扩写、压缩、审校、状态变更与定稿。

约定：
- 所有生成接口只返回候选，绝不直接覆盖 chapters.content；
- 前端"采用"候选后仍需调用 PUT /api/chapters/{id} 正式保存；
- 每次生成写入 generation_records，request_id 相同则不重复生成。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.services.llm.chapter_generation_service import chapter_generation_service
from backend.services.llm.text_revision_service import text_revision_service
from backend.services.novel.chapter_finalize_service import finalize_chapter_service

router = APIRouter(prefix="/api/llm", tags=["chapter-ai"])
logger = logging.getLogger(__name__)

ChapterKind = Literal[
    "chapter_plan",
    "chapter_draft",
    "chapter_continue",
    "chapter_rewrite",
    "chapter_expand",
    "chapter_compress",
    "consistency_review",
    "state_change_proposal",
]


def _sse_event(event: str, data: dict) -> str:
    """格式化一条 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_response(event_stream: AsyncGenerator[str, None]) -> StreamingResponse:
    """构造禁用代理缓冲的 SSE 响应。"""
    return StreamingResponse(
        event_stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        # 结构化输出解析失败、无可用 Provider 等，属于上游模型侧问题
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


class ChapterSelectionPayload(BaseModel):
    """选中片段。"""

    start: Optional[int] = Field(default=None, ge=0)
    end: Optional[int] = Field(default=None, ge=0)
    text: str = Field(default="", max_length=20000)


class ChapterAIRequestBase(BaseModel):
    """章节 AI 请求的公共字段（扁平生成参数，便于直接透传工作流）。"""

    novel_id: str = Field(min_length=1)
    # 由路由按路径参数覆盖，客户端传入无效
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


class ChapterPlanRequest(ChapterAIRequestBase):
    """章节方案生成请求。"""


class ChapterDraftRequest(ChapterAIRequestBase):
    """章节正文生成请求。"""

    plan: Optional[Dict[str, Any]] = None
    target_words: Optional[int] = Field(default=None, gt=0, le=20000)


class ChapterContinueRequest(ChapterAIRequestBase):
    """续写请求。"""

    plan: Optional[Dict[str, Any]] = None
    target_words: Optional[int] = Field(default=None, gt=0, le=20000)
    tail_text: Optional[str] = Field(default=None, max_length=8000)


class ChapterSelectionRequest(ChapterAIRequestBase):
    """改写/扩写/压缩请求，必须携带选区。"""

    selection: ChapterSelectionPayload
    instruction: str = Field(default="", max_length=2000)
    locked_facts: List[str] = Field(default_factory=list, max_length=20)


class ChapterContentRequest(ChapterAIRequestBase):
    """审校与状态变更提取请求。"""

    content: Optional[str] = Field(default=None, max_length=200000)


class ChapterStreamRequest(ChapterAIRequestBase):
    """统一流式生成请求，靠 kind 分发到具体生成类型。"""

    kind: ChapterKind
    plan: Optional[Dict[str, Any]] = None
    target_words: Optional[int] = Field(default=None, gt=0, le=20000)
    tail_text: Optional[str] = Field(default=None, max_length=8000)
    selection: Optional[ChapterSelectionPayload] = None
    instruction: str = Field(default="", max_length=2000)
    locked_facts: List[str] = Field(default_factory=list, max_length=20)
    content: Optional[str] = Field(default=None, max_length=200000)


class ChapterFinalizeRequest(BaseModel):
    """定稿请求：stage=review 先审校，stage=commit 再落库。"""

    novel_id: str = Field(min_length=1)
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
    stage: Literal["review", "commit"] = "review"
    expected_version: Optional[int] = Field(default=None, ge=1)
    accepted_event_ids: List[str] = Field(default_factory=list, max_length=200)
    rejected_event_ids: List[str] = Field(default_factory=list, max_length=200)
    ignored_issues: List[str] = Field(default_factory=list, max_length=200)
    blocking_issues: List[Dict[str, Any]] = Field(default_factory=list, max_length=200)
    force: bool = False
    # 增量新增：提交时回传审校依据，服务端据此重新校验（不信任客户端 blocking_issues）
    review_generation_id: str = Field(default="", max_length=80)
    hard_rule_signature: str = Field(default="", max_length=80)
    blueprint_version: Optional[int] = Field(default=None, ge=1)


class ChapterSettingRevisionRequest(ChapterAIRequestBase):
    """正文修改建议生成请求（规格 4.11）。"""

    card_ids: List[str] = Field(default_factory=list, max_length=60)
    revision_type: str = Field(default="supplement")
    instruction: str = Field(default="", max_length=4000)
    target_range: Dict[str, Any] = Field(default_factory=dict)
    target_text: str = Field(default="", max_length=20000)
    card_changes: List[Dict[str, Any]] = Field(default_factory=list)


DISPATCH = {
    "chapter_plan": chapter_generation_service.generate_plan,
    "chapter_draft": chapter_generation_service.generate_draft,
    "chapter_continue": chapter_generation_service.generate_continue,
    "chapter_rewrite": chapter_generation_service.rewrite_selection,
    "chapter_expand": chapter_generation_service.expand_selection,
    "chapter_compress": chapter_generation_service.compress_selection,
    "consistency_review": chapter_generation_service.review_chapter,
    "state_change_proposal": chapter_generation_service.propose_state_changes,
}


def _bind(payload: ChapterAIRequestBase, chapter_id: str) -> Any:
    """把路径中的章节 ID 写入请求体，客户端传入值一律以路径为准。"""
    return payload.model_copy(update={"chapter_id": chapter_id})


async def _dispatch(kind: str, payload: ChapterAIRequestBase, chapter_id: str) -> Dict[str, Any]:
    """按生成类型调用对应服务方法。"""
    handler = DISPATCH.get(kind)
    if handler is None:
        raise InvalidIdError(f"不支持的生成类型: {kind}")
    if kind in {"chapter_rewrite", "chapter_expand", "chapter_compress"}:
        selection = getattr(payload, "selection", None)
        if selection is None or not selection.text.strip():
            raise InvalidIdError("缺少选中文本，无法执行该操作")
    return await handler(_bind(payload, chapter_id))


async def _run_with_progress(
    kind: str,
    payload: ChapterAIRequestBase,
    chapter_id: str,
) -> AsyncGenerator[str, None]:
    """执行生成并周期性推送进度事件。

    说明：Provider 侧只有在支持流式时才有原始分片，这里刻意只推送
    等待进度与最终结构化结果，避免把 JSON 分片暴露给前端。
    """
    started_at = time.perf_counter()
    task = asyncio.ensure_future(_dispatch(kind, payload, chapter_id))
    while True:
        done, _pending = await asyncio.wait({task}, timeout=1.0)
        if done:
            break
        yield _sse_event(
            "progress",
            {
                "status": "progress",
                "step": kind,
                "elapsed_seconds": round(time.perf_counter() - started_at, 1),
            },
        )
    try:
        result = task.result()
    except Exception as exc:
        logger.exception("章节 AI 生成失败 kind=%s chapter_id=%s", kind, chapter_id)
        payload_error = _http_exception(exc)
        yield _sse_event(
            "error",
            {"success": False, "status_code": payload_error.status_code, "error": str(payload_error.detail)},
        )
        return
    yield _sse_event("done", {"success": True, "result": result})


@router.post("/chapters/{chapter_id}/plan")
async def generate_chapter_plan(chapter_id: str, request: ChapterPlanRequest):
    """生成章节剧情方案候选（不写库）。"""
    try:
        return await _dispatch("chapter_plan", request, chapter_id)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/chapters/{chapter_id}/draft")
async def generate_chapter_draft(chapter_id: str, request: ChapterDraftRequest):
    """生成章节正文候选（不覆盖章节正文）。"""
    try:
        return await _dispatch("chapter_draft", request, chapter_id)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/chapters/{chapter_id}/continue")
async def generate_chapter_continue(chapter_id: str, request: ChapterContinueRequest):
    """生成续写候选，只携带当前章节尾部。"""
    try:
        return await _dispatch("chapter_continue", request, chapter_id)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/chapters/{chapter_id}/rewrite")
async def rewrite_chapter_selection(chapter_id: str, request: ChapterSelectionRequest):
    """改写选中片段，返回候选片段与影响说明。"""
    try:
        return await _dispatch("chapter_rewrite", request, chapter_id)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/chapters/{chapter_id}/expand")
async def expand_chapter_selection(chapter_id: str, request: ChapterSelectionRequest):
    """扩写选中片段。"""
    try:
        return await _dispatch("chapter_expand", request, chapter_id)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/chapters/{chapter_id}/compress")
async def compress_chapter_selection(chapter_id: str, request: ChapterSelectionRequest):
    """压缩选中片段。"""
    try:
        return await _dispatch("chapter_compress", request, chapter_id)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/chapters/{chapter_id}/review")
async def review_chapter(chapter_id: str, request: ChapterContentRequest):
    """一致性审校，返回结构化问题列表。"""
    try:
        return await _dispatch("consistency_review", request, chapter_id)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/chapters/{chapter_id}/propose-state-changes")
async def propose_chapter_state_changes(chapter_id: str, request: ChapterContentRequest):
    """从正文提取状态变更候选。"""
    try:
        return await _dispatch("state_change_proposal", request, chapter_id)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/chapters/{chapter_id}/analyze-setting-cards")
async def analyze_chapter_setting_cards(chapter_id: str, request: ChapterContentRequest):
    """分析章节正文，产出新卡片、补充、冲突与事实变化候选。

    全部结果都是候选，只写入生成记录，不直接修改正式卡片或正文。
    """
    try:
        return await chapter_generation_service.analyze_setting_cards(
            request.model_copy(update={"chapter_id": chapter_id})
        )
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/chapters/{chapter_id}/stream")
async def stream_chapter_ai(chapter_id: str, request: ChapterStreamRequest):
    """统一流式生成入口，通过 kind 选择生成类型。"""
    return _sse_response(_run_with_progress(request.kind, request, chapter_id))


@router.post("/chapters/{chapter_id}/setting-revision")
async def generate_chapter_setting_revision(
    chapter_id: str, request: ChapterSettingRevisionRequest
):
    """生成正文修改建议候选（补充/事实修正/术语统一），不直接写回正文。

    Args:
        chapter_id: 章节 ObjectId 字符串。
        request: 小说、目标卡片、修改类型与范围。

    Returns:
        只含候选建议的响应体，用户确认后才写回章节。
    """
    try:
        return await text_revision_service.generate(
            request.model_copy(update={"chapter_id": chapter_id})
        )
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.post("/chapters/{chapter_id}/finalize")
async def finalize_chapter(chapter_id: str, request: ChapterFinalizeRequest):
    """定稿两阶段接口。

    stage=review：执行一致性审校并生成待确认的状态变更候选；
    stage=commit：按用户确认结果事务化落库并定稿。

    Args:
        chapter_id: 章节 ObjectId 字符串。
        request: 定稿请求体。

    Returns:
        审校结果或定稿结果。
    """
    payload = request.model_copy(update={"chapter_id": chapter_id})
    try:
        if request.stage == "review":
            return await finalize_chapter_service.review(payload)
        return await finalize_chapter_service.commit(payload)
    except Exception as exc:
        raise _http_exception(exc) from exc


@router.get("/chapters/{chapter_id}/pending-state-changes")
async def list_pending_state_changes(chapter_id: str, novel_id: str):
    """列出章节当前待确认的状态变更（用于刷新后恢复确认列表）。"""
    from backend.db.repositories.story_event_repository import story_event_repo

    try:
        chapter = await chapter_generation_service.load_chapter(chapter_id)
        events = await story_event_repo.list_events(
            novel_id, chapter_id=str(chapter.get("chapter_id") or ""), status="pending"
        )
        return {"data": events, "total": len(events), "chapter_version": chapter.get("version", 1)}
    except Exception as exc:
        raise _http_exception(exc) from exc


__all__ = ["router"]
