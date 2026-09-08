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
- 启动自愈：:func:`recover_interrupted_runs` 在 lifespan startup 把残留
  ``status='RUNNING'`` 的 run 收尾为 FAILED（工作线程随进程死亡，单进程
  部署下启动瞬间不可能存在真正还在跑的 run）。PAUSED / 终态 run 不动。
- 协作式取消：:meth:`cancel_run` 把 RUNNING run 置为 CANCELLED（端点语义）。
  ``_run_nodes`` 节点循环**开始前**与**执行完毕 checkpoint 前**各查一次
  DB 状态：若已 CANCELLED 则停止推进——开始前命中时把刚 insert 的 RUNNING
  节点行收尾为 CANCELLED、剩余节点不再 insert；checkpoint 前命中时当前节点
  标 CANCELLED 且丢弃 output（不推进下游、不写 checkpoint）。LLM 节点不
  杀进程，让后台调用跑完结果丢弃即可（避免跨进程信号复杂度）。

设计要点：
- checkpoint_json 每节点完成后落盘；崩溃后 :meth:`resume` 从最近一个 COMPLETED 节点的
  下一个节点继续。
- AI 节点的 mock_script 从 ``ctx["mock_providers"][agent_name]`` 取（list 形式：第一次返回 [0]、
  耗尽重复末条，与 MockProvider 语义对齐）。
- run_id 在 start_with_nodes() 内生成并插入；所有 workflow_run_nodes 行同 run_id 关联。
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


# ---------------------------------------------------------------------------
# checkpoint 体积软上限（2026-09-06 审查批次二）
# ---------------------------------------------------------------------------

_CHECKPOINT_MAX_BYTES = 512 * 1024
"""checkpoint_json 序列化后的软上限（512KB）。超出时按顶层键体积从大到小
逐个替换为截断标记，直到回到上限内。

取舍：checkpoint 的恢复语义只服务「PAUSED 后 resume」与崩溃审计；PAUSED
落盘路径（``_finalize_run`` PAUSED 分支）**不做截断**，保证人工审阅 resume
拿到全量 ctx；崩溃恢复在单进程部署下由 ``recover_interrupted_runs`` 收尾为
FAILED（不可 resume），故每节点 checkpoint / 终态落盘可以安全截断。
"""

_CHECKPOINT_TRUNCATED_KEY = "__checkpoint_truncated__"
"""截断标记键；值携带 original_bytes 供审计估算被截掉的体积。"""

_CHECKPOINT_MIN_TRUNCATE_BYTES = 4096
"""小于该体积的顶层值不截断（控制流信号 / 小 payload 保真优先）。"""


def _cap_checkpoint_payload(
    scrubbed: Any, max_bytes: int = _CHECKPOINT_MAX_BYTES
) -> tuple[Any, int]:
    """对 scrub 后的 checkpoint 载荷做体积软上限，返回 ``(capped, truncated_n)``。

    - 序列化后 ≤ ``max_bytes`` → 原样返回（零拷贝，热路径零开销）。
    - 超出 → 顶层键按各自 JSON 体积从大到小逐个替换为
      ``{"__checkpoint_truncated__": True, "original_bytes": n}``，直到回到
      上限内；体积 ≤ ``_CHECKPOINT_MIN_TRUNCATE_BYTES`` 的小值不截。
    - 序列化失败（不可 JSON 值）→ 原样返回，不抛（checkpoint 落盘不能被
      软上限逻辑打断）。
    """
    if not isinstance(scrubbed, dict) or not scrubbed:
        return scrubbed, 0
    try:
        total = len(_dump_json(scrubbed).encode("utf-8"))
        if total <= max_bytes:
            return scrubbed, 0
        sized: list[tuple[str, int]] = []
        for k, v in scrubbed.items():
            try:
                n = len(_dump_json(v).encode("utf-8"))
            except (TypeError, ValueError):
                n = 0
            sized.append((k, n))
        capped = dict(scrubbed)
        truncated = 0
        for k, n in sorted(sized, key=lambda kv: kv[1], reverse=True):
            if total <= max_bytes or n <= _CHECKPOINT_MIN_TRUNCATE_BYTES:
                break
            marker = {_CHECKPOINT_TRUNCATED_KEY: True, "original_bytes": n}
            capped[k] = marker
            total = total - n + len(_dump_json(marker).encode("utf-8"))
            truncated += 1
        return capped, truncated
    except Exception:  # noqa: BLE001 —— 软上限失败不阻断 checkpoint 落盘
        return scrubbed, 0


class WorkflowRunConflict(Exception):
    """同 chapter 下已存在 RUNNING/PENDING run，启动被拒（409 语义）。

    设计要点：
    - 抛 ``sqlite3.IntegrityError`` 在 SQL 层由 0017 部分唯一索引
      ``idx_workflow_runs_active`` 兜底触发（覆盖 TOCTOU 窗口）。
    - engine 层捕获后转为业务异常，API 路由（``routers/workflows.py``）
      映射为 HTTP 409，与既有 ``_check_active_run_for_chapter`` 的 409
      同语义（避免 500/422 误导客户端）。
    """


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


def _fetch_run_status(db_path: str | Path, run_id: str) -> str | None:
    """单行 SELECT workflow_runs.status；run 不存在 → None。

    协作式取消的探针：节点循环开始前 / checkpoint 前各调一次，确认 run
    是否已被外部置为 CANCELLED。轻量、无锁；SQLite 跨线程由 WAL + busy_timeout
    保证。返回的 status 是 DB 权威值——内存里持有的旧快照不可信。
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    return row["status"] if row else None


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
            try:
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
            except sqlite3.IntegrityError as exc:
                # 0017 部分唯一索引 ``idx_workflow_runs_active`` 兜底 TOCTOU：
                # 双 start 窄窗口内第二个 INSERT 会被拒。转为业务异常供 API 层
                # 映射为 409（与 ``_check_active_run_for_chapter`` 同语义）。
                msg = str(exc)
                if "idx_workflow_runs_active" in msg or "UNIQUE" in msg.upper():
                    raise WorkflowRunConflict(
                        f"chapter {chapter_id!r} already has an active workflow run"
                    ) from exc
                raise
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

    # -------------------------------------------------------------- cancel_run
    def cancel_run(self, run_id: str) -> dict[str, str]:
        """协作式取消 RUNNING run（端点语义）。

        状态机：
        - run 不存在 → 抛 ``ValueError("workflow run ... not found")``。
          由 API 路由层映射为 HTTP 404。
        - status=RUNNING → UPDATE 为 CANCELLED（带 ended_at），返回
          ``{"run_id", "status": "CANCELLED", "previous_status": "RUNNING"}``。
        - status ∈ {COMPLETED, FAILED, CANCELLED, PAUSED} → 抛 ``ValueError``
          含 "must be RUNNING to cancel"，由 API 路由层映射为 HTTP 409。
          PAUSED run 的取消走 resume 后再 reject/approve 决议路径，不在本端点范围。

        协作式取消语义：仅 UPDATE run 行 + 写 ended_at；后台节点循环下一次
        探针（节点开始前 / checkpoint 前）查 DB 时命中 CANCELLED 即停止推进
        并把当前节点标 CANCELLED + 丢弃 output。LLM 节点不杀进程，让后台
        调用自然跑完、结果丢弃即可。
        """
        current = _fetch_run_status(self.db_path, run_id)
        if current is None:
            raise ValueError(f"workflow run {run_id!r} not found")
        if current != "RUNNING":
            raise ValueError(
                f"workflow run {run_id!r} status={current!r}, must be RUNNING to cancel"
            )

        conn = get_connection(self.db_path)
        try:
            # 单条 UPDATE：仅在 status='RUNNING' 时翻转（防御 TOCTOU：API 层
            # 校验后到此处窄窗口内 run 状态被改——节点异常/PAUSE/CANCELLED
            # 都可能——WHERE 兜底避免覆盖其他终态）。affected=0 → 不动。
            cur = conn.execute(
                """
                UPDATE workflow_runs
                SET status = 'CANCELLED', ended_at = ?
                WHERE run_id = ? AND status = 'RUNNING'
                """,
                (now_iso(), run_id),
            )
            if cur.rowcount == 0:
                # 竞态：UPDATE 未命中说明状态已被改（例如 _run_nodes 刚置 FAILED）
                # → 不覆盖，抛 409。
                raise ValueError(
                    f"workflow run {run_id!r} status changed concurrently; "
                    f"current status is no longer RUNNING"
                )
            conn.commit()
        finally:
            conn.close()

        log.info("workflow run %s cancelled via cancel_run", run_id)
        return {
            "run_id": run_id,
            "status": "CANCELLED",
            "previous_status": "RUNNING",
        }

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

            # 协作式取消探针 1（节点开始前）：run 已 CANCELLED → 把当前节点标
            # CANCELLED（不写 output）、剩余节点不再 insert、run 收尾 CANCELLED、
            # 正常 return（不抛错）。LLM 调用若已在跑，让它自然结束结果丢弃即可。
            if _fetch_run_status(self.db_path, run_id) == "CANCELLED":
                self._finalize_run(
                    run_id, status="CANCELLED", ctx=ctx, current_node=None
                )
                log.info(
                    "workflow run %s cancelled before node %s (start probe)",
                    run_id, node.node_id,
                )
                return

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
            # 即便最终被取消，节点 fn 仍可能已调过 LLM 拿到产出——探针 2 在
            # checkpoint 前丢弃该 output；此处先把 output 暂存 ctx 但不写
            # workflow_runs.checkpoint_json（探针 2 命中时跳过 _update_run_checkpoint）。
            # 保留 ctx 是为 COMPLETED 路径写 checkpoint 时不丢上下文。
            ctx[node.node_id] = output
            ctx.update(output)  # 顶层 key 直接 merge，便于跨节点引用

            latency = int((time.monotonic() - t0) * 1000)

            # 协作式取消探针 2（checkpoint 前）：run 已 CANCELLED → 节点标
            # FAILED（携带 error='cancelled by user' 区分真实失败）+ 丢弃
            # output（不写 output_json、不推进下游、不写 checkpoint）、run 收尾
            # CANCELLED、return。COMPLETED 路径不命中此分支时按既有口径写
            # COMPLETED + checkpoint。
            #
            # 节点行用 FAILED 而非 CANCELLED：workflow_run_nodes.status CHECK
            # 仅含 PENDING/RUNNING/COMPLETED/FAILED/SKIPPED（0001_init.sql L407），
            # 扩枚举需重建表会破坏 ai_call_logs FK 引用（FK 指向 wfrn 表名，
            # SQLite ALTER TABLE RENAME 不更新 FK 引用的表名），代价高于节点行
            # 复用 FAILED。error 字段足以在审计时区分「真失败」与「用户取消」。
            if _fetch_run_status(self.db_path, run_id) == "CANCELLED":
                self._update_node_row(
                    node_run_id,
                    status="FAILED",
                    output=None,
                    latency_ms=latency,
                    error="cancelled by user",
                )
                self._finalize_run(
                    run_id, status="CANCELLED", ctx=ctx, current_node=node.node_id
                )
                log.info(
                    "workflow run %s cancelled after node %s (checkpoint probe)",
                    run_id, node.node_id,
                )
                return

            self._update_node_row(
                node_run_id,
                status="COMPLETED",
                output=output,
                latency_ms=latency,
                error=None,
            )

            # 每节点完成后写 checkpoint + 更新 current_node（崩溃可恢复）
            self._update_run_checkpoint(run_id, ctx, current_node=node.node_id)

        # 全部节点完成 → COMPLETED（_finalize_run 不会覆盖 CANCELLED——status
        # 在终态集合里走 L665 同分支；防御性断言此时 run 必非终态）。
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
        capped, truncated_n = _cap_checkpoint_payload(scrubbed)
        if truncated_n:
            log.debug(
                "checkpoint capped for run %s: %d top-level values truncated",
                run_id, truncated_n,
            )
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                UPDATE workflow_runs
                SET checkpoint_json = ?, current_node = ?
                WHERE run_id = ?
                """,
                (_dump_json(capped), current_node, run_id),
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
        # PAUSED 落盘不截断（人工审阅 resume 需要全量 ctx）；其余终态做软上限
        if status == "PAUSED":
            capped, truncated_n = scrubbed, 0
        else:
            capped, truncated_n = _cap_checkpoint_payload(scrubbed)
        if truncated_n:
            log.debug(
                "final checkpoint capped for run %s (%s): %d values truncated",
                run_id, status, truncated_n,
            )
        conn = get_connection(self.db_path)
        try:
            if status == "CANCELLED":
                # CANCELLED 收尾无守卫：cancel_run 已置 CANCELLED + ended_at，此处
                # 幂等重写（status=CANCELLED / ended_at=now / checkpoint 落盘）无害。
                conn.execute(
                    """
                    UPDATE workflow_runs
                    SET status = ?, ended_at = ?, checkpoint_json = ?, current_node = ?, error = ?
                    WHERE run_id = ?
                    """,
                    (status, now_iso(), _dump_json(capped), current_node, error, run_id),
                )
            elif status in ("COMPLETED", "FAILED"):
                # 守卫：WHERE status='RUNNING'。场景——节点 fn 完成（或抛异常）后
                # _update_node_row / _update_run_checkpoint 之间的 5-20ms 窗口内，
                # cancel_run 已把 run 翻 CANCELLED 并返回 200。若此处无守卫，
                # 本 UPDATE 会把 CANCELLED 覆盖回 COMPLETED/FAILED，取消契约被击穿。
                # resume 路径已先 _mark_run_running 翻 RUNNING，不受影响；PAUSED
                # 走 else 分支不动。
                # rowcount=0 → run 已被外部置 CANCELLED，cancel_run 已写 ended_at，
                # 此处不写 ended_at 覆盖、静默跳过收尾；debug 日志便于审计。
                cur = conn.execute(
                    """
                    UPDATE workflow_runs
                    SET status = ?, ended_at = ?, checkpoint_json = ?, current_node = ?, error = ?
                    WHERE run_id = ? AND status = 'RUNNING'
                    """,
                    (status, now_iso(), _dump_json(capped), current_node, error, run_id),
                )
                if cur.rowcount == 0:
                    log.debug(
                        "_finalize_run skip: run %s status=%r (already not RUNNING); "
                        "likely cancelled during finalize window",
                        run_id, status,
                    )
            else:  # PAUSED: 保留 ended_at = NULL（仍可 resume）
                conn.execute(
                    """
                    UPDATE workflow_runs
                    SET status = ?, checkpoint_json = ?, current_node = ?, error = ?
                    WHERE run_id = ?
                    """,
                    (status, _dump_json(capped), current_node, error, run_id),
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


# ---------------------------------------------------------------------------
# Startup self-healing: 工作线程只活在进程内，进程重启后 DB 中残留的 RUNNING
# run 必为孤儿（永不推进），且会触发 409 唯一索引阻断同 chapter 的新 run。
# 单进程部署下，启动瞬间不可能存在真正还在跑的 RUNNING run → 一律按中断
# 收尾为 FAILED；PAUSED 不动（合法持久状态，等待人工 resume）。
# 此外，对历史已 FAILED/CANCELLED 但节点行仍 RUNNING/PENDING 的孤儿节点
# （run 收尾时漏掉节点行的产物），启动时一并清扫为 FAILED，避免永远推进。
# ---------------------------------------------------------------------------


def recover_interrupted_runs(db_path: Path | str) -> list[str]:
    """启动时把 ``workflow_runs`` 中所有 ``status='RUNNING'`` 的 run 收尾为 FAILED。

    收尾口径：
    - run 行：``status='FAILED'``, ``error='interrupted: service restart killed worker thread'``,
      ``ended_at=<UTC now ISO>``。
    - 该 run 下仍处于 ``RUNNING`` / ``PENDING`` 的节点行：``status='FAILED'``,
      ``error='interrupted by restart'``, ``ended_at=<UTC now ISO>``（已
      ``COMPLETED`` / ``FAILED`` / ``SKIPPED`` 的节点行保留原状，便于审计）。
    - 孤儿节点清扫：run 已处于 ``FAILED`` / ``CANCELLED`` 终态、但其下节点行
      仍为 ``RUNNING`` / ``PENDING`` 的僵尸节点，一并收尾为 FAILED（口径同上）。
      这类残留通常源于历史 bug：run 收尾逻辑漏掉节点行（例如本次启动前已
      收尾为 FAILED 的 run）；不处理会一直阻塞唯一索引、产生永远推进的孤儿。
      ``PAUSED`` run 的 ``PENDING`` 节点行是合法的人工审阅等待状态，绝不动；
      ``COMPLETED`` run 的 ``RUNNING`` 节点行视为矛盾数据，本次也不扫。
    - PAUSED / COMPLETED run 本身不动。

    返回受影响 run_id 列表（便于日志）；无受影响返回 ``[]``；任何异常
    ``log.warning`` 不抛——启动不能被它阻断。
    """
    db_path = str(Path(db_path).resolve()) if not str(db_path).startswith(":memory:") else str(db_path)
    conn = get_connection(db_path)
    try:
        # 1) 找出当前所有 RUNNING run_id（用于更新节点行 + 返回）
        rows = conn.execute(
            "SELECT run_id FROM workflow_runs WHERE status = 'RUNNING'"
        ).fetchall()
        run_ids: list[str] = [row["run_id"] for row in rows]
        now = now_iso()

        # 2) 收尾 RUNNING run 行（若有 RUNNING run）
        if run_ids:
            conn.execute(
                """
                UPDATE workflow_runs
                SET status = 'FAILED',
                    error = 'interrupted: service restart killed worker thread',
                    ended_at = ?
                WHERE status = 'RUNNING'
                """,
                (now,),
            )
            # 3) 收尾这些 run 下仍处于 RUNNING/PENDING 的节点行
            placeholders = ",".join("?" for _ in run_ids)
            conn.execute(
                f"""
                UPDATE workflow_run_nodes
                SET status = 'FAILED',
                    error = 'interrupted by restart',
                    ended_at = ?
                WHERE run_id IN ({placeholders})
                  AND status IN ('RUNNING', 'PENDING')
                """,
                (now, *run_ids),
            )

        # 4) 孤儿节点清扫：run 已 FAILED/CANCELLED 终态、但节点行仍
        # RUNNING/PENDING 的僵尸节点一并收尾。典型产物是历史已 FAILED 但
        # 当时漏掉节点行的 run（生产库中那条 scene_planner 永远 RUNNING 的
        # wfrn_fbc58dda2a70）。当前 RUNNING run 收尾后其下节点由步骤 3
        # 处理，步骤 4 对那些行是幂等空操作；放在最末确保即使无 RUNNING
        # run（启动时只有终态残留）也照样执行。PAUSED 不扫，COMPLETED 不扫。
        cur = conn.execute(
            """
            UPDATE workflow_run_nodes
            SET status = 'FAILED',
                error = 'interrupted by restart',
                ended_at = ?
            WHERE status IN ('RUNNING', 'PENDING')
              AND run_id IN (
                SELECT run_id FROM workflow_runs
                WHERE status IN ('FAILED', 'CANCELLED')
              )
            """,
            (now,),
        )
        swept = cur.rowcount
        if swept:
            log.info(
                "recover_interrupted_runs: 孤儿节点清扫收尾 %d 条残留 RUNNING/PENDING 节点行",
                swept,
            )
        conn.commit()
        return run_ids
    except Exception as exc:  # noqa: BLE001 —— 启动不能被自愈阻断
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        log.warning("recover_interrupted_runs failed (non-fatal): %s", exc)
        return []
    finally:
        conn.close()


__all__ = [
    "WorkflowEngine",
    "WorkflowNode",
    "PauseRequested",
    "recover_interrupted_runs",
    "_fetch_run_status",
    "_cap_checkpoint_payload",
    "_CHECKPOINT_MAX_BYTES",
    "_CHECKPOINT_TRUNCATED_KEY",
]
