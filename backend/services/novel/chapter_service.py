"""章节业务逻辑层。"""
from typing import Any, Dict, List, Optional

from backend.api.error_contract import ServiceError
from backend.db.repositories.chapter_repository import ChapterRepository, CHAPTER_STATUS, STATUS_LABEL
from backend.db.errors import InvalidIdError
from backend.services.novel.chapter_finalize_service import finalize_chapter_service
from backend.services.novel.chapter_link_validator import (
    touches_link_fields,
    validate_and_normalize_links,
)


class ChapterService:
    def __init__(self):
        self.repo = ChapterRepository()

    async def create_chapter(self, novel_id, data: Dict[str, Any]):
        if not novel_id:
            raise InvalidIdError("novel_id 不能为空")
        status = data.get("status", "draft")
        if status not in CHAPTER_STATUS:
            raise InvalidIdError(f"非法状态: {status}")
        # v2.0：定稿状态只能通过定稿门禁写入，新建时直接带 finalized 属于绕过
        if status == "finalized":
            raise ServiceError(
                "REVIEW_REQUIRED",
                "新建章节不能直接设为已定稿，请先保存后通过定稿流程确认",
            )
        # 新建时没有历史关联可放行，所有业务 ID 必须是当前小说的有效实体
        data = await validate_and_normalize_links(novel_id, data)
        chapter = await self.repo.create_chapter(novel_id, data)
        await self._snapshot_chapter(
            chapter, operation_type="create", source="manual", summary="创建章节"
        )
        return chapter

    async def list_chapters(self, novel_id, volume_id=None, include_deleted=False):
        return await self.repo.list_chapters(novel_id, volume_id, include_deleted)

    async def get_chapter(self, chapter_id, include_deleted=False):
        return await self.repo.get_chapter(chapter_id, include_deleted)

    async def save_chapter(self, chapter_id, data: Dict[str, Any], expected_version=None):
        # v2.0：定稿状态只能经门禁写入，保存接口不再接受 status=finalized
        if str(data.get("status") or "") == "finalized":
            raise ServiceError(
                "REVIEW_REQUIRED",
                "保存接口不能直接定稿，请通过定稿流程提交审校记录与逐项确认",
            )
        # 自动保存草稿：保存内容时若处于 draft 保持 draft，不强制改状态
        if touches_link_fields(data):
            existing = await self.repo.get_chapter(chapter_id, include_deleted=True)
            data = await validate_and_normalize_links(
                str(existing.get("novel_id")), data, existing=existing
            )
        chapter = await self.repo.update_chapter(chapter_id, data, expected_version)
        await self._snapshot_chapter(
            chapter, operation_type="update", source="manual", summary="保存章节"
        )
        return chapter

    async def finalize_chapter(
        self,
        chapter_id,
        *,
        request_id: str = "",
        review_generation_id: str = "",
        exceptions: Optional[List[Dict[str, Any]]] = None,
        expected_version: Optional[int] = None,
        accepted_event_ids: Optional[List[str]] = None,
        rejected_event_ids: Optional[List[str]] = None,
        hard_rule_signature: str = "",
        blueprint_version: Optional[int] = None,
    ):
        """定稿统一走门禁：必须带服务端审校记录，阻断项逐项裁决。

        Args:
            chapter_id: 章节 Mongo _id 字符串。
            request_id: 客户端幂等键。
            review_generation_id: 服务端保存的一致性审校记录 ID。
            exceptions: 逐项例外 ``[{"issue_id": ..., "reason": ...}]``。
            expected_version: 客户端基于的章节版本。
            accepted_event_ids: 接受的事实变化事件。
            rejected_event_ids: 拒绝的事实变化事件。
            hard_rule_signature: 审校时的硬规则签名。
            blueprint_version: 审校时的蓝图版本。

        Returns:
            定稿结果字典。
        """
        chapter = await self.repo.get_chapter(chapter_id)
        novel_id = str(chapter.get("novel_id") or "")
        return await finalize_chapter_service.finalize_plain(
            novel_id,
            str(chapter.get("_id") or chapter_id),
            request_id=request_id,
            review_generation_id=review_generation_id,
            exceptions=exceptions,
            expected_version=expected_version,
            accepted_event_ids=accepted_event_ids,
            rejected_event_ids=rejected_event_ids,
            hard_rule_signature=hard_rule_signature,
            blueprint_version=blueprint_version,
        )

    async def reopen_chapter(self, chapter_id, reason: str = ""):
        # 定稿后的正文处于锁定状态，重开必须记录原因与时间，供事后审计
        return await self.repo.reopen_chapter(chapter_id, reason)

    async def delete_chapter(self, chapter_id):
        return await self.repo.soft_delete_chapter(chapter_id)

    async def restore_chapter(self, chapter_id):
        return await self.repo.restore_chapter(chapter_id)

    async def get_navigation(self, novel_id, number: int):
        return await self.repo.get_navigation(novel_id, number)

    @staticmethod
    def status_meta():
        return {"statuses": list(CHAPTER_STATUS), "labels": STATUS_LABEL}

    @staticmethod
    async def _snapshot_chapter(
        chapter: Dict[str, Any],
        *,
        operation_type: str,
        source: str,
        summary: str = "",
    ) -> None:
        """章节内容写入后自动写一条版本快照（v2.0 T10）。

        内容未变化时会复用上一条快照，避免连续自动保存产生版本噪声。
        """
        from backend.services.novel.entity_version_service import entity_version_service

        await entity_version_service.snapshot_chapter(
            str(chapter.get("novel_id") or ""),
            chapter,
            operation_type=operation_type,
            source=source,
            summary=summary,
        )
