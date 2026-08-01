"""全书级核心角色与人物关系的直接生成预览 API。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.db.errors import InvalidIdError, NotFoundError
from backend.llm.schemas.character_pydantic import (
    CoreCharactersGenerateRequestV1,
    CoreCharactersResultSchemaV1,
)
from backend.llm.schemas.character_relation_pydantic import (
    CharacterRelationsGenerateRequestV1,
    CharacterRelationsResultSchemaV1,
)
from backend.services.llm.character_generation_service import (
    CHARACTER_RELATION_STEP,
    CORE_CHARACTER_STEP,
    CharacterGenerationService,
)
from backend.services.novel.character_name import GenerationDomainError


router = APIRouter(prefix="/api/llm", tags=["character-generation"])
logger = logging.getLogger(__name__)


def _sse_event(event: str, data: dict) -> str:
    """格式化单条 UTF-8 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_response(event_stream: AsyncGenerator[str, None]) -> StreamingResponse:
    """构造禁用代理缓冲的 SSE 响应。"""
    return StreamingResponse(
        event_stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _http_exception(exc: Exception) -> HTTPException:
    """把角色生成领域异常转换为稳定 HTTP 错误。

    Args:
        exc: 服务层或 Repository 抛出的异常。

    Returns:
        可由普通预览路由直接抛出的 HTTPException。
    """
    if isinstance(exc, GenerationDomainError):
        return HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, InvalidIdError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail="角色生成失败")


def _sse_error_payload(exc: Exception) -> dict:
    """把流建立后的异常转换为固定 error 事件字段。

    Args:
        exc: 流式生成期间捕获的异常。

    Returns:
        包含 success、status_code 与安全错误文本的事件数据。
    """
    if isinstance(exc, GenerationDomainError):
        return {
            "success": False,
            "status_code": exc.status_code,
            "error": exc.message,
        }
    if isinstance(exc, NotFoundError):
        return {"success": False, "status_code": 404, "error": str(exc)}
    if isinstance(exc, InvalidIdError):
        return {"success": False, "status_code": 400, "error": str(exc)}
    if isinstance(exc, ValueError):
        return {"success": False, "status_code": 502, "error": str(exc)}
    return {"success": False, "status_code": 500, "error": "角色生成失败"}


@router.post(
    "/generate-core-characters",
    response_model=CoreCharactersResultSchemaV1,
)
async def generate_core_characters(
    request: CoreCharactersGenerateRequestV1,
) -> CoreCharactersResultSchemaV1:
    """直接生成全书级核心角色和势力绑定候选，不写数据库。

    Args:
        request: 小说 ID、角色数量、指导文本和扁平七项生成参数。

    Returns:
        已通过结构与当前小说事实校验的候选结果。
    """
    try:
        return await CharacterGenerationService.generate_core_characters_preview(
            request
        )
    except (
        GenerationDomainError,
        NotFoundError,
        InvalidIdError,
        ValueError,
    ) as exc:
        raise _http_exception(exc) from exc
    except Exception as exc:
        logger.exception("直接生成核心角色失败")
        raise _http_exception(exc) from exc


@router.post("/generate-core-characters/stream")
async def generate_core_characters_stream(
    request: CoreCharactersGenerateRequestV1,
) -> StreamingResponse:
    """通过 SSE 直接生成全书级核心角色候选。

    Args:
        request: 小说 ID、角色数量、指导文本和扁平七项生成参数。

    Returns:
        仅包含 progress、done 或 error 的 SSE 响应。
    """
    try:
        prepared_stream = (
            await CharacterGenerationService.stream_core_characters_preview(request)
        )
    except (
        GenerationDomainError,
        NotFoundError,
        InvalidIdError,
        ValueError,
    ) as exc:
        raise _http_exception(exc) from exc
    except Exception as exc:
        logger.exception("准备流式核心角色生成失败")
        raise _http_exception(exc) from exc

    async def event_stream() -> AsyncGenerator[str, None]:
        """转发核心角色服务事件并在请求取消时立即终止。

        Args:
            无。

        Yields:
            已格式化的核心角色 progress、done 或 error SSE 文本。
        """
        try:
            async for event in prepared_stream:
                if event["status"] == "progress":
                    yield _sse_event(
                        "progress",
                        {
                            "status": "progress",
                            "step": CORE_CHARACTER_STEP,
                            "chunk_count": event["chunk_count"],
                            "characters": event["characters"],
                        },
                    )
                    continue
                result = CoreCharactersResultSchemaV1.model_validate(
                    event["result"].model_dump()
                )
                yield _sse_event(
                    "done",
                    {"success": True, "result": result.model_dump(mode="json")},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("流式生成核心角色失败")
            yield _sse_event("error", _sse_error_payload(exc))

    return _sse_response(event_stream())


@router.post(
    "/generate-character-relations",
    response_model=CharacterRelationsResultSchemaV1,
)
async def generate_character_relations(
    request: CharacterRelationsGenerateRequestV1,
) -> CharacterRelationsResultSchemaV1:
    """直接生成仅追加的全书级角色关系候选，不写数据库。

    Args:
        request: 小说 ID、角色白名单、关系约束和扁平七项生成参数。

    Returns:
        已通过结构、端点和现有关系冲突校验的候选结果。
    """
    try:
        return await CharacterGenerationService.generate_character_relations_preview(
            request
        )
    except (
        GenerationDomainError,
        NotFoundError,
        InvalidIdError,
        ValueError,
    ) as exc:
        raise _http_exception(exc) from exc
    except Exception as exc:
        logger.exception("直接生成角色关系失败")
        raise _http_exception(exc) from exc


@router.post("/generate-character-relations/stream")
async def generate_character_relations_stream(
    request: CharacterRelationsGenerateRequestV1,
) -> StreamingResponse:
    """通过 SSE 直接生成仅追加的全书级角色关系候选。

    Args:
        request: 小说 ID、角色白名单、关系约束和扁平七项生成参数。

    Returns:
        仅包含 progress、done 或 error 的 SSE 响应。
    """
    try:
        prepared_stream = (
            await CharacterGenerationService.stream_character_relations_preview(request)
        )
    except (
        GenerationDomainError,
        NotFoundError,
        InvalidIdError,
        ValueError,
    ) as exc:
        raise _http_exception(exc) from exc
    except Exception as exc:
        logger.exception("准备流式角色关系生成失败")
        raise _http_exception(exc) from exc

    async def event_stream() -> AsyncGenerator[str, None]:
        """转发关系生成服务事件并在请求取消时立即终止。

        Args:
            无。

        Yields:
            已格式化的角色关系 progress、done 或 error SSE 文本。
        """
        try:
            async for event in prepared_stream:
                if event["status"] == "progress":
                    yield _sse_event(
                        "progress",
                        {
                            "status": "progress",
                            "step": CHARACTER_RELATION_STEP,
                            "chunk_count": event["chunk_count"],
                            "characters": event["characters"],
                        },
                    )
                    continue
                result = CharacterRelationsResultSchemaV1.model_validate(
                    event["result"].model_dump()
                )
                yield _sse_event(
                    "done",
                    {"success": True, "result": result.model_dump(mode="json")},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("流式生成角色关系失败")
            yield _sse_event("error", _sse_error_payload(exc))

    return _sse_response(event_stream())


__all__ = ["router"]
