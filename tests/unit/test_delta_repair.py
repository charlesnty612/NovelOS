"""Delta 确定性自动修复（arbiter-lite）单元测试。"""

from __future__ import annotations

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


def test_repair_resolved_hook_skips_fill_when_hook_abandoned():
    """审计 §A1 守卫：ABANDONED 钩子的 resolved_hooks 不填 from_status。

    修复前：repair 把 from_status 填为 ABANDONED，再叠 validator 接受
    ABANDONED→ABANDONED 的合法迁移——但业务语义上"对一个已放弃的钩子
    又来一次 resolved delta"本身是异常；更糟的是若 to_status=RESOLVED，
    后续 write_through 旧逻辑会把 payoff_chapter_id 写入并触发状态机
    "复活"风险。修复后：ABANDONED 时 repair 保持 from_status=None，
    无 fill-before 记录，把"异常是否拒绝"的判断完整交给下游业务层
    （validator 对 from_status=None 跳过迁移检查是跨分支/回滚语义）。
    """
    delta = _minimal_delta(
        resolved_hooks=[
            {
                "change_id": "rh_abandoned",
                "op": "update",
                "target_id": "hook_aban",
                "hook_id": "hook_aban",
                "from_status": None,
                "to_status": "RESOLVED",
                "payoff_summary": "误判复活",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {"hooks": [{"hook_id": "hook_aban", "status": "ABANDONED"}]}
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    # 关键断言：ABANDONED 时不产生 fill-before 记录、from_status 保持 None
    assert repairs == []
    assert repaired["resolved_hooks"][0]["from_status"] is None
    # 回归：ACTIVE 时仍正常 fill-before（已有用例覆盖，这里再快速 smoke）
    delta_active = _minimal_delta(
        resolved_hooks=[{**repaired["resolved_hooks"][0], "hook_id": "hook_act",
                         "target_id": "hook_act", "from_status": None}],
    )
    snap_active = {"hooks": [{"hook_id": "hook_act", "status": "ACTIVE"}]}
    rep2, rep2_repairs = repair_delta(delta_active, snapshot=snap_active)
    assert len(rep2_repairs) == 1
    assert rep2_repairs[0]["rule"] == "fill-before"
    assert rep2["resolved_hooks"][0]["from_status"] == "ACTIVE"


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


# ---------------------------------------------------------------------------
# id-prefix-reconcile 规则（F3 修复 / wfr_6d77d905b4b1）
# ---------------------------------------------------------------------------


_F3_HEX = "e98a1ca56b66"


def test_repair_id_prefix_reconcile_char_to_fac_in_character_changes():
    """F3 主用例：observer 把 fac_xxx 错写为 char_xxx（character_changes）。"""
    delta = _minimal_delta(
        character_changes=[
            {
                "change_id": "cc_1",
                "op": "update",
                "target_id": f"char_{_F3_HEX}",
                "character_id": f"char_{_F3_HEX}",
                "facet": "state",
                "field": "state.influence",
                "before": 0.3,
                "after": 0.5,
                "confidence": 0.85,
                "evidence": _evidence(),
                "risk_level": "MEDIUM",
            }
        ],
    )
    # snapshot 同时含 character + faction（让前缀调和仍能唯一定位到 faction）
    snapshot = {
        "characters": [{"character_id": "char_other"}],
        "world": {"factions": {f"fac_{_F3_HEX}": {"name": "宝源当沈家"}}},
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)

    # 留下 reconcile 记录
    assert len(repairs) == 1
    assert repairs[0]["rule"] == "id_prefix_reconcile"
    assert repairs[0]["array"] == "character_changes"
    assert repairs[0]["from_id"] == f"char_{_F3_HEX}"
    assert repairs[0]["to_id"] == f"fac_{_F3_HEX}"
    assert repairs[0]["from_prefix"] == "char"
    assert repairs[0]["to_prefix"] == "fac"
    # 改写成功：character_id + target_id 同步改写
    assert repaired["character_changes"][0]["character_id"] == f"fac_{_F3_HEX}"
    assert repaired["character_changes"][0]["target_id"] == f"fac_{_F3_HEX}"
    # 字段语义是否破坏（faction 不该走 character_changes 数组）属于业务约束，
    # 本规则只调前缀；validator 在 cross-bucket 写法上的具体报错不在本测试覆盖范围


def test_repair_id_prefix_reconcile_fac_to_char_in_world_changes_faction():
    """F3 反向：observer 把 char_xxx 错写为 fac_xxx（world_changes kind=faction）。"""
    delta = _minimal_delta(
        world_changes=[
            {
                "change_id": "wc_1",
                "op": "update",
                "target_id": f"fac_{_F3_HEX}",
                "world_kind": "faction",
                "world_id": f"fac_{_F3_HEX}",
                "field": "data_json.influence",
                "before": 0.3,
                "after": 0.5,
                "confidence": 0.85,
                "evidence": _evidence(),
                "risk_level": "MEDIUM",
            }
        ],
    )
    snapshot = {
        "characters": [
            {"character_id": f"char_{_F3_HEX}", "current_state": {"influence": 0.3}}
        ]
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)

    assert len(repairs) == 1
    assert repairs[0]["rule"] == "id_prefix_reconcile"
    assert repairs[0]["array"] == "world_changes"
    assert repairs[0]["from_id"] == f"fac_{_F3_HEX}"
    assert repairs[0]["to_id"] == f"char_{_F3_HEX}"
    assert repaired["world_changes"][0]["world_id"] == f"char_{_F3_HEX}"
    assert repaired["world_changes"][0]["target_id"] == f"char_{_F3_HEX}"


def test_repair_id_prefix_reconcile_in_relationship_endpoints():
    """F3 覆盖：relationship_changes.from/to 端点。"""
    delta = _minimal_delta(
        relationship_changes=[
            {
                "change_id": "rc_1",
                "op": "add",
                "target_id": "rel_new",
                "from_character_id": f"char_{_F3_HEX}",  # 实际是 character
                "to_character_id": "char_other_aa",
                "relation_type": "ally",
                "after": {"intensity": 0.8},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
            {
                "change_id": "rc_2",
                "op": "add",
                "target_id": "rel_new2",
                "from_character_id": "char_a",
                "to_character_id": f"char_{_F3_HEX}",  # 实际是 character
                "relation_type": "ally",
                "after": {"intensity": 0.7},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
        ],
    )
    snapshot = {
        "characters": [
            {"character_id": f"char_{_F3_HEX}"},
            {"character_id": "char_a"},
            {"character_id": "char_other_aa"},
        ]
    }
    # 这里错写与正写一致（都是 char_），不应产生 repair（自身表已存在）
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert repairs == []
    # 现在把 fac_ 错写成 char_ 端点，看是否被改回
    delta2 = _minimal_delta(
        relationship_changes=[
            {
                "change_id": "rc_x",
                "op": "add",
                "target_id": "rel_x",
                "from_character_id": f"char_{_F3_HEX}",  # 错写：实为 faction
                "to_character_id": "char_a",
                "relation_type": "ally",
                "after": {"intensity": 0.6},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snap2 = {
        "characters": [{"character_id": "char_a"}],
        "world": {"factions": {f"fac_{_F3_HEX}": {"name": "宝源当沈家"}}},
    }
    rep2, repairs2 = repair_delta(delta2, snapshot=snap2)
    assert any(r["rule"] == "id_prefix_reconcile" for r in repairs2)
    assert rep2["relationship_changes"][0]["from_character_id"] == f"fac_{_F3_HEX}"


def test_repair_id_prefix_reconcile_in_new_events_participants():
    """F3 覆盖：new_events[*].participants[] 列表项。"""
    delta = _minimal_delta(
        new_events=[
            {
                "change_id": "ev_1",
                "op": "add",
                "target_id": "evt_new_1",
                "event_id": "evt_new_1",
                "type": "encounter",
                "participants": [
                    "char_a",
                    f"char_{_F3_HEX}",  # 错写：实为 faction
                ],
                "time": {"timeline_day": 1, "in_story_date": None},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "MEDIUM",
            }
        ],
    )
    snapshot = {
        "characters": [{"character_id": "char_a"}],
        "world": {"factions": {f"fac_{_F3_HEX}": {"name": "宝源当沈家"}}},
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    id_prefix_repairs = [r for r in repairs if r["rule"] == "id_prefix_reconcile"]
    assert len(id_prefix_repairs) == 1
    assert id_prefix_repairs[0]["array"] == "new_events"
    assert id_prefix_repairs[0]["field"] == "participants[1]"
    assert id_prefix_repairs[0]["from_id"] == f"char_{_F3_HEX}"
    assert id_prefix_repairs[0]["to_id"] == f"fac_{_F3_HEX}"
    assert repaired["new_events"][0]["participants"][1] == f"fac_{_F3_HEX}"


def test_repair_id_prefix_reconcile_zero_hit_leaves_unchanged():
    """后缀零命中 → 不改写（validator 后续会报错）。"""
    delta = _minimal_delta(
        character_changes=[
            {
                "change_id": "cc_1",
                "op": "update",
                "target_id": f"char_ghost_{_F3_HEX}",
                "character_id": f"char_ghost_{_F3_HEX}",
                "facet": "state",
                "field": "state.x",
                "before": 0,
                "after": 1,
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    # factions 表里没有相同后缀的 id
    snapshot = {"world": {"factions": {f"fac_{_F3_HEX}": {"name": "另一组织"}}}}
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert repairs == []
    assert repaired["character_changes"][0]["character_id"] == f"char_ghost_{_F3_HEX}"


def test_repair_id_prefix_reconcile_multi_hit_leaves_unchanged():
    """后缀在对方表多条命中 → 不改写（避免歧义，validator 报错）。

    通过 monkeypatch 让所有 fac_ 前缀的 id 解析后共享同一 hex 后缀，
    模拟 id 生成器异常导致的多命中场景：本规则不做歧义选择，避免猜测制造幻觉。
    """
    from packages.core.story_state.delta_repair import _reconcile_id_prefix
    suffix = "deadbeef0001"
    fake_index = {
        "characters": {},
        "factions": {
            f"fac_{suffix}": {"name": "组织A"},
            f"fac_alt_{suffix}": {"name": "组织B"},
        },
    }
    # 让两个 id 的 _split_id_prefix 解析结果后缀一致——模拟多命中退化场景
    from packages.core.story_state import delta_repair as dr
    original = dr._split_id_prefix

    def _collapsed(value):
        if not isinstance(value, str) or "_" not in value:
            return None
        prefix, _, _rest = value.partition("_")
        if prefix not in ("char", "fac"):
            return None
        # 把第二段（无论是不是 hex）统一坍缩为 suffix——强制两 id 共享后缀
        return prefix, suffix

    dr._split_id_prefix = _collapsed
    try:
        new_value, repair = _reconcile_id_prefix(f"char_{suffix}", fake_index)
    finally:
        dr._split_id_prefix = original
    assert new_value == f"char_{suffix}"  # 不改写（多命中）
    assert repair is None


def test_repair_id_prefix_reconcile_no_op_when_id_already_correct():
    """原前缀表已存在 → 不产生记录、不改写。"""
    delta = _minimal_delta(
        character_changes=[
            {
                "change_id": "cc_1",
                "op": "update",
                "target_id": f"char_{_F3_HEX}",
                "character_id": f"char_{_F3_HEX}",
                "facet": "state",
                "field": "state.x",
                "before": 0,
                "after": 1,
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    # character 本身已存在；对方表也有一条同后缀 faction —— 不应被改写
    snapshot = {
        "characters": [{"character_id": f"char_{_F3_HEX}"}],
        "world": {"factions": {f"fac_{_F3_HEX}": {"name": "X"}}},
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert repairs == []
    assert repaired["character_changes"][0]["character_id"] == f"char_{_F3_HEX}"


def test_repair_id_prefix_reconcile_skips_non_char_fac_prefixes():
    """非 char/fac 前缀（hook_/debt_/evt_）不做前缀调和。"""
    delta = _minimal_delta(
        character_changes=[
            {
                "change_id": "cc_1",
                "op": "update",
                "target_id": f"hook_{_F3_HEX}",  # 错写：把 hook id 写进 character_id
                "character_id": f"hook_{_F3_HEX}",
                "facet": "state",
                "field": "state.x",
                "before": None,
                "after": 1,
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            }
        ],
    )
    snapshot = {
        "hooks": [{"hook_id": f"hook_{_F3_HEX}"}],
    }
    repaired, repairs = repair_delta(delta, snapshot=snapshot)
    assert repairs == []
    assert repaired["character_changes"][0]["character_id"] == f"hook_{_F3_HEX}"


# ---------------------------------------------------------------------------
# change-id-uniquify 规则（F4 修复 / wfr_6765f6de4d76）
# ---------------------------------------------------------------------------


def test_repair_change_id_uniquify_three_placeholders_in_one_array():
    """F4 主用例：同数组三条 change 共用占位符字面量 → 三条都被重写。

    生产事故 wfr_6765f6de4d76 现场：observer 照抄 ``cc:01HXXXXXXXX`` 到多条
    character_changes。三条都被改写为 ``cc:<12 位 hex>``，前缀保留且互不重复，
    repairs 留痕三条。
    """
    delta = _minimal_delta(
        character_changes=[
            {
                "change_id": "cc:01HXXXXXXXX",
                "op": "update",
                "target_id": "char_a",
                "character_id": "char_a",
                "facet": "state",
                "field": "state.x",
                "before": 0,
                "after": 1,
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
            {
                "change_id": "cc:01HXXXXXXXX",
                "op": "update",
                "target_id": "char_b",
                "character_id": "char_b",
                "facet": "state",
                "field": "state.y",
                "before": 0,
                "after": 1,
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
            {
                "change_id": "cc:01HXXXXXXXX",
                "op": "update",
                "target_id": "char_c",
                "character_id": "char_c",
                "facet": "state",
                "field": "state.z",
                "before": 0,
                "after": 1,
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
        ],
    )
    repaired, repairs = repair_delta(delta, snapshot={})

    # 三条都应被重写
    new_ids = [c["change_id"] for c in repaired["character_changes"]]
    assert len(new_ids) == 3
    for cid in new_ids:
        assert cid.startswith("cc:")
        assert len(cid.split(":", 1)[1]) == 12
        assert "X" not in cid and "x" not in cid
    # 互不相同
    assert len(set(new_ids)) == 3
    # repairs 三条留痕
    uni_repairs = [r for r in repairs if r["rule"] == "change_id_uniquify"]
    assert len(uni_repairs) == 3
    for r in uni_repairs:
        assert r["array"] == "character_changes"
        assert r["from_id"] == "cc:01HXXXXXXXX"
        assert r["to_id"].startswith("cc:")
        assert r["to_id"] != "cc:01HXXXXXXXX"
    # validator 端 change_id 唯一性预检：去重后 delta 应通过（其它字段允许其它报错）
    new_change_ids = [c["change_id"] for c in repaired["character_changes"]]
    assert len(new_change_ids) == len(set(new_change_ids))


def test_repair_change_id_uniquify_mixed_placeholder_and_unique():
    """F4 混合用例：占位符与正常唯一 id 混合 → 只重写占位符/重复者。"""
    delta = _minimal_delta(
        world_changes=[
            {
                "change_id": "wc:abc123def456",  # 正常唯一 id → 不动
                "op": "update",
                "target_id": "loc_1",
                "world_kind": "location",
                "world_id": "loc_1",
                "field": "data_json.atmosphere",
                "before": "阴暗",
                "after": "明亮",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
            {
                "change_id": "wc:01HXXXX",  # 占位符 → 重写
                "op": "update",
                "target_id": "loc_2",
                "world_kind": "location",
                "world_id": "loc_2",
                "field": "data_json.population",
                "before": 500,
                "after": 1000,
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
            {
                "change_id": "wc:abc123def456",  # 与第一条重复 → 重写
                "op": "update",
                "target_id": "loc_3",
                "world_kind": "location",
                "world_id": "loc_3",
                "field": "data_json.atmosphere",
                "before": "宁静",
                "after": "喧嚣",
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
        ],
    )
    repaired, repairs = repair_delta(delta, snapshot={})

    # 第一条不动
    assert repaired["world_changes"][0]["change_id"] == "wc:abc123def456"
    # 第二条占位符被改写
    second_new = repaired["world_changes"][1]["change_id"]
    assert second_new.startswith("wc:") and second_new != "wc:01HXXXX"
    assert "X" not in second_new
    # 第三条因重复被改写（且与第一条不同）
    third_new = repaired["world_changes"][2]["change_id"]
    assert third_new.startswith("wc:") and third_new != "wc:abc123def456"
    # 三条互不重复
    new_ids = [c["change_id"] for c in repaired["world_changes"]]
    assert len(new_ids) == len(set(new_ids))
    # 留痕两条
    uni_repairs = [r for r in repairs if r["rule"] == "change_id_uniquify"]
    assert len(uni_repairs) == 2
    uni_indexes = {r["index"] for r in uni_repairs}
    assert uni_indexes == {1, 2}


def test_repair_change_id_uniquify_all_unique_no_change():
    """F4 零命中用例：全唯一正常 id → 零改动零留痕。"""
    delta = _minimal_delta(
        relationship_changes=[
            {
                "change_id": "rc:001122334455",
                "op": "add",
                "target_id": "rel_1",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "friend",
                "after": {"trust": 0.5},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
            {
                "change_id": "rc:66778899aabb",
                "op": "add",
                "target_id": "rel_2",
                "from_character_id": "char_c",
                "to_character_id": "char_d",
                "relation_type": "ally",
                "after": {"intensity": 0.7},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
        ],
    )
    repaired, repairs = repair_delta(delta, snapshot={})
    assert [c["change_id"] for c in repaired["relationship_changes"]] == [
        "rc:001122334455",
        "rc:66778899aabb",
    ]
    uni_repairs = [r for r in repairs if r["rule"] == "change_id_uniquify"]
    assert uni_repairs == []


def test_repair_change_id_uniquify_cross_array_isolated():
    """F4 跨数组隔离：跨数组同字面量各自处理互不影响。

    验证 seen 集合按数组独立维护：``cc:01HXXX`` 与 ``wc:01HXXX`` 分属不同数组，
    各自独立判断占位符 + 各自独立重写，互不干扰。
    """
    delta = _minimal_delta(
        character_changes=[
            {
                "change_id": "cc:01HXXX",
                "op": "update",
                "target_id": "char_a",
                "character_id": "char_a",
                "facet": "state",
                "field": "state.x",
                "before": 0,
                "after": 1,
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
        ],
        world_changes=[
            {
                "change_id": "wc:01HXXX",
                "op": "update",
                "target_id": "loc_1",
                "world_kind": "location",
                "world_id": "loc_1",
                "field": "data_json.x",
                "before": 0,
                "after": 1,
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
        ],
        new_events=[
            {
                "change_id": "ev:01HXXX",
                "op": "add",
                "target_id": "evt_1",
                "event_id": "evt_1",
                "type": "revelation",
                "participants": ["char_a"],
                "time": {"timeline_day": 1, "in_story_date": None},
                "confidence": 0.9,
                "evidence": _evidence(),
                "risk_level": "LOW",
            },
        ],
    )
    repaired, repairs = repair_delta(delta, snapshot={})

    # 三个数组各产生一条修复记录
    uni_repairs = [r for r in repairs if r["rule"] == "change_id_uniquify"]
    assert len(uni_repairs) == 3
    by_array = {r["array"]: r for r in uni_repairs}
    assert set(by_array.keys()) == {"character_changes", "world_changes", "new_events"}
    # 前缀各自保留
    assert by_array["character_changes"]["to_id"].startswith("cc:")
    assert by_array["world_changes"]["to_id"].startswith("wc:")
    assert by_array["new_events"]["to_id"].startswith("ev:")
    # 实际条目上 change_id 已替换
    assert repaired["character_changes"][0]["change_id"].startswith("cc:")
    assert repaired["world_changes"][0]["change_id"].startswith("wc:")
    assert repaired["new_events"][0]["change_id"].startswith("ev:")
