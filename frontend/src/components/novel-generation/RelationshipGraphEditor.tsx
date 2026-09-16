"use client";

import { useMemo, useState } from "react";
import { Button } from "@heroui/react";
import { ConfirmActionModal } from "@/components/shared/RelationEditorPrimitives";
import {
  CHARACTER_RELATION_TYPES,
  CHARACTER_RELATION_TYPE_LABEL,
  FACTION_RELATION_TYPE_LABEL,
  createCharacterRelation,
  deleteCharacterRelation,
  updateCharacterRelation,
  type FactionRelationResponse,
} from "@/lib/relationApi";
import type { CharacterRelationResponseV1 } from "@/types/character";

interface RelationshipGraphEditorProps {
  novelId?: string | null;
  relations: CharacterRelationResponseV1[];
  characters: { character_id?: string; name: string }[];
  /** 蓝图中已选中的关系业务 ID；传入时展示蓝图范围勾选。 */
  selectedRelationIds?: string[];
  onChangeRelations: (relations: CharacterRelationResponseV1[]) => void;
  onChangeSelectedRelationIds?: (ids: string[]) => void;
  factionRelations?: FactionRelationResponse[];
  readOnly?: boolean;
  onError?: (message: string) => void;
}

interface RelationDraft {
  relation_type: string;
  current_state: string;
  core_conflict: string;
  hidden_tension: string;
  possible_change: string;
  story_value: string;
  intensity: number;
}

const EMPTY_DRAFT: RelationDraft = {
  relation_type: "friend",
  current_state: "",
  core_conflict: "",
  hidden_tension: "",
  possible_change: "",
  story_value: "",
  intensity: 3,
};

const DRAFT_FIELDS: { key: keyof Omit<RelationDraft, "relation_type" | "intensity">; label: string }[] = [
  { key: "current_state", label: "当前状态" },
  { key: "core_conflict", label: "核心矛盾" },
  { key: "hidden_tension", label: "潜在张力" },
  { key: "possible_change", label: "可能变化" },
  { key: "story_value", label: "故事价值" },
];

/**
 * 功能：结构化关系图编辑器。
 *
 * 以「边」为单位展示与编辑角色关系：可勾选进入蓝图范围、可新增 / 修改 / 删除关系，
 * 势力关系以只读方式并列展示。所有结构化字段都会写回正式关系，不只保存画布坐标。
 */
export default function RelationshipGraphEditor({
  novelId,
  relations,
  characters,
  selectedRelationIds,
  onChangeRelations,
  onChangeSelectedRelationIds,
  factionRelations = [],
  readOnly = false,
  onError,
}: RelationshipGraphEditorProps) {
  const [draft, setDraft] = useState<RelationDraft>(EMPTY_DRAFT);
  const [editingRelationId, setEditingRelationId] = useState<string | null>(null);
  const [dialogMode, setDialogMode] = useState<"closed" | "create" | "edit">("closed");
  const [pendingDelete, setPendingDelete] = useState<CharacterRelationResponseV1 | null>(null);
  const [saving, setSaving] = useState(false);

  const characterNameMap = useMemo(() => {
    const map = new Map<string, string>();
    characters.forEach((character) => {
      if (character.character_id) map.set(character.character_id, character.name);
    });
    return map;
  }, [characters]);

  const activeRelations = useMemo(
    () => relations.filter((relation) => !relation.is_deleted),
    [relations]
  );

  const resolveName = (id: string, preset: string | null | undefined) =>
    preset || characterNameMap.get(id) || id;

  const toggleSelected = (relationId: string) => {
    if (!onChangeSelectedRelationIds) return;
    const current = selectedRelationIds ?? [];
    onChangeSelectedRelationIds(
      current.includes(relationId)
        ? current.filter((item) => item !== relationId)
        : [...current, relationId]
    );
  };

  const openCreate = () => {
    setDraft(EMPTY_DRAFT);
    setEditingRelationId(null);
    setDialogMode("create");
  };

  const openEdit = (relation: CharacterRelationResponseV1) => {
    setDraft({
      relation_type: relation.relation_type,
      current_state: relation.current_state,
      core_conflict: relation.core_conflict,
      hidden_tension: relation.hidden_tension,
      possible_change: relation.possible_change,
      story_value: relation.story_value,
      intensity: relation.intensity,
    });
    setEditingRelationId(relation.relation_id);
    setDialogMode("edit");
  };

  const closeDialog = () => {
    setDialogMode("closed");
    setEditingRelationId(null);
  };

  const saveDraft = async () => {
    if (!novelId) {
      onError?.("缺少小说 ID，无法保存关系");
      return;
    }
    if (dialogMode === "edit") {
      const target = activeRelations.find(
        (relation) => relation.relation_id === editingRelationId
      );
      if (!target) return;
      setSaving(true);
      try {
        const updated = await updateCharacterRelation(novelId, target.relation_id, {
          ...draft,
          relation_type: draft.relation_type as CharacterRelationResponseV1["relation_type"],
          expected_version: target.version,
        });
        onChangeRelations(
          relations.map((relation) =>
            relation.relation_id === target.relation_id
              ? { ...relation, ...updated }
              : relation
          )
        );
        closeDialog();
      } catch (error) {
        onError?.(error instanceof Error ? error.message : "保存关系失败");
      } finally {
        setSaving(false);
      }
      return;
    }

    const [sourceId, targetId] = [
      characters[0]?.character_id ?? "",
      characters[1]?.character_id ?? "",
    ];
    if (!sourceId || !targetId || sourceId === targetId) {
      onError?.("至少需要先有两位角色，才能创建关系");
      return;
    }
    setSaving(true);
    try {
      const created = await createCharacterRelation(novelId, {
        source_character_id: sourceId,
        target_character_id: targetId,
        ...draft,
        relation_type: draft.relation_type as CharacterRelationResponseV1["relation_type"],
        is_active: true,
      });
      onChangeRelations([...relations, created]);
      closeDialog();
    } catch (error) {
      onError?.(error instanceof Error ? error.message : "创建关系失败");
    } finally {
      setSaving(false);
    }
  };

  const confirmDelete = async () => {
    if (!novelId || !pendingDelete) return;
    setSaving(true);
    try {
      await deleteCharacterRelation(
        novelId,
        pendingDelete.relation_id,
        pendingDelete.version
      );
      onChangeRelations(
        relations.filter((relation) => relation.relation_id !== pendingDelete.relation_id)
      );
      setPendingDelete(null);
    } catch (error) {
      onError?.(error instanceof Error ? error.message : "删除关系失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-foreground">关系图</div>
          <div className="mt-0.5 text-xs text-muted">
            结构化关系会写回正式关系表；勾选后纳入创作蓝图的设定范围
          </div>
        </div>
        <Button variant="ghost" size="sm" onPress={openCreate} isDisabled={readOnly}>
          新增角色关系
        </Button>
      </div>

      {activeRelations.length === 0 && (
        <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted">
          还没有角色关系，可先新建关系或从角色工作台生成
        </p>
      )}

      <div className="space-y-2">
        {activeRelations.map((relation) => {
          const selected = (selectedRelationIds ?? []).includes(relation.relation_id);
          const sourceName = resolveName(
            relation.source_character_id,
            relation.source_character_name
          );
          const targetName = resolveName(
            relation.target_character_id,
            relation.target_character_name
          );
          return (
            <div
              key={relation.relation_id}
              className={`rounded-lg border p-3 ${
                selected ? "border-accent bg-accent/5" : "border-border bg-surface"
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                {onChangeSelectedRelationIds && (
                  <label className="flex items-center gap-1.5 text-xs text-muted">
                    <input
                      type="checkbox"
                      checked={selected}
                      disabled={readOnly}
                      onChange={() => toggleSelected(relation.relation_id)}
                    />
                    纳入蓝图
                  </label>
                )}
                <span className="text-sm font-medium text-foreground">
                  {sourceName} → {targetName}
                </span>
                <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-xs text-muted">
                  {CHARACTER_RELATION_TYPE_LABEL[relation.relation_type] ?? relation.relation_type}
                </span>
                <span className="text-xs text-muted">强度 {relation.intensity}</span>
                <span className="text-xs text-muted">v{relation.version}</span>
                <div className="ml-auto flex items-center gap-1">
                  <button
                    type="button"
                    className="rounded border border-border px-2 py-1 text-xs disabled:opacity-40"
                    disabled={readOnly}
                    onClick={() => openEdit(relation)}
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    className="rounded border border-red-200 px-2 py-1 text-xs text-red-600 disabled:opacity-40"
                    disabled={readOnly}
                    onClick={() => setPendingDelete(relation)}
                  >
                    删除
                  </button>
                </div>
              </div>
              <p className="mt-1 text-xs text-muted">{relation.current_state}</p>
            </div>
          );
        })}
      </div>

      {factionRelations.length > 0 && (
        <div className="space-y-2 border-t border-border pt-3">
          <div className="text-xs font-medium text-muted">势力关系（只读）</div>
          {factionRelations
            .filter((relation) => !relation.is_deleted)
            .map((relation) => (
              <div
                key={relation.relation_id}
                className="rounded-lg border border-border bg-surface-secondary px-3 py-2 text-xs"
              >
                <span className="font-medium text-foreground">
                  {relation.source_faction_name || relation.source_faction_id} →{" "}
                  {relation.target_faction_name || relation.target_faction_id}
                </span>
                <span className="ml-2 rounded-full bg-surface px-2 py-0.5 text-muted">
                  {FACTION_RELATION_TYPE_LABEL[relation.relation_type] ?? relation.relation_type}
                </span>
                {(relation.description || relation.current_state) && (
                  <p className="mt-1 text-muted">
                    {relation.description || relation.current_state}
                  </p>
                )}
              </div>
            ))}
        </div>
      )}

      {dialogMode !== "closed" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-xl border border-border bg-surface p-5">
            <div className="mb-3 text-sm font-medium text-foreground">
              {dialogMode === "create" ? "新增角色关系" : "编辑角色关系"}
            </div>
            <div className="space-y-3">
              <label className="block text-sm">
                <span className="mb-1 block text-muted">关系类型</span>
                <select
                  className="w-full rounded-lg border border-border px-3 py-2"
                  value={draft.relation_type}
                  onChange={(event) =>
                    setDraft({ ...draft, relation_type: event.target.value })
                  }
                >
                  {CHARACTER_RELATION_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {CHARACTER_RELATION_TYPE_LABEL[type] ?? type}
                    </option>
                  ))}
                </select>
              </label>
              {DRAFT_FIELDS.map((field) => (
                <label key={field.key} className="block text-sm">
                  <span className="mb-1 block text-muted">{field.label}</span>
                  <textarea
                    className="min-h-14 w-full rounded-lg border border-border px-3 py-2"
                    value={draft[field.key]}
                    onChange={(event) => setDraft({ ...draft, [field.key]: event.target.value })}
                  />
                </label>
              ))}
              <label className="block text-sm">
                <span className="mb-1 block text-muted">强度（1-5）</span>
                <input
                  type="number"
                  min={1}
                  max={5}
                  className="w-24 rounded-lg border border-border px-3 py-2"
                  value={draft.intensity}
                  onChange={(event) =>
                    setDraft({ ...draft, intensity: Number(event.target.value) || 1 })
                  }
                />
              </label>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" size="sm" onPress={closeDialog} isDisabled={saving}>
                取消
              </Button>
              <Button variant="primary" size="sm" onPress={saveDraft} isDisabled={saving}>
                {saving ? "保存中…" : "保存"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {pendingDelete && (
        <ConfirmActionModal
          title="删除角色关系"
          message="删除后会进入关系回收站，可在角色工作台恢复。"
          confirmText="删除"
          cancelText="取消"
          danger
          onCancel={() => setPendingDelete(null)}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  );
}
