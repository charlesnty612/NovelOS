"""apply_delta 纯函数单测（Sprint 2）。

覆盖 Delta 的 7 个数组中至少一条 apply 断言：
- character_changes（state facet update / knowledge 整体替换 / definition update）
- world_changes（location update / rule add / politics update）
- relationship_changes（update）
- new_events（append + recent_events 截断到 50）
- resolved_hooks（status 更新 + payoff_summary）
- new_hooks（append，status=OPEN）
- debt_changes（add / update / remove）

设计要点：
- apply_delta 是纯函数，测试不依赖 DB；只需构造 state 字典 + delta 字典。
- 不走 JSON Schema 校验：本测试只验证 apply 语义，不验证 schema 合规（schema 由 validator 单测覆盖）。
- delta 条目字段名与 ``state-delta.schema.json`` 对齐（除 change_id / target_id 等无
  业务语义的字段——填占位即可）。
"""

from __future__ import annotations

import copy

from packages.core.story_state.applier import RECENT_EVENTS_CAP, apply_delta


def _baseline_state() -> dict:
    """构造一个最小 state JSON；字符 ID 不重要（applier 按业务字段匹配）。"""
    return {
        "state_version": 1,
        "characters": [
            {
                "character_id": "char_alice",
                "name": "Alice",
                "current_state": {"location": "Forest", "emotion": "calm"},
                "knowledge": ["k_known_a"],
                "beliefs": [],
                "relationships": [],
                "facet": "state",
                "definition": {"personality": "brave"},
            }
        ],
        "world": {
            "current_time_in_story": None,
            "locations": {"loc_village": {"name": "Village", "data_json": {"population": 100}}},
            "factions": {},
            "world_rules": [],
            "active_resources": {},
            "politics": {},
        },
        "hooks": [
            {
                "hook_id": "hook_secret",
                "name": "Secret",
                "status": "OPEN",
                "importance": 0.7,
            }
        ],
        "debts": [],
        "recent_events": [],
        "events": {},
    }


def _base_change_meta(change_id: str) -> dict:
    return {
        "change_id": change_id,
        "target_id": change_id,
        "confidence": 0.9,
        "evidence": {
            "chapter_id": "ch_test",
            "excerpt": "excerpt",
        },
        "risk_level": "LOW",
    }


# ----------------------------------------------------------------- character_changes


def test_character_state_update_overwrites_top_level_key():
    state = _baseline_state()
    delta = {
        "character_changes": [
            {
                **_base_change_meta("cc_1"),
                "op": "update",
                "character_id": "char_alice",
                "facet": "state",
                "field": "state.location",
                "before": "Forest",
                "after": "Cave",
            }
        ]
    }
    new_state = apply_delta(state, delta)
    assert new_state["characters"][0]["current_state"]["location"] == "Cave"
    assert state["characters"][0]["current_state"]["location"] == "Forest"  # 入参未变


def test_character_knowledge_replaced_whole():
    state = _baseline_state()
    delta = {
        "character_changes": [
            {
                **_base_change_meta("cc_2"),
                "op": "update",
                "character_id": "char_alice",
                "facet": "state",
                "field": "knowledge",
                "before": ["k_known_a"],
                "after": ["k_known_a", "k_known_b"],
            }
        ]
    }
    new_state = apply_delta(state, delta)
    assert new_state["characters"][0]["knowledge"] == ["k_known_a", "k_known_b"]


def test_character_definition_update():
    state = _baseline_state()
    delta = {
        "character_changes": [
            {
                **_base_change_meta("cc_3"),
                "op": "update",
                "character_id": "char_alice",
                "facet": "definition",
                "field": "core.personality",
                "before": "brave",
                "after": "cunning",
            }
        ]
    }
    new_state = apply_delta(state, delta)
    assert new_state["characters"][0]["definition"]["personality"] == "cunning"


def test_character_remove_unknown_target_is_noop():
    state = _baseline_state()
    delta = {
        "character_changes": [
            {
                **_base_change_meta("cc_4"),
                "op": "remove",
                "character_id": "char_nobody",
                "facet": "state",
                "field": "location",
                "before": None,
                "after": None,
            }
        ]
    }
    new_state = apply_delta(state, delta)
    assert new_state == state


# ----------------------------------------------------------------- world_changes


def test_world_location_update_overwrites_data_json_key():
    state = _baseline_state()
    delta = {
        "world_changes": [
            {
                **_base_change_meta("wc_1"),
                "op": "update",
                "world_kind": "location",
                "world_id": "loc_village",
                "field": "data_json.population",
                "before": 100,
                "after": 250,
            }
        ]
    }
    new_state = apply_delta(state, delta)
    assert new_state["world"]["locations"]["loc_village"]["data_json"]["population"] == 250


def test_world_location_update_top_level_name():
    state = _baseline_state()
    delta = {
        "world_changes": [
            {
                **_base_change_meta("wc_1b"),
                "op": "update",
                "world_kind": "location",
                "world_id": "loc_village",
                "field": "name",
                "before": "Village",
                "after": "Old Village",
            }
        ]
    }
    new_state = apply_delta(state, delta)
    assert new_state["world"]["locations"]["loc_village"]["name"] == "Old Village"


def test_world_rule_add_appends_to_list():
    state = _baseline_state()
    delta = {
        "world_changes": [
            {
                **_base_change_meta("wc_2"),
                "op": "add",
                "world_kind": "rule",
                "world_id": "wrule_magic",
                "field": "statement",
                "before": None,
                "after": {
                    "name": "Magic rule",
                    "statement": "Mana regenerates at dawn.",
                    "data_json": {},
                },
            }
        ]
    }
    new_state = apply_delta(state, delta)
    rules = new_state["world"]["world_rules"]
    assert any(r.get("world_rule_id") == "wrule_magic" for r in rules)


def test_world_politics_update_sets_top_level_kind():
    state = _baseline_state()
    delta = {
        "world_changes": [
            {
                **_base_change_meta("wc_3"),
                "op": "update",
                "world_kind": "politics",
                "world_id": "policy_x",
                "field": "value",
                "before": None,
                "after": {"value": "new edict"},
            }
        ]
    }
    new_state = apply_delta(state, delta)
    assert new_state["world"]["politics"]["policy_x"] == {"value": "new edict"}


# ----------------------------------------------------------------- relationship_changes


def test_relationship_update_replaces_entry():
    state = _baseline_state()
    # 先 seed 一个 relationship
    state["characters"][0]["relationships"] = [
        {
            "relationship_id": "rel_1",
            "from_character_id": "char_alice",
            "to_character_id": "char_bob",
            "relation_type": "ally",
            "state_json": {"intensity": 0.3},
        }
    ]
    delta = {
        "relationship_changes": [
            {
                **_base_change_meta("rc_1"),
                "op": "update",
                "from_character_id": "char_alice",
                "to_character_id": "char_bob",
                "relation_type": "ally",
                "target_id": "rel_1",
                "before": {"intensity": 0.3},
                "after": {"intensity": 0.9},
            }
        ]
    }
    new_state = apply_delta(state, delta)
    rels = new_state["characters"][0]["relationships"]
    assert len(rels) == 1
    assert rels[0]["state_json"] == {"intensity": 0.9}


# ----------------------------------------------------------------- new_events


def test_new_events_append_and_truncate_to_cap():
    state = _baseline_state()
    events = [
        {
            **_base_change_meta(f"ev_{i}"),
            "op": "add",
            "event_id": f"event_{i}",
            "target_id": f"event_{i}",
            "type": "other",
            "participants": ["char_alice"],
            "time": {"timeline_day": i + 1, "in_story_date": None},
            "description": f"event {i}",
        }
        for i in range(RECENT_EVENTS_CAP + 5)
    ]
    delta = {"new_events": events}
    new_state = apply_delta(state, delta)
    assert len(new_state["recent_events"]) == RECENT_EVENTS_CAP
    # 截断保留最新 50 条
    assert new_state["recent_events"][-1] == f"event_{RECENT_EVENTS_CAP + 4}"
    assert new_state["recent_events"][0] == "event_5"
    # events 详情：全部键存在（不截断；只是 recent 列表截断）
    assert len(new_state["events"]) == RECENT_EVENTS_CAP + 5


# ----------------------------------------------------------------- resolved_hooks / new_hooks


def test_resolved_hooks_updates_status_and_payoff():
    state = _baseline_state()
    delta = {
        "resolved_hooks": [
            {
                **_base_change_meta("rh_1"),
                "op": "update",
                "hook_id": "hook_secret",
                "target_id": "hook_secret",
                "from_status": "OPEN",
                "to_status": "RESOLVED",
                "payoff_summary": "秘密被揭穿",
            }
        ]
    }
    new_state = apply_delta(state, delta)
    h = next(h for h in new_state["hooks"] if h["hook_id"] == "hook_secret")
    assert h["status"] == "RESOLVED"
    assert h["payoff_summary"] == "秘密被揭穿"


def test_new_hooks_appends_with_status_open():
    state = _baseline_state()
    delta = {
        "new_hooks": [
            {
                **_base_change_meta("nh_1"),
                "op": "add",
                "hook_id": "hook_new",
                "target_id": "hook_new",
                "name": "新伏笔",
                "importance": 0.5,
                "description": "待解决",
                "chapter_id": "ch_test",
            }
        ]
    }
    new_state = apply_delta(state, delta)
    h = next(h for h in new_state["hooks"] if h["hook_id"] == "hook_new")
    assert h["status"] == "OPEN"


# ----------------------------------------------------------------- debt_changes


def test_debt_changes_add_update_remove():
    state = copy.deepcopy(_baseline_state())
    state["debts"] = [
        {
            "debt_id": "debt_a",
            "description": "先存的",
            "severity": 0.4,
            "status": "open",
        }
    ]

    # add
    new_state = apply_delta(
        state,
        {
            "debt_changes": [
                {
                    **_base_change_meta("dc_add"),
                    "op": "add",
                    "debt_id": "debt_b",
                    "target_id": "debt_b",
                    "description": "新债",
                    "severity_after": 0.6,
                    "status_after": "open",
                    "chapter_id": "ch_test",
                }
            ]
        },
    )
    assert any(d["debt_id"] == "debt_b" for d in new_state["debts"])

    # update
    new_state = apply_delta(
        new_state,
        {
            "debt_changes": [
                {
                    **_base_change_meta("dc_upd"),
                    "op": "update",
                    "debt_id": "debt_a",
                    "target_id": "debt_a",
                    "severity_after": 0.9,
                    "status_after": "acknowledged",
                }
            ]
        },
    )
    a = next(d for d in new_state["debts"] if d["debt_id"] == "debt_a")
    assert a["severity"] == 0.9
    assert a["status"] == "acknowledged"

    # remove
    new_state = apply_delta(
        new_state,
        {
            "debt_changes": [
                {
                    **_base_change_meta("dc_rm"),
                    "op": "remove",
                    "debt_id": "debt_b",
                    "target_id": "debt_b",
                    "after": None,
                    "reason": "已原谅",
                }
            ]
        },
    )
    assert not any(d["debt_id"] == "debt_b" for d in new_state["debts"])


# ----------------------------------------------------------------- immutability / multiple arrays


def test_apply_does_not_mutate_input_state():
    state = _baseline_state()
    snapshot = copy.deepcopy(state)
    delta = {
        "character_changes": [
            {
                **_base_change_meta("cc_x"),
                "op": "update",
                "character_id": "char_alice",
                "facet": "state",
                "field": "emotion",
                "before": "calm",
                "after": "angry",
            }
        ],
        "new_hooks": [
            {
                **_base_change_meta("nh_x"),
                "op": "add",
                "hook_id": "hook_x",
                "target_id": "hook_x",
                "name": "X",
                "importance": 0.3,
                "description": "X",
                "chapter_id": "ch_test",
            }
        ],
    }
    apply_delta(state, delta)
    assert state == snapshot


def test_apply_all_arrays_together():
    state = _baseline_state()
    delta = {
        "character_changes": [
            {
                **_base_change_meta("cc_a"),
                "op": "update",
                "character_id": "char_alice",
                "facet": "state",
                "field": "state.location",
                "before": "Forest",
                "after": "Cave",
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [
            {
                **_base_change_meta("ev_a"),
                "op": "add",
                "event_id": "event_a",
                "target_id": "event_a",
                "type": "revelation",
                "participants": ["char_alice"],
                "time": {"timeline_day": 1, "in_story_date": None},
                "description": "首次事件",
            }
        ],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    new_state = apply_delta(state, delta)
    assert new_state["characters"][0]["current_state"]["location"] == "Cave"
    assert new_state["recent_events"] == ["event_a"]
    assert new_state["events"]["event_a"]["type"] == "revelation"
