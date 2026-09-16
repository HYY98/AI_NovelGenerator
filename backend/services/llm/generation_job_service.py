"""AI 生成任务服务：把长耗时生成从 HTTP 连接里拆出来。

问题背景：原来的生成接口是「请求-响应式 SSE」，浏览器一离开页面就断开连接，
后端随之取消任务，前端组件状态也一起丢失，于是"切走再回来进度就没了"。

现在的做法：
1. 生成任务在后台 asyncio 任务里跑，与 HTTP 连接解耦，断开连接不会中断生成；
2. 任务状态与最终结果落库，前端凭 job_id（或"某小说下最近的可恢复任务"）重新接上；
3. 进度百分比由**真实产出**驱动：已完成条目数 / 目标条目数，
   当前条目内部再按「已接收字数 / 历史单条目平均字数」推进，
   历史样本来自 generation_job_stats，因此进度会随实际速率自适应，
   而不是写死的假进度条。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence
from uuid import uuid4

from backend.api.error_contract import ServiceError
from backend.config.config import get_config_value
from backend.db.repositories.generation_job_repository import (
    ACTIVE_JOB_STATUSES,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    RESUMABLE_JOB_STATUSES,
    generation_job_repo,
)
from backend.db.utils import get_utc_now

logger = logging.getLogger(__name__)

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_ERROR = "error"

# 进度落库节流：内存态实时更新，数据库只按间隔写入，避免每来一个分片就写一次
PERSIST_INTERVAL_SECONDS = 2.0
# 无历史样本时的兜底估计值
DEFAULT_ITEM_CHARS = 800
DEFAULT_TOTAL_CHARS = 3000
# 推理模型思考阶段的兜底预计推理字数（历史样本会用 EMA 覆盖）
DEFAULT_THINKING_CHARS = 2000
# 思考阶段最多贡献该步骤的进度比例：推理内容按历史样本折算，正文产出仍占大头
THINKING_PHASE_WEIGHT = 0.3
# 指数滑动平均系数（新样本占比）
STATS_ALPHA = 0.3
# 并发限额默认值：全局上限与单小说上限（可通过 config.yaml 的 generation_jobs 段覆盖）
DEFAULT_MAX_CONCURRENT_JOBS = 10
DEFAULT_MAX_CONCURRENT_JOBS_PER_NOVEL = 2


def _iso(value: Any) -> Optional[str]:
    """把 datetime 转成 ISO 字符串。"""
    return value.isoformat() if value is not None else None


@dataclass
class GenerationStepSpec:
    """任务启动时声明的步骤规格。"""

    key: str
    weight: float = 1.0
    # 目标条目数：已知要产出多少条时填入，进度按「完成条目数 / 目标」计算
    target_items: Optional[int] = None
    # 单条目预计字数：用于当前条目内部推进（缺省时按 default 兜底）
    expected_item_chars: Optional[int] = None
    # 无条目目标时的总字数预计（例如整段文本生成）
    expected_total_chars: Optional[int] = None
    # 推理模型思考阶段的预计推理字数（缺省时按 default 兜底）
    expected_thinking_chars: Optional[int] = None
    # 历史平均耗时（秒）：用于推算"预计进度/预计剩余时间"
    expected_duration_seconds: Optional[float] = None


@dataclass
class GenerationStep:
    """任务中一个步骤的实时状态。"""

    key: str
    weight: float = 1.0
    status: str = STEP_PENDING
    # 流中已出现的条目数：出现第 N 条标记说明前 N-1 条已经写完，最后一条仍在生成
    started_items: int = 0
    target_items: Optional[int] = None
    chars: int = 0
    # 推理内容累计字数：推理模型思考阶段也属于真实产出，用它避免长时间 0%
    thinking_chars: int = 0
    expected_item_chars: Optional[int] = None
    expected_total_chars: Optional[int] = None
    expected_thinking_chars: Optional[int] = None
    # 历史平均耗时（秒）：完成过的同步骤样本，用来推算预计进度与剩余时间
    expected_duration_seconds: Optional[float] = None
    inner: float = 0.0
    # 进度只增不减：阶段切换时不允许百分比回退
    best_inner: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    # 条目推进辅助：记录当前条目开始时的累计字数
    chars_at_item_start: int = 0

    @property
    def elapsed_seconds(self) -> Optional[float]:
        """步骤已耗时（秒）。"""
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.perf_counter()
        return round(end - self.started_at, 1)

    @property
    def completed_items(self) -> int:
        """已完成条目数（正在生成的那一条不算）。"""
        if self.status == STEP_DONE:
            return max(self.started_items, self.target_items or 0)
        return max(0, self.started_items - 1)

    @property
    def inflight_ratio(self) -> float:
        """当前正在生成的条目完成度（按累计字数与历史单条目字数推算）。"""
        if not self.target_items:
            return 0.0
        per_item = self.expected_item_chars or DEFAULT_ITEM_CHARS
        if per_item <= 0:
            return 0.0
        return min(1.0, max(0, self.chars - self.chars_at_item_start) / per_item)

    @property
    def phase(self) -> str:
        """当前所处阶段，供前端展示不同文案。

        Returns:
            pending（未开始）、waiting（已提交等待首个分片）、thinking（模型思考中）、
            writing（正在产出正文）或 done。
        """
        if self.status == STEP_DONE:
            return "done"
        if self.status == STEP_PENDING:
            return "pending"
        if self.started_items == 0 and self.chars == 0:
            return "thinking" if self.thinking_chars > 0 else "waiting"
        return "writing"

    @property
    def thinking_ratio(self) -> float:
        """思考阶段完成度：真实推理字数 / 预计推理字数，最多贡献 THINKING_PHASE_WEIGHT。"""
        if not self.thinking_chars or self.completed_items > 0:
            return 0.0
        expected = self.expected_thinking_chars or DEFAULT_THINKING_CHARS
        if expected <= 0:
            return 0.0
        return min(1.0, self.thinking_chars / expected) * THINKING_PHASE_WEIGHT

    @property
    def estimated_percent(self) -> int:
        """按历史平均耗时推算的步骤完成度（0-100）。

        与按真实产出计算的 percent 相互独立：一个回答"做了多少活"，
        一个回答"按以往经验大概进行到哪一步"。
        """
        if self.status == STEP_DONE:
            return 100
        if self.status == STEP_PENDING:
            return 0
        expected = self.expected_duration_seconds
        if not expected or expected <= 0:
            return 0
        elapsed = self.elapsed_seconds or 0.0
        return max(0, min(99, round(elapsed / expected * 100)))

    @property
    def estimated_remaining_seconds(self) -> Optional[float]:
        """按历史平均耗时推算的该步骤剩余秒数；没有样本时返回 None。"""
        expected = self.expected_duration_seconds
        if self.status == STEP_DONE:
            return 0.0
        if not expected or expected <= 0:
            return None
        if self.status == STEP_PENDING:
            return round(float(expected), 1)
        elapsed = self.elapsed_seconds or 0.0
        # 超过历史耗时后不再给出负数，由前端按"可能很快完成"展示
        return round(max(0.0, expected - elapsed), 1)

    def recompute_inner(self) -> None:
        """按真实产出重算步骤内部完成度（0-1），并保持单调不减。"""
        if self.status == STEP_DONE:
            self.inner = 1.0
            self.best_inner = 1.0
            return
        target = self.target_items or 0
        candidate = 0.0
        if target > 0:
            inflight = self.inflight_ratio
            writing_ratio = (self.completed_items + inflight) / target
            candidate = max(writing_ratio, self.thinking_ratio)
        elif self.expected_total_chars:
            candidate = self.chars / self.expected_total_chars
        # 既没有条目目标也没有字数预期时保持不确定进度，前端用已用时与字数展示活动
        self.inner = min(0.99, max(candidate, self.best_inner))
        self.best_inner = self.inner

    def to_public(self) -> Dict[str, Any]:
        """转换成前端可用的快照字段。"""
        return {
            "key": self.key,
            "status": self.status,
            "phase": self.phase,
            "percent": round(self.inner * 100),
            "done_items": self.completed_items,
            "started_items": self.started_items,
            "target_items": self.target_items,
            "inflight_percent": round(self.inflight_ratio * 100),
            "chars": self.chars,
            "thinking_chars": self.thinking_chars,
            "expected_total_chars": self.expected_total_chars,
            "expected_item_chars": self.expected_item_chars,
            "expected_thinking_chars": self.expected_thinking_chars,
            "expected_duration_seconds": self.expected_duration_seconds,
            "estimated_percent": self.estimated_percent,
            "estimated_remaining_seconds": self.estimated_remaining_seconds,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
        }


@dataclass
class GenerationJob:
    """一个完整的生成任务。"""

    job_id: str
    workflow: str
    novel_id: Optional[str] = None
    steps: List[GenerationStep] = field(default_factory=list)
    status: str = JOB_STATUS_QUEUED
    result: Any = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    acked: bool = False
    created_at: Any = field(default_factory=get_utc_now)
    updated_at: Any = field(default_factory=get_utc_now)
    # 活跃时间用 perf_counter 计时，墙上时间单独保存用于展示与落库
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    started_wall_at: Optional[Any] = None
    finished_wall_at: Optional[Any] = None
    last_persisted_at: float = 0.0
    # v2.0：任务范围、输入快照、版本基线、检查点与续跑次数
    scope: Dict[str, Any] = field(default_factory=dict)
    input_snapshot: Dict[str, Any] = field(default_factory=dict)
    source_versions: Dict[str, Any] = field(default_factory=dict)
    request_hash: str = ""
    checkpoint: Dict[str, Any] = field(default_factory=dict)
    resume_count: int = 0
    # 用户主动取消与被动中断需要区分，前者是 cancelled，后者是 interrupted
    cancel_requested: bool = False

    # ------------------------------------------------------------------
    # 进度计算
    # ------------------------------------------------------------------
    def recompute(self) -> None:
        """按步骤权重汇总出整体完成度。"""
        for step in self.steps:
            step.recompute_inner()

    @property
    def percent(self) -> int:
        """整体百分比（0-100，按步骤权重加权）。"""
        total_weight = sum(step.weight for step in self.steps) or 1.0
        accumulated = 0.0
        for step in self.steps:
            if step.status == STEP_DONE:
                fraction = 1.0
            elif step.status in (STEP_RUNNING, STEP_ERROR):
                fraction = step.inner
            else:
                fraction = 0.0
            accumulated += step.weight * fraction
        return max(0, min(100, round(accumulated / total_weight * 100)))

    @property
    def indeterminate(self) -> bool:
        """是否存在可信总量：没有任何真实产出预期时前端退化为不确定进度。"""
        if self.status == JOB_STATUS_SUCCEEDED:
            return False
        for step in self.steps:
            if step.status in (STEP_PENDING, STEP_RUNNING):
                return not (step.target_items or step.expected_total_chars)
        return False

    @property
    def elapsed_seconds(self) -> Optional[float]:
        """任务已耗时（秒）。"""
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.perf_counter()
        return round(end - self.started_at, 1)

    @property
    def eta_seconds(self) -> Optional[float]:
        """按当前真实速率推算的剩余秒数；样本不足时返回 None。"""
        if self.status != JOB_STATUS_RUNNING:
            return None
        elapsed = self.elapsed_seconds or 0.0
        percent = self.percent
        if percent < 5 or elapsed <= 0:
            return None
        return round(elapsed * (100 - percent) / percent, 1)

    @property
    def estimated_percent(self) -> Optional[int]:
        """按历史平均耗时推算的整体完成度（0-100，按步骤权重加权）。

        与 percent 的区别：percent 由真实产出（推理/正文/条目）驱动，回答
        "已经产出多少"；这里由历史耗时驱动，回答"按以往经验大概进行到哪"。
        没有任何历史样本时返回 None，前端不展示预计进度。
        """
        if self.status == JOB_STATUS_SUCCEEDED:
            return 100
        if not any(step.expected_duration_seconds for step in self.steps):
            return None
        total_weight = sum(step.weight for step in self.steps) or 1.0
        accumulated = sum(
            step.weight * step.estimated_percent for step in self.steps
        )
        return max(0, min(99, round(accumulated / total_weight)))

    @property
    def estimated_remaining_seconds(self) -> Optional[float]:
        """按历史平均耗时推算的剩余秒数；完全没有样本时返回 None。"""
        values = [
            value
            for value in (step.estimated_remaining_seconds for step in self.steps)
            if value is not None
        ]
        if not values:
            return None
        return round(sum(values), 1)

    @property
    def estimated_total_seconds(self) -> Optional[float]:
        """预计总耗时（已用时 + 预计剩余）。"""
        remaining = self.estimated_remaining_seconds
        if remaining is None:
            return None
        return round((self.elapsed_seconds or 0.0) + remaining, 1)

    def to_public(self, *, include_result: bool = True) -> Dict[str, Any]:
        """转换成前端可用的任务快照。"""
        payload: Dict[str, Any] = {
            "job_id": self.job_id,
            "workflow": self.workflow,
            "novel_id": self.novel_id,
            "status": self.status,
            "percent": self.percent,
            "indeterminate": self.indeterminate,
            "elapsed_seconds": self.elapsed_seconds,
            "eta_seconds": self.eta_seconds,
            # 预计口径：基于历史平均耗时，与真实产出进度并存展示
            "estimated_percent": self.estimated_percent,
            "estimated_remaining_seconds": self.estimated_remaining_seconds,
            "estimated_total_seconds": self.estimated_total_seconds,
            "steps": [step.to_public() for step in self.steps],
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_wall_at),
            "finished_at": _iso(self.finished_wall_at),
            "updated_at": _iso(self.updated_at),
            "acked": self.acked,
            # v2.0：可恢复性与断点信息
            "resumable": self.status in RESUMABLE_JOB_STATUSES,
            "checkpoint": self.checkpoint or {},
            "request_hash": self.request_hash,
            "source_versions": self.source_versions or {},
            "scope": self.scope or {},
            "resume_count": self.resume_count,
        }
        if self.error:
            payload["error"] = self.error
            payload["error_code"] = self.error_code
        if include_result and self.status == JOB_STATUS_SUCCEEDED:
            payload["result"] = self.result
        return payload


class JobReporter:
    """传给生成流程的进度上报器。"""

    def __init__(self, service: "GenerationJobService", job: GenerationJob) -> None:
        self._service = service
        self._job = job

    def _find_step(self, key: str) -> Optional[GenerationStep]:
        """按 key 找到步骤；key 为空时默认第一个未完成步骤。"""
        if key:
            for step in self._job.steps:
                if step.key == key:
                    return step
            return None
        for step in self._job.steps:
            if step.status != STEP_DONE:
                return step
        return self._job.steps[-1] if self._job.steps else None

    def start_step(self, key: str = "") -> None:
        """把步骤标记为运行中并落库（步骤切换是重要的可观察节点）。"""
        step = self._find_step(key)
        if step is None:
            return
        step.status = STEP_RUNNING
        step.started_at = time.perf_counter()
        step.chars_at_item_start = step.chars
        self._job.recompute()
        self._service.persist_later(self._job, force=True)

    def progress(
        self,
        *,
        step_key: str = "",
        chars: Optional[int] = None,
        thinking_chars: Optional[int] = None,
        started_items: Optional[int] = None,
        target_items: Optional[int] = None,
    ) -> None:
        """上报真实产出：累计正文字数、推理字数、已出现条目数与目标条目数。"""
        step = self._find_step(step_key)
        if step is None:
            return
        if target_items is not None and target_items > 0:
            step.target_items = target_items
        if chars is not None:
            step.chars = max(step.chars, int(chars))
        if thinking_chars is not None:
            step.thinking_chars = max(step.thinking_chars, int(thinking_chars))
        if started_items is not None:
            incoming = max(0, int(started_items))
            if incoming > step.started_items:
                step.started_items = incoming
                # 条目推进说明下一条目从这里开始，inflight 按新条目的增量字数计算
                step.chars_at_item_start = step.chars
        self._job.recompute()
        self._service.persist_later(self._job)

    def finish_step(
        self,
        *,
        step_key: str = "",
        chars: Optional[int] = None,
        thinking_chars: Optional[int] = None,
        started_items: Optional[int] = None,
    ) -> None:
        """标记步骤完成。"""
        step = self._find_step(step_key)
        if step is None:
            return
        if chars is not None:
            step.chars = max(step.chars, int(chars))
        if thinking_chars is not None:
            step.thinking_chars = max(step.thinking_chars, int(thinking_chars))
        if started_items is not None:
            step.started_items = max(step.started_items, int(started_items))
        step.status = STEP_DONE
        step.finished_at = time.perf_counter()
        step.inner = 1.0
        self._job.recompute()
        # v2.0：每完成一步就写检查点，中断续跑时可据此跳过已完成内容
        completed = list(self._job.checkpoint.get("completed_steps") or [])
        if step.key not in completed:
            completed.append(step.key)
        self._job.checkpoint = {**self._job.checkpoint, "completed_steps": completed}
        self._service.persist_later(self._job, force=True)

    def fail_step(self, message: str, *, step_key: str = "") -> None:
        """标记步骤失败并保留已完成的真实进度。"""
        step = self._find_step(step_key)
        if step is None:
            return
        step.status = STEP_ERROR
        step.error = message
        step.finished_at = time.perf_counter()
        self._job.recompute()
        self._service.persist_later(self._job, force=True)

    def checkpoint(self, update: Dict[str, Any]) -> None:
        """写入任务检查点（已完成步骤、部分结果、源版本等）并落库。

        Args:
            update: 需要合并进检查点的键值；None/非 dict 会被忽略。
        """
        if not isinstance(update, dict):
            return
        self._job.checkpoint = {**self._job.checkpoint, **update}
        self._service.persist_later(self._job, force=True)

    @property
    def completed_steps(self) -> List[str]:
        """已完成的步骤 key 列表（含历史检查点），供续跑时跳过。"""
        return list(self._job.checkpoint.get("completed_steps") or [])

    def is_step_done(self, key: str) -> bool:
        """判断某步骤是否已完成，续跑时可据此跳过不重复已确认内容。"""
        return key in self.completed_steps or any(
            step.key == key and step.status == STEP_DONE for step in self._job.steps
        )


class GenerationJobService:
    """生成任务注册表与执行器（进程内单例）。"""

    def __init__(self) -> None:
        self._jobs: Dict[str, GenerationJob] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._runners: Dict[str, Callable[["JobReporter"], Awaitable[Any]]] = {}
        self._stats_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._persist_locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------
    async def start_job(
        self,
        *,
        workflow: str,
        steps: Sequence[GenerationStepSpec],
        runner: Callable[[JobReporter], Awaitable[Any]],
        novel_id: Optional[str] = None,
        scope: Optional[Dict[str, Any]] = None,
        input_snapshot: Optional[Dict[str, Any]] = None,
        source_versions: Optional[Dict[str, Any]] = None,
        request_hash: str = "",
    ) -> GenerationJob:
        """创建并启动一个后台生成任务，首次推进前先落库。

        Args:
            workflow: 工作流名称，用于历史样本与恢复查询。
            steps: 步骤规格（含目标条目数/字数预期）。
            runner: 实际执行体，接收 JobReporter 上报进度并返回最终结果。
            novel_id: 小说 ObjectId 字符串，可为空。
            scope: 任务范围（章节/卡片等），续跑时用于校验一致性。
            input_snapshot: 输入快照，便于中断后复现。
            source_versions: 生成时依赖的版本基线。
            request_hash: 请求指纹，用于识别同一请求的重复任务。

        Returns:
            已启动的任务对象。

        Raises:
            Exception: 首次落库失败时直接抛出，避免运行一个数据库里不存在的任务。
        """
        specs = list(steps)
        # v2.0：并发限额，超出时拒绝启动并返回明确错误，避免排队任务挤爆进程
        global_limit, per_novel_limit = self._job_limits()
        running_in_memory = sum(
            1 for item in self._jobs.values() if item.status in ACTIVE_JOB_STATUSES
        )
        if running_in_memory >= global_limit:
            raise ServiceError(
                "JOB_LIMIT_REACHED",
                f"同时运行的任务数已达上限 {global_limit}，请稍后再试",
                status=429,
            )
        if novel_id:
            same_novel_running = sum(
                1
                for item in self._jobs.values()
                if item.status in ACTIVE_JOB_STATUSES and item.novel_id == novel_id
            )
            if same_novel_running >= per_novel_limit:
                raise ServiceError(
                    "JOB_LIMIT_REACHED",
                    f"本小说已有 {per_novel_limit} 个任务在运行，请等待完成后再发起新生成",
                    status=429,
                )
        await self._apply_learned_expectations(workflow, specs)
        job = GenerationJob(
            job_id=f"job_{uuid4().hex[:16]}",
            workflow=workflow,
            novel_id=novel_id,
            scope=dict(scope or {}),
            input_snapshot=dict(input_snapshot or {}),
            source_versions=dict(source_versions or {}),
            request_hash=str(request_hash or ""),
            steps=[
                GenerationStep(
                    key=spec.key,
                    weight=spec.weight,
                    target_items=spec.target_items,
                    expected_item_chars=spec.expected_item_chars,
                    expected_total_chars=spec.expected_total_chars,
                    expected_thinking_chars=spec.expected_thinking_chars,
                    expected_duration_seconds=spec.expected_duration_seconds,
                )
                for spec in specs
            ],
        )
        # v2.0：任务必须先落库再运行，落库失败就不要启动后台协程
        await generation_job_repo.insert_job(
            {
                "job_id": job.job_id,
                "workflow": workflow,
                "novel_id": novel_id,
                "status": JOB_STATUS_QUEUED,
                "scope": dict(scope or {}),
                "input_snapshot": dict(input_snapshot or {}),
                "source_versions": dict(source_versions or {}),
                "request_hash": str(request_hash or ""),
                "checkpoint": {},
                "resume_count": 0,
                "percent": 0,
                "indeterminate": job.indeterminate,
                "steps": [step.to_public() for step in job.steps],
                "result": None,
                "error": None,
                "error_code": None,
                "acked": False,
                "created_at": job.created_at,
                "updated_at": job.created_at,
                "heartbeat_at": job.created_at,
            }
        )
        self._jobs[job.job_id] = job
        self._runners[job.job_id] = runner
        self._tasks[job.job_id] = asyncio.create_task(
            self._run_job(job, runner), name=f"generation-job:{job.job_id}"
        )
        logger.info(
            "生成任务已落库并启动 job_id=%s workflow=%s novel_id=%s",
            job.job_id,
            workflow,
            novel_id,
        )
        return job

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """读取任务快照，内存优先、其次读库。

        Args:
            job_id: 任务业务 ID。

        Returns:
            任务快照；任务不存在时返回 None。
        """
        job = self._jobs.get(job_id)
        if job is not None:
            job.recompute()
            return job.to_public()
        document = await generation_job_repo.get_job(job_id)
        if not document:
            return None
        return self._document_to_public(document)

    async def find_resumable_job(
        self,
        *,
        novel_id: Optional[str],
        workflows: Sequence[str],
    ) -> Optional[Dict[str, Any]]:
        """查找指定小说下最近一个尚未被消费的任务（用于离开页面后重新接上）。

        Args:
            novel_id: 小说 ObjectId 字符串。
            workflows: 允许的工作流名称列表。

        Returns:
            任务快照；没有可恢复任务时返回 None。
        """
        # 内存里的任务最新，优先扫描；已取消的任务不再具备恢复价值
        candidates = [
            job
            for job in self._jobs.values()
            if job.workflow in workflows
            and not job.acked
            and job.status != JOB_STATUS_CANCELLED
            and (novel_id is None or job.novel_id == novel_id)
        ]
        if candidates:
            candidates.sort(key=lambda item: item.created_at)
            job = candidates[-1]
            job.recompute()
            return job.to_public()

        document = await generation_job_repo.find_active_job(novel_id, workflows)
        if not document:
            return None
        status = str(document.get("status") or "")
        if status in ACTIVE_JOB_STATUSES:
            # 库里仍是活动态但进程内已找不到 → 后端重启或进程异常退出。
            # v2.0：不再判死为 failed，而是转成 interrupted 并保留检查点，允许续跑。
            await generation_job_repo.mark_interrupted(
                str(document.get("job_id") or ""),
                reason="生成任务已随服务重启中断，可从断点继续",
                checkpoint=document.get("checkpoint") or {},
            )
            logger.warning(
                "发现被中断的生成任务 job_id=%s workflow=%s",
                document.get("job_id"),
                document.get("workflow"),
            )
            document["status"] = JOB_STATUS_INTERRUPTED
        return self._document_to_public(document)

    async def cancel_job(
        self, job_id: str, *, novel_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """取消任务：中止后台协程并保留已完成的真实进度。

        Args:
            job_id: 任务业务 ID。
            novel_id: 调用方期望的小说 ID，用于校验归属，跨小说任务不可取消。

        Returns:
            取消后的任务快照；任务不存在时返回 None。

        Raises:
            ServiceError: 提供 novel_id 且任务不属于该小说时抛出 JOB_SCOPE_MISMATCH。
        """
        job = self._jobs.get(job_id)
        # v2.0：取消/删除必须校验小说归属，防止跨小说误操作
        if novel_id is not None:
            if job is not None:
                owned_novel = job.novel_id
            else:
                document = await generation_job_repo.get_job(job_id)
                owned_novel = str((document or {}).get("novel_id") or "") or None
            if owned_novel and str(owned_novel) != str(novel_id):
                raise ServiceError(
                    "JOB_SCOPE_MISMATCH",
                    "任务不属于该小说，无法取消",
                )
        task = self._tasks.get(job_id)
        if job is not None:
            # 先标记为用户主动取消，协程收到 CancelledError 时才知道该判 cancelled 而非 interrupted
            job.cancel_requested = True
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                logger.warning("等待生成任务取消时出现异常 job_id=%s", job_id, exc_info=True)
        if job is not None:
            if job.status in ACTIVE_JOB_STATUSES:
                job.status = JOB_STATUS_CANCELLED
            # 取消的任务没有结果可消费，直接标记已消费，避免下次进入又被恢复
            job.acked = True
            job.finished_at = job.finished_at or time.perf_counter()
            job.finished_wall_at = job.finished_wall_at or get_utc_now()
            job.recompute()
            await self._persist(job, force=True)
            return job.to_public()
        return await self.get_job(job_id)

    async def ack_job(self, job_id: str) -> None:
        """标记任务已被前端消费，避免重复弹出历史结果。"""
        job = self._jobs.get(job_id)
        if job is not None:
            job.acked = True
            await self._persist(job, force=True)
            return
        await generation_job_repo.ack_job(job_id)

    async def resume_job(
        self,
        job_id: str,
        *,
        novel_id: Optional[str] = None,
        request_hash: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """从检查点续跑一个被中断的任务。

        Args:
            job_id: 任务业务 ID。
            novel_id: 调用方期望的小说 ID，用于校验范围一致性。
            request_hash: 调用方期望的请求指纹，用于校验续跑的是同一请求。

        Returns:
            续跑后的任务快照；任务不存在时返回 None。

        Raises:
            ServiceError: 状态不允许续跑，或范围/指纹与任务不一致。
        """
        job = self._jobs.get(job_id)
        document = None
        if job is None:
            document = await generation_job_repo.get_job(job_id)
            if not document:
                return None

        status = job.status if job is not None else str((document or {}).get("status") or "")
        if status not in (JOB_STATUS_INTERRUPTED, JOB_STATUS_FAILED):
            raise ServiceError(
                "JOB_NOT_RESUMABLE",
                f"任务当前状态为 {status}，只有 interrupted/failed 的任务可以续跑",
            )
        if novel_id is not None:
            job_novel_id = job.novel_id if job is not None else str(
                (document or {}).get("novel_id") or ""
            )
            if str(job_novel_id) != str(novel_id):
                raise ServiceError(
                    "JOB_SCOPE_MISMATCH",
                    "任务所属小说与请求不一致，不能续跑",
                )
        if request_hash is not None:
            job_hash = job.request_hash if job is not None else str(
                (document or {}).get("request_hash") or ""
            )
            if job_hash and job_hash != str(request_hash):
                raise ServiceError(
                    "JOB_SCOPE_MISMATCH",
                    "任务请求指纹与当前请求不一致，请重新发起生成",
                )

        runner = self._runners.get(job_id)
        if job is None or runner is None:
            # 进程重启后执行体已丢失，保留 interrupted 状态与检查点，提示重新发起
            await generation_job_repo.mark_interrupted(
                job_id,
                reason="生成任务的执行上下文已随服务重启丢失，无法续跑，请重新发起",
                checkpoint=(document or {}).get("checkpoint") or {},
            )
            raise ServiceError(
                "JOB_NOT_RESUMABLE",
                "该任务的执行上下文已随服务重启丢失，无法续跑，请重新发起生成",
            )

        job.status = JOB_STATUS_RUNNING
        job.error = None
        job.error_code = None
        job.resume_count += 1
        job.finished_at = None
        job.finished_wall_at = None
        job.recompute()
        await self._persist(job, force=True)
        self._tasks[job_id] = asyncio.create_task(
            self._run_job(job, runner), name=f"generation-job:{job_id}"
        )
        logger.info(
            "生成任务已续跑 job_id=%s resume_count=%s", job_id, job.resume_count
        )
        return job.to_public()

    async def list_jobs(
        self,
        *,
        novel_id: Optional[str] = None,
        workflow: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> Dict[str, Any]:
        """列出历史任务，按创建时间倒序。"""
        documents = await generation_job_repo.list_jobs(
            novel_id=novel_id,
            workflow=workflow,
            status=status,
            limit=limit,
            skip=skip,
        )
        total = await generation_job_repo.count_jobs(
            novel_id=novel_id, workflow=workflow, status=status
        )
        return {
            "data": [self._document_to_public(doc) for doc in documents],
            "total": total,
            "limit": limit,
            "skip": skip,
        }

    async def mark_interrupted(self, job_id: str, *, reason: str = "") -> Optional[Dict[str, Any]]:
        """把任务标记为中断（保留检查点），用于运维介入或进程退出前的收尾。"""
        job = self._jobs.get(job_id)
        if job is not None:
            job.status = JOB_STATUS_INTERRUPTED
            job.error = reason or "生成被中断，可从断点继续"
            await self._persist(job, force=True)
            return job.to_public()
        matched = await generation_job_repo.mark_interrupted(job_id, reason=reason)
        if not matched:
            return None
        document = await generation_job_repo.get_job(job_id)
        return self._document_to_public(document) if document else None

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    async def _run_job(
        self,
        job: GenerationJob,
        runner: Callable[[JobReporter], Awaitable[Any]],
    ) -> None:
        """执行任务的协程：与 HTTP 请求无关，前端断开也不会被取消。"""
        job.status = JOB_STATUS_RUNNING
        job.started_at = time.perf_counter()
        job.started_wall_at = get_utc_now()
        job.recompute()
        await self._persist(job, force=True)
        reporter = JobReporter(self, job)
        try:
            result = await runner(reporter)
        except asyncio.CancelledError:
            if job.cancel_requested:
                job.status = JOB_STATUS_CANCELLED
                job.error = "生成已取消"
                logger.info("生成任务被取消 job_id=%s", job.job_id)
            else:
                # 非用户主动取消：保留检查点，允许从中断处续跑
                job.status = JOB_STATUS_INTERRUPTED
                job.error = "生成被中断，可从断点继续"
                logger.warning("生成任务被中断 job_id=%s", job.job_id)
        except BaseException as exc:  # noqa: BLE001 - 进程退出等场景也要留痕
            if isinstance(exc, Exception):
                job.status = JOB_STATUS_FAILED
                job.error, job.error_code = _describe_error(exc)
                logger.exception("生成任务失败 job_id=%s", job.job_id)
            else:
                job.status = JOB_STATUS_INTERRUPTED
                job.error = "生成被中断，可从断点继续"
                logger.warning("生成任务异常中断 job_id=%s", job.job_id)
                raise
        else:
            job.status = JOB_STATUS_SUCCEEDED
            job.result = _serialize_result(result)
            for step in job.steps:
                if step.status != STEP_DONE:
                    step.status = STEP_DONE
                    step.finished_at = step.finished_at or time.perf_counter()
                    step.inner = 1.0
            job.recompute()
            await self._record_stats(job)
        finally:
            job.finished_at = time.perf_counter()
            job.finished_wall_at = get_utc_now()
            job.recompute()
            await self._persist(job, force=True)
            self._tasks.pop(job.job_id, None)

    def _document_to_public(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """把历史任务文档转换成前端快照（不含内存态字段）。"""
        return {
            "job_id": document.get("job_id"),
            "workflow": document.get("workflow"),
            "novel_id": str(document.get("novel_id") or "") or None,
            "status": document.get("status"),
            "percent": int(document.get("percent") or 0),
            "indeterminate": bool(document.get("indeterminate")),
            "elapsed_seconds": document.get("elapsed_seconds"),
            "eta_seconds": document.get("eta_seconds"),
            "estimated_percent": document.get("estimated_percent"),
            "estimated_remaining_seconds": document.get("estimated_remaining_seconds"),
            "estimated_total_seconds": document.get("estimated_total_seconds"),
            "steps": document.get("steps") or [],
            "created_at": _iso(document.get("created_at")),
            "finished_at": _iso(document.get("finished_at")),
            "error": document.get("error"),
            "error_code": document.get("error_code"),
            "acked": bool(document.get("acked")),
            "result": document.get("result"),
            # v2.0：可恢复性与断点信息
            "resumable": str(document.get("status") or "") in RESUMABLE_JOB_STATUSES,
            "checkpoint": document.get("checkpoint") or {},
            "request_hash": document.get("request_hash") or "",
            "source_versions": document.get("source_versions") or {},
            "scope": document.get("scope") or {},
            "resume_count": int(document.get("resume_count") or 0),
            "updated_at": _iso(document.get("updated_at")),
        }

    def persist_later(self, job: GenerationJob, *, force: bool = False) -> None:
        """按节流间隔安排落库（内存态始终实时）。"""
        now = time.perf_counter()
        if not force and now - job.last_persisted_at < PERSIST_INTERVAL_SECONDS:
            return
        job.last_persisted_at = now
        asyncio.create_task(self._persist(job))  # noqa: RUF006 - 结果不需要等待

    async def _persist(self, job: GenerationJob, *, force: bool = False) -> None:
        """把任务快照写入数据库（同一任务串行，避免旧快照后到覆盖终态）。"""
        if not force:
            job.last_persisted_at = time.perf_counter()
        lock = self._persist_locks.setdefault(job.job_id, asyncio.Lock())
        async with lock:
            matched = await generation_job_repo.update_job(
                job.job_id,
                {
                    "workflow": job.workflow,
                    "novel_id": job.novel_id,
                    "status": job.status,
                    "scope": job.scope or {},
                    "input_snapshot": job.input_snapshot or {},
                    "source_versions": job.source_versions or {},
                    "request_hash": job.request_hash,
                    "checkpoint": job.checkpoint or {},
                    "resume_count": job.resume_count,
                    "percent": job.percent,
                    "indeterminate": job.indeterminate,
                    "elapsed_seconds": job.elapsed_seconds,
                    "eta_seconds": job.eta_seconds,
                    "estimated_percent": job.estimated_percent,
                    "estimated_remaining_seconds": job.estimated_remaining_seconds,
                    "estimated_total_seconds": job.estimated_total_seconds,
                    "steps": [step.to_public() for step in job.steps],
                    "result": job.result if job.status == JOB_STATUS_SUCCEEDED else None,
                    "error": job.error,
                    "error_code": job.error_code,
                    "acked": job.acked,
                    "started_at": job.started_wall_at,
                    "finished_at": job.finished_wall_at,
                },
            )
            if matched == 0:
                # 任务文档丢失（清理或异常）时补写，避免"跑着的任务库里查不到"
                logger.error(
                    "生成任务落库未命中，尝试补写 job_id=%s", job.job_id
                )
                await generation_job_repo.insert_job(
                    {
                        "job_id": job.job_id,
                        "workflow": job.workflow,
                        "novel_id": job.novel_id,
                        "status": job.status,
                        "scope": job.scope or {},
                        "input_snapshot": job.input_snapshot or {},
                        "source_versions": job.source_versions or {},
                        "request_hash": job.request_hash,
                        "checkpoint": job.checkpoint or {},
                        "resume_count": job.resume_count,
                        "steps": [step.to_public() for step in job.steps],
                        "created_at": job.created_at,
                        "updated_at": get_utc_now(),
                    }
                )
        if job.status not in ACTIVE_JOB_STATUSES:
            self._persist_locks.pop(job.job_id, None)

    @staticmethod
    def _job_limits() -> tuple[int, int]:
        """读取并发限额配置（全局上限、单小说上限）。

        Returns:
            (全局并发上限, 单小说并发上限)，两者至少为 1。
        """
        try:
            cfg = get_config_value("generation_jobs") or {}
        except Exception:  # noqa: BLE001 - 配置读取失败按默认值
            cfg = {}
        global_limit = int(cfg.get("max_concurrent") or DEFAULT_MAX_CONCURRENT_JOBS)
        per_novel_limit = int(
            cfg.get("max_concurrent_per_novel") or DEFAULT_MAX_CONCURRENT_JOBS_PER_NOVEL
        )
        return max(1, global_limit), max(1, per_novel_limit)

    async def _apply_learned_expectations(
        self,
        workflow: str,
        specs: List[GenerationStepSpec],
    ) -> None:
        """用历史样本补全步骤预期，让进度百分比贴近真实产出规模。"""
        stats = await self._get_stats(workflow)
        if not stats:
            return
        durations = [
            float(stats[spec.key].get("duration_ms_ema") or 0)
            for spec in specs
            if spec.key in stats and stats[spec.key].get("duration_ms_ema")
        ]
        for spec in specs:
            sample = stats.get(spec.key) or {}
            if spec.expected_item_chars is None and sample.get("item_chars_ema"):
                spec.expected_item_chars = int(sample["item_chars_ema"])
            if spec.expected_total_chars is None and sample.get("total_chars_ema"):
                spec.expected_total_chars = int(sample["total_chars_ema"])
            if spec.expected_thinking_chars is None and sample.get("thinking_chars_ema"):
                spec.expected_thinking_chars = int(sample["thinking_chars_ema"])
            # 预计耗时优先按"单条目耗时 × 本次目标条目数"折算，退化到整步平均耗时
            if spec.expected_duration_seconds is None:
                per_item_ms = sample.get("duration_per_item_ms_ema")
                expected_items = spec.target_items or int(sample.get("target_items") or 0)
                if per_item_ms and expected_items > 0:
                    spec.expected_duration_seconds = round(
                        float(per_item_ms) / 1000 * expected_items, 1
                    )
                elif sample.get("duration_ms_ema"):
                    spec.expected_duration_seconds = round(
                        float(sample["duration_ms_ema"]) / 1000, 1
                    )
            # 多步骤任务用历史耗时决定权重：耗时长的步骤占更大比例
            if len(specs) > 1 and durations:
                duration = float(sample.get("duration_ms_ema") or 0)
                if duration > 0:
                    spec.weight = max(0.1, duration)

    async def _get_stats(self, workflow: str) -> Dict[str, Dict[str, Any]]:
        """读取（并缓存）某工作流的历史样本。"""
        cached = self._stats_cache.get(workflow)
        if cached is not None:
            return cached
        stats = await generation_job_repo.get_step_stats(workflow)
        self._stats_cache[workflow] = stats
        return stats

    async def _record_stats(self, job: GenerationJob) -> None:
        """任务成功后更新历史样本（指数滑动平均）。"""
        samples: Dict[str, Dict[str, Any]] = {}
        for step in job.steps:
            if step.status != STEP_DONE:
                continue
            duration_ms = None
            if step.started_at is not None and step.finished_at is not None:
                duration_ms = (step.finished_at - step.started_at) * 1000
            produced_items = step.started_items or step.target_items or 0
            samples[step.key] = {
                "duration_ms_ema": round(duration_ms, 1) if duration_ms else None,
                # 单条目耗时：用于不同目标条目数之间的预计耗时折算
                "duration_per_item_ms_ema": (
                    round(duration_ms / produced_items, 1)
                    if duration_ms and produced_items
                    else None
                ),
                "item_chars_ema": (
                    round(step.chars / produced_items, 1)
                    if produced_items and step.chars
                    else None
                ),
                "total_chars_ema": step.chars or None,
                # 推理字数样本：让思考阶段的预计规模随实际观察自适应
                "thinking_chars_ema": step.thinking_chars or None,
                "target_items": produced_items or None,
            }
        if not samples:
            return
        current = await self._get_stats(job.workflow)
        merged: Dict[str, Dict[str, Any]] = {}
        for key, sample in samples.items():
            previous = current.get(key) or {}
            entry: Dict[str, Any] = {"samples": int(previous.get("samples") or 0) + 1}
            for field_name in (
                "duration_ms_ema",
                "duration_per_item_ms_ema",
                "item_chars_ema",
                "total_chars_ema",
                "thinking_chars_ema",
            ):
                incoming = sample.get(field_name)
                if incoming is None:
                    entry[field_name] = previous.get(field_name)
                    continue
                old_value = previous.get(field_name)
                entry[field_name] = (
                    round(float(old_value) * (1 - STATS_ALPHA) + float(incoming) * STATS_ALPHA, 1)
                    if old_value
                    else round(float(incoming), 1)
                )
            if sample.get("target_items"):
                entry["target_items"] = sample["target_items"]
            merged[key] = entry
        await generation_job_repo.upsert_step_stats(job.workflow, merged)
        self._stats_cache[job.workflow] = {**current, **merged}


def _describe_error(exc: Exception) -> tuple[str, Optional[str]]:
    """把异常转换成对用户安全的中文提示与错误码。

    Args:
        exc: 生成流程抛出的异常。

    Returns:
        (用户可读信息, 错误码) 二元组。
    """
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
    if getattr(exc, "status_code", None):
        return str(message), str(code) if code else None
    return str(message), str(code) if code else None


def _serialize_result(result: Any) -> Any:
    """把生成结果转换成可 JSON 序列化且可落库的结构。"""
    if result is None:
        return None
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, (dict, list, str, int, float, bool)):
        return result
    return str(result)


generation_job_service = GenerationJobService()
