"""阵营关系创建、查询和完整生命周期 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.llm.schemas.faction_relation_pydantic import (
    FactionRelationActiveRequestV1,
    FactionRelationCreateRequestV1,
    FactionRelationResponseV1,
    FactionRelationUpdateRequestV1,
    FactionRelationVersionRequestV1,
)
from backend.services.novel.character_name import GenerationDomainError
from backend.services.novel.faction_relation_service import FactionRelationService

router = APIRouter(prefix="/api/faction-relations", tags=["faction-relations"])


def serialize_faction_relation(relation: dict[str, Any]) -> FactionRelationResponseV1:
    """把阵营关系 BSON 文档转换为严格公开响应。

    Args:
        relation: MongoDB 阵营关系文档。

    Returns:
        不含 MongoDB ``_id`` 和内部幂等字段的公开响应。
    """
    public_fields = set(FactionRelationResponseV1.model_fields)
    payload = {key: value for key, value in relation.items() if key in public_fields}
    if "novel_id" in payload:
        payload["novel_id"] = str(payload["novel_id"])
    return FactionRelationResponseV1.model_validate(payload)


def _translate_domain_error(exc: Exception) -> HTTPException:
    """把阵营关系领域异常转换为稳定 HTTP 错误。

    Args:
        exc: Repository 或 Service 抛出的领域异常。

    Returns:
        可由路由直接抛出的 HTTPException。
    """
    if isinstance(exc, GenerationDomainError):
        return HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, NotFoundError):
        return HTTPException(
            status_code=404,
            detail={"code": "RELATION_NOT_FOUND", "message": str(exc)},
        )
    if isinstance(exc, DuplicateKeyError):
        message = str(exc)
        code = "VERSION_CONFLICT" if "版本冲突" in message else "RELATION_CONFLICT"
        return HTTPException(status_code=409, detail={"code": code, "message": message})
    if isinstance(exc, InvalidIdError):
        return HTTPException(
            status_code=400,
            detail={"code": "INVALID_OBJECT_ID", "message": str(exc)},
        )
    return HTTPException(
        status_code=422,
        detail={"code": "RELATION_VALIDATION_FAILED", "message": str(exc)},
    )


@router.post("/novel/{novel_id}", response_model=FactionRelationResponseV1)
async def create_faction_relation(
    novel_id: str,
    request: FactionRelationCreateRequestV1,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> FactionRelationResponseV1:
    """人工创建一条正式阵营关系。

    Args:
        novel_id: 小说 ObjectId 字符串。
        request: 只使用稳定 faction_id 的严格创建请求。
        idempotency_key: 必填创建幂等请求头。

    Returns:
        新建或幂等回放的完整阵营关系。
    """
    try:
        relation = await FactionRelationService.create_relation(
            novel_id,
            request,
            idempotency_key=idempotency_key,
        )
        return serialize_faction_relation(relation)
    except (
        GenerationDomainError,
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_domain_error(exc) from exc


@router.get(
    "/novel/{novel_id}",
    response_model=dict[str, list[FactionRelationResponseV1]],
)
async def get_relations_by_novel(
    novel_id: str,
    is_active: bool | None = None,
) -> dict[str, list[FactionRelationResponseV1]]:
    """获取指定小说下阵营关系，可按有效态筛选。

    Args:
        novel_id: 小说 ObjectId 字符串。
        is_active: True/False 分别筛选有效/无效，None 返回全部未删除关系。

    Returns:
        阵营关系列表响应。
    """
    try:
        relations = await FactionRelationService.get_relations_by_novel(
            novel_id,
            active_only=is_active,
        )
        return {"data": [serialize_faction_relation(item) for item in relations]}
    except (NotFoundError, InvalidIdError, TypeError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


@router.get(
    "/novel/{novel_id}/faction/{faction_id}",
    response_model=dict[str, list[FactionRelationResponseV1]],
)
async def get_relations_by_faction(
    novel_id: str,
    faction_id: str,
    is_active: bool | None = None,
) -> dict[str, list[FactionRelationResponseV1]]:
    """获取某个阵营参与的当前有效关系。

    Args:
        novel_id: 小说 ObjectId 字符串。
        faction_id: 业务层阵营 ID。
        is_active: True/False 分别筛选有效/无效，None 返回全部未删除关系。

    Returns:
        阵营关系列表响应。
    """
    try:
        relations = await FactionRelationService.get_relations_by_faction(
            novel_id,
            faction_id,
            active_only=is_active,
        )
        return {"data": [serialize_faction_relation(item) for item in relations]}
    except (NotFoundError, InvalidIdError, TypeError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


@router.get(
    "/novel/{novel_id}/trash",
    response_model=dict[str, list[FactionRelationResponseV1]],
)
async def list_deleted_faction_relations(
    novel_id: str,
) -> dict[str, list[FactionRelationResponseV1]]:
    """列出小说阵营关系回收站。

    Args:
        novel_id: 小说 ObjectId 字符串。

    Returns:
        包含已软删除阵营关系的 ``data`` 响应。
    """
    try:
        relations = await FactionRelationService.list_deleted_relations(novel_id)
        return {"data": [serialize_faction_relation(item) for item in relations]}
    except (NotFoundError, InvalidIdError, TypeError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


@router.get(
    "/novel/{novel_id}/{relation_id}",
    response_model=FactionRelationResponseV1,
)
async def get_faction_relation(
    novel_id: str,
    relation_id: str,
) -> FactionRelationResponseV1:
    """获取一条阵营关系详情。

    Args:
        novel_id: 小说 ObjectId 字符串。
        relation_id: 稳定阵营关系业务 ID。

    Returns:
        匹配的完整阵营关系。
    """
    try:
        relation = await FactionRelationService.get_relation(novel_id, relation_id)
        return serialize_faction_relation(relation)
    except (NotFoundError, InvalidIdError, TypeError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


@router.put(
    "/novel/{novel_id}/{relation_id}",
    response_model=FactionRelationResponseV1,
)
async def update_faction_relation(
    novel_id: str,
    relation_id: str,
    request: FactionRelationUpdateRequestV1,
) -> FactionRelationResponseV1:
    """更新阵营关系内容，端点保持不可变。

    Args:
        novel_id: 小说 ObjectId 字符串。
        relation_id: 稳定阵营关系业务 ID。
        request: 内容字段与 expected_version。

    Returns:
        更新后的完整阵营关系。
    """
    try:
        relation = await FactionRelationService.update_relation(
            novel_id,
            relation_id,
            request,
        )
        return serialize_faction_relation(relation)
    except (
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_domain_error(exc) from exc


@router.put(
    "/novel/{novel_id}/{relation_id}/active",
    response_model=FactionRelationResponseV1,
)
async def set_faction_relation_active(
    novel_id: str,
    relation_id: str,
    request: FactionRelationActiveRequestV1,
) -> FactionRelationResponseV1:
    """修改阵营关系的用户启停意图。

    Args:
        novel_id: 小说 ObjectId 字符串。
        relation_id: 稳定阵营关系业务 ID。
        request: 目标状态与 expected_version。

    Returns:
        更新后的完整阵营关系。
    """
    try:
        relation = await FactionRelationService.set_relation_active(
            novel_id,
            relation_id,
            expected_version=request.expected_version,
            is_active=request.is_active,
        )
        return serialize_faction_relation(relation)
    except (
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_domain_error(exc) from exc


@router.delete(
    "/novel/{novel_id}/{relation_id}",
    response_model=FactionRelationResponseV1,
)
async def delete_faction_relation(
    novel_id: str,
    relation_id: str,
    expected_version: int = Query(..., ge=1),
) -> FactionRelationResponseV1:
    """将阵营关系软删除到回收站。

    Args:
        novel_id: 小说 ObjectId 字符串。
        relation_id: 稳定阵营关系业务 ID。
        expected_version: 调用方持有的乐观锁版本。

    Returns:
        软删除后的完整阵营关系。
    """
    try:
        relation = await FactionRelationService.soft_delete_relation(
            novel_id,
            relation_id,
            expected_version=expected_version,
        )
        return serialize_faction_relation(relation)
    except (
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_domain_error(exc) from exc


@router.post(
    "/novel/{novel_id}/{relation_id}/restore",
    response_model=FactionRelationResponseV1,
)
async def restore_faction_relation(
    novel_id: str,
    relation_id: str,
    request: FactionRelationVersionRequestV1,
) -> FactionRelationResponseV1:
    """恢复回收站中的阵营关系。

    Args:
        novel_id: 小说 ObjectId 字符串。
        relation_id: 稳定阵营关系业务 ID。
        request: 携带 expected_version 的恢复请求。

    Returns:
        恢复后的完整阵营关系。
    """
    try:
        relation = await FactionRelationService.restore_relation(
            novel_id,
            relation_id,
            expected_version=request.expected_version,
        )
        return serialize_faction_relation(relation)
    except (
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_domain_error(exc) from exc


@router.delete("/novel/{novel_id}/{relation_id}/hard")
async def hard_delete_faction_relation(
    novel_id: str,
    relation_id: str,
    expected_version: int = Query(..., ge=1),
) -> dict[str, Any]:
    """物理删除回收站中的阵营关系。

    Args:
        novel_id: 小说 ObjectId 字符串。
        relation_id: 稳定阵营关系业务 ID。
        expected_version: 调用方持有的乐观锁版本。

    Returns:
        包含删除结果和稳定关系 ID 的响应。
    """
    try:
        deleted = await FactionRelationService.hard_delete_relation(
            novel_id,
            relation_id,
            expected_version=expected_version,
        )
        return {"deleted": deleted, "relation_id": relation_id}
    except (
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_domain_error(exc) from exc


__all__ = ["router", "serialize_faction_relation"]
