"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiGet, apiPost, apiPut, apiDelete, ApiRequestError } from "@/lib/api";

type CardType = "location" | "item" | "rule";

interface StateHistoryItem {
  from: string;
  to: string;
  note: string;
  chapter: string;
}

interface Card {
  _id: string;
  card_id: string;
  name: string;
  aliases: string[];
  fields: Record<string, string>;
  enabled: boolean;
  importance: number;
  first_appearance_chapter: string;
  current_state: string;
  state_history?: StateHistoryItem[];
  tags: string[];
  version: number;
  is_deleted?: boolean;
}

// 字段结构与后端 setting_card_repository.CARD_TYPES 保持一致（权威在后端）
const CARD_SCHEMA: Record<
  CardType,
  { title: string; hint: string; stateOptions: string[]; fields: [string, string][] }
> = {
  location: {
    title: "地点卡",
    hint: "管理地点的空间状态：层级、环境、出入条件与状态变化，保证空间连续性。",
    stateOptions: ["开放", "封锁", "已毁", "迁移", "未知"],
    fields: [
      ["location_type", "地点类型"],
      ["region", "所属区域"],
      ["parent_location", "上级地点"],
      ["appearance", "环境外观"],
      ["atmosphere", "氛围"],
      ["geographic_features", "特殊地理特征"],
      ["purpose", "剧情用途"],
      ["access_conditions", "出入条件"],
      ["danger_factors", "危险因素"],
      ["related_characters", "关联人物"],
      ["related_factions", "关联势力"],
      ["related_items", "关联物品"],
      ["notes", "备注"],
    ],
  },
  item: {
    title: "物品卡",
    hint: "管理物品的归属、能力、限制与流转，避免凭空出现或能力越权。",
    stateOptions: ["完好", "受损", "已损毁", "遗失", "封印中"],
    fields: [
      ["category", "物品类型"],
      ["appearance", "外观"],
      ["origin", "来源"],
      ["maker", "制造者"],
      ["owner", "当前持有者"],
      ["abilities", "能力和使用方式"],
      ["limitations", "使用限制"],
      ["cost", "代价"],
      ["cooldown", "冷却时间"],
      ["quantity", "数量"],
      ["durability", "耐久/损毁状态"],
      ["secret", "重要秘密或揭示阶段"],
      ["notes", "备注"],
    ],
  },
  rule: {
    title: "设定规则卡",
    hint: "区分世界硬规则与写作软规则，硬规则会强制注入章节生成。",
    stateOptions: ["生效中", "已失效", "待揭示"],
    fields: [
      ["rule_category", "规则分类"],
      ["scope", "适用范围"],
      ["definition", "规则定义"],
      ["trigger", "触发条件"],
      ["constraints", "约束和代价"],
      ["exceptions", "例外条件"],
      ["consequences", "违反后果"],
      ["priority", "规则优先级"],
      ["is_hard_rule", "是否硬规则(必须遵守)"],
      ["notes", "备注"],
    ],
  },
};

function emptyFields(type: CardType): Record<string, string> {
  return Object.fromEntries(CARD_SCHEMA[type].fields.map(([key]) => [key, ""]));
}

function errMsg(e: unknown, fallback: string): string {
  return e instanceof ApiRequestError ? e.message : fallback;
}

// ============================================================
// 设定卡工作区
// ============================================================
export function CardWorkspace({ type, novelId }: { type: CardType; novelId?: string }) {
  const schema = CARD_SCHEMA[type];
  const [cards, setCards] = useState<Card[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [showDeleted, setShowDeleted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cardsRef = useRef<Card[]>([]);
  cardsRef.current = cards;

  const loadCards = useCallback(async () => {
    if (!novelId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const list = await apiGet<Card[]>(
        `/api/setting-cards/${novelId}?type=${type}&include_deleted=${showDeleted}`
      );
      // 回收站视图只展示已软删除的卡片
      setCards(showDeleted ? list.filter((c) => c.is_deleted) : list);
      setSelected((prev) => (prev && list.some((c) => c._id === prev) ? prev : null));
    } catch (e) {
      setError(errMsg(e, "加载失败"));
    } finally {
      setLoading(false);
    }
  }, [novelId, type, showDeleted]);

  useEffect(() => {
    loadCards();
  }, [loadCards]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return cards;
    return cards.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.aliases.some((a) => a.toLowerCase().includes(q))
    );
  }, [cards, query]);

  const current = cards.find((c) => c._id === selected) || null;

  const create = async () => {
    if (!novelId) {
      setError("请先保存小说后再添加卡片");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const card = await apiPost<Card>(`/api/setting-cards/${novelId}`, {
        type,
        name: "未命名",
        enabled: true,
        importance: 3,
        aliases: [],
        tags: [],
        fields: emptyFields(type),
      });
      setCards((prev) => [...prev, card]);
      setSelected(card._id);
    } catch (e) {
      setError(errMsg(e, "创建失败"));
    } finally {
      setSaving(false);
    }
  };

  // 本地即时更新 + 防抖整体保存（带乐观锁版本）
  const patch = (change: Partial<Card>) => {
    if (!current || current.is_deleted) return;
    setConflict(null);
    setCards((prev) => prev.map((c) => (c._id === current._id ? { ...c, ...change } : c)));
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      const latest = cardsRef.current.find((c) => c._id === current._id);
      if (!latest) return;
      setSaving(true);
      try {
        const saved = await apiPut<Card>(
          `/api/setting-cards/${novelId}/${latest._id}?expected_version=${latest.version}`,
          {
            name: latest.name,
            aliases: latest.aliases,
            fields: latest.fields,
            enabled: latest.enabled,
            importance: latest.importance,
            first_appearance_chapter: latest.first_appearance_chapter,
            current_state: latest.current_state,
            tags: latest.tags,
          }
        );
        // 只回写版本与状态历史，避免覆盖用户正在输入的内容
        setCards((prev) =>
          prev.map((c) =>
            c._id === latest._id
              ? { ...c, version: saved.version, state_history: saved.state_history }
              : c
          )
        );
        setConflict(null);
      } catch (e) {
        if (e instanceof ApiRequestError && e.status === 409) {
          setConflict(e.message);
        } else {
          setError(errMsg(e, "保存失败"));
        }
      } finally {
        setSaving(false);
      }
    }, 600);
  };

  const patchField = (key: string, value: string) => {
    if (!current) return;
    patch({ fields: { ...current.fields, [key]: value } });
  };

  const reloadOne = async () => {
    if (!current || !novelId) return;
    try {
      const fresh = await apiGet<Card>(
        `/api/setting-cards/${novelId}/${current._id}?include_deleted=true`
      );
      setCards((prev) => prev.map((c) => (c._id === fresh._id ? fresh : c)));
      setConflict(null);
    } catch (e) {
      setError(errMsg(e, "重新加载失败"));
    }
  };

  const forceOverwrite = async () => {
    if (!current || !novelId) return;
    setSaving(true);
    try {
      const saved = await apiPut<Card>(`/api/setting-cards/${novelId}/${current._id}`, {
        name: current.name,
        aliases: current.aliases,
        fields: current.fields,
        enabled: current.enabled,
        importance: current.importance,
        first_appearance_chapter: current.first_appearance_chapter,
        current_state: current.current_state,
        tags: current.tags,
      });
      setCards((prev) => prev.map((c) => (c._id === saved._id ? saved : c)));
      setConflict(null);
    } catch (e) {
      setError(errMsg(e, "覆盖失败"));
    } finally {
      setSaving(false);
    }
  };

  const softRemove = async () => {
    if (!current || !novelId) return;
    if (!window.confirm(`确定把「${current.name}」移入回收站吗？`)) return;
    try {
      await apiDelete(`/api/setting-cards/${novelId}/${current._id}`);
      if (!showDeleted) {
        setCards((prev) => prev.filter((c) => c._id !== current._id));
        setSelected(null);
      } else {
        await loadCards();
      }
    } catch (e) {
      setError(errMsg(e, "删除失败"));
    }
  };

  const restore = async () => {
    if (!current || !novelId) return;
    try {
      await apiPost<Card>(`/api/setting-cards/${novelId}/${current._id}/restore`, {});
      await loadCards();
    } catch (e) {
      setError(errMsg(e, "恢复失败"));
    }
  };

  const hardRemove = async () => {
    if (!current || !novelId) return;
    if (!window.confirm(`彻底删除「${current.name}」后不可恢复，确定吗？`)) return;
    try {
      await apiDelete(`/api/setting-cards/${novelId}/${current._id}/hard`);
      setCards((prev) => prev.filter((c) => c._id !== current._id));
      setSelected(null);
    } catch (e) {
      setError(errMsg(e, "彻底删除失败"));
    }
  };

  if (!novelId) {
    return (
      <section className="flex h-full items-center justify-center bg-background p-5 md:p-7">
        <p className="text-sm text-muted">请先在「小说信息」中保存小说，之后即可维护{schema.title}。</p>
      </section>
    );
  }

  const importanceStars = (n: number) => "★".repeat(n) + "☆".repeat(Math.max(0, 5 - n));

  return (
    <section className="flex h-full min-h-0 flex-col gap-4 bg-background p-5 md:p-7">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-foreground">{schema.title}</h1>
          <p className="mt-1 text-sm text-muted">{schema.hint}</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowDeleted((v) => !v)}
            className={`rounded-lg border px-3 py-1.5 text-xs ${
              showDeleted ? "border-accent text-accent" : "border-border text-muted"
            }`}
          >
            {showDeleted ? "← 返回正常列表" : "回收站"}
          </button>
          <span className="text-xs text-muted">
            {saving ? "保存中…" : loading ? "加载中…" : showDeleted ? `回收站 ${cards.length} 张` : `共 ${cards.length} 张`}
          </span>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-600">{error}</div>
      )}
      {conflict && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-700">
          <div>版本冲突：{conflict}</div>
          <div className="mt-2 flex gap-2">
            <button onClick={reloadOne} className="rounded border border-amber-400 px-2 py-1 text-xs">
              放弃修改，加载服务器版本
            </button>
            <button onClick={forceOverwrite} className="rounded border border-amber-400 px-2 py-1 text-xs">
              以我的内容强制覆盖
            </button>
          </div>
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col gap-4 md:flex-row">
        <aside className="flex w-full flex-col gap-3 md:w-64">
          {!showDeleted && (
            <div className="flex gap-2">
              <input
                className="min-w-0 flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索名称/别名"
              />
              <button
                onClick={create}
                disabled={saving}
                className="shrink-0 rounded-lg bg-accent px-3 py-2 text-sm text-white disabled:opacity-50"
              >
                新增
              </button>
            </div>
          )}
          <div className="min-h-32 flex-1 space-y-1 overflow-auto rounded-xl border border-border bg-surface p-2">
            {loading ? (
              <p className="p-5 text-center text-sm text-muted">加载中…</p>
            ) : (
              visible.map((card) => (
                <button
                  key={card._id}
                  onClick={() => setSelected(card._id)}
                  className={`flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm ${
                    selected === card._id ? "bg-accent/10 text-accent" : "hover:bg-surface-secondary"
                  }`}
                >
                  <span className="truncate">
                    {card.is_deleted && <span className="mr-1 text-red-500">[已删]</span>}
                    {card.name}
                  </span>
                  <span className="ml-auto shrink-0 text-xs text-muted">
                    {showDeleted ? "" : card.enabled ? "启用" : "停用"}
                  </span>
                </button>
              ))
            )}
            {!loading && !visible.length && (
              <p className="p-5 text-center text-sm text-muted">
                {showDeleted ? "回收站为空" : `还没有${schema.title}`}
              </p>
            )}
          </div>
        </aside>

        <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-border bg-surface p-5">
          {current ? (
            current.is_deleted ? (
              <div className="space-y-4">
                <h2 className="text-lg font-medium">{current.name}（已删除）</h2>
                <p className="text-sm text-muted">业务ID：{current.card_id}</p>
                <div className="flex gap-3">
                  <button onClick={restore} className="rounded-lg bg-accent px-4 py-2 text-sm text-white">
                    恢复此卡
                  </button>
                  <button
                    onClick={hardRemove}
                    className="rounded-lg border border-red-200 px-4 py-2 text-sm text-red-600"
                  >
                    彻底删除
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-3">
                  <input
                    className="min-w-0 flex-1 rounded-lg border border-border px-3 py-2 text-base font-medium"
                    value={current.name}
                    onChange={(e) => patch({ name: e.target.value })}
                  />
                  <label className="flex shrink-0 items-center gap-2 text-sm text-muted">
                    <input
                      type="checkbox"
                      checked={current.enabled}
                      onChange={(e) => patch({ enabled: e.target.checked })}
                    />
                    生成时启用
                  </label>
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <label className="block text-sm">
                    <span className="mb-1 block text-muted">别名（逗号分隔）</span>
                    <input
                      className="w-full rounded-lg border border-border px-3 py-2"
                      value={current.aliases.join("，")}
                      onChange={(e) =>
                        patch({
                          aliases: e.target.value
                            .split(/[,，]/)
                            .map((s) => s.trim())
                            .filter(Boolean),
                        })
                      }
                    />
                  </label>
                  <label className="block text-sm">
                    <span className="mb-1 block text-muted">重要性 {importanceStars(current.importance)}</span>
                    <select
                      className="w-full rounded-lg border border-border px-3 py-2"
                      value={current.importance}
                      onChange={(e) => patch({ importance: Number(e.target.value) })}
                    >
                      {[1, 2, 3, 4, 5].map((n) => (
                        <option key={n} value={n}>
                          {n} 星
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block text-sm">
                    <span className="mb-1 block text-muted">首次出现章节</span>
                    <input
                      className="w-full rounded-lg border border-border px-3 py-2"
                      value={current.first_appearance_chapter || ""}
                      onChange={(e) => patch({ first_appearance_chapter: e.target.value })}
                      placeholder="如：第3章"
                    />
                  </label>
                </div>

                <label className="block text-sm">
                  <span className="mb-1 block text-muted">当前状态</span>
                  <input
                    className="w-full rounded-lg border border-border px-3 py-2"
                    list={`state-options-${type}`}
                    value={current.current_state || ""}
                    onChange={(e) => patch({ current_state: e.target.value })}
                    placeholder="选择或输入当前状态"
                  />
                  <datalist id={`state-options-${type}`}>
                    {schema.stateOptions.map((s) => (
                      <option key={s} value={s} />
                    ))}
                  </datalist>
                </label>

                {schema.fields.map(([key, label]) => (
                  <label key={key} className="block text-sm">
                    <span className="mb-1 block text-muted">{label}</span>
                    <textarea
                      className="min-h-16 w-full rounded-lg border border-border px-3 py-2"
                      value={current.fields[key] || ""}
                      onChange={(e) => patchField(key, e.target.value)}
                    />
                  </label>
                ))}

                {current.state_history && current.state_history.length > 0 && (
                  <div className="rounded-lg bg-surface-secondary p-3 text-xs text-muted">
                    <div className="mb-1 font-medium">状态变化记录</div>
                    {current.state_history.map((h, i) => (
                      <div key={i}>
                        「{h.from || "空"}」→「{h.to}」{h.chapter ? `（${h.chapter}）` : ""}
                        {h.note ? `：${h.note}` : ""}
                      </div>
                    ))}
                  </div>
                )}

                <div className="flex items-center justify-between border-t border-border pt-3">
                  <span className="text-xs text-muted">
                    {current.card_id} · v{current.version}
                  </span>
                  <button
                    className="rounded-lg border border-red-200 px-4 py-2 text-sm text-red-600"
                    onClick={softRemove}
                  >
                    移入回收站
                  </button>
                </div>
              </div>
            )
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-muted">
              选择一张卡片开始编辑，或点击「新增」创建
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

// ============================================================
// 章节编辑工作区（真实后端 + 自动保存 + 乐观锁 + 关联实体）
// ============================================================
type ChapterStatus = "draft" | "editing" | "finalized";
const STATUS_LABEL: Record<ChapterStatus, string> = {
  draft: "草稿",
  editing: "修改中",
  finalized: "已定稿",
};

// 状态下拉可选项：已定稿后不允许直接回草稿，只能重开为修改中
const statusOptionsFor = (s: ChapterStatus): { value: ChapterStatus; label: string }[] => {
  if (s === "finalized") {
    return [
      { value: "finalized", label: "已定稿" },
      { value: "editing", label: "重新打开为修改中" },
    ];
  }
  return [
    { value: "draft", label: "草稿" },
    { value: "editing", label: "修改中" },
    { value: "finalized", label: "已定稿" },
  ];
};

interface Chapter {
  _id: string;
  chapter_id: string;
  number: number;
  title: string;
  content: string;
  status: ChapterStatus;
  word_count: number;
  volume_id: string | null;
  linked_character_ids: string[];
  linked_location_ids: string[];
  linked_item_ids: string[];
  linked_rule_ids: string[];
  summary: string;
  unresolved_threads: string[];
  version: number;
  is_deleted?: boolean;
  deleted_at?: string | null;
}

interface LinkOption {
  id: string; // 业务ID：角色 char_ / 地点 loc_ / 物品 itm_ / 规则 rul_
  name: string;
}

export function ChapterEditorWorkspace({ novelId }: { novelId?: string }) {
  const [list, setList] = useState<Chapter[]>([]);
  const [current, setCurrent] = useState<Chapter | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedTip, setSavedTip] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [showDeleted, setShowDeleted] = useState(false);
  const [linkOpts, setLinkOpts] = useState<{
    characters: LinkOption[];
    locations: LinkOption[];
    items: LinkOption[];
    rules: LinkOption[];
  }>({ characters: [], locations: [], items: [], rules: [] });

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentRef = useRef<Chapter | null>(null);
  const pendingRef = useRef<Partial<Chapter>>({});
  currentRef.current = current;

  const loadList = useCallback(async () => {
    if (!novelId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const res = await apiGet<{ data: Chapter[] }>(
        `/api/chapters/novel/${novelId}?include_deleted=true`
      );
      const all = res.data || [];
      // 回收站只看已删，正常列表只看未删
      setList(all.filter((c) => (showDeleted ? c.is_deleted : !c.is_deleted)));
    } catch (e) {
      setError(errMsg(e, "章节列表加载失败"));
    } finally {
      setLoading(false);
    }
  }, [novelId, showDeleted]);

  // 加载关联实体选项（角色 + 三类设定卡）
  useEffect(() => {
    if (!novelId) return;
    (async () => {
      try {
        const [charRaw, locRaw, itemRaw, ruleRaw] = await Promise.all([
          apiGet<unknown>(`/api/characters/novel/${novelId}`),
          apiGet<{ card_id: string; name: string }[]>(
            `/api/setting-cards/${novelId}?type=location&enabled_only=true`
          ),
          apiGet<{ card_id: string; name: string }[]>(
            `/api/setting-cards/${novelId}?type=item&enabled_only=true`
          ),
          apiGet<{ card_id: string; name: string }[]>(
            `/api/setting-cards/${novelId}?type=rule&enabled_only=true`
          ),
        ]);
        // 角色接口返回 {data:[...]}，角色唯一标识为业务ID character_id（无 _id）
        let characters: LinkOption[] = [];
        const charArr = Array.isArray(charRaw)
          ? (charRaw as { character_id: string; name: string }[])
          : ((charRaw as { data?: { character_id: string; name: string }[] })?.data || []);
        characters = (charArr || [])
          .filter((c) => c.character_id)
          .map((c) => ({ id: c.character_id, name: c.name }));
        const toOpt = (arr: { card_id: string; name: string }[]): LinkOption[] =>
          (arr || []).filter((c) => c.card_id).map((c) => ({ id: c.card_id, name: c.name }));
        setLinkOpts({
          characters,
          locations: toOpt(locRaw),
          items: toOpt(itemRaw),
          rules: toOpt(ruleRaw),
        });
      } catch {
        // 关联选项加载失败不阻塞主编辑
      }
    })();
  }, [novelId]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const openChapter = async (id: string) => {
    setConflict(null);
    setError(null);
    try {
      const ch = await apiGet<Chapter>(`/api/chapters/${id}?include_deleted=true`);
      setCurrent(ch);
    } catch (e) {
      setError(errMsg(e, "章节加载失败"));
    }
  };

  const createChapter = async () => {
    if (!novelId) return;
    try {
      const ch = await apiPost<Chapter>(`/api/chapters/novel/${novelId}/create`, {});
      await loadList();
      setCurrent(ch);
    } catch (e) {
      setError(errMsg(e, "新建章节失败"));
    }
  };

  // 防抖自动保存（带乐观锁）；连续修改不同字段时累积变更，避免前一次改动被清掉
  const scheduleSave = useCallback((patchObj: Partial<Chapter>) => {
    if (!currentRef.current) return;
    setCurrent((prev) => (prev ? { ...prev, ...patchObj } : prev));
    setConflict(null);
    Object.assign(pendingRef.current, patchObj);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      const latest = currentRef.current;
      if (!latest) {
        pendingRef.current = {};
        return;
      }
      const body = { ...pendingRef.current };
      pendingRef.current = {};
      setSaving(true);
      try {
        const saved = await apiPut<Chapter>(
          `/api/chapters/${latest._id}?expected_version=${latest.version}`,
          body
        );
        setCurrent((prev) =>
          prev ? { ...prev, version: saved.version, word_count: saved.word_count } : prev
        );
        // 同步左侧列表的标题与状态
        setList((prev) =>
          prev.map((c) => (c._id === saved._id ? { ...c, title: saved.title, status: saved.status } : c))
        );
        setConflict(null);
        setSavedTip("已自动保存 " + new Date().toLocaleTimeString());
      } catch (e) {
        // 保存失败，把变更放回待保存队列，下次编辑重试
        Object.assign(pendingRef.current, body);
        if (e instanceof ApiRequestError && e.status === 409) setConflict(e.message);
        else setError(errMsg(e, "保存失败"));
      } finally {
        setSaving(false);
      }
    }, 700);
  }, []);

  const changeStatus = async (status: ChapterStatus) => {
    if (!current || status === current.status) return;
    try {
      if (status === "finalized") {
        if (!window.confirm("确定将本章定稿吗？定稿后正文将锁定，需重新打开为「修改中」才能继续编辑。")) return;
        const saved = await apiPost<Chapter>(`/api/chapters/${current._id}/finalize`, {});
        setCurrent(saved);
        await loadList();
      } else if (status === "editing" && current.status === "finalized") {
        // 已定稿重新打开需要确认，走 reopen；后端同样禁止定稿直接回草稿
        if (!window.confirm("重新打开后本章变为「修改中」，可以继续编辑正文。确定吗？")) return;
        const saved = await apiPost<Chapter>(`/api/chapters/${current._id}/reopen`, {});
        setCurrent(saved);
        await loadList();
      } else {
        // 草稿 <-> 修改中 之间切换走普通自动保存
        scheduleSave({ status });
      }
    } catch (e) {
      setError(errMsg(e, "状态更新失败"));
    }
  };

  const removeChapter = async () => {
    if (!current) return;
    if (!window.confirm(`确定删除第 ${current.number} 章「${current.title}」吗？（可在章节回收站恢复）`)) return;
    try {
      await apiDelete(`/api/chapters/${current._id}`);
      setCurrent(null);
      await loadList();
    } catch (e) {
      setError(errMsg(e, "删除失败"));
    }
  };

  const restoreChapter = async () => {
    if (!current) return;
    try {
      const saved = await apiPost<Chapter>(`/api/chapters/${current._id}/restore`, {});
      setCurrent(saved);
      await loadList();
      setShowDeleted(false);
    } catch (e) {
      setError(errMsg(e, "恢复失败"));
    }
  };

  const gotoNeighbor = async (dir: "prev" | "next") => {
    if (!current || !novelId) return;
    try {
      const nav = await apiGet<{ prev: Chapter | null; next: Chapter | null }>(
        `/api/chapters/novel/${novelId}/navigation/${current.number}`
      );
      const target = nav[dir];
      if (target) await openChapter(target._id);
    } catch (e) {
      setError(errMsg(e, "导航失败"));
    }
  };

  const replaceAll = () => {
    if (!current || !findText) return;
    scheduleSave({ content: current.content.split(findText).join(replaceText) });
  };

  const toggleLink = (
    field: keyof Pick<
      Chapter,
      "linked_character_ids" | "linked_location_ids" | "linked_item_ids" | "linked_rule_ids"
    >,
    id: string
  ) => {
    const cur = currentRef.current;
    if (!cur) return;
    // 优先基于尚未 flush 的待保存值，保证快速连续点选同一分组不丢失
    const base = (pendingRef.current[field] as string[] | undefined) || cur[field] || [];
    const next = base.includes(id) ? base.filter((x) => x !== id) : [...base, id];
    scheduleSave({ [field]: next } as Partial<Chapter>);
  };

  const wordCount = current ? current.content.replace(/\s/g, "").length : 0;
  const finalized = current?.status === "finalized";
  const deleted = !!current?.is_deleted;
  const readOnly = finalized || deleted;

  const LinkGroup = ({
    title,
    options,
    field,
  }: {
    title: string;
    options: LinkOption[];
    field: "linked_character_ids" | "linked_location_ids" | "linked_item_ids" | "linked_rule_ids";
  }) => {
    if (!options.length) return null;
    const selectedIds = current?.[field] || [];
    return (
      <div className="text-sm">
        <div className="mb-1 text-muted">{title}</div>
        <div className="flex flex-wrap gap-1.5">
          {options.map((o) => {
            const on = selectedIds.includes(o.id);
            return (
              <button
                key={o.id}
                onClick={() => toggleLink(field, o.id)}
                className={`rounded-full border px-2.5 py-1 text-xs ${
                  on ? "border-accent bg-accent/10 text-accent" : "border-border text-muted"
                }`}
              >
                {o.name}
              </button>
            );
          })}
        </div>
      </div>
    );
  };

  if (!novelId) {
    return (
      <section className="flex h-full items-center justify-center bg-background p-7">
        <p className="text-sm text-muted">请先在「小说信息」中保存小说，之后即可进行章节编辑。</p>
      </section>
    );
  }

  return (
    <section className="flex h-full min-h-0 flex-col gap-3 bg-background p-5 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">章节编辑</h1>
          <p className="mt-1 text-sm text-muted">逐章维护正文，自动保存草稿，定稿前做一致性检查。</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted">
          {saving ? "保存中…" : savedTip}
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-600">{error}</div>
      )}
      {conflict && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-700">
          版本冲突：{conflict}
          <button
            onClick={() => current && openChapter(current._id)}
            className="ml-3 rounded border border-amber-400 px-2 py-0.5 text-xs"
          >
            重新加载服务器版本
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row">
        {/* 左：章节列表 */}
        <aside className="flex w-full flex-col gap-2 lg:w-56">
          {!showDeleted && (
            <button
              onClick={createChapter}
              className="shrink-0 rounded-lg bg-accent px-3 py-2 text-sm text-white"
            >
              + 新建章节
            </button>
          )}
          <button
            onClick={() => {
              setShowDeleted((v) => !v);
              setCurrent(null);
            }}
            className={`shrink-0 rounded-lg border px-3 py-1.5 text-xs ${
              showDeleted ? "border-accent text-accent" : "border-border text-muted"
            }`}
          >
            {showDeleted ? "← 返回章节列表" : "章节回收站"}
          </button>
          <div className="min-h-28 flex-1 space-y-1 overflow-auto rounded-xl border border-border bg-surface p-2">
            {loading ? (
              <p className="p-4 text-center text-sm text-muted">加载中…</p>
            ) : (
              list.map((ch) => (
                <button
                  key={ch._id}
                  onClick={() => openChapter(ch._id)}
                  className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm ${
                    current?._id === ch._id ? "bg-accent/10 text-accent" : "hover:bg-surface-secondary"
                  }`}
                >
                  <span className="shrink-0 text-xs text-muted">{ch.number}</span>
                  <span className="min-w-0 flex-1 truncate">
                    {showDeleted && <span className="mr-1 text-red-500">[已删]</span>}
                    {ch.title || `第${ch.number}章`}
                  </span>
                  {!showDeleted && (
                    <span className="shrink-0 text-[10px] text-muted">{STATUS_LABEL[ch.status]}</span>
                  )}
                </button>
              ))
            )}
            {!loading && !list.length && (
              <p className="p-4 text-center text-sm text-muted">
                {showDeleted ? "回收站为空" : "还没有章节"}
              </p>
            )}
          </div>
        </aside>

        {/* 中：正文编辑 */}
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          {current ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  className="min-w-0 flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-base font-medium"
                  value={current.title}
                  disabled={readOnly}
                  onChange={(e) => scheduleSave({ title: e.target.value })}
                />
                {deleted ? (
                  <>
                    <span className="text-sm text-red-500">该章节已删除</span>
                    <button
                      onClick={restoreChapter}
                      className="rounded-lg bg-accent px-3 py-2 text-sm text-white"
                    >
                      恢复章节
                    </button>
                  </>
                ) : (
                  <>
                    <select
                      className="rounded-lg border border-border bg-surface px-2 py-2 text-sm"
                      value={current.status}
                      onChange={(e) => changeStatus(e.target.value as ChapterStatus)}
                    >
                      {statusOptionsFor(current.status).map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                    <button onClick={() => gotoNeighbor("prev")} className="rounded-lg border border-border px-3 py-2 text-sm">
                      上一章
                    </button>
                    <button onClick={() => gotoNeighbor("next")} className="rounded-lg border border-border px-3 py-2 text-sm">
                      下一章
                    </button>
                    <button onClick={removeChapter} className="rounded-lg border border-red-200 px-3 py-2 text-sm text-red-600">
                      删除
                    </button>
                  </>
                )}
              </div>

              {!readOnly && (
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    className="w-28 rounded-lg border border-border bg-surface px-3 py-1.5 text-sm"
                    value={findText}
                    onChange={(e) => setFindText(e.target.value)}
                    placeholder="查找"
                  />
                  <input
                    className="w-28 rounded-lg border border-border bg-surface px-3 py-1.5 text-sm"
                    value={replaceText}
                    onChange={(e) => setReplaceText(e.target.value)}
                    placeholder="替换为"
                  />
                  <button onClick={replaceAll} className="rounded-lg border border-border px-3 py-1.5 text-sm">
                    全部替换
                  </button>
                  <span className="ml-auto text-xs text-muted">
                    {wordCount} 字 · {current.chapter_id} · v{current.version}
                    {finalized && " · 已定稿，正文锁定"}
                  </span>
                </div>
              )}
              {readOnly && (
                <span className="text-xs text-muted">
                  {wordCount} 字 · {current.chapter_id} · v{current.version}
                  {deleted ? " · 已删除" : " · 已定稿，正文锁定"}
                </span>
              )}

              <textarea
                className={`min-h-[320px] flex-1 resize-y rounded-xl border border-border bg-surface p-4 text-[15px] leading-8 lg:min-h-0 ${
                  readOnly ? "cursor-not-allowed bg-surface-secondary text-muted" : ""
                }`}
                value={current.content}
                readOnly={readOnly}
                onChange={(e) => scheduleSave({ content: e.target.value })}
                placeholder={
                  deleted
                    ? "该章节已删除，可点击「恢复章节」找回"
                    : finalized
                      ? "本章已定稿，如需修改请先切换为「修改中」"
                      : "从这一章开始写..."
                }
              />
            </>
          ) : (
            <div className="flex min-h-40 flex-1 items-center justify-center rounded-xl border border-dashed border-border text-sm text-muted">
              从左侧选择章节，或点击「新建章节」
            </div>
          )}
        </div>

        {/* 右：关联与摘要 */}
        {current && !deleted && (
          <aside className="flex w-full flex-col gap-3 overflow-auto rounded-xl border border-border bg-surface p-3 lg:w-60">
            <LinkGroup title="关联角色" options={linkOpts.characters} field="linked_character_ids" />
            <LinkGroup title="关联地点" options={linkOpts.locations} field="linked_location_ids" />
            <LinkGroup title="关联物品" options={linkOpts.items} field="linked_item_ids" />
            <LinkGroup title="关联规则" options={linkOpts.rules} field="linked_rule_ids" />
            <label className="block text-sm">
              <span className="mb-1 block text-muted">本章摘要</span>
              <textarea
                className="min-h-20 w-full rounded-lg border border-border px-3 py-2"
                value={current.summary}
                onChange={(e) => scheduleSave({ summary: e.target.value })}
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-muted">未解决伏笔（每行一条）</span>
              <textarea
                className="min-h-20 w-full rounded-lg border border-border px-3 py-2"
                value={current.unresolved_threads.join("\n")}
                onChange={(e) =>
                  scheduleSave({
                    unresolved_threads: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
                  })
                }
              />
            </label>
          </aside>
        )}
      </div>
    </section>
  );
}
