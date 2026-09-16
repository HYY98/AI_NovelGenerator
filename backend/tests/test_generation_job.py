"""AI 生成任务（T04）单元测试。

覆盖：首次落库先于执行、重启后中断可查询而非 None、跨小说取消被拒、
检查点记录已完成步骤、并发限额拒绝。全部不依赖数据库，用假仓储替换模块级单例。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from backend.services.llm import generation_job_service as gj


class FakeJobRepo:
    """记录调用并返回固定结果的假仓储。"""

    def __init__(self) -> None:
        self.inserted: List[Dict[str, Any]] = []
        self.updated: List[tuple[str, Dict[str, Any]]] = []
        self.interrupted: List[str] = []
        self.find_active_result: Optional[Dict[str, Any]] = None
        self.jobs: Dict[str, Dict[str, Any]] = {}

    async def insert_job(self, document: Dict[str, Any]) -> str:
        self.inserted.append(dict(document))
        self.jobs[str(document.get("job_id") or "")] = dict(document)
        return str(document.get("job_id") or "")

    async def update_job(self, job_id: str, fields: Dict[str, Any]) -> int:
        self.updated.append((job_id, dict(fields)))
        return 1

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.jobs.get(job_id)

    async def find_active_job(self, novel_id, workflows) -> Optional[Dict[str, Any]]:
        return self.find_active_result

    async def mark_interrupted(
        self, job_id: str, reason: str = "", checkpoint=None
    ) -> int:
        self.interrupted.append(job_id)
        return 1

    async def get_step_stats(self, workflow: str) -> Dict[str, Dict[str, Any]]:
        return {}

    async def upsert_step_stats(self, workflow: str, samples) -> None:
        return None

    async def count_active_jobs(self, novel_id=None) -> int:
        return 0

    async def list_jobs(self, **kwargs) -> List[Dict[str, Any]]:
        return []

    async def count_jobs(self, **kwargs) -> int:
        return 0

    async def ack_job(self, job_id: str) -> None:
        return None


def test_start_job_inserts_before_running(monkeypatch):
    """启动任务必须先完整落库，再启动 runner。"""
    fake = FakeJobRepo()
    monkeypatch.setattr(gj, "generation_job_repo", fake)
    service = gj.GenerationJobService()

    async def runner(reporter):
        return {"ok": True}

    job = asyncio.run(
        service.start_job(
            workflow="w",
            novel_id="n1",
            steps=[gj.GenerationStepSpec(key="s1", target_items=1)],
            runner=runner,
            scope={"chapter_id": "c1"},
            input_snapshot={"idea": "x"},
            source_versions={"blueprint_version": 3},
            request_hash="h1",
        )
    )

    assert fake.inserted, "任务必须先落库"
    doc = fake.inserted[0]
    assert doc["job_id"] == job.job_id
    assert doc["status"] == gj.JOB_STATUS_QUEUED
    assert doc["novel_id"] == "n1"
    assert doc["workflow"] == "w"
    assert doc["scope"] == {"chapter_id": "c1"}
    assert doc["input_snapshot"] == {"idea": "x"}
    assert doc["source_versions"] == {"blueprint_version": 3}
    assert doc["request_hash"] == "h1"
    assert doc["checkpoint"] == {}
    assert doc["steps"], "落库文档应包含步骤快照"


def test_find_resumable_marks_interrupted_not_none(monkeypatch):
    """重启后库里仍是活动态的任务要转 interrupted 并返回快照，而不是 None。"""
    fake = FakeJobRepo()
    fake.find_active_result = {
        "job_id": "j1",
        "workflow": "w",
        "novel_id": "n1",
        "status": "running",
        "percent": 40,
        "indeterminate": False,
        "steps": [],
        "checkpoint": {"completed_steps": ["s1"]},
        "source_versions": {},
        "scope": {},
        "resume_count": 0,
        "acked": False,
    }
    monkeypatch.setattr(gj, "generation_job_repo", fake)
    service = gj.GenerationJobService()

    snapshot = asyncio.run(
        service.find_resumable_job(novel_id="n1", workflows=["w"])
    )

    assert snapshot is not None
    assert snapshot["status"] == "interrupted"
    assert snapshot["checkpoint"] == {"completed_steps": ["s1"]}
    assert "j1" in fake.interrupted


def test_cancel_cross_novel_rejected(monkeypatch):
    """跨小说任务不可取消：novel_id 不匹配时拒绝。"""
    fake = FakeJobRepo()
    monkeypatch.setattr(gj, "generation_job_repo", fake)
    service = gj.GenerationJobService()
    service._jobs["j1"] = gj.GenerationJob(
        job_id="j1", workflow="w", novel_id="novelA"
    )

    with pytest.raises(gj.ServiceError) as exc:
        asyncio.run(service.cancel_job("j1", novel_id="novelB"))
    assert exc.value.code == "JOB_SCOPE_MISMATCH"


def test_cancel_owned_job_allowed(monkeypatch):
    """归属匹配时允许取消，并转为 cancelled。"""
    fake = FakeJobRepo()
    monkeypatch.setattr(gj, "generation_job_repo", fake)
    service = gj.GenerationJobService()
    job = gj.GenerationJob(job_id="j1", workflow="w", novel_id="novelA")
    job.status = gj.JOB_STATUS_RUNNING
    service._jobs["j1"] = job

    snapshot = asyncio.run(service.cancel_job("j1", novel_id="novelA"))
    assert snapshot is not None
    assert snapshot["status"] == "cancelled"


def test_checkpoint_records_completed_steps():
    """完成步骤后检查点记录 completed_steps，续跑可据此跳过。"""
    job = gj.GenerationJob(job_id="j1", workflow="w")
    job.steps = [gj.GenerationStep(key="s1"), gj.GenerationStep(key="s2")]

    class FakeService:
        def persist_later(self, _job, *, force=False):
            return None

    reporter = gj.JobReporter(FakeService(), job)
    reporter.start_step("s1")
    assert not reporter.is_step_done("s1")
    reporter.finish_step(step_key="s1")

    assert reporter.is_step_done("s1")
    assert not reporter.is_step_done("s2")
    assert reporter.completed_steps == ["s1"]

    # 重复完成同一步骤不会重复记录
    reporter.finish_step(step_key="s1")
    assert reporter.completed_steps == ["s1"]

    reporter.checkpoint({"partial_result": {"a": 1}})
    assert job.checkpoint["partial_result"] == {"a": 1}
    assert job.checkpoint["completed_steps"] == ["s1"]


def test_concurrency_limit_rejects(monkeypatch):
    """活动任务数达到上限时拒绝启动，且不落库。"""
    fake = FakeJobRepo()
    monkeypatch.setattr(gj, "generation_job_repo", fake)
    service = gj.GenerationJobService()
    service._jobs["j0"] = gj.GenerationJob(
        job_id="j0", workflow="w", novel_id="n0", status=gj.JOB_STATUS_RUNNING
    )
    monkeypatch.setattr(service, "_job_limits", lambda: (1, 1))

    async def runner(reporter):
        return {}

    with pytest.raises(gj.ServiceError) as exc:
        asyncio.run(
            service.start_job(
                workflow="w",
                novel_id="n0",
                steps=[gj.GenerationStepSpec(key="s1")],
                runner=runner,
            )
        )
    assert exc.value.code == "JOB_LIMIT_REACHED"
    assert fake.inserted == [], "超出限额时不应落库"
