"use client";

import type { useTranslations } from "next-intl";
import TagInput from "@/components/shared/TagInput";
import type { ApiFieldErrors } from "@/lib/api";
import type {
  CharacterCreateRequestV1,
  CharacterProfileFields,
  CoreCharacterCandidateV1,
} from "@/types/character";

/** 角色表单可承接的后端人工创建请求或 AI 核心角色候选。 */
export type CharacterProfileSource = CharacterCreateRequestV1 | CoreCharacterCandidateV1;

interface CharacterProfileFormProps {
  value: CharacterProfileFields;
  characterRef?: CoreCharacterCandidateV1["character_ref"];
  disabled?: boolean;
  fieldErrors?: ApiFieldErrors;
  onChange: (value: CharacterProfileFields) => void;
  t: ReturnType<typeof useTranslations>;
}

const ROLE_TYPES: CharacterProfileFields["role_type"][] = [
  "protagonist",
  "deuteragonist",
  "antagonist",
  "supporting",
  "minor",
];

const IMPORTANCE_LEVELS: CharacterProfileFields["importance_level"][] = [
  "core",
  "major",
  "supporting",
  "background",
];

const INPUT_CLASS_NAME =
  "min-h-11 w-full rounded-xl border border-border bg-background/80 px-3.5 py-2.5 text-sm text-foreground outline-none transition-[border-color,box-shadow] placeholder:text-muted/55 focus:border-accent focus:ring-2 focus:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-60";

const TEXTAREA_CLASS_NAME = `${INPUT_CLASS_NAME} min-h-28 resize-y leading-6`;

/**
 * 渲染全书级角色的人工创建或 AI 候选编辑表单。
 *
 * Args:
 *   props: 角色业务字段、可选候选引用、禁用状态、字段更新回调和翻译函数。
 *
 * Returns:
 *   覆盖角色创建与候选白名单业务字段的响应式表单节点。
 */
export default function CharacterProfileForm({
  value,
  characterRef,
  disabled = false,
  fieldErrors = {},
  onChange,
  t,
}: CharacterProfileFormProps) {
  const updateField = <K extends keyof CharacterProfileFields>(
    field: K,
    nextValue: CharacterProfileFields[K],
  ) => {
    // 始终从白名单业务字段对象派生新值，表单不注入正式 ID、状态、结构引用或审计字段。
    onChange({ ...value, [field]: nextValue });
  };

  const removeLabel = (item: string) => t("tag.remove", { value: item });
  const errorFor = (field: keyof CharacterProfileFields) => fieldErrors[field]?.[0];

  return (
    <div className="space-y-6">
      <FormSection
        title={t("sections.basic")}
        description={t("sections.basicDescription")}
      >
        <div className="grid gap-5 md:grid-cols-2">
          {characterRef && (
            <ReadOnlyField
              label={t("fields.characterRef")}
              value={characterRef}
              className="md:col-span-2"
            />
          )}

          <TextField
            field="name"
            label={t("fields.name")}
            value={value.name}
            onChange={(nextValue) => updateField("name", nextValue)}
            placeholder={t("placeholders.name")}
            maxLength={80}
            disabled={disabled}
            required
            error={errorFor("name")}
          />

          <TagInput
            name="aliases"
            label={t("fields.aliases")}
            values={value.aliases}
            onChange={(nextValue) => updateField("aliases", nextValue)}
            getRemoveAriaLabel={removeLabel}
            placeholder={t("placeholders.aliases")}
            limitReachedText={t("tag.limitReached", { count: 10 })}
            maxItems={10}
            disabled={disabled}
            error={errorFor("aliases")}
          />

          <SelectField
            field="role_type"
            label={t("fields.roleType")}
            value={value.role_type}
            onChange={(nextValue) => updateField("role_type", nextValue)}
            disabled={disabled}
          >
            {ROLE_TYPES.map((roleType) => (
              <option key={roleType} value={roleType}>
                {t(`options.roleType.${roleType}`)}
              </option>
            ))}
          </SelectField>

          <SelectField
            field="importance_level"
            label={t("fields.importanceLevel")}
            value={value.importance_level}
            onChange={(nextValue) => updateField("importance_level", nextValue)}
            disabled={disabled}
          >
            {IMPORTANCE_LEVELS.map((importanceLevel) => (
              <option key={importanceLevel} value={importanceLevel}>
                {t(`options.importanceLevel.${importanceLevel}`)}
              </option>
            ))}
          </SelectField>

          <TextField
            field="gender"
            label={t("fields.gender")}
            value={value.gender}
            onChange={(nextValue) => updateField("gender", nextValue)}
            placeholder={t("placeholders.gender")}
            maxLength={80}
            disabled={disabled}
            required
            error={errorFor("gender")}
          />

          <TextField
            field="age_group"
            label={t("fields.ageGroup")}
            value={value.age_group}
            onChange={(nextValue) => updateField("age_group", nextValue)}
            placeholder={t("placeholders.ageGroup")}
            maxLength={80}
            disabled={disabled}
            required
            error={errorFor("age_group")}
          />

          <TextField
            field="race"
            label={t("fields.race")}
            value={value.race}
            onChange={(nextValue) => updateField("race", nextValue)}
            placeholder={t("placeholders.race")}
            maxLength={80}
            disabled={disabled}
            required
            error={errorFor("race")}
          />

          <TextAreaField
            field="identity"
            label={t("fields.identity")}
            value={value.identity}
            onChange={(nextValue) => updateField("identity", nextValue)}
            placeholder={t("placeholders.identity")}
            maxLength={500}
            disabled={disabled}
            required
            error={errorFor("identity")}
            className="md:col-span-2"
          />
        </div>
      </FormSection>

      <FormSection
        title={t("sections.profile")}
        description={t("sections.profileDescription")}
      >
        <div className="grid gap-5 md:grid-cols-2">
          <TextAreaField
            field="appearance"
            label={t("fields.appearance")}
            value={value.appearance}
            onChange={(nextValue) => updateField("appearance", nextValue)}
            placeholder={t("placeholders.appearance")}
            maxLength={1000}
            disabled={disabled}
            required
            error={errorFor("appearance")}
          />

          <TextAreaField
            field="personality"
            label={t("fields.personality")}
            value={value.personality}
            onChange={(nextValue) => updateField("personality", nextValue)}
            placeholder={t("placeholders.personality")}
            maxLength={1000}
            disabled={disabled}
            required
            error={errorFor("personality")}
          />

          <TagInput
            name="strengths"
            label={t("fields.strengths")}
            values={value.strengths}
            onChange={(nextValue) => updateField("strengths", nextValue)}
            getRemoveAriaLabel={removeLabel}
            placeholder={t("placeholders.strengths")}
            limitReachedText={t("tag.limitReached", { count: 12 })}
            maxItems={12}
            disabled={disabled}
            error={errorFor("strengths")}
          />

          <TagInput
            name="weaknesses"
            label={t("fields.weaknesses")}
            values={value.weaknesses}
            onChange={(nextValue) => updateField("weaknesses", nextValue)}
            getRemoveAriaLabel={removeLabel}
            placeholder={t("placeholders.weaknesses")}
            limitReachedText={t("tag.limitReached", { count: 12 })}
            maxItems={12}
            disabled={disabled}
            error={errorFor("weaknesses")}
          />

          <TagInput
            name="abilities"
            label={t("fields.abilities")}
            values={value.abilities}
            onChange={(nextValue) => updateField("abilities", nextValue)}
            getRemoveAriaLabel={removeLabel}
            placeholder={t("placeholders.abilities")}
            limitReachedText={t("tag.limitReached", { count: 12 })}
            maxItems={12}
            disabled={disabled}
            error={errorFor("abilities")}
            className="md:col-span-2"
          />
        </div>
      </FormSection>

      <FormSection
        title={t("sections.motivation")}
        description={t("sections.motivationDescription")}
      >
        <div className="grid gap-5 md:grid-cols-2">
          <TextAreaField
            field="core_desire"
            label={t("fields.coreDesire")}
            value={value.core_desire}
            onChange={(nextValue) => updateField("core_desire", nextValue)}
            placeholder={t("placeholders.coreDesire")}
            maxLength={1000}
            disabled={disabled}
            required
            error={errorFor("core_desire")}
          />

          <TextAreaField
            field="core_fear"
            label={t("fields.coreFear")}
            value={value.core_fear}
            onChange={(nextValue) => updateField("core_fear", nextValue)}
            placeholder={t("placeholders.coreFear")}
            maxLength={1000}
            disabled={disabled}
            required
            error={errorFor("core_fear")}
          />

          <TextAreaField
            field="conflict_with_mainline"
            label={t("fields.conflictWithMainline")}
            value={value.conflict_with_mainline}
            onChange={(nextValue) => updateField("conflict_with_mainline", nextValue)}
            placeholder={t("placeholders.conflictWithMainline")}
            maxLength={1000}
            disabled={disabled}
            required
            error={errorFor("conflict_with_mainline")}
          />

          <TextAreaField
            field="relationship_with_protagonist"
            label={t("fields.relationshipWithProtagonist")}
            value={value.relationship_with_protagonist}
            onChange={(nextValue) => updateField("relationship_with_protagonist", nextValue)}
            placeholder={t("placeholders.relationshipWithProtagonist")}
            maxLength={1000}
            disabled={disabled}
            required
            error={errorFor("relationship_with_protagonist")}
          />
        </div>
      </FormSection>

      <FormSection
        title={t("sections.story")}
        description={t("sections.storyDescription")}
      >
        <div className="grid gap-5 md:grid-cols-2">
          <TextAreaField
            field="initial_state"
            label={t("fields.initialState")}
            value={value.initial_state}
            onChange={(nextValue) => updateField("initial_state", nextValue)}
            placeholder={t("placeholders.initialState")}
            maxLength={1000}
            disabled={disabled}
            required
            error={errorFor("initial_state")}
          />

          <TextAreaField
            field="growth_direction"
            label={t("fields.growthDirection")}
            value={value.growth_direction}
            onChange={(nextValue) => updateField("growth_direction", nextValue)}
            placeholder={t("placeholders.growthDirection")}
            maxLength={1000}
            disabled={disabled}
            required
            error={errorFor("growth_direction")}
          />

          <TextAreaField
            field="story_function"
            label={t("fields.storyFunction")}
            value={value.story_function}
            onChange={(nextValue) => updateField("story_function", nextValue)}
            placeholder={t("placeholders.storyFunction")}
            maxLength={1000}
            disabled={disabled}
            required
            error={errorFor("story_function")}
          />

          <TextAreaField
            field="arc_seed"
            label={t("fields.arcSeed")}
            value={value.arc_seed}
            onChange={(nextValue) => updateField("arc_seed", nextValue)}
            placeholder={t("placeholders.arcSeed")}
            maxLength={1000}
            disabled={disabled}
            required
            error={errorFor("arc_seed")}
          />

          <TagInput
            name="tags"
            label={t("fields.tags")}
            values={value.tags}
            onChange={(nextValue) => updateField("tags", nextValue)}
            getRemoveAriaLabel={removeLabel}
            placeholder={t("placeholders.tags")}
            limitReachedText={t("tag.limitReached", { count: 10 })}
            maxItems={10}
            disabled={disabled}
            error={errorFor("tags")}
            className="md:col-span-2"
          />
        </div>
      </FormSection>
    </div>
  );
}

interface FormSectionProps {
  title: string;
  description: string;
  children: React.ReactNode;
}

/**
 * 渲染角色表单的语义分组容器。
 *
 * Args:
 *   props: 分组标题、说明和字段节点。
 *
 * Returns:
 *   带分组标题及响应式内容区的表单章节。
 */
function FormSection({ title, description, children }: FormSectionProps) {
  return (
    <section className="border-b border-border pb-7 last:border-b-0 last:pb-0">
      <div className="mb-5">
        <h3 className="text-base font-semibold text-foreground">{title}</h3>
        <p className="mt-1 text-sm leading-6 text-muted">{description}</p>
      </div>
      {children}
    </section>
  );
}

interface FieldShellProps {
  label: string;
  children: React.ReactNode;
  className?: string;
  error?: string;
  errorId?: string;
}

/**
 * 渲染具有统一标题与间距的角色字段外壳。
 *
 * Args:
 *   props: 字段标题、输入节点和可选布局类名。
 *
 * Returns:
 *   统一样式的字段容器。
 */
function FieldShell({ label, children, className = "", error, errorId }: FieldShellProps) {
  return (
    <label className={`grid gap-2 text-sm ${className}`}>
      <span className="text-xs font-semibold tracking-wide text-muted">{label}</span>
      {children}
      {error && <span id={errorId} className="text-xs text-red-600 dark:text-red-300">{error}</span>}
    </label>
  );
}

interface TextFieldProps {
  field: keyof CharacterProfileFields;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  maxLength?: number;
  disabled?: boolean;
  required?: boolean;
  className?: string;
  error?: string;
}

/**
 * 渲染角色表单的单行文本字段。
 *
 * Args:
 *   props: 字段标题、值、更新回调及原生输入限制。
 *
 * Returns:
 *   统一视觉与禁用行为的文本输入节点。
 */
function TextField({
  field,
  label,
  value,
  onChange,
  placeholder,
  maxLength,
  disabled,
  required,
  className,
  error,
}: TextFieldProps) {
  const errorId = `character-${field}-error`;
  return (
    <FieldShell label={label} className={className} error={error} errorId={errorId}>
      <input
        name={field}
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        maxLength={maxLength}
        disabled={disabled}
        required={required}
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={error ? errorId : undefined}
        className={INPUT_CLASS_NAME}
      />
    </FieldShell>
  );
}

interface TextAreaFieldProps extends TextFieldProps {
  rows?: number;
}

/**
 * 渲染角色表单的多行叙述字段。
 *
 * Args:
 *   props: 字段标题、值、更新回调、行数及文本长度限制。
 *
 * Returns:
 *   可纵向调整高度的多行输入节点。
 */
function TextAreaField({
  field,
  label,
  value,
  onChange,
  placeholder,
  maxLength,
  disabled,
  required,
  className,
  error,
  rows = 4,
}: TextAreaFieldProps) {
  const errorId = `character-${field}-error`;
  return (
    <FieldShell label={label} className={className} error={error} errorId={errorId}>
      <textarea
        name={field}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        maxLength={maxLength}
        disabled={disabled}
        required={required}
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={error ? errorId : undefined}
        rows={rows}
        className={TEXTAREA_CLASS_NAME}
      />
    </FieldShell>
  );
}

interface SelectFieldProps<TValue extends string> {
  field: keyof CharacterProfileFields;
  label: string;
  value: TValue;
  onChange: (value: TValue) => void;
  disabled?: boolean;
  children: React.ReactNode;
  error?: string;
}

/**
 * 渲染角色枚举字段的原生选择器。
 *
 * Args:
 *   props: 字段标题、枚举值、更新回调、选项节点和禁用状态。
 *
 * Returns:
 *   保留强类型枚举值的选择器节点。
 */
function SelectField<TValue extends string>({
  field,
  label,
  value,
  onChange,
  disabled,
  children,
  error,
}: SelectFieldProps<TValue>) {
  const errorId = `character-${field}-error`;
  return (
    <FieldShell label={label} error={error} errorId={errorId}>
      <select
        name={field}
        value={value}
        onChange={(event) => onChange(event.target.value as TValue)}
        disabled={disabled}
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={error ? errorId : undefined}
        className={INPUT_CLASS_NAME}
      >
        {children}
      </select>
    </FieldShell>
  );
}

interface ReadOnlyFieldProps {
  label: string;
  value: string;
  className?: string;
}

/**
 * 渲染 AI 候选的只读局部引用，避免用户修改关系绑定依据。
 *
 * Args:
 *   props: 字段标题、候选局部引用和可选布局类名。
 *
 * Returns:
 *   只读且可复制的候选引用输入节点。
 */
function ReadOnlyField({ label, value, className }: ReadOnlyFieldProps) {
  return (
    <FieldShell label={label} className={className}>
      <input
        type="text"
        value={value}
        readOnly
        aria-readonly="true"
        className={`${INPUT_CLASS_NAME} cursor-default bg-surface-secondary/65 font-mono text-xs`}
      />
    </FieldShell>
  );
}
