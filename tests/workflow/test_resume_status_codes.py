"""缺陷 2（低）复现：resume 端点 ValueError 分桶映射。

证据：docs/testing/audit-workflows-engine-20260829.md 第 4 条。

约定：httpx ASGI + asyncio.run 自管事件循环（不引入 pytest-asyncio）。

待验证：
- resume_async 抛「workflow run X not found」→ 路由映射 404
- resume_async 抛「workflow run X status=Y, must be PAUSED to resume」→ 路由映射 409
- 其他 ValueError → 400（兜底保留）
- 404/409 分支必须有 logging.debug 留痕

2026-09-18 追加：resume 竞态 → 索引拒绝 → 500（应为 409）的复现与看守。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest

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

    orig_get_wf_name = wf_mod.control.get_workflow_name_for_run

    def _stub_name(db, run_id):
        return "chapter-plan"
    wf_mod.control.get_workflow_name_for_run = _stub_name

    orig_get_wf = wf_mod.control.get_workflow

    def _stub_def(name):
        return {"name": name, "nodes": [], "checkpoint_exclude": []}
    wf_mod.control.get_workflow = _stub_def

    class _FakeEngine:
        def resume_async(self, run_id, nodes, human_input=None, regenerate=False):
            raise ValueError(
                "workflow run 'run_paused_001' status='FAILED', must be PAUSED to resume"
            )

    orig_engine = wf_mod.control._engine

    def _fake_engine(request):
        return _FakeEngine()
    wf_mod.control._engine = _fake_engine

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
        wf_mod.control.get_workflow_name_for_run = orig_get_wf_name
        wf_mod.control.get_workflow = orig_get_wf
        wf_mod.control._engine = orig_engine

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

    orig_get_wf_name = wf_mod.control.get_workflow_name_for_run

    def _stub_name(db, run_id):
        return "chapter-plan"
    wf_mod.control.get_workflow_name_for_run = _stub_name

    orig_get_wf = wf_mod.control.get_workflow

    def _stub_def(name):
        return {"name": name, "nodes": [], "checkpoint_exclude": []}
    wf_mod.control.get_workflow = _stub_def

    class _FakeEngine:
        def resume_async(self, run_id, nodes, human_input=None, regenerate=False):
            raise ValueError("workflow run 'run_paused_002' not found")

    orig_engine = wf_mod.control._engine

    def _fake_engine(request):
        return _FakeEngine()
    wf_mod.control._engine = _fake_engine

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
        wf_mod.control.get_workflow_name_for_run = orig_get_wf_name
        wf_mod.control.get_workflow = orig_get_wf
        wf_mod.control._engine = orig_engine

    assert r.status_code == 404, f"期望 404，实际 {r.status_code}: {r.text}"
    debug_msgs = debug_calls
    assert any("resume" in m for m in debug_msgs), (
        f"应有 logging.debug 留痕，实际={debug_msgs}"
    )


# ---------------------------------------------------------------------------
# 2026-09-18 修复看守：resume 竞态撞 0017 部分唯一索引 → 500（应为 409）
# ---------------------------------------------------------------------------
#
# 实机证据（data/serve.out.log，run wfr_dcbfa4324d4c，2026-09-18T14:39:53）：
#
#   POST /api/runs/wfr_dcbfa4324d4c/resume HTTP/1.1" 500 Internal Server Error
#   sqlite3.IntegrityError: UNIQUE constraint failed: workflow_runs.chapter_id
#     control.py resume_run → engine.resume_async → engine._mark_run_running 的 UPDATE
#
# 真因：resume 的章节互斥守卫是 check-then-act（control.py 的
# ``_check_active_run_for_chapter`` 查一次），到真正落 RUNNING 的 ``_mark_run_running``
# 之间还要 JOIN 反查 workflow 名 + 取定义 + 读整份 checkpoint_json 拼 ctx（毫秒~秒级）。
# 同刻 auto_revise 回路的 daemon 线程正为**同一个 chapter** 启动 write 子 run
# （revise.py → common._run_workflow_return_payload → engine.start_with_nodes_async），
# 落在该窗口内 → 预检看不到它，UPDATE 被部分唯一索引
# ``idx_workflow_runs_active`` 拒绝 → 原始 sqlite3 异常外溢成 500。
# start 路径早已把同一拒绝转成 WorkflowRunConflict→409，resume 路径缺这一层，是缺口本身。
#
# 修复后：预检（引擎侧）+ UPDATE 的索引拒绝 → WorkflowRunConflict → 端点 409；
# 部分唯一索引**不动**（同 chapter 至多一条 RUNNING/PENDING 的不变量保持不变）。


def _seed_chapter_and_workflow(
    db_path, *, chapter_id: str, workflow_name: str = "chapter-plan"
) -> str:
    """建 project + chapter + workflows 行；返回 workflow_id。"""
    from packages.core.ids import now_iso

    conn = get_connection(db_path)
    try:
        now = now_iso()
        conn.execute(
            "INSERT OR IGNORE INTO projects(project_id, name, created_at, updated_at) "
            "VALUES (?,?,?,?)",
            ("prj_resume_race", "T", now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO chapters(chapter_id, project_id, number, created_at, "
            "updated_at) VALUES (?,?,?,?,?)",
            (chapter_id, "prj_resume_race", 1, now, now),
        )
        wf_id = f"wfid_{chapter_id}"
        conn.execute(
            "INSERT OR IGNORE INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES (?,?,'v1','{}',?,?)",
            (wf_id, workflow_name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return wf_id


def _insert_run(
    db_path, *, run_id: str, workflow_id: str, status: str, chapter_id: str | None
) -> None:
    from packages.core.ids import now_iso

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workflow_runs "
            "(run_id, workflow_id, chapter_id, status, started_at, checkpoint_json) "
            "VALUES (?,?,?,?,?,'{}')",
            (run_id, workflow_id, chapter_id, status, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _run_status(db_path, run_id: str) -> str:
    from packages.core.workflow_runtime.runs import get_run as _get_run

    return _get_run(db_path, run_id)["status"]


def _active_run_ids(db_path, chapter_id: str) -> list[str]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT run_id FROM workflow_runs "
            "WHERE chapter_id = ? AND status IN ('RUNNING','PENDING')",
            (chapter_id,),
        ).fetchall()
    finally:
        conn.close()
    return [r["run_id"] for r in rows]


def _make_racy_prepare(db_path, wf_id: str, chapter_id: str):
    """返回一个 ``_prepare_resume_ctx`` 替身：执行真正实现**之前**落一条同 chapter
    的 RUNNING run——该位置正是实机竞态窗口（端点预检之后、_mark_run_running 之前）。"""
    from packages.core.workflow_runtime.engine import WorkflowEngine

    orig_prepare = WorkflowEngine._prepare_resume_ctx
    injected: list[str] = []

    def _racy_prepare(self, **kwargs):
        if not injected:
            injected.append("run_racer")
            _insert_run(
                db_path,
                run_id="run_racer",
                workflow_id=wf_id,
                status="RUNNING",
                chapter_id=chapter_id,
            )
        return orig_prepare(self, **kwargs)

    return _racy_prepare, injected


def test_resume_blocked_by_existing_active_run_returns_409(tmp_path: Path) -> None:
    """第一道守卫（端点预检）回归看守：同 chapter 已有 RUNNING run → 409，自身不翻转。"""
    app, settings = _setup_app(tmp_path)
    chapter_id = "ch_guard"
    wf_id = _seed_chapter_and_workflow(settings.db_path, chapter_id=chapter_id)
    _insert_run(
        settings.db_path,
        run_id="run_paused_guard",
        workflow_id=wf_id,
        status="PAUSED",
        chapter_id=chapter_id,
    )
    _insert_run(
        settings.db_path,
        run_id="run_running_guard",
        workflow_id=wf_id,
        status="RUNNING",
        chapter_id=chapter_id,
    )

    async def _do():
        async with _make_client(app) as client:
            return await client.post("/api/runs/run_paused_guard/resume", json={})

    r = _run(_do())

    assert r.status_code == 409, f"期望 409，实际 {r.status_code}: {r.text}"
    assert "another active workflow run" in r.json()["detail"]
    assert _run_status(settings.db_path, "run_paused_guard") == "PAUSED"


def test_resume_race_with_newly_active_run_returns_409_not_500(
    tmp_path: Path, monkeypatch
) -> None:
    """竞态复现（实机形状）：预检通过后、置 RUNNING 前，同 chapter 落了别的 RUNNING run。

    修复前：`sqlite3.IntegrityError: UNIQUE constraint failed: workflow_runs.chapter_id`
    从 `_mark_run_running` 外溢 → 未捕获 → HTTP 500（用 raise_app_exceptions=False
    把 ASGI 异常落成响应状态码，正是 uvicorn 的行为）。
    修复后：409，detail 指明冲突来源；resume 目标仍为 PAUSED（未翻转），
    同 chapter 活跃行恒为 1，且索引未被放松（再插一条 RUNNING 仍被拒）。
    """
    app, settings = _setup_app(tmp_path)
    db_path = settings.db_path
    chapter_id = "ch_resume_race"
    wf_id = _seed_chapter_and_workflow(db_path, chapter_id=chapter_id)
    _insert_run(
        db_path,
        run_id="run_paused_race",
        workflow_id=wf_id,
        status="PAUSED",
        chapter_id=chapter_id,
    )

    from packages.core.workflow_runtime.engine import WorkflowEngine

    racy_prepare, injected = _make_racy_prepare(db_path, wf_id, chapter_id)
    monkeypatch.setattr(WorkflowEngine, "_prepare_resume_ctx", racy_prepare)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def _do():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post("/api/runs/run_paused_race/resume", json={})

    r = _run(_do())

    assert injected, "竞态注入未生效（_prepare_resume_ctx 未被调用）——测试自身失效"
    assert r.status_code == 409, (
        f"期望 409（干净冲突），实际 {r.status_code}: {r.text[:400]}"
    )
    detail = r.json().get("detail", "")
    assert "already has an active workflow run" in detail, detail
    assert "run_racer" in detail, detail  # 冲突来源可读（哪个 run 占着 chapter）
    # 不变量一：竞态失败**不翻转** resume 目标（仍是 PAUSED，可稍后重试）
    assert _run_status(db_path, "run_paused_race") == "PAUSED"
    # 不变量二：同 chapter 活跃行恒为 1（多出来的是竞态方，不是被 resume 的 run）
    assert _active_run_ids(db_path, chapter_id) == ["run_racer"]
    # 不变量三：部分唯一索引未被放松——再插一条同 chapter RUNNING 行仍被索引拒绝
    with pytest.raises(sqlite3.IntegrityError):
        _insert_run(
            db_path,
            run_id="run_third",
            workflow_id=wf_id,
            status="RUNNING",
            chapter_id=chapter_id,
        )


def test_engine_resume_conflict_raises_domain_error(tmp_path: Path) -> None:
    """引擎预检（不经 HTTP 的调用方）：同 chapter 已有 RUNNING run 时 ``resume_async``
    抛 ``WorkflowRunConflict``（域异常），不是 ``sqlite3.IntegrityError``。"""
    from packages.core.workflow_runtime.engine import WorkflowEngine, WorkflowRunConflict

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    db_path = settings.db_path
    chapter_id = "ch_engine_conflict"
    wf_id = _seed_chapter_and_workflow(db_path, chapter_id=chapter_id)
    _insert_run(
        db_path,
        run_id="run_paused_engine",
        workflow_id=wf_id,
        status="PAUSED",
        chapter_id=chapter_id,
    )
    _insert_run(
        db_path,
        run_id="run_running_engine",
        workflow_id=wf_id,
        status="RUNNING",
        chapter_id=chapter_id,
    )

    engine = WorkflowEngine(db_path)
    with pytest.raises(WorkflowRunConflict) as exc_info:
        engine.resume_async("run_paused_engine", [])
    assert chapter_id in str(exc_info.value)
    assert _run_status(db_path, "run_paused_engine") == "PAUSED"


def test_engine_resume_race_raises_domain_error_not_sqlite(tmp_path: Path, monkeypatch) -> None:
    """引擎侧原子兜底：预检与 UPDATE 之间的窗口内落一条同 chapter RUNNING run
    （``_prepare_resume_ctx`` 内注入）→ 索引拒绝转 ``WorkflowRunConflict``。

    本用例是「预检不足、索引必须做权威判定」的直接证据：撤掉
    ``_mark_run_running`` 的 IntegrityError→WorkflowRunConflict 转换即转红
    （抛 sqlite3.IntegrityError）。
    """
    from packages.core.workflow_runtime.engine import WorkflowEngine, WorkflowRunConflict

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    db_path = settings.db_path
    chapter_id = "ch_engine_race"
    wf_id = _seed_chapter_and_workflow(db_path, chapter_id=chapter_id)
    _insert_run(
        db_path,
        run_id="run_paused_engine_race",
        workflow_id=wf_id,
        status="PAUSED",
        chapter_id=chapter_id,
    )

    racy_prepare, injected = _make_racy_prepare(db_path, wf_id, chapter_id)
    monkeypatch.setattr(WorkflowEngine, "_prepare_resume_ctx", racy_prepare)

    engine = WorkflowEngine(db_path)
    with pytest.raises(WorkflowRunConflict) as exc_info:
        engine.resume_async("run_paused_engine_race", [])

    assert injected, "竞态注入未生效——测试自身失效"
    assert "run_racer" in str(exc_info.value)
    assert _run_status(db_path, "run_paused_engine_race") == "PAUSED"
    assert _active_run_ids(db_path, chapter_id) == ["run_racer"]
