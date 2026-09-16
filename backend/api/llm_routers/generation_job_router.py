"""生成任务状态路由：查询、取消与重连。

前端发起生成后拿到 job_id，即使切换到其他页面、刷新浏览器，
回到原工作区也能通过本组接口重新接上仍在运行的任务（或已完成的候选结果）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.api.error_contract import to_http_exception
from backend.services.llm.character_generation_service import (
    CHARACTER_RELATION_WORKFLOW_NAME,
    CORE_CHARACTER_WORKFLOW_NAME,
)
from backend.services.llm.generation_job_service import generation_job_service

router = APIRouter(prefix="/api/llm/generation-jobs", tags=["generation-jobs"])


class ResumeRequest(BaseModel):
    """续跑被中断任务的请求。"""

    novel_id: Optional[str] = Field(default=None, max_length=80)
    request_hash: Optional[str] = Field(default=None, max_length=128)


class CancelRequest(BaseModel):
    """取消任务请求；可携带小说 ID 做归属校验。"""

    novel_id: Optional[str] = Field(default=None, max_length=80)

# 允许通过 /active 恢复的工作流：与前端角色卡工作区一一对应
RESUMABLE_WORKFLOWS: tuple[str, ...] = (
    CORE_CHARACTER_WORKFLOW_NAME,
    CHARACTER_RELATION_WORKFLOW_NAME,
)


@router.get("/active")
async def find_active_job(
    novel_id: str = Query("", description="小说 ObjectId，留空表示不限定小说"),
    workflow: str = Query(
        "",
        description="逗号分隔的工作流名称，留空表示所有可恢复工作流",
    ),
) -> dict:
    """查询某小说下最近一个尚未消费的生成任务（含刚完成的结果）。

    Args:
        novel_id: 小说 ObjectId 字符串。
        workflow: 逗号分隔的工作流名称过滤条件。

    Returns:
        ``{"job": 任务快照 | null}``；没有可恢复任务时 job 为 null。
    """
    workflows = tuple(
        item.strip() for item in workflow.split(",") if item.strip()
    ) or RESUMABLE_WORKFLOWS
    job = await generation_job_service.find_resumable_job(
        novel_id=novel_id or None,
        workflows=workflows,
    )
    return {"job": job}


@router.get("/history")
async def list_generation_jobs(
    novel_id: str = Query("", description="小说 ObjectId，留空表示不限定"),
    workflow: str = Query("", description="工作流名称过滤"),
    status: str = Query("", description="任务状态过滤"),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
) -> dict:
    """列出历史生成任务，按创建时间倒序。"""
    try:
        return await generation_job_service.list_jobs(
            novel_id=novel_id or None,
            workflow=workflow or None,
            status=status or None,
            limit=limit,
            skip=skip,
        )
    except Exception as exc:
        raise to_http_exception(exc)


@router.post("/{job_id}/resume")
async def resume_generation_job(job_id: str, req: ResumeRequest) -> dict:
    """从检查点续跑被中断的生成任务。

    只有 interrupted / failed 状态可续跑；小说与请求指纹不一致时拒绝。
    """
    try:
        job = await generation_job_service.resume_job(
            job_id,
            novel_id=req.novel_id,
            request_hash=req.request_hash,
        )
    except Exception as exc:
        raise to_http_exception(exc)
    if job is None:
        raise HTTPException(status_code=404, detail="生成任务不存在或已被清理")
    return {"job": job}


@router.get("/{job_id}")
async def get_generation_job(job_id: str) -> dict:
    """读取生成任务快照（进度、状态、失败原因与最终结果）。"""
    job = await generation_job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="生成任务不存在或已被清理")
    return {"job": job}


@router.post("/{job_id}/cancel")
async def cancel_generation_job(job_id: str, req: CancelRequest) -> dict:
    """取消仍在运行的生成任务，已完成的真实进度会被保留。

    携带 novel_id 时会校验任务归属，跨小说任务不可取消（JOB_SCOPE_MISMATCH）。
    """
    try:
        job = await generation_job_service.cancel_job(job_id, novel_id=req.novel_id)
    except Exception as exc:
        raise to_http_exception(exc)
    if job is None:
        raise HTTPException(status_code=404, detail="生成任务不存在或已被清理")
    return {"job": job}


@router.post("/{job_id}/ack")
async def ack_generation_job(job_id: str) -> dict:
    """确认任务结果已被消费，避免下次进入工作区重复弹出历史候选。"""
    job = await generation_job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="生成任务不存在或已被清理")
    await generation_job_service.ack_job(job_id)
    return {"success": True}


__all__ = ["router"]
