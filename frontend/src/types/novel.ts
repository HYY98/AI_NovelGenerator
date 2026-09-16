import type { NovelRewriteFieldKey } from "@/lib/novelFields";
import type { BlueprintStatus, GenerationMode } from "@/types/novelBlueprint";

export type { NovelRewriteFieldKey } from "@/lib/novelFields";
export type { BlueprintStatus, GenerationMode };

export interface NovelSummary {
  _id: string;
  title: string;
  subtitle?: string;
  genre: string;
  tags: string[];
  cover_image?: string;
  status: string;
  stats: {
    chapter_count: number;
    total_word_count: number;
  };
  created_at: string;
  updated_at: string;
}

export interface NovelDetail extends NovelSummary {
  introduction?: string;
  summary?: string;
  core_seed?: string;
  worldview?: string;
  writing_style?: string;
  narrative_pov?: string;
  era_background?: string;
  plot?: string;
  tone?: string;
  target_audience?: string;
  core_idea?: string;
  number_of_chapters?: number;
  words_per_chapter?: number;
  /** 增量新增：小说采用的生成模式，缺失时按 quick 处理。 */
  generation_mode?: GenerationMode;
  /** 增量新增：当前创作蓝图状态。 */
  blueprint_status?: BlueprintStatus;
  /** 增量新增：当前确认或草稿蓝图版本。 */
  blueprint_version?: number;
  /** 增量新增：战力体系业务 ID；战力体系嵌套在蓝图中时为空。 */
  power_system_id?: string | null;
  /** 增量新增：最近一次正文设定分析对应的章节版本。 */
  last_analyzed_chapter_version?: number | null;
}

export interface CreateNovelRequest {
  title: string;
  subtitle?: string;
  genre?: string;
  tags?: string[];
  introduction?: string;
  summary?: string;
  core_seed?: string;
  worldview?: string;
  writing_style?: string;
  narrative_pov?: string;
  era_background?: string;
  cover_image?: string;
  plot?: string;
  tone?: string;
  target_audience?: string;
  core_idea?: string;
  number_of_chapters?: number;
  words_per_chapter?: number;
}

export interface RewriteChatMessage {
  id: string;
  role: "user" | "assistant";
  target_field: NovelRewriteFieldKey;
  content: string;
  provider?: string;
  status?: "failed";
  error_message?: string;
  created_at: string;
}

export interface RewriteFieldRevision {
  id: string;
  value: string | string[];
  source: "initial" | "manual" | "ai";
  instruction?: string;
  created_at: string;
}

export interface WritingDraftRewriteState {
  messagesByField: Partial<Record<NovelRewriteFieldKey, RewriteChatMessage[]>>;
  revisionsByField: Partial<Record<NovelRewriteFieldKey, RewriteFieldRevision[]>>;
  activeRevisionIdByField: Partial<Record<NovelRewriteFieldKey, string>>;
}

export interface RewriteNovelFieldRequest {
  provider: string;
  use_stream?: boolean;
  target_field: NovelRewriteFieldKey;
  instruction: string;
  current_value: string | string[];
  context: Record<string, unknown>;
  chat_history: Pick<RewriteChatMessage, "role" | "content">[];
}

export interface RewriteNovelFieldResponse {
  target_field: NovelRewriteFieldKey;
  value: string | string[];
}

export type FactionRelationType =
  | "hostile"
  | "allied"
  | "cold_war"
  | "dependent"
  | "subordinate"
  | "trade_partner"
  | "secret_cooperation"
  | "historical_enemy";

export interface CoreFaction {
  _id?: string;
  novel_id?: string;
  faction_id?: string;
  is_deleted?: boolean;
  deleted_at?: string | null;
  name: string;
  alias?: string[];
  faction_type: string;
  level_type?: string;
  parent_faction_id?: string | null;
  positioning: string;
  public_stance: string;
  core_goal: string;
  hidden_goal?: string;
  resources_and_advantages: string[];
  organization_style: string;
  core_values: string[];
  conflict_with_mainline: string;
  is_public: boolean;
  influence_scope: string;
  active_status?: string;
  expandability: string;
  tags: string[];
  sort_order?: number;
  version: number;
}

export interface GeneratedCoreFaction {
  name: string;
  faction_type: string;
  positioning: string;
  public_stance: string;
  core_goal: string;
  hidden_goal?: string;
  resources_and_advantages: string[];
  organization_style: string;
  core_values: string[];
  conflict_with_mainline: string;
  is_public: boolean;
  influence_scope: string;
  expandability: string;
  tags: string[];
}

export interface FactionRelation {
  _id?: string;
  novel_id?: string;
  relation_id?: string;
  source_faction_id?: string;
  target_faction_id?: string;
  source_faction_name?: string;
  target_faction_name?: string;
  relation_type: FactionRelationType;
  current_state: string;
  core_conflict: string;
  hidden_tension?: string;
  possible_change: string;
  intensity: number;
  is_active: boolean;
  user_is_active?: boolean;
  version?: number;
  is_deleted?: boolean;
  deleted_at?: string | null;
  deletion_sources?: string[];
  disabled_by_faction_ids?: string[];
  created_at?: string;
  updated_at?: string;
}

/** 人工创建正式阵营关系时只提交稳定的 faction_id 端点。 */
export interface FactionRelationCreateRequestV1 {
  source_faction_id: string;
  target_faction_id: string;
  relation_type: FactionRelationType;
  current_state: string;
  core_conflict: string;
  hidden_tension: string;
  possible_change: string;
  intensity: number;
  is_active?: boolean;
}

/** 正式阵营关系允许更新的内容字段；端点不可通过更新接口改写。 */
export interface FactionRelationUpdateRequestV1 {
  expected_version: number;
  relation_type: FactionRelationType;
  current_state: string;
  core_conflict: string;
  hidden_tension: string;
  possible_change: string;
  intensity: number;
}

/** 正式阵营关系的用户启停请求。 */
export interface FactionRelationActiveUpdateRequestV1 {
  expected_version: number;
  is_active: boolean;
}

export interface GeneratedFactionRelation extends FactionRelation {
  source_faction_name: string;
  target_faction_name: string;
}

export interface GeneratedCoreFactionRelation {
  source_faction_name: string;
  target_faction_name: string;
  relation_type: FactionRelationType;
  current_state: string;
  core_conflict: string;
  hidden_tension?: string;
  possible_change: string;
  intensity: number;
  is_active: boolean;
}

export interface GeneratedCoreFactionsPayload {
  core_factions: GeneratedCoreFaction[];
  faction_relations: GeneratedCoreFactionRelation[];
}

export interface CoreFactionsPayload {
  core_factions: CoreFaction[];
  faction_relations: GeneratedFactionRelation[];
}

export interface CoreFactionsSavePayload {
  core_factions: GeneratedCoreFaction[];
  faction_relations: GeneratedCoreFactionRelation[];
}

export interface GenerateCoreFactionsRequest {
  novel_id: string;
  faction_count?: number;
  independent_faction_count?: number;
  user_guidance?: string;
  single_faction_integrity_score?: number | null;
  connect_to_existing?: boolean;
  existing_relation_targets?: Array<{
    faction_name: string;
    relation_score: number;
  }>;
  existing_relation_score?: number | null;
  temperature?: number | null;
  top_p?: number | null;
  max_tokens?: number | null;
  presence_penalty?: number | null;
  frequency_penalty?: number | null;
  system_prompt?: string | null;
  use_stream?: boolean;
}

export interface BulkCreateCoreFactionsResponse {
  factions: CoreFaction[];
  faction_relations: FactionRelation[];
}

/** 增量新增：AI 建书返回中的分阶段生成状态，用于前端展示蓝图/大纲/章节进度。 */
export interface AICreateGenerationStatus {
  mode?: GenerationMode;
  blueprint_id?: string | null;
  blueprint_version?: number | null;
  outline_status?: string | null;
  chapter_generation_status?: string | null;
}

export interface AICreateRequest {
  user_idea: string;
  number_of_chapters?: number;
  words_per_chapter?: number;
  cached_steps?: AICreateCachedSteps;
  /** 增量新增：quick 保留原四步建书行为，guided 复用已确认蓝图。 */
  generation_mode?: GenerationMode;
  /** 增量新增：guided 模式中蓝图所属小说的 ObjectId。 */
  novel_id?: string | null;
  /** 增量新增：guided 模式使用的已确认蓝图业务 ID。 */
  blueprint_id?: string | null;
  /** 增量新增：guided 模式使用的蓝图版本。 */
  blueprint_version?: number | null;
  // 可选生成参数
  temperature?: number | null;
  top_p?: number | null;
  max_tokens?: number | null;
  presence_penalty?: number | null;
  frequency_penalty?: number | null;
  system_prompt?: string | null;
  use_stream?: boolean;
}

export interface AICreateStepResult {
  step: string;
  data: Record<string, unknown>;
}

export interface AICreateResponse {
  expand_idea?: {
    plot: string;
  };
  extract_idea: {
    plot?: string;
    genre: string;
    tone: string;
    target_audience: string;
    core_idea: string;
  };
  core_seed: {
    core_seed: string;
  };
  novel_meta: {
    title: string;
    subtitle: string;
    introduction: string;
    summary: string;
    worldview: string;
    writing_style: string;
    narrative_pov: string;
    era_background: string;
    tags: string[];
  };
}

/** 增量新增：AI 建书 SSE 完成事件中的扩展字段。 */
export interface AICreateDoneExtra extends AICreateGenerationStatus {
  success?: boolean;
  failed_step?: AICreateStepKey;
  partial_result?: unknown;
  result?: AICreateResponse;
}

export type AICreateStepKey = "expand_idea" | "extract_idea" | "core_seed" | "novel_meta";

export type AICreateCachedSteps = Partial<{
  expand_idea: NonNullable<AICreateResponse["expand_idea"]>;
  extract_idea: AICreateResponse["extract_idea"];
  core_seed: AICreateResponse["core_seed"];
  novel_meta: AICreateResponse["novel_meta"];
}>;

/** AI 创建草稿，用于本地存储传递到 Writing 创建态 */
export interface WritingDraft extends CreateNovelRequest {
  _fromAI?: boolean;
  _rewriteState?: WritingDraftRewriteState;
}

/** Writing 侧栏导航项（增量新增创作设定中心、战力体系与正文同步中心入口） */
export type WritingSidebarItem =
  | "novel-info"
  | "chapter-editor"
  | "character-cards"
  | "location-cards"
  | "faction-cards"
  | "item-cards"
  | "rule-cards"
  | "relationship-map"
  | "creation-blueprint"
  | "power-system"
  | "setting-sync";
