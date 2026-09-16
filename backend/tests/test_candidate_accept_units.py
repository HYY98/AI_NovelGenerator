"""候选采纳核心逻辑单元测试（v2.0 T02）。

覆盖四类关键行为，不依赖数据库：
1. 字段白名单绕过（受保护结构性字段 / is_hard_rule 默认 false）；
2. 并发认领与重复采纳（claim_candidate 分支逻辑，mock 仓储返回）；
3. 版本冲突（服务端版本推进 → VERSION_STALE）；
4. 正文范围定位与幂等哈希等纯函数。

并发采纳与版本冲突的完整 DB 行为见 scripts/verify_v2_batch1.py（需 MongoDB）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, patch

from backend.api.error_contract import ServiceError
from backend.db.repositories.operation_record_repository import build_request_hash
from backend.llm.schemas.candidate_accept_pydantic import CandidateAcceptRequest
from backend.services.novel import candidate_accept_service
from backend.services.novel.setting_card_accept_service import (
    PROTECTED_STRUCTURAL_FIELDS,
    SettingCardAcceptService,
)
from backend.services.llm.text_revision_service import TextRevisionService


def run(coro):
    """同步测试里运行协程（项目未引入 pytest-asyncio）。"""
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# 1. 字段白名单绕过
# ----------------------------------------------------------------------
def test_filter_fields_drops_protected_structural_fields():
    warnings: List[str] = []
    result = SettingCardAcceptService._filter_fields(
        "location",
        {
            "name": "恶意改名",
            "current_state": "已毁",
            "importance": 5,
            "card_id": "loc_hacked",
            "version": 999,
            "appearance": "森林",
        },
        warnings,
    )
    # 受保护的结构性字段一律被剔除，只有合法白名单字段保留
    assert "name" not in result
    assert "current_state" not in result
    assert "importance" not in result
    assert "card_id" not in result
    assert "version" not in result
    assert result.get("appearance") == "森林"
    assert any("已忽略" in w for w in warnings)


def test_protected_fields_include_current_state_and_is_hard_rule():
    # current_state 属动态状态，绝不允许从 payload 覆盖
    assert "current_state" in PROTECTED_STRUCTURAL_FIELDS
    assert "name" in PROTECTED_STRUCTURAL_FIELDS
    assert "importance" in PROTECTED_STRUCTURAL_FIELDS


def test_filter_fields_hard_rule_default_false():
    warnings: List[str] = []
    result = SettingCardAcceptService._filter_fields(
        "rule", {"is_hard_rule": "true", "definition": "规则"}, warnings
    )
    # 未显式确认时 is_hard_rule 强制回落 false，AI 不能擅自置 true
    assert result.get("is_hard_rule") == "false"
    assert result.get("definition") == "规则"


def test_filter_fields_hard_rule_confirmed_true():
    result = SettingCardAcceptService._filter_fields(
        "rule",
        {"is_hard_rule": "true", "definition": "规则"},
        [],
        confirm_hard_rule=True,
    )
    assert result.get("is_hard_rule") == "true"


# ----------------------------------------------------------------------
# 2. 动态状态与稳定资料分离
# ----------------------------------------------------------------------
def test_extract_state_changes_from_current_state():
    svc = SettingCardAcceptService()
    card = {"card_id": "loc_1", "type": "location", "first_appearance_chapter": ""}
    items = svc._extract_state_changes({"current_state": "开放", "name": "森林"}, card)
    assert len(items) == 1
    assert items[0]["entity_type"] == "location"
    assert items[0]["entity_id"] == "loc_1"
    assert items[0]["event_type"] == "state_change"
    assert items[0]["after"] == {"current_state": "开放"}


def test_extract_state_changes_from_state_changes_list():
    svc = SettingCardAcceptService()
    card = {"card_id": "itm_1", "type": "item", "first_appearance_chapter": ""}
    items = svc._extract_state_changes(
        {
            "state_changes": [
                {"entity_type": "item", "entity_id": "itm_1", "before": {}, "after": {"durability": "受损"}}
            ]
        },
        card,
    )
    assert len(items) == 1
    assert items[0]["after"] == {"durability": "受损"}


def test_extract_state_changes_empty_without_dynamic_state():
    svc = SettingCardAcceptService()
    card = {"card_id": "rul_1", "type": "rule"}
    assert svc._extract_state_changes({"name": "规则", "fields": {}}, card) == []


# ----------------------------------------------------------------------
# 3. 并发认领与重复采纳（claim_candidate 分支逻辑）
# ----------------------------------------------------------------------
def test_claim_candidate_success():
    async def _run():
        with patch.object(
            candidate_accept_service.generation_record_repo,
            "set_candidate_status",
            new=AsyncMock(return_value=True),
        ):
            with patch.object(
                candidate_accept_service,
                "resolve_candidate",
                new=AsyncMock(return_value={"candidate_id": "gen1#c1", "status": "pending"}),
            ):
                # 认领成功不应抛异常
                await candidate_accept_service.claim_candidate("n", "gen1", "gen1#c1", "op1")

    run(_run())


def test_claim_candidate_already_accepted():
    async def _run():
        with patch.object(
            candidate_accept_service.generation_record_repo,
            "set_candidate_status",
            new=AsyncMock(return_value=False),
        ):
            with patch.object(
                candidate_accept_service,
                "resolve_candidate",
                side_effect=[
                    {"candidate_id": "gen1#c1", "status": "pending"},
                    {"candidate_id": "gen1#c1", "status": "accepted"},
                ],
            ):
                with pytest.raises(ServiceError) as exc:
                    await candidate_accept_service.claim_candidate("n", "gen1", "gen1#c1", "op1")
        assert exc.value.code == "CANDIDATE_ALREADY_ACCEPTED"

    run(_run())


def test_claim_candidate_already_resolved():
    async def _run():
        with patch.object(
            candidate_accept_service.generation_record_repo,
            "set_candidate_status",
            new=AsyncMock(return_value=False),
        ):
            with patch.object(
                candidate_accept_service,
                "resolve_candidate",
                side_effect=[
                    {"candidate_id": "gen1#c2", "status": "pending"},
                    {"candidate_id": "gen1#c2", "status": "ignored"},
                ],
            ):
                with pytest.raises(ServiceError) as exc:
                    await candidate_accept_service.claim_candidate("n", "gen1", "gen1#c2", "op1")
        assert exc.value.code == "CANDIDATE_ALREADY_RESOLVED"

    run(_run())


# ----------------------------------------------------------------------
# 4. 版本冲突（服务端版本推进）
# ----------------------------------------------------------------------
def test_check_server_versions_stale_blueprint():
    svc = SettingCardAcceptService()
    record = {"blueprint_version": 1}

    async def _run():
        with patch.object(
            SettingCardAcceptService,
            "_current_blueprint_version",
            new=AsyncMock(return_value=2),
        ):
            with patch.object(
                SettingCardAcceptService,
                "_current_chapter_version",
                new=AsyncMock(return_value=None),
            ):
                with pytest.raises(ServiceError) as exc:
                    await svc._check_server_versions("n", record)
        assert exc.value.code == "VERSION_STALE"

    run(_run())


def test_check_server_versions_ok_when_versions_match():
    svc = SettingCardAcceptService()
    record = {"blueprint_version": 3}

    async def _run():
        with patch.object(
            SettingCardAcceptService,
            "_current_blueprint_version",
            new=AsyncMock(return_value=3),
        ):
            with patch.object(
                SettingCardAcceptService,
                "_current_chapter_version",
                new=AsyncMock(return_value=None),
            ):
                # 版本一致不抛异常
                await svc._check_server_versions("n", record, blueprint_version=3)

    run(_run())


# ----------------------------------------------------------------------
# 5. 正文范围定位与幂等哈希纯函数
# ----------------------------------------------------------------------
def test_resolve_range_unique():
    start, end = TextRevisionService._resolve_range("他去了森林", {"before_text": "森林"})
    assert "他去了森林"[start:end] == "森林"


def test_resolve_range_not_unique():
    with pytest.raises(ServiceError) as exc:
        TextRevisionService._resolve_range("他去了森林，森林很大", {"before_text": "森林"})
    assert exc.value.code == "TEXT_ANCHOR_NOT_UNIQUE"


def test_resolve_range_not_found():
    with pytest.raises(ServiceError) as exc:
        TextRevisionService._resolve_range("他去了森林", {"before_text": "大海"})
    assert exc.value.code == "TEXT_ANCHOR_INVALID"


def test_apply_operation_variants():
    assert TextRevisionService._apply_operation("abc", 1, 2, operation="replace", text="X") == "aXc"
    assert TextRevisionService._apply_operation("abc", 1, 1, operation="insert", text="X") == "aXbc"
    assert TextRevisionService._apply_operation("abc", 1, 2, operation="delete", text="") == "ac"


def test_build_request_hash_deterministic_and_order_insensitive():
    assert build_request_hash({"a": 1, "b": 2}) == build_request_hash({"b": 2, "a": 1})
    assert build_request_hash({"a": 1}) != build_request_hash({"a": 2})


# ----------------------------------------------------------------------
# 6. DTO 校验
# ----------------------------------------------------------------------
def test_dto_rejects_invalid_decision():
    with pytest.raises(ValidationError):
        CandidateAcceptRequest(novel_id="n", generation_id="g", request_id="r", decision="bad")


def test_dto_rejects_invalid_action():
    with pytest.raises(ValidationError):
        CandidateAcceptRequest(
            novel_id="n", generation_id="g", request_id="r", decision="accept", action="drop"
        )


def test_dto_accepts_ignore_decision():
    req = CandidateAcceptRequest(
        novel_id="n", generation_id="g", request_id="r", decision="ignore"
    )
    assert req.decision == "ignore"
