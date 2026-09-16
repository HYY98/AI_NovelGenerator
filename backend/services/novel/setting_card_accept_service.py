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

from backend.api.error_contract import ServiceError
from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.db.repositories.generation_record_repository import generation_record_repo
from backend.db.repositories.novel_blueprint_repository import novel_blueprint_repo
from backend.db.repositories.setting_card_repository import (
    CARD_TYPES,
    SettingCardRepository,
)
from backend.db.repositories.story_event_repository import story_event_repo
from backend.db.utils import to_object_id
from backend.services.novel.candidate_accept_service import (
    claim_candidate,
    mark_candidate_rejected,
    release_candidate,
    resolve_candidate,
    run_operation,
)

logger = logging.getLogger(__name__)

# 允许的采纳动作
ACCEPT_ACTIONS = ("create", "merge", "rewrite", "reject")

# 受保护的结构性字段：绝不允许从 accepted_fields / candidate.fields 覆盖。
# 这些字段只由后端从候选的固定结构提取（name/aliases/importance）或由后端初始化
# （current_state 走 story_events，is_hard_rule 需显式确认，见 _filter_fields）。
PROTECTED_STRUCTURAL_FIELDS = frozenset(
    {
        "type",
        "name",
        "aliases",
        "importance",
        "current_state",
        "card_id",
        "enabled",
        "first_appearance_chapter",
        "version",
        "state_history",
        "tags",
        "sort_order",
        "reason",
        "conflicts",
        "source",
        "candidate_index",
        "__state_changes__",
    }
)


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
        # 先剔除受保护的结构性字段，再从类型白名单过滤，双重保险
        filtered = {
            str(key): str(value)
            for key, value in (fields or {}).items()
            if str(key) in allowed
            and str(key) not in PROTECTED_STRUCTURAL_FIELDS
            and str(value).strip()
        }
        dropped = [
            str(key)
            for key in (fields or {})
            if str(key) not in allowed or str(key) in PROTECTED_STRUCTURAL_FIELDS
        ]
        if dropped:
            warnings.append(f"字段 {', '.join(dropped)} 不属于 {card_type} 卡白名单或为受保护字段，已忽略")

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
        request_id: str = "",
        candidate_id: str = "",
        target_card_id: str = "",
        accepted_fields: Optional[Dict[str, Any]] = None,
        blueprint_version: Optional[int] = None,
        chapter_version: Optional[int] = None,
        outline_version: Optional[int] = None,
        expected_version: Optional[int] = None,
        create_state_change_events: bool = True,
        confirm_hard_rule: bool = False,
    ) -> Dict[str, Any]:
        """采纳一条设定卡候选，走统一提交协议。

        Args:
            novel_id: 小说 ObjectId 字符串。
            generation_id: 生成记录业务 ID。
            action: create / merge / rewrite / reject。
            request_id: 客户端幂等键，必填。
            candidate_id: 候选 ID；多候选记录必填，单候选记录可为空。
            target_card_id: 目标卡片业务 ID，create 时为空。
            accepted_fields: 采纳的字段差异。
            blueprint_version: 客户端当前蓝图版本，与服务端当前值比对。
            chapter_version: 客户端当前章节版本，与服务端当前值比对。
            outline_version: 客户端当前大纲版本。
            expected_version: 目标卡片版本，用于乐观锁。
            create_state_change_events: 是否把事实变化写入待确认事件。
            confirm_hard_rule: 是否确认把规则卡设为硬规则。

        Returns:
            采纳结果，包含卡片、告警、事件数量、操作 ID 与是否命中重放。

        Raises:
            ServiceError: 幂等冲突、候选已被采纳或版本已变化。
            NotFoundError: 生成记录或目标卡片不存在。
            InvalidIdError: 缺少必要参数或字段不属于该卡类型。
        """
        action = self._validate_action(action)
        if not str(request_id or "").strip():
            raise ServiceError("REQUEST_ID_REQUIRED", "采纳操作缺少幂等键 request_id")

        request_payload = {
            "novel_id": str(novel_id),
            "generation_id": generation_id,
            "candidate_id": candidate_id,
            "action": action,
            "target_card_id": target_card_id,
            "accepted_fields": accepted_fields or {},
            "expected_version": expected_version,
        }

        async def _executor(handle) -> Dict[str, Any]:
            return await self._accept_with_claim(
                novel_id,
                handle.operation_id,
                generation_id=generation_id,
                action=action,
                candidate_id=candidate_id,
                target_card_id=target_card_id,
                accepted_fields=accepted_fields,
                blueprint_version=blueprint_version,
                chapter_version=chapter_version,
                outline_version=outline_version,
                expected_version=expected_version,
                create_state_change_events=create_state_change_events,
                confirm_hard_rule=confirm_hard_rule,
            )

        outcome = await run_operation(
            novel_id=novel_id,
            operation_type="setting_card_accept",
            request_id=request_id,
            request_payload=request_payload,
            target={
                "generation_id": generation_id,
                "candidate_id": candidate_id,
                "action": action,
            },
            executor=_executor,
        )
        body = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
        body["operation_id"] = outcome.get("operation_id")
        body["replayed"] = bool(outcome.get("replayed"))
        return body

    async def _accept_with_claim(
        self,
        novel_id: str,
        operation_id: str,
        *,
        generation_id: str,
        action: str,
        candidate_id: str,
        target_card_id: str,
        accepted_fields: Optional[Dict[str, Any]],
        blueprint_version: Optional[int],
        chapter_version: Optional[int],
        outline_version: Optional[int],
        expected_version: Optional[int],
        create_state_change_events: bool,
        confirm_hard_rule: bool,
    ) -> Dict[str, Any]:
        """在已认领操作的前提下执行采纳写入。"""
        warnings: List[str] = []

        # 1. 归属校验：generation_id 必须属于该小说
        record = await generation_record_repo.get_owned_record(novel_id, generation_id)
        # 2. 候选解析：多候选记录必须定位到具体候选
        resolved = await resolve_candidate(novel_id, generation_id, candidate_id)
        if str(resolved.get("status")) == "accepted":
            raise ServiceError(
                "CANDIDATE_ALREADY_ACCEPTED",
                f"候选 {resolved.get('candidate_id') or generation_id} 已被采纳，请勿重复提交",
            )
        # 3. 版本校验：以服务端当前版本为基准，客户端传入值只做一致性比对
        await self._check_server_versions(
            novel_id,
            record,
            blueprint_version=blueprint_version,
            chapter_version=chapter_version,
        )

        if action == "reject":
            await mark_candidate_rejected(
                novel_id,
                generation_id,
                str(resolved.get("candidate_id") or ""),
                reason="用户拒绝",
                operation_id=operation_id,
            )
            await generation_record_repo.mark_accepted_action(
                novel_id, generation_id, "reject"
            )
            return {
                "action": "reject",
                "card": None,
                "events_created": 0,
                "warnings": warnings,
                "candidate_id": str(resolved.get("candidate_id") or ""),
                "candidate_status": "rejected",
            }

        # 4. 并发认领：同一候选只有一个请求能走到写入
        await claim_candidate(
            novel_id, generation_id, str(resolved.get("candidate_id") or ""), operation_id
        )

        card: Optional[Dict[str, Any]] = None
        state_change_source: Dict[str, Any] = {}
        try:
            if action == "create":
                card, state_change_source = await self._accept_create(
                    novel_id,
                    record,
                    resolved.get("data") or {},
                    accepted_fields,
                    warnings,
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
        except Exception:
            # 正式写入失败：释放认领，允许用同一 request_id 重试
            await release_candidate(
                novel_id, generation_id, str(resolved.get("candidate_id") or "")
            )
            raise

        # 5. 事实变化写入待确认事件（动态状态从候选提取，不落卡片稳定字段）
        events_created = 0
        if create_state_change_events and card:
            events_created = await self._create_state_change_events(
                novel_id, generation_id, state_change_source, card, warnings
            )

        # 6. 正式写入成功后才标记采纳，失败不会走到这里
        adopted = await generation_record_repo.mark_accepted_action(
            novel_id,
            generation_id,
            action,
            accepted_fields=accepted_fields or {},
        )
        if not adopted:
            warnings.append("该候选已被其他请求处理，实体已写入但采纳标记未更新")

        return {
            "action": action,
            "card": card,
            "events_created": events_created,
            "warnings": warnings,
            "candidate_id": str(resolved.get("candidate_id") or ""),
            "candidate_status": "accepted",
        }

    async def _check_server_versions(
        self,
        novel_id: str,
        record: Dict[str, Any],
        *,
        blueprint_version: Optional[int] = None,
        chapter_version: Optional[int] = None,
    ) -> None:
        """以服务端当前版本为基准校验候选是否过期。

        Args:
            novel_id: 小说 ObjectId 字符串。
            record: 生成记录。
            blueprint_version: 客户端当前蓝图版本。
            chapter_version: 客户端当前章节版本。

        Raises:
            ServiceError: 服务端版本已推进，或客户端版本落后于服务端。
        """
        recorded_blueprint = record.get("blueprint_version")
        current_blueprint = await self._current_blueprint_version(novel_id)
        if recorded_blueprint is not None and current_blueprint is not None:
            if int(recorded_blueprint) != int(current_blueprint):
                raise ServiceError(
                    "VERSION_STALE",
                    f"蓝图已更新（服务端 v{current_blueprint}，候选基于 v{recorded_blueprint}），请重新生成后再采纳",
                )
        if blueprint_version is not None and current_blueprint is not None:
            if int(blueprint_version) != int(current_blueprint):
                raise ServiceError(
                    "VERSION_STALE",
                    f"蓝图已更新（服务端 v{current_blueprint}，请求携带 v{blueprint_version}），请刷新后重试",
                )

        chapter_id = str(record.get("chapter_id") or "") or str(
            (record.get("scope") or {}).get("chapter_id") or ""
        )
        recorded_chapter = record.get("chapter_version")
        if chapter_id:
            current_chapter = await self._current_chapter_version(novel_id, chapter_id)
            if recorded_chapter is not None and current_chapter is not None:
                if int(recorded_chapter) != int(current_chapter):
                    raise ServiceError(
                        "VERSION_STALE",
                        f"章节已更新（服务端 v{current_chapter}，候选基于 v{recorded_chapter}），请重新生成后再采纳",
                    )
            if chapter_version is not None and current_chapter is not None:
                if int(chapter_version) != int(current_chapter):
                    raise ServiceError(
                        "VERSION_STALE",
                        f"章节已更新（服务端 v{current_chapter}，请求携带 v{chapter_version}），请刷新后重试",
                    )

    @staticmethod
    async def _current_blueprint_version(novel_id: str) -> Optional[int]:
        """读取小说当前蓝图版本号；无蓝图时返回 None。"""
        try:
            docs = await novel_blueprint_repo.find_by_novel(novel_id, limit=1)
        except Exception:  # noqa: BLE001 - 蓝图缺失不应阻断采纳
            return None
        if not docs:
            return None
        version = docs[0].get("version")
        return int(version) if version is not None else None

    @staticmethod
    async def _current_chapter_version(novel_id: str, chapter_id: str) -> Optional[int]:
        """读取章节当前版本号；章节不存在时返回 None。"""
        from backend.db.mongo import get_database

        doc = await get_database()["chapters"].find_one(
            {"novel_id": to_object_id(novel_id), "chapter_id": chapter_id}
        )
        if not doc:
            return None
        version = doc.get("version")
        return int(version) if version is not None else None

    async def _accept_create(
        self,
        novel_id: str,
        record: Dict[str, Any],
        candidate: Dict[str, Any],
        accepted_fields: Optional[Dict[str, Any]],
        warnings: List[str],
        *,
        confirm_hard_rule: bool = False,
    ) -> Dict[str, Any]:
        """采纳新建卡片的候选。

        Args:
            novel_id: 小说 ObjectId 字符串。
            record: 生成记录。
            candidate: 已解析出的候选数据。
            accepted_fields: 采纳的字段差异。
            warnings: 告警收集列表。
            confirm_hard_rule: 是否确认把规则卡设为硬规则。

        Returns:
            ``(新建的卡片文档, 用于提取动态状态的候选数据)``。

        Raises:
            InvalidIdError: 候选为空或缺少必要字段。
        """
        candidate = dict(candidate or {})
        nested = candidate.get("candidates")
        if isinstance(nested, list) and nested:
            index = int((accepted_fields or {}).get("candidate_index", 0) or 0)
            if index >= len(nested):
                raise InvalidIdError(f"候选下标 {index} 超出范围")
            candidate = dict(nested[index] or {})
        if not candidate:
            raise InvalidIdError("该生成记录没有可采纳的卡片候选")

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
        # 稳定资料落卡片：name/aliases/fields/importance 只从候选固定结构取；
        # current_state 属动态状态，不落卡片（置空），改走 story_events 待确认。
        card = await self.card_repo.create_card(
            novel_id,
            {
                "type": card_type,
                "name": name,
                "aliases": [str(a) for a in candidate.get("aliases") or [] if str(a).strip()],
                "fields": filtered,
                "importance": int(candidate.get("importance") or 3),
                "current_state": "",
            },
        )
        await self._snapshot_card(
            novel_id,
            card,
            operation_type="accept",
            source="ai_adopt",
            summary=f"采纳新建 {card_type} 卡",
        )
        return card, candidate

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
        card = await self.card_repo.update_card_by_business_id(
            novel_id,
            target_card_id,
            {"fields": merged_fields},
            expected_version=expected_version,
        )
        await self._snapshot_card(
            novel_id,
            card,
            operation_type="accept",
            source="ai_adopt",
            summary=f"采纳 {action} {card_type} 卡",
        )
        return card

    @staticmethod
    async def _snapshot_card(
        novel_id: str,
        card: Dict[str, Any],
        *,
        operation_type: str,
        source: str,
        summary: str = "",
    ) -> None:
        """设定卡采纳写入后自动写一条版本快照（v2.0 T10）。

        内容未变化时复用上一条快照，避免重复采纳产生版本噪声。
        """
        from backend.services.novel.entity_version_service import entity_version_service

        await entity_version_service.snapshot_entity(
            str(novel_id),
            "setting_card",
            str(card.get("card_id") or ""),
            {
                "fields": dict(card.get("fields") or {}),
                "name": str(card.get("name") or ""),
            },
            operation_type=operation_type,
            source=source,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # 事实变化事件
    # ------------------------------------------------------------------
    def _extract_state_changes(
        self,
        candidate: Dict[str, Any],
        card: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """从候选提取动态状态变化，转成 story_event 条目。

        候选的动态状态有三种来源（优先级从高到低）：
        1. ``state_changes`` 列表（CardStateChangeSchema，显式 before/after）；
        2. ``state_change`` 字符串（抽取建议里的单条状态变化描述）；
        3. ``current_state`` 字段（新建候选的当前状态，作为 after 写入）。

        动态状态不落卡片稳定字段，统一进入 story_events 待确认。

        Args:
            candidate: 已解析出的候选数据。
            card: 已写入的卡片文档（提供 card_id/type 兜底）。

        Returns:
            事件条目列表，供 ``story_event_repo.create_events`` 使用。
        """
        entity_type = str(card.get("type") or candidate.get("type") or "").strip()
        entity_id = str(card.get("card_id") or candidate.get("card_id") or "").strip()
        if not entity_id or entity_type not in ("location", "item", "rule"):
            return []
        items: List[Dict[str, Any]] = []

        def _make_item(
            *,
            before: Any = None,
            after: Any = None,
            evidence: Any = None,
            confidence: float = 0.0,
            note: str = "",
        ) -> Dict[str, Any]:
            evidence = evidence or {}
            return {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "event_type": "state_change",
                "before": before or {},
                "after": after or {},
                "evidence": {"chapter_id": str(card.get("first_appearance_chapter") or "")},
                "evidence_text": str(evidence.get("text") or ""),
                "evidence_start": evidence.get("start"),
                "evidence_end": evidence.get("end"),
                "confidence": float(confidence or 0.0),
                "status": "pending",
                "source": "ai",
                "generation_id": str(card.get("_generation_id") or ""),
                "note": str(note or ""),
            }

        state_changes = candidate.get("state_changes")
        if isinstance(state_changes, list):
            for change in state_changes:
                if not isinstance(change, dict):
                    continue
                change_entity = str(change.get("entity_id") or "").strip() or entity_id
                change_type = str(change.get("entity_type") or entity_type).strip()
                evidence = change.get("evidence") or {}
                items.append(
                    _make_item(
                        before=change.get("before") or {},
                        after=change.get("after") or {},
                        evidence=evidence,
                        confidence=float(change.get("confidence") or 0.0),
                        note=str(change.get("note") or ""),
                    )
                )
                # 让这条事件指向真正的实体（若候选显式给出）
                items[-1]["entity_type"] = change_type
                items[-1]["entity_id"] = change_entity

        single_change = candidate.get("state_change")
        if isinstance(single_change, str) and single_change.strip():
            items.append(_make_item(after={"state": single_change.strip()}))

        current_state = str(candidate.get("current_state") or "").strip()
        if current_state:
            items.append(_make_item(after={"current_state": current_state}))

        return items

    async def _create_state_change_events(
        self,
        novel_id: str,
        generation_id: str,
        candidate: Dict[str, Any],
        card: Dict[str, Any],
        warnings: List[str],
    ) -> int:
        """把候选携带的动态状态变化写入 story_events 待确认。

        与卡片稳定资料分离：卡片本体只存 name/aliases/fields/importance，
        current_state 等动态事实通过 story_events 二次确认后才生效。

        Args:
            novel_id: 小说 ObjectId 字符串。
            generation_id: 生成记录业务 ID。
            candidate: 已解析出的候选数据。
            card: 已写入的卡片文档。
            warnings: 告警收集列表。

        Returns:
            实际写入的事件数量；无候选时返回 0。
        """
        items = self._extract_state_changes(candidate, card)
        if not items:
            return 0
        # 事件需要关联到产生它的生成记录
        for item in items:
            item["generation_id"] = generation_id
        events = await story_event_repo.create_events(novel_id, items)
        warnings.append(f"已生成 {len(events)} 条事实变化候选，需确认后才会生效")
        return len(events)


setting_card_accept_service = SettingCardAcceptService()
