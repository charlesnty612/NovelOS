"""/api/chapters/{id}/reopen 集成测试（重开返修批次，2026-09-26）。

覆盖（任务书给死）：
- COMMITTED 章 reopen（带 reason）→ 200 且状态变 REVIEWED，revision_note 附加
- 无 body reopen → 200（请求体整体可选）
- 非 COMMITTED（DRAFTED 直插）→ 409
- chapter 不存在 → 404
- 有活动 run（RUNNING / PENDING）→ 409（先于状态迁移，章状态不得翻转）
- 终态 run（COMPLETED）不阻塞 → 200
- 一致性抽查：reopen 后跑 scripts/check_state_sync.py --db <临时库> 结论 SYNC
  （证明章状态翻转不产生快照漂移；无快照项目按 2026-09-21 修复口径单列、不计漂移）

测试模式参考 ``tests/api/test_chapter_drafts.py``（httpx.ASGITransport + tmp_path db）
与 ``tests/api/test_workflow_cancel.py``（直插 workflows / workflow_runs 造活动 run）。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_state_sync.py"


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "reopen 项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _insert_chapter(db_path: str, pid: str, *, status: str = "COMMITTED") -> str:
    """直插 COMMITTED 章（绕开状态机，测试聚焦 reopen 端点本身）。"""
    cid = new_id("ch")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, 1, 'C1', '{}', ?, 'VISIBLE', NULL, ?, ?)
            """,
            (cid, pid, status, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_workflow_run(db_path: str, chapter_id: str, status: str) -> str:
    """直插指定章的活动 run（RUNNING/PENDING 守卫测试用）。"""
    rid = new_id("wfr")
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT workflow_id FROM workflows WHERE name = 'chapter-review'"
        ).fetchone()
        wf_id = row["workflow_id"] if row is not None else new_id("wf")
        if row is None:
            now = now_iso()
            conn.execute(
                """
                INSERT INTO workflows (workflow_id, name, version, definition_json,
                                       created_at, updated_at)
                VALUES (?, 'chapter-review', 'v1', '{}', ?, ?)
                """,
                (wf_id, now, now),
            )
        conn.execute(
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, chapter_id, status, current_node,
                 checkpoint_json, error, retry_count, started_at, ended_at)
            VALUES (?, ?, ?, ?, NULL, '{}', NULL, 0, ?, NULL)
            """,
            (rid, wf_id, chapter_id, status, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------


def test_reopen_committed_returns_200_and_reviewed(tmp_path: Path):
    """COMMITTED 章 reopen（带 reason）→ 200 + status=REVIEWED + note 附加。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = _insert_chapter(str(tmp_path / "novelos.db"), pid)

            r = await _request(
                app, "POST", f"/api/chapters/{cid}/reopen",
                json={"reason": "打脸段落矛盾"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["chapter_id"] == cid
            assert body["status"] == "REVIEWED"
            assert body["plan_json"]["revision_note"] == "reopen: 打脸段落矛盾"

    asyncio.run(run())


def test_reopen_without_body_returns_200(tmp_path: Path):
    """无 body（或空 JSON）reopen → 200（请求体整体可选）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = _insert_chapter(str(tmp_path / "novelos.db"), pid)

            r = await _request(app, "POST", f"/api/chapters/{cid}/reopen")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "REVIEWED"

    asyncio.run(run())


def test_reopen_completed_run_does_not_block(tmp_path: Path):
    """终态 run（COMPLETED）不阻塞重开 → 200。"""
    app = _create_app(tmp_path)
    db_path = str(tmp_path / "novelos.db")

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = _insert_chapter(db_path, pid)
            _insert_workflow_run(db_path, cid, "COMPLETED")

            r = await _request(app, "POST", f"/api/chapters/{cid}/reopen")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "REVIEWED"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 守卫路径
# ---------------------------------------------------------------------------


def test_patch_committed_to_reviewed_returns_409(tmp_path: Path):
    """通用 PATCH COMMITTED→REVIEWED → 409（2026-09-26 收尾批次封死借道）。

    该边保留给 reopen 专用：同一请求体走 PATCH /chapters/{id} 被拦后，reopen
    专用端点仍 200（专用入口不回归）。突变验证：撤 update() 守卫 → 本测试必红。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = _insert_chapter(str(tmp_path / "novelos.db"), pid)

            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}", json={"status": "REVIEWED"},
            )
            assert r.status_code == 409, r.text
            assert "COMMITTED" in r.json()["detail"] and "REVIEWED" in r.json()["detail"]

            # 章状态不得翻转
            g = await _request(app, "GET", f"/api/chapters/{cid}")
            assert g.json()["status"] == "COMMITTED"

            # reopen 专用端点不受影响（不回归）
            r2 = await _request(app, "POST", f"/api/chapters/{cid}/reopen")
            assert r2.status_code == 200, r2.text
            assert r2.json()["status"] == "REVIEWED"

    asyncio.run(run())


def test_patch_other_fields_unaffected_with_active_run(tmp_path: Path):
    """守卫只锁 COMMITTED→REVIEWED 这条边：同章有 RUNNING run 时 PATCH 其它
    合法字段（title）照常 200（update() 本就无活动 run 守卫，本测试防的是
    收尾批次误把守卫扩大到整条 PATCH 路径）。"""
    app = _create_app(tmp_path)
    db_path = str(tmp_path / "novelos.db")

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = _insert_chapter(db_path, pid)
            _insert_workflow_run(db_path, cid, "RUNNING")

            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}", json={"title": "新标题"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["title"] == "新标题"

            # PENDING run 同口径
            cid2 = _insert_chapter(db_path, pid, status="DRAFTED")
            conn = get_connection(db_path)
            try:
                conn.execute("UPDATE chapters SET number = 2 WHERE chapter_id = ?", (cid2,))
                conn.commit()
            finally:
                conn.close()
            _insert_workflow_run(db_path, cid2, "PENDING")
            r2 = await _request(
                app, "PATCH", f"/api/chapters/{cid2}", json={"title": "另一标题"},
            )
            assert r2.status_code == 200, r2.text

    asyncio.run(run())


def test_reopen_non_committed_returns_409(tmp_path: Path):
    """DRAFTED 章 reopen → 409（不能借道 DRAFTED→REVIEWED 合法推进边）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = _insert_chapter(str(tmp_path / "novelos.db"), pid, status="DRAFTED")

            r = await _request(app, "POST", f"/api/chapters/{cid}/reopen")
            assert r.status_code == 409, r.text
            assert "DRAFTED" in r.json()["detail"]
            assert "COMMITTED" in r.json()["detail"]

    asyncio.run(run())


def test_reopen_unknown_chapter_returns_404(tmp_path: Path):
    """chapter 不存在 → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/chapters/ch_nope/reopen")
            assert r.status_code == 404

    asyncio.run(run())


def test_reopen_with_running_run_returns_409(tmp_path: Path):
    """同章有 RUNNING run → 409（活动 run 守卫先于状态迁移）。"""
    app = _create_app(tmp_path)
    db_path = str(tmp_path / "novelos.db")

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = _insert_chapter(db_path, pid)
            _insert_workflow_run(db_path, cid, "RUNNING")

            r = await _request(app, "POST", f"/api/chapters/{cid}/reopen")
            assert r.status_code == 409, r.text
            assert "active workflow run" in r.json()["detail"]

            # 章状态不得翻转
            g = await _request(app, "GET", f"/api/chapters/{cid}")
            assert g.json()["status"] == "COMMITTED"

    asyncio.run(run())


def test_reopen_with_pending_run_returns_409(tmp_path: Path):
    """同章有 PENDING run → 409（守卫口径含 PENDING，与 workflows start 一致）。"""
    app = _create_app(tmp_path)
    db_path = str(tmp_path / "novelos.db")

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = _insert_chapter(db_path, pid)
            _insert_workflow_run(db_path, cid, "PENDING")

            r = await _request(app, "POST", f"/api/chapters/{cid}/reopen")
            assert r.status_code == 409, r.text

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 一致性抽查：重开后 check_state_sync 结论 SYNC
# ---------------------------------------------------------------------------


def test_reopen_keeps_state_sync_ok(tmp_path: Path):
    """reopen 后跑 scripts/check_state_sync.py --db <临时库> → 退出码 0（SYNC）。

    章状态翻转只写 chapters.status，不触碰 story_states / 集合表，不产生快照漂移。
    """
    app = _create_app(tmp_path)
    db_path = tmp_path / "novelos.db"

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = _insert_chapter(str(db_path), pid)
            r = await _request(app, "POST", f"/api/chapters/{cid}/reopen",
                               json={"reason": "sync 抽查"})
            assert r.status_code == 200, r.text

    asyncio.run(run())

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db_path), "--json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"check_state_sync reported drift after reopen: "
        f"rc={proc.returncode} stdout={proc.stdout[-2000:]} stderr={proc.stderr[-500:]}"
    )
    report = json.loads(proc.stdout)
    # 结论 SYNC 且零漂移；无快照项目单列、不计漂移（2026-09-21 修复口径）
    assert report["conclusion"] == "SYNC OK"
    assert report["drifts"] == []
