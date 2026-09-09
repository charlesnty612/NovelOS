"""rollback 对 character-add 的实体级回收回归测试。

覆盖任务书指定的 4 个用例：
- C1：commit 含 character_changes op=add + facet=definition 字段 → rollback 后
  characters 表无此行、character_states 无该角色行、快照 state["characters"]
  无此 id，且角色计数回到 commit 前。
- C2：同一 delta 同时 add 角色 + add 涉及该角色的 relationship + new_events
  + new_hooks → rollback → 全部清除（characters/character_states/relationships
  表与快照两桶均无残留）。
- C3（回归）：commit 只对既有角色做 update → rollback → 角色存活、before 值
  恢复（既有行为不变）。
- C4：角色在 commit1 add、commit2 update → 只回滚 commit2 → 角色存活（实体
  删除只由「逆 add」触发，不能误杀历史角色）。

测试模式与 ``tests/integration/test_story_state_rollback_fixes.py`` 对齐：
ASGI 全链路（create_app + httpx ASGI + lifespan + commit + rollback 端点）。
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


async def _make_project(app, name: str = "rollback_char") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


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
        "created_at": "2026-08-30T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
    }


async def _setup(tmp_path: Path, *, project_name: str = "rollback_char", baseline_chars: int = 0):
    app = _create_app(tmp_path)
    pid = None
    chap = None
    baseline_ids: list[str] = []
    async with app.router.lifespan_context(app):
        pid = await _make_project(app, name=project_name)
        chap = await _make_chapter(app, pid)
        # 可选：在 init 前先建 baseline 角色，使快照 characters 非空
        for i in range(baseline_chars):
            cid = await _new_character_via_rest(
                app, pid, name=f"baseline_{i}",
            )
            baseline_ids.append(cid)
        r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
        assert r.status_code == 201, r.text
    return {"app": app, "pid": pid, "chap": chap, "baseline_ids": baseline_ids}


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


async def _commit_delta(app, pid: str, delta: dict, *, workflow_run_id: str | None = None):
    r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
    assert r.status_code == 201, r.text
    r = await _request(
        app, "POST", f"/api/projects/{pid}/commits",
        json={
            "delta_id": delta["delta_id"],
            # facet=definition 走 HIGH 风险路径，必须 approved=True；其他场景
            # approved=True 也无害（测试统一开口子）
            "author_approval": {"approver": "user:local:test", "approved": True},
            "workflow_run_id": workflow_run_id or f"wfr_{delta['delta_id']}",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _rollback_commit(app, commit_id: str):
    r = await _request(
        app, "POST", f"/api/commits/{commit_id}/rollback",
        json={"author_approval": {"approver": "user:local:test", "approved": True}},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _new_character_via_rest(app, pid: str, *, name: str, role: str = "supporting") -> str:
    """通过 REST POST /characters 直接创建角色行（不经过 commit）。"""
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": name, "role": role},
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


# ----------------------------------------------------------------- C1: rollback add → 实体消失


def test_rollback_character_add_deletes_character_row_and_states_and_snapshot(tmp_path: Path):
    """C1：commit 含 character_changes op=add（facet=definition 首次写入
    core_json）→ rollback → characters 表无此行、character_states 无该角色
    任何行、快照 state["characters"] 无此 id，角色计数回到 commit 前。"""
    setup = asyncio.run(_setup(tmp_path))
    pid = setup["pid"]
    chap = setup["chap"]
    app = setup["app"]

    async def run():
        async with app.router.lifespan_context(app):
            # commit 前基线角色计数
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            baseline_count = len(r.json().get("characters") or [])

            # 通过 REST 新建一个角色（characters 行 INSERT）
            cid = await _new_character_via_rest(app, pid, name="新登场角色")

            # commit：character_changes op=add，facet=definition，字段 core.personality
            d = {
                **_make_meta("dlt_char_add", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_char_add",
                        "op": "add",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "definition",
                        "field": "core.personality",
                        "before": None,
                        "after": "mysterious",
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
            commit = await _commit_delta(app, pid, d)

            # commit 后：角色应在 characters 表 + character_states 表 + 快照
            conn = _open_db(tmp_path / "novelos.db")
            try:
                n = conn.execute("SELECT COUNT(*) AS c FROM characters WHERE character_id = ?", (cid,)).fetchone()
                assert n["c"] == 1
                n = conn.execute("SELECT COUNT(*) AS c FROM character_states WHERE character_id = ?", (cid,)).fetchone()
                assert n["c"] >= 1
            finally:
                conn.close()
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            snap_ids_after = {c["character_id"] for c in (r.json().get("characters") or [])}
            assert cid in snap_ids_after

            # rollback
            await _rollback_commit(app, commit["commit_id"])

            # rollback 后：DB 域表 + 快照均无该角色
            conn = _open_db(tmp_path / "novelos.db")
            try:
                n = conn.execute("SELECT COUNT(*) AS c FROM characters WHERE character_id = ?", (cid,)).fetchone()
                assert n["c"] == 0, "characters 行未删"
                n = conn.execute("SELECT COUNT(*) AS c FROM character_states WHERE character_id = ?", (cid,)).fetchone()
                assert n["c"] == 0, "character_states 行未删"
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200
            snap = r.json()
            snap_ids = {c["character_id"] for c in (snap.get("characters") or [])}
            assert cid not in snap_ids, "快照 characters[] 残留"
            assert len(snap.get("characters") or []) == baseline_count, "角色计数未回到 commit 前"

    asyncio.run(run())


# ----------------------------------------------------------------- C2: rollback add + 涉及该角色的关系 + events/hooks 全清


def test_rollback_character_add_clears_relationships_events_hooks_no_residue(tmp_path: Path):
    """C2：同一 delta 同时 add 角色 + add 涉及该角色的 relationship +
    new_events + new_hooks → rollback → characters/character_states/
    relationships 表与快照两桶均无残留。"""
    setup = asyncio.run(_setup(tmp_path, baseline_chars=1))
    pid = setup["pid"]
    chap = setup["chap"]
    app = setup["app"]
    base_cid = setup["baseline_ids"][0]

    async def run():
        async with app.router.lifespan_context(app):
            # 基线角色（用做关系 from 端点）
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert any(c["character_id"] == base_cid for c in r.json()["characters"])

            cid_new = await _new_character_via_rest(app, pid, name="关系挂载角色")

            d = {
                **_make_meta("dlt_char_mix", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_char_mix",
                        "op": "add",
                        "target_id": cid_new,
                        "character_id": cid_new,
                        "facet": "definition",
                        "field": "core.personality",
                        "before": None,
                        "after": "shrewd",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "world_changes": [],
                "relationship_changes": [
                    {
                        "change_id": "rc_mix",
                        "op": "add",
                        "target_id": "rel_mix",
                        "from_character_id": base_cid,
                        "to_character_id": cid_new,
                        "relation_type": "ally",
                        "after": {"intensity": 0.6},
                        "visibility": "VISIBLE",
                        "who_knows": None,
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "new_events": [
                    {
                        "change_id": "ev_mix",
                        "op": "add",
                        "target_id": "event_mix",
                        "event_id": "event_mix",
                        "type": "encounter",
                        "participants": [cid_new],
                        "time": {"timeline_day": 1, "in_story_date": None},
                        "description": "遭遇新角色",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_mix",
                        "op": "add",
                        "target_id": "hook_mix",
                        "hook_id": "hook_mix",
                        "name": "新角色伏笔",
                        "importance": 0.7,
                        "description": "新角色登场的伏笔",
                        "confidence": 0.85,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "debt_changes": [],
            }
            commit = await _commit_delta(app, pid, d)

            # commit 后：上述元素都在
            conn = _open_db(tmp_path / "novelos.db")
            try:
                assert conn.execute("SELECT COUNT(*) AS c FROM characters WHERE character_id = ?", (cid_new,)).fetchone()["c"] == 1
                assert conn.execute("SELECT COUNT(*) AS c FROM relationships WHERE from_character_id = ? OR to_character_id = ?", (cid_new, cid_new)).fetchone()["c"] >= 1
                assert conn.execute("SELECT COUNT(*) AS c FROM plot_events WHERE event_id = ?", ("event_mix",)).fetchone()["c"] == 1
                assert conn.execute("SELECT COUNT(*) AS c FROM hooks WHERE hook_id = ?", ("hook_mix",)).fetchone()["c"] == 1
            finally:
                conn.close()

            # rollback
            await _rollback_commit(app, commit["commit_id"])

            # rollback 后：DB 域表无残留
            conn = _open_db(tmp_path / "novelos.db")
            try:
                assert conn.execute("SELECT COUNT(*) AS c FROM characters WHERE character_id = ?", (cid_new,)).fetchone()["c"] == 0, "characters 行残留"
                assert conn.execute("SELECT COUNT(*) AS c FROM character_states WHERE character_id = ?", (cid_new,)).fetchone()["c"] == 0, "character_states 行残留"
                # 关系：本提交 add 的（涉及 cid_new）必须清空；baseline 角色自身不应
                # 有任何残留关系指向已删角色
                assert conn.execute(
                    "SELECT COUNT(*) AS c FROM relationships WHERE from_character_id = ? OR to_character_id = ?",
                    (cid_new, cid_new),
                ).fetchone()["c"] == 0, "relationships 残留指向已删角色"
                assert conn.execute("SELECT COUNT(*) AS c FROM plot_events WHERE event_id = ?", ("event_mix",)).fetchone()["c"] == 0, "plot_events 残留"
                assert conn.execute("SELECT COUNT(*) AS c FROM hooks WHERE hook_id = ?", ("hook_mix",)).fetchone()["c"] == 0, "hooks 残留"
            finally:
                conn.close()

            # 快照两桶（characters[*].relationships 与 world.factions[*].relationships）
            # 均无残留
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            snap = r.json()
            snap_char_ids = {c["character_id"] for c in (snap.get("characters") or [])}
            assert cid_new not in snap_char_ids
            for c in snap.get("characters") or []:
                rels = c.get("relationships") or []
                for r_item in rels:
                    assert r_item.get("from_character_id") != cid_new
                    assert r_item.get("to_character_id") != cid_new
            # events / hooks 也不在快照
            events = snap.get("events") or {}
            assert "event_mix" not in (events if isinstance(events, dict) else {e.get("event_id") for e in events if isinstance(e, dict)})
            hooks_ids = {h.get("hook_id") for h in (snap.get("hooks") or []) if isinstance(h, dict)}
            assert "hook_mix" not in hooks_ids

    asyncio.run(run())


# ----------------------------------------------------------------- C3 (回归): rollback update → 角色存活、before 恢复


def test_rollback_character_update_keeps_character_and_restores_before(tmp_path: Path):
    """C3（回归）：commit 只对既有角色做 update → rollback → 角色存活、
    before 值恢复（既有行为不变）。"""
    setup = asyncio.run(_setup(tmp_path, baseline_chars=1))
    pid = setup["pid"]
    chap = setup["chap"]
    app = setup["app"]
    cid = setup["baseline_ids"][0]

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert any(c["character_id"] == cid for c in r.json()["characters"])

            # 第一次 update：state.location = "Forest"
            d1 = {
                **_make_meta("dlt_char_upd_1", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_upd_1",
                        "op": "update",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "state",
                        "field": "state.location",
                        "before": "Unknown",
                        "after": "Forest",
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
            await _commit_delta(app, pid, d1)

            # 第二次 update：state.location = "Cave"（逆回滚需恢复 Forest）
            d2 = {
                **_make_meta("dlt_char_upd_2", chap, 2),
                "character_changes": [
                    {
                        "change_id": "cc_upd_2",
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
            commit2 = await _commit_delta(app, pid, d2)

            # rollback commit2（update 路径，角色应存活，state.location 回到 Forest）
            await _rollback_commit(app, commit2["commit_id"])

            conn = _open_db(tmp_path / "novelos.db")
            try:
                n = conn.execute("SELECT COUNT(*) AS c FROM characters WHERE character_id = ?", (cid,)).fetchone()
                assert n["c"] == 1, "既有角色被误删"
                # character_states 取最新版本，应为 Forest（commit1 的 after）
                row = conn.execute(
                    "SELECT state_json FROM character_states WHERE character_id = ? ORDER BY state_version DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                sj = json.loads(row["state_json"]) if row else {}
                assert sj.get("location") == "Forest", f"state.location 未恢复 before={sj.get('location')!r}"
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            char = next(c for c in r.json()["characters"] if c["character_id"] == cid)
            assert "Cave" not in json.dumps(char)

    asyncio.run(run())


# ----------------------------------------------------------------- C4: 历史角色不被逆 update 误杀


def test_rollback_later_update_does_not_kill_preexisting_character(tmp_path: Path):
    """C4：角色在 commit1 add、commit2 update → 只回滚 commit2 → 角色存活
    （实体删除只由「逆 add」触发，不能误杀历史角色）。"""
    setup = asyncio.run(_setup(tmp_path))
    pid = setup["pid"]
    chap = setup["chap"]
    app = setup["app"]

    async def run():
        async with app.router.lifespan_context(app):
            cid = await _new_character_via_rest(app, pid, name="历史角色")

            # commit1：add（facet=definition）
            d_add = {
                **_make_meta("dlt_preex_add", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_preex_add",
                        "op": "add",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "definition",
                        "field": "core.personality",
                        "before": None,
                        "after": "stoic",
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
            commit1 = await _commit_delta(app, pid, d_add)

            # commit2：update（state.location）
            d_upd = {
                **_make_meta("dlt_preex_upd", chap, 2),
                "character_changes": [
                    {
                        "change_id": "cc_preex_upd",
                        "op": "update",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "state",
                        "field": "state.location",
                        "before": "Homeland",
                        "after": "City",
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
            commit2 = await _commit_delta(app, pid, d_upd)

            # 只回滚 commit2（update 路径，角色应存活，state.location 回到 Homeland）
            await _rollback_commit(app, commit2["commit_id"])

            conn = _open_db(tmp_path / "novelos.db")
            try:
                # 角色必须还在
                row = conn.execute("SELECT name FROM characters WHERE character_id = ?", (cid,)).fetchone()
                assert row is not None, "历史角色被逆 update 误杀"
                assert row["name"] == "历史角色"
                # character_states 也必须保留至少 1 行（v1 seed）
                n = conn.execute("SELECT COUNT(*) AS c FROM character_states WHERE character_id = ?", (cid,)).fetchone()
                assert n["c"] >= 1, "character_states 历史被误清"
                # 最新 state_version 应回到 Homeland（commit1 add 后状态）
                row = conn.execute(
                    "SELECT state_json FROM character_states WHERE character_id = ? ORDER BY state_version DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                sj = json.loads(row["state_json"]) if row else {}
                assert sj.get("location") == "Homeland", f"state.location 未恢复到 commit1 后值={sj.get('location')!r}"
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            snap_char_ids = {c["character_id"] for c in (r.json().get("characters") or [])}
            assert cid in snap_char_ids, "快照中历史角色被误删"

            # commit1 也必须仍可回滚（验证不变量：commit1 的逆 add 仍能删角色）
            # 顺序：commit1 的 rollback 会沿 tip→commit2 倒序，目前 commit2 已
            # rollback，所以 commit1 现在是 tip——直接回滚即可
            await _rollback_commit(app, commit1["commit_id"])
            conn = _open_db(tmp_path / "novelos.db")
            try:
                n = conn.execute("SELECT COUNT(*) AS c FROM characters WHERE character_id = ?", (cid,)).fetchone()
                assert n["c"] == 0, "commit1 rollback 未清掉历史角色"
            finally:
                conn.close()

    asyncio.run(run())


# ----------------------------------------------------------------- C5: 既有角色字段级 add → rollback 角色存活


def test_rollback_preexisting_character_field_add_keeps_character(tmp_path: Path):
    """C5：既有角色（commit 前已在快照中）+ 本 commit 对其 op=add
    facet=state 新字段 → rollback → **角色存活**、characters/
    character_states 行仍在、被 add 的字段被字段级 remove 正确移除。

    关键：与顾青生产事故同源 bug 的反向断言——char_08f503251717 的
    state.knowledge/state.belief/state.goal 等 7 条字段级 add，回滚后角色
    必须保留（既有角色不能被误删），新加字段由逆 delta 字段级 remove 恢复。
    """
    setup = asyncio.run(_setup(tmp_path, baseline_chars=1))
    pid = setup["pid"]
    chap = setup["chap"]
    app = setup["app"]
    cid = setup["baseline_ids"][0]

    async def run():
        async with app.router.lifespan_context(app):
            # 提交前快照已有 cid（baseline init 时就 INSERT 走 REST → snapshot 包含）
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            pre_snap_char_ids = {c["character_id"] for c in (r.json().get("characters") or [])}
            assert cid in pre_snap_char_ids, "提交前快照应已含 baseline 角色"

            # 本 commit：对既有角色 op=add facet=state field=knowledge after="..."（字段级）
            d = {
                **_make_meta("dlt_preex_field_add", chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_field_add",
                        "op": "add",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "state",
                        "field": "knowledge",
                        "before": None,
                        "after": "secret_revelation",
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
            commit = await _commit_delta(app, pid, d)

            # commit 后：knowledge 字段写入 character_states 最新 version
            conn = _open_db(tmp_path / "novelos.db")
            try:
                row = conn.execute(
                    "SELECT state_json FROM character_states WHERE character_id = ? "
                    "ORDER BY state_version DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                sj = json.loads(row["state_json"]) if row else {}
                assert sj.get("knowledge") == "secret_revelation", f"知识字段未写入={sj!r}"
            finally:
                conn.close()

            # rollback
            await _rollback_commit(app, commit["commit_id"])

            # 关键断言：角色必须存活（既有角色不能被字段级 add 误删）
            conn = _open_db(tmp_path / "novelos.db")
            try:
                row = conn.execute("SELECT name FROM characters WHERE character_id = ?", (cid,)).fetchone()
                assert row is not None, "既有角色被字段级 add 误删"
                assert row["name"] == "baseline_0"
                # character_states 行还在（baseline seed v1）
                n = conn.execute("SELECT COUNT(*) AS c FROM character_states WHERE character_id = ?", (cid,)).fetchone()
                assert n["c"] >= 1, "character_states 历史被误清"
                # knowledge 字段被字段级 remove 正确移除（v1 seed 不含）
                row = conn.execute(
                    "SELECT state_json FROM character_states WHERE character_id = ? "
                    "ORDER BY state_version DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                sj = json.loads(row["state_json"]) if row else {}
                assert "knowledge" not in sj, f"knowledge 字段未被字段级 remove={sj!r}"
            finally:
                conn.close()

            # 快照也确认角色仍在
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            snap_char_ids = {c["character_id"] for c in (r.json().get("characters") or [])}
            assert cid in snap_char_ids, "快照中既有角色被字段级 add 误删"

    asyncio.run(run())

