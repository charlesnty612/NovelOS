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
    strip_observer_violations,
    strip_think_blocks,
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


# ---------------------------------------------------------------------------
# finish_reason 透传与 length 分支文案（V3.9+ 排障体验修复）
# ---------------------------------------------------------------------------


def test_extract_json_empty_with_length_finish_reason_raises_actionable_message():
    """content='' + finish_reason='length' → 报错文案明确提示 max_tokens 预算被思考耗尽，
    给「调大 max_tokens（建议 16384）」可行动指引，不再误导「解析器问题」。
    """
    with pytest.raises(AgentOutputError) as exc:
        extract_json("", finish_reason="length")
    msg = str(exc.value)
    assert "max_tokens" in msg
    assert "16384" in msg
    assert "length" in msg


def test_extract_json_empty_with_stop_finish_reason_keeps_original_message_with_suffix():
    """content='' + finish_reason='stop' → 保留原「empty output after stripping fences」文案，
    末尾追加 ``(finish_reason=stop)`` 便于排障（与既有报错兼容 + 增量诊断）。
    """
    with pytest.raises(AgentOutputError) as exc:
        extract_json("", finish_reason="stop")
    msg = str(exc.value)
    assert msg.startswith("empty output after stripping fences")
    assert "(finish_reason=stop)" in msg


def test_extract_json_empty_without_finish_reason_keeps_original_message():
    """content='' + finish_reason=None → 既有行为零变化（与历史报错完全兼容）。"""
    with pytest.raises(AgentOutputError) as exc:
        extract_json("", finish_reason=None)
    assert str(exc.value) == "empty output after stripping fences"


def test_extract_json_empty_with_non_string_finish_reason_falls_back():
    """上游偶发下发非字符串 finish_reason（如整型 / None / dict）→ 走「未知」分支，
    不误判 length、不污染文案。仅当 ``isinstance(str) and non-empty`` 时才追加后缀。
    """
    with pytest.raises(AgentOutputError) as exc:
        extract_json("", finish_reason=None)
    assert "(finish_reason=" not in str(exc.value)
    # 显式 None 与缺省参数等价
    with pytest.raises(AgentOutputError) as exc2:
        extract_json("")
    assert "(finish_reason=" not in str(exc2.value)


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


# ---------------------------------------------------------------------------
# 第三级兜底：json_repair（Sprint 4 生产事故：observer char 562 缺逗号，
# strict=False 救不回，重试也失败 → 加 json_repair 自动修复）
# ---------------------------------------------------------------------------


def test_extract_json_repairs_missing_comma_between_array_elements():
    """LLM 在数组元素之间漏掉逗号（生产事故典型形态）→ 三级兜底修复成功。"""
    payload, meta = extract_json('{"a":["x" "y"]}', return_meta=True)
    assert payload == {"a": ["x", "y"]}
    assert meta == {"repaired": True}


def test_extract_json_repairs_missing_comma_between_object_keys():
    """LLM 在两个顶层键之间漏掉逗号 → 修复成功。"""
    payload, meta = extract_json('{"a":1 "b":2}', return_meta=True)
    assert payload == {"a": 1, "b": 2}
    assert meta["repaired"] is True


def test_extract_json_repairs_chinese_long_string_with_unescaped_quotes():
    """中文长字符串内含未转义双引号 → 修复成功（json_repair 对中文内容实测 OK）。"""
    raw = '{"text":"他说：\"你好\"，继续往下写"}'
    payload, meta = extract_json(raw, return_meta=True)
    assert meta["repaired"] is True
    assert payload["text"].startswith("他说")
    assert "你好" in payload["text"]


def test_extract_json_clean_json_no_repair_meta_false():
    """干净 JSON → repaired=False，不触发第三级（零开销覆盖）。"""
    payload, meta = extract_json('{"a": 1, "b": [2, 3]}', return_meta=True)
    assert payload == {"a": 1, "b": [2, 3]}
    assert meta == {"repaired": False}


def test_extract_json_return_meta_default_false_keeps_legacy_dict_contract():
    """return_meta 不传（默认 False）→ 行为与旧版完全一致：返回 dict 而非 tuple。
    该断言保护既有调用方（runner / 测试套件）的零侵入契约。
    """
    result = extract_json('{"a":1 "b":2}')
    assert isinstance(result, dict)
    assert result == {"a": 1, "b": 2}


def test_extract_json_unrepairable_garbage_raises_with_pos_context():
    """花括号内是 LLM 偶发输出但顶层不是 dict 的形态（json_repair 修复后是 list）→
    仍抛 AgentOutputError，错误消息含 pos 上下文。
    这种样本模拟「LLM 输出本身不含字典结构」，前两级 strict 也救不回。
    """
    # {[1,2,3]}：有 {} 让 braces 分支通过，前两级 json.loads 失败，
    # json_repair 修复为 list（顶层非 dict），被「非 dict 视为修复失败」分支拦截抛错
    garbage = "{[1,2,3]}"
    with pytest.raises(AgentOutputError) as exc:
        extract_json(garbage)
    msg = str(exc.value)
    # 错误消息需带 pos= 上下文（排障关键证据）
    assert "pos=" in msg
    assert "invalid JSON" in msg


def test_extract_json_truncated_json_repaired():
    """截断 JSON（首尾花括号闭合但内部未闭合）→ json_repair 自动补齐成功。"""
    # 闭合花括号已加，但内部字符串未闭合——json_repair 实测能补全
    payload, meta = extract_json('{"a":1, "b":[1,2,3}', return_meta=True)
    assert payload["a"] == 1
    assert payload["b"] == [1, 2, 3]
    assert meta["repaired"] is True
