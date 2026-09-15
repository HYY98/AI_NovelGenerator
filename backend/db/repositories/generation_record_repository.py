"""AI 生成记录仓储，异步 MongoDB。

每次 AI 生成都会写入一条记录，保存输入上下文快照、候选结果与采纳状态。
候选结果只用于预览，用户点击「采用」后由业务层调用正式保存接口，
本仓储不负责把候选写入正式章节或卡片。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId

from backend.db.base import BaseRepository
from backend.db.errors import DuplicateKeyError, NotFoundError
from backend.db.repositories.id_sequence_repository import id_sequence_repo
from backend.db.utils import get_utc_now, to_object_id

logger = logging.getLogger(__name__)

# 生成类型（与技术指引 19.3 保持一致）
GENERATION_KINDS = (
    "location_card",
    "item_card",
    "rule_card",
    "extract_cards",
    "complete_card",
    "check_card_conflicts",
    "chapter_plan",
    "chapter_draft",
    "chapter_continue",
    "chapter_rewrite",
    "chapter_expand",
    "chapter_compress",
    "consistency_review",
    "state_change_proposal",
)

# 生成记录状态
GENERATION_STATUS = ("pending", "completed", "failed")


def serialize(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把文档中的 ObjectId 转成字符串，便于 JSON 序列化。"""
    if not doc:
        return doc
    for key in ("_id", "novel_id"):
        if key in doc and isinstance(doc[key], ObjectId):
            doc[key] = str(doc[key])
    return doc


class GenerationRecordRepository(BaseRepository):
    """生成记录数据访问层。"""

    def __init__(self) -> None:
        super().__init__("generation_records")

    async def _next_generation_id(self, novel_id, session=None) -> str:
        allocated = await id_sequence_repo.allocate_many(
            novel_id, "generation_record", "gen", 1, session=session
        )
        return allocated[0]

    async def find_by_request_id(
        self,
        novel_id,
        kind: str,
        request_id: str,
        session=None,
    ) -> Optional[Dict[str, Any]]:
        """按小说 + 生成类型 + 客户端幂等 ID 查找已存在的生成记录。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            kind: 生成类型。
            request_id: 客户端生成的幂等 ID。
            session: 可选 MongoDB 会话。

        Returns:
            命中的生成记录；不存在时返回 None。
        """
        if not request_id:
            return None
        doc = await self.find_one(
            {
                "novel_id": to_object_id(novel_id),
                "kind": kind,
                "request_id": request_id,
            },
            session=session,
        )
        return serialize(doc)

    async def create_record(
        self,
        novel_id,
        data: Dict[str, Any],
        session=None,
    ) -> Dict[str, Any]:
        """新建一条生成记录。

        Args:
            novel_id: 小说 ObjectId 或字符串。
            data: 记录字段，至少包含 kind。
            session: 可选 MongoDB 会话。

        Returns:
            已落库的生成记录。
        """
        kind = str(data.get("kind", "")).strip()
        if kind not in GENERATION_KINDS:
            raise DuplicateKeyError(f"不支持的生成类型: {kind}")
        generation_id = await self._next_generation_id(novel_id, session=session)
        doc = {
            "generation_id": generation_id,
            "novel_id": to_object_id(novel_id),
            "chapter_id": str(data.get("chapter_id") or ""),
            "card_id": str(data.get("card_id") or ""),
            "kind": kind,
            "status": data.get("status", "completed"),
            "input_snapshot": data.get("input_snapshot", {}),
            "result": data.get("result", {}),
            "accepted": bool(data.get("accepted", False)),
            "provider": str(data.get("provider") or ""),
            "model": str(data.get("model") or ""),
            "error": str(data.get("error") or ""),
        }
        # request_id 为空时不落该字段，让幂等唯一索引只覆盖真正带幂等键的记录
        request_id = str(data.get("request_id") or "").strip()
        if request_id:
            doc["request_id"] = request_id
        inserted = await self.insert_one(doc, session=session)
        # 回读必须按 Mongo _id 查，业务 ID 与 _id 不是同一个值
        found = await self.find_one({"_id": ObjectId(inserted)}, session=session)
        if not found:
            raise NotFoundError(f"生成记录写入后无法回读: {inserted}")
        return serialize(found)

    async def get_record(
        self,
        generation_id: str,
        include_deleted: bool = False,
        session=None,
    ) -> Dict[str, Any]:
        """按业务 ID 读取生成记录。"""
        doc = await self.find_one(
            {"generation_id": generation_id},
            include_deleted=include_deleted,
            session=session,
        )
        if not doc:
            raise NotFoundError(f"生成记录不存在: {generation_id}")
        return serialize(doc)

    async def list_records(
        self,
        novel_id,
        chapter_id: Optional[str] = None,
        card_id: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 50,
        include_deleted: bool = False,
        session=None,
    ) -> List[Dict[str, Any]]:
        """列出小说下的生成记录，按创建时间倒序。"""
        flt: Dict[str, Any] = {"novel_id": to_object_id(novel_id)}
        if chapter_id:
            flt["chapter_id"] = chapter_id
        if card_id:
            flt["card_id"] = card_id
        if kind:
            flt["kind"] = kind
        docs = await self.find_many(
            flt,
            include_deleted=include_deleted,
            limit=max(1, min(200, limit)),
            sort=[("created_at", -1)],
            session=session,
        )
        return [serialize(d) for d in docs]

    async def mark_accepted(self, generation_id: str, session=None) -> bool:
        """标记候选结果已被用户采用。"""
        return await self.update_one(
            {"generation_id": generation_id},
            {"accepted": True, "accepted_at": get_utc_now()},
            session=session,
        )

    async def update_result(
        self,
        generation_id: str,
        result: Dict[str, Any],
        status: str = "completed",
        session=None,
    ) -> bool:
        """回写候选结果或失败状态。

        Args:
            generation_id: 生成记录业务 ID。
            result: 候选结果字典。
            status: pending / completed / failed。
            session: 可选 MongoDB 会话。

        Returns:
            实际更新成功时返回 True。
        """
        if status not in GENERATION_STATUS:
            raise DuplicateKeyError(f"非法的生成状态: {status}")
        return await self.update_one(
            {"generation_id": generation_id},
            {"result": result, "status": status},
            session=session,
        )


generation_record_repo = GenerationRecordRepository()
