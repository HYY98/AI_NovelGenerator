"""章节 AI 上下文组装器。

按技术指引第 9 节组装章节生成所需的上下文：小说基础信息、章节目标、
上一章与最近章节摘要、当前章节关联的角色与设定卡、必须遵守的硬规则、
未解决伏笔，并产出可追溯的上下文版本快照。

核心约束：生成只能看到"当前章节可见"的设定，不能把未来章节才会发生的事实当作已知。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.db.repositories.chapter_repository import ChapterRepository
from backend.db.repositories.novel_blueprint_repository import novel_blueprint_repo
from backend.db.repositories.power_system_repository import power_system_repo
from backend.db.repositories.setting_card_repository import (
    CARD_TYPES,
    SettingCardRepository,
    coerce_hard_rule_flag,
)
from backend.services.novel.character_service import CharacterService

logger = logging.getLogger(__name__)

# 最近章节摘要保留数量（含上一章）
RECENT_CHAPTER_LIMIT = 3
# 未解决伏笔最多注入条数
MAX_THREADS = 20
# 核心卡片（importance >= 该值）自动注入
CORE_CARD_IMPORTANCE = 4
# 单字段注入提示词的最大长度，避免超长设定挤爆上下文
FIELD_TEXT_LIMIT = 400
# 续写时携带的前文尾部字数
CONTINUE_TAIL_CHARS = 1500
# 战力体系与创作蓝图注入提示词的最大长度，避免超长设定挤爆上下文
POWER_SYSTEM_TEXT_LIMIT = 900
BLUEPRINT_TEXT_LIMIT = 600

# 三类卡在提示词中的分区标题与重点字段
CARD_PROMPT_SECTIONS: Dict[str, Dict[str, Any]] = {
    "location": {
        "title": "当前章节地点",
        "fields": [
            ("region", "所属区域"),
            ("location_type", "地点类型"),
            ("atmosphere", "氛围"),
            ("access_conditions", "出入条件"),
            ("danger_factors", "危险因素"),
            ("purpose", "剧情用途"),
        ],
    },
    "item": {
        "title": "当前章节物品",
        "fields": [
            ("category", "物品类型"),
            ("origin", "来源"),
            ("abilities", "能力"),
            ("limitations", "使用限制"),
            ("cost", "代价"),
            ("owner", "当前持有者"),
        ],
    },
    "rule": {
        "title": "必须遵守的硬规则",
        "fields": [
            ("rule_category", "规则分类"),
            ("scope", "适用范围"),
            ("definition", "规则定义"),
            ("trigger", "触发条件"),
            ("constraints", "约束和代价"),
            ("exceptions", "例外条件"),
            ("consequences", "违反后果"),
        ],
    },
}


@dataclass
class ChapterContext:
    """一次章节 AI 调用使用的完整上下文。"""

    novel: Dict[str, Any]
    chapter: Dict[str, Any]
    linked_characters: List[Dict[str, Any]] = field(default_factory=list)
    linked_cards: List[Dict[str, Any]] = field(default_factory=list)
    core_cards: List[Dict[str, Any]] = field(default_factory=list)
    hard_rules: List[Dict[str, Any]] = field(default_factory=list)
    previous_summary: str = ""
    recent_summaries: List[Dict[str, Any]] = field(default_factory=list)
    unresolved_threads: List[str] = field(default_factory=list)
    # 增量新增：已确认的战力体系与创作蓝图，只有确认版本才会注入
    power_system: Optional[Dict[str, Any]] = None
    blueprint: Optional[Dict[str, Any]] = None
    snapshot: Dict[str, Any] = field(default_factory=dict)
    text: str = ""
    # 当前小说真实存在的业务 ID，用于校验 AI 返回的 used_entities / 状态变更候选
    known_character_ids: set = field(default_factory=set)
    known_card_ids: set = field(default_factory=set)

    @property
    def chapter_id(self) -> str:
        """当前章节业务 ID。"""
        return str(self.chapter.get("chapter_id", ""))

    @property
    def content_tail(self) -> str:
        """当前章节正文尾部，用于「继续写」而不是整篇重发。"""
        content = str(self.chapter.get("content") or "")
        return content[-CONTINUE_TAIL_CHARS:]


def _safe_int(value: Any, default: int = 0) -> int:
    """把任意值安全转换为整数。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clip(value: Any, limit: int = FIELD_TEXT_LIMIT) -> str:
    """把任意值转成截断后的单行文本。"""
    text = str(value or "").strip()
    return text[:limit]


def _safe_novel_text(novel: Dict[str, Any], key: str, fallback: str = "未提供") -> str:
    """读取小说字段并转成适合提示词的文本。"""
    value = novel.get(key)
    if isinstance(value, list):
        joined = "、".join(str(item).strip() for item in value if str(item).strip())
        return joined or fallback
    text = str(value or "").strip()
    return text or fallback


def _iter_card_prompt_lines(card: Dict[str, Any]) -> List[str]:
    """把单张卡片渲染成提示词中的多行文本。"""
    card_type = str(card.get("type") or "")
    section = CARD_PROMPT_SECTIONS.get(card_type, {})
    fields_map = card.get("fields") or {}
    lines = [f"名称：{_clip(card.get('name'), 80)}"]
    aliases = card.get("aliases") or []
    if aliases:
        lines.append(f"别名：{'、'.join(str(a) for a in aliases if str(a).strip())}")
    if card.get("current_state"):
        lines.append(f"当前状态：{_clip(card.get('current_state'), 60)}")
    for key, label in section.get("fields", []):
        value = fields_map.get(key)
        if value:
            lines.append(f"{label}：{_clip(value)}")
    if card_type == "rule":
        lines.append(
            "硬规则：{}".format("是" if coerce_hard_rule_flag(card.get("is_hard_rule")) else "否")
        )
    return lines


def _render_card_block(title: str, cards: List[Dict[str, Any]]) -> str:
    """渲染一类卡片的分区文本。"""
    if not cards:
        return ""
    blocks: List[str] = []
    for card in cards:
        blocks.append("\n".join(_iter_card_prompt_lines(card)))
    return f"【{title}】\n" + "\n\n".join(blocks)


def _render_character_block(characters: List[Dict[str, Any]]) -> str:
    """渲染关联角色分区文本。"""
    if not characters:
        return ""
    blocks: List[str] = []
    for character in characters:
        lines = [
            f"姓名：{_clip(character.get('name'), 80)}",
            f"身份：{_clip(character.get('identity'), 200)}",
            f"性格：{_clip(character.get('personality'), 200)}",
        ]
        if character.get("core_desire"):
            lines.append(f"核心欲望：{_clip(character.get('core_desire'), 200)}")
        if character.get("initial_state"):
            lines.append(f"初始状态：{_clip(character.get('initial_state'), 200)}")
        if character.get("abilities"):
            lines.append(f"能力：{_clip(character.get('abilities'), 200)}")
        blocks.append("\n".join(lines))
    return "【本章关联角色】\n" + "\n\n".join(blocks)


def _render_summary_block(context: "ChapterContext") -> str:
    """渲染最近章节摘要与未解决伏笔分区。"""
    parts: List[str] = []
    if context.previous_summary:
        parts.append(f"【上一章摘要】\n{_clip(context.previous_summary, 1200)}")
    if context.recent_summaries:
        lines = [
            f"第{item.get('number')}章 {item.get('title') or ''}：{_clip(item.get('summary'), 300)}"
            for item in context.recent_summaries
        ]
        parts.append("【最近章节摘要】\n" + "\n".join(lines))
    if context.unresolved_threads:
        parts.append(
            "【尚未回收的伏笔】\n"
            + "\n".join(f"- {thread}" for thread in context.unresolved_threads)
        )
    return "\n\n".join(parts)


def _render_power_system_block(power_system: Dict[str, Any]) -> str:
    """渲染结构化战力体系分区文本。"""
    lines = [f"体系名称：{_clip(power_system.get('name'), 80)}"]
    if power_system.get("description"):
        lines.append(f"体系说明：{_clip(power_system.get('description'), 240)}")
    levels = power_system.get("levels") or []
    if isinstance(levels, list) and levels:
        ordered = sorted(
            (item for item in levels if isinstance(item, dict)),
            key=lambda item: _safe_int(item.get("order")),
        )
        lines.append("境界/等级（从低到高）：")
        for item in ordered[:20]:
            name = _clip(item.get("name"), 40) or "未命名"
            detail = _clip(item.get("description"), 120)
            lines.append(f"- {name}：{detail}" if detail else f"- {name}")
    dimensions = power_system.get("power_dimensions") or []
    if isinstance(dimensions, list) and dimensions:
        lines.append("战力维度：" + "、".join(str(item) for item in dimensions[:10]))
    resource = power_system.get("resource")
    if isinstance(resource, dict) and resource:
        parts = [
            f"{key}：{_clip(value, 80)}"
            for key, value in resource.items()
            if str(value or "").strip()
        ]
        if parts:
            lines.append("能量/资源：" + "；".join(parts[:6]))
    restrictions = power_system.get("restrictions") or []
    if isinstance(restrictions, list) and restrictions:
        lines.append(
            "限制条件：" + "、".join(_clip(item, 80) for item in restrictions[:8])
        )
    counters = power_system.get("counters") or []
    if isinstance(counters, list) and counters:
        lines.append(
            "克制关系：" + _clip(json.dumps(counters, ensure_ascii=False, default=str), 200)
        )
    return _clip("【战力体系（已确认）】\n" + "\n".join(lines), POWER_SYSTEM_TEXT_LIMIT)


def _render_blueprint_block(blueprint: Dict[str, Any]) -> str:
    """渲染已确认创作蓝图分区文本。"""
    parts: List[str] = []
    if str(blueprint.get("plot_summary") or "").strip():
        parts.append(f"大致剧情：{_clip(blueprint.get('plot_summary'), 300)}")
    if str(blueprint.get("worldview") or "").strip():
        parts.append(f"世界观：{_clip(blueprint.get('worldview'), 300)}")
    if not parts:
        return ""
    return _clip("【创作蓝图（已确认）】\n" + "\n".join(parts), BLUEPRINT_TEXT_LIMIT)


def _compute_context_hash(text: str) -> str:
    """计算上下文文本的稳定摘要，便于追踪生成依据。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ChapterContextService:
    """章节 AI 上下文组装服务。"""

    def __init__(self) -> None:
        self.chapter_repo = ChapterRepository()
        self.card_repo = SettingCardRepository()

    async def _load_characters(self, novel_id: str, limit: int = 0) -> Dict[str, Dict[str, Any]]:
        """读取小说角色并建立业务 ID 索引。"""
        try:
            characters = await CharacterService.get_characters_by_novel(novel_id)
        except Exception:
            logger.warning("加载角色失败，章节 AI 上下文将缺少角色信息", exc_info=True)
            return {}
        lookup: Dict[str, Dict[str, Any]] = {}
        for character in characters or []:
            character_id = str(character.get("character_id") or "")
            if character_id:
                lookup[character_id] = character
        if limit and len(lookup) > limit:
            return dict(list(lookup.items())[:limit])
        return lookup

    async def _load_cards(self, novel_id: str) -> Dict[str, Dict[str, Any]]:
        """读取小说全部未删除卡片并建立业务 ID 索引。"""
        try:
            cards = await self.card_repo.list_cards(novel_id)
        except Exception:
            logger.warning("加载设定卡失败，章节 AI 上下文将缺少卡片信息", exc_info=True)
            return {}
        lookup: Dict[str, Dict[str, Any]] = {}
        for card in cards or []:
            card_id = str(card.get("card_id") or "")
            if card_id:
                lookup[card_id] = card
        return lookup

    async def _load_power_system(self, novel_id: str) -> Optional[Dict[str, Any]]:
        """读取已确认战力体系，未确认或加载失败时返回 None。"""
        try:
            return await power_system_repo.find_confirmed(novel_id)
        except Exception:
            logger.warning(
                "加载战力体系失败，章节 AI 上下文将缺少战力体系", exc_info=True
            )
            return None

    async def _load_blueprint(self, novel_id: str) -> Optional[Dict[str, Any]]:
        """读取已确认创作蓝图，未确认或加载失败时返回 None。"""
        try:
            return await novel_blueprint_repo.find_confirmed(novel_id)
        except Exception:
            logger.warning(
                "加载创作蓝图失败，章节 AI 上下文将缺少蓝图摘要", exc_info=True
            )
            return None

    async def build(
        self,
        novel,
        chapter: Dict[str, Any],
        *,
        user_prompt: str = "",
        include_core_cards: bool = True,
    ) -> ChapterContext:
        """组装一次章节 AI 调用的上下文。

        Args:
            novel: 已落库的小说文档。
            chapter: 当前章节文档（需含 content），章节业务ID 用于关联查询。
            user_prompt: 用户本次的额外要求。
            include_core_cards: 是否自动注入全书核心卡片。

        Returns:
            含提示词文本与版本快照的 ChapterContext。
        """
        novel_id = str(novel.get("_id") or novel.get("novel_id") or "")
        characters = await self._load_characters(novel_id)
        cards = await self._load_cards(novel_id)

        linked_characters = [
            characters[cid]
            for cid in dict.fromkeys(chapter.get("linked_character_ids") or [])
            if cid in characters
        ]

        linked_ids: List[str] = []
        for key in ("linked_location_ids", "linked_item_ids", "linked_rule_ids"):
            for card_id in chapter.get(key) or []:
                if card_id not in linked_ids:
                    linked_ids.append(card_id)
        linked_cards = [cards[cid] for cid in linked_ids if cid in cards]

        hard_rules = [
            card
            for card in cards.values()
            if card.get("type") == "rule"
            and card.get("enabled", True)
            and coerce_hard_rule_flag(card.get("is_hard_rule"))
        ]
        # 关联卡里的硬规则已经在 linked_cards 中，避免重复注入
        linked_card_ids = {str(card.get("card_id")) for card in linked_cards}

        core_cards: List[Dict[str, Any]] = []
        if include_core_cards:
            core_cards = [
                card
                for card in cards.values()
                if _safe_int(card.get("importance"), 3) >= CORE_CARD_IMPORTANCE
                and card.get("enabled", True)
                and str(card.get("card_id")) not in linked_card_ids
            ]

        previous_summary, recent_summaries, unresolved_threads = await self._load_chapter_history(
            novel_id, chapter
        )
        # 增量新增：只注入已确认的战力体系与创作蓝图，未确认内容不进入上下文
        power_system = await self._load_power_system(novel_id)
        blueprint = await self._load_blueprint(novel_id)

        context = ChapterContext(
            novel=novel,
            chapter=chapter,
            linked_characters=linked_characters,
            linked_cards=linked_cards,
            core_cards=core_cards,
            hard_rules=hard_rules,
            power_system=power_system,
            blueprint=blueprint,
            previous_summary=previous_summary,
            recent_summaries=recent_summaries,
            unresolved_threads=unresolved_threads,
            known_character_ids=set(characters.keys()),
            known_card_ids=set(cards.keys()),
        )
        context.text = self._render(context, user_prompt)
        context.snapshot = self._build_snapshot(context, characters, cards)
        return context

    async def _load_chapter_history(
        self,
        novel_id: str,
        chapter: Dict[str, Any],
    ) -> tuple[str, List[Dict[str, Any]], List[str]]:
        """读取上一章摘要、最近章节摘要与未解决伏笔。

        Args:
            novel_id: 小说 ObjectId 字符串。
            chapter: 当前章节文档。

        Returns:
            上一章摘要、最近章节摘要列表、未解决伏笔列表。
        """
        try:
            chapters = await self.chapter_repo.list_chapters(novel_id)
        except Exception:
            logger.warning("加载章节历史失败，章节 AI 上下文将缺少前情", exc_info=True)
            return "", [], []

        current_number = _safe_int(chapter.get("number"))
        earlier = [c for c in chapters if _safe_int(c.get("number")) < current_number]
        earlier.sort(key=lambda c: _safe_int(c.get("number")), reverse=True)

        previous_summary = ""
        recent: List[Dict[str, Any]] = []
        for item in earlier[:RECENT_CHAPTER_LIMIT]:
            summary = str(item.get("summary") or "").strip()
            if summary:
                recent.append(
                    {
                        "chapter_id": item.get("chapter_id"),
                        "version": item.get("version", 1),
                        "number": item.get("number"),
                        "title": item.get("title"),
                        "summary": summary,
                    }
                )
        if recent:
            previous_summary = str(recent[0].get("summary") or "")

        threads: List[str] = []
        # 当前章节自己声明的伏笔也要参与，否则重写时会漏掉正在追踪的线索
        for item in [chapter] + earlier:
            for thread in item.get("unresolved_threads") or []:
                text = str(thread).strip()
                if text and text not in threads:
                    threads.append(text)
                if len(threads) >= MAX_THREADS:
                    break
            if len(threads) >= MAX_THREADS:
                break
        return previous_summary, recent, threads

    def _render(self, context: ChapterContext, user_prompt: str) -> str:
        """把上下文渲染成提示词正文。"""
        novel = context.novel
        chapter = context.chapter
        blocks: List[str] = [
            "【小说基础信息】",
            f"标题：{_safe_novel_text(novel, 'title')}",
            f"类型：{_safe_novel_text(novel, 'genre', '未分类')}",
            f"基调：{_safe_novel_text(novel, 'tone')}",
            f"叙事视角：{_safe_novel_text(novel, 'narrative_pov')}",
            f"写作风格：{_safe_novel_text(novel, 'writing_style')}",
            f"时代背景：{_safe_novel_text(novel, 'era_background')}",
            f"核心创意：{_safe_novel_text(novel, 'core_idea')}",
            f"世界观：{_safe_novel_text(novel, 'worldview')}",
            f"主线剧情：{_safe_novel_text(novel, 'plot')}",
            "",
            "【本章信息】",
            f"章节序号：第{chapter.get('number')}章",
            f"章节标题：{chapter.get('title') or '未命名'}",
            f"本章摘要：{_clip(chapter.get('summary'), 600) or '未填写'}",
            f"本章蓝图：{_clip(json.dumps(chapter.get('blueprint_snapshot') or {}, ensure_ascii=False), 800) or '未填写'}",
        ]
        if context.power_system:
            blocks.extend(["", _render_power_system_block(context.power_system)])
        if context.blueprint:
            blueprint_block = _render_blueprint_block(context.blueprint)
            if blueprint_block:
                blocks.extend(["", blueprint_block])
        for card_type, section in CARD_PROMPT_SECTIONS.items():
            cards = [c for c in context.linked_cards if c.get("type") == card_type]
            if card_type == "rule":
                # 关联规则与全书硬规则合并展示，避免同一张卡出现两次
                known = {str(c.get("card_id")) for c in cards}
                cards = cards + [
                    c for c in context.hard_rules if str(c.get("card_id")) not in known
                ]
                title = section["title"]
            else:
                title = section["title"]
            block = _render_card_block(title, cards)
            if block:
                blocks.extend(["", block])
        if context.core_cards:
            blocks.extend(["", _render_card_block("全书核心设定（仅供参考，不得与上文冲突）", context.core_cards)])
        character_block = _render_character_block(context.linked_characters)
        if character_block:
            blocks.extend(["", character_block])
        summary_block = _render_summary_block(context)
        if summary_block:
            blocks.extend(["", summary_block])
        if user_prompt.strip():
            blocks.extend(["", "【本次用户额外要求】", user_prompt.strip()])
        return "\n".join(blocks).strip()

    def _build_snapshot(
        self,
        context: ChapterContext,
        characters: Dict[str, Dict[str, Any]],
        cards: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """生成上下文版本快照，便于解释旧章节依据了哪些版本。"""
        used_card_ids = {
            str(card.get("card_id"))
            for card in context.linked_cards + context.core_cards + context.hard_rules
        }
        used_character_ids = {
            str(item.get("character_id")) for item in context.linked_characters
        }
        snapshot: Dict[str, Any] = {
            "novel_version": context.novel.get("version", 1),
            "chapter_version": context.chapter.get("version", 1),
            "characters": {
                cid: characters[cid].get("version", 1)
                for cid in sorted(used_character_ids)
                if cid in characters
            },
            "cards": {
                card_id: cards[card_id].get("version", 1)
                for card_id in sorted(used_card_ids)
                if card_id in cards
            },
            "previous_chapters": {
                str(item.get("chapter_id")): item.get("version", 1)
                for item in context.recent_summaries
                if item.get("chapter_id")
            },
            "context_hash": _compute_context_hash(context.text),
        }
        if context.power_system:
            snapshot["power_system"] = {
                "power_system_id": str(
                    context.power_system.get("power_system_id") or ""
                ),
                "version": context.power_system.get("version", 1),
            }
        if context.blueprint:
            snapshot["blueprint"] = {
                "blueprint_id": str(context.blueprint.get("blueprint_id") or ""),
                "version": context.blueprint.get("version", 1),
            }
        return snapshot


chapter_context_service = ChapterContextService()
