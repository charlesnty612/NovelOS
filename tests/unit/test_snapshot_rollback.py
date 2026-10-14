"""V3.1 P1-1 回归测试（节 5）：rollback 逆清理全集合覆盖（审计 §第3条）。

由 test_snapshot_rebuild.py 按既有分节拆出；_now_iso 为本节私有。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.unit.snapshot_rebuild_helpers import (
    _create_app,
    _evidence,
    _fetch_latest_snapshot_json,
    _make_chapter,
    _make_meta,
    _make_project,
    _open_db,
    _request,
)

# ----------------------------------------------------------------- 5. rollback 逆清理全集合覆盖(审计 §第3条)


def test_rollback_inverse_cleanup_covers_all_collections(tmp_path: Path):
    """rollback 路径必须清理全 7 个领域集合,而非仅 events/hooks。

    复现 ``docs/testing/audit-story-state-20260829.md`` 第 3 条:
    原 ``_inverse_cleanup`` 只收集 ``remove_event_ids`` / ``remove_hook_ids``,
    ``commit_delta`` 阶段仅 DELETE ``plot_events`` / ``hooks`` / ``timeline_events``;
    ``relationships`` / ``narrative_debts`` / ``locations`` / ``factions`` /
    ``world_rules`` 在 rollback 后残留 → 半回滚态。

    角色侧语义（2026-08-31 修复扩展 + 字段级守卫）:
    - 守卫语义：「提交前快照（``delta_row.previous_state_version`` 处的 main
      snapshot）」中存在 character_id → 该 add 是字段级，**不走实体级 DELETE**
      （留给逆 delta 的字段级 remove 恢复）；不存在 → 视为该 commit 实体级首
      次引入的角色，触发实体回收（顾青生产事故场景——但顾青于 ch2 commit
      之前快照已存在，故 ch2 rollback 时不会触发实体 DELETE，需重放 SOP）。
    - 本 fixture 的 ``char_actor`` 在 init_genesis **之前**就 SQL INSERT 到
      characters 表——``build_initial_state`` 从 characters 表 SELECT 构造
      v1 snapshot（snapshot.py L271）→ v1 snapshot 中已含 ``char_actor`` →
      提交前快照已有该 cid → 不在 ``preexisting_char_ids`` 之外走收集 → 新
      守卫下 **不触发实体级 DELETE**，角色与 character_states 保留。**字段
      仍由逆 delta 的字段级 remove 正确移除**（state.location 的 field=
      knowledge 类；本 fixture 是 facet=state field=mood，由 write_through
      步骤 5 按逆 delta 的 after 写回 state_json）。
    - 守卫生效的具象路径：rollback_char_delete C5（既有角色字段级 add → 角色
      存活，字段由逆 delta 字段级 remove 恢复）。
    - char_target 在 v2 commit 中未被任何 change 引用（relationship 仅作为
      to 端点），rollback 不删（验证「只清本 commit 引用过的角色」）。

    测试设计:
    - v1 init。
    - v2 commit 同时写入:
        * location(loc_rbck) / faction(fac_rbck) / world_rule(rule_rbck)
        * character_state 增量(char_actor 的 state field)
        * relationship (char_actor→char_target, type=ally)
        * debt(dbt_rbck)  + new_event(evt_rbck) + new_hook(hk_rbck)
    - 校验 v2 提交后 7 集合 DB 行均存在。
    - 调 ``/api/commits/{v2_commit_id}/rollback``。
    - 修复后断言:
        * relationships / narrative_debts / locations / factions / world_rules
          全部无对应残留行(逆 add → DELETE / 逆 update → 恢复 before);
        * 事件 evt_rbck / 钩 hk_rbck 已被 DELETE(原有回滚回归);
        * char_actor 保留——其在 commit 前快照(v1, init_genesis 由 characters 表
          聚合)中已存在, 字段级守卫豁免实体 DELETE(字段由逆 delta 恢复);
        * char_target 保留(未被本 commit 引用);
        * snapshot 同步保留两角色条目。
    - 修复前(红):关系/债务/世界实体表均残留。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="rollback_full_cleanup")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # 准备 2 个角色(relationship 需要 from→to 两侧角色存在)
            from packages.core.db import get_connection
            _conn = get_connection(str(db_path))
            try:
                _conn.execute(
                    """INSERT INTO characters
                       (character_id, project_id, name, core_json, visibility,
                        who_knows, created_at, updated_at)
                       VALUES (?, ?, ?, '{}', 'VISIBLE', NULL, ?, ?)
                    """,
                    ("char_actor", pid, "行动者", _now_iso(), _now_iso()),
                )
                _conn.execute(
                    """INSERT INTO characters
                       (character_id, project_id, name, core_json, visibility,
                        who_knows, created_at, updated_at)
                       VALUES (?, ?, ?, '{}', 'VISIBLE', NULL, ?, ?)
                    """,
                    ("char_target", pid, "目标者", _now_iso(), _now_iso()),
                )
                _conn.commit()
            finally:
                _conn.close()

            # v1 init
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            # v2 commit:写入全 6 集合
            v2_delta_id = "dlt_full_collections"
            v2_delta = {
                **_make_meta(v2_delta_id, chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_rbck_1",
                        "op": "add",
                        "target_id": "char_actor",
                        "character_id": "char_actor",
                        "facet": "state",
                        "field": "mood",
                        "after": "警惕",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "world_changes": [
                    {
                        "change_id": "wc_loc_rbck",
                        "op": "add",
                        "target_id": "loc_rbck",
                        "world_id": "loc_rbck",
                        "world_kind": "location",
                        "field": "name",
                        "after": {"name": "回滚测试点", "statement": "用于回滚全集合的地点", "data_json": {}},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                    {
                        "change_id": "wc_fac_rbck",
                        "op": "add",
                        "target_id": "fac_rbck",
                        "world_id": "fac_rbck",
                        "world_kind": "faction",
                        "field": "name",
                        "after": {"name": "回滚测试派系", "statement": "用于回滚全集合的派系", "data_json": {}},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                    {
                        "change_id": "wc_rule_rbck",
                        "op": "add",
                        "target_id": "rule_rbck",
                        "world_id": "rule_rbck",
                        "world_kind": "rule",
                        "field": "name",
                        "after": {"name": "回滚测试规则", "statement": "用于回滚全集合的规则", "data_json": {}},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "relationship_changes": [
                    {
                        "change_id": "rc_rbck_1",
                        "op": "add",
                        "target_id": "rel_rbck_1",
                        "from_character_id": "char_actor",
                        "to_character_id": "char_target",
                        "relation_type": "ally",
                        "after": {"intensity": 0.7, "note": "初始盟友关系"},
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "new_events": [
                    {
                        "change_id": "ce_rbck_1",
                        "op": "add",
                        "target_id": "evt_rbck",
                        "event_id": "evt_rbck",
                        "type": "encounter",
                        "cause": [],
                        "effects": [],
                        "participants": ["char_actor", "char_target"],
                        "location": "loc_rbck",
                        "time": {"timeline_day": 1, "in_story_date": None},
                        "description": "回滚测试事件",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_rbck_1",
                        "op": "add",
                        "target_id": "hk_rbck",
                        "hook_id": "hk_rbck",
                        "name": "回滚测试钩",
                        "importance": 0.5,
                        "description": "回滚全集合测试用钩",
                        "expected_payoff_chapter_id": None,
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "debt_changes": [
                    {
                        "change_id": "dc_rbck_1",
                        "op": "add",
                        "target_id": "dbt_rbck",
                        "debt_id": "dbt_rbck",
                        "description": "回滚测试伏债",
                        "status_after": "open",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
            }

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=v2_delta)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": v2_delta_id,
                    "author_approval": {"approver": "u", "approved": True},
                    "workflow_run_id": f"wfr_{v2_delta_id}",
                },
            )
            assert r.status_code == 201, r.text
            v2_commit_id = r.json()["commit_id"]

            # 提交后断言:6 集合 DB 行均存在(预条件)
            conn = _open_db(db_path)
            try:
                assert conn.execute("SELECT 1 FROM locations WHERE location_id=?", ("loc_rbck",)).fetchone()
                assert conn.execute("SELECT 1 FROM factions WHERE faction_id=?", ("fac_rbck",)).fetchone()
                assert conn.execute("SELECT 1 FROM world_rules WHERE world_rule_id=?", ("rule_rbck",)).fetchone()
                assert conn.execute(
                    "SELECT 1 FROM relationships WHERE from_character_id=? AND to_character_id=? AND relation_type=?",
                    ("char_actor", "char_target", "ally"),
                ).fetchone()
                assert conn.execute("SELECT 1 FROM narrative_debts WHERE debt_id=?", ("dbt_rbck",)).fetchone()
                assert conn.execute("SELECT 1 FROM plot_events WHERE event_id=?", ("evt_rbck",)).fetchone()
                assert conn.execute("SELECT 1 FROM hooks WHERE hook_id=?", ("hk_rbck",)).fetchone()
            finally:
                conn.close()

            # 回滚 v2
            r = await _request(
                app, "POST", f"/api/commits/{v2_commit_id}/rollback",
                json={"author_approval": {"approver": "u", "approved": True}},
            )
            assert r.status_code in (200, 201), r.text

            # 关键断言(修复后绿 / 修复前红):
            # 1) 6 集合（loc/fac/rule/debt/event/hook）DB 表全部清理干净(逆 add → DELETE)
            conn = _open_db(db_path)
            try:
                leftovers = {
                    "locations": conn.execute("SELECT 1 FROM locations WHERE location_id=?", ("loc_rbck",)).fetchone(),
                    "factions": conn.execute("SELECT 1 FROM factions WHERE faction_id=?", ("fac_rbck",)).fetchone(),
                    "world_rules": conn.execute("SELECT 1 FROM world_rules WHERE world_rule_id=?", ("rule_rbck",)).fetchone(),
                    # 关系：char_actor 在 commit 前快照中存在 → 字段级守卫豁免
                    # 实体级回收，但关系本身在 commit_delta 步骤 5 由逆 delta
                    # 的 op=remove 清理（write_through 对 op=remove 不 DELETE，
                    # 但逆 delta 中关系 op=remove 走 remove_relationship_keys）→
                    # 应被 DELETE。验证语义需分两类：保留 char_actor + char_target；
                    # 仅 relationship 行 DELETE。
                    "relationships": conn.execute(
                        "SELECT 1 FROM relationships WHERE from_character_id=? AND to_character_id=? AND relation_type=?",
                        ("char_actor", "char_target", "ally"),
                    ).fetchone(),
                    "narrative_debts": conn.execute("SELECT 1 FROM narrative_debts WHERE debt_id=?", ("dbt_rbck",)).fetchone(),
                    "plot_events": conn.execute("SELECT 1 FROM plot_events WHERE event_id=?", ("evt_rbck",)).fetchone(),
                    "hooks": conn.execute("SELECT 1 FROM hooks WHERE hook_id=?", ("hk_rbck",)).fetchone(),
                }
            finally:
                conn.close()
            for tbl, row in leftovers.items():
                assert row is None, (
                    f"rollback 逆清理失败:{tbl} 表残留已回滚 commit 的行 {row}"
                )

            # 2) 角色实体保留（字段级守卫）：char_actor 在 commit 前 snapshot_v1
            # 已存在（build_initial_state 从 characters 表 SELECT），新守卫下
            # 不进 remove_character_ids；character_states 至少 v1 seed 行仍在。
            conn = _open_db(db_path)
            try:
                actor_row = conn.execute("SELECT name FROM characters WHERE character_id=?", ("char_actor",)).fetchone()
                assert actor_row is not None, (
                    "rollback 后 char_actor 应保留（commit 前快照已有该 cid，字段级守卫豁免实体 DELETE）"
                )
                n_states = conn.execute(
                    "SELECT COUNT(*) AS c FROM character_states WHERE character_id=?", ("char_actor",),
                ).fetchone()
                assert n_states["c"] >= 1, "character_states 历史行被误清"
                target_row = conn.execute("SELECT name FROM characters WHERE character_id=?", ("char_target",)).fetchone()
                assert target_row is not None, "char_target 应保留（未被本 commit 引用）"
            finally:
                conn.close()

            # 3) 快照同步:对应 id 不在 6 集合中;角色两侧保留
            final_snap = _fetch_latest_snapshot_json(db_path, pid)
            assert "loc_rbck" not in (final_snap.get("world", {}).get("locations") or {}), (
                f"rollback 后 snapshot.world.locations 残留 loc_rbck: {final_snap.get('world', {}).get('locations')}"
            )
            assert "fac_rbck" not in (final_snap.get("world", {}).get("factions") or {}), (
                f"rollback 后 snapshot.world.factions 残留 fac_rbck: {final_snap.get('world', {}).get('factions')}"
            )
            assert "rule_rbck" not in (final_snap.get("world", {}).get("world_rules") or []), (
                "rollback 后 snapshot.world.world_rules 残留 rule_rbck"
            )
            snap_char_ids = {c.get("character_id") for c in (final_snap.get("characters") or [])}
            assert "char_actor" in snap_char_ids, (
                f"rollback 后 snapshot.characters 应保留 char_actor（字段级守卫）: {snap_char_ids}"
            )
            assert "char_target" in snap_char_ids, (
                f"rollback 后 snapshot.characters 应保留 char_target（未引用）: {snap_char_ids}"
            )
            assert not any(d.get("debt_id") == "dbt_rbck" for d in final_snap.get("debts", [])), (
                "rollback 后 snapshot.debts 残留 dbt_rbck"
            )

    asyncio.run(run())


def _now_iso() -> str:
    from packages.core.ids import now_iso as _ni
    return _ni()


