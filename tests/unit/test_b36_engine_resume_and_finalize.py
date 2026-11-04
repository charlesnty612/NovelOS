"""批次三十六 · 工作流引擎域（批 1）判别用例。

覆盖 5 条已实证缺陷：

- **C-3** ``_mark_run_running`` 的 UPDATE 只有 ``WHERE run_id=?`` ⇒ 同一条 run 的
  并发 resume 双双重启。修法：``AND status='PAUSED'`` + rowcount 0 ⇒
  ``WorkflowRunConflict``。
- **C-4** 节点产出 / checkpoint 不可 JSON 序列化 ⇒ ``TypeError`` 穿出
  ``_run_nodes`` ⇒ run 永久 RUNNING。三处（``_update_node_row`` /
  ``_update_run_checkpoint`` / ``_finalize_run``）兜底为 ``repr`` 截断 + 显式 FAILED。
- **C-7** ``_prepare_resume_ctx`` 找不到 checkpoint 里的 ``current_node`` ⇒
  ``start_index=0`` 从头重跑。修法：显式拒 resume（run 保持 PAUSED）。
- **C-13** FAILED / CANCELLED 收尾时把该 run 遗留的 proposed/validated delta 置
  ``superseded``（复用既有闭集取值，不 DELETE）；启动自愈路径同口径。
- **C-34** ``_resolve_checkpoint_exclude`` 未注册 ⇒ 静默 ``[]``。修法：WARNING。

断言全部打在真实值上（DB 实际 status / error / output_json / checkpoint_json）。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_runtime.engine import (
    PauseRequested,
    WorkflowEngine,
    WorkflowNode,
    WorkflowRunConflict,
    _resolve_checkpoint_exclude,
    recover_interrupted_runs,
)
from packages.core.workflow_runtime.runs import get_run
from tests._db_fixtures import migrated_db

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _UnserializableProbe:
    """``json.dumps`` 必抛 TypeError 的对象；repr 供断言定位。"""

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<B36_UNSERIALIZABLE_PROBE>"


def _make_engine(tmp_path: Path) -> WorkflowEngine:
    return WorkflowEngine(migrated_db(tmp_path))


def _ensure_workflow(db_path: Path, name: str) -> str:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT workflow_id FROM workflows WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            return row["workflow_id"]
        wf_id = new_id("wf")
        now = now_iso()
        conn.execute(
            """
            INSERT INTO workflows
                (workflow_id, name, version, definition_json, created_at, updated_at)
            VALUES (?, ?, 'v1', '{}', ?, ?)
            """,
            (wf_id, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return wf_id


def _insert_run(
    db_path: Path,
    *,
    workflow_id: str,
    status: str,
    current_node: str | None = None,
    chapter_id: str | None = None,
    checkpoint_json: str = "{}",
) -> str:
    run_id = new_id("wfr")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, chapter_id, status, current_node,
                 checkpoint_json, retry_count, started_at, ended_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                run_id,
                workflow_id,
                chapter_id,
                status,
                current_node,
                checkpoint_json,
                now_iso(),
                now_iso() if status in ("COMPLETED", "FAILED", "CANCELLED") else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def _run_status(db_path: Path, run_id: str) -> str | None:
    run = get_run(db_path, run_id)
    return run["status"] if run else None


def _seed_chapter(db_path: Path, chapter_id: str = "ch_b36_delta") -> str:
    conn = get_connection(db_path)
    try:
        now = now_iso()
        conn.execute(
            "INSERT OR IGNORE INTO projects(project_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("prj_b36", "b36", now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO chapters(chapter_id, project_id, number, created_at, "
            "updated_at) VALUES (?, ?, 1, ?, ?)",
            (chapter_id, "prj_b36", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return chapter_id


def _insert_delta(
    db_path: Path, *, delta_id: str, chapter_id: str, run_id: str, status: str
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO state_deltas
                (delta_id, chapter_id, workflow_run_id, previous_state_version,
                 payload_json, status, created_by, created_at)
            VALUES (?, ?, ?, 1, '{}', ?, 'b36', ?)
            """,
            (delta_id, chapter_id, run_id, status, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _delta_statuses(db_path: Path, delta_ids: list[str]) -> dict[str, str]:
    placeholders = ",".join("?" for _ in delta_ids)
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT delta_id, status FROM state_deltas WHERE delta_id IN ({placeholders})",
            tuple(delta_ids),
        ).fetchall()
    finally:
        conn.close()
    return {row["delta_id"]: row["status"] for row in rows}


# ---------------------------------------------------------------------------
# C-3 · resume TOCTOU
# ---------------------------------------------------------------------------


def test_mark_run_running_flips_paused_run(tmp_path: Path) -> None:
    """乐观方向：PAUSED → RUNNING 仍然成功（正常 resume 不被误伤）。"""
    engine = _make_engine(tmp_path)
    wf_id = _ensure_workflow(engine.db_path, "b36-c3-normal-wf")
    run_id = _insert_run(engine.db_path, workflow_id=wf_id, status="PAUSED")

    engine._mark_run_running(run_id)

    assert _run_status(engine.db_path, run_id) == "RUNNING"


def test_resume_still_completes_after_mark_running_guard(tmp_path: Path) -> None:
    """正常 resume 全链：PAUSED → RUNNING → COMPLETED，后续节点真的执行。"""
    engine = _make_engine(tmp_path)
    wf_id = _ensure_workflow(engine.db_path, "b36-c3-resume-wf")
    run_id = _insert_run(
        engine.db_path,
        workflow_id=wf_id,
        status="PAUSED",
        current_node="gate",
    )
    nodes = [
        WorkflowNode(node_id="gate", kind="Human", fn=lambda _ctx: {}),
        WorkflowNode(node_id="after", kind="State", fn=lambda _ctx: {"after_ran": True}),
    ]

    engine.resume(run_id, nodes)

    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "COMPLETED", run
    assert run["checkpoint_json"].get("after_ran") is True
    assert [n["node_id"] for n in run["nodes"]] == ["after"]


@pytest.mark.parametrize("status", ["RUNNING", "COMPLETED", "FAILED", "CANCELLED"])
def test_mark_run_running_rejects_non_paused_run(
    tmp_path: Path, status: str
) -> None:
    """并发方向：状态已不是 PAUSED ⇒ WorkflowRunConflict，且状态不被改写。"""
    engine = _make_engine(tmp_path)
    wf_id = _ensure_workflow(engine.db_path, f"b36-c3-{status.lower()}-wf")
    run_id = _insert_run(engine.db_path, workflow_id=wf_id, status=status)

    with pytest.raises(WorkflowRunConflict) as exc_info:
        engine._mark_run_running(run_id)

    message = str(exc_info.value)
    assert run_id in message
    assert "PAUSED" in message
    assert status in message
    assert _run_status(engine.db_path, run_id) == status


def test_concurrent_mark_run_running_only_one_wins(tmp_path: Path) -> None:
    """并发双 resume：两个线程同时翻转同一条 PAUSED run，只允许一个成功。"""
    engine = _make_engine(tmp_path)
    wf_id = _ensure_workflow(engine.db_path, "b36-c3-race-wf")
    run_id = _insert_run(engine.db_path, workflow_id=wf_id, status="PAUSED")

    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def _attempt() -> None:
        other = WorkflowEngine(engine.db_path)
        barrier.wait(timeout=10)
        try:
            other._mark_run_running(run_id)
            outcome = "ok"
        except WorkflowRunConflict:
            outcome = "conflict"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert sorted(results) == ["conflict", "ok"], results
    assert _run_status(engine.db_path, run_id) == "RUNNING"


# ---------------------------------------------------------------------------
# C-4 · 不可序列化产出 / checkpoint
# ---------------------------------------------------------------------------


def test_unserializable_node_output_fails_run_instead_of_stuck_running(
    tmp_path: Path,
) -> None:
    """节点产出 ``{'bad': object()}`` ⇒ 节点 FAILED + run 有终态 + repr 截断留痕。"""
    engine = _make_engine(tmp_path)
    node = WorkflowNode(
        node_id="bad_node",
        kind="State",
        fn=lambda _ctx: {"bad": _UnserializableProbe()},
    )

    run_id = engine.start_with_nodes("b36-c4-node-wf", [node])

    run = get_run(engine.db_path, run_id)
    assert run is not None, "run row 丢失"
    assert run["status"] == "FAILED", f"run 卡在 {run['status']!r}（应为 FAILED）"
    assert run["ended_at"] is not None
    assert "B36_UNSERIALIZABLE_PROBE" in (run["error"] or "")
    assert "not JSON-serializable" in (run["error"] or "")

    node_row = run["nodes"][0]
    assert node_row["status"] == "FAILED"
    assert "B36_UNSERIALIZABLE_PROBE" in (node_row["error"] or "")
    assert node_row["output_json"]["__unserializable__"] is True
    assert "B36_UNSERIALIZABLE_PROBE" in node_row["output_json"]["repr"]

    # _finalize_run 自身的 checkpoint dump 也必须兜底（否则上面第二次抛错）
    assert run["checkpoint_json"].get("__unserializable__") is True


def test_unserializable_ctx_checkpoint_fails_node_and_run(tmp_path: Path) -> None:
    """节点产出可序列化，但 ctx 混入坏值 ⇒ checkpoint 落盘失败 ⇒ 节点/run 双 FAILED。"""
    engine = _make_engine(tmp_path)
    node = WorkflowNode(node_id="ok_node", kind="State", fn=lambda _ctx: {"ok": 1})

    run_id = engine.start_with_nodes(
        "b36-c4-ctx-wf", [node], initial_ctx={"weird": _UnserializableProbe()}
    )

    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "FAILED", run
    node_row = run["nodes"][0]
    assert node_row["status"] == "FAILED", "checkpoint 失败后节点行仍是 COMPLETED"
    assert "checkpoint not JSON-serializable" in (node_row["error"] or "")
    assert node_row["output_json"] is None
    assert "B36_UNSERIALIZABLE_PROBE" in (run["error"] or "")
    assert run["checkpoint_json"].get("__unserializable__") is True


def test_finalize_run_survives_unserializable_ctx(tmp_path: Path) -> None:
    """直接调 ``_finalize_run``：同类输入下不炸，仍写出 FAILED 终态。"""
    engine = _make_engine(tmp_path)
    wf_id = _ensure_workflow(engine.db_path, "b36-c4-finalize-wf")
    run_id = _insert_run(engine.db_path, workflow_id=wf_id, status="RUNNING")

    engine._finalize_run(
        run_id,
        status="FAILED",
        ctx={"bad": _UnserializableProbe(), "ok": 1},
        current_node=None,
        error="boom",
    )

    run = get_run(engine.db_path, run_id)
    assert run is not None and run["status"] == "FAILED", run
    assert "boom" in (run["error"] or "")
    assert "checkpoint not JSON-serializable" in (run["error"] or "")
    assert run["checkpoint_json"].get("__unserializable__") is True


# ---------------------------------------------------------------------------
# C-7 · current_node 找不到 ⇒ fail-closed
# ---------------------------------------------------------------------------


def test_resume_rejects_unknown_current_node_and_keeps_paused(tmp_path: Path) -> None:
    """节点改名 / 迁移后 resume：显式拒绝，不得从第一个节点重跑。"""
    engine = _make_engine(tmp_path)
    wf_id = _ensure_workflow(engine.db_path, "b36-c7-wf")
    run_id = _insert_run(
        engine.db_path,
        workflow_id=wf_id,
        status="PAUSED",
        current_node="ghost_node",
    )
    nodes = [
        WorkflowNode(node_id="renamed_node", kind="State", fn=lambda _ctx: {"ran": True}),
    ]

    with pytest.raises(ValueError) as exc_info:
        engine.resume(run_id, nodes)

    message = str(exc_info.value)
    assert run_id in message
    assert "ghost_node" in message
    assert "renamed_node" in message

    assert _run_status(engine.db_path, run_id) == "PAUSED"
    # 没有任何节点被重跑（否则会多出 workflow_run_nodes 行）
    run = get_run(engine.db_path, run_id)
    assert run is not None and run["nodes"] == []


# ---------------------------------------------------------------------------
# C-13 · delta 残留清理
# ---------------------------------------------------------------------------


_ALL_DELTA_STATUSES = ["proposed", "validated", "applied", "rejected", "superseded"]


def test_failed_run_supersedes_only_pending_deltas(tmp_path: Path) -> None:
    """FAILED 收尾：proposed/validated → superseded；applied/rejected 不动。"""
    engine = _make_engine(tmp_path)
    chapter_id = _seed_chapter(engine.db_path)

    def _node(ctx):
        for status in _ALL_DELTA_STATUSES:
            _insert_delta(
                engine.db_path,
                delta_id=f"dlt_{status}",
                chapter_id=chapter_id,
                run_id=ctx["run_id"],
                status=status,
            )
        raise RuntimeError("boom")

    run_id = engine.start_with_nodes(
        "b36-c13-failed-wf",
        [WorkflowNode(node_id="boom", kind="State", fn=_node)],
    )

    assert _run_status(engine.db_path, run_id) == "FAILED"
    assert _delta_statuses(engine.db_path, [f"dlt_{s}" for s in _ALL_DELTA_STATUSES]) == {
        "dlt_proposed": "superseded",
        "dlt_validated": "superseded",
        "dlt_applied": "applied",
        "dlt_rejected": "rejected",
        "dlt_superseded": "superseded",
    }


def test_paused_run_keeps_pending_deltas(tmp_path: Path) -> None:
    """PAUSED 不是终态：delta 必须原样保留（可 resume 后继续 commit）。"""
    engine = _make_engine(tmp_path)
    chapter_id = _seed_chapter(engine.db_path)

    def _node(ctx):
        _insert_delta(
            engine.db_path,
            delta_id="dlt_pause_proposed",
            chapter_id=chapter_id,
            run_id=ctx["run_id"],
            status="proposed",
        )
        _insert_delta(
            engine.db_path,
            delta_id="dlt_pause_validated",
            chapter_id=chapter_id,
            run_id=ctx["run_id"],
            status="validated",
        )
        raise PauseRequested({"stage": "gate"})

    run_id = engine.start_with_nodes(
        "b36-c13-paused-wf",
        [WorkflowNode(node_id="gate", kind="Human", fn=_node)],
    )

    assert _run_status(engine.db_path, run_id) == "PAUSED"
    assert _delta_statuses(
        engine.db_path, ["dlt_pause_proposed", "dlt_pause_validated"]
    ) == {
        "dlt_pause_proposed": "proposed",
        "dlt_pause_validated": "validated",
    }


def test_cancelled_finalize_supersedes_pending_deltas(tmp_path: Path) -> None:
    """CANCELLED 收尾同样清理。"""
    engine = _make_engine(tmp_path)
    chapter_id = _seed_chapter(engine.db_path)
    wf_id = _ensure_workflow(engine.db_path, "b36-c13-cancel-wf")
    run_id = _insert_run(engine.db_path, workflow_id=wf_id, status="RUNNING")
    _insert_delta(
        engine.db_path,
        delta_id="dlt_cancel_proposed",
        chapter_id=chapter_id,
        run_id=run_id,
        status="proposed",
    )

    engine._finalize_run(run_id, status="CANCELLED", ctx={}, current_node=None)

    assert _run_status(engine.db_path, run_id) == "CANCELLED"
    assert _delta_statuses(engine.db_path, ["dlt_cancel_proposed"]) == {
        "dlt_cancel_proposed": "superseded"
    }


def test_paused_finalize_cleans_by_actual_terminal_not_param(tmp_path: Path) -> None:
    """PAUSED→CANCELLED 竞态：形参是 PAUSED，实际落库是 CANCELLED ⇒ 必须清理。

    这是 C-13 最容易写错的点：按形参判会漏清，按实际终态判才对。
    """
    engine = _make_engine(tmp_path)
    chapter_id = _seed_chapter(engine.db_path)
    wf_id = _ensure_workflow(engine.db_path, "b36-c13-pause-cancel-wf")
    run_id = _insert_run(
        engine.db_path, workflow_id=wf_id, status="RUNNING", current_node="gate"
    )
    _insert_delta(
        engine.db_path,
        delta_id="dlt_pause_cancel_proposed",
        chapter_id=chapter_id,
        run_id=run_id,
        status="proposed",
    )

    # 模拟 cancel_run 先落地：run 已是 CANCELLED
    engine.cancel_run(run_id)
    assert _run_status(engine.db_path, run_id) == "CANCELLED"

    # Human 节点随后才抛 PauseRequested：_finalize_run 形参 PAUSED，实际写 CANCELLED
    engine._finalize_run(run_id, status="PAUSED", ctx={}, current_node="gate")

    assert _run_status(engine.db_path, run_id) == "CANCELLED"
    assert _delta_statuses(engine.db_path, ["dlt_pause_cancel_proposed"]) == {
        "dlt_pause_cancel_proposed": "superseded"
    }


def test_recover_interrupted_runs_supersedes_pending_deltas(tmp_path: Path) -> None:
    """启动自愈（不经 _finalize_run）也要清理该 run 的 pending delta。"""
    engine = _make_engine(tmp_path)
    chapter_id = _seed_chapter(engine.db_path)
    wf_id = _ensure_workflow(engine.db_path, "b36-c13-recover-wf")
    run_id = _insert_run(engine.db_path, workflow_id=wf_id, status="RUNNING")
    _insert_delta(
        engine.db_path,
        delta_id="dlt_recover_proposed",
        chapter_id=chapter_id,
        run_id=run_id,
        status="proposed",
    )
    _insert_delta(
        engine.db_path,
        delta_id="dlt_recover_applied",
        chapter_id=chapter_id,
        run_id=run_id,
        status="applied",
    )

    recovered = recover_interrupted_runs(engine.db_path, instance_id="b36-other-instance")

    assert run_id in recovered
    assert _run_status(engine.db_path, run_id) == "FAILED"
    assert _delta_statuses(
        engine.db_path, ["dlt_recover_proposed", "dlt_recover_applied"]
    ) == {
        "dlt_recover_proposed": "superseded",
        "dlt_recover_applied": "applied",
    }


# ---------------------------------------------------------------------------
# C-34 · checkpoint_exclude 未注册必须可观测
# ---------------------------------------------------------------------------


def test_unregistered_workflow_exclude_logs_warning(tmp_path: Path, caplog) -> None:
    """workflow 未注册 ⇒ 返回 [] 但必须 WARNING（此前静默）。"""
    engine = _make_engine(tmp_path)
    wf_id = _ensure_workflow(engine.db_path, "b36-c34-unregistered-wf")
    run_id = _insert_run(engine.db_path, workflow_id=wf_id, status="PAUSED")

    with caplog.at_level(logging.WARNING, logger="novelos.workflow_runtime"):
        resolved = _resolve_checkpoint_exclude(engine.db_path, run_id)

    assert resolved == []
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "b36-c34-unregistered-wf" in msg and "not registered" in msg
        for msg in messages
    ), messages
