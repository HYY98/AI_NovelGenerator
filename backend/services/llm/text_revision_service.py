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
from typing import Any, Dict, List

from backend.db.errors import DuplicateKeyError, NotFoundError
from backend.db.repositories.chapter_repository import ChapterRepository
from backend.db.repositories.novel_repository import novel_repo
from backend.db.repositories.setting_card_repository import SettingCardRepository
from backend.db.repositories.text_revision_repository import text_revision_repo
from backend.llm.prompts.prompt_selector import get_prompt_section
from backend.llm.schemas.setting_card_pydantic import TextRevisionResultSchema
from backend.services.llm.chapter_context_service import chapter_context_service
from backend.services.llm.generation_support import (
    resolve_runtime,
    save_record,
)
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
            # before_text 必须能在正文中精确定位，否则无法安全替换
            if before_text not in content:
                warnings.append("忽略了一条无法在正文中精确定位的修改建议")
                continue
            position = content.find(before_text)
            saved = await text_revision_repo.create_revision(
                novel_id,
                {
                    "chapter_id": chapter_id,
                    "chapter_version": chapter_version,
                    "target_range": {"start": position, "end": position + len(before_text)},
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

    async def apply(
        self,
        novel_id: str,
        revision_id: str,
        *,
        after_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """确认一条修改建议并写回章节正文。

        写回前必须校验：建议归属、状态为 pending、章节版本未变化、
        before_text 能在正文中精确定位。任一校验失败都不写正文。

        Args:
            novel_id: 小说 ObjectId 字符串。
            revision_id: 建议业务 ID。
            after_text: 用户编辑后的文本；为空时使用 AI 原文。

        Returns:
            包含更新后章节与建议状态的字典。

        Raises:
            NotFoundError: 建议不存在或不属于该小说。
            DuplicateKeyError: 建议已处理、章节版本已变化或原文无法定位。
        """
        revision = await text_revision_repo.get_revision(novel_id, revision_id)
        if str(revision.get("status") or "") != "pending":
            raise DuplicateKeyError("该修改建议已处理，请勿重复确认")

        chapter_id = str(revision.get("chapter_id") or "")
        chapter = await self.chapter_repo.get_chapter(chapter_id)
        if str(chapter.get("novel_id") or "") != novel_id:
            raise DuplicateKeyError(f"章节 {chapter_id} 不属于该小说")

        chapter_version = int(chapter.get("version") or 1)
        if chapter_version != int(revision.get("chapter_version") or 0):
            raise DuplicateKeyError(
                "章节正文已更新，该建议基于旧版本生成，请重新生成后再确认"
            )

        content = str(chapter.get("content") or "")
        before_text = str(revision.get("before_text") or "")
        if not before_text or before_text not in content:
            raise DuplicateKeyError("建议原文无法在当前正文中精确定位，无法安全应用")

        final_text = str(after_text if after_text is not None else revision.get("after_text") or "")
        if not final_text.strip():
            raise DuplicateKeyError("建议文本为空，无法应用")

        updated = await self.chapter_repo.update_chapter(
            chapter_id,
            {"content": content.replace(before_text, final_text, 1)},
            expected_version=chapter_version,
        )
        # 正式写回成功后才把建议标记为 accepted
        await text_revision_repo.update_status(novel_id, revision_id, "accepted")
        # 同章节中基于旧版本的其它待处理建议标记为冲突，避免用户误用过期建议
        conflicted = await text_revision_repo.mark_conflicted_by_chapter(
            novel_id, chapter_id, int(updated.get("version") or chapter_version)
        )
        return {
            "chapter": updated,
            "revision": await text_revision_repo.get_revision(novel_id, revision_id),
            "conflicted_count": conflicted,
        }


text_revision_service = TextRevisionService()
