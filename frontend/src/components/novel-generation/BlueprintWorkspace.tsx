"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@heroui/react";
import NovelBlueprintWizard from "@/components/novel-generation/NovelBlueprintWizard";
import PowerSystemEditor from "@/components/novel-generation/PowerSystemEditor";
import {
  WorkspaceEmptyState,
  WorkspaceNotice,
  WorkspacePage,
  WorkspaceStatusChip,
} from "@/components/shared/WorkspacePage";
import { confirmBlueprint, getLatestBlueprint } from "@/lib/chapterAiApi";
import {
  blueprintPowerSystem,
  createEmptyBlueprint,
  normalizeBlueprint,
  type NovelBlueprint,
} from "@/types/novelBlueprint";
import type { PowerSystem } from "@/types/powerSystem";

interface WorkspaceProps {
  novelId: string;
}

function useBlueprint(novelId: string) {
  const [blueprint, setBlueprint] = useState<NovelBlueprint | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!novelId) return;
    setLoading(true);
    try {
      const res = await getLatestBlueprint(novelId);
      setBlueprint(res ? normalizeBlueprint(res, createEmptyBlueprint()) : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "蓝图加载失败");
    } finally {
      setLoading(false);
    }
  }, [novelId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { blueprint, setBlueprint, loading, error, setError, reload };
}

/**
 * 功能：把蓝图状态映射为页头徽标的文案与语气。
 *
 * Args:
 *   blueprint: 当前蓝图，尚未创建时传 null。
 *
 * Returns:
 *   供 WorkspaceStatusChip 直接使用的标签与色调。
 */
function blueprintStatusChip(blueprint: NovelBlueprint | null): {
  label: string;
  tone: "muted" | "success";
} {
  if (!blueprint) return { label: "尚未创建蓝图", tone: "muted" };
  if (blueprint.status === "confirmed") {
    return { label: `已确认 · v${blueprint.version}`, tone: "success" };
  }
  if (blueprint.status === "pending_confirmation") {
    return { label: `待确认 · v${blueprint.version}`, tone: "muted" };
  }
  if (blueprint.status === "superseded") {
    return { label: `历史版本 · v${blueprint.version}`, tone: "muted" };
  }
  return { label: `草稿 · v${blueprint.version}`, tone: "muted" };
}

/**
 * 功能：写作工作台的「创作设定中心」工作区。
 *
 * 承载七步蓝图向导，并在蓝图确认后继续卷章大纲与分章正文生成流程。
 */
export function CreationBlueprintWorkspace({ novelId }: WorkspaceProps) {
  const { blueprint, setBlueprint, loading, error } = useBlueprint(novelId);

  const statusChip = blueprintStatusChip(blueprint);

  return (
    <WorkspacePage
      eyebrow="创作与同步"
      title="创作设定中心"
      description="先确认创作蓝图，再生成卷章大纲，最后按章生成正文候选；AI 结果不会自动覆盖已确认设定"
      actions={<WorkspaceStatusChip tone={statusChip.tone}>{statusChip.label}</WorkspaceStatusChip>}
    >
      {error && <WorkspaceNotice tone="error">{error}</WorkspaceNotice>}
      {loading && !blueprint && <WorkspaceNotice>蓝图加载中…</WorkspaceNotice>}

      <NovelBlueprintWizard
        key={blueprint?.blueprint_id ?? "new"}
        novelId={novelId}
        initialMode="guided"
        initialBlueprint={blueprint}
        withOutlineFlow
        onCancel={() => undefined}
        onConfirmed={(next) => setBlueprint(next)}
      />
    </WorkspacePage>
  );
}

/**
 * 功能：写作工作台的「战力体系」工作区。
 *
 * 战力体系随蓝图一起保存：草稿通过 confirm=false 保存，已确认蓝图进入只读并提示创建新版本。
 */
export function PowerSystemWorkspace({ novelId }: WorkspaceProps) {
  const { blueprint, setBlueprint, loading, error, setError } = useBlueprint(novelId);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const base = blueprint ?? createEmptyBlueprint();
  const confirmed = base.status === "confirmed";

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await confirmBlueprint(novelId, base.blueprint_id, {
        expected_version: base.version || 1,
        plot_summary: base.plot_summary,
        worldview: base.worldview,
        power_system: base.power_system,
        selected_character_ids: base.selected_character_ids,
        selected_faction_ids: base.selected_faction_ids,
        selected_setting_card_ids: base.selected_setting_card_ids,
        selected_relation_ids: base.selected_relation_ids,
        confirm: false,
        generation_mode: base.generation_mode,
      });
      const next = normalizeBlueprint({ ...base, ...res }, base);
      setBlueprint(next);
      setMessage(`战力体系已保存（蓝图 v${next.version}，草稿状态）`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存战力体系失败");
    } finally {
      setSaving(false);
    }
  };

  const statusChip = blueprintStatusChip(blueprint);

  return (
    <WorkspacePage
      eyebrow="创作与同步"
      title="战力体系"
      description="战力体系作为创作蓝图的一部分保存；已确认的蓝图不会原地修改，需要创建新版本"
      actions={<WorkspaceStatusChip tone={statusChip.tone}>{statusChip.label}</WorkspaceStatusChip>}
    >
      {error && <WorkspaceNotice tone="error">{error}</WorkspaceNotice>}
      {message && <WorkspaceNotice tone="success">{message}</WorkspaceNotice>}
      {loading && !blueprint && <WorkspaceNotice>加载中…</WorkspaceNotice>}

      {!blueprint && !loading && (
        <WorkspaceEmptyState
          title="还没有创作蓝图"
          description="请先在「创作设定中心」创建并确认蓝图，再回到这里编辑战力体系。"
        />
      )}

      {blueprint && (
        <>
          <PowerSystemEditor
            value={blueprintPowerSystem(base)}
            onChange={(value: PowerSystem) =>
              setBlueprint({ ...base, power_system: value })
            }
            readOnly={confirmed}
          />

          {/* 保存区吸附在滚动容器底部，长表单下也不用来回滚动找按钮。 */}
          <div className="sticky bottom-0 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-surface/95 px-4 py-3 backdrop-blur">
            <p className="text-xs leading-5 text-muted">
              {confirmed
                ? `当前蓝图已确认（v${base.version}），如需修改请到创作设定中心创建新版本`
                : "草稿状态下保存只更新蓝图草稿，不会改动已确认版本"}
            </p>
            <Button
              variant="primary"
              size="sm"
              onPress={handleSave}
              isDisabled={saving || confirmed}
            >
              {saving ? "保存中…" : "保存战力体系"}
            </Button>
          </div>
        </>
      )}
    </WorkspacePage>
  );
}
