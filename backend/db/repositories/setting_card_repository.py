"""设定卡（地点/物品/规则）数据访问层，异步 MongoDB。"""
import re
import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from backend.db.base import BaseRepository
from backend.db.utils import to_object_id, get_utc_now
from backend.db.errors import NotFoundError, DuplicateKeyError
from backend.db.repositories.id_sequence_repository import id_sequence_repo

logger = logging.getLogger(__name__)

# 三类卡片的字段定义：key -> 中文标签（权威定义，前后端共用此结构）
CARD_TYPES: Dict[str, Dict[str, Any]] = {
    "location": {
        "label": "地点卡",
        "prefix": "loc",
        "state_options": ["开放", "封锁", "已毁", "迁移", "未知"],
        "fields": {
            "location_type": "地点类型",
            "region": "所属区域",
            "parent_location": "上级地点",
            "appearance": "环境外观",
            "atmosphere": "氛围",
            "geographic_features": "特殊地理特征",
            "purpose": "剧情用途",
            "access_conditions": "出入条件",
            "danger_factors": "危险因素",
            "related_characters": "关联人物",
            "related_factions": "关联势力",
            "related_items": "关联物品",
            "notes": "备注",
        },
    },
    "item": {
        "label": "物品卡",
        "prefix": "itm",
        "state_options": ["完好", "受损", "已损毁", "遗失", "封印中"],
        "fields": {
            "category": "物品类型",
            "appearance": "外观",
            "origin": "来源",
            "maker": "制造者",
            "owner": "当前持有者",
            "abilities": "能力和使用方式",
            "limitations": "使用限制",
            "cost": "代价",
            "cooldown": "冷却时间",
            "quantity": "数量",
            "durability": "耐久/损毁状态",
            "secret": "重要秘密或揭示阶段",
            "notes": "备注",
            # 模块3.5：物品卡战力关联字段
            "required_power_level": "使用所需境界",
            "power_bonus": "战力增幅",
            "power_cost": "战力代价",
        },
    },
    "rule": {
        "label": "设定规则卡",
        "prefix": "rul",
        "state_options": ["生效中", "已失效", "待揭示"],
        "fields": {
            "rule_category": "规则分类",
            "scope": "适用范围",
            "definition": "规则定义",
            "trigger": "触发条件",
            "constraints": "约束和代价",
            "exceptions": "例外条件",
            "consequences": "违反后果",
            "priority": "规则优先级",
            "is_hard_rule": "是否硬规则(必须遵守)",
            "notes": "备注",
        },
    },
}


# 规则卡 is_hard_rule 的历史文本取值，服务层统一转换成布尔语义
_TRUE_TEXT_VALUES = frozenset({"1", "true", "yes", "y", "on", "是", "硬规则", "必须"})


def coerce_hard_rule_flag(value: Any) -> bool:
    """把规则卡 is_hard_rule 的任意历史取值转换成布尔语义。

    Args:
        value: 历史文本（如 "是"/"否"/"true"）或布尔值。

    Returns:
        命中硬规则取值时返回 True，其余返回 False。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in _TRUE_TEXT_VALUES


def serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把文档中的 ObjectId 全部转成字符串，便于 JSON 序列化。"""
    if not doc:
        return doc
    for key in ("_id", "novel_id", "volume_id"):
        if key in doc and isinstance(doc[key], ObjectId):
            doc[key] = str(doc[key])
    return doc


class SettingCardRepository(BaseRepository):
    def __init__(self):
        super().__init__("setting_cards")

    async def _next_card_id(self, novel_id, card_type: str, session=None) -> str:
        prefix = CARD_TYPES[card_type]["prefix"]
        allocated = await id_sequence_repo.allocate_many(
            novel_id, f"setting_card_{card_type}", prefix, 1, session=session
        )
        return allocated[0]

    async def list_cards(
        self,
        novel_id: str,
        card_type: Optional[str] = None,
        query: str = "",
        include_deleted: bool = False,
        enabled_only: bool = False,
        session=None,
    ) -> List[Dict[str, Any]]:
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if card_type:
            flt["type"] = card_type
        if enabled_only:
            flt["enabled"] = True
        if query:
            rx = re.compile(re.escape(query), re.IGNORECASE)
            flt["$or"] = [{"name": rx}, {"aliases": rx}]
        docs = await self.find_many(
            flt, include_deleted=include_deleted,
            sort=[("sort_order", 1), ("created_at", 1)], session=session,
        )
        return [serialize(d) for d in docs]

    async def get_card_by_oid(self, novel_id, card_id, include_deleted=False, session=None):
        doc = await self.find_one(
            {"_id": ObjectId(card_id), "novel_id": to_object_id(novel_id)},
            include_deleted=include_deleted, session=session,
        )
        if not doc:
            raise NotFoundError(f"卡片不存在: {card_id}")
        return serialize(doc)

    async def get_card_by_business_id(
        self,
        novel_id,
        card_id: str,
        include_deleted: bool = False,
        session=None,
    ) -> Dict[str, Any]:
        """按业务 ID（loc_/itm_/rul_）读取卡片。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            card_id: 卡片业务 ID。
            include_deleted: 是否包含软删除卡片。
            session: 可选 MongoDB 会话。

        Returns:
            命中的卡片文档。

        Raises:
            NotFoundError: 卡片不存在时抛出。
        """
        doc = await self.find_one(
            {"novel_id": to_object_id(novel_id), "card_id": card_id},
            include_deleted=include_deleted,
            session=session,
        )
        if not doc:
            raise NotFoundError(f"卡片不存在: {card_id}")
        return serialize(doc)

    async def list_cards_by_ids(
        self,
        novel_id,
        card_ids: List[str],
        session=None,
    ) -> List[Dict[str, Any]]:
        """按业务 ID 列表批量读取卡片。"""
        cleaned = [item for item in dict.fromkeys(card_ids) if item]
        if not cleaned:
            return []
        docs = await self.find_many(
            {"novel_id": to_object_id(novel_id), "card_id": {"$in": cleaned}},
            session=session,
        )
        return [serialize(d) for d in docs]

    async def name_exists(self, novel_id, card_type, name, exclude_oid=None, session=None) -> bool:
        flt: Dict[str, Any] = {
            "novel_id": to_object_id(novel_id),
            "type": card_type,
            "name": name.strip(),
        }
        if exclude_oid:
            flt["_id"] = {"$ne": ObjectId(exclude_oid)}
        return await self.exists(flt, session=session)

    async def create_card(self, novel_id, data: Dict[str, Any], session=None) -> Dict[str, Any]:
        card_type = data["type"]
        card_id = await self._next_card_id(novel_id, card_type, session=session)
        doc = {
            "card_id": card_id,
            "novel_id": to_object_id(novel_id),
            "type": card_type,
            "name": data["name"].strip(),
            "aliases": data.get("aliases", []),
            "fields": data.get("fields", {}),
            "enabled": data.get("enabled", True),
            "importance": data.get("importance", 3),
            "first_appearance_chapter": data.get("first_appearance_chapter", ""),
            "current_state": data.get("current_state", ""),
            "state_history": [],
            "tags": data.get("tags", []),
            "sort_order": data.get("sort_order", 0),
            "version": 1,
        }
        # 规则卡额外保存布尔语义的硬规则标记，兼容历史文本值
        if card_type == "rule":
            doc["is_hard_rule"] = coerce_hard_rule_flag(
                data.get("is_hard_rule", (data.get("fields") or {}).get("is_hard_rule"))
            )
        oid = await self.insert_one(doc, session=session)
        return await self.get_card_by_oid(novel_id, oid, session=session)

    async def update_card(self, novel_id, card_id, data: Dict[str, Any], expected_version=None, session=None):
        existing = await self.get_card_by_oid(novel_id, card_id, session=session)
        # 乐观锁基准：显式 expected_version 优先，否则以刚读到的版本为基准（仍走原子条件更新）
        base_version = expected_version if expected_version is not None else existing.get("version", 1)
        # version 一律由 $inc 原子自增，不允许外部直接写
        set_fields: Dict[str, Any] = {k: v for k, v in data.items() if k != "version"}
        if "current_state" in set_fields and set_fields["current_state"] != existing.get("current_state"):
            history = list(existing.get("state_history", []))
            history.append({
                "from": existing.get("current_state", ""),
                "to": set_fields["current_state"],
                "note": set_fields.pop("_state_note", ""),
                "chapter": set_fields.pop("_state_chapter", ""),
            })
            set_fields["state_history"] = history
        # 原子条件更新：版本号匹配才写入并 +1，并发时后写不会覆盖先写
        flt = {
            "_id": ObjectId(card_id),
            "novel_id": to_object_id(novel_id),
            "is_deleted": False,
            "version": base_version,
        }
        update = {"$set": {**set_fields, "updated_at": get_utc_now()}, "$inc": {"version": 1}}
        result = await self.collection.update_one(flt, update, session=session)
        if result.matched_count == 0:
            latest = await self.collection.find_one({"_id": ObjectId(card_id)}, session=session)
            if not latest:
                raise NotFoundError(f"卡片不存在: {card_id}")
            raise DuplicateKeyError(
                f"卡片已被其他页面修改（服务器版本 {latest.get('version')}，当前基于版本 {base_version}），请刷新后重试"
            )
        return await self.get_card_by_oid(novel_id, card_id, session=session)

    async def update_card_by_business_id(
        self,
        novel_id,
        card_id: str,
        data: Dict[str, Any],
        expected_version: Optional[int] = None,
        session=None,
    ) -> Dict[str, Any]:
        """按业务 card_id 更新卡片，语义与 update_card 一致。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            card_id: 卡片业务 ID（loc_/itm_/rul_）。
            data: 待更新字段。
            expected_version: 客户端基于的卡片版本；为空时以当前读到的版本为基准。
            session: 可选 MongoDB 会话。

        Returns:
            更新后的卡片文档。

        Raises:
            NotFoundError: 卡片不存在。
            DuplicateKeyError: 版本冲突。
        """
        existing = await self.get_card_by_business_id(novel_id, card_id, session=session)
        return await self.update_card(
            novel_id,
            existing["_id"],
            data,
            expected_version=expected_version,
            session=session,
        )

    async def hard_delete_card_by_business_id(
        self,
        novel_id,
        card_id: str,
        session=None,
    ) -> bool:
        """按业务 card_id 物理删除卡片。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            card_id: 卡片业务 ID（loc_/itm_/rul_）。
            session: 可选 MongoDB 会话。

        Returns:
            实际删除成功时返回 True。

        Raises:
            NotFoundError: 卡片不存在。
        """
        existing = await self.get_card_by_business_id(
            novel_id, card_id, include_deleted=True, session=session
        )
        return await self.hard_delete_card(novel_id, existing["_id"], session=session)

    async def soft_delete_card(self, novel_id, card_id, session=None) -> bool:
        await self.get_card_by_oid(novel_id, card_id, session=session)
        return await self.soft_delete_one(
            {"_id": ObjectId(card_id), "novel_id": to_object_id(novel_id)}, session=session
        )

    async def restore_card(self, novel_id, card_id, session=None):
        ok = await self.restore_one(
            {"_id": ObjectId(card_id), "novel_id": to_object_id(novel_id)}, session=session
        )
        if not ok:
            raise NotFoundError(f"待恢复卡片不存在: {card_id}")
        return await self.get_card_by_oid(novel_id, card_id, include_deleted=True, session=session)

    async def hard_delete_card(self, novel_id, card_id, session=None) -> bool:
        await self.get_card_by_oid(novel_id, card_id, include_deleted=True, session=session)
        return await self.hard_delete_one(
            {"_id": ObjectId(card_id), "novel_id": to_object_id(novel_id)}, session=session
        )

    async def count_by_type(self, novel_id, card_type=None, include_deleted=False) -> int:
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if card_type:
            flt["type"] = card_type
        return await self.count_documents(flt, include_deleted=include_deleted)
