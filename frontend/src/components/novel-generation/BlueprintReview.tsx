"use client";

import { Button } from "@heroui/react";
import {
  hasBlockingConflict,
  type BlueprintConflict,
  type BlueprintSuggestion,
  type NovelBlueprint,
} from "@/types/novelBlueprint";

interface BlueprintReviewProps {
  blueprint: NovelBlueprint;
  suggestions?: BlueprintSuggestion[];
  /** 切换建议的采纳状态；候选默认不生效。 */
  onToggleSuggestion?: (suggestion: BlueprintSuggestion, accepted: boolean) => void;
  /** 冲突被显式忽略时回调；blocking 冲突忽略后才允许确认。 */
  onIgnoreConflict?: (conflict: BlueprintConflict) => void;
  onSaveDraft: () => void;
  onConfirm: () => void;
  onGenerateOutline?: () => void;
  saving?: boolean;
  confirming?: boolean;
  readOnly?: boolean;
  hint?: string;
}

const SEVERITY_LABEL: Record<BlueprintConflict["severity"], string> = {
  blocking: "阻断",
  warning: "警告",
  notice: "提示",
};

const SEVERITY_CLASS: Record<BlueprintConflict["severity"], string> = {
  blocking: "bg-red-50 text-red-600 border-red-200",
  warning: "bg-amber-50 text-amber-700 border-amber-200",
  notice: "bg-surface-secondary text-muted border-border",
};

/**
 * 功能：创作蓝图确认面板。
 *
 * 汇总蓝图内容、AI 候选建议与冲突；存在未处理的阻断冲突时禁止确认，
 * 用户显式忽略后方可继续，保证「未确认的 AI 结果不作为硬规则」。
 */
export default function BlueprintReview({
  blueprint,
  suggestions = [],
  onToggleSuggestion,
  onIgnoreConflict,
  onSaveDraft,
  onConfirm,
  onGenerateOutline,
  saving = false,
  confirming = false,
  readOnly = false,
  hint,
}: BlueprintReviewProps) {
  const blocking = hasBlockingConflict(blueprint);
  const acceptedCount = suggestions.filter((item) => item.accepted).length;

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm font-medium text-foreground">蓝图预览</div>
          <span className="text-xs text-muted">
            版本 v{blueprint.version || 1} · 状态 {blueprint.status}
          </span>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <SummaryBlock label="剧情梗概" value={blueprint.plot_summary} />
          <SummaryBlock label="世界观" value={blueprint.worldview} />
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted">
          <Badge text={`角色 ${blueprint.selected_character_ids.length}`} />
          <Badge text={`势力 ${blueprint.selected_faction_ids.length}`} />
          <Badge text={`设定卡 ${blueprint.selected_setting_card_ids.length}`} />
          <Badge text={`关系 ${blueprint.selected_relation_ids.length}`} />
          <Badge
            text={`战力等级 ${blueprint.power_system?.levels.length ?? 0}`}
          />
          {blueprint.power_system?.name && <Badge text={blueprint.power_system.name} />}
        </div>
      </div>

      {blueprint.conflicts.length > 0 && (
        <div className="space-y-2 rounded-xl border border-border bg-surface p-4">
          <div className="text-sm font-medium text-foreground">
            冲突检查（{blueprint.conflicts.length}）
          </div>
          {blueprint.conflicts.map((conflict) => (
            <div
              key={conflict.conflict_id}
              className={`rounded-lg border px-3 py-2 text-xs ${SEVERITY_CLASS[conflict.severity]}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-surface px-2 py-0.5">
                  {SEVERITY_LABEL[conflict.severity]}
                </span>
                <span className="font-medium">{conflict.message}</span>
              </div>
              <p className="mt-1 text-muted">建议：{conflict.suggestion}</p>
              {conflict.severity === "blocking" && !conflict.ignored && onIgnoreConflict && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-1"
                  onPress={() => onIgnoreConflict(conflict)}
                >
                  已知晓，仍要继续
                </Button>
              )}
              {conflict.ignored && (
                <p className="mt-1 text-muted">已显式忽略，可继续确认</p>
              )}
            </div>
          ))}
        </div>
      )}

      {suggestions.length > 0 && onToggleSuggestion && (
        <div className="space-y-2 rounded-xl border border-accent/40 bg-accent/5 p-4">
          <div className="text-sm font-medium text-foreground">
            AI 候选建议（已采纳 {acceptedCount}/{suggestions.length}）
          </div>
          {suggestions.map((suggestion) => (
            <label
              key={suggestion.suggestion_id}
              className="flex cursor-pointer items-start gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-xs"
            >
              <input
                type="checkbox"
                className="mt-0.5"
                checked={suggestion.accepted === true}
                disabled={readOnly || suggestion.rejected === true}
                onChange={(event) => onToggleSuggestion(suggestion, event.target.checked)}
              />
              <span className="min-w-0 flex-1">
                <span className="font-medium text-foreground">
                  {suggestion.title || suggestion.target}
                </span>
                <span className="ml-2 rounded-full bg-surface-secondary px-2 py-0.5 text-muted">
                  {suggestion.target}
                </span>
                <span className="mt-1 block whitespace-pre-wrap text-foreground">
                  {suggestion.content}
                </span>
                {suggestion.rejected && (
                  <span className="mt-1 block text-muted">已忽略</span>
                )}
              </span>
            </label>
          ))}
        </div>
      )}

      {hint && <p className="text-xs text-muted">{hint}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          onPress={onConfirm}
          isDisabled={readOnly || confirming || blocking}
        >
          {confirming ? "确认中…" : "确认蓝图"}
        </Button>
        <Button variant="ghost" size="sm" onPress={onSaveDraft} isDisabled={readOnly || saving}>
          {saving ? "保存中…" : "保存草稿"}
        </Button>
        {onGenerateOutline && (
          <Button
            variant="ghost"
            size="sm"
            onPress={onGenerateOutline}
            isDisabled={blueprint.status !== "confirmed"}
          >
            生成卷章大纲
          </Button>
        )}
        {blocking && (
          <span className="text-xs text-red-600">
            存在未处理的阻断冲突，需先处理或显式忽略
          </span>
        )}
      </div>
    </div>
  );
}

function SummaryBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface-secondary p-3">
      <div className="text-xs text-muted">{label}</div>
      <p className="mt-1 whitespace-pre-wrap text-xs text-foreground">
        {value?.trim() ? value : "（未填写）"}
      </p>
    </div>
  );
}

function Badge({ text }: { text: string }) {
  return (
    <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-muted">{text}</span>
  );
}
