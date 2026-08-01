"""角色主档公开 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import ValidationError

from backend.api.default_routers.character_faction_binding_router import (
    serialize_character_faction_binding,
)
from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.llm.schemas.character_pydantic import (
    CharacterCreateRequestV1,
    CharacterRestoreRequestV1,
    CharacterResponseV1,
    CharacterStatusUpdateRequestV1,
    CharacterUpdateRequestV1,
    CoreCharactersResultSchemaV1,
)
from backend.services.novel.character_service import CharacterService
from backend.services.novel.character_name import (
    GenerationDomainError,
    normalize_character_name,
)


router = APIRouter(prefix="/api/characters", tags=["characters"])


_CHARACTER_TEXT_RESPONSE_DEFAULTS: tuple[str, ...] = (
    "gender",
    "age_group",
    "race",
    "identity",
    "appearance",
    "personality",
    "core_desire",
    "core_fear",
    "conflict_with_mainline",
    "relationship_with_protagonist",
    "initial_state",
    "growth_direction",
    "story_function",
    "arc_seed",
)


def _coerce_legacy_text_list(value: Any) -> list[str]:
    """把旧版字符串或字符串数组转换为当前数组响应。

    Args:
        value: 旧版 ``alias``、``ability`` 或当前数组字段值。

    Returns:
        去除空白项后的字符串列表。
    """
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _coerce_legacy_role_type(value: Any) -> str:
    """把旧版角色类型映射到当前公开枚举。

    Args:
        value: 角色文档中的 role_type。

    Returns:
        当前 CharacterRoleType 字符串。
    """
    current_types = {
        "protagonist",
        "deuteragonist",
        "antagonist",
        "supporting",
        "minor",
    }
    role_type = str(value or "").strip()
    if role_type in current_types:
        return role_type
    legacy_types = {
        "protagonist_core": "protagonist",
        "deuteragonist_core": "deuteragonist",
        "antagonist_core": "antagonist",
        "supporting_core": "supporting",
        "minor_core": "minor",
    }
    if not role_type:
        return "supporting"
    # 未知非空枚举交给严格响应 Schema 报错，避免静默伪装成 supporting。
    return legacy_types.get(role_type, role_type)


def serialize_character(character: dict[str, Any]) -> CharacterResponseV1:
    """把角色 BSON 文档转换为严格公开响应。

    Args:
        character: CharacterRepository 返回的角色 BSON 文档。

    Returns:
        不含 Mongo ``_id`` 和内部生成批次字段的 CharacterResponseV1。
    """
    public_fields = set(CharacterResponseV1.model_fields)
    payload = {key: value for key, value in character.items() if key in public_fields}
    if "novel_id" in payload:
        payload["novel_id"] = str(payload["novel_id"])

    # 旧版 alias/ability、supporting_core 与缺失技术字段只在响应层兼容，原文档仍保留。
    aliases_value = character.get("aliases")
    if aliases_value is None:
        aliases_value = character.get("alias")
    abilities_value = character.get("abilities")
    if abilities_value is None:
        abilities_value = character.get("ability")
    payload["aliases"] = _coerce_legacy_text_list(aliases_value)
    payload["abilities"] = _coerce_legacy_text_list(abilities_value)
    payload.setdefault("strengths", [])
    payload.setdefault("weaknesses", [])
    payload.setdefault("tags", [])
    payload["role_type"] = _coerce_legacy_role_type(character.get("role_type"))
    payload.setdefault(
        "importance_level",
        "core" if character.get("is_core_character") is True else "supporting",
    )
    for field in _CHARACTER_TEXT_RESPONSE_DEFAULTS:
        payload.setdefault(field, "")
    payload.setdefault("status", "active")
    payload.setdefault("is_core_character", False)
    payload.setdefault("first_appearance_volume_id", None)
    payload.setdefault("first_appearance_chapter_id", None)
    payload.setdefault("extra", {})
    payload.setdefault("version", 1)
    payload.setdefault("is_deleted", False)
    payload.setdefault("deleted_at", None)
    payload.setdefault("deletion_sources", [])
    if "sort_order" not in payload:
        character_id = str(payload.get("character_id") or "")
        try:
            payload["sort_order"] = int(character_id.rsplit("_", 1)[-1]) * 10
        except (TypeError, ValueError):
            payload["sort_order"] = 0
    return CharacterResponseV1.model_validate(payload)


def _translate_character_error(exc: Exception) -> HTTPException:
    """把角色领域异常转换为稳定 HTTP 状态。

    Args:
        exc: Repository、Service 或领域校验抛出的异常。

    Returns:
        可由路由直接抛出的 HTTPException。
    """
    if isinstance(exc, GenerationDomainError):
        return HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DuplicateKeyError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, InvalidIdError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post("/novel/{novel_id}", response_model=CharacterResponseV1)
async def create_character(
    novel_id: str,
    req: CharacterCreateRequestV1,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> CharacterResponseV1:
    """在指定小说中人工创建角色主档。

    Args:
        novel_id: 小说 ObjectId 字符串。
        req: 已通过严格 Schema 校验的角色创建请求。
        idempotency_key: 必填的创建幂等请求头。

    Returns:
        已创建角色的完整公开响应。
    """
    try:
        character = await CharacterService.create_character(
            novel_id,
            req,
            idempotency_key=idempotency_key,
        )
        return serialize_character(character)
    except GenerationDomainError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/novel/{novel_id}/bulk-core-with-bindings",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": CoreCharactersResultSchemaV1.model_json_schema()
                }
            },
        }
    },
)
async def bulk_create_core_characters_with_bindings(
    novel_id: str,
    req: dict[str, Any],
) -> dict[str, Any]:
    """批量创建全书级核心角色及其可选势力绑定。

    Args:
        novel_id: 小说 ObjectId 字符串。
        req: 待执行严格 Schema 校验的角色与绑定生成结果。

    Returns:
        新创建的角色与角色—势力绑定。
    """
    try:
        # 在 Pydantic 聚合校验前完成规范化名称判重，使业务冲突稳定返回 409。
        normalized_names: set[str] = set()
        raw_characters = req.get("core_characters")
        if isinstance(raw_characters, list):
            for character in raw_characters:
                if not isinstance(character, dict):
                    continue
                name = character.get("name")
                if not isinstance(name, str):
                    continue
                try:
                    _, normalized_name = normalize_character_name(name)
                except ValueError:
                    # 名称自身非法时交给严格 Schema 或领域校验返回 422。
                    continue
                if normalized_name in normalized_names:
                    raise DuplicateKeyError("核心角色候选名称规范化后不能重复")
                normalized_names.add(normalized_name)

        validated_request = CoreCharactersResultSchemaV1.model_validate(req)
        result = await CharacterService.bulk_create_core_with_bindings(
            novel_id,
            validated_request,
        )
        return {
            "characters": [
                serialize_character(character) for character in result["characters"]
            ],
            "character_faction_bindings": [
                serialize_character_faction_binding(binding)
                for binding in result["character_faction_bindings"]
            ],
        }
    except GenerationDomainError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/novel/{novel_id}", response_model=dict[str, list[CharacterResponseV1]])
async def get_characters_by_novel(novel_id: str) -> dict[str, list[CharacterResponseV1]]:
    """获取小说内全部未删除角色。

    Args:
        novel_id: 小说 ObjectId 字符串。

    Returns:
        包含稳定排序角色列表的 ``data`` 响应。
    """
    try:
        characters = await CharacterService.get_characters_by_novel(novel_id)
        return {"data": [serialize_character(character) for character in characters]}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/novel/{novel_id}/trash",
    response_model=dict[str, list[CharacterResponseV1]],
)
async def get_deleted_characters(
    novel_id: str,
) -> dict[str, list[CharacterResponseV1]]:
    """获取小说角色回收站列表。

    Args:
        novel_id: 小说 ObjectId 字符串。

    Returns:
        包含软删除角色列表的 ``data`` 响应。
    """
    try:
        characters = await CharacterService.get_deleted_characters_by_novel(novel_id)
        return {"data": [serialize_character(character) for character in characters]}
    except (NotFoundError, InvalidIdError, TypeError, ValueError) as exc:
        raise _translate_character_error(exc) from exc


@router.put(
    "/novel/{novel_id}/{character_id}",
    response_model=CharacterResponseV1,
)
async def update_character(
    novel_id: str,
    character_id: str,
    request: CharacterUpdateRequestV1,
) -> CharacterResponseV1:
    """使用乐观锁更新角色档案白名单字段。

    Args:
        novel_id: 小说 ObjectId 字符串。
        character_id: 稳定角色业务 ID。
        request: 含 expected_version 的严格更新请求。

    Returns:
        更新后的完整角色公开响应。
    """
    try:
        character = await CharacterService.update_character(
            novel_id,
            character_id,
            request,
        )
        return serialize_character(character)
    except (
        GenerationDomainError,
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_character_error(exc) from exc


@router.put(
    "/novel/{novel_id}/{character_id}/status",
    response_model=CharacterResponseV1,
)
async def update_character_status(
    novel_id: str,
    character_id: str,
    request: CharacterStatusUpdateRequestV1,
) -> CharacterResponseV1:
    """切换角色活动状态并联动关系与势力绑定。

    Args:
        novel_id: 小说 ObjectId 字符串。
        character_id: 稳定角色业务 ID。
        request: 新状态与 expected_version。

    Returns:
        状态更新后的完整角色公开响应。
    """
    try:
        character = await CharacterService.update_character_status(
            novel_id,
            character_id,
            request.status,
            expected_version=request.expected_version,
        )
        return serialize_character(character)
    except (
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_character_error(exc) from exc


@router.delete(
    "/novel/{novel_id}/{character_id}",
    response_model=CharacterResponseV1,
)
async def soft_delete_character(
    novel_id: str,
    character_id: str,
    expected_version: int = Query(..., ge=1),
) -> CharacterResponseV1:
    """使用乐观锁把角色移入回收站。

    Args:
        novel_id: 小说 ObjectId 字符串。
        character_id: 稳定角色业务 ID。
        expected_version: 客户端基于的角色版本。

    Returns:
        已软删除的完整角色公开响应。
    """
    try:
        character = await CharacterService.soft_delete_character(
            novel_id,
            character_id,
            expected_version=expected_version,
        )
        return serialize_character(character)
    except (
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_character_error(exc) from exc


@router.post(
    "/novel/{novel_id}/{character_id}/restore",
    response_model=CharacterResponseV1,
)
async def restore_character(
    novel_id: str,
    character_id: str,
    request: CharacterRestoreRequestV1,
) -> CharacterResponseV1:
    """使用乐观锁恢复回收站角色。

    Args:
        novel_id: 小说 ObjectId 字符串。
        character_id: 稳定角色业务 ID。
        request: 含 expected_version 的恢复请求。

    Returns:
        恢复后的完整角色公开响应。
    """
    try:
        character = await CharacterService.restore_character(
            novel_id,
            character_id,
            expected_version=request.expected_version,
        )
        return serialize_character(character)
    except (
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_character_error(exc) from exc


@router.delete("/novel/{novel_id}/{character_id}/hard")
async def hard_delete_character(
    novel_id: str,
    character_id: str,
    expected_version: int = Query(..., ge=1),
) -> dict[str, dict[str, int]]:
    """永久删除回收站角色及其全部关系和势力绑定。

    Args:
        novel_id: 小说 ObjectId 字符串。
        character_id: 稳定角色业务 ID。
        expected_version: 客户端基于的角色版本。

    Returns:
        三类文档的精确删除计数。
    """
    try:
        stats = await CharacterService.hard_delete_character(
            novel_id,
            character_id,
            expected_version=expected_version,
        )
        return {"stats": stats}
    except (
        NotFoundError,
        DuplicateKeyError,
        InvalidIdError,
        TypeError,
        ValueError,
    ) as exc:
        raise _translate_character_error(exc) from exc


@router.get("/novel/{novel_id}/{character_id}", response_model=CharacterResponseV1)
async def get_character(novel_id: str, character_id: str) -> CharacterResponseV1:
    """获取小说内单个未删除角色详情。

    Args:
        novel_id: 小说 ObjectId 字符串。
        character_id: 小说内稳定角色业务 ID。

    Returns:
        匹配角色的完整公开响应。
    """
    try:
        character = await CharacterService.get_character(novel_id, character_id)
        return serialize_character(character)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = ["router", "serialize_character"]
