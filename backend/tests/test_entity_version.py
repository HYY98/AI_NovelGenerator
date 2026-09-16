"""实体内容版本（T10）单元测试。

覆盖：版本 DTO 归一化、文本 diff、两版本对比、章节保存写快照、还原产生新版本。
全部不依赖数据库，用假仓储替换服务依赖。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from backend.services.novel.entity_version_service import (
    EntityVersionService,
    _diff_text,
)


class FakeVersionRepo:
    """记录快照并返回固定结果的假版本仓储。"""

    def __init__(self) -> None:
        self.snapshots: List[Dict[str, Any]] = []
        self.versions: Dict[str, Dict[str, Any]] = {}
        self._next = 1

    async def create_snapshot(
        self,
        novel_id,
        *,
        entity_type: str,
        entity_id: str,
        content_snapshot,
        operation_type: str,
        source: str = "system",
        summary: str = "",
        chapter_id: str = "",
        parent_revision_id: str = "",
        operation_id: str = "",
        skip_if_unchanged: bool = True,
        session=None,
    ) -> Dict[str, Any]:
        revision_id = f"rev_{self._next}"
        self._next += 1
        doc = {
            "revision_id": revision_id,
            "novel_id": novel_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "chapter_id": chapter_id,
            "operation_type": operation_type,
            "source": source,
            "summary": summary,
            "content_snapshot": content_snapshot,
            "parent_revision_id": parent_revision_id,
            "created_at": None,
            "is_current": True,
            "version": self._next,
            "content_hash": "hash",
        }
        self.snapshots.append(doc)
        self.versions[revision_id] = doc
        return doc

    async def get_version(self, novel_id, revision_id: str):
        return self.versions.get(revision_id)

    async def list_versions(self, novel_id, entity_type, entity_id, *, limit=50, skip=0, session=None):
        return [
            d
            for d in self.snapshots
            if d["entity_type"] == entity_type and d["entity_id"] == entity_id
        ]

    async def count_versions(self, novel_id, entity_type, entity_id, session=None) -> int:
        return len(
            [d for d in self.snapshots if d["entity_id"] == entity_id]
        )


class FakeChapterRepo:
    def __init__(self) -> None:
        self.current_revision: List[tuple[str, str]] = []
        self.chapters: Dict[str, Dict[str, Any]] = {}

    async def set_current_revision(self, chapter_id, revision_id: str, session=None) -> bool:
        self.current_revision.append((str(chapter_id), str(revision_id)))
        return True

    async def get_chapter_by_business_id(self, novel_id, chapter_id: str, include_deleted=False, session=None):
        return self.chapters.get(chapter_id, {"chapter_id": chapter_id, "_id": chapter_id, "version": 1})

    async def update_chapter(self, chapter_id, data, expected_version=None, session=None):
        merged = dict(self.chapters.get(str(chapter_id), {"_id": chapter_id, "chapter_id": chapter_id}))
        merged.update(data)
        merged["version"] = int(merged.get("version", 1)) + 1
        self.chapters[str(chapter_id)] = merged
        return merged


def _service(fake_repo: FakeVersionRepo) -> EntityVersionService:
    service = EntityVersionService()
    service.repo = fake_repo
    service.chapter_repo = FakeChapterRepo()
    return service


def test_version_to_public_normalizes():
    doc = {
        "revision_id": "rev_1",
        "entity_type": "chapter",
        "entity_id": "chap_1",
        "chapter_id": "chap_1",
        "operation_type": "update",
        "source": "manual",
        "summary": "保存",
        "content_snapshot": {"content": "abc"},
        "parent_revision_id": "",
        "created_at": None,
        "is_current": True,
        "version": 2,
        "content_hash": "h1",
        "_id": "ignored",
    }
    out = EntityVersionService.version_to_public(doc)
    assert out["revision_id"] == "rev_1"
    assert out["entity_type"] == "chapter"
    assert out["source"] == "manual"
    assert out["content_snapshot"] == {"content": "abc"}
    assert out["version"] == 2
    assert out["content_hash"] == "h1"
    assert "_id" not in out


def test_diff_text_operations():
    ops = _diff_text("ab", "acb")
    # ab -> acb：在 a 后插入 c
    assert any(o["op"] == "add" and o["to_text"] == "c" for o in ops)
    assert _diff_text("same", "same") == []


def test_diff_versions_between_two():
    fake = FakeVersionRepo()
    fake.versions["rev_1"] = {
        "revision_id": "rev_1",
        "entity_type": "chapter",
        "entity_id": "chap_1",
        "content_snapshot": {"content": "第一版正文", "title": "旧标题"},
        "summary": "创建",
        "created_at": None,
    }
    fake.versions["rev_2"] = {
        "revision_id": "rev_2",
        "entity_type": "chapter",
        "entity_id": "chap_1",
        "content_snapshot": {"content": "第一版正文改", "title": "旧标题"},
        "summary": "保存",
        "created_at": None,
    }
    service = _service(fake)

    result = asyncio.run(
        service.diff_versions("n1", "chapter", "chap_1", "rev_1", "rev_2")
    )
    assert result["changed_fields"] == ["content"]
    assert result["from"]["revision_id"] == "rev_1"
    assert result["to"]["revision_id"] == "rev_2"
    assert result["text_diff"], "正文变化应产生行级 diff"


def test_snapshot_chapter_writes_version():
    fake = FakeVersionRepo()
    service = _service(fake)
    chapter = {
        "novel_id": "n1",
        "_id": "chap_mongo_1",
        "chapter_id": "chap_1",
        "content": "正文",
        "title": "标题",
        "summary": "",
        "version": 3,
        "status": "draft",
    }
    version = asyncio.run(
        service.snapshot_chapter(
            "n1", chapter, operation_type="update", source="manual", summary="保存章节"
        )
    )
    assert version["revision_id"] == "rev_1"
    assert fake.snapshots[0]["entity_type"] == "chapter"
    assert fake.snapshots[0]["operation_type"] == "update"
    assert fake.snapshots[0]["source"] == "manual"
    # 章节当前版本指针已更新
    assert service.chapter_repo.current_revision == [
        ("chap_mongo_1", "rev_1")
    ]


def test_restore_produces_new_version(monkeypatch):
    fake = FakeVersionRepo()
    service = _service(fake)
    service.chapter_repo.chapters["chap_1"] = {
        "chapter_id": "chap_1",
        "_id": "chap_mongo_1",
        "novel_id": "n1",
        "content": "当前正文",
        "title": "标题",
        "version": 5,
    }
    fake.versions["rev_old"] = {
        "revision_id": "rev_old",
        "entity_type": "chapter",
        "entity_id": "chap_1",
        "content_snapshot": {"content": "旧正文", "title": "旧标题"},
    }

    async def fake_run_operation(
        *, novel_id, operation_type, request_id, request_payload, target, executor, actor="user"
    ):
        class Handle:
            operation_id = "op_1"

            async def succeed(self, result_refs=None):
                return None

            async def fail(self, *args):
                return None

        result = await executor(Handle())
        return {"operation_id": "op_1", "replayed": False, "result": result}

    monkeypatch.setattr(
        "backend.services.novel.entity_version_service.run_operation", fake_run_operation
    )

    outcome = asyncio.run(
        service.restore_version(
            "n1",
            "chapter",
            "chap_1",
            "rev_old",
            request_id="req-1",
            reason="还原到旧版",
        )
    )
    assert outcome["new_revision_id"] == "rev_1"
    # 还原是写新快照，历史不删
    assert fake.snapshots, "还原应产生新快照"
    assert fake.snapshots[-1]["operation_type"] == "restore"
    assert fake.snapshots[-1]["source"] == "restore"
