/** 章节 / 设定卡 AI 接口封装。所有生成接口都只返回候选，不会覆盖正文。 */

import { apiGet, apiPost } from "@/lib/api";
import type {
  AIEnvelope,
  CardCompleteResult,
  CardConflictResult,
  CardExtractResult,
  CardGenerateResult,
  ChapterCompress,
  ChapterContinue,
  ChapterDraft,
  ChapterExpand,
  ChapterPlan,
  ChapterRewrite,
  ConsistencyReview,
  FinalizeCommitResult,
  FinalizeReviewResult,
  GenerationParams,
  StateChangeEvent,
} from "@/lib/aiTypes";

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
  params?: GenerationParams
): Promise<AIEnvelope<CardCompleteResult>> {
  return apiPost(`/api/llm/setting-cards/${cardId}/complete`, {
    ...base(novelId, params),
    card_id: cardId,
    fill_empty_only: true,
    locked_fields: lockedFields,
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
