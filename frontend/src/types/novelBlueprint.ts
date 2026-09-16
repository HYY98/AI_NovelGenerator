/** 创作蓝图、大纲与战力体系的前端数据契约。 */

import {
  createEmptyPowerSystem,
  splitLines,
  type PowerSystem,
} from "@/types/powerSystem";

/** 小说生成模式：快速生成 / 设定辅助生成。 */
export type GenerationMode = "quick" | "guided";

/** 蓝图生命周期。已确认版本不可原地修改，修改会生成新版本并把旧版本置为 superseded。 */
export type BlueprintStatus = "draft" | "pending_confirmation" | "confirmed" | "superseded";

/** 蓝图内容来源。AI 补全的内容在未确认前只作为候选。 */
export type BlueprintSource = "user" | "ai" | "mixed";

/** 蓝图向导的七个步骤。 */
export type BlueprintStepKey =
  | "basic"
  | "plot"
  | "entities"
  | "cards"
  | "power"
  | "relations"
  | "review";

export const BLUEPRINT_STEPS: BlueprintStepKey[] = [
  "basic",
  "plot",
  "entities",
  "cards",
  "power",
  "relations",
  "review",
];

export const BLUEPRINT_STEP_LABEL: Record<BlueprintStepKey, string> = {
  basic: "基本信息",
  plot: "剧情与世界观",
  entities: "角色与势力",
  cards: "地点/物品/规则",
  power: "战力体系",
  relations: "关系图",
  review: "预览并生成",
};

/** AI 对蓝图的补全候选。未接受前不视为已确认事实。 */
export interface BlueprintSuggestion {
  suggestion_id: string;
  /** 候选针对的蓝图字段，例如 `worldview`、`power_system.levels`。 */
  target: string;
  title: string;
  content: string;
  /** 后端给出的采纳动作建议。 */
  suggested_action: "create" | "merge" | "skip";
  reason: string;
  confidence?: number;
  accepted?: boolean;
  rejected?: boolean;
}

/** 蓝图整理阶段发现的冲突。blocking 未处理时不允许确认蓝图。 */
export interface BlueprintConflict {
  conflict_id: string;
  severity: "blocking" | "warning" | "notice";
  message: string;
  related_ids: string[];
  suggestion: string;
  /** 用户显式忽略后记录原因，允许继续确认。 */
  ignored?: boolean;
}

/** 创作蓝图。 */
export interface NovelBlueprint {
  blueprint_id: string;
  novel_id?: string | null;
  version: number;
  generation_mode: GenerationMode;
  title?: string;
  genre?: string;
  plot_summary: string;
  worldview: string;
  power_system: PowerSystem | null;
  selected_character_ids: string[];
  selected_faction_ids: string[];
  selected_setting_card_ids: string[];
  selected_relation_ids: string[];
  ai_suggestions: BlueprintSuggestion[];
  conflicts: BlueprintConflict[];
  questions: string[];
  status: BlueprintStatus;
  source: BlueprintSource;
  confirmed_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** 功能：构造一份空白蓝图草稿。
 * Args: mode: 生成模式，默认 guided。
 * Returns: 字段齐全且为空的 NovelBlueprint。
 */
export function createEmptyBlueprint(mode: GenerationMode = "guided"): NovelBlueprint {
  return {
    blueprint_id: "",
    novel_id: null,
    version: 0,
    generation_mode: mode,
    title: "",
    genre: "",
    plot_summary: "",
    worldview: "",
    power_system: null,
    selected_character_ids: [],
    selected_faction_ids: [],
    selected_setting_card_ids: [],
    selected_relation_ids: [],
    ai_suggestions: [],
    conflicts: [],
    questions: [],
    status: "draft",
    source: "user",
    confirmed_at: null,
  };
}

/** 功能：把后端返回的蓝图摘要安全合并为前端蓝图，缺失字段使用默认值。
 * Args: raw: 后端返回的原始对象；fallback: 合并基线。
 * Returns: 字段完整的 NovelBlueprint。
 */
export function normalizeBlueprint(
  raw: unknown,
  fallback: NovelBlueprint = createEmptyBlueprint()
): NovelBlueprint {
  const toStringArray = (value: unknown): string[] =>
    Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  const source = (
    raw && typeof raw === "object" ? raw : {}
  ) as Partial<NovelBlueprint> & Record<string, unknown>;

  return {
    ...fallback,
    ...source,
    generation_mode: source.generation_mode === "quick" ? "quick" : "guided",
    status: source.status ?? fallback.status,
    source: source.source ?? fallback.source,
    plot_summary: source.plot_summary ?? "",
    worldview: source.worldview ?? "",
    power_system: source.power_system ?? null,
    selected_character_ids: toStringArray(source.selected_character_ids),
    selected_faction_ids: toStringArray(source.selected_faction_ids),
    selected_setting_card_ids: toStringArray(source.selected_setting_card_ids),
    selected_relation_ids: toStringArray(source.selected_relation_ids),
    ai_suggestions: Array.isArray(source.ai_suggestions)
      ? (source.ai_suggestions as BlueprintSuggestion[])
      : [],
    conflicts: Array.isArray(source.conflicts) ? (source.conflicts as BlueprintConflict[]) : [],
    questions: toStringArray(source.questions),
  };
}

/** 功能：判断蓝图是否还有未处理的阻断级冲突。
 * Args: blueprint: 待判断蓝图。
 * Returns: 存在未忽略的 blocking 冲突时返回 true。
 */
export function hasBlockingConflict(blueprint: NovelBlueprint): boolean {
  return blueprint.conflicts.some((item) => item.severity === "blocking" && !item.ignored);
}

// ---------------------------------------------------------------------------
// 大纲
// ---------------------------------------------------------------------------

/** 大纲中的战力变化。 */
export interface OutlinePowerChange {
  entity_id: string;
  name: string;
  from: string;
  to: string;
  reason: string;
}

/** 大纲章节。 */
export interface OutlineChapter {
  title: string;
  summary: string;
  goals: string[];
  conflicts: string[];
  character_ids: string[];
  faction_ids: string[];
  setting_card_ids: string[];
  power_changes: OutlinePowerChange[];
  foreshadowing: string[];
  /** 确认后由后端写入的正式章节业务 ID；未创建时为空。 */
  chapter_id?: string | null;
}

/** 大纲卷。 */
export interface OutlineVolume {
  title: string;
  summary: string;
  chapters: OutlineChapter[];
  /** 确认后由后端写入的正式卷业务 ID；未创建时为空。 */
  volume_id?: string | null;
}

/** 卷章大纲。用户修改后保存为新版本，AI 原始结果保留。 */
export interface NovelOutline {
  volumes: OutlineVolume[];
  version: number;
  generation_id?: string | null;
  /** 生成该大纲时使用的蓝图版本。 */
  blueprint_version?: number | null;
  confirmed?: boolean;
}

/** 功能：构造一份空大纲。
 * Args: 无。
 * Returns: 卷列表为空、版本为 1 的 NovelOutline。
 */
export function createEmptyOutline(): NovelOutline {
  return { volumes: [], version: 1, generation_id: null, blueprint_version: null, confirmed: false };
}

/** 功能：把后端返回的大纲结构安全合并为前端大纲。
 * Args: raw: 后端返回的原始对象。
 * Returns: 字段完整的 NovelOutline。
 */
export function normalizeOutline(raw: Partial<NovelOutline> & Record<string, unknown>): NovelOutline {
  const toStringArray = (value: unknown): string[] =>
    Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

  const volumes: OutlineVolume[] = Array.isArray(raw.volumes)
    ? (raw.volumes as unknown as Record<string, unknown>[]).map((volume) => ({
        title: typeof volume.title === "string" ? volume.title : "",
        summary: typeof volume.summary === "string" ? volume.summary : "",
        volume_id: typeof volume.volume_id === "string" ? volume.volume_id : null,
        chapters: Array.isArray(volume.chapters)
          ? (volume.chapters as Record<string, unknown>[]).map((chapter) => ({
              title: typeof chapter.title === "string" ? chapter.title : "",
              summary: typeof chapter.summary === "string" ? chapter.summary : "",
              goals: toStringArray(chapter.goals),
              conflicts: toStringArray(chapter.conflicts),
              character_ids: toStringArray(chapter.character_ids),
              faction_ids: toStringArray(chapter.faction_ids),
              setting_card_ids: toStringArray(chapter.setting_card_ids),
              power_changes: Array.isArray(chapter.power_changes)
                ? (chapter.power_changes as OutlinePowerChange[])
                : [],
              foreshadowing: toStringArray(chapter.foreshadowing),
              chapter_id: typeof chapter.chapter_id === "string" ? chapter.chapter_id : null,
            }))
          : [],
      }))
    : [];

  return {
    volumes,
    version: typeof raw.version === "number" ? raw.version : 1,
    generation_id: typeof raw.generation_id === "string" ? raw.generation_id : null,
    blueprint_version: typeof raw.blueprint_version === "number" ? raw.blueprint_version : null,
    confirmed: raw.confirmed === true,
  };
}

/** 功能：统计大纲的卷数、章节数与目标字数口径的章节总数。
 * Args: outline: 待统计大纲。
 * Returns: 卷数与章节数。
 */
export function countOutline(outline: NovelOutline): { volumeCount: number; chapterCount: number } {
  return {
    volumeCount: outline.volumes.length,
    chapterCount: outline.volumes.reduce((sum, volume) => sum + volume.chapters.length, 0),
  };
}

/** 功能：把战力体系从蓝图取出，缺失时返回空白体系而不是 null，便于编辑器受控渲染。
 * Args: blueprint: 来源蓝图。
 * Returns: 一定非空的 PowerSystem。
 */
export function blueprintPowerSystem(blueprint: NovelBlueprint): PowerSystem {
  return blueprint.power_system ?? createEmptyPowerSystem();
}

export { splitLines };
