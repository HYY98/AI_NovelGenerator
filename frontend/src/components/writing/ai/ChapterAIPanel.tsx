"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ApiRequestError } from "@/lib/api";
import type {
  ChapterDraft,
  ChapterPlan,
  ConsistencyReview,
  FinalizeReviewResult,
  ReviewIssue,
} from "@/lib/aiTypes";
import { newRequestId } from "@/lib/aiTypes";
import {
  compressChapterSelection,
  expandChapterSelection,
  finalizeCommit,
  finalizeReview,
  generateChapterContinue,
  generateChapterDraft,
  generateChapterPlan,
  reviewChapter,
  rewriteChapterSelection,
} from "@/lib/chapterAiApi";
import { AIButton, AISection, AIWarnings, SeverityBadge } from "./AIShared";
import ChapterSettingSyncPanel from "./ChapterSettingSyncPanel";

/** 章节 AI 面板需要的最小章节信息。 */
export interface ChapterAITarget {
  _id: string;
  chapter_id: string;
  number: number;
  title: string;
  content: string;
  status: string;
  version: number;
  is_deleted?: boolean;
}

/** 候选内容被采纳后回写给编辑器的补丁。 */
export interface ChapterAIApplyPatch {
  /** 整篇替换正文。 */
  replaceAll?: string;
  /** 追加到正文末尾。 */
  append?: string;
  /** 替换选中片段。 */
  replaceRange?: { start: number; end: number; text: string };
  title?: string;
  summary?: string;
  unresolvedThreads?: string[];
  /** 章节蓝图（采纳章节方案时写入）。 */
  blueprint?: ChapterPlan;
}

interface ChapterAIPanelProps {
  novelId?: string;
  chapter: ChapterAITarget;
  selection: { start: number; end: number; text: string } | null;
  /** 使用 AI 前先落库当前编辑内容，避免基于旧正文生成。 */
  flush: () => Promise<boolean>;
  onApply: (patch: ChapterAIApplyPatch) => void;
  /** 定稿成功后重新加载章节。 */
  onReload: () => void;
  onError: (message: string) => void;
  /** 父组件（状态下拉选择「已定稿」时）递增该值即可自动进入定稿确认流程。 */
  finalizeSignal?: number;
}

/**
 * 功能：章节 AI 写作助手面板。
 *
 * 所有 AI 输出都先进入预览区，只有用户点击「采用」才会写回正文，
 * 从流程上杜绝 AI 直接覆盖用户正文。
 */
export default function ChapterAIPanel({
  novelId,
  chapter,
  selection,
  flush,
  onApply,
  onReload,
  onError,
  finalizeSignal,
}: ChapterAIPanelProps) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [conflicts, setConflicts] = useState<string[]>([]);
  const [userPrompt, setUserPrompt] = useState("");
  const [instruction, setInstruction] = useState("");

  const [plan, setPlan] = useState<ChapterPlan | null>(null);
  const [draftKind, setDraftKind] = useState<"draft" | "continue">("draft");
  const [draft, setDraft] = useState<ChapterDraft | null>(null);
  const [segment, setSegment] = useState<string | null>(null);
  const [review, setReview] = useState<ConsistencyReview | null>(null);
  const [finalizeData, setFinalizeData] = useState<FinalizeReviewResult | null>(null);
  const [acceptedEvents, setAcceptedEvents] = useState<Record<string, boolean>>({});
  const [ignoredIssues, setIgnoredIssues] = useState<Record<string, boolean>>({});
  const [forceFinalize, setForceFinalize] = useState(false);
  /** 增量新增：本章设定同步面板的展开状态。 */
  const [showSettingSync, setShowSettingSync] = useState(false);

  const readOnly = chapter.status === "finalized" || !!chapter.is_deleted;
  const selectionText = selection?.text ?? "";
  const busyLabel = useMemo(() => {
    const labels: Record<string, string> = {
      plan: "正在生成章节方案…",
      draft: "正在生成本章正文…",
      continue: "正在续写…",
      rewrite: "正在改写选段…",
      expand: "正在扩写选段…",
      compress: "正在压缩选段…",
      review: "正在做一致性审校…",
      finalizeReview: "正在审校并提取状态变更…",
      finalizeCommit: "正在定稿落库…",
    };
    return labels[busy] ?? "";
  }, [busy]);

  const run = async (label: string, task: () => Promise<void>) => {
    setError("");
    setInfo("");
    setBusy(label);
    try {
      await task();
    } catch (err) {
      const message =
        err instanceof ApiRequestError
          ? err.message
          : err instanceof Error
            ? err.message
            : "AI 操作失败";
      setError(message);
      onError(message);
    } finally {
      setBusy("");
    }
  };

  /** 使用 AI 前确保本地改动已落库。 */
  const ensureSaved = async (): Promise<boolean> => {
    const ok = await flush();
    if (!ok) {
      setError("正文还有未保存的改动，请先处理保存冲突后再使用 AI 功能");
      return false;
    }
    return true;
  };

  const applyGenerationMeta = (nextWarnings: string[], nextConflicts: string[]) => {
    setWarnings(nextWarnings);
    setConflicts(nextConflicts);
  };

  const doPlan = () =>
    run("plan", async () => {
      if (!novelId) return;
      if (!(await ensureSaved())) return;
      const res = await generateChapterPlan(chapter._id, novelId, {
        request_id: newRequestId("plan"),
        user_prompt: userPrompt,
      });
      setPlan(res.data);
      applyGenerationMeta(res.warnings, res.conflicts);
      setInfo(res.reused ? "已复用上一次的章节方案候选" : "章节方案候选已生成，确认后可采纳为本章蓝图");
    });

  const doDraft = () =>
    run("draft", async () => {
      if (!novelId) return;
      if (!plan) {
        setError("请先生成并确认章节方案，再生成正文");
        return;
      }
      if (!(await ensureSaved())) return;
      const res = await generateChapterDraft(chapter._id, novelId, plan, {
        request_id: newRequestId("draft"),
        user_prompt: userPrompt,
      });
      setDraftKind("draft");
      setDraft(res.data);
      applyGenerationMeta(res.warnings, res.conflicts);
      setInfo("正文候选已生成，采用后才会写入章节正文");
    });

  const doContinue = () =>
    run("continue", async () => {
      if (!novelId) return;
      if (!(await ensureSaved())) return;
      const res = await generateChapterContinue(chapter._id, novelId, plan, {
        request_id: newRequestId("cont"),
        user_prompt: userPrompt,
      });
      setDraftKind("continue");
      setDraft({
        title: "",
        content: res.data.content,
        summary: res.data.summary,
        unresolved_threads: res.data.unresolved_threads,
        used_entities: { characters: [], locations: [], items: [], rules: [] },
        state_change_proposals: [],
      });
      applyGenerationMeta(res.warnings, res.conflicts);
      setInfo("续写候选已生成，只会把新增内容追加到正文末尾");
    });

  const doSegmentAction = (kind: "rewrite" | "expand" | "compress") =>
    run(kind, async () => {
      if (!novelId) return;
      if (!selection || !selectionText.trim()) {
        setError("请先在正文中选中要处理的片段");
        return;
      }
      if (!(await ensureSaved())) return;
      const payload = { start: selection.start, end: selection.end, text: selectionText };
      const res =
        kind === "rewrite"
          ? await rewriteChapterSelection(chapter._id, novelId, payload, instruction, {
              request_id: newRequestId("rew"),
            })
          : kind === "expand"
            ? await expandChapterSelection(chapter._id, novelId, payload, instruction, {
                request_id: newRequestId("exp"),
              })
            : await compressChapterSelection(chapter._id, novelId, payload, instruction, {
                request_id: newRequestId("cmp"),
              });
      const data = res.data;
      const text =
        "rewritten_text" in data
          ? data.rewritten_text
          : "expanded_text" in data
            ? data.expanded_text
            : data.compressed_text;
      setSegment(text);
      applyGenerationMeta(res.warnings, res.conflicts);
      setInfo("选段候选已生成，采用后会替换当前选区内容");
    });

  const doReview = () =>
    run("review", async () => {
      if (!novelId) return;
      if (!(await ensureSaved())) return;
      const res = await reviewChapter(chapter._id, novelId, chapter.content, {
        request_id: newRequestId("review"),
      });
      setReview(res.data);
      applyGenerationMeta(res.warnings, res.conflicts);
      setInfo(
        res.data.blocking_count > 0
          ? `发现 ${res.data.blocking_count} 条阻断级问题，请先修改正文再定稿`
          : "一致性审校完成"
      );
    });

  const doFinalizeReview = () =>
    run("finalizeReview", async () => {
      if (!novelId) return;
      if (!(await ensureSaved())) return;
      const res = await finalizeReview(chapter._id, novelId, {
        request_id: newRequestId("fin"),
      });
      setFinalizeData(res);
      setReview(res.review);
      setAcceptedEvents({});
      setIgnoredIssues({});
      setForceFinalize(false);
      applyGenerationMeta(res.warnings, []);
      setInfo(
        res.state_change_events.length
          ? "请逐条确认状态变更，确认后才会写入正式设定"
          : "没有检测到需要确认的状态变更，可直接定稿"
      );
    });

  const doFinalizeCommit = () =>
    run("finalizeCommit", async () => {
      if (!novelId || !finalizeData) return;
      if (!(await ensureSaved())) return;
      const accepted = Object.entries(acceptedEvents)
        .filter(([, value]) => value)
        .map(([key]) => key);
      const rejected = finalizeData.state_change_events
        .map((item) => item.event_id)
        .filter((eventId) => !accepted.includes(eventId));
      const blockingIssues = finalizeData.review.issues
        .map((issue, index) => ({ issue, id: `issue-${index}` }))
        .filter((item) => item.issue.severity === "blocking")
        .map((item) => ({ id: item.id, severity: item.issue.severity }));
      const ignored = Object.entries(ignoredIssues)
        .filter(([, value]) => value)
        .map(([key]) => key);

      const res = await finalizeCommit(chapter._id, novelId, {
        expected_version: chapter.version,
        accepted_event_ids: accepted,
        rejected_event_ids: rejected,
        ignored_issues: ignored,
        blocking_issues: blockingIssues,
        force: forceFinalize,
        // 回传审校依据，服务端以保存的审校记录与硬规则签名重新校验
        review_generation_id: finalizeData.review_generation_id,
        hard_rule_signature: finalizeData.hard_rule_signature ?? "",
        blueprint_version: finalizeData.blueprint_version ?? undefined,
      });
      setInfo(res.message);
      setFinalizeData(null);
      setReview(null);
      setDraft(null);
      setSegment(null);
      onReload();
    });

  const toggleAccepted = (eventId: string) =>
    setAcceptedEvents((prev) => ({ ...prev, [eventId]: !prev[eventId] }));

  // 状态下拉选择「已定稿」时由父组件递增 finalizeSignal，这里自动打开定稿确认流程
  const signalRef = useRef(0);
  useEffect(() => {
    if (!finalizeSignal || finalizeSignal === signalRef.current) return;
    signalRef.current = finalizeSignal;
    void doFinalizeReview();
    // 仅在信号变化时触发一次，其余依赖使用最新闭包即可
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finalizeSignal]);

  const issueKey = (issue: ReviewIssue, index: number) => `issue-${index}`;

  return (
    <div className="space-y-3 rounded-xl border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-foreground">AI 写作助手</div>
          <div className="mt-0.5 text-xs text-muted">
            先生成方案，再生成正文；所有候选都要手动采用
          </div>
        </div>
        {busyLabel && <span className="text-xs text-accent">{busyLabel}</span>}
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
          {error}
        </div>
      )}
      {info && (
        <div className="rounded-lg border border-border bg-surface-secondary px-3 py-2 text-xs text-muted">
          {info}
        </div>
      )}
      <AIWarnings warnings={warnings} conflicts={conflicts} />

      <label className="block text-xs">
        <span className="mb-1 block text-muted">本次额外要求（可选）</span>
        <input
          className="w-full rounded-lg border border-border bg-surface px-3 py-1.5 text-sm"
          value={userPrompt}
          onChange={(event) => setUserPrompt(event.target.value)}
          placeholder="例如：本章要完成第一次战利品回收"
        />
      </label>

      <div className="flex flex-wrap gap-2">
        <AIButton label="AI 生成剧情方案" onClick={doPlan} disabled={!!busy || readOnly} />
        <AIButton
          label="AI 生成本章"
          tone="primary"
          onClick={doDraft}
          disabled={!!busy || readOnly}
          title={plan ? "基于已确认的方案生成正文" : "需要先生成章节方案"}
        />
        <AIButton label="继续写" onClick={doContinue} disabled={!!busy || readOnly} />
        <AIButton label="一致性审校" onClick={doReview} disabled={!!busy || readOnly} />
        <AIButton
          label="定稿…"
          tone="danger"
          onClick={doFinalizeReview}
          disabled={!!busy || readOnly}
          title="定稿会先审校并列出状态变更，确认后才落库"
        />
        {novelId && (
          <AIButton
            label={showSettingSync ? "收起本章设定同步" : "本章设定同步"}
            onClick={() => setShowSettingSync((prev) => !prev)}
            disabled={!!busy}
            title="分析本章正文与已确认设定的差异，逐条确认后写入"
          />
        )}
      </div>

      {/* 增量新增：本章设定同步候选展示区，AI 结果只作候选，需逐条确认 */}
      {showSettingSync && novelId && (
        <AISection
          title="本章设定同步"
          hint="新卡片、字段补充、状态变化与正文修改建议都需要逐条确认"
          actions={<AIButton label="收起" onClick={() => setShowSettingSync(false)} />}
        >
          <ChapterSettingSyncPanel
            novelId={novelId}
            chapterId={chapter._id}
            chapterVersion={chapter.version}
            content={chapter.content}
            onApply={(nextContent) => onApply({ replaceAll: nextContent })}
            onNotify={(message) => setInfo(message)}
          />
        </AISection>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <input
          className="min-w-0 flex-1 rounded-lg border border-border bg-surface px-3 py-1.5 text-xs"
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          placeholder="选段处理要求，例如：增强紧张感，保持事实不变"
        />
        <AIButton
          label="改写选段"
          onClick={() => doSegmentAction("rewrite")}
          disabled={!!busy || readOnly || !selectionText.trim()}
        />
        <AIButton
          label="扩写"
          onClick={() => doSegmentAction("expand")}
          disabled={!!busy || readOnly || !selectionText.trim()}
        />
        <AIButton
          label="压缩"
          onClick={() => doSegmentAction("compress")}
          disabled={!!busy || readOnly || !selectionText.trim()}
        />
      </div>
      <div className="text-xs text-muted">
        {selectionText.trim()
          ? `已选中 ${selectionText.length} 字，可用于改写/扩写/压缩`
          : "在左侧正文中选中片段后即可使用改写/扩写/压缩"}
      </div>

      {plan && (
        <AISection
          title="章节方案候选"
          hint={plan.goal || "待确认的剧情走向"}
          actions={
            <div className="flex gap-2">
              <AIButton
                label="采纳为本章蓝图"
                tone="primary"
                onClick={() => {
                  onApply({ blueprint: plan });
                  setInfo("方案已采纳为本章蓝图");
                }}
              />
              <AIButton label="丢弃" onClick={() => setPlan(null)} />
            </div>
          }
        >
          {plan.title && <div>标题：{plan.title}</div>}
          {plan.conflict && <div>主要冲突：{plan.conflict}</div>}
          {plan.beats.length > 0 && (
            <ol className="list-decimal space-y-0.5 pl-5">
              {plan.beats.map((beat, index) => (
                <li key={index}>{beat}</li>
              ))}
            </ol>
          )}
          {plan.rule_constraints.length > 0 && (
            <div className="text-xs text-muted">硬规则约束：{plan.rule_constraints.join("；")}</div>
          )}
          {plan.foreshadowing.length > 0 && (
            <div className="text-xs text-muted">伏笔：{plan.foreshadowing.join("；")}</div>
          )}
          {plan.ending_hook && <div className="text-xs text-muted">结尾推进：{plan.ending_hook}</div>}
        </AISection>
      )}

      {draft && (
        <AISection
          title={draftKind === "draft" ? "本章正文候选" : "续写候选"}
          hint={`${draft.content.replace(/\s/g, "").length} 字（未写入章节，需手动采用）`}
          actions={
            <div className="flex flex-wrap gap-2">
              {draftKind === "draft" ? (
                <>
                  <AIButton
                    label="覆盖正文"
                    tone="primary"
                    onClick={() => {
                      onApply({
                        replaceAll: draft.content,
                        title: draft.title || undefined,
                        summary: draft.summary,
                        unresolvedThreads: draft.unresolved_threads,
                      });
                      setDraft(null);
                    }}
                  />
                  <AIButton
                    label="追加到末尾"
                    onClick={() => {
                      onApply({ append: draft.content, summary: draft.summary });
                      setDraft(null);
                    }}
                  />
                </>
              ) : (
                <AIButton
                  label="追加到末尾"
                  tone="primary"
                  onClick={() => {
                    onApply({ append: draft.content, summary: draft.summary });
                    setDraft(null);
                  }}
                />
              )}
              <AIButton label="丢弃" onClick={() => setDraft(null)} />
            </div>
          }
        >
          <textarea
            className="max-h-64 min-h-32 w-full rounded-lg border border-border bg-surface p-2 text-xs leading-6"
            value={draft.content}
            readOnly
          />
          {draft.summary && <div className="text-xs text-muted">摘要：{draft.summary}</div>}
          {draft.unresolved_threads.length > 0 && (
            <div className="text-xs text-muted">
              未解决伏笔：{draft.unresolved_threads.join("；")}
            </div>
          )}
          {draft.state_change_proposals.length > 0 && (
            <div className="text-xs text-amber-700">
              正文提到 {draft.state_change_proposals.length} 处设定变化，定稿时会进入待确认列表
            </div>
          )}
        </AISection>
      )}

      {segment !== null && (
        <AISection
          title="选段候选"
          hint="采用后替换当前选区，不会动其它内容"
          actions={
            <div className="flex gap-2">
              <AIButton
                label="替换选区"
                tone="primary"
                onClick={() => {
                  if (!selection) return;
                  onApply({
                    replaceRange: {
                      start: selection.start,
                      end: selection.end,
                      text: segment,
                    },
                  });
                  setSegment(null);
                }}
                disabled={!selection}
              />
              <AIButton label="丢弃" onClick={() => setSegment(null)} />
            </div>
          }
        >
          <textarea
            className="max-h-48 min-h-24 w-full rounded-lg border border-border bg-surface p-2 text-xs leading-6"
            value={segment}
            readOnly
          />
        </AISection>
      )}

      {review && (
        <AISection
          title="一致性审校结果"
          hint={review.summary || "结构化检查结果"}
          actions={
            <AIButton label="关闭" onClick={() => setReview(null)} />
          }
        >
          {review.issues.length === 0 && <div className="text-xs text-muted">未发现一致性问题</div>}
          {review.issues.map((issue, index) => (
            <div key={index} className="rounded border border-border bg-surface px-2 py-1.5 text-xs">
              <div className="flex items-center gap-2">
                <SeverityBadge severity={issue.severity} />
                <span className="text-muted">{issue.category}</span>
                {issue.entity_id && <span className="text-muted">{issue.entity_id}</span>}
              </div>
              <div className="mt-1 text-foreground">{issue.message}</div>
              {issue.evidence && <div className="mt-0.5 text-muted">依据：{issue.evidence}</div>}
              {issue.suggestion && <div className="mt-0.5 text-muted">建议：{issue.suggestion}</div>}
            </div>
          ))}
        </AISection>
      )}

      {finalizeData && (
        <AISection
          title="定稿确认"
          hint="只有勾选为「接受」的变更才会写入正式设定"
          actions={
            <div className="flex gap-2">
              <AIButton
                label="全选变更"
                onClick={() =>
                  setAcceptedEvents(
                    Object.fromEntries(
                      finalizeData.state_change_events.map((item) => [item.event_id, true])
                    )
                  )
                }
              />
              <AIButton
                label="确认定稿"
                tone="primary"
                disabled={!!busy}
                onClick={doFinalizeCommit}
              />
              <AIButton label="取消" onClick={() => setFinalizeData(null)} />
            </div>
          }
        >
          {finalizeData.state_change_events.length === 0 ? (
            <div className="text-xs text-muted">没有检测到需要确认的状态变更</div>
          ) : (
            finalizeData.state_change_events.map((event) => (
              <label
                key={event.event_id}
                className="flex cursor-pointer items-start gap-2 rounded border border-border bg-surface px-2 py-1.5"
              >
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={!!acceptedEvents[event.event_id]}
                  onChange={() => toggleAccepted(event.event_id)}
                />
                <span className="min-w-0 text-xs">
                  <span className="block text-foreground">
                    {event.entity_id} · {event.event_type}
                  </span>
                  <span className="block text-muted">
                    {Object.entries(event.before)
                      .map(([key, value]) => `${key}=${value}`)
                      .join("，") || "空"}
                    {" → "}
                    {Object.entries(event.after)
                      .map(([key, value]) => `${key}=${value}`)
                      .join("，")}
                  </span>
                  {event.evidence?.text && (
                    <span className="mt-0.5 block text-muted">依据：{event.evidence.text}</span>
                  )}
                </span>
              </label>
            ))
          )}

          {finalizeData.review.issues.some((issue) => issue.severity === "blocking") && (
            <div className="rounded border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-600">
              <div>存在阻断级问题，直接定稿需要显式确认：</div>
              {finalizeData.review.issues.map((issue, index) =>
                issue.severity === "blocking" ? (
                  <label key={issueKey(issue, index)} className="mt-1 flex cursor-pointer items-start gap-2">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={!!ignoredIssues[issueKey(issue, index)]}
                      onChange={() =>
                        setIgnoredIssues((prev) => ({
                          ...prev,
                          [issueKey(issue, index)]: !prev[issueKey(issue, index)],
                        }))
                      }
                    />
                    <span>{issue.message}</span>
                  </label>
                ) : null
              )}
              <label className="mt-2 flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  checked={forceFinalize}
                  onChange={() => setForceFinalize((prev) => !prev)}
                />
                <span>我已知悉风险，仍然定稿</span>
              </label>
            </div>
          )}
        </AISection>
      )}
    </div>
  );
}
