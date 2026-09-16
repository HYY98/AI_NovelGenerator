"""章节定稿服务。

定稿不是"把状态改成 finalized"，而是一次带确认的写入流程（技术指引第 10、23 节）：

1. 校验章节版本与正文完整性；
2. 执行一致性审校，阻断级问题不允许直接定稿；
3. 提取实体事实变更候选，写入 story_events（pending）；
4. 用户在待确认列表中逐条接受/拒绝；
5. 事务内落库：更新章节状态、写入事件结论、把接受的变更应用到卡片；
6. MongoDB 不支持事务时自动降级为顺序写入，并把过程记录在事件状态里。

角色档案涉及关系与势力绑定的联动，属于必须由用户在角色页确认的"正式设定"，
因此角色类事件只做确认记录，不在这里自动改写角色档案。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.api.error_contract import ServiceError
from backend.db.errors import DuplicateKeyError, InvalidIdError, NotFoundError
from backend.db.repositories.chapter_repository import ChapterRepository
from backend.db.utils import get_utc_now
from backend.db.repositories.generation_record_repository import generation_record_repo
from backend.db.repositories.novel_blueprint_repository import novel_blueprint_repo
from backend.db.repositories.setting_card_repository import (
    CARD_TYPES,
    SettingCardRepository,
    coerce_hard_rule_flag,
)
from backend.db.repositories.story_event_repository import story_event_repo
from backend.db.transaction import run_mongo_write_unit
from backend.services.llm.chapter_generation_service import chapter_generation_service
from backend.services.novel.candidate_accept_service import run_operation

logger = logging.getLogger(__name__)

# 事件字段名到卡片字段键的映射（after 里出现的键优先按卡片字段处理）
STATE_FIELD = "current_state"


@dataclass
class _GenerationRequest:
    """供章节 AI 服务使用的轻量请求对象。"""

    novel_id: str
    chapter_id: str
    request_id: str = ""
    user_prompt: str = ""
    provider: str = ""
    content: str = ""
    use_stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    system_prompt: Optional[str] = None


@dataclass
class _AppliedChange:
    """已应用到正式卡片的变更记录。"""

    card_id: str
    card_type: str
    current_state: str = ""
    fields: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


class FinalizeChapterService:
    """章节定稿的两个阶段：审校准备与用户确认后提交。"""

    def __init__(self) -> None:
        self.chapter_repo = ChapterRepository()
        self.card_repo = SettingCardRepository()

    # ------------------------------------------------------------------
    # 阶段一：审校 + 生成待确认变更
    # ------------------------------------------------------------------
    async def review(self, request: Any) -> Dict[str, Any]:
        """执行一致性审校并生成待确认的状态变更候选。

        Args:
            request: 含 novel_id、chapter_id(ObjectId)、request_id、user_prompt、provider 的请求。

        Returns:
            含审校问题、待确认事件与章节版本信息的字典。

        Raises:
            NotFoundError: 章节不存在时抛出。
            InvalidIdError: 章节正文为空时抛出。
        """
        chapter = await self.chapter_repo.get_chapter(request.chapter_id)
        if chapter.get("is_deleted"):
            raise InvalidIdError("章节已删除，无法审校")
        if not str(chapter.get("content") or "").strip():
            raise InvalidIdError("章节正文为空，请先写入正文再定稿")

        novel_id = request.novel_id
        chapter_biz_id = str(chapter.get("chapter_id") or "")

        review_response = await chapter_generation_service.review_chapter(request)
        review_data = review_response.get("data") or {}

        proposal_response = await chapter_generation_service.propose_state_changes(request)
        proposals = (proposal_response.get("data") or {}).get("proposals", [])

        events = await self._replace_pending_events(
            novel_id,
            chapter_biz_id,
            proposals,
            generation_id=proposal_response.get("candidate_id", ""),
        )

        blocking = [
            issue for issue in review_data.get("issues", []) if issue.get("severity") == "blocking"
        ]
        # 增量新增：记录本次审校依据的硬规则签名与蓝图版本，提交时由服务端复核
        hard_rules = await self.list_hard_rules(novel_id)
        blueprint = await novel_blueprint_repo.find_confirmed(novel_id)
        return {
            "chapter_id": chapter_biz_id,
            "chapter_version": chapter.get("version", 1),
            "chapter_status": chapter.get("status"),
            "review": review_data,
            "review_generation_id": review_response.get("candidate_id", ""),
            "state_change_events": events,
            "blocking_count": len(blocking),
            "can_finalize": len(blocking) == 0,
            "hard_rules": [
                {
                    "card_id": str(card.get("card_id") or ""),
                    "name": str(card.get("name") or ""),
                    "version": int(card.get("version") or 1),
                }
                for card in hard_rules
            ],
            "hard_rule_signature": _hard_rule_signature(hard_rules),
            "blueprint_id": str((blueprint or {}).get("blueprint_id") or ""),
            "blueprint_version": int((blueprint or {}).get("version") or 0) or None,
            "warnings": list(review_response.get("warnings", []) or [])
            + list(proposal_response.get("warnings", []) or []),
        }

    async def _replace_pending_events(
        self,
        novel_id: str,
        chapter_id: str,
        proposals: List[Dict[str, Any]],
        *,
        generation_id: str = "",
    ) -> List[Dict[str, Any]]:
        """用最新一批候选替换该章节旧的待确认事件。

        Args:
            novel_id: 小说 ObjectId 字符串。
            chapter_id: 章节业务 ID。
            proposals: AI 返回的状态变更候选。
            generation_id: 产生这批候选的生成记录 ID。

        Returns:
            新写入的待确认事件列表。
        """
        previous = await story_event_repo.list_events(
            novel_id, chapter_id=chapter_id, status="pending"
        )
        for event in previous:
            # 重复审校时旧候选不应继续留在待确认列表里，标记拒绝并保留痕迹
            await story_event_repo.update_event_status(
                novel_id,
                event["event_id"],
                "rejected",
                note="已被新的审校结果取代",
            )

        valid_proposals = [
            item
            for item in proposals
            if str(item.get("entity_id") or "").strip()
            and str(item.get("after") or {})
        ]
        if not valid_proposals:
            return []

        items = [
            {
                "chapter_id": chapter_id,
                "entity_type": item.get("entity_type", "other"),
                "entity_id": item.get("entity_id", ""),
                "event_type": _map_event_type(item.get("change_type")),
                "before": item.get("before") or {},
                "after": item.get("after") or {},
                "evidence": {"text": item.get("evidence", "")},
                "confidence": item.get("confidence", 0.0),
                "status": "pending",
                "source": "ai",
                "generation_id": generation_id,
            }
            for item in valid_proposals
        ]
        return await story_event_repo.create_events(novel_id, items)

    # ------------------------------------------------------------------
    # 阶段二：用户确认后提交
    # ------------------------------------------------------------------
    async def commit(self, request: Any) -> Dict[str, Any]:
        """按用户确认结果执行定稿写入。

        Args:
            request: 含 novel_id、chapter_id、expected_version、accepted_event_ids、
                rejected_event_ids、ignored_issues、allow_partial 的请求。

        Returns:
            定稿结果，含更新后的章节、已应用变更与仍需人工处理的角色变更。

        Raises:
            NotFoundError: 章节不存在时抛出。
            InvalidIdError: 章节正文为空或版本号缺失时抛出。
            DuplicateKeyError: 章节已定稿或版本冲突时抛出。
        """
        chapter = await self.chapter_repo.get_chapter(request.chapter_id)
        if chapter.get("status") == "finalized":
            raise DuplicateKeyError("章节已定稿，如需修改请先重开为修改中")
        if not str(chapter.get("content") or "").strip():
            raise InvalidIdError("章节正文为空，无法定稿")

        current_version = int(chapter.get("version", 1))
        expected_version = getattr(request, "expected_version", None)
        if expected_version is None:
            raise InvalidIdError("缺少 expected_version，无法安全定稿")
        if int(expected_version) != current_version:
            raise DuplicateKeyError(
                f"章节已变更（服务器版本 {current_version}，提交版本 {expected_version}），请刷新后重试"
            )

        novel_id = request.novel_id
        chapter_biz_id = str(chapter.get("chapter_id") or "")
        accepted_ids = _clean_ids(getattr(request, "accepted_event_ids", None))
        rejected_ids = _clean_ids(getattr(request, "rejected_event_ids", None))
        if set(accepted_ids) & set(rejected_ids):
            raise InvalidIdError("同一条变更不能同时被接受与拒绝")

        # 增量新增：服务端复核审校依据，不信任客户端回传的 blocking_issues
        hard_rules = await self.list_hard_rules(novel_id)
        current_signature = _hard_rule_signature(hard_rules)
        requested_signature = str(getattr(request, "hard_rule_signature", "") or "").strip()
        if requested_signature and requested_signature != current_signature:
            raise DuplicateKeyError("硬规则已更新，请重新审校后再定稿")
        blueprint = await novel_blueprint_repo.find_confirmed(novel_id)
        current_blueprint_version = int((blueprint or {}).get("version") or 0) or None
        requested_blueprint_version = getattr(request, "blueprint_version", None)
        if requested_blueprint_version is not None and int(requested_blueprint_version) != (
            current_blueprint_version or 0
        ):
            raise DuplicateKeyError("创作蓝图已更新，请重新审校后再定稿")

        pending = await story_event_repo.list_events(
            novel_id, chapter_id=chapter_biz_id, status="pending"
        )
        pending_by_id = {event["event_id"]: event for event in pending}
        unknown = [eid for eid in accepted_ids + rejected_ids if eid not in pending_by_id]
        if unknown:
            raise InvalidIdError(f"存在不属于本章待确认列表的变更: {', '.join(unknown)}")

        # v2.0：定稿必须引用服务端保存的审校记录，阻断项由服务端重算
        review_generation_id = str(getattr(request, "review_generation_id", "") or "").strip()
        if not review_generation_id:
            raise ServiceError(
                "REVIEW_REQUIRED",
                "定稿必须引用服务端保存的一致性审校记录（review_generation_id）",
            )
        exceptions = [
            item
            for item in (getattr(request, "exceptions", None) or [])
            if isinstance(item, dict)
        ]
        if bool(getattr(request, "force", False)) and not exceptions:
            raise ServiceError(
                "FINALIZE_FORCE_REMOVED",
                "强制定稿开关已下线，请对每条阻断问题提交 exceptions（issue_id + reason）",
            )

        _record, issues = await self._load_review(
            novel_id, chapter_biz_id, review_generation_id, current_version
        )
        gate = self._evaluate_gate(issues, exceptions)

        applied, pending_character = await self._persist_finalize(
            chapter,
            novel_id,
            current_version=current_version,
            accepted_ids=accepted_ids,
            rejected_ids=rejected_ids,
            pending_by_id=pending_by_id,
            exceptions=gate["exceptions"],
            review_generation_id=review_generation_id,
            hard_rule_signature=current_signature,
            blueprint_version=current_blueprint_version,
        )

        updated = await self.chapter_repo.get_chapter(request.chapter_id)
        navigation = await self.chapter_repo.get_navigation(
            novel_id, int(chapter.get("number") or 0)
        )
        return {
            "chapter": updated,
            "accepted_count": len(accepted_ids),
            "rejected_count": len(rejected_ids),
            "applied_card_changes": [change.__dict__ for change in applied],
            "pending_character_updates": pending_character,
            "next_chapter": navigation.get("next"),
            "exceptions": gate["exceptions"],
            "hard_rule_signature": current_signature,
            "review_generation_id": review_generation_id,
            "message": "章节已定稿"
            + ("，仍有角色状态变更需在角色页确认" if pending_character else ""),
        }

    # ------------------------------------------------------------------
    # v2.0：统一门禁（AI 与非 AI 路径共用）
    # ------------------------------------------------------------------
    async def finalize_plain(
        self,
        novel_id: str,
        chapter_id: str,
        *,
        request_id: str = "",
        review_generation_id: str = "",
        exceptions: Optional[List[Dict[str, Any]]] = None,
        expected_version: Optional[int] = None,
        accepted_event_ids: Optional[List[str]] = None,
        rejected_event_ids: Optional[List[str]] = None,
        hard_rule_signature: str = "",
        blueprint_version: Optional[int] = None,
        actor: str = "local",
    ) -> Dict[str, Any]:
        """非 AI 路径的定稿入口，与 AI 提交共用同一套门禁。

        Args:
            novel_id: 小说 ObjectId 字符串。
            chapter_id: 章节 Mongo _id（与既有接口一致）。
            request_id: 客户端幂等键。
            review_generation_id: 服务端保存的一致性审校记录 ID，必填。
            exceptions: 逐项例外，形如 ``[{"issue_id": "...", "reason": "..."}]``。
            expected_version: 客户端基于的章节版本。
            accepted_event_ids: 用户接受的事实变化事件 ID。
            rejected_event_ids: 用户拒绝的事实变化事件 ID。
            hard_rule_signature: 客户端审校时的硬规则签名。
            blueprint_version: 客户端审校时的蓝图版本。
            actor: 定稿裁决人标识，本地版可为 ``local``。

        Returns:
            定稿结果，含操作 ID 与是否命中重放。

        Raises:
            ServiceError: 缺少审校记录、门禁未通过、版本冲突或章节已定稿。
        """
        if not str(request_id or "").strip():
            raise ServiceError("REQUEST_ID_REQUIRED", "定稿操作缺少幂等键 request_id")
        if not str(review_generation_id or "").strip():
            raise ServiceError(
                "REVIEW_REQUIRED",
                "定稿必须引用服务端保存的一致性审校记录（review_generation_id）",
            )

        request_payload = {
            "novel_id": str(novel_id),
            "chapter_id": str(chapter_id),
            "review_generation_id": str(review_generation_id),
            "exceptions": exceptions or [],
            "expected_version": expected_version,
            "accepted_event_ids": accepted_event_ids or [],
            "rejected_event_ids": rejected_event_ids or [],
        }

        async def _executor(handle) -> Dict[str, Any]:
            return await self._finalize_plain_inner(
                novel_id,
                chapter_id,
                operation_id=handle.operation_id,
                review_generation_id=review_generation_id,
                exceptions=exceptions or [],
                expected_version=expected_version,
                accepted_event_ids=accepted_event_ids or [],
                rejected_event_ids=rejected_event_ids or [],
                hard_rule_signature=hard_rule_signature,
                blueprint_version=blueprint_version,
                actor=actor,
            )

        outcome = await run_operation(
            novel_id=novel_id,
            operation_type="chapter_finalize",
            request_id=request_id,
            request_payload=request_payload,
            target={"chapter_id": str(chapter_id), "review": str(review_generation_id)},
            executor=_executor,
            actor=actor,
        )
        body = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
        body["operation_id"] = outcome.get("operation_id")
        body["replayed"] = bool(outcome.get("replayed"))
        return body

    async def _finalize_plain_inner(
        self,
        novel_id: str,
        chapter_id: str,
        *,
        operation_id: str,
        review_generation_id: str,
        exceptions: List[Dict[str, Any]],
        expected_version: Optional[int],
        accepted_event_ids: List[str],
        rejected_event_ids: List[str],
        hard_rule_signature: str,
        blueprint_version: Optional[int],
        actor: str = "local",
    ) -> Dict[str, Any]:
        """在已认领操作的前提下执行定稿写入。"""
        chapter = await self.chapter_repo.get_chapter(chapter_id)
        if chapter.get("status") == "finalized":
            raise ServiceError("CHAPTER_FINALIZED", "章节已定稿，如需修改请先重开为修改中")
        if not str(chapter.get("content") or "").strip():
            raise ServiceError("FINALIZE_NO_RULES", "章节正文为空，无法定稿")

        current_version = int(chapter.get("version", 1))
        if expected_version is not None and int(expected_version) != current_version:
            raise ServiceError(
                "VERSION_STALE",
                f"章节已变更（服务器版本 {current_version}，提交版本 {expected_version}），请刷新后重试",
            )

        chapter_biz_id = str(chapter.get("chapter_id") or "")
        _record, issues = await self._load_review(
            novel_id, chapter_biz_id, review_generation_id, current_version
        )
        hard_rules = await self.list_hard_rules(novel_id)
        current_signature = _hard_rule_signature(hard_rules)
        if hard_rule_signature and hard_rule_signature != current_signature:
            raise ServiceError("REVIEW_INVALID", "硬规则已更新，请重新审校后再定稿")
        blueprint = await novel_blueprint_repo.find_confirmed(novel_id)
        current_blueprint_version = int((blueprint or {}).get("version") or 0) or None
        if blueprint_version is not None and int(blueprint_version) != (
            current_blueprint_version or 0
        ):
            raise ServiceError("REVIEW_INVALID", "创作蓝图已更新，请重新审校后再定稿")

        gate = self._evaluate_gate(issues, exceptions)
        accepted_ids = _clean_ids(accepted_event_ids)
        rejected_ids = _clean_ids(rejected_event_ids)
        if set(accepted_ids) & set(rejected_ids):
            raise ServiceError("CANDIDATE_SCOPE_MISMATCH", "同一条变更不能同时被接受与拒绝")

        pending = await story_event_repo.list_events(
            novel_id, chapter_id=chapter_biz_id, status="pending"
        )
        pending_by_id = {event["event_id"]: event for event in pending}
        unknown = [eid for eid in accepted_ids + rejected_ids if eid not in pending_by_id]
        if unknown:
            raise ServiceError(
                "CANDIDATE_SCOPE_MISMATCH",
                f"存在不属于本章待确认列表的变更: {', '.join(unknown)}",
            )

        applied, pending_character = await self._persist_finalize(
            chapter,
            novel_id,
            current_version=current_version,
            accepted_ids=accepted_ids,
            rejected_ids=rejected_ids,
            pending_by_id=pending_by_id,
            exceptions=gate["exceptions"],
            review_generation_id=review_generation_id,
            hard_rule_signature=current_signature,
            blueprint_version=current_blueprint_version,
            operation_id=operation_id,
            actor=actor,
        )
        updated = await self.chapter_repo.get_chapter(chapter_id)
        return {
            "chapter": updated,
            "accepted_count": len(accepted_ids),
            "rejected_count": len(rejected_ids),
            "applied_card_changes": [change.__dict__ for change in applied],
            "pending_character_updates": pending_character,
            "exceptions": gate["exceptions"],
            "hard_rule_signature": current_signature,
            "review_generation_id": review_generation_id,
            "message": "章节已定稿"
            + ("，仍有角色状态变更需在角色页确认" if pending_character else ""),
            "result_refs": {
                "chapter_id": chapter_biz_id,
                "review_generation_id": review_generation_id,
                "version": int(updated.get("version") or current_version),
            },
        }

    async def _load_review(
        self,
        novel_id: str,
        chapter_biz_id: str,
        review_generation_id: str,
        current_version: int,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """读取并校验定稿引用的审校记录，返回带稳定 ID 的问题列表。

        Raises:
            ServiceError: 记录不存在、类型不符、章节不符或版本过期。
        """
        record = await generation_record_repo.get_owned_record(novel_id, review_generation_id)
        kind = str(record.get("kind") or "")
        action_type = str(record.get("action_type") or "")
        if kind not in ("consistency_review",) and action_type not in ("consistency_review",):
            raise ServiceError("REVIEW_INVALID", "定稿引用的审校记录类型不正确")
        record_chapter = str(record.get("chapter_id") or "") or str(
            (record.get("scope") or {}).get("chapter_id") or ""
        )
        if record_chapter and record_chapter != chapter_biz_id:
            raise ServiceError("REVIEW_INVALID", "审校记录不属于当前章节")
        record_version = int(
            record.get("chapter_version")
            or (record.get("input_snapshot") or {}).get("chapter_version")
            or 0
        )
        if record_version and record_version != current_version:
            raise ServiceError(
                "REVIEW_INVALID", "审校结果基于旧章节版本，请重新审校后再定稿"
            )
        issues = (record.get("result") or {}).get("data", {}).get("issues") or []
        if not issues:
            payload_result = (record.get("payload") or {}).get("result") or {}
            issues = (payload_result.get("data") or {}).get("issues") or []
        return record, [self._annotate_issue(issue, index) for index, issue in enumerate(issues)]

    @staticmethod
    def _annotate_issue(issue: Dict[str, Any], index: int) -> Dict[str, Any]:
        """为审校问题生成稳定的 issue_id，供前端逐项裁决。"""
        if not isinstance(issue, dict):
            return {"issue_id": f"iss_{index:04d}", "severity": "warning", "content": str(issue)}
        seed = "|".join(
            str(issue.get(key) or "")
            for key in ("severity", "category", "start", "end", "content", "text")
        )
        digest = hashlib.sha1(f"{index}:{seed}".encode("utf-8")).hexdigest()[:12]
        annotated = dict(issue)
        annotated["issue_id"] = str(issue.get("issue_id") or f"iss_{digest}")
        annotated.setdefault("severity", "warning")
        return annotated

    def _evaluate_gate(
        self,
        issues: List[Dict[str, Any]],
        exceptions: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """按服务端问题列表评估门禁。

        Args:
            issues: 服务端审校问题（已带 issue_id）。
            exceptions: 客户端提交的逐项例外。

        Returns:
            ``{"blocking": [...], "exceptions": [...], "unresolved": [...]}``。

        Raises:
            ServiceError: 例外不合法或仍存在未裁决的阻断项。
        """
        normalized: List[Dict[str, Any]] = []
        by_id = {str(item.get("issue_id") or ""): item for item in issues}
        for item in exceptions or []:
            issue_id = str(item.get("issue_id") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if not issue_id:
                raise ServiceError("FINALIZE_BLOCKED", "例外缺少 issue_id")
            if issue_id not in by_id:
                raise ServiceError("FINALIZE_BLOCKED", f"例外引用了不存在的问题: {issue_id}")
            if not reason:
                raise ServiceError("FINALIZE_BLOCKED", f"问题 {issue_id} 的例外缺少原因说明")
            if str(by_id[issue_id].get("severity") or "") != "blocking":
                raise ServiceError(
                    "FINALIZE_BLOCKED", f"问题 {issue_id} 不是阻断级问题，无需提交例外"
                )
            normalized.append(
                {
                    "issue_id": issue_id,
                    "reason": reason,
                    "scope": str(item.get("scope") or "this_chapter"),
                    "severity": "blocking",
                    "content": str(by_id[issue_id].get("content") or ""),
                }
            )

        blocking = [
            {
                "issue_id": str(item.get("issue_id") or ""),
                "severity": "blocking",
                "category": str(item.get("category") or ""),
                "content": str(item.get("content") or item.get("text") or ""),
                "reason_required": True,
            }
            for item in issues
            if str(item.get("severity") or "") == "blocking"
        ]
        covered = {item["issue_id"] for item in normalized}
        unresolved = [item for item in blocking if item["issue_id"] not in covered]
        if unresolved:
            raise ServiceError(
                "FINALIZE_BLOCKED",
                f"存在 {len(unresolved)} 条未裁决的阻断级问题，请逐项确认后再定稿",
                details={"blocking_issues": unresolved},
            )
        return {"blocking": blocking, "exceptions": normalized, "unresolved": unresolved}

    async def _persist_finalize(
        self,
        chapter: Dict[str, Any],
        novel_id: str,
        *,
        current_version: int,
        accepted_ids: List[str],
        rejected_ids: List[str],
        pending_by_id: Dict[str, Dict[str, Any]],
        exceptions: List[Dict[str, Any]],
        review_generation_id: str,
        hard_rule_signature: str,
        blueprint_version: Optional[int],
        operation_id: str = "",
        actor: str = "local",
    ) -> tuple[List[_AppliedChange], List[Dict[str, Any]]]:
        """在（可选）事务内完成定稿写入，并写一条内容版本快照。"""
        from backend.services.novel.entity_version_service import entity_version_service

        chapter_mongo_id = str(chapter.get("_id"))
        chapter_biz_id = str(chapter.get("chapter_id") or "")

        async def _write(session):
            applied_changes: List[_AppliedChange] = []
            character_changes: List[Dict[str, Any]] = []
            for event_id in accepted_ids:
                event = pending_by_id[event_id]
                if event.get("entity_type") in {"location", "item", "rule"}:
                    change = await self._apply_card_change(
                        novel_id, event, chapter_biz_id, session=session
                    )
                    if change:
                        applied_changes.append(change)
                elif event.get("entity_type") == "character":
                    character_changes.append(
                        {
                            "event_id": event_id,
                            "entity_id": event.get("entity_id", ""),
                            "before": event.get("before", {}),
                            "after": event.get("after", {}),
                            "evidence": (event.get("evidence") or {}).get("text", ""),
                        }
                    )
                await story_event_repo.update_event_status(
                    novel_id, event_id, "accepted", session=session
                )
            for event_id in rejected_ids:
                await story_event_repo.update_event_status(
                    novel_id, event_id, "rejected", session=session
                )
            await self.chapter_repo.update_chapter(
                chapter_mongo_id,
                {"status": "finalized", "state_change_proposals": []},
                expected_version=current_version,
                session=session,
            )
            return applied_changes, character_changes

        applied, pending_character = await run_mongo_write_unit(_write, "finalize_chapter")

        # 定稿决策留痕：写入章节文档，便于事后审计"谁放行了什么"
        await self.chapter_repo.collection.update_one(
            {"_id": chapter["_id"]},
            {
                "$push": {
                    "finalize_decisions": {
                        "chapter_version": current_version,
                        "review_generation_id": review_generation_id,
                        "hard_rule_signature": hard_rule_signature,
                        "blueprint_version": blueprint_version,
                        "exceptions": exceptions,
                        "operation_id": operation_id,
                        "actor": actor,
                        "created_at": get_utc_now(),
                    }
                }
            },
        )
        updated = await self.chapter_repo.get_chapter(chapter_mongo_id)
        await entity_version_service.snapshot_chapter(
            novel_id,
            updated,
            operation_type="finalize",
            source="finalize",
            summary=f"定稿，例外 {len(exceptions)} 条",
            operation_id=operation_id,
        )
        return applied, pending_character

    async def _apply_card_change(
        self,
        novel_id: str,
        event: Dict[str, Any],
        chapter_id: str,
        *,
        session=None,
    ) -> Optional[_AppliedChange]:
        """把一条已接受的事件应用到正式卡片。

        Args:
            novel_id: 小说 ObjectId 字符串。
            event: 已接受的事件文档。
            chapter_id: 来源章节业务 ID，写入状态历史。
            session: 可选 MongoDB 会话。

        Returns:
            应用成功时返回变更记录；卡片不存在或没有可写字段时返回 None。
        """
        card_id = str(event.get("entity_id") or "")
        warnings: List[str] = []
        try:
            card = await self.card_repo.get_card_by_business_id(novel_id, card_id)
        except NotFoundError:
            warnings.append(f"事件引用的卡片已不存在: {card_id}")
            logger.warning("定稿应用变更时卡片不存在 card_id=%s", card_id)
            return None

        card_type = str(card.get("type") or "")
        schema_fields = set(CARD_TYPES.get(card_type, {}).get("fields", {}).keys())
        fields = dict(card.get("fields") or {})
        new_state = str(card.get("current_state") or "")
        changed_fields: Dict[str, str] = {}

        for key, value in (event.get("after") or {}).items():
            text = str(value).strip()
            if not text:
                continue
            if key == STATE_FIELD:
                new_state = text
                continue
            if key in schema_fields:
                fields[key] = text
                changed_fields[key] = text
                continue
            warnings.append(f"字段 {key} 不属于{card_type}卡，已忽略")

        state_changed = new_state != str(card.get("current_state") or "")
        if not changed_fields and not state_changed:
            if warnings:
                return _AppliedChange(card_id, card_type, warnings=warnings)
            return None

        payload: Dict[str, Any] = {
            "current_state": new_state,
            "fields": fields,
            # 由 Repository 追加 state_history，并记录来源章节与依据
            "_state_note": (event.get("evidence") or {}).get("text", ""),
            "_state_chapter": chapter_id,
        }
        await self.card_repo.update_card(
            novel_id,
            str(card.get("_id")),
            payload,
            expected_version=card.get("version", 1),
            session=session,
        )
        return _AppliedChange(
            card_id=card_id,
            card_type=card_type,
            current_state=new_state if state_changed else "",
            fields=changed_fields,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # 硬规则校验辅助
    # ------------------------------------------------------------------
    async def list_hard_rules(self, novel_id: str) -> List[Dict[str, Any]]:
        """列出当前生效的硬规则，供前端定稿前展示。"""
        cards = await self.card_repo.list_cards(novel_id, card_type="rule", enabled_only=True)
        return [card for card in cards if coerce_hard_rule_flag(card.get("is_hard_rule"))]


def _hard_rule_signature(rules: List[Dict[str, Any]]) -> str:
    """计算硬规则集合的稳定签名：任一规则版本变化都会导致签名变化。"""
    payload = json.dumps(
        sorted(
            [str(card.get("card_id") or ""), int(card.get("version") or 1)]
            for card in rules
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _clean_ids(values: Any) -> List[str]:
    """清理业务 ID 数组：去空、去重、保持顺序。"""
    if not values:
        return []
    out: List[str] = []
    for item in values:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _map_event_type(change_type: Any) -> str:
    """把 AI 给出的变化类型映射到事件类型枚举。"""
    text = str(change_type or "").strip()
    allowed = {
        "state_change",
        "transfer",
        "discover",
        "use",
        "damage",
        "repair",
        "destroy",
        "seal",
        "unseal",
        "lose",
        "recover",
        "move",
        "relationship",
        "create",
        "other",
    }
    return text if text in allowed else "other"


finalize_chapter_service = FinalizeChapterService()
