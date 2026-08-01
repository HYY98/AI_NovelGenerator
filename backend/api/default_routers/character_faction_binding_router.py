"""全书级角色—势力绑定创建与查询 API。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.llm.schemas.character_pydantic import (
    FACTION_BUSINESS_ID_MAX_LENGTH,
    FACTION_BUSINESS_ID_MIN_LENGTH,
    FACTION_BUSINESS_ID_PATTERN,
)
from backend.services.novel.character_faction_binding_service import CharacterFactionBindingService
from backend.services.novel.character_name import GenerationDomainError


router = APIRouter(prefix="/api/character-faction-bindings", tags=["character-faction-bindings"])


class CharacterFactionBindingCreateRequestV1(BaseModel):
    """全书级角色—势力初始绑定创建请求。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    character_id: str = Field(pattern=r"^char_[0-9]{6,}$")
    faction_id: str = Field(
        min_length=FACTION_BUSINESS_ID_MIN_LENGTH,
        max_length=FACTION_BUSINESS_ID_MAX_LENGTH,
        pattern=FACTION_BUSINESS_ID_PATTERN,
    )
    membership_type: Literal["primary", "secondary", "covert"]
    role_title: str | None = Field(default=None, max_length=100)
    public_status: str | None = Field(default=None, max_length=200)
    loyalty_level: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("role_title", "public_status", "notes", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        """去除可选文本首尾空白并把空串规范化为 None。

        Args:
            value: 客户端提交的可选文本。

        Returns:
            去除首尾空白后的文本，或 None。
        """
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None


class CharacterFactionBindingResponseV1(BaseModel):
    """全书级角色—势力绑定公开响应。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    novel_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    binding_id: str = Field(pattern=r"^cfb_[0-9]{6,}$")
    character_id: str = Field(pattern=r"^char_[0-9]{6,}$")
    faction_id: str = Field(
        min_length=FACTION_BUSINESS_ID_MIN_LENGTH,
        max_length=FACTION_BUSINESS_ID_MAX_LENGTH,
        pattern=FACTION_BUSINESS_ID_PATTERN,
    )
    membership_type: Literal["primary", "secondary", "covert"]
    role_title: str | None = Field(default=None, max_length=100)
    public_status: str | None = Field(default=None, max_length=200)
    loyalty_level: int | None = Field(default=None, ge=1, le=5)
    joined_volume_id: None = None
    left_volume_id: None = None
    is_active: bool
    notes: str | None = Field(default=None, max_length=1000)
    sort_order: int = Field(ge=0)
    version: int = Field(ge=1)
    is_deleted: bool
    deleted_at: datetime | None = None
    deletion_sources: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


def serialize_character_faction_binding(
    binding: dict,
) -> CharacterFactionBindingResponseV1:
    """将 MongoDB 绑定文档转换为不暴露内部 _id 的公开字典。

    Args:
        binding: MongoDB 返回的角色—势力绑定文档。

    Returns:
        不含 MongoDB ``_id`` 和内部字段的严格公开响应。
    """
    public_fields = set(CharacterFactionBindingResponseV1.model_fields)
    serialized = {key: value for key, value in binding.items() if key in public_fields}
    if "novel_id" in serialized:
        serialized["novel_id"] = str(serialized["novel_id"])
    return CharacterFactionBindingResponseV1.model_validate(serialized)


def _translate_domain_error(exc: Exception) -> HTTPException:
    """把领域异常转换为稳定 HTTP 状态。

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
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DuplicateKeyError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, InvalidIdError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post(
    "/novel/{novel_id}",
    response_model=CharacterFactionBindingResponseV1,
)
async def create_binding(
    novel_id: str,
    request: CharacterFactionBindingCreateRequestV1,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> CharacterFactionBindingResponseV1:
    """创建一条全书级 active 核心角色—核心势力绑定。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        request: 经严格 Schema 校验的绑定创建请求。
        idempotency_key: 必填的创建幂等请求头。

    Returns:
        新创建的公开绑定文档。
    """
    try:
        binding = await CharacterFactionBindingService.create_binding(
            novel_id,
            request,
            idempotency_key=idempotency_key,
        )
        return serialize_character_faction_binding(binding)
    except (GenerationDomainError, NotFoundError, DuplicateKeyError, InvalidIdError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


@router.get(
    "/novel/{novel_id}",
    response_model=dict[str, list[CharacterFactionBindingResponseV1]],
)
async def list_bindings(
    novel_id: str,
    character_id: str | None = Query(default=None, pattern=r"^char_[0-9]{6,}$"),
    faction_id: str | None = Query(
        default=None,
        min_length=FACTION_BUSINESS_ID_MIN_LENGTH,
        max_length=FACTION_BUSINESS_ID_MAX_LENGTH,
        pattern=FACTION_BUSINESS_ID_PATTERN,
    ),
    membership_type: Literal["primary", "secondary", "covert"] | None = None,
    is_active: bool | None = True,
) -> dict[str, list[CharacterFactionBindingResponseV1]]:
    """列出小说内全书级角色—势力绑定。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        character_id: 可选角色业务 ID 过滤。
        faction_id: 可选势力业务 ID 过滤。
        membership_type: 可选成员类型过滤。
        is_active: 可选活动状态过滤。

    Returns:
        稳定排序的公开绑定文档列表。
    """
    try:
        bindings = await CharacterFactionBindingService.list_bindings(
            novel_id,
            character_id=character_id,
            faction_id=faction_id,
            membership_type=membership_type,
            is_active=is_active,
        )
        return {
            "data": [
                serialize_character_faction_binding(binding) for binding in bindings
            ]
        }
    except (NotFoundError, InvalidIdError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


@router.get(
    "/novel/{novel_id}/character/{character_id}",
    response_model=dict[str, list[CharacterFactionBindingResponseV1]],
)
async def list_bindings_by_character(
    novel_id: str,
    character_id: str,
) -> dict[str, list[CharacterFactionBindingResponseV1]]:
    """列出指定 active 核心角色的全书级势力绑定。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        character_id: 稳定角色业务 ID。

    Returns:
        该角色的公开绑定文档列表。
    """
    try:
        bindings = await CharacterFactionBindingService.list_bindings(
            novel_id,
            character_id=character_id,
        )
        return {
            "data": [
                serialize_character_faction_binding(binding) for binding in bindings
            ]
        }
    except (NotFoundError, InvalidIdError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


@router.get(
    "/novel/{novel_id}/faction/{faction_id}",
    response_model=dict[str, list[CharacterFactionBindingResponseV1]],
)
async def list_bindings_by_faction(
    novel_id: str,
    faction_id: str = Path(
        min_length=FACTION_BUSINESS_ID_MIN_LENGTH,
        max_length=FACTION_BUSINESS_ID_MAX_LENGTH,
        pattern=FACTION_BUSINESS_ID_PATTERN,
    ),
) -> dict[str, list[CharacterFactionBindingResponseV1]]:
    """列出指定 active 核心势力的全书级角色绑定。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        faction_id: 稳定势力业务 ID。

    Returns:
        该势力的公开绑定文档列表。
    """
    try:
        bindings = await CharacterFactionBindingService.list_bindings(
            novel_id,
            faction_id=faction_id,
        )
        return {
            "data": [
                serialize_character_faction_binding(binding) for binding in bindings
            ]
        }
    except (NotFoundError, InvalidIdError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


@router.get(
    "/novel/{novel_id}/{binding_id}",
    response_model=CharacterFactionBindingResponseV1,
)
async def get_binding(novel_id: str, binding_id: str) -> CharacterFactionBindingResponseV1:
    """获取一条全书级角色—势力绑定详情。

    Args:
        novel_id: 小说 MongoDB ObjectId 字符串。
        binding_id: 稳定绑定业务 ID。

    Returns:
        公开绑定文档。
    """
    try:
        binding = await CharacterFactionBindingService.get_binding(novel_id, binding_id)
        return serialize_character_faction_binding(binding)
    except (NotFoundError, InvalidIdError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc


__all__ = [
    "CharacterFactionBindingCreateRequestV1",
    "CharacterFactionBindingResponseV1",
    "router",
    "serialize_character_faction_binding",
]
