"""Workflow Engine（Sprint 4-A）。

职责：
- :class:`WorkflowEngine` 按工作流定义顺序执行节点，把节点输出合并进 ctx 跨节点传递。
- 工作流定义 = 有序节点列表，每节点 ``{"node_id": str, "kind": ..., "fn": callable(ctx)->dict}``。
- 执行流程：插 workflow_runs（status=RUNNING）→ 顺序执行每节点（插 workflow_run_nodes 行）
  → 写 checkpoint_json + UPDATE current_node。
- Human 节点：fn 抛 :class:`PauseRequested` → 节点行 status=PENDING、run status=PAUSED、
  checkpoint 落盘、返回。
- :meth:`resume` 从 checkpoint 恢复 ctx，把 human_input 并入 ctx["human_input"]，
  从 current_node 下一节点继续。
- AI 节点 fn 内部调 :func:`packages.core.agent_runtime.runner.run_agent`（run_id/node_run_id 传入），
  ``mock_providers`` 参数（``{agent_name: [scripted_responses...]}``）透传给 run_agent 的 mock_script。
- 节点异常：节点行 FAILED + error，run FAILED，不重试（重试在 run_agent 内部）。

设计要点：
- checkpoint_json 每节点完成后落盘；崩溃后 :meth:`resume` 从最近一个 COMPLETED 节点的
  下一个节点继续。
- AI 节点的 mock_script 从 ``ctx["mock_providers"][agent_name]`` 取（list 形式：第一次返回 [0]、
  耗尽重复末条，与 MockProvider 语义对齐）。
- run_id 在 start() 内生成并插入；所有 workflow_run_nodes 行同 run_id 关联。
- pause/resume 场景下，PAUSED run 不再被任何新执行路径修改，仅 resume() 接手。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.logging_config import get_logger
from packages.core.workflow_runtime.runs import get_run as _get_run

log = get_logger("novelos.workflow_runtime")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PauseRequested(Exception):
    """Human 节点抛出以请求挂起；payload 通过 ``args[0]`` 传出供 API 返回。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("workflow paused for human input")
        self.payload = payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _parse_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _ensure_workflow(conn: sqlite3.Connection, name: str, version: str = "v1") -> str:
    """确保 workflows 表有 name 行；返回 workflow_id。"""
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
        VALUES (?, ?, ?, '{}', ?, ?)
        """,
        (wf_id, name, version, now, now),
    )
    return wf_id


def _get_agent_id(conn: sqlite3.Connection, agent_name: str | None) -> str | None:
    if not agent_name:
        return None
    row = conn.execute("SELECT agent_id FROM agents WHERE name = ?", (agent_name,)).fetchone()
    return row["agent_id"] if row else None


# ---------------------------------------------------------------------------
# WorkflowNode + Engine
# ---------------------------------------------------------------------------


class WorkflowNode:
    """工作流节点定义（in-memory）。"""

    def __init__(
        self,
        node_id: str,
        kind: str,
        fn: Callable[[dict[str, Any]], dict[str, Any]],
        agent_name: str | None = None,
    ) -> None:
        if kind not in {"AI", "State", "Transform", "Human", "Simulation"}:
            raise ValueError(f"unsupported node kind: {kind!r}")
        self.node_id = node_id
        self.kind = kind
        self.fn = fn
        # AI 节点可指定 agent_name（用于 workflow_run_nodes.agent_id 与 mock_providers 索引）
        self.agent_name = agent_name


class WorkflowEngine:
    """Workflow 执行器；构造接收 db_path，按工作流定义顺序执行节点。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(Path(db_path).resolve()) if not str(db_path).startswith(":memory:") else str(db_path)

    # -------------------------------------------------------------- ensure
    def ensure_workflow(self, name: str) -> str:
        """确保 ``workflows.name`` 行存在；返回 workflow_id。"""
        conn = get_connection(self.db_path)
        try:
            return _ensure_workflow(conn, name)
        finally:
            conn.close()

    # -------------------------------------------------------------- start
    def start(
        self,
        workflow_name: str,
        *,
        chapter_id: str | None = None,
        initial_ctx: dict[str, Any] | None = None,
        mock_providers: dict[str, list[str]] | None = None,
    ) -> str:
        """启动一个 workflow run。

        必须传入 ``nodes`` 的方式：调用方负责从业务流（packages.workflows.*）按 workflow_name 查到
        ``nodes: list[WorkflowNode]``，再调 :meth:`_run_nodes` 完成执行。本方法只负责 run 行与
        ctx 初始化，避免循环依赖。

        返回 ``run_id``。
        """
        raise NotImplementedError(
            "use start_with_nodes() instead; this entry point is reserved for future orchestration"
        )

    # -------------------------------------------------------------- start_with_nodes
    def start_with_nodes(
        self,
        workflow_name: str,
        nodes: list[WorkflowNode],
        *,
        chapter_id: str | None = None,
        initial_ctx: dict[str, Any] | None = None,
        mock_providers: dict[str, list[str]] | None = None,
    ) -> str:
        """启动并顺序执行 nodes 列表；返回 run_id。

        完整执行直到：
        - 全部节点完成 → run.status=COMPLETED。
        - 任意节点抛 :class:`PauseRequested` → 节点行 PENDING、run PAUSED、checkpoint 落盘、返回 run_id。
        - 任意节点抛其他异常 → 节点行 FAILED、run FAILED、checkpoint 落盘、返回 run_id。
        """
        if not nodes:
            raise ValueError("nodes must be a non-empty list")

        ctx: dict[str, Any] = dict(initial_ctx or {})
        if mock_providers is not None:
            ctx["mock_providers"] = mock_providers

        conn = get_connection(self.db_path)
        try:
            wf_id = _ensure_workflow(conn, workflow_name)
            run_id = new_id("wfr")
            now = now_iso()
            conn.execute(
                """
                INSERT INTO workflow_runs
                    (run_id, workflow_id, chapter_id, status, current_node,
                     checkpoint_json, error, retry_count, started_at, ended_at)
                VALUES (?, ?, ?, 'RUNNING', NULL, '{}', NULL, 0, ?, NULL)
                """,
                (run_id, wf_id, chapter_id, now),
            )
            conn.commit()
        finally:
            conn.close()

        self._run_nodes(
            run_id=run_id,
            nodes=nodes,
            ctx=ctx,
            start_index=0,
        )
        return run_id

    # -------------------------------------------------------------- resume
    def resume(
        self,
        run_id: str,
        nodes: list[WorkflowNode],
        *,
        human_input: dict[str, Any] | None = None,
    ) -> str:
        """从最近一次 PAUSED 状态恢复执行；human_input 并入 ctx["human_input"]。

        返回 run_id（同入参）。如无 PAUSED run 或 run 已结束 → 抛 ValueError。
        """
        run = _get_run(self.db_path, run_id)
        if run is None:
            raise ValueError(f"workflow run {run_id!r} not found")
        if run["status"] != "PAUSED":
            raise ValueError(
                f"workflow run {run_id!r} status={run['status']!r}, must be PAUSED to resume"
            )

        ctx: dict[str, Any] = dict(run.get("checkpoint_json") or {})
        ctx.setdefault("human_input", {})
        if human_input is not None:
            existing = ctx.get("human_input") or {}
            if isinstance(existing, dict):
                existing.update(human_input)
            else:
                existing = human_input
            ctx["human_input"] = existing

        # 定位 current_node 在 nodes 列表中的 index；从 index+1 开始执行
        current = run.get("current_node")
        start_index = 0
        for i, node in enumerate(nodes):
            if node.node_id == current:
                start_index = i + 1
                break

        # 收尾旧的 PENDING 节点行（标记 SKIPPED），便于审计
        self._skip_pending_node_rows(run_id, current)

        self._run_nodes(
            run_id=run_id,
            nodes=nodes,
            ctx=ctx,
            start_index=start_index,
        )
        return run_id

    # -------------------------------------------------------------- internal: _run_nodes
    def _run_nodes(
        self,
        *,
        run_id: str,
        nodes: list[WorkflowNode],
        ctx: dict[str, Any],
        start_index: int,
    ) -> None:
        """顺序执行 nodes[start_index:]；处理 PauseRequested / 异常 / checkpoint 落盘。"""
        for idx in range(start_index, len(nodes)):
            node = nodes[idx]
            node_run_id = self._insert_node_row(run_id, node, ctx)
            t0 = time.monotonic()

            # 注入当前节点 run_id 与 node_run_id 进 ctx（便于 AI 节点 fn 内部传给 run_agent）
            ctx["run_id"] = run_id
            ctx["_current_node_run_id"] = node_run_id
            ctx["_current_node_id"] = node.node_id

            try:
                output = node.fn(ctx)
            except PauseRequested as exc:
                # Human 节点挂起：节点行 PENDING、run PAUSED、checkpoint 落盘
                latency = int((time.monotonic() - t0) * 1000)
                payload = exc.payload if isinstance(exc.payload, dict) else {"payload": exc.payload}
                ctx[node.node_id] = {"__pause_payload__": payload}
                self._update_node_row(
                    node_run_id,
                    status="PENDING",
                    output=ctx[node.node_id],
                    latency_ms=latency,
                    error=None,
                )
                self._finalize_run(run_id, status="PAUSED", ctx=ctx, current_node=node.node_id)
                log.info("workflow run %s paused at node %s", run_id, node.node_id)
                return
            except BaseException as exc:  # noqa: BLE001
                # 节点异常：节点行 FAILED、run FAILED
                latency = int((time.monotonic() - t0) * 1000)
                self._update_node_row(
                    node_run_id,
                    status="FAILED",
                    output=None,
                    latency_ms=latency,
                    error=f"{type(exc).__name__}: {exc}",
                )
                self._finalize_run(run_id, status="FAILED", ctx=ctx, current_node=node.node_id, error=str(exc))
                log.warning("workflow run %s failed at node %s: %s", run_id, node.node_id, exc)
                return

            # 节点成功：合并输出进 ctx
            if output is None:
                output = {}
            ctx[node.node_id] = output
            ctx.update(output)  # 顶层 key 直接 merge，便于跨节点引用

            latency = int((time.monotonic() - t0) * 1000)
            self._update_node_row(
                node_run_id,
                status="COMPLETED",
                output=output,
                latency_ms=latency,
                error=None,
            )

            # 每节点完成后写 checkpoint + 更新 current_node（崩溃可恢复）
            self._update_run_checkpoint(run_id, ctx, current_node=node.node_id)

        # 全部节点完成 → COMPLETED
        self._finalize_run(run_id, status="COMPLETED", ctx=ctx, current_node=None)

    # -------------------------------------------------------------- internal: DB helpers
    def _insert_node_row(
        self,
        run_id: str,
        node: WorkflowNode,
        ctx: dict[str, Any],
    ) -> str:
        node_run_id = new_id("wfrn")
        started_at = now_iso()
        agent_id = _get_agent_id(get_connection(self.db_path), node.agent_name) if node.agent_name else None
        # 把传入 ctx 序列化进 input_json（注意：避开 mock_providers 以防超长，仅写关键 key）
        input_payload = {
            "chapter_id": ctx.get("chapter_id"),
            "human_input": ctx.get("human_input"),
            "available_keys": sorted([k for k in ctx.keys() if k != "mock_providers"]),
        }
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO workflow_run_nodes
                    (node_run_id, run_id, node_id, agent_id, status,
                     input_json, output_json, prompt_version, model_id,
                     token_usage_json, latency_ms, error, started_at, ended_at)
                VALUES (?, ?, ?, ?, 'RUNNING', ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, NULL)
                """,
                (
                    node_run_id,
                    run_id,
                    node.node_id,
                    agent_id,
                    _dump_json(input_payload),
                    started_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return node_run_id

    def _update_node_row(
        self,
        node_run_id: str,
        *,
        status: str,
        output: dict[str, Any] | None,
        latency_ms: int,
        error: str | None,
    ) -> None:
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                UPDATE workflow_run_nodes
                SET status = ?, output_json = ?, latency_ms = ?, error = ?, ended_at = ?
                WHERE node_run_id = ?
                """,
                (status, _dump_json(output) if output is not None else None, latency_ms, error, now_iso(), node_run_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _update_run_checkpoint(
        self,
        run_id: str,
        ctx: dict[str, Any],
        *,
        current_node: str,
    ) -> None:
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                UPDATE workflow_runs
                SET checkpoint_json = ?, current_node = ?
                WHERE run_id = ?
                """,
                (_dump_json(ctx), current_node, run_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        ctx: dict[str, Any] | None,
        current_node: str | None,
        error: str | None = None,
    ) -> None:
        conn = get_connection(self.db_path)
        try:
            if status in ("COMPLETED", "FAILED", "CANCELLED"):
                conn.execute(
                    """
                    UPDATE workflow_runs
                    SET status = ?, ended_at = ?, checkpoint_json = ?, current_node = ?, error = ?
                    WHERE run_id = ?
                    """,
                    (status, now_iso(), _dump_json(ctx or {}), current_node, error, run_id),
                )
            else:  # PAUSED: 保留 ended_at = NULL（仍可 resume）
                conn.execute(
                    """
                    UPDATE workflow_runs
                    SET status = ?, checkpoint_json = ?, current_node = ?, error = ?
                    WHERE run_id = ?
                    """,
                    (status, _dump_json(ctx or {}), current_node, error, run_id),
                )
            conn.commit()
        finally:
            conn.close()

    def _skip_pending_node_rows(self, run_id: str, current_node: str | None) -> None:
        """resume 时把上一次的 PENDING 行收尾为 SKIPPED，便于审计。"""
        if not current_node:
            return
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                UPDATE workflow_run_nodes
                SET status = 'SKIPPED', ended_at = ?, error = 'resumed'
                WHERE run_id = ? AND node_id = ? AND status = 'PENDING'
                """,
                (now_iso(), run_id, current_node),
            )
            conn.commit()
        finally:
            conn.close()


__all__ = ["WorkflowEngine", "WorkflowNode", "PauseRequested"]
