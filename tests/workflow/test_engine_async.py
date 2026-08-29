"""WorkflowEngine 异步 API 测试（Sprint P0 异步化）。

覆盖：
- start_with_nodes_async 立即返回；run 行 status=RUNNING；后台线程最终推到 COMPLETED。
- start_with_nodes_async 在第一个节点 PauseRequested 时后台线程推到 PAUSED。
- resume_async 立即返回；PAUSED run 经后台线程恢复后推到终态。
- 同步版 start_with_nodes / resume 行为不变（仍阻塞到终态），保证脚本/测试/project-init 继续可用。
- _run_nodes_safe 兜底：节点异常已被引擎内部收尾为 FAILED（不外溢）。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations
from packages.core.workflow_runtime.engine import (
    PauseRequested,
    WorkflowEngine,
    WorkflowNode,
)


def _make_engine(tmp_path: Path) -> WorkflowEngine:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return WorkflowEngine(settings.db_path)


async def _wait_for_terminal(engine: WorkflowEngine, run_id: str, *, timeout: float = 10.0) -> str:
    """轮询 run 行直到 status ∈ {COMPLETED, PAUSED, FAILED, CANCELLED}；返回终态。"""
    from packages.core.workflow_runtime.runs import get_run

    deadline = time.monotonic() + timeout
    terminal = {"COMPLETED", "PAUSED", "FAILED", "CANCELLED"}
    while time.monotonic() < deadline:
        run = get_run(engine.db_path, run_id)
        if run is None:
            raise AssertionError(f"run {run_id} disappeared")
        if run["status"] in terminal:
            return run["status"]
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach terminal within {timeout}s")


def _ai_node(_name: str, payload: dict) -> WorkflowNode:
    """构造一个简单 AI 节点：fn 返回 payload。"""

    def fn(_ctx):
        return payload

    return WorkflowNode(node_id=_name, kind="AI", fn=fn)


def _pause_node(_name: str, payload: dict) -> WorkflowNode:
    """构造一个 Human 节点：fn 抛 PauseRequested。"""

    def fn(_ctx):
        raise PauseRequested(payload)

    return WorkflowNode(node_id=_name, kind="Human", fn=fn)


def _failing_node(_name: str, msg: str) -> WorkflowNode:
    """构造一个 State 节点：fn 抛通用异常（引擎会收尾为 FAILED）。"""

    def fn(_ctx):
        raise RuntimeError(msg)

    return WorkflowNode(node_id=_name, kind="State", fn=fn)


# ---------------------------------------------------------------------------
# sync 路径行为不变（DoD 之一）
# ---------------------------------------------------------------------------


def test_start_with_nodes_sync_still_blocks_to_terminal(tmp_path: Path) -> None:
    """同步 start_with_nodes 仍阻塞到终态（脚本/测试/project-init 继续用）。"""
    engine = _make_engine(tmp_path)
    nodes = [_ai_node("n1", {"k": "v"})]

    t0 = time.monotonic()
    run_id = engine.start_with_nodes("sync-still-blocks", nodes)
    elapsed = time.monotonic() - t0

    # 阻塞到终态：函数返回时 run.status 已是 COMPLETED
    from packages.core.workflow_runtime.runs import get_run

    run = get_run(engine.db_path, run_id)
    assert run["status"] == "COMPLETED"
    # 阻塞 ≠ 异步等待：单节点无 sleep 时阻塞时长应远小于异步超时阈值（0.5s）
    assert elapsed < 0.5


def test_resume_sync_still_blocks_to_terminal(tmp_path: Path) -> None:
    """同步 resume 仍阻塞到终态。"""
    engine = _make_engine(tmp_path)
    nodes = [_ai_node("n1", {"a": 1}), _pause_node("n2", {"q": "ok"})]
    run_id = engine.start_with_nodes("sync-resume-blocks", nodes)

    from packages.core.workflow_runtime.runs import get_run

    assert get_run(engine.db_path, run_id)["status"] == "PAUSED"

    nodes_for_resume = [_ai_node("n1", {"a": 1}), _ai_node("n2", {"q": "ok"})]
    t0 = time.monotonic()
    engine.resume(run_id, nodes_for_resume)
    elapsed = time.monotonic() - t0

    assert get_run(engine.db_path, run_id)["status"] == "COMPLETED"
    assert elapsed < 0.5


# ---------------------------------------------------------------------------
# async 路径：start_with_nodes_async
# ---------------------------------------------------------------------------


def test_start_with_nodes_async_returns_immediately(tmp_path: Path) -> None:
    """async 立即返回：函数返回时 run.status=RUNNING，行已落库。"""
    engine = _make_engine(tmp_path)
    # 用一个会睡一点的节点来放大"同步 vs 异步"差距；单节点本身无 sleep
    nodes = [_ai_node("n1", {"k": "v"})]

    t0 = time.monotonic()
    run_id = engine.start_with_nodes_async("async-immediate", nodes)
    elapsed = time.monotonic() - t0

    # 立即返回（远小于异步化前一个真实节点的执行时长；这里节点本身就很快）
    assert elapsed < 0.3, f"async start took {elapsed}s, should be < 0.3s"

    from packages.core.workflow_runtime.runs import get_run

    run = get_run(engine.db_path, run_id)
    assert run["status"] == "RUNNING", f"expected RUNNING right after start, got {run['status']}"


def test_start_with_nodes_async_completes_in_background(tmp_path: Path) -> None:
    """async 启动后后台线程最终推到 COMPLETED。"""
    engine = _make_engine(tmp_path)
    nodes = [_ai_node("n1", {"k": "v"}), _ai_node("n2", {"k2": "v2"})]

    run_id = engine.start_with_nodes_async("async-completes", nodes)
    final = asyncio.run(_wait_for_terminal(engine, run_id, timeout=10.0))
    assert final == "COMPLETED"


def test_start_with_nodes_async_pause_in_background(tmp_path: Path) -> None:
    """async 启动后第一个节点 PauseRequested → 后台线程推到 PAUSED。"""
    engine = _make_engine(tmp_path)
    nodes = [_pause_node("human", {"q": "approve?"}), _ai_node("n2", {})]

    run_id = engine.start_with_nodes_async("async-pause", nodes)
    final = asyncio.run(_wait_for_terminal(engine, run_id, timeout=10.0))
    assert final == "PAUSED"


def test_start_with_nodes_async_failure_is_finalized(tmp_path: Path) -> None:
    """async 启动后节点异常 → 后台线程把 run 收尾为 FAILED（不外溢到调用线程）。"""
    engine = _make_engine(tmp_path)
    nodes = [_failing_node("boom", "kaboom")]

    run_id = engine.start_with_nodes_async("async-fail", nodes)
    final = asyncio.run(_wait_for_terminal(engine, run_id, timeout=10.0))
    assert final == "FAILED"


# ---------------------------------------------------------------------------
# async 路径：resume_async
# ---------------------------------------------------------------------------


def test_resume_async_returns_immediately(tmp_path: Path) -> None:
    """async resume 立即返回：挂起点之后的慢节点在后台推进，返回时 run 仍是 RUNNING。

    关键构造：慢节点必须放在挂起点**之后**（resume 从 current_node 的下一位开始），
    否则慢节点根本不会被执行，run 瞬间终态，测试退化为线程调度竞速（偶发假绿）。
    断言用节点自身 sleep 当时钟（返回时 run 仍 RUNNING），不测墙钟——
    全量套件高负载下墙钟断言受环境噪声影响不稳定。
    """
    engine = _make_engine(tmp_path)
    # 先用同步 start_with_nodes 制造 PAUSED run（挂在 n2）
    nodes = [_ai_node("n1", {"a": 1}), _pause_node("n2", {"q": "ok"})]
    run_id = engine.start_with_nodes("resume-async-prepare", nodes)

    from packages.core.workflow_runtime.runs import get_run

    assert get_run(engine.db_path, run_id)["status"] == "PAUSED"

    # 挂起点之后的慢节点：sleep 500ms。若 resume_async 同步执行，调用方至少
    # 等 500ms 才返回；异步则应立即返回且此刻 run 仍是 RUNNING。
    sleep_ms = 500

    def _slow_fn(_ctx):
        time.sleep(sleep_ms / 1000)
        return {}

    nodes_for_resume = [
        _ai_node("n1", {"a": 1}),
        _ai_node("n2", {"q": "ok"}),
        WorkflowNode(node_id="n3", kind="State", fn=_slow_fn),
    ]
    t0 = time.monotonic()
    engine.resume_async(run_id, nodes_for_resume, human_input={"approved": True})
    elapsed = time.monotonic() - t0

    # 立即返回的确定性断言：返回时后台慢节点（sleep 500ms）必然尚未跑完，
    # run 行必须仍是 RUNNING。
    assert elapsed < sleep_ms / 1000, (
        f"async resume took {elapsed}s >= node sleep {sleep_ms}ms；疑似同步执行"
    )
    status_now = get_run(engine.db_path, run_id)["status"]
    assert status_now == "RUNNING", (
        f"resume_async 返回时 run 已是 {status_now}；未实现立即返回"
    )

    # 等到终态避免线程悬挂
    final = asyncio.run(_wait_for_terminal(engine, run_id, timeout=10.0))
    assert final == "COMPLETED"


def test_resume_async_completes_in_background(tmp_path: Path) -> None:
    """async resume 后台线程推到 COMPLETED。"""
    engine = _make_engine(tmp_path)
    nodes = [_ai_node("n1", {"a": 1}), _pause_node("n2", {"q": "ok"})]
    run_id = engine.start_with_nodes("resume-async-bg", nodes)

    nodes_for_resume = [_ai_node("n1", {"a": 1}), _ai_node("n2", {"q": "ok"})]
    engine.resume_async(run_id, nodes_for_resume, human_input={"approved": True})

    final = asyncio.run(_wait_for_terminal(engine, run_id, timeout=10.0))
    assert final == "COMPLETED"


def test_resume_async_validates_paused_status(tmp_path: Path) -> None:
    """async resume 在调用线程同步校验 PAUSED 状态——非 PAUSED → ValueError，**不启动后台线程**。"""
    engine = _make_engine(tmp_path)
    # 先造一个非 PAUSED 的 RUNNING 状态的 run（用同步 start_with_nodes 但节点很快——终态变 COMPLETED）
    nodes = [_ai_node("n1", {"k": "v"})]
    run_id = engine.start_with_nodes("resume-async-validate", nodes)

    from packages.core.workflow_runtime.runs import get_run

    assert get_run(engine.db_path, run_id)["status"] == "COMPLETED"

    # async resume 应该抛 ValueError（status 非 PAUSED），且不应启动后台线程
    try:
        engine.resume_async(run_id, nodes)
    except ValueError as exc:
        assert "must be PAUSED" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-PAUSED run")

    # run 行状态不变（COMPLETED），无副作用
    assert get_run(engine.db_path, run_id)["status"] == "COMPLETED"


def test_resume_async_rejects_unknown_run(tmp_path: Path) -> None:
    """async resume 对未知 run_id 抛 ValueError。"""
    engine = _make_engine(tmp_path)
    nodes = [_ai_node("n1", {"k": "v"})]
    try:
        engine.resume_async("wfr_does_not_exist", nodes)
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown run")
