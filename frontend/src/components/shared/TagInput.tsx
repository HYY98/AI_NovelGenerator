"use client";

import { useId, useState, type KeyboardEvent } from "react";

const TAG_SEPARATOR = /[\n,，、;；]+/;

interface TagInputProps {
  name?: string;
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  getRemoveAriaLabel: (value: string) => string;
  placeholder?: string;
  description?: string;
  limitReachedText?: string;
  disabled?: boolean;
  maxItems?: number;
  className?: string;
  error?: string;
}

/**
 * 渲染可复用的标签输入控件，支持回车与常见中英文分隔符批量录入。
 *
 * Args:
 *   props: 标签标题、当前值、变更回调及可选的提示和数量限制。
 *
 * Returns:
 *   可添加、查看并移除标签的 React 节点。
 */
export default function TagInput({
  name,
  label,
  values,
  onChange,
  getRemoveAriaLabel,
  placeholder,
  description,
  limitReachedText,
  disabled = false,
  maxItems,
  className = "",
  error,
}: TagInputProps) {
  const inputId = useId();
  const descriptionId = useId();
  const errorId = useId();
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("");
  const reachedLimit = maxItems !== undefined && values.length >= maxItems;

  const commitDraft = (rawValue: string) => {
    // 粘贴整段列表时一次完成拆分、去重，避免用户逐项重复录入。
    const candidates = rawValue
      .split(TAG_SEPARATOR)
      .map((item) => item.trim())
      .filter(Boolean);
    if (candidates.length === 0) {
      setDraft("");
      return;
    }

    const nextValues = [...values];
    const pendingValues: string[] = [];
    for (const candidate of candidates) {
      if (nextValues.includes(candidate)) {
        continue;
      }
      if (maxItems !== undefined && nextValues.length >= maxItems) {
        pendingValues.push(candidate);
        continue;
      }
      nextValues.push(candidate);
    }
    onChange(nextValues);
    // 达到数量上限时保留尚未接收的内容，避免粘贴多项后静默丢失。
    setDraft(pendingValues.join("，"));
    setStatus(pendingValues.length > 0 ? (limitReachedText ?? "") : "");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) {
      return;
    }

    if (event.key === "Enter" || event.key === "," || event.key === ";") {
      event.preventDefault();
      commitDraft(draft);
    }
  };

  const removeValue = (value: string) => {
    onChange(values.filter((item) => item !== value));
    setStatus("");
  };

  return (
    <div className={`grid gap-2 text-sm ${className}`}>
      <label htmlFor={inputId} className="text-xs font-semibold tracking-wide text-muted">
        {label}
      </label>
      <div
        className={`flex min-h-11 flex-wrap items-center gap-2 rounded-xl border bg-background/80 px-3 py-2 transition-[border-color,box-shadow] focus-within:ring-2 focus-within:ring-accent/15 ${
          error ? "border-red-500" : "border-border focus-within:border-accent"
        } ${
          disabled ? "cursor-not-allowed opacity-60" : ""
        }`}
      >
        {values.map((value) => (
          <span
            key={value}
            className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-accent/10 px-2.5 py-1 text-xs font-medium text-accent"
          >
            <span className="truncate">{value}</span>
            <button
              type="button"
              onClick={() => removeValue(value)}
              className="grid h-4 w-4 shrink-0 place-items-center rounded-full text-accent/70 transition-colors hover:bg-accent/15 hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              aria-label={getRemoveAriaLabel(value)}
              disabled={disabled}
            >
              ×
            </button>
          </span>
        ))}
        <input
          id={inputId}
          name={name}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={() => commitDraft(draft)}
          placeholder={values.length === 0 ? placeholder : undefined}
          className="min-w-32 flex-1 bg-transparent py-1 text-sm text-foreground outline-none placeholder:text-muted/55"
          disabled={disabled || reachedLimit}
          aria-invalid={Boolean(error) || undefined}
          aria-describedby={[
            description || maxItems !== undefined ? descriptionId : "",
            error ? errorId : "",
          ].filter(Boolean).join(" ") || undefined}
        />
      </div>
      {(description || maxItems !== undefined) && (
        <span id={descriptionId} className="flex items-center justify-between gap-3 text-xs leading-5 text-muted">
          <span>{description}</span>
          {maxItems !== undefined && <span className="shrink-0 tabular-nums">{values.length}/{maxItems}</span>}
        </span>
      )}
      {status && (
        <span className="text-xs leading-5 text-amber-700 dark:text-amber-300" role="status" aria-live="polite">
          {status}
        </span>
      )}
      {error && <span id={errorId} className="text-xs leading-5 text-red-600 dark:text-red-300">{error}</span>}
    </div>
  );
}
