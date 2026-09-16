"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "@heroui/react";
import { AISection, AIWarnings } from "@/components/writing/ai/AIShared";
import BlueprintCardsStep from "@/components/novel-generation/BlueprintCardsStep";
import BlueprintOverviewStep from "@/components/novel-generation/BlueprintOverviewStep";
import BlueprintReview from "@/components/novel-generation/BlueprintReview";
import OutlineReview from "@/components/novel-generation/OutlineReview";
import PowerSystemEditor from "@/components/novel-generation/PowerSystemEditor";
import RelationshipGraphEditor from "@/components/novel-generation/RelationshipGraphEditor";
import { apiGet } from "@/lib/api";
import {
  confirmBlueprint,
  generateNovelChapters,
  generateNovelOutline,
  confirmNovelOutline,
  prepareBlueprint,
} from "@/lib/chapterAiApi";
import {
  listCharacterRelations,
  listFactionRelations,
  type FactionRelationResponse,
} from "@/lib/relationApi";
import type {
  GeneratedChapterCandidate,
  GenerateChaptersResponse,
  GenerateOutlineResponse,
} from "@/lib/chapterAiApi";
import type { CharacterRelationResponseV1 } from "@/types/character";
import {
  BLUEPRINT_STEPS,
  BLUEPRINT_STEP_LABEL,
  blueprintPowerSystem,
  createEmptyBlueprint,
  normalizeBlueprint,
  normalizeOutline,
  type BlueprintConflict,
  type BlueprintStepKey,
  type BlueprintSuggestion,
  type GenerationMode,
  type NovelBlueprint,
  type NovelOutline,
} from "@/types/novelBlueprint";
import { createEmptyOutline } from "@/types/novelBlueprint";
import type { PowerSystem } from "@/types/powerSystem";
/** 蓝图向导使用的设定卡选项，字段与设定卡接口返回保持一致。 */
interface SettingCardOption {
  card_id: string;
  type: string;
  name: string;
  current_state?: string;
  is_hard_rule?: boolean;
}

interface NovelBlueprintWizardProps {
  novelId?: string;
  initialMode: GenerationMode;
  onCancel: () => void;
  onConfirmed: (blueprint: NovelBlueprint) => void;
  /** 已有蓝图时传入，用于刷新后继续编辑。 */
  initialBlueprint?: NovelBlueprint | null;
  /** 是否包含蓝图确认之后的大纲与分章生成流程。 */
  withOutlineFlow?: boolean;
}

interface CharacterOption {
  character_id: string;
  name: string;
  core_position?: string;
}

interface FactionOption {
  faction_id: string;
  name: string;
}

function newRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * 功能：创作蓝图七步向导。
 *
 * 依次整理基本信息、剧情世界观、角色势力、地点物品规则、战力体系、关系图并预览确认。
 * AI 补全结果只作为候选：剧情与世界观需要用户点击「采纳」才写入，
 * 战力体系候选需用户显式应用，未确认的蓝图不会成为硬规则。
 */
export default function NovelBlueprintWizard({
  novelId,
  initialMode,
  onCancel,
  onConfirmed,
  initialBlueprint = null,
  withOutlineFlow = false,
}: NovelBlueprintWizardProps) {
  const [step, setStep] = useState<BlueprintStepKey>("basic");
  const [blueprint, setBlueprint] = useState<NovelBlueprint>(
    () => normalizeBlueprint(initialBlueprint ?? createEmptyBlueprint(initialMode), createEmptyBlueprint(initialMode))
  );
  const [targetChapterCount, setTargetChapterCount] = useState(100);
  const [targetWordCount, setTargetWordCount] = useState(3000);

  const [characters, setCharacters] = useState<CharacterOption[]>([]);
  const [factions, setFactions] = useState<FactionOption[]>([]);
  const [settingCards, setSettingCards] = useState<SettingCardOption[]>([]);
  const [relations, setRelations] = useState<CharacterRelationResponseV1[]>([]);
  const [factionRelations, setFactionRelations] = useState<FactionRelationResponse[]>([]);

  const [preparing, setPreparing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [hint, setHint] = useState<string | null>(null);
  const [powerSuggestion, setPowerSuggestion] = useState<string | null>(null);

  // 大纲与分章生成（创作设定中心流程）
  const [outline, setOutline] = useState<NovelOutline>(createEmptyOutline());
  const [outlineGenerationId, setOutlineGenerationId] = useState<string | null>(null);
  const [generatingOutline, setGeneratingOutline] = useState(false);
  const [generatingChapters, setGeneratingChapters] = useState(false);
  const [chapterCandidates, setChapterCandidates] = useState<GeneratedChapterCandidate[]>([]);

  const confirmed = blueprint.status === "confirmed";
  const readOnly = confirmed;

  const loadEntities = useCallback(async () => {
    if (!novelId) return;
    const [characterRes, factionRes, cardRes, relationRes, factionRelationRes] =
      await Promise.allSettled([
        apiGet<{ data: CharacterOption[] }>(`/api/characters/novel/${novelId}`),
        apiGet<{ data: FactionOption[] }>(`/api/factions/novel/${novelId}/level/core`),
        apiGet<SettingCardOption[]>(`/api/setting-cards/${novelId}`),
        listCharacterRelations(novelId),
        listFactionRelations(novelId),
      ]);
    if (characterRes.status === "fulfilled") setCharacters(characterRes.value.data ?? []);
    if (factionRes.status === "fulfilled") setFactions(factionRes.value.data ?? []);
    if (cardRes.status === "fulfilled") setSettingCards(cardRes.value ?? []);
    if (relationRes.status === "fulfilled") setRelations(relationRes.value.data ?? []);
    if (factionRelationRes.status === "fulfilled") {
      setFactionRelations(factionRelationRes.value.data ?? []);
    }
  }, [novelId]);

  useEffect(() => {
    void loadEntities().catch(() => undefined);
  }, [loadEntities]);

  const patchBlueprint = useCallback(
    (change: Partial<NovelBlueprint>) => setBlueprint((prev) => ({ ...prev, ...change })),
    []
  );

  const suggestions = useMemo(() => blueprint.ai_suggestions ?? [], [blueprint.ai_suggestions]);

  const handlePrepareBlueprint = async () => {
    setPreparing(true);
    setError(null);
    setWarnings([]);
    try {
      const res = await prepareBlueprint({
        novel_id: novelId ?? null,
        generation_mode: initialMode,
        plot_summary: blueprint.plot_summary,
        worldview: blueprint.worldview,
        power_system: blueprint.power_system,
        character_ids: blueprint.selected_character_ids,
        faction_ids: blueprint.selected_faction_ids,
        setting_card_ids: blueprint.selected_setting_card_ids,
        relation_ids: blueprint.selected_relation_ids,
        request_id: newRequestId(),
      });
      const incoming = (res.data?.suggestions ?? []) as BlueprintSuggestion[];
      const conflicts = (res.data?.conflicts ?? []) as BlueprintConflict[];
      const existingKeys = new Set(
        suggestions.map((item) => `${item.target}:${item.title}:${item.content}`)
      );
      const merged = [
        ...suggestions,
        ...incoming
          .filter((item) => !existingKeys.has(`${item.target}:${item.title}:${item.content}`))
          .map((item, index) => ({
            ...item,
            suggestion_id: item.suggestion_id || `sug-${res.generation_id}-${index}`,
          })),
      ];
      setBlueprint((prev) => ({
        ...prev,
        blueprint_id: res.blueprint_id || prev.blueprint_id,
        version: res.version || prev.version,
        status: res.status ?? prev.status,
        ai_suggestions: merged,
        conflicts,
        questions: res.data?.questions ?? prev.questions,
      }));
      setWarnings(res.warnings ?? []);
      if (res.data?.power_system) {
        setPowerSuggestion("AI 已生成战力体系候选，可在「战力体系」步骤查看并应用");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI 整理创作蓝图失败");
    } finally {
      setPreparing(false);
    }
  };

  const applySuggestionToField = (suggestion: BlueprintSuggestion) => {
    setBlueprint((prev) => {
      const next: NovelBlueprint = {
        ...prev,
        ai_suggestions: prev.ai_suggestions.map((item) =>
          item.suggestion_id === suggestion.suggestion_id ? { ...item, accepted: true } : item
        ),
      };
      const target = suggestion.target ?? "";
      const append = (base: string) =>
        base.trim() ? `${base.trim()}\n${suggestion.content}` : suggestion.content;
      if (target === "plot_summary") {
        next.plot_summary = append(prev.plot_summary);
      } else if (target === "worldview") {
        next.worldview = append(prev.worldview);
      } else if (target.startsWith("power_system")) {
        setPowerSuggestion(suggestion.content);
      } else {
        next.questions = [...prev.questions, `${suggestion.title}：${suggestion.content}`];
      }
      return next;
    });
  };

  const handleRejectSuggestion = (suggestion: BlueprintSuggestion) => {
    setBlueprint((prev) => ({
      ...prev,
      ai_suggestions: prev.ai_suggestions.map((item) =>
        item.suggestion_id === suggestion.suggestion_id ? { ...item, rejected: true } : item
      ),
    }));
  };

  const handleToggleSuggestion = (suggestion: BlueprintSuggestion, accepted: boolean) => {
    setBlueprint((prev) => ({
      ...prev,
      ai_suggestions: prev.ai_suggestions.map((item) =>
        item.suggestion_id === suggestion.suggestion_id
          ? { ...item, accepted, rejected: !accepted }
          : item
      ),
    }));
  };

  const handleIgnoreConflict = (conflict: BlueprintConflict) => {
    setBlueprint((prev) => ({
      ...prev,
      conflicts: prev.conflicts.map((item) =>
        item.conflict_id === conflict.conflict_id ? { ...item, ignored: true } : item
      ),
    }));
  };

  const handlePowerChange = (value: PowerSystem) => patchBlueprint({ power_system: value });

  const handleApplyPowerSuggestion = () => {
    if (!powerSuggestion) return;
    let parsed: PowerSystem | null = null;
    try {
      parsed = JSON.parse(powerSuggestion) as PowerSystem;
    } catch {
      parsed = null;
    }
    if (parsed && Array.isArray(parsed.levels)) {
      handlePowerChange(parsed);
    } else {
      handlePowerChange({
        ...blueprintPowerSystem(blueprint),
        description: [blueprintPowerSystem(blueprint).description, powerSuggestion]
          .filter(Boolean)
          .join("\n"),
      });
    }
    setPowerSuggestion(null);
    setHint("已应用 AI 战力体系候选，可继续修改后再确认");
  };

  const handleSaveBlueprint = async (doConfirm: boolean) => {
    setError(null);
    if (doConfirm) setConfirming(true);
    else setSaving(true);
    try {
      if (!novelId) {
        // 新建小说场景下还没有 novel_id，交给父组件在小说创建时提交蓝图。
        if (doConfirm) {
          onConfirmed({ ...blueprint, status: "pending_confirmation" });
        } else {
          setHint("草稿已在本地暂存，创建小说后会自动提交");
        }
        return;
      }
      const res = await confirmBlueprint(novelId, blueprint.blueprint_id, {
        expected_version: blueprint.version || 1,
        plot_summary: blueprint.plot_summary,
        worldview: blueprint.worldview,
        power_system: blueprint.power_system,
        selected_character_ids: blueprint.selected_character_ids,
        selected_faction_ids: blueprint.selected_faction_ids,
        selected_setting_card_ids: blueprint.selected_setting_card_ids,
        selected_relation_ids: blueprint.selected_relation_ids,
        confirm: doConfirm,
        generation_mode: initialMode,
        request_id: newRequestId(),
      });
      const next = normalizeBlueprint({ ...blueprint, ...res }, blueprint);
      setBlueprint(next);
      setHint(doConfirm ? `蓝图已确认（v${next.version}）` : `草稿已保存（v${next.version}）`);
      if (doConfirm) onConfirmed(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : doConfirm ? "确认蓝图失败" : "保存草稿失败");
    } finally {
      setConfirming(false);
      setSaving(false);
    }
  };

  // -------------------------------------------------------------------------
  // 大纲与分章生成（仅在 withOutlineFlow 时启用）
  // -------------------------------------------------------------------------

  const handleGenerateOutline = async () => {
    if (!novelId || !blueprint.blueprint_id) return;
    setGeneratingOutline(true);
    setError(null);
    try {
      const res: GenerateOutlineResponse = await generateNovelOutline(novelId, {
        blueprint_id: blueprint.blueprint_id,
        blueprint_version: blueprint.version,
        target_chapter_count: targetChapterCount,
        target_word_count: targetWordCount,
        request_id: newRequestId(),
      });
      setOutlineGenerationId(res.generation_id);
      setOutline(
        normalizeOutline({
          volumes: res.volumes ?? [],
          version: 1,
          generation_id: res.generation_id,
          blueprint_version: res.blueprint_version,
        })
      );
      setWarnings(res.warnings ?? []);
      setStep("review");
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成大纲失败");
    } finally {
      setGeneratingOutline(false);
    }
  };

  const handleConfirmOutline = async (doConfirm: boolean) => {
    if (!novelId || !outlineGenerationId) return;
    setSaving(!doConfirm);
    setConfirming(doConfirm);
    setError(null);
    try {
      const res = await confirmNovelOutline(novelId, outlineGenerationId, {
        outline_version: outline.version || 1,
        outline: { volumes: outline.volumes },
        confirm: doConfirm,
        blueprint_id: blueprint.blueprint_id,
        blueprint_version: blueprint.version,
        request_id: newRequestId(),
      });
      setOutline((prev) => ({
        ...prev,
        volumes: res.volumes ?? prev.volumes,
        version: res.outline_version ?? prev.version,
        confirmed: doConfirm,
      }));
      setHint(doConfirm ? "大纲已确认，卷章已创建" : "大纲草稿已保存为新版本");
    } catch (err) {
      setError(err instanceof Error ? err.message : "确认大纲失败");
    } finally {
      setSaving(false);
      setConfirming(false);
    }
  };

  const handleGenerateChapters = async (chapterIds: string[]) => {
    if (!novelId || !outlineGenerationId) return;
    setGeneratingChapters(true);
    setError(null);
    try {
      const res: GenerateChaptersResponse = await generateNovelChapters(novelId, {
        outline_generation_id: outlineGenerationId,
        outline_version: outline.version || 1,
        chapter_ids: chapterIds,
        start_index: 0,
        count: chapterIds.length,
        blueprint_version: blueprint.version,
        request_id: newRequestId(),
        allow_overwrite: false,
      });
      setChapterCandidates(res.chapters ?? []);
      setWarnings(res.warnings ?? []);
      setHint(
        `已生成 ${res.chapters?.length ?? 0} 章候选正文，需逐章确认后才会写入正式章节`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成章节正文失败");
    } finally {
      setGeneratingChapters(false);
    }
  };

  const stepIndex = BLUEPRINT_STEPS.indexOf(step);
  const goStep = (next: BlueprintStepKey) => setStep(next);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {BLUEPRINT_STEPS.map((key, index) => (
          <button
            key={key}
            type="button"
            onClick={() => goStep(key)}
            className={`rounded-full border px-3 py-1 text-xs ${
              key === step
                ? "border-accent bg-accent/10 text-foreground"
                : index < stepIndex
                  ? "border-border bg-surface text-muted"
                  : "border-border bg-surface-secondary text-muted"
            }`}
          >
            {index + 1}. {BLUEPRINT_STEP_LABEL[key]}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
          {error}
        </div>
      )}
      {hint && (
        <div className="rounded-lg border border-border bg-surface-secondary px-3 py-2 text-xs text-muted">
          {hint}
        </div>
      )}
      <AIWarnings warnings={warnings} />

      {step === "basic" && (
        <BlueprintOverviewStep
          blueprint={blueprint}
          onChange={(next) => setBlueprint(next)}
          suggestions={suggestions}
          onAcceptSuggestion={applySuggestionToField}
          onRejectSuggestion={handleRejectSuggestion}
          onGenerateSuggestions={handlePrepareBlueprint}
          targetChapterCount={targetChapterCount}
          targetWordCount={targetWordCount}
          onChangeTarget={(chapterCount, wordCount) => {
            setTargetChapterCount(chapterCount);
            setTargetWordCount(wordCount);
          }}
          readOnly={readOnly}
        />
      )}

      {step === "entities" && (
        <BlueprintCardsStep
          mode="entities"
          characters={characters}
          factions={factions}
          settingCards={settingCards}
          selectedCharacterIds={blueprint.selected_character_ids}
          selectedFactionIds={blueprint.selected_faction_ids}
          selectedCardIds={blueprint.selected_setting_card_ids}
          onChangeSelection={({ characterIds, factionIds }) =>
            patchBlueprint({
              selected_character_ids: characterIds ?? blueprint.selected_character_ids,
              selected_faction_ids: factionIds ?? blueprint.selected_faction_ids,
            })
          }
          onRefresh={() => void loadEntities()}
          readOnly={readOnly}
        />
      )}

      {step === "cards" && (
        <BlueprintCardsStep
          mode="cards"
          characters={characters}
          factions={factions}
          settingCards={settingCards}
          selectedCharacterIds={blueprint.selected_character_ids}
          selectedFactionIds={blueprint.selected_faction_ids}
          selectedCardIds={blueprint.selected_setting_card_ids}
          onChangeSelection={({ cardIds }) =>
            patchBlueprint({ selected_setting_card_ids: cardIds ?? blueprint.selected_setting_card_ids })
          }
          onRefresh={() => void loadEntities()}
          readOnly={readOnly}
        />
      )}

      {step === "power" && (
        <div className="space-y-3">
          {powerSuggestion && (
            <div className="space-y-2 rounded-xl border border-accent/40 bg-accent/5 p-3">
              <div className="text-xs font-medium text-foreground">AI 战力体系候选</div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-2 text-xs text-foreground">
                {powerSuggestion}
              </pre>
              <div className="flex gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  onPress={handleApplyPowerSuggestion}
                  isDisabled={readOnly}
                >
                  应用到战力体系
                </Button>
                <Button variant="ghost" size="sm" onPress={() => setPowerSuggestion(null)}>
                  忽略
                </Button>
              </div>
            </div>
          )}
          <PowerSystemEditor
            value={blueprintPowerSystem(blueprint)}
            onChange={handlePowerChange}
            onGenerateSuggestion={handlePrepareBlueprint}
            suggestionHint={preparing ? "AI 正在整理战力体系候选…" : undefined}
            readOnly={readOnly}
          />
        </div>
      )}

      {step === "relations" && (
        <RelationshipGraphEditor
          novelId={novelId ?? null}
          relations={relations}
          characters={characters}
          factionRelations={factionRelations}
          selectedRelationIds={blueprint.selected_relation_ids}
          onChangeRelations={setRelations}
          onChangeSelectedRelationIds={(ids) => patchBlueprint({ selected_relation_ids: ids })}
          onError={(message) => setError(message)}
          readOnly={readOnly}
        />
      )}

      {step === "review" && (
        <div className="space-y-4">
          <BlueprintReview
            blueprint={blueprint}
            suggestions={suggestions}
            onToggleSuggestion={handleToggleSuggestion}
            onIgnoreConflict={handleIgnoreConflict}
            onSaveDraft={() => void handleSaveBlueprint(false)}
            onConfirm={() => void handleSaveBlueprint(true)}
            onGenerateOutline={withOutlineFlow ? handleGenerateOutline : undefined}
            saving={saving}
            confirming={confirming}
            readOnly={readOnly}
            hint={readOnly ? "蓝图已确认，如需修改请创建新版本" : undefined}
          />

          {withOutlineFlow && (
            <AISection title="卷章大纲">
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onPress={handleGenerateOutline}
                    isDisabled={generatingOutline || blueprint.status !== "confirmed"}
                  >
                    {generatingOutline ? "生成中…" : "生成卷章大纲"}
                  </Button>
                  {blueprint.status !== "confirmed" && (
                    <span className="text-xs text-muted">需先确认蓝图才能生成大纲</span>
                  )}
                </div>
                {outline.volumes.length > 0 && (
                  <OutlineReview
                    outline={outline}
                    onChange={setOutline}
                    onConfirm={handleConfirmOutline}
                    onGenerateChapters={handleGenerateChapters}
                    saving={saving}
                    confirming={confirming}
                    readOnly={false}
                  />
                )}
              </div>
            </AISection>
          )}

          {chapterCandidates.length > 0 && (
            <AISection title={`分章正文候选（${chapterCandidates.length}）`}>
              <div className="space-y-2">
                <p className="text-xs text-muted">
                  候选正文不会自动写入正式章节，需逐章确认后保存
                </p>
                {generatingChapters && <p className="text-xs text-muted">生成中…</p>}
                {chapterCandidates.map((chapter, index) => (
                  <div
                    key={`${chapter.chapter_id ?? "candidate"}-${index}`}
                    className="rounded-lg border border-border bg-surface p-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-foreground">
                        {chapter.title || `第 ${index + 1} 章`}
                      </span>
                      <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-xs text-muted">
                        {chapter.status}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-4 whitespace-pre-wrap text-xs text-muted">
                      {chapter.content}
                    </p>
                  </div>
                ))}
              </div>
            </AISection>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button variant="ghost" size="sm" onPress={onCancel}>
          取消
        </Button>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onPress={() => goStep(BLUEPRINT_STEPS[Math.max(0, stepIndex - 1)])}
            isDisabled={stepIndex === 0}
          >
            上一步
          </Button>
          {stepIndex < BLUEPRINT_STEPS.length - 1 ? (
            <Button
              variant="primary"
              size="sm"
              onPress={() => goStep(BLUEPRINT_STEPS[Math.min(BLUEPRINT_STEPS.length - 1, stepIndex + 1)])}
            >
              下一步
            </Button>
          ) : (
            !readOnly && (
              <Button
                variant="primary"
                size="sm"
                onPress={() => void handleSaveBlueprint(true)}
                isDisabled={confirming}
              >
                {confirming ? "确认中…" : "确认设定并生成"}
              </Button>
            )
          )}
        </div>
      </div>
    </div>
  );
}
