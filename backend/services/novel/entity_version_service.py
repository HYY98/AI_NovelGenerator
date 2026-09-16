"""实体内容版本服务（v2.0 T10 版本基础）。

对外只暴露快照写入、版本列表与还原三类能力：
- 快照：章节保存 / 采纳 / 正文修正 / 定稿 / 还原都会写一条，内容未变化则复用上一条；
- 列表：按实体查询版本链，带分页；
- 还原：把历史内容写成新快照并切换当前版本指针，历史只增不删。
"""

from __future__ import annotations

import difflib
import logging
from typing import Any, Dict, List, Optional

from backend.api.error_contract import ServiceError
from backend.db.repositories.chapter_repository import ChapterRepository
from backend.db.repositories.entity_version_repository import entity_version_repo
from backend.db.repositories.setting_card_repository import SettingCardRepository
from backend.services.novel.candidate_accept_service import run_operation

logger = logging.getLogger(__name__)


class EntityVersionService:
    """实体内容版本服务。"""

    def __init__(self) -> None:
        self.repo = entity_version_repo
        self.chapter_repo = ChapterRepository()
        self.card_repo = SettingCardRepository()

    async def snapshot_chapter(
        self,
        novel_id: str,
        chapter: Dict[str, Any],
        *,
        operation_type: str,
        source: str = "system",
        summary: str = "",
        operation_id: str = "",
    ) -> Dict[str, Any]:
        """为章节写一条内容快照并更新当前版本指针。

        Args:
            novel_id: 小说 ObjectId 字符串。
            chapter: 章节文档（写入后的最新状态）。
            operation_type: 产生该版本的操作类型。
            source: 来源标识。
            summary: 变更摘要。
            operation_id: 关联的幂等操作 ID。

        Returns:
            版本文档。
        """
        entity_id = str(chapter.get("chapter_id") or chapter.get("_id") or "")
        snapshot = {
            "content": str(chapter.get("content") or ""),
            "title": str(chapter.get("title") or ""),
            "summary": str(chapter.get("summary") or ""),
            "version": int(chapter.get("version") or 1),
            "status": str(chapter.get("status") or ""),
        }
        version = await self.repo.create_snapshot(
            novel_id,
            entity_type="chapter",
            entity_id=entity_id,
            content_snapshot=snapshot,
            operation_type=operation_type,
            source=source,
            summary=summary,
            chapter_id=entity_id,
            operation_id=operation_id,
        )
        await self.chapter_repo.set_current_revision(
            str(chapter.get("_id") or entity_id), str(version.get("revision_id") or "")
        )
        return version

    async def snapshot_entity(
        self,
        novel_id: str,
        entity_type: str,
        entity_id: str,
        content_snapshot: Dict[str, Any],
        *,
        operation_type: str,
        source: str = "system",
        summary: str = "",
        chapter_id: str = "",
        operation_id: str = "",
    ) -> Dict[str, Any]:
        """为任意受支持实体写一条内容快照。"""
        return await self.repo.create_snapshot(
            novel_id,
            entity_type=entity_type,
            entity_id=entity_id,
            content_snapshot=content_snapshot,
            operation_type=operation_type,
            source=source,
            summary=summary,
            chapter_id=chapter_id,
            operation_id=operation_id,
        )

    async def list_versions(
        self,
        novel_id: str,
        entity_type: str,
        entity_id: str,
        *,
        limit: int = 50,
        skip: int = 0,
    ) -> Dict[str, Any]:
        """列出实体版本。

        Returns:
            ``{data, total, limit, skip}``。
        """
        items = await self.repo.list_versions(
            novel_id, entity_type, entity_id, limit=limit, skip=skip
        )
        total = await self.repo.count_versions(novel_id, entity_type, entity_id)
        return {
            "data": [self.version_to_public(item) for item in items],
            "total": total,
            "limit": limit,
            "skip": skip,
        }

    @staticmethod
    def version_to_public(document: Dict[str, Any]) -> Dict[str, Any]:
        """把版本文档归一化为契约定义的 VersionedEntity DTO 形态。

        契约（v2.0 6.7）要求的字段：revision_id、entity_type、entity_id、
        chapter_id、operation_type、source、summary、content_snapshot、
        parent_revision_id、created_at、is_current。这里同时带上乐观锁用的
        ``version`` 与 ``content_hash``，供前端做 diff 与冲突展示。
        """
        if not document:
            return {}
        return {
            "revision_id": str(document.get("revision_id") or ""),
            "entity_type": str(document.get("entity_type") or ""),
            "entity_id": str(document.get("entity_id") or ""),
            "chapter_id": str(document.get("chapter_id") or ""),
            "operation_type": str(document.get("operation_type") or ""),
            "source": str(document.get("source") or "system"),
            "summary": str(document.get("summary") or ""),
            "content_snapshot": document.get("content_snapshot") or {},
            "parent_revision_id": str(document.get("parent_revision_id") or ""),
            "created_at": _iso(document.get("created_at")),
            "is_current": bool(document.get("is_current")),
            "version": int(document.get("version") or 1),
            "content_hash": str(document.get("content_hash") or ""),
        }

    async def diff_versions(
        self,
        novel_id: str,
        entity_type: str,
        entity_id: str,
        from_revision_id: str,
        to_revision_id: str,
    ) -> Dict[str, Any]:
        """对比两个版本的内容差异（字段级 + 正文行级）。

        Args:
            novel_id: 小说 ObjectId 字符串。
            entity_type: chapter / setting_card。
            entity_id: 实体业务 ID。
            from_revision_id: 基线版本业务 ID。
            to_revision_id: 目标版本业务 ID。

        Returns:
            含 from/to 元信息、变更字段列表、字段前后值与正文行级 diff 的字典。

        Raises:
            ServiceError: 版本不存在或不属于该实体。
        """
        base = await self.repo.get_version(novel_id, from_revision_id)
        target = await self.repo.get_version(novel_id, to_revision_id)
        if not base:
            raise ServiceError("VERSION_NOT_FOUND", f"版本不存在: {from_revision_id}")
        if not target:
            raise ServiceError("VERSION_NOT_FOUND", f"版本不存在: {to_revision_id}")
        for document, revision_id in ((base, from_revision_id), (target, to_revision_id)):
            if str(document.get("entity_type") or "") != entity_type or str(
                document.get("entity_id") or ""
            ) != str(entity_id):
                raise ServiceError(
                    "VERSION_NOT_FOUND",
                    f"版本 {revision_id} 不属于 {entity_type}/{entity_id}",
                )

        from_snapshot = base.get("content_snapshot") or {}
        to_snapshot = target.get("content_snapshot") or {}
        from_map = from_snapshot if isinstance(from_snapshot, dict) else {"content": from_snapshot}
        to_map = to_snapshot if isinstance(to_snapshot, dict) else {"content": to_snapshot}

        changed_fields = [
            key
            for key in sorted(set(from_map) | set(to_map))
            if from_map.get(key) != to_map.get(key)
        ]

        return {
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "from": {
                "revision_id": str(base.get("revision_id") or ""),
                "created_at": _iso(base.get("created_at")),
                "summary": str(base.get("summary") or ""),
            },
            "to": {
                "revision_id": str(target.get("revision_id") or ""),
                "created_at": _iso(target.get("created_at")),
                "summary": str(target.get("summary") or ""),
            },
            "changed_fields": changed_fields,
            "field_diffs": {
                key: {"from": from_map.get(key), "to": to_map.get(key)}
                for key in changed_fields
                if key != "content"
            },
            "text_diff": _diff_text(
                str(from_map.get("content") or ""), str(to_map.get("content") or "")
            ),
        }

    async def restore_version(
        self,
        novel_id: str,
        entity_type: str,
        entity_id: str,
        revision_id: str,
        *,
        request_id: str,
        reason: str = "",
        actor: str = "user",
    ) -> Dict[str, Any]:
        """还原到指定版本：写新快照，不删除历史。

        Args:
            novel_id: 小说 ObjectId 字符串。
            entity_type: chapter / setting_card。
            entity_id: 实体业务 ID。
            revision_id: 目标版本业务 ID。
            request_id: 客户端幂等键。
            reason: 还原原因。
            actor: 操作发起者。

        Returns:
            ``{operation_id, replayed, entity_type, entity_id, revision_id, entity}``。
        """
        if not str(request_id or "").strip():
            raise ServiceError("REQUEST_ID_REQUIRED", "还原操作缺少幂等键 request_id")
        if entity_type == "chapter":
            executor_factory = self._restore_chapter
        elif entity_type == "setting_card":
            executor_factory = self._restore_setting_card
        else:
            raise ServiceError("VERSION_NOT_FOUND", f"不支持还原的实体类型: {entity_type}")

        target = await self.repo.get_version(novel_id, revision_id)
        if not target:
            raise ServiceError("VERSION_NOT_FOUND", f"版本不存在: {revision_id}")
        if str(target.get("entity_type") or "") != entity_type or str(
            target.get("entity_id") or ""
        ) != str(entity_id):
            raise ServiceError(
                "VERSION_NOT_FOUND",
                f"版本 {revision_id} 不属于 {entity_type}/{entity_id}",
            )

        async def _executor(handle) -> Dict[str, Any]:
            return await executor_factory(
                novel_id,
                entity_id,
                target,
                operation_id=handle.operation_id,
                reason=reason,
            )

        outcome = await run_operation(
            novel_id=novel_id,
            operation_type=f"{entity_type}_restore",
            request_id=request_id,
            request_payload={
                "novel_id": str(novel_id),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "revision_id": revision_id,
            },
            target={"entity_type": entity_type, "entity_id": entity_id, "revision_id": revision_id},
            executor=_executor,
            actor=actor,
        )
        body = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
        body["operation_id"] = outcome.get("operation_id")
        body["replayed"] = bool(outcome.get("replayed"))
        return body

    async def _restore_chapter(
        self,
        novel_id: str,
        chapter_id: str,
        target: Dict[str, Any],
        *,
        operation_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """还原章节正文到目标版本。"""
        from backend.db.errors import DuplicateKeyError

        chapter = await self.chapter_repo.get_chapter_by_business_id(novel_id, chapter_id)
        snapshot = target.get("content_snapshot") or {}
        try:
            updated = await self.chapter_repo.update_chapter(
                str(chapter.get("_id")),
                {
                    "content": str(snapshot.get("content") or ""),
                    "title": str(snapshot.get("title") or chapter.get("title") or ""),
                },
                expected_version=int(chapter.get("version") or 1),
            )
        except DuplicateKeyError as exc:
            raise ServiceError("CHAPTER_FINALIZED", str(exc)) from exc
        version = await self.snapshot_chapter(
            novel_id,
            updated,
            operation_type="restore",
            source="restore",
            summary=str(reason or f"还原到 {target.get('revision_id')}"),
            operation_id=operation_id,
        )
        return {
            "entity_type": "chapter",
            "entity_id": chapter_id,
            "revision_id": str(target.get("revision_id") or ""),
            "new_revision_id": str(version.get("revision_id") or ""),
            "entity": updated,
            "result_refs": {
                "entity_type": "chapter",
                "entity_id": chapter_id,
                "revision_id": str(target.get("revision_id") or ""),
                "new_revision_id": str(version.get("revision_id") or ""),
            },
        }

    async def _restore_setting_card(
        self,
        novel_id: str,
        card_id: str,
        target: Dict[str, Any],
        *,
        operation_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """还原设定卡字段到目标版本。"""
        card = await self.card_repo.get_card_by_business_id(novel_id, card_id)
        snapshot = target.get("content_snapshot") or {}
        fields = snapshot.get("fields") if isinstance(snapshot, dict) else None
        updated = await self.card_repo.update_card_by_business_id(
            novel_id,
            card_id,
            {"fields": dict(fields or {})},
            expected_version=int(card.get("version") or 1),
        )
        version = await self.snapshot_entity(
            novel_id,
            "setting_card",
            card_id,
            {"fields": dict(updated.get("fields") or {}), "name": str(updated.get("name") or "")},
            operation_type="restore",
            source="restore",
            summary=str(reason or f"还原到 {target.get('revision_id')}"),
            operation_id=operation_id,
        )
        return {
            "entity_type": "setting_card",
            "entity_id": card_id,
            "revision_id": str(target.get("revision_id") or ""),
            "new_revision_id": str(version.get("revision_id") or ""),
            "entity": updated,
            "result_refs": {
                "entity_type": "setting_card",
                "entity_id": card_id,
                "revision_id": str(target.get("revision_id") or ""),
                "new_revision_id": str(version.get("revision_id") or ""),
            },
        }


def _iso(value: Any) -> Optional[str]:
    """把 datetime 转成 ISO 字符串。"""
    return value.isoformat() if value is not None else None


def _diff_text(before: str, after: str) -> List[Dict[str, Any]]:
    """用字符级差异生成可读的文本 diff。

    Args:
        before: 基线文本。
        after: 目标文本。

    Returns:
        操作列表，每项形如 ``{"op", "from_start", "from_end", "from_text", "to_start", "to_end", "to_text"}``。
    """
    if before == after:
        return []
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    operations: List[Dict[str, Any]] = []
    op_map = {"delete": "remove", "insert": "add", "replace": "change"}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        operations.append(
            {
                "op": op_map.get(tag, tag),
                "from_start": i1,
                "from_end": i2,
                "from_text": before[i1:i2],
                "to_start": j1,
                "to_end": j2,
                "to_text": after[j1:j2],
            }
        )
    return operations


entity_version_service = EntityVersionService()
