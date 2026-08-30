"""启动自愈：``recover_interrupted_runs`` 收尾孤儿 RUNNING run + 孤儿节点清扫。

覆盖：
- 用例 A：构造 RUNNING run + 其 RUNNING/PENDING 节点行 + 另一个 PAUSED run
  + 一个 COMPLETED run → 调用 recover → 断言 RUNNING→FAILED 且 error 正确、
  其节点行 FAILED、PAUSED/COMPLETED 不受影响；返回值含正确 run_id。
- 用例 B：无 RUNNING 时调用返回空且不报错。
- 用例 C：进程内仍处于 RUNNING 的 run 不应收尾（仅验证幂等：连续两次调用
  第二次返回空）。
- 用例 D：孤儿节点清扫——已 FAILED run 下的 RUNNING/PENDING 节点行被扫为
  FAILED；PAUSED run 的 PENDING 节点原样不动；COMPLETED run 的 RUNNING 节
  点原样不动（本次不扫 COMPLETED）。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_runtime.engine import recover_interrupted_runs


def _make_db(tmp_path: Path) -> Path:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings.db_path


def _ensure_workflow(conn, name: str = "test-wf") -> str:
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
    conn, *, wf_id: str, status: str, chapter_id: str | None = None
) -> str:
    run_id = new_id("wfr")
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
        VALUES (?, ?, ?, ?, NULL, '{}', ?, 0, ?, ?)
        """,
        (run_id, wf_id, chapter_id, status, error, started, ended),
    )
    return run_id


def _insert_node_row(
    conn, *, run_id: str, node_id: str, status: str
) -> str:
    node_run_id = new_id("wfrn")
    started = now_iso()
    ended = now_iso() if status in ("COMPLETED", "FAILED", "SKIPPED") else None
    error = "interrupted by restart" if status == "FAILED" else None
    conn.execute(
        """
        INSERT INTO workflow_run_nodes
            (node_run_id, run_id, node_id, agent_id, status,
             input_json, output_json, prompt_version, model_id,
             token_usage_json, latency_ms, error, started_at, ended_at)
        VALUES (?, ?, ?, NULL, ?, '{}', NULL, NULL, NULL, NULL, NULL, ?, ?, ?)
        """,
        (node_run_id, run_id, node_id, status, error, started, ended),
    )
    return node_run_id


def _fetch_run(conn, run_id: str) -> dict:
    row = conn.execute(
        "SELECT status, error, ended_at FROM workflow_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return dict(row) if row else {}


def _fetch_nodes(conn, run_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT node_id, status, error FROM workflow_run_nodes WHERE run_id = ? ORDER BY node_id",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 用例 A：RUNNING run + 节点 RUNNING/PENDING + PAUSED + COMPLETED → 仅收尾 RUNNING
# ---------------------------------------------------------------------------


def test_recover_interrupted_runs_marks_running_as_failed(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    conn = get_connection(db_path)
    try:
        wf_id = _ensure_workflow(conn)

        # 1) RUNNING run（孤儿）+ 其下 RUNNING / PENDING 节点行
        # chapter_id=None 避开 chapters FK 与 0017 部分唯一索引（非章节任务形态，
        # 与 project-init 同语义）。
        running_run = _insert_run(conn, wf_id=wf_id, status="RUNNING", chapter_id=None)
        _insert_node_row(
            conn, run_id=running_run, node_id="n1", status="RUNNING"
        )
        _insert_node_row(
            conn, run_id=running_run, node_id="n2", status="PENDING"
        )

        # 2) PAUSED run（合法持久状态）—— 不应被收尾
        paused_run = _insert_run(conn, wf_id=wf_id, status="PAUSED", chapter_id=None)
        _insert_node_row(conn, run_id=paused_run, node_id="p1", status="PENDING")

        # 3) COMPLETED run（终态）—— 不应被收尾
        completed_run = _insert_run(conn, wf_id=wf_id, status="COMPLETED")
        _insert_node_row(conn, run_id=completed_run, node_id="c1", status="COMPLETED")

        # 4) 另一 RUNNING run 但其节点行已 COMPLETED —— run 行应收尾，节点行保留
        mixed_run = _insert_run(conn, wf_id=wf_id, status="RUNNING")
        _insert_node_row(conn, run_id=mixed_run, node_id="m1", status="COMPLETED")

        conn.commit()
    finally:
        conn.close()

    recovered = recover_interrupted_runs(db_path)

    # 返回值含两个 RUNNING run（running_run + mixed_run），不含 PAUSED / COMPLETED
    assert running_run in recovered
    assert mixed_run in recovered
    assert paused_run not in recovered
    assert completed_run not in recovered
    assert len(recovered) == 2

    conn = get_connection(db_path)
    try:
        # RUNNING run 行已收尾
        r = _fetch_run(conn, running_run)
        assert r["status"] == "FAILED"
        assert r["error"] == "interrupted: service restart killed worker thread"
        assert r["ended_at"] is not None

        # 其下 RUNNING / PENDING 节点行已收尾
        nodes = {n["node_id"]: n for n in _fetch_nodes(conn, running_run)}
        assert nodes["n1"]["status"] == "FAILED"
        assert nodes["n1"]["error"] == "interrupted by restart"
        assert nodes["n2"]["status"] == "FAILED"
        assert nodes["n2"]["error"] == "interrupted by restart"

        # PAUSED run 不动
        p = _fetch_run(conn, paused_run)
        assert p["status"] == "PAUSED"
        assert p["ended_at"] is None
        p_nodes = {n["node_id"]: n for n in _fetch_nodes(conn, paused_run)}
        assert p_nodes["p1"]["status"] == "PENDING"

        # COMPLETED run 不动
        c = _fetch_run(conn, completed_run)
        assert c["status"] == "COMPLETED"
        c_nodes = {n["node_id"]: n for n in _fetch_nodes(conn, completed_run)}
        assert c_nodes["c1"]["status"] == "COMPLETED"

        # mixed_run：run 行收尾，节点行保留 COMPLETED（不在 RUNNING/PENDING 范围）
        m = _fetch_run(conn, mixed_run)
        assert m["status"] == "FAILED"
        assert m["error"] == "interrupted: service restart killed worker thread"
        m_nodes = {n["node_id"]: n for n in _fetch_nodes(conn, mixed_run)}
        assert m_nodes["m1"]["status"] == "COMPLETED"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 用例 B：无 RUNNING 时调用返回空且不报错
# ---------------------------------------------------------------------------


def test_recover_interrupted_runs_noop_when_no_running(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    conn = get_connection(db_path)
    try:
        wf_id = _ensure_workflow(conn)
        _insert_run(conn, wf_id=wf_id, status="PAUSED")
        _insert_run(conn, wf_id=wf_id, status="COMPLETED")
        _insert_run(conn, wf_id=wf_id, status="FAILED")
        conn.commit()
    finally:
        conn.close()

    recovered = recover_interrupted_runs(db_path)
    assert recovered == []


# ---------------------------------------------------------------------------
# 用例 C：幂等（连续两次调用，第二次返回空）
# ---------------------------------------------------------------------------


def test_recover_interrupted_runs_is_idempotent(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    conn = get_connection(db_path)
    try:
        wf_id = _ensure_workflow(conn)
        run_id = _insert_run(conn, wf_id=wf_id, status="RUNNING")
        _insert_node_row(conn, run_id=run_id, node_id="n1", status="RUNNING")
        conn.commit()
    finally:
        conn.close()

    first = recover_interrupted_runs(db_path)
    second = recover_interrupted_runs(db_path)

    assert first == [run_id]
    assert second == []


# ---------------------------------------------------------------------------
# 用例 D：孤儿节点清扫——已 FAILED run 下的 RUNNING/PENDING 节点被扫；
# PAUSED run 的 PENDING 节点原样不动；COMPLETED run 的 RUNNING 节点原样不动
# ---------------------------------------------------------------------------


def test_recover_sweeps_orphan_nodes_under_failed_runs(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    conn = get_connection(db_path)
    try:
        wf_id = _ensure_workflow(conn)

        # 1) 已 FAILED run + 仍 RUNNING 的孤儿节点 + 仍 PENDING 的孤儿节点
        failed_run = _insert_run(conn, wf_id=wf_id, status="FAILED", chapter_id=None)
        _insert_node_row(
            conn, run_id=failed_run, node_id="zombie_running", status="RUNNING"
        )
        _insert_node_row(
            conn, run_id=failed_run, node_id="zombie_pending", status="PENDING"
        )

        # 2) PAUSED run + 其 PENDING 节点行（合法人工审阅等待状态）—— 绝不动
        paused_run = _insert_run(conn, wf_id=wf_id, status="PAUSED", chapter_id=None)
        _insert_node_row(
            conn, run_id=paused_run, node_id="paused_pending", status="PENDING"
        )

        # 3) COMPLETED run + 矛盾数据 RUNNING 节点行——本次不扫
        completed_run = _insert_run(
            conn, wf_id=wf_id, status="COMPLETED", chapter_id=None
        )
        _insert_node_row(
            conn, run_id=completed_run, node_id="completed_running", status="RUNNING"
        )

        conn.commit()
    finally:
        conn.close()

    # 没有 RUNNING run，返回空；孤儿清扫只 log 不影响返回值
    assert recover_interrupted_runs(db_path) == []

    conn = get_connection(db_path)
    try:
        # FAILED run 本身不动（保持 FAILED），其下节点行被扫为 FAILED
        r = _fetch_run(conn, failed_run)
        assert r["status"] == "FAILED"

        nodes = {n["node_id"]: n for n in _fetch_nodes(conn, failed_run)}
        assert nodes["zombie_running"]["status"] == "FAILED"
        assert nodes["zombie_running"]["error"] == "interrupted by restart"
        assert nodes["zombie_pending"]["status"] == "FAILED"
        assert nodes["zombie_pending"]["error"] == "interrupted by restart"

        # 节点行的 ended_at 已被刷为非空
        row = conn.execute(
            "SELECT ended_at FROM workflow_run_nodes WHERE run_id = ? AND node_id = ?",
            (failed_run, "zombie_running"),
        ).fetchone()
        assert row["ended_at"] is not None

        # PAUSED run + PENDING 节点原样不动
        p = _fetch_run(conn, paused_run)
        assert p["status"] == "PAUSED"
        p_nodes = {n["node_id"]: n for n in _fetch_nodes(conn, paused_run)}
        assert p_nodes["paused_pending"]["status"] == "PENDING"
        assert p_nodes["paused_pending"]["error"] is None

        # COMPLETED run + RUNNING 节点原样不动（本次不扫 COMPLETED）
        c = _fetch_run(conn, completed_run)
        assert c["status"] == "COMPLETED"
        c_nodes = {n["node_id"]: n for n in _fetch_nodes(conn, completed_run)}
        assert c_nodes["completed_running"]["status"] == "RUNNING"
        assert c_nodes["completed_running"]["error"] is None
    finally:
        conn.close()