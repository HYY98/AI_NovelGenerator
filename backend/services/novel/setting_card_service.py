"""设定卡业务逻辑层：字段校验、归一化、乐观锁。"""
from typing import Any, Dict, List, Optional

from bson import ObjectId

from backend.db.utils import to_object_id
from backend.db.repositories.setting_card_repository import (
    CARD_TYPES,
    SettingCardRepository,
    coerce_hard_rule_flag,
)
from backend.db.errors import InvalidIdError, DuplicateKeyError

# 受保护字段：通过通用更新接口修改时必须显式确认
PROTECTED_UPDATE_FIELDS = ("name", "current_state")
RULE_PROTECTED_UPDATE_FIELDS = ("is_hard_rule",)


class SettingCardService:
    def __init__(self):
        self.repo = SettingCardRepository()

    @staticmethod
    def _validate_type(card_type: str):
        if card_type not in CARD_TYPES:
            raise InvalidIdError(f"不支持的卡片类型: {card_type}，支持 location/item/rule")

    def _normalize_fields(self, card_type: str, fields: Dict[str, Any]) -> Dict[str, str]:
        valid = CARD_TYPES[card_type]["fields"]
        out = {}
        for key in valid:
            val = fields.get(key, "")
            if isinstance(val, list):
                val = "、".join(str(x) for x in val if x)
            out[key] = str(val or "").strip()
        return out

    @staticmethod
    def _normalize_str_list(value) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return [s.strip() for s in str(value).replace("，", ",").split(",") if s.strip()]

    async def list_cards(self, novel_id, card_type=None, query="", include_deleted=False, enabled_only=False):
        if card_type:
            self._validate_type(card_type)
        return await self.repo.list_cards(novel_id, card_type, query, include_deleted, enabled_only)

    async def _resolve_card_oid(self, novel_id, card_id, *, include_deleted: bool = False) -> str:
        """把卡片 ObjectId 或业务 card_id 统一解析为卡片 ObjectId 字符串。

        Args:
            novel_id: 小说 ObjectId 字符串。
            card_id: 卡片 ObjectId 字符串或业务 ID（loc_/itm_/rul_）。
            include_deleted: 按业务 ID 解析时是否允许命中回收站卡片。

        Returns:
            卡片 ObjectId 字符串。

        Raises:
            NotFoundError: 业务 ID 在小说下找不到对应卡片。
        """
        if ObjectId.is_valid(str(card_id)):
            return str(card_id)
        card = await self.repo.get_card_by_business_id(
            novel_id, str(card_id), include_deleted=include_deleted
        )
        return str(card["_id"])

    async def get_card(self, novel_id, card_id, include_deleted=False):
        oid = await self._resolve_card_oid(novel_id, card_id, include_deleted=include_deleted)
        return await self.repo.get_card_by_oid(novel_id, oid, include_deleted=include_deleted)

    async def create_card(self, novel_id, data: Dict[str, Any]):
        card_type = data.get("type")
        self._validate_type(card_type)
        name = (data.get("name") or "").strip()
        if not name:
            raise InvalidIdError("卡片名称不能为空")
        if await self.repo.name_exists(novel_id, card_type, name):
            raise DuplicateKeyError(f"{CARD_TYPES[card_type]['label']}名称已存在: {name}")
        data["name"] = name
        data["fields"] = self._normalize_fields(card_type, data.get("fields", {}))
        # 规则卡的 is_hard_rule 统一落成布尔语义，供章节生成强制注入与审校使用
        if card_type == "rule":
            data["is_hard_rule"] = coerce_hard_rule_flag(
                data["fields"].get("is_hard_rule")
            )
        data["aliases"] = self._normalize_str_list(data.get("aliases"))
        data["tags"] = self._normalize_str_list(data.get("tags"))
        data["importance"] = max(1, min(5, int(data.get("importance", 3) or 3)))
        return await self.repo.create_card(novel_id, data)

    @staticmethod
    def _protected_diff(existing: Dict[str, Any], data: Dict[str, Any]) -> List[str]:
        """找出本次更新中发生变化且属于受保护范围的字段。"""
        protected = list(PROTECTED_UPDATE_FIELDS)
        if str(existing.get("type") or "") == "rule":
            protected.extend(RULE_PROTECTED_UPDATE_FIELDS)
        changed: List[str] = []
        for key in protected:
            if key == "is_hard_rule":
                incoming_value = data.get("is_hard_rule")
                fields_payload = data.get("fields")
                if incoming_value is None and isinstance(fields_payload, dict):
                    incoming_value = fields_payload.get(key)
                if incoming_value is None:
                    continue
                current_flag = coerce_hard_rule_flag(
                    existing.get("is_hard_rule")
                    or (existing.get("fields") or {}).get(key)
                )
                if coerce_hard_rule_flag(incoming_value) != current_flag:
                    changed.append(key)
                continue
            if key not in data:
                continue
            if str(data.get(key) or "").strip() != str(existing.get(key) or "").strip():
                changed.append(key)
        return changed

    async def update_card(
        self,
        novel_id,
        card_id,
        data: Dict[str, Any],
        expected_version=None,
        *,
        allow_protected_fields: bool = False,
    ):
        """更新卡片；受保护字段默认锁定，只有用户显式确认才允许修改。

        Args:
            novel_id: 小说 ObjectId 字符串。
            card_id: 卡片 ObjectId 字符串。
            data: 待更新字段。
            expected_version: 乐观锁版本。
            allow_protected_fields: 界面上的用户显式编辑传 True；
                AI 与自动化路径必须保持默认 False。

        Raises:
            InvalidIdError: 未显式确认却修改了受保护字段。
        """
        oid = await self._resolve_card_oid(novel_id, card_id)
        existing = await self.repo.get_card_by_oid(novel_id, oid)
        if not allow_protected_fields:
            blocked = self._protected_diff(existing, data)
            if blocked:
                raise InvalidIdError(
                    f"字段 {', '.join(blocked)} 受保护，需显式确认后才能修改"
                )
        if "name" in data:
            data["name"] = data["name"].strip()
            if not data["name"]:
                raise InvalidIdError("卡片名称不能为空")
            if await self.repo.name_exists(novel_id, existing["type"], data["name"], exclude_oid=oid):
                raise DuplicateKeyError(f"卡片名称已存在: {data['name']}")
        if "fields" in data:
            data["fields"] = self._normalize_fields(existing["type"], data["fields"])
            if existing.get("type") == "rule":
                data["is_hard_rule"] = coerce_hard_rule_flag(
                    data["fields"].get("is_hard_rule")
                )
        if "aliases" in data:
            data["aliases"] = self._normalize_str_list(data["aliases"])
        if "tags" in data:
            data["tags"] = self._normalize_str_list(data["tags"])
        if "importance" in data and data["importance"] is not None:
            data["importance"] = max(1, min(5, int(data["importance"])))
        return await self.repo.update_card(novel_id, oid, data, expected_version=expected_version)

    async def delete_card(self, novel_id, card_id):
        oid = await self._resolve_card_oid(novel_id, card_id)
        return await self.repo.soft_delete_card(novel_id, oid)

    async def restore_card(self, novel_id, card_id):
        oid = await self._resolve_card_oid(novel_id, card_id, include_deleted=True)
        return await self.repo.restore_card(novel_id, oid)

    async def hard_delete_card(self, novel_id, card_id):
        oid = await self._resolve_card_oid(novel_id, card_id, include_deleted=True)
        return await self.repo.hard_delete_card(novel_id, oid)

    def get_card_types(self):
        return CARD_TYPES

    async def get_stats(self, novel_id):
        stats: Dict[str, int] = {"total": 0}
        for t in CARD_TYPES:
            c = await self.repo.count_by_type(novel_id, t)
            stats[t] = c
            stats["total"] += c
        total_with_deleted = await self.repo.count_documents(
            {"novel_id": to_object_id(novel_id)}, include_deleted=True
        )
        stats["deleted"] = total_with_deleted - stats["total"]
        return stats
