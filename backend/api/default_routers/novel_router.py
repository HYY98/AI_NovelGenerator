from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from backend.db.repositories.novel_repository import novel_repo
from backend.services.novel.novel_service import NovelService
from backend.db.errors import NotFoundError, InvalidIdError

router = APIRouter(prefix="/api/novels", tags=["novels"])

class CreateNovelRequest(BaseModel):
    title: str
    subtitle: Optional[str] = None
    genre: Optional[str] = "unclassified"
    tags: Optional[List[str]] = []
    introduction: Optional[str] = None
    summary: Optional[str] = None
    core_seed: Optional[str] = None
    worldview: Optional[str] = None
    writing_style: Optional[str] = None
    narrative_pov: Optional[str] = None
    era_background: Optional[str] = None
    cover_image: Optional[str] = None
    plot: Optional[str] = None
    tone: Optional[str] = None
    target_audience: Optional[str] = None
    core_idea: Optional[str] = None
    number_of_chapters: Optional[int] = None
    words_per_chapter: Optional[int] = None

class UpdateNovelRequest(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    genre: Optional[str] = None
    tags: Optional[List[str]] = None
    introduction: Optional[str] = None
    summary: Optional[str] = None
    core_seed: Optional[str] = None
    worldview: Optional[str] = None
    writing_style: Optional[str] = None
    narrative_pov: Optional[str] = None
    era_background: Optional[str] = None
    cover_image: Optional[str] = None
    plot: Optional[str] = None
    tone: Optional[str] = None
    target_audience: Optional[str] = None
    core_idea: Optional[str] = None
    number_of_chapters: Optional[int] = None
    words_per_chapter: Optional[int] = None

class StatusUpdate(BaseModel):
    status: str

class UpdateNovelVersionedRequest(BaseModel):
    """按乐观锁更新小说可编辑字段的请求。"""

    expected_version: int
    generation_mode: Optional[str] = None
    blueprint_status: Optional[str] = None
    blueprint_version: Optional[int] = None
    power_system_id: Optional[str] = None
    last_analyzed_chapter_version: Optional[int] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    genre: Optional[str] = None
    tags: Optional[List[str]] = None
    synopsis: Optional[str] = None
    outline: Optional[str] = None
    cover_image: Optional[str] = None
    world_time_period: Optional[str] = None
    world_atmosphere: Optional[str] = None
    world_social_structure: Optional[str] = None
    world_technology_level: Optional[str] = None
    power_system_name: Optional[str] = None
    power_system_description: Optional[str] = None

class BlueprintCreateRequest(BaseModel):
    """创建蓝图草稿的请求。"""

    generation_mode: Optional[str] = "guided"
    plot_summary: Optional[str] = None
    worldview: Optional[str] = None
    power_system: Optional[dict] = None
    selected_character_ids: Optional[List[str]] = []
    selected_faction_ids: Optional[List[str]] = []
    selected_setting_card_ids: Optional[List[str]] = []
    selected_relation_ids: Optional[List[str]] = []
    ai_suggestions: Optional[List[dict]] = []
    conflicts: Optional[List[dict]] = []
    source: Optional[str] = "user"

class BlueprintUpdateRequest(BaseModel):
    """更新未确认蓝图的请求。"""

    expected_version: int
    plot_summary: Optional[str] = None
    worldview: Optional[str] = None
    power_system: Optional[dict] = None
    selected_character_ids: Optional[List[str]] = None
    selected_faction_ids: Optional[List[str]] = None
    selected_setting_card_ids: Optional[List[str]] = None
    selected_relation_ids: Optional[List[str]] = None
    ai_suggestions: Optional[List[dict]] = None
    conflicts: Optional[List[dict]] = None
    source: Optional[str] = None
    status: Optional[str] = None

class BlueprintConfirmRequest(BaseModel):
    """确认蓝图的请求。

    增量新增：允许在确认时携带完整蓝图内容，先更新草稿再确认。
    """

    expected_version: Optional[int] = None
    force: bool = False
    confirm: bool = True
    plot_summary: Optional[str] = None
    worldview: Optional[str] = None
    power_system: Optional[dict] = None
    selected_character_ids: Optional[List[str]] = None
    selected_faction_ids: Optional[List[str]] = None
    selected_setting_card_ids: Optional[List[str]] = None
    selected_relation_ids: Optional[List[str]] = None
    ai_suggestions: Optional[List[dict]] = None
    conflicts: Optional[List[dict]] = None
    generation_mode: Optional[str] = None

@router.post("/create")
async def create_novel(req: CreateNovelRequest):
    """创建一个新的小说项目。"""
    data = req.model_dump(exclude_unset=True)
    try:
        novel_id = await novel_repo.create_novel(data)
        return {"id": novel_id, "message": "Novel created"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/list")
async def get_all_novels():
    """获取所有小说的列表，仅包含基础信息。"""
    novels = await novel_repo.get_all_novels()
    for novel in novels:
        if "_id" in novel:
            novel["_id"] = str(novel["_id"])
        novel["stats"] = {
            "chapter_count": novel.get("current_chapter_count", 0),
            "total_word_count": novel.get("current_word_count", 0)
        }
    return {"data": novels}

@router.get("/deleted/list")
async def get_deleted_novels():
    """获取所有已软删除的小说列表（回收站）。"""
    novels = await novel_repo.get_deleted_novels()
    for novel in novels:
        if "_id" in novel:
            novel["_id"] = str(novel["_id"])
        novel["stats"] = {
            "chapter_count": novel.get("current_chapter_count", 0),
            "total_word_count": novel.get("current_word_count", 0)
        }
    return {"data": novels}

@router.get("/{novel_id}")
async def get_novel(novel_id: str):
    """根据ID获取指定小说的详细信息。"""
    try:
        novel = await novel_repo.get_novel_by_id(novel_id)
        novel["_id"] = str(novel["_id"])
        novel["stats"] = {
            "chapter_count": novel.get("current_chapter_count", 0),
            "total_word_count": novel.get("current_word_count", 0)
        }
        return novel
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidIdError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{novel_id}")
async def update_novel(novel_id: str, req: UpdateNovelRequest):
    """更新指定小说的基础信息（如标题、简介等）。"""
    try:
        success = await novel_repo.update_novel_info(novel_id, req.model_dump(exclude_unset=True))
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{novel_id}/update")
async def update_novel_versioned(novel_id: str, req: UpdateNovelVersionedRequest):
    """按乐观锁更新小说可编辑字段，版本冲突返回 409。"""
    from backend.db.errors import DuplicateKeyError

    try:
        updated = await novel_repo.update_novel(
            novel_id,
            req.model_dump(exclude_unset=True, exclude={"expected_version"}),
            expected_version=req.expected_version,
        )
        if updated is not None:
            if "_id" in updated:
                updated["_id"] = str(updated["_id"])
            updated["stats"] = {
                "chapter_count": updated.get("current_chapter_count", 0),
                "total_word_count": updated.get("current_word_count", 0),
            }
        return updated
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DuplicateKeyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except InvalidIdError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{novel_id}/blueprint")
async def create_blueprint(novel_id: str, req: BlueprintCreateRequest):
    """为小说创建一条蓝图草稿。"""
    from backend.db.errors import DuplicateKeyError
    from backend.services.novel.blueprint_service import blueprint_service

    try:
        return await blueprint_service.create_blueprint(
            novel_id, req.model_dump(exclude_unset=True)
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (DuplicateKeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{novel_id}/blueprint/{blueprint_id}")
async def update_blueprint(novel_id: str, blueprint_id: str, req: BlueprintUpdateRequest):
    """更新一条尚未确认的蓝图，已确认蓝图需先创建新版本。"""
    from backend.db.errors import DuplicateKeyError
    from backend.services.novel.blueprint_service import blueprint_service

    try:
        return await blueprint_service.update_blueprint(
            novel_id,
            blueprint_id,
            req.model_dump(exclude_unset=True, exclude={"expected_version"}),
            expected_version=req.expected_version,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DuplicateKeyError as e:
        raise HTTPException(status_code=409, detail=str(e))

@router.post("/{novel_id}/blueprint/{blueprint_id}/confirm")
async def confirm_blueprint(novel_id: str, blueprint_id: str, req: BlueprintConfirmRequest):
    """确认蓝图，使其对后续生成可见。

    校验项：必填字段、blocking 冲突、实体 ID 归属、状态字段白名单。
    存在 blocking 冲突时需显式传 force=true 才会强制定稿，并写入审计日志。
    """
    from backend.db.errors import DuplicateKeyError
    from backend.services.novel.blueprint_service import blueprint_service

    try:
        update_data = req.model_dump(
            exclude_unset=True,
            exclude={"expected_version", "force", "confirm"},
        )
        if update_data:
            if req.expected_version is None:
                raise ValueError("更新蓝图内容时必须提供 expected_version")
            await blueprint_service.update_blueprint(
                novel_id,
                blueprint_id,
                update_data,
                expected_version=req.expected_version,
            )
        if not req.confirm:
            return await blueprint_service.get_blueprint(novel_id, blueprint_id)
        return await blueprint_service.confirm_blueprint(
            novel_id,
            blueprint_id,
            expected_version=req.expected_version,
            force=req.force,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DuplicateKeyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{novel_id}/blueprint/current")
async def get_current_blueprint(novel_id: str):
    """读取当前已确认的蓝图，不存在时返回 null。"""
    from backend.services.novel.blueprint_service import blueprint_service

    try:
        return await blueprint_service.get_current(novel_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.patch("/{novel_id}/status")
async def update_status(novel_id: str, req: StatusUpdate):
    """更新指定小说的状态（例如从草稿变为连载中）。"""
    try:
        success = await novel_repo.update_novel_status(novel_id, req.status)
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{novel_id}")
async def soft_delete(novel_id: str):
    """软删除指定的小说及将其放入回收站。"""
    try:
        success = await novel_repo.soft_delete_novel(novel_id)
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{novel_id}/restore")
async def restore_novel(novel_id: str):
    """从回收站中恢复（取消软删除）指定的小说。"""
    try:
        success = await novel_repo.restore_novel(novel_id)
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{novel_id}/hard")
async def hard_delete(novel_id: str):
    """彻底（物理）删除指定的小说及其所有关联数据，不可恢复。"""
    try:
        stats = await NovelService.hard_delete_novel(novel_id)
        return {"message": "Hard deleted successfully", "stats": stats}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
