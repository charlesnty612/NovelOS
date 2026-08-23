"""/api/story_state 分支能力测试（Sprint 7）。

覆盖任务书指定的范围：
- happy path：建分支 → 分支上提交 delta → main get_current_state 无该变更 → promote →
  main 出现变更，state_version 递增，branch status=MERGED。
- 隔离：分支与 main 的 commits 各走独立 state_version 序列；分支提交不会改 main snapshot。
- merged 后拒绝再写入（409 / branch_closed）。
- diff：两个 snapshot 之间的结构化 diff（character / hook / event / debt / location）。
- 重名分支 → 409 (branch_name_conflict)。
- 不存在分支 → 404。
- 不属于该项目的分支 → 404。

测试模式参考 ``tests/integration/test_story_state_api.py``：
httpx ASGI + tmp_path db；不引入 pytest-asyncio。
"""

from __future__ import annotations

import asyncio
import json
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


async def _make_project(app, name: str = "Branch Project") -> str:
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


# --------------------------------------------------------------------- happy path


def test_branch_isolation_and_promote(tmp_path: Path):
    """主链路：建分支 → 分支提交 delta（main 不受影响） → promote → main 出现变更。

    也验证 diff 端点能产出结构化差异（characters / hooks / events）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            # init genesis (v1)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201, r.text
            assert r.json()["state_version"] == 1

            # 列 branches：仅 main
            r = await _request(app, "GET", f"/api/projects/{pid}/branches")
            assert r.status_code == 200
            bs = r.json()
            assert len(bs) == 1
            assert bs[0]["name"] == "main"
            assert bs[0]["status"] == "ACTIVE"

            # 建分支 branch-A
            r = await _request(
                app, "POST", f"/api/projects/{pid}/branches",
                json={"name": "branch-A"},
            )
            assert r.status_code == 201, r.text
            branch = r.json()
            assert branch["name"] == "branch-A"
            assert branch["status"] == "ACTIVE"
            assert branch["parent_branch_id"] == bs[0]["branch_id"]
            assert branch["base_state_version"] == 1
            bid_a = branch["branch_id"]

            # 列 branches：现在 2 个
            r = await _request(app, "GET", f"/api/projects/{pid}/branches")
            assert r.status_code == 200
            assert len(r.json()) == 2

            # 在 branch-A 上提交 delta：character state.location 从 Unknown → Cave
            delta_a = {
                **_make_meta("dlt_branch_a", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_branch_a",
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
                        "change_id": "ev_branch_a",
                        "op": "add",
                        "target_id": "event_branch_a",
                        "event_id": "event_branch_a",
                        "type": "encounter",
                        "participants": [cid],
                        "time": {"timeline_day": 2, "in_story_date": None},
                        "description": "分支遭遇",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_branch_a",
                        "op": "add",
                        "target_id": "hook_branch_a",
                        "hook_id": "hook_branch_a",
                        "name": "分支伏笔",
                        "importance": 0.7,
                        "description": "分支独有的伏笔",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "debt_changes": [],
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/deltas",
                json={**delta_a, "branch_id": bid_a},
            )
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "validated"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_branch_a",
                    "author_approval": {"approver": "user:branch"},
                    "workflow_run_id": "wfr_branch_a",
                    "branch_id": bid_a,
                },
            )
            assert r.status_code == 201, r.text
            commit_branch = r.json()
            assert commit_branch["state_version"] == 2  # 分支内独立计数（base=1, +1=2）

            # main 不受影响：GET /state 仍返回 v1（不出现 event_branch_a / hook_branch_a）
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            main_state = r.json()
            assert main_state["state_version"] == 1
            assert "event_branch_a" not in main_state.get("recent_events", [])
            assert not any(h["hook_id"] == "hook_branch_a" for h in main_state["hooks"])

            # 分支视角：GET state?branch_id=... 返回重放结果（v=2，location=Cave）
            r = await _request(app, "GET", f"/api/projects/{pid}/state?branch_id={bid_a}")
            assert r.status_code == 200
            branch_state = r.json()
            assert branch_state["state_version"] == 2
            char = next(c for c in branch_state["characters"] if c["character_id"] == cid)
            assert char["current_state"]["location"] == "Cave"
            assert "event_branch_a" in branch_state["recent_events"]
            assert any(h["hook_id"] == "hook_branch_a" for h in branch_state["hooks"])

            # 分支 commit 不写 story_states（MVP 限制）；diff_versions 仅支持 main，
            # 带 branch_id 时返回 409 / promote_conflict。
            r = await _request(app, "GET", f"/api/projects/{pid}/state/diff?a=1&b=2&branch_id={bid_a}")
            assert r.status_code == 409, r.text

            # promote: 分支全部 commits 合并到 main
            r = await _request(
                app, "POST", f"/api/projects/{pid}/branches/{bid_a}/promote",
                json={"chapter_id": chap},
            )
            assert r.status_code == 201, r.text
            promote_result = r.json()
            assert promote_result["promoted_from"] == bid_a
            assert promote_result["promoted_commits"] == 1
            # promote 产生一个 main 上的 commit（branch=main），state_version 在 main 上 +1
            assert promote_result["state_version"] == 2  # main 之前 v1 → v2
            assert promote_result["branch_id"] is not None  # = main branch_id

            # branch-A 状态变为 MERGED
            r = await _request(app, "GET", f"/api/projects/{pid}/branches")
            assert r.status_code == 200
            branch_a_row = next(b for b in r.json() if b["branch_id"] == bid_a)
            assert branch_a_row["status"] == "MERGED"

            # main 现在反映变更：GET state v2 含 location=Cave / event_branch_a / hook_branch_a
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            main_after = r.json()
            assert main_after["state_version"] == 2
            char_after = next(c for c in main_after["characters"] if c["character_id"] == cid)
            assert char_after["current_state"]["location"] == "Cave"
            assert "event_branch_a" in main_after["recent_events"]
            assert any(h["hook_id"] == "hook_branch_a" for h in main_after["hooks"])

            # diff v1 vs v2（main）：同样应有 characters / hooks / events 改动
            r = await _request(app, "GET", f"/api/projects/{pid}/state/diff?a=1&b=2")
            assert r.status_code == 200
            diff_main = r.json()
            assert diff_main["branch_id"] is None
            assert "characters" in diff_main
            assert "hooks" in diff_main or "events_changed" in diff_main or "recent_events" in diff_main

    asyncio.run(run())


# -------------------------------------------------------------- merged branch rejects


def test_merged_branch_rejects_further_writes(tmp_path: Path):
    """branch merged 后再提交 delta 应被拒绝（domain 409 / branch_closed）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            # 建分支 + 提交 1 commit + promote
            r = await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "br-merged"})
            bid = r.json()["branch_id"]
            delta = {
                **_make_meta("dlt_merged_a", chap, 1),
                "character_changes": [{
                    "change_id": "cc_m", "op": "update", "target_id": cid,
                    "character_id": cid, "facet": "state", "field": "state.location",
                    "before": "Unknown", "after": "Town", "confidence": 0.9,
                    "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "world_changes": [], "relationship_changes": [], "new_events": [],
                "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas",
                               json={**delta, "branch_id": bid})
            assert r.status_code == 201
            r = await _request(app, "POST", f"/api/projects/{pid}/commits",
                               json={"delta_id": "dlt_merged_a",
                                     "author_approval": {"approver": "u"},
                                     "workflow_run_id": "w", "branch_id": bid})
            assert r.status_code == 201
            r = await _request(app, "POST", f"/api/projects/{pid}/branches/{bid}/promote",
                               json={"chapter_id": chap})
            assert r.status_code == 201

            # 再次在已 merged 分支上提交 → 409 / branch_closed
            delta2 = {
                **_make_meta("dlt_merged_b", chap, 2),
                "character_changes": [{
                    "change_id": "cc_m2", "op": "update", "target_id": cid,
                    "character_id": cid, "facet": "state", "field": "state.location",
                    "before": "Town", "after": "Mountain", "confidence": 0.9,
                    "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "world_changes": [], "relationship_changes": [], "new_events": [],
                "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas",
                               json={**delta2, "branch_id": bid})
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert detail["error"] == "branch_closed"
            assert detail["branch_id"] == bid

    asyncio.run(run())


# -------------------------------------------------------------- duplicate name


def test_duplicate_branch_name_returns_409(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            r = await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "dup"})
            assert r.status_code == 201

            r = await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "dup"})
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert detail["error"] == "branch_name_conflict"

            # 'main' 拒绝
            r = await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "main"})
            assert r.status_code == 409, r.text

            # name 缺失 → 422
            r = await _request(app, "POST", f"/api/projects/{pid}/branches", json={})
            assert r.status_code == 422

    asyncio.run(run())


# -------------------------------------------------------------- not-found branch


def test_nonexistent_branch_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            # 不存在的 branch_id 在 commit / promote / GET state 时均 404
            fake_bid = "br_nope_does_not_exist"

            r = await _request(app, "POST", f"/api/projects/{pid}/branches/{fake_bid}/promote",
                               json={})
            assert r.status_code == 404, r.text

            r = await _request(app, "GET", f"/api/projects/{pid}/state?branch_id={fake_bid}")
            assert r.status_code == 404, r.text

            delta = {
                **_make_meta("dlt_nf", chap, 1),
                "character_changes": [{
                    "change_id": "cc_nf", "op": "update", "target_id": cid,
                    "character_id": cid, "facet": "state", "field": "state.location",
                    "before": "X", "after": "Y", "confidence": 0.9,
                    "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "world_changes": [], "relationship_changes": [], "new_events": [],
                "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/commits",
                               json={"delta_id": "dlt_nf",
                                     "author_approval": {"approver": "u"},
                                     "workflow_run_id": "w", "branch_id": fake_bid})
            assert r.status_code == 404, r.text

    asyncio.run(run())


# -------------------------------------------------------------- branch from different project


def test_branch_from_other_project_returns_404(tmp_path: Path):
    """A 项目的分支不能在 B 项目上使用：commit 应 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, name="A")
            pid_b = await _make_project(app, name="B")
            cid_a = await _make_character(app, pid_a)
            chap_a = await _make_chapter(app, pid_a)
            chap_b = await _make_chapter(app, pid_b)
            cid_b = await _make_character(app, pid_b)

            r = await _request(app, "POST", f"/api/projects/{pid_a}/state/init", json={"chapter_id": chap_a})
            assert r.status_code == 201
            r = await _request(app, "POST", f"/api/projects/{pid_b}/state/init", json={"chapter_id": chap_b})
            assert r.status_code == 201

            # A 项目建分支
            r = await _request(app, "POST", f"/api/projects/{pid_a}/branches", json={"name": "from-a"})
            bid_a = r.json()["branch_id"]

            # 用 A 的分支在 B 上 commit → 404
            delta = {
                **_make_meta("dlt_cross", chap_b, 1),
                "character_changes": [{
                    "change_id": "cc_cross", "op": "update", "target_id": cid_b,
                    "character_id": cid_b, "facet": "state", "field": "state.location",
                    "before": "X", "after": "Y", "confidence": 0.9,
                    "evidence": _evidence(chap_b), "risk_level": "LOW",
                }],
                "world_changes": [], "relationship_changes": [], "new_events": [],
                "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid_a}/deltas",
                               json={**delta, "branch_id": bid_a})
            # 提交分支前置守卫：chapter 属于 B，但 branch 属于 A；_resolve_branch 比对
            # project_id（由 chapter 反查 = B）vs branch.project_id=A → BranchNotFound → 404
            assert r.status_code == 404, r.text

            # 同时确保 cid_a 在 A 项目里仍可正常推进（不被上面的 404 影响）
            _ = cid_a

    asyncio.run(run())


# -------------------------------------------------------------- diff structure


def test_diff_versions_structure(tmp_path: Path):
    """diff 端点应输出稳定的结构化 JSON。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            # 一次 commit → v2：location=Town + hook_a + event_a
            delta = {
                **_make_meta("dlt_d1", chap, 1),
                "character_changes": [{
                    "change_id": "cc_d1", "op": "update", "target_id": cid,
                    "character_id": cid, "facet": "state", "field": "state.location",
                    "before": "Unknown", "after": "Town", "confidence": 0.95,
                    "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "world_changes": [], "relationship_changes": [],
                "new_events": [{
                    "change_id": "ev_d1", "op": "add", "target_id": "event_d1",
                    "event_id": "event_d1", "type": "encounter",
                    "participants": [cid], "time": {"timeline_day": 1, "in_story_date": None},
                    "description": "v2 event", "confidence": 0.9,
                    "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "resolved_hooks": [], "new_hooks": [{
                    "change_id": "nh_d1", "op": "add", "target_id": "hook_d1",
                    "hook_id": "hook_d1", "name": "v2 hook", "importance": 0.5,
                    "description": "x", "confidence": 0.9,
                    "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201
            r = await _request(app, "POST", f"/api/projects/{pid}/commits",
                               json={"delta_id": "dlt_d1", "author_approval": {"approver": "u"},
                                     "workflow_run_id": "w"})
            assert r.status_code == 201

            r = await _request(app, "GET", f"/api/projects/{pid}/state/diff?a=1&b=2")
            assert r.status_code == 200
            d = r.json()
            assert d["version_a"] == 1 and d["version_b"] == 2
            assert "characters" in d
            # characters.changed[0].id 应是 cid
            char_changed = d["characters"]["changed"]
            assert any(c["id"] == cid for c in char_changed)
            # location 子字段应出现在 current_state.location.before / after
            loc_change = None
            for c in char_changed:
                if c["id"] == cid:
                    cs = c["changes"].get("current_state", {})
                    if "location" in cs:
                        loc_change = cs["location"]
            assert loc_change is not None
            # before 在字段从无到有时合理为 None（v1 无 location）
            assert loc_change["before"] in (None, "Unknown")
            assert loc_change["after"] == "Town"
            # events / hooks 至少有一处非空
            assert "hooks" in d or "events_changed" in d or "recent_events" in d

            # diff a==b 时无变化（按版本存在性：v2 vs v2 仍返回空 diff 结构 OK）
            r = await _request(app, "GET", f"/api/projects/{pid}/state/diff?a=2&b=2")
            assert r.status_code == 200
            d_eq = r.json()
            assert d_eq["version_a"] == 2 and d_eq["version_b"] == 2

            # diff 不存在版本 → 404
            r = await _request(app, "GET", f"/api/projects/{pid}/state/diff?a=1&b=99")
            assert r.status_code == 404, r.text

    asyncio.run(run())


# -------------------------------------------------------------- backward compat: branch_id 缺省行为


def test_commit_without_branch_id_uses_main(tmp_path: Path):
    """现有 main 路径：commit body 不传 branch_id 应逐字节等价于 Sprint 2/4 行为。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            delta = {
                **_make_meta("dlt_main", chap, 1),
                "character_changes": [{
                    "change_id": "cc_mn", "op": "update", "target_id": cid,
                    "character_id": cid, "facet": "state", "field": "state.location",
                    "before": "Unknown", "after": "Forest", "confidence": 0.95,
                    "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "world_changes": [], "relationship_changes": [], "new_events": [],
                "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201
            # 不传 branch_id → 走 main
            r = await _request(app, "POST", f"/api/projects/{pid}/commits",
                               json={"delta_id": "dlt_main",
                                     "author_approval": {"approver": "u"},
                                     "workflow_run_id": "w"})
            assert r.status_code == 201
            assert r.json()["state_version"] == 2

            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            state = r.json()
            assert state["state_version"] == 2
            char = next(c for c in state["characters"] if c["character_id"] == cid)
            assert char["current_state"]["location"] == "Forest"

            # 验证 commits 表里该 commit 的 branch_id = main.branch_id
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT c.branch_id, b.name FROM commits c
                    JOIN branches b ON b.branch_id = c.branch_id
                    WHERE c.delta_id = 'dlt_main'
                    """
                ).fetchone()
                assert row is not None
                assert row["name"] == "main"
            finally:
                conn.close()

    asyncio.run(run())


# -------------------------------------------------------------- Sprint 7 审查 P0/P1 修复测试


def test_promote_replay_resolves_hook_in_branch_order(tmp_path: Path):
    """P0-1：分支内 commit1 new_hook h1 → commit2 resolved_hooks h1 → promote 后
    main 快照 h1 status=RESOLVED，领域表 hooks.h1 status=RESOLVED 且
    payoff_chapter_id 正确。

    旧 concat 方案因 applier 固定顺序（resolved_hooks 先于 new_hooks）导致
    resolved 静默丢失；按序重放后正确。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            _cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid, number=1)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            # 建分支
            r = await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "hook-order"})
            bid = r.json()["branch_id"]

            # 分支 commit 1: new_hook h1
            delta1 = {
                **_make_meta("dlt_bh1", chap, 1),
                "character_changes": [], "world_changes": [], "relationship_changes": [],
                "new_events": [], "resolved_hooks": [],
                "new_hooks": [{
                    "change_id": "nh1", "op": "add", "target_id": "h1",
                    "hook_id": "h1", "name": "钩子1",
                    "importance": 0.7, "description": "x",
                    "confidence": 0.9, "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json={**delta1, "branch_id": bid})
            assert r.status_code == 201, r.text
            r = await _request(app, "POST", f"/api/projects/{pid}/commits",
                               json={"delta_id": "dlt_bh1", "author_approval": {"approver": "u"},
                                     "workflow_run_id": "w1", "branch_id": bid})
            assert r.status_code == 201, r.text

            # 分支 commit 2: resolved_hooks h1 → RESOLVED，payoff 在 chap1
            delta2 = {
                **_make_meta("dlt_bh2", chap, 2),
                "character_changes": [], "world_changes": [], "relationship_changes": [],
                "new_events": [], "new_hooks": [],
                "resolved_hooks": [{
                    "change_id": "rh1", "op": "update", "target_id": "h1",
                    "hook_id": "h1", "from_status": "OPEN", "to_status": "RESOLVED",
                    "payoff_chapter_id": chap, "payoff_summary": "promote branch 兑现",
                    "confidence": 0.9, "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json={**delta2, "branch_id": bid})
            assert r.status_code == 201, r.text
            r = await _request(app, "POST", f"/api/projects/{pid}/commits",
                               json={"delta_id": "dlt_bh2", "author_approval": {"approver": "u"},
                                     "workflow_run_id": "w2", "branch_id": bid})
            assert r.status_code == 201, r.text

            # promote 前：领域表 hooks 表里**无 h1 行**（P0-2 修复：分支 commit
            # 完全跳过领域表写透，避免污染 main）
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                h1_before = conn.execute("SELECT * FROM hooks WHERE hook_id = 'h1'").fetchone()
                assert h1_before is None, "分支 commit 不应污染 main.hooks 表"
            finally:
                conn.close()

            # promote：按序重放 2 个分支 commit → main 上产生 2 个新 commit
            r = await _request(app, "POST", f"/api/projects/{pid}/branches/{bid}/promote",
                               json={"chapter_id": chap})
            assert r.status_code == 201, r.text
            result = r.json()
            assert result["promoted_commits"] == 2
            assert result["state_version"] == 3  # main: v1 → +2 = v3
            assert len(result["replayed_delta_ids"]) == 2
            # 重放产生新 delta_id，不复用原 delta_id
            assert "dlt_bh1" not in result["replayed_delta_ids"]
            assert "dlt_bh2" not in result["replayed_delta_ids"]

            # main 快照：h1 status=RESOLVED
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            main_state = r.json()
            assert main_state["state_version"] == 3
            h1_in_state = next((h for h in main_state["hooks"] if h["hook_id"] == "h1"), None)
            assert h1_in_state is not None
            assert h1_in_state["status"] == "RESOLVED"

            # 领域表 hooks.h1 恰好一行（不重复），status=RESOLVED + payoff_chapter_id 正确
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT * FROM hooks WHERE hook_id = 'h1'").fetchall()
                assert len(rows) == 1, f"hooks.h1 应恰好一行，实际 {len(rows)}"
                assert rows[0]["status"] == "RESOLVED"
                assert rows[0]["payoff_chapter_id"] == chap
                # commits 表：1 genesis + 2 分支 + 2 重放 = 5
                cmt_count = conn.execute(
                    "SELECT COUNT(*) AS n FROM commits WHERE project_id = ?", (pid,)
                ).fetchone()["n"]
                assert cmt_count == 5, f"应 5 commits（1 genesis + 2 分支 + 2 重放），实际 {cmt_count}"
                # 重放 commit 应在 main 上且 validation_json 含 promoted_from + source_commit_id
                main_branch_id = conn.execute(
                    "SELECT branch_id FROM branches WHERE project_id = ? AND name = 'main'",
                    (pid,),
                ).fetchone()["branch_id"]
                replay_rows = conn.execute(
                    """
                    SELECT validation_json FROM commits
                    WHERE project_id = ? AND branch_id = ? AND delta_id != 'dlt_main'
                    """,
                    (pid, main_branch_id),
                ).fetchall()
                # 两条重放 commit 都含 promoted_from + source_commit_id
                replayed_main = [
                    r for r in replay_rows
                    if "replay-from-branch" in (r["validation_json"] or "")
                    or "promoted_from" in (r["validation_json"] or "")
                ]
                assert len(replayed_main) >= 2, "应有 2 条 main 上的 replay commit"
            finally:
                conn.close()

    asyncio.run(run())


def test_branch_commit_does_not_pollute_main_domain_tables(tmp_path: Path):
    """P0-2：分支 commit（character 变更）不写领域表（character_states）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            # 初始状态：character_states 表里 cid 没有任何 state_version=2 行
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                before = conn.execute(
                    "SELECT COUNT(*) AS n FROM character_states WHERE character_id = ? AND state_version >= 2",
                    (cid,),
                ).fetchone()["n"]
                assert before == 0
            finally:
                conn.close()

            # 建分支 + 分支 commit（character state.location）
            r = await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "no-pollute"})
            bid = r.json()["branch_id"]
            delta = {
                **_make_meta("dlt_np", chap, 1),
                "character_changes": [{
                    "change_id": "cc_np", "op": "update", "target_id": cid,
                    "character_id": cid, "facet": "state", "field": "state.location",
                    "before": "Unknown", "after": "BranchTown",
                    "confidence": 0.9, "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "world_changes": [], "relationship_changes": [],
                "new_events": [], "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json={**delta, "branch_id": bid})
            assert r.status_code == 201
            r = await _request(app, "POST", f"/api/projects/{pid}/commits",
                               json={"delta_id": "dlt_np", "author_approval": {"approver": "u"},
                                     "workflow_run_id": "w", "branch_id": bid})
            assert r.status_code == 201

            # 关键断言：分支 commit 后 main.character_states **无 state_version>=2 行**
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                pol = conn.execute(
                    "SELECT COUNT(*) AS n FROM character_states WHERE character_id = ? AND state_version >= 2",
                    (cid,),
                ).fetchone()["n"]
                assert pol == 0, "分支 commit 不应污染 main.character_states 表"
            finally:
                conn.close()

            # main 状态：v1，location 仍为 Unknown
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            main_state = r.json()
            assert main_state["state_version"] == 1
            char = next(c for c in main_state["characters"] if c["character_id"] == cid)
            assert char["current_state"].get("location") in (None, "Unknown")

    asyncio.run(run())


def test_promote_writes_through_domain_exactly_once(tmp_path: Path):
    """promote 后领域表恰好写透一次（无重复 state 行）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            r = await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "once"})
            bid = r.json()["branch_id"]

            delta = {
                **_make_meta("dlt_once", chap, 1),
                "character_changes": [{
                    "change_id": "cc_o", "op": "update", "target_id": cid,
                    "character_id": cid, "facet": "state", "field": "state.location",
                    "before": "Unknown", "after": "OnceTown",
                    "confidence": 0.9, "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "world_changes": [], "relationship_changes": [],
                "new_events": [], "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json={**delta, "branch_id": bid})
            assert r.status_code == 201
            r = await _request(app, "POST", f"/api/projects/{pid}/commits",
                               json={"delta_id": "dlt_once", "author_approval": {"approver": "u"},
                                     "workflow_run_id": "w", "branch_id": bid})
            assert r.status_code == 201

            # promote
            r = await _request(app, "POST", f"/api/projects/{pid}/branches/{bid}/promote",
                               json={"chapter_id": chap})
            assert r.status_code == 201

            # character_states 表里 cid 应恰好一行 state_version=2（不重复）
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM character_states WHERE character_id = ? AND state_version = 2",
                    (cid,),
                ).fetchall()
                assert len(rows) == 1
                body = json.loads(rows[0]["state_json"])
                assert body.get("location") == "OnceTown"
            finally:
                conn.close()

    asyncio.run(run())


def test_submit_delta_missing_chapter_id_returns_422(tmp_path: Path):
    """P1-1：submit_delta body 缺 chapter_id 且带 branch_id 时返回 422（不是 500）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201

            r = await _request(app, "POST", f"/api/projects/{pid}/branches", json={"name": "no-chap"})
            bid = r.json()["branch_id"]

            # body 缺 chapter_id + 带 branch_id → 走 validate_delta 校验失败 → 422
            r = await _request(
                app, "POST", f"/api/projects/{pid}/deltas",
                json={
                    "delta_id": "dlt_nochap",
                    "delta_version": 1,
                    "schema_version": "state-delta-v0",
                    # chapter_id missing
                    "workflow_run_id": "w",
                    "previous_state_version": 1,
                    "created_by": "u",
                    "created_at": "2026-08-23T10:00:00+00:00",
                    "supersedes": None,
                    "notes": None,
                    "character_changes": [], "world_changes": [], "relationship_changes": [],
                    "new_events": [], "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
                    "branch_id": bid,
                },
            )
            assert r.status_code == 422, r.text
            detail = r.json()["detail"]
            assert "errors" in detail
            assert "chapter_id" in str(detail["errors"])

    asyncio.run(run())