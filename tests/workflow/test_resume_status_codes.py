"""缺陷 2（低）复现：resume 端点 ValueError 分桶映射。

证据：docs/testing/audit-workflows-engine-20260829.md 第 4 条。

约定：httpx ASGI + asyncio.run 自管事件循环（不引入 pytest-asyncio）。

待验证：
- resume_async 抛「workflow run X not found」→ 路由映射 404
- resume_async 抛「workflow run X status=Y, must be PAUSED to resume」→ 路由映射 409
- 其他 ValueError → 400（兜底保留）
- 404/409 分支必须有 logging.debug 留痕
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.api.routers.workflows import log as wf_log
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection


def _setup_app(tmp_path: Path):
    # log_level=DEBUG 让 caplog 能捕到 logging.debug 留痕（验收条件）
    settings = Settings(data_dir=tmp_path, log_level="DEBUG")
    apply_migrations(settings.db_path)
    app = create_app(settings)
    return app, settings


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _run(coro):
    return asyncio.run(coro)


def test_resume_unknown_run_maps_to_404(tmp_path: Path, caplog, monkeypatch) -> None:
    """resume_async 抛 'not found' → 路由返回 404 + logging.debug。"""
    app, _settings = _setup_app(tmp_path)

    # caplog 在已有 configure_logging 的环境里对 novelos.* logger propagate 不可靠；
    # 改用 monkeypatch 替换 logger.debug 为可断言 spy。
    debug_calls: list[str] = []
    def _spy_debug(msg, *args, **kwargs):
        debug_calls.append(msg % args if args else str(msg))
    monkeypatch.setattr(wf_log, "debug", _spy_debug)

    async def _do():
        async with _make_client(app) as client:
            return await client.post(
                "/api/runs/run_definitely_does_not_exist_xyz/resume",
                json={},
            )

    r = _run(_do())

    assert r.status_code == 404, f"期望 404，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert "not found" in body.get("detail", "").lower()
    # 兜底层会记录 debug；前置层（无 ValueError）无 debug 是允许的——状态码已 404 即可


def test_resume_non_paused_run_maps_to_409(tmp_path: Path, caplog, monkeypatch) -> None:
    """前置校验：直接 RUNNING run 触发 409。"""
    app, settings = _setup_app(tmp_path)

    from packages.core.workflow_runtime.engine import WorkflowEngine
    _engine = WorkflowEngine(settings.db_path)
    wf_id = _engine.ensure_workflow("chapter-plan")
    # ensure_workflow 用短连接 INSERT，不 commit；显式 commit 让 FK 可见
    conn0 = get_connection(settings.db_path)
    try:
        conn0.execute(
            "INSERT OR IGNORE INTO workflows (workflow_id, name, version, definition_json, created_at, updated_at)"
            " VALUES (?, ?, 'v1', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (wf_id, "chapter-plan"),
        )
        conn0.commit()
    finally:
        conn0.close()

    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO workflow_runs (run_id, workflow_id, status, started_at, checkpoint_json)
            VALUES (?, ?, 'RUNNING', '2026-01-01T00:00:00Z', '{}')
            """,
            ("run_running_001", wf_id),
        )
        conn.commit()
    finally:
        conn.close()

    # caplog 在已有 configure_logging 的环境里对 novelos.* logger propagate 不可靠；
    # 改用 monkeypatch 替换 logger.debug 为可断言 spy。
    debug_calls: list[str] = []
    def _spy_debug(msg, *args, **kwargs):
        debug_calls.append(msg % args if args else str(msg))
    monkeypatch.setattr(wf_log, "debug", _spy_debug)

    async def _do():
        async with _make_client(app) as client:
            return await client.post("/api/runs/run_running_001/resume", json={})

    r = _run(_do())

    assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert "paused" in body.get("detail", "").lower() or "RUNNING" in body.get("detail", "")
    # 兜底层会记录 debug；前置层（无 ValueError）无 debug 是允许的——状态码已 409 即可


def test_resume_value_error_409_via_mocked_resume_async(tmp_path: Path, caplog, monkeypatch) -> None:
    """兜底：engine.resume_async 抛 'must be PAUSED'（竞态：前置校验后状态被改）→ 409。"""
    app, settings = _setup_app(tmp_path)

    # 通过 engine.ensure_workflow 创建 workflows 行，拿到真实 workflow_id
    from packages.core.workflow_runtime.engine import WorkflowEngine
    _engine = WorkflowEngine(settings.db_path)
    wf_id = _engine.ensure_workflow("chapter-plan")
    # ensure_workflow 用短连接 INSERT，不 commit；显式 commit 让 FK 可见
    conn0 = get_connection(settings.db_path)
    try:
        conn0.execute(
            "INSERT OR IGNORE INTO workflows (workflow_id, name, version, definition_json, created_at, updated_at)"
            " VALUES (?, ?, 'v1', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (wf_id, "chapter-plan"),
        )
        conn0.commit()
    finally:
        conn0.close()

    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO workflow_runs (run_id, workflow_id, status, started_at, checkpoint_json)
            VALUES (?, ?, 'PAUSED', '2026-01-01T00:00:00Z', '{}')
            """,
            ("run_paused_001", wf_id),
        )
        conn.commit()
    finally:
        conn.close()

    from packages.core.api.routers import workflows as wf_mod

    orig_get_wf_name = wf_mod.get_workflow_name_for_run

    def _stub_name(db, run_id):
        return "chapter-plan"
    wf_mod.get_workflow_name_for_run = _stub_name

    orig_get_wf = wf_mod.get_workflow

    def _stub_def(name):
        return {"name": name, "nodes": [], "checkpoint_exclude": []}
    wf_mod.get_workflow = _stub_def

    class _FakeEngine:
        def resume_async(self, run_id, nodes, human_input=None, regenerate=False):
            raise ValueError(
                "workflow run 'run_paused_001' status='FAILED', must be PAUSED to resume"
            )

    orig_engine = wf_mod._engine

    def _fake_engine(request):
        return _FakeEngine()
    wf_mod._engine = _fake_engine

    # caplog 在已有 configure_logging 的环境里对 novelos.* logger propagate 不可靠；
    # 改用 monkeypatch 替换 logger.debug 为可断言 spy。
    debug_calls: list[str] = []
    def _spy_debug(msg, *args, **kwargs):
        debug_calls.append(msg % args if args else str(msg))
    monkeypatch.setattr(wf_log, "debug", _spy_debug)

    try:
        async def _do():
            async with _make_client(app) as client:
                return await client.post("/api/runs/run_paused_001/resume", json={})

        r = _run(_do())
    finally:
        wf_mod.get_workflow_name_for_run = orig_get_wf_name
        wf_mod.get_workflow = orig_get_wf
        wf_mod._engine = orig_engine

    assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert "PAUSED" in body.get("detail", "") or "paused" in body.get("detail", "").lower()

    debug_msgs = debug_calls
    assert any("resume" in m for m in debug_msgs), (
        f"应有 logging.debug 留痕，实际={debug_msgs}"
    )


def test_resume_value_error_404_via_mocked_resume_async(tmp_path: Path, caplog, monkeypatch) -> None:
    """兜底：engine.resume_async 抛 'not found'（前置 get_run 后 run 被删）→ 404。"""
    app, settings = _setup_app(tmp_path)

    from packages.core.workflow_runtime.engine import WorkflowEngine
    _engine = WorkflowEngine(settings.db_path)
    wf_id = _engine.ensure_workflow("chapter-plan")
    # ensure_workflow 用短连接 INSERT，不 commit；显式 commit 让 FK 可见
    conn0 = get_connection(settings.db_path)
    try:
        conn0.execute(
            "INSERT OR IGNORE INTO workflows (workflow_id, name, version, definition_json, created_at, updated_at)"
            " VALUES (?, ?, 'v1', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (wf_id, "chapter-plan"),
        )
        conn0.commit()
    finally:
        conn0.close()

    conn = get_connection(settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO workflow_runs (run_id, workflow_id, status, started_at, checkpoint_json)
            VALUES (?, ?, 'PAUSED', '2026-01-01T00:00:00Z', '{}')
            """,
            ("run_paused_002", wf_id),
        )
        conn.commit()
    finally:
        conn.close()

    from packages.core.api.routers import workflows as wf_mod

    orig_get_wf_name = wf_mod.get_workflow_name_for_run

    def _stub_name(db, run_id):
        return "chapter-plan"
    wf_mod.get_workflow_name_for_run = _stub_name

    orig_get_wf = wf_mod.get_workflow

    def _stub_def(name):
        return {"name": name, "nodes": [], "checkpoint_exclude": []}
    wf_mod.get_workflow = _stub_def

    class _FakeEngine:
        def resume_async(self, run_id, nodes, human_input=None, regenerate=False):
            raise ValueError("workflow run 'run_paused_002' not found")

    orig_engine = wf_mod._engine

    def _fake_engine(request):
        return _FakeEngine()
    wf_mod._engine = _fake_engine

    # caplog 在已有 configure_logging 的环境里对 novelos.* logger propagate 不可靠；
    # 改用 monkeypatch 替换 logger.debug 为可断言 spy。
    debug_calls: list[str] = []
    def _spy_debug(msg, *args, **kwargs):
        debug_calls.append(msg % args if args else str(msg))
    monkeypatch.setattr(wf_log, "debug", _spy_debug)

    try:
        async def _do():
            async with _make_client(app) as client:
                return await client.post("/api/runs/run_paused_002/resume", json={})

        r = _run(_do())
    finally:
        wf_mod.get_workflow_name_for_run = orig_get_wf_name
        wf_mod.get_workflow = orig_get_wf
        wf_mod._engine = orig_engine

    assert r.status_code == 404, f"期望 404，实际 {r.status_code}: {r.text}"
    debug_msgs = debug_calls
    assert any("resume" in m for m in debug_msgs), (
        f"应有 logging.debug 留痕，实际={debug_msgs}"
    )
