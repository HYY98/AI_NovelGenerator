"use client";

import { useMemo, useState } from "react";
import { Button } from "@heroui/react";
import { ConfirmActionModal } from "@/components/shared/RelationEditorPrimitives";
import {
  createEmptyPowerLevel,
  normalizePowerLevels,
  splitLines,
  validatePowerSystem,
  type PowerCounter,
  type PowerLevel,
  type PowerResource,
  type PowerSystem,
} from "@/types/powerSystem";

interface PowerSystemEditorProps {
  value: PowerSystem;
  onChange: (value: PowerSystem) => void;
  readOnly?: boolean;
  /** AI 补全入口；补全结果由父组件以候选形式下发，不在此处直接覆盖。 */
  onGenerateSuggestion?: () => void;
  /** 展示 AI 候选差异时使用，提示用户当前存在未采纳的候选。 */
  suggestionHint?: string;
}

type SectionKey = "levels" | "dimensions" | "resource" | "restrictions" | "special" | "counters";

const SECTION_LABEL: Record<SectionKey, string> = {
  levels: "等级 / 境界",
  dimensions: "力量维度",
  resource: "能量资源",
  restrictions: "使用限制",
  special: "特殊规则",
  counters: "克制关系",
};

const LIST_FIELD_HINT = "每行一条，也可用逗号分隔";

/** 与其它工作台统一的基础输入样式。 */
const INPUT_CLASS =
  "min-h-11 w-full rounded-xl border border-border bg-background/80 px-3.5 py-2.5 text-sm text-foreground outline-none transition-[border-color,box-shadow] placeholder:text-muted/55 focus:border-accent focus:ring-2 focus:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-60";

const TEXTAREA_CLASS = `${INPUT_CLASS} min-h-20 resize-y leading-6`;

/** 等级行与克制关系行内使用的紧凑输入样式。 */
const COMPACT_INPUT_CLASS =
  "min-w-0 rounded-xl border border-border bg-background/80 px-3 py-2 text-sm text-foreground outline-none transition-[border-color,box-shadow] placeholder:text-muted/55 focus:border-accent focus:ring-2 focus:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-60";

const ROW_BUTTON_CLASS =
  "rounded-lg border border-border px-2.5 py-1.5 text-xs text-muted transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-40";

const DANGER_ROW_BUTTON_CLASS =
  "rounded-lg border border-red-200 px-2.5 py-1.5 text-xs text-red-600 transition-colors hover:border-red-400 disabled:cursor-not-allowed disabled:opacity-40 dark:border-red-900/60 dark:text-red-300";

/** 功能：可折叠分区的标题按钮；定义在组件外以避免渲染期创建组件。 */
function SectionHeader({
  section,
  expanded,
  onToggle,
  count,
}: {
  section: SectionKey;
  expanded: boolean;
  onToggle: () => void;
  /** 可选条目数量徽标，便于收起状态下判断是否已填写。 */
  count?: number;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      className="flex w-full items-center justify-between gap-3 rounded-xl border border-border bg-surface-secondary px-3.5 py-2.5 text-left text-sm font-medium text-foreground transition-colors hover:border-accent/60"
    >
      <span className="flex min-w-0 items-center gap-2">
        <span className="truncate">{SECTION_LABEL[section]}</span>
        {typeof count === "number" && count > 0 && (
          <span className="shrink-0 rounded-full border border-border bg-surface px-2 py-0.5 text-[11px] font-normal text-muted">
            {count}
          </span>
        )}
      </span>
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        className={`shrink-0 text-muted transition-transform ${expanded ? "rotate-90" : ""}`}
      >
        <path d="m9 18 6-6-6-6" />
      </svg>
    </button>
  );
}

/**
 * 功能：结构化战力体系编辑器。
 *
 * 支持等级增删改与排序、维度、资源、限制、特殊规则和克制关系。
 * AI 补全只通过 onGenerateSuggestion 触发，结果由父组件作为候选下发，
 * 编辑器本身不直接改写任何已填写内容。
 */
export default function PowerSystemEditor({
  value,
  onChange,
  readOnly = false,
  onGenerateSuggestion,
  suggestionHint,
}: PowerSystemEditorProps) {
  const [editingLevelIndex, setEditingLevelIndex] = useState<number | null>(null);
  const [expandedSections, setExpandedSections] = useState<Record<SectionKey, boolean>>({
    levels: true,
    dimensions: true,
    resource: false,
    restrictions: false,
    special: false,
    counters: false,
  });
  const [levelPendingDelete, setLevelPendingDelete] = useState<number | null>(null);

  const validationErrors = useMemo(() => validatePowerSystem(value), [value]);

  const patch = (change: Partial<PowerSystem>) => onChange({ ...value, ...change });

  const patchLevel = (index: number, change: Partial<PowerLevel>) => {
    const levels = value.levels.map((level, i) => (i === index ? { ...level, ...change } : level));
    patch({ levels });
  };

  const addLevel = () => {
    const nextOrder = value.levels.reduce((max, level) => Math.max(max, level.order), -1) + 1;
    const levels = [...value.levels, createEmptyPowerLevel(nextOrder)];
    patch({ levels });
    setEditingLevelIndex(levels.length - 1);
  };

  const removeLevel = (index: number) => {
    const levels = value.levels.filter((_, i) => i !== index);
    patch({ levels: normalizePowerLevels(levels) });
    setLevelPendingDelete(null);
    if (editingLevelIndex === index) setEditingLevelIndex(null);
    else if (editingLevelIndex !== null && editingLevelIndex > index) {
      setEditingLevelIndex(editingLevelIndex - 1);
    }
  };

  const moveLevel = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= value.levels.length) return;
    const levels = [...value.levels];
    const [moved] = levels.splice(index, 1);
    levels.splice(target, 0, moved);
    patch({ levels: normalizePowerLevels(levels) });
    if (editingLevelIndex === index) setEditingLevelIndex(target);
  };

  const patchResource = (change: Partial<PowerResource>) => {
    const base: PowerResource = value.resource ?? {
      name: "",
      source: "",
      consumption: "",
      recovery: "",
    };
    patch({ resource: { ...base, ...change } });
  };

  const patchCounter = (index: number, change: Partial<PowerCounter>) => {
    const counters = value.counters.map((item, i) => (i === index ? { ...item, ...change } : item));
    patch({ counters });
  };

  const toggleSection = (key: SectionKey) =>
    setExpandedSections((prev) => ({ ...prev, [key]: !prev[key] }));

  const levelError = (index: number, field: "name" | "order") =>
    validationErrors.find((issue) => issue.field === `levels.${index}.${field}`)?.message;

  return (
    <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-foreground">战力体系</div>
          <div className="mt-0.5 text-xs text-muted">
            等级顺序在小说内唯一；已确认蓝图进入只读状态后需创建新版本才能修改
          </div>
        </div>
        {onGenerateSuggestion && (
          <Button variant="ghost" size="sm" onPress={onGenerateSuggestion} isDisabled={readOnly}>
            AI 补全战力体系
          </Button>
        )}
      </div>

      {suggestionHint && (
        <div className="rounded-lg border border-accent/40 bg-accent/5 px-3 py-2 text-xs text-muted">
          {suggestionHint}
        </div>
      )}

      <label className="block text-sm">
        <span className="mb-1 block text-muted">体系名称</span>
        <input
          className={INPUT_CLASS}
          value={value.name}
          disabled={readOnly}
          onChange={(event) => patch({ name: event.target.value })}
          placeholder="例如：灵力九阶"
        />
      </label>

      <label className="block text-sm">
        <span className="mb-1 block text-muted">体系描述</span>
        <textarea
          className={TEXTAREA_CLASS}
          value={value.description}
          disabled={readOnly}
          onChange={(event) => patch({ description: event.target.value })}
          placeholder="说明这套体系的来源、衡量方式与世界观定位"
        />
      </label>

      <div className="space-y-2">
        <SectionHeader
          section="levels"
          expanded={expandedSections.levels}
          onToggle={() => toggleSection("levels")}
          count={value.levels.length}
        />
        {expandedSections.levels && (
          <div className="space-y-2">
            {value.levels.length === 0 && (
              <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted">
                还没有等级，点击「新增等级」开始搭建
              </p>
            )}
            {value.levels.map((level, index) => (
              <div key={`${level.order}-${index}`} className="rounded-lg border border-border bg-surface p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-muted">#{index + 1}</span>
                  <input
                    className={`${COMPACT_INPUT_CLASS} flex-1 font-medium`}
                    value={level.name}
                    disabled={readOnly}
                    onChange={(event) => patchLevel(index, { name: event.target.value })}
                    placeholder={`第 ${index + 1} 阶名称`}
                  />
                  <label className="flex items-center gap-1 text-xs text-muted">
                    顺序
                    <input
                      type="number"
                      className={`${COMPACT_INPUT_CLASS} w-16 px-2`}
                      value={level.order}
                      disabled={readOnly}
                      onChange={(event) =>
                        patchLevel(index, { order: Number(event.target.value) || 0 })
                      }
                      min={0}
                    />
                  </label>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      className={ROW_BUTTON_CLASS}
                      disabled={readOnly || index === 0}
                      onClick={() => moveLevel(index, -1)}
                      title="上移"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className={ROW_BUTTON_CLASS}
                      disabled={readOnly || index === value.levels.length - 1}
                      onClick={() => moveLevel(index, 1)}
                      title="下移"
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      className={ROW_BUTTON_CLASS}
                      onClick={() =>
                        setEditingLevelIndex(editingLevelIndex === index ? null : index)
                      }
                    >
                      {editingLevelIndex === index ? "收起详情" : "编辑详情"}
                    </button>
                    <button
                      type="button"
                      className={DANGER_ROW_BUTTON_CLASS}
                      disabled={readOnly}
                      onClick={() => setLevelPendingDelete(index)}
                    >
                      删除
                    </button>
                  </div>
                </div>

                {(levelError(index, "name") || levelError(index, "order")) && (
                  <p className="mt-1 text-xs text-red-600">
                    {levelError(index, "name") ?? levelError(index, "order")}
                  </p>
                )}

                {editingLevelIndex === index && (
                  <div className="mt-3 space-y-2">
                    <label className="block text-sm">
                      <span className="mb-1 block text-muted">等级描述</span>
                      <textarea
                        className={`${TEXTAREA_CLASS} min-h-16`}
                        value={level.description}
                        disabled={readOnly}
                        onChange={(event) => patchLevel(index, { description: event.target.value })}
                      />
                    </label>
                    {(["requirements", "abilities", "limitations"] as const).map((field) => (
                      <label key={field} className="block text-sm">
                        <span className="mb-1 block text-muted">
                          {field === "requirements"
                            ? "晋升条件"
                            : field === "abilities"
                              ? "能力范围"
                              : "限制和代价"}
                          （{LIST_FIELD_HINT}）
                        </span>
                        <textarea
                          className={`${TEXTAREA_CLASS} min-h-14`}
                          value={level[field].join("\n")}
                          disabled={readOnly}
                          onChange={(event) =>
                            patchLevel(index, { [field]: splitLines(event.target.value) })
                          }
                        />
                      </label>
                    ))}
                  </div>
                )}
              </div>
            ))}

            <div className="flex flex-wrap items-center gap-2">
              <Button variant="ghost" size="sm" onPress={addLevel} isDisabled={readOnly}>
                新增等级
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onPress={() => patch({ levels: normalizePowerLevels(value.levels) })}
                isDisabled={readOnly || value.levels.length === 0}
              >
                重排顺序
              </Button>
            </div>
          </div>
        )}
      </div>

      <div className="space-y-2">
        <SectionHeader
          section="dimensions"
          expanded={expandedSections.dimensions}
          onToggle={() => toggleSection("dimensions")}
          count={value.power_dimensions.length}
        />
        {expandedSections.dimensions && (
          <label className="block text-sm">
            <span className="mb-1 block text-muted">力量维度（{LIST_FIELD_HINT}）</span>
            <textarea
              className={`${TEXTAREA_CLASS} min-h-16`}
              value={value.power_dimensions.join("\n")}
              disabled={readOnly}
              onChange={(event) => patch({ power_dimensions: splitLines(event.target.value) })}
              placeholder={"攻击\n防御\n速度"}
            />
          </label>
        )}
      </div>

      <div className="space-y-2">
        <SectionHeader
          section="resource"
          expanded={expandedSections.resource}
          onToggle={() => toggleSection("resource")}
          count={value.resource ? Object.values(value.resource).filter((item) => item?.trim()).length : 0}
        />
        {expandedSections.resource && (
          <div className="grid gap-3 sm:grid-cols-2">
            {(["name", "source", "consumption", "recovery"] as const).map((field) => (
              <label key={field} className="block text-sm">
                <span className="mb-1 block text-muted">
                  {field === "name"
                    ? "能量名称"
                    : field === "source"
                      ? "能量来源"
                      : field === "consumption"
                        ? "消耗方式"
                        : "恢复方式"}
                </span>
                <input
                  className={INPUT_CLASS}
                  value={value.resource?.[field] ?? ""}
                  disabled={readOnly}
                  onChange={(event) => patchResource({ [field]: event.target.value })}
                />
              </label>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <SectionHeader
          section="restrictions"
          expanded={expandedSections.restrictions}
          onToggle={() => toggleSection("restrictions")}
          count={value.restrictions.length}
        />
        {expandedSections.restrictions && (
          <label className="block text-sm">
            <span className="mb-1 block text-muted">使用限制（{LIST_FIELD_HINT}）</span>
            <textarea
              className={`${TEXTAREA_CLASS} min-h-16`}
              value={value.restrictions.join("\n")}
              disabled={readOnly}
              onChange={(event) => patch({ restrictions: splitLines(event.target.value) })}
            />
          </label>
        )}
      </div>

      <div className="space-y-2">
        <SectionHeader
          section="special"
          expanded={expandedSections.special}
          onToggle={() => toggleSection("special")}
          count={value.special_rules.length}
        />
        {expandedSections.special && (
          <label className="block text-sm">
            <span className="mb-1 block text-muted">特殊规则（{LIST_FIELD_HINT}）</span>
            <textarea
              className={`${TEXTAREA_CLASS} min-h-16`}
              value={value.special_rules.join("\n")}
              disabled={readOnly}
              onChange={(event) => patch({ special_rules: splitLines(event.target.value) })}
            />
          </label>
        )}
      </div>

      <div className="space-y-2">
        <SectionHeader
          section="counters"
          expanded={expandedSections.counters}
          onToggle={() => toggleSection("counters")}
          count={value.counters.length}
        />
        {expandedSections.counters && (
          <div className="space-y-2">
            {value.counters.map((counter, index) => (
              <div key={index} className="grid gap-2 sm:grid-cols-[1fr_1fr_2fr_auto]">
                <input
                  className={COMPACT_INPUT_CLASS}
                  value={counter.source}
                  disabled={readOnly}
                  onChange={(event) => patchCounter(index, { source: event.target.value })}
                  placeholder="克制方"
                />
                <input
                  className={COMPACT_INPUT_CLASS}
                  value={counter.target}
                  disabled={readOnly}
                  onChange={(event) => patchCounter(index, { target: event.target.value })}
                  placeholder="被克制方"
                />
                <input
                  className={COMPACT_INPUT_CLASS}
                  value={counter.description}
                  disabled={readOnly}
                  onChange={(event) => patchCounter(index, { description: event.target.value })}
                  placeholder="克制说明"
                />
                <button
                  type="button"
                  className={DANGER_ROW_BUTTON_CLASS}
                  disabled={readOnly}
                  onClick={() =>
                    patch({ counters: value.counters.filter((_, i) => i !== index) })
                  }
                >
                  删除
                </button>
              </div>
            ))}
            <Button
              variant="ghost"
              size="sm"
              onPress={() =>
                patch({
                  counters: [...value.counters, { source: "", target: "", description: "" }],
                })
              }
              isDisabled={readOnly}
            >
              新增克制关系
            </Button>
          </div>
        )}
      </div>

      {validationErrors.length > 0 && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
          <div className="font-medium">战力体系存在 {validationErrors.length} 个校验问题</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {validationErrors.map((issue, index) => (
              <li key={`${issue.field}-${index}`}>{issue.message}</li>
            ))}
          </ul>
        </div>
      )}

      {levelPendingDelete !== null && (
        <ConfirmActionModal
          title="删除战力等级"
          message={`确定删除「${value.levels[levelPendingDelete]?.name || `第 ${levelPendingDelete + 1} 阶`}」吗？删除后其余等级会自动重排。`}
          confirmText="删除"
          cancelText="取消"
          danger
          onCancel={() => setLevelPendingDelete(null)}
          onConfirm={() => removeLevel(levelPendingDelete)}
        />
      )}
    </div>
  );
}
