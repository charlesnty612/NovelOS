"""Observer Delta 关系端点 / 事件参与者接受 faction_id 的单测（修复 wfr_6619a7bfa6fa）。

背景：
- 生产事故 wfr_6619a7bfa6fa：组织间关系（faction↔faction，如两家典当行商战）被
  validator 误杀。根因：``relationship_changes.from_character_id`` /
  ``to_character_id`` 与 ``new_events.participants[]`` 此前只查 characters 桶；
  observer 输出的合法 faction 端点被作为"不在 snapshot"业务错误拒绝。
- 修复：validator 与 applier 端点解析扩为 characters ∪ factions；端点身份对
  Schema 字段名（``from_character_id`` / ``to_character_id`` / ``participants``）
  透明。

覆盖：
- ① validator 接受 faction 端点的 relationship add / update。
- ② validator 接受 faction 作为 event participant（与 character 混合亦可）。
- ③ 负向：端点不在 characters ∪ factions 仍被拒（保留原有防幻觉能力）。
- ④ applier 对 faction 端点关系正常落库到 ``state["world"]["factions"][fid].relationships``。
- ⑤ delta_repair 对 faction 端点不误改：target_id 不在 relationships 桶时静默放过
   （端点合法性由 validator 单独把关）。

设计要点：
- 独立构造 snapshot / delta，与既有 test_validator_reference_checks.py 不复用——
  本文件聚焦 faction 端点这一缺口，避免牵连既有 fixture。
- 复用 state-delta.schema.json 字段填全以通过 schema 校验；本文件不验证 schema
  本身（已有 test_validator.py 覆盖）。
"""

from __future__ import annotations

from packages.core.story_state.applier import apply_delta
from packages.core.story_state.delta_repair import repair_delta
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
    factions: list[str] | None = None,
) -> dict:
    return {
        "state_version": 1,
        "characters": [
            {"character_id": cid, "name": cid, "facet": "state",
             "current_state": {}, "knowledge": [], "beliefs": [], "relationships": []}
            for cid in (characters or [])
        ],
        "world": {
            "current_time_in_story": None,
            "locations": {},
            "factions": {fid: {"name": fid} for fid in (factions or [])},
            "world_rules": [],
            "active_resources": {},
        },
        "hooks": [],
        "debts": [],
        "recent_events": [],
        "events": {},
    }


# ---------------------------------------------------------------------------
# ① relationship_changes 端点接受 faction
# ---------------------------------------------------------------------------


def test_relationship_add_with_both_faction_endpoints_passes():
    """faction↔faction 组织间关系（典当行商战原型场景）。"""
    snap = _base_snapshot(factions=["fac_pawnshop_east", "fac_pawnshop_west"])
    delta = _good_delta()
    delta["relationship_changes"] = [
        {
            "change_id": "rc_1",
            "op": "add",
            "target_id": "rel_pawnshop_feud",
            "from_character_id": "fac_pawnshop_east",
            "to_character_id": "fac_pawnshop_west",
            "relation_type": "hostile",
            "before": None,
            "after": {"intensity": 0.9, "since_chapter": 3},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "MEDIUM",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    ref_errs = [e for e in errors if "不在 snapshot" in e]
    assert ref_errs == [], f"faction 端点应通过引用校验，实际：{ref_errs}"


def test_relationship_update_with_faction_endpoints_passes():
    snap = _base_snapshot(factions=["fac_pawnshop_east", "fac_pawnshop_west"])
    delta = _good_delta()
    delta["relationship_changes"] = [
        {
            "change_id": "rc_1",
            "op": "update",
            "target_id": "rel_pawnshop_feud",
            "from_character_id": "fac_pawnshop_east",
            "to_character_id": "fac_pawnshop_west",
            "relation_type": "hostile",
            "before": {"intensity": 0.5},
            "after": {"intensity": 0.9},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "MEDIUM",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    ref_errs = [e for e in errors if "不在 snapshot" in e]
    assert ref_errs == [], f"faction 端点 update 应通过：{ref_errs}"


def test_relationship_mixed_character_and_faction_endpoints_passes():
    """人物↔组织关系（一端 character、一端 faction）也应通过。"""
    snap = _base_snapshot(
        characters=["char_lord_chen"],
        factions=["fac_merchants_guild"],
    )
    delta = _good_delta()
    delta["relationship_changes"] = [
        {
            "change_id": "rc_1",
            "op": "add",
            "target_id": "rel_chen_guild",
            "from_character_id": "char_lord_chen",
            "to_character_id": "fac_merchants_guild",
            "relation_type": "ally",
            "before": None,
            "after": {"intensity": 0.6},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    ref_errs = [e for e in errors if "不在 snapshot" in e]
    assert ref_errs == [], f"person↔org 应通过：{ref_errs}"


# ---------------------------------------------------------------------------
# ② new_events.participants 接受 faction
# ---------------------------------------------------------------------------


def test_event_participant_faction_passes():
    """事件参与者可包含 faction（商会、门派作为整体参与）。"""
    snap = _base_snapshot(factions=["fac_merchants_guild"])
    delta = _good_delta()
    delta["new_events"] = [
        {
            "change_id": "ev_1",
            "op": "add",
            "target_id": "evt_test",
            "event_id": "evt_test",
            "type": "conflict",
            "participants": ["fac_merchants_guild"],
            "time": {"timeline_day": 1, "in_story_date": None},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "MEDIUM",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    ref_errs = [e for e in errors if "participants" in e and "不在 snapshot" in e]
    assert ref_errs == [], f"faction participant 应通过：{ref_errs}"


def test_event_participant_mixed_character_and_faction_passes():
    snap = _base_snapshot(
        characters=["char_alice"],
        factions=["fac_merchants_guild"],
    )
    delta = _good_delta()
    delta["new_events"] = [
        {
            "change_id": "ev_1",
            "op": "add",
            "target_id": "evt_test",
            "event_id": "evt_test",
            "type": "encounter",
            "participants": ["char_alice", "fac_merchants_guild"],
            "time": {"timeline_day": 1, "in_story_date": None},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    ref_errs = [e for e in errors if "participants" in e and "不在 snapshot" in e]
    assert ref_errs == [], f"混合 participant 应通过：{ref_errs}"


# ---------------------------------------------------------------------------
# ③ 负向：端点不在 characters ∪ factions 仍被拒（防幻觉）
# ---------------------------------------------------------------------------


def test_relationship_unknown_endpoint_still_rejected():
    """端点既不在 characters 也不在 factions → 仍报 business 错误。"""
    snap = _base_snapshot(
        characters=["char_alice"],
        factions=["fac_real"],
    )
    delta = _good_delta()
    delta["relationship_changes"] = [
        {
            "change_id": "rc_1",
            "op": "add",
            "target_id": "rel_ghost",
            "from_character_id": "char_alice",
            "to_character_id": "fac_ghost",  # fac_ 前缀但不在 snapshot
            "relation_type": "ally",
            "before": None,
            "after": {"trust": 0.5},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "LOW",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert any(
        "fac_ghost" in e and "不在 snapshot" in e for e in errors
    ), f"幻觉端点应被拒，实际：{errors}"


def test_event_participant_unknown_still_rejected():
    snap = _base_snapshot(characters=["char_alice"], factions=["fac_real"])
    delta = _good_delta()
    delta["new_events"] = [
        {
            "change_id": "ev_1",
            "op": "add",
            "target_id": "evt_test",
            "event_id": "evt_test",
            "type": "conflict",
            "participants": ["char_alice", "fac_ghost"],
            "time": {"timeline_day": 1, "in_story_date": None},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "MEDIUM",
        }
    ]
    errors = validate_delta(delta, snapshot=snap)
    assert any(
        "fac_ghost" in e and "participants" in e and "不在 snapshot" in e for e in errors
    ), f"幻觉 participant 应被拒，实际：{errors}"


# ---------------------------------------------------------------------------
# ④ applier：faction 端点关系正常落库
# ---------------------------------------------------------------------------


def test_applier_writes_faction_relationship_into_factions_bucket():
    """faction↔faction 关系应写入 state['world']['factions'][fid].relationships。"""
    state = {
        "state_version": 1,
        "characters": [],
        "world": {
            "current_time_in_story": None,
            "locations": {},
            "factions": {
                "fac_pawnshop_east": {"name": "东典当"},
                "fac_pawnshop_west": {"name": "西典当"},
            },
            "world_rules": [],
            "active_resources": {},
        },
        "hooks": [],
        "debts": [],
        "recent_events": [],
        "events": {},
    }
    delta = {
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [
            {
                "change_id": "rc_1",
                "op": "add",
                "target_id": "rel_pawnshop_feud",
                "from_character_id": "fac_pawnshop_east",
                "to_character_id": "fac_pawnshop_west",
                "relation_type": "hostile",
                "before": None,
                "after": {"intensity": 0.9},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "MEDIUM",
            }
        ],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    new_state = apply_delta(state, delta)
    east = new_state["world"]["factions"]["fac_pawnshop_east"]
    rels = east.get("relationships") or []
    assert len(rels) == 1, f"应落入东典当的 relationships，实际：{rels}"
    assert rels[0]["from_character_id"] == "fac_pawnshop_east"
    assert rels[0]["to_character_id"] == "fac_pawnshop_west"
    assert rels[0]["relation_type"] == "hostile"
    assert rels[0]["state_json"] == {"intensity": 0.9}


def test_applier_writes_character_faction_mixed_relationship():
    """一端 character、一端 faction：挂到 character.relationships 桶（按 from 端点定位）。"""
    state = {
        "state_version": 1,
        "characters": [
            {
                "character_id": "char_lord_chen",
                "name": "陈公",
                "current_state": {},
                "knowledge": [],
                "beliefs": [],
                "relationships": [],
                "facet": "state",
            }
        ],
        "world": {
            "current_time_in_story": None,
            "locations": {},
            "factions": {"fac_merchants_guild": {"name": "商会"}},
            "world_rules": [],
            "active_resources": {},
        },
        "hooks": [],
        "debts": [],
        "recent_events": [],
        "events": {},
    }
    delta = {
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [
            {
                "change_id": "rc_1",
                "op": "add",
                "target_id": "rel_chen_guild",
                "from_character_id": "char_lord_chen",
                "to_character_id": "fac_merchants_guild",
                "relation_type": "ally",
                "before": None,
                "after": {"intensity": 0.6},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    new_state = apply_delta(state, delta)
    chen = next(c for c in new_state["characters"] if c["character_id"] == "char_lord_chen")
    rels = chen["relationships"]
    assert len(rels) == 1
    assert rels[0]["to_character_id"] == "fac_merchants_guild"


def test_applier_faction_relationship_remove_clears_entry():
    """remove 操作按 (from,to,type) 同时清理 characters / factions 两侧。"""
    state = {
        "state_version": 1,
        "characters": [],
        "world": {
            "current_time_in_story": None,
            "locations": {},
            "factions": {
                "fac_pawnshop_east": {
                    "name": "东典当",
                    "relationships": [
                        {
                            "relationship_id": "rel_pawnshop_feud",
                            "from_character_id": "fac_pawnshop_east",
                            "to_character_id": "fac_pawnshop_west",
                            "relation_type": "hostile",
                            "state_json": {"intensity": 0.5},
                        }
                    ],
                },
                "fac_pawnshop_west": {"name": "西典当"},
            },
            "world_rules": [],
            "active_resources": {},
        },
        "hooks": [],
        "debts": [],
        "recent_events": [],
        "events": {},
    }
    delta = {
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [
            {
                "change_id": "rc_1",
                "op": "remove",
                "target_id": "rel_pawnshop_feud",
                "from_character_id": "fac_pawnshop_east",
                "to_character_id": "fac_pawnshop_west",
                "relation_type": "hostile",
                "before": None,
                "after": None,
                "reason": "和解",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "MEDIUM",
            }
        ],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    new_state = apply_delta(state, delta)
    east = new_state["world"]["factions"]["fac_pawnshop_east"]
    assert east.get("relationships") == [], f"remove 后应清空，实际：{east.get('relationships')}"


# ---------------------------------------------------------------------------
# ⑤ delta_repair：对 faction 端点不误改
# ---------------------------------------------------------------------------


def test_delta_repair_does_not_corrupt_faction_relationship_changes():
    """新加的 faction 关系 target_id 在 relationships 桶中不存在 → repair 静默放过，
    不强行 fill-before 或 insert-to-update（端点合法性由 validator 单独把关）。"""
    delta = _good_delta()
    delta["relationship_changes"] = [
        {
            "change_id": "rc_1",
            "op": "add",
            "target_id": "rel_pawnshop_feud",
            "from_character_id": "fac_pawnshop_east",
            "to_character_id": "fac_pawnshop_west",
            "relation_type": "hostile",
            "before": None,
            "after": {"intensity": 0.9},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "MEDIUM",
        }
    ]
    # snapshot 含 factions（但不预置 relationship_id）→ entity=None，不触发任何 repair 规则
    snapshot = {
        "world": {
            "factions": {
                "fac_pawnshop_east": {"name": "东典当"},
                "fac_pawnshop_west": {"name": "西典当"},
            }
        }
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    rel_repairs = [r for r in repairs if r.get("array") == "relationship_changes"]
    assert rel_repairs == [], f"新 faction 关系不应触发 repair 记录：{rel_repairs}"
    # repaired.delta 形状不变
    assert repaired["relationship_changes"][0]["op"] == "add"
    assert repaired["relationship_changes"][0]["from_character_id"] == "fac_pawnshop_east"


def test_delta_repair_faction_relationship_existing_id_insert_to_update():
    """已存在的 faction 关系（target_id 在 relationships 桶命中）→ 正常
    insert-to-update；端点是 faction 也不影响修复逻辑（repair 只看 relationship_id）。"""
    delta = _good_delta()
    delta["relationship_changes"] = [
        {
            "change_id": "rc_1",
            "op": "add",
            "target_id": "rel_pawnshop_feud",
            "from_character_id": "fac_pawnshop_east",
            "to_character_id": "fac_pawnshop_west",
            "relation_type": "hostile",
            "before": None,
            "after": {"intensity": 0.9},
            "confidence": 0.9,
            "evidence": _evidence(),
            "risk_level": "MEDIUM",
        }
    ]
    snapshot = {
        "characters": [
            {
                "character_id": "char_alice",
                "relationships": [
                    {
                        "relationship_id": "rel_pawnshop_feud",
                        "from_character_id": "fac_pawnshop_east",
                        "to_character_id": "fac_pawnshop_west",
                        "relation_type": "hostile",
                        "state_json": {"intensity": 0.3},
                    }
                ],
            }
        ]
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    rel_repairs = [r for r in repairs if r.get("array") == "relationship_changes"]
    assert len(rel_repairs) == 1
    assert rel_repairs[0]["rule"] == "insert-to-update"
    assert repaired["relationship_changes"][0]["op"] == "update"
    assert repaired["relationship_changes"][0]["before"] == {"intensity": 0.3}