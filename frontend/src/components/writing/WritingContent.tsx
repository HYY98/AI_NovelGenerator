"use client";

import { useState } from "react";
import type { WritingSidebarItem } from "@/types/novel";
import WritingSidebar from "./WritingSidebar";
import WritingPlaceholder from "./WritingPlaceholder";
import NovelInfoWorkspace from "./novel-info/NovelInfoWorkspace";
import FactionCardsWorkspace from "./factions/FactionCardsWorkspace";
import CharacterCardsWorkspace from "./characters/CharacterCardsWorkspace";
import { CardWorkspace, ChapterEditorWorkspace } from "./StoryContinuityWorkspace";

interface WritingContentProps {
  mode: "create" | "edit";
  novelId?: string;
}

/**
 * 渲染小说创作页侧栏及当前选中的全书级写作工作台。
 *
 * Args:
 *   mode: 小说创建或编辑模式。
 *   novelId: 已保存小说的 ObjectId，创建模式下可为空。
 *
 * Returns:
 *   包含侧栏和当前模块内容区的 React 节点。
 */
export default function WritingContent({ mode, novelId }: WritingContentProps) {
  const [activeItem, setActiveItem] = useState<WritingSidebarItem>("novel-info");

  const renderMainArea = () => {
    if (activeItem === "novel-info") {
      return <NovelInfoWorkspace mode={mode} novelId={novelId} />;
    }
    if (activeItem === "faction-cards") {
      return <FactionCardsWorkspace mode={mode} novelId={novelId} />;
    }
    if (activeItem === "character-cards") {
      return <CharacterCardsWorkspace mode={mode} novelId={novelId} initialView="profiles" />;
    }
    if (activeItem === "relationship-map") {
      return <CharacterCardsWorkspace mode={mode} novelId={novelId} initialView="relations" />;
    }
    if (activeItem === "chapter-editor") return <ChapterEditorWorkspace novelId={novelId} />;
    if (activeItem === "location-cards") return <CardWorkspace type="location" novelId={novelId} />;
    if (activeItem === "item-cards") return <CardWorkspace type="item" novelId={novelId} />;
    if (activeItem === "rule-cards") return <CardWorkspace type="rule" novelId={novelId} />;
    return <WritingPlaceholder moduleKey={activeItem} />;
  };

  return (
    // 顶栏的下边框占据 1px，工作区同步扣除，避免页面产生无意义的整页滚动条。
    <div className="flex h-[calc(100vh-3.5rem-1px)] flex-col md:flex-row">
      <WritingSidebar activeItem={activeItem} onSelect={setActiveItem} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {renderMainArea()}
      </div>
    </div>
  );
}
