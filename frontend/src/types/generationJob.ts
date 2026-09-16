/**
 * AI 生成任务（generation_jobs）的前端契约。
 *
 * 生成任务在后端独立协程里执行，前端凭 job_id 轮询即可，
 * 因此切换页面、刷新浏览器都不会丢进度，也不会中断后台生成。
 */

/** 任务整体状态：queued/running 为活动态，其余为终态。 */
export type GenerationJobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

/** 单步骤状态。 */
export type GenerationJobStepStatus = "pending" | "running" | "done" | "error";

/**
 * 步骤所处阶段：
 * pending 未开始、waiting 已提交等待首个分片、thinking 模型思考中（推理模型）、
 * writing 正在产出正文、done 已完成。
 */
export type GenerationJobStepPhase = "pending" | "waiting" | "thinking" | "writing" | "done";

/** 任务中的单个步骤快照。 */
export interface GenerationJobStep {
  key: string;
  status: GenerationJobStepStatus;
  /** 当前阶段，用于区分"思考中"与"产出正文"两种真实进度。 */
  phase: GenerationJobStepPhase;
  /** 该步骤自身的完成度百分比（真实产出驱动）。 */
  percent: number;
  /** 已完成条目数（不含正在生成的那一条）。 */
  done_items: number;
  /** 流中已出现的条目数（含正在生成的那一条）。 */
  started_items: number;
  /** 目标条目数；为空表示后端没有可信总量。 */
  target_items: number | null;
  /** 当前正在生成的条目完成度百分比。 */
  inflight_percent: number;
  /** 已接收的正文（JSON）字数。 */
  chars: number;
  /** 已接收的推理内容字数：推理模型思考阶段的真实产出。 */
  thinking_chars: number;
  expected_total_chars?: number | null;
  expected_item_chars?: number | null;
  expected_thinking_chars?: number | null;
  /** 历史平均耗时（秒）：预计进度与预计剩余时间的推算依据。 */
  expected_duration_seconds?: number | null;
  /** 按历史平均耗时推算的步骤完成度（0-100）。 */
  estimated_percent?: number | null;
  /** 按历史平均耗时推算的该步骤剩余秒数。 */
  estimated_remaining_seconds?: number | null;
  elapsed_seconds: number | null;
  error?: string | null;
}

/** 生成任务快照。 */
export interface GenerationJobSnapshot {
  job_id: string;
  workflow: string;
  novel_id?: string | null;
  status: GenerationJobStatus;
  /** 整体完成度百分比（按步骤加权，来自真实产出）。 */
  percent: number;
  /** 后端没有可信总量时为 true，前端退化为不确定进度条。 */
  indeterminate: boolean;
  elapsed_seconds: number | null;
  /** 按真实速率推算的剩余秒数；样本不足时为空。 */
  eta_seconds: number | null;
  /** 按历史平均耗时推算的整体完成度；没有历史样本时为空。 */
  estimated_percent?: number | null;
  /** 按历史平均耗时推算的整体剩余秒数。 */
  estimated_remaining_seconds?: number | null;
  /** 预计总耗时（已用时 + 预计剩余）。 */
  estimated_total_seconds?: number | null;
  steps: GenerationJobStep[];
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  error_code?: string | null;
  acked?: boolean;
  /** 任务成功时携带的最终结果（候选数据）。 */
  result?: unknown;
}

/** 判断任务是否处于活动态（需要继续轮询）。 */
export function isActiveGenerationJob(job: GenerationJobSnapshot | null): boolean {
  return job?.status === "queued" || job?.status === "running";
}
