"""章节详情接口新增字段 ``last_review_completed_at`` 的集成测试。

覆盖（任务书给死）：
- 有 COMPLETED chapter-review run → GET /api/chapters/{cid}.last_review_completed_at
  返回最近一次 ended_at（多条 run 时取 ended_at 最大者）；
- 无 COMPLETED chapter-review run → 字段为 null（前端据此不渲染「未审」徽标）。

测试模式与 ``tests/api/test_chapter_commit_freshness_guard.py`` 一致：
httpx.ASGITransport + ``Settings(data_dir=tmp_path)`` 拉临时 db；不跑完整 workflow，
直接 SQL 注入 workflows + workflow_runs 行，聚焦 ``ChapterService.
get_last_review_completed_at`` 的 SELECT 行为。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project_and_chapter(app) -> str:
    """建项目 + 章节（默认 PLANNED），返回 chapter_id。"""
    r = await _request(app, "POST", "/api/projects", json={"name": "last-review"})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]

    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": 1, "title": "lr chapter"},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


def _ensure_workflow(conn, name: str) -> str:
    """确保 workflows 表有 name 行，返回 workflow_id。"""
    row = conn.execute(
        "SELECT workflow_id FROM workflows WHERE name = ?", (name,)
    ).fetchone()
    if row is not None:
        return row["workflow_id"]
    wf_id = new_id("wf")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO workflows (workflow_id, name, version, definition_json, created_at, updated_at)
        VALUES (?, ?, 'v1', '{}', ?, ?)
        """,
        (wf_id, name, now, now),
    )
    return wf_id


def _insert_review_run(
    conn, *, chapter_id: str | None, status: str, ended_at: str | None
) -> str:
    """插入 chapter-review workflow_runs 行；COMPLETED 必传 ended_at。
    chapter_id 可传 None（"非章节任务" run，符合表上 FK NOT NULL 约束）。
    """
    wf_id = _ensure_workflow(conn, "chapter-review")
    rid = new_id("wfr")
    started = now_iso()
    conn.execute(
        """
        INSERT INTO workflow_runs
            (run_id, workflow_id, chapter_id, status, current_node,
             checkpoint_json, error, retry_count, started_at, ended_at)
        VALUES (?, ?, ?, ?, NULL, '{}', NULL, 0, ?, ?)
        """,
        (rid, wf_id, chapter_id, status, started, ended_at),
    )
    return rid


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def test_last_review_completed_at_returns_latest_completed_ended_at(tmp_path: Path):
    """多条 chapter-review run 中，取最近一条 status='COMPLETED' 的 ended_at。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            cid = await _make_project_and_chapter(app)

            conn = get_connection(app.state.settings.db_path)
            try:
                # 1) 早的 COMPLETED run（应当被「最近」覆盖）
                _insert_review_run(
                    conn, chapter_id=cid, status="COMPLETED",
                    ended_at="2026-08-20T10:00:00+00:00",
                )
                # 2) 中间一条 FAILED（不影响选择）
                _insert_review_run(
                    conn, chapter_id=cid, status="FAILED",
                    ended_at="2026-08-22T10:00:00+00:00",
                )
                # 3) 最近的 COMPLETED run（应当被返回）
                _insert_review_run(
                    conn, chapter_id=cid, status="COMPLETED",
                    ended_at="2026-08-25T10:00:00+00:00",
                )
                # 4) RUNNING（status 不符，忽略；ended_at 为 None 也应忽略）
                _insert_review_run(
                    conn, chapter_id=cid, status="RUNNING", ended_at=None
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["chapter_id"] == cid
            assert body["last_review_completed_at"] == "2026-08-25T10:00:00+00:00"

    asyncio.run(run())


def test_last_review_completed_at_is_null_when_no_completed_review(tmp_path: Path):
    """无 COMPLETED chapter-review run 时字段为 null。

    负例覆盖：仅有 FAILED / RUNNING 等非 COMPLETED 状态的 review run → 前端
    据此不渲染「未审」徽标（视为「该章节从未审过」）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            cid = await _make_project_and_chapter(app)

            conn = get_connection(app.state.settings.db_path)
            try:
                # 仅有 FAILED 状态的 review run → 不应被采纳
                _insert_review_run(
                    conn, chapter_id=cid, status="FAILED",
                    ended_at="2026-08-21T10:00:00+00:00",
                )
                # 还有一条 COMPLETED 但 chapter_id 为 NULL（"非章节任务" run） → 不应被采纳
                # （SQL 用 chapter_id 过滤），且避免伪造 project / chapter 行带来的 FK 噪声。
                _insert_review_run(
                    conn, chapter_id=None, status="COMPLETED",
                    ended_at="2026-08-23T10:00:00+00:00",
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/chapters/{cid}")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["chapter_id"] == cid
            assert body["last_review_completed_at"] is None

    asyncio.run(run())