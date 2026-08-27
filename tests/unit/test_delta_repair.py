"""Delta 确定性自动修复（arbiter-lite）单元测试。"""

from __future__ import annotations

import pytest

from packages.core.story_state.delta_repair import repair_delta
from packages.core.story_state.validator import validate_delta


def _minimal_delta(**overrides):
    delta = {
        "delta_id": "dlt_test",
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": "ch_test",
        "workflow_run_id": "run_test",
        "previous_state_version": 1,
        "created_by": "observer:v1",
        "created_at": "2026-01-01T00:00:00Z",
        "supersedes": None,
        "notes": None,
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    delta.update(overrides)
    return delta


def _evidence(chapter_id: str = "ch_test"):
    return {
        "chapter_id": chapter_id,
        "excerpt": " excerpt ",
    }


# ---------------------------------------------------------------------------
# fill-before 规则
# ---------------------------------------------------------------------------


def test_repair_world_change_fill_before_from_snapshot():
    delta = _minimal_delta(
        world_changes=[
            {
                "change_id": "wc_1",
                "op": "update",
                "target_id": "loc_1",
                "world_kind": "location",
                "world_id": "loc_1",
                "field": "data_json.atmosphere",
                "before": None,
                "after": "明亮",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {
        "world": {
            "locations": {
                "loc_1": {
                    "name": "京城",
                    "data_json": {"atmosphere": "阴暗"},
                }
            }
        }
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "fill-before"
    assert repairs[0]["array"] == "world_changes"
    assert repairs[0]["target_id"] == "loc_1"
    assert repaired["world_changes"][0]["before"] == "阴暗"
    assert validate_delta(repaired) == []


def test_repair_character_state_fill_before_from_snapshot():
    delta = _minimal_delta(
        character_changes=[
            {
                "change_id": "cc_1",
                "op": "update",
                "target_id": "char_1",
                "character_id": "char_1",
                "facet": "state",
                "field": "state.location",
                "before": None,
                "after": "京城",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {
        "characters": [
            {
                "character_id": "char_1",
                "current_state": {"location": "江南"},
            }
        ]
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "fill-before"
    assert repaired["character_changes"][0]["before"] == "江南"
    assert validate_delta(repaired) == []


def test_repair_relationship_change_fill_before_from_snapshot():
    delta = _minimal_delta(
        relationship_changes=[
            {
                "change_id": "rc_1",
                "op": "update",
                "target_id": "rel_1",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "friend",
                "before": None,
                "after": {"trust": 0.9},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {
        "characters": [
            {
                "character_id": "char_a",
                "relationships": [
                    {
                        "relationship_id": "rel_1",
                        "from_character_id": "char_a",
                        "to_character_id": "char_b",
                        "relation_type": "friend",
                        "state_json": {"trust": 0.5},
                    }
                ],
            }
        ]
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "fill-before"
    assert repaired["relationship_changes"][0]["before"] == {"trust": 0.5}
    assert validate_delta(repaired) == []


# ---------------------------------------------------------------------------
# insert-to-update / drop-duplicate 规则
# ---------------------------------------------------------------------------


def test_repair_relationship_insert_to_update_when_id_exists():
    delta = _minimal_delta(
        relationship_changes=[
            {
                "change_id": "rc_1",
                "op": "add",
                "target_id": "rel_1",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "friend",
                "after": {"trust": 0.9},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {
        "characters": [
            {
                "character_id": "char_a",
                "relationships": [
                    {
                        "relationship_id": "rel_1",
                        "from_character_id": "char_a",
                        "to_character_id": "char_b",
                        "relation_type": "friend",
                        "state_json": {"trust": 0.5},
                    }
                ],
            }
        ]
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "insert-to-update"
    assert repaired["relationship_changes"][0]["op"] == "update"
    assert repaired["relationship_changes"][0]["before"] == {"trust": 0.5}
    assert validate_delta(repaired) == []


def test_repair_relationship_drop_duplicate_when_after_equals_current():
    delta = _minimal_delta(
        relationship_changes=[
            {
                "change_id": "rc_1",
                "op": "add",
                "target_id": "rel_1",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "friend",
                "after": {"trust": 0.5},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {
        "characters": [
            {
                "character_id": "char_a",
                "relationships": [
                    {
                        "relationship_id": "rel_1",
                        "from_character_id": "char_a",
                        "to_character_id": "char_b",
                        "relation_type": "friend",
                        "state_json": {"trust": 0.5},
                    }
                ],
            }
        ]
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "drop-duplicate"
    assert repaired["relationship_changes"] == []
    assert validate_delta(repaired) == []


def test_repair_world_change_add_existing_location_insert_to_update():
    delta = _minimal_delta(
        world_changes=[
            {
                "change_id": "wc_1",
                "op": "add",
                "target_id": "loc_1",
                "world_kind": "location",
                "world_id": "loc_1",
                "field": "data_json.population",
                "after": 1000,
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {
        "world": {
            "locations": {
                "loc_1": {
                    "name": "京城",
                    "data_json": {"population": 500},
                }
            }
        }
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "insert-to-update"
    assert repaired["world_changes"][0]["op"] == "update"
    assert repaired["world_changes"][0]["before"] == 500
    assert validate_delta(repaired) == []


# ---------------------------------------------------------------------------
# 不可修降级
# ---------------------------------------------------------------------------


def test_unrepairable_update_before_none_when_entity_missing():
    delta = _minimal_delta(
        world_changes=[
            {
                "change_id": "wc_1",
                "op": "update",
                "target_id": "loc_missing",
                "world_kind": "location",
                "world_id": "loc_missing",
                "field": "data_json.atmosphere",
                "before": None,
                "after": "明亮",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {"world": {"locations": {}}}
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert repairs == []
    assert repaired["world_changes"][0]["before"] is None
    errors = validate_delta(repaired)
    assert any("before 为 None" in e for e in errors)


# ---------------------------------------------------------------------------
# 其它数组
# ---------------------------------------------------------------------------


def test_repair_debt_change_fill_status_before():
    delta = _minimal_delta(
        debt_changes=[
            {
                "change_id": "dc_1",
                "op": "update",
                "target_id": "debt_1",
                "debt_id": "debt_1",
                "status_before": None,
                "status_after": "paid",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {"debts": [{"debt_id": "debt_1", "status": "open", "severity": 0.5}]}
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 2  # status_before + severity_before
    status_repairs = [r for r in repairs if r["field"] == "status_before"]
    assert status_repairs
    assert repaired["debt_changes"][0]["status_before"] == "open"
    assert validate_delta(repaired) == []


def test_repair_resolved_hook_fill_from_status():
    delta = _minimal_delta(
        resolved_hooks=[
            {
                "change_id": "rh_1",
                "op": "update",
                "target_id": "hook_1",
                "hook_id": "hook_1",
                "from_status": None,
                "to_status": "RESOLVED",
                "payoff_summary": "结算",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {"hooks": [{"hook_id": "hook_1", "status": "ACTIVE"}]}
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "fill-before"
    assert repaired["resolved_hooks"][0]["from_status"] == "ACTIVE"
    assert validate_delta(repaired) == []


def test_repair_new_event_drop_duplicate():
    delta = _minimal_delta(
        new_events=[
            {
                "change_id": "ne_1",
                "op": "add",
                "target_id": "ev_1",
                "event_id": "ev_1",
                "type": "revelation",
                "participants": ["char_1"],
                "time": {"timeline_day": 1},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {"events": {"ev_1": {"type": "revelation"}}}
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "drop-duplicate"
    assert repaired["new_events"] == []


def test_repair_new_hook_drop_duplicate():
    delta = _minimal_delta(
        new_hooks=[
            {
                "change_id": "nh_1",
                "op": "add",
                "target_id": "hook_1",
                "hook_id": "hook_1",
                "name": "伏笔",
                "importance": 0.8,
                "description": "desc",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {"hooks": [{"hook_id": "hook_1", "status": "OPEN"}]}
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "drop-duplicate"
    assert repaired["new_hooks"] == []


# ---------------------------------------------------------------------------
# 不改变输入的纯函数语义
# ---------------------------------------------------------------------------


def test_repair_does_not_mutate_input_delta():
    delta = _minimal_delta(
        world_changes=[
            {
                "change_id": "wc_1",
                "op": "update",
                "target_id": "loc_1",
                "world_kind": "location",
                "world_id": "loc_1",
                "field": "data_json.atmosphere",
                "before": None,
                "after": "明亮",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {"world": {"locations": {"loc_1": {"data_json": {"atmosphere": "阴暗"}}}}}
    original_before = delta["world_changes"][0]["before"]
    repair_delta(delta, snapshot=snapshot)
    assert delta["world_changes"][0]["before"] is original_before


# ---------------------------------------------------------------------------
# update-to-add-field 规则（实体存在但字段无现值 = 新增子键）
# ---------------------------------------------------------------------------


def test_repair_world_change_update_new_field_becomes_add():
    """生产实测 ch073：observer 对 loc 的 data_json 新子键用 op='update' 且 before=None，
    校验器拒绝（update 必须 before 非 None）。实体存在但字段无现值时应转为字段级 add。"""
    delta = _minimal_delta(
        world_changes=[
            {
                "change_id": "wc_1",
                "op": "update",
                "target_id": "loc_1",
                "world_kind": "location",
                "world_id": "loc_1",
                "field": "data_json.upper_bound_conclusion",
                "before": None,
                "after": "丑初二刻过半反转",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {
        "world": {
            "locations": {
                "loc_1": {"name": "京城", "data_json": {"atmosphere": "阴暗"}}
            }
        }
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "update-to-add-field"
    assert repaired["world_changes"][0]["op"] == "add"
    assert validate_delta(repaired) == []


def test_repair_character_state_update_new_field_becomes_add():
    delta = _minimal_delta(
        character_changes=[
            {
                "change_id": "cc_1",
                "op": "update",
                "target_id": "char_1",
                "character_id": "char_1",
                "facet": "state",
                "field": "state.new_track",
                "before": None,
                "after": "初现",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {
        "characters": [
            {"character_id": "char_1", "current_state": {"location": "江南"}}
        ]
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "update-to-add-field"
    assert repaired["character_changes"][0]["op"] == "add"
    assert validate_delta(repaired) == []


def test_repair_update_after_none_not_converted():
    """update 且 before/after 均为 None：语义空洞，不可修，留给校验器报错。"""
    delta = _minimal_delta(
        world_changes=[
            {
                "change_id": "wc_1",
                "op": "update",
                "target_id": "loc_1",
                "world_kind": "location",
                "world_id": "loc_1",
                "field": "data_json.atmosphere",
                "before": None,
                "after": None,
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {"world": {"locations": {"loc_1": {"data_json": {}}}}}
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert repairs == []
    assert repaired["world_changes"][0]["op"] == "update"
    assert validate_delta(repaired) != []
