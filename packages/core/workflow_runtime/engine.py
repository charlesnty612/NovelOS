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
import threading
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


def _scrub_ctx_for_checkpoint(
    ctx: dict[str, Any], exclude: list[str] | None
) -> dict[str, Any]:
    """对 ctx 做浅拷贝并剔除 ``exclude`` 顶层键；用于落盘，避免敏感数据进
    ``workflow_runs.checkpoint_json``。

    非 dict ctx / ``exclude`` 为空/None → 原样返回（调用方兼容性）。
    """
    if not exclude or not isinstance(ctx, dict):
        return ctx
    scrubbed = {k: v for k, v in ctx.items() if k not in exclude}
    return scrubbed


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

        必须传入 ``nodes`` 的方式：调用方负责从
        :mod:`packages.core.workflow_registry`（按 workflow_name）查到
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
        checkpoint_exclude: list[str] | None = None,
    ) -> str:
        """启动并顺序执行 nodes 列表；返回 run_id。

        完整执行直到：
        - 全部节点完成 → run.status=COMPLETED。
        - 任意节点抛 :class:`PauseRequested` → 节点行 PENDING、run PAUSED、checkpoint 落盘、返回 run_id。
        - 任意节点抛其他异常 → 节点行 FAILED、run FAILED、checkpoint 落盘、返回 run_id。

        ``checkpoint_exclude``（可选）：落盘到 ``workflow_runs.checkpoint_json`` 之前剔除的 ctx
        顶层键列表（浅拷贝，不修改内存 ctx）。用于硬边界：参考书原文 ``ctx["text"]`` 等敏感
        数据不应落 workflow_runs / Pause 后被 audit export。
        """
        if not nodes:
            raise ValueError("nodes must be a non-empty list")

        ctx: dict[str, Any] = dict(initial_ctx or {})
        if mock_providers is not None:
            ctx["mock_providers"] = mock_providers
        self._checkpoint_exclude = list(checkpoint_exclude or [])

        run_id = self._insert_run_row(
            workflow_name=workflow_name,
            chapter_id=chapter_id,
        )

        self._run_nodes(
            run_id=run_id,
            nodes=nodes,
            ctx=ctx,
            start_index=0,
        )
        return run_id

    # -------------------------------------------------------------- start_with_nodes_async
    def start_with_nodes_async(
        self,
        workflow_name: str,
        nodes: list[WorkflowNode],
        *,
        chapter_id: str | None = None,
        initial_ctx: dict[str, Any] | None = None,
        mock_providers: dict[str, list[str]] | None = None,
        checkpoint_exclude: list[str] | None = None,
    ) -> str:
        """异步版 :meth:`start_with_nodes`：插入 RUNNING 行后立刻返回 run_id，执行在后台线程推进。

        设计要点：
        - 调用线程不做任何节点执行，只负责插行 + 启动 daemon thread 后立即返回。
        - 后台线程复用 :meth:`_run_nodes`（同一份异常/PAUSE/FAIL 收尾逻辑），不再包 try。
        - SQLite 跨线程安全由 ``packages.core.db.get_connection``（每次新建 + WAL +
          busy_timeout）保证；observer 双腿并行已在生产验证同模式。
        - ``_run_nodes`` 抛出的任何异常都会被引擎层（_finalize_run 等）兜底写 FAILED，
          若仍有外溢异常则在线程内 logging.exception 留痕，不二次写库。
        """
        if not nodes:
            raise ValueError("nodes must be a non-empty list")

        ctx: dict[str, Any] = dict(initial_ctx or {})
        if mock_providers is not None:
            ctx["mock_providers"] = mock_providers
        self._checkpoint_exclude = list(checkpoint_exclude or [])

        run_id = self._insert_run_row(
            workflow_name=workflow_name,
            chapter_id=chapter_id,
        )

        thread = threading.Thread(
            target=self._run_nodes_safe,
            kwargs={
                "run_id": run_id,
                "nodes": nodes,
                "ctx": ctx,
                "start_index": 0,
            },
            name=f"workflow-{run_id}",
            daemon=True,
        )
        thread.start()
        return run_id

    # -------------------------------------------------------------- internal: _insert_run_row
    def _insert_run_row(
        self,
        *,
        workflow_name: str,
        chapter_id: str | None,
    ) -> str:
        """确保 workflows 行 + 插入 workflow_runs 行（status=RUNNING）；返回 run_id。

        供 :meth:`start_with_nodes`（同步）与 :meth:`start_with_nodes_async`（异步）
        共用，保持两者落库口径一致。
        """
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
        return run_id

    def _run_nodes_safe(
        self,
        *,
        run_id: str,
        nodes: list[WorkflowNode],
        ctx: dict[str, Any],
        start_index: int,
    ) -> None:
        """异步线程入口：包一层 try/except，捕获 _run_nodes 之外可能外溢的异常。

        _run_nodes 内部已经把 PauseRequested / BaseException 收尾为 PAUSED/FAILED，
        行状态由引擎内部维护，线程内不再二次写库——异常外溢仅作 logging 留痕。
        """
        try:
            self._run_nodes(
                run_id=run_id,
                nodes=nodes,
                ctx=ctx,
                start_index=start_index,
            )
        except BaseException:  # noqa: BLE001
            log.exception("workflow run %s background thread crashed", run_id)

    # -------------------------------------------------------------- resume
    def resume(
        self,
        run_id: str,
        nodes: list[WorkflowNode],
        *,
        human_input: dict[str, Any] | None = None,
        regenerate: bool = False,
    ) -> str:
        """从最近一次 PAUSED 状态恢复执行；human_input 并入 ctx["human_input"]。

        ``regenerate=True`` 时从挂起节点本身重跑（而非其下一节点），用于「带意见
        重新生成当前关」。此时不会把上一轮的 PENDING 行收尾为 SKIPPED，让审计链
        保留两次执行（首次+重生成）。

        返回 run_id（同入参）。如无 PAUSED run 或 run 已结束 → 抛 ValueError。
        """
        run = _get_run(self.db_path, run_id)
        if run is None:
            raise ValueError(f"workflow run {run_id!r} not found")
        if run["status"] != "PAUSED":
            raise ValueError(
                f"workflow run {run_id!r} status={run['status']!r}, must be PAUSED to resume"
            )

        ctx, start_index = self._prepare_resume_ctx(
            run=run,
            nodes=nodes,
            human_input=human_input,
            regenerate=regenerate,
        )

        # 与 resume_async 同口径：恢复执行期 run 行为 RUNNING（见 _mark_run_running 注释）
        self._mark_run_running(run_id)

        self._run_nodes(
            run_id=run_id,
            nodes=nodes,
            ctx=ctx,
            start_index=start_index,
        )
        return run_id

    # -------------------------------------------------------------- resume_async
    def resume_async(
        self,
        run_id: str,
        nodes: list[WorkflowNode],
        *,
        human_input: dict[str, Any] | None = None,
        regenerate: bool = False,
    ) -> str:
        """异步版 :meth:`resume`：参数校验在调用线程同步做，剩余执行在后台线程推进。

        设计要点：
        - 调用线程做必要的 PAUSED 校验（便于调用方 raise HTTPException）；校验失败抛
          ValueError，**不启动后台线程**。
        - 校验通过后启动 daemon thread 跑 ``_run_nodes_safe``，立即返回 run_id。
        - ``_skip_pending_node_rows`` 等收尾副作用放到后台线程里跑——它本身就是
          :meth:`_run_nodes` 之前必须做的 DB 写，不会影响 API 响应即时性。
        """
        run = _get_run(self.db_path, run_id)
        if run is None:
            raise ValueError(f"workflow run {run_id!r} not found")
        if run["status"] != "PAUSED":
            raise ValueError(
                f"workflow run {run_id!r} status={run['status']!r}, must be PAUSED to resume"
            )

        ctx, start_index = self._prepare_resume_ctx(
            run=run,
            nodes=nodes,
            human_input=human_input,
            regenerate=regenerate,
        )

        # 恢复执行前把 run 行置回 RUNNING：resume 执行期间 run 不再是「已暂停」，
        # 轮询方（前端 2s 轮询 / auto_revise 等待环）据此继续跟踪直到终态。
        # 同步时代无此翻转——执行期行一直标 PAUSED，端点阻塞到终态无人察觉；
        # 异步化后 PAUSED 会让轮询方误停，必须在调用线程同步翻转。
        self._mark_run_running(run_id)

        thread = threading.Thread(
            target=self._run_nodes_safe,
            kwargs={
                "run_id": run_id,
                "nodes": nodes,
                "ctx": ctx,
                "start_index": start_index,
            },
            name=f"workflow-resume-{run_id}",
            daemon=True,
        )
        thread.start()
        return run_id

    # -------------------------------------------------------------- internal: _prepare_resume_ctx
    def _prepare_resume_ctx(
        self,
        *,
        run: dict[str, Any],
        nodes: list[WorkflowNode],
        human_input: dict[str, Any] | None,
        regenerate: bool,
    ) -> tuple[dict[str, Any], int]:
        """构造 resume 用的 ctx + start_index。

        抽离自 :meth:`resume`，供 :meth:`resume_async` 复用，保证两条路径（同步/异步）
        的 ctx 拼装、``human_input`` 合并、``regenerate_stage`` 标记写入、
        ``_skip_pending_node_rows`` 副作用口径完全一致。
        """
        ctx: dict[str, Any] = dict(run.get("checkpoint_json") or {})
        ctx.setdefault("human_input", {})
        if human_input is not None:
            existing = ctx.get("human_input") or {}
            if isinstance(existing, dict):
                existing.update(human_input)
            else:
                existing = human_input
            ctx["human_input"] = existing

        # 定位 current_node 在 nodes 列表中的 index。
        # 默认从 index+1 开始（正常推进）；regenerate=True 时从 index 本身重跑当前挂起节点。
        current = run.get("current_node")
        start_index = 0
        matched_index = -1
        for i, node in enumerate(nodes):
            if node.node_id == current:
                matched_index = i
                start_index = i + 1
                break

        if regenerate and matched_index >= 0:
            start_index = matched_index
            # 在 ctx 写入标记：让 pipeline 的 _resolve_stage_input 知道
            # 当前正在重跑哪个关，以便跳过自身 revisions 优先层
            ctx["regenerate_stage"] = current
            # regenerate 不收尾旧 PENDING 行——保留审计链（首次+重生成两条节点行）
        else:
            # 收尾旧的 PENDING 节点行（标记 SKIPPED），便于审计
            self._skip_pending_node_rows(run["run_id"], current)

        return ctx, start_index

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
        scrubbed = _scrub_ctx_for_checkpoint(ctx, getattr(self, "_checkpoint_exclude", None))
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                UPDATE workflow_runs
                SET checkpoint_json = ?, current_node = ?
                WHERE run_id = ?
                """,
                (_dump_json(scrubbed), current_node, run_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _mark_run_running(self, run_id: str) -> None:
        """resume 接受后把 run 行置回 RUNNING（清 ended_at；error 留待终态覆盖）。

        同步时代 resume 执行期间行一直标 PAUSED（端点阻塞无人察觉）；异步化后
        轮询方依赖 status 区分「已暂停待审批」与「恢复执行中」，必须翻转。
        """
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                "UPDATE workflow_runs SET status = 'RUNNING', ended_at = NULL WHERE run_id = ?",
                (run_id,),
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
        scrubbed = _scrub_ctx_for_checkpoint(ctx or {}, getattr(self, "_checkpoint_exclude", None))
        conn = get_connection(self.db_path)
        try:
            if status in ("COMPLETED", "FAILED", "CANCELLED"):
                conn.execute(
                    """
                    UPDATE workflow_runs
                    SET status = ?, ended_at = ?, checkpoint_json = ?, current_node = ?, error = ?
                    WHERE run_id = ?
                    """,
                    (status, now_iso(), _dump_json(scrubbed), current_node, error, run_id),
                )
            else:  # PAUSED: 保留 ended_at = NULL（仍可 resume）
                conn.execute(
                    """
                    UPDATE workflow_runs
                    SET status = ?, checkpoint_json = ?, current_node = ?, error = ?
                    WHERE run_id = ?
                    """,
                    (status, _dump_json(scrubbed), current_node, error, run_id),
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
