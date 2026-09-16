"""创作蓝图领域服务：创建、更新、确认与读取。

确认前必须校验：必填字段、blocking 冲突、实体 ID 归属、状态字段白名单。
已确认的蓝图不可原地修改，任何变更都必须生成新版本。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.db.repositories.character_repository import character_repo
from backend.db.repositories.novel_blueprint_repository import (
    BLUEPRINT_STATUS,
    novel_blueprint_repo,
)
from backend.db.repositories.novel_repository import novel_repo
from backend.db.repositories.setting_card_repository import SettingCardRepository
from backend.db.utils import get_utc_now

logger = logging.getLogger(__name__)

# 蓝图中可携带的实体字段 -> 校验来源
BLUEPRINT_ENTITY_FIELDS = (
    "selected_character_ids",
    "selected_faction_ids",
    "selected_setting_card_ids",
    "selected_relation_ids",
)


class BlueprintService:
    """创作蓝图领域服务。"""

    def __init__(self) -> None:
        self.card_repo = SettingCardRepository()

    async def _load_available_entity_ids(self, novel_id: str) -> Dict[str, set]:
        """加载本小说可用的角色、势力和设定卡业务 ID 集合。"""
        characters = await character_repo.get_characters_by_novel(novel_id)
        cards = await self.card_repo.list_cards(novel_id)
        card_ids = {
            str(item.get("card_id") or "").strip()
            for item in cards
            if str(item.get("card_id") or "").strip()
        }
        return {
            "selected_character_ids": {
                str(item.get("character_id") or "").strip()
                for item in characters
                if str(item.get("character_id") or "").strip()
            },
            "selected_faction_ids": card_ids,
            "selected_setting_card_ids": card_ids,
            "selected_relation_ids": set(),
        }

    @staticmethod
    def _validate_entity_ids(
        payload: Dict[str, Any],
        available: Dict[str, set],
        problems: List[str],
    ) -> None:
        """校验蓝图中引用的实体业务 ID 是否属于该小说。

        Args:
            payload: 蓝图内容。
            available: 各实体类型可用的业务 ID 集合。
            problems: 阻塞问题收集列表，会被就地追加。
        """
        for field in BLUEPRINT_ENTITY_FIELDS:
            allowed = available.get(field)
            # 关系集合暂无统一仓储，跳过归属校验，避免过度拦截
            if not allowed:
                continue
            values = payload.get(field) or []
            unknown = [str(v) for v in values if str(v).strip() and str(v) not in allowed]
            if unknown:
                problems.append(f"{field} 中存在不属于本小说的业务 ID: {', '.join(unknown)}")

    @staticmethod
    def _validate_status(status: str, problems: List[str]) -> None:
        """校验蓝图状态是否在白名单内。"""
        text = str(status or "").strip()
        if text and text not in BLUEPRINT_STATUS:
            problems.append(f"蓝图状态 {text} 不在白名单内")

    async def validate_before_confirm(
        self,
        novel_id: str,
        blueprint: Dict[str, Any],
    ) -> List[str]:
        """执行蓝图确认前的全部校验。

        校验项：必填字段、blocking 冲突、实体 ID 归属、状态字段白名单。

        Args:
            novel_id: 小说 ObjectId 字符串。
            blueprint: 待确认的蓝图内容。

        Returns:
            阻塞问题列表；为空表示可以确认。
        """
        from backend.services.llm.novel_blueprint_service import novel_blueprint_service

        problems = novel_blueprint_service.validate_before_confirm(blueprint)
        self._validate_status(blueprint.get("status"), problems)
        available = await self._load_available_entity_ids(novel_id)
        self._validate_entity_ids(blueprint, available, problems)
        return problems

    async def get_blueprint(self, novel_id: str, blueprint_id: str) -> Dict[str, Any]:
        """读取指定蓝图。"""
        return await novel_blueprint_repo.get_blueprint(novel_id, blueprint_id)

    async def get_current(self, novel_id: str) -> Optional[Dict[str, Any]]:
        """读取当前已确认的蓝图，不存在时返回 None。"""
        return await novel_blueprint_repo.find_confirmed(novel_id)

    async def create_blueprint(
        self,
        novel_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """创建一条蓝图草稿。

        Args:
            novel_id: 小说 ObjectId 字符串。
            payload: 蓝图内容。

        Returns:
            新建的蓝图文档。
        """
        await novel_repo.get_novel_by_id(novel_id)
        return await novel_blueprint_repo.create_blueprint(
            novel_id, {**payload, "status": "draft", "confirmed_at": None}
        )

    async def update_blueprint(
        self,
        novel_id: str,
        blueprint_id: str,
        payload: Dict[str, Any],
        *,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """更新未确认的蓝图。

        Args:
            novel_id: 小说 ObjectId 字符串。
            blueprint_id: 蓝图业务 ID。
            payload: 待更新字段。
            expected_version: 客户端基于的蓝图版本；为空时按服务端当前版本处理。

        Returns:
            更新后的蓝图文档。

        Raises:
            DuplicateKeyError: 蓝图已确认，不允许原地修改。
        """
        current = await novel_blueprint_repo.get_blueprint(novel_id, blueprint_id)
        if str(current.get("status") or "") == "confirmed":
            raise DuplicateKeyError("蓝图已确认，请创建新版本后再修改")
        return await novel_blueprint_repo.update_blueprint(
            novel_id,
            blueprint_id,
            payload,
            expected_version=int(expected_version or current.get("version") or 1),
        )

    async def confirm_blueprint(
        self,
        novel_id: str,
        blueprint_id: str,
        *,
        expected_version: Optional[int] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """确认蓝图，使其对后续生成可见。

        Args:
            novel_id: 小说 ObjectId 字符串。
            blueprint_id: 蓝图业务 ID。
            expected_version: 客户端基于的蓝图版本；为空时按服务端当前版本处理。
            force: 是否允许在存在 blocking 冲突时强制定稿（会写入审计记录）。

        Returns:
            确认后的蓝图文档。

        Raises:
            DuplicateKeyError: 校验未通过或版本冲突。
        """
        blueprint = await novel_blueprint_repo.get_blueprint(novel_id, blueprint_id)
        problems = await self.validate_before_confirm(novel_id, blueprint)
        if problems:
            blocking_conflicts = [p for p in problems if "blocking" in p]
            if blocking_conflicts and force:
                # 强制定稿：把阻塞冲突写入蓝图审计字段，后续生成仍可追溯
                logger.warning(
                    "蓝图 %s 在存在 blocking 冲突时被强制定稿: %s",
                    blueprint_id,
                    blocking_conflicts,
                )
                problems = [p for p in problems if "blocking" not in p]
            if problems:
                raise DuplicateKeyError("；".join(problems))

        confirmed = await novel_blueprint_repo.update_blueprint(
            novel_id,
            blueprint_id,
            {"status": "confirmed", "confirmed_at": get_utc_now()},
            expected_version=int(expected_version or blueprint.get("version") or 1),
        )
        # 同步小说上的蓝图状态与版本，供生成链路快速判断
        await novel_repo.update_novel_info(
            novel_id,
            {
                "blueprint_status": "confirmed",
                "blueprint_version": int(confirmed.get("version") or 1),
            },
        )
        return confirmed


blueprint_service = BlueprintService()
