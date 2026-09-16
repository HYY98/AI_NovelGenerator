"use client";

import { Button } from "@heroui/react";
import { AIWarnings } from "@/components/writing/ai/AIShared";
import type { BlueprintSuggestion, NovelBlueprint } from "@/types/novelBlueprint";

interface BlueprintOverviewStepProps {
  blueprint: NovelBlueprint;
  onChange: (blueprint: NovelBlueprint) => void;
  /** AI 候选建议；未采纳前不参与蓝图正式内容。 */
  suggestions?: BlueprintSuggestion[];
  onAcceptSuggestion?: (suggestion: BlueprintSuggestion) => void;
  onRejectSuggestion?: (suggestion: BlueprintSuggestion) => void;
  onGenerateSuggestions?: () => void;
  /** 目标规模，作为后续生成大纲的输入。 */
  targetChapterCount?: number;
  targetWordCount?: number;
  onChangeTarget?: (chapterCount: number, wordCount: number) => void;
  readOnly?: boolean;
  warnings?: string[];
}

/**
 * 功能：创作蓝图向导的「基本信息 + 剧情世界观」步骤。
 *
 * 编辑剧情梗概与世界观，展示并采纳 AI 候选建议；目标规模只在本地保存，
 * 在生成大纲时作为请求参数提交。
 */
export default function BlueprintOverviewStep({
  blueprint,
  onChange,
  suggestions = [],
  onAcceptSuggestion,
  onRejectSuggestion,
  onGenerateSuggestions,
  targetChapterCount,
  targetWordCount,
  onChangeTarget,
  readOnly = false,
  warnings = [],
}: BlueprintOverviewStepProps) {
  const patch = (change: Partial<NovelBlueprint>) => onChange({ ...blueprint, ...change });
  const pendingSuggestions = suggestions.filter(
    (item) => !item.accepted && !item.rejected
  );

  return (
    <div className="space-y-4">
      {onChangeTarget && (
        <div className="grid gap-3 rounded-xl border border-border bg-surface p-4 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block text-muted">目标章节数</span>
            <input
              type="number"
              min={1}
              className="w-full rounded-lg border border-border px-3 py-2"
              value={targetChapterCount ?? 0}
              disabled={readOnly}
              onChange={(event) =>
                onChangeTarget(
                  Number(event.target.value) || 0,
                  targetWordCount ?? 0
                )
              }
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-muted">每章目标字数</span>
            <input
              type="number"
              min={1}
              step={100}
              className="w-full rounded-lg border border-border px-3 py-2"
              value={targetWordCount ?? 0}
              disabled={readOnly}
              onChange={(event) =>
                onChangeTarget(
                  targetChapterCount ?? 0,
                  Number(event.target.value) || 0
                )
              }
            />
          </label>
        </div>
      )}

      <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="text-sm font-medium text-foreground">剧情梗概</div>
            <div className="mt-0.5 text-xs text-muted">
              主线目标、核心冲突与结局方向，AI 只会在此基础上补全候选
            </div>
          </div>
          {onGenerateSuggestions && (
            <Button variant="ghost" size="sm" onPress={onGenerateSuggestions} isDisabled={readOnly}>
              AI 补全设定
            </Button>
          )}
        </div>
        <textarea
          className="min-h-32 w-full rounded-lg border border-border px-3 py-2"
          value={blueprint.plot_summary}
          disabled={readOnly}
          onChange={(event) => patch({ plot_summary: event.target.value })}
          placeholder="用 200-500 字描述主线剧情、核心矛盾与结局走向"
        />
      </div>

      <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
        <div>
          <div className="text-sm font-medium text-foreground">世界观</div>
          <div className="mt-0.5 text-xs text-muted">世界规则、地理、历史与势力格局</div>
        </div>
        <textarea
          className="min-h-32 w-full rounded-lg border border-border px-3 py-2"
          value={blueprint.worldview}
          disabled={readOnly}
          onChange={(event) => patch({ worldview: event.target.value })}
          placeholder="描述世界的基本规则、力量来源、地理与历史背景"
        />
      </div>

      {warnings.length > 0 && <AIWarnings warnings={warnings} />}

      {pendingSuggestions.length > 0 && (
        <div className="space-y-2 rounded-xl border border-accent/40 bg-accent/5 p-4">
          <div className="text-sm font-medium text-foreground">
            AI 候选建议（{pendingSuggestions.length}）
          </div>
          <div className="text-xs text-muted">候选不会自动生效，采纳后才会写入蓝图</div>
          {pendingSuggestions.map((suggestion) => (
            <div
              key={suggestion.suggestion_id}
              className="rounded-lg border border-border bg-surface p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-foreground">{suggestion.title}</span>
                <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-xs text-muted">
                  {suggestion.target}
                </span>
                {typeof suggestion.confidence === "number" && (
                  <span className="text-xs text-muted">
                    置信度 {suggestion.confidence.toFixed(2)}
                  </span>
                )}
              </div>
              <p className="mt-1 whitespace-pre-wrap text-xs text-foreground">
                {suggestion.content}
              </p>
              <p className="mt-1 text-xs text-muted">理由：{suggestion.reason}</p>
              <div className="mt-2 flex gap-2">
                {onAcceptSuggestion && (
                  <Button
                    variant="primary"
                    size="sm"
                    onPress={() => onAcceptSuggestion(suggestion)}
                    isDisabled={readOnly}
                  >
                    采纳
                  </Button>
                )}
                {onRejectSuggestion && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onPress={() => onRejectSuggestion(suggestion)}
                    isDisabled={readOnly}
                  >
                    忽略
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
