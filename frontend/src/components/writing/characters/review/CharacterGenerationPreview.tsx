"use client";

import { Button } from "@heroui/react";
import type { useTranslations } from "next-intl";
import type { CoreFaction } from "@/types/novel";
import type {
  CharacterFactionBindingCandidateV1,
  CharacterFactionMembershipType,
  CharacterRelationCandidateV1,
  CharacterRelationsReviewProjectionV1,
  CharacterResponseV1,
  CoreCharacterCandidateV1,
  CoreCharactersResultV1,
} from "@/types/character";
import CharacterProfileForm from "../forms/CharacterProfileForm";
import CharacterRelationEditor from "../forms/CharacterRelationEditor";

const INPUT_CLASS_NAME =
  "min-h-11 w-full rounded-xl border border-border bg-background/80 px-3.5 py-2.5 text-sm text-foreground outline-none transition-[border-color,box-shadow] focus:border-accent focus:ring-2 focus:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-60";

const TEXTAREA_CLASS_NAME = `${INPUT_CLASS_NAME} min-h-24 resize-y leading-6`;

const MEMBERSHIP_TYPES: readonly CharacterFactionMembershipType[] = [
  "primary",
  "secondary",
  "covert",
];

/** 本地角色生成预览公开属性。 */
export interface CharacterGenerationPreviewProps {
  corePreview: CoreCharactersResultV1 | null;
  relationPreview: CharacterRelationsReviewProjectionV1 | null;
  selectedCoreIndex: number;
  selectedRelationIndex: number;
  characters: CharacterResponseV1[];
  factions: CoreFaction[];
  saving: boolean;
  onSelectCore: (index: number) => void;
  onSelectRelation: (index: number) => void;
  onCoreChange: (index: number, value: CoreCharacterCandidateV1) => void;
  onBindingChange: (
    characterRef: string,
    value: CharacterFactionBindingCandidateV1
  ) => void;
  onRelationChange: (index: number, value: CharacterRelationCandidateV1) => void;
  onToggleRelation: (relationRef: string) => void;
  onConfirm: () => void;
  onDiscard: () => void;
  t: ReturnType<typeof useTranslations>;
}

/**
 * 功能：限制候选索引处于当前候选数组范围内。
 * Args:
 *   index: 外部保存的当前候选索引。
 *   length: 当前候选数量。
 * Returns:
 *   可安全读取数组的索引；空数组返回 -1。
 */
function clampCandidateIndex(index: number, length: number): number {
  if (length === 0) return -1;
  return Math.min(Math.max(index, 0), length - 1);
}

/**
 * 功能：为缺失的角色势力绑定构造符合“未绑定”约束的本地候选。
 * Args:
 *   characterRef: 核心角色候选的稳定局部引用。
 * Returns:
 *   除 character_ref 外全部为 null 的绑定候选。
 */
function createUnboundCandidate(
  characterRef: string
): CharacterFactionBindingCandidateV1 {
  return {
    character_ref: characterRef,
    faction_id: null,
    membership_type: null,
    role_title: null,
    public_status: null,
    loyalty_level: null,
    reason: null,
  };
}

/**
 * 功能：筛出拥有正式 faction_id、可供角色候选绑定的核心势力。
 * Args:
 *   factions: 当前小说的核心势力列表。
 * Returns:
 *   faction_id 已收窄为非空字符串的势力列表。
 */
function getBindableFactions(
  factions: CoreFaction[]
): Array<CoreFaction & { faction_id: string }> {
  return factions.filter(
    (faction): faction is CoreFaction & { faction_id: string } =>
      typeof faction.faction_id === "string" && faction.faction_id.length > 0
  );
}

/**
 * 功能：渲染核心角色或人物关系的一次性本地生成预览。
 * Args:
 *   props: 候选预览、正式引用数据、交互状态和确认或放弃回调。
 * Returns:
 *   支持候选切换、白名单编辑、关系勾选与一次确认写入的预览界面。
 */
export default function CharacterGenerationPreview({
  corePreview,
  relationPreview,
  selectedCoreIndex,
  selectedRelationIndex,
  characters,
  factions,
  saving,
  onSelectCore,
  onSelectRelation,
  onCoreChange,
  onBindingChange,
  onRelationChange,
  onToggleRelation,
  onConfirm,
  onDiscard,
  t,
}: CharacterGenerationPreviewProps) {
  const isCorePreview = corePreview !== null;
  const title = isCorePreview
    ? t("candidate.characterTitle")
    : t("candidate.relationTitle");
  const meta = corePreview
    ? t("candidate.characterMeta", {
        characters: corePreview.core_characters.length,
        bindings: corePreview.binding_candidates.length,
      })
    : t("candidate.relationMeta", {
        relations: relationPreview?.relations.length ?? 0,
        selected: relationPreview?.selected_relation_refs.length ?? 0,
      });

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-5">
      <header className="rounded-2xl border border-border bg-surface/70 p-4 shadow-sm sm:p-5">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">
          {t("candidate.title")}
        </p>
        <h2 className="mt-1 text-xl font-semibold text-foreground">{title}</h2>
        <p className="mt-1 text-sm leading-6 text-muted">{meta}</p>
      </header>

      {corePreview ? (
        <CoreCandidateReview
          preview={corePreview}
          selectedIndex={selectedCoreIndex}
          factions={factions}
          disabled={saving}
          onSelect={onSelectCore}
          onCoreChange={onCoreChange}
          onBindingChange={onBindingChange}
          t={t}
        />
      ) : relationPreview ? (
        <RelationCandidateReview
          preview={relationPreview}
          selectedIndex={selectedRelationIndex}
          characters={characters}
          disabled={saving}
          onSelect={onSelectRelation}
          onRelationChange={onRelationChange}
          onToggleRelation={onToggleRelation}
          t={t}
        />
      ) : null}

      <footer className="sticky bottom-0 z-10 flex flex-col-reverse gap-2 rounded-2xl border border-border bg-surface/95 p-3 shadow-lg backdrop-blur sm:flex-row sm:items-center sm:justify-end">
        <Button variant="ghost" size="sm" onPress={onDiscard} isDisabled={saving}>
          {t("candidate.discard")}
        </Button>
        <Button
          variant="primary"
          size="sm"
          onPress={onConfirm}
          isDisabled={saving}
        >
          {saving
            ? t("candidate.confirming")
            : isCorePreview
              ? t("candidate.confirmCharacters")
              : t("candidate.confirmRelations")}
        </Button>
      </footer>
    </div>
  );
}

interface CoreCandidateReviewProps {
  preview: CoreCharactersResultV1;
  selectedIndex: number;
  factions: CoreFaction[];
  disabled: boolean;
  onSelect: (index: number) => void;
  onCoreChange: (index: number, value: CoreCharacterCandidateV1) => void;
  onBindingChange: (
    characterRef: string,
    value: CharacterFactionBindingCandidateV1
  ) => void;
  t: ReturnType<typeof useTranslations>;
}

/**
 * 功能：渲染核心角色候选列表、角色档案表单和同角色势力绑定编辑器。
 * Args:
 *   props: 核心候选预览、势力数据、选择状态和编辑回调。
 * Returns:
 *   左侧候选导航与右侧角色详情编辑布局。
 */
function CoreCandidateReview({
  preview,
  selectedIndex,
  factions,
  disabled,
  onSelect,
  onCoreChange,
  onBindingChange,
  t,
}: CoreCandidateReviewProps) {
  const activeIndex = clampCandidateIndex(selectedIndex, preview.core_characters.length);
  const activeCandidate = preview.core_characters[activeIndex];
  if (!activeCandidate) {
    return (
      <p className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted">
        {t("emptyCharacters")}
      </p>
    );
  }

  const activeBinding =
    preview.binding_candidates.find(
      (binding) => binding.character_ref === activeCandidate.character_ref
    ) ?? createUnboundCandidate(activeCandidate.character_ref);

  return (
    <div className="grid min-h-[620px] gap-5 xl:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="self-start rounded-2xl border border-border bg-surface/65 p-3 xl:sticky xl:top-0">
        <h3 className="px-2 pb-3 text-sm font-semibold text-foreground">
          {t("candidate.chooseCharacter")}
        </h3>
        <div className="space-y-2">
          {preview.core_characters.map((candidate, index) => {
            const selected = index === activeIndex;
            return (
              <button
                key={candidate.character_ref}
                type="button"
                onClick={() => onSelect(index)}
                aria-current={selected ? "true" : undefined}
                className={`w-full rounded-xl border px-3 py-3 text-left transition-colors ${
                  selected
                    ? "border-accent bg-accent/10"
                    : "border-border bg-background/65 hover:border-accent/45"
                }`}
              >
                <span className="block truncate text-sm font-semibold text-foreground">
                  {candidate.name || t("card.untitled")}
                </span>
                <span className="mt-1 block truncate font-mono text-xs text-muted">
                  {candidate.character_ref}
                </span>
                <span className="mt-2 block text-xs text-muted">
                  {t(`options.roleType.${candidate.role_type}`)} ·{" "}
                  {t(`options.importanceLevel.${candidate.importance_level}`)}
                </span>
              </button>
            );
          })}
        </div>
      </aside>

      <div className="min-w-0 space-y-5">
        <section className="rounded-2xl border border-border bg-surface/55 p-4 sm:p-5">
          <div className="mb-5 border-b border-border pb-4">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">
              {t("editor.characterEyebrow")}
            </p>
            <h3 className="mt-1 text-lg font-semibold text-foreground">
              {t("editor.editCandidateTitle")}
            </h3>
            <p className="mt-1 text-sm leading-6 text-muted">
              {t("editor.characterDescription")}
            </p>
          </div>
          <CharacterProfileForm
            value={activeCandidate}
            characterRef={activeCandidate.character_ref}
            disabled={disabled}
            onChange={(value) =>
              onCoreChange(activeIndex, {
                ...value,
                character_ref: activeCandidate.character_ref,
              })
            }
            t={t}
          />
        </section>

        <FactionBindingCandidateEditor
          value={activeBinding}
          factions={factions}
          disabled={disabled}
          onChange={(value) => onBindingChange(activeCandidate.character_ref, value)}
          t={t}
        />
      </div>
    </div>
  );
}

interface FactionBindingCandidateEditorProps {
  value: CharacterFactionBindingCandidateV1;
  factions: CoreFaction[];
  disabled: boolean;
  onChange: (value: CharacterFactionBindingCandidateV1) => void;
  t: ReturnType<typeof useTranslations>;
}

/**
 * 功能：编辑核心角色候选对应的可空势力绑定。
 * Args:
 *   props: 当前绑定、可选势力、禁用状态、更新回调和国际化函数。
 * Returns:
 *   遵守后端“无势力时其余字段全部为 null”规则的绑定表单。
 */
function FactionBindingCandidateEditor({
  value,
  factions,
  disabled,
  onChange,
  t,
}: FactionBindingCandidateEditorProps) {
  const bindableFactions = getBindableFactions(factions);

  /**
   * 功能：切换绑定势力并同步建立或清空整组可空业务字段。
   * Args:
   *   factionId: 新选择的正式势力 ID；空字符串表示不绑定。
   * Returns:
   *   无；通过 onChange 提交新的完整绑定候选。
   */
  const changeFaction = (factionId: string): void => {
    if (!factionId) {
      // 后端要求无势力候选不能残留任何半条绑定信息。
      onChange(createUnboundCandidate(value.character_ref));
      return;
    }
    onChange({
      ...value,
      faction_id: factionId,
      membership_type: value.membership_type ?? "primary",
      role_title: value.role_title,
      public_status: value.public_status ?? "",
      loyalty_level: value.loyalty_level ?? 3,
      reason: value.reason ?? "",
    });
  };

  /**
   * 功能：更新绑定候选的单个白名单字段。
   * Args:
   *   field: 待更新的绑定字段名。
   *   nextValue: 与字段类型匹配的新值。
   * Returns:
   *   无；通过 onChange 提交派生后的完整候选。
   */
  const updateField = <K extends keyof CharacterFactionBindingCandidateV1>(
    field: K,
    nextValue: CharacterFactionBindingCandidateV1[K]
  ): void => {
    onChange({ ...value, [field]: nextValue });
  };

  return (
    <section className="rounded-2xl border border-border bg-surface/55 p-4 sm:p-5">
      <div className="mb-5 border-b border-border pb-4">
        <h3 className="text-base font-semibold text-foreground">{t("binding.title")}</h3>
        <p className="mt-1 text-sm leading-6 text-muted">{t("binding.description")}</p>
      </div>

      <div className="grid gap-5 md:grid-cols-2">
        <FieldShell label={t("binding.faction")} className="md:col-span-2">
          <select
            value={value.faction_id ?? ""}
            onChange={(event) => changeFaction(event.target.value)}
            disabled={disabled}
            className={INPUT_CLASS_NAME}
          >
            <option value="">{t("binding.unbound")}</option>
            {bindableFactions.map((faction) => (
              <option key={faction.faction_id} value={faction.faction_id}>
                {faction.name}
              </option>
            ))}
          </select>
        </FieldShell>

        {value.faction_id !== null && (
          <>
            <FieldShell label={t("binding.membershipType")}>
              <select
                value={value.membership_type ?? "primary"}
                onChange={(event) =>
                  updateField(
                    "membership_type",
                    event.target.value as CharacterFactionMembershipType
                  )
                }
                disabled={disabled}
                className={INPUT_CLASS_NAME}
                required
              >
                {MEMBERSHIP_TYPES.map((membershipType) => (
                  <option key={membershipType} value={membershipType}>
                    {t(`options.membershipType.${membershipType}`)}
                  </option>
                ))}
              </select>
            </FieldShell>

            <FieldShell label={t("binding.roleTitle")}>
              <input
                type="text"
                value={value.role_title ?? ""}
                onChange={(event) =>
                  updateField("role_title", event.target.value || null)
                }
                placeholder={t("placeholders.roleTitle")}
                maxLength={100}
                disabled={disabled}
                className={INPUT_CLASS_NAME}
              />
            </FieldShell>

            <FieldShell label={t("binding.publicStatus")}>
              <input
                type="text"
                value={value.public_status ?? ""}
                onChange={(event) =>
                  updateField("public_status", event.target.value || null)
                }
                placeholder={t("placeholders.publicStatus")}
                maxLength={200}
                disabled={disabled}
                className={INPUT_CLASS_NAME}
                required
              />
            </FieldShell>

            <FieldShell label={t("binding.loyaltyLevel")}>
              <input
                type="number"
                min={1}
                max={5}
                step={1}
                value={value.loyalty_level ?? ""}
                onChange={(event) =>
                  updateField(
                    "loyalty_level",
                    event.target.value ? Number(event.target.value) : null
                  )
                }
                disabled={disabled}
                className={INPUT_CLASS_NAME}
                required
              />
            </FieldShell>

            <FieldShell label={t("binding.notes")} className="md:col-span-2">
              <textarea
                value={value.reason ?? ""}
                onChange={(event) => updateField("reason", event.target.value || null)}
                placeholder={t("placeholders.notes")}
                maxLength={1000}
                disabled={disabled}
                rows={3}
                className={TEXTAREA_CLASS_NAME}
                required
              />
            </FieldShell>
          </>
        )}
      </div>
    </section>
  );
}

interface RelationCandidateReviewProps {
  preview: CharacterRelationsReviewProjectionV1;
  selectedIndex: number;
  characters: CharacterResponseV1[];
  disabled: boolean;
  onSelect: (index: number) => void;
  onRelationChange: (index: number, value: CharacterRelationCandidateV1) => void;
  onToggleRelation: (relationRef: string) => void;
  t: ReturnType<typeof useTranslations>;
}

/**
 * 功能：渲染关系候选选择列表及当前关系编辑器。
 * Args:
 *   props: 关系审阅投影、正式角色、选择状态和编辑回调。
 * Returns:
 *   支持切换候选、勾选提交范围和编辑关系白名单字段的布局。
 */
function RelationCandidateReview({
  preview,
  selectedIndex,
  characters,
  disabled,
  onSelect,
  onRelationChange,
  onToggleRelation,
  t,
}: RelationCandidateReviewProps) {
  const activeIndex = clampCandidateIndex(selectedIndex, preview.relations.length);
  const activeRelation = preview.relations[activeIndex];
  const characterNames = new Map(
    characters.map((character) => [character.character_id, character.name])
  );

  if (!activeRelation) {
    return (
      <p className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted">
        {t("emptyRelations")}
      </p>
    );
  }

  return (
    <div className="grid min-h-[620px] gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
      <aside className="self-start rounded-2xl border border-border bg-surface/65 p-3 xl:sticky xl:top-0">
        <h3 className="px-2 pb-3 text-sm font-semibold text-foreground">
          {t("candidate.chooseRelation")}
        </h3>
        <div className="space-y-2">
          {preview.relations.map((relation, index) => {
            const selectedForCommit = preview.selected_relation_refs.includes(
              relation.relation_ref
            );
            const selectedForEdit = index === activeIndex;
            const sourceName =
              characterNames.get(relation.source_character_id) ??
              relation.source_character_id;
            const targetName =
              characterNames.get(relation.target_character_id) ??
              relation.target_character_id;

            return (
              <article
                key={relation.relation_ref}
                className={`rounded-xl border p-3 transition-colors ${
                  selectedForEdit
                    ? "border-accent bg-accent/10"
                    : "border-border bg-background/65"
                }`}
              >
                <label className="flex cursor-pointer items-center gap-2 text-xs font-medium text-muted">
                  <input
                    type="checkbox"
                    checked={selectedForCommit}
                    onChange={() => {
                      // 勾选提交范围时同步切换右侧编辑对象，减少候选错位。
                      onSelect(index);
                      onToggleRelation(relation.relation_ref);
                    }}
                    disabled={disabled}
                    className="size-4 rounded border-border accent-[var(--color-accent)]"
                  />
                  {t("candidate.selected")}
                </label>
                <button
                  type="button"
                  onClick={() => onSelect(index)}
                  aria-current={selectedForEdit ? "true" : undefined}
                  className="mt-2 w-full text-left"
                >
                  <span className="block text-sm font-semibold leading-6 text-foreground">
                    {sourceName} → {targetName}
                  </span>
                  <span className="mt-1 block text-xs text-muted">
                    {t(`options.relationType.${relation.relation_type}`)} ·{" "}
                    {t("fields.intensity")}: {relation.intensity}/5
                  </span>
                  <span className="mt-1 block truncate font-mono text-xs text-muted">
                    {relation.relation_ref}
                  </span>
                </button>
              </article>
            );
          })}
        </div>
      </aside>

      <section className="min-w-0 rounded-2xl border border-border bg-surface/55 p-4 sm:p-5">
        <div className="mb-5 border-b border-border pb-4">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">
            {t("editor.relationEyebrow")}
          </p>
          <h3 className="mt-1 text-lg font-semibold text-foreground">
            {t("editor.editRelationCandidateTitle")}
          </h3>
          <p className="mt-1 text-sm leading-6 text-muted">
            {t("editor.relationDescription")}
          </p>
        </div>
        <CharacterRelationEditor
          value={activeRelation}
          characters={characters}
          relationRef={activeRelation.relation_ref}
          disabled={disabled}
          onChange={(value) =>
            onRelationChange(activeIndex, {
              ...value,
              relation_ref: activeRelation.relation_ref,
              is_active: true,
            })
          }
          t={t}
        />
      </section>
    </div>
  );
}

interface FieldShellProps {
  label: string;
  children: React.ReactNode;
  className?: string;
}

/**
 * 功能：为势力绑定控件提供统一标签、间距和可点击区域。
 * Args:
 *   props: 字段标签、控件节点和可选布局类名。
 * Returns:
 *   可直接包裹原生表单控件的标签节点。
 */
function FieldShell({ label, children, className = "" }: FieldShellProps) {
  return (
    <label className={`grid gap-2 text-sm ${className}`}>
      <span className="text-xs font-semibold tracking-wide text-muted">{label}</span>
      {children}
    </label>
  );
}
