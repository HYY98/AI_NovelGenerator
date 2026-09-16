/** 正文修改建议的数据契约（对应后端 text_revisions 集合）。 */

/** 修改类型：补充、事实修正、术语统一。本次不实现大范围整改。 */
export type TextRevisionType = "supplement" | "correction" | "terminology";

/** 建议状态。版本变化后由后端标记为 conflicted，前端禁止按旧坐标写入。 */
export type TextRevisionStatus = "pending" | "accepted" | "rejected" | "conflicted";

/** 正文字符范围。 */
export interface TextRange {
  start: number;
  end: number;
}

/** 一条正文修改建议。 */
export interface TextRevision {
  revision_id: string;
  novel_id?: string;
  chapter_id?: string;
  generation_id?: string | null;
  /** 建议生成时的章节版本；采纳时必须与当前版本一致。 */
  chapter_version: number;
  target_range: TextRange;
  before_text: string;
  after_text: string;
  revision_type: TextRevisionType;
  reason: string;
  status: TextRevisionStatus;
  /** 关联卡片业务 ID，用于展示建议来源。 */
  card_ids?: string[];
  warnings?: string[];
  accepted_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** 生成建议接口直接返回的候选（尚未持久化或刚持久化的轻量结构）。 */
export interface TextRevisionCandidate {
  revision_id: string;
  before_text: string;
  after_text: string;
  reason: string;
  revision_type: TextRevisionType;
  target_range?: TextRange;
  card_ids?: string[];
  warnings?: string[];
}

export const TEXT_REVISION_TYPE_LABEL: Record<TextRevisionType, string> = {
  supplement: "补充",
  correction: "事实修正",
  terminology: "术语统一",
};

export const TEXT_REVISION_STATUS_LABEL: Record<TextRevisionStatus, string> = {
  pending: "待确认",
  accepted: "已采纳",
  rejected: "已拒绝",
  conflicted: "已失效",
};

/** 功能：判断修改建议是否仍可安全写入当前正文。
 *
 * 同时校验章节版本和「原文精确匹配」两个条件，只要有一个不满足就判定失效，
 * 避免按旧坐标覆盖用户已经改过的正文。
 *
 * Args:
 *   revision: 待校验的修改建议。
 *   currentChapterVersion: 当前章节版本。
 *   content: 当前章节正文。
 *
 * Returns:
 *   可写入时返回 {ok:true}；否则返回 {ok:false, reason}。
 */
export function checkTextRevisionApplicable(
  revision: Pick<TextRevision, "chapter_version" | "target_range" | "before_text" | "status">,
  currentChapterVersion: number,
  content: string
): { ok: boolean; reason?: string } {
  if (revision.status === "conflicted") {
    return { ok: false, reason: "该建议已因正文变化失效，请重新分析本章设定" };
  }
  if (revision.status === "accepted") {
    return { ok: false, reason: "该建议已采纳，不能重复写入" };
  }
  if (revision.status === "rejected") {
    return { ok: false, reason: "该建议已被拒绝" };
  }
  if (revision.chapter_version !== currentChapterVersion) {
    return {
      ok: false,
      reason: `正文版本已变化（建议基于 v${revision.chapter_version}，当前 v${currentChapterVersion}），请重新分析后再采纳`,
    };
  }
  const currentSlice = content.slice(revision.target_range.start, revision.target_range.end);
  if (currentSlice !== revision.before_text) {
    return { ok: false, reason: "目标范围内的正文已发生变化，建议失效，请重新分析" };
  }
  return { ok: true };
}

/** 功能：按 start 升序排序建议，保证展示顺序与正文顺序一致。
 * Args: revisions: 待排序建议。
 * Returns: 排序后的新数组，不修改入参。
 */
export function sortTextRevisionsByRange<T extends { target_range?: TextRange }>(
  revisions: T[]
): T[] {
  return [...revisions].sort(
    (a, b) => (a.target_range?.start ?? 0) - (b.target_range?.start ?? 0)
  );
}

// ---------------------------------------------------------------------------
// 正文影响分析（模块 4.3.4）：卡片修改后先定位受影响章节，再逐处生成建议
// ---------------------------------------------------------------------------

/** 正文中被命中的一处位置。 */
export interface TextRevisionImpactMatch {
  start: number;
  end: number;
  text: string;
}

/** 某张卡片在某个章节中的命中摘要。 */
export interface TextRevisionImpactCardHit {
  card_id: string;
  card_name: string;
  card_type: string;
  hit_count: number;
  matches: TextRevisionImpactMatch[];
}

/** 正文影响分析命中的一个章节。 */
export interface TextRevisionImpactChapter {
  chapter_id: string;
  chapter_number?: number | null;
  title?: string;
  chapter_version: number;
  impacts: TextRevisionImpactCardHit[];
}

/** 正文影响分析响应：定位结果 + 参与分析的卡片摘要。 */
export interface TextRevisionImpactResult {
  novel_id: string;
  cards?: {
    card_id: string;
    name: string;
    type: string;
    version: number;
    current_state?: string;
    tokens?: string[];
  }[];
  chapters: TextRevisionImpactChapter[];
  total_chapters: number;
  warnings: string[];
}
