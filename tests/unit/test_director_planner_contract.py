"""P1 规划合并：director_planner 契约 + 相邻对象合并兜底 + 输入 ID 白名单。

覆盖三块（对应 `_refs/p1_merged_prompt_v2.md`「上线前剩余事项」1 / 2 / 9）：

1. ``extract_json`` 相邻顶层对象合并兜底三态——单对象 / 双对象 / 键冲突；
2. ``validate_contract("director_planner")`` 合同三态——双契约齐备 / scene_plan 缺席
   （计划-only 降级）/ scene_plan 在场但结构违规；
3. 输入 ID 白名单（E-MRG-16 / E-DIR-03）——hook / debt / character / location，含
   「输入为空 ⇒ 输出必须 ``[]``」与「命中即 output-invalid」两条硬口径。

golden fixture：``tests/fixtures/p1_director_planner_merged_ch{3,4}.json`` 为二次 A/B 回放
（v0.2，2026-09-14）两章的真实产出，逐字节取自 `_refs/p1_ab/merged_v2_ch{3,4}.json`
（本测试不引用 _refs 路径）。白名单输入侧集合（两章同项目；hook / debt 两表实测为空）
内联在 :data:`_A_B_WHITELIST_INPUT`——口径见 `_refs/p1_ab/merged_v2_ch{3,4}_user_payload.json`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.agent_runtime.exceptions import AgentOutputError
from packages.core.agent_runtime.structured_output import (
    extract_json,
    validate_contract,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN_FILES = (
    "p1_director_planner_merged_ch3.json",
    "p1_director_planner_merged_ch4.json",
)

# A/B 二次回放（v0.2）白名单输入侧：两章同项目，hook_ledger_excerpt / narrative_debt_excerpt
# 实测为空数组（G2 零幻觉门的判定条件），available_* 为项目全量实体。
_A_B_WHITELIST_INPUT: dict = {
    "hook_ledger_excerpt": [],
    "narrative_debt_excerpt": [],
    "available_characters": [
        {"character_id": "char_edbb745cca44", "name": "n/a"},
        {"character_id": "char_f0be6a066e17", "name": "n/a"},
        {"character_id": "char_49a2ad9ac576", "name": "n/a"},
        {"character_id": "char_de36e230adbb", "name": "n/a"},
        {"character_id": "char_5934ce186818", "name": "n/a"},
    ],
    "available_locations": [
        {"location_id": "loc_d28b9affa14b", "name": "n/a"},
        {"location_id": "loc_04934c53e33b", "name": "n/a"},
        {"location_id": "loc_e68fd4e46150", "name": "n/a"},
        {"location_id": "loc_3fd31cd8c893", "name": "n/a"},
        {"location_id": "loc_20a0b72b6be1", "name": "n/a"},
    ],
}


def _load_golden(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _minimal_plan_only() -> dict:
    """计划-only 形态（无 scene_plan）：顶层导演契约的最小合规体。"""
    return {
        "schema_version": "director-plan.v1",
        "prompt_version": "director_planner:v1",
        "chapter_id": "ch_x",
        "chapter_goal": "单句目标",
        "core_conflict": "冲突",
        "turning_point": "转折",
        "expected_role": "setup",
        "key_beats": [],
        "character_changes_planned": [],
        "hook_handling": [],
        "debt_handling": [],
        "deviations": [],
        "knowledge_leakage_check": {"uses_hidden_knowledge": False, "leakage_details": None},
        "open_questions": [],
    }


def _minimal_scene_plan(chapter_id: str = "ch_x") -> dict:
    return {
        "schema_version": "scene-plan.v1",
        "prompt_version": "director_planner:v1",
        "chapter_id": chapter_id,
        "scenes": [
            {
                "scene_id": "scene_001",
                "purpose": "本场叙事功能",
                "characters": [],
                "location": None,
                "conflict": "核心冲突",
                "turn": None,
                "time_in_story": "当日",
                "pov": "third_person_limited",
                "pov_character_id": None,
                "information_boundary": [],
                "ending_hook": None,
                "target_words": 3000,
                "slots": [
                    {
                        "slot_id": "scene_001_action_01",
                        "type": "action",
                        "purpose": "本 slot 叙事任务",
                        "characters": [],
                        "target_mood": None,
                        "constraints": [],
                    }
                ],
            }
        ],
        "notes_for_writer": "",
        "deviations": [],
    }


# ---------------------------------------------------------------------------
# 1. extract_json：相邻顶层对象合并兜底（单对象 / 双对象 / 键冲突）
# ---------------------------------------------------------------------------


def test_merge_single_object_unchanged():
    """单对象形态走 plain 路径，不触发合并兜底（meta 可观测字段为 None）。"""
    payload, meta = extract_json('{"a": 1, "b": [2]}', return_meta=True)
    assert payload == {"a": 1, "b": [2]}
    assert meta == {"repaired": False, "merged_object_count": None}


def test_merge_two_adjacent_objects():
    """相邻双对象（ch3 首轮真实失败形态：导演计划 + scene_plan 两个顶层对象）→ 合并成功。"""
    raw = '前言\n{"director": {"x": 1}}\n\n{"scene_plan": {"scenes": []}}\n后记'
    payload, meta = extract_json(raw, return_meta=True)
    assert payload == {"director": {"x": 1}, "scene_plan": {"scenes": []}}
    assert meta == {"repaired": False, "merged_object_count": 2}


def test_merge_two_adjacent_objects_inside_code_fence():
    """围栏包裹的相邻双对象同样合并（去围栏后再扫描顶层对象）。"""
    raw = '```json\n{"x": 1}\n{"y": 2}\n```'
    payload, meta = extract_json(raw, return_meta=True)
    assert payload == {"x": 1, "y": 2}
    assert meta["merged_object_count"] == 2


def test_merge_key_conflict_raises():
    """键冲突（可能是两次完整回答）→ 不猜，判解析失败（触发既有重试）。"""
    with pytest.raises(AgentOutputError) as exc:
        extract_json('{"a": 1}{"a": 2}')
    assert "cannot be merged" in str(exc.value)
    assert "key conflicts" in str(exc.value)


def test_merge_identical_key_same_value_is_allowed():
    """同名键但取值相同 → 不算冲突（两对象是同一份内容的分片）。"""
    payload = extract_json('{"a": 1}{"a": 1, "b": 2}')
    assert payload == {"a": 1, "b": 2}


def test_merge_not_triggered_for_array_wrapped_objects():
    """数组包裹的多元素（``[{...}, {...}]``）不是「相邻顶层对象」→ 不合并（保持既有报错）。"""
    with pytest.raises(AgentOutputError):
        extract_json('[{"a": 1}, {"b": 2}]')


def test_merge_ignores_braces_inside_strings():
    """字符串内的花括号不计入配平——合并后内容保持完整。"""
    payload, meta = extract_json('{"a": "}{"}{"b": 2}', return_meta=True)
    assert payload == {"a": "}{", "b": 2}
    assert meta["merged_object_count"] == 2


# ---------------------------------------------------------------------------
# 2. validate_contract("director_planner")：双契约三态
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", GOLDEN_FILES)
def test_golden_merged_output_passes_double_contract(name: str):
    """A/B 实跑产出（真实模型输出）通过双契约 + 白名单（无输入侧集合也须通过结构态）。"""
    payload = _load_golden(name)
    validate_contract("director_planner", payload)
    validate_contract("scene_planner", payload["scene_plan"])
    validate_contract(
        "director_planner", payload, input_payload=_A_B_WHITELIST_INPUT
    )


@pytest.mark.parametrize("name", GOLDEN_FILES)
def test_golden_merged_output_zero_hallucinated_hook_debt_ids(name: str):
    """G2 零幻觉门：两章输入 hook/debt 空表 ⇒ 输出两表必须为 ``[]``；字数闭环 100%。"""
    payload = _load_golden(name)
    assert payload["hook_handling"] == []
    assert payload["debt_handling"] == []
    total_words = sum(s.get("target_words") or 0 for s in payload["scene_plan"]["scenes"])
    assert total_words == 3000, f"expected 3000, got {total_words}"


def test_director_planner_scene_plan_optional_plan_only():
    """scene_plan 缺席 = 计划-only 输出：不判契约失败（降级由 chapter-plan 记录）。"""
    validate_contract("director_planner", _minimal_plan_only())


def test_director_planner_valid_scene_plan_passes():
    payload = _minimal_plan_only()
    payload["scene_plan"] = _minimal_scene_plan()
    validate_contract("director_planner", payload)


def test_director_planner_rejects_bad_scene_schema_version():
    """子对象 schema_version 必须对齐 ``scene-plan.v1``（子契约走既有 scene_planner 校验器）。"""
    payload = _minimal_plan_only()
    scene_plan = _minimal_scene_plan()
    scene_plan["schema_version"] = "scene-plan.v2"
    payload["scene_plan"] = scene_plan
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("director_planner", payload)
    assert "scene-plan.v1" in str(exc.value)


def test_director_planner_rejects_non_object_scene_plan():
    payload = _minimal_plan_only()
    payload["scene_plan"] = "not an object"
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("director_planner", payload)
    assert "scene_plan must be an object" in str(exc.value)


def test_director_planner_rejects_empty_scenes():
    payload = _minimal_plan_only()
    scene_plan = _minimal_scene_plan()
    scene_plan["scenes"] = []
    payload["scene_plan"] = scene_plan
    with pytest.raises(AgentOutputError):
        validate_contract("director_planner", payload)


def test_director_planner_rejects_bad_scene_pov_and_slot_type():
    payload = _minimal_plan_only()
    scene_plan = _minimal_scene_plan()
    scene_plan["scenes"][0]["pov"] = "second_person"
    payload["scene_plan"] = scene_plan
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("director_planner", payload)
    assert "pov" in str(exc.value)

    payload = _minimal_plan_only()
    scene_plan = _minimal_scene_plan()
    scene_plan["scenes"][0]["slots"][0]["type"] = "monologue"
    payload["scene_plan"] = scene_plan
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("director_planner", payload)
    assert "type" in str(exc.value)


def test_director_planner_requires_director_schema_version():
    payload = _minimal_plan_only()
    payload["schema_version"] = "director-plan.v0"
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("director_planner", payload)
    assert "director-plan.v1" in str(exc.value)


# ---------------------------------------------------------------------------
# 3. 输入 ID 白名单（E-MRG-16 / E-DIR-03）
# ---------------------------------------------------------------------------


def _payload_with(**overrides) -> dict:
    payload = _minimal_plan_only()
    payload["scene_plan"] = _minimal_scene_plan()
    payload.update(overrides)
    return payload


def test_whitelist_empty_input_forbids_hook_and_debt_entries():
    """输入 hook/debt 两表为空 ⇒ 输出必须 ``[]``（命中即 output-invalid）。"""
    input_payload = dict(_A_B_WHITELIST_INPUT)
    payload = _payload_with(
        hook_handling=[{"hook_id": "hook_made_up", "action": "introduce", "rationale": "x"}],
        debt_handling=[{"debt_id": "debt_made_up", "action": "advance", "rationale": "y"}],
    )
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("director_planner", payload, input_payload=input_payload)
    msg = str(exc.value)
    assert "hook_handling[0] must be []" in msg
    assert "debt_handling[0] must be []" in msg


def test_whitelist_accepts_ids_present_in_input():
    """输入非空且 ID 命中集合 → 通过。"""
    input_payload = dict(_A_B_WHITELIST_INPUT)
    input_payload["hook_ledger_excerpt"] = [{"hook_id": "hook_real"}]
    input_payload["narrative_debt_excerpt"] = [{"debt_id": "debt_real"}]
    payload = _payload_with(
        hook_handling=[{"hook_id": "hook_real", "action": "advance", "rationale": "x"}],
        debt_handling=[{"debt_id": "debt_real", "action": "advance", "rationale": "y"}],
    )
    validate_contract("director_planner", payload, input_payload=input_payload)


def test_whitelist_rejects_unknown_hook_and_debt_ids_non_empty_input():
    """输入非空但 ID 不在集合内（幻觉 ID）→ 拒绝。"""
    input_payload = dict(_A_B_WHITELIST_INPUT)
    input_payload["hook_ledger_excerpt"] = [{"hook_id": "hook_real"}]
    payload = _payload_with(
        hook_handling=[{"hook_id": "hook_fake", "action": "advance", "rationale": "x"}],
    )
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("director_planner", payload, input_payload=input_payload)
    assert "not in input hook set" in str(exc.value)


def test_whitelist_rejects_unknown_character_and_location_ids():
    """beats / scenes / slots 引用的 character / location ID 必须 ∈ available_*。"""
    payload = _payload_with(
        key_beats=[
            {
                "beat_id": "beat_001",
                "purpose": "x",
                "involved_characters": ["char_ghost"],
                "involved_locations": ["loc_ghost"],
            }
        ]
    )
    scenes = payload["scene_plan"]["scenes"]
    scenes[0]["characters"] = ["char_ghost"]
    scenes[0]["location"] = "loc_ghost"
    scenes[0]["slots"][0]["characters"] = ["char_ghost"]
    with pytest.raises(AgentOutputError) as exc:
        validate_contract(
            "director_planner", payload, input_payload=_A_B_WHITELIST_INPUT
        )
    msg = str(exc.value)
    assert "key_beats[0].involved_characters" in msg
    assert "key_beats[0].involved_locations" in msg
    assert "scene[0].characters" in msg
    assert "scene[0].location" in msg
    assert "scene[0].slots[0].characters" in msg


def test_whitelist_null_location_allowed():
    """``scene.location`` 允许 null（新项目 / 无地点场景）。"""
    payload = _payload_with()
    payload["scene_plan"]["scenes"][0]["location"] = None
    validate_contract(
        "director_planner", payload, input_payload=_A_B_WHITELIST_INPUT
    )


def test_whitelist_not_applied_when_other_expected():
    """白名单只在 ``director_planner`` 档生效——其它 agent 行为零变化。"""
    payload = _minimal_plan_only()
    payload["hook_handling"] = [{"hook_id": "hook_made_up", "action": "advance", "rationale": "x"}]
    # director 档只查 schema_version（既有口径，不因本次改动收紧）
    validate_contract("director", payload, input_payload={"hook_ledger_excerpt": []})


def test_whitelist_error_is_capped():
    """白名单报错条数封顶（防重试提示被超长幻觉清单撑爆）。"""
    input_payload = dict(_A_B_WHITELIST_INPUT)
    payload = _payload_with(
        hook_handling=[
            {"hook_id": f"hook_fake_{i}", "action": "advance", "rationale": "x"}
            for i in range(12)
        ]
    )
    with pytest.raises(AgentOutputError) as exc:
        validate_contract("director_planner", payload, input_payload=input_payload)
    assert "(+7 more)" in str(exc.value)


def test_golden_fixture_mutation_is_caught_by_whitelist():
    """突变验证用途：往 golden 产出里注入一个幻觉 hook_id → 白名单必红。"""
    payload = _load_golden(GOLDEN_FILES[0])
    payload["hook_handling"] = [
        {"hook_id": "hook_fabricated_by_test", "action": "introduce", "rationale": "x"}
    ]
    with pytest.raises(AgentOutputError):
        validate_contract(
            "director_planner", payload, input_payload=_A_B_WHITELIST_INPUT
        )
