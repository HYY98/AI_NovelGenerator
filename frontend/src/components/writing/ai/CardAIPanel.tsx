"use client";

import { useMemo, useState } from "react";
import { ApiRequestError } from "@/lib/api";
import type {
  AIEnvelope,
  CardCandidate,
  CardCompleteResult,
  CardConflictItem,
  CardExtractItem,
  CardRewriteResult,
} from "@/lib/aiTypes";
import { newRequestId } from "@/lib/aiTypes";
import {
  acceptSettingCardCandidate,
  checkSettingCardConflicts,
  completeSettingCard,
  extractSettingCards,
  generateSettingCard,
  rewriteSettingCard,
} from "@/lib/chapterAiApi";
import { AIButton, AISection, AIWarnings, AdoptRow, SeverityBadge } from "./AIShared";

export interface CardAITarget {
  _id: string;
  card_id: string;
  name: string;
  fields: Record<string, string>;
  current_state: string;
  version: number;
  is_deleted?: boolean;
}

interface CardAIPanelProps {
  novelId?: string;
  cardType: "location" | "item" | "rule";
  card: CardAITarget | null;
  /** 事件型卡片的扩展字段标签，与 CARD_SCHEMA 保持一致。 */
  fieldLabels: [string, string][];
  /** 可选：把生成绑定到当前章节上下文。 */
  chapterId?: string;
  /** 提取用的原文与来源说明。 */
  sourceText: string;
  sourceDocument: "worldview" | "novel_info" | "chapter" | "card";
  sourceDocumentLabel: string;
  /** 把字段写入当前卡片（空值不覆盖）。 */
  onApplyFields: (fields: Record<string, string>, note: string) => void;
  /** 采纳候选为一张新卡。 */
  onCreateCard: (candidate: CardCandidate) => void;
  onError: (message: string) => void;
}

/**
 * 功能：设定卡 AI 面板，提供生成、补全、提取与冲突检查。
 *
 * 生成结果只作为候选展示，用户逐字段/逐项确认后才会写入正式卡片。
 */
export default function CardAIPanel({
  novelId,
  cardType,
  card,
  fieldLabels,
  chapterId,
  sourceText,
  sourceDocument,
  sourceDocumentLabel,
  onApplyFields,
  onCreateCard,
  onError,
}: CardAIPanelProps) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [conflicts, setConflicts] = useState<string[]>([]);
  const [userPrompt, setUserPrompt] = useState("");

  const [candidates, setCandidates] = useState<CardCandidate[] | null>(null);
  const [selectedFields, setSelectedFields] = useState<Record<string, boolean>>({});
  const [completeResult, setCompleteResult] = useState<CardCompleteResult | null>(null);
  const [lockedFields, setLockedFields] = useState<Record<string, boolean>>({ name: true, current_state: true });
  const [extractItems, setExtractItems] = useState<CardExtractItem[] | null>(null);
  const [conflictItems, setConflictItems] = useState<CardConflictItem[] | null>(null);

  // 增量新增：AI 改写候选与字段级确认状态
  const [rewriteResult, setRewriteResult] = useState<AIEnvelope<CardRewriteResult> | null>(null);
  const [rewriteTargets, setRewriteTargets] = useState<Record<string, boolean>>({});
  const [adoptedFields, setAdoptedFields] = useState<Record<string, boolean>>({});
  const [confirmHardRule, setConfirmHardRule] = useState(false);
  const [adopting, setAdopting] = useState(false);

  const labelOf = useMemo(() => {
    const map = new Map(fieldLabels);
    return (key: string) => map.get(key) ?? key;
  }, [fieldLabels]);

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

  const doGenerate = () =>
    run("generate", async () => {
      if (!novelId) return;
      const res = await generateSettingCard(novelId, cardType, chapterId ?? "", {
        request_id: newRequestId("card"),
        user_prompt: userPrompt,
      });
      setCandidates(res.data.candidates);
      setWarnings(res.warnings);
      setConflicts(res.conflicts);
      setInfo(
        res.data.candidates.length
          ? "已生成卡片候选，采纳后才会创建正式卡片"
          : "模型没有给出可用候选，可补充要求后重试"
      );
    });

  const doComplete = () =>
    run("complete", async () => {
      if (!novelId || !card) return;
      const locked = Object.entries(lockedFields)
        .filter(([, value]) => value)
        .map(([key]) => key);
      const res = await completeSettingCard(novelId, card.card_id, locked, {
        request_id: newRequestId("cmp"),
        user_prompt: userPrompt,
      });
      setCompleteResult(res.data);
      setSelectedFields(
        Object.fromEntries(Object.keys(res.data.filled_fields).map((key) => [key, true]))
      );
      setWarnings(res.warnings);
      setConflicts(res.conflicts);
      setInfo(
        Object.keys(res.data.filled_fields).length
          ? "已生成补全候选，请勾选要写回的字段"
          : "该卡片没有可补全的空字段"
      );
    });

  const doExtract = () =>
    run("extract", async () => {
      if (!novelId) return;
      if (!sourceText.trim()) {
        setError(`当前${sourceDocumentLabel}为空，无法提取卡片`);
        return;
      }
      const res = await extractSettingCards(
        novelId,
        sourceDocument,
        sourceText,
        chapterId ?? "",
        { request_id: newRequestId("ext") }
      );
      setExtractItems(res.data.items);
      setWarnings(res.warnings);
      setConflicts(res.conflicts);
      setInfo(
        res.data.items.length
          ? "已从内容中识别出候选条目，采纳后才会创建卡片"
          : "没有识别到新的设定条目"
      );
    });

  const doCheckConflicts = () =>
    run("conflicts", async () => {
      if (!novelId || !card) return;
      const res = await checkSettingCardConflicts(novelId, card.card_id, {
        request_id: newRequestId("cf"),
      });
      setConflictItems(res.data.conflicts);
      setWarnings(res.warnings);
      setConflicts(res.conflicts);
      setInfo(res.data.conflicts.length ? "发现潜在冲突，请逐条核对" : "未发现明显冲突");
    });

  /** 增量新增：请求 AI 改写选中字段，只返回字段级候选补丁。 */
  const doRewrite = () =>
    run("rewrite", async () => {
      if (!novelId || !card) return;
      const fields = Object.entries(rewriteTargets)
        .filter(([, value]) => value)
        .map(([key]) => key);
      if (!fields.length) {
        setError("请先勾选要改写的字段");
        return;
      }
      const locked = Object.entries(lockedFields)
        .filter(([, value]) => value)
        .map(([key]) => key);
      const res = await rewriteSettingCard(card.card_id, {
        novel_id: novelId,
        fields,
        instruction: userPrompt,
        locked_fields: locked,
        expected_version: card.version,
        request_id: newRequestId("rw"),
      });
      setRewriteResult(res);
      setAdoptedFields(
        Object.fromEntries(res.data.changed_fields.map((diff) => [diff.field, true]))
      );
      setConfirmHardRule(false);
      setWarnings(res.warnings);
      setConflicts(res.conflicts);
      setInfo(
        res.data.changed_fields.length
          ? "已生成字段级改写候选，勾选后才会写入正式卡片"
          : "AI 没有给出可写入的字段改动"
      );
    });

  /** 增量新增：通过统一采纳接口写入选中的字段改动。 */
  const applyRewrite = async () => {
    if (!novelId || !card || !rewriteResult) return;
    const selected: Record<string, string> = {};
    for (const diff of rewriteResult.data.changed_fields) {
      if (adoptedFields[diff.field]) selected[diff.field] = diff.new_value;
    }
    if (!Object.keys(selected).length) {
      setError("请至少勾选一个要写入的字段");
      return;
    }
    const hasProtected = rewriteResult.data.changed_fields.some(
      (diff) => adoptedFields[diff.field] && diff.protected
    );
    if (hasProtected && !confirmHardRule) {
      setError("勾选了受保护字段（名称 / 当前状态 / 硬规则），请先确认后再写入");
      return;
    }
    setAdopting(true);
    try {
      const res = await acceptSettingCardCandidate({
        novel_id: novelId,
        generation_id: rewriteResult.candidate_id,
        action: "rewrite",
        target_card_id: card.card_id,
        selected_fields: selected,
        expected_card_version: card.version,
        confirm_hard_rule: confirmHardRule,
      });
      onApplyFields(selected, "AI 改写采纳");
      setRewriteResult(null);
      setInfo(res.message ?? "已把选中的改写字段写入卡片");
    } catch (err) {
      const message =
        err instanceof ApiRequestError
          ? err.message
          : err instanceof Error
            ? err.message
            : "采纳改写候选失败";
      setError(message);
      onError(message);
    } finally {
      setAdopting(false);
    }
  };

  const applySelectedFields = () => {
    if (!completeResult) return;
    const fields: Record<string, string> = {};
    for (const [key, value] of Object.entries(completeResult.filled_fields)) {
      if (selectedFields[key]) fields[key] = value;
    }
    if (!Object.keys(fields).length) {
      setError("请至少勾选一个要写回的字段");
      return;
    }
    onApplyFields(fields, "AI 补全");
    setCompleteResult(null);
    setInfo("已把选中的补全字段写入卡片");
  };

  return (
    <div className="space-y-3 rounded-xl border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-foreground">AI 设定助手</div>
          <div className="mt-0.5 text-xs text-muted">
            生成/提取/补全都只是候选，确认后才会写入正式设定
          </div>
        </div>
        {busy && <span className="text-xs text-accent">处理中…</span>}
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

      <input
        className="w-full rounded-lg border border-border bg-surface px-3 py-1.5 text-xs"
        value={userPrompt}
        onChange={(event) => setUserPrompt(event.target.value)}
        placeholder="额外要求，例如：生成一个适合第一章救援的高危地点"
      />

      <div className="flex flex-wrap gap-2">
        <AIButton label="AI 生成" onClick={doGenerate} disabled={!!busy} />
        <AIButton
          label="AI 补全"
          onClick={doComplete}
          disabled={!!busy || !card || !!card.is_deleted}
        />
        <AIButton label="从小说内容提取" onClick={doExtract} disabled={!!busy} />
        <AIButton
          label="检查冲突"
          onClick={doCheckConflicts}
          disabled={!!busy || !card || !!card.is_deleted}
        />
        <AIButton
          label="AI 改写字段"
          onClick={doRewrite}
          disabled={!!busy || !card || !!card.is_deleted}
          title="只生成字段级候选，确认后才写入"
        />
      </div>

      {card && (
        <div className="space-y-2 rounded-lg border border-border bg-surface-secondary p-2">
          <div className="text-xs text-muted">
            锁定字段（AI 补全时不得返回或改写这些字段）
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {([["name", "名称"], ["current_state", "当前状态"]] as [string, string][])
              .concat(fieldLabels)
              .map(([key, label]) => (
                <label key={key} className="flex items-center gap-1 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={!!lockedFields[key]}
                    onChange={() =>
                      setLockedFields((prev) => ({ ...prev, [key]: !prev[key] }))
                    }
                  />
                  <span>{label}</span>
                </label>
              ))}
          </div>
          {/* 增量新增：AI 改写目标字段；受保护字段默认不勾选 */}
          <div className="border-t border-border pt-2 text-xs text-muted">
            AI 改写目标字段（名称 / 当前状态为受保护字段，需二次确认）
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {([["name", "名称"], ["current_state", "当前状态"]] as [string, string][])
              .concat(fieldLabels)
              .map(([key, label]) => (
                <label key={`rw-${key}`} className="flex items-center gap-1 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={!!rewriteTargets[key]}
                    onChange={() =>
                      setRewriteTargets((prev) => ({ ...prev, [key]: !prev[key] }))
                    }
                  />
                  <span>{label}</span>
                </label>
              ))}
          </div>
        </div>
      )}

      {candidates && candidates.length > 0 && (
        <AISection
          title="卡片候选"
          hint="采纳会新建一张卡片；合并只会把非空字段写入当前卡片"
        >
          {candidates.map((candidate, index) => (
            <div key={index} className="rounded border border-border bg-surface px-2 py-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-foreground">{candidate.name}</span>
                <span className="text-muted">
                  {candidate.current_state || "无状态"} · 重要性 {candidate.importance}
                </span>
              </div>
              {candidate.reason && <div className="mt-1 text-muted">理由：{candidate.reason}</div>}
              {candidate.conflicts.length > 0 && (
                <div className="mt-1 text-amber-700">冲突：{candidate.conflicts.join("；")}</div>
              )}
              <div className="mt-1 space-y-0.5">
                {Object.entries(candidate.fields).map(([key, value]) => (
                  <div key={key}>
                    <span className="text-muted">{labelOf(key)}：</span>
                    <span className="text-foreground">{value}</span>
                  </div>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <AIButton
                  label="采纳为新建卡"
                  tone="primary"
                  onClick={() => {
                    onCreateCard(candidate);
                    setInfo(`已创建卡片「${candidate.name}」`);
                  }}
                />
                <AIButton
                  label="合并到当前卡"
                  disabled={!card}
                  onClick={() => {
                    onApplyFields(candidate.fields, "AI 生成候选合并");
                    setInfo("已把候选的非空字段写入当前卡片");
                  }}
                />
                <AIButton
                  label="丢弃"
                  onClick={() =>
                    setCandidates((prev) => (prev ?? []).filter((_, i) => i !== index))
                  }
                />
              </div>
            </div>
          ))}
        </AISection>
      )}

      {/* 增量新增：字段级 diff 预览，确认后才通过统一采纳接口写入 */}
      {rewriteResult && (
        <AISection
          title="AI 改写候选"
          hint="逐字段确认，受保护字段需要二次确认才会写入"
          actions={
            <div className="flex gap-2">
              <AIButton
                label={adopting ? "写入中…" : "确认写入选中字段"}
                tone="primary"
                onClick={applyRewrite}
                disabled={adopting}
              />
              <AIButton label="丢弃" onClick={() => setRewriteResult(null)} disabled={adopting} />
            </div>
          }
        >
          {rewriteResult.data.changed_fields.length === 0 && (
            <div className="text-xs text-muted">AI 没有给出可写入的字段改动</div>
          )}
          {rewriteResult.data.changed_fields.map((diff) => (
            <div key={diff.field} className="rounded border border-border bg-surface px-2 py-2 text-xs">
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={!!adoptedFields[diff.field]}
                  onChange={() =>
                    setAdoptedFields((prev) => ({ ...prev, [diff.field]: !prev[diff.field] }))
                  }
                />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-foreground">{labelOf(diff.field)}</span>
                    {diff.protected && (
                      <span className="rounded-full bg-red-50 px-2 py-0.5 text-[10px] text-red-600">
                        受保护字段
                      </span>
                    )}
                  </span>
                  <span className="mt-1 block rounded bg-surface-secondary px-2 py-1 text-muted line-through">
                    {diff.old_value || "（空）"}
                  </span>
                  <span className="mt-1 block rounded bg-accent/10 px-2 py-1 text-foreground">
                    {diff.new_value || "（空）"}
                  </span>
                  {diff.note && <span className="mt-1 block text-muted">说明：{diff.note}</span>}
                </span>
              </label>
            </div>
          ))}
          {rewriteResult.data.preserved_fields.length > 0 && (
            <div className="text-xs text-muted">
              保持不变的字段：{rewriteResult.data.preserved_fields.map(labelOf).join("、")}
            </div>
          )}
          {rewriteResult.data.changed_fields.some((diff) => diff.protected) && (
            <label className="flex items-center gap-2 rounded border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-600">
              <input
                type="checkbox"
                checked={confirmHardRule}
                onChange={() => setConfirmHardRule((prev) => !prev)}
              />
              <span>我确认要修改受保护字段（名称 / 当前状态 / 硬规则）</span>
            </label>
          )}
        </AISection>
      )}

      {completeResult && (
        <AISection
          title="补全候选"
          hint="只补空白字段，锁定字段不会被写入"
          actions={
            <div className="flex gap-2">
              <AIButton label="写回选中字段" tone="primary" onClick={applySelectedFields} />
              <AIButton label="取消" onClick={() => setCompleteResult(null)} />
            </div>
          }
        >
          {Object.keys(completeResult.filled_fields).length === 0 && (
            <div className="text-xs text-muted">没有可补全的空字段</div>
          )}
          {Object.entries(completeResult.filled_fields).map(([key, value]) => (
            <AdoptRow
              key={key}
              label={labelOf(key)}
              value={value}
              checked={!!selectedFields[key]}
              onToggle={() =>
                setSelectedFields((prev) => ({ ...prev, [key]: !prev[key] }))
              }
            />
          ))}
          {completeResult.conflicts.length > 0 && (
            <div className="text-xs text-amber-700">
              补全时发现冲突：{completeResult.conflicts.join("；")}
            </div>
          )}
        </AISection>
      )}

      {extractItems && extractItems.length > 0 && (
        <AISection title="提取结果" hint={`来源：${sourceDocumentLabel}`}>
          {extractItems.map((item, index) => (
            <div key={index} className="rounded border border-border bg-surface px-2 py-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-foreground">{item.name}</span>
                <span className="text-muted">
                  {item.type} · 置信度 {item.confidence.toFixed(2)}
                </span>
                {item.suggested_action === "merge" && (
                  <span className="text-amber-700">建议合并到 {item.duplicate_of || "已有卡片"}</span>
                )}
              </div>
              {item.evidence && <div className="mt-1 text-muted">依据：{item.evidence}</div>}
              {item.state_change && (
                <div className="mt-1 text-amber-700">状态变化：{item.state_change}</div>
              )}
              <div className="mt-2 flex flex-wrap gap-2">
                <AIButton
                  label="采纳为新建卡"
                  tone="primary"
                  onClick={() => {
                    onCreateCard({
                      type: item.type,
                      name: item.name,
                      aliases: item.aliases,
                      fields: item.fields,
                      current_state: "",
                      importance: 3,
                      reason: item.evidence,
                      conflicts: [],
                      source: "ai_extracted",
                    });
                    setExtractItems((prev) => (prev ?? []).filter((_, i) => i !== index));
                  }}
                />
                <AIButton
                  label="跳过"
                  onClick={() =>
                    setExtractItems((prev) => (prev ?? []).filter((_, i) => i !== index))
                  }
                />
              </div>
            </div>
          ))}
        </AISection>
      )}

      {conflictItems && (
        <AISection
          title="冲突检查结果"
          actions={<AIButton label="关闭" onClick={() => setConflictItems(null)} />}
        >
          {conflictItems.length === 0 && (
            <div className="text-xs text-muted">未发现明显冲突</div>
          )}
          {conflictItems.map((item, index) => (
            <div key={index} className="rounded border border-border bg-surface px-2 py-1.5 text-xs">
              <div className="flex items-center gap-2">
                <SeverityBadge severity={item.severity} />
                {item.related_card_ids.length > 0 && (
                  <span className="text-muted">{item.related_card_ids.join("、")}</span>
                )}
              </div>
              <div className="mt-1 text-foreground">{item.message}</div>
              {item.suggestion && <div className="mt-0.5 text-muted">建议：{item.suggestion}</div>}
            </div>
          ))}
        </AISection>
      )}
    </div>
  );
}
