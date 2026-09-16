import type { ReactNode } from "react";

/**
 * 功能：写作工作台的统一页面外壳。
 *
 * 提供全高滚动容器、页面内边距、居中内容宽度与统一页头排版。
 * 各工作区自行拼装布局时容易出现内容被父级 overflow-hidden 裁切、
 * 或页头字号与其它工作区不一致的问题，这里统一收敛。
 */
export function WorkspacePage({
  eyebrow,
  title,
  description,
  actions,
  children,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  /** 页头右侧的状态徽标或操作按钮。 */
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="workspace-scrollbar flex h-full min-h-0 flex-col overflow-y-auto bg-background">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 p-5 md:p-7">
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
          <div className="min-w-0">
            {eyebrow && (
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">{eyebrow}</p>
            )}
            <h1 className="mt-1 text-xl font-semibold text-foreground">{title}</h1>
            {description && (
              <p className="mt-1.5 max-w-3xl text-sm leading-6 text-muted">{description}</p>
            )}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
        </header>
        {children}
      </div>
    </section>
  );
}

const NOTICE_TONE_CLASS: Record<"info" | "success" | "error", string> = {
  info: "border-border bg-surface-secondary text-muted",
  success: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-300",
  error: "border-red-200 bg-red-50 text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300",
};

/** 工作台内的状态提示条，统一提示、成功与错误三种语气。 */
export function WorkspaceNotice({
  tone = "info",
  children,
}: {
  tone?: "info" | "success" | "error";
  children: ReactNode;
}) {
  return (
    <div
      className={`rounded-xl border px-4 py-3 text-sm ${NOTICE_TONE_CLASS[tone]}`}
      role={tone === "error" ? "alert" : undefined}
    >
      {children}
    </div>
  );
}

/** 工作台内的状态徽标，用于页头展示蓝图版本与确认状态。 */
export function WorkspaceStatusChip({
  tone = "muted",
  children,
}: {
  tone?: "muted" | "success";
  children: ReactNode;
}) {
  const toneClass =
    tone === "success"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-300"
      : "border-border bg-surface-secondary text-muted";
  return (
    <span className={`rounded-full border px-3 py-1 text-xs font-medium ${toneClass}`}>{children}</span>
  );
}

/** 工作台内的空状态卡片，统一虚线边框与居中排版。 */
export function WorkspaceEmptyState({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-surface-secondary/40 px-6 py-10 text-center">
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description && <p className="mx-auto mt-1.5 max-w-xl text-sm leading-6 text-muted">{description}</p>}
      {children && <div className="mt-4 flex flex-wrap items-center justify-center gap-2">{children}</div>}
    </div>
  );
}
