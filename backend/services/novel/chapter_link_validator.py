"""章节关联实体的校验与归一化（技术指引 7.1 / 18.1）。

章节只保存业务 ID，服务端在写入前必须确认：
1. 业务 ID 属于当前小说；
2. 卡片类型与关联字段一致；
3. 软删除实体不能作为新的章节关联；
4. 自动去重、拒绝空字符串与 null。

历史遗留的关联（原本就存在于章节上、但卡片后来被软删除）会被保留，
避免用户只是改个标题就被强制清空既有关系。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.db.errors import InvalidIdError
from backend.db.repositories.setting_card_repository import CARD_TYPES, SettingCardRepository

logger = logging.getLogger(__name__)

# 关联字段 -> 卡片类型（角色走 characters 集合，单独处理）
CARD_LINK_FIELDS: Dict[str, str] = {
    "linked_location_ids": "location",
    "linked_item_ids": "item",
    "linked_rule_ids": "rule",
}

CARD_LINK_LABELS: Dict[str, str] = {
    "linked_location_ids": "关联地点",
    "linked_item_ids": "关联物品",
    "linked_rule_ids": "关联规则",
}

CHARACTER_LINK_FIELD = "linked_character_ids"

# 全部关联字段，供调用方判断是否需要做校验
ALL_LINK_FIELDS = (*CARD_LINK_FIELDS, CHARACTER_LINK_FIELD)


def touches_link_fields(data: Dict[str, Any]) -> bool:
    """判断请求是否触及关联字段。

    Args:
        data: 待写入的章节字段。

    Returns:
        命中任意关联字段时返回 True。自动保存的绝大多数请求都不会命中，
        从而避免每次保存都多查一次章节。
    """
    return any(field in data for field in ALL_LINK_FIELDS)


def _clean_ids(values: Any) -> List[str]:
    """清理业务 ID 数组：去空、去重、保持顺序。"""
    if values is None:
        return []
    if not isinstance(values, list):
        raise InvalidIdError("关联字段必须是数组")
    cleaned: List[str] = []
    for item in values:
        if item is None:
            continue
        text = str(item).strip()
        if not text:
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


async def validate_and_normalize_links(
    novel_id: str,
    data: Dict[str, Any],
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """校验并归一化章节请求中的关联字段。

    Args:
        novel_id: 小说 ObjectId 字符串。
        data: 待写入的章节字段（只处理出现过的关联键）。
        existing: 当前章节文档，用于放行历史遗留关联。

    Returns:
        归一化后的字段字典（原地副本）。

    Raises:
        InvalidIdError: 业务 ID 不属于该小说，或卡片类型与字段不一致时抛出。
    """
    touched = [key for key in (*CARD_LINK_FIELDS, CHARACTER_LINK_FIELD) if key in data]
    if not touched:
        return data

    normalized = dict(data)

    if CHARACTER_LINK_FIELD in touched:
        normalized[CHARACTER_LINK_FIELD] = await _validate_characters(
            novel_id, _clean_ids(data[CHARACTER_LINK_FIELD]), existing
        )

    card_fields = [key for key in CARD_LINK_FIELDS if key in touched]
    if card_fields:
        card_repo = SettingCardRepository()
        # include_deleted=True 一次取全，再按 is_deleted 分类判断
        cards = await card_repo.list_cards(novel_id, include_deleted=True)
        active: Dict[str, str] = {}
        deleted: set = set()
        for card in cards:
            card_id = str(card.get("card_id") or "")
            if not card_id:
                continue
            if card.get("is_deleted"):
                deleted.add(card_id)
            else:
                active[card_id] = str(card.get("type") or "")
        for field in card_fields:
            normalized[field] = _validate_cards(
                _clean_ids(data[field]), CARD_LINK_FIELDS[field], active, deleted, existing, field
            )

    return normalized


async def _validate_characters(
    novel_id: str,
    ids: List[str],
    existing: Optional[Dict[str, Any]],
) -> List[str]:
    """校验角色关联业务 ID。"""
    if not ids:
        return []

    from backend.services.novel.character_service import CharacterService

    active = {
        str(item.get("character_id"))
        for item in await CharacterService.get_characters_by_novel(novel_id)
        if item.get("character_id")
    }
    deleted = {
        str(item.get("character_id"))
        for item in await CharacterService.get_deleted_characters_by_novel(novel_id)
        if item.get("character_id")
    }
    keep = set((existing or {}).get(CHARACTER_LINK_FIELD) or [])

    result: List[str] = []
    for character_id in ids:
        if character_id in active:
            result.append(character_id)
            continue
        if character_id in deleted and character_id in keep:
            # 章节原本就关联了这个已被软删除的角色，保留历史关联
            result.append(character_id)
            continue
        if character_id in deleted:
            raise InvalidIdError(f"角色已删除，不能作为新的章节关联: {character_id}")
        raise InvalidIdError(f"角色不存在于当前小说: {character_id}")
    return result


def _validate_cards(
    ids: List[str],
    expected_type: str,
    active: Dict[str, str],
    deleted: set,
    existing: Optional[Dict[str, Any]],
    field: str,
) -> List[str]:
    """校验三类卡片关联业务 ID 与类型一致性。"""
    if not ids:
        return []
    label = CARD_LINK_LABELS[field]
    keep = set((existing or {}).get(field) or [])
    result: List[str] = []
    for card_id in ids:
        if card_id in active:
            actual_type = active[card_id]
            if actual_type != expected_type:
                raise InvalidIdError(
                    f"{label}只能关联{CARD_TYPES[expected_type]['label']}，"
                    f"但 {card_id} 是{CARD_TYPES.get(actual_type, {}).get('label', actual_type)}"
                )
            result.append(card_id)
            continue
        if card_id in deleted and card_id in keep:
            result.append(card_id)
            continue
        if card_id in deleted:
            raise InvalidIdError(f"{label}中包含已删除的卡片: {card_id}")
        raise InvalidIdError(f"{label}中包含不存在的卡片: {card_id}")
    return result
