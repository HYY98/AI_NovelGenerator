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
  /** 增量新增：本次审校依据的硬规则清单与签名，提交时原样回传由服务端复核。 */
  hard_rules?: { card_id: string; name: string; version: number }[];
  hard_rule_signature?: string;
  blueprint_id?: string;
  blueprint_version?: number | null;
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
  /** 是否使用了强制定稿（存在阻断级问题时才会为 true）。 */
  forced?: boolean;
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

/** 正文证据：片段文本与在正文字中的字符范围。 */
export interface SettingEvidence {
  evidence_text: string;
  evidence_start: number | null;
  evidence_end: number | null;
  confidence?: number | null;
  chapter_id?: string | null;
}

export interface CardExtractItem extends SettingEvidence {
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
  /** 后端建议合并到的目标卡片业务 ID。 */
  target_card_id?: string | null;
  /** 产生该候选的生成记录，统一采纳接口按它去重。 */
  generation_id?: string | null;
  /** 卡片当前状态候选；受保护字段，默认不勾选。 */
  current_state?: string;
  is_hard_rule?: boolean;
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

// ---------------------------------------------------------------------------
// 卡片 AI 改写、字段差异与统一采纳（增量开发说明书 4.8 / 4.9）
// ---------------------------------------------------------------------------

/** 单个字段的 old/new 差异。受保护字段默认不允许 AI 改写。 */
export interface CardFieldDiff {
  field: string;
  old_value: string;
  new_value: string;
  /** 受保护字段（name / current_state / 规则卡 is_hard_rule）需要用户显式确认。 */
  protected?: boolean;
  note?: string;
}

/** AI 改写卡片字段返回的候选补丁，不写入正式卡片。 */
export interface CardRewriteResult {
  target_card_id: string;
  target_card_version?: number | null;
  changed_fields: CardFieldDiff[];
  preserved_fields: string[];
  notes?: string;
}

/** 统一采纳动作。 */
export type CardAcceptAction = "create" | "merge" | "rewrite" | "reject" | "skip";

/** 统一采纳接口的请求体。selected_fields 由前端按字段勾选结果组装。 */
export interface CardAcceptRequest {
  novel_id: string;
  generation_id: string;
  action: CardAcceptAction;
  target_card_id?: string | null;
  selected_fields?: Record<string, string>;
  expected_card_version?: number | null;
  /** current_state / is_hard_rule 等受保护字段必须显式确认后才允许写入。 */
  confirm_hard_rule?: boolean;
}

/** 统一采纳接口的响应。 */
export interface CardAcceptResult {
  card: Record<string, unknown> | null;
  card_id: string;
  card_version: number | null;
  action: CardAcceptAction;
  generation_status?: string | null;
  story_event_ids?: string[];
  message?: string;
}

/** 卡片状态变化候选，确认后优先写入 story_events。 */
export interface CardStateChangeCandidate extends SettingEvidence {
  change_id: string;
  card_id: string;
  card_name: string;
  from_state: string;
  to_state: string;
  reason: string;
}

// ---------------------------------------------------------------------------
// 章节设定分析（增量开发说明书 4.10）
// ---------------------------------------------------------------------------

/** 正文中新发现的卡片候选。 */
export interface NewCardCandidate extends SettingEvidence {
  type: string;
  name: string;
  aliases: string[];
  fields: Record<string, string>;
  current_state?: string;
  is_hard_rule?: boolean;
  suggested_action: "create" | "merge" | "skip";
  duplicate_of?: string | null;
  reason?: string;
}

/** 既有卡片的字段补充候选。 */
export interface ExistingCardUpdate extends SettingEvidence {
  card_id: string;
  card_name: string;
  fields: Record<string, string>;
  new_fields: string[];
  reason?: string;
}

/** 正文与已确认设定之间的冲突。 */
export interface SettingConflictItem extends SettingEvidence {
  severity: ReviewSeverity;
  card_id: string;
  card_name: string;
  message: string;
  suggestion: string;
}

/** 角色 / 关系 / 战力变化候选。 */
export interface EntityChangeCandidate extends SettingEvidence {
  change_id: string;
  entity_type: "character" | "faction" | "relation" | "power";
  entity_id: string;
  entity_name: string;
  before: string;
  after: string;
  reason: string;
}

/** 章节设定分析结果。结果只作为候选，不得直接修改正式实体。 */
export interface ChapterSettingAnalysisResult {
  generation_id?: string | null;
  chapter_id?: string | null;
  chapter_version?: number | null;
  new_cards: NewCardCandidate[];
  card_updates: ExistingCardUpdate[];
  conflicts: SettingConflictItem[];
  state_changes: CardStateChangeCandidate[];
  entity_changes: EntityChangeCandidate[];
  notes?: string;
  /** 正文版本已变化时后端标记为过期，前端禁止按旧证据写入。 */
  expired?: boolean;
}

/** 生成正文修改建议的响应（增量开发说明书 4.11）。 */
export interface TextRevisionGenerationResult {
  generation_id: string;
  chapter_version?: number | null;
  revisions: {
    revision_id: string;
    before_text: string;
    after_text: string;
    reason: string;
    revision_type: "supplement" | "correction" | "terminology";
    target_range?: { start: number; end: number };
    card_ids?: string[];
    warnings?: string[];
  }[];
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
