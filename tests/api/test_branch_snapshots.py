"""分支快照物化测试（V2.0 Wave B 任务一）。

覆盖任务书指定的 4 条物化用例（与本任务书 §实施方案 1.4 对应）：
1. **分支创建后物化行存在**——``branch_snapshots`` 表该分支有一行
   （state_version == branches.base_state_version）。
2. **分支上 3 个 commit 后读取结果与全量重放逐字节一致**——``branch_snapshots``
   增量重放路径与「基线 + 全量 delta 重放」产出的 snapshot dict 逐字节相同
   （序列化 + 反序列化后比较）。
3. **promote 后主线状态一致**——promote 完成后 main 分支在 ``branch_snapshots``
   中物化到新 latest version；下次 GET /state?branch_id=main_branch_id
   走「最近物化 + 0 增量」快路径，结果与最新 ``story_states`` 一致。
4. **大分支（构造 ≥5 delta）读取只重放增量部分**——``branch_snapshots``
   物化后，分支有 N 个 commit（大于物化点之后应有 M 个），读取时
   ``get_current_state`` 只走 M 个 delta 的增量重放；
   验证手段：直接调用 ``branch_current_state`` 比较实际重放次数
   （通过 SQLite 的 ``apply_delta`` 调用次数计数），或者对比 rowcount。

测试模式参考 ``tests/api/test_branches.py``：httpx ASGI + tmp_path db。

设计要点：
- 用 sqlite3 直连读 ``branch_snapshots`` 表 + ``state_deltas`` 表断言物化行；
- 「逐字节一致」用 ``json.loads + json.dumps(sort_keys=True)`` 比较；
- 「增量重放」断言：构造 5+ commit，然后构造第 1 commit 后的中间物化（直接 SQL），
  再读分支状态——验证 ``branch_current_state`` 走增量路径（与全量重放结果一致
  但 SQL 查询数/apply 次数下降；本测试以「结果逐字节一致」为主，「增量」
  通过对比 ``commits WHERE resulting_state_version > materialized_version``
  与实际重放 SQL 间接验证）。
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


async def _make_project(app, name: str = "mat_proj") -> str:
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
        "created_at": "2026-08-24T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
    }


def _sorted_json(obj) -> str:
    """递归序列化用于「逐字节一致」断言。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# 1. 分支创建后物化行存在
# ============================================================================


def test_create_branch_materializes_initial_snapshot(tmp_path: Path):
    """创建分支 → ``branch_snapshots`` 表立刻有一行
    （state_version == branches.base_state_version），内容等于
    base_state_version 处的 main 快照（或 build_initial_state 兜底）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201, r.text
            base_version = r.json()["state_version"]  # 1

            # 建分支 → 触发物化
            r = await _request(
                app, "POST", f"/api/projects/{pid}/branches",
                json={"name": "mat-A"},
            )
            assert r.status_code == 201, r.text
            bid = r.json()["branch_id"]
            assert r.json()["base_state_version"] == base_version

            # 直查 branch_snapshots 表
            conn = _conn(tmp_path / "novelos.db")
            try:
                row = conn.execute(
                    "SELECT state_version, snapshot_json FROM branch_snapshots "
                    "WHERE branch_id = ?",
                    (bid,),
                ).fetchone()
                assert row is not None, "branch_snapshots 行未落盘"
                assert int(row["state_version"]) == base_version, (
                    f"state_version={row['state_version']} != base_version={base_version}"
                )
                snap = json.loads(row["snapshot_json"])
                # 内容应与 base_version 处的 main story_states 快照一致
                # （base_version=1 时 genesis v1 物化内容，含 cid 角色）
                assert snap["state_version"] == base_version
                assert any(c["character_id"] == cid for c in snap.get("characters", []))
            finally:
                conn.close()

    asyncio.run(run())


# ============================================================================
# 2. 分支上 3 个 commit 后读取结果与全量重放逐字节一致
# ============================================================================


def test_branch_with_3_commits_matches_full_replay_bytewise(tmp_path: Path):
    """分支上 3 个 commit → 每次 commit 后 branch_current_state 走「物化+增量」
    读路径与「全量重放」结果**逐字节一致**（序列化后 sort_keys=True 比对）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201, r.text
            base_version = r.json()["state_version"]

            r = await _request(
                app, "POST", f"/api/projects/{pid}/branches",
                json={"name": "mat-3c"},
            )
            bid = r.json()["branch_id"]

            # 3 次 commit，每次改 character location / emotion
            locations = ["Cave", "Town", "Mountain"]
            for i, loc in enumerate(locations):
                prev_v = base_version + i  # 分支内独立计数：base=1, +1=2, +2=3, +3=4
                delta_id = f"dlt_mat3c_{i}"
                delta = {
                    **_make_meta(delta_id, chap, prev_v),
                    "character_changes": [{
                        "change_id": f"cc_{i}", "op": "update", "target_id": cid,
                        "character_id": cid, "facet": "state",
                        "field": "state.location", "before": locations[i - 1] if i else "Unknown",
                        "after": loc, "confidence": 0.95,
                        "evidence": _evidence(chap), "risk_level": "LOW",
                    }],
                    "world_changes": [], "relationship_changes": [], "new_events": [],
                    "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
                }
                r = await _request(
                    app, "POST", f"/api/projects/{pid}/deltas",
                    json={**delta, "branch_id": bid},
                )
                assert r.status_code == 201, r.text
                assert r.json()["status"] == "validated"
                r = await _request(
                    app, "POST", f"/api/projects/{pid}/commits",
                    json={
                        "delta_id": delta_id,
                        "author_approval": {"approver": "user:mat"},
                        "workflow_run_id": f"wfr_mat3c_{i}",
                        "branch_id": bid,
                    },
                )
                assert r.status_code == 201, r.text

            # 此时：branch_snapshots 行 state_version=base_version（创建时落）
            # 但分支内部 commits.resulting_state_version 已到 base+3；
            # 读路径走「物化 + (base+1..base+3) 增量重放」。
            r = await _request(app, "GET", f"/api/projects/{pid}/state?branch_id={bid}")
            assert r.status_code == 200, r.text
            branch_state_via_incremental = r.json()

            # 模拟「全量重放」= 手工：从 base_version 的 story_states 取 base，
            # 重放本分支全部 commits（删 branch_snapshots 行后调用）。
            conn = _conn(tmp_path / "novelos.db")
            try:
                conn.execute("DELETE FROM branch_snapshots WHERE branch_id = ?", (bid,))
                conn.commit()
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/projects/{pid}/state?branch_id={bid}")
            assert r.status_code == 200, r.text
            branch_state_via_full_replay = r.json()

            # 逐字节一致
            assert _sorted_json(branch_state_via_incremental) == _sorted_json(branch_state_via_full_replay), (
                "增量重放结果与全量重放不一致"
            )
            # 兜底断言：state_version 正确 + 最终 location 正确
            assert branch_state_via_incremental["state_version"] == base_version + 3
            char = next(c for c in branch_state_via_incremental["characters"] if c["character_id"] == cid)
            assert char["current_state"]["location"] == "Mountain"

    asyncio.run(run())


# ============================================================================
# 3. promote 后主线状态一致
# ============================================================================


def test_promote_materializes_main_snapshot_and_state_consistent(tmp_path: Path):
    """promote 完成后：
    - main 分支在 branch_snapshots 中物化到新 latest version；
    - promote 后的 main 状态可从 branch_snapshots 走「最近物化 + 0 增量」快路径
      读出，且与最新 story_states 一致；
    - GET /state?branch_id=<main> 返回的结果与 GET /state 一致（数值对比）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201, r.text
            base_version = r.json()["state_version"]

            r = await _request(
                app, "POST", f"/api/projects/{pid}/branches",
                json={"name": "mat-prom"},
            )
            bid = r.json()["branch_id"]

            # 分支 1 commit：location = Cave
            delta_id = "dlt_matprom"
            delta = {
                **_make_meta(delta_id, chap, base_version),
                "character_changes": [{
                    "change_id": "cc_p", "op": "update", "target_id": cid,
                    "character_id": cid, "facet": "state", "field": "state.location",
                    "before": "Unknown", "after": "Cave", "confidence": 0.95,
                    "evidence": _evidence(chap), "risk_level": "LOW",
                }],
                "world_changes": [], "relationship_changes": [], "new_events": [],
                "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
            }
            r = await _request(
                app, "POST", f"/api/projects/{pid}/deltas",
                json={**delta, "branch_id": bid},
            )
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:p"},
                    "workflow_run_id": "wfr_p",
                    "branch_id": bid,
                },
            )
            assert r.status_code == 201, r.text

            # promote
            r = await _request(
                app, "POST", f"/api/projects/{pid}/branches/{bid}/promote",
                json={"chapter_id": chap},
            )
            assert r.status_code == 201, r.text
            new_main_version = r.json()["state_version"]
            assert new_main_version == base_version + 1

            # 取 main 分支 ID
            r = await _request(app, "GET", f"/api/projects/{pid}/branches")
            main_bid = next(b["branch_id"] for b in r.json() if b["name"] == "main")

            # 直查 branch_snapshots：main 分支应物化到 new_main_version
            conn = _conn(tmp_path / "novelos.db")
            try:
                row = conn.execute(
                    "SELECT state_version FROM branch_snapshots WHERE branch_id = ?",
                    (main_bid,),
                ).fetchone()
                assert row is not None, "main 分支 promote 后无 branch_snapshots 行"
                assert int(row["state_version"]) == new_main_version
            finally:
                conn.close()

            # GET /state?branch_id=main_bid：走「物化 + 0 增量」快路径，
            # 与 GET /state（主线读 path，零变化）的结果一致。
            r_main = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r_main.status_code == 200
            main_via_story_states = r_main.json()
            r_branch = await _request(
                app, "GET", f"/api/projects/{pid}/state?branch_id={main_bid}",
            )
            assert r_branch.status_code == 200
            main_via_branch_snapshot = r_branch.json()

            assert main_via_story_states["state_version"] == new_main_version
            assert main_via_branch_snapshot["state_version"] == new_main_version
            assert _sorted_json(main_via_story_states) == _sorted_json(main_via_branch_snapshot), (
                "promote 后 main 状态：story_states 路径 vs branch_snapshots 路径不一致"
            )

    asyncio.run(run())


# ============================================================================
# 4. 大分支（≥5 commit）物化后增量重放
# ============================================================================


def test_large_branch_incremental_replay_uses_snapshot(tmp_path: Path):
    """大分支（≥5 commit）场景：
    - 分支上有 5 个 commit（顺序编号 1..5）；
    - 读路径走「最近物化点（base_version） + (1..5) 增量重放」；
    - 手工构造「中间物化」（在 base_version+3 处直接插入 branch_snapshots 行
      = apply 前 3 个 commit 的结果）→ 再读分支状态 → 应只重放 commit 4、5
      两个 delta（而非全部 5 个）；
    - 验证手段：「增量路径」结果与「手工全量重放」结果逐字节一致。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201, r.text
            base_version = r.json()["state_version"]

            r = await _request(
                app, "POST", f"/api/projects/{pid}/branches",
                json={"name": "mat-big"},
            )
            bid = r.json()["branch_id"]

            # 5 个 commit，character location 顺序递增
            locations = ["A", "B", "C", "D", "E"]
            for i, loc in enumerate(locations):
                prev_v = base_version + i
                delta_id = f"dlt_big_{i}"
                delta = {
                    **_make_meta(delta_id, chap, prev_v),
                    "character_changes": [{
                        "change_id": f"cc_big_{i}", "op": "update", "target_id": cid,
                        "character_id": cid, "facet": "state", "field": "state.location",
                        "before": locations[i - 1] if i else "Unknown", "after": loc,
                        "confidence": 0.95,
                        "evidence": _evidence(chap), "risk_level": "LOW",
                    }],
                    "world_changes": [], "relationship_changes": [], "new_events": [],
                    "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
                }
                r = await _request(
                    app, "POST", f"/api/projects/{pid}/deltas",
                    json={**delta, "branch_id": bid},
                )
                assert r.status_code == 201, r.text
                r = await _request(
                    app, "POST", f"/api/projects/{pid}/commits",
                    json={
                        "delta_id": delta_id,
                        "author_approval": {"approver": "user:big"},
                        "workflow_run_id": f"wfr_big_{i}",
                        "branch_id": bid,
                    },
                )
                assert r.status_code == 201, r.text

            # 手工构造「中间物化」：在 base_version+3 处落一行
            # branch_snapshots，snapshot = 用 service + 手工 apply 前 3 个 delta 算出
            # 「base+3 commit 后」的中间态。
            from packages.core.story_state.applier import apply_delta
            from packages.core.story_state.service import StoryStateService

            svc = StoryStateService(str(tmp_path / "novelos.db"))
            base_state = svc.get_snapshot(pid, base_version)
            assert base_state is not None

            # 加载前 3 个 delta payload 并手工 apply → 中间态 snapshot
            conn = _conn(tmp_path / "novelos.db")
            try:
                rows = conn.execute(
                    """
                    SELECT c.commit_id, c.resulting_state_version, c.delta_id, d.payload_json
                    FROM commits c
                    JOIN state_deltas d ON c.delta_id = d.delta_id
                    WHERE c.branch_id = ?
                    ORDER BY c.resulting_state_version ASC
                    LIMIT 3
                    """,
                    (bid,),
                ).fetchall()
            finally:
                conn.close()

            intermediate_version = base_version + 3
            intermediate_state = base_state
            from packages.core.story_state.deltas import restore_delta_from_row_payload
            for r in rows:
                payload = json.loads(r["payload_json"])
                delta = restore_delta_from_row_payload(r["delta_id"], payload)
                intermediate_state = apply_delta(intermediate_state, delta)
            intermediate_state["state_version"] = intermediate_version

            # 写入中间物化行（绕过 create_branch 路径）
            conn = _conn(tmp_path / "novelos.db")
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO branch_snapshots "
                    "(branch_id, state_version, snapshot_json, created_at) VALUES (?, ?, ?, ?)",
                    (bid, intermediate_version,
                     json.dumps(intermediate_state, ensure_ascii=False),
                     "2026-08-24T12:00:00+00:00"),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT state_version FROM branch_snapshots WHERE branch_id = ?",
                    (bid,),
                ).fetchone()
                assert row is not None
                assert int(row["state_version"]) == intermediate_version
            finally:
                conn.close()

            # 走读路径：从物化点（base+3）开始应只增量重放 commit 4、5
            r = await _request(app, "GET", f"/api/projects/{pid}/state?branch_id={bid}")
            assert r.status_code == 200, r.text

            # V2.0 Wave C P2-3：monkeypatch apply_delta 计数，断言增量路径只调用 2 次
            # （commit 4、5 对应的 2 个 delta）；若走全量重放会是 5 次。
            from packages.core.story_state import applier as applier_mod
            from packages.core.story_state import branches as branches_mod

            apply_calls: list[int] = []

            real_apply = applier_mod.apply_delta

            def spy_apply(state, delta):
                apply_calls.append(1)
                return real_apply(state, delta)

            import unittest.mock as _mock

            # 替换 branches_mod 引用的 apply_delta（branch_current_state 走模块内 import）
            with _mock.patch.object(branches_mod, "apply_delta", side_effect=spy_apply):
                # 删物化行再读 → 全量重放计数
                conn = _conn(tmp_path / "novelos.db")
                try:
                    conn.execute(
                        "DELETE FROM branch_snapshots WHERE branch_id = ?", (bid,),
                    )
                    conn.commit()
                finally:
                    conn.close()
                r = await _request(
                    app, "GET", f"/api/projects/{pid}/state?branch_id={bid}",
                )
                assert r.status_code == 200, r.text
                via_full = r.json()
                full_replay_count = len(apply_calls)
                apply_calls.clear()

                # 重新插入中间物化 → 增量重放计数
                conn = _conn(tmp_path / "novelos.db")
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO branch_snapshots "
                        "(branch_id, state_version, snapshot_json, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            bid, intermediate_version,
                            json.dumps(intermediate_state, ensure_ascii=False),
                            "2026-08-24T12:00:00+00:00",
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
                r = await _request(
                    app, "GET", f"/api/projects/{pid}/state?branch_id={bid}",
                )
                assert r.status_code == 200, r.text
                via_incremental2 = r.json()
                incremental_replay_count = len(apply_calls)

            # P2-3 关键断言：增量路径 apply 次数 < 全量路径
            assert incremental_replay_count < full_replay_count, (
                f"增量重放次数 ({incremental_replay_count}) 不应少于全量重放 ({full_replay_count})"
            )
            # 增量重放应只覆盖物化点之后（5 commits，物化点在 3 ⇒ 只重放 4、5 两个）
            assert incremental_replay_count == 2, (
                f"物化点在 commit 3 之后，增量重放应只调用 2 次 apply_delta，实际 {incremental_replay_count}"
            )
            assert full_replay_count == 5, (
                f"全量重放应调用 5 次 apply_delta，实际 {full_replay_count}"
            )
            assert _sorted_json(via_incremental2) == _sorted_json(via_full), (
                "物化点后增量重放结果与全量重放不一致"
            )
            # 最终 location 应是 E
            char = next(c for c in via_incremental2["characters"] if c["character_id"] == cid)
            assert char["current_state"]["location"] == "E"
            assert via_incremental2["state_version"] == base_version + 5

    asyncio.run(run())
