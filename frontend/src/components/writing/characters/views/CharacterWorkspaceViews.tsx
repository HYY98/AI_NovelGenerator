"use client";

import { useState, type ReactNode } from "react";
import { Button, Modal } from "@heroui/react";
import type { useTranslations } from "next-intl";
import type {
  CharacterFactionBindingResponseV1,
  CharacterImportanceLevel,
  CharacterRelationResponseV1,
  CharacterResponseV1,
  CharacterRoleType,
} from "@/types/character";

type CharactersTranslator = ReturnType<typeof useTranslations>;

/** 角色工作台当前展示的数据视图。 */
export type CharacterWorkspaceView = "profiles" | "relations" | "trash";

export interface UnavailablePanelProps {
  title: string;
  message: string;
}

export interface WorkspaceHeaderProps {
  view: CharacterWorkspaceView;
  characterCount: number;
  relationCount: number;
  trashCount: number;
  loading: boolean;
  onViewChange: (view: CharacterWorkspaceView) => void;
  onRefresh: () => void;
  t: CharactersTranslator;
}

export interface ProfilesViewProps {
  characters: CharacterResponseV1[];
  allCharacterCount: number;
  search: string;
  roleFilter: "all" | CharacterRoleType;
  importanceFilter: "all" | CharacterImportanceLevel;
  bindingsByCharacter: ReadonlyMap<string, CharacterFactionBindingResponseV1[]>;
  factionNames: ReadonlyMap<string, string>;
  relationCountByCharacter: ReadonlyMap<string, number>;
  loading: boolean;
  generationLocked: boolean;
  actionId: string;
  onSearchChange: (value: string) => void;
  onRoleFilterChange: (value: "all" | CharacterRoleType) => void;
  onImportanceFilterChange: (value: "all" | CharacterImportanceLevel) => void;
  onManualCreate: () => void;
  onGenerate: () => void;
  onEdit: (character: CharacterResponseV1) => void;
  onToggleStatus: (character: CharacterResponseV1) => void;
  onDelete: (character: CharacterResponseV1) => void;
  t: CharactersTranslator;
}

export interface RelationsViewProps {
  relations: CharacterRelationResponseV1[];
  coreCharacters: CharacterResponseV1[];
  characterNames: ReadonlyMap<string, string>;
  loading: boolean;
  generationLocked: boolean;
  actionId: string;
  onManualCreate: () => void;
  onGenerate: () => void;
  onEdit: (relation: CharacterRelationResponseV1) => void;
  onCopy: (relation: CharacterRelationResponseV1) => void;
  onToggleActive: (relation: CharacterRelationResponseV1) => void;
  onDelete: (relation: CharacterRelationResponseV1) => void;
  t: CharactersTranslator;
}

export interface TrashViewProps {
  characters: CharacterResponseV1[];
  relations: CharacterRelationResponseV1[];
  actionId: string;
  onRestoreCharacter: (character: CharacterResponseV1) => void;
  onHardDeleteCharacter: (character: CharacterResponseV1) => void;
  onRestoreRelation: (relation: CharacterRelationResponseV1) => void;
  onHardDeleteRelation: (relation: CharacterRelationResponseV1) => void;
  t: CharactersTranslator;
}

export interface ModalLayerProps {
  title: string;
  description: string;
  onClose: () => void;
  footer: ReactNode;
  children: ReactNode;
  dismissable?: boolean;
  busy?: boolean;
}

const ROLE_TYPES: CharacterRoleType[] = [
  "protagonist",
  "deuteragonist",
  "antagonist",
  "supporting",
  "minor",
];

const IMPORTANCE_LEVELS: CharacterImportanceLevel[] = [
  "core",
  "major",
  "supporting",
  "background",
];

const FILTER_CLASS =
  "min-h-10 rounded-xl border border-border bg-background/80 px-3 text-sm text-foreground outline-none transition-[border-color,box-shadow] placeholder:text-muted/55 focus:border-accent focus:ring-2 focus:ring-accent/15";

/**
 * 功能：渲染小说未保存时的角色功能不可用提示。
 * Args:
 *   props: 提示标题与说明文案。
 * Returns:
 *   居中展示的不可用状态 React 节点。
 */
export function UnavailablePanel({ title, message }: UnavailablePanelProps) {
  return (
    <section className="grid h-full min-h-72 place-items-center bg-background px-5 py-10">
      <div className="w-full max-w-xl border-y border-border py-10 text-center">
        <span className="text-2xl text-accent" aria-hidden="true">◇</span>
        <h2 className="mt-3 text-xl font-semibold text-foreground">{title}</h2>
        <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted">{message}</p>
      </div>
    </section>
  );
}

/**
 * 功能：渲染工作台标题、计数、视图切换和刷新动作。
 * Args:
 *   props: 当前视图、各领域计数、加载状态与交互回调。
 * Returns:
 *   角色工作台的页头 React 节点。
 */
export function WorkspaceHeader({
  view,
  characterCount,
  relationCount,
  trashCount,
  loading,
  onViewChange,
  onRefresh,
  t,
}: WorkspaceHeaderProps) {
  const tabs: Array<{ value: CharacterWorkspaceView; label: string; count: number }> = [
    { value: "profiles", label: t("tabs.profiles"), count: characterCount },
    { value: "relations", label: t("tabs.relations"), count: relationCount },
    { value: "trash", label: t("tabs.trash"), count: trashCount },
  ];

  return (
    <header className="shrink-0 border-b border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold uppercase tracking-[0.18em] text-accent">{t("workspaceEyebrow")}</p>
          <h1 className="sr-only">{t("title")}</h1>
        </div>
        <Button className="shrink-0" variant="ghost" size="sm" onPress={onRefresh} isDisabled={loading}>
          {loading ? t("loading") : t("refresh")}
        </Button>
      </div>
      <div className="flex gap-5 overflow-x-auto px-4 sm:px-6" role="tablist" aria-label={t("title")}>
        {tabs.map((tab) => (
          <button
            key={tab.value}
            type="button"
            role="tab"
            aria-selected={view === tab.value}
            onClick={() => onViewChange(tab.value)}
            className={`flex min-h-11 shrink-0 items-center gap-2 border-b-2 px-1 text-sm font-medium transition-colors ${
              view === tab.value
                ? "border-accent text-accent"
                : "border-transparent text-muted hover:border-border hover:text-foreground"
            }`}
          >
            {tab.label}
            <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-[11px] tabular-nums text-muted">{tab.count}</span>
          </button>
        ))}
      </div>
    </header>
  );
}

/**
 * 功能：渲染角色名册与单一档案详情组成的主从工作台。
 * Args:
 *   props: 角色列表、筛选状态、势力绑定投影与档案操作回调。
 * Returns:
 *   可响应桌面、平板和移动端布局的档案工作台 React 节点。
 */
export function ProfilesView({
  characters,
  allCharacterCount,
  search,
  roleFilter,
  importanceFilter,
  bindingsByCharacter,
  factionNames,
  relationCountByCharacter,
  loading,
  generationLocked,
  actionId,
  onSearchChange,
  onRoleFilterChange,
  onImportanceFilterChange,
  onManualCreate,
  onGenerate,
  onEdit,
  onToggleStatus,
  onDelete,
  t,
}: ProfilesViewProps) {
  const [selectedId, setSelectedId] = useState("");
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [rosterCollapsed, setRosterCollapsed] = useState(false);
  const selected = characters.find((item) => item.character_id === selectedId) ?? characters[0];
  const filtersActive = Boolean(search) || roleFilter !== "all" || importanceFilter !== "all";

  // lg 以上让主从区域继承工作台剩余高度，窄屏继续采用自然单列滚动。
  return (
    <div className="flex h-full min-h-0 flex-col gap-5">
      <section className="shrink-0 border-b border-border pb-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="grid flex-1 gap-3 lg:grid-cols-[minmax(220px,1fr)_180px_180px]">
            <label className="grid gap-1.5 text-xs font-semibold tracking-wide text-muted">
              <span>{t("filters.searchLabel")}</span>
              <input type="search" value={search} onChange={(event) => onSearchChange(event.target.value)} placeholder={t("filters.searchPlaceholder")} className={FILTER_CLASS} />
            </label>
            <label className="grid gap-1.5 text-xs font-semibold tracking-wide text-muted">
              <span>{t("filters.roleType")}</span>
              <select value={roleFilter} onChange={(event) => onRoleFilterChange(event.target.value as "all" | CharacterRoleType)} className={FILTER_CLASS}>
                <option value="all">{t("filters.allRoleTypes")}</option>
                {ROLE_TYPES.map((item) => <option key={item} value={item}>{t(`options.roleType.${item}`)}</option>)}
              </select>
            </label>
            <label className="grid gap-1.5 text-xs font-semibold tracking-wide text-muted">
              <span>{t("filters.importanceLevel")}</span>
              <select value={importanceFilter} onChange={(event) => onImportanceFilterChange(event.target.value as "all" | CharacterImportanceLevel)} className={FILTER_CLASS}>
                <option value="all">{t("filters.allImportanceLevels")}</option>
                {IMPORTANCE_LEVELS.map((item) => <option key={item} value={item}>{t(`options.importanceLevel.${item}`)}</option>)}
              </select>
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" className="hidden sm:inline-flex lg:hidden" onPress={() => setRosterCollapsed((value) => !value)}>{rosterCollapsed ? t("actions.showRoster") : t("actions.hideRoster")}</Button>
            {filtersActive && <Button variant="ghost" size="sm" onPress={() => { onSearchChange(""); onRoleFilterChange("all"); onImportanceFilterChange("all"); }}>{t("filters.clear")}</Button>}
            <Button variant="outline" size="sm" onPress={onManualCreate} isDisabled={loading}>{t("manualCreateCharacter")}</Button>
            <Button variant="primary" size="sm" onPress={onGenerate} isDisabled={loading || generationLocked}>{generationLocked ? t("generating") : t("generateCharacters")}</Button>
          </div>
        </div>
      </section>

      {loading && allCharacterCount === 0 ? <LoadingPanel label={t("loading")} /> : !selected ? (
        <EmptyPanel title={filtersActive ? t("filters.noResults") : t("emptyCharacters")} description={filtersActive ? undefined : t("emptyCharactersHint")} />
      ) : (
        <div className="grid min-h-[520px] flex-1 overflow-hidden border-y border-border bg-surface/35 lg:min-h-0 lg:grid-cols-[minmax(240px,0.34fr)_minmax(0,1fr)]">
          <aside
            className={`${mobileDetailOpen ? "hidden sm:block" : "block"} ${rosterCollapsed ? "sm:hidden lg:block" : ""} workspace-scrollbar max-h-[55vh] overflow-y-auto border-b border-border outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/30 lg:min-h-0 lg:max-h-none lg:border-b-0 lg:border-r`}
            aria-label={t("tabs.profiles")}
            tabIndex={0}
          >
            <ul className="divide-y divide-border">
              {characters.map((character) => (
                <li key={character.character_id}>
                  <button type="button" onClick={() => { setSelectedId(character.character_id); setMobileDetailOpen(true); }} className={`w-full px-4 py-4 text-left transition-colors ${selected?.character_id === character.character_id ? "bg-accent/10" : "hover:bg-surface-secondary/55"}`}>
                    <span className="flex items-center justify-between gap-3">
                      <span className="truncate text-sm font-semibold text-foreground">{character.name || t("card.untitled")}</span>
                      <span className={`size-2 shrink-0 rounded-full ${character.status === "active" ? "bg-emerald-500" : "bg-muted/45"}`} />
                    </span>
                    <span className="mt-1 block truncate text-xs text-muted">{t(`options.roleType.${character.role_type}`)} · {t(`options.importanceLevel.${character.importance_level}`)}</span>
                    <span className="mt-2 block text-[11px] tabular-nums text-muted">{t("relationCount", { count: relationCountByCharacter.get(character.character_id) ?? 0 })}</span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>
          <div
            className={`${mobileDetailOpen ? "block" : "hidden sm:block"} workspace-scrollbar min-w-0 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/30 lg:min-h-0 lg:overflow-y-auto`}
            role="region"
            aria-label={`${t("tabs.profiles")} · ${selected.name}`}
            tabIndex={0}
          >
            <button type="button" className="mx-5 mt-4 text-sm font-medium text-accent sm:hidden" onClick={() => setMobileDetailOpen(false)}>← {t("actions.backToRoster")}</button>
            <CharacterDetail character={selected} bindings={bindingsByCharacter.get(selected.character_id) ?? []} factionNames={factionNames} busy={actionId.includes(selected.character_id)} onEdit={() => onEdit(selected)} onToggleStatus={() => onToggleStatus(selected)} onDelete={() => onDelete(selected)} t={t} />
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * 功能：渲染关系边清单与单一关系详情组成的主从工作台。
 * Args:
 *   props: 正式关系、端点名称投影、生成状态与关系操作回调。
 * Returns:
 *   关系清单和详情编辑入口组成的响应式 React 节点。
 */
export function RelationsView({
  relations,
  coreCharacters,
  characterNames,
  loading,
  generationLocked,
  actionId,
  onManualCreate,
  onGenerate,
  onEdit,
  onCopy,
  onToggleActive,
  onDelete,
  t,
}: RelationsViewProps) {
  const [selectedId, setSelectedId] = useState("");
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [rosterCollapsed, setRosterCollapsed] = useState(false);
  const selected = relations.find((item) => item.relation_id === selectedId) ?? relations[0];
  const names = (relation: CharacterRelationResponseV1) => ({
    source: relation.source_character_name || characterNames.get(relation.source_character_id) || relation.source_character_id,
    target: relation.target_character_name || characterNames.get(relation.target_character_id) || relation.target_character_id,
  });
  const insufficientCharacters = coreCharacters.length < 2;

  // 与角色档案共用同一滚动边界，详情滚动不会带动左侧关系边清单。
  return (
    <div className="flex h-full min-h-0 flex-col gap-5">
      <section className="flex shrink-0 flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-foreground">{t("tabs.relations")}</h2>
          <p className="mt-1 text-sm text-muted">{t("relationWorkspaceHint")}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" size="sm" className="hidden sm:inline-flex lg:hidden" onPress={() => setRosterCollapsed((value) => !value)}>{rosterCollapsed ? t("actions.showRoster") : t("actions.hideRoster")}</Button>
          <Button variant="outline" size="sm" onPress={onManualCreate} isDisabled={loading || insufficientCharacters}>{t("manualCreateRelation")}</Button>
          <Button variant="primary" size="sm" onPress={onGenerate} isDisabled={loading || generationLocked || insufficientCharacters}>{generationLocked ? t("generating") : t("generateRelations")}</Button>
        </div>
        {insufficientCharacters && <p className="text-sm text-amber-700 dark:text-amber-300">{t("needTwoCoreCharacters")}</p>}
      </section>

      {loading && relations.length === 0 ? <LoadingPanel label={t("loading")} /> : !selected ? (
        <EmptyPanel title={t("emptyRelations")} description={t("emptyRelationsHint")} />
      ) : (
        <div className="grid min-h-[500px] flex-1 overflow-hidden border-y border-border bg-surface/35 lg:min-h-0 lg:grid-cols-[minmax(260px,0.38fr)_minmax(0,1fr)]">
          <aside
            className={`${mobileDetailOpen ? "hidden sm:block" : "block"} ${rosterCollapsed ? "sm:hidden lg:block" : ""} workspace-scrollbar max-h-[55vh] overflow-y-auto border-b border-border outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/30 lg:min-h-0 lg:max-h-none lg:border-b-0 lg:border-r`}
            aria-label={t("tabs.relations")}
            tabIndex={0}
          >
            <ul className="divide-y divide-border">
              {relations.map((relation) => {
                const relationNames = names(relation);
                return (
                  <li key={relation.relation_id}>
                    <button type="button" onClick={() => { setSelectedId(relation.relation_id); setMobileDetailOpen(true); }} className={`w-full px-4 py-4 text-left transition-colors ${selected.relation_id === relation.relation_id ? "bg-accent/10" : "hover:bg-surface-secondary/55"}`}>
                      <span className="block truncate text-sm font-semibold text-foreground">{relationNames.source} <span className="text-accent">→</span> {relationNames.target}</span>
                      <span className="mt-1 block text-xs text-muted">{t(`options.relationType.${relation.relation_type}`)} · {relation.intensity}/5</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </aside>
          <div
            className={`${mobileDetailOpen ? "block" : "hidden sm:block"} workspace-scrollbar min-w-0 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/30 lg:min-h-0 lg:overflow-y-auto`}
            role="region"
            aria-label={`${t("tabs.relations")} · ${names(selected).source} → ${names(selected).target}`}
            tabIndex={0}
          >
            <button type="button" className="mx-5 mt-4 text-sm font-medium text-accent sm:hidden" onClick={() => setMobileDetailOpen(false)}>← {t("actions.backToRoster")}</button>
            <RelationDetail relation={selected} names={names(selected)} busy={actionId.includes(selected.relation_id)} onEdit={() => onEdit(selected)} onCopy={() => onCopy(selected)} onToggleActive={() => onToggleActive(selected)} onDelete={() => onDelete(selected)} t={t} />
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * 功能：渲染角色与人物关系回收站，并只在此处暴露永久删除。
 * Args:
 *   props: 已软删实体、当前动作标识以及恢复和永久删除回调。
 * Returns:
 *   按角色和关系分区展示的回收站 React 节点。
 */
export function TrashView({ characters, relations, actionId, onRestoreCharacter, onHardDeleteCharacter, onRestoreRelation, onHardDeleteRelation, t }: TrashViewProps) {
  return (
    <div className="space-y-8">
      <TrashSection title={t("trash.characters")} empty={characters.length === 0} emptyText={t("trash.emptyCharacters")}>
        {characters.map((character) => (
          <TrashRow key={character.character_id} title={character.name} meta={character.character_id} busy={actionId.includes(character.character_id)} onRestore={() => onRestoreCharacter(character)} onHardDelete={() => onHardDeleteCharacter(character)} t={t} />
        ))}
      </TrashSection>
      <TrashSection title={t("trash.relations")} empty={relations.length === 0} emptyText={t("trash.emptyRelations")}>
        {relations.map((relation) => (
          <TrashRow key={relation.relation_id} title={`${relation.source_character_name || relation.source_character_id} → ${relation.target_character_name || relation.target_character_id}`} meta={relation.relation_id} busy={actionId.includes(relation.relation_id)} onRestore={() => onRestoreRelation(relation)} onHardDelete={() => onHardDeleteRelation(relation)} t={t} />
        ))}
      </TrashSection>
    </div>
  );
}

/**
 * 功能：渲染 HeroUI v3 模态容器，统一焦点圈定、Escape、滚动锁和焦点恢复。
 * Args:
 *   props: 弹窗标题、说明、正文、底部动作、关闭回调、可关闭策略与保存状态。
 * Returns:
 *   移动端近全屏、桌面端限高的统一模态 React 节点。
 */
export function ModalLayer({ title, description, onClose, footer, children, dismissable = true, busy = false }: ModalLayerProps) {
  return (
      <Modal.Backdrop
        isOpen
        onOpenChange={(isOpen) => !isOpen && !busy && onClose()}
        variant="blur"
        isDismissable={dismissable && !busy}
        isKeyboardDismissDisabled={busy}
      >
        <Modal.Container size="full" scroll="inside" className="p-0 sm:p-4">
          <Modal.Dialog className="h-[100dvh] w-full sm:h-auto sm:max-h-[min(90vh,960px)] sm:max-w-5xl">
            <Modal.Header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4 sm:px-6">
              <div>
                <Modal.Heading className="text-lg font-semibold text-foreground">{title}</Modal.Heading>
                <p className="mt-1 text-sm leading-6 text-muted">{description}</p>
              </div>
              <Modal.CloseTrigger aria-label={title} isDisabled={busy} />
            </Modal.Header>
            <Modal.Body className="min-h-0 px-5 py-5 sm:px-6">{children}</Modal.Body>
            <Modal.Footer className="sticky bottom-0 flex justify-end gap-2 border-t border-border bg-surface px-5 py-4 sm:px-6">{footer}</Modal.Footer>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
  );
}

function CharacterDetail({ character, bindings, factionNames, busy, onEdit, onToggleStatus, onDelete, t }: { character: CharacterResponseV1; bindings: CharacterFactionBindingResponseV1[]; factionNames: ReadonlyMap<string, string>; busy: boolean; onEdit: () => void; onToggleStatus: () => void; onDelete: () => void; t: CharactersTranslator }) {
  const sections = [
    { title: t("sections.basic"), values: [[t("fields.identity"), character.identity], [t("fields.appearance"), character.appearance], [t("fields.personality"), character.personality]] },
    { title: t("sections.motivation"), values: [[t("fields.coreDesire"), character.core_desire], [t("fields.coreFear"), character.core_fear], [t("fields.conflictWithMainline"), character.conflict_with_mainline], [t("fields.relationshipWithProtagonist"), character.relationship_with_protagonist]] },
    { title: t("sections.story"), values: [[t("fields.initialState"), character.initial_state], [t("fields.growthDirection"), character.growth_direction], [t("fields.storyFunction"), character.story_function], [t("fields.arcSeed"), character.arc_seed]] },
  ];
  const activeBindings = bindings.filter((item) => item.is_active && !item.is_deleted);
  return (
    <article className="min-w-0 px-5 py-6 sm:px-7">
      <header className="flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap gap-2"><StatusPill active={character.is_core_character}>{character.is_core_character ? t("status.core") : t("status.nonCore")}</StatusPill><StatusPill active={character.status === "active"}>{character.status === "active" ? t("status.active") : t("status.inactive")}</StatusPill></div>
          <h2 className="mt-3 text-2xl font-semibold text-foreground">{character.name}</h2>
          <p className="mt-1 text-sm text-muted">{character.aliases.join(" · ") || character.character_id}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onPress={onEdit} isDisabled={busy}>{t("actions.edit")}</Button>
          <Button size="sm" variant="ghost" onPress={onToggleStatus} isDisabled={busy}>{character.status === "active" ? t("actions.deactivate") : t("actions.activate")}</Button>
          <Button size="sm" variant="ghost" className="text-danger" onPress={onDelete} isDisabled={busy}>{t("actions.delete")}</Button>
        </div>
      </header>
      {sections.map((section) => (
        <section key={section.title} className="border-b border-border py-6 last:border-b-0">
          <h3 className="text-sm font-semibold uppercase tracking-[0.12em] text-accent">{section.title}</h3>
          <dl className="mt-4 grid gap-x-8 gap-y-5 md:grid-cols-2">
            {section.values.map(([label, value]) => <SummaryField key={label} label={label} value={value} />)}
          </dl>
        </section>
      ))}
      <section className="pt-5">
        <h3 className="text-sm font-semibold text-foreground">{t("binding.title")}</h3>
        <p className="mt-2 text-sm leading-6 text-muted">{activeBindings.length ? activeBindings.map((item) => `${factionNames.get(item.faction_id) ?? item.faction_id}${item.role_title ? ` · ${item.role_title}` : ""}`).join("；") : t("binding.unbound")}</p>
      </section>
    </article>
  );
}

function RelationDetail({ relation, names, busy, onEdit, onCopy, onToggleActive, onDelete, t }: { relation: CharacterRelationResponseV1; names: { source: string; target: string }; busy: boolean; onEdit: () => void; onCopy: () => void; onToggleActive: () => void; onDelete: () => void; t: CharactersTranslator }) {
  const narrative = [[t("fields.currentState"), relation.current_state], [t("fields.coreConflict"), relation.core_conflict], [t("fields.hiddenTension"), relation.hidden_tension], [t("fields.possibleChange"), relation.possible_change], [t("fields.storyValue"), relation.story_value]];
  return (
    <article className="min-w-0 px-5 py-6 sm:px-7">
      <header className="border-b border-border pb-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div><p className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">{t(`options.relationType.${relation.relation_type}`)}</p><h2 className="mt-2 text-xl font-semibold text-foreground">{names.source} <span className="text-accent">→</span> {names.target}</h2><p className="mt-2 text-sm text-muted">{t("fields.intensity")}: {relation.intensity}/5 · {relation.is_active ? t("status.active") : t("status.inactive")}</p></div>
          <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onPress={onEdit} isDisabled={busy}>{t("actions.edit")}</Button><Button size="sm" variant="ghost" onPress={onCopy} isDisabled={busy}>{t("actions.copyAsNew")}</Button><Button size="sm" variant="ghost" onPress={onToggleActive} isDisabled={busy}>{relation.user_is_active ?? relation.is_active ? t("actions.deactivate") : t("actions.activate")}</Button><Button size="sm" variant="ghost" className="text-danger" onPress={onDelete} isDisabled={busy}>{t("actions.delete")}</Button></div>
        </div>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-surface-secondary"><div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(0, Math.min(5, relation.intensity)) * 20}%` }} /></div>
      </header>
      <dl className="grid gap-x-8 gap-y-6 py-6 md:grid-cols-2">{narrative.map(([label, value]) => <SummaryField key={label} label={label} value={value} />)}</dl>
    </article>
  );
}

function TrashSection({ title, empty, emptyText, children }: { title: string; empty: boolean; emptyText: string; children: ReactNode }) {
  return <section><h2 className="border-b border-border pb-3 text-lg font-semibold text-foreground">{title}</h2>{empty ? <p className="py-8 text-sm text-muted">{emptyText}</p> : <div className="divide-y divide-border">{children}</div>}</section>;
}

function TrashRow({ title, meta, busy, onRestore, onHardDelete, t }: { title: string; meta: string; busy: boolean; onRestore: () => void; onHardDelete: () => void; t: CharactersTranslator }) {
  return <div className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><p className="truncate text-sm font-semibold text-foreground">{title}</p><p className="mt-1 truncate font-mono text-xs text-muted">{meta}</p></div><div className="flex gap-2"><Button size="sm" variant="outline" onPress={onRestore} isDisabled={busy}>{t("actions.restore")}</Button><Button size="sm" variant="ghost" className="text-danger" onPress={onHardDelete} isDisabled={busy}>{t("actions.hardDelete")}</Button></div></div>;
}

function SummaryField({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs font-semibold tracking-wide text-muted">{label}</dt><dd className="mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground/85">{value || "—"}</dd></div>;
}

function StatusPill({ active, children }: { active: boolean; children: ReactNode }) {
  return <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${active ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-surface-secondary text-muted"}`}>{children}</span>;
}

function EmptyPanel({ title, description }: { title: string; description?: string }) {
  return <div className="border-y border-dashed border-border px-5 py-14 text-center"><p className="text-sm font-medium text-foreground">{title}</p>{description && <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-muted">{description}</p>}</div>;
}

function LoadingPanel({ label }: { label: string }) {
  return <div className="border-y border-border px-5 py-14 text-center" role="status"><span className="mx-auto block size-6 animate-spin rounded-full border-2 border-border border-t-accent" aria-hidden="true" /><p className="mt-3 text-sm text-muted">{label}</p></div>;
}
