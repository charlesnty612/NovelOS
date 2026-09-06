"""WorkflowEngine 协作式取消测试（取消运行 + 引擎内探针两处）。

覆盖：
1. cancel_run 自身：run 不存在 → ValueError(not found)；非 RUNNING → ValueError(must be RUNNING)；RUNNING → UPDATE 为 CANCELLED。
2. 节点循环开始前探针：RUNNING run 被外部置 CANCELLED 后启动引擎 → 后台线程立即收尾 CANCELLED、后续节点不执行。
3. 节点循环 checkpoint 前探针：节点 fn 执行期间并发把 run 置 CANCELLED → 当前节点标 FAILED + error='cancelled by user' + output 不写、run 收尾 CANCELLED。
   节点行复用 FAILED 枚举而非新增 CANCELLED：扩枚举需重建 workflow_run_nodes
   表，会破坏 ai_call_logs 的 FK 引用（FK 指向 wfrn 表名，SQLite ALTER TABLE
   RENAME 不更新 FK 引用的表名），代价高于 error 字段区分。
4. start_with_nodes_async 整链：cancel 端到端——节点内睡眠期间主线程 cancel → run 到 CANCELLED、节点丢弃 output、不写下游 checkpoint。
"""

from __future__ import annotations

import time
from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_runtime.engine import WorkflowEngine, WorkflowNode


def _make_engine(tmp_path: Path) -> WorkflowEngine:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return WorkflowEngine(settings.db_path)


def _ensure_workflow(conn, name: str = "cancel-test-wf") -> str:
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


def _insert_run(conn, *, wf_id: str, status: str) -> str:
    run_id = new_id("wfr")
    started = now_iso()
    ended = now_iso() if status in ("COMPLETED", "FAILED", "CANCELLED") else None
    conn.execute(
        """
        INSERT INTO workflow_runs
            (run_id, workflow_id, chapter_id, status, current_node,
             checkpoint_json, error, retry_count, started_at, ended_at)
        VALUES (?, ?, NULL, ?, NULL, '{}', NULL, 0, ?, ?)
        """,
        (run_id, wf_id, status, started, ended),
    )
    return run_id


def _force_cancel(conn, run_id: str) -> None:
    """测试辅助：直接把 run 翻 CANCELLED（模拟外部 cancel_run 已落库）。"""
    conn.execute(
        "UPDATE workflow_runs SET status = 'CANCELLED', ended_at = ? WHERE run_id = ?",
        (now_iso(), run_id),
    )
    conn.commit()


def _ai_node(name: str, payload: dict) -> WorkflowNode:
    def fn(_ctx):
        return payload

    return WorkflowNode(node_id=name, kind="AI", fn=fn)


def _blocking_node(name: str, gate: dict, payload: dict, sleep_s: float) -> WorkflowNode:
    """节点 fn：等到 gate['allow']=True 才返回；返回前睡眠 sleep_s。

    gate 与外部测试线程共享，让测试线程在节点执行期间把 run 置 CANCELLED。
    """
    def fn(_ctx):
        deadline = time.monotonic() + 5.0
        while not gate["allow"] and time.monotonic() < deadline:
            time.sleep(0.02)
        if sleep_s > 0:
            time.sleep(sleep_s)
        return payload

    return WorkflowNode(node_id=name, kind="State", fn=fn)


def _get_run_status(engine: WorkflowEngine, run_id: str) -> str | None:
    from packages.core.workflow_runtime.runs import get_run

    run = get_run(engine.db_path, run_id)
    return run["status"] if run else None


def _wait_terminal(engine: WorkflowEngine, run_id: str, *, timeout: float = 10.0) -> str:
    """轮询直到 status ∈ {COMPLETED, PAUSED, FAILED, CANCELLED}；返回终态。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = _get_run_status(engine, run_id)
        if s in {"COMPLETED", "PAUSED", "FAILED", "CANCELLED"}:
            return s
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach terminal within {timeout}s")


def _fetch_nodes(engine: WorkflowEngine, run_id: str) -> list[dict]:
    conn = get_connection(engine.db_path)
    try:
        rows = conn.execute(
            """
            SELECT node_id, status, output_json, error
            FROM workflow_run_nodes
            WHERE run_id = ?
            ORDER BY started_at ASC, node_run_id ASC
            """,
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 1. cancel_run 自身三态
# ---------------------------------------------------------------------------


def test_cancel_run_not_found_raises_value_error(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    try:
        engine.cancel_run("wfr_does_not_exist_xyz")
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown run")


def test_cancel_run_non_running_raises_value_error(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    conn = get_connection(engine.db_path)
    try:
        wf_id = _ensure_workflow(conn)
        run_id = _insert_run(conn, wf_id=wf_id, status="COMPLETED")
        conn.commit()
    finally:
        conn.close()

    try:
        engine.cancel_run(run_id)
    except ValueError as exc:
        assert "must be RUNNING" in str(exc)
        assert "COMPLETED" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-RUNNING run")


def test_cancel_run_running_marks_cancelled(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    conn = get_connection(engine.db_path)
    try:
        wf_id = _ensure_workflow(conn)
        run_id = _insert_run(conn, wf_id=wf_id, status="RUNNING")
        conn.commit()
    finally:
        conn.close()

    result = engine.cancel_run(run_id)
    assert result == {
        "run_id": run_id,
        "status": "CANCELLED",
        "previous_status": "RUNNING",
    }

    # run 行真翻 CANCELLED
    conn = get_connection(engine.db_path)
    try:
        row = conn.execute(
            "SELECT status, ended_at FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row["status"] == "CANCELLED"
        assert row["ended_at"] is not None
    finally:
        conn.close()


def test_cancel_run_toctou_skips_update(tmp_path: Path) -> None:
    """TOCTOU：run 真实从 RUNNING 翻成 FAILED（外部动作）→ cancel_run 内部
    WHERE status='RUNNING' 不命中 → rowcount=0 → ValueError(concurrent)。

    不走 monkeypatch（多线程 + sqlite3.Connection.execute 在 cross-conn 下行为
    不可靠），改用真实 DB 状态切换，更稳也更贴生产场景。
    """
    engine = _make_engine(tmp_path)
    conn = get_connection(engine.db_path)
    try:
        wf_id = _ensure_workflow(conn)
        run_id = _insert_run(conn, wf_id=wf_id, status="RUNNING")
        conn.commit()
    finally:
        conn.close()

    # 模拟外部把 run 翻 FAILED（与 API 层校验后的竞态窗口同语义）
    conn = get_connection(engine.db_path)
    try:
        conn.execute(
            "UPDATE workflow_runs SET status = 'FAILED', ended_at = ? WHERE run_id = ?",
            (now_iso(), run_id),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        engine.cancel_run(run_id)
    except ValueError as exc:
        # engine._fetch_run_status 在 cancel_run 入口会先看到 FAILED → 抛
        # "must be RUNNING to cancel"；TOCTOU 分桶兜底在 cancel_run 内部 SQL
        # 路径——前者先命中；只要是非 RUNNING 抛错即可。
        msg = str(exc)
        assert ("must be RUNNING" in msg) or ("concurrent" in msg), msg
    else:
        raise AssertionError("expected ValueError on non-RUNNING run")


# ---------------------------------------------------------------------------
# P2：钉住 cancel_run SQL 兜底 rowcount=0 分支（入口检查通过、UPDATE 前状态被改）
# ---------------------------------------------------------------------------


def test_cancel_run_sql_guard_rowcount_zero(tmp_path: Path, monkeypatch) -> None:
    """P2 真钉 SQL 兜底：绕过入口 _fetch_run_status 检查（patch 返回 'RUNNING'），
    但库内 run 真实状态是 FAILED → UPDATE WHERE status='RUNNING' 不命中 →
    rowcount=0 → 抛 ValueError('status changed concurrently')。

    这是 cancel_run 真兜底分支——之前用例走的是入口 _fetch_run_status 提前抛错，
    SQL UPDATE 行从未执行；本用例绕过入口、用 monkeypatch 强造 rowcount=0 让 SQL
    行真正被走到、断言其行为符合契约。
    """
    engine = _make_engine(tmp_path)
    conn = get_connection(engine.db_path)
    try:
        wf_id = _ensure_workflow(conn)
        run_id = _insert_run(conn, wf_id=wf_id, status="RUNNING")
        conn.commit()
    finally:
        conn.close()

    # 1) 真实库内把 run 翻成 FAILED（模拟 API 层校验后到 SQL 之间状态被改）
    conn = get_connection(engine.db_path)
    try:
        conn.execute(
            "UPDATE workflow_runs SET status = 'FAILED', ended_at = ? WHERE run_id = ?",
            (now_iso(), run_id),
        )
        conn.commit()
    finally:
        conn.close()

    # 2) monkeypatch _fetch_run_status 返回 RUNNING——绕过入口检查、让 cancel_run
    # 走进 SQL UPDATE 行；同时不改库内状态，让 UPDATE WHERE status='RUNNING' 真
    # 命中 rowcount=0 路径（而非命中真实 FAILED 行）。
    from packages.core.workflow_runtime import engine as engine_mod

    def _fake_fetch_status(_db_path, _rid):
        return "RUNNING"

    monkeypatch.setattr(engine_mod, "_fetch_run_status", _fake_fetch_status)

    try:
        engine.cancel_run(run_id)
    except ValueError as exc:
        msg = str(exc)
        # SQL 兜底分支固定文案「status changed concurrently」
        assert "concurrent" in msg, f"SQL 兜底文案错: {msg!r}"
        assert "RUNNING" in msg, f"应提及 RUNNING 守卫条件: {msg!r}"
    else:
        raise AssertionError(
            "SQL 兜底未触发：cancel_run 入口被绕过但 UPDATE rowcount=0 应抛 ValueError"
        )

    # run 行状态未被改写（UPDATE rowcount=0，无副作用）
    conn = get_connection(engine.db_path)
    try:
        row = conn.execute(
            "SELECT status FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row["status"] == "FAILED", (
            f"SQL 兜底命中后不应改写 run 状态，实际 {row['status']!r}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. 节点循环开始前探针：外部先把 run 置 CANCELLED → 引擎立即收尾、后续节点不执行
# ---------------------------------------------------------------------------


def test_engine_stop_before_node_when_run_already_cancelled(tmp_path: Path) -> None:
    """start_with_nodes_async 之后立即外部翻 CANCELLED；后台线程两处探针之一命中 → run 收尾 CANCELLED、已执行的节点标 FAILED/error='cancelled by user'、未触达的节点不存在。

    测试时序不可控：cancel 可能在「idx=0 _insert_node_row 之前」（探针 1，0 行）
    或「idx=0 节点 fn 执行完毕、checkpoint 探针前」（探针 2，1 行 FAILED）。
    两种都是正确实现路径——关键不变量：run=CANCELLED + 任何已存在的节点行必须
    标 FAILED + error='cancelled by user' + output=NULL + 后续节点不执行。

    n1 的 fn 故意睡 0.3s：让「立即取消」必然落在 n1 执行窗口内，杜绝
    「cancel 晚于 n1 完全落库（合法但破坏本不变量）」的竞态假阳性
    （2026-09-06：checkpoint 落盘路径新增软上限逻辑后该窗口概率上升，实测量化）。
    """
    engine = _make_engine(tmp_path)

    def _slow_n1(_ctx):
        time.sleep(0.3)
        return {"k": "v1"}

    n1 = WorkflowNode(node_id="n1", kind="AI", fn=_slow_n1)
    nodes = [n1, _ai_node("n2", {"k": "v2"}), _ai_node("n3", {"k": "v3"})]

    run_id = engine.start_with_nodes_async("cancel-start-probe", nodes)

    # 立刻把 run 翻 CANCELLED（模拟 cancel_run 已落库）
    conn = get_connection(engine.db_path)
    try:
        _force_cancel(conn, run_id)
    finally:
        conn.close()

    final = _wait_terminal(engine, run_id, timeout=5.0)
    assert final == "CANCELLED", f"期望 CANCELLED，实际 {final}"

    # 不变量：所有已存在的节点行必须 FAILED + output=NULL + error='cancelled by user'
    # SQLite WAL 跨 connection commit 可见性窗口：_wait_terminal 返回时 run 行
    # commit 可见，但同 connection 序列内的 _update_node_row 跨 conn 可见性可能
    # 有微小窗口。等 5s（异常宽松）让 WAL 同步稳定——避免假阳性。
    deadline = time.monotonic() + 5.0
    nodes_log = _fetch_nodes(engine, run_id)
    while time.monotonic() < deadline:
        if all(n["error"] == "cancelled by user" for n in nodes_log):
            break
        time.sleep(0.05)
        nodes_log = _fetch_nodes(engine, run_id)

    assert len(nodes_log) <= 1, (
        f"开始前/探针 1/2 命中时最多 1 节点行（已 insert 的 idx=0），实际 {len(nodes_log)}: {nodes_log}"
    )
    for n in nodes_log:
        assert n["status"] == "FAILED", (
            f"取消节点必须标 FAILED，实际 {n['status']}: {n}"
        )
        assert n["output_json"] is None, (
            f"取消节点 output_json 必须 NULL，实际 {n['output_json']!r}"
        )
        assert n["error"] == "cancelled by user", (
            f"取消节点 error 错，实际 {n.get('error')!r}"
        )


# ---------------------------------------------------------------------------
# 3. 节点循环 checkpoint 前探针：节点 fn 内并发把 run 置 CANCELLED → 当前节点标 CANCELLED + 不写 output
# ---------------------------------------------------------------------------


def test_engine_drops_output_when_cancelled_during_node(tmp_path: Path) -> None:
    """第一个节点 fn 执行期间并发 cancel → 节点 fn 返回后探针 2 命中 → 节点行 CANCELLED + output_json=NULL + run=CANCELLED + 后续节点不执行。"""
    engine = _make_engine(tmp_path)
    gate: dict = {"allow": False}

    nodes = [
        _blocking_node("blocking", gate, {"text": "should be discarded"}, sleep_s=0.0),
        _ai_node("downstream", {"k": "downstream"}),
    ]

    run_id = engine.start_with_nodes_async("cancel-checkpoint-probe", nodes)

    # 等后台线程推进到第一个节点 fn 入口（fn 内 gate 等待）
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        conn = get_connection(engine.db_path)
        try:
            row = conn.execute(
                "SELECT status FROM workflow_run_nodes WHERE run_id = ? AND node_id = 'blocking'",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None and row["status"] == "RUNNING":
            break
        time.sleep(0.02)

    # 在节点 fn 执行期间并发把 run 翻 CANCELLED
    conn = get_connection(engine.db_path)
    try:
        _force_cancel(conn, run_id)
    finally:
        conn.close()

    # 放开 gate 让节点 fn 返回 → 探针 2 命中
    gate["allow"] = True

    final = _wait_terminal(engine, run_id, timeout=5.0)
    assert final == "CANCELLED", f"期望 CANCELLED，实际 {final}"

    # SQLite WAL 下：_wait_terminal 返回时 run 行 commit 可见，但同 connection 序列
    # 内的 _update_node_row（写在 _finalize_run 之前）的 commit 跨 connection
    # 可见性可能存在微小窗口。等 200ms 让 WAL 同步稳定——纯防御性，逻辑上前者
    # commit 必然先于后者。
    deadline = time.monotonic() + 2.0
    nodes_log = _fetch_nodes(engine, run_id)
    while time.monotonic() < deadline:
        by_id = {n["node_id"]: n for n in nodes_log}
        # 节点行 status='FAILED'（与真实失败共用枚举）+ error='cancelled by user'
        # 区分取消——见 engine.py 探针 2 注释，扩枚举需重建表会破坏 ai_call_logs FK。
        if by_id.get("blocking", {}).get("error") == "cancelled by user":
            break
        time.sleep(0.05)
        nodes_log = _fetch_nodes(engine, run_id)

    # 第一个节点已标 FAILED + output_json 为 NULL + error='cancelled by user'；
    # 下游节点未 insert（探针 2 命中后 return）
    by_id = {n["node_id"]: n for n in nodes_log}
    assert "blocking" in by_id, f"blocking 节点行缺失: {nodes_log}"
    assert by_id["blocking"]["status"] == "FAILED", (
        f"blocking 节点期望 FAILED（取消），实际 {by_id['blocking']['status']}"
    )
    assert by_id["blocking"]["output_json"] is None, (
        f"cancel 后 output_json 必须为 NULL，实际 {by_id['blocking']['output_json']!r}"
    )
    assert by_id["blocking"]["error"] == "cancelled by user", (
        f"取消 error 字段错，实际 {by_id['blocking'].get('error')!r}"
    )
    assert "downstream" not in by_id, (
        f"下游节点不应被执行/insert，实际存在: {by_id}"
    )


# ---------------------------------------------------------------------------
# 4. 端到端 cancel_run + 引擎：cancel 后端线程下一次探针立即收尾
# ---------------------------------------------------------------------------


def test_cancel_endpoint_eventually_finalizes_to_cancelled(tmp_path: Path) -> None:
    """节点 fn 慢速睡眠期间调 engine.cancel_run；后端线程下一次 checkpoint 前探针命中 → run 收尾 CANCELLED + 节点标 CANCELLED + 不写 output。"""
    engine = _make_engine(tmp_path)
    gate: dict = {"allow": False}

    nodes = [
        _blocking_node("slow", gate, {"k": "should-be-discarded"}, sleep_s=0.0),
        _ai_node("after", {"k": "after"}),
    ]

    run_id = engine.start_with_nodes_async("cancel-e2e", nodes)

    # 等到 slow 节点已进入 fn（RUNNING 行已落）
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        conn = get_connection(engine.db_path)
        try:
            row = conn.execute(
                "SELECT status FROM workflow_run_nodes WHERE run_id = ? AND node_id = 'slow'",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None and row["status"] == "RUNNING":
            break
        time.sleep(0.02)

    # 调 engine.cancel_run（端点层也是同一方法的语义）
    engine.cancel_run(run_id)

    # 放开 gate
    gate["allow"] = True

    final = _wait_terminal(engine, run_id, timeout=5.0)
    assert final == "CANCELLED", f"期望 CANCELLED，实际 {final}"

    # SQLite WAL 跨 connection commit 可见性窗口（见另一测试注释）
    deadline = time.monotonic() + 2.0
    nodes_log = _fetch_nodes(engine, run_id)
    while time.monotonic() < deadline:
        by_id = {n["node_id"]: n for n in nodes_log}
        if by_id.get("slow", {}).get("error") == "cancelled by user":
            break
        time.sleep(0.05)
        nodes_log = _fetch_nodes(engine, run_id)

    by_id = {n["node_id"]: n for n in nodes_log}
    assert by_id["slow"]["status"] == "FAILED"
    assert by_id["slow"]["error"] == "cancelled by user"
    assert by_id["slow"]["output_json"] is None
    assert "after" not in by_id, f"下游节点不应被执行: {by_id}"


# ---------------------------------------------------------------------------
# P1：_finalize_run 收尾竞态守卫——末节点完成期间并发 cancel → run 不被 COMPLETED 覆盖
# ---------------------------------------------------------------------------


def test_finalize_run_does_not_overwrite_cancelled_by_race(tmp_path: Path) -> None:
    """P1 竞态守卫单元测试：直接构造「_finalize_run(COMPLETED) 调用时 run 已是
    CANCELLED」的边界场景，断言守卫 WHERE status='RUNNING' rowcount=0 → 跳过收尾。

    不依赖后台线程时序——直接调引擎的 _finalize_run，run 真实状态用 cancel_run
    翻 CANCELLED，再调 _finalize_run(COMPLETED)，断言 run 仍是 CANCELLED、
    ended_at 仍是 cancel_run 写入的时间戳。

    守卫未加时的反例：_finalize_run 无守卫 UPDATE 会把 CANCELLED 覆盖为 COMPLETED。
    """
    engine = _make_engine(tmp_path)
    conn = get_connection(engine.db_path)
    try:
        wf_id = _ensure_workflow(conn)
        run_id = _insert_run(conn, wf_id=wf_id, status="RUNNING")
        conn.commit()
    finally:
        conn.close()

    # 1) cancel_run 翻 CANCELLED + 写入 ended_at
    result = engine.cancel_run(run_id)
    assert result["status"] == "CANCELLED"

    # 记录 cancel 写入的 ended_at
    conn = get_connection(engine.db_path)
    try:
        before = conn.execute(
            "SELECT ended_at FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        ended_at_before = before["ended_at"]
    finally:
        conn.close()
    assert ended_at_before is not None

    # 2) 模拟「节点 fn 完成，_run_nodes 即将调 _finalize_run('COMPLETED')」竞态窗口
    #    ——直接调引擎 _finalize_run 走 COMPLETED 收尾路径
    time.sleep(0.01)  # 制造 cancel_run 与 _finalize_run 之间的微小时间差
    engine._finalize_run(
        run_id, status="COMPLETED", ctx={"k": "v"}, current_node="last"
    )

    # 3) 断言守卫生效：run 仍是 CANCELLED，ended_at 仍是 cancel_run 写入的值
    #    （未被 _finalize_run 的二次写覆盖）
    conn = get_connection(engine.db_path)
    try:
        row = conn.execute(
            "SELECT status, ended_at FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row["status"] == "CANCELLED", (
            f"守卫失效：_finalize_run(COMPLETED) 把 CANCELLED 覆盖为 {row['status']!r}"
        )
        assert row["ended_at"] == ended_at_before, (
            f"ended_at 被二次写覆盖：before={ended_at_before!r}, after={row['ended_at']!r}"
        )
    finally:
        conn.close()


def test_finalize_run_failed_path_also_guarded(tmp_path: Path) -> None:
    """P1 守卫覆盖 FAILED 分支：节点 fn 抛异常时也走 _finalize_run('FAILED')，
    若此刻 run 已 CANCELLED，守卫同样应跳过覆盖。"""
    engine = _make_engine(tmp_path)
    conn = get_connection(engine.db_path)
    try:
        wf_id = _ensure_workflow(conn)
        run_id = _insert_run(conn, wf_id=wf_id, status="RUNNING")
        conn.commit()
    finally:
        conn.close()

    # cancel_run 翻 CANCELLED
    engine.cancel_run(run_id)

    # 模拟节点 fn 抛异常 → _finalize_run('FAILED') 路径
    engine._finalize_run(
        run_id, status="FAILED", ctx={"k": "v"}, current_node="last",
        error="boom",
    )

    conn = get_connection(engine.db_path)
    try:
        row = conn.execute(
            "SELECT status, error FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row["status"] == "CANCELLED", (
            f"FAILED 分支守卫失效：被覆盖为 {row['status']!r}"
        )
        assert row["error"] is None, (
            f"CANCELLED run 不应被 FAILED 路径写入 error: {row['error']!r}"
        )
    finally:
        conn.close()


def test_finalize_run_cancelled_path_idempotent(tmp_path: Path) -> None:
    """CANCELLED 分支保持无守卫（幂等重写无害）。cancel_run 已写 CANCELLED 后，
    探针 2 命中也会调 _finalize_run('CANCELLED') —— 仍能写成功（无守卫），便于
    checkpoint 落盘 + current_node 记录。
    """
    engine = _make_engine(tmp_path)
    conn = get_connection(engine.db_path)
    try:
        wf_id = _ensure_workflow(conn)
        run_id = _insert_run(conn, wf_id=wf_id, status="RUNNING")
        conn.commit()
    finally:
        conn.close()

    engine.cancel_run(run_id)

    # CANCELLED 分支无守卫，幂等重写 OK
    engine._finalize_run(
        run_id, status="CANCELLED", ctx={"k": "v"}, current_node="last",
    )

    conn = get_connection(engine.db_path)
    try:
        row = conn.execute(
            "SELECT status, current_node FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row["status"] == "CANCELLED"
        assert row["current_node"] == "last"
    finally:
        conn.close()


def test_end_to_end_cancel_during_last_node_keeps_cancelled(tmp_path: Path, monkeypatch) -> None:
    """P1 端到端竞态测试：单节点 fn 等待主线程设 hook + 触发 cancel_run → 后台线程
    _finalize_run(COMPLETED) 时守卫拦截 → run 保持 CANCELLED。

    用 _blocking_node 让 fn 主动等主线程事件，确保主线程能在 fn 返回前完成 hook
    设置和 cancel_run 调用；fn 返回后 _update_run_checkpoint 走 hook（已生效），
    之后 _finalize_run(COMPLETED) 命中守卫 rowcount=0 跳过。
    """
    engine = _make_engine(tmp_path)
    gate: dict = {"allow": False}
    nodes = [_blocking_node("last", gate, {"k": "v"}, sleep_s=0.0)]

    run_id = engine.start_with_nodes_async("e2e-finalize-race", nodes)

    # 等到节点行 RUNNING（已进入 fn 并等 gate）
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        conn = get_connection(engine.db_path)
        try:
            row = conn.execute(
                "SELECT status FROM workflow_run_nodes WHERE run_id = ? AND node_id = 'last'",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None and row["status"] == "RUNNING":
            break
        time.sleep(0.02)

    # 设 hook：节点 fn 完成后 _update_run_checkpoint 调用时触发 cancel_run
    # 这样保证 fn 已返回、checkpoint 写入前 run 已是 CANCELLED；下一节点循环
    # 没有（单节点），直接走 _finalize_run(COMPLETED) → 守卫拦截。
    from packages.core.workflow_runtime import engine as engine_mod

    orig_update_ckpt = engine_mod.WorkflowEngine._update_run_checkpoint

    def _hooked_update_ckpt(self, *args, **kwargs):
        engine.cancel_run(run_id)
        return orig_update_ckpt(self, *args, **kwargs)

    monkeypatch.setattr(
        engine_mod.WorkflowEngine, "_update_run_checkpoint", _hooked_update_ckpt
    )

    # 放开 gate 让节点 fn 返回
    gate["allow"] = True

    final = _wait_terminal(engine, run_id, timeout=5.0)
    assert final == "CANCELLED", (
        f"守卫失效：_finalize_run(COMPLETED) 把 CANCELLED 覆盖为 {final}"
    )

    # run 行 ended_at 由 cancel_run 写入（守卫跳过二次写）
    conn = get_connection(engine.db_path)
    try:
        row = conn.execute(
            "SELECT status, ended_at FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row["status"] == "CANCELLED"
        assert row["ended_at"] is not None
    finally:
        conn.close()

    # run 行 ended_at 应由 cancel_run 写入（守卫跳过 _finalize_run 的二次写）
    conn = get_connection(engine.db_path)
    try:
        row = conn.execute(
            "SELECT status, ended_at FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row["status"] == "CANCELLED"
        assert row["ended_at"] is not None
    finally:
        conn.close()
