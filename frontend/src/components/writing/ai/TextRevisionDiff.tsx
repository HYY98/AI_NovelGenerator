"use client";

import { useState } from "react";
import { Button } from "@heroui/react";
import { AIWarnings } from "@/components/writing/ai/AIShared";
import {
  TEXT_REVISION_STATUS_LABEL,
  TEXT_REVISION_TYPE_LABEL,
  checkTextRevisionApplicable,
  type TextRevision,
} from "@/types/textRevision";

interface TextRevisionDiffProps {
  revision: TextRevision;
  index: number;
  currentChapterVersion: number;
  chapterContent: string;
  onAccept: (revision: TextRevision, afterText: string) => void;
  onReject: (revision: TextRevision) => void;
  disabled?: boolean;
}

const STATUS_CLASS: Record<TextRevision["status"], string> = {
  pending: "bg-surface-secondary text-muted",
  accepted: "bg-emerald-50 text-emerald-700",
  rejected: "bg-surface-secondary text-muted",
  conflicted: "bg-red-50 text-red-600",
};

/**
 * 功能：单条正文修改建议的差异对比卡片。
 *
 * 并排展示 before / after，允许用户在采纳前继续编辑修改后文本；
 * 当章节版本变化或目标范围原文不匹配时禁止采纳，避免覆盖用户已有的正文。
 */
function TextRevisionDiffCard({
  revision,
  index,
  currentChapterVersion,
  chapterContent,
  onAccept,
  onReject,
  disabled = false,
}: TextRevisionDiffProps) {
  const [afterText, setAfterText] = useState(revision.after_text);

  const applicable = checkTextRevisionApplicable(
    revision,
    currentChapterVersion,
    chapterContent
  );
  const dirty = afterText !== revision.after_text;

  return (
    <div className="rounded-lg border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-foreground">#{index + 1}</span>
        <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-xs text-muted">
          {TEXT_REVISION_TYPE_LABEL[revision.revision_type] ?? revision.revision_type}
        </span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs ${STATUS_CLASS[revision.status]}`}
        >
          {TEXT_REVISION_STATUS_LABEL[revision.status] ?? revision.status}
        </span>
        <span className="text-xs text-muted">
          范围 [{revision.target_range.start}, {revision.target_range.end}) · 基于 v
          {revision.chapter_version}
        </span>
      </div>

      <p className="mt-2 text-xs text-muted">理由：{revision.reason}</p>

      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <div className="rounded-lg border border-border bg-surface-secondary p-2">
          <div className="mb-1 text-xs text-muted">修改前</div>
          <p className="whitespace-pre-wrap text-xs text-foreground">{revision.before_text}</p>
        </div>
        <div className="rounded-lg border border-accent/40 bg-accent/5 p-2">
          <div className="mb-1 text-xs text-muted">修改后（采纳前可继续编辑）</div>
          <textarea
            className="min-h-20 w-full rounded-lg border border-border bg-surface px-2 py-1 text-xs"
            value={afterText}
            disabled={disabled || revision.status !== "pending"}
            onChange={(event) => setAfterText(event.target.value)}
          />
        </div>
      </div>

      {revision.warnings && revision.warnings.length > 0 && (
        <div className="mt-2">
          <AIWarnings warnings={revision.warnings} />
        </div>
      )}

      {!applicable.ok && (
        <p className="mt-2 rounded-lg border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-600">
          {applicable.reason}
        </p>
      )}

      {revision.status === "pending" && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            onPress={() => onAccept(revision, afterText)}
            isDisabled={disabled || !applicable.ok}
          >
            采纳并写入
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onPress={() => onReject(revision)}
            isDisabled={disabled}
          >
            忽略
          </Button>
          {dirty && <span className="text-xs text-muted">已手动调整修改后文本</span>}
        </div>
      )}

      {revision.status === "accepted" && (
        <p className="mt-2 text-xs text-emerald-700">已采纳并写入正文</p>
      )}
      {revision.status === "conflicted" && (
        <p className="mt-2 text-xs text-red-600">正文已变化，该建议失效</p>
      )}
    </div>
  );
}

/**
 * 功能：正文修改建议卡片的对外入口。
 *
 * 用建议 ID 与原始修改后文本作为 key，使底层草稿状态随建议变化自动重建，
 * 避免在 effect 中同步派生状态。
 */
export default function TextRevisionDiff(props: TextRevisionDiffProps) {
  return (
    <TextRevisionDiffCard
      key={`${props.revision.revision_id}:${props.revision.after_text}`}
      {...props}
    />
  );
}
