"""结构化输出提取 + 契约校验单测（Sprint 3）。

覆盖：
- 提取：纯 JSON / 带 ```json 围栏 / 前后带废话 / 坏 JSON / 空文本。
- 契约：observer 顶层白名单 / observer 越权 / director schema_version /
  writer schema_version + prose + self_report / None 跳过。
"""

from __future__ import annotations

import pytest

from packages.core.agent_runtime.exceptions import AgentOutputError
from packages.core.agent_runtime.structured_output import (
    OBSERVER_ALLOWED_KEYS,
    extract_json,
    strip_code_fence,
    validate_contract,
)

# ---------------------------------------------------------------------------
# extract_json 提取
# ---------------------------------------------------------------------------


def test_extract_json_pure_json():
    payload = extract_json('{"a": 1, "b": [2, 3]}')
    assert payload == {"a": 1, "b": [2, 3]}


def test_extract_json_with_code_fence():
    raw = '```json\n{"foo": "bar"}\n```'
    assert extract_json(raw) == {"foo": "bar"}


def test_extract_json_with_surrounding_text():
    raw = '好的，结果如下：\n```json\n{"x": 1}\n```\n以上。'
    assert extract_json(raw) == {"x": 1}


def test_extract_json_with_no_fence_but_surrounding_text():
    raw = '这是分析：\n{"k": "v"}\n结束。'
    assert extract_json(raw) == {"k": "v"}


def test_extract_json_malformed_raises():
    with pytest.raises(AgentOutputError):
        extract_json("{not a json}")


def test_extract_json_empty_raises():
    with pytest.raises(AgentOutputError):
        extract_json("")


def test_extract_json_no_braces_raises():
    with pytest.raises(AgentOutputError):
        extract_json("nothing here")


def test_extract_json_non_dict_raises():
    with pytest.raises(AgentOutputError):
        extract_json("[1, 2, 3]")


def test_strip_code_fence_removes_both_markers():
    cleaned = strip_code_fence("```json\nfoo\n```")
    assert "```" not in cleaned
    assert "foo" in cleaned


# ---------------------------------------------------------------------------
# validate_contract 契约校验
# ---------------------------------------------------------------------------


def _valid_observer_payload() -> dict:
    return {k: [] for k in OBSERVER_ALLOWED_KEYS}


def test_validate_contract_observer_ok():
    validate_contract("observer", _valid_observer_payload())


def test_validate_contract_observer_missing_array():
    payload = _valid_observer_payload()
    del payload["new_events"]
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("observer", payload)
    assert "new_events" in str(exc.value)


def test_validate_contract_observer_forbidden_key():
    payload = _valid_observer_payload()
    payload["delta_id"] = "dlt_xyz"
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("observer", payload)
    assert "delta_id" in str(exc.value)


def test_validate_contract_observer_deviations_forbidden():
    payload = _valid_observer_payload()
    payload["deviations"] = []
    with pytest.raises(AgentOutputError):
        validate_contract("observer", payload)


def test_validate_contract_observer_non_list_value():
    payload = _valid_observer_payload()
    payload["new_events"] = {}
    with pytest.raises(AgentOutputError):
        validate_contract("observer", payload)


def test_validate_contract_director_ok():
    validate_contract("director", {"schema_version": "director-plan.v1", "chapter_id": "ch_1"})


def test_validate_contract_director_missing_schema_version():
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("director", {"chapter_id": "ch_1"})
    assert "director-plan.v1" in str(exc.value)


def test_validate_contract_writer_ok():
    validate_contract("writer", {
        "schema_version": "writer-output.v1",
        "prose": "正文",
        "self_report": {"word_count": 1, "scene_count": 1},
    })


def test_validate_contract_writer_missing_prose():
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("writer", {
            "schema_version": "writer-output.v1",
            "self_report": {},
        })
    assert "prose" in str(exc.value)


def test_validate_contract_writer_missing_self_report():
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("writer", {
            "schema_version": "writer-output.v1",
            "prose": "x",
        })
    assert "self_report" in str(exc.value)


def test_validate_contract_none_passes_anything():
    validate_contract(None, {"anything": True})


def test_validate_contract_unknown_passes_through():
    # 未知 expected → 当 None 处理
    validate_contract("foo", {"anything": True})
