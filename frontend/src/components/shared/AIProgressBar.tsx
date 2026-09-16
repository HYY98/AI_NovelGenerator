"use client";

interface AIProgressBarProps {
  /** 明确的进度百分比（0-100）；为空时展示循环扫光的不确定进度。 */
  value?: number | null;
  /** 进度条下方的状态文字，例如"已生成 1,234 字"。 */
  label?: string;
  className?: string;
}

/**
 * 功能：AI 调用进行中的进度条。
 *
 * 有明确百分比时按比例填充，没有可预估总量时退化为循环扫光动画，
 * 两种形态都提供 role="progressbar" 以便读屏软件播报。
 *
 * Args:
 *   value: 进度百分比，null/undefined 表示不确定进度。
 *   label: 可选的进度文字说明。
 *   className: 外层容器附加样式。
 *
 * Returns:
 *   进度条 React 节点。
 */
export function AIProgressBar({ value = null, label, className = "" }: AIProgressBarProps) {
  const clamped =
    value == null || Number.isNaN(value) ? null : Math.max(0, Math.min(100, value));

  return (
    <div className={`space-y-1 ${className}`}>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-accent/15"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={clamped ?? undefined}
      >
        {clamped == null ? (
          <div className="progress-indeterminate h-full w-1/3 rounded-full bg-accent" />
        ) : (
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out"
            style={{ width: `${clamped}%` }}
          />
        )}
      </div>
      {label && <p className="text-[11px] leading-4 text-muted">{label}</p>}
    </div>
  );
}
