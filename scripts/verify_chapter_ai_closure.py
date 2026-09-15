"""章节 AI 闭环的端到端验证脚本（不调用大模型）。

验证内容：
1. 章节关联业务 ID 的校验（不存在 / 类型不匹配 / 重复去重）；
2. 章节 AI 上下文组装（关联卡片、硬规则、上下文版本快照）；
3. 定稿提交：事件确认 → 卡片状态与字段写回、状态历史记录、章节定稿；
4. 重复定稿被拒绝；
5. 业务 ID 序列分配。

脚本会创建一本临时小说并在结束时清理，不会影响既有数据。

用法（在项目根目录执行，必须使用后端虚拟环境）：
    .venv/bin/python scripts/verify_chapter_ai_closure.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bson import ObjectId  # noqa: E402

from backend.db.errors import InvalidIdError  # noqa: E402
from backend.db.mongo import close_mongo_connection, connect_to_mongo, get_database  # noqa: E402
from backend.db.repositories.chapter_repository import ChapterRepository  # noqa: E402
from backend.db.repositories.novel_repository import novel_repo  # noqa: E402
from backend.db.repositories.setting_card_repository import SettingCardRepository  # noqa: E402
from backend.db.repositories.story_event_repository import story_event_repo  # noqa: E402
from backend.services.llm.chapter_context_service import chapter_context_service  # noqa: E402
from backend.services.novel.chapter_finalize_service import finalize_chapter_service  # noqa: E402
from backend.services.novel.chapter_service import ChapterService  # noqa: E402

CHAPTER_REPO = ChapterRepository()
CARD_REPO = SettingCardRepository()
FAILURES: list[str] = []


def check(label: str, condition: bool, extra: str = "") -> None:
    """打印单条断言结果并记录失败项。

    Args:
        label: 断言描述。
        condition: 断言结果。
        extra: 附加说明。

    Returns:
        无。
    """
    print(f"[{'PASS' if condition else 'FAIL'}] {label} {extra}".rstrip())
    if not condition:
        FAILURES.append(label)


class _CommitRequest:
    """定稿提交请求的最小载体。"""

    def __init__(self, novel_id: str, chapter_oid: str, version: int) -> None:
        self.novel_id = novel_id
        self.chapter_id = chapter_oid
        self.expected_version = version
        self.accepted_event_ids: list[str] = []
        self.rejected_event_ids: list[str] = []
        self.ignored_issues: list[str] = []
        self.blocking_issues: list[dict] = []
        self.force = False


async def run() -> int:
    """执行全部验证步骤并在结束时清理临时数据。

    Returns:
        进程退出码，0 表示全部通过。
    """
    await connect_to_mongo()
    db = get_database()
    novel_id: str | None = None
    try:
        novel_id = await novel_repo.create_novel(
            {
                "title": "__AI闭环验证临时小说__",
                "genre": "测试",
                "tone": "冷峻",
                "worldview": "噬渊每百年入侵一次。",
                "plot": "主角在救援中发现噬渊核心。",
                "words_per_chapter": 1200,
            }
        )
        chapter = await CHAPTER_REPO.create_chapter(
            novel_id, {"title": "第一章 验证", "content": "测试正文。" * 20}
        )
        card = await CARD_REPO.create_card(
            novel_id,
            {
                "type": "item",
                "name": "验证核心",
                "fields": {"owner": "苏小满", "durability": "完好"},
                "current_state": "完好",
            },
        )
        await CARD_REPO.create_card(
            novel_id,
            {
                "type": "rule",
                "name": "验证硬规则",
                "fields": {"definition": "噬渊每百年入侵一次", "is_hard_rule": "是"},
            },
        )

        service = ChapterService()
        try:
            await service.save_chapter(chapter["_id"], {"linked_item_ids": ["itm_999999"]})
            check("非法卡片业务ID 被拒绝", False, "（未抛异常）")
        except InvalidIdError as exc:
            check("非法卡片业务ID 被拒绝", True, f"-> {exc}")

        try:
            await service.save_chapter(chapter["_id"], {"linked_location_ids": [card["card_id"]]})
            check("卡片类型不匹配 被拒绝", False, "（未抛异常）")
        except InvalidIdError as exc:
            check("卡片类型不匹配 被拒绝", True, f"-> {exc}")

        updated = await service.save_chapter(
            chapter["_id"], {"linked_item_ids": [card["card_id"], card["card_id"]]}
        )
        check(
            "合法关联去重保存",
            updated.get("linked_item_ids") == [card["card_id"]],
            f"-> {updated.get('linked_item_ids')}",
        )

        novel = await novel_repo.get_novel_by_id(novel_id)
        fresh = await CHAPTER_REPO.get_chapter(chapter["_id"])
        context = await chapter_context_service.build(
            novel, fresh, user_prompt="本章要完成第一次战利品回收"
        )
        check("上下文包含关联物品", "验证核心" in context.text)
        check("上下文包含硬规则", "验证硬规则" in context.text)
        check("上下文包含用户要求", "第一次战利品回收" in context.text)
        check(
            "上下文快照含卡片版本",
            card["card_id"] in context.snapshot.get("cards", {}),
            f"-> {list(context.snapshot.get('cards', {}))}",
        )
        check("上下文快照含 hash", len(context.snapshot.get("context_hash", "")) == 64)

        events = await story_event_repo.create_events(
            novel_id,
            [
                {
                    "chapter_id": fresh["chapter_id"],
                    "entity_type": "item",
                    "entity_id": card["card_id"],
                    "event_type": "transfer",
                    "before": {"owner": "苏小满", "current_state": "完好"},
                    "after": {"owner": "林嘉豪", "current_state": "受损"},
                    "evidence": {"text": "苏小满把验证核心交给林嘉豪保管"},
                    "confidence": 0.9,
                }
            ],
        )
        request = _CommitRequest(novel_id, chapter["_id"], fresh["version"])
        request.accepted_event_ids = [events[0]["event_id"]]
        result = await finalize_chapter_service.commit(request)
        check(
            "章节状态为已定稿",
            result["chapter"]["status"] == "finalized",
            f"-> {result['chapter']['status']}",
        )

        card_after = await CARD_REPO.get_card_by_business_id(novel_id, card["card_id"])
        check(
            "卡片持有者已更新",
            card_after["fields"]["owner"] == "林嘉豪",
            f"-> {card_after['fields'].get('owner')}",
        )
        check(
            "卡片当前状态已更新",
            card_after["current_state"] == "受损",
            f"-> {card_after['current_state']}",
        )
        history = card_after.get("state_history") or []
        check(
            "状态历史带来源章节",
            bool(history) and history[-1].get("chapter") == fresh["chapter_id"],
            f"-> {history[-1] if history else None}",
        )
        check(
            "事件状态 accepted",
            (await story_event_repo.get_event(events[0]["event_id"]))["status"] == "accepted",
        )

        try:
            await finalize_chapter_service.commit(request)
            check("重复定稿被拒绝", False, "（未抛异常）")
        except Exception as exc:  # DuplicateKeyError
            check("重复定稿被拒绝", True, f"-> {type(exc).__name__}")

        sequence = await db["id_sequences"].find_one(
            {"novel_id": ObjectId(novel_id), "entity_type": "story_event"}
        )
        check("evt 序列已分配", bool(sequence), f"-> {sequence.get('value') if sequence else None}")
    finally:
        if novel_id:
            oid = ObjectId(novel_id)
            for name in ("chapters", "setting_cards", "story_events", "generation_records", "id_sequences"):
                deleted = await db[name].delete_many({"novel_id": oid})
                if deleted.deleted_count:
                    print(f"cleanup {name}: {deleted.deleted_count}")
            await db["novels"].delete_one({"_id": oid})
            print("cleanup novels: 1")
        await close_mongo_connection()

    print("\nFAILURES:", FAILURES if FAILURES else "none")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
