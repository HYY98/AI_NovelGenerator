"use client";

import { useState } from "react";
import { Button } from "@heroui/react";

export type BlueprintCardsStepMode = "entities" | "cards";

interface BlueprintCardsStepProps {
  mode: BlueprintCardsStepMode;
  characters: { character_id: string; name: string; core_position?: string }[];
  factions: { faction_id: string; name: string }[];
  settingCards: {
    card_id: string;
    type: string;
    name: string;
    current_state?: string;
    is_hard_rule?: boolean;
  }[];
  selectedCharacterIds: string[];
  selectedFactionIds: string[];
  selectedCardIds: string[];
  onChangeSelection: (patch: {
    characterIds?: string[];
    factionIds?: string[];
    cardIds?: string[];
  }) => void;
  readOnly?: boolean;
  /** 打开对应工作台新建实体的回调；新建后由父组件刷新列表。 */
  onOpenCreation?: (kind: "character" | "faction" | "location" | "item" | "rule") => void;
  onRefresh?: () => void;
}

const CARD_TYPE_LABEL: Record<string, string> = {
  character: "角色",
  location: "地点",
  faction: "势力",
  item: "物品",
  rule: "规则",
};

/**
 * 功能：创作蓝图向导的实体与设定卡选择步骤。
 *
 * mode=entities 时选择角色与势力，mode=cards 时选择地点 / 物品 / 规则卡。
 * 只维护「纳入蓝图范围」的 ID 集合，不复制卡片内容。
 */
export default function BlueprintCardsStep({
  mode,
  characters,
  factions,
  settingCards,
  selectedCharacterIds,
  selectedFactionIds,
  selectedCardIds,
  onChangeSelection,
  readOnly = false,
  onOpenCreation,
  onRefresh,
}: BlueprintCardsStepProps) {
  const [keyword, setKeyword] = useState("");

  const normalizedKeyword = keyword.trim().toLowerCase();
  const match = (name: string) =>
    !normalizedKeyword || name.toLowerCase().includes(normalizedKeyword);

  const filteredCharacters = characters.filter((item) => match(item.name));
  const filteredFactions = factions.filter((item) => match(item.name));
  const filteredCards = settingCards.filter(
    (card) =>
      (mode === "cards" ? card.type !== "character" && card.type !== "faction" : true) &&
      match(card.name)
  );

  const toggle = (list: string[], id: string) =>
    list.includes(id) ? list.filter((item) => item !== id) : [...list, id];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="min-w-0 flex-1 rounded-lg border border-border px-3 py-2 text-sm"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          placeholder="搜索名称"
        />
        {onRefresh && (
          <Button variant="ghost" size="sm" onPress={onRefresh}>
            刷新列表
          </Button>
        )}
      </div>

      {mode === "entities" ? (
        <>
          <SelectionGroup
            title={`角色（已选 ${selectedCharacterIds.length}）`}
            emptyText="还没有角色"
            onCreate={onOpenCreation ? () => onOpenCreation("character") : undefined}
            createText="新建角色"
            readOnly={readOnly}
          >
            {filteredCharacters.map((character) => (
              <SelectionRow
                key={character.character_id}
                checked={selectedCharacterIds.includes(character.character_id)}
                disabled={readOnly}
                label={character.name}
                description={character.core_position}
                onChange={() =>
                  onChangeSelection({
                    characterIds: toggle(selectedCharacterIds, character.character_id),
                  })
                }
              />
            ))}
          </SelectionGroup>

          <SelectionGroup
            title={`势力（已选 ${selectedFactionIds.length}）`}
            emptyText="还没有势力"
            onCreate={onOpenCreation ? () => onOpenCreation("faction") : undefined}
            createText="新建势力"
            readOnly={readOnly}
          >
            {filteredFactions.map((faction) => (
              <SelectionRow
                key={faction.faction_id}
                checked={selectedFactionIds.includes(faction.faction_id)}
                disabled={readOnly}
                label={faction.name}
                onChange={() =>
                  onChangeSelection({ factionIds: toggle(selectedFactionIds, faction.faction_id) })
                }
              />
            ))}
          </SelectionGroup>
        </>
      ) : (
        <SelectionGroup
          title={`地点 / 物品 / 规则卡（已选 ${selectedCardIds.length}）`}
          emptyText="还没有地点、物品或规则卡"
          onCreate={onOpenCreation ? () => onOpenCreation("location") : undefined}
          createText="新建设定卡"
          readOnly={readOnly}
        >
          {filteredCards.map((card) => (
            <SelectionRow
              key={card.card_id}
              checked={selectedCardIds.includes(card.card_id)}
              disabled={readOnly}
              label={card.name}
              badge={CARD_TYPE_LABEL[card.type] ?? card.type}
              hardRule={card.is_hard_rule === true}
              description={card.current_state}
              onChange={() =>
                onChangeSelection({ cardIds: toggle(selectedCardIds, card.card_id) })
              }
            />
          ))}
        </SelectionGroup>
      )}
    </div>
  );
}

function SelectionGroup({
  title,
  emptyText,
  children,
  onCreate,
  createText,
  readOnly,
}: {
  title: string;
  emptyText: string;
  children: React.ReactNode;
  onCreate?: () => void;
  createText?: string;
  readOnly?: boolean;
}) {
  const isEmpty = !children || (Array.isArray(children) && children.length === 0);
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{title}</span>
        {onCreate && createText && (
          <Button variant="ghost" size="sm" onPress={onCreate} isDisabled={readOnly}>
            {createText}
          </Button>
        )}
      </div>
      {isEmpty ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted">
          {emptyText}
        </p>
      ) : (
        <div className="max-h-64 space-y-1 overflow-y-auto">{children}</div>
      )}
    </div>
  );
}

function SelectionRow({
  checked,
  disabled,
  label,
  description,
  badge,
  hardRule,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  label: string;
  description?: string;
  badge?: string;
  hardRule?: boolean;
  onChange: () => void;
}) {
  return (
    <label
      className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
        checked ? "border-accent bg-accent/5" : "border-border"
      }`}
    >
      <input
        type="checkbox"
        className="mt-0.5"
        checked={checked}
        disabled={disabled}
        onChange={onChange}
      />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-1.5">
          <span className="font-medium text-foreground">{label}</span>
          {badge && (
            <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-xs text-muted">
              {badge}
            </span>
          )}
          {hardRule && (
            <span className="rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600">
              硬规则
            </span>
          )}
        </span>
        {description && <span className="block text-xs text-muted">{description}</span>}
      </span>
    </label>
  );
}
