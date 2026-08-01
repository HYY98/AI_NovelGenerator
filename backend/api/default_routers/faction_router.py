from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from typing import Dict, List, Optional

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.llm.schemas.novel_pydantic import CoreFactionsResultSchema
from backend.services.novel.faction_service import FactionService

router = APIRouter(prefix="/api/factions", tags=["factions"])

FACTION_PUBLIC_FIELDS = frozenset(
    {
        "_id",
        "novel_id",
        "faction_id",
        "name",
        "alias",
        "faction_type",
        "level_type",
        "parent_faction_id",
        "positioning",
        "public_stance",
        "core_goal",
        "hidden_goal",
        "resources_and_advantages",
        "organization_style",
        "core_values",
        "conflict_with_mainline",
        "is_public",
        "influence_scope",
        "active_status",
        "expandability",
        "tags",
        "first_appearance_volume_id",
        "first_appearance_chapter_id",
        "sort_order",
        "extra",
        "version",
        "is_deleted",
        "deleted_at",
        "deletion_sources",
        "created_at",
        "updated_at",
    }
)


class CreateFactionRequest(BaseModel):
    name: str
    alias: Optional[List[str]] = None
    faction_type: Optional[str] = None
    level_type: Optional[str] = None
    parent_faction_id: Optional[str] = None
    positioning: Optional[str] = None
    public_stance: Optional[str] = None
    core_goal: Optional[str] = None
    hidden_goal: Optional[str] = None
    resources_and_advantages: Optional[List[str]] = None
    organization_style: Optional[str] = None
    core_values: Optional[List[str]] = None
    conflict_with_mainline: Optional[str] = None
    is_public: Optional[bool] = None
    influence_scope: Optional[str] = None
    active_status: Optional[str] = None
    expandability: Optional[str] = None
    tags: Optional[List[str]] = None
    first_appearance_volume_id: Optional[str] = None
    first_appearance_chapter_id: Optional[str] = None
    sort_order: Optional[int] = None
    extra: Optional[Dict] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """拒绝空白势力名称并返回去除首尾空白的值。

        Args:
            value: 调用方提交的势力名称。

        Returns:
            首尾空白已清理的非空名称。
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("势力名称不能为空")
        return normalized


class UpdateFactionRequest(BaseModel):
    expected_version: int = Field(ge=1, strict=True)
    name: Optional[str] = None
    alias: Optional[List[str]] = None
    faction_type: Optional[str] = None
    level_type: Optional[str] = None
    parent_faction_id: Optional[str] = None
    positioning: Optional[str] = None
    public_stance: Optional[str] = None
    core_goal: Optional[str] = None
    hidden_goal: Optional[str] = None
    resources_and_advantages: Optional[List[str]] = None
    organization_style: Optional[str] = None
    core_values: Optional[List[str]] = None
    conflict_with_mainline: Optional[str] = None
    is_public: Optional[bool] = None
    influence_scope: Optional[str] = None
    active_status: Optional[str] = None
    expandability: Optional[str] = None
    tags: Optional[List[str]] = None
    first_appearance_volume_id: Optional[str] = None
    first_appearance_chapter_id: Optional[str] = None
    sort_order: Optional[int] = None
    extra: Optional[Dict] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> str:
        """在 DTO 层拒绝显式 null 或仅含空白的更新名称。

        Args:
            value: 更新请求中的可选势力名称。

        Returns:
            首尾空白已清理的非空名称。
        """
        if value is None or not value.strip():
            raise ValueError("势力名称不能为空")
        return value.strip()


class RestoreFactionRequest(BaseModel):
    """使用乐观锁恢复回收站势力的请求。"""

    expected_version: int = Field(ge=1, strict=True)


class BatchUpdateSortOrderRequest(BaseModel):
    sort_map: Dict[str, int]


def _serialize_faction(faction: dict) -> dict:
    """按公开字段白名单序列化阵营并过滤内部并发护栏。

    Args:
        faction: MongoDB 阵营文档。

    Returns:
        不修改输入且可 JSON 序列化的公开阵营文档。
    """
    serialized = {
        field: value
        for field, value in faction.items()
        if field in FACTION_PUBLIC_FIELDS
    }
    if "_id" in serialized:
        serialized["_id"] = str(serialized["_id"])
    if "novel_id" in serialized:
        serialized["novel_id"] = str(serialized["novel_id"])
    return serialized


def _serialize_factions(factions: list) -> list:
    """批量序列化阵营列表。

    Args:
        factions: MongoDB 阵营文档列表。

    Returns:
        可 JSON 序列化的阵营文档列表。
    """
    return [_serialize_faction(faction) for faction in factions]


def _serialize_relation(relation: dict) -> dict:
    """将阵营关系文档中的 ObjectId 转为字符串。

    Args:
        relation: MongoDB 阵营关系文档。

    Returns:
        可 JSON 序列化的阵营关系文档。
    """
    if "_id" in relation:
        relation["_id"] = str(relation["_id"])
    if "novel_id" in relation:
        relation["novel_id"] = str(relation["novel_id"])
    return relation


def _serialize_relations(relations: list) -> list:
    """批量序列化阵营关系列表。

    Args:
        relations: MongoDB 阵营关系文档列表。

    Returns:
        可 JSON 序列化的阵营关系文档列表。
    """
    for relation in relations:
        _serialize_relation(relation)
    return relations


@router.post("/novel/{novel_id}/create")
async def create_faction(novel_id: str, req: CreateFactionRequest):
    """创建一个新阵营，挂载到指定小说下。

    Args:
        novel_id: 小说 ObjectId 字符串。
        req: 阵营创建请求。

    Returns:
        新阵营的 MongoDB id、业务 faction_id 和消息。
    """
    data = req.model_dump(exclude_unset=True)
    try:
        faction_oid, faction_id = await FactionService.create_faction(novel_id, data)
        return {"id": faction_oid, "faction_id": faction_id, "message": "Faction created"}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, InvalidIdError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/novel/{novel_id}/bulk-core-with-relations")
async def bulk_create_core_factions_with_relations(novel_id: str, req: CoreFactionsResultSchema):
    """批量创建全书核心阵营和阵营关系。

    Args:
        novel_id: 小说 ObjectId 字符串。
        req: 已校验的核心阵营生成结果。

    Returns:
        新创建的核心阵营与阵营关系。
    """
    try:
        result = await FactionService.bulk_create_core_factions_with_relations(
            novel_id,
            req.model_dump(),
        )
        return {
            "factions": _serialize_factions(result["factions"]),
            "faction_relations": _serialize_relations(result["faction_relations"]),
        }
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, InvalidIdError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/novel/{novel_id}")
async def get_factions_by_novel(novel_id: str):
    """获取指定小说下的所有阵营列表。

    Args:
        novel_id: 小说 ObjectId 字符串。

    Returns:
        阵营列表响应。
    """
    try:
        factions = await FactionService.get_factions_by_novel(novel_id)
        return {"data": _serialize_factions(factions)}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/novel/{novel_id}/level/{level_type}")
async def get_factions_by_level_type(novel_id: str, level_type: str):
    """获取指定小说下特定层级类型的阵营列表。

    Args:
        novel_id: 小说 ObjectId 字符串。
        level_type: 阵营层级类型。

    Returns:
        阵营列表响应。
    """
    try:
        factions = await FactionService.get_factions_by_level_type(novel_id, level_type)
        return {"data": _serialize_factions(factions)}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/novel/{novel_id}/trash")
async def get_deleted_factions(novel_id: str, level_type: Optional[str] = None):
    """获取指定小说下已软删除的阵营列表。

    Args:
        novel_id: 小说 ObjectId 字符串。
        level_type: 可选阵营层级类型。

    Returns:
        已软删除阵营列表响应。
    """
    try:
        factions = await FactionService.get_deleted_factions_by_level_type(novel_id, level_type)
        return {"data": _serialize_factions(factions)}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/novel/{novel_id}/children/{parent_faction_id}")
async def get_child_factions(novel_id: str, parent_faction_id: str):
    """获取指定父级阵营的所有直接子阵营。

    Args:
        novel_id: 小说 ObjectId 字符串。
        parent_faction_id: 父级业务阵营 ID。

    Returns:
        子阵营列表响应。
    """
    try:
        factions = await FactionService.get_child_factions(novel_id, parent_faction_id)
        return {"data": _serialize_factions(factions)}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/novel/{novel_id}/batch-sort")
async def batch_update_sort_order(novel_id: str, req: BatchUpdateSortOrderRequest):
    """批量更新阵营的排序权重。

    Args:
        novel_id: 小说 ObjectId 字符串。
        req: 批量排序更新请求。

    Returns:
        被更新的阵营数量。
    """
    try:
        updated = await FactionService.batch_update_sort_order(novel_id, req.sort_map)
        return {"updated_count": updated}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/novel/{novel_id}/{faction_id}")
async def get_faction(novel_id: str, faction_id: str):
    """根据 novel_id + faction_id 获取单个阵营详情。

    Args:
        novel_id: 小说 ObjectId 字符串。
        faction_id: 业务层阵营 ID。

    Returns:
        阵营详情。
    """
    try:
        faction = await FactionService.get_faction(novel_id, faction_id)
        return _serialize_faction(faction)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/novel/{novel_id}/{faction_id}")
async def update_faction(novel_id: str, faction_id: str, req: UpdateFactionRequest):
    """更新阵营的基础信息。

    Args:
        novel_id: 小说 ObjectId 字符串。
        faction_id: 业务层阵营 ID。
        req: 含 expected_version 的阵营更新请求。

    Returns:
        更新是否成功。
    """
    try:
        success = await FactionService.update_faction_info(
            novel_id,
            faction_id,
            req.model_dump(exclude_unset=True),
        )
        return {"success": success}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, InvalidIdError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/novel/{novel_id}/{faction_id}")
async def soft_delete_faction(
    novel_id: str,
    faction_id: str,
    expected_version: int = Query(..., ge=1),
):
    """软删除指定阵营。

    Args:
        novel_id: 小说 ObjectId 字符串。
        faction_id: 业务层阵营 ID。
        expected_version: 客户端基于的势力版本。

    Returns:
        软删除是否成功。
    """
    try:
        success = await FactionService.soft_delete_faction(
            novel_id,
            faction_id,
            expected_version=expected_version,
        )
        return {"success": success}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, InvalidIdError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/novel/{novel_id}/{faction_id}/restore")
async def restore_faction(
    novel_id: str,
    faction_id: str,
    req: RestoreFactionRequest,
):
    """恢复已软删除的阵营。

    Args:
        novel_id: 小说 ObjectId 字符串。
        faction_id: 业务层阵营 ID。
        req: 含 expected_version 的恢复请求。

    Returns:
        恢复是否成功。
    """
    try:
        success = await FactionService.restore_faction(
            novel_id,
            faction_id,
            expected_version=req.expected_version,
        )
        return {"success": success}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, InvalidIdError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/novel/{novel_id}/{faction_id}/hard")
async def hard_delete_faction(
    novel_id: str,
    faction_id: str,
    expected_version: int = Query(..., ge=1),
):
    """彻底物理删除指定阵营。

    Args:
        novel_id: 小说 ObjectId 字符串。
        faction_id: 业务层阵营 ID。
        expected_version: 客户端基于的势力版本。

    Returns:
        删除统计。
    """
    try:
        stats = await FactionService.hard_delete_faction(
            novel_id,
            faction_id,
            expected_version=expected_version,
        )
        return {"message": "Hard deleted successfully", "stats": stats}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except InvalidIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
