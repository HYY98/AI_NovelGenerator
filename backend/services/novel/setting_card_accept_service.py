"""设定卡候选统一采纳服务。

所有 AI 候选（新建、合并、改写）都必须经过本服务才能写入正式实体。
统一校验顺序：
1. 生成记录归属校验（generation_id 必须属于该小说）
2. 版本校验（蓝图版本、章节版本、卡片版本）
3. 字段白名单校验（只写该卡类型允许的字段）
4. 写入正式实体
5. 事实变化写入 story_events 待确认
6. 标记生成记录已采纳

第 4 步失败时不会执行第 6 步，避免出现"标记已采纳但未写入"的不一致状态。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.db.repositories.generation_record_repository import generation_record_repo
from backend.db.repositories.setting_card_repository import (
    CARD_TYPES,
    SettingCardRepository,
)
from backend.db.repositories.story_event_repository import story_event_repo

logger = logging.getLogger(__name__)

# 允许的采纳动作
ACCEPT_ACTIONS = ("create", "merge", "rewrite", "reject")


class SettingCardAcceptService:
    """设定卡候选统一采纳服务。"""

    def __init__(self) -> None:
        self.card_repo = SettingCardRepository()

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_action(action: str) -> str:
        """校验采纳动作。"""
        text = str(action or "").strip().lower()
        if text not in ACCEPT_ACTIONS:
            raise InvalidIdError(
                f"非法的采纳动作: {action}，支持 create/merge/rewrite/reject"
            )
        return text

    @staticmethod
    def _filter_fields(
        card_type: str,
        fields: Dict[str, Any],
        warnings: List[str],
        *,
        confirm_hard_rule: bool = False,
    ) -> Dict[str, str]:
        """按卡片类型白名单过滤字段，并强制受保护字段的安全默认值。

        is_hard_rule 默认必须是 false：AI 不能单独把它置为 true，
        只有调用方显式声明 confirm_hard_rule 时才允许写入 true。

        Args:
            card_type: 卡片类型。
            fields: 待写入字段。
            warnings: 告警收集列表，会被就地追加。
            confirm_hard_rule: 是否确认把规则卡设为硬规则。

        Returns:
            过滤后的字段字典。
        """
        allowed = CARD_TYPES.get(card_type, {}).get("fields", {})
        filtered = {
            str(key): str(value)
            for key, value in (fields or {}).items()
            if str(key) in allowed and str(value).strip()
        }
        dropped = [str(key) for key in (fields or {}) if str(key) not in allowed]
        if dropped:
            warnings.append(f"字段 {', '.join(dropped)} 不属于 {card_type} 卡，已忽略")

        # is_hard_rule 安全默认值：未显式确认前一律回落为 false
        if "is_hard_rule" in filtered and not confirm_hard_rule:
            value = str(filtered["is_hard_rule"]).strip().lower()
            if value not in ("false", "0", "no", "否"):
                warnings.append("is_hard_rule 需用户显式确认，已回落为 false")
            filtered["is_hard_rule"] = "false"
        return filtered

    @staticmethod
    def _check_versions(
        record: Dict[str, Any],
        blueprint_version: Optional[int],
        chapter_version: Optional[int],
        outline_version: Optional[int] = None,
    ) -> None:
        """校验生成时的版本快照是否与当前一致。

        Args:
            record: 生成记录。
            blueprint_version: 客户端当前的蓝图版本。
            chapter_version: 客户端当前的章节版本。
            outline_version: 客户端当前的大纲版本。

        Raises:
            DuplicateKeyError: 版本已变化，需要重新生成。
        """
        for field, current, label in (
            ("blueprint_version", blueprint_version, "蓝图"),
            ("outline_version", outline_version, "大纲"),
            ("chapter_version", chapter_version, "章节"),
        ):
            recorded = record.get(field)
            if recorded is None or current is None:
                continue
            if int(recorded) != int(current):
                raise DuplicateKeyError(
                    f"{label}已更新，该候选基于旧版本生成，请重新生成后再采纳"
                )

    # ------------------------------------------------------------------
    # 采纳入口
    # ------------------------------------------------------------------
    async def accept(
        self,
        novel_id: str,
        *,
        generation_id: str,
        action: str,
        target_card_id: str = "",
        accepted_fields: Optional[Dict[str, Any]] = None,
        blueprint_version: Optional[int] = None,
        chapter_version: Optional[int] = None,
        outline_version: Optional[int] = None,
        expected_version: Optional[int] = None,
        create_state_change_events: bool = True,
        confirm_hard_rule: bool = False,
    ) -> Dict[str, Any]:
        """采纳一条设定卡候选。

        Args:
            novel_id: 小说 ObjectId 字符串。
            generation_id: 生成记录业务 ID。
            action: create / merge / rewrite / reject。
            target_card_id: 目标卡片业务 ID，create 时为空。
            accepted_fields: 采纳的字段差异。
            blueprint_version: 客户端当前蓝图版本，用于过期校验。
            chapter_version: 客户端当前章节版本，用于过期校验。
            expected_version: 目标卡片版本，用于乐观锁。
            create_state_change_events: 是否把事实变化写入待确认事件。

        Returns:
            采纳结果，包含卡片、告警与写入的事件数量。

        Raises:
            NotFoundError: 生成记录或目标卡片不存在。
            DuplicateKeyError: 动作非法、版本冲突或重复采纳。
            InvalidIdError: 缺少必要参数或字段不属于该卡类型。
        """
        action = self._validate_action(action)
        warnings: List[str] = []

        # 1. 归属校验：generation_id 必须属于该小说
        record = await generation_record_repo.get_owned_record(novel_id, generation_id)
        # 2. 版本校验
        self._check_versions(
            record, blueprint_version, chapter_version, outline_version
        )

        if action == "reject":
            adopted = await generation_record_repo.mark_accepted_action(
                novel_id, generation_id, "reject"
            )
            if not adopted:
                raise DuplicateKeyError("该候选已被处理，请勿重复提交")
            return {
                "action": "reject",
                "card": None,
                "events_created": 0,
                "warnings": warnings,
            }

        card: Optional[Dict[str, Any]] = None
        if action == "create":
            card = await self._accept_create(
                novel_id, record, accepted_fields, warnings,
                confirm_hard_rule=confirm_hard_rule,
            )
        else:
            if not target_card_id:
                raise InvalidIdError(f"{action} 动作必须提供 target_card_id")
            card = await self._accept_update(
                novel_id,
                record,
                action,
                target_card_id,
                accepted_fields,
                expected_version,
                warnings,
                confirm_hard_rule=confirm_hard_rule,
            )

        # 5. 事实变化写入待确认事件（只写候选，需要用户二次确认才生效）
        events_created = 0
        if create_state_change_events and card:
            events_created = await self._create_state_change_events(
                novel_id, generation_id, card, warnings
            )

        # 6. 正式写入成功后才标记采纳，失败不会走到这里
        adopted = await generation_record_repo.mark_accepted_action(
            novel_id,
            generation_id,
            action,
            accepted_fields=accepted_fields or {},
        )
        if not adopted:
            # 极少数并发场景：实体已写入但记录已被其他请求标记，实体以已写入为准
            warnings.append("该候选已被其他请求处理，实体已写入但采纳标记未更新")

        return {
            "action": action,
            "card": card,
            "events_created": events_created,
            "warnings": warnings,
        }

    async def _accept_create(
        self,
        novel_id: str,
        record: Dict[str, Any],
        accepted_fields: Optional[Dict[str, Any]],
        warnings: List[str],
        *,
        confirm_hard_rule: bool = False,
    ) -> Dict[str, Any]:
        """采纳新建卡片的候选。"""
        result = record.get("result") or {}
        data = result.get("data") or {}
        candidates = data.get("candidates") or []
        if not candidates:
            raise InvalidIdError("该生成记录没有可采纳的卡片候选")
        index = int(accepted_fields.get("candidate_index", 0) or 0)
        if index >= len(candidates):
            raise InvalidIdError(f"候选下标 {index} 超出范围")
        candidate = candidates[index]

        card_type = str(candidate.get("type") or "").strip().lower()
        if card_type not in CARD_TYPES:
            raise InvalidIdError(f"非法的卡片类型: {card_type}")
        name = str(candidate.get("name") or "").strip()
        if not name:
            raise InvalidIdError("候选卡片缺少名称，无法创建")

        merged_fields = dict(candidate.get("fields") or {})
        merged_fields.update(
            {
                str(k): str(v)
                for k, v in (accepted_fields or {}).items()
                if k != "candidate_index" and str(v).strip()
            }
        )
        filtered = self._filter_fields(
            card_type, merged_fields, warnings, confirm_hard_rule=confirm_hard_rule
        )
        return await self.card_repo.create_card(
            novel_id,
            {
                "type": card_type,
                "name": name,
                "aliases": [str(a) for a in candidate.get("aliases") or [] if str(a).strip()],
                "fields": filtered,
                "importance": int(candidate.get("importance") or 3),
                "current_state": str(candidate.get("current_state") or ""),
            },
        )

    async def _accept_update(
        self,
        novel_id: str,
        record: Dict[str, Any],
        action: str,
        target_card_id: str,
        accepted_fields: Optional[Dict[str, Any]],
        expected_version: Optional[int],
        warnings: List[str],
        *,
        confirm_hard_rule: bool = False,
    ) -> Dict[str, Any]:
        """采纳合并或改写候选，走乐观锁更新。"""
        # 目标卡片归属校验：业务 ID 直接查该小说下的卡片
        existing = await self.card_repo.get_card_by_business_id(novel_id, target_card_id)
        card_type = str(existing.get("type") or "").strip().lower()
        if card_type not in CARD_TYPES:
            raise InvalidIdError(f"非法的卡片类型: {card_type}")

        requested = dict(accepted_fields or {})
        # 改写动作支持直接从生成记录的 changed_fields 取候选值
        if action == "rewrite" and not requested:
            data = (record.get("result") or {}).get("data") or {}
            requested = {
                str(item.get("field") or ""): str(item.get("new_value") or "")
                for item in (data.get("changed_fields") or [])
                if str(item.get("field") or "").strip()
            }
        if not requested:
            raise InvalidIdError("没有需要写入的字段，无法采纳")

        filtered = self._filter_fields(
            card_type, requested, warnings, confirm_hard_rule=confirm_hard_rule
        )
        if not filtered:
            raise InvalidIdError("采纳字段全部不属于该卡类型，已取消写入")

        merged_fields = dict(existing.get("fields") or {})
        merged_fields.update(filtered)
        return await self.card_repo.update_card_by_business_id(
            novel_id,
            target_card_id,
            {"fields": merged_fields},
            expected_version=expected_version,
        )

    # ------------------------------------------------------------------
    # 事实变化事件
    # ------------------------------------------------------------------
    async def _create_state_change_events(
        self,
        novel_id: str,
        generation_id: str,
        card: Dict[str, Any],
        warnings: List[str],
    ) -> int:
        """把卡片携带的事实变化候选写入 story_events 待确认。

        Args:
            novel_id: 小说 ObjectId 字符串。
            generation_id: 生成记录业务 ID。
            card: 已写入的卡片文档。
            warnings: 告警收集列表。

        Returns:
            实际写入的事件数量；无候选时返回 0。
        """
        state_changes = (card.get("fields") or {}).get("__state_changes__")
        if not isinstance(state_changes, list) or not state_changes:
            return 0
        items: List[Dict[str, Any]] = []
        for change in state_changes:
            if not isinstance(change, dict):
                continue
            entity_id = str(change.get("entity_id") or "").strip() or str(
                card.get("card_id") or ""
            )
            if not entity_id:
                continue
            evidence = change.get("evidence") or {}
            items.append(
                {
                    "entity_type": str(change.get("entity_type") or card.get("type") or ""),
                    "entity_id": entity_id,
                    "event_type": str(change.get("change_type") or "state_change"),
                    "before": change.get("before") or {},
                    "after": change.get("after") or {},
                    "evidence": {"chapter_id": str(card.get("first_appearance_chapter") or "")},
                    "evidence_text": str(evidence.get("text") or ""),
                    "evidence_start": evidence.get("start"),
                    "evidence_end": evidence.get("end"),
                    "confidence": float(change.get("confidence") or 0.0),
                    "status": "pending",
                    "source": "ai",
                    "generation_id": generation_id,
                    "note": str(change.get("note") or ""),
                }
            )
        if not items:
            return 0
        events = await story_event_repo.create_events(novel_id, items)
        warnings.append(f"已生成 {len(events)} 条事实变化候选，需确认后才会生效")
        return len(events)


setting_card_accept_service = SettingCardAcceptService()
