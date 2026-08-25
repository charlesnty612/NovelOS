"""validate_delta 引用实体存在性校验 单测（M3 引擎包）。

覆盖（M3 任务书）：
- 引用存在通过：character/world/hook/debt/relationship 引用 snapshot 已存在的实体 → 0 错误。
- 引用不存在报错：character_id / world_id / hook_id / debt_id / event participant 引用
  不存在的实体 → business 错误。
- 本 delta add 自愈通过：在同一 delta 中先 add 一个 character，再在 relationship_changes
  / new_events.participants 中引用它 → 不报错（自愈合法）。
- 字段缺失跳过：change 项缺 character_id / from_character_id 等字段 → 不抛异常、不报错。
- snapshot 缺失 / None：整体跳过引用存在性校验（向后兼容）。

设计要点：
- 复用 test_validator.py 中的 _good_delta() / _evidence() 思路，但本文件独立构造以
  避免测试间依赖。构造合法 delta 时按 state-delta.schema.json 字段填全，避免 schema
  校验先失败遮蔽业务校验。
"""

from __future__ import annotations

# ruff: noqa: I001  —— 文件仅含 1 个 import，I001 误报
from packages.core.story_state.validator import validate_delta


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _good_delta() -> dict:
    return {
        "delta_id": "dlt_aaaaaaaaaaaaaaaa",
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": "ch_test",
        "workflow_run_id": "wfr_test",
        "previous_state_version": 1,
        "created_by": "observer:v1",
        "created_at": "2026-08-23T10:00:00+00:00",
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


def _evidence(chapter_id: str = "ch_test") -> dict:
    return {"chapter_id": chapter_id, "scene_id": None, "excerpt": "excerpt", "span": None}


def _base_snapshot(
    *,
    characters: list[str] | None = None,
    locations: list[str] | None = None,
    factions: list[str] | None = None,
    world_rules: list[str] | None = None,
    hooks: list[str] | None = None,
    debts: list[str] | None = None,
    events: list[str] | None = None,
) -> dict:
    """构造一个「引用存在性校验」用的最小 snapshot。"""
    return {
        "state_version": 1,
        "characters": [
            {"character_id": cid, "name": cid, "facet": "state",
             "current_state": {}, "knowledge": [], "beliefs": [], "relationships": []}
            for cid in (characters or [])
        ],
        "world": {
            "current_time_in_story": None,
            "locations": {lid: {"name": lid} for lid in (locations or [])},
            "factions": {fid: {"name": fid} for fid in (factions or [])},
            "world_rules": [
                {"world_rule_id": rid, "name": rid, "statement": "", "data_json": {}, "visibility": "PUBLIC"}
                for rid in (world_rules or [])
            ],
            "active_resources": {},
        },
        "hooks": [{"hook_id": hid, "status": "OPEN"} for hid in (hooks or [])],
        "debts": [{"debt_id": did, "status": "open"} for did in (debts or [])],
        "recent_events": list(events or []),
        "events": {eid: {"type": "transition"} for eid in (events or [])},
    }


# ---------------------------------------------------------------------------
# 1. 引用存在通过
# ---------------------------------------------------------------------------


def test_reference_existence_passes_when_in_snapshot():
    snap = _base_snapshot(characters=["char_alice", "char_bob"])
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_alice",
            "character_id": "char_alice",
            "facet": "state",
            "field": "state.location",
            "before": "Forest",
            "after": "Cave",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert errors == []


def test_reference_existence_no_snapshot_does_not_check():
    """snapshot=None → 整体跳过引用存在性校验（向后兼容）。"""
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_ghost",
            "character_id": "char_ghost",
            "facet": "state",
            "field": "state.location",
            "before": "X",
            "after": "Y",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta)
    assert errors == []


# ---------------------------------------------------------------------------
# 2. 引用不存在报错
# ---------------------------------------------------------------------------


def test_reference_existence_missing_character_id_reports_business_error():
    snap = _base_snapshot(characters=["char_alice"])
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_ghost",
            "character_id": "char_ghost",
            "facet": "state",
            "field": "state.location",
            "before": "X",
            "after": "Y",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert any(
        "business" in e and "character_id" in e and "char_ghost" in e for e in errors
    ), f"期望引用不存在 business 错误，实际：{errors}"


def test_reference_existence_missing_world_id_reports_business_error():
    snap = _base_snapshot(locations=["loc_real"])
    delta = _good_delta()
    delta["world_changes"] = [
        {
            "change_id": "wc_1",
            "op": "update",
            "target_id": "loc_ghost",
            "world_kind": "location",
            "world_id": "loc_ghost",
            "field": "population",
            "before": 100,
            "after": 200,
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert any("business" in e and "world_id" in e and "loc_ghost" in e for e in errors)


def test_reference_existence_missing_world_rule_id_reports_business_error():
    snap = _base_snapshot(world_rules=["rule_real"])
    delta = _good_delta()
    delta["world_changes"] = [
        {
            "change_id": "wc_1",
            "op": "update",
            "target_id": "rule_ghost",
            "world_kind": "rule",
            "world_id": "rule_ghost",
            "field": "statement",
            "before": "x",
            "after": "y",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert any("business" in e and "world_id" in e and "rule_ghost" in e for e in errors)


def test_reference_existence_missing_faction_id_reports_business_error():
    snap = _base_snapshot(factions=["fac_real"])
    delta = _good_delta()
    delta["world_changes"] = [
        {
            "change_id": "wc_1",
            "op": "update",
            "target_id": "fac_ghost",
            "world_kind": "faction",
            "world_id": "fac_ghost",
            "field": "power",
            "before": 10,
            "after": 20,
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert any("business" in e and "world_id" in e and "fac_ghost" in e for e in errors)


def test_reference_existence_missing_relationship_endpoints_reports_business_error():
    snap = _base_snapshot(characters=["char_alice"])
    delta = _good_delta()
    delta["relationship_changes"] = [
        {
            "change_id": "rc_1",
            "op": "add",
            "target_id": "rel_alice_bob",
            "from_character_id": "char_alice",
            "to_character_id": "char_ghost",
            "relation_type": "ally",
            "before": None,
            "after": {"trust": 0.5},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert any("to_character_id" in e and "char_ghost" in e for e in errors)


def test_reference_existence_missing_resolved_hook_id_reports_business_error():
    snap = _base_snapshot(hooks=["hook_real"])
    delta = _good_delta()
    delta["resolved_hooks"] = [
        {
            "change_id": "rh_1",
            "op": "update",
            "target_id": "hook_ghost",
            "hook_id": "hook_ghost",
            "from_status": "OPEN",
            "to_status": "RESOLVED",
            "payoff_summary": "结算说明",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert any("business" in e and "hook_id" in e and "hook_ghost" in e for e in errors)


def test_reference_existence_missing_debt_id_reports_business_error():
    snap = _base_snapshot(debts=["debt_real"])
    delta = _good_delta()
    delta["debt_changes"] = [
        {
            "change_id": "dc_1",
            "op": "update",
            "target_id": "debt_ghost",
            "debt_id": "debt_ghost",
            "status_before": "open",
            "status_after": "paid",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert any("business" in e and "debt_id" in e and "debt_ghost" in e for e in errors)


def test_reference_existence_missing_event_participant_reports_business_error():
    snap = _base_snapshot(characters=["char_alice"])
    delta = _good_delta()
    delta["new_events"] = [
        {
            "change_id": "ev_1",
            "op": "add",
            "target_id": "evt_test",
            "event_id": "evt_test",
            "type": "revelation",
            "participants": ["char_alice", "char_ghost"],
            "time": {"timeline_day": 1},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert any("participants" in e and "char_ghost" in e for e in errors)


def test_reference_existence_new_event_id_already_exists_reports_business_error():
    """new_events 的 event_id 必须不在 snapshot（add 唯一性）。"""
    snap = _base_snapshot(events=["evt_existing"])
    delta = _good_delta()
    delta["new_events"] = [
        {
            "change_id": "ev_1",
            "op": "add",
            "target_id": "evt_existing",
            "event_id": "evt_existing",
            "type": "revelation",
            "participants": ["char_alice"],
            "time": {"timeline_day": 1},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    # event_id 冲突报错；snapshot 中无 char_alice → 也会触发 participants 报错
    assert any("event_id" in e and "evt_existing" in e for e in errors)


# ---------------------------------------------------------------------------
# 3. 本 delta add 自愈通过
# ---------------------------------------------------------------------------


def test_reference_existence_delta_self_added_character_passes_for_relationship():
    """同一 delta 中先 add 一个 character，再在 relationship_changes 引用它 → 通过。"""
    snap = _base_snapshot(characters=["char_alice"])
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "add",
            "target_id": "char_bob",
            "character_id": "char_bob",
            "facet": "state",
            "field": "name",
            "before": None,
            "after": "Bob",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    delta["relationship_changes"] = [
        {
            "change_id": "rc_1",
            "op": "add",
            "target_id": "rel_alice_bob",
            "from_character_id": "char_alice",
            "to_character_id": "char_bob",  # 本 delta 内 add 自愈
            "relation_type": "ally",
            "before": None,
            "after": {"trust": 0.5},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    # 引用存在性校验应通过：char_bob 在 created_ids["characters"] 中
    ref_errs = [e for e in errors if "不在 snapshot 且未被本 delta add 创建" in e]
    assert ref_errs == [], f"自愈后应通过，实际仍有引用错误：{ref_errs}"


def test_reference_existence_delta_self_added_character_passes_for_event_participant():
    snap = _base_snapshot(characters=["char_alice"])
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "add",
            "target_id": "char_bob",
            "character_id": "char_bob",
            "facet": "state",
            "field": "name",
            "before": None,
            "after": "Bob",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    delta["new_events"] = [
        {
            "change_id": "ev_1",
            "op": "add",
            "target_id": "evt_test",
            "event_id": "evt_test",
            "type": "revelation",
            "participants": ["char_alice", "char_bob"],  # bob 自愈
            "time": {"timeline_day": 1},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    ref_errs = [e for e in errors if "participants" in e]
    assert ref_errs == [], f"参与者自愈后应通过：{ref_errs}"


def test_reference_existence_delta_self_added_hook_passes_for_resolved():
    """极少见但合法：同一 delta 中 new_hooks 添加 hook，再 resolved_hooks 引用它。
    created_ids["hooks"] 应兜底此情形。"""
    snap = _base_snapshot()  # 无 hooks
    delta = _good_delta()
    delta["new_hooks"] = [
        {
            "change_id": "nh_1",
            "op": "add",
            "target_id": "hook_x",
            "hook_id": "hook_x",
            "name": "Hook X",
            "importance": 0.5,
            "description": "desc",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    delta["resolved_hooks"] = [
        {
            "change_id": "rh_1",
            "op": "update",
            "target_id": "hook_x",
            "hook_id": "hook_x",  # 本 delta 内 add 自愈
            "from_status": "OPEN",
            "to_status": "RESOLVED",
            "payoff_summary": "结算",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    ref_errs = [e for e in errors if "hook_id" in e and "不在 snapshot" in e]
    assert ref_errs == [], f"hook 自愈后应通过：{ref_errs}"


# ---------------------------------------------------------------------------
# 4. 字段缺失跳过
# ---------------------------------------------------------------------------


def test_reference_existence_missing_character_id_does_not_raise():
    """character_id 字段缺失时跳过引用校验（schema 已校验必填）。"""
    snap = _base_snapshot(characters=["char_alice"])
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_alice",
            # character_id 故意缺失
            "facet": "state",
            "field": "state.location",
            "before": "X",
            "after": "Y",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    # 不抛异常即可；可能产生 schema 错误，但 _reference_existence_errors 应跳过
    errors = validate_delta(delta, snapshot=snap)
    ref_errs = [e for e in errors if "不在 snapshot" in e]
    assert ref_errs == [], f"字段缺失时应跳过引用校验：{ref_errs}"


def test_reference_existence_empty_string_id_does_not_error():
    """character_id 为空字符串视为缺失，不报错。"""
    snap = _base_snapshot(characters=["char_alice"])
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_alice",
            "character_id": "",
            "facet": "state",
            "field": "state.location",
            "before": "X",
            "after": "Y",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    ref_errs = [e for e in errors if "不在 snapshot" in e]
    assert ref_errs == [], f"空 ID 视为缺失：{ref_errs}"


def test_reference_existence_non_dict_snapshot_does_not_crash():
    """snapshot 不是 dict 时整体跳过引用校验（不抛异常）。"""
    delta = _good_delta()
    errors = validate_delta(delta, snapshot=None)
    assert errors == []
    # snapshot=非 dict 也跳过
    errors = validate_delta(delta, snapshot="not_a_dict")
    assert errors == []
    errors = validate_delta(delta, snapshot=[])
    assert errors == []


# ---------------------------------------------------------------------------
# 5. backward-compat & integration
# ---------------------------------------------------------------------------


def test_reference_existing_passes_when_in_snapshot_for_all_kinds():
    """全类型引用在 snapshot 中时全通过。"""
    snap = _base_snapshot(
        characters=["char_alice", "char_bob"],
        locations=["loc_village"],
        factions=["fac_rebels"],
        world_rules=["rule_no_magic"],
        hooks=["hook_open_1"],
        debts=["debt_001"],
        events=["evt_001"],
    )
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_alice",
            "character_id": "char_alice",
            "facet": "state",
            "field": "state.location",
            "before": "X",
            "after": "Y",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    delta["world_changes"] = [
        {
            "change_id": "wc_1",
            "op": "update",
            "target_id": "loc_village",
            "world_kind": "location",
            "world_id": "loc_village",
            "field": "population",
            "before": 100,
            "after": 200,
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    delta["resolved_hooks"] = [
        {
            "change_id": "rh_1",
            "op": "update",
            "target_id": "hook_open_1",
            "hook_id": "hook_open_1",
            "from_status": "OPEN",
            "to_status": "RESOLVED",
            "payoff_summary": "结算",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert errors == []


def test_existing_validator_signature_unchanged():
    """validate_delta 不带 snapshot 调用时与原版字节级兼容。"""
    delta = _good_delta()
    delta["character_changes"] = [
        {
            "change_id": "cc_1",
            "op": "update",
            "target_id": "char_alice",
            "character_id": "char_alice",
            "facet": "state",
            "field": "state.location",
            "before": "Forest",
            "after": "Cave",
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta)
    assert errors == []
