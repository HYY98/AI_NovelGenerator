"use client";

import { useEffect, useMemo, useState } from "react";

type CardType = "location" | "item" | "rule";
type Card = { id: string; name: string; enabled: boolean; fields: Record<string, string> };

const CARD_META: Record<CardType, { title: string; fields: [string, string][] }> = {
  location: { title: "地点卡", fields: [["region", "所属区域"], ["appearance", "环境与外观"], ["purpose", "用途与剧情作用"], ["connections", "关联人物与地点"], ["notes", "备注"]] },
  item: { title: "物品卡", fields: [["category", "物品类别"], ["appearance", "外观"], ["abilities", "功能与能力"], ["limitations", "限制与代价"], ["owner", "持有者与归属"], ["notes", "备注"]] },
  rule: { title: "设定规则卡", fields: [["scope", "适用范围"], ["definition", "规则内容"], ["constraints", "约束与代价"], ["exceptions", "例外条件"], ["consequences", "违反后果"], ["notes", "备注"]] },
};

function storageKey(novelId?: string) { return `novel-generator-cards:${novelId || "draft"}`; }

export function CardWorkspace({ type, novelId }: { type: CardType; novelId?: string }) {
  const meta = CARD_META[type];
  const [cards, setCards] = useState<Card[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    try { setCards(JSON.parse(localStorage.getItem(storageKey(novelId)) || "[]")); } catch { setCards([]); }
  }, [novelId]);
  const visible = useMemo(() => cards.filter((c) => c.name.toLowerCase().includes(query.toLowerCase())), [cards, query]);
  const current = cards.find((c) => c.id === selected) || null;
  const update = (next: Card[]) => { setCards(next); localStorage.setItem(storageKey(novelId), JSON.stringify(next)); };
  const create = () => { const card = { id: crypto.randomUUID(), name: "未命名", enabled: true, fields: Object.fromEntries(meta.fields.map(([key]) => [key, ""])) }; update([...cards, card]); setSelected(card.id); };
  const patch = (change: Partial<Card> & { fields?: Record<string, string> }) => current && update(cards.map((c) => c.id === current.id ? { ...c, ...change } : c));

  return <section className="flex h-full min-h-0 flex-col gap-5 bg-background p-5 md:p-7">
    <div><h1 className="text-xl font-semibold text-foreground">{meta.title}</h1><p className="mt-1 text-sm text-muted">把会反复出现的事实沉淀成可检索设定，生成章节时保持一致。</p></div>
    <div className="flex min-h-0 flex-1 flex-col gap-4 md:flex-row">
      <aside className="flex w-full flex-col gap-3 md:w-72">
        <div className="flex gap-2"><input className="min-w-0 flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索卡片" /><button onClick={create} className="rounded-lg bg-accent px-3 py-2 text-sm text-white">新增</button></div>
        <div className="min-h-32 flex-1 space-y-1 overflow-auto rounded-xl border border-border bg-surface p-2">{visible.map((card) => <button key={card.id} onClick={() => setSelected(card.id)} className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm ${selected === card.id ? "bg-accent/10 text-accent" : "hover:bg-surface-secondary"}`}><span className="truncate">{card.name}</span><span className="ml-2 text-xs text-muted">{card.enabled ? "启用" : "停用"}</span></button>)}{!visible.length && <p className="p-5 text-center text-sm text-muted">还没有{meta.title}</p>}</div>
      </aside>
      <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-border bg-surface p-5">{current ? <div className="space-y-4"><div className="flex items-center gap-3"><input className="min-w-0 flex-1 rounded-lg border border-border px-3 py-2 text-base font-medium" value={current.name} onChange={(e) => patch({ name: e.target.value })} /><label className="flex shrink-0 items-center gap-2 text-sm text-muted"><input type="checkbox" checked={current.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />生成时启用</label></div>{meta.fields.map(([key, label]) => <label key={key} className="block text-sm"><span className="mb-1 block text-muted">{label}</span><textarea className="min-h-20 w-full rounded-lg border border-border px-3 py-2" value={current.fields[key]} onChange={(e) => patch({ fields: { ...current.fields, [key]: e.target.value } })} /></label>)}<button className="rounded-lg border border-red-200 px-4 py-2 text-sm text-red-600" onClick={() => { update(cards.filter((c) => c.id !== current.id)); setSelected(null); }}>删除卡片</button></div> : <div className="flex h-full items-center justify-center text-sm text-muted">选择一张卡片开始编辑</div>}</div>
    </div>
  </section>;
}

export function ChapterEditorWorkspace({ novelId }: { novelId?: string }) {
  const key = `novel-generator-chapters:${novelId || "draft"}`;
  const [chapters, setChapters] = useState<Record<string, string>>({});
  const [number, setNumber] = useState("1");
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  useEffect(() => { try { setChapters(JSON.parse(localStorage.getItem(key) || "{}")); } catch { setChapters({}); } }, [key]);
  const text = chapters[number] || "";
  const save = (value: string) => { const next = { ...chapters, [number]: value }; setChapters(next); localStorage.setItem(key, JSON.stringify(next)); };
  const replaceAll = () => find && save(text.split(find).join(replace));
  return <section className="flex h-full min-h-0 flex-col gap-4 bg-background p-5 md:p-7"><div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-xl font-semibold">章节编辑</h1><p className="mt-1 text-sm text-muted">逐章维护正文，保留修改痕迹并在定稿前做一致性检查。</p></div><div className="flex items-center gap-2"><span className="text-sm text-muted">第</span><input className="w-20 rounded-lg border border-border bg-surface px-3 py-2" type="number" min="1" value={number} onChange={(e) => setNumber(String(Math.max(1, Number(e.target.value))))} /><span className="text-sm text-muted">章</span><button className="rounded-lg bg-accent px-4 py-2 text-sm text-white" onClick={() => save(text)}>保存</button></div></div><div className="flex flex-wrap gap-2"><input className="rounded-lg border border-border bg-surface px-3 py-2 text-sm" value={find} onChange={(e) => setFind(e.target.value)} placeholder="查找" /><input className="rounded-lg border border-border bg-surface px-3 py-2 text-sm" value={replace} onChange={(e) => setReplace(e.target.value)} placeholder="替换为" /><button className="rounded-lg border border-border bg-surface px-3 py-2 text-sm" onClick={replaceAll}>全部替换</button><span className="ml-auto self-center text-sm text-muted">{text.replace(/\s/g, "").length} 字</span></div><textarea className="min-h-0 flex-1 resize-none rounded-xl border border-border bg-surface p-5 text-[15px] leading-8" value={text} onChange={(e) => save(e.target.value)} placeholder="从这一章开始写..." /></section>;
}
