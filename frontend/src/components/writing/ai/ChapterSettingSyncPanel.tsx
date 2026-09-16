"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "@heroui/react";
import { AIButton, AISection, AIWarnings } from "@/components/writing/ai/AIShared";
import SettingEvidencePanel from "@/components/writing/ai/SettingEvidencePanel";
import {
  WorkspaceEmptyState,
  WorkspaceNotice,
  WorkspacePage,
} from "@/components/shared/WorkspacePage";
import TextRevisionDiff from "@/components/writing/ai/TextRevisionDiff";
import { apiGet } from "@/lib/api";
import {
  acceptSettingCardCandidate,
  acceptTextRevision,
  analyzeChapterSettingCards,
  generateTextRevisions,
  listTextRevisions,
} from "@/lib/chapterAiApi";
import type {
  CardStateChangeCandidate,
  ChapterSettingAnalysisResult,
  EntityChangeCandidate,
  ExistingCardUpdate,
  NewCardCandidate,
  SettingConflictItem,
} from "@/lib/aiTypes";
import {
  sortTextRevisionsByRange,
  type TextRevision,
  type TextRevisionType,
} from "@/types/textRevision";

/** 章节列表项，字段与 `/api/chapters/novel/{id}` 返回保持一致。 */
interface ChapterOption {
  _id: string;
  number: number;
  title: string;
  content: string;
  version: number;
}

interface ChapterSettingSyncPanelProps {
  novelId: string;
  chapterId: string;
  chapterVersion: number;
  content: string;
  /** 采纳正文修改建议后回调，由父组件按原有自动保存链路写回正文。 */
  onApply: (newContent: string) => void;
  onNotify?: (message: string, tone?: "success" | "error") => void;
  /** 定位正文范围的回调，用于从证据跳转到编辑器。 */
  onLocateRange?: (range: { start: number; end: number }) => void;
}

type SyncTab = "candidates" | "revisions" | "history";

const TAB_LABEL: Record<SyncTab, string> = {
  candidates: "设定候选",
  revisions: "正文修改建议",
  history: "修改历史",
};

export const REVISION_TYPE_LABEL: Record<TextRevisionType, string> = {
  supplement: "补充设定",
  correction: "事实修正",
  terminology: "术语统一",
};

function newRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * 功能：本章设定同步面板。
 *
 * 分析本章正文后，把新卡片、既有卡片补充、冲突、状态变化和正文修改建议以候选形式展示；
 * 所有候选都需要用户逐条确认，AI 不自动修改正式设定，也不静默覆盖用户输入。
 */
export default function ChapterSettingSyncPanel({
  novelId,
  chapterId,
  chapterVersion,
  content,
  onApply,
  onNotify,
  onLocateRange,
}: ChapterSettingSyncPanelProps) {
  const [tab, setTab] = useState<SyncTab>("candidates");
  const [analysis, setAnalysis] = useState<ChapterSettingAnalysisResult | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const [adopting, setAdopting] = useState<string | null>(null);
  const [rejectedKeys, setRejectedKeys] = useState<string[]>([]);
  const [adoptedKeys, setAdoptedKeys] = useState<string[]>([]);
  const [selectedFields, setSelectedFields] = useState<Record<string, string[]>>({});

  const [revisions, setRevisions] = useState<TextRevision[]>([]);
  const [history, setHistory] = useState<TextRevision[]>([]);
  const [revisionType, setRevisionType] = useState<TextRevisionType>("supplement");
  const [instruction, setInstruction] = useState("");
  const [generating, setGenerating] = useState(false);

  const loadHistory = useCallback(async () => {
    if (!chapterId || !novelId) return;
    try {
      const data = await listTextRevisions(chapterId, novelId);
      setHistory(sortTextRevisionsByRange(data));
    } catch {
      setHistory([]);
    }
  }, [chapterId, novelId]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const stale = useMemo(
    () =>
      Boolean(
        analysis &&
          typeof analysis.chapter_version === "number" &&
          analysis.chapter_version !== chapterVersion
      ),
    [analysis, chapterVersion]
  );

  const handleAnalyze = async () => {
    setAnalyzing(true);
    setError(null);
    setMessage(null);
    setRejectedKeys([]);
    setAdoptedKeys([]);
    setSelectedFields({});
    try {
      const res = await analyzeChapterSettingCards(chapterId, {
        novel_id: novelId,
        chapter_version: chapterVersion,
        request_id: newRequestId(),
      });
      setAnalysis({ ...res.data, generation_id: res.candidate_id ?? res.data.generation_id });
      setRevisions([]);
      setTab("candidates");
    } catch (err) {
      setError(err instanceof Error ? err.message : "本章设定分析失败");
    } finally {
      setAnalyzing(false);
    }
  };

  const runAdopt = async (
    key: string,
    payload: Parameters<typeof acceptSettingCardCandidate>[0]
  ) => {
    if (!analysis?.generation_id) {
      setError("缺少生成记录，请重新分析本章");
      return;
    }
    setAdopting(key);
    setError(null);
    try {
      const res = await acceptSettingCardCandidate({
        ...payload,
        novel_id: novelId,
        generation_id: analysis.generation_id,
      });
      setAdoptedKeys((prev) => (prev.includes(key) ? prev : [...prev, key]));
      setMessage(res.message ?? "候选已采纳");
      onNotify?.(res.message ?? "候选已采纳", "success");
    } catch (err) {
      setError(err instanceof Error ? err.message : "采纳候选失败");
      onNotify?.(err instanceof Error ? err.message : "采纳候选失败", "error");
    } finally {
      setAdopting(null);
    }
  };

  const handleAdoptNewCard = (item: NewCardCandidate, index: number) => {
    const key = `new-${index}`;
    const merge = Boolean(item.duplicate_of);
    void runAdopt(key, {
      novel_id: novelId,
      generation_id: "",
      action: merge ? "merge" : "create",
      target_card_id: item.duplicate_of ?? null,
      selected_fields: item.fields ?? {},
      confirm_hard_rule: item.is_hard_rule === true,
    });
  };

  const handleAdoptUpdate = (item: ExistingCardUpdate, index: number) => {
    const key = `update-${index}`;
    const fields = selectedFields[key] ?? Object.keys(item.fields ?? {});
    const picked = Object.fromEntries(
      fields.filter((field) => field in (item.fields ?? {})).map((field) => [field, item.fields[field]])
    );
    if (Object.keys(picked).length === 0) {
      setError("请至少勾选一个要写入的字段");
      return;
    }
    void runAdopt(key, {
      novel_id: novelId,
      generation_id: "",
      action: "rewrite",
      target_card_id: item.card_id,
      selected_fields: picked,
    });
  };

  const handleAdoptStateChange = (item: CardStateChangeCandidate, index: number) => {
    const key = `state-${index}`;
    void runAdopt(key, {
      novel_id: novelId,
      generation_id: "",
      action: "rewrite",
      target_card_id: item.card_id,
      selected_fields: { current_state: item.to_state },
      confirm_hard_rule: true,
    });
  };

  const toggleField = (key: string, field: string) => {
    setSelectedFields((prev) => {
      const current = prev[key] ?? [];
      return {
        ...prev,
        [key]: current.includes(field)
          ? current.filter((item) => item !== field)
          : [...current, field],
      };
    });
  };

  const handleGenerateRevisions = async () => {
    setGenerating(true);
    setError(null);
    try {
      const res = await generateTextRevisions(chapterId, {
        novel_id: novelId,
        card_ids: [
          ...new Set([
            ...(analysis?.card_updates ?? []).map((item) => item.card_id),
            ...(analysis?.state_changes ?? []).map((item) => item.card_id),
          ]),
        ],
        revision_type: revisionType,
        instruction,
        chapter_version: chapterVersion,
        request_id: newRequestId(),
      });
      const mapped: TextRevision[] = (res.data?.revisions ?? []).map((item) => ({
        revision_id: item.revision_id,
        chapter_version: res.data?.chapter_version ?? chapterVersion,
        target_range: item.target_range ?? { start: 0, end: 0 },
        before_text: item.before_text,
        after_text: item.after_text,
        revision_type: item.revision_type,
        reason: item.reason,
        status: "pending",
        card_ids: item.card_ids,
        warnings: item.warnings,
      }));
      setRevisions(sortTextRevisionsByRange(mapped));
      setTab("revisions");
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成正文修改建议失败");
    } finally {
      setGenerating(false);
    }
  };

  const handleAcceptRevision = async (revision: TextRevision, afterText: string) => {
    setError(null);
    try {
      await acceptTextRevision(chapterId, revision.revision_id, {
        novel_id: novelId,
        expected_chapter_version: revision.chapter_version,
        after_text: afterText,
      });
      const nextContent =
        content.slice(0, revision.target_range.start) +
        afterText +
        content.slice(revision.target_range.end);
      onApply(nextContent);
      setRevisions((prev) =>
        prev.map((item) =>
          item.revision_id === revision.revision_id
            ? { ...item, status: "accepted", after_text: afterText }
            : item
        )
      );
      setMessage("已按建议更新本章正文");
      onNotify?.("已按建议更新本章正文", "success");
      void loadHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "采纳正文修改建议失败");
      onNotify?.(err instanceof Error ? err.message : "采纳正文修改建议失败", "error");
    }
  };

  const handleRejectRevision = (revision: TextRevision) => {
    setRevisions((prev) =>
      prev.map((item) =>
        item.revision_id === revision.revision_id ? { ...item, status: "rejected" } : item
      )
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {(Object.keys(TAB_LABEL) as SyncTab[]).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`rounded-full border px-3 py-1 text-xs ${
              key === tab
                ? "border-accent bg-accent/10 text-foreground"
                : "border-border bg-surface text-muted"
            }`}
          >
            {TAB_LABEL[key]}
          </button>
        ))}
        <div className="ml-auto">
          <AIButton
            label={analyzing ? "分析中…" : "分析本章设定"}
            tone="primary"
            onClick={handleAnalyze}
            disabled={analyzing}
          />
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
          {error}
        </div>
      )}
      {message && (
        <div className="rounded-lg border border-border bg-surface-secondary px-3 py-2 text-xs text-muted">
          {message}
        </div>
      )}
      {stale && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
          正文已经变化（分析结果基于 v{analysis?.chapter_version}，当前 v{chapterVersion}），请重新分析后再采纳候选
        </div>
      )}

      {tab === "candidates" && (
        <CandidatesTab
          analysis={analysis}
          stale={stale}
          adopting={adopting}
          adoptedKeys={adoptedKeys}
          rejectedKeys={rejectedKeys}
          selectedFields={selectedFields}
          onToggleField={toggleField}
          onAdoptNewCard={handleAdoptNewCard}
          onAdoptUpdate={handleAdoptUpdate}
          onAdoptStateChange={handleAdoptStateChange}
          onReject={(key) => setRejectedKeys((prev) => [...prev, key])}
          onLocateRange={onLocateRange}
        />
      )}

      {tab === "revisions" && (
        <div className="space-y-3">
          <div className="rounded-xl border border-border bg-surface p-3">
            <div className="flex flex-wrap items-end gap-2">
              <label className="block text-xs">
                <span className="mb-1 block text-muted">修改类型</span>
                <select
                  className="rounded-lg border border-border px-3 py-1.5 text-sm"
                  value={revisionType}
                  onChange={(event) => setRevisionType(event.target.value as TextRevisionType)}
                >
                  {(Object.keys(REVISION_TYPE_LABEL) as TextRevisionType[]).map((type) => (
                    <option key={type} value={type}>
                      {REVISION_TYPE_LABEL[type]}
                    </option>
                  ))}
                </select>
              </label>
              <label className="min-w-0 flex-1 text-xs">
                <span className="mb-1 block text-muted">补充说明（可选）</span>
                <input
                  className="w-full rounded-lg border border-border px-3 py-1.5 text-sm"
                  value={instruction}
                  onChange={(event) => setInstruction(event.target.value)}
                  placeholder="例如：统一本章对「玄天宗」的称呼"
                />
              </label>
              <Button
                variant="primary"
                size="sm"
                onPress={handleGenerateRevisions}
                isDisabled={generating}
              >
                {generating ? "生成中…" : "生成修改建议"}
              </Button>
            </div>
            <p className="mt-2 text-xs text-muted">
              本次只处理补充、事实修正与术语统一；大范围整改不在本轮范围内
            </p>
          </div>

          {revisions.length === 0 ? (
            <p className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-xs text-muted">
              还没有修改建议，选择类型后点击「生成修改建议」
            </p>
          ) : (
            <div className="space-y-2">
              {revisions.map((revision, index) => (
                <TextRevisionDiff
                  key={revision.revision_id}
                  revision={revision}
                  index={index}
                  currentChapterVersion={chapterVersion}
                  chapterContent={content}
                  onAccept={handleAcceptRevision}
                  onReject={handleRejectRevision}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "history" && (
        <div className="space-y-2">
          {history.length === 0 ? (
            <p className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-xs text-muted">
              本章还没有正文修改历史
            </p>
          ) : (
            history.map((revision, index) => (
              <div key={revision.revision_id} className="rounded-lg border border-border bg-surface p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-medium text-foreground">#{index + 1}</span>
                  <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-muted">
                    {REVISION_TYPE_LABEL[revision.revision_type] ?? revision.revision_type}
                  </span>
                  <span className="text-muted">{revision.status}</span>
                  <span className="text-muted">
                    范围 [{revision.target_range.start}, {revision.target_range.end})
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted">理由：{revision.reason}</p>
                <div className="mt-1 grid gap-2 sm:grid-cols-2">
                  <p className="whitespace-pre-wrap rounded-lg bg-surface-secondary p-2 text-xs">
                    {revision.before_text}
                  </p>
                  <p className="whitespace-pre-wrap rounded-lg bg-accent/5 p-2 text-xs">
                    {revision.after_text}
                  </p>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

interface CandidatesTabProps {
  analysis: ChapterSettingAnalysisResult | null;
  stale: boolean;
  adopting: string | null;
  adoptedKeys: string[];
  rejectedKeys: string[];
  selectedFields: Record<string, string[]>;
  onToggleField: (key: string, field: string) => void;
  onAdoptNewCard: (item: NewCardCandidate, index: number) => void;
  onAdoptUpdate: (item: ExistingCardUpdate, index: number) => void;
  onAdoptStateChange: (item: CardStateChangeCandidate, index: number) => void;
  onReject: (key: string) => void;
  onLocateRange?: (range: { start: number; end: number }) => void;
}

function CandidatesTab({
  analysis,
  stale,
  adopting,
  adoptedKeys,
  rejectedKeys,
  selectedFields,
  onToggleField,
  onAdoptNewCard,
  onAdoptUpdate,
  onAdoptStateChange,
  onReject,
  onLocateRange,
}: CandidatesTabProps) {
  if (!analysis) {
    return (
      <p className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-xs text-muted">
        点击「分析本章设定」获取新卡片、字段补充、冲突与变化候选
      </p>
    );
  }

  const total =
    (analysis.new_cards?.length ?? 0) +
    (analysis.card_updates?.length ?? 0) +
    (analysis.state_changes?.length ?? 0);

  return (
    <div className="space-y-3">
      <AIWarnings warnings={analysis.notes ? [analysis.notes] : []} />

      <AISection title={`新发现的设定（${analysis.new_cards?.length ?? 0}）`}>
        {(analysis.new_cards ?? []).length === 0 && (
          <p className="text-xs text-muted">没有新发现的设定</p>
        )}
        {(analysis.new_cards ?? []).map((item, index) => {
          const key = `new-${index}`;
          const done = adoptedKeys.includes(key) || rejectedKeys.includes(key);
          return (
            <div key={key} className="space-y-2 rounded-lg border border-border bg-surface p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-foreground">{item.name}</span>
                <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-xs text-muted">
                  {item.type}
                </span>
                {item.is_hard_rule && (
                  <span className="rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600">
                    硬规则
                  </span>
                )}
                {item.duplicate_of && (
                  <span className="text-xs text-muted">建议合并到 {item.duplicate_of}</span>
                )}
              </div>
              <SettingEvidencePanel
                evidence={[item]}
                onLocate={onLocateRange}
              />
              {!done ? (
                <div className="flex gap-2">
                  <Button
                    variant="primary"
                    size="sm"
                    onPress={() => onAdoptNewCard(item, index)}
                    isDisabled={stale || adopting === key}
                  >
                    {adopting === key ? "采纳中…" : item.duplicate_of ? "合并到既有卡片" : "新建卡片"}
                  </Button>
                  <Button variant="ghost" size="sm" onPress={() => onReject(key)}>
                    忽略
                  </Button>
                </div>
              ) : (
                <p className="text-xs text-muted">
                  {adoptedKeys.includes(key) ? "已采纳" : "已忽略"}
                </p>
              )}
            </div>
          );
        })}
      </AISection>

      <AISection title={`既有卡片补充（${analysis.card_updates?.length ?? 0}）`}>
        {(analysis.card_updates ?? []).length === 0 && (
          <p className="text-xs text-muted">没有字段补充候选</p>
        )}
        {(analysis.card_updates ?? []).map((item, index) => {
          const key = `update-${index}`;
          const done = adoptedKeys.includes(key) || rejectedKeys.includes(key);
          const fields = Object.keys(item.fields ?? {});
          const picked = selectedFields[key] ?? fields;
          return (
            <div key={key} className="space-y-2 rounded-lg border border-border bg-surface p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-foreground">{item.card_name}</span>
                <span className="text-xs text-muted">{item.card_id}</span>
              </div>
              {item.reason && <p className="text-xs text-muted">理由：{item.reason}</p>}
              <div className="space-y-1">
                {fields.map((field) => (
                  <label key={field} className="flex items-start gap-2 text-xs">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={picked.includes(field)}
                      disabled={done}
                      onChange={() => onToggleField(key, field)}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="text-muted">{field}：</span>
                      <span className="whitespace-pre-wrap text-foreground">
                        {item.fields[field]}
                      </span>
                      {item.new_fields?.includes(field) && (
                        <span className="ml-1 rounded-full bg-accent/10 px-2 py-0.5 text-muted">
                          新增字段
                        </span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
              <SettingEvidencePanel evidence={[item]} onLocate={onLocateRange} />
              {!done ? (
                <div className="flex gap-2">
                  <Button
                    variant="primary"
                    size="sm"
                    onPress={() => onAdoptUpdate(item, index)}
                    isDisabled={stale || adopting === key}
                  >
                    {adopting === key ? "采纳中…" : "写入选中字段"}
                  </Button>
                  <Button variant="ghost" size="sm" onPress={() => onReject(key)}>
                    忽略
                  </Button>
                </div>
              ) : (
                <p className="text-xs text-muted">
                  {adoptedKeys.includes(key) ? "已采纳" : "已忽略"}
                </p>
              )}
            </div>
          );
        })}
      </AISection>

      <AISection title={`状态变化（${analysis.state_changes?.length ?? 0}）`}>
        {(analysis.state_changes ?? []).length === 0 && (
          <p className="text-xs text-muted">没有状态变化候选</p>
        )}
        {(analysis.state_changes ?? []).map((item, index) => {
          const key = `state-${index}`;
          const done = adoptedKeys.includes(key) || rejectedKeys.includes(key);
          return (
            <div key={key} className="space-y-2 rounded-lg border border-border bg-surface p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-foreground">{item.card_name}</span>
                <span className="text-xs text-muted">
                  {item.from_state || "（空）"} → {item.to_state}
                </span>
              </div>
              <p className="text-xs text-muted">理由：{item.reason}</p>
              <SettingEvidencePanel evidence={[item]} onLocate={onLocateRange} />
              {!done ? (
                <div className="flex gap-2">
                  <Button
                    variant="primary"
                    size="sm"
                    onPress={() => onAdoptStateChange(item, index)}
                    isDisabled={stale || adopting === key}
                  >
                    {adopting === key ? "采纳中…" : "确认状态变化"}
                  </Button>
                  <Button variant="ghost" size="sm" onPress={() => onReject(key)}>
                    忽略
                  </Button>
                </div>
              ) : (
                <p className="text-xs text-muted">
                  {adoptedKeys.includes(key) ? "已确认并写入事件流" : "已忽略"}
                </p>
              )}
            </div>
          );
        })}
      </AISection>

      {(analysis.conflicts?.length ?? 0) > 0 && (
        <AISection title={`冲突（${analysis.conflicts.length}）`}>
          {(analysis.conflicts as SettingConflictItem[]).map((conflict, index) => (
            <div
              key={`conflict-${index}`}
              className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-surface px-2 py-0.5">{conflict.severity}</span>
                <span className="font-medium">{conflict.card_name}</span>
              </div>
              <p className="mt-1">{conflict.message}</p>
              <p className="mt-1 text-muted">建议：{conflict.suggestion}</p>
              <SettingEvidencePanel evidence={[conflict]} onLocate={onLocateRange} />
            </div>
          ))}
        </AISection>
      )}

      {(analysis.entity_changes?.length ?? 0) > 0 && (
        <AISection title={`角色 / 关系 / 战力变化（${analysis.entity_changes.length}）`}>
          {(analysis.entity_changes as EntityChangeCandidate[]).map((change, index) => (
            <div
              key={`entity-${index}`}
              className="rounded-lg border border-border bg-surface p-3 text-xs"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-muted">
                  {change.entity_type}
                </span>
                <span className="font-medium text-foreground">{change.entity_name}</span>
              </div>
              <p className="mt-1 text-muted">
                {change.before || "（空）"} → {change.after}
              </p>
              <p className="mt-1 text-muted">理由：{change.reason}</p>
              <SettingEvidencePanel evidence={[change]} onLocate={onLocateRange} />
              <p className="mt-1 text-muted">需到对应工作台确认后写入事件流</p>
            </div>
          ))}
        </AISection>
      )}

      {total === 0 && (
        <p className="rounded-xl border border-dashed border-border px-3 py-4 text-center text-xs text-muted">
          本次分析没有产生新的设定候选
        </p>
      )}
    </div>
  );
}

/**
 * 功能：单章同步编辑器，持有该章正文草稿与版本号。
 *
 * 由父组件以章节 ID 作为 key 挂载，切换章节即重建状态，避免在 effect 中同步状态。
 */
function ChapterSyncEditor({
  novelId,
  chapter,
}: {
  novelId: string;
  chapter: ChapterOption;
}) {
  const [draft, setDraft] = useState(chapter.content ?? "");
  const [version, setVersion] = useState(chapter.version ?? 1);

  return (
    <>
      <ChapterSettingSyncPanel
        novelId={novelId}
        chapterId={chapter._id}
        chapterVersion={version}
        content={draft}
        onApply={(next) => {
          setDraft(next);
          setVersion((prev) => prev + 1);
        }}
      />
      <p className="text-xs text-muted">
        此处采纳的正文修改会通过章节编辑器的自动保存链路写回；如需精细编辑请到章节工作台
      </p>
    </>
  );
}

/**
 * 功能：写作侧边栏「正文同步中心」工作区。
 *
 * 在没有章节上下文时提供章节选择；选中后渲染本章设定同步面板。
 */
export function SettingSyncWorkspace({
  novelId,
  onOpenChapter,
}: {
  novelId: string;
  onOpenChapter?: (chapterId: string) => void;
}) {
  const [chapters, setChapters] = useState<ChapterOption[]>([]);
  const [loadedFor, setLoadedFor] = useState<string>("");
  const [selected, setSelected] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!novelId) return;
    apiGet<{ data: ChapterOption[] }>(`/api/chapters/novel/${novelId}?include_deleted=false`)
      .then((res) => {
        setChapters(res.data ?? []);
        setLoadedFor(novelId);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "章节列表加载失败")
      );
  }, [novelId]);

  const current = chapters.find((chapter) => chapter._id === selected);
  const loading = Boolean(novelId) && loadedFor !== novelId && !error;

  return (
    <WorkspacePage
      eyebrow="创作与同步"
      title="正文同步中心"
      description="分析本章正文与已确认设定的差异，逐条确认后再写入设定或正文"
    >
      {error && <WorkspaceNotice tone="error">{error}</WorkspaceNotice>}

      <div className="rounded-xl border border-border bg-surface p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <label className="grid min-w-0 flex-1 gap-1.5 text-sm">
            <span className="text-xs font-semibold tracking-wide text-muted">选择章节</span>
            <select
              className="min-h-11 w-full rounded-xl border border-border bg-background/80 px-3.5 py-2.5 text-sm text-foreground outline-none transition-[border-color,box-shadow] focus:border-accent focus:ring-2 focus:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-60"
              value={selected}
              disabled={loading || chapters.length === 0}
              onChange={(event) => setSelected(event.target.value)}
            >
              <option value="">请选择章节</option>
              {chapters.map((chapter) => (
                <option key={chapter._id} value={chapter._id}>
                  第 {chapter.number} 章 · {chapter.title || "无标题"}
                </option>
              ))}
            </select>
          </label>

          {current && onOpenChapter && (
            <Button variant="ghost" size="sm" onPress={() => onOpenChapter(current._id)}>
              在章节编辑器中打开
            </Button>
          )}
        </div>

        {current && (
          <p className="mt-3 border-t border-border pt-3 text-xs text-muted">
            第 {current.number} 章 · {current.title || "无标题"} · 版本 v{current.version}
          </p>
        )}
      </div>

      {loading && <WorkspaceNotice>章节列表加载中…</WorkspaceNotice>}
      {!loading && !error && chapters.length === 0 && (
        <WorkspaceNotice>这本小说还没有章节，先到「章节编辑」创建章节后再回来同步</WorkspaceNotice>
      )}

      {current ? (
        <ChapterSyncEditor key={current._id} novelId={novelId} chapter={current} />
      ) : (
        !loading &&
        chapters.length > 0 && (
          <WorkspaceEmptyState
            title="选择章节后开始分析"
            description="系统会比对正文与已确认的设定卡、角色和势力，给出可逐条确认的候选，确认前不会写入正式数据。"
          />
        )
      )}
    </WorkspacePage>
  );
}
