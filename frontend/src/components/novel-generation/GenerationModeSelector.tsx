"use client";

import type { GenerationMode } from "@/types/novelBlueprint";

interface GenerationModeSelectorProps {
  value: GenerationMode;
  onChange: (mode: GenerationMode) => void;
  disabled?: boolean;
}

const MODE_OPTIONS: {
  key: GenerationMode;
  title: string;
  summary: string;
  details: string[];
}[] = [
  {
    key: "quick",
    title: "快速生成",
    summary: "一句话创意，AI 自动补全并生成小说",
    details: ["保留原有四步建书行为", "适合先出稿再逐步补设定", "生成后仍可在创作设定中心补齐蓝图"],
  },
  {
    key: "guided",
    title: "设定辅助生成",
    summary: "先整理创作蓝图，确认后再生成大纲与正文",
    details: ["七步向导整理剧情、世界观、战力体系与关系", "蓝图确认后才生成大纲", "大纲确认后逐章生成正文候选"],
  },
];

/**
 * 功能：生成模式选择卡片组。
 *
 * 只负责模式选择，不触发任何生成请求；快速模式保持原有四步建书行为不变。
 */
export default function GenerationModeSelector({
  value,
  onChange,
  disabled = false,
}: GenerationModeSelectorProps) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {MODE_OPTIONS.map((option) => {
        const selected = value === option.key;
        return (
          <button
            key={option.key}
            type="button"
            disabled={disabled}
            onClick={() => onChange(option.key)}
            className={`rounded-xl border p-4 text-left transition-colors disabled:opacity-50 ${
              selected
                ? "border-accent bg-accent/5"
                : "border-border bg-surface hover:border-accent/60"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-foreground">{option.title}</span>
              <span
                className={`h-3 w-3 rounded-full border ${
                  selected ? "border-accent bg-accent" : "border-border"
                }`}
              />
            </div>
            <p className="mt-1 text-xs text-muted">{option.summary}</p>
            <ul className="mt-2 space-y-0.5">
              {option.details.map((detail) => (
                <li key={detail} className="text-xs text-muted">
                  · {detail}
                </li>
              ))}
            </ul>
          </button>
        );
      })}
    </div>
  );
}
