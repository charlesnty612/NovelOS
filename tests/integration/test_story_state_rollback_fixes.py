"""/api/story_state Sprint 2 修复回归测试。

覆盖任务书指定的 6 项新行为：
1. 含 debt_changes（1 条 update）的 commit → rollback 成功，debts 表与快照均还原。
2. 含 resolved_hooks 的 commit → rollback 成功，hooks.status 还原、payoff_chapter_id 置 NULL。
3. 含 new_event+new_hook 的 commit → rollback 后 plot_events/hooks 表行数为 0（领域表无残留）。
4. who_knows 三态写透断言（NULL/'[]'/数组）。
5. validator：update 缺 before、remove 缺 reason 均被拒。
6. facet=definition 且 risk_level=LOW 未审批 → 409。
7. P2-7：集成测试持久化用例改为真实「重启」——同一 tmp db 路径上第二个 create_app
   实例 GET /api/projects/{pid}/state 断言 v3。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings

# ----------------------------------------------------------------- helpers


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "回归项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_character(app, pid: str, name: str = "林夕") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": name, "role": "protagonist"},
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


def _evidence(chapter_id: str) -> dict:
    return {"chapter_id": chapter_id, "scene_id": None, "excerpt": "excerpt", "span": None}


def _make_meta(delta_id: str, chapter_id: str, prev_version: int) -> dict:
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": f"wfr_{delta_id}",
        "previous_state_version": prev_version,
        "created_by": "observer:v1",
        "created_at": "2026-08-23T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
    }


async def _setup_project(tmp_path: Path):
    app = _create_app(tmp_path)
    pid = None
    cid = None
    chap = None
    async with app.router.lifespan_context(app):
        pid = await _make_project(app)
        cid = await _make_character(app, pid)
        chap = await _make_chapter(app, pid)
        r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
        assert r.status_code == 201
    return {"app": app, "pid": pid, "cid": cid, "chap": chap}


# ----------------------------------------------------------------- 1. debt_changes rollback


def test_rollback_with_debt_update_restores_debts_table_and_snapshot(tmp_path: Path):
    """新测 1：含 1 条 debt_changes（update）的 commit → rollback 成功，
    debts 表与快照中 debts[] 列表均还原。"""
    setup = asyncio.run(_setup_project(tmp_path))
    pid = setup["pid"]
    chap = setup["chap"]
    app = setup["app"]

    async def run():
        async with app.router.lifespan_context(app):
            # 第 1 步：add 一个 debt
            d_add = {
                **_make_meta("dlt_debt_add", chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [
                    {
                        "change_id": "dc_add",
                        "op": "add",
                        "target_id": "debt_x",
                        "debt_id": "debt_x",
                        "description": "A owes B",
                        "severity_after": 0.4,
                        "status_after": "open",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d_add)
            assert r.status_code == 201
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_debt_add",
                    "author_approval": {"approver": "user:local:test"},
                    "workflow_run_id": "wfr_debt_add",
                },
            )
            assert r.status_code == 201

            # 第 2 步：update 这个 debt
            d_upd = {
                **_make_meta("dlt_debt_upd", chap, 2),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [
                    {
                        "change_id": "dc_upd",
                        "op": "update",
                        "target_id": "debt_x",
                        "debt_id": "debt_x",
                        "status_before": "open",
                        "status_after": "acknowledged",
                        "severity_before": 0.4,
                        "severity_after": 0.7,
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d_upd)
            assert r.status_code == 201
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_debt_upd",
                    "author_approval": {"approver": "user:local:test"},
                    "workflow_run_id": "wfr_debt_upd",
                },
            )
            assert r.status_code == 201
            commit_upd = r.json()

            # rollback update commit
            r = await _request(
                app, "POST", f"/api/commits/{commit_upd['commit_id']}/rollback",
                json={"author_approval": {"approver": "user:local:test", "approved": True}},
            )
            assert r.status_code == 201, r.text

            # 验证 narrative_debts 表 status/severity 已还原
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT status, severity FROM narrative_debts WHERE debt_id = ?",
                    ("debt_x",),
                ).fetchone()
                assert row["status"] == "open"
                assert abs(row["severity"] - 0.4) < 1e-9
            finally:
                conn.close()

            # 验证快照中 debts[0].status 还原
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            snap = r.json()
            debt = next(d for d in snap["debts"] if d["debt_id"] == "debt_x")
            assert debt["status"] == "open"
            assert abs(debt["severity"] - 0.4) < 1e-9

    asyncio.run(run())


# ----------------------------------------------------------------- 2. resolved_hooks rollback


def test_rollback_with_resolved_hook_clears_payoff_chapter_id(tmp_path: Path):
    """新测 2：含 resolved_hooks 的 commit → rollback 成功，
    hooks.status 还原、payoff_chapter_id 置 NULL。"""
    setup = asyncio.run(_setup_project(tmp_path))
    pid = setup["pid"]
    chap = setup["chap"]
    app = setup["app"]

    async def run():
        async with app.router.lifespan_context(app):
            # 先建一个 hook（commit 到 v2）
            d_hook = {
                **_make_meta("dlt_new_hook", chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_1",
                        "op": "add",
                        "target_id": "hook_secret",
                        "hook_id": "hook_secret",
                        "name": "secret",
                        "importance": 0.7,
                        "description": "a secret",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d_hook)
            assert r.status_code == 201
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_new_hook",
                    "author_approval": {"approver": "user:local:test"},
                    "workflow_run_id": "wfr_new_hook",
                },
            )
            assert r.status_code == 201

            # resolve hook（commit 到 v3）
            d_resolve = {
                **_make_meta("dlt_resolve", chap, 2),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [
                    {
                        "change_id": "rh_1",
                        "op": "update",
                        "target_id": "hook_secret",
                        "hook_id": "hook_secret",
                        "from_status": "OPEN",
                        "to_status": "RESOLVED",
                        "payoff_chapter_id": chap,
                        "payoff_summary": "secret revealed",
                        "confidence": 0.95,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d_resolve)
            assert r.status_code == 201
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_resolve",
                    "author_approval": {"approver": "user:local:test"},
                    "workflow_run_id": "wfr_resolve",
                },
            )
            assert r.status_code == 201
            commit_resolve = r.json()

            # 验证 hooks 表此时 status=RESOLVED + payoff_chapter_id 非空
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT status, payoff_chapter_id FROM hooks WHERE hook_id = ?",
                    ("hook_secret",),
                ).fetchone()
                assert row["status"] == "RESOLVED"
                assert row["payoff_chapter_id"] == chap
            finally:
                conn.close()

            # rollback resolve commit
            r = await _request(
                app, "POST", f"/api/commits/{commit_resolve['commit_id']}/rollback",
                json={"author_approval": {"approver": "user:local:test", "approved": True}},
            )
            assert r.status_code == 201, r.text

            # 验证 hooks.status 还原为 OPEN + payoff_chapter_id 置 NULL
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT status, payoff_chapter_id FROM hooks WHERE hook_id = ?",
                    ("hook_secret",),
                ).fetchone()
                assert row["status"] == "OPEN"
                assert row["payoff_chapter_id"] is None
            finally:
                conn.close()

    asyncio.run(run())


# ----------------------------------------------------------------- 3. new_event+new_hook rollback → no residuals


def test_rollback_clears_domain_table_rows_for_new_events_and_new_hooks(tmp_path: Path):
    """新测 3：含 new_event + new_hook 的 commit → rollback 后
    plot_events / hooks 表对应行数为 0（领域表无残留）。"""
    setup = asyncio.run(_setup_project(tmp_path))
    pid = setup["pid"]
    chap = setup["chap"]
    cid = setup["cid"]
    app = setup["app"]

    async def run():
        async with app.router.lifespan_context(app):
            d = {
                **_make_meta("dlt_event_hook", chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [
                    {
                        "change_id": "ev_1",
                        "op": "add",
                        "target_id": "event_first",
                        "event_id": "event_first",
                        "type": "encounter",
                        "participants": [cid],
                        "time": {"timeline_day": 1, "in_story_date": None},
                        "description": "首次遭遇",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_1",
                        "op": "add",
                        "target_id": "hook_mystery",
                        "hook_id": "hook_mystery",
                        "name": "神秘符号",
                        "importance": 0.8,
                        "description": "墙上出现的神秘符号",
                        "confidence": 0.85,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d)
            assert r.status_code == 201
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_event_hook",
                    "author_approval": {"approver": "user:local:test"},
                    "workflow_run_id": "wfr_event_hook",
                },
            )
            assert r.status_code == 201
            commit_v2 = r.json()

            # rollback
            r = await _request(
                app, "POST", f"/api/commits/{commit_v2['commit_id']}/rollback",
                json={"author_approval": {"approver": "user:local:test", "approved": True}},
            )
            assert r.status_code == 201, r.text

            # 领域表无残留
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM plot_events WHERE event_id = ?",
                    ("event_first",),
                ).fetchone()
                assert row["c"] == 0
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM hooks WHERE hook_id = ?",
                    ("hook_mystery",),
                ).fetchone()
                assert row["c"] == 0
            finally:
                conn.close()

    asyncio.run(run())


# ----------------------------------------------------------------- 4. who_knows 三态写透


def test_who_knows_three_state_writethrough(tmp_path: Path):
    """新测 4：who_knows 三态写透断言（NULL / '[]' / 数组）。"""
    setup = asyncio.run(_setup_project(tmp_path))
    pid = setup["pid"]
    chap = setup["chap"]
    cid = setup["cid"]
    app = setup["app"]

    async def run():
        async with app.router.lifespan_context(app):
            # 三段 delta：NULL / [] / 数组 各走一次；逐段 previous_state_version +1
            cases = [
                ("dlt_who_null", None, 1),
                ("dlt_who_empty", [], 2),
                ("dlt_who_full", ["char_alice", "char_bob"], 3),
            ]
            for delta_id, who_knows, prev_v in cases:
                d = {
                    **_make_meta(delta_id, chap, prev_v),
                    "character_changes": [
                        {
                            "change_id": f"cc_{delta_id}",
                            "op": "update",
                            "target_id": cid,
                            "character_id": cid,
                            "facet": "state",
                            "field": "state.location",
                            "before": "Unknown",
                            "after": "Cave",
                            "confidence": 0.9,
                            "evidence": _evidence(chap),
                            "risk_level": "LOW",
                            "who_knows": who_knows,
                        }
                    ],
                    "world_changes": [],
                    "relationship_changes": [],
                    "new_events": [],
                    "resolved_hooks": [],
                    "new_hooks": [],
                    "debt_changes": [],
                }
                r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d)
                assert r.status_code == 201, r.text
                r = await _request(
                    app, "POST", f"/api/projects/{pid}/commits",
                    json={
                        "delta_id": delta_id,
                        "author_approval": {"approver": "user:local:test"},
                        "workflow_run_id": f"wfr_{delta_id}",
                    },
                )
                assert r.status_code == 201, r.text

            # 验证最后一次写入的 character_states 行 who_knows 列（数组 → JSON）
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT who_knows FROM character_states WHERE character_id = ? "
                    "ORDER BY state_version DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                assert row["who_knows"] is not None
                # JSON 数组（ensure_ascii=False → 中英混排可保留）
                parsed = json.loads(row["who_knows"])
                assert parsed == ["char_alice", "char_bob"]
            finally:
                conn.close()

            # 直接 service 层验证三态编码 helper（NULL / '[]' / 数组 JSON）
            from packages.core.story_state.service import _encode_who_knows
            assert _encode_who_knows(None) is None
            assert _encode_who_knows([]) == "[]"
            assert _encode_who_knows(["a", "b"]) == '["a", "b"]'

    asyncio.run(run())


# ----------------------------------------------------------------- 5. validator business rules


def test_validator_rejects_update_without_before():
    """新测 5a：op=update 缺 before → validator 报错。"""
    from packages.core.story_state.validator import validate_delta

    delta = {
        "delta_id": "dlt_v",
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": "ch_test",
        "workflow_run_id": "wfr_test",
        "previous_state_version": 1,
        "created_by": "observer:v1",
        "created_at": "2026-08-23T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
        "character_changes": [
            {
                "change_id": "cc_1",
                "op": "update",
                "target_id": "char_alice",
                "character_id": "char_alice",
                "facet": "state",
                "field": "state.location",
                # before 故意缺失（schema 允许 null）—— 但 business 规则应拒
                "after": "Cave",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "scene_id": None, "excerpt": "e", "span": None},
                "risk_level": "LOW",
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    errors = validate_delta(delta)
    assert any("op='update'" in e and "before" in e for e in errors)


def test_validator_rejects_remove_without_reason():
    """新测 5b：op=remove 缺 reason → validator 报错。"""
    from packages.core.story_state.validator import validate_delta

    delta = {
        "delta_id": "dlt_v",
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": "ch_test",
        "workflow_run_id": "wfr_test",
        "previous_state_version": 1,
        "created_by": "observer:v1",
        "created_at": "2026-08-23T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
        "character_changes": [
            {
                "change_id": "cc_1",
                "op": "remove",
                "target_id": "char_alice",
                "character_id": "char_alice",
                "facet": "state",
                "field": "state.location",
                "before": "Forest",
                "after": None,
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "scene_id": None, "excerpt": "e", "span": None},
                "risk_level": "LOW",
                # reason 故意缺失
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    errors = validate_delta(delta)
    assert any("op='remove'" in e and "reason" in e for e in errors)


# ----------------------------------------------------------------- 6. facet=definition LOW risk without approval


def test_facet_definition_low_risk_requires_approval(tmp_path: Path):
    """新测 6：facet=definition 且 risk_level=LOW 未审批 → 409。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            # facet=definition + risk_level=LOW 仍需审批
            d = {
                **_make_meta("dlt_def_low", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_def",
                        "op": "update",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "definition",
                        "field": "core.personality",
                        "before": "brave",
                        "after": "cunning",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",  # 关键：LOW 但 facet=definition
                    }
                ],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d)
            assert r.status_code == 201
            assert r.json()["status"] == "validated"

            # commit 无 approved=True → 409
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_def_low",
                    "author_approval": {"approver": "user:local:test"},  # 无 approved=True
                    "workflow_run_id": "wfr_def_low",
                },
            )
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert detail["error"] == "approval_required"
            assert "cc_def" in detail["high_risk_change_ids"]

    asyncio.run(run())


# ----------------------------------------------------------------- 7. P2-7：真实「重启」断言


def test_persistence_v3_after_real_restart(tmp_path: Path):
    """P2-7：集成测试持久化用例改为真实「重启」——同一 tmp db 路径上
    第二个 create_app 实例 GET /api/projects/{pid}/state 断言 v3。"""
    # 第一阶段：建项目并提交到 v3
    app1 = _create_app(tmp_path)

    async def phase1():
        async with app1.router.lifespan_context(app1):
            pid = await _make_project(app1)
            cid = await _make_character(app1, pid)
            chap = await _make_chapter(app1, pid)
            r = await _request(app1, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201
            d = {
                **_make_meta("dlt_persist", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_persist",
                        "op": "update",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "state",
                        "field": "state.location",
                        "before": "Unknown",
                        "after": "Cave",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app1, "POST", f"/api/projects/{pid}/deltas", json=d)
            assert r.status_code == 201
            r = await _request(
                app1, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_persist",
                    "author_approval": {"approver": "user:local:test"},
                    "workflow_run_id": "wfr_persist",
                },
            )
            assert r.status_code == 201
            commit_v2 = r.json()
            # rollback → v3
            r = await _request(
                app1, "POST", f"/api/commits/{commit_v2['commit_id']}/rollback",
                json={"author_approval": {"approver": "user:local:test", "approved": True}},
            )
            assert r.status_code == 201
            assert r.json()["state_version"] == 3
            return pid

    pid = asyncio.run(phase1())

    # 第二阶段：新 create_app 实例（真实重启语义）
    app2 = _create_app(tmp_path)

    async def phase2():
        async with app2.router.lifespan_context(app2):
            r = await _request(app2, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            snap = r.json()
            assert snap["state_version"] == 3
            # 字符状态：rollback 后 location = before = "Unknown"
            char = next(c for c in snap["characters"] if c["character_id"] == "char_x" or c.get("name") == "林夕")
            assert char["current_state"].get("location") == "Unknown"

    asyncio.run(phase2())


# ----------------------------------------------------------------- 8. rollback idempotency guard (P0 production bug)


def test_rollback_is_idempotent_second_rollback_rejected(tmp_path: Path):
    """新测 8（P0 生产 bug）：rollback 幂等守卫——同一 commit 第二次 rollback
    必须被拒（409 StateConflictError），commits 表只产生一条逆 commit。

    生产实锤：cmt_6c9faba70b8c 被回滚两次，产生 cmt_480699e94622 + cmt_6a3a028d522d
    两条逆 commit，update 类变更被反转两次=回到原值，状态污染。
    """
    setup = asyncio.run(_setup_project(tmp_path))
    pid = setup["pid"]
    chap = setup["chap"]
    cid = setup["cid"]
    app = setup["app"]

    async def run():
        async with app.router.lifespan_context(app):
            # commit 一条 character update（v2）
            d = {
                **_make_meta("dlt_idem_target", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_idem",
                        "op": "update",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "state",
                        "field": "state.location",
                        "before": "Forest",
                        "after": "Cave",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_idem_target",
                    "author_approval": {"approver": "user:local:test"},
                    "workflow_run_id": "wfr_idem_target",
                },
            )
            assert r.status_code == 201, r.text
            target_commit = r.json()["commit_id"]

            # 第一次 rollback → 201
            r1 = await _request(
                app, "POST", f"/api/commits/{target_commit}/rollback",
                json={"author_approval": {"approver": "user:local:test", "approved": True}},
            )
            assert r1.status_code == 201, r1.text
            first_rollback_commit = r1.json()["commit_id"]

            # 第二次 rollback 同 commit → 409（幂等守卫）
            r2 = await _request(
                app, "POST", f"/api/commits/{target_commit}/rollback",
                json={"author_approval": {"approver": "user:local:test", "approved": True}},
            )
            assert r2.status_code == 409, r2.text
            detail = r2.json()["detail"]
            assert "already been rolled back" in str(detail)

            # commits 表只有一条逆 commit（rollback_of = target_commit）
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT commit_id FROM commits WHERE rollback_of = ?",
                    (target_commit,),
                ).fetchall()
                assert len(rows) == 1, f"expected 1 rollback, got {len(rows)}"
                assert rows[0]["commit_id"] == first_rollback_commit
            finally:
                conn.close()

    asyncio.run(run())


# ----------------------------------------------------------------- 9. rollback demotes COMMITTED chapter to DRAFTED (P1 production bug)


def test_rollback_demotes_committed_chapter_to_drafted(tmp_path: Path):
    """新测 9（P1 生产 bug）：rollback 成功后，若原 commit 的 chapter 当前
    status='COMMITTED'，必须联动回退为 'DRAFTED'。

    生产实锤：COMMITTED 章 rollback 后仍 COMMITTED，save_draft 拦截后续改稿。
    """
    setup = asyncio.run(_setup_project(tmp_path))
    pid = setup["pid"]
    chap = setup["chap"]
    cid = setup["cid"]
    app = setup["app"]

    async def run():
        async with app.router.lifespan_context(app):
            # 先把 chapter 推到 COMMITTED（PLANNED→DRAFTED→REVIEWED→COMMITTED）
            for next_status in ("DRAFTED", "REVIEWED", "COMMITTED"):
                r = await _request(
                    app, "PATCH", f"/api/chapters/{chap}",
                    json={"status": next_status},
                )
                assert r.status_code == 200, r.text
                assert r.json()["status"] == next_status

            # commit 一条 character update（v2）
            d = {
                **_make_meta("dlt_committed_target", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_committed",
                        "op": "update",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "state",
                        "field": "state.location",
                        "before": "Forest",
                        "after": "Cave",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_committed_target",
                    "author_approval": {"approver": "user:local:test"},
                    "workflow_run_id": "wfr_committed_target",
                },
            )
            assert r.status_code == 201, r.text
            target_commit = r.json()["commit_id"]

            # rollback → 201
            r = await _request(
                app, "POST", f"/api/commits/{target_commit}/rollback",
                json={"author_approval": {"approver": "user:local:test", "approved": True}},
            )
            assert r.status_code == 201, r.text

            # 验证：chapter.status 已联动回退为 DRAFTED
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT status, updated_at FROM chapters WHERE chapter_id = ?",
                    (chap,),
                ).fetchone()
                assert row["status"] == "DRAFTED", f"expected DRAFTED, got {row['status']}"
                assert row["updated_at"] is not None
            finally:
                conn.close()

            # 验证：GET /api/chapters/{id} 也返回 DRAFTED（API 层一致）
            r = await _request(app, "GET", f"/api/chapters/{chap}")
            assert r.status_code == 200
            assert r.json()["status"] == "DRAFTED"

            # 验证：save_draft 不再被拦截（status=DRAFTED 允许）
            r = await _request(
                app, "POST", f"/api/chapters/{chap}/drafts",
                json={"content": "rollback 后改稿测试"},
            )
            assert r.status_code == 201, r.text

    asyncio.run(run())


# ----------------------------------------------------------------- 10. rollback does NOT touch non-COMMITTED chapter status


def _setup_then_drive_and_rollback_status(
    tmp_root: Path, initial_status: str,
) -> str:
    """helper for test 10：每个用例独立 tmp_path/setup，跑完后返回 rollback
    之后 chapters.status 的最终值。

    设计要点：使用 sync httpx（非 ASGITransport + 内部 asyncio.run），
    避免嵌套 event loop。
    """
    from packages.core.api.main import create_app as _create
    from packages.core.config import Settings as _Settings

    sub = tmp_root / initial_status.lower()
    sub.mkdir()
    s = _Settings(data_dir=sub, log_level="WARNING")
    app = _create(s)

    async def _go():
        async with app.router.lifespan_context(app):
            # 1) 建项目 + 角色 + 章 + init state
            async with _make_client(app) as client:
                r = await client.post("/api/projects", json={"name": f"proj_{initial_status}"})
                assert r.status_code == 201, r.text
                pid = r.json()["project_id"]
                r = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "林夕", "role": "protagonist"},
                )
                assert r.status_code == 201, r.text
                cid = r.json()["character_id"]
                r = await client.post(
                    f"/api/projects/{pid}/chapters",
                    json={"number": 1, "title": "第一章"},
                )
                assert r.status_code == 201, r.text
                chap = r.json()["chapter_id"]
                r = await client.post(
                    f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
                )
                assert r.status_code == 201, r.text

                # 2) 推到 initial_status（仅 PLANNED/DRAFTED/REVIEWED 可达；RELEASED 略）
                chain = {
                    "PLANNED": [],
                    "DRAFTED": ["DRAFTED"],
                    "REVIEWED": ["DRAFTED", "REVIEWED"],
                }[initial_status]
                for s_ in chain:
                    r = await client.patch(f"/api/chapters/{chap}", json={"status": s_})
                    assert r.status_code == 200, r.text

                # 3) commit character update（v2）
                did = f"dlt_{initial_status.lower()}"
                d = {
                    **_make_meta(did, chap, 1),
                    "character_changes": [
                        {
                            "change_id": f"cc_{initial_status.lower()}",
                            "op": "update",
                            "target_id": cid,
                            "character_id": cid,
                            "facet": "state",
                            "field": "state.location",
                            "before": "Forest",
                            "after": "Cave",
                            "confidence": 0.9,
                            "evidence": _evidence(chap),
                            "risk_level": "LOW",
                        }
                    ],
                    "world_changes": [],
                    "relationship_changes": [],
                    "new_events": [],
                    "resolved_hooks": [],
                    "new_hooks": [],
                    "debt_changes": [],
                }
                r = await client.post(f"/api/projects/{pid}/deltas", json=d)
                assert r.status_code == 201, r.text
                r = await client.post(
                    f"/api/projects/{pid}/commits",
                    json={
                        "delta_id": did,
                        "author_approval": {"approver": "user:local:test"},
                        "workflow_run_id": f"wfr_{initial_status.lower()}",
                    },
                )
                assert r.status_code == 201, r.text
                target_commit = r.json()["commit_id"]

                # 4) rollback
                r = await client.post(
                    f"/api/commits/{target_commit}/rollback",
                    json={
                        "author_approval": {
                            "approver": "user:local:test",
                            "approved": True,
                        }
                    },
                )
                assert r.status_code == 201, r.text

            # 5) 直接读 DB
            conn = sqlite3.connect(str(sub / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT status FROM chapters WHERE chapter_id = ?",
                    (chap,),
                ).fetchone()
                return row["status"]
            finally:
                conn.close()

    return asyncio.run(_go())


def test_rollback_does_not_touch_non_committed_chapter_status(tmp_path: Path):
    """新测 10：rollback 触发时，chapter 状态为非 COMMITTED（PLANNED/DRAFTED/
    REVIEWED）一律不动——只有 COMMITTED→DRAFTED 是单一联动语义。

    防 over-reach：避免误把 DRAFTED 改成 PLANNED、或把 REVIEWED 改成 DRAFTED。
    RELEASED 状态由状态机不可达（COMMITTED→RELEASED 在此测试栈无 setter），跳过。
    """
    # DRAFTED：rollback 后仍 DRAFTED（不被错降级）
    final_drafted = _setup_then_drive_and_rollback_status(tmp_path, "DRAFTED")
    assert final_drafted == "DRAFTED", (
        f"DRAFTED chapter should stay DRAFTED, got {final_drafted}"
    )

    # REVIEWED：rollback 后仍 REVIEWED（不被错降级）
    final_reviewed = _setup_then_drive_and_rollback_status(tmp_path, "REVIEWED")
    assert final_reviewed == "REVIEWED", (
        f"REVIEWED chapter should stay REVIEWED, got {final_reviewed}"
    )

    # PLANNED：rollback 后仍 PLANNED（基线，状态不动）
    final_planned = _setup_then_drive_and_rollback_status(tmp_path, "PLANNED")
    assert final_planned == "PLANNED", (
        f"PLANNED chapter should stay PLANNED, got {final_planned}"
    )
