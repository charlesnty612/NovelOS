"""M1 回归：cancel × pause 竞态守卫（V3.9 全量检修）。

缺陷形状：``engine._finalize_run`` 的 PAUSED 分支无 ``WHERE status='RUNNING'`` 守卫
（COMPLETED / FAILED 分支有且已被 test_engine_cancel.py 覆盖；PauseRequested 路径
此前全文无测试）。

推演（本文件用真实线程时序复现）：用户 cancel（``cancel_run`` → 200 CANCELLED）后，
Human 节点抛 :class:`PauseRequested` → ``_finalize_run(PAUSED)`` 无守卫把 run 写回
``PAUSED``（且 ``ended_at`` 保持 NULL）→ 取消被静默回滚，run 可被 ``resume`` 续跑。

修复：PAUSED 分支镜像加 ``WHERE status='RUNNING'`` 守卫；rowcount=0 时读当前状态，
若已 CANCELLED 则按 CANCELLED 语义收尾（status/ended_at/checkpoint/current_node），
否则（已被其它路径置终态）静默跳过。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_runtime.engine import (
    PauseRequested,
    WorkflowEngine,
    WorkflowNode,
)


def _make_engine(tmp_path: Path) -> WorkflowEngine:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return WorkflowEngine(settings.db_path)


def _ensure_workflow(conn, name: str = "m1-pause-cancel-wf") -> str:
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


def _insert_running_run(engine: WorkflowEngine) -> str:
    conn = get_connection(engine.db_path)
    try:
        wf_id = _ensure_workflow(conn)
        run_id = new_id("wfr")
        now = now_iso()
        conn.execute(
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, chapter_id, status, current_node,
                 checkpoint_json, error, retry_count, started_at, ended_at)
            VALUES (?, ?, NULL, 'RUNNING', NULL, '{}', NULL, 0, ?, NULL)
            """,
            (run_id, wf_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def _fetch_run(engine: WorkflowEngine, run_id: str) -> dict:
    conn = get_connection(engine.db_path)
    try:
        row = conn.execute(
            "SELECT status, ended_at, current_node, checkpoint_json FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def _node_status(engine: WorkflowEngine, run_id: str, node_id: str) -> str | None:
    conn = get_connection(engine.db_path)
    try:
        row = conn.execute(
            "SELECT status FROM workflow_run_nodes WHERE run_id = ? AND node_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (run_id, node_id),
        ).fetchone()
    finally:
        conn.close()
    return row["status"] if row else None


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# 1. 端到端时序：cancel 后 Human 节点才抛 PauseRequested → 终态必须 CANCELLED
# ---------------------------------------------------------------------------


def test_pause_after_cancel_keeps_run_cancelled_and_not_resumable(tmp_path: Path) -> None:
    """Human 节点挂起与 cancel 的真实竞态时序（撤修复必红：run 被写回 PAUSED）。"""
    engine = _make_engine(tmp_path)
    node_entered = threading.Event()
    release_pause = threading.Event()

    def _human_fn(_ctx):
        node_entered.set()
        # 等主线程完成 cancel_run 后再抛 PauseRequested（复刻「cancel 先落地，
        # 挂起请求随后到达」的窄窗口）
        release_pause.wait(5.0)
        raise PauseRequested({"stage": "m1-gate", "message": "等待人工决议"})

    nodes = [
        WorkflowNode(node_id="human_gate", kind="Human", fn=_human_fn),
        WorkflowNode(node_id="downstream", kind="AI", fn=lambda _c: {"ok": True}),
    ]

    run_id = engine.start_with_nodes_async("m1-pause-cancel-wf", nodes)
    assert node_entered.wait(5.0), "Human 节点未在 5s 内进入执行"

    # 用户取消：cancel_run 写 CANCELLED + ended_at 并返回 200
    cancelled = engine.cancel_run(run_id)
    assert cancelled["status"] == "CANCELLED"

    # 放开 Human 节点 → 抛 PauseRequested → _finalize_run(PAUSED) 竞态窗口
    release_pause.set()
    # 竞态兜底会把 Human 节点行从 PENDING 标 FAILED（cancelled by user，M1-a）
    assert _wait_until(lambda: _node_status(engine, run_id, "human_gate") == "FAILED"), (
        "Human 节点行未被竞态兜底标 FAILED（M1-a 口径对齐未生效）"
    )
    # 给 _finalize_run(PAUSED) 落库留时间窗（无守卫时会把 CANCELLED 覆盖为 PAUSED）
    time.sleep(0.3)

    run = _fetch_run(engine, run_id)
    assert run["status"] == "CANCELLED", (
        f"cancel 被静默回滚：终态 {run['status']!r}（期望 CANCELLED）；"
        f"ended_at={run['ended_at']!r}"
    )
    assert run["ended_at"] is not None, "CANCELLED 终态必须有 ended_at"

    # M1-a：节点行口径与正常 cancel 对齐——FAILED + error='cancelled by user'
    conn = get_connection(engine.db_path)
    try:
        node_row = conn.execute(
            "SELECT status, error, output_json FROM workflow_run_nodes "
            "WHERE run_id = ? AND node_id = ? ORDER BY rowid DESC LIMIT 1",
            (run_id, "human_gate"),
        ).fetchone()
    finally:
        conn.close()
    assert node_row["status"] == "FAILED" and node_row["error"] == "cancelled by user", (
        f"节点行口径未对齐：status={node_row['status']!r} error={node_row['error']!r}"
    )
    assert node_row["output_json"] is None, "取消路径必须丢弃 pause payload（output_json=NULL）"

    # 下游节点不得执行
    assert _node_status(engine, run_id, "downstream") is None, (
        "取消后下游节点不应被执行"
    )

    # 不可 resume：状态已不是 PAUSED
    try:
        engine.resume(run_id, nodes)
    except ValueError as exc:
        assert "PAUSED" in str(exc), f"resume 报错文案应含 PAUSED，实际 {exc}"
    else:
        raise AssertionError("CANCELLED run 不应可 resume（取消被回滚）")


# ---------------------------------------------------------------------------
# 2. 单元级钉守卫：_finalize_run(PAUSED) 在 run 已 CANCELLED 时不回写
# ---------------------------------------------------------------------------


def test_finalize_run_paused_does_not_overwrite_cancelled(tmp_path: Path) -> None:
    """直接调 _finalize_run(PAUSED)：run 已 CANCELLED → 守卫未命中 → 不回写 PAUSED。"""
    engine = _make_engine(tmp_path)
    run_id = _insert_running_run(engine)
    engine.cancel_run(run_id)

    engine._finalize_run(
        run_id, status="PAUSED", ctx={"k": "v"}, current_node="human_gate"
    )

    run = _fetch_run(engine, run_id)
    assert run["status"] == "CANCELLED", (
        f"PAUSED 分支守卫失效：run 被覆盖为 {run['status']!r}"
    )
    assert run["ended_at"] is not None, "CANCELLED 语义收尾必须保留 ended_at"


def test_finalize_run_paused_unaffected_when_run_still_running(tmp_path: Path) -> None:
    """正常挂起路径不受影响：RUNNING → PAUSED、ended_at 保持 NULL、checkpoint 落盘。"""
    engine = _make_engine(tmp_path)
    run_id = _insert_running_run(engine)

    engine._finalize_run(
        run_id, status="PAUSED", ctx={"human_gate": {"__pause_payload__": {"x": 1}}},
        current_node="human_gate",
    )

    run = _fetch_run(engine, run_id)
    assert run["status"] == "PAUSED"
    assert run["ended_at"] is None, "PAUSED 仍可 resume，ended_at 必须为 NULL"
    assert run["current_node"] == "human_gate"
    ckpt = json.loads(run["checkpoint_json"])
    assert "__pause_payload__" in ckpt.get("human_gate", {}), ckpt


def test_finalize_run_paused_skips_when_run_failed(tmp_path: Path) -> None:
    """run 已被其它路径置 FAILED → PAUSED 收尾不得覆盖终态。"""
    engine = _make_engine(tmp_path)
    run_id = _insert_running_run(engine)
    conn = get_connection(engine.db_path)
    try:
        conn.execute(
            "UPDATE workflow_runs SET status = 'FAILED', ended_at = ?, error = 'boom' WHERE run_id = ?",
            (now_iso(), run_id),
        )
        conn.commit()
    finally:
        conn.close()

    engine._finalize_run(
        run_id, status="PAUSED", ctx={"k": "v"}, current_node="human_gate"
    )

    run = _fetch_run(engine, run_id)
    assert run["status"] == "FAILED", f"终态被覆盖为 {run['status']!r}"
