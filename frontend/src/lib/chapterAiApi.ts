/** 章节 / 设定卡 AI 接口封装。所有生成接口都只返回候选，不会覆盖正文。 */

import { apiGet, apiPost, ApiRequestError } from "@/lib/api";
import type {
  AIEnvelope,
  CardAcceptAction,
  CardAcceptRequest,
  CardAcceptResult,
  CardCompleteResult,
  CardConflictResult,
  CardExtractResult,
  CardGenerateResult,
  CardRewriteResult,
  ChapterCompress,
  ChapterContinue,
  ChapterDraft,
  ChapterExpand,
  ChapterPlan,
  ChapterRewrite,
  ChapterSettingAnalysisResult,
  ConsistencyReview,
  FinalizeCommitResult,
  FinalizeReviewResult,
  GenerationParams,
  StateChangeEvent,
  TextRevisionGenerationResult,
} from "@/lib/aiTypes";
import type {
  BlueprintStatus,
  GenerationMode,
  NovelBlueprint,
  NovelOutline,
} from "@/types/novelBlueprint";
import type { PowerSystem } from "@/types/powerSystem";
import type {
  TextRevision,
  TextRevisionImpactResult,
  TextRevisionType,
} from "@/types/textRevision";

interface BaseBody extends GenerationParams {
  novel_id: string;
  chapter_id?: string;
  use_stream: boolean;
}

function base(novelId: string, params: GenerationParams = {}): BaseBody {
  return {
    novel_id: novelId,
    request_id: params.request_id ?? "",
    user_prompt: params.user_prompt ?? "",
    provider: params.provider,
    temperature: params.temperature,
    max_tokens: params.max_tokens,
    use_stream: false,
  };
}

export function generateChapterPlan(
  chapterId: string,
  novelId: string,
  params?: GenerationParams
): Promise<AIEnvelope<ChapterPlan>> {
  return apiPost(`/api/llm/chapters/${chapterId}/plan`, base(novelId, params));
}

export function generateChapterDraft(
  chapterId: string,
  novelId: string,
  plan: ChapterPlan | null,
  params?: GenerationParams & { target_words?: number }
): Promise<AIEnvelope<ChapterDraft>> {
  return apiPost(`/api/llm/chapters/${chapterId}/draft`, {
    ...base(novelId, params),
    plan,
    target_words: params?.target_words,
  });
}

export function generateChapterContinue(
  chapterId: string,
  novelId: string,
  plan: ChapterPlan | null,
  params?: GenerationParams & { target_words?: number }
): Promise<AIEnvelope<ChapterContinue>> {
  return apiPost(`/api/llm/chapters/${chapterId}/continue`, {
    ...base(novelId, params),
    plan,
    target_words: params?.target_words,
  });
}

interface SelectionBody {
  selection: { start: number | null; end: number | null; text: string };
  instruction: string;
  locked_facts: string[];
}

function selectionBody(
  novelId: string,
  selection: SelectionBody["selection"],
  instruction: string,
  params?: GenerationParams
): BaseBody & SelectionBody {
  return {
    ...base(novelId, params),
    selection,
    instruction,
    locked_facts: [],
  };
}

export function rewriteChapterSelection(
  chapterId: string,
  novelId: string,
  selection: SelectionBody["selection"],
  instruction: string,
  params?: GenerationParams
): Promise<AIEnvelope<ChapterRewrite>> {
  return apiPost(
    `/api/llm/chapters/${chapterId}/rewrite`,
    selectionBody(novelId, selection, instruction, params)
  );
}

export function expandChapterSelection(
  chapterId: string,
  novelId: string,
  selection: SelectionBody["selection"],
  instruction: string,
  params?: GenerationParams
): Promise<AIEnvelope<ChapterExpand>> {
  return apiPost(
    `/api/llm/chapters/${chapterId}/expand`,
    selectionBody(novelId, selection, instruction, params)
  );
}

export function compressChapterSelection(
  chapterId: string,
  novelId: string,
  selection: SelectionBody["selection"],
  instruction: string,
  params?: GenerationParams
): Promise<AIEnvelope<ChapterCompress>> {
  return apiPost(
    `/api/llm/chapters/${chapterId}/compress`,
    selectionBody(novelId, selection, instruction, params)
  );
}

export function reviewChapter(
  chapterId: string,
  novelId: string,
  content?: string,
  params?: GenerationParams
): Promise<AIEnvelope<ConsistencyReview>> {
  return apiPost(`/api/llm/chapters/${chapterId}/review`, {
    ...base(novelId, params),
    content,
  });
}

export function proposeStateChanges(
  chapterId: string,
  novelId: string,
  content?: string,
  params?: GenerationParams
): Promise<AIEnvelope<{ proposals: unknown[]; summary: string }>> {
  return apiPost(`/api/llm/chapters/${chapterId}/propose-state-changes`, {
    ...base(novelId, params),
    content,
  });
}

export function finalizeReview(
  chapterId: string,
  novelId: string,
  params?: GenerationParams
): Promise<FinalizeReviewResult> {
  return apiPost(`/api/llm/chapters/${chapterId}/finalize`, {
    ...base(novelId, params),
    stage: "review",
  });
}

export function finalizeCommit(
  chapterId: string,
  novelId: string,
  payload: {
    expected_version: number;
    accepted_event_ids: string[];
    rejected_event_ids: string[];
    ignored_issues: string[];
    blocking_issues: { id?: string; severity?: string }[];
    force?: boolean;
    /** 审校依据：服务端据此重新校验，避免客户端伪造已处理。 */
    review_generation_id?: string;
    hard_rule_signature?: string;
    blueprint_version?: number;
  }
): Promise<FinalizeCommitResult> {
  return apiPost(`/api/llm/chapters/${chapterId}/finalize`, {
    ...base(novelId),
    stage: "commit",
    ...payload,
  });
}

export function listPendingStateChanges(
  chapterId: string,
  novelId: string
): Promise<{ data: StateChangeEvent[]; total: number; chapter_version: number }> {
  return apiGet(
    `/api/llm/chapters/${chapterId}/pending-state-changes?novel_id=${encodeURIComponent(novelId)}`
  );
}

// ---------------------------------------------------------------------------
// 设定卡 AI
// ---------------------------------------------------------------------------

export function generateSettingCard(
  novelId: string,
  cardType: "location" | "item" | "rule",
  chapterId: string,
  params?: GenerationParams
): Promise<AIEnvelope<CardGenerateResult>> {
  return apiPost("/api/llm/setting-cards/generate", {
    ...base(novelId, params),
    chapter_id: chapterId,
    type: cardType,
  });
}

export function completeSettingCard(
  novelId: string,
  cardId: string,
  lockedFields: string[],
  params?: GenerationParams & { expected_version?: number }
): Promise<AIEnvelope<CardCompleteResult>> {
  return apiPost(`/api/llm/setting-cards/${cardId}/complete`, {
    ...base(novelId, params),
    card_id: cardId,
    fill_empty_only: true,
    locked_fields: lockedFields,
    expected_version: params?.expected_version ?? null,
  });
}

export function extractSettingCards(
  novelId: string,
  sourceDocument: "worldview" | "novel_info" | "chapter" | "card",
  sourceText: string,
  chapterId: string,
  params?: GenerationParams
): Promise<AIEnvelope<CardExtractResult>> {
  return apiPost("/api/llm/setting-cards/extract", {
    ...base(novelId, params),
    chapter_id: chapterId,
    source_document: sourceDocument,
    source_text: sourceText,
  });
}

export function checkSettingCardConflicts(
  novelId: string,
  cardId: string,
  params?: GenerationParams
): Promise<AIEnvelope<CardConflictResult>> {
  return apiPost("/api/llm/setting-cards/check-conflicts", {
    ...base(novelId, params),
    card_id: cardId,
  });
}

// ---------------------------------------------------------------------------
// 创作蓝图（增量开发说明书 4.2 / 4.3）
// ---------------------------------------------------------------------------

/** 准备创作蓝图的请求体。 */
export interface PrepareBlueprintRequest {
  novel_id: string | null;
  generation_mode: GenerationMode;
  plot_summary: string;
  worldview: string;
  power_system: PowerSystem | null;
  character_ids: string[];
  faction_ids: string[];
  setting_card_ids: string[];
  relation_ids: string[];
  request_id?: string;
  provider?: string;
  temperature?: number;
  max_tokens?: number;
}

/** 准备创作蓝图的响应。AI 结果只作为候选，不写入正式卡片。 */
export interface PrepareBlueprintResponse {
  generation_id: string;
  blueprint_id: string;
  version: number;
  status: BlueprintStatus;
  data: {
    plot_summary: string;
    worldview: string;
    power_system: PowerSystem | null;
    suggestions: unknown[];
    conflicts: unknown[];
    questions: string[];
  };
  warnings: string[];
  context_snapshot?: Record<string, unknown>;
  reused?: boolean;
}

/** 保存或确认创作蓝图的请求体。confirm=false 时只保存草稿。 */
export interface ConfirmBlueprintRequest {
  expected_version: number;
  plot_summary: string;
  worldview: string;
  power_system: PowerSystem | null;
  selected_character_ids: string[];
  selected_faction_ids: string[];
  selected_setting_card_ids: string[];
  selected_relation_ids: string[];
  confirm: boolean;
  generation_mode?: GenerationMode;
  request_id?: string;
}

/** 蓝图确认响应；后端返回完整蓝图摘要。 */
export interface ConfirmBlueprintResponse extends Partial<NovelBlueprint> {
  blueprint_id: string;
  version: number;
  status: BlueprintStatus;
}

/**
 * 功能：调用 AI 整理并补全创作蓝图，只返回候选。
 * Args: payload: 蓝图输入与生成参数。
 * Returns: 蓝图候选、建议与冲突。
 */
export function prepareBlueprint(
  payload: PrepareBlueprintRequest
): Promise<PrepareBlueprintResponse> {
  return apiPost("/api/llm/novels/prepare-blueprint", payload);
}

/**
 * 功能：保存草稿或确认创作蓝图。
 * Args:
 *   novelId: 小说 ObjectId。
 *   blueprintId: 蓝图业务 ID；为空时由后端新建草稿。
 *   payload: 蓝图内容与确认标记。
 * Returns: 确认后的蓝图摘要与版本。
 */
export async function confirmBlueprint(
  novelId: string,
  blueprintId: string,
  payload: ConfirmBlueprintRequest
): Promise<ConfirmBlueprintResponse> {
  if (!blueprintId) {
    const created = await apiPost<ConfirmBlueprintResponse>(
      `/api/novels/${novelId}/blueprint`,
      {
        generation_mode: payload.generation_mode,
        plot_summary: payload.plot_summary,
        worldview: payload.worldview,
        power_system: payload.power_system,
        selected_character_ids: payload.selected_character_ids,
        selected_faction_ids: payload.selected_faction_ids,
        selected_setting_card_ids: payload.selected_setting_card_ids,
        selected_relation_ids: payload.selected_relation_ids,
        source: "user",
      },
    );
    if (!payload.confirm) return created;
    return apiPost(`/api/novels/${novelId}/blueprint/${created.blueprint_id}/confirm`, {
      ...payload,
      expected_version: created.version,
    });
  }
  return apiPost(`/api/novels/${novelId}/blueprint/${blueprintId}/confirm`, payload);
}

/**
 * 功能：读取小说当前蓝图，供刷新后继续编辑。
 * Args: novelId: 小说 ObjectId。
 * Returns: 蓝图摘要；后端尚未提供该接口或不存在时返回 null，不抛错。
 */
export async function getLatestBlueprint(
  novelId: string
): Promise<ConfirmBlueprintResponse | null> {
  try {
    return await apiGet<ConfirmBlueprintResponse>(
      `/api/novels/${novelId}/blueprint/current`
    );
  } catch (error) {
    if (error instanceof ApiRequestError && (error.status === 404 || error.status === 501)) {
      return null;
    }
    throw error;
  }
}

// ---------------------------------------------------------------------------
// 大纲与分章生成（增量开发说明书 4.4 / 4.5 / 4.6）
// ---------------------------------------------------------------------------

export interface GenerateOutlineRequest {
  blueprint_id: string;
  blueprint_version: number;
  target_chapter_count: number;
  target_word_count: number;
  request_id?: string;
  provider?: string;
  model?: string;
}

export interface GenerateOutlineResponse {
  generation_id: string;
  blueprint_version: number;
  volumes: NovelOutline["volumes"];
  warnings: string[];
  conflicts: unknown[];
  reused?: boolean;
}

export interface ConfirmOutlineRequest {
  outline_version: number;
  outline: { volumes: NovelOutline["volumes"] };
  confirm: boolean;
  blueprint_id?: string;
  blueprint_version?: number;
  request_id?: string;
}

export interface ConfirmOutlineResponse {
  generation_id: string;
  outline_version: number;
  status: string;
  volumes: NovelOutline["volumes"];
  message?: string;
}

export interface GenerateChaptersRequest {
  outline_generation_id: string;
  outline_version: number;
  chapter_ids: string[];
  start_index: number;
  count: number;
  blueprint_version: number;
  request_id?: string;
  provider?: string;
  model?: string;
  /** 已有用户正文的章节必须显式确认覆盖，默认禁止。 */
  allow_overwrite?: boolean;
}

export interface GeneratedChapterCandidate {
  chapter_id: string | null;
  title: string;
  content: string;
  summary: string;
  index: number;
  generation_id?: string | null;
  status: string;
  warnings: string[];
}

export interface GenerateChaptersResponse {
  generation_id: string;
  blueprint_version: number;
  outline_version: number;
  chapters: GeneratedChapterCandidate[];
  warnings: string[];
  failed?: { index: number; title: string; message: string }[];
}

/**
 * 功能：按已确认蓝图生成卷章大纲候选。
 * Args: novelId: 小说 ObjectId；payload: 蓝图版本与目标规模。
 * Returns: 大纲候选，未确认前不会创建正式卷和章节。
 */
export function generateNovelOutline(
  novelId: string,
  payload: GenerateOutlineRequest
): Promise<GenerateOutlineResponse> {
  return apiPost(`/api/llm/novels/${novelId}/generate-outline`, payload);
}

/**
 * 功能：确认大纲；用户修改后的大纲作为新版本保存。
 * Args:
 *   novelId: 小说 ObjectId。
 *   generationId: 大纲生成记录 ID。
 *   payload: 大纲版本与内容。
 * Returns: 确认结果与正式卷章结构。
 */
export function confirmNovelOutline(
  novelId: string,
  generationId: string,
  payload: ConfirmOutlineRequest
): Promise<ConfirmOutlineResponse> {
  return apiPost(`/api/llm/novels/${novelId}/outlines/${generationId}/confirm`, payload);
}

/**
 * 功能：按已确认大纲分章生成正文候选。
 * Args: novelId: 小说 ObjectId；payload: 章节范围与版本信息。
 * Returns: 每章候选正文与生成记录；不直接写入正式章节。
 */
export function generateNovelChapters(
  novelId: string,
  payload: GenerateChaptersRequest
): Promise<GenerateChaptersResponse> {
  return apiPost(`/api/llm/novels/${novelId}/generate-chapters`, payload);
}

// ---------------------------------------------------------------------------
// 卡片 AI 改写与统一采纳（增量开发说明书 4.8 / 4.9）
// ---------------------------------------------------------------------------

export interface RewriteCardRequest {
  novel_id: string;
  fields: string[];
  instruction: string;
  locked_fields: string[];
  expected_version?: number | null;
  request_id?: string;
  provider?: string;
  model?: string;
}

/**
 * 功能：请求 AI 改写卡片指定字段，只返回字段级候选补丁。
 * Args: cardBusinessId: 卡片业务 ID；payload: 目标字段与锁定字段。
 * Returns: 包含 changed_fields / preserved_fields 的候选。
 */
export function rewriteSettingCard(
  cardBusinessId: string,
  payload: RewriteCardRequest
): Promise<AIEnvelope<CardRewriteResult>> {
  return apiPost(`/api/llm/setting-cards/${cardBusinessId}/rewrite`, payload);
}

/**
 * 功能：统一采纳卡片候选（create / merge / rewrite / reject / skip）。
 * Args: payload: 生成记录、目标卡片、选中字段与版本。
 * Returns: 正式卡片摘要、采纳动作与生成记录状态。
 */
export function acceptSettingCardCandidate(
  payload: CardAcceptRequest
): Promise<CardAcceptResult> {
  return apiPost("/api/llm/setting-cards/accept", payload);
}

/** 功能：拒绝或忽略一条候选，后端记录采纳动作为 reject。 */
export function rejectSettingCardCandidate(
  novelId: string,
  generationId: string,
  action: Extract<CardAcceptAction, "reject"> = "reject"
): Promise<CardAcceptResult> {
  return acceptSettingCardCandidate({
    novel_id: novelId,
    generation_id: generationId,
    action,
  });
}

// ---------------------------------------------------------------------------
// 章节设定分析与正文修改建议（增量开发说明书 4.10 / 4.11 / 4.12）
// ---------------------------------------------------------------------------

export interface AnalyzeChapterSettingRequest {
  novel_id: string;
  chapter_version: number;
  request_id?: string;
  provider?: string;
  model?: string;
}

/**
 * 功能：分析本章正文，提取新卡片、补充、冲突与变化候选。
 * Args: chapterId: 章节 ObjectId；payload: 小说与章节版本。
 * Returns: 只含候选的分析结果，不修改正式实体。
 */
export function analyzeChapterSettingCards(
  chapterId: string,
  payload: AnalyzeChapterSettingRequest
): Promise<AIEnvelope<ChapterSettingAnalysisResult>> {
  return apiPost(`/api/llm/chapters/${chapterId}/analyze-setting-cards`, payload);
}

export interface GenerateTextRevisionRequest {
  novel_id: string;
  card_ids: string[];
  revision_type: TextRevisionType;
  instruction: string;
  target_range?: { start: number; end: number } | null;
  chapter_version: number;
  request_id?: string;
  provider?: string;
  model?: string;
}

/**
 * 功能：根据卡片变更生成正文补充、修正或术语统一建议。
 * Args: chapterId: 章节 ObjectId；payload: 目标卡片、类型与范围。
 * Returns: 候选建议列表；不直接写回章节。
 */
export function generateTextRevisions(
  chapterId: string,
  payload: GenerateTextRevisionRequest
): Promise<AIEnvelope<TextRevisionGenerationResult>> {
  return apiPost(`/api/llm/chapters/${chapterId}/setting-revision`, payload);
}

export interface AcceptTextRevisionRequest {
  novel_id: string;
  expected_chapter_version: number;
  after_text: string;
}

export interface AcceptTextRevisionResponse {
  revision_id: string;
  status: string;
  chapter_version: number;
  message?: string;
}

/**
 * 功能：确认一条正文修改建议并写回章节正文。
 * Args:
 *   chapterId: 章节 ObjectId。
 *   revisionId: 修改建议业务 ID。
 *   payload: 小说、期望章节版本与最终文本。
 * Returns: 写入结果与新章节版本。
 */
export function acceptTextRevision(
  chapterId: string,
  revisionId: string,
  payload: AcceptTextRevisionRequest
): Promise<AcceptTextRevisionResponse> {
  return apiPost(`/api/chapters/${chapterId}/text-revisions/${revisionId}/accept`, payload);
}

/**
 * 功能：读取某章节的正文修改建议历史。
 * Args:
 *   chapterId: 章节 ObjectId。
 *   novelId: 小说 ObjectId，用于归属校验。
 * Returns: 建议列表；后端尚未提供该接口时返回空数组，不抛错。
 */
export async function listTextRevisions(
  chapterId: string,
  novelId: string
): Promise<TextRevision[]> {
  try {
    const res = await apiGet<{ data: TextRevision[] } | TextRevision[]>(
      `/api/chapters/${chapterId}/text-revisions?novel_id=${encodeURIComponent(novelId)}`
    );
    return Array.isArray(res) ? res : res.data ?? [];
  } catch (error) {
    if (error instanceof ApiRequestError && (error.status === 404 || error.status === 501)) {
      return [];
    }
    throw error;
  }
}

/**
 * 功能：卡片修改后分析正文中受影响的章节与位置（正文影响分析）。
 * Args:
 *   novelId: 小说 ObjectId。
 *   cardIds: 卡片业务 ID 列表。
 * Returns: 命中章节与字符范围列表；只做定位，不生成也不写入建议。
 */
export function analyzeTextRevisionImpact(
  novelId: string,
  cardIds: string[]
): Promise<TextRevisionImpactResult> {
  return apiPost(`/api/llm/novels/${novelId}/text-revision/impact`, {
    card_ids: cardIds,
  });
}
