"use client";

import type { ReactNode } from "react";
import type { ReviewSeverity } from "@/lib/aiTypes";

/** AI 面板中的分区标题。 */
export function AISection({
  title,
  hint,
  children,
  actions,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface-secondary p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-foreground">{title}</div>
          {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
        </div>
        {actions}
      </div>
      <div className="space-y-2 text-sm">{children}</div>
    </div>
  );
}

const SEVERITY_STYLE: Record<ReviewSeverity, string> = {
  blocking: "border-red-300 bg-red-50 text-red-600",
  warning: "border-amber-300 bg-amber-50 text-amber-700",
  notice: "border-border bg-surface text-muted",
};

const SEVERITY_LABEL: Record<ReviewSeverity, string> = {
  blocking: "阻断",
  warning: "警告",
  notice: "提示",
};

/** 审校严重级别徽标。 */
export function SeverityBadge({ severity }: { severity: ReviewSeverity }) {
  return (
    <span
      className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${
        SEVERITY_STYLE[severity] ?? SEVERITY_STYLE.notice
      }`}
    >
      {SEVERITY_LABEL[severity] ?? severity}
    </span>
  );
}

/** 展示生成告警与冲突。 */
export function AIWarnings({
  warnings,
  conflicts,
}: {
  warnings?: string[];
  conflicts?: string[];
}) {
  const warnList = warnings ?? [];
  const conflictList = conflicts ?? [];
  if (!warnList.length && !conflictList.length) return null;
  return (
    <div className="space-y-1 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-700">
      {conflictList.map((item, index) => (
        <div key={`c-${index}`}>冲突：{item}</div>
      ))}
      {warnList.map((item, index) => (
        <div key={`w-${index}`}>{item}</div>
      ))}
    </div>
  );
}

/** AI 面板中的操作按钮。 */
export function AIButton({
  label,
  onClick,
  disabled,
  tone = "default",
  title,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: "default" | "primary" | "danger";
  title?: string;
}) {
  const toneClass =
    tone === "primary"
      ? "bg-accent text-white border-accent"
      : tone === "danger"
        ? "border-red-200 text-red-600"
        : "border-border text-foreground";
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg border px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50 ${toneClass}`}
    >
      {label}
    </button>
  );
}

/** 候选项的采纳勾选行。 */
export function AdoptRow({
  checked,
  onToggle,
  label,
  value,
}: {
  checked: boolean;
  onToggle: () => void;
  label: string;
  value: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded border border-border bg-surface px-2 py-1.5">
      <input type="checkbox" checked={checked} onChange={onToggle} className="mt-1" />
      <span className="min-w-0 text-xs">
        <span className="block text-muted">{label}</span>
        <span className="block break-words text-foreground">{value || "（空）"}</span>
      </span>
    </label>
  );
}
