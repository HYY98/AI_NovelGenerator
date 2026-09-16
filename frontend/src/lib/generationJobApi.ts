import { ApiRequestError, apiGet, apiPost } from "@/lib/api";
import type { GenerationJobSnapshot } from "@/types/generationJob";

/** 核心角色生成工作流名（与后端保持一致）。 */
export const CORE_CHARACTERS_WORKFLOW = "create_characters_by_ai";
/** 人物关系生成工作流名（与后端保持一致）。 */
export const CHARACTER_RELATIONS_WORKFLOW = "create_character_relations_by_ai";

/** 后端统一返回 {"job": ...} 包装。 */
interface GenerationJobResponse {
  job: GenerationJobSnapshot | null;
}

/**
 * 功能：启动核心角色生成任务（后台执行，与页面生命周期解耦）。
 * Args:
 *   payload: 核心角色生成请求体（小说、数量、指导与生成参数）。
 * Returns: 新任务快照，含 job_id 与初始进度。
 */
export async function startCoreCharactersJob(
  payload: Record<string, unknown>
): Promise<GenerationJobSnapshot> {
  const response = await apiPost<GenerationJobResponse>(
    "/api/llm/generate-core-characters/jobs",
    payload
  );
  if (!response.job) {
    throw new Error("后端未返回生成任务");
  }
  return response.job;
}

/**
 * 功能：启动人物关系生成任务（后台执行，与页面生命周期解耦）。
 * Args:
 *   payload: 关系生成请求体（小说、角色白名单、约束与生成参数）。
 * Returns: 新任务快照，含 job_id 与初始进度。
 */
export async function startCharacterRelationsJob(
  payload: Record<string, unknown>
): Promise<GenerationJobSnapshot> {
  const response = await apiPost<GenerationJobResponse>(
    "/api/llm/generate-character-relations/jobs",
    payload
  );
  if (!response.job) {
    throw new Error("后端未返回生成任务");
  }
  return response.job;
}

/**
 * 功能：读取生成任务的最新快照。
 * Args:
 *   jobId: 任务业务 ID。
 * Returns: 最新任务快照。
 */
export async function getGenerationJob(jobId: string): Promise<GenerationJobSnapshot> {
  const response = await apiGet<GenerationJobResponse>(
    `/api/llm/generation-jobs/${encodeURIComponent(jobId)}`
  );
  if (!response.job) {
    throw new Error("生成任务不存在");
  }
  return response.job;
}

/**
 * 功能：取消仍在运行的生成任务并消费其结果，避免下次进入重复弹出。
 * Args:
 *   jobId: 任务业务 ID。
 *   novelId: 小说 ObjectId；传入后由后端校验归属，跨小说任务不可取消。
 * Returns: 无；任务已结束时静默忽略。
 */
export async function cancelGenerationJob(jobId: string, novelId?: string): Promise<void> {
  await apiPost(`/api/llm/generation-jobs/${encodeURIComponent(jobId)}/cancel`, {
    novel_id: novelId ?? null,
  });
  await ackGenerationJob(jobId);
}

/**
 * 功能：标记任务结果已被消费。
 * Args:
 *   jobId: 任务业务 ID。
 * Returns: 无。
 */
export async function ackGenerationJob(jobId: string): Promise<void> {
  try {
    await apiPost(`/api/llm/generation-jobs/${encodeURIComponent(jobId)}/ack`, {});
  } catch {
    // 消费标记失败不影响主流程，最多下次进入时多弹一次确认。
  }
}

/**
 * 功能：查询某小说下最近一个可恢复的生成任务。
 * Args:
 *   novelId: 小说 ObjectId。
 *   workflows: 需要匹配的工作流名称列表。
 * Returns: 可恢复的任务快照；没有时返回 null。
 */
export async function findActiveGenerationJob(
  novelId: string,
  workflows: string[]
): Promise<GenerationJobSnapshot | null> {
  const query = new URLSearchParams({
    novel_id: novelId,
    workflow: workflows.join(","),
  });
  try {
    const response = await apiGet<GenerationJobResponse>(
      `/api/llm/generation-jobs/active?${query.toString()}`
    );
    return response.job ?? null;
  } catch (error) {
    // 老版本后端没有该接口时按"无进行中任务"处理，不影响正常生成。
    if (error instanceof ApiRequestError && (error.status === 404 || error.status === 501)) {
      return null;
    }
    throw error;
  }
}

/**
 * 功能：把任务快照换算成进度条文案所需的数字。
 * Args:
 *   job: 任务快照。
 * Returns: 整体百分比（0-100，取整）与当前步骤。
 */
export function describeGenerationJob(job: GenerationJobSnapshot): {
  percent: number;
  step: GenerationJobSnapshot["steps"][number] | null;
} {
  const step = job.steps.find((item) => item.status === "running") ?? job.steps[0] ?? null;
  return { percent: Math.max(0, Math.min(100, Math.round(job.percent))), step };
}
