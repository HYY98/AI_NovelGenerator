"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTranslations } from "next-intl";
import { Button } from "@heroui/react";
import {
  ApiRequestError,
  apiDelete,
  apiGet,
  apiPost,
  apiPostSSE,
  apiPut,
  type ApiFieldErrors,
} from "@/lib/api";
import {
  buildGenerationParamsPayload,
  DEFAULT_GENERATION_PARAMS,
  type GenerationParamsValue,
} from "@/components/shared/GenerationParamsCollapse";
import { AIProgressBar } from "@/components/shared/AIProgressBar";
import {
  ConfirmActionModal,
  focusRelationEditorError,
  RelationEditorErrorSummary,
} from "@/components/shared/RelationEditorPrimitives";
import {
  CREATE_CHARACTER_RELATIONS_WORKFLOW_NAME,
  CREATE_CORE_CHARACTERS_WORKFLOW_NAME,
  normalizeAppConfig,
  type AppConfig,
} from "@/types/config";
import type { CoreFaction } from "@/types/novel";
import type {
  BulkAppendCharacterRelationsRequestV1,
  CharacterCreateRequestV1,
  CharacterFactionBindingCandidateV1,
  CharacterFactionBindingResponseV1,
  CharacterImportanceLevel,
  CharacterProfileFields,
  CharacterRelationFields,
  CharacterRelationUpdateRequestV1,
  CharacterRelationResponseV1,
  CharacterRelationsReviewProjectionV1,
  CharacterRelationsResultV1,
  CharacterResponseV1,
  CharacterUpdateRequestV1,
  CharacterRoleType,
  CoreCharactersResultV1,
} from "@/types/character";
import CharacterProfileForm from "./forms/CharacterProfileForm";
import CharacterRelationEditor from "./forms/CharacterRelationEditor";
import CharacterGenerationPreview from "./review/CharacterGenerationPreview";
import {
  CoreGenerationDialog,
  RelationGenerationDialog,
  type CoreGenerationDraft,
  type RelationGenerationDraft,
} from "./dialogs/CharacterGenerationDialogs";
import {
  ModalLayer,
  ProfilesView,
  RelationsView,
  TrashView,
  UnavailablePanel,
  WorkspaceHeader,
  type CharacterWorkspaceView,
} from "./views/CharacterWorkspaceViews";

interface CharacterCardsWorkspaceProps {
  mode: "create" | "edit";
  novelId?: string;
  initialView?: CharacterWorkspaceView;
}

type GenerationKind = "core_characters" | "character_relations";

type CoreCharactersPreviewRequest = Partial<GenerationParamsValue> & {
  novel_id: string;
  character_count: number;
  user_guidance: string | null;
};

type CharacterRelationsPreviewRequest = Partial<GenerationParamsValue> & {
  novel_id: string;
  character_ids: string[];
  allow_isolated_characters: boolean;
  relation_count_limit: number | null;
  user_guidance: string | null;
};

type CharacterGenerationProgress = {
  characters: number;
  chunkCount: number;
};

type CharacterGenerationState =
  | { status: "idle" }
  | { status: "generating"; kind: GenerationKind; progress?: CharacterGenerationProgress }
  | {
      status: "preview";
      kind: "core_characters";
      data: CoreCharactersResultV1;
      selectedIndex: number;
      saving: boolean;
      error: string;
    }
  | {
      status: "preview";
      kind: "character_relations";
      data: CharacterRelationsReviewProjectionV1;
      selectedIndex: number;
      saving: boolean;
      error: string;
    }
  | { status: "error"; kind: GenerationKind; message: string };

interface StableCommand {
  fingerprint: string;
  key: string;
}

type DeleteIntent =
  | { kind: "character"; entity: CharacterResponseV1; hard: boolean }
  | { kind: "relation"; entity: CharacterRelationResponseV1; hard: boolean };

const DEFAULT_CORE_GENERATION: CoreGenerationDraft = {
  count: 3,
  guidance: "",
  params: DEFAULT_GENERATION_PARAMS,
};

const REQUIRED_PROFILE_TEXT_FIELDS: Array<
  keyof Pick<
    CharacterProfileFields,
    | "name"
    | "gender"
    | "age_group"
    | "race"
    | "identity"
    | "appearance"
    | "personality"
    | "core_desire"
    | "core_fear"
    | "conflict_with_mainline"
    | "relationship_with_protagonist"
    | "initial_state"
    | "growth_direction"
    | "story_function"
    | "arc_seed"
  >
> = [
  "name",
  "gender",
  "age_group",
  "race",
  "identity",
  "appearance",
  "personality",
  "core_desire",
  "core_fear",
  "conflict_with_mainline",
  "relationship_with_protagonist",
  "initial_state",
  "growth_direction",
  "story_function",
  "arc_seed",
];

const REQUIRED_RELATION_TEXT_FIELDS: Array<
  keyof Pick<
    CharacterRelationFields,
    "current_state" | "core_conflict" | "hidden_tension" | "possible_change" | "story_value"
  >
> = ["current_state", "core_conflict", "hidden_tension", "possible_change", "story_value"];

/**
 * 创建字段齐全的空角色档案。
 *
 * Args:
 *   无。
 *
 * Returns:
 *   可直接交给角色表单编辑的白名单业务字段。
 */
function createEmptyCharacterProfile(): CharacterProfileFields {
  return {
    name: "",
    aliases: [],
    role_type: "supporting",
    importance_level: "supporting",
    gender: "",
    age_group: "",
    race: "",
    identity: "",
    appearance: "",
    personality: "",
    core_desire: "",
    core_fear: "",
    strengths: [],
    weaknesses: [],
    abilities: [],
    conflict_with_mainline: "",
    relationship_with_protagonist: "",
    initial_state: "",
    growth_direction: "",
    story_function: "",
    arc_seed: "",
    tags: [],
  };
}

/**
 * 功能：构造手工角色创建接口的严格请求载荷。
 * Args:
 *   profile: 用户填写的角色业务档案。
 *   isCoreCharacter: 与重要程度独立的核心身份开关。
 * Returns:
 *   包含核心标记、默认排序与扩展字段的创建请求。
 */
export function buildManualCharacterCreateRequest(
  profile: CharacterProfileFields,
  isCoreCharacter: boolean,
): CharacterCreateRequestV1 {
  return {
    ...profile,
    is_core_character: isCoreCharacter,
    sort_order: 0,
    extra: {},
  };
}

/**
 * 功能：构造正式角色普通编辑接口的严格白名单载荷。
 * Args:
 *   character: 提供当前 CAS 版本的正式角色。
 *   profile: 编辑器中允许修改的角色业务档案字段。
 * Returns:
 *   仅含 expected_version 与档案白名单、不含核心标记和审计字段的请求。
 */
export function buildCharacterUpdateRequest(
  character: CharacterResponseV1,
  profile: CharacterProfileFields,
): CharacterUpdateRequestV1 {
  return {
    expected_version: character.version,
    ...profile,
  };
}

/**
 * 功能：从正式角色响应中提取普通更新允许的档案白名单。
 * Args:
 *   character: 已落库的角色响应。
 * Returns:
 *   不含核心标记、ID、状态、版本及审计字段的表单值。
 */
export function characterProfileFromResponse(
  character: CharacterResponseV1,
): CharacterProfileFields {
  const empty = createEmptyCharacterProfile();
  return Object.fromEntries(
    Object.keys(empty).map((key) => [key, structuredClone(character[key as keyof CharacterProfileFields])]),
  ) as unknown as CharacterProfileFields;
}

/**
 * 判断角色表单是否满足后端全部必填文本约束。
 *
 * Args:
 *   value: 当前角色业务字段。
 *
 * Returns:
 *   所有必填文本去除空白后均非空时返回 true。
 */
function isCompleteCharacterProfile(value: CharacterProfileFields): boolean {
  return REQUIRED_PROFILE_TEXT_FIELDS.every((field) => value[field].trim().length > 0);
}

/**
 * 创建字段齐全的空角色关系。
 *
 * Args:
 *   characters: 可作为端点的 active 核心角色。
 *
 * Returns:
 *   默认选择前两个不同角色的关系表单值。
 */
function createEmptyRelation(characters: CharacterResponseV1[]): CharacterRelationFields {
  return {
    source_character_id: characters[0]?.character_id ?? "",
    target_character_id: characters[1]?.character_id ?? "",
    relation_type: "ally",
    current_state: "",
    core_conflict: "",
    hidden_tension: "",
    possible_change: "",
    story_value: "",
    intensity: 3,
    is_active: true,
  };
}

/**
 * 功能：从正式人物关系响应中提取编辑器允许的业务字段。
 * Args:
 *   relation: 已落库的人物关系响应。
 * Returns:
 *   可用于内容编辑或复制创建的关系草稿。
 */
export function characterRelationFromResponse(
  relation: CharacterRelationResponseV1,
): CharacterRelationFields {
  return {
    source_character_id: relation.source_character_id,
    target_character_id: relation.target_character_id,
    relation_type: relation.relation_type,
    current_state: relation.current_state,
    core_conflict: relation.core_conflict,
    hidden_tension: relation.hidden_tension,
    possible_change: relation.possible_change,
    story_value: relation.story_value,
    intensity: relation.intensity,
    is_active: relation.user_is_active ?? relation.is_active,
  };
}

/**
 * 功能：把任意提交异常归一化为弹窗摘要与字段错误。
 * Args:
 *   error: API 或浏览器抛出的未知异常。
 * Returns:
 *   可直接写入编辑弹窗状态的错误信息。
 */
function describeEditorError(error: unknown): { message: string; fieldErrors: ApiFieldErrors } {
  return error instanceof ApiRequestError
    ? { message: error.message, fieldErrors: error.fieldErrors }
    : { message: error instanceof Error ? error.message : String(error), fieldErrors: {} };
}

/**
 * 判断角色关系是否满足本轮创建与候选编辑的本地必填约束。
 *
 * Args:
 *   value: 当前关系业务字段。
 *
 * Returns:
 *   端点不同、叙述字段完整且强度合法时返回 true。
 */
function isCompleteRelation(value: CharacterRelationFields): boolean {
  return (
    Boolean(value.source_character_id) &&
    Boolean(value.target_character_id) &&
    value.source_character_id !== value.target_character_id &&
    value.intensity >= 1 &&
    value.intensity <= 5 &&
    REQUIRED_RELATION_TEXT_FIELDS.every((field) => value[field].trim().length > 0)
  );
}

/**
 * 判断核心角色的势力绑定候选是否满足后端的可空绑定约束。
 *
 * Args:
 *   value: 当前角色对应的势力绑定候选。
 *
 * Returns:
 *   未绑定时其余字段全空，或已绑定且必填字段完整时返回 true。
 */
function isCompleteBindingCandidate(value: CharacterFactionBindingCandidateV1): boolean {
  if (value.faction_id === null) {
    return (
      value.membership_type === null &&
      value.role_title === null &&
      value.public_status === null &&
      value.loyalty_level === null &&
      value.reason === null
    );
  }

  return (
    value.membership_type !== null &&
    Boolean(value.public_status?.trim()) &&
    value.loyalty_level !== null &&
    value.loyalty_level >= 1 &&
    value.loyalty_level <= 5 &&
    Boolean(value.reason?.trim())
  );
}

/**
 * 生成在相同请求重试期间保持不变的幂等命令键。
 *
 * Args:
 *   cache: 组件生命周期内的命令缓存。
 *   scope: 逻辑操作范围。
 *   payload: 本次请求载荷。
 *
 * Returns:
 *   同范围同载荷复用、载荷变化后自动轮换的合法幂等键。
 */
function getStableCommandKey(
  cache: Map<string, StableCommand>,
  scope: string,
  payload: unknown,
): string {
  const fingerprint = JSON.stringify(payload);
  const cached = cache.get(scope);
  if (cached?.fingerprint === fingerprint) return cached.key;
  const key = `${scope}:${globalThis.crypto.randomUUID()}`;
  cache.set(scope, { fingerprint, key });
  return key;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

/**
 * 对核心角色预览执行最低限度的运行时结构校验。
 *
 * Args:
 *   value: 普通响应或 SSE done 事件中的未知结果。
 *
 * Returns:
 *   结果含角色候选与绑定候选数组时返回 true。
 */
function isCoreCharactersPayload(value: unknown): value is CoreCharactersResultV1 {
  if (!isRecord(value) || !Array.isArray(value.core_characters) || !Array.isArray(value.binding_candidates)) {
    return false;
  }
  return value.core_characters.every(
    (item) => isRecord(item) && typeof item.character_ref === "string" && typeof item.name === "string",
  );
}

/**
 * 对角色关系预览执行最低限度的运行时结构校验。
 *
 * Args:
 *   value: 普通响应或 SSE done 事件中的未知结果。
 *
 * Returns:
 *   结果含关系候选数组时返回 true。
 */
function isCharacterRelationsPayload(value: unknown): value is CharacterRelationsResultV1 {
  return (
    isRecord(value) &&
    Array.isArray(value.relations) &&
    value.relations.every(
      (item) =>
        isRecord(item) &&
        typeof item.relation_ref === "string" &&
        typeof item.source_character_id === "string" &&
        typeof item.target_character_id === "string",
    )
  );
}

/**
 * 从直接生成 SSE 的 done 事件中提取并校验结果。
 *
 * Args:
 *   data: SSE done 事件数据。
 *   guard: 对目标领域结果执行结构收窄的函数。
 *
 * Returns:
 *   结构合法时返回生成结果，否则返回 null。
 */
function extractStreamResult<T>(
  data: Record<string, unknown>,
  guard: (value: unknown) => value is T,
): T | null {
  return guard(data.result) ? data.result : null;
}

/**
 * 判断角色生成步骤当前使用的 Provider 是否支持流式响应。
 *
 * Args:
 *   config: 已归一化应用配置。
 *   workflowName: 角色或角色关系生成工作流名。
 *   stepName: 工作流中的单一步骤名。
 *
 * Returns:
 *   Provider 已启用且声明支持流式时返回 true。
 */
function supportsCharacterStreaming(
  config: AppConfig,
  workflowName: string,
  stepName: string,
): boolean {
  const workflow = config.llm.workflows?.[workflowName];
  const providerAlias =
    workflow?.steps?.[stepName]?.provider ||
    workflow?.default_provider ||
    config.llm.default_provider ||
    "";
  const provider = config.llm.providers[providerAlias];
  return Boolean(provider?.enabled && provider.supports_streaming);
}

/**
 * 渲染全书级角色档案、核心角色生成审核和关系边摘要工作台。
 *
 * Args:
 *   mode: 写作页创建或编辑模式。
 *   novelId: 已保存小说 ObjectId。
 *   initialView: 从侧栏入口决定的初始角色或关系视图。
 *
 * Returns:
 *   不包含卷级状态与复杂图布局的角色工作台 React 节点。
 */
export default function CharacterCardsWorkspace({
  mode,
  novelId,
  initialView = "profiles",
}: CharacterCardsWorkspaceProps) {
  const t = useTranslations("writing.characters");
  const [view, setView] = useState<CharacterWorkspaceView>(initialView);
  const [characters, setCharacters] = useState<CharacterResponseV1[]>([]);
  const [relations, setRelations] = useState<CharacterRelationResponseV1[]>([]);
  const [trashCharacters, setTrashCharacters] = useState<CharacterResponseV1[]>([]);
  const [trashRelations, setTrashRelations] = useState<CharacterRelationResponseV1[]>([]);
  const [bindings, setBindings] = useState<CharacterFactionBindingResponseV1[]>([]);
  const [factions, setFactions] = useState<CoreFaction[]>([]);
  const [loading, setLoading] = useState(false);
  const [dataError, setDataError] = useState("");
  const [actionError, setActionError] = useState("");
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState<CharacterRoleType | "all">("all");
  const [importanceFilter, setImportanceFilter] = useState<CharacterImportanceLevel | "all">("all");
  const [manualCharacterOpen, setManualCharacterOpen] = useState(false);
  const [manualCharacter, setManualCharacter] = useState<CharacterProfileFields>(createEmptyCharacterProfile);
  const [manualCharacterIsCore, setManualCharacterIsCore] = useState(false);
  const [editingCharacter, setEditingCharacter] = useState<CharacterResponseV1 | null>(null);
  const [manualRelationOpen, setManualRelationOpen] = useState(false);
  const [manualRelation, setManualRelation] = useState<CharacterRelationFields>(() => createEmptyRelation([]));
  const [editingRelation, setEditingRelation] = useState<CharacterRelationResponseV1 | null>(null);
  const [coreDialogOpen, setCoreDialogOpen] = useState(false);
  const [coreGenerationParamsOpen, setCoreGenerationParamsOpen] = useState(false);
  const [coreGeneration, setCoreGeneration] = useState<CoreGenerationDraft>(DEFAULT_CORE_GENERATION);
  const [relationDialogOpen, setRelationDialogOpen] = useState(false);
  const [relationGenerationParamsOpen, setRelationGenerationParamsOpen] = useState(false);
  const [relationGeneration, setRelationGeneration] = useState<RelationGenerationDraft>({
    selectedCharacterIds: [],
    allowIsolatedCharacters: false,
    relationCountLimit: "",
    guidance: "",
    params: DEFAULT_GENERATION_PARAMS,
  });
  const [generationState, setGenerationState] = useState<CharacterGenerationState>({ status: "idle" });
  const [coreStreamingSupported, setCoreStreamingSupported] = useState(false);
  const [relationStreamingSupported, setRelationStreamingSupported] = useState(false);
  const [submittingManual, setSubmittingManual] = useState(false);
  const [editorError, setEditorError] = useState("");
  const [editorFieldErrors, setEditorFieldErrors] = useState<ApiFieldErrors>({});
  const [actionId, setActionId] = useState("");
  const [deleteIntent, setDeleteIntent] = useState<DeleteIntent | null>(null);

  const generationAbortRef = useRef<AbortController | null>(null);
  const commandCacheRef = useRef(new Map<string, StableCommand>());

  const canUseSavedNovel = mode === "edit" && Boolean(novelId);
  const generationError =
    generationState.status === "error"
      ? generationState.message
      : generationState.status === "preview"
        ? generationState.error
        : "";
  const coreCharacters = useMemo(
    () => characters.filter((item) => item.is_core_character && item.status === "active"),
    [characters],
  );

  const filteredCharacters = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return characters.filter((character) => {
      if (roleFilter !== "all" && character.role_type !== roleFilter) return false;
      if (importanceFilter !== "all" && character.importance_level !== importanceFilter) return false;
      if (!query) return true;
      return [character.name, ...character.aliases, ...character.tags]
        .join(" ")
        .toLocaleLowerCase()
        .includes(query);
    });
  }, [characters, importanceFilter, roleFilter, search]);

  const characterNames = useMemo(
    () => new Map(characters.map((character) => [character.character_id, character.name])),
    [characters],
  );
  const factionNames = useMemo(
    () =>
      new Map(
        factions.flatMap((faction) =>
          faction.faction_id ? [[faction.faction_id, faction.name] as const] : [],
        ),
      ),
    [factions],
  );
  const bindingsByCharacter = useMemo(() => {
    const result = new Map<string, CharacterFactionBindingResponseV1[]>();
    for (const binding of bindings) {
      result.set(binding.character_id, [...(result.get(binding.character_id) ?? []), binding]);
    }
    return result;
  }, [bindings]);
  const relationCountByCharacter = useMemo(() => {
    const result = new Map<string, number>();
    for (const relation of relations) {
      result.set(relation.source_character_id, (result.get(relation.source_character_id) ?? 0) + 1);
      result.set(relation.target_character_id, (result.get(relation.target_character_id) ?? 0) + 1);
    }
    return result;
  }, [relations]);

  const clearPreview = useCallback(() => {
    setGenerationState({ status: "idle" });
  }, []);

  const loadData = useCallback(async () => {
    if (!novelId) return;
    setLoading(true);
    setDataError("");
    try {
      const characterResponse = await apiGet<{ data: CharacterResponseV1[] }>(
        `/api/characters/novel/${novelId}`,
      );
      setCharacters(characterResponse.data);

      // 辅助数据独立加载；任一集合失败时仍保留已经成功取得的角色主档。
      const [relationResult, bindingResult, factionResult, characterTrashResult, relationTrashResult] = await Promise.allSettled([
        apiGet<{ data: CharacterRelationResponseV1[] }>(`/api/character-relations/novel/${novelId}`),
        apiGet<{ data: CharacterFactionBindingResponseV1[] }>(
          `/api/character-faction-bindings/novel/${novelId}`,
        ),
        apiGet<{ data: CoreFaction[] }>(`/api/factions/novel/${novelId}/level/core`),
        apiGet<{ data: CharacterResponseV1[] }>(`/api/characters/novel/${novelId}/trash`),
        apiGet<{ data: CharacterRelationResponseV1[] }>(`/api/character-relations/novel/${novelId}/trash`),
      ]);
      const optionalErrors: string[] = [];
      if (relationResult.status === "fulfilled") setRelations(relationResult.value.data);
      else optionalErrors.push(t("errors.relationsLoadFailed"));
      if (bindingResult.status === "fulfilled") setBindings(bindingResult.value.data);
      else optionalErrors.push(t("errors.bindingsLoadFailed"));
      if (factionResult.status === "fulfilled") setFactions(factionResult.value.data);
      else optionalErrors.push(t("errors.factionsLoadFailed"));
      if (characterTrashResult.status === "fulfilled") setTrashCharacters(characterTrashResult.value.data);
      else optionalErrors.push(t("errors.trashLoadFailed"));
      if (relationTrashResult.status === "fulfilled") setTrashRelations(relationTrashResult.value.data);
      else optionalErrors.push(t("errors.trashLoadFailed"));
      setDataError(optionalErrors.join(" "));
    } catch (error) {
      setDataError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [novelId, t]);

  const loadGenerationCapabilities = useCallback(async () => {
    try {
      const response = await apiGet<{ data: AppConfig }>("/api/config");
      const config = normalizeAppConfig(response.data);
      setCoreStreamingSupported(
        supportsCharacterStreaming(
          config,
          CREATE_CORE_CHARACTERS_WORKFLOW_NAME,
          "create_core_characters",
        ),
      );
      setRelationStreamingSupported(
        supportsCharacterStreaming(
          config,
          CREATE_CHARACTER_RELATIONS_WORKFLOW_NAME,
          "create_character_relations",
        ),
      );
    } catch {
      // 配置读取失败时仍允许生成，直接回退到普通 JSON 接口。
      setCoreStreamingSupported(false);
      setRelationStreamingSupported(false);
    }
  }, []);

  useEffect(() => {
    setView(initialView);
  }, [initialView]);

  useEffect(() => {
    if (canUseSavedNovel) {
      void loadData();
      void loadGenerationCapabilities();
    }
  }, [canUseSavedNovel, loadData, loadGenerationCapabilities]);

  useEffect(() => {
    // 小说切换时立即中止旧请求并丢弃其本地预览，避免结果串入新小说。
    generationAbortRef.current?.abort();
    generationAbortRef.current = null;
    setGenerationState({ status: "idle" });
    setActionError("");
    setDataError("");
    setEditorError("");
    setEditorFieldErrors({});
    return () => generationAbortRef.current?.abort();
  }, [novelId]);

  const handleCancelGeneration = () => {
    // 面板中的取消动作只中止当前浏览器请求并清理瞬时状态，不再参与参数弹窗的关闭。
    generationAbortRef.current?.abort();
    generationAbortRef.current = null;
    setGenerationState({ status: "idle" });
    setActionError("");
  };

  const handleGenerateCoreCharacters = async () => {
    if (
      !novelId ||
      generationAbortRef.current ||
      generationState.status === "generating" ||
      generationState.status === "preview"
    ) return;

    const request: CoreCharactersPreviewRequest = {
      novel_id: novelId,
      character_count: Math.max(1, Math.min(50, coreGeneration.count)),
      user_guidance: coreGeneration.guidance.trim() || null,
      ...buildGenerationParamsPayload(coreGeneration.params),
    };

    const controller = new AbortController();
    generationAbortRef.current = controller;
    setGenerationState({ status: "generating", kind: "core_characters" });
    setActionError("");
    // 请求一经发起就退出参数弹窗，后续状态、取消动作和错误统一留在角色卡面板。
    setCoreDialogOpen(false);

    try {
      let result: CoreCharactersResultV1 | null = null;
      if (request.use_stream && coreStreamingSupported) {
        await apiPostSSE(
          "/api/llm/generate-core-characters/stream",
          request,
          (event, data) => {
            if (event === "progress") {
              // 流式进度事件携带累计 chunk 数与字符数，用于驱动进度条
              const characters = Number(data.characters ?? 0);
              const chunkCount = Number(data.chunk_count ?? 0);
              setGenerationState((prev) =>
                prev.status === "generating"
                  ? { ...prev, progress: { characters, chunkCount } }
                  : prev,
              );
              return;
            }
            if (event === "error") {
              throw new Error(String(data.error || data.message || t("errors.generateFailed")));
            }
            if (event === "done") {
              result = extractStreamResult(data, isCoreCharactersPayload);
            }
          },
          { signal: controller.signal },
        );
      } else {
        const response = await apiPost<unknown>(
          "/api/llm/generate-core-characters",
          request,
          { signal: controller.signal },
        );
        result = isCoreCharactersPayload(response) ? response : null;
      }

      if (!result) throw new Error(t("errors.invalidCorePreview"));
      setGenerationState({
        status: "preview",
        kind: "core_characters",
        data: structuredClone(result),
        selectedIndex: 0,
        saving: false,
        error: "",
      });
    } catch (error) {
      if (!controller.signal.aborted) {
        setGenerationState({
          status: "error",
          kind: "core_characters",
          message: error instanceof Error ? error.message : String(error),
        });
      }
    } finally {
      if (generationAbortRef.current === controller) {
        generationAbortRef.current = null;
      }
    }
  };

  const handleGenerateRelations = async () => {
    if (
      !novelId ||
      generationAbortRef.current ||
      generationState.status === "generating" ||
      generationState.status === "preview"
    ) return;

    const request: CharacterRelationsPreviewRequest = {
      novel_id: novelId,
      character_ids: relationGeneration.selectedCharacterIds,
      allow_isolated_characters: relationGeneration.allowIsolatedCharacters,
      relation_count_limit: relationGeneration.relationCountLimit
        ? Number(relationGeneration.relationCountLimit)
        : null,
      user_guidance: relationGeneration.guidance.trim() || null,
      ...buildGenerationParamsPayload(relationGeneration.params),
    };

    const controller = new AbortController();
    generationAbortRef.current = controller;
    setGenerationState({ status: "generating", kind: "character_relations" });
    setActionError("");
    // 人物关系生成与角色生成共用同一瞬时交互：参数弹窗关闭，工作台接管运行状态。
    setRelationDialogOpen(false);

    try {
      let result: CharacterRelationsResultV1 | null = null;
      if (request.use_stream && relationStreamingSupported) {
        await apiPostSSE(
          "/api/llm/generate-character-relations/stream",
          request,
          (event, data) => {
            if (event === "progress") {
              // 与核心角色生成共用同一份进度状态，切换生成类型时会被重置
              const characters = Number(data.characters ?? 0);
              const chunkCount = Number(data.chunk_count ?? 0);
              setGenerationState((prev) =>
                prev.status === "generating"
                  ? { ...prev, progress: { characters, chunkCount } }
                  : prev,
              );
              return;
            }
            if (event === "error") {
              throw new Error(String(data.error || data.message || t("errors.generateFailed")));
            }
            if (event === "done") {
              result = extractStreamResult(data, isCharacterRelationsPayload);
            }
          },
          { signal: controller.signal },
        );
      } else {
        const response = await apiPost<unknown>(
          "/api/llm/generate-character-relations",
          request,
          { signal: controller.signal },
        );
        result = isCharacterRelationsPayload(response) ? response : null;
      }

      if (!result) throw new Error(t("errors.invalidRelationPreview"));
      const relations = structuredClone(result.relations);
      setGenerationState({
        status: "preview",
        kind: "character_relations",
        data: {
          relations,
          // 新生成的关系默认全部勾选；确认时仍只提交用户当前保留的候选。
          selected_relation_refs: relations.map((relation) => relation.relation_ref),
        },
        selectedIndex: 0,
        saving: false,
        error: "",
      });
    } catch (error) {
      if (!controller.signal.aborted) {
        setGenerationState({
          status: "error",
          kind: "character_relations",
          message: error instanceof Error ? error.message : String(error),
        });
      }
    } finally {
      if (generationAbortRef.current === controller) {
        generationAbortRef.current = null;
      }
    }
  };

  const handleSaveCharacter = async () => {
    if (!novelId || !isCompleteCharacterProfile(manualCharacter)) {
      const fieldErrors = Object.fromEntries(
        REQUIRED_PROFILE_TEXT_FIELDS
          .filter((field) => !manualCharacter[field].trim())
          .map((field) => [field, [t("errors.requiredField")]]),
      );
      setEditorError(t("errors.profileRequired"));
      setEditorFieldErrors(fieldErrors);
      focusRelationEditorError(fieldErrors);
      return;
    }
    setSubmittingManual(true);
    setEditorError("");
    setEditorFieldErrors({});
    try {
      if (editingCharacter) {
        const payload = buildCharacterUpdateRequest(editingCharacter, manualCharacter);
        await apiPut(
          `/api/characters/novel/${novelId}/${editingCharacter.character_id}`,
          payload,
        );
      } else {
        const payload = buildManualCharacterCreateRequest(manualCharacter, manualCharacterIsCore);
        const commandScope = "manual-character";
        const key = getStableCommandKey(commandCacheRef.current, commandScope, payload);
        await apiPost(`/api/characters/novel/${novelId}`, payload, {
          headers: { "Idempotency-Key": key },
        });
        commandCacheRef.current.delete(commandScope);
      }
      setManualCharacterOpen(false);
      setEditingCharacter(null);
      setManualCharacter(createEmptyCharacterProfile());
      setManualCharacterIsCore(false);
      await loadData();
    } catch (error) {
      const description = describeEditorError(error);
      setEditorError(description.message);
      setEditorFieldErrors(description.fieldErrors);
      focusRelationEditorError(description.fieldErrors);
    } finally {
      setSubmittingManual(false);
    }
  };

  const openManualRelation = () => {
    setManualRelation(createEmptyRelation(coreCharacters));
    setEditingRelation(null);
    setManualRelationOpen(true);
    setEditorError("");
    setEditorFieldErrors({});
  };

  const handleSaveRelation = async () => {
    if (!novelId || !isCompleteRelation(manualRelation)) {
      const fieldErrors: ApiFieldErrors = Object.fromEntries(
        REQUIRED_RELATION_TEXT_FIELDS
          .filter((field) => !manualRelation[field].trim())
          .map((field) => [field, [t("errors.requiredField")]]),
      );
      if (!manualRelation.source_character_id) fieldErrors.source_character_id = [t("errors.requiredField")];
      if (!manualRelation.target_character_id) fieldErrors.target_character_id = [t("errors.requiredField")];
      if (manualRelation.source_character_id === manualRelation.target_character_id) {
        fieldErrors.target_character_id = [t("errors.sameRelationEndpoint")];
      }
      if (manualRelation.intensity < 1 || manualRelation.intensity > 5) {
        fieldErrors.intensity = [t("errors.intensityRange")];
      }
      setEditorError(t("errors.relationRequired"));
      setEditorFieldErrors(fieldErrors);
      focusRelationEditorError(fieldErrors);
      return;
    }
    setSubmittingManual(true);
    setEditorError("");
    setEditorFieldErrors({});
    try {
      if (editingRelation) {
        const payload: CharacterRelationUpdateRequestV1 = {
          expected_version: editingRelation.version,
          relation_type: manualRelation.relation_type,
          current_state: manualRelation.current_state,
          core_conflict: manualRelation.core_conflict,
          hidden_tension: manualRelation.hidden_tension,
          possible_change: manualRelation.possible_change,
          story_value: manualRelation.story_value,
          intensity: manualRelation.intensity,
        };
        await apiPut(
          `/api/character-relations/novel/${novelId}/${editingRelation.relation_id}`,
          payload,
        );
      } else {
        const payload = { ...manualRelation, sort_order: 0 };
        const commandScope = "manual-relation";
        const key = getStableCommandKey(commandCacheRef.current, commandScope, payload);
        await apiPost(`/api/character-relations/novel/${novelId}`, payload, {
          headers: { "Idempotency-Key": key },
        });
        commandCacheRef.current.delete(commandScope);
      }
      setManualRelationOpen(false);
      setEditingRelation(null);
      await loadData();
    } catch (error) {
      const description = describeEditorError(error);
      setEditorError(description.message);
      setEditorFieldErrors(description.fieldErrors);
      focusRelationEditorError(description.fieldErrors);
    } finally {
      setSubmittingManual(false);
    }
  };

  const openCharacterEditor = (character: CharacterResponseV1) => {
    setEditingCharacter(character);
    setManualCharacter(characterProfileFromResponse(character));
    setManualCharacterIsCore(character.is_core_character);
    setEditorError("");
    setEditorFieldErrors({});
    setManualCharacterOpen(true);
  };

  const openRelationEditor = (relation: CharacterRelationResponseV1) => {
    setEditingRelation(relation);
    setManualRelation(characterRelationFromResponse(relation));
    setEditorError("");
    setEditorFieldErrors({});
    setManualRelationOpen(true);
  };

  const copyRelationAsNew = (relation: CharacterRelationResponseV1) => {
    // 复制流程刻意清除正式 ID 与版本；端点在创建态重新开放选择。
    setEditingRelation(null);
    setManualRelation(characterRelationFromResponse(relation));
    setEditorError("");
    setEditorFieldErrors({});
    setManualRelationOpen(true);
  };

  const handleToggleCharacterStatus = async (character: CharacterResponseV1) => {
    if (!novelId) return;
    setActionId(`character-status:${character.character_id}`);
    setActionError("");
    try {
      await apiPut(`/api/characters/novel/${novelId}/${character.character_id}/status`, {
        expected_version: character.version,
        status: character.status === "active" ? "inactive" : "active",
      });
      await loadData();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setActionId("");
    }
  };

  const handleToggleRelationActive = async (relation: CharacterRelationResponseV1) => {
    if (!novelId) return;
    setActionId(`relation-active:${relation.relation_id}`);
    setActionError("");
    try {
      await apiPut(`/api/character-relations/novel/${novelId}/${relation.relation_id}/active`, {
        expected_version: relation.version,
        is_active: !(relation.user_is_active ?? relation.is_active),
      });
      await loadData();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setActionId("");
    }
  };

  const handleRestoreCharacter = async (character: CharacterResponseV1) => {
    if (!novelId) return;
    setActionId(`restore-character:${character.character_id}`);
    setActionError("");
    try {
      await apiPost(`/api/characters/novel/${novelId}/${character.character_id}/restore`, {
        expected_version: character.version,
      });
      await loadData();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setActionId("");
    }
  };

  const handleRestoreRelation = async (relation: CharacterRelationResponseV1) => {
    if (!novelId) return;
    setActionId(`restore-relation:${relation.relation_id}`);
    setActionError("");
    try {
      await apiPost(`/api/character-relations/novel/${novelId}/${relation.relation_id}/restore`, {
        expected_version: relation.version,
      });
      await loadData();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setActionId("");
    }
  };

  const handleConfirmDelete = async () => {
    if (!deleteIntent || !novelId) return;
    const id = deleteIntent.kind === "character"
      ? deleteIntent.entity.character_id
      : deleteIntent.entity.relation_id;
    setActionId(`${deleteIntent.hard ? "hard" : "soft"}:${id}`);
    setEditorError("");
    try {
      if (deleteIntent.kind === "character") {
        await apiDelete(
          `/api/characters/novel/${novelId}/${id}${deleteIntent.hard ? "/hard" : ""}?expected_version=${deleteIntent.entity.version}`,
        );
      } else {
        await apiDelete(
          `/api/character-relations/novel/${novelId}/${id}${deleteIntent.hard ? "/hard" : ""}?expected_version=${deleteIntent.entity.version}`,
        );
      }
      setDeleteIntent(null);
      await loadData();
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : String(error));
    } finally {
      setActionId("");
    }
  };

  const handleConfirmPreview = async () => {
    if (!novelId || generationState.status !== "preview" || generationState.saving) return;
    const previewState = generationState;
    setActionError("");
    setGenerationState({ ...previewState, saving: true, error: "" });
    try {
      if (previewState.kind === "core_characters") {
        if (!previewState.data.core_characters.every(isCompleteCharacterProfile)) {
          throw new Error(t("errors.profileRequired"));
        }
        if (!previewState.data.binding_candidates.every(isCompleteBindingCandidate)) {
          throw new Error(t("errors.bindingRequired"));
        }
        await apiPost(
          `/api/characters/novel/${novelId}/bulk-core-with-bindings`,
          previewState.data,
        );
      } else {
        const selectedRelationRefs = new Set(previewState.data.selected_relation_refs);
        const selectedRelations = previewState.data.relations.filter((relation) =>
          selectedRelationRefs.has(relation.relation_ref),
        );
        if (selectedRelations.length === 0) {
          throw new Error(t("errors.selectRelation"));
        }
        if (!selectedRelations.every(isCompleteRelation)) {
          throw new Error(t("errors.relationRequired"));
        }
        const request: BulkAppendCharacterRelationsRequestV1 = {
          relations: selectedRelations,
        };
        await apiPost(
          `/api/character-relations/novel/${novelId}/bulk-append`,
          request,
        );
      }

      clearPreview();
      await loadData();
    } catch (error) {
      setGenerationState({
        ...previewState,
        saving: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  };

  const handleDiscardPreview = () => {
    clearPreview();
    setActionError("");
  };

  const openCoreGeneration = () => {
    if (generationState.status === "generating" || generationState.status === "preview") return;
    setActionError("");
    void loadGenerationCapabilities();
    setCoreDialogOpen(true);
  };

  const openRelationGeneration = () => {
    if (generationState.status === "generating" || generationState.status === "preview") return;
    const coreIds = coreCharacters.slice(0, 100).map((character) => character.character_id);
    setRelationGeneration({
      selectedCharacterIds: coreIds,
      allowIsolatedCharacters: false,
      relationCountLimit: "",
      guidance: "",
      params: DEFAULT_GENERATION_PARAMS,
    });
    setActionError("");
    void loadGenerationCapabilities();
    setRelationDialogOpen(true);
  };

  if (!canUseSavedNovel) {
    return <UnavailablePanel title={t("title")} message={t("createModeUnavailable")} />;
  }

  const generationLocked =
    generationState.status === "generating" || generationState.status === "preview";
  // 桌面主从视图占满内容区并把滚动交给左右窗格；预览和回收站仍由页面内容层滚动。
  const splitPaneActive =
    generationState.status !== "preview" && (view === "profiles" || view === "relations");

  return (
    <section className="flex h-full min-h-0 flex-col bg-background">
      <WorkspaceHeader
        view={view}
        characterCount={characters.length}
        relationCount={relations.length}
        trashCount={trashCharacters.length + trashRelations.length}
        loading={loading}
        onViewChange={setView}
        onRefresh={() => void loadData()}
        t={t}
      />

      {(dataError || actionError || generationError) && (
        <div className="mx-4 mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/35 dark:text-red-300" role="alert">
          {actionError || generationError || dataError}
        </div>
      )}

      {generationState.status === "generating" && (
        <div
          className="mx-4 mt-4 flex flex-col gap-3 rounded-xl border border-accent/25 bg-accent/5 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
          role="status"
          aria-live="polite"
        >
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <span
              className="mt-1 h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-accent"
              aria-hidden="true"
            />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-foreground">
                {generationState.kind === "core_characters"
                  ? t("generation.generatingCharacters")
                  : t("generation.generatingRelations")}
              </p>
              <p className="mt-1 text-xs leading-5 text-muted">
                {t("generation.generatingHint")}
              </p>
              <AIProgressBar
                className="mt-2"
                label={
                  generationState.progress && generationState.progress.characters > 0
                    ? t("generation.progressChars", {
                        count: generationState.progress.characters,
                      })
                    : undefined
                }
              />
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0 self-start sm:self-auto"
            onPress={handleCancelGeneration}
          >
            {t("generation.cancelGeneration")}
          </Button>
        </div>
      )}

      <div
        className={`workspace-scrollbar min-h-0 flex-1 px-4 py-5 sm:px-6 ${
          splitPaneActive
            ? "overflow-y-auto lg:overflow-hidden"
            : "overflow-y-auto"
        }`}
      >
        {generationState.status === "preview" ? (
          <CharacterGenerationPreview
            corePreview={generationState.kind === "core_characters" ? generationState.data : null}
            relationPreview={generationState.kind === "character_relations" ? generationState.data : null}
            selectedCoreIndex={generationState.kind === "core_characters" ? generationState.selectedIndex : 0}
            selectedRelationIndex={generationState.kind === "character_relations" ? generationState.selectedIndex : 0}
            characters={coreCharacters}
            factions={factions}
            saving={generationState.saving}
            onSelectCore={(selectedIndex) => {
              setGenerationState((current) =>
                current.status === "preview" && current.kind === "core_characters"
                  ? { ...current, selectedIndex }
                  : current,
              );
            }}
            onSelectRelation={(selectedIndex) => {
              setGenerationState((current) =>
                current.status === "preview" && current.kind === "character_relations"
                  ? { ...current, selectedIndex }
                  : current,
              );
            }}
            onCoreChange={(index, value) => {
              setGenerationState((current) => {
                if (current.status !== "preview" || current.kind !== "core_characters") {
                  return current;
                }
                const data = structuredClone(current.data);
                data.core_characters[index] = value;
                return { ...current, data, error: "" };
              });
            }}
            onBindingChange={(characterRef, value) => {
              setGenerationState((current) => {
                if (current.status !== "preview" || current.kind !== "core_characters") {
                  return current;
                }
                const data = structuredClone(current.data);
                const index = data.binding_candidates.findIndex((item) => item.character_ref === characterRef);
                if (index >= 0) {
                  data.binding_candidates[index] = value;
                } else {
                  // 容错补齐生成结果中缺失的“一角色一绑定候选”，确认时仍交由后端严格复核。
                  data.binding_candidates.push(value);
                }
                return { ...current, data, error: "" };
              });
            }}
            onRelationChange={(index, value) => {
              setGenerationState((current) => {
                if (current.status !== "preview" || current.kind !== "character_relations") {
                  return current;
                }
                const data = structuredClone(current.data);
                data.relations[index] = value;
                return { ...current, data, error: "" };
              });
            }}
            onToggleRelation={(relationRef) => {
              setGenerationState((current) => {
                if (current.status !== "preview" || current.kind !== "character_relations") {
                  return current;
                }
                const data = structuredClone(current.data);
                data.selected_relation_refs = data.selected_relation_refs.includes(relationRef)
                  ? data.selected_relation_refs.filter((item) => item !== relationRef)
                  : [...data.selected_relation_refs, relationRef];
                return { ...current, data, error: "" };
              });
            }}
            onConfirm={() => void handleConfirmPreview()}
            onDiscard={handleDiscardPreview}
            t={t}
          />
        ) : view === "profiles" ? (
          <ProfilesView
            characters={filteredCharacters}
            allCharacterCount={characters.length}
            search={search}
            roleFilter={roleFilter}
            importanceFilter={importanceFilter}
            bindingsByCharacter={bindingsByCharacter}
            factionNames={factionNames}
            relationCountByCharacter={relationCountByCharacter}
            loading={loading}
            generationLocked={generationLocked}
            actionId={actionId}
            onSearchChange={setSearch}
            onRoleFilterChange={setRoleFilter}
            onImportanceFilterChange={setImportanceFilter}
            onManualCreate={() => {
              setManualCharacter(createEmptyCharacterProfile());
              setEditingCharacter(null);
              setManualCharacterIsCore(false);
              setManualCharacterOpen(true);
              setEditorError("");
              setEditorFieldErrors({});
            }}
            onGenerate={openCoreGeneration}
            onEdit={openCharacterEditor}
            onToggleStatus={(character) => void handleToggleCharacterStatus(character)}
            onDelete={(character) => {
              setEditorError("");
              setDeleteIntent({ kind: "character", entity: character, hard: false });
            }}
            t={t}
          />
        ) : view === "relations" ? (
          <RelationsView
            relations={relations}
            coreCharacters={coreCharacters}
            characterNames={characterNames}
            loading={loading}
            generationLocked={generationLocked}
            actionId={actionId}
            onManualCreate={openManualRelation}
            onGenerate={openRelationGeneration}
            onEdit={openRelationEditor}
            onCopy={copyRelationAsNew}
            onToggleActive={(relation) => void handleToggleRelationActive(relation)}
            onDelete={(relation) => {
              setEditorError("");
              setDeleteIntent({ kind: "relation", entity: relation, hard: false });
            }}
            t={t}
          />
        ) : (
          <TrashView
            characters={trashCharacters}
            relations={trashRelations}
            actionId={actionId}
            onRestoreCharacter={(character) => void handleRestoreCharacter(character)}
            onHardDeleteCharacter={(character) => {
              setEditorError("");
              setDeleteIntent({ kind: "character", entity: character, hard: true });
            }}
            onRestoreRelation={(relation) => void handleRestoreRelation(relation)}
            onHardDeleteRelation={(relation) => {
              setEditorError("");
              setDeleteIntent({ kind: "relation", entity: relation, hard: true });
            }}
            t={t}
          />
        )}
      </div>

      {manualCharacterOpen && (
        <ModalLayer
          title={editingCharacter ? t("editor.editSavedCharacterTitle") : t("manualCharacter.title")}
          description={editingCharacter ? t("editor.savedCharacterDescription") : t("manualCharacter.description")}
          busy={submittingManual}
          onClose={() => {
            setManualCharacterOpen(false);
            setEditingCharacter(null);
          }}
          footer={(
            <>
              <Button variant="ghost" size="sm" onPress={() => setManualCharacterOpen(false)} isDisabled={submittingManual}>
                {t("actions.cancel")}
              </Button>
              <Button variant="primary" size="sm" onPress={() => void handleSaveCharacter()} isDisabled={submittingManual}>
                {submittingManual ? t("actions.saving") : editingCharacter ? t("actions.save") : t("actions.create")}
              </Button>
            </>
          )}
        >
          {editorError && <RelationEditorErrorSummary message={editorError} fieldErrors={editorFieldErrors} className="mb-5" />}
          <fieldset className="mb-6 border-b border-border pb-5" disabled={Boolean(editingCharacter) || submittingManual}>
            <legend className="text-sm font-semibold text-foreground">{t("manualCharacter.coreIdentity")}</legend>
            <div className="mt-3 grid grid-cols-2 gap-2 rounded-xl bg-surface-secondary/55 p-1" aria-label={t("manualCharacter.coreIdentity")}>
              {[false, true].map((isCore) => (
                <button key={String(isCore)} type="button" aria-pressed={manualCharacterIsCore === isCore} onClick={() => setManualCharacterIsCore(isCore)} className={`min-h-10 rounded-lg px-3 text-sm font-medium transition-colors ${manualCharacterIsCore === isCore ? "bg-background text-foreground shadow-sm" : "text-muted"}`}>
                  {isCore ? t("status.core") : t("status.nonCore")}
                </button>
              ))}
            </div>
            <p className="mt-2 text-xs leading-5 text-muted">{editingCharacter ? t("manualCharacter.coreImmutableHint") : t("manualCharacter.coreHint")}</p>
          </fieldset>
          <CharacterProfileForm value={manualCharacter} onChange={setManualCharacter} disabled={submittingManual} t={t} fieldErrors={editorFieldErrors} />
        </ModalLayer>
      )}

      {manualRelationOpen && (
        <ModalLayer
          title={editingRelation ? t("editor.editSavedRelationTitle") : t("manualRelation.title")}
          description={editingRelation ? t("editor.savedRelationDescription") : t("manualRelation.description")}
          busy={submittingManual}
          onClose={() => {
            setManualRelationOpen(false);
            setEditingRelation(null);
          }}
          footer={(
            <>
              <Button variant="ghost" size="sm" onPress={() => setManualRelationOpen(false)} isDisabled={submittingManual}>
                {t("actions.cancel")}
              </Button>
              <Button variant="primary" size="sm" onPress={() => void handleSaveRelation()} isDisabled={submittingManual}>
                {submittingManual ? t("actions.saving") : editingRelation ? t("actions.save") : t("actions.create")}
              </Button>
            </>
          )}
        >
          {editorError && <RelationEditorErrorSummary message={editorError} fieldErrors={editorFieldErrors} className="mb-5" />}
          <CharacterRelationEditor
            value={manualRelation}
            characters={editingRelation ? characters : coreCharacters}
            onChange={setManualRelation}
            disabled={submittingManual}
            endpointsReadOnly={Boolean(editingRelation)}
            fieldErrors={editorFieldErrors}
            t={t}
          />
        </ModalLayer>
      )}

      {deleteIntent && (
        <ConfirmActionModal
          title={deleteIntent.hard ? t("confirm.hardDeleteTitle") : t("confirm.deleteTitle")}
          message={(
            <>
              <p>{deleteIntent.hard ? t("confirm.hardDeleteDescription") : t("confirm.deleteDescription")}</p>
              <p className="mt-3 font-medium text-foreground">{deleteIntent.kind === "character" ? deleteIntent.entity.name : `${deleteIntent.entity.source_character_name || deleteIntent.entity.source_character_id} → ${deleteIntent.entity.target_character_name || deleteIntent.entity.target_character_id}`}</p>
            </>
          )}
          confirmText={deleteIntent.hard ? t("actions.hardDelete") : t("actions.delete")}
          cancelText={t("actions.cancel")}
          loadingText={t("actions.processing")}
          danger
          error={editorError}
          isLoading={Boolean(actionId)}
          onCancel={() => setDeleteIntent(null)}
          onConfirm={() => void handleConfirmDelete()}
        />
      )}

      {coreDialogOpen && (
        <CoreGenerationDialog
          value={coreGeneration}
          paramsOpen={coreGenerationParamsOpen}
          onChange={setCoreGeneration}
          onParamsOpenChange={setCoreGenerationParamsOpen}
          onCancel={() => setCoreDialogOpen(false)}
          onSubmit={() => void handleGenerateCoreCharacters()}
          t={t}
        />
      )}

      {relationDialogOpen && (
        <RelationGenerationDialog
          value={relationGeneration}
          characters={coreCharacters}
          paramsOpen={relationGenerationParamsOpen}
          onChange={setRelationGeneration}
          onParamsOpenChange={setRelationGenerationParamsOpen}
          onCancel={() => setRelationDialogOpen(false)}
          onSubmit={() => void handleGenerateRelations()}
          t={t}
        />
      )}
    </section>
  );
}
