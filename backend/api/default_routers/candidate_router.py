"""候选路由（v2.0 T02）：小说候选列表与逐条决定。

所有采纳类写操作都要求携带 request_id，重复提交只生效一次。
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from backend.api.error_contract import to_http_exception
from backend.db.repositories.generation_record_repository import generation_record_repo
from backend.services.novel.candidate_accept_service import (
    mark_candidate_ignored,
    mark_candidate_rejected,
    resolve_candidate,
    run_operation,
    summarize_candidate,
)

novel_candidate_router = APIRouter(prefix="/api/novels", tags=["candidates"])
candidate_action_router = APIRouter(prefix="/api/candidates", tags=["candidates"])

# 记录类型到采纳实现的分发规则
SETTING_CARD_KINDS = ("location_card", "item_card", "rule_card")
SETTING_CARD_ACTIONS = ("setting_card_generate",)
TEXT_REVISION_KINDS = ("generate_text_revision",)
TEXT_REVISION_ACTIONS = ("text_revision_generate",)


class CandidateDecisionRequest(BaseModel):
    """候选逐条决定请求。"""

    novel_id: str = Field(min_length=1, max_length=80)
    generation_id: str = Field(min_length=1, max_length=80)
    candidate_id: str = Field(default="", max_length=120)
    decision: str = Field(pattern="^(accept|reject|ignore)$")
    request_id: str = Field(min_length=1, max_length=120)
    reason: Optional[str] = Field(default="", max_length=2000)
    action: Optional[str] = Field(default="merge", max_length=20)
    target_card_id: Optional[str] = Field(default="", max_length=80)
    accepted_fields: Optional[Dict[str, Any]] = None
    after_text: Optional[str] = Field(default=None, max_length=20000)
    expected_chapter_version: Optional[int] = None
    expected_content_hash: Optional[str] = Field(default="", max_length=128)
    operation: Optional[str] = Field(default="replace", max_length=20)
    occurrence: Optional[int] = None
    expected_version: Optional[int] = None


@novel_candidate_router.get("/{novel_id}/candidates")
async def list_candidates(
    novel_id: str,
    status: Optional[str] = Query(default=None, description="生成记录状态"),
    kind: Optional[str] = Query(default=None, description="旧 kind"),
    action_type: Optional[str] = Query(default=None, description="新 action_type"),
    chapter_id: Optional[str] = Query(default=None),
    card_id: Optional[str] = Query(default=None),
    candidate_status: Optional[str] = Query(default=None, description="pending/accepted/rejected"),
    is_latest: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
):
    """列出小说下的候选，展开为逐候选条目。"""
    try:
        records = await generation_record_repo.list_records(
            novel_id,
            chapter_id=chapter_id,
            card_id=card_id,
            kind=kind,
            limit=200,
        )
        items: List[Dict[str, Any]] = []
        for record in records:
            if status and str(record.get("status") or "") != status:
                continue
            if action_type and str(record.get("action_type") or "") != action_type:
                continue
            if is_latest is not None and bool(record.get("is_latest", False)) != is_latest:
                continue
            for item in summarize_candidate(record):
                if candidate_status and item.get("candidate_status") != candidate_status:
                    continue
                items.append(item)
        total = len(items)
        return {"data": items[skip: skip + limit], "total": total, "limit": limit, "skip": skip}
    except Exception as exc:
        raise to_http_exception(exc)


@candidate_action_router.post("/decision")
async def decide_candidate(req: CandidateDecisionRequest):
    """对一条候选做接受或拒绝，按记录类型分发到对应采纳实现。

    - 设定卡候选：写入正式卡片并逐候选落状态；
    - 正文修改建议：写回章节正文；
    - 其它类型：暂不支持，返回 422 并列入后续批次。
    """
    from backend.services.llm.text_revision_service import text_revision_service
    from backend.services.novel.setting_card_accept_service import (
        setting_card_accept_service,
    )

    try:
        record = await generation_record_repo.get_owned_record(
            req.novel_id, req.generation_id
        )
        kind = str(record.get("kind") or "")
        action_type = str(record.get("action_type") or "")

        if req.decision in ("reject", "ignore"):
            mark_fn = (
                mark_candidate_rejected if req.decision == "reject" else mark_candidate_ignored
            )
            reason = req.reason or ("用户拒绝" if req.decision == "reject" else "用户忽略")

            async def _resolve(handle) -> Dict[str, Any]:
                resolved = await resolve_candidate(
                    req.novel_id, req.generation_id, req.candidate_id
                )
                await mark_fn(
                    req.novel_id,
                    req.generation_id,
                    str(resolved.get("candidate_id") or ""),
                    reason=reason,
                    operation_id=handle.operation_id,
                )
                return {
                    "generation_id": req.generation_id,
                    "candidate_id": str(resolved.get("candidate_id") or ""),
                    "status": req.decision if req.decision == "reject" else "ignored",
                    "result_refs": {
                        "generation_id": req.generation_id,
                        "candidate_id": str(resolved.get("candidate_id") or ""),
                        "status": req.decision if req.decision == "reject" else "ignored",
                    },
                }

            outcome = await run_operation(
                novel_id=req.novel_id,
                operation_type=f"candidate_{req.decision}",
                request_id=req.request_id,
                request_payload={
                    "novel_id": req.novel_id,
                    "generation_id": req.generation_id,
                    "candidate_id": req.candidate_id,
                    "decision": req.decision,
                },
                target={"generation_id": req.generation_id},
                executor=_resolve,
            )
            body = dict(outcome.get("result") or {})
            body["operation_id"] = outcome.get("operation_id")
            body["replayed"] = bool(outcome.get("replayed"))
            return body

        if kind in SETTING_CARD_KINDS or action_type in SETTING_CARD_ACTIONS:
            result = await setting_card_accept_service.accept(
                req.novel_id,
                generation_id=req.generation_id,
                action=req.action or "merge",
                request_id=req.request_id,
                candidate_id=req.candidate_id,
                target_card_id=req.target_card_id or "",
                accepted_fields=req.accepted_fields,
                expected_version=req.expected_version,
            )
            result["generation_id"] = req.generation_id
            return result

        if kind in TEXT_REVISION_KINDS or action_type in TEXT_REVISION_ACTIONS:
            revision_id = str(req.candidate_id or "").strip()
            if not revision_id:
                # 未指定候选时取该记录下的第一条建议
                data = (record.get("result") or {}).get("data") or {}
                revisions = data.get("revisions") or []
                if not revisions:
                    raise HTTPException(422, "该生成记录没有可采纳的修改建议")
                revision_id = str(revisions[0].get("revision_id") or "")
            return await text_revision_service.apply(
                req.novel_id,
                revision_id,
                request_id=req.request_id,
                after_text=req.after_text,
                expected_chapter_version=req.expected_chapter_version,
                expected_content_hash=req.expected_content_hash or "",
                operation=req.operation or "replace",
                occurrence=req.occurrence,
            )

        raise HTTPException(
            422,
            f"该记录类型（kind={kind or '-'} / action_type={action_type or '-'}）暂不支持逐条采纳",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise to_http_exception(exc)
