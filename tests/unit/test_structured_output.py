"""结构化输出提取 + 契约校验单测（Sprint 3）。

覆盖：
- 提取：纯 JSON / 带 ```json 围栏 / 前后带废话 / 坏 JSON / 空文本。
- 契约：observer 顶层白名单 / observer 越权（剥离，非抛错）/ director schema_version /
  writer schema_version + prose + self_report / None 跳过。
- 剥离：strip_observer_violations 元信息 + 辅助字段剥离 + 自动补齐缺失数组。
"""

from __future__ import annotations

import pytest

from packages.core.agent_runtime.exceptions import AgentOutputError
from packages.core.agent_runtime.structured_output import (
    OBSERVER_ALLOWED_KEYS,
    OBSERVER_FORBIDDEN_KEYS,
    extract_json,
    strip_code_fence,
    strip_think_blocks,
    strip_observer_violations,
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


def test_extract_json_removes_think_block():
    raw = "<think>internal reasoning\nwith a newline</think>\n```json\n{\"ok\": true}\n```"
    assert extract_json(raw) == {"ok": True}


def test_strip_think_blocks_is_multiline_and_case_insensitive():
    raw = "前文<think>\nFirst line\nsecond line\n</think>正文"
    cleaned = strip_think_blocks(raw)
    assert cleaned == "前文正文"


def test_strip_think_blocks_preserves_literal_unclosed_tag():
    assert strip_think_blocks("正文<think>未结束") == "正文<think>未结束"


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


def test_validate_contract_observer_forbidden_key_no_longer_raises():
    """P2-1 修订：越权字段由 strip_observer_violations 剥离；validate_contract 不再抛错。

    仅当 7 数组齐全且均为 list 时视为结构合法；越权字段是剥离范畴，非契约失败。
    """
    payload = _valid_observer_payload()
    payload["delta_id"] = "dlt_xyz"
    validate_contract("observer", payload)  # 不抛错


def test_validate_contract_observer_deviations_no_longer_raises():
    payload = _valid_observer_payload()
    payload["deviations"] = []
    validate_contract("observer", payload)  # 不抛错


def test_validate_contract_observer_non_list_value():
    payload = _valid_observer_payload()
    payload["new_events"] = {}
    with pytest.raises(AgentOutputError):
        validate_contract("observer", payload)


# ---------------------------------------------------------------------------
# strip_observer_violations 剥离策略
# ---------------------------------------------------------------------------


def test_strip_observer_violations_strips_metadata():
    payload = _valid_observer_payload()
    payload["delta_id"] = "dlt_xyz"
    payload["schema_version"] = "chapter-extract.v0"
    payload["notes"] = "should be stripped"
    cleaned, stripped = strip_observer_violations(payload)
    assert "delta_id" not in cleaned
    assert "schema_version" not in cleaned
    assert "notes" not in cleaned
    assert set(cleaned.keys()) == OBSERVER_ALLOWED_KEYS
    assert set(stripped) == {"delta_id", "schema_version", "notes"}


def test_strip_observer_violations_strips_prompt_aux_fields():
    payload = _valid_observer_payload()
    payload["deviations"] = [{"x": 1}]
    payload["self_check"] = "ok"
    payload["unresolved_plan_intents"] = []
    cleaned, stripped = strip_observer_violations(payload)
    assert set(cleaned.keys()) == OBSERVER_ALLOWED_KEYS
    assert set(stripped) == {"deviations", "self_check", "unresolved_plan_intents"}


def test_strip_observer_violations_auto_fills_missing_arrays():
    """P2-1 边界：剥离时自动补齐缺失的 7 数组为 []（不计入 stripped_keys）。"""
    payload = {"character_changes": [{"x": 1}], "delta_id": "dlt_xyz"}
    cleaned, stripped = strip_observer_violations(payload)
    for k in OBSERVER_ALLOWED_KEYS:
        assert k in cleaned
        assert isinstance(cleaned[k], list)
    # 缺省数组被自动补为 []
    assert cleaned["new_events"] == []
    assert cleaned["world_changes"] == []
    # stripped 仅含越权字段，不含自动补的 5 个空数组
    assert stripped == ["delta_id"]


def test_strip_observer_violations_empty_payload_returns_seven_empty_arrays():
    cleaned, stripped = strip_observer_violations({})
    assert set(cleaned.keys()) == OBSERVER_ALLOWED_KEYS
    for k in OBSERVER_ALLOWED_KEYS:
        assert cleaned[k] == []
    assert stripped == []


def test_strip_observer_violations_strips_unknown_extras_too():
    """未知字段（既非白名单也非越权）按越权处理——与原 _validate_observer 严格语义一致。"""
    payload = _valid_observer_payload()
    payload["rogue_extra"] = "x"
    cleaned, stripped = strip_observer_violations(payload)
    assert "rogue_extra" not in cleaned
    assert "rogue_extra" in stripped


def test_strip_observer_violations_preserves_array_contents():
    payload = _valid_observer_payload()
    payload["new_events"] = [{"event_id": "e1"}]
    cleaned, _ = strip_observer_violations(payload)
    assert cleaned["new_events"] == [{"event_id": "e1"}]


def test_strip_observer_violations_keys_sorted():
    payload = _valid_observer_payload()
    payload["delta_id"] = "1"
    payload["schema_version"] = "v"
    payload["notes"] = "n"
    _, stripped = strip_observer_violations(payload)
    assert stripped == sorted(stripped)
    # 校验 13 个越权键全部覆盖
    assert len(OBSERVER_FORBIDDEN_KEYS) == 13


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
