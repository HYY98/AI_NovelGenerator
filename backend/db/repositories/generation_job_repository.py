"""AI 生成任务仓储（generation_jobs / generation_job_stats），异步 MongoDB。

生成任务的状态与结果落库后，前端切换页面或刷新浏览器都能凭 job_id 重新接上，
后端进程重启也能识别出被中断的历史任务，避免出现永远处于 running 的僵尸任务。

generation_job_stats 按 workflow + step 保存历史耗时与产出规模样本，
用于把进度百分比校准成真实产出速率（而不是写死的时间估算）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from bson import ObjectId

from backend.db.base import BaseRepository
from backend.db.utils import get_utc_now, to_object_id

logger = logging.getLogger(__name__)

JOBS_COLLECTION = "generation_jobs"
STATS_COLLECTION = "generation_job_stats"

# 任务状态：queued 已入队、running 生成中、interrupted 已中断可续跑、
# succeeded/failed/cancelled 为终态
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_INTERRUPTED = "interrupted"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"

# 活动态：这几个状态的任务可以被前端重新接上
ACTIVE_JOB_STATUSES: tuple[str, ...] = (JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)
# 可续跑状态：中断的任务保留检查点，不直接判死
RESUMABLE_JOB_STATUSES: tuple[str, ...] = (
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_INTERRUPTED,
)


class GenerationJobRepository(BaseRepository):
    """AI 生成任务的数据访问层。"""

    def __init__(self) -> None:
        super().__init__(JOBS_COLLECTION)

    @property
    def stats_collection(self):
        """历史样本集合（按 workflow 聚合的耗时与产出规模）。"""
        from backend.db.mongo import get_database

        return get_database()[STATS_COLLECTION]

    @staticmethod
    def _normalize_novel_id(novel_id: Any) -> Optional[ObjectId]:
        """把小说 ID 统一成 ObjectId；缺失或非法时返回 None。"""
        if novel_id in (None, ""):
            return None
        try:
            return to_object_id(novel_id)
        except Exception:
            logger.warning("生成任务的 novel_id 非法，已按无小说归属处理")
            return None

    async def insert_job(self, document: Dict[str, Any]) -> str:
        """写入一条新建的生成任务。

        Args:
            document: 任务文档，需包含 job_id、workflow、novel_id、status 与 steps。

        Returns:
            命中的 job_id。
        """
        prepared = self._prepare_audit_fields_for_insert(document)
        prepared["novel_id"] = self._normalize_novel_id(prepared.get("novel_id"))
        await self.collection.insert_one(prepared)
        return str(prepared.get("job_id") or "")

    async def update_job(self, job_id: str, fields: Dict[str, Any]) -> int:
        """按 job_id 更新任务字段，返回实际匹配数。

        Args:
            job_id: 任务业务 ID。
            fields: 需要写入的字段（内部会补 updated_at 与心跳时间）。

        Returns:
            匹配的文档数；为 0 表示任务尚未落库，调用方需要补写。
        """
        payload = dict(fields)
        now = get_utc_now()
        payload["updated_at"] = now
        payload.setdefault("heartbeat_at", now)
        result = await self.collection.update_one({"job_id": job_id}, {"$set": payload})
        return int(result.matched_count)

    async def save_job(self, job_id: str, fields: Dict[str, Any]) -> int:
        """按 job_id 覆盖写入任务字段，任务不存在时补建（upsert）。

        这是早期「非 upsert 的 update」缺陷的修正：启动路径必须使用 insert_job
        先完整落库；save_job 作为兜底也必须是 upsert，避免"跑着的任务库里查不到"。

        Args:
            job_id: 任务业务 ID。
            fields: 需要写入的字段（内部会补 updated_at 与心跳时间）。

        Returns:
            实际生效的文档数（matched 或 upsert 均为 1）；异常直接抛出，不吞错。
        """
        payload = dict(fields)
        now = get_utc_now()
        payload["updated_at"] = now
        payload.setdefault("heartbeat_at", now)
        result = await self.collection.update_one(
            {"job_id": job_id},
            {"$set": payload, "$setOnInsert": {"job_id": job_id, "created_at": now}},
            upsert=True,
        )
        return 1 if (result.matched_count or result.upserted_id is not None) else 0

    async def mark_interrupted(
        self,
        job_id: str,
        *,
        reason: str = "",
        checkpoint: Optional[Dict[str, Any]] = None,
    ) -> int:
        """把任务标记为中断，保留检查点以便后续续跑。"""
        payload: Dict[str, Any] = {
            "status": JOB_STATUS_INTERRUPTED,
            "interrupted_at": get_utc_now(),
        }
        if reason:
            payload["error"] = reason
        if checkpoint is not None:
            payload["checkpoint"] = checkpoint
        return await self.update_job(job_id, payload)

    async def count_jobs(
        self,
        *,
        novel_id: Optional[Any] = None,
        workflow: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        """统计任务数量。"""
        query: Dict[str, Any] = {}
        normalized_novel_id = self._normalize_novel_id(novel_id)
        if normalized_novel_id is not None:
            query["novel_id"] = normalized_novel_id
        if workflow:
            query["workflow"] = workflow
        if status:
            query["status"] = status
        return await self.collection.count_documents(query)

    async def count_active_jobs(self, novel_id: Optional[Any] = None) -> int:
        """统计仍在排队或运行中的任务数量（用于并发限额）。

        Args:
            novel_id: 可选小说 ObjectId；传入时只统计该小说的活动任务。

        Returns:
            活动任务数量。
        """
        query: Dict[str, Any] = {"status": {"$in": list(ACTIVE_JOB_STATUSES)}}
        normalized_novel_id = self._normalize_novel_id(novel_id)
        if normalized_novel_id is not None:
            query["novel_id"] = normalized_novel_id
        return await self.collection.count_documents(query)

    async def list_jobs(
        self,
        *,
        novel_id: Optional[Any] = None,
        workflow: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        """列出历史任务，按创建时间倒序。"""
        query: Dict[str, Any] = {}
        normalized_novel_id = self._normalize_novel_id(novel_id)
        if normalized_novel_id is not None:
            query["novel_id"] = normalized_novel_id
        if workflow:
            query["workflow"] = workflow
        if status:
            query["status"] = status
        cursor = (
            self.collection.find(query)
            .sort([("created_at", -1)])
            .skip(max(0, skip))
            .limit(max(1, min(200, limit)))
        )
        return await cursor.to_list(length=limit)

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """按业务 ID 读取任务文档。"""
        if not job_id:
            return None
        return await self.collection.find_one({"job_id": job_id})

    async def find_active_job(
        self,
        novel_id: Optional[Any],
        workflows: Sequence[str],
    ) -> Optional[Dict[str, Any]]:
        """查找指定小说下最近的一个可恢复任务。

        同时覆盖“运行中/排队中”与“刚刚成功但前端还没消费”“刚失败”的任务，
        这样用户离开页面期间完成的结果回到页面时仍然能拿到。

        Args:
            novel_id: 小说 ObjectId 或字符串；为空时只按 workflow 匹配。
            workflows: 允许的工作流名称列表。

        Returns:
            最近一条尚未被前端确认消费的任务文档；没有时返回 None。
        """
        query: Dict[str, Any] = {
            "workflow": {"$in": list(workflows)},
            "acked": {"$ne": True},
            "status": {
                "$in": [
                    *RESUMABLE_JOB_STATUSES,
                    JOB_STATUS_SUCCEEDED,
                    JOB_STATUS_FAILED,
                ]
            },
        }
        normalized_novel_id = self._normalize_novel_id(novel_id)
        if normalized_novel_id is not None:
            query["novel_id"] = normalized_novel_id
        cursor = self.collection.find(query).sort([("created_at", -1)]).limit(1)
        documents = await cursor.to_list(length=1)
        return documents[0] if documents else None

    async def ack_job(self, job_id: str) -> None:
        """把任务标记为已被前端消费，避免反复弹出历史结果。"""
        await self.save_job(job_id, {"acked": True})

    async def get_step_stats(self, workflow: str) -> Dict[str, Dict[str, Any]]:
        """读取某工作流的历史步骤样本。

        Args:
            workflow: 工作流名称，例如 create_core_characters_by_ai。

        Returns:
            step key 到历史样本（耗时、单条目字数、总字数、样本数）的映射。
        """
        document = await self.stats_collection.find_one({"workflow": workflow})
        steps = (document or {}).get("steps") or {}
        return {
            str(key): dict(value)
            for key, value in steps.items()
            if isinstance(value, dict)
        }

    async def upsert_step_stats(
        self,
        workflow: str,
        samples: Dict[str, Dict[str, Any]],
    ) -> None:
        """写入步骤样本（调用方已完成指数滑动平均）。

        Args:
            workflow: 工作流名称。
            samples: step key 到最新样本的映射。

        Returns:
            无。
        """
        if not samples:
            return
        now = get_utc_now()
        payload = {
            f"steps.{key}": {**value, "updated_at": now}
            for key, value in samples.items()
        }
        payload["workflow"] = workflow
        payload["updated_at"] = now
        try:
            await self.stats_collection.update_one(
                {"workflow": workflow},
                {"$set": payload, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )
        except Exception:
            logger.warning("写入步骤样本失败 workflow=%s", workflow, exc_info=True)

    async def delete_jobs_for_novel(self, novel_id: Any, workflows: Iterable[str]) -> int:
        """清理某小说在某工作流下的历史任务（仅用于测试与排障）。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            workflows: 工作流名称集合。

        Returns:
            实际删除的文档数。
        """
        normalized_novel_id = self._normalize_novel_id(novel_id)
        if normalized_novel_id is None:
            return 0
        result = await self.collection.delete_many(
            {"novel_id": normalized_novel_id, "workflow": {"$in": list(workflows)}}
        )
        return int(result.deleted_count)


generation_job_repo = GenerationJobRepository()
