"""章节业务逻辑层。"""
from typing import Any, Dict, List, Optional

from backend.db.repositories.chapter_repository import ChapterRepository, CHAPTER_STATUS, STATUS_LABEL
from backend.db.errors import InvalidIdError


class ChapterService:
    def __init__(self):
        self.repo = ChapterRepository()

    async def create_chapter(self, novel_id, data: Dict[str, Any]):
        if not novel_id:
            raise InvalidIdError("novel_id 不能为空")
        status = data.get("status", "draft")
        if status not in CHAPTER_STATUS:
            raise InvalidIdError(f"非法状态: {status}")
        return await self.repo.create_chapter(novel_id, data)

    async def list_chapters(self, novel_id, volume_id=None, include_deleted=False):
        return await self.repo.list_chapters(novel_id, volume_id, include_deleted)

    async def get_chapter(self, chapter_id, include_deleted=False):
        return await self.repo.get_chapter(chapter_id, include_deleted)

    async def save_chapter(self, chapter_id, data: Dict[str, Any], expected_version=None):
        # 自动保存草稿：保存内容时若处于 draft 保持 draft，不强制改状态
        return await self.repo.update_chapter(chapter_id, data, expected_version)

    async def finalize_chapter(self, chapter_id):
        return await self.repo.change_status(chapter_id, "finalized")

    async def reopen_chapter(self, chapter_id):
        return await self.repo.change_status(chapter_id, "editing")

    async def delete_chapter(self, chapter_id):
        return await self.repo.soft_delete_chapter(chapter_id)

    async def restore_chapter(self, chapter_id):
        return await self.repo.restore_chapter(chapter_id)

    async def get_navigation(self, novel_id, number: int):
        return await self.repo.get_navigation(novel_id, number)

    @staticmethod
    def status_meta():
        return {"statuses": list(CHAPTER_STATUS), "labels": STATUS_LABEL}
