"""全书级角色关系创建与查询 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import ValidationError

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.llm.schemas.character_relation_pydantic import (
    CharacterRelationActiveRequestV1,
    CharacterRelationCreateRequestV1,
    CharacterRelationResponseV1,
    CharacterRelationUpdateRequestV1,
    CharacterRelationVersionRequestV1,
    CharacterRelationsResultSchemaV1,
    CharacterRelationType,
    build_relation_semantic_key,
)
from backend.services.novel.character_relation_service import (
    RELATION_TYPES,
    CharacterRelationService,
)
from backend.services.novel.character_name import GenerationDomainError


router = APIRouter(prefix="/api/character-relations", tags=["character-relations"])


def serialize_character_relation(relation: dict[str, Any]) -> CharacterRelationResponseV1:
    """把角色关系 BSON 文档转换为严格公开响应。

    Args:
        relation: CharacterRelationRepository 返回的角色关系 BSON 文档。

    Returns:
        不含 MongoDB ``_id`` 和内部索引字段的公开响应。
    """
    public_fields = set(CharacterRelationResponseV1.model_fields)
    payload = {key: value for key, value in relation.items() if key in public_fields}
    if "novel_id" in payload:
        payload["novel_id"] = str(payload["novel_id"])
    return CharacterRelationResponseV1.model_validate(payload)


def _translate_domain_error(exc: Exception) -> HTTPException:
    """把角色关系领域异常转换为稳定 HTTP 状态。

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


@router.post("/novel/{novel_id}", response_model=CharacterRelationResponseV1)
async def create_character_relation(
    novel_id: str,
    request: CharacterRelationCreateRequestV1,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> CharacterRelationResponseV1:
    """创建一条全书级 active 核心角色关系。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        request: 经严格 Schema 校验的关系创建请求。
        idempotency_key: 必填的创建幂等请求头。

    Returns:
        新创建的完整角色关系公开响应。
    """
    try:
        relation = await CharacterRelationService.create_relation(
            novel_id,
            request,
            idempotency_key=idempotency_key,
        )
        return serialize_character_relation(relation)
    except (
        GenerationDomainError,
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_domain_error(exc) from exc


@router.post(
    "/novel/{novel_id}/bulk-append",
    response_model=dict[str, list[CharacterRelationResponseV1]],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": CharacterRelationsResultSchemaV1.model_json_schema()
                }
            },
        }
    },
)
async def bulk_append_character_relations(
    novel_id: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    """把用户选择并编辑后的关系候选追加为正式全书级关系。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        request: 待执行严格 Schema 校验的关系候选集合。

    Returns:
        新创建的正式角色关系列表。
    """
    try:
        # 在 Pydantic 聚合校验前识别内部及反向语义冲突，使冲突稳定返回 409。
        semantic_keys: set[tuple[str, str, str]] = set()
        raw_relations = request.get("relations")
        if isinstance(raw_relations, list):
            for relation in raw_relations:
                if not isinstance(relation, dict):
                    continue
                source_id = relation.get("source_character_id")
                target_id = relation.get("target_character_id")
                relation_type = relation.get("relation_type")
                if (
                    not isinstance(source_id, str)
                    or not isinstance(target_id, str)
                    or source_id == target_id
                    or not isinstance(relation_type, str)
                    or relation_type not in RELATION_TYPES
                ):
                    continue
                semantic_key = build_relation_semantic_key(
                    source_id,
                    target_id,
                    relation_type,
                )
                if semantic_key in semantic_keys:
                    raise DuplicateKeyError(
                        "relations 中不能包含重复或反向重复的同类型语义边"
                    )
                semantic_keys.add(semantic_key)

        validated_request = CharacterRelationsResultSchemaV1.model_validate(request)
        result = await CharacterRelationService.bulk_append_relations(
            novel_id,
            validated_request,
        )
        return {
            "character_relations": [
                serialize_character_relation(relation)
                for relation in result["character_relations"]
            ],
        }
    except (
        GenerationDomainError,
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_domain_error(exc) from exc


@router.get(
    "/novel/{novel_id}",
    response_model=dict[str, list[CharacterRelationResponseV1]],
)
async def list_character_relations(
    novel_id: str,
    character_id: str | None = Query(default=None, pattern=r"^char_[0-9]{6,}$"),
    relation_type: CharacterRelationType | None = None,
    is_active: bool | None = None,
) -> dict[str, list[CharacterRelationResponseV1]]:
    """列出小说内全书级角色关系。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        character_id: 可选角色端点业务 ID 过滤。
        relation_type: 可选关系类型过滤。
        is_active: 可选活动状态过滤；None 表示返回全部未删除关系。

    Returns:
        包含稳定排序关系列表的 ``data`` 响应。
    """
    try:
        relations = await CharacterRelationService.list_relations(
            novel_id,
            character_id=character_id,
            relation_type=relation_type,
            is_active=is_active,
        )
        return {"data": [serialize_character_relation(relation) for relation in relations]}
    except (NotFoundError, InvalidIdError, TypeError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


@router.get(
    "/novel/{novel_id}/trash",
    response_model=dict[str, list[CharacterRelationResponseV1]],
)
async def list_deleted_character_relations(
    novel_id: str,
) -> dict[str, list[CharacterRelationResponseV1]]:
    """列出小说角色关系回收站。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。

    Returns:
        包含已软删除角色关系的 ``data`` 响应。
    """
    try:
        relations = await CharacterRelationService.list_deleted_relations(novel_id)
        return {"data": [serialize_character_relation(relation) for relation in relations]}
    except (NotFoundError, InvalidIdError, TypeError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


@router.put(
    "/novel/{novel_id}/{relation_id}",
    response_model=CharacterRelationResponseV1,
)
async def update_character_relation(
    novel_id: str,
    relation_id: str,
    request: CharacterRelationUpdateRequestV1,
) -> CharacterRelationResponseV1:
    """更新角色关系内容，端点保持不可变。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        relation_id: 稳定角色关系业务 ID。
        request: 内容字段与 expected_version。

    Returns:
        更新后的完整角色关系。
    """
    try:
        relation = await CharacterRelationService.update_relation(
            novel_id,
            relation_id,
            request,
        )
        return serialize_character_relation(relation)
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
    response_model=CharacterRelationResponseV1,
)
async def set_character_relation_active(
    novel_id: str,
    relation_id: str,
    request: CharacterRelationActiveRequestV1,
) -> CharacterRelationResponseV1:
    """显式修改角色关系的用户启停意图。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        relation_id: 稳定角色关系业务 ID。
        request: 目标状态与 expected_version。

    Returns:
        更新后的完整角色关系。
    """
    try:
        relation = await CharacterRelationService.set_relation_active(
            novel_id,
            relation_id,
            expected_version=request.expected_version,
            is_active=request.is_active,
        )
        return serialize_character_relation(relation)
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
    response_model=CharacterRelationResponseV1,
)
async def delete_character_relation(
    novel_id: str,
    relation_id: str,
    expected_version: int = Query(..., ge=1),
) -> CharacterRelationResponseV1:
    """将角色关系软删除到回收站。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        relation_id: 稳定角色关系业务 ID。
        expected_version: 调用方持有的乐观锁版本。

    Returns:
        软删除后的完整角色关系。
    """
    try:
        relation = await CharacterRelationService.soft_delete_relation(
            novel_id,
            relation_id,
            expected_version=expected_version,
        )
        return serialize_character_relation(relation)
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
    response_model=CharacterRelationResponseV1,
)
async def restore_character_relation(
    novel_id: str,
    relation_id: str,
    request: CharacterRelationVersionRequestV1,
) -> CharacterRelationResponseV1:
    """恢复回收站中的角色关系。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        relation_id: 稳定角色关系业务 ID。
        request: 携带 expected_version 的恢复请求。

    Returns:
        恢复后的完整角色关系。
    """
    try:
        relation = await CharacterRelationService.restore_relation(
            novel_id,
            relation_id,
            expected_version=request.expected_version,
        )
        return serialize_character_relation(relation)
    except (
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_domain_error(exc) from exc


@router.delete("/novel/{novel_id}/{relation_id}/hard")
async def hard_delete_character_relation(
    novel_id: str,
    relation_id: str,
    expected_version: int = Query(..., ge=1),
) -> dict[str, Any]:
    """物理删除回收站中的角色关系。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        relation_id: 稳定角色关系业务 ID。
        expected_version: 调用方持有的乐观锁版本。

    Returns:
        包含删除结果和稳定关系 ID 的响应。
    """
    try:
        deleted = await CharacterRelationService.hard_delete_relation(
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


@router.get("/novel/{novel_id}/{relation_id}", response_model=CharacterRelationResponseV1)
async def get_character_relation(
    novel_id: str,
    relation_id: str,
) -> CharacterRelationResponseV1:
    """获取一条全书级角色关系详情。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        relation_id: 稳定角色关系业务 ID。

    Returns:
        匹配关系的完整公开响应。
    """
    try:
        relation = await CharacterRelationService.get_relation(novel_id, relation_id)
        return serialize_character_relation(relation)
    except (NotFoundError, InvalidIdError, TypeError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


__all__ = ["router", "serialize_character_relation"]
