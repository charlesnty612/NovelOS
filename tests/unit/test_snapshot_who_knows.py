"""V3.1 P1 回归测试：snapshot 携带 who_knows（P1 节）。

由 test_snapshot_rebuild.py 按既有分节拆出。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tests.unit.snapshot_rebuild_helpers import (
    _create_app,
    _evidence,
    _fetch_latest_snapshot_json,
    _make_chapter,
    _make_client,
    _make_meta,
    _make_project,
    _open_db,
    _request,
)

# ----------------------------------------------------------------- P1: snapshot 携带 who_knows
# 背景:knowledge_leakage guardrail (§4.5) 读取 snapshot.events[eid].who_knows 与
# snapshot.hooks[hid].who_knows。原 bug:rebuild_snapshot_collections_from_db 的
# 三个 _load_* SELECT 不含 who_knows,导致快照全部 who_knows=None,校验空转。
# 修复后:快照三层 (events / hooks / debts) 都必须携带 who_knows。
# 关系侧:_load_relationships_for 同步补 visibility/who_knows,与知识防护网配套。
# 模式:与既有 test_consistency_roundtrip_no_drift_after_commit_delta 同款端到端
# ASGI 调用,但额外断言 who_knows 字段值。
# 存量快照不回填——只有 commit 重建后产生的新快照才带全 who_knows。


def test_snapshot_hooks_debts_events_carry_who_knows(tmp_path: Path):
    """commit 后新快照的 hooks / debts / events 条目 must carry who_knows=非 None,
    否则 knowledge_leakage guardrail 全 pass(校验空转)。

    验证矩阵:
    - new_hooks 含显式 who_knows → snap.hooks[hid].who_knows == 同 list
    - new_events 含显式 who_knows → snap.events[eid].who_knows == 同 list
    - debt_changes 含显式 who_knows → snap.debts[did].who_knows == 同 list
    - relationship_changes 含 who_knows → snap.characters[cid].relationships[].who_knows == 同 list
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="who_knows_snapshot")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # init genesis
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            # seed 两个 character（relationships 端到端需要 from/to 两角色行）
            async with _make_client(app) as client:
                rc = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "actor", "role": "supporting"},
                )
            assert rc.status_code in (201, 200), rc.text
            actor_cid = rc.json()["character_id"]
            async with _make_client(app) as client:
                rc2 = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "b", "role": "supporting"},
                )
            assert rc2.status_code in (201, 200), rc2.text
            b_cid = rc2.json()["character_id"]

            delta_id = "dlt_who_knows_snapshot"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [
                    {
                        "change_id": "rc_wk_1",
                        "op": "add",
                        "target_id": f"{actor_cid}:{b_cid}",
                        "from_character_id": actor_cid,
                        "to_character_id": b_cid,
                        "relation_type": "trust",
                        "before": None,
                        "after": {"value": 0.7},
                        "who_knows": [actor_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "new_events": [
                    {
                        "change_id": "ev_wk_1",
                        "op": "add",
                        "target_id": "evt_wk",
                        "event_id": "evt_wk",
                        "type": "revelation",
                        "cause": [],
                        "effects": [],
                        "participants": [actor_cid],
                        "location": None,
                        "time": {"timeline_day": 1, "in_story_date": None},
                        "description": "暗中约定",
                        "who_knows": [actor_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_wk_1",
                        "op": "add",
                        "target_id": "hk_wk",
                        "hook_id": "hk_wk",
                        "name": "秘密约定钩",
                        "importance": 0.7,
                        "description": "秘密约定存续",
                        "expected_payoff_chapter_id": None,
                        "who_knows": [actor_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "debt_changes": [
                    {
                        "change_id": "dc_wk_1",
                        "op": "add",
                        "target_id": "dbt_wk",
                        "debt_id": "dbt_wk",
                        "description": "秘密约定待兑",
                        "status_after": "open",
                        "who_knows": [actor_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
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

            # hooks:每个条目必有 who_knows,且与 delta 声明一致
            hooks_by_id = {h["hook_id"]: h for h in snap["hooks"]}
            assert "hk_wk" in hooks_by_id, "new hook 应进 snapshot"
            assert hooks_by_id["hk_wk"].get("who_knows") == [actor_cid], (
                f"hook.who_knows 应保留 delta 声明;实际={hooks_by_id['hk_wk'].get('who_knows')}"
            )

            # events:每个条目必有 who_knows
            assert "evt_wk" in snap["events"], "new event 应进 snapshot"
            assert snap["events"]["evt_wk"].get("who_knows") == [actor_cid], (
                f"event.who_knows 应保留 delta 声明;实际={snap['events']['evt_wk'].get('who_knows')}"
            )

            # debts:每个条目必有 who_knows
            debts_by_id = {d["debt_id"]: d for d in snap["debts"]}
            assert "dbt_wk" in debts_by_id, "new debt 应进 snapshot"
            assert debts_by_id["dbt_wk"].get("who_knows") == [actor_cid], (
                f"debt.who_knows 应保留 delta 声明;实际={debts_by_id['dbt_wk'].get('who_knows')}"
            )

            # relationships:actor.relationships[].who_knows 与 delta 声明一致
            actor_char = next(
                c for c in snap["characters"] if c["character_id"] == actor_cid
            )
            assert actor_char["relationships"], "actor 关系应进 snapshot"
            target_rel = next(
                (
                    r for r in actor_char["relationships"]
                    if r.get("to_character_id") == b_cid
                    and r.get("relation_type") == "trust"
                ),
                None,
            )
            assert target_rel is not None, "trust 关系应在 snapshot"
            assert target_rel.get("who_knows") == [actor_cid], (
                f"relationship.who_knows 应保留 delta 声明;实际={target_rel.get('who_knows')}"
            )
            assert target_rel.get("visibility") == "RESTRICTED", (
                f"relationship.visibility 应保留;实际={target_rel.get('visibility')}"
            )

    asyncio.run(run())


def test_snapshot_relationships_persisted_to_db(tmp_path: Path):
    """端到端:commit 后 DB 行 relationships 携带 visibility/who_knows(P0 修复回归)。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="rel_db_persist")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            async with _make_client(app) as client:
                rc = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "actor", "role": "supporting"},
                )
            assert rc.status_code in (201, 200), rc.text
            actor_cid = rc.json()["character_id"]
            async with _make_client(app) as client:
                rc2 = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "b", "role": "supporting"},
                )
            assert rc2.status_code in (201, 200), rc2.text
            b_cid = rc2.json()["character_id"]

            delta_id = "dlt_rel_persist"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [
                    {
                        "change_id": "rc_persist_1",
                        "op": "add",
                        "target_id": f"{actor_cid}:{b_cid}",
                        "from_character_id": actor_cid,
                        "to_character_id": b_cid,
                        "relation_type": "rival",
                        "before": None,
                        "after": {"value": 0.5},
                        "who_knows": [actor_cid, b_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text

            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text

            # 直接读 DB 行断言
            conn = _open_db(db_path)
            try:
                row = conn.execute(
                    "SELECT visibility, who_knows, state_json FROM relationships "
                    "WHERE from_character_id = ? AND to_character_id = ? AND relation_type = ?",
                    (actor_cid, b_cid, "rival"),
                ).fetchone()
                assert row is not None, "relationship 行应落库"
                assert row["visibility"] == "RESTRICTED", (
                    f"DB 行 visibility 应为 RESTRICTED;实际={row['visibility']}"
                )
                assert json.loads(row["who_knows"]) == [actor_cid, b_cid], (
                    f"DB 行 who_knows 应为 [{actor_cid!r}, {b_cid!r}];实际={row['who_knows']}"
                )
            finally:
                conn.close()

    asyncio.run(run())


def test_relationship_inverse_update_restores_state_json_and_preserves_columns(
    tmp_path: Path,
):
    """P0 修复回归:rollback 路径下,relationship 逆 update 必须:
    1. 恢复 before 的 state_json（既有行为）;
    2. 不破坏 visibility / who_knows 列——按三态语义（缺省=沿用）保留。

    背景:observer-v1 当前 schema 只允许 change 声明 after 的 V/W,before
    字段不含 V/W（语义上 before 的 V/W 是「上一态」的,observer 不能精确
    表达）。commit_delta 阶段 write_through 已把 after.V/after.W 落库；
    rollback 时三态语义下,V/W 列按缺省语义保留 DB 现值——等同"恢复到
    add 时 V/W"或"保留 update 后 V/W",由数据流决定。本测试聚焦「不破坏」：
    state_json 必须恢复,V/W 列不因 rollback 写入 None/PUBLIC 等默认值。

    流程:
    1. add 带 V=RESTRICTED/W=[actor_cid]（DB 落 RESTRICTED/[actor_cid]）。
    2. update 改 V=VISIBLE/W=[actor_cid,b_cid],state_json 0.3→0.9。
    3. rollback update:state_json 恢复为 0.3,V/W 保留 update 后值（VISIBLE/[a,b]）
       ——因 update 的 V/W 缺省语义不再精确回退到 add 时,这是 observer-v1
       当前 schema 的合理语义边界。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="rel_inverse_preserve")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            async with _make_client(app) as client:
                rc = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "actor", "role": "supporting"},
                )
            assert rc.status_code in (201, 200), rc.text
            actor_cid = rc.json()["character_id"]
            async with _make_client(app) as client:
                rc2 = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "b", "role": "supporting"},
                )
            assert rc2.status_code in (201, 200), rc2.text
            b_cid = rc2.json()["character_id"]

            # step 1: add 带 V=RESTRICTED/W=[actor_cid]
            delta_add_id = "dlt_rel_inv_add"
            delta_add = {
                **_make_meta(delta_add_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [
                    {
                        "change_id": "rc_inv_add",
                        "op": "add",
                        "target_id": f"{actor_cid}:{b_cid}",
                        "from_character_id": actor_cid,
                        "to_character_id": b_cid,
                        "relation_type": "mentor",
                        "before": None,
                        "after": {"value": 0.3},
                        "who_knows": [actor_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta_add)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_add_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_add_id}",
                },
            )
            assert r.status_code == 201, r.text

            # step 2: update 改 V=VISIBLE/W=[a,b]
            delta_upd_id = "dlt_rel_inv_upd"
            delta_upd = {
                **_make_meta(delta_upd_id, chap, 2),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [
                    {
                        "change_id": "rc_inv_upd",
                        "op": "update",
                        "target_id": f"{actor_cid}:{b_cid}",
                        "from_character_id": actor_cid,
                        "to_character_id": b_cid,
                        "relation_type": "mentor",
                        "before": {"value": 0.3},
                        "after": {"value": 0.9},
                        "who_knows": [actor_cid, b_cid],
                        "visibility": "VISIBLE",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta_upd)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_upd_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_upd_id}",
                },
            )
            assert r.status_code == 201, r.text
            commit_upd_id = r.json()["commit_id"]

            # step 3: rollback update
            async with _make_client(app) as client:
                rr = await client.post(
                    f"/api/commits/{commit_upd_id}/rollback",
                    json={
                        "author_approval": {
                            "approver": "user:local:test",
                            "approved": True,
                            "notes": "rollback for who_knows visibility preserve test",
                        }
                    },
                )
            assert rr.status_code in (200, 201), rr.text

            # 断言：state_json 已恢复 before,V/W 列未被 rollback 写入 NULL/PUBLIC
            conn = _open_db(db_path)
            try:
                row = conn.execute(
                    "SELECT visibility, who_knows, state_json FROM relationships "
                    "WHERE from_character_id = ? AND to_character_id = ? AND relation_type = ?",
                    (actor_cid, b_cid, "mentor"),
                ).fetchone()
                assert row is not None, "rollback 后 relationship 行应仍在"
                # state_json 必须已恢复（既有行为）
                assert json.loads(row["state_json"]) == {"value": 0.3}, (
                    f"rollback 应恢复 state_json;实际={row['state_json']}"
                )
                # V/W 列不因 rollback 缺省语义被覆盖为 NULL/PUBLIC——
                # observer-v1 当前 schema 不支持 before 携带 V/W,逆 update
                # 路径按三态缺省语义保留 DB 现值（update 后的 V/W）。
                # 原 bug:逆 UPDATE 不写 V/W 列,看似"恢复"了——但因 write_through
                # 写透时也是缺省语义,实际是「原 update 后 V/W 因 add 缺省+update
                # 缺省导致两者皆 None」,而修复后 write_through 落 V=VISIBLE/W=[a,b],
                # 逆 UPDATE 不破坏这两列。关键断言:V 列不为 NULL,W 列 JSON 不为空。
                assert row["visibility"] is not None, (
                    f"rollback 不应清空 visibility 列;实际={row['visibility']}"
                )
                wk_raw = row["who_knows"]
                assert wk_raw is not None, (
                    f"rollback 不应清空 who_knows 列;实际={wk_raw}"
                )
                assert json.loads(wk_raw), f"rollback 后 who_knows 应非空列表;实际={wk_raw}"
            finally:
                conn.close()

    asyncio.run(run())
