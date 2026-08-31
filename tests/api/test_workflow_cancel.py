"""工作流运行取消端点测试（协作式）。

覆盖：
- RUNNING run → POST cancel → 200 + status=CANCELLED；再调一次（已 CANCELLED）→ 409。
- 终态（COMPLETED / FAILED）→ 409。
- PAUSED → 409（PAUSED 的取消走 resume 后驳回/决议路径，不在本端点范围）。
- run 不存在 → 404。
- 兜底：engine.cancel_run 在前置校验后抛 ValueError 时分桶映射 404/409。

约定：httpx ASGI + asyncio.run 自管事件循环（不引入 pytest-asyncio）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso


def _setup_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    app = create_app(settings)
    return app, settings


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _run(coro):
    return asyncio.run(coro)


def _ensure_workflow(conn, name: str) -> str:
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


def _insert_run(
    conn, *, wf_id: str, status: str, run_id: str | None = None
) -> str:
    rid = run_id or new_id("wfr")
    started = now_iso()
    ended = now_iso() if status in ("COMPLETED", "FAILED", "CANCELLED") else None
    error = (
        "interrupted: service restart killed worker thread"
        if status == "FAILED"
        else None
    )
    conn.execute(
        """
        INSERT INTO workflow_runs
            (run_id, workflow_id, chapter_id, status, current_node,
             checkpoint_json, error, retry_count, started_at, ended_at)
        VALUES (?, ?, NULL, ?, NULL, '{}', ?, 0, ?, ?)
        """,
        (rid, wf_id, status, error, started, ended),
    )
    return rid


# ---------------------------------------------------------------------------
# 1. RUNNING → 200；再调（已 CANCELLED）→ 409
# ---------------------------------------------------------------------------


def test_cancel_running_run_returns_200_and_marks_cancelled(tmp_path: Path) -> None:
    """RUNNING run POST cancel → 200 + body.status=CANCELLED + run 行真翻。"""
    app, settings = _setup_app(tmp_path)

    conn = get_connection(settings.db_path)
    try:
        wf_id = _ensure_workflow(conn, "chapter-plan")
        run_id = _insert_run(conn, wf_id=wf_id, status="RUNNING")
        conn.commit()
    finally:
        conn.close()

    async def _do():
        async with _make_client(app) as client:
            return await client.post(f"/api/runs/{run_id}/cancel")

    r = _run(_do())
    assert r.status_code == 200, f"期望 200，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert body == {"run_id": run_id, "status": "CANCELLED"}, body

    # run 行真翻 CANCELLED + ended_at 非空
    conn = get_connection(settings.db_path)
    try:
        row = conn.execute(
            "SELECT status, ended_at FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row["status"] == "CANCELLED"
        assert row["ended_at"] is not None
    finally:
        conn.close()


def test_cancel_already_cancelled_run_returns_409(tmp_path: Path) -> None:
    """幂等：已 CANCELLED 的 run 再 cancel → 409（与终态 409 一致）。"""
    app, settings = _setup_app(tmp_path)

    conn = get_connection(settings.db_path)
    try:
        wf_id = _ensure_workflow(conn, "chapter-plan")
        run_id = _insert_run(conn, wf_id=wf_id, status="CANCELLED")
        conn.commit()
    finally:
        conn.close()

    async def _do():
        async with _make_client(app) as client:
            return await client.post(f"/api/runs/{run_id}/cancel")

    r = _run(_do())
    assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert "CANCELLED" in body.get("detail", ""), body


# ---------------------------------------------------------------------------
# 2. 终态（COMPLETED / FAILED）→ 409
# ---------------------------------------------------------------------------


def test_cancel_completed_run_returns_409(tmp_path: Path) -> None:
    app, settings = _setup_app(tmp_path)

    conn = get_connection(settings.db_path)
    try:
        wf_id = _ensure_workflow(conn, "chapter-plan")
        run_id = _insert_run(conn, wf_id=wf_id, status="COMPLETED")
        conn.commit()
    finally:
        conn.close()

    async def _do():
        async with _make_client(app) as client:
            return await client.post(f"/api/runs/{run_id}/cancel")

    r = _run(_do())
    assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert "COMPLETED" in body.get("detail", ""), body
    assert "RUNNING" in body.get("detail", ""), body


def test_cancel_failed_run_returns_409(tmp_path: Path) -> None:
    app, settings = _setup_app(tmp_path)

    conn = get_connection(settings.db_path)
    try:
        wf_id = _ensure_workflow(conn, "chapter-plan")
        run_id = _insert_run(conn, wf_id=wf_id, status="FAILED")
        conn.commit()
    finally:
        conn.close()

    async def _do():
        async with _make_client(app) as client:
            return await client.post(f"/api/runs/{run_id}/cancel")

    r = _run(_do())
    assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert "FAILED" in body.get("detail", ""), body


# ---------------------------------------------------------------------------
# 3. PAUSED → 409（走 resume 后驳回路径）
# ---------------------------------------------------------------------------


def test_cancel_paused_run_returns_409(tmp_path: Path) -> None:
    """PAUSED run 的取消不在本端点范围（走 resume 后驳回/决议路径）→ 409。"""
    app, settings = _setup_app(tmp_path)

    conn = get_connection(settings.db_path)
    try:
        wf_id = _ensure_workflow(conn, "chapter-plan")
        run_id = _insert_run(conn, wf_id=wf_id, status="PAUSED")
        conn.commit()
    finally:
        conn.close()

    async def _do():
        async with _make_client(app) as client:
            return await client.post(f"/api/runs/{run_id}/cancel")

    r = _run(_do())
    assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert "PAUSED" in body.get("detail", ""), body
    assert "RUNNING" in body.get("detail", ""), body


# ---------------------------------------------------------------------------
# 4. run 不存在 → 404
# ---------------------------------------------------------------------------


def test_cancel_unknown_run_returns_404(tmp_path: Path) -> None:
    app, _settings = _setup_app(tmp_path)

    async def _do():
        async with _make_client(app) as client:
            return await client.post("/api/runs/wfr_definitely_does_not_exist/cancel")

    r = _run(_do())
    assert r.status_code == 404, f"期望 404，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert "not found" in body.get("detail", "").lower(), body


# ---------------------------------------------------------------------------
# 5. 兜底：engine.cancel_run 抛 ValueError → 端点分桶映射
# ---------------------------------------------------------------------------


def test_cancel_run_value_error_404_via_mocked_engine(tmp_path: Path, monkeypatch) -> None:
    """兜底：engine.cancel_run 抛 'not found'（前置 get_run 后被并发删）→ 404。"""
    app, settings = _setup_app(tmp_path)

    conn = get_connection(settings.db_path)
    try:
        wf_id = _ensure_workflow(conn, "chapter-plan")
        run_id = _insert_run(conn, wf_id=wf_id, status="RUNNING")
        conn.commit()
    finally:
        conn.close()

    from packages.core.api.routers import workflows as wf_mod

    orig_engine = wf_mod._engine

    class _FakeEngine:
        def cancel_run(self, rid):
            raise ValueError(f"workflow run {rid!r} not found")

    def _fake_engine(request):
        return _FakeEngine()

    monkeypatch.setattr(wf_mod, "_engine", _fake_engine)
    try:
        async def _do():
            async with _make_client(app) as client:
                return await client.post(f"/api/runs/{run_id}/cancel")

        r = _run(_do())
    finally:
        monkeypatch.setattr(wf_mod, "_engine", orig_engine)

    assert r.status_code == 404, f"期望 404，实际 {r.status_code}: {r.text}"
    assert "not found" in r.json().get("detail", "").lower()


def test_cancel_run_value_error_409_via_mocked_engine(tmp_path: Path, monkeypatch) -> None:
    """兜底：engine.cancel_run 抛 'must be RUNNING'（TOCTOU 并发改状态）→ 409。"""
    app, settings = _setup_app(tmp_path)

    conn = get_connection(settings.db_path)
    try:
        wf_id = _ensure_workflow(conn, "chapter-plan")
        run_id = _insert_run(conn, wf_id=wf_id, status="RUNNING")
        conn.commit()
    finally:
        conn.close()

    from packages.core.api.routers import workflows as wf_mod

    orig_engine = wf_mod._engine

    class _FakeEngine:
        def cancel_run(self, rid):
            raise ValueError(
                f"workflow run {rid!r} status changed concurrently; "
                f"current status is no longer RUNNING"
            )

    def _fake_engine(request):
        return _FakeEngine()

    monkeypatch.setattr(wf_mod, "_engine", _fake_engine)
    try:
        async def _do():
            async with _make_client(app) as client:
                return await client.post(f"/api/runs/{run_id}/cancel")

        r = _run(_do())
    finally:
        monkeypatch.setattr(wf_mod, "_engine", orig_engine)

    assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert "concurrent" in body.get("detail", "") or "RUNNING" in body.get("detail", "")
