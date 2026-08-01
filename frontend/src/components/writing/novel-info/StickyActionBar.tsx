"use client";

import { Button } from "@heroui/react";

interface StickyActionBarProps {
  children: React.ReactNode;
}

/**
 * 渲染创建态底部操作栏，并让按钮与编辑工作台正文宽度对齐。
 *
 * Args:
 *   children: 需要固定展示的操作按钮。
 *
 * Returns:
 *   带背景模糊和顶部边界的底部操作区域。
 */
export default function StickyActionBar({ children }: StickyActionBarProps) {
  return (
    <div className="sticky bottom-0 z-10 border-t border-border bg-background/95 px-4 py-4 backdrop-blur-sm sm:px-6">
      <div className="mx-auto flex max-w-7xl flex-col justify-end gap-2 sm:flex-row sm:items-center sm:gap-3 [&>*]:w-full sm:[&>*]:w-auto">
        {children}
      </div>
    </div>
  );
}

interface ActionButtonProps {
  label: string;
  onPress: () => void;
  variant?: "primary" | "ghost" | "outline";
  isDisabled?: boolean;
  className?: string;
}

export function ActionButton({
  label,
  onPress,
  variant = "primary",
  isDisabled = false,
  className = "",
}: ActionButtonProps) {
  return (
    <Button
      variant={variant}
      onPress={onPress}
      isDisabled={isDisabled}
      className={
        variant === "primary"
          ? `bg-accent text-white hover:bg-accent-hover ${className}`
          : className
      }
    >
      {label}
    </Button>
  );
}
