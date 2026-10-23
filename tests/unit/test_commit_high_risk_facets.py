"""commit 风控门的两条子句：``facet=="definition"`` / ``world_kind=="rule"``（2026-10-23 · D-9）。

## 缺口（D-9）

``packages/workflows/chapter_commit/pipeline_common.py::_has_high_risk_change`` 的判定
有三条子句：

    risk_level == "HIGH"  |  character facet == "definition"  |  world_kind == "rule"

但 workflow 侧**无任何夹具**用后两个值（带 ``facet`` 的夹具全是 ``"state"``）。
Lead 插桩量化（``tests/workflow`` + ``tests/api`` 全量跑完）：
该函数被调用 **41 次**，其中 ``facet=='definition'`` **0 次**、``world_kind=='rule'`` **0 次**
⇒ **把这两条子句删掉，41 次调用的判定结果一次都不会变** ⇒ 全仓无任何测试会红。

⇒ 这不是「覆盖率低」，是**判别力为零**：两条子句当前**没有任何东西能区分它们存在与否**。

⚠️ **孪生实现（两份独立实现，判定口径一致、覆盖互不相干）**：
``packages/core/story_state/deltas.py::high_risk_change_ids`` 是**另一份**判定，
它的三条子句与 workflow 侧**完全对称**——同样是
``risk_level=="HIGH"`` / ``character facet=="definition"`` / ``world world_kind=="rule"``，
（core 侧见 ``deltas.py:116`` 自己的 docstring 第 3 条 + ``:127-131`` 的实现）。

⚠️ **2026-10-23 订正**：本文件早先的版本把 core 侧写成「只认 HIGH 与 definition、
**不认** ``world_kind=="rule"``、覆盖不对称」——**那是错的**（对抗审查 A 实测证伪）。
写错的后果很实：照着这句话去「对齐」core 侧（把 ``world_kind=rule`` 从 core 侧删掉）
**恰好造出一个真的门禁缺口**——core 侧 ``commits.py`` 的 HIGH 风险门
（``high_ids = high_risk_change_ids(delta)``）将不再拦 ``world_kind=rule`` 变更，
走 API 直接提交可绕过人工审批。
⇒ 本文件现在**把两侧口径对称这件事钉成断言**（见 ``test_core_side_judges_world_kind_rule_as_high_risk``），
让「以后有人改坏其中一侧」立刻变红，而不是靠文档提醒。

## 本文件钉住的机制（四层，各守一段）

- **L1 判定层**：``_has_high_risk_change`` 对两个子句各自返回 True；
  反向对照（同结构但 ``facet="state"`` / ``world_kind="location"``）返回 False
  —— 证明它认的是**值**不是「只要有 changes 就 True」。
- **L2 传递层**：``_build_delta`` 把判定结果写进返回值的 ``needs_high_risk_approval``
  —— 钉住「判定确实进了 ctx」（否则 L1 绿了也没人消费）。
- **L3 后果层**：需审批且**未获人工批准**时 ``_commit_node`` 拒收
  —— 钉住「命中即要人工批准」的真实后果（不批准就不许提交）。
  ⚠️ L3 的 ctx 标志**由 payload 现场推导**（``_has_high_risk_change(payload)``），
  **不是**手写 ``True``——否则把 payload 换成什么结果都一样（零判别力）。
- **L4 孪生对称层**：core 侧 ``high_risk_change_ids`` 对三条子句的判定与 workflow 侧一致
  —— 钉住「两份实现口径一致」这个**事实**，防止后人只改一侧。

零真实 LLM 调用、零内容仓接触：L1/L2 是纯 dict，L3 在连库**之前**就抛错（不需要 DB），
L4 是纯 dict。
"""

from __future__ import annotations

import pytest

from packages.workflows.chapter_commit.commit import _commit_node
from packages.workflows.chapter_commit.pipeline_common import _has_high_risk_change


def _char_change(**over) -> dict:
    """一条 character_changes 夹具（默认是「低风险、facet=state」⇒ 不该触发）。"""
    base = {
        "change_id": "cc_1",
        "op": "update",
        "target_id": "char_1",
        "character_id": "char_1",
        "facet": "state",
        "field": "state.location",
        "before": "Village",
        "after": "Cave",
        "confidence": 0.9,
        "evidence": {
            "chapter_id": "ch_1",
            "scene_id": None,
            "excerpt": "excerpt",
            "span": None,
        },
        "risk_level": "LOW",
    }
    base.update(over)
    return base


def _world_change(**over) -> dict:
    """一条 world_changes 夹具（默认是「低风险、world_kind=location」⇒ 不该触发）。"""
    base = {
        "change_id": "wc_1",
        "op": "update",
        "target_id": "loc_1",
        "world_id": "loc_1",
        "world_kind": "location",
        "field": "name",
        "before": "旧名",
        "after": "新名",
        "confidence": 0.9,
        "evidence": {
            "chapter_id": "ch_1",
            "scene_id": None,
            "excerpt": "excerpt",
            "span": None,
        },
        "risk_level": "LOW",
    }
    base.update(over)
    return base


def _payload(**over) -> dict:
    """observer_payload 夹具：7 数组齐全，其余留空。"""
    payload = {
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    payload.update(over)
    return payload


# ---------------------------------------------------------------------------
# L1 判定层：两个子句各自可区分
# ---------------------------------------------------------------------------


def test_definition_facet_triggers_high_risk():
    """character facet=definition ⇒ 需人工审批（D-9 的零区分子句之一）。"""
    assert _has_high_risk_change(
        _payload(character_changes=[_char_change(facet="definition")])
    ) is True


def test_world_kind_rule_triggers_high_risk():
    """world world_kind=rule ⇒ 需人工审批（D-9 的零区分子句之二）。"""
    assert _has_high_risk_change(
        _payload(world_changes=[_world_change(world_kind="rule")])
    ) is True


def test_risk_level_high_still_triggers():
    """对照组：risk_level=HIGH 这条子句本来就有效，别把它一起弄坏。"""
    assert _has_high_risk_change(
        _payload(character_changes=[_char_change(risk_level="HIGH")])
    ) is True


def test_low_risk_state_facet_does_not_trigger():
    """反向对照：facet=state + LOW ⇒ 不触发（它认的是**值**，不是「有 changes 就 True」）。"""
    assert _has_high_risk_change(_payload(character_changes=[_char_change()])) is False


def test_low_risk_location_world_change_does_not_trigger():
    """反向对照：world_kind=location + LOW ⇒ 不触发。"""
    assert _has_high_risk_change(_payload(world_changes=[_world_change()])) is False


def test_empty_payload_does_not_trigger():
    assert _has_high_risk_change(_payload()) is False


# ---------------------------------------------------------------------------
# L2 传递层：判定结果进了 ctx（否则 L1 绿了也没人消费）
# ---------------------------------------------------------------------------


def _fresh_db_with_character(tmp_path):
    """临时库 + project + character + chapter + genesis v1（供 _inject_validate_node 用）。"""
    import json
    import uuid
    from datetime import datetime, timezone
    from pathlib import Path

    from packages.core.db import apply_migrations, get_connection
    from packages.core.story_state.service import StoryStateService

    repo_root = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, repo_root / "database" / "migrations")

    now = datetime.now(timezone.utc).isoformat()
    suffix = uuid.uuid4().hex[:8]
    pid, cid, chap = f"prj_{suffix}", f"char_{suffix}", f"ch_{suffix}"
    wid = f"wrule_{suffix}"
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (pid, "d9-proj", now, now),
        )
        conn.execute(
            "INSERT INTO characters (character_id, project_id, name, role, core_json, "
            "visibility, created_at, updated_at) VALUES (?, ?, ?, 'protagonist', ?, "
            "'VISIBLE', ?, ?)",
            (cid, pid, "Alice", json.dumps({"personality": "calm"}), now, now),
        )
        conn.execute(
            "INSERT INTO character_states (character_id, state_version, state_json, "
            "visibility, created_at) VALUES (?, 1, ?, 'VISIBLE', ?)",
            (cid, json.dumps({"location": "Village", "emotion": "calm"}), now),
        )
        # world_rule：world_kind=rule 的变更必须指向**真实存在**的规则，
        # 否则 validator 的引用存在性校验会失败 ⇒ 走进 observer 重试路径。
        conn.execute(
            "INSERT INTO world_rules (world_rule_id, project_id, name, statement, "
            "data_json, visibility, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '{}', 'PUBLIC', ?, ?)",
            (wid, pid, "旧规则", "规则陈述", now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, created_at, updated_at) "
            "VALUES (?, ?, 1, '第一章', '{}', 'DRAFTED', 'VISIBLE', ?, ?)",
            (chap, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    StoryStateService(db_path).init_genesis(pid, chap)
    return db_path, {
        "project_id": pid,
        "character_id": cid,
        "chapter_id": chap,
        "world_rule_id": wid,
    }


def _run_inject_validate(db_path, seed, payload):
    import copy

    from packages.core.context_engine import build_observer_input
    from packages.workflows.chapter_commit.commit import _inject_validate_node

    # observer_input 用**生产同一个构造函数**造（``build_observer_input``）——
    # `_inject_validate_node` 会把 ``observer_input.previous_state`` 当作快照传给
    # ``validate_delta`` 做引用存在性校验；不给快照 ⇒ 角色/世界条目会被判「不在快照」
    # ⇒ 走进 observer 重试路径（还要 prompt）⇒ 测试变成在测重试而不是在测判定。
    observer_input = build_observer_input(
        str(db_path), seed["chapter_id"], snapshot_mode="trimmed"
    )
    # evidence.chapter_id 也必须是真实章节（validator 校验引用存在性），
    # 否则同样是「引用不存在」⇒ 走进重试 ⇒ 测试在测重试。
    payload = copy.deepcopy(payload)
    for key in ("character_changes", "world_changes", "relationship_changes",
                "new_events", "resolved_hooks", "new_hooks", "debt_changes"):
        for item in payload.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("evidence"), dict):
                item["evidence"]["chapter_id"] = seed["chapter_id"]
    return _inject_validate_node(
        {
            "db_path": str(db_path),
            "chapter_id": seed["chapter_id"],
            "run_id": f"wfr_{seed['chapter_id']}",
            "observer_payload": payload,
            "observer_input": observer_input,
        }
    )


def test_inject_validate_marks_definition_facet_as_needing_approval(tmp_path):
    """``_inject_validate_node`` 真的把判定写进返回值（它才是产出该键的节点）。"""
    db_path, seed = _fresh_db_with_character(tmp_path)
    out = _run_inject_validate(
        db_path,
        seed,
        _payload(
            character_changes=[
                _char_change(
                    facet="definition",
                    character_id=seed["character_id"],
                    target_id=seed["character_id"],
                )
            ]
        ),
    )
    assert out["needs_high_risk_approval"] is True


def test_inject_validate_marks_world_kind_rule_as_needing_approval(tmp_path):
    db_path, seed = _fresh_db_with_character(tmp_path)
    out = _run_inject_validate(
        db_path,
        seed,
        _payload(
            world_changes=[
                _world_change(
                    world_kind="rule",
                    world_id=seed["world_rule_id"],
                    target_id=seed["world_rule_id"],
                )
            ]
        ),
    )
    assert out["needs_high_risk_approval"] is True


def test_inject_validate_low_risk_payload_does_not_need_approval(tmp_path):
    db_path, seed = _fresh_db_with_character(tmp_path)
    out = _run_inject_validate(
        db_path,
        seed,
        _payload(
            character_changes=[
                _char_change(
                    character_id=seed["character_id"],
                    target_id=seed["character_id"],
                )
            ]
        ),
    )
    assert out["needs_high_risk_approval"] is False


# ---------------------------------------------------------------------------
# L3 后果层：命中 ⇒ 未获人工批准就不许提交
# ---------------------------------------------------------------------------


def test_commit_node_rejects_when_high_risk_not_approved():
    """需审批且未批准 ⇒ commit 被拒（不批准就不许提交）。

    ⚠️ ctx 标志由 payload **现场推导**（不是手写 True）——否则这条用例与
    payload 内容无关：把 ``world_kind=="rule"`` 子句从判定里撤掉，它仍会绿
    （对抗审查 A 实测过：零判别力）。
    """
    payload = _payload(character_changes=[_char_change(facet="definition")])
    ctx = {
        "db_path": ":memory:unused",  # 拒收发生在连库之前 ⇒ 用不到真实库
        "chapter_id": "ch_1",
        "run_id": "wfr_1",
        "delta_id": "dlt_1",
        "needs_high_risk_approval": _has_high_risk_change(payload),
        "observer_payload": payload,
    }
    assert ctx["needs_high_risk_approval"] is True, "夹具本身必须命中高风险子句"
    with pytest.raises(ValueError, match="未通过 author approval"):
        _commit_node(ctx)


def test_commit_node_rejects_world_rule_change_when_not_approved():
    """world_kind=rule 同样必须走人工批准（**两侧口径一致**的一条，见 L4）。

    ctx 标志同样由 payload 现场推导 ⇒ 撤掉 ``world_kind=="rule"`` 子句 ⇒
    标志变 False ⇒ 下面 ``pytest.raises`` 不成立 ⇒ 本用例红。
    """
    payload = _payload(world_changes=[_world_change(world_kind="rule")])
    ctx = {
        "db_path": ":memory:unused",
        "chapter_id": "ch_1",
        "run_id": "wfr_1",
        "delta_id": "dlt_1",
        "needs_high_risk_approval": _has_high_risk_change(payload),
        "observer_payload": payload,
    }
    assert ctx["needs_high_risk_approval"] is True, "夹具本身必须命中高风险子句"
    with pytest.raises(ValueError, match="未通过 author approval"):
        _commit_node(ctx)


# ---------------------------------------------------------------------------
# L4 孪生对称层：core 侧判定与 workflow 侧口径一致
# ---------------------------------------------------------------------------


def test_core_side_judges_world_kind_rule_as_high_risk():
    """core 侧 ``high_risk_change_ids`` **也认** ``world_kind=="rule"``。

    事实陈述（2026-10-23 实测订正，见文件头）：两份实现口径**对称**。
    这条断言的作用不是「补覆盖率」，而是**防止后人只改一侧**：
    若有人照旧文档去 core 侧删掉 ``world_kind=rule``（以为那是在「对齐」），
    core 侧 ``commits.py`` 的 HIGH 风险门就再也拦不住世界规则变更
    ⇒ 走 API 直接提交可绕过人工审批。
    """
    from packages.core.story_state.deltas import high_risk_change_ids

    payload = _payload(world_changes=[_world_change(world_kind="rule")])
    assert high_risk_change_ids(payload) == ["wc_1"]


def test_core_side_judges_definition_facet_as_high_risk():
    """core 侧对 ``facet=="definition"`` 同样命中（与 world 子句对称）。"""
    from packages.core.story_state.deltas import high_risk_change_ids

    payload = _payload(character_changes=[_char_change(facet="definition")])
    assert high_risk_change_ids(payload) == ["cc_1"]


def test_core_side_ignores_low_risk_state_and_location():
    """反向对照：core 侧同样认**值**（facet=state / world_kind=location ⇒ 不命中）。"""
    from packages.core.story_state.deltas import high_risk_change_ids

    payload = _payload(
        character_changes=[_char_change()],
        world_changes=[_world_change()],
    )
    assert high_risk_change_ids(payload) == []
