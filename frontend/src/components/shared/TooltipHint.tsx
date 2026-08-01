"use client";

interface TooltipHintProps {
  text: string;
  className?: string;
}

/**
 * 渲染一个可放在标签旁的问号说明气泡。
 *
 * Args:
 *   text: 悬停或聚焦时展示的说明文本。
 *   className: 可选的外层样式。
 *
 * Returns:
 *   带问号圆点与悬浮说明的 React 节点。
 */
export function TooltipHint({ text, className = "" }: TooltipHintProps) {
  return (
    <span className={`group relative inline-flex align-middle ${className}`}>
      <button
        type="button"
        className="grid h-4 w-4 place-items-center rounded-full border border-border bg-surface-secondary text-[10px] font-semibold leading-none text-muted outline-none transition-colors hover:border-accent hover:text-accent focus-visible:border-accent focus-visible:text-accent"
        aria-label={text}
      >
        ?
      </button>
      <span className="pointer-events-none absolute left-1/2 top-5 z-30 hidden w-64 -translate-x-1/2 rounded-md border border-border bg-surface px-3 py-2 text-xs font-normal leading-5 text-foreground shadow-lg group-hover:block group-focus-within:block">
        {text}
      </span>
    </span>
  );
}
