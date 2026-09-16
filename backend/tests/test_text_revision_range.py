"""正文修正精确范围替换的纯函数单元测试（T02）。

覆盖：精确范围优先、重复原文唯一定位、occurrence 选择、replace/insert/delete 操作，
以及范围越界 / 原文不唯一时的明确报错，不依赖数据库。
"""

from __future__ import annotations

import pytest

from backend.api.error_contract import ServiceError
from backend.services.llm.text_revision_service import TextRevisionService


def test_locate_exact_range_preferred_over_repeated_text():
    # 正文里"再见"出现两次，但请求给定了精确范围，必须命中指定那一处
    content = "甲乙再见丙丁再见戊"
    # "再见"分别位于 [2,4) 与 [6,8)
    assert content[2:4] == "再见"
    assert content[6:8] == "再见"
    located = TextRevisionService._locate_generation_range(
        content,
        "再见",
        {"start": 6, "end": 8},
    )
    assert located == (6, 8)


def test_locate_ambiguous_without_range_returns_none():
    content = "他说再见，她也说再见。"
    located = TextRevisionService._locate_generation_range(content, "再见", {})
    assert located is None


def test_locate_unique_anchor():
    content = "窗外下着小雨，他推门而入。"
    located = TextRevisionService._locate_generation_range(content, "推门而入", {})
    assert content[located[0] : located[1]] == "推门而入"


def test_locate_missing_text_returns_none():
    content = "今天天气不错。"
    assert TextRevisionService._locate_generation_range(content, "不存在的句子", {}) is None


def test_resolve_range_occurrence_selects_second():
    content = "甲说：好的。乙说：好的。"
    revision = {"target_range": {}, "before_text": "好的"}
    start, end = TextRevisionService._resolve_range(content, revision, occurrence=1)
    assert content[start:end] == "好的"
    # 第二处"好的"应晚于第一处
    assert start > content.find("好的")


def test_resolve_range_not_unique_without_occurrence_raises():
    content = "甲说：好的。乙说：好的。"
    revision = {"target_range": {}, "before_text": "好的"}
    with pytest.raises(ServiceError) as exc:
        TextRevisionService._resolve_range(content, revision)
    assert exc.value.code == "TEXT_ANCHOR_NOT_UNIQUE"


def test_resolve_range_out_of_bounds_raises():
    # 精确范围越界且原文无法定位时，必须明确报错而不是静默失败
    content = "短"
    revision = {"target_range": {"start": 0, "end": 10}, "before_text": "不存在"}
    with pytest.raises(ServiceError) as exc:
        TextRevisionService._resolve_range(content, revision)
    assert exc.value.code == "TEXT_ANCHOR_INVALID"


def test_resolve_range_empty_anchor_raises():
    # 范围越界且没有原文锚点时，无法安全定位
    content = "短"
    revision = {"target_range": {"start": 0, "end": 10}, "before_text": ""}
    with pytest.raises(ServiceError) as exc:
        TextRevisionService._resolve_range(content, revision)
    assert exc.value.code == "TEXT_ANCHOR_INVALID"


def test_apply_replace():
    assert TextRevisionService._apply_operation(
        "ABCHDEF", 1, 3, operation="replace", text="XYZ"
    ) == "AXYZHDEF"


def test_apply_insert():
    assert TextRevisionService._apply_operation(
        "ABCD", 2, 2, operation="insert", text="X"
    ) == "ABXCD"


def test_apply_delete():
    assert TextRevisionService._apply_operation(
        "ABCDE", 1, 4, operation="delete", text=""
    ) == "AE"


def test_apply_invalid_operation_raises():
    with pytest.raises(ServiceError) as exc:
        TextRevisionService._apply_operation("ABCD", 0, 1, operation="move", text="X")
    assert exc.value.code == "TEXT_OPERATION_INVALID"
