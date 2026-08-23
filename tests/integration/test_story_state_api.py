"""/api/story_state 集成测试（Sprint 2）。

完整链路（happy path + 负例）：
- 建 project → 建 character → 建 chapter → init genesis → GET state (v1)
- submit 合法 delta（含 1 条 character state update + 1 条 new_hook + 1 条 new_event）
- commit → GET state (v2)：
  - 断言 current_state.location == "Cave"
  - 断言 character_states 表出现 v2 行
  - 断言 hooks 表出现新行
  - 断言 plot_events 表出现新行
- rollback → GET state (v3，回到 v1 语义)
  - 断言 character state.location 回退为初值
  - 断言 hooks 不含新行
- 负例：
  - 乐观锁：用过期 previous_state_version 提交 → 409
  - HIGH 无审批：含 risk_level=HIGH 的 change 但 author_approval.approved 未给 → 409
  - schema 非法：缺 required → 422
  - 关库重开 GET state 仍是 v3（持久化）

测试模式参考 ``tests/integration/test_characters_api.py``：
httpx ASGI + tmp_path db；不引入 pytest-asyncio。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "集成项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_character(app, pid: str, name: str = "林夕") -> str:
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/characters",
        json={"name": name, "role": "protagonist"},
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/chapters",
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


# --------------------------------------------------------------------- happy path


def test_full_lifecycle_commit_and_rollback(tmp_path: Path):
    """主链路：project → character → chapter → init → submit → commit → rollback。"""
    app = _create_app(tmp_path)
    captured: dict[str, str] = {}

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            captured["pid"] = pid
            captured["cid"] = cid

            # init genesis（v1）
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201, r.text
            v1 = r.json()
            assert v1["state_version"] == 1

            # GET state v1
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            state_v1 = r.json()
            assert state_v1["state_version"] == 1
            char_v1 = next(c for c in state_v1["characters"] if c["character_id"] == cid)
            assert char_v1["current_state"] == {}  # 默认空

            # 提交 delta（包含 1 character update + 1 new_hook + 1 new_event）
            delta_id = "dlt_commit_aaaa"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_1",
                        "op": "update",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "state",
                        "field": "state.location",
                        "before": "Unknown",
                        "after": "Cave",
                        "confidence": 0.95,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
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

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "validated"

            # commit
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:local:test", "notes": None},
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text
            commit_v2 = r.json()
            assert commit_v2["state_version"] == 2

            # GET state v2
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            state_v2 = r.json()
            assert state_v2["state_version"] == 2
            char_v2 = next(c for c in state_v2["characters"] if c["character_id"] == cid)
            assert char_v2["current_state"]["location"] == "Cave"
            assert any(h["hook_id"] == "hook_mystery" for h in state_v2["hooks"])
            assert "event_first" in state_v2["recent_events"]
            assert state_v2["events"]["event_first"]["type"] == "encounter"

            # 直接查 DB：character_states 表出现 v2 行；hooks 表出现新行；plot_events 出现新行
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT state_version FROM character_states WHERE character_id = ? ORDER BY state_version",
                    (cid,),
                ).fetchall()
                versions = [r["state_version"] for r in rows]
                assert versions == [1, 2]
                hook_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM hooks WHERE hook_id = ?", ("hook_mystery",)
                ).fetchone()["c"]
                assert hook_count == 1
                event_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM plot_events WHERE event_id = ?", ("event_first",)
                ).fetchone()["c"]
                assert event_count == 1
                # commits 表应有 2 条
                commit_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM commits WHERE project_id = ?", (pid,)
                ).fetchone()["c"]
                assert commit_count == 2
            finally:
                conn.close()

            # rollback commit_v2
            r = await _request(
                app,
                "POST",
                f"/api/commits/{commit_v2['commit_id']}/rollback",
                json={"author_approval": {"approver": "user:local:test", "approved": True, "notes": "revert"}},
            )
            assert r.status_code == 201, r.text
            rollback_commit = r.json()
            assert rollback_commit["state_version"] == 3
            assert rollback_commit["rollback_of"] == commit_v2["commit_id"]

            # GET state v3（应回到 v1 语义：location 回退为 before 的原值；hooks 中无 hook_mystery）
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            state_v3 = r.json()
            assert state_v3["state_version"] == 3
            char_v3 = next(c for c in state_v3["characters"] if c["character_id"] == cid)
            # 逆 update 语义：location 被设回 before="Unknown"（P2-6 要求 update 必有 before）
            assert char_v3["current_state"].get("location") == "Unknown"
            assert not any(h["hook_id"] == "hook_mystery" for h in state_v3["hooks"])
            # recent_events 在快照层 mutate 移除
            assert "event_first" not in state_v3.get("recent_events", [])

    asyncio.run(run())

    # P2-7：真实「重启」断言——新建 create_app 实例（同 tmp_path 即同 db）后 GET state。
    # 旧实现仅 sqlite3.connect 直接读表不算重启（同一进程），改为新 ASGI app 上下文。
    app_restart = _create_app(tmp_path)
    pid_captured = captured["pid"]
    cid_captured = captured["cid"]

    async def restart_check():
        async with app_restart.router.lifespan_context(app_restart):
            r = await _request(app_restart, "GET", f"/api/projects/{pid_captured}/state")
            assert r.status_code == 200
            snap = r.json()
            assert snap["state_version"] == 3
            char_v3 = next(c for c in snap["characters"] if c["character_id"] == cid_captured)
            assert char_v3["current_state"].get("location") == "Unknown"
            assert not any(h["hook_id"] == "hook_mystery" for h in snap["hooks"])

    asyncio.run(restart_check())


# ----------------------------------------------------------------- optimistic lock


def test_optimistic_lock_conflict_returns_409(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)

            # init
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            # 第一条 delta：成功 commit 到 v2
            d1 = {
                **_make_meta("dlt_first", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_first",
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
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d1)
            assert r.status_code == 201
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_first",
                    "author_approval": {"approver": "user:local:test"},
                    "workflow_run_id": "wfr_first",
                },
            )
            assert r.status_code == 201, r.text

            # 第二条 delta：previous_state_version 用过期值 1（实际已是 2）
            d2 = {
                **_make_meta("dlt_second", chap, 1),  # 过期
                "character_changes": [
                    {
                        "change_id": "cc_second",
                        "op": "update",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "state",
                        "field": "state.location",
                        "before": "Cave",
                        "after": "Town",
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
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d2)
            assert r.status_code == 201

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_second",
                    "author_approval": {"approver": "user:local:test"},
                    "workflow_run_id": "wfr_second",
                },
            )
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert detail["error"] == "optimistic_lock"
            assert detail["actual_version"] == 2
            assert detail["expected_version"] == 1

    asyncio.run(run())


# ----------------------------------------------------------------- HIGH approval gate


def test_high_risk_without_approval_returns_409(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            # facet=definition 触发 HIGH（按 state-delta-v0.md §2.5.1 末段）
            delta = {
                **_make_meta("dlt_high", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_high",
                        "op": "update",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "definition",
                        "field": "core.personality",
                        "before": "brave",
                        "after": "cunning",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "HIGH",
                    }
                ],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201
            assert r.json()["status"] == "validated"

            # commit 但无 approved=True → 409
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_high",
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": "wfr_high",
                },
            )
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert detail["error"] == "approval_required"
            assert "cc_high" in detail["high_risk_change_ids"]

            # 加 approved=True → 通过
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_high",
                    "author_approval": {"approver": "user:local:test", "approved": True},
                    "workflow_run_id": "wfr_high",
                },
            )
            assert r.status_code == 201, r.text

    asyncio.run(run())


# ----------------------------------------------------------------- schema invalid


def test_invalid_schema_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            _cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)

            # 用 schema_version 非法触发 schema 错误（chapter_id 保留 → FK 不破坏）
            delta = {
                **_make_meta("dlt_bad", chap, 1),
            }
            delta["schema_version"] = "state-delta-v9"

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 422, r.text
            detail = r.json()["detail"]
            assert "errors" in detail
            assert any("schema_version" in e for e in detail["errors"])

            # 即便无效，状态表中也应留下 rejected 行（审计）
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT status FROM state_deltas WHERE delta_id = ?", ("dlt_bad",)
                ).fetchone()
                assert row is not None
                assert row["status"] == "rejected"
            finally:
                conn.close()

    asyncio.run(run())


def test_missing_chapter_id_returns_422_without_rejected_row(tmp_path: Path):
    """chapter_id 缺失触发 schema 错误：因 FK 无法落 rejected 行，仅返回错误。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            _cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)

            # 缺 chapter_id 顶层字段
            delta = {
                **_make_meta("dlt_no_chap", chap, 1),
            }
            delta.pop("chapter_id")

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 422, r.text
            detail = r.json()["detail"]
            assert "errors" in detail
            assert any("chapter_id" in e for e in detail["errors"])

            # chapter_id 缺失 → 因 FK 约束无法落 rejected 行
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            try:
                row = conn.execute(
                    "SELECT status FROM state_deltas WHERE delta_id = ?", ("dlt_no_chap",)
                ).fetchone()
                assert row is None
            finally:
                conn.close()

    asyncio.run(run())


# ----------------------------------------------------------------- 404 cases


def test_get_state_for_missing_project_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/projects/prj_nope/state")
            assert r.status_code == 404

            r = await _request(
                app, "POST", "/api/projects/prj_nope/state/init", json={"chapter_id": "ch_x"}
            )
            assert r.status_code == 404

    asyncio.run(run())


def test_commit_nonexistent_delta_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_nonexistent",
                    "author_approval": {"approved": True},
                    "workflow_run_id": "wfr_x",
                },
            )
            assert r.status_code == 404

    asyncio.run(run())


# ----------------------------------------------------------------- lists


def test_list_commits_and_deltas(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            _cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            # 列 commits（应有 1 条 genesis）
            r = await _request(app, "GET", f"/api/projects/{pid}/commits")
            assert r.status_code == 200
            cs = r.json()
            assert len(cs) == 1
            assert cs[0]["resulting_state_version"] == 1

            # 列 deltas（应有 1 条 applied genesis）
            r = await _request(app, "GET", f"/api/projects/{pid}/chapters/{chap}/deltas")
            assert r.status_code == 200
            ds = r.json()
            assert len(ds) == 1
            assert ds[0]["status"] == "applied"

    asyncio.run(run())
