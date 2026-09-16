"""定稿门禁与作者裁决的单元测试（T03）。

覆盖：缺少审校记录被拒、幂等键必填、阻断项逐项裁决留痕、硬规则签名变化，
不依赖数据库（无审校ID与请求ID校验发生在任何数据访问之前）。
"""

from __future__ import annotations

import asyncio

import pytest

from backend.api.error_contract import ServiceError
from backend.services.novel.chapter_finalize_service import (
    FinalizeChapterService,
    _hard_rule_signature,
    finalize_chapter_service,
)


def test_finalize_requires_request_id():
    with pytest.raises(ServiceError) as exc:
        asyncio.run(
            finalize_chapter_service.finalize_plain(
                "novel_1",
                "chapter_1",
                request_id="",
                review_generation_id="gen_review",
            )
        )
    assert exc.value.code == "REQUEST_ID_REQUIRED"


def test_finalize_requires_review_id():
    # 缺少服务端审校记录，必须在任何写入之前被门禁拒绝
    with pytest.raises(ServiceError) as exc:
        asyncio.run(
            finalize_chapter_service.finalize_plain(
                "novel_1",
                "chapter_1",
                request_id="req-1",
                review_generation_id="",
            )
        )
    assert exc.value.code == "REVIEW_REQUIRED"


def test_evaluate_gate_blocking_unresolved_raises():
    issues = [
        {"issue_id": "iss_a", "severity": "blocking", "category": "fact", "content": "前后矛盾"},
    ]
    with pytest.raises(ServiceError) as exc:
        finalize_chapter_service._evaluate_gate(issues, [])
    assert exc.value.code == "FINALIZE_BLOCKED"
    assert "iss_a" in [item["issue_id"] for item in exc.value.details.get("blocking_issues", [])]


def test_evaluate_gate_exception_resolves_with_audit():
    issues = [
        {"issue_id": "iss_a", "severity": "blocking", "category": "fact", "content": "前后矛盾"},
        {"issue_id": "iss_w", "severity": "warning", "category": "style", "content": "节奏偏快"},
    ]
    gate = finalize_chapter_service._evaluate_gate(
        issues,
        [{"issue_id": "iss_a", "reason": "作者设定允许", "scope": "this_chapter"}],
    )
    # 作者裁决留痕：issue_id、reason、scope 都被持久化到返回值
    assert len(gate["exceptions"]) == 1
    exception = gate["exceptions"][0]
    assert exception["issue_id"] == "iss_a"
    assert exception["reason"] == "作者设定允许"
    assert exception["scope"] == "this_chapter"
    assert gate["unresolved"] == []


def test_evaluate_gate_empty_reason_rejected():
    issues = [{"issue_id": "iss_a", "severity": "blocking", "content": "矛盾"}]
    with pytest.raises(ServiceError) as exc:
        finalize_chapter_service._evaluate_gate(issues, [{"issue_id": "iss_a", "reason": ""}])
    assert exc.value.code == "FINALIZE_BLOCKED"


def test_evaluate_gate_unknown_issue_rejected():
    issues = [{"issue_id": "iss_a", "severity": "blocking", "content": "矛盾"}]
    with pytest.raises(ServiceError) as exc:
        finalize_chapter_service._evaluate_gate(
            issues, [{"issue_id": "iss_ghost", "reason": "无"}]
        )
    assert exc.value.code == "FINALIZE_BLOCKED"


def test_evaluate_gate_non_blocking_exception_rejected():
    issues = [
        {"issue_id": "iss_a", "severity": "blocking", "content": "矛盾"},
        {"issue_id": "iss_w", "severity": "warning", "content": "建议"},
    ]
    with pytest.raises(ServiceError) as exc:
        finalize_chapter_service._evaluate_gate(
            issues,
            [
                {"issue_id": "iss_a", "reason": "允许"},
                {"issue_id": "iss_w", "reason": "对非阻断项开例外"},
            ],
        )
    assert exc.value.code == "FINALIZE_BLOCKED"


def test_hard_rule_signature_changes_on_version():
    rules_v1 = [{"card_id": "rule_1", "version": 1}]
    rules_v2 = [{"card_id": "rule_1", "version": 2}]
    assert _hard_rule_signature(rules_v1) != _hard_rule_signature(rules_v2)


def test_annotate_issue_generates_stable_id():
    service = FinalizeChapterService()
    issue = service._annotate_issue(
        {"severity": "blocking", "content": "角色 A 在第 5 章已死亡，但本章又出场"}, 0
    )
    assert issue["issue_id"].startswith("iss_")
    assert issue["severity"] == "blocking"
