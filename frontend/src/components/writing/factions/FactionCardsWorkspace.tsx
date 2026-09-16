"use client";

import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Button, Modal } from "@heroui/react";
import { ApiRequestError, apiDelete, apiGet, apiPost, apiPostSSE, apiPut, type ApiFieldErrors } from "@/lib/api";
import {
  buildGenerationParamsPayload,
  DEFAULT_GENERATION_PARAMS,
  GenerationParamsCollapse,
  type GenerationParamsValue,
} from "@/components/shared/GenerationParamsCollapse";
import {
  ConfirmActionModal,
  focusRelationEditorError,
  RelationEditorErrorSummary,
  RelationFieldShell,
  RelationIntensityField,
} from "@/components/shared/RelationEditorPrimitives";
import { TooltipHint } from "@/components/shared/TooltipHint";
import TagInput from "@/components/shared/TagInput";
import { CREATE_FACTIONS_WORKFLOW_NAME, normalizeAppConfig, type AppConfig } from "@/types/config";
import type {
  BulkCreateCoreFactionsResponse,
  CoreFaction,
  CoreFactionsPayload,
  CoreFactionsSavePayload,
  FactionRelation,
  FactionRelationCreateRequestV1,
  FactionRelationUpdateRequestV1,
  FactionRelationType,
  GeneratedCoreFactionsPayload,
  GeneratedFactionRelation,
  GenerateCoreFactionsRequest,
} from "@/types/novel";

interface FactionCardsWorkspaceProps {
  mode: "create" | "edit";
  novelId?: string;
}

type FactionWorkspaceView = "profiles" | "relations" | "trash";
type FactionLevelFilter = "all" | "core" | "volume" | "local";
type EditorState = { mode: "create" | "edit"; draft: CoreFaction } | null;
type DeleteIntent = { mode: "soft" | "hard"; faction: CoreFaction } | null;
type RelationEditorState = {
  mode: "create" | "edit";
  draft: FactionRelationCreateRequestV1;
  source?: FactionRelation;
} | null;
type RelationDeleteIntent = { mode: "soft" | "hard"; relation: FactionRelation } | null;
interface ExistingRelationTarget {
  factionName: string;
  relationScore: number;
}

interface FactionGenerationOptions {
  factionCount: number;
  independentEnabled: boolean;
  independentFactionCount: number;
  userGuidance: string;
  singleFactionIntegrityScore: number;
  connectToExisting: boolean;
  existingRelationTargets: ExistingRelationTarget[];
}

const RELATION_TYPES: FactionRelationType[] = [
  "hostile",
  "allied",
  "cold_war",
  "dependent",
  "subordinate",
  "trade_partner",
  "secret_cooperation",
  "historical_enemy",
];

const CORE_FACTION_WARNING_COUNT = 6;
const DEFAULT_FACTION_GENERATION_OPTIONS: FactionGenerationOptions = {
  factionCount: 3,
  independentEnabled: false,
  independentFactionCount: 1,
  userGuidance: "",
  singleFactionIntegrityScore: 5,
  connectToExisting: false,
  existingRelationTargets: [],
};

/**
 * 解析核心阵营生成步骤实际使用的 Provider。
 *
 * Args:
 *   config: 前端已归一化的应用配置。
 *
 * Returns:
 *   核心阵营生成步骤对应的 Provider 别名；未配置时返回全局默认 Provider。
 */
function resolveCoreFactionsGenerationProvider(config: AppConfig): string {
  const workflow = config.llm.workflows?.[CREATE_FACTIONS_WORKFLOW_NAME];
  return (
    workflow?.steps?.create_core_factions?.provider ||
    workflow?.default_provider ||
    config.llm.default_provider ||
    ""
  );
}

/**
 * 判断核心阵营生成当前是否可以使用流式响应。
 *
 * Args:
 *   config: 前端已归一化的应用配置。
 *
 * Returns:
 *   仅当步骤 Provider 已启用且声明支持流式时返回 true。
 */
function supportsCoreFactionsStreaming(config: AppConfig): boolean {
  const providerAlias = resolveCoreFactionsGenerationProvider(config);
  const provider = config.llm.providers[providerAlias];
  return Boolean(provider?.enabled && provider.supports_streaming);
}

/**
 * 从核心阵营生成 SSE 完成事件中提取最终结果。
 *
 * Args:
 *   data: 后端 SSE done 事件携带的数据。
 *
 * Returns:
 *   通过基础结构判断的生成结果；格式不匹配时返回 null。
 */
function extractCoreFactionsStreamResult(data: Record<string, unknown>): GeneratedCoreFactionsPayload | null {
  const result = data.result;
  if (!result || typeof result !== "object") {
    return null;
  }

  const payload = result as Partial<GeneratedCoreFactionsPayload>;
  if (!Array.isArray(payload.core_factions) || !Array.isArray(payload.faction_relations)) {
    return null;
  }

  return payload as GeneratedCoreFactionsPayload;
}

/**
 * 归一化 AI 生成结果，补齐前端编辑所需默认值。
 *
 * Args:
 *   payload: 后端返回的核心阵营与关系预览。
 *
 * Returns:
 *   可直接进入前端编辑状态的预览数据。
 */
function normalizeGeneratedPayload(payload: GeneratedCoreFactionsPayload): CoreFactionsPayload {
  return {
    core_factions: payload.core_factions.map((faction, index) =>
      createFactionDraft(faction, index),
    ),
    faction_relations: payload.faction_relations.map((relation) => ({
      ...relation,
      hidden_tension: relation.hidden_tension ?? "",
      intensity: relation.intensity ?? 3,
      is_active: relation.is_active ?? true,
    })),
  };
}

/**
 * 生成可编辑的核心阵营草稿。
 *
 * Args:
 *   source: 可选的已有阵营数据。
 *   index: 草稿排序位置。
 *
 * Returns:
 *   字段齐全的核心阵营草稿。
 */
function createFactionDraft(source: Partial<CoreFaction> = {}, index = 0): CoreFaction {
  return {
    _id: source._id,
    novel_id: source.novel_id,
    faction_id: source.faction_id,
    name: source.name ?? "",
    alias: source.alias ?? [],
    faction_type: source.faction_type ?? "",
    level_type: source.level_type ?? "core",
    parent_faction_id: source.parent_faction_id ?? null,
    positioning: source.positioning ?? "",
    public_stance: source.public_stance ?? "",
    core_goal: source.core_goal ?? "",
    hidden_goal: source.hidden_goal ?? "",
    resources_and_advantages: source.resources_and_advantages ?? [],
    organization_style: source.organization_style ?? "",
    core_values: source.core_values ?? [],
    conflict_with_mainline: source.conflict_with_mainline ?? "",
    is_public: source.is_public ?? true,
    influence_scope: source.influence_scope ?? "",
    active_status: source.active_status ?? "active",
    expandability: source.expandability ?? "",
    tags: source.tags ?? [],
    sort_order: source.sort_order ?? (index + 1) * 10,
    // 新建与 AI 预览草稿从版本 1 起步；正式数据沿用接口返回的当前版本。
    version: source.version ?? 1,
  };
}

/**
 * 提取阵营写入 API 支持的字段。
 *
 * Args:
 *   draft: 前端编辑中的阵营草稿。
 *
 * Returns:
 *   可提交给创建或更新接口的阵营字段。
 */
function buildFactionRequest(draft: CoreFaction): Partial<CoreFaction> {
  return {
    name: draft.name.trim(),
    alias: draft.alias ?? [],
    faction_type: draft.faction_type.trim(),
    level_type: draft.level_type ?? "core",
    parent_faction_id: draft.parent_faction_id ?? null,
    positioning: draft.positioning.trim(),
    public_stance: draft.public_stance.trim(),
    core_goal: draft.core_goal.trim(),
    hidden_goal: draft.hidden_goal?.trim() ?? "",
    resources_and_advantages: draft.resources_and_advantages ?? [],
    organization_style: draft.organization_style.trim(),
    core_values: draft.core_values ?? [],
    conflict_with_mainline: draft.conflict_with_mainline.trim(),
    is_public: draft.is_public,
    influence_scope: draft.influence_scope.trim(),
    active_status: draft.active_status ?? "active",
    expandability: draft.expandability.trim(),
    tags: draft.tags ?? [],
    sort_order: draft.sort_order ?? 0,
  };
}

/**
 * 构造势力档案更新请求，并携带当前实体版本用于 CAS 校验。
 *
 * Args:
 *   draft: 包含服务端当前版本的势力编辑草稿。
 *
 * Returns:
 *   仅包含可编辑字段及 expected_version 的更新请求体。
 */
export function buildFactionUpdateRequest(
  draft: CoreFaction,
): Partial<CoreFaction> & { expected_version: number } {
  return {
    ...buildFactionRequest(draft),
    expected_version: draft.version,
  };
}

/**
 * 构造势力恢复接口使用的乐观锁请求体。
 *
 * Args:
 *   faction: 待恢复的正式势力实体。
 *
 * Returns:
 *   仅包含当前 expected_version 的请求体。
 */
export function buildFactionVersionRequest(
  faction: Pick<CoreFaction, "version">,
): { expected_version: number } {
  return { expected_version: faction.version };
}

/**
 * 为势力软删除或硬删除接口附加当前实体版本查询参数。
 *
 * Args:
 *   path: 不含查询参数的删除接口路径。
 *   faction: 待删除的正式势力实体。
 *
 * Returns:
 *   带 expected_version 查询参数的删除接口路径。
 */
export function buildFactionVersionedDeletePath(
  path: string,
  faction: Pick<CoreFaction, "version">,
): string {
  const query = new URLSearchParams({ expected_version: String(faction.version) });
  return `${path}?${query.toString()}`;
}

/**
 * 构造批量保存核心阵营接口接受的窄请求体。
 *
 * Args:
 *   preview: 前端预览编辑态中的核心阵营与关系数据。
 *
 * Returns:
 *   只包含后端 CoreFactionsResultSchema 允许字段的请求体。
 */
function buildCoreFactionsSavePayload(preview: CoreFactionsPayload): CoreFactionsSavePayload {
  return {
    core_factions: preview.core_factions.map((faction) => ({
      // 批量保存接口复用 AI 输出 schema，不能携带编辑态或数据库字段。
      name: faction.name.trim(),
      faction_type: faction.faction_type.trim(),
      positioning: faction.positioning.trim(),
      public_stance: faction.public_stance.trim(),
      core_goal: faction.core_goal.trim(),
      hidden_goal: faction.hidden_goal?.trim() ?? "",
      resources_and_advantages: faction.resources_and_advantages ?? [],
      organization_style: faction.organization_style.trim(),
      core_values: faction.core_values ?? [],
      conflict_with_mainline: faction.conflict_with_mainline.trim(),
      is_public: faction.is_public,
      influence_scope: faction.influence_scope.trim(),
      expandability: faction.expandability.trim(),
      tags: faction.tags ?? [],
    })),
    faction_relations: preview.faction_relations.map((relation) => ({
      // 关系保存阶段仍使用阵营名称引用，后端会统一映射为稳定的 faction_id。
      source_faction_name: relation.source_faction_name.trim(),
      target_faction_name: relation.target_faction_name.trim(),
      relation_type: relation.relation_type,
      current_state: relation.current_state.trim(),
      core_conflict: relation.core_conflict.trim(),
      hidden_tension: relation.hidden_tension?.trim() ?? "",
      possible_change: relation.possible_change.trim(),
      intensity: relation.intensity ?? 3,
      is_active: relation.is_active ?? true,
    })),
  };
}

/**
 * 判断单个阵营生成是否允许配置与已有阵营的关系。
 *
 * Args:
 *   options: 当前 AI 生成面板参数。
 *   hasExistingCoreFactions: 当前小说是否已有未删除核心阵营。
 *
 * Returns:
 *   满足单个、非独立、且存在已有阵营时返回 true。
 */
function canConfigureExistingRelation(
  options: FactionGenerationOptions,
  hasExistingCoreFactions: boolean,
): boolean {
  return options.factionCount === 1 && !options.independentEnabled && hasExistingCoreFactions;
}

/**
 * 读取已有核心阵营可用于选择器展示的名称。
 *
 * Args:
 *   faction: 已保存的核心阵营数据。
 *
 * Returns:
 *   去除首尾空白后的阵营名称。
 */
function getFactionName(faction: CoreFaction): string {
  return faction.name.trim();
}

/**
 * 归一化已有阵营关系目标，避免重复名称与越界分值进入请求体。
 *
 * Args:
 *   targets: 参数面板中已选择的关系目标。
 *
 * Returns:
 *   名称去重且分值限制在 0-10 的目标列表。
 */
function normalizeExistingRelationTargets(targets: ExistingRelationTarget[]): ExistingRelationTarget[] {
  const seen = new Set<string>();
  const normalized: ExistingRelationTarget[] = [];
  for (const target of targets) {
    const factionName = target.factionName.trim();
    if (!factionName || seen.has(factionName)) {
      continue;
    }
    seen.add(factionName);
    normalized.push({
      factionName,
      relationScore: Math.max(0, Math.min(10, target.relationScore)),
    });
  }
  return normalized;
}

/**
 * 构造核心阵营 AI 生成请求体。
 *
 * Args:
 *   novelId: 当前小说 ObjectId。
 *   options: 势力生成策略参数。
 *   genParams: 复用的请求级 LLM 生成参数。
 *   hasExistingCoreFactions: 当前小说是否已有未删除核心阵营。
 *
 * Returns:
 *   可提交到 generate-core-factions 接口的请求体。
 */
function buildGenerateCoreFactionsRequest(
  novelId: string,
  options: FactionGenerationOptions,
  genParams: GenerationParamsValue,
  hasExistingCoreFactions: boolean,
): GenerateCoreFactionsRequest {
  const factionCount = Math.max(1, Math.min(6, options.factionCount));
  const independentFactionCount = options.independentEnabled
    ? Math.max(1, Math.min(factionCount, options.independentFactionCount))
    : 0;
  const shouldConnectToExisting =
    canConfigureExistingRelation({ ...options, factionCount }, hasExistingCoreFactions) &&
    options.connectToExisting &&
    options.existingRelationTargets.length > 0;

  return {
    novel_id: novelId,
    faction_count: factionCount,
    independent_faction_count: independentFactionCount,
    user_guidance: options.userGuidance.trim(),
    ...(factionCount === 1 && {
      single_faction_integrity_score: options.singleFactionIntegrityScore,
      connect_to_existing: shouldConnectToExisting,
      ...(shouldConnectToExisting && {
        existing_relation_targets: options.existingRelationTargets.map((target) => ({
          faction_name: target.factionName,
          relation_score: target.relationScore,
        })),
      }),
    }),
    ...buildGenerationParamsPayload(genParams),
  };
}

/**
 * 生成关系端点下拉列表，合并本次预览阵营和已保存阵营。
 *
 * Args:
 *   preview: 当前 AI 生成预览。
 *   savedFactions: 已保存核心阵营列表。
 *
 * Returns:
 *   去重后的阵营名称列表。
 */
function buildRelationFactionOptions(preview: CoreFactionsPayload, savedFactions: CoreFaction[]): string[] {
  const names = new Set<string>();
  for (const faction of [...preview.core_factions, ...savedFactions]) {
    const name = faction.name.trim();
    if (name) {
      names.add(name);
    }
  }
  return Array.from(names);
}

/**
 * 检查预览中的新增关系是否都至少连接一个本次生成阵营。
 *
 * Args:
 *   preview: 当前 AI 生成预览。
 *
 * Returns:
 *   存在只连接已有阵营的关系时返回 true。
 */
function hasRelationWithoutGeneratedFaction(preview: CoreFactionsPayload): boolean {
  const generatedNames = new Set(preview.core_factions.map((faction) => faction.name.trim()).filter(Boolean));
  return preview.faction_relations.some((relation) => {
    const sourceName = relation.source_faction_name.trim();
    const targetName = relation.target_faction_name.trim();
    return !generatedNames.has(sourceName) && !generatedNames.has(targetName);
  });
}

/**
 * Resolve the stable frontend identity for a saved core faction.
 *
 * Args:
 *   faction: A faction returned by the API or held in local state.
 *
 * Returns:
 *   The business id when available, then Mongo id, then the faction name.
 */
function getFactionRenderKey(faction: CoreFaction): string {
  return faction.faction_id || faction._id || faction.name;
}

/**
 * 功能：按势力层级与搜索关键词筛选正式势力档案。
 *
 * Args:
 *   factions: 当前小说下已加载的正式势力。
 *   search: 用户输入的名称或档案关键词。
 *   levelFilter: 全部或指定势力层级。
 *
 * Returns:
 *   同时满足层级和关键词条件的势力列表，原始数组保持不变。
 */
export function filterFactionProfiles(
  factions: CoreFaction[],
  search: string,
  levelFilter: FactionLevelFilter,
): CoreFaction[] {
  const query = search.trim().toLocaleLowerCase();

  return factions.filter((faction) => {
    const level = faction.level_type || "core";
    if (levelFilter !== "all" && level !== levelFilter) {
      return false;
    }
    if (!query) {
      return true;
    }

    // 搜索覆盖名册识别信息和主要叙述字段，便于从较长的势力档案中快速定位。
    const searchableValues = [
      faction.name,
      faction.faction_type,
      faction.positioning,
      faction.public_stance,
      faction.core_goal,
      faction.hidden_goal,
      faction.organization_style,
      faction.conflict_with_mainline,
      faction.influence_scope,
      ...(faction.alias ?? []),
      ...(faction.tags ?? []),
      ...(faction.resources_and_advantages ?? []),
      ...(faction.core_values ?? []),
    ];
    return searchableValues.some((value) => value?.toLocaleLowerCase().includes(query));
  });
}

/**
 * Resolve the stable frontend identity for a saved faction relation.
 *
 * Args:
 *   relation: A relation returned by the API or held in local state.
 *
 * Returns:
 *   The business id when available, then Mongo id, then a relation endpoint signature.
 */
function getRelationRenderKey(relation: FactionRelation): string {
  return (
    relation.relation_id ||
    relation._id ||
    `${relation.source_faction_id ?? relation.source_faction_name ?? ""}->${relation.target_faction_id ?? relation.target_faction_name ?? ""}:${relation.relation_type}`
  );
}

/**
 * Merge incoming API rows into current state without duplicating React keys.
 *
 * Args:
 *   current: Rows already rendered in the workspace.
 *   incoming: Fresh rows returned by save or reload APIs.
 *   getKey: Stable identity resolver for each row.
 *
 * Returns:
 *   A list where later rows replace earlier rows with the same key.
 */
function mergeUniqueByKey<T>(current: T[], incoming: T[], getKey: (item: T) => string): T[] {
  const rowsByKey = new Map<string, T>();

  // 保存响应和刷新请求可能交错返回，统一按业务 ID 覆盖旧行，避免同一条记录被追加两次。
  for (const item of [...current, ...incoming]) {
    rowsByKey.set(getKey(item), item);
  }

  return Array.from(rowsByKey.values());
}

/**
 * 渲染已保存小说的势力卡工作台，并承接核心阵营生成、编辑与垃圾桶操作。
 *
 * Args:
 *   mode: 写作页当前模式，只有 edit 模式允许读取和写入已保存小说。
 *   novelId: 已保存小说的 ObjectId 字符串。
 *
 * Returns:
 *   势力卡模块 React 节点。
 */
export default function FactionCardsWorkspace({ mode, novelId }: FactionCardsWorkspaceProps) {
  const t = useTranslations("writing.factions");
  const [activeView, setActiveView] = useState<FactionWorkspaceView>("profiles");
  const [search, setSearch] = useState("");
  const [levelFilter, setLevelFilter] = useState<FactionLevelFilter>("all");
  const [factions, setFactions] = useState<CoreFaction[]>([]);
  const [trashFactions, setTrashFactions] = useState<CoreFaction[]>([]);
  const [relations, setRelations] = useState<FactionRelation[]>([]);
  const [trashRelations, setTrashRelations] = useState<FactionRelation[]>([]);
  const [preview, setPreview] = useState<CoreFactionsPayload | null>(null);
  const [editor, setEditor] = useState<EditorState>(null);
  const [deleteIntent, setDeleteIntent] = useState<DeleteIntent>(null);
  const [relationEditor, setRelationEditor] = useState<RelationEditorState>(null);
  const [relationDeleteIntent, setRelationDeleteIntent] = useState<RelationDeleteIntent>(null);
  const [relationEditorError, setRelationEditorError] = useState("");
  const [relationFieldErrors, setRelationFieldErrors] = useState<ApiFieldErrors>({});
  const [relationCommandKey, setRelationCommandKey] = useState("");
  const [generationDialogOpen, setGenerationDialogOpen] = useState(false);
  const [generationOptions, setGenerationOptions] = useState<FactionGenerationOptions>(DEFAULT_FACTION_GENERATION_OPTIONS);
  const [generationParams, setGenerationParams] = useState<GenerationParamsValue>(DEFAULT_GENERATION_PARAMS);
  const [showGenerationParams, setShowGenerationParams] = useState(false);
  const [coreFactionsStreamingSupported, setCoreFactionsStreamingSupported] = useState(false);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actionId, setActionId] = useState("");
  const [error, setError] = useState("");

  const canUseSavedNovel = mode === "edit" && Boolean(novelId);
  const coreFactions = useMemo(
    () => factions.filter((faction) => (faction.level_type || "core") === "core"),
    [factions],
  );
  const filteredFactions = useMemo(
    () => filterFactionProfiles(factions, search, levelFilter),
    [factions, levelFilter, search],
  );
  const shouldWarnTooManyCoreFactions = coreFactions.length >= CORE_FACTION_WARNING_COUNT;
  const canOpenGeneratePanel = !preview && !generating && !saving;
  const availableRelationFactions = useMemo(
    () => coreFactions.filter((faction) => Boolean(faction.faction_id) && !faction.is_deleted && (faction.active_status ?? "active") === "active"),
    [coreFactions],
  );

  const relationTypeLabels: Record<FactionRelationType, string> = useMemo(
    () => ({
      hostile: t("relationTypes.hostile"),
      allied: t("relationTypes.allied"),
      cold_war: t("relationTypes.coldWar"),
      dependent: t("relationTypes.dependent"),
      subordinate: t("relationTypes.subordinate"),
      trade_partner: t("relationTypes.tradePartner"),
      secret_cooperation: t("relationTypes.secretCooperation"),
      historical_enemy: t("relationTypes.historicalEnemy"),
    }),
    [t],
  );

  const workspaceTabs = useMemo(
    () => [
      { key: "profiles" as const, label: t("tabs.profiles"), count: factions.length },
      { key: "relations" as const, label: t("tabs.relations"), count: relations.length },
      { key: "trash" as const, label: t("tabs.trash"), count: trashFactions.length + trashRelations.length },
    ],
    [factions.length, relations.length, trashFactions.length, trashRelations.length, t],
  );

  const previewRelationFactionOptions = useMemo(
    () => (preview ? buildRelationFactionOptions(preview, coreFactions) : []),
    [preview, coreFactions],
  );

  const loadData = useCallback(async () => {
    if (!novelId) return;
    try {
      setLoading(true);
      setError("");
      const [coreRes, volumeRes, localRes, relationRes, trashRes, relationTrashRes] = await Promise.all([
        apiGet<{ data: CoreFaction[] }>(`/api/factions/novel/${novelId}/level/core`),
        apiGet<{ data: CoreFaction[] }>(`/api/factions/novel/${novelId}/level/volume`),
        apiGet<{ data: CoreFaction[] }>(`/api/factions/novel/${novelId}/level/local`),
        apiGet<{ data: FactionRelation[] }>(`/api/faction-relations/novel/${novelId}`),
        apiGet<{ data: CoreFaction[] }>(`/api/factions/novel/${novelId}/trash`),
        apiGet<{ data: FactionRelation[] }>(`/api/faction-relations/novel/${novelId}/trash`),
      ]);
      const loadedFactions = [...coreRes.data, ...volumeRes.data, ...localRes.data];
      setFactions(mergeUniqueByKey([], loadedFactions.map((item, index) => createFactionDraft(item, index)), getFactionRenderKey));
      setRelations(mergeUniqueByKey([], relationRes.data, getRelationRenderKey));
      setTrashFactions(mergeUniqueByKey([], trashRes.data.map((item, index) => createFactionDraft(item, index)), getFactionRenderKey));
      setTrashRelations(mergeUniqueByKey([], relationTrashRes.data, getRelationRenderKey));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [novelId]);

  const loadGenerationCapabilities = useCallback(async () => {
    try {
      const res = await apiGet<{ data: AppConfig }>("/api/config");
      const config = normalizeAppConfig(res.data);
      setCoreFactionsStreamingSupported(supportsCoreFactionsStreaming(config));
    } catch {
      // 配置读取失败时不阻塞势力卡使用，后续请求回退到兼容 JSON 接口。
      setCoreFactionsStreamingSupported(false);
    }
  }, []);

  useEffect(() => {
    if (canUseSavedNovel) {
      loadData();
      loadGenerationCapabilities();
    }
  }, [canUseSavedNovel, loadData, loadGenerationCapabilities]);

  const handleOpenGenerationDialog = () => {
    if (preview) {
      setError(t("previewBlockingGenerate"));
      return;
    }
    setError("");
    void loadGenerationCapabilities();
    setGenerationDialogOpen(true);
  };

  const handleGenerationOptionsChange = (patch: Partial<FactionGenerationOptions>) => {
    setGenerationOptions((current) => {
      const next = { ...current, ...patch };
      const factionCount = Math.max(1, Math.min(6, next.factionCount));
      return {
        ...next,
        factionCount,
        independentFactionCount: Math.max(1, Math.min(factionCount, next.independentFactionCount)),
        singleFactionIntegrityScore: Math.max(0, Math.min(10, next.singleFactionIntegrityScore)),
        connectToExisting: canConfigureExistingRelation({ ...next, factionCount }, coreFactions.length > 0)
          ? next.connectToExisting
          : false,
        existingRelationTargets: canConfigureExistingRelation({ ...next, factionCount }, coreFactions.length > 0)
          ? normalizeExistingRelationTargets(next.existingRelationTargets)
          : [],
      };
    });
  };

  const handleGenerate = async () => {
    if (!novelId || preview) return;
    try {
      setGenerating(true);
      setError("");
      const request = buildGenerateCoreFactionsRequest(novelId, generationOptions, generationParams, coreFactions.length > 0);
      let result: GeneratedCoreFactionsPayload | null = null;
      const shouldUseStream = Boolean(request.use_stream && coreFactionsStreamingSupported);

      if (shouldUseStream) {
        await apiPostSSE("/api/llm/generate-core-factions/stream", request, (event, eventData) => {
          if (event === "error") {
            throw new Error(String(eventData.error || eventData.message || t("generateFailed")));
          }

          if (event === "done") {
            result = extractCoreFactionsStreamResult(eventData);
          }
        });
      } else {
        result = await apiPost<GeneratedCoreFactionsPayload>("/api/llm/generate-core-factions", request);
      }

      if (!result) {
        throw new Error(t("generateFailed"));
      }

      setPreview(normalizeGeneratedPayload(result));
      setGenerationDialogOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setGenerating(false);
    }
  };

  const handleSavePreview = async () => {
    if (!novelId || !preview) return;
    if (hasRelationWithoutGeneratedFaction(preview)) {
      setError(t("validation.relationMustIncludeNewFaction"));
      return;
    }
    try {
      setSaving(true);
      setError("");
      const saved = await apiPost<BulkCreateCoreFactionsResponse>(
        `/api/factions/novel/${novelId}/bulk-core-with-relations`,
        buildCoreFactionsSavePayload(preview),
      );
      setFactions((prev) => mergeUniqueByKey(prev, saved.factions.map((item, index) => createFactionDraft(item, index)), getFactionRenderKey));
      setRelations((prev) => mergeUniqueByKey(prev, saved.faction_relations, getRelationRenderKey));
      setPreview(null);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleOpenCreate = () => {
    setError("");
    setActiveView("profiles");
    setLevelFilter("core");
    setEditor({
      mode: "create",
      draft: createFactionDraft({ level_type: "core", sort_order: (coreFactions.length + 1) * 10 }, coreFactions.length),
    });
  };

  const handleOpenEdit = (faction: CoreFaction) => {
    setError("");
    setActiveView("profiles");
    setEditor({ mode: "edit", draft: createFactionDraft(faction) });
  };

  const handleSaveEditor = async () => {
    if (!novelId || !editor) return;
    const request = buildFactionRequest(editor.draft);
    if (!request.name) {
      setError(t("validation.nameRequired"));
      return;
    }

    try {
      setSaving(true);
      setError("");
      if (editor.mode === "create") {
        await apiPost(`/api/factions/novel/${novelId}/create`, request);
      } else if (editor.draft.faction_id) {
        await apiPut(
          `/api/factions/novel/${novelId}/${editor.draft.faction_id}`,
          buildFactionUpdateRequest(editor.draft),
        );
      }
      setEditor(null);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleConfirmDelete = async () => {
    if (!novelId || !deleteIntent?.faction.faction_id) return;
    const factionId = deleteIntent.faction.faction_id;
    const requestKey = `${deleteIntent.mode}:${factionId}`;
    try {
      setActionId(requestKey);
      setError("");
      const deletePath = `/api/factions/novel/${novelId}/${factionId}${deleteIntent.mode === "hard" ? "/hard" : ""}`;
      await apiDelete(buildFactionVersionedDeletePath(deletePath, deleteIntent.faction));
      setDeleteIntent(null);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionId("");
    }
  };

  const handleRestore = async (faction: CoreFaction) => {
    if (!novelId || !faction.faction_id) return;
    const requestKey = `restore:${faction.faction_id}`;
    try {
      setActionId(requestKey);
      setError("");
      await apiPost(
        `/api/factions/novel/${novelId}/${faction.faction_id}/restore`,
        buildFactionVersionRequest(faction),
      );
      await loadData();
      setActiveView("profiles");
      const restoredLevel = faction.level_type || "core";
      setLevelFilter(restoredLevel === "core" || restoredLevel === "volume" || restoredLevel === "local" ? restoredLevel : "all");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionId("");
    }
  };

  const openCreateRelation = () => {
    setActiveView("relations");
    setRelationEditor({ mode: "create", draft: createFactionRelationDraft(availableRelationFactions) });
    setRelationCommandKey(`faction-relation:${globalThis.crypto.randomUUID()}`);
    setRelationEditorError("");
    setRelationFieldErrors({});
  };

  const openEditRelation = (relation: FactionRelation) => {
    setRelationEditor({ mode: "edit", draft: createFactionRelationDraft(factions, relation), source: relation });
    setRelationEditorError("");
    setRelationFieldErrors({});
  };

  const copyRelationAsNew = (relation: FactionRelation) => {
    setRelationEditor({ mode: "create", draft: createFactionRelationDraft(availableRelationFactions, relation) });
    setRelationCommandKey(`faction-relation:${globalThis.crypto.randomUUID()}`);
    setRelationEditorError("");
    setRelationFieldErrors({});
  };

  const handleSaveRelation = async () => {
    if (!novelId || !relationEditor) return;
    if (!isCompleteFactionRelation(relationEditor.draft)) {
      const fields: ApiFieldErrors = {};
      for (const field of ["current_state", "core_conflict", "possible_change"] as const) {
        if (!relationEditor.draft[field].trim()) fields[field] = [t("relationValidation.required")];
      }
      if (!relationEditor.draft.source_faction_id) fields.source_faction_id = [t("relationValidation.required")];
      if (!relationEditor.draft.target_faction_id) fields.target_faction_id = [t("relationValidation.required")];
      if (relationEditor.draft.source_faction_id === relationEditor.draft.target_faction_id) fields.target_faction_id = [t("relationValidation.sameEndpoint")];
      if (relationEditor.draft.intensity < 1 || relationEditor.draft.intensity > 5) fields.intensity = [t("relationValidation.intensity")];
      setRelationEditorError(t("relationValidation.incomplete"));
      setRelationFieldErrors(fields);
      focusRelationEditorError(fields);
      return;
    }
    setSaving(true);
    setRelationEditorError("");
    setRelationFieldErrors({});
    try {
      if (relationEditor.mode === "create") {
        await apiPost(`/api/faction-relations/novel/${novelId}`, relationEditor.draft, {
          headers: { "Idempotency-Key": relationCommandKey || `faction-relation:${globalThis.crypto.randomUUID()}` },
        });
      } else if (relationEditor.source?.relation_id) {
        const request: FactionRelationUpdateRequestV1 = {
          expected_version: relationEditor.source.version ?? 1,
          relation_type: relationEditor.draft.relation_type,
          current_state: relationEditor.draft.current_state,
          core_conflict: relationEditor.draft.core_conflict,
          hidden_tension: relationEditor.draft.hidden_tension,
          possible_change: relationEditor.draft.possible_change,
          intensity: relationEditor.draft.intensity,
        };
        await apiPut(`/api/faction-relations/novel/${novelId}/${relationEditor.source.relation_id}`, request);
      }
      setRelationEditor(null);
      setRelationCommandKey("");
      await loadData();
    } catch (err) {
      if (err instanceof ApiRequestError) {
        setRelationEditorError(err.message);
        setRelationFieldErrors(err.fieldErrors);
        focusRelationEditorError(err.fieldErrors);
      } else {
        setRelationEditorError(err instanceof Error ? err.message : String(err));
        focusRelationEditorError({});
      }
    } finally {
      setSaving(false);
    }
  };

  const handleToggleRelation = async (relation: FactionRelation) => {
    if (!novelId || !relation.relation_id) return;
    setActionId(`relation-active:${relation.relation_id}`);
    setError("");
    try {
      await apiPut(`/api/faction-relations/novel/${novelId}/${relation.relation_id}/active`, {
        expected_version: relation.version ?? 1,
        is_active: !(relation.user_is_active ?? relation.is_active),
      });
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionId("");
    }
  };

  const handleRestoreRelation = async (relation: FactionRelation) => {
    if (!novelId || !relation.relation_id) return;
    setActionId(`relation-restore:${relation.relation_id}`);
    setError("");
    try {
      await apiPost(`/api/faction-relations/novel/${novelId}/${relation.relation_id}/restore`, { expected_version: relation.version ?? 1 });
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionId("");
    }
  };

  const handleConfirmRelationDelete = async () => {
    if (!novelId || !relationDeleteIntent?.relation.relation_id) return;
    const relation = relationDeleteIntent.relation;
    const id = relation.relation_id;
    setActionId(`relation-${relationDeleteIntent.mode}:${id}`);
    setRelationEditorError("");
    try {
      await apiDelete(`/api/faction-relations/novel/${novelId}/${id}${relationDeleteIntent.mode === "hard" ? "/hard" : ""}?expected_version=${relation.version ?? 1}`);
      setRelationDeleteIntent(null);
      await loadData();
    } catch (err) {
      setRelationEditorError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionId("");
    }
  };

  const updatePreviewFaction = (index: number, patch: Partial<CoreFaction>) => {
    setPreview((prev) => {
      if (!prev) return prev;
      const nextFactions = [...prev.core_factions];
      const currentName = nextFactions[index].name;
      nextFactions[index] = { ...nextFactions[index], ...patch };

      // 关系预览使用阵营名称引用，阵营改名时同步维护引用避免保存失败。
      const nextRelations = prev.faction_relations.map((relation) => ({
        ...relation,
        source_faction_name:
          relation.source_faction_name === currentName && patch.name
            ? patch.name
            : relation.source_faction_name,
        target_faction_name:
          relation.target_faction_name === currentName && patch.name
            ? patch.name
            : relation.target_faction_name,
      }));

      return { core_factions: nextFactions, faction_relations: nextRelations };
    });
  };

  const updatePreviewRelation = (index: number, patch: Partial<GeneratedFactionRelation>) => {
    setPreview((prev) => {
      if (!prev) return prev;
      const nextRelations = [...prev.faction_relations];
      nextRelations[index] = { ...nextRelations[index], ...patch };
      return { ...prev, faction_relations: nextRelations };
    });
  };

  if (!canUseSavedNovel) {
    return (
      <div className="flex h-full items-center justify-center px-6">
        <div className="max-w-sm text-center">
          <h2 className="text-lg font-semibold text-foreground">{t("title")}</h2>
          <p className="mt-2 text-sm text-muted">{t("createModeUnavailable")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <header className="shrink-0 border-b border-border bg-surface">
        <div className="px-4 py-3 sm:px-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold uppercase tracking-[0.18em] text-accent">{t("workspaceEyebrow")}</p>
              <h2 className="sr-only">{t("title")}</h2>
            </div>
            <Button className="shrink-0" variant="ghost" size="sm" onPress={loadData} isDisabled={loading || saving || generating}>
              {loading ? t("loading") : t("refresh")}
            </Button>
          </div>

          {error && (
            <div className="mt-3 border-y border-red-200 bg-red-50/70 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
              {error}
            </div>
          )}
          {activeView === "profiles" && preview && (
            <InfoBlock tone="muted" text={t("previewBlockingGenerate")} />
          )}
          {activeView === "profiles" && shouldWarnTooManyCoreFactions && (
            <InfoBlock tone="warning" text={t("tooManyWarning")} />
          )}
        </div>

        <nav className="flex gap-5 overflow-x-auto px-4 sm:px-6" role="tablist" aria-label={t("title")}>
          {workspaceTabs.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={activeView === item.key}
              onClick={() => setActiveView(item.key)}
              className={`flex min-h-11 shrink-0 items-center gap-2 border-b-2 px-1 text-sm font-medium transition-colors ${
                activeView === item.key
                  ? "border-accent text-accent"
                  : "border-transparent text-muted hover:border-border hover:text-foreground"
              }`}
            >
              <span>{item.label}</span>
              <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-[11px] text-muted">{item.count}</span>
            </button>
          ))}
        </nav>
      </header>

      <main className="workspace-scrollbar min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:overflow-hidden">
        {activeView === "profiles" && (
          <div className="flex h-full min-h-0 w-full flex-col gap-5">
            {preview && (
              <section className="workspace-scrollbar max-h-[46%] shrink-0 overflow-y-auto border-b border-border pb-7 pr-1">
                <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">{t("previewEyebrow")}</p>
                    <h3 className="mt-1 text-lg font-semibold text-foreground">{t("previewTitle")}</h3>
                    <p className="mt-1 text-sm text-muted">
                      {t("previewMeta", {
                        factions: preview.core_factions.length,
                        relations: preview.faction_relations.length,
                      })}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="ghost" size="sm" onPress={() => setPreview(null)} isDisabled={saving}>
                      {t("discardPreview")}
                    </Button>
                    <Button
                      variant="primary"
                      size="sm"
                      className="bg-accent text-white hover:bg-accent-hover"
                      onPress={handleSavePreview}
                      isDisabled={saving}
                    >
                      {saving ? t("saving") : t("savePreview")}
                    </Button>
                  </div>
                </div>

                <FactionPreviewEditor
                  preview={preview}
                  relationFactionOptions={previewRelationFactionOptions}
                  relationTypeLabels={relationTypeLabels}
                  onFactionChange={updatePreviewFaction}
                  onRelationChange={updatePreviewRelation}
                  t={t}
                />
              </section>
            )}
            <FactionProfilesWorkspace
              factions={filteredFactions}
              allFactionCount={factions.length}
              search={search}
              levelFilter={levelFilter}
              loading={loading}
              generationLocked={!canOpenGeneratePanel}
              actionId={actionId}
              onSearchChange={setSearch}
              onLevelFilterChange={setLevelFilter}
              onManualCreate={handleOpenCreate}
              onGenerate={handleOpenGenerationDialog}
              onEdit={handleOpenEdit}
              onDelete={(faction) => {
                setError("");
                setDeleteIntent({ mode: "soft", faction });
              }}
              t={t}
            />
          </div>
        )}

        {activeView === "relations" && (
          <div className="flex h-full min-h-0 w-full flex-col gap-5">
            <section className="flex shrink-0 flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h3 className="text-lg font-semibold text-foreground">{t("tabs.relations")}</h3>
                <p className="mt-1 text-sm text-muted">{t("savedRelationsHint")}</p>
              </div>
              <Button variant="outline" size="sm" onPress={openCreateRelation} isDisabled={availableRelationFactions.length < 2 || saving}>
                {t("relationActions.create")}
              </Button>
            </section>
            {relations.length === 0 ? (
              <EmptyBlock text={t("emptyRelations")} />
            ) : (
              <div className="min-h-[500px] flex-1 lg:min-h-0">
                <FactionRelationsWorkspace relations={relations} relationTypeLabels={relationTypeLabels} actionId={actionId} onEdit={openEditRelation} onCopy={copyRelationAsNew} onToggle={(relation) => void handleToggleRelation(relation)} onDelete={(relation) => { setRelationEditorError(""); setRelationDeleteIntent({ mode: "soft", relation }); }} t={t} />
              </div>
            )}
          </div>
        )}

        {activeView === "trash" && (
          <div className="workspace-scrollbar h-full w-full overflow-y-auto pr-1">
            <TrashPanel
              factions={trashFactions}
              relations={trashRelations}
              actionId={actionId}
              onRestore={handleRestore}
              onHardDelete={(faction) => {
                setError("");
                setDeleteIntent({ mode: "hard", faction });
              }}
              onRestoreRelation={(relation) => void handleRestoreRelation(relation)}
              onHardDeleteRelation={(relation) => {
                setRelationEditorError("");
                setRelationDeleteIntent({ mode: "hard", relation });
              }}
              t={t}
            />
          </div>
        )}
      </main>

      {editor && (
        <EditorModal
          editor={editor}
          saving={saving}
          error={error}
          onChange={(draft) => setEditor((prev) => (prev ? { ...prev, draft } : prev))}
          onCancel={() => setEditor(null)}
          onSave={handleSaveEditor}
          t={t}
        />
      )}

      {relationEditor && (
        <FactionRelationEditorModal
          editor={relationEditor}
          factions={relationEditor.mode === "create" ? availableRelationFactions : coreFactions}
          saving={saving}
          error={relationEditorError}
          fieldErrors={relationFieldErrors}
          onChange={(draft) => setRelationEditor((current) => current ? { ...current, draft } : current)}
          onCancel={() => setRelationEditor(null)}
          onSave={() => void handleSaveRelation()}
          t={t}
        />
      )}

      {generationDialogOpen && (
        <GenerateCoreFactionsModal
          options={generationOptions}
          genParams={generationParams}
          showGenParams={showGenerationParams}
          generating={generating}
          error={error}
          existingFactions={coreFactions}
          onOptionsChange={handleGenerationOptionsChange}
          onGenParamsChange={setGenerationParams}
          onShowGenParamsChange={setShowGenerationParams}
          onCancel={() => setGenerationDialogOpen(false)}
          onGenerate={handleGenerate}
          t={t}
        />
      )}

      {deleteIntent && (
          <ConfirmActionModal
          title={deleteIntent.mode === "soft" ? t("deleteConfirmTitle") : t("hardDeleteConfirmTitle")}
          message={
            deleteIntent.mode === "soft"
              ? t("deleteConfirmMessage", { name: deleteIntent.faction.name })
              : t("hardDeleteConfirmMessage", { name: deleteIntent.faction.name })
          }
          confirmText={deleteIntent.mode === "soft" ? t("delete") : t("hardDelete")}
          danger={deleteIntent.mode === "hard"}
          error={error}
          isLoading={actionId === `${deleteIntent.mode}:${deleteIntent.faction.faction_id}`}
          onCancel={() => setDeleteIntent(null)}
          onConfirm={handleConfirmDelete}
          cancelText={t("cancel")}
          loadingText={t("processing")}
        />
      )}

      {relationDeleteIntent && (
        <ConfirmActionModal
          title={relationDeleteIntent.mode === "soft" ? t("relationConfirm.deleteTitle") : t("relationConfirm.hardDeleteTitle")}
          message={relationDeleteIntent.mode === "soft" ? t("relationConfirm.deleteMessage") : t("relationConfirm.hardDeleteMessage")}
          confirmText={relationDeleteIntent.mode === "soft" ? t("delete") : t("hardDelete")}
          danger
          error={relationEditorError}
          isLoading={actionId === `relation-${relationDeleteIntent.mode}:${relationDeleteIntent.relation.relation_id}`}
          onCancel={() => setRelationDeleteIntent(null)}
          onConfirm={() => void handleConfirmRelationDelete()}
          cancelText={t("cancel")}
          loadingText={t("processing")}
        />
      )}
    </div>
  );
}

interface FactionPreviewEditorProps {
  preview: CoreFactionsPayload;
  relationFactionOptions: string[];
  relationTypeLabels: Record<FactionRelationType, string>;
  onFactionChange: (index: number, patch: Partial<CoreFaction>) => void;
  onRelationChange: (index: number, patch: Partial<GeneratedFactionRelation>) => void;
  t: ReturnType<typeof useTranslations>;
}

/**
 * 功能：创建人工阵营关系表单草稿，或从正式响应提取可复制字段。
 * Args:
 *   factions: 当前可选的活动核心势力。
 *   source: 可选的已落库关系。
 * Returns:
 *   只包含关系创建契约字段的草稿。
 */
export function createFactionRelationDraft(
  factions: CoreFaction[],
  source?: FactionRelation,
): FactionRelationCreateRequestV1 {
  const requestedSourceId = source?.source_faction_id;
  const sourceFactionId = factions.some((item) => item.faction_id === requestedSourceId)
    ? requestedSourceId ?? ""
    : factions[0]?.faction_id ?? "";
  const requestedTargetId = source?.target_faction_id;
  const targetFactionId = factions.some((item) => item.faction_id === requestedTargetId && item.faction_id !== sourceFactionId)
    ? requestedTargetId ?? ""
    : factions.find((item) => item.faction_id !== sourceFactionId)?.faction_id ?? "";
  return {
    source_faction_id: sourceFactionId,
    target_faction_id: targetFactionId,
    relation_type: source?.relation_type ?? "allied",
    current_state: source?.current_state ?? "",
    core_conflict: source?.core_conflict ?? "",
    hidden_tension: source?.hidden_tension ?? "",
    possible_change: source?.possible_change ?? "",
    intensity: source?.intensity ?? 3,
    is_active: source?.user_is_active ?? source?.is_active ?? true,
  };
}

/**
 * 功能：校验人工阵营关系是否满足提交前的最低约束。
 * Args:
 *   draft: 当前关系草稿。
 * Returns:
 *   端点不同、必填叙述完整且强度位于 1 至 5 时返回 true；隐藏张力允许为空。
 */
function isCompleteFactionRelation(draft: FactionRelationCreateRequestV1): boolean {
  return Boolean(
    draft.source_faction_id &&
    draft.target_faction_id &&
    draft.source_faction_id !== draft.target_faction_id &&
    draft.current_state.trim() &&
    draft.core_conflict.trim() &&
    draft.possible_change.trim() &&
    Number.isInteger(draft.intensity) &&
    draft.intensity >= 1 &&
    draft.intensity <= 5
  );
}

/**
 * 渲染 AI 势力生成结果的单项校对工作区。
 *
 * Args:
 *   props: 势力与关系预览、可选关系端点、字段更新回调及翻译函数。
 *
 * Returns:
 *   可在势力和关系标签间切换编辑的 React 节点。
 */
function FactionPreviewEditor({
  preview,
  relationFactionOptions,
  relationTypeLabels,
  onFactionChange,
  onRelationChange,
  t,
}: FactionPreviewEditorProps) {
  const [activeFactionIndex, setActiveFactionIndex] = useState(0);
  const [activeRelationIndex, setActiveRelationIndex] = useState(0);
  const activeFaction = preview.core_factions[activeFactionIndex] ?? preview.core_factions[0];
  const activeRelation = preview.faction_relations[activeRelationIndex] ?? preview.faction_relations[0];

  return (
    <div className="space-y-10">
      <section>
        <SectionHeading
          index="01"
          title={t("previewSections.factions")}
          description={t("previewSections.factionsHint")}
        />
        <div className="flex gap-5 overflow-x-auto border-b border-border" role="tablist" aria-label={t("factionSelectorLabel")}>
          {preview.core_factions.map((faction, index) => (
            <button
              key={index}
              type="button"
              role="tab"
              aria-selected={activeFactionIndex === index}
              onClick={() => setActiveFactionIndex(index)}
              className={`min-w-fit border-b-2 pb-3 text-sm font-medium transition-colors ${
                activeFactionIndex === index
                  ? "border-accent text-accent"
                  : "border-transparent text-muted hover:text-foreground"
              }`}
            >
              {faction.name.trim() || t("untitledFaction", { index: index + 1 })}
            </button>
          ))}
        </div>
        {activeFaction && (
          <div className="pt-7" role="tabpanel">
            <FactionEditor
              value={activeFaction}
              onChange={(patch) => onFactionChange(activeFactionIndex, patch)}
              t={t}
            />
          </div>
        )}
      </section>

      <section className="border-t border-border pt-8">
        <SectionHeading
          index="02"
          title={t("previewSections.relations")}
          description={t("previewSections.relationsHint")}
        />
        {preview.faction_relations.length === 0 ? (
          <p className="border-l-2 border-accent/30 pl-4 text-sm leading-6 text-muted">{t("emptyPreviewRelations")}</p>
        ) : (
          <>
            <div className="flex gap-5 overflow-x-auto border-b border-border" role="tablist" aria-label={t("relationSelectorLabel")}>
              {preview.faction_relations.map((relation, index) => (
                <button
                  key={index}
                  type="button"
                  role="tab"
                  aria-selected={activeRelationIndex === index}
                  onClick={() => setActiveRelationIndex(index)}
                  className={`min-w-fit border-b-2 pb-3 text-sm font-medium transition-colors ${
                    activeRelationIndex === index
                      ? "border-accent text-accent"
                      : "border-transparent text-muted hover:text-foreground"
                  }`}
                >
                  {relation.source_faction_name} → {relation.target_faction_name}
                </button>
              ))}
            </div>

            {activeRelation && (
              <div className="space-y-6 pt-7" role="tabpanel">
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(170px,0.7fr)_minmax(0,1fr)]">
                  <SelectInput
                    label={t("fields.sourceFaction")}
                    value={activeRelation.source_faction_name}
                    options={relationFactionOptions}
                    onChange={(value) => onRelationChange(activeRelationIndex, { source_faction_name: value })}
                  />
                  <SelectInput
                    label={t("fields.relationType")}
                    value={activeRelation.relation_type}
                    options={RELATION_TYPES}
                    optionLabel={(option) => relationTypeLabels[option as FactionRelationType]}
                    onChange={(value) => onRelationChange(activeRelationIndex, { relation_type: value as FactionRelationType })}
                  />
                  <SelectInput
                    label={t("fields.targetFaction")}
                    value={activeRelation.target_faction_name}
                    options={relationFactionOptions}
                    onChange={(value) => onRelationChange(activeRelationIndex, { target_faction_name: value })}
                  />
                </div>
                <TextArea
                  label={t("fields.currentState")}
                  value={activeRelation.current_state}
                  rows={3}
                  placeholder={t("placeholders.currentState")}
                  onChange={(value) => onRelationChange(activeRelationIndex, { current_state: value })}
                />
                <TextArea
                  label={t("fields.coreConflict")}
                  value={activeRelation.core_conflict}
                  rows={4}
                  placeholder={t("placeholders.coreConflict")}
                  onChange={(value) => onRelationChange(activeRelationIndex, { core_conflict: value })}
                />
                <div className="grid gap-5 lg:grid-cols-2">
                  <TextArea
                    label={t("fields.hiddenTension")}
                    value={activeRelation.hidden_tension ?? ""}
                    rows={3}
                    placeholder={t("placeholders.hiddenTension")}
                    onChange={(value) => onRelationChange(activeRelationIndex, { hidden_tension: value })}
                  />
                  <TextArea
                    label={t("fields.possibleChange")}
                    value={activeRelation.possible_change}
                    rows={3}
                    placeholder={t("placeholders.possibleChange")}
                    onChange={(value) => onRelationChange(activeRelationIndex, { possible_change: value })}
                  />
                </div>
                <label className="grid gap-3 text-sm">
                  <span className="text-xs font-semibold tracking-wide text-muted">
                    {t("fields.intensity")}: <strong className="text-foreground">{activeRelation.intensity}/5</strong>
                  </span>
                  <input
                    type="range"
                    min={1}
                    max={5}
                    value={activeRelation.intensity}
                    onChange={(event) => onRelationChange(activeRelationIndex, { intensity: Number(event.target.value) })}
                    className="w-full accent-[var(--color-accent)]"
                  />
                  <span className="flex justify-between text-xs text-muted">
                    <span>{t("intensityLow")}</span>
                    <span>{t("intensityHigh")}</span>
                  </span>
                </label>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

/**
 * 按身份、战略、组织和剧情作用渲染势力档案编辑器。
 *
 * Args:
 *   props: 当前势力数据、字段更新回调、翻译函数、别名可见性及禁用状态。
 *
 * Returns:
 *   使用语义化控件编辑势力字段的 React 节点。
 */
function FactionEditor({
  value,
  onChange,
  t,
  showAliases = false,
  disabled = false,
}: {
  value: CoreFaction;
  onChange: (patch: Partial<CoreFaction>) => void;
  t: ReturnType<typeof useTranslations>;
  showAliases?: boolean;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-9">
      <section>
        <SectionHeading index="A" title={t("sections.identity")} description={t("sections.identityHint")} />
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(180px,0.8fr)_minmax(180px,0.8fr)]">
          <TextInput
            label={t("fields.name")}
            value={value.name}
            required
            placeholder={t("placeholders.name")}
            disabled={disabled}
            onChange={(name) => onChange({ name })}
          />
          <TextInput
            label={t("fields.factionType")}
            value={value.faction_type}
            placeholder={t("placeholders.factionType")}
            disabled={disabled}
            onChange={(faction_type) => onChange({ faction_type })}
          />
          <TextInput
            label={t("fields.influenceScope")}
            value={value.influence_scope}
            placeholder={t("placeholders.influenceScope")}
            disabled={disabled}
            onChange={(influence_scope) => onChange({ influence_scope })}
          />
        </div>
        {showAliases && (
          <TagInput
            className="mt-5"
            label={t("fields.aliases")}
            values={value.alias ?? []}
            onChange={(alias) => onChange({ alias })}
            getRemoveAriaLabel={(tag) => t("removeTag", { tag })}
            placeholder={t("placeholders.aliases")}
            description={t("hints.aliases")}
            limitReachedText={t("tagLimitReached")}
            disabled={disabled}
          />
        )}
        <div className="mt-5 border-l-2 border-accent/30 pl-4">
          <PublicStatusSwitch
            checked={value.is_public}
            disabled={disabled}
            label={t("fields.isPublic")}
            description={value.is_public ? t("publicStatus.publicHint") : t("publicStatus.hiddenHint")}
            onChange={(is_public) => onChange({ is_public })}
          />
        </div>
      </section>

      <section className="border-t border-border pt-8">
        <SectionHeading index="B" title={t("sections.strategy")} description={t("sections.strategyHint")} />
        <div className="grid gap-5 lg:grid-cols-2">
          <TextArea
            className="lg:col-span-2"
            label={t("fields.positioning")}
            value={value.positioning}
            rows={4}
            placeholder={t("placeholders.positioning")}
            disabled={disabled}
            onChange={(positioning) => onChange({ positioning })}
          />
          <TextArea
            label={t("fields.publicStance")}
            value={value.public_stance}
            rows={3}
            placeholder={t("placeholders.publicStance")}
            disabled={disabled}
            onChange={(public_stance) => onChange({ public_stance })}
          />
          <TextArea
            label={t("fields.coreGoal")}
            value={value.core_goal}
            rows={4}
            placeholder={t("placeholders.coreGoal")}
            disabled={disabled}
            onChange={(core_goal) => onChange({ core_goal })}
          />
          <TextArea
            className="lg:col-span-2"
            label={t("fields.hiddenGoal")}
            value={value.hidden_goal ?? ""}
            rows={3}
            placeholder={t("placeholders.hiddenGoal")}
            disabled={disabled}
            onChange={(hidden_goal) => onChange({ hidden_goal })}
          />
        </div>
      </section>

      <section className="border-t border-border pt-8">
        <SectionHeading index="C" title={t("sections.structure")} description={t("sections.structureHint")} />
        <TextArea
          label={t("fields.organizationStyle")}
          value={value.organization_style}
          rows={3}
          placeholder={t("placeholders.organizationStyle")}
          disabled={disabled}
          onChange={(organization_style) => onChange({ organization_style })}
        />
        <div className="mt-5 grid gap-5 lg:grid-cols-2">
          <TagInput
            label={t("fields.resources")}
            values={value.resources_and_advantages ?? []}
            onChange={(resources_and_advantages) => onChange({ resources_and_advantages })}
            getRemoveAriaLabel={(tag) => t("removeTag", { tag })}
            placeholder={t("placeholders.resources")}
            description={t("hints.resources")}
            limitReachedText={t("tagLimitReached")}
            maxItems={6}
            disabled={disabled}
          />
          <TagInput
            label={t("fields.values")}
            values={value.core_values ?? []}
            onChange={(core_values) => onChange({ core_values })}
            getRemoveAriaLabel={(tag) => t("removeTag", { tag })}
            placeholder={t("placeholders.values")}
            description={t("hints.values")}
            limitReachedText={t("tagLimitReached")}
            maxItems={6}
            disabled={disabled}
          />
        </div>
      </section>

      <section className="border-t border-border pt-8">
        <SectionHeading index="D" title={t("sections.story")} description={t("sections.storyHint")} />
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(280px,0.8fr)]">
          <TextArea
            label={t("fields.conflict")}
            value={value.conflict_with_mainline}
            rows={5}
            placeholder={t("placeholders.conflict")}
            disabled={disabled}
            onChange={(conflict_with_mainline) => onChange({ conflict_with_mainline })}
          />
          <TextArea
            label={t("fields.expandability")}
            value={value.expandability}
            rows={4}
            placeholder={t("placeholders.expandability")}
            disabled={disabled}
            onChange={(expandability) => onChange({ expandability })}
          />
        </div>
        <TagInput
          className="mt-5"
          label={t("fields.tags")}
          values={value.tags ?? []}
          onChange={(tags) => onChange({ tags })}
          getRemoveAriaLabel={(tag) => t("removeTag", { tag })}
          placeholder={t("placeholders.tags")}
          description={t("hints.tags")}
          limitReachedText={t("tagLimitReached")}
          maxItems={8}
          disabled={disabled}
        />
      </section>
    </div>
  );
}

/**
 * 渲染势力编辑区的语义分段标题。
 *
 * Args:
 *   index: 分段序号或字母标识。
 *   title: 分段标题。
 *   description: 分段用途说明。
 *
 * Returns:
 *   带序号、标题与说明的 React 节点。
 */
function SectionHeading({ index, title, description }: { index: string; title: string; description: string }) {
  return (
    <div className="mb-5 flex items-start gap-3">
      <span className="mt-0.5 font-mono text-xs font-semibold text-accent">{index}</span>
      <div>
        <h4 className="text-sm font-semibold text-foreground">{title}</h4>
        <p className="mt-1 text-xs leading-5 text-muted">{description}</p>
      </div>
    </div>
  );
}

/**
 * 渲染具有明确状态说明的公开性开关。
 *
 * Args:
 *   checked: 当前是否公开存在。
 *   disabled: 是否禁止切换。
 *   label: 开关标题。
 *   description: 当前状态说明。
 *   onChange: 状态变更回调。
 *
 * Returns:
 *   可访问的 switch 控件 React 节点。
 */
function PublicStatusSwitch({
  checked,
  disabled,
  label,
  description,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  label: string;
  description: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <p className="text-sm font-medium text-foreground">{label}</p>
        <p className="mt-1 text-xs leading-5 text-muted">{description}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        disabled={disabled}
        className={`relative h-7 w-12 shrink-0 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 ${
          checked ? "bg-accent" : "bg-surface-secondary"
        }`}
      >
        <span
          className={`absolute left-1 top-1 h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
            checked ? "translate-x-5" : "translate-x-0"
          }`}
        />
      </button>
    </div>
  );
}

/**
 * 渲染手动创建或编辑核心势力的模态工作区。
 *
 * Args:
 *   props: 编辑状态、保存状态、错误信息、草稿更新与保存取消回调及翻译函数。
 *
 * Returns:
 *   带固定标题和操作区的势力编辑对话框 React 节点。
 */
function EditorModal({
  editor,
  saving,
  error,
  onChange,
  onCancel,
  onSave,
  t,
}: {
  editor: NonNullable<EditorState>;
  saving: boolean;
  error: string;
  onChange: (draft: CoreFaction) => void;
  onCancel: () => void;
  onSave: () => void;
  t: ReturnType<typeof useTranslations>;
}) {
  return (
      <Modal.Backdrop isOpen onOpenChange={(isOpen) => !isOpen && !saving && onCancel()} variant="blur" isDismissable={!saving}>
        <Modal.Container size="full" scroll="inside" className="p-0 sm:p-4">
          <Modal.Dialog className="h-[100dvh] w-full sm:h-auto sm:max-h-[90vh] sm:max-w-5xl">
            <Modal.Header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4 sm:px-7">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">{t("editor.eyebrow")}</p>
            <Modal.Heading className="mt-1 text-lg font-semibold text-foreground">
              {editor.mode === "create" ? t("editor.createTitle") : t("editor.editTitle")}
            </Modal.Heading>
            <p className="mt-1 text-sm leading-6 text-muted">{t("editor.description")}</p>
          </div>
              <Modal.CloseTrigger aria-label={t("cancel")} isDisabled={saving} />
            </Modal.Header>
        {error && (
          <div className="mx-5 mt-4 border-l-4 border-red-500 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300 sm:mx-7" role="alert">
            {error}
          </div>
        )}
        <Modal.Body className="px-5 py-6 sm:px-7 sm:py-8">
          <FactionEditor
            value={editor.draft}
            onChange={(patch) => onChange({ ...editor.draft, ...patch })}
            t={t}
            showAliases
            disabled={saving}
          />
        </Modal.Body>
        <Modal.Footer className="sticky bottom-0 flex justify-end gap-2 border-t border-border bg-surface px-5 py-4 sm:px-7">
          <Button variant="ghost" size="sm" onPress={onCancel} isDisabled={saving}>
            {t("cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            className="bg-accent text-white hover:bg-accent-hover"
            onPress={onSave}
            isDisabled={saving}
          >
            {saving ? t("saving") : t("save")}
          </Button>
        </Modal.Footer>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
  );
}

/**
 * 渲染核心势力 AI 生成策略与高级参数对话框。
 *
 * Args:
 *   props: 生成策略、请求参数、已有势力、错误与加载状态，以及各项更新和提交回调。
 *
 * Returns:
 *   可配置生成规模、独立性和已有关系目标的 React 节点。
 */
function GenerateCoreFactionsModal({
  options,
  genParams,
  showGenParams,
  generating,
  error,
  existingFactions,
  onOptionsChange,
  onGenParamsChange,
  onShowGenParamsChange,
  onCancel,
  onGenerate,
  t,
}: {
  options: FactionGenerationOptions;
  genParams: GenerationParamsValue;
  showGenParams: boolean;
  generating: boolean;
  error: string;
  existingFactions: CoreFaction[];
  onOptionsChange: (patch: Partial<FactionGenerationOptions>) => void;
  onGenParamsChange: (value: GenerationParamsValue) => void;
  onShowGenParamsChange: (isOpen: boolean) => void;
  onCancel: () => void;
  onGenerate: () => void;
  t: ReturnType<typeof useTranslations>;
}) {
  const canSetExistingRelation = canConfigureExistingRelation(options, existingFactions.length > 0);
  const [targetDraftName, setTargetDraftName] = useState("");
  const existingFactionNames = useMemo(() => {
    const names = new Set<string>();
    for (const faction of existingFactions) {
      const name = getFactionName(faction);
      if (name) {
        names.add(name);
      }
    }
    return Array.from(names);
  }, [existingFactions]);
  const selectedTargetNames = useMemo(
    () => new Set(options.existingRelationTargets.map((target) => target.factionName.trim()).filter(Boolean)),
    [options.existingRelationTargets],
  );
  const availableRelationTargets = useMemo(
    () => existingFactionNames.filter((name) => !selectedTargetNames.has(name)),
    [existingFactionNames, selectedTargetNames],
  );
  const selectedRelationDraftName = availableRelationTargets.includes(targetDraftName)
    ? targetDraftName
    : availableRelationTargets[0] ?? "";

  const handleAddExistingTarget = () => {
    const factionName = selectedRelationDraftName.trim();
    if (!factionName || selectedTargetNames.has(factionName)) {
      return;
    }
    onOptionsChange({
      existingRelationTargets: [
        ...options.existingRelationTargets,
        { factionName, relationScore: 5 },
      ],
    });
  };

  const handleUpdateExistingTarget = (index: number, patch: Partial<ExistingRelationTarget>) => {
    onOptionsChange({
      existingRelationTargets: options.existingRelationTargets.map((target, targetIndex) =>
        targetIndex === index ? { ...target, ...patch } : target,
      ),
    });
  };

  const handleRemoveExistingTarget = (index: number) => {
    onOptionsChange({
      existingRelationTargets: options.existingRelationTargets.filter((_, targetIndex) => targetIndex !== index),
    });
  };

  return (
      <Modal.Backdrop isOpen onOpenChange={(isOpen) => !isOpen && !generating && onCancel()} variant="blur" isDismissable={!generating}>
        <Modal.Container size="lg" scroll="inside" className="px-3 sm:px-6">
          {/* 生成面板保持紧凑尺寸，避免在窄屏/小窗口下铺满整屏。 */}
          <Modal.Dialog className="w-full max-w-2xl">
            <Modal.Header className="flex items-start justify-between gap-3 border-b border-border px-5 py-3.5 sm:px-6">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">{t("generateDialog.eyebrow")}</p>
            <Modal.Heading className="mt-1 text-lg font-semibold text-foreground">{t("generateDialog.title")}</Modal.Heading>
            <p className="mt-1 text-sm leading-6 text-muted">{t("generateDialog.description")}</p>
          </div>
              <Modal.CloseTrigger aria-label={t("cancel")} isDisabled={generating} />
            </Modal.Header>

        {error && (
          <div className="mx-5 mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300 sm:mx-6" role="alert">
            {error}
          </div>
        )}

        <Modal.Body className="space-y-4 px-5 py-5 sm:px-6">
          <div className="grid gap-4 md:grid-cols-[minmax(180px,0.7fr)_minmax(0,1.3fr)]">
            <label className="grid gap-2 text-sm">
              <span className="text-xs font-semibold tracking-wide text-muted">{t("generateDialog.factionCount")}</span>
              <input
                type="number"
                min={1}
                max={6}
                value={options.factionCount}
                onChange={(event) => onOptionsChange({ factionCount: Number(event.target.value) || 1 })}
                className="min-h-11 rounded-xl border border-border bg-background/80 px-3.5 py-2.5 text-sm text-foreground outline-none transition-[border-color,box-shadow] focus:border-accent focus:ring-2 focus:ring-accent/15"
                disabled={generating}
              />
            </label>

            <div className="grid gap-2 text-sm">
              <div className="flex min-h-11 items-center gap-2 text-foreground">
                <input
                  id="independent-factions-toggle"
                  type="checkbox"
                  checked={options.independentEnabled}
                  onChange={(event) => onOptionsChange({ independentEnabled: event.target.checked })}
                  className="h-4 w-4 accent-[var(--color-accent)]"
                  disabled={generating}
                />
                <label htmlFor="independent-factions-toggle">{t("generateDialog.independentToggle")}</label>
                <TooltipHint text={t("generateDialog.tooltips.independent")} />
              </div>
              {options.independentEnabled && (
                <input
                  type="number"
                  min={1}
                  max={options.factionCount}
                  value={options.independentFactionCount}
                  onChange={(event) => onOptionsChange({ independentFactionCount: Number(event.target.value) || 1 })}
                  className="min-h-11 rounded-xl border border-border bg-background/80 px-3.5 py-2.5 text-sm text-foreground outline-none transition-[border-color,box-shadow] focus:border-accent focus:ring-2 focus:ring-accent/15"
                  aria-label={t("generateDialog.independentCount")}
                  disabled={generating}
                />
              )}
            </div>
          </div>

          <TextArea
            label={t("generateDialog.userGuidance")}
            value={options.userGuidance}
            rows={3}
            placeholder={t("placeholders.userGuidance")}
            disabled={generating}
            onChange={(userGuidance) => onOptionsChange({ userGuidance })}
          />

          {options.factionCount === 1 && (
            <div className="grid gap-4 border-t border-border pt-4">
              <div className="grid gap-2 text-sm">
                <span className="inline-flex items-center gap-1 text-xs font-medium text-muted">
                  {t("generateDialog.integrityScore", { score: options.singleFactionIntegrityScore })}
                  <TooltipHint text={t("generateDialog.tooltips.integrity")} />
                </span>
                <input
                  type="range"
                  min={0}
                  max={10}
                  value={options.singleFactionIntegrityScore}
                  onChange={(event) => onOptionsChange({ singleFactionIntegrityScore: Number(event.target.value) })}
                  className="w-full accent-[var(--color-accent)]"
                  aria-label={t("generateDialog.integrityScore", { score: options.singleFactionIntegrityScore })}
                  disabled={generating}
                />
              </div>

              {canSetExistingRelation && (
                <div className="grid gap-3 border-l-2 border-accent/25 pl-4">
                  <div className="flex items-center gap-2 text-sm text-foreground">
                    <input
                      id="connect-existing-factions-toggle"
                      type="checkbox"
                      checked={options.connectToExisting}
                      onChange={(event) =>
                        onOptionsChange({
                          connectToExisting: event.target.checked,
                          existingRelationTargets: event.target.checked ? options.existingRelationTargets : [],
                        })
                      }
                      className="h-4 w-4 accent-[var(--color-accent)]"
                      disabled={generating}
                    />
                    <label htmlFor="connect-existing-factions-toggle">{t("generateDialog.connectExisting")}</label>
                  </div>
                  {options.connectToExisting && (
                    <div className="grid gap-3">
                      <div className="flex gap-2">
                        <select
                          value={selectedRelationDraftName}
                          onChange={(event) => setTargetDraftName(event.target.value)}
                          className="min-h-11 min-w-0 flex-1 rounded-xl border border-border bg-background/80 px-3.5 py-2.5 text-sm text-foreground outline-none transition-[border-color,box-shadow] focus:border-accent focus:ring-2 focus:ring-accent/15"
                          disabled={generating || availableRelationTargets.length === 0}
                          aria-label={t("generateDialog.existingRelationTarget")}
                        >
                          {availableRelationTargets.map((name) => (
                            <option key={name} value={name}>
                              {name}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={handleAddExistingTarget}
                          className="min-h-11 rounded-xl border border-border px-3.5 text-sm font-medium text-foreground transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
                          disabled={generating || !selectedRelationDraftName}
                        >
                          {t("generateDialog.addExistingRelationTarget")}
                        </button>
                      </div>

                      {options.existingRelationTargets.length === 0 ? (
                        <p className="text-xs leading-5 text-muted">{t("generateDialog.existingRelationTargetsEmpty")}</p>
                      ) : (
                        <div className="divide-y divide-dashed divide-border">
                          {options.existingRelationTargets.map((target, index) => (
                            <div key={`${target.factionName}-${index}`} className="py-3 first:pt-1 last:pb-0">
                              <div className="mb-2 flex items-center justify-between gap-2">
                                <span className="min-w-0 truncate text-sm font-medium text-foreground">{target.factionName}</span>
                                <button
                                  type="button"
                                  onClick={() => handleRemoveExistingTarget(index)}
                                  className="shrink-0 text-xs text-muted transition-colors hover:text-danger"
                                  disabled={generating}
                                >
                                  {t("generateDialog.removeExistingRelationTarget")}
                                </button>
                              </div>
                              <div className="grid gap-2 text-sm">
                                <span className="inline-flex items-center gap-1 text-xs font-medium text-muted">
                                  {t("generateDialog.existingRelationScore", {
                                    name: target.factionName,
                                    score: target.relationScore,
                                  })}
                                  <TooltipHint text={t("generateDialog.tooltips.existingRelation")} />
                                </span>
                                <input
                                  type="range"
                                  min={0}
                                  max={10}
                                  value={target.relationScore}
                                  onChange={(event) => handleUpdateExistingTarget(index, { relationScore: Number(event.target.value) })}
                                  className="w-full accent-[var(--color-accent)]"
                                  aria-label={t("generateDialog.existingRelationScore", {
                                    name: target.factionName,
                                    score: target.relationScore,
                                  })}
                                  disabled={generating}
                                />
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          <GenerationParamsCollapse
            value={genParams}
            onChange={onGenParamsChange}
            isOpen={showGenParams}
            onOpenChange={onShowGenParamsChange}
            disabled={generating}
            panelClassName="rounded-none border-x-0 border-b-0 bg-transparent px-0"
          />
        </Modal.Body>

        <Modal.Footer className="flex justify-end gap-2 border-t border-border px-5 py-3.5 sm:px-6">
          <Button variant="ghost" size="sm" onPress={onCancel} isDisabled={generating}>
            {t("cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            className="bg-accent text-white hover:bg-accent-hover"
            onPress={onGenerate}
            isDisabled={generating}
          >
            {generating ? t("generating") : t("generateDialog.submit")}
          </Button>
        </Modal.Footer>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
  );
}

/**
 * 功能：渲染势力名称名册与单一势力详情组成的主从工作台。
 *
 * Args:
 *   props: 已筛选势力、搜索与类别状态、加载状态及档案操作回调。
 *
 * Returns:
 *   名册和详情可独立滚动的响应式势力档案工作台。
 */
function FactionProfilesWorkspace({
  factions,
  allFactionCount,
  search,
  levelFilter,
  loading,
  generationLocked,
  actionId,
  onSearchChange,
  onLevelFilterChange,
  onManualCreate,
  onGenerate,
  onEdit,
  onDelete,
  t,
}: {
  factions: CoreFaction[];
  allFactionCount: number;
  search: string;
  levelFilter: FactionLevelFilter;
  loading: boolean;
  generationLocked: boolean;
  actionId: string;
  onSearchChange: (value: string) => void;
  onLevelFilterChange: (value: FactionLevelFilter) => void;
  onManualCreate: () => void;
  onGenerate: () => void;
  onEdit: (faction: CoreFaction) => void;
  onDelete: (faction: CoreFaction) => void;
  t: ReturnType<typeof useTranslations>;
}) {
  const [selectedKey, setSelectedKey] = useState("");
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [rosterCollapsed, setRosterCollapsed] = useState(false);
  const selected = factions.find((item) => getFactionRenderKey(item) === selectedKey) ?? factions[0];
  const filtersActive = Boolean(search) || levelFilter !== "all";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-5">
      <section className={`shrink-0 border-b border-border pb-5 ${mobileDetailOpen ? "hidden sm:block" : ""}`}>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="grid flex-1 gap-3 md:grid-cols-[minmax(220px,1fr)_190px]">
            <label className="grid gap-1.5 text-xs font-semibold tracking-wide text-muted">
              <span>{t("filters.searchLabel")}</span>
              <input
                type="search"
                value={search}
                onChange={(event) => onSearchChange(event.target.value)}
                placeholder={t("filters.searchPlaceholder")}
                className={FACTION_FILTER_CLASS}
              />
            </label>
            <label className="grid gap-1.5 text-xs font-semibold tracking-wide text-muted">
              <span>{t("filters.levelType")}</span>
              <select value={levelFilter} onChange={(event) => onLevelFilterChange(event.target.value as FactionLevelFilter)} className={FACTION_FILTER_CLASS}>
                <option value="all">{t("categories.all")}</option>
                {(["core", "volume", "local"] as const).map((level) => (
                  <option key={level} value={level}>{t(`categories.${level}`)}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" className="hidden sm:inline-flex lg:hidden" onPress={() => setRosterCollapsed((value) => !value)}>
              {rosterCollapsed ? t("profileActions.showList") : t("profileActions.hideList")}
            </Button>
            {filtersActive && (
              <Button variant="ghost" size="sm" onPress={() => { onSearchChange(""); onLevelFilterChange("all"); }}>
                {t("filters.clear")}
              </Button>
            )}
            <Button variant="outline" size="sm" onPress={onManualCreate} isDisabled={loading}>{t("manualCreate")}</Button>
            <Button variant="primary" size="sm" onPress={onGenerate} isDisabled={loading || generationLocked}>
              {generationLocked ? t("generating") : t("generate")}
            </Button>
          </div>
        </div>
      </section>

      {loading && allFactionCount === 0 ? (
        <div className="border-y border-border px-5 py-14 text-center" role="status">{t("loading")}</div>
      ) : !selected ? (
        <div className="border-y border-dashed border-border px-5 py-14 text-center text-sm text-muted">
          {filtersActive ? t("filters.noResults") : t("emptyFactions")}
        </div>
      ) : (
        <div
          className={`grid min-h-[420px] flex-1 overflow-hidden border-y border-border bg-surface/35 sm:min-h-0 ${
            rosterCollapsed
              ? "sm:grid-cols-1 lg:grid-cols-[minmax(240px,0.34fr)_minmax(0,1fr)]"
              : "sm:grid-cols-[minmax(180px,0.38fr)_minmax(0,1fr)] lg:grid-cols-[minmax(240px,0.34fr)_minmax(0,1fr)]"
          }`}
        >
          <aside
            className={`workspace-scrollbar max-h-[55vh] overflow-y-auto border-b border-border outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/30 sm:min-h-0 sm:max-h-none sm:border-b-0 sm:border-r ${mobileDetailOpen ? "hidden sm:block" : "block"} ${rosterCollapsed ? "sm:hidden lg:block" : ""}`}
            aria-label={t("tabs.profiles")}
            tabIndex={0}
          >
            <ul className="divide-y divide-border">
              {factions.map((faction) => {
                const key = getFactionRenderKey(faction);
                return (
                  <li key={key}>
                    <button
                      type="button"
                      onClick={() => { setSelectedKey(key); setMobileDetailOpen(true); }}
                      className={`w-full px-4 py-4 text-left transition-colors ${getFactionRenderKey(selected) === key ? "bg-accent/10" : "hover:bg-surface-secondary/55"}`}
                    >
                      <span className="flex items-center justify-between gap-3">
                        <span className="truncate text-sm font-semibold text-foreground">{faction.name}</span>
                        <span className={`size-2 shrink-0 rounded-full ${(faction.active_status ?? "active") === "active" ? "bg-emerald-500" : "bg-muted/45"}`} aria-hidden="true" />
                      </span>
                      <span className="mt-1 block truncate text-xs text-muted">{getFactionLevelLabel(faction, t)} · {faction.faction_type || t("card.typeEmpty")}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </aside>
          <div
            className={`workspace-scrollbar min-w-0 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/30 sm:min-h-0 sm:overflow-y-auto ${mobileDetailOpen ? "block" : "hidden sm:block"}`}
            tabIndex={0}
          >
            <button type="button" className="mx-5 mt-4 text-sm font-medium text-accent sm:hidden" onClick={() => setMobileDetailOpen(false)}>
              ← {t("profileActions.back")}
            </button>
            <FactionDetail
              faction={selected}
              busy={Boolean(selected.faction_id && actionId.includes(selected.faction_id))}
              onEdit={() => onEdit(selected)}
              onDelete={() => onDelete(selected)}
              t={t}
            />
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * 功能：渲染按身份、战略、组织和剧情作用分区的势力详情。
 *
 * Args:
 *   props: 当前势力、动作状态以及编辑和删除回调。
 *
 * Returns:
 *   不使用卡片嵌套、以章节分隔组织的完整势力档案。
 */
function FactionDetail({ faction, busy, onEdit, onDelete, t }: {
  faction: CoreFaction;
  busy: boolean;
  onEdit: () => void;
  onDelete: () => void;
  t: ReturnType<typeof useTranslations>;
}) {
  const sections: Array<{ title: string; values: Array<[string, string]> }> = [
    {
      title: t("sections.identity"),
      values: [
        [t("fields.factionType"), faction.faction_type],
        [t("fields.levelType"), getFactionLevelLabel(faction, t)],
        [t("fields.influenceScope"), faction.influence_scope],
        [t("fields.isPublic"), faction.is_public ? t("publicStatus.public") : t("publicStatus.hidden")],
      ],
    },
    {
      title: t("sections.strategy"),
      values: [
        [t("fields.positioning"), faction.positioning],
        [t("fields.publicStance"), faction.public_stance],
        [t("fields.coreGoal"), faction.core_goal],
        [t("fields.hiddenGoal"), faction.hidden_goal ?? ""],
      ],
    },
    {
      title: t("sections.structure"),
      values: [
        [t("fields.organizationStyle"), faction.organization_style],
        [t("fields.resources"), (faction.resources_and_advantages ?? []).join(" · ")],
        [t("fields.values"), (faction.core_values ?? []).join(" · ")],
      ],
    },
    {
      title: t("sections.story"),
      values: [
        [t("fields.conflict"), faction.conflict_with_mainline],
        [t("fields.expandability"), faction.expandability],
      ],
    },
  ];

  return (
    <article className="min-w-0 px-5 py-6 sm:px-7">
      <header className="flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap gap-2">
            <FactionStatusPill active>{getFactionLevelLabel(faction, t)}</FactionStatusPill>
            <FactionStatusPill active={(faction.active_status ?? "active") === "active"}>
              {(faction.active_status ?? "active") === "active" ? t("factionStatus.active") : t("factionStatus.inactive")}
            </FactionStatusPill>
          </div>
          <h3 className="mt-3 text-2xl font-semibold text-foreground">{faction.name}</h3>
          <p className="mt-1 text-sm text-muted">{(faction.alias ?? []).join(" · ") || faction.faction_id}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onPress={onEdit} isDisabled={busy}>{t("edit")}</Button>
          <Button size="sm" variant="ghost" className="text-danger" onPress={onDelete} isDisabled={busy}>{t("delete")}</Button>
        </div>
      </header>
      {sections.map((section) => (
        <section key={section.title} className="border-b border-border py-6 last:border-b-0">
          <h4 className="text-sm font-semibold uppercase tracking-[0.12em] text-accent">{section.title}</h4>
          <dl className="mt-4 grid gap-x-8 gap-y-5 md:grid-cols-2">
            {section.values.map(([label, value]) => <FactionSummaryField key={label} label={label} value={value} />)}
          </dl>
        </section>
      ))}
      {(faction.tags ?? []).length > 0 && (
        <section className="pt-5">
          <h4 className="text-xs font-semibold tracking-wide text-muted">{t("fields.tags")}</h4>
          <p className="mt-2 text-sm leading-6 text-foreground/85">{faction.tags.join(" · ")}</p>
        </section>
      )}
    </article>
  );
}

/** 获取势力层级的本地化名称，未知层级保留服务端原值。 */
function getFactionLevelLabel(faction: CoreFaction, t: ReturnType<typeof useTranslations>): string {
  const level = faction.level_type || "core";
  if (level === "core" || level === "volume" || level === "local") {
    return t(`categories.${level}`);
  }
  return level;
}

/** 渲染详情中的标签和值。 */
function FactionSummaryField({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs font-semibold tracking-wide text-muted">{label}</dt><dd className="mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground/85">{value || "—"}</dd></div>;
}

/** 渲染势力详情顶部的层级或状态标签。 */
function FactionStatusPill({ active, children }: { active: boolean; children: ReactNode }) {
  return <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${active ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-surface-secondary text-muted"}`}>{children}</span>;
}

const FACTION_FILTER_CLASS = "min-h-10 rounded-xl border border-border bg-background/80 px-3 text-sm text-foreground outline-none transition-[border-color,box-shadow] placeholder:text-muted/55 focus:border-accent focus:ring-2 focus:ring-accent/15";

function TrashPanel({
  factions,
  relations,
  actionId,
  onRestore,
  onHardDelete,
  onRestoreRelation,
  onHardDeleteRelation,
  t,
}: {
  factions: CoreFaction[];
  relations: FactionRelation[];
  actionId: string;
  onRestore: (faction: CoreFaction) => void;
  onHardDelete: (faction: CoreFaction) => void;
  onRestoreRelation: (relation: FactionRelation) => void;
  onHardDeleteRelation: (relation: FactionRelation) => void;
  t: ReturnType<typeof useTranslations>;
}) {
  if (factions.length === 0 && relations.length === 0) {
    return <EmptyBlock text={t("trashEmpty")} />;
  }

  return (
    <div className="space-y-8">
      <section>
        <h3 className="border-b border-border pb-3 text-lg font-semibold text-foreground">{t("trashSections.factions")}</h3>
        {factions.length === 0 ? <p className="py-6 text-sm text-muted">{t("trashSections.emptyFactions")}</p> : <div className="divide-y divide-border">
          {factions.map((faction) => (
            <div key={getFactionRenderKey(faction)} className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-foreground">{faction.name}</p>
                <p className="mt-1 truncate text-xs text-muted">{getFactionLevelLabel(faction, t)} · {faction.faction_type || t("card.typeEmpty")} · {faction.faction_id}</p>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onPress={() => onRestore(faction)} isDisabled={actionId === `restore:${faction.faction_id}`}>
                  {actionId === `restore:${faction.faction_id}` ? t("restoring") : t("restore")}
                </Button>
                <Button variant="ghost" size="sm" className="text-danger" onPress={() => onHardDelete(faction)}>{t("hardDelete")}</Button>
              </div>
            </div>
          ))}
        </div>}
      </section>
      <section>
        <h3 className="border-b border-border pb-3 text-lg font-semibold text-foreground">{t("trashSections.relations")}</h3>
        {relations.length === 0 ? <p className="py-6 text-sm text-muted">{t("trashSections.emptyRelations")}</p> : <div className="divide-y divide-border">
          {relations.map((relation) => (
            <div key={getRelationRenderKey(relation)} className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0"><p className="truncate text-sm font-semibold text-foreground">{relation.source_faction_name ?? relation.source_faction_id} → {relation.target_faction_name ?? relation.target_faction_id}</p><p className="mt-1 font-mono text-xs text-muted">{relation.relation_id}</p></div>
              <div className="flex gap-2"><Button variant="outline" size="sm" onPress={() => onRestoreRelation(relation)} isDisabled={actionId === `relation-restore:${relation.relation_id}`}>{actionId === `relation-restore:${relation.relation_id}` ? t("restoring") : t("restore")}</Button><Button variant="ghost" size="sm" className="text-danger" onPress={() => onHardDeleteRelation(relation)}>{t("hardDelete")}</Button></div>
            </div>
          ))}
        </div>}
      </section>
    </div>
  );
}

/**
 * 功能：渲染阵营关系清单与详情组成的响应式主从工作台。
 * Args:
 *   props: 正式关系、类型标签、动作状态、编辑/复制/启停/删除回调与翻译函数。
 * Returns:
 *   桌面双栏、中屏可收起、小屏列表到详情切换的关系工作台。
 */
function FactionRelationsWorkspace({ relations, relationTypeLabels, actionId, onEdit, onCopy, onToggle, onDelete, t }: {
  relations: FactionRelation[];
  relationTypeLabels: Record<FactionRelationType, string>;
  actionId: string;
  onEdit: (relation: FactionRelation) => void;
  onCopy: (relation: FactionRelation) => void;
  onToggle: (relation: FactionRelation) => void;
  onDelete: (relation: FactionRelation) => void;
  t: ReturnType<typeof useTranslations>;
}) {
  const [selectedKey, setSelectedKey] = useState("");
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [listCollapsed, setListCollapsed] = useState(false);
  const selected = relations.find((item) => getRelationRenderKey(item) === selectedKey) ?? relations[0];
  if (!selected) return null;
  const sourceName = selected.source_faction_name ?? selected.source_faction_id ?? "—";
  const targetName = selected.target_faction_name ?? selected.target_faction_id ?? "—";
  const busy = Boolean(selected.relation_id && actionId.includes(selected.relation_id));

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden border-y border-border bg-surface/30">
      <div className="shrink-0 border-b border-border px-3 py-2 sm:flex sm:justify-end lg:hidden">
        <Button size="sm" variant="ghost" className="hidden sm:inline-flex" onPress={() => setListCollapsed((value) => !value)}>{listCollapsed ? t("relationActions.showList") : t("relationActions.hideList")}</Button>
      </div>
      <div
        className={`grid min-h-0 flex-1 ${
          listCollapsed
            ? "sm:grid-cols-1 lg:grid-cols-[minmax(260px,0.38fr)_minmax(0,1fr)]"
            : "sm:grid-cols-[minmax(190px,0.4fr)_minmax(0,1fr)] lg:grid-cols-[minmax(260px,0.38fr)_minmax(0,1fr)]"
        }`}
      >
        <aside className={`workspace-scrollbar min-h-0 overflow-y-auto border-b border-border outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/30 sm:border-b-0 sm:border-r ${mobileDetailOpen ? "hidden sm:block" : "block"} ${listCollapsed ? "sm:hidden lg:block" : ""}`} aria-label={t("tabs.relations")} tabIndex={0}>
          <ul className="divide-y divide-border">
            {relations.map((relation) => {
              const key = getRelationRenderKey(relation);
              return <li key={key}><button type="button" onClick={() => { setSelectedKey(key); setMobileDetailOpen(true); }} className={`w-full px-4 py-4 text-left transition-colors ${getRelationRenderKey(selected) === key ? "bg-accent/10" : "hover:bg-surface-secondary/50"}`}><span className="block truncate text-sm font-semibold text-foreground">{relation.source_faction_name ?? relation.source_faction_id} <span className="text-accent">→</span> {relation.target_faction_name ?? relation.target_faction_id}</span><span className="mt-1 block text-xs text-muted">{relationTypeLabels[relation.relation_type]} · {relation.intensity}/5</span></button></li>;
            })}
          </ul>
        </aside>
        <article className={`workspace-scrollbar min-h-0 min-w-0 overflow-y-auto px-5 py-6 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/30 sm:px-7 ${mobileDetailOpen ? "block" : "hidden sm:block"}`} tabIndex={0}>
          <button type="button" className="mb-4 text-sm font-medium text-accent sm:hidden" onClick={() => setMobileDetailOpen(false)}>← {t("relationActions.back")}</button>
          <header className="flex flex-col gap-4 border-b border-border pb-5 xl:flex-row xl:items-start xl:justify-between">
            <div><p className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">{relationTypeLabels[selected.relation_type]}</p><h4 className="mt-2 text-xl font-semibold text-foreground">{sourceName} <span className="text-accent">→</span> {targetName}</h4><p className="mt-2 text-sm text-muted">{selected.is_active ? t("relationStatus.active") : t("relationStatus.inactive")} · {selected.intensity}/5</p></div>
            <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onPress={() => onEdit(selected)} isDisabled={busy}>{t("edit")}</Button><Button size="sm" variant="ghost" onPress={() => onCopy(selected)} isDisabled={busy}>{t("relationActions.copy")}</Button><Button size="sm" variant="ghost" onPress={() => onToggle(selected)} isDisabled={busy}>{(selected.user_is_active ?? selected.is_active) ? t("relationActions.deactivate") : t("relationActions.activate")}</Button><Button size="sm" variant="ghost" className="text-danger" onPress={() => onDelete(selected)} isDisabled={busy}>{t("delete")}</Button></div>
          </header>
          <dl className="grid gap-x-8 gap-y-6 py-6 md:grid-cols-2">
            {[[t("fields.currentState"), selected.current_state], [t("fields.coreConflict"), selected.core_conflict], [t("fields.hiddenTension"), selected.hidden_tension ?? ""], [t("fields.possibleChange"), selected.possible_change]].map(([label, value]) => <div key={label}><dt className="text-xs font-semibold tracking-wide text-muted">{label}</dt><dd className="mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground">{value || "—"}</dd></div>)}
          </dl>
          <div className="h-2 overflow-hidden rounded-full bg-surface-secondary" aria-label={`${t("fields.intensity")}: ${selected.intensity}/5`}><div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(0, Math.min(5, selected.intensity)) * 20}%` }} /></div>
        </article>
      </div>
    </div>
  );
}

/**
 * 功能：渲染正式阵营关系的创建或编辑弹窗。
 * Args:
 *   props: 编辑模式、势力候选、错误状态、草稿更新及保存取消回调。
 * Returns:
 *   使用 HeroUI v3 Modal 且在编辑态锁定端点的关系表单。
 */
function FactionRelationEditorModal({ editor, factions, saving, error, fieldErrors, onChange, onCancel, onSave, t }: {
  editor: NonNullable<RelationEditorState>;
  factions: CoreFaction[];
  saving: boolean;
  error: string;
  fieldErrors: ApiFieldErrors;
  onChange: (draft: FactionRelationCreateRequestV1) => void;
  onCancel: () => void;
  onSave: () => void;
  t: ReturnType<typeof useTranslations>;
}) {
  const sourceOptionsId = useId();
  const targetOptionsId = useId();
  const fieldError = (name: string) => fieldErrors[name]?.[0];
  const describedBy = (name: string) => fieldError(name) ? `faction-relation-${name}-error` : undefined;
  const update = <K extends keyof FactionRelationCreateRequestV1>(name: K, value: FactionRelationCreateRequestV1[K]) => onChange({ ...editor.draft, [name]: value });
  const endpoint = (name: "source_faction_id" | "target_faction_id", label: string, opposite: string) => {
    if (editor.mode === "edit") {
      return <RelationFieldShell label={label} error={fieldError(name)} errorId={`faction-relation-${name}-error`}><input name={name} readOnly aria-readonly="true" aria-invalid={Boolean(fieldError(name)) || undefined} aria-describedby={describedBy(name)} value={`${factions.find((item) => item.faction_id === editor.draft[name])?.name ?? editor.draft[name]} · ${editor.draft[name]}`} className={`${RELATION_INPUT_CLASS} bg-surface-secondary/55`} /></RelationFieldShell>;
    }
    const optionListId = name === "source_faction_id" ? sourceOptionsId : targetOptionsId;
    return (
      <RelationFieldShell label={label} error={fieldError(name)} errorId={`faction-relation-${name}-error`}>
        <input type="text" name={name} list={optionListId} value={editor.draft[name]} onChange={(event) => update(name, event.target.value)} onBlur={(event) => { const isAllowedEndpoint = factions.some((faction) => faction.faction_id === event.currentTarget.value && faction.faction_id !== opposite); if (event.currentTarget.value && !isAllowedEndpoint) update(name, ""); }} autoComplete="off" aria-invalid={Boolean(fieldError(name)) || undefined} aria-describedby={describedBy(name)} className={RELATION_INPUT_CLASS} />
        <datalist id={optionListId}>
          {factions.filter((faction) => faction.faction_id !== opposite).map((faction) => <option key={faction.faction_id} value={faction.faction_id} label={`${faction.name} · ${faction.faction_id}`} />)}
        </datalist>
      </RelationFieldShell>
    );
  };
  return (
      <Modal.Backdrop isOpen onOpenChange={(isOpen) => !isOpen && !saving && onCancel()} variant="blur" isDismissable={!saving}>
        <Modal.Container size="full" scroll="inside" className="p-0 sm:p-4"><Modal.Dialog className="h-[100dvh] w-full sm:h-auto sm:max-h-[90vh] sm:max-w-5xl">
          <Modal.Header className="border-b border-border px-5 py-4 sm:px-7"><div><p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">{t("relationEditor.eyebrow")}</p><Modal.Heading className="mt-1 text-lg font-semibold text-foreground">{editor.mode === "create" ? t("relationEditor.createTitle") : t("relationEditor.editTitle")}</Modal.Heading><p className="mt-1 text-sm leading-6 text-muted">{editor.mode === "create" ? t("relationEditor.createDescription") : t("relationEditor.editDescription")}</p></div><Modal.CloseTrigger aria-label={t("cancel")} isDisabled={saving} /></Modal.Header>
          <Modal.Body className="px-5 py-6 sm:px-7">
            {error && <RelationEditorErrorSummary message={error} fieldErrors={fieldErrors} className="mb-6" />}
            <div className="space-y-8">
              <section className="border-b border-border pb-7"><h4 className="text-sm font-semibold text-foreground">{t("relationEditor.endpoints")}</h4><div className="mt-5 grid gap-5 md:grid-cols-2">{endpoint("source_faction_id", t("fields.sourceFaction"), editor.draft.target_faction_id)}{endpoint("target_faction_id", t("fields.targetFaction"), editor.draft.source_faction_id)}<RelationFieldShell label={t("fields.relationType")} error={fieldError("relation_type")} errorId="faction-relation-relation_type-error"><select name="relation_type" value={editor.draft.relation_type} onChange={(event) => update("relation_type", event.target.value as FactionRelationType)} aria-invalid={Boolean(fieldError("relation_type")) || undefined} aria-describedby={describedBy("relation_type")} className={RELATION_INPUT_CLASS}>{RELATION_TYPES.map((type) => <option key={type} value={type}>{t(`relationTypes.${type === "cold_war" ? "coldWar" : type === "trade_partner" ? "tradePartner" : type === "secret_cooperation" ? "secretCooperation" : type === "historical_enemy" ? "historicalEnemy" : type}`)}</option>)}</select></RelationFieldShell><RelationIntensityField value={editor.draft.intensity} error={fieldError("intensity")} errorId="faction-relation-intensity-error" onChange={(value) => update("intensity", value)} label={t("fields.intensity")} inputClassName={RELATION_INPUT_CLASS} /></div></section>
              <section><h4 className="text-sm font-semibold text-foreground">{t("relationEditor.narrative")}</h4><div className="mt-5 grid gap-5 md:grid-cols-2">{(["current_state", "core_conflict", "hidden_tension", "possible_change"] as const).map((name) => <RelationFieldShell key={name} label={t(`fields.${name === "current_state" ? "currentState" : name === "core_conflict" ? "coreConflict" : name === "hidden_tension" ? "hiddenTension" : "possibleChange"}`)} error={fieldError(name)} errorId={`faction-relation-${name}-error`} className={name === "current_state" ? "md:col-span-2" : ""}><textarea name={name} value={editor.draft[name]} rows={name === "current_state" ? 3 : 5} onChange={(event) => update(name, event.target.value)} aria-invalid={Boolean(fieldError(name)) || undefined} aria-describedby={describedBy(name)} className={`${RELATION_INPUT_CLASS} min-h-28 resize-y leading-6`} /></RelationFieldShell>)}</div></section>
            </div>
          </Modal.Body>
          <Modal.Footer className="sticky bottom-0 border-t border-border bg-surface px-5 py-4 sm:px-7"><Button variant="ghost" size="sm" onPress={onCancel} isDisabled={saving}>{t("cancel")}</Button><Button variant="primary" size="sm" onPress={onSave} isDisabled={saving}>{saving ? t("saving") : t("save")}</Button></Modal.Footer>
        </Modal.Dialog></Modal.Container>
      </Modal.Backdrop>
  );
}

const RELATION_INPUT_CLASS = "min-h-11 w-full rounded-xl border border-border bg-background/80 px-3.5 py-2.5 text-sm text-foreground outline-none focus:border-accent focus:ring-2 focus:ring-accent/15";

function InfoBlock({ text, tone }: { text: string; tone: "muted" | "warning" }) {
  const className =
    tone === "warning"
      ? "mt-3 border-y border-amber-200 bg-amber-50/70 px-3 py-2 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
      : "mt-3 border-y border-border px-3 py-2 text-sm text-muted";
  return <div className={className}>{text}</div>;
}

function EmptyBlock({ text }: { text: string }) {
  return (
    <div className="border-y border-dashed border-border px-4 py-8 text-center text-sm text-muted">
      {text}
    </div>
  );
}

/**
 * 渲染势力表单中的单行文本输入。
 *
 * Args:
 *   props: 字段标签、当前值、必填与禁用状态、占位提示、外层样式及变更回调。
 *
 * Returns:
 *   带标签和聚焦反馈的单行输入 React 节点。
 */
function TextInput({
  label,
  value,
  required,
  placeholder,
  disabled,
  className = "",
  onChange,
}: {
  label: string;
  value: string;
  required?: boolean;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className={`grid gap-2 text-sm ${className}`}>
      <span className="text-xs font-semibold tracking-wide text-muted">{label}{required ? " *" : ""}</span>
      <input
        type="text"
        value={value}
        required={required}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-11 rounded-xl border border-border bg-background/80 px-3.5 py-2.5 text-sm text-foreground outline-none transition-[border-color,box-shadow] placeholder:text-muted/55 focus:border-accent focus:ring-2 focus:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-60"
      />
    </label>
  );
}

/**
 * 渲染可按语义配置初始高度的多行文本输入。
 *
 * Args:
 *   props: 字段标签、当前值、占位提示、行数、禁用状态、外层样式及变更回调。
 *
 * Returns:
 *   可纵向调整高度的多行输入 React 节点。
 */
function TextArea({
  label,
  value,
  placeholder,
  rows = 3,
  disabled,
  className = "",
  onChange,
}: {
  label: string;
  value: string;
  placeholder?: string;
  rows?: number;
  disabled?: boolean;
  className?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className={`grid gap-2 text-sm ${className}`}>
      <span className="text-xs font-semibold tracking-wide text-muted">{label}</span>
      <textarea
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        className="resize-y rounded-xl border border-border bg-background/80 px-3.5 py-3 text-sm leading-6 text-foreground outline-none transition-[border-color,box-shadow] placeholder:text-muted/55 focus:border-accent focus:ring-2 focus:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-60"
      />
    </label>
  );
}

/**
 * 渲染关系端点或枚举字段的原生选择器。
 *
 * Args:
 *   props: 字段标签、当前值、选项、可选标签映射、禁用状态及变更回调。
 *
 * Returns:
 *   使用项目主题样式的下拉选择 React 节点。
 */
function SelectInput({
  label,
  value,
  options,
  optionLabel,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  optionLabel?: (value: string) => string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid gap-2 text-sm">
      <span className="text-xs font-semibold tracking-wide text-muted">{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-11 rounded-xl border border-border bg-background/80 px-3.5 py-2.5 text-sm text-foreground outline-none transition-[border-color,box-shadow] focus:border-accent focus:ring-2 focus:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {optionLabel ? optionLabel(option) : option}
          </option>
        ))}
      </select>
    </label>
  );
}
