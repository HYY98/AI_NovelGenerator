"use client";

import { useMemo, type ReactNode } from "react";
import type { useTranslations } from "next-intl";
import { Button, Modal } from "@heroui/react";
import {
  GenerationParamsCollapse,
  type GenerationParamsValue,
} from "@/components/shared/GenerationParamsCollapse";
import type { CharacterResponseV1 } from "@/types/character";

/** 核心角色生成表单草稿。 */
export interface CoreGenerationDraft {
  count: number;
  guidance: string;
  params: GenerationParamsValue;
}

/** 人物关系生成表单草稿。 */
export interface RelationGenerationDraft {
  selectedCharacterIds: string[];
  allowIsolatedCharacters: boolean;
  relationCountLimit: string;
  guidance: string;
  params: GenerationParamsValue;
}

/** 核心角色生成对话框的公开属性。 */
export interface CoreGenerationDialogProps {
  value: CoreGenerationDraft;
  paramsOpen: boolean;
  onChange: (value: CoreGenerationDraft) => void;
  onParamsOpenChange: (isOpen: boolean) => void;
  onCancel: () => void;
  onSubmit: () => void;
  t: ReturnType<typeof useTranslations>;
}

/** 人物关系生成对话框的公开属性。 */
export interface RelationGenerationDialogProps {
  value: RelationGenerationDraft;
  characters: CharacterResponseV1[];
  paramsOpen: boolean;
  onChange: (value: RelationGenerationDraft) => void;
  onParamsOpenChange: (isOpen: boolean) => void;
  onCancel: () => void;
  onSubmit: () => void;
  t: ReturnType<typeof useTranslations>;
}

const INPUT_CLASS_NAME =
  "min-h-11 w-full rounded-xl border border-border bg-background/80 px-3.5 py-2.5 text-sm text-foreground outline-none transition-[border-color,box-shadow] placeholder:text-muted/55 focus:border-accent focus:ring-2 focus:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-60";

const TEXTAREA_CLASS_NAME = `${INPUT_CLASS_NAME} min-h-28 resize-y leading-6`;

/**
 * 判断一个可选数值草稿是否位于后端允许的闭区间。
 *
 * Args:
 *   value: 可为空的数值输入字符串。
 *   minimum: 允许的最小值。
 *   maximum: 允许的最大值。
 *   integerOnly: 是否要求输入为整数。
 *
 * Returns:
 *   空字符串或合法区间数值返回 true，否则返回 false。
 */
function isOptionalNumberValid(
  value: string,
  minimum: number,
  maximum: number,
  integerOnly = false,
): boolean {
  if (value === "") return true;
  const parsed = Number(value);
  return (
    Number.isFinite(parsed) &&
    parsed >= minimum &&
    parsed <= maximum &&
    (!integerOnly || Number.isInteger(parsed))
  );
}

/**
 * 渲染全书级核心角色生成参数对话框。
 *
 * Args:
 *   props: 当前草稿、草稿更新回调、取消与提交动作，以及角色模块翻译函数。
 *
 * Returns:
 *   支持数量、指导文本和高级生成参数的模态对话框。
 */
export function CoreGenerationDialog({
  value,
  paramsOpen,
  onChange,
  onParamsOpenChange,
  onCancel,
  onSubmit,
  t,
}: CoreGenerationDialogProps) {
  const canSubmit =
    Number.isInteger(value.count) &&
    value.count >= 1 &&
    value.count <= 50;

  return (
    <DialogShell
      eyebrow={t("generation.characterEyebrow")}
      title={t("generation.characterTitle")}
      description={t("generation.characterDescription")}
      cancelLabel={t("actions.cancel")}
      onCancel={onCancel}
      footer={(
        <>
          <Button variant="ghost" size="sm" onPress={onCancel}>
            {t("actions.cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            className="bg-accent text-white hover:bg-accent-hover"
            onPress={onSubmit}
            isDisabled={!canSubmit}
          >
            {t("generation.submitCharacters")}
          </Button>
        </>
      )}
    >
      <div className="grid gap-5">
        <FieldShell label={t("generation.requestedCharacterCount")} hint="1–50">
          <input
            type="number"
            min={1}
            max={50}
            step={1}
            value={value.count}
            onChange={(event) => {
              const nextCount = Number(event.target.value);
              if (!Number.isFinite(nextCount)) return;
              onChange({ ...value, count: nextCount });
            }}
            className={INPUT_CLASS_NAME}
          />
        </FieldShell>

        <GuidanceField
          label={t("generation.userGuidance")}
          value={value.guidance}
          onChange={(guidance) => onChange({ ...value, guidance })}
        />

        <GenerationParamsCollapse
          value={value.params}
          onChange={(params) => onChange({ ...value, params })}
          isOpen={paramsOpen}
          onOpenChange={onParamsOpenChange}
          panelClassName="rounded-none border-x-0 border-b-0 bg-transparent px-0"
        />
      </div>
    </DialogShell>
  );
}

/**
 * 渲染全书级人物关系生成参数对话框。
 *
 * Args:
 *   props: 关系草稿、活动核心角色、草稿更新回调、取消与提交动作及翻译函数。
 *
 * Returns:
 *   支持角色多选、孤立规则、数量上限和高级参数的模态对话框。
 */
export function RelationGenerationDialog({
  value,
  characters,
  paramsOpen,
  onChange,
  onParamsOpenChange,
  onCancel,
  onSubmit,
  t,
}: RelationGenerationDialogProps) {
  const selectedCharacterIdSet = useMemo(
    () => new Set(value.selectedCharacterIds),
    [value.selectedCharacterIds],
  );
  const selectedCharacterCount = value.selectedCharacterIds.length;
  const maximumRelationCount = selectedCharacterCount * (selectedCharacterCount - 1);
  const minimumRelationCount = value.allowIsolatedCharacters
    ? 1
    : Math.ceil(selectedCharacterCount / 2);
  const countLimitValid =
    value.relationCountLimit === "" ||
    (isOptionalNumberValid(value.relationCountLimit, 1, 500, true) &&
      Number(value.relationCountLimit) <= maximumRelationCount &&
      Number(value.relationCountLimit) >= minimumRelationCount);
  const canSubmit =
    selectedCharacterCount >= 2 &&
    selectedCharacterCount <= 100 &&
    selectedCharacterIdSet.size === selectedCharacterCount &&
    countLimitValid;

  /**
   * 切换一个参与关系生成的核心角色。
   *
   * Args:
   *   characterId: 待切换的正式角色业务 ID。
   *
   * Returns:
   *   无；通过 onChange 提交新的关系生成草稿。
   */
  const toggleCharacter = (characterId: string) => {
    const alreadySelected = selectedCharacterIdSet.has(characterId);
    if (!alreadySelected && value.selectedCharacterIds.length >= 100) return;
    const selectedCharacterIds = alreadySelected
      ? value.selectedCharacterIds.filter((item) => item !== characterId)
      : [...value.selectedCharacterIds, characterId];
    onChange({ ...value, selectedCharacterIds });
  };

  return (
    <DialogShell
      eyebrow={t("generation.relationEyebrow")}
      title={t("generation.relationTitle")}
      description={t("generation.relationDescription")}
      cancelLabel={t("actions.cancel")}
      onCancel={onCancel}
      wide
      footer={(
        <>
          <Button variant="ghost" size="sm" onPress={onCancel}>
            {t("actions.cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            className="bg-accent text-white hover:bg-accent-hover"
            onPress={onSubmit}
            isDisabled={!canSubmit}
          >
            {t("generation.submitRelations")}
          </Button>
        </>
      )}
    >
      <div className="grid gap-6">
        <fieldset className="grid gap-3">
          <legend className="text-xs font-semibold tracking-wide text-muted">
            {t("generation.selectedCharacters")} ({selectedCharacterCount}/100)
          </legend>
          <div className="grid max-h-56 gap-2 overflow-y-auto rounded-xl border border-border bg-background/55 p-3 sm:grid-cols-2">
            {characters.map((character) => {
              const selected = selectedCharacterIdSet.has(character.character_id);
              const selectionLimitReached = !selected && selectedCharacterCount >= 100;
              return (
                <CheckboxRow
                  key={character.character_id}
                  checked={selected}
                  disabled={selectionLimitReached}
                  onChange={() => toggleCharacter(character.character_id)}
                  title={character.name}
                  detail={character.character_id}
                />
              );
            })}
          </div>
        </fieldset>

        <div className="grid gap-5 md:grid-cols-2">
          <ToggleField
            label={t("generation.allowIsolated")}
            checked={value.allowIsolatedCharacters}
            onChange={(allowIsolatedCharacters) =>
              onChange({ ...value, allowIsolatedCharacters })
            }
          />

          <FieldShell
            label={t("generation.relationCountLimit")}
            hint={selectedCharacterCount >= 2 ? `${minimumRelationCount}–${Math.min(500, maximumRelationCount)}` : "1–500"}
          >
            <input
              type="number"
              min={selectedCharacterCount >= 2 ? minimumRelationCount : 1}
              max={selectedCharacterCount >= 2 ? Math.min(500, maximumRelationCount) : 500}
              step={1}
              value={value.relationCountLimit}
              onChange={(event) =>
                onChange({ ...value, relationCountLimit: event.target.value })
              }
              className={INPUT_CLASS_NAME}
            />
          </FieldShell>
        </div>

        <GuidanceField
          label={t("generation.userGuidance")}
          value={value.guidance}
          onChange={(guidance) => onChange({ ...value, guidance })}
        />

        <GenerationParamsCollapse
          value={value.params}
          onChange={(params) => onChange({ ...value, params })}
          isOpen={paramsOpen}
          onOpenChange={onParamsOpenChange}
          panelClassName="rounded-none border-x-0 border-b-0 bg-transparent px-0"
        />
      </div>
    </DialogShell>
  );
}

interface DialogShellProps {
  eyebrow: string;
  title: string;
  description: string;
  cancelLabel: string;
  children: ReactNode;
  footer: ReactNode;
  onCancel: () => void;
  wide?: boolean;
}

/**
 * 渲染生成表单共用的可访问模态容器。
 *
 * Args:
 *   props: 标题、说明、内容、底部动作、关闭回调与宽版开关。
 *
 * Returns:
 *   支持遮罩点击和 Escape 关闭的模态对话框节点。
 */
function DialogShell({
  eyebrow,
  title,
  description,
  cancelLabel,
  children,
  footer,
  onCancel,
  wide = false,
}: DialogShellProps) {
  return (
      <Modal.Backdrop isOpen onOpenChange={(isOpen) => !isOpen && onCancel()} variant="blur" isDismissable>
        <Modal.Container size={wide ? "lg" : "md"} scroll="inside" className="px-3 sm:px-6">
          <Modal.Dialog className={`w-full ${wide ? "max-w-4xl" : "max-w-3xl"}`}>
            <Modal.Header className="flex items-start justify-between gap-3 border-b border-border px-5 py-4 sm:px-6">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">
              {eyebrow}
            </p>
            <Modal.Heading className="mt-1 text-lg font-semibold text-foreground">
              {title}
            </Modal.Heading>
            <p className="mt-1 text-sm leading-6 text-muted">
              {description}
            </p>
          </div>
          <Modal.CloseTrigger aria-label={cancelLabel} />
            </Modal.Header>
            <Modal.Body className="min-h-0 px-5 py-6 sm:px-6">{children}</Modal.Body>
            <Modal.Footer className="flex justify-end gap-2 border-t border-border px-5 py-4 sm:px-6">
              {footer}
            </Modal.Footer>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
  );
}

interface FieldShellProps {
  label: string;
  children: ReactNode;
  hint?: string;
}

/**
 * 渲染具有统一标签、提示与间距的生成参数字段。
 *
 * Args:
 *   props: 字段标题、输入节点与可选范围提示。
 *
 * Returns:
 *   统一样式的字段标签节点。
 */
function FieldShell({ label, children, hint }: FieldShellProps) {
  return (
    <label className="grid gap-2 text-sm">
      <span className="flex items-center justify-between gap-3 text-xs font-semibold tracking-wide text-muted">
        <span>{label}</span>
        {hint && <span className="font-normal tabular-nums">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

interface GuidanceFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
}

/**
 * 渲染最长 2000 字符的生成指导文本区。
 *
 * Args:
 *   props: 字段标题、当前指导文本与更新回调。
 *
 * Returns:
 *   符合后端 user_guidance 长度限制的文本区。
 */
function GuidanceField({
  label,
  value,
  onChange,
}: GuidanceFieldProps) {
  return (
    <FieldShell label={label} hint={`${value.length}/2000`}>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        maxLength={2000}
        rows={4}
        className={TEXTAREA_CLASS_NAME}
      />
    </FieldShell>
  );
}

interface ToggleFieldProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

/**
 * 渲染布尔生成选项的统一复选框行。
 *
 * Args:
 *   props: 选项标题、当前布尔值与更新回调。
 *
 * Returns:
 *   具有完整可点击区域的复选框节点。
 */
function ToggleField({
  label,
  checked,
  onChange,
}: ToggleFieldProps) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-xl border border-border bg-background/55 px-4 py-3 text-sm text-foreground">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 accent-[var(--color-accent)]"
      />
      <span className="font-medium">{label}</span>
    </label>
  );
}

interface CheckboxRowProps {
  checked: boolean;
  onChange: () => void;
  title: string;
  detail: string;
  disabled?: boolean;
}

/**
 * 渲染角色或既有关系多选列表中的一行。
 *
 * Args:
 *   props: 选择状态、切换回调、主副文本与可选禁用状态。
 *
 * Returns:
 *   可点击且支持键盘操作的原生复选框行。
 */
function CheckboxRow({ checked, onChange, title, detail, disabled = false }: CheckboxRowProps) {
  return (
    <label
      className={`flex items-start gap-3 rounded-lg border px-3 py-2.5 text-sm transition-colors ${
        checked
          ? "border-accent/45 bg-accent/8"
          : "border-transparent bg-surface-secondary/45 hover:border-border"
      } ${disabled ? "cursor-not-allowed opacity-55" : "cursor-pointer"}`}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        disabled={disabled}
        className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-accent)]"
      />
      <span className="min-w-0">
        <span className="block truncate font-medium text-foreground">{title}</span>
        <span className="mt-0.5 block truncate text-xs text-muted">{detail}</span>
      </span>
    </label>
  );
}
