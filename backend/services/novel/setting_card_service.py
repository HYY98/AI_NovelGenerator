"""设定卡业务逻辑层：字段校验、归一化、乐观锁。"""
from typing import Any, Dict, List, Optional

from backend.db.utils import to_object_id
from backend.db.repositories.setting_card_repository import SettingCardRepository, CARD_TYPES
from backend.db.errors import InvalidIdError, DuplicateKeyError


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

    async def get_card(self, novel_id, card_id, include_deleted=False):
        return await self.repo.get_card_by_oid(novel_id, card_id, include_deleted=include_deleted)

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
        data["aliases"] = self._normalize_str_list(data.get("aliases"))
        data["tags"] = self._normalize_str_list(data.get("tags"))
        data["importance"] = max(1, min(5, int(data.get("importance", 3) or 3)))
        return await self.repo.create_card(novel_id, data)

    async def update_card(self, novel_id, card_id, data: Dict[str, Any], expected_version=None):
        existing = await self.repo.get_card_by_oid(novel_id, card_id)
        if "name" in data:
            data["name"] = data["name"].strip()
            if not data["name"]:
                raise InvalidIdError("卡片名称不能为空")
            if await self.repo.name_exists(novel_id, existing["type"], data["name"], exclude_oid=card_id):
                raise DuplicateKeyError(f"卡片名称已存在: {data['name']}")
        if "fields" in data:
            data["fields"] = self._normalize_fields(existing["type"], data["fields"])
        if "aliases" in data:
            data["aliases"] = self._normalize_str_list(data["aliases"])
        if "tags" in data:
            data["tags"] = self._normalize_str_list(data["tags"])
        if "importance" in data and data["importance"] is not None:
            data["importance"] = max(1, min(5, int(data["importance"])))
        return await self.repo.update_card(novel_id, card_id, data, expected_version=expected_version)

    async def delete_card(self, novel_id, card_id):
        return await self.repo.soft_delete_card(novel_id, card_id)

    async def restore_card(self, novel_id, card_id):
        return await self.repo.restore_card(novel_id, card_id)

    async def hard_delete_card(self, novel_id, card_id):
        return await self.repo.hard_delete_card(novel_id, card_id)

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
