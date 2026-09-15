/** 章节 / 设定卡 AI 接口的数据契约（与后端 schemas 保持一致）。 */

export interface AISourceRef {
  type: string;
  chapter_id?: string;
  card_id?: string;
  evidence?: string;
}

/** 所有 AI 生成接口的统一响应外壳（技术指引 19.2）。 */
export interface AIEnvelope<T> {
  kind: string;
  candidate_id: string;
  data: T;
  source: AISourceRef;
  warnings: string[];
  conflicts: string[];
  provider?: string;
  model?: string;
  context_snapshot?: Record<string, unknown>;
  reused?: boolean;
}

export interface ChapterPlan {
  title: string;
  goal: string;
  conflict: string;
  beats: string[];
  character_changes: string[];
  location_usage: string[];
  item_usage: string[];
  rule_constraints: string[];
  foreshadowing: string[];
  ending_hook: string;
}

export interface UsedEntities {
  characters: string[];
  locations: string[];
  items: string[];
  rules: string[];
}

export interface StateChangeProposal {
  entity_type: string;
  entity_id: string;
  change_type: string;
  before: Record<string, string>;
  after: Record<string, string>;
  evidence: string;
  confidence: number;
  note: string;
}

export interface ChapterDraft {
  title: string;
  content: string;
  summary: string;
  unresolved_threads: string[];
  used_entities: UsedEntities;
  state_change_proposals: StateChangeProposal[];
}

export interface ChapterContinue {
  content: string;
  summary: string;
  unresolved_threads: string[];
}

export interface ChapterRewrite {
  rewritten_text: string;
  change_notes: string[];
  preserved_facts: string[];
}

export interface ChapterExpand {
  expanded_text: string;
  added_details: string[];
}

export interface ChapterCompress {
  compressed_text: string;
  removed_notes: string[];
}

export type ReviewSeverity = "blocking" | "warning" | "notice";

export interface ReviewIssue {
  severity: ReviewSeverity;
  category: string;
  entity_type: string;
  entity_id: string;
  message: string;
  evidence: string;
  suggestion: string;
  can_ignore: boolean;
}

export interface ConsistencyReview {
  summary: string;
  issues: ReviewIssue[];
  blocking_count: number;
}

export interface StateChangeEvent {
  event_id: string;
  chapter_id: string;
  entity_type: string;
  entity_id: string;
  event_type: string;
  before: Record<string, string>;
  after: Record<string, string>;
  evidence: { text?: string };
  confidence: number;
  status: string;
}

export interface FinalizeReviewResult {
  chapter_id: string;
  chapter_version: number;
  chapter_status: string;
  review: ConsistencyReview;
  review_generation_id: string;
  state_change_events: StateChangeEvent[];
  blocking_count: number;
  can_finalize: boolean;
  warnings: string[];
}

export interface FinalizeCommitResult {
  chapter: Record<string, unknown>;
  accepted_count: number;
  rejected_count: number;
  applied_card_changes: {
    card_id: string;
    card_type: string;
    current_state: string;
    fields: Record<string, string>;
    warnings: string[];
  }[];
  pending_character_updates: {
    event_id: string;
    entity_id: string;
    before: Record<string, string>;
    after: Record<string, string>;
    evidence: string;
  }[];
  next_chapter: Record<string, unknown> | null;
  message: string;
}

export interface CardCandidate {
  type: string;
  name: string;
  aliases: string[];
  fields: Record<string, string>;
  current_state: string;
  importance: number;
  reason: string;
  conflicts: string[];
  source: string;
}

export interface CardGenerateResult {
  candidates: CardCandidate[];
  notes: string;
}

export interface CardCompleteResult {
  filled_fields: Record<string, string>;
  notes: string;
  conflicts: string[];
}

export interface CardExtractItem {
  type: string;
  name: string;
  aliases: string[];
  fields: Record<string, string>;
  evidence: string;
  duplicate_of: string;
  new_fields: string[];
  state_change: string;
  confidence: number;
  suggested_action: "create" | "merge" | "skip";
}

export interface CardExtractResult {
  items: CardExtractItem[];
  notes: string;
}

export interface CardConflictItem {
  severity: ReviewSeverity;
  message: string;
  related_card_ids: string[];
  suggestion: string;
}

export interface CardConflictResult {
  conflicts: CardConflictItem[];
  notes: string;
}

/** 生成请求的公共参数。 */
export interface GenerationParams {
  user_prompt?: string;
  request_id?: string;
  provider?: string;
  temperature?: number;
  max_tokens?: number;
}

/** 产生一个客户端幂等 ID。 */
export function newRequestId(prefix: string): string {
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`.slice(0, 80);
}
