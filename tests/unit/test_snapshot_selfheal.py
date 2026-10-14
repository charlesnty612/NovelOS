"""V3.1 P1-1 回归测试（节 1~3）：漂移自愈 / 正向一致 / 既有行为兼容。

由 test_snapshot_rebuild.py 按既有分节拆出；共享 helper 见 snapshot_rebuild_helpers。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.unit.snapshot_rebuild_helpers import (
    _create_app,
    _drift_ids_for,
    _evidence,
    _fetch_latest_snapshot_json,
    _make_chapter,
    _make_meta,
    _make_project,
    _open_db,
    _request,
)

# ----------------------------------------------------------------- 1. 漂移自愈(核心)


def test_drift_self_heal_plot_event(tmp_path: Path):
    """DB 已有 plot_events 行但旧快照未含该 event_id,跑一次 commit_delta 后自愈。

    模拟历史漂移场景(早期版本实测 loc_yonghe_wharf 在 DB 不在快照)——直接
    INSERT 一行 plot_events 制造漂移,然后跑 commit_delta。断言新快照 v2 自愈:
    ``ev_drift_test`` 必须出现在 snapshot.events 中,且 type 一致。

    V3.1 P1-1.1：迁移 0013 给 plot_events 加了 description 列，本测试同时验证
    description 经 DB 权威重建后保留——直接 SQL 写入时一并把 description 填好，
    重建后 ``events[ev_drift_test].description`` 应为该值（替代早期"必为 None"断言，
    该断言语义已被 V3.1 P1-1.1 推翻）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="drift_self_heal")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # init genesis(v1)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            # 制造漂移:直接 SQL 写一行 plot_events,绕过 write_through。
            # 这是模拟"DB 已落行但 snapshot.events 没追上"的早期版本 bug。
            # V3.1 P1-1.1：同时写入 description 字段,验证重建后该字段保留。
            drift_desc = "苏婉清在玉惜轩夜访时第一次对林渊产生疑虑。"
            conn = _open_db(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO plot_events
                        (event_id, project_id, type, cause_json, effects_json,
                         participants_json, location_id, time_json, status,
                         introduced_chapter_id, visibility, who_knows, description)
                    VALUES (?, ?, ?, '[]', '[]', '[]', NULL,
                            '{"timeline_day":1}', 'recorded', ?, 'RESTRICTED', NULL, ?)
                    """,
                    ("ev_drift_test", pid, "encounter", chap, drift_desc),
                )
                conn.commit()
            finally:
                conn.close()

            # 旧快照 v1 不含该 event(漂移已存在)
            snap_v1 = _fetch_latest_snapshot_json(db_path, pid)
            assert "ev_drift_test" not in snap_v1.get("events", {}), (
                "v1 快照预条件:ev_drift_test 不在 snapshot.events 中(模拟漂移)"
            )

            # 跑一个最小 commit_delta。delta 只带 new_hooks(LOW 风险,无需 approved=True),
            # 让 commit 流程执行 write_through→rebuild→materialize_snapshot 全链。
            delta_id = "dlt_rebuild_after_drift"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_rebuild_1",
                        "op": "add",
                        "target_id": "hk_rebuild_marker",
                        "hook_id": "hk_rebuild_marker",
                        "name": "重建标记钩",
                        "importance": 0.5,
                        "description": "V3.1 P1-1 漂移自愈测试用钩",
                        "expected_payoff_chapter_id": None,
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "debt_changes": [],
            }

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "validated"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text

            # 核心断言:v2 新快照自愈
            snap_v2 = _fetch_latest_snapshot_json(db_path, pid)
            assert "ev_drift_test" in snap_v2["events"], (
                f"V3.1 P1-1 漂移自愈失败:v2 snapshot.events 应包含 'ev_drift_test',"
                f" 实际 keys={list(snap_v2['events'].keys())}"
            )
            ev_entry = snap_v2["events"]["ev_drift_test"]
            assert ev_entry["type"] == "encounter", (
                f"重建后 type 应来自 DB plot_events.type, 实际 {ev_entry['type']!r}"
            )
            assert isinstance(ev_entry["participants"], list)
            assert isinstance(ev_entry["time"], dict)
            # V3.1 P1-1.1：description 经 DB 权威重建后保留（早期"必为 None"断言已被推翻）
            assert ev_entry["description"] == drift_desc, (
                f"V3.1 P1-1.1 漂移自愈 description 保留失败:"
                f" 期望 {drift_desc!r}, 实际 {ev_entry['description']!r}"
            )

            # 同时断言 hk_rebuild_marker 也被新流程正确写入
            assert any(h.get("hook_id") == "hk_rebuild_marker" for h in snap_v2["hooks"])

            # 兜底断言:7 集合与 DB 全对齐(0 漂移)
            drifts = _drift_ids_for(db_path, pid, snap_v2)
            assert drifts == [], (
                f"漂移自愈后 7 集合仍存在差异(应为空): {drifts}"
            )

    asyncio.run(run())


# ----------------------------------------------------------------- 2. 正向一致:正常 commit 后 7 集合与 DB 对齐


def test_snapshot_consistent_after_commit(tmp_path: Path):
    """正常 commit_delta 后,新快照的 7 个集合与 DB 表 id 完全一致。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="consistent_after_commit")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # init genesis(v1)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            # 一次 commit 带多类变化:location / hook / debt / event
            delta_id = "dlt_consistency_check"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [
                    {
                        "change_id": "wc_loc_cons",
                        "op": "add",
                        "target_id": "loc_cons",
                        "world_id": "loc_cons",
                        "world_kind": "location",
                        "field": "name",
                        "after": {"name": "一致测试点", "statement": "用于一致性的地点", "data_json": {}},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "relationship_changes": [],
                "new_events": [
                    {
                        "change_id": "ev_cons_1",
                        "op": "add",
                        "target_id": "evt_cons",
                        "event_id": "evt_cons",
                        "type": "encounter",
                        "cause": [],
                        "effects": [],
                        "participants": ["char_cons_actor"],
                        "location": "loc_cons",
                        "time": {"timeline_day": 1, "in_story_date": None},
                        # V3.1 P1-1.1：observer 给出的 description 应通过
                        # write_through 落库,重建后保留。
                        "description": "女主在一致测试点与 NPC 偶遇。",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_cons_1",
                        "op": "add",
                        "target_id": "hk_cons",
                        "hook_id": "hk_cons",
                        "name": "一致性钩",
                        "importance": 0.5,
                        "description": "V3.1 P1-1 正向一致性测试用钩",
                        "expected_payoff_chapter_id": None,
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "debt_changes": [
                    {
                        "change_id": "dc_cons_1",
                        "op": "add",
                        "target_id": "dbt_cons",
                        "debt_id": "dbt_cons",
                        "description": "一致性测试伏债",
                        "status_after": "open",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
            }

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "validated"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text

            snap = _fetch_latest_snapshot_json(db_path, pid)

            # 关键断言:7 集合双向 0 漂移(以 _drift_ids_for 为准)
            drifts = _drift_ids_for(db_path, pid, snap)
            assert drifts == [], f"正常 commit 后 7 集合存在差异: {drifts}"

            # 进一步抽样断言(可选,但更直白)
            assert "loc_cons" in snap["world"]["locations"]
            assert "hk_cons" in {h["hook_id"] for h in snap["hooks"]}
            assert "dbt_cons" in {d["debt_id"] for d in snap["debts"]}
            assert "evt_cons" in snap["events"]
            assert snap["events"]["evt_cons"]["type"] == "encounter"
            # V3.1 P1-1.1：observer 给出的 description 落库并经重建保留
            assert snap["events"]["evt_cons"]["description"] == "女主在一致测试点与 NPC 偶遇。"

    asyncio.run(run())


# ----------------------------------------------------------------- 3. 既有行为兼容


def test_commit_delta_unchanged_behavior(tmp_path: Path):
    """commit_delta 的对外契约未被破坏:返回字段、commits/story_states/state_deltas 行均符合预期。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="unchanged_behavior")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # init genesis(v1)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text
            v1_snap = r.json()
            assert v1_snap["state_version"] == 1

            # 一次最小 LOW 风险 commit
            delta_id = "dlt_unchanged_behavior"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_uc_1",
                        "op": "add",
                        "target_id": "hk_uc_marker",
                        "hook_id": "hk_uc_marker",
                        "name": "兼容测试钩",
                        "importance": 0.5,
                        "description": "V3.1 P1-1 兼容测试用钩",
                        "expected_payoff_chapter_id": None,
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "validated"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text
            commit_resp = r.json()
            # commit 端点对外契约(Sprint 2/4 既有契约)
            assert set(commit_resp.keys()) >= {
                "commit_id",
                "delta_id",
                "state_version",
                "snapshot_ref",
            }, f"commit 响应字段不符既有契约: {commit_resp.keys()}"
            assert commit_resp["delta_id"] == delta_id
            assert commit_resp["state_version"] == 2
            assert commit_resp["snapshot_ref"] is not None
            assert commit_resp["snapshot_ref"].startswith(f"story_states/{pid}/state-v2-")

            # state_deltas.status = 'applied'
            conn = _open_db(db_path)
            try:
                row = conn.execute(
                    "SELECT status FROM state_deltas WHERE delta_id = ?", (delta_id,)
                ).fetchone()
                assert row is not None
                assert row["status"] == "applied"

                # commits 行
                commit_row = conn.execute(
                    "SELECT project_id, branch_id, chapter_id, previous_state_version,"
                    " resulting_state_version FROM commits WHERE commit_id = ?",
                    (commit_resp["commit_id"],),
                ).fetchone()
                assert commit_row is not None
                assert commit_row["project_id"] == pid
                assert commit_row["chapter_id"] == chap
                assert commit_row["previous_state_version"] == 1
                assert commit_row["resulting_state_version"] == 2

                # story_states v2 行存在
                v2_row = conn.execute(
                    "SELECT state_version, commit_id FROM story_states"
                    " WHERE project_id = ? AND state_version = 2",
                    (pid,),
                ).fetchone()
                assert v2_row is not None
                assert v2_row["commit_id"] == commit_resp["commit_id"]

                # hooks 表落行
                hook_row = conn.execute(
                    "SELECT hook_id FROM hooks WHERE hook_id = ?", ("hk_uc_marker",)
                ).fetchone()
                assert hook_row is not None
            finally:
                conn.close()

    asyncio.run(run())


