"""正文修改建议 AI 服务。

统一约定：
- AI 只产出待确认的修改建议，写入 text_revisions 与 generation_records；
- 建议必须绑定章节业务 ID、生成时的章节版本与目标范围，确认时做精确匹配；
- 本次只支持 supplement / correction / terminology 三类，不做大范围正文整改；
- 用户确认后才由章节服务写回正文，AI 从不直接改正文。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from backend.api.error_contract import ServiceError
from backend.db.errors import DuplicateKeyError, NotFoundError
from backend.db.repositories.chapter_repository import ChapterRepository
from backend.db.repositories.novel_repository import novel_repo
from backend.db.repositories.setting_card_repository import SettingCardRepository
from backend.db.repositories.text_revision_repository import text_revision_repo
from backend.db.utils import content_hash
from backend.llm.prompts.prompt_selector import get_prompt_section
from backend.llm.schemas.setting_card_pydantic import TextRevisionResultSchema
from backend.services.llm.chapter_context_service import chapter_context_service
from backend.services.llm.generation_support import (
    resolve_runtime,
    save_record,
)
from backend.services.novel.candidate_accept_service import run_operation
from backend.services.llm.workflow_service import (
    build_generation_kwargs,
    generate_structured_result,
)

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "text_revision_ai"

# 本次支持的修改类型
REVISION_TYPES = ("supplement", "correction", "terminology")
# 影响分析单章最多返回的命中位置数，以及最多返回的受影响章节数
MAX_IMPACT_MATCHES = 5
MAX_IMPACT_CHAPTERS = 50


def _safe_chapter_number(chapter: Dict[str, Any]) -> int:
    """读取章节序号，非法值按 0 处理。"""
    try:
        return int(chapter.get("number") or 0)
    except (TypeError, ValueError):
        return 0


class TextRevisionService:
    """正文修改建议 AI 服务。"""

    def __init__(self) -> None:
        self.chapter_repo = ChapterRepository()
        self.card_repo = SettingCardRepository()

    @staticmethod
    def _validate_revision_type(value: str) -> str:
        """校验修改类型。"""
        text = str(value or "").strip().lower()
        if text not in REVISION_TYPES:
            raise ValueError(
                f"不支持的修改类型: {value}，支持 supplement/correction/terminology"
            )
        return text

    async def generate(
        self,
        request: Any,
    ) -> Dict[str, Any]:
        """为指定章节生成正文修改建议候选。

        只针对请求给定的范围生成建议，不做整章或整本整改。

        Args:
            request: 请求模型，需包含 novel_id、chapter_id、revision_type、
                target_range、card_changes 等字段。

        Returns:
            包含建议列表的候选响应体。

        Raises:
            ValueError: 修改类型非法。
            NotFoundError: 章节不存在或不属于该小说。
        """
        novel_id = str(request.novel_id)
        chapter_id = str(getattr(request, "chapter_id", "") or "").strip()
        if not chapter_id:
            raise ValueError("生成正文修改建议必须指定章节业务 ID")
        revision_type = self._validate_revision_type(getattr(request, "revision_type", ""))

        # 先校验章节归属，避免把建议挂到别的章节上
        chapter = await self.chapter_repo.get_chapter(chapter_id)
        if str(chapter.get("novel_id") or "") != novel_id:
            raise ValueError(f"章节 {chapter_id} 不属于该小说")

        novel = await novel_repo.get_novel_by_id(novel_id)
        context = await chapter_context_service.build(
            novel, chapter, user_prompt=getattr(request, "instruction", "") or ""
        )
        content = str(chapter.get("content") or "")
        target_range = getattr(request, "target_range", None) or {}
        start = int(target_range.get("start") or 0)
        end = int(target_range.get("end") or 0)
        target_text = str(getattr(request, "target_text", "") or "").strip()
        if not target_text and end > start:
            target_text = content[start:end]

        prompts = get_prompt_section("generate_text_revision")
        prompt = (
            prompts["generate_text_revision_prompt_base"].format(
                context_text=context.text,
                content=content,
                revision_type=revision_type,
                target_range=f"start={start}, end={end}",
                target_text=target_text or "（未指定，请按上下文推断最小范围）",
                card_changes_json=json.dumps(
                    getattr(request, "card_changes", None) or [],
                    ensure_ascii=False,
                    default=str,
                ),
                instruction=str(getattr(request, "instruction", "") or "").strip() or "（无）",
            )
            + "\n"
            + prompts["generate_text_revision_prompt_without_schema_suffix"]
        ).strip()

        service, provider, use_json_schema, _stream = resolve_runtime(
            request, WORKFLOW_NAME, "generate_text_revision"
        )
        result = await generate_structured_result(
            service,
            prompt,
            TextRevisionResultSchema,
            "generate_text_revision",
            gen_kwargs=build_generation_kwargs(request),
            use_json_schema=use_json_schema,
        )

        chapter_version = int(chapter.get("version") or 1)
        warnings: List[str] = []
        revisions: List[Dict[str, Any]] = []
        for item in result.revisions:
            before_text = str(item.before_text or "").strip()
            after_text = str(item.after_text or "").strip()
            if not before_text or not after_text:
                warnings.append("忽略了一条缺少原文或建议文本的修改建议")
                continue
            # 精确范围或唯一文本锚点定位，避免原文重复出现时 find 命中错误位置
            located = self._locate_generation_range(content, before_text, target_range)
            if located is None:
                warnings.append("忽略了一条无法在正文中唯一定位的修改建议，请指定精确范围")
                continue
            start, end = located
            saved = await text_revision_repo.create_revision(
                novel_id,
                {
                    "chapter_id": chapter_id,
                    "chapter_version": chapter_version,
                    "target_range": {"start": start, "end": end},
                    "before_text": before_text,
                    "after_text": after_text,
                    "revision_type": self._validate_revision_type(item.revision_type),
                    "reason": str(item.reason or ""),
                    "card_ids": list(getattr(request, "card_ids", None) or []),
                    "status": "pending",
                },
            )
            revisions.append({**saved, "warnings": list(item.warnings or [])})

        if not revisions:
            warnings.append("AI 未产出可用的修改建议，请缩小范围或补充要求后重试")

        return await save_record(
            novel_id,
            "generate_text_revision",
            chapter_id=chapter_id,
            snapshot=context.snapshot,
            data={"revisions": revisions},
            provider=provider,
            warnings=warnings,
            conflicts=[],
            chapter_version=chapter_version,
        )

    async def analyze_impact(self, request: Any) -> Dict[str, Any]:
        """按卡片定位受影响的章节与正文范围（确定性文本扫描，不调用 AI）。

        卡片改名或状态变化后，先列出正文里仍引用旧名称/旧设定的章节，
        再让用户逐处生成修改建议；本方法只做定位，不产出也不写入任何建议。

        Args:
            request: 请求模型，需包含 novel_id 与 card_ids。

        Returns:
            含命中章节列表、卡片摘要与告警的影响分析结果。

        Raises:
            ValueError: 未提供卡片业务 ID。
        """
        novel_id = str(getattr(request, "novel_id", "") or "")
        card_ids = [
            str(item).strip()
            for item in (getattr(request, "card_ids", None) or [])
            if str(item).strip()
        ]
        if not card_ids:
            raise ValueError("正文影响分析必须指定至少一个卡片业务 ID")

        warnings: List[str] = []
        cards: List[Dict[str, Any]] = []
        for card_id in card_ids:
            try:
                card = await self.card_repo.get_card_by_business_id(novel_id, card_id)
            except NotFoundError:
                warnings.append(f"卡片不存在或不属于该小说: {card_id}")
                continue
            tokens: List[str] = []
            for token in [str(card.get("name") or "")] + [
                str(alias) for alias in (card.get("aliases") or [])
            ]:
                text = token.strip()
                if text and text not in tokens:
                    tokens.append(text)
            cards.append(
                {
                    "card_id": str(card.get("card_id") or card_id),
                    "name": str(card.get("name") or ""),
                    "type": str(card.get("type") or ""),
                    "version": int(card.get("version") or 1),
                    "current_state": str(card.get("current_state") or ""),
                    "tokens": tokens,
                }
            )
        if not cards:
            return {
                "novel_id": novel_id,
                "cards": [],
                "chapters": [],
                "total_chapters": 0,
                "warnings": warnings,
            }

        chapters = await self.chapter_repo.list_chapters(novel_id)
        hits: List[Dict[str, Any]] = []
        for chapter in sorted(chapters or [], key=_safe_chapter_number):
            content = str(chapter.get("content") or "")
            if not content:
                continue
            chapter_hits: List[Dict[str, Any]] = []
            for card in cards:
                matches: List[Dict[str, Any]] = []
                for token in card["tokens"]:
                    start = content.find(token)
                    while start >= 0 and len(matches) < MAX_IMPACT_MATCHES:
                        matches.append(
                            {"start": start, "end": start + len(token), "text": token}
                        )
                        start = content.find(token, start + len(token))
                    if len(matches) >= MAX_IMPACT_MATCHES:
                        break
                if matches:
                    chapter_hits.append(
                        {
                            "card_id": card["card_id"],
                            "card_name": card["name"],
                            "card_type": card["type"],
                            "hit_count": len(matches),
                            "matches": matches,
                        }
                    )
            if chapter_hits:
                hits.append(
                    {
                        "chapter_id": str(chapter.get("chapter_id") or ""),
                        "chapter_number": chapter.get("number"),
                        "title": str(chapter.get("title") or ""),
                        "chapter_version": int(chapter.get("version") or 1),
                        "impacts": chapter_hits,
                    }
                )
            if len(hits) >= MAX_IMPACT_CHAPTERS:
                warnings.append(
                    f"受影响章节过多，仅返回前 {MAX_IMPACT_CHAPTERS} 章"
                )
                break

        if not hits:
            warnings.append("正文中未找到引用这些卡片的章节")
        return {
            "novel_id": novel_id,
            "cards": cards,
            "chapters": hits,
            "total_chapters": len(hits),
            "warnings": warnings,
        }

    @staticmethod
    def _locate_generation_range(
        content: str,
        before_text: str,
        target_range: Dict[str, Any],
    ) -> tuple[int, int] | None:
        """生成建议时定位原文范围，避免用 ``find`` 命中错误位置。

        定位优先级：请求给定的精确范围（内容匹配时）优先；否则用唯一文本锚点。
        原文出现多次且没有精确范围时返回 None，由调用方跳过该条建议。

        Args:
            content: 章节当前正文。
            before_text: AI 返回的原文片段。
            target_range: 请求指定的 ``{start, end}``，可为空。

        Returns:
            (start, end) 半开区间；无法唯一定位时返回 None。
        """
        start = int(target_range.get("start") or 0)
        end = int(target_range.get("end") or 0)
        # 精确范围优先：范围合法且内容匹配时直接采用，不依赖文本搜索
        if 0 <= start < end <= len(content) and content[start:end] == before_text:
            return start, end
        # 退化为唯一文本锚点：原文必须在正文中恰好出现一次
        positions: List[int] = []
        index = content.find(before_text)
        while index >= 0:
            positions.append(index)
            index = content.find(before_text, index + 1)
        if len(positions) != 1:
            return None
        position = positions[0]
        return position, position + len(before_text)

    @staticmethod
    def _resolve_range(
        content: str,
        revision: Dict[str, Any],
        *,
        occurrence: Optional[int] = None,
    ) -> tuple[int, int]:
        """解析建议要作用的正文范围，优先使用精确范围，其次用唯一文本锚点。

        Args:
            content: 章节当前正文。
            revision: 修改建议文档。
            occurrence: 原文多次出现时指定第几次出现，从 0 开始。

        Returns:
            (start, end) 半开区间。

        Raises:
            ServiceError: 范围缺失、原文不可定位或原文不唯一。
        """
        target_range = revision.get("target_range") or {}
        start = target_range.get("start")
        end = target_range.get("end")
        before_text = str(revision.get("before_text") or "")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start <= end <= len(content)
        ):
            if not before_text or content[start:end] == before_text:
                return start, end
        if not before_text:
            raise ServiceError(
                "TEXT_ANCHOR_INVALID", "建议缺少可定位的原文或精确范围，无法安全应用"
            )
        positions: List[int] = []
        index = content.find(before_text)
        while index >= 0:
            positions.append(index)
            index = content.find(before_text, index + 1)
        if not positions:
            raise ServiceError(
                "TEXT_ANCHOR_INVALID", "建议原文无法在当前正文中定位，请重新生成建议"
            )
        if len(positions) > 1 and occurrence is None:
            raise ServiceError(
                "TEXT_ANCHOR_NOT_UNIQUE",
                f"建议原文在正文中出现 {len(positions)} 次，请指定 occurrence 或改用精确范围",
                details={"count": len(positions), "positions": positions[:10]},
            )
        chosen = occurrence if occurrence is not None else 0
        if chosen < 0 or chosen >= len(positions):
            raise ServiceError(
                "TEXT_ANCHOR_INVALID",
                f"occurrence {chosen} 超出范围（共 {len(positions)} 处）",
            )
        position = positions[chosen]
        return position, position + len(before_text)

    @staticmethod
    def _apply_operation(
        content: str,
        start: int,
        end: int,
        *,
        operation: str,
        text: str,
    ) -> str:
        """按操作类型生成新正文。

        Args:
            content: 原正文。
            start: 起始下标。
            end: 结束下标（不含）。
            operation: replace / insert / delete。
            text: 目标文本，delete 时忽略。

        Returns:
            新正文。

        Raises:
            ServiceError: 操作类型非法。
        """
        if operation == "replace":
            return content[:start] + text + content[end:]
        if operation == "insert":
            return content[:start] + text + content[start:]
        if operation == "delete":
            return content[:start] + content[end:]
        raise ServiceError(
            "TEXT_OPERATION_INVALID",
            f"不支持的正文操作: {operation}，支持 replace/insert/delete",
        )

    async def apply(
        self,
        novel_id: str,
        revision_id: str,
        *,
        chapter_id: str = "",
        after_text: Optional[str] = None,
        request_id: str = "",
        expected_chapter_version: Optional[int] = None,
        expected_content_hash: str = "",
        operation: str = "replace",
        occurrence: Optional[int] = None,
    ) -> Dict[str, Any]:
        """确认一条修改建议并写回章节正文，走统一提交协议。

        写回前校验：建议归属、路径章节一致、状态为 pending、章节版本与正文哈希未变化、
        作用范围可精确定位。任一校验失败都不写正文。

        Args:
            novel_id: 小说 ObjectId 字符串。
            revision_id: 建议业务 ID。
            chapter_id: 路径章节 ID，用于归属校验。
            after_text: 用户编辑后的文本；为空时使用建议原文。
            request_id: 客户端幂等键，必填。
            expected_chapter_version: 客户端基于的章节版本。
            expected_content_hash: 客户端基于的正文哈希。
            operation: replace / insert / delete。
            occurrence: 原文多次出现时指定第几次出现。

        Returns:
            包含更新后章节、建议状态、操作 ID 与是否命中重放的字典。

        Raises:
            ServiceError: 幂等冲突、版本不一致、范围不可定位或操作非法。
            NotFoundError: 建议不存在或不属于该章节。
        """
        operation = str(operation or "replace").strip().lower() or "replace"
        if operation not in ("replace", "insert", "delete"):
            raise ServiceError(
                "TEXT_OPERATION_INVALID",
                f"不支持的正文操作: {operation}，支持 replace/insert/delete",
            )
        if not str(request_id or "").strip():
            raise ServiceError(
                "REQUEST_ID_REQUIRED", "确认修改建议缺少幂等键 request_id"
            )

        request_payload = {
            "novel_id": str(novel_id),
            "revision_id": revision_id,
            "chapter_id": str(chapter_id or ""),
            "after_text": after_text,
            "operation": operation,
            "occurrence": occurrence,
            "expected_chapter_version": expected_chapter_version,
        }

        async def _executor(handle) -> Dict[str, Any]:
            return await self._apply_with_operation(
                novel_id,
                revision_id,
                handle.operation_id,
                chapter_id=chapter_id,
                after_text=after_text,
                expected_chapter_version=expected_chapter_version,
                expected_content_hash=expected_content_hash,
                operation=operation,
                occurrence=occurrence,
            )

        outcome = await run_operation(
            novel_id=novel_id,
            operation_type="text_revision_apply",
            request_id=request_id,
            request_payload=request_payload,
            target={"revision_id": revision_id, "chapter_id": str(chapter_id or "")},
            executor=_executor,
        )
        body = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
        body["operation_id"] = outcome.get("operation_id")
        body["replayed"] = bool(outcome.get("replayed"))
        return body

    async def _apply_with_operation(
        self,
        novel_id: str,
        revision_id: str,
        operation_id: str,
        *,
        chapter_id: str,
        after_text: Optional[str],
        expected_chapter_version: Optional[int],
        expected_content_hash: str,
        operation: str,
        occurrence: Optional[int],
    ) -> Dict[str, Any]:
        """在已认领操作的前提下把建议写回正文。"""
        from backend.services.novel.entity_version_service import entity_version_service

        revision = await text_revision_repo.get_revision(novel_id, revision_id)
        # 路径章节必须与该建议所属章节一致，避免跨章节误写
        if chapter_id and str(revision.get("chapter_id") or "") != str(chapter_id):
            raise NotFoundError(f"修改建议 {revision_id} 不属于章节 {chapter_id}")
        if str(revision.get("status") or "") != "pending":
            raise ServiceError(
                "CANDIDATE_ALREADY_ACCEPTED", "该修改建议已处理，请勿重复确认"
            )

        target_chapter_id = str(revision.get("chapter_id") or "")
        chapter = await self.chapter_repo.get_chapter(target_chapter_id)
        if str(chapter.get("novel_id") or "") != novel_id:
            raise ServiceError(
                "CANDIDATE_SCOPE_MISMATCH", f"章节 {target_chapter_id} 不属于该小说"
            )

        chapter_version = int(chapter.get("version") or 1)
        if expected_chapter_version is not None:
            if int(expected_chapter_version) != chapter_version:
                raise ServiceError(
                    "VERSION_STALE",
                    f"章节已更新（服务端 v{chapter_version}，请求基于 v{expected_chapter_version}），请刷新后重试",
                )
        if chapter_version != int(revision.get("chapter_version") or 0):
            raise ServiceError(
                "VERSION_STALE",
                "章节正文已更新，该建议基于旧版本生成，请重新生成后再确认",
            )

        content = str(chapter.get("content") or "")
        if expected_content_hash:
            if content_hash(content) != str(expected_content_hash).strip().lower():
                raise ServiceError(
                    "VERSION_STALE", "正文内容已变化，请刷新后重新确认该建议"
                )

        start, end = self._resolve_range(content, revision, occurrence=occurrence)
        final_text = str(
            after_text if after_text is not None else revision.get("after_text") or ""
        )
        if operation != "delete" and not final_text.strip():
            raise ServiceError("TEXT_ANCHOR_INVALID", "建议文本为空，无法应用")

        new_content = self._apply_operation(
            content, start, end, operation=operation, text=final_text
        )
        try:
            updated = await self.chapter_repo.update_chapter(
                target_chapter_id,
                {"content": new_content},
                expected_version=chapter_version,
            )
        except DuplicateKeyError as exc:
            raise ServiceError("VERSION_STALE", str(exc)) from exc

        # 正式写回成功后才把建议标记为 accepted
        await text_revision_repo.update_status(novel_id, revision_id, "accepted")
        # 同章节中基于旧版本的其它待处理建议标记为冲突，避免用户误用过期建议
        conflicted = await text_revision_repo.mark_conflicted_by_chapter(
            novel_id, target_chapter_id, int(updated.get("version") or chapter_version)
        )
        snapshot = await entity_version_service.snapshot_chapter(
            novel_id,
            updated,
            operation_type=f"text_revision_{operation}",
            source="text_revision",
            summary=str(revision.get("reason") or ""),
            operation_id=operation_id,
        )
        result = {
            "chapter": updated,
            "revision": await text_revision_repo.get_revision(novel_id, revision_id),
            "conflicted_count": conflicted,
            "applied_range": {"start": start, "end": end},
            "operation": operation,
            "content_version": updated.get("version"),
            "current_revision_id": str(snapshot.get("revision_id") or ""),
            "candidate_id": revision_id,
            "candidate_status": "accepted",
        }
        result["result_refs"] = {
            "revision_id": revision_id,
            "chapter_id": target_chapter_id,
            "content_version": updated.get("version"),
            "current_revision_id": str(snapshot.get("revision_id") or ""),
            "applied_range": {"start": start, "end": end},
        }
        return result


text_revision_service = TextRevisionService()
