"use client";

import { Button } from "@heroui/react";
import type { SettingEvidence } from "@/lib/aiTypes";

interface SettingEvidencePanelProps {
  evidence: SettingEvidence[];
  /** 定位到正文对应范围的回调，由父组件负责滚动或高亮编辑器。 */
  onLocate?: (range: { start: number; end: number }) => void;
  title?: string;
}

/**
 * 功能：展示 AI 候选对应的正文证据。
 *
 * 每条证据展示片段文本与字符范围，并提供定位按钮跳转到正文对应位置，
 * 让用户能核对证据是否仍然成立。
 */
export default function SettingEvidencePanel({
  evidence,
  onLocate,
  title = "正文证据",
}: SettingEvidencePanelProps) {
  const valid = evidence.filter(
    (item) => item.evidence_text || typeof item.evidence_start === "number"
  );
  if (valid.length === 0) return null;

  return (
    <div className="space-y-1.5 rounded-lg border border-border bg-surface-secondary p-3">
      <div className="text-xs font-medium text-muted">{title}</div>
      {valid.map((item, index) => {
        const hasRange =
          typeof item.evidence_start === "number" && typeof item.evidence_end === "number";
        return (
          <div key={`${item.evidence_start ?? "na"}-${index}`} className="text-xs">
            <div className="flex flex-wrap items-center gap-2">
              {hasRange && (
                <span className="rounded-full bg-surface px-2 py-0.5 text-muted">
                  [{item.evidence_start}, {item.evidence_end})
                </span>
              )}
              {typeof item.confidence === "number" && (
                <span className="text-muted">置信度 {item.confidence.toFixed(2)}</span>
              )}
              {hasRange && onLocate && (
                <Button
                  variant="ghost"
                  size="sm"
                  onPress={() =>
                    onLocate({
                      start: item.evidence_start as number,
                      end: item.evidence_end as number,
                    })
                  }
                >
                  定位
                </Button>
              )}
            </div>
            <p className="mt-1 whitespace-pre-wrap text-foreground">{item.evidence_text}</p>
          </div>
        );
      })}
    </div>
  );
}
