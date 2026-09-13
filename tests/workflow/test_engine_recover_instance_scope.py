"""F6 回归：启动自愈按实例归属收敛，跨实例不互杀（V3.9 全量检修）。

缺陷形状：``recover_interrupted_runs`` 无差别清剿全部 RUNNING run——实例 B 启动会把
实例 A 正在跑的 run 置 FAILED（m1_long_run 历史事故旁证）。

修法：
1. 迁移 0026：``workflow_runs.instance_id TEXT``（可空 = 0026 前旧行）；
2. ``WorkflowEngine`` 构造时取当前进程实例 id（模块级 uuid4 hex，可显式注入）；
   ``_insert_run_row`` 把 instance_id 写进 run 行；
3. ``recover_interrupted_runs(db_path, instance_id)`` 只清
   ``instance_id = ? OR instance_id IS NULL``（NULL 旧行一次性收敛）；
4. ``main.py`` lifespan 传当前实例 id。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_runtime.engine import (
    WorkflowEngine,
    WorkflowNode,
    current_instance_id,
    recover_interrupted_runs,
)


def _make_db(tmp_path: Path) -> Path:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings.db_path


def _ensure_workflow(conn, name: str = "f6-wf") -> str:
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


def _insert_running_row(db_path: Path, *, instance_id: str | None) -> str:
    run_id = new_id("wfr")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        wf_id = _ensure_workflow(conn)
        conn.execute(
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, chapter_id, status, current_node,
                 checkpoint_json, error, retry_count, started_at, ended_at, instance_id)
            VALUES (?, ?, NULL, 'RUNNING', NULL, '{}', NULL, 0, ?, NULL, ?)
            """,
            (run_id, wf_id, now, instance_id),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def _run_row(db_path: Path, run_id: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT status, error, instance_id FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# 1. 跨实例不互杀 + NULL 旧行仍收敛
# ---------------------------------------------------------------------------


def test_recover_does_not_kill_other_instance_run(tmp_path: Path) -> None:
    """B 实例启动自愈不得清 A 实例的 RUNNING run；NULL 旧行仍被清。"""
    db_path = _make_db(tmp_path)
    engine_a = WorkflowEngine(db_path)
    run_a = engine_a._insert_run_row(workflow_name="f6-wf", chapter_id=None)
    # NULL = 0026 前旧行
    run_legacy = _insert_running_row(db_path, instance_id=None)

    engine_b = WorkflowEngine(db_path, instance_id="inst_b")
    recovered = recover_interrupted_runs(db_path, engine_b._instance_id)

    assert run_a not in recovered, "B 启动自愈不得清 A 的 run（跨实例互杀回归）"
    row_a = _run_row(db_path, run_a)
    assert row_a["status"] == "RUNNING", f"A 的 run 被误杀：{row_a}"
    assert row_a["instance_id"] == engine_a._instance_id
    # NULL 旧行仍被一次性收敛
    assert run_legacy in recovered
    assert _run_row(db_path, run_legacy)["status"] == "FAILED"


def test_recover_sweeps_own_instance_running_rows(tmp_path: Path) -> None:
    """同一实例 id 的 RUNNING run 会被收敛（保真：过滤不是「永不清」）。"""
    db_path = _make_db(tmp_path)
    engine = WorkflowEngine(db_path)
    run_id = engine._insert_run_row(workflow_name="f6-wf", chapter_id=None)

    recovered = recover_interrupted_runs(db_path, engine._instance_id)

    assert recovered == [run_id]
    row = _run_row(db_path, run_id)
    assert row["status"] == "FAILED"
    assert row["error"] == "interrupted: service restart killed worker thread"


def test_recover_without_instance_id_sweeps_legacy_rows_only(tmp_path: Path) -> None:
    """不传 instance_id（旧调用形态）：只清 NULL 旧行，带 id 的 RUNNING 行不动。"""
    db_path = _make_db(tmp_path)
    engine = WorkflowEngine(db_path)
    run_stamped = engine._insert_run_row(workflow_name="f6-wf", chapter_id=None)
    run_legacy = _insert_running_row(db_path, instance_id=None)

    recovered = recover_interrupted_runs(db_path)

    assert recovered == [run_legacy]
    assert _run_row(db_path, run_stamped)["status"] == "RUNNING"
    assert _run_row(db_path, run_legacy)["status"] == "FAILED"


# ---------------------------------------------------------------------------
# 2. 启动路径：run 行落 instance_id；lifespan 按当前实例收敛
# ---------------------------------------------------------------------------


def test_start_with_nodes_stamps_instance_id(tmp_path: Path) -> None:
    """start_with_nodes / start_with_nodes_async 落行时写 instance_id。"""
    db_path = _make_db(tmp_path)
    engine = WorkflowEngine(db_path, instance_id="inst_x")
    nodes = [WorkflowNode(node_id="n1", kind="State", fn=lambda _c: {"ok": True})]

    run_id = engine.start_with_nodes("f6-stamp", nodes)
    assert _run_row(db_path, run_id)["instance_id"] == "inst_x"

    run_id_async = engine.start_with_nodes_async("f6-stamp", nodes)
    row = _run_row(db_path, run_id_async)
    assert row["instance_id"] == "inst_x"


def test_instance_id_defaults_to_process_instance(tmp_path: Path) -> None:
    """未显式注入时使用进程级实例 id（模块导入时生成的 uuid4 hex）。"""
    engine = WorkflowEngine(_make_db(tmp_path))
    assert engine._instance_id == current_instance_id()
    assert isinstance(engine._instance_id, str) and len(engine._instance_id) >= 32


def test_lifespan_recovery_leaves_other_instance_runs(tmp_path: Path) -> None:
    """lifespan 启动自愈只收敛「本实例 + 旧 NULL 行」，他实例 RUNNING run 不动。"""
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    run_other = _insert_running_row(settings.db_path, instance_id="inst_other_process")
    run_legacy = _insert_running_row(settings.db_path, instance_id=None)

    app = create_app(settings)

    async def _boot():
        async with app.router.lifespan_context(app):
            return None

    asyncio.run(_boot())

    assert _run_row(settings.db_path, run_other)["status"] == "RUNNING", (
        "lifespan 自愈误杀他实例 run"
    )
    assert _run_row(settings.db_path, run_legacy)["status"] == "FAILED", (
        "lifespan 自愈应照旧收敛 NULL 旧行"
    )
