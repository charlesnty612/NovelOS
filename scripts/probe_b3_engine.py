"""B-3 编排引擎与启动入口实证探针（2026-11-04 B 档逐条实证批）。

覆盖（审计报告 §四 B-3）：
- B3-1 ``_mark_run_running`` 的 UPDATE 无状态条件（resume TOCTOU 的机械前提）。
- B3-2 ``_insert_node_row`` 内联 ``get_connection`` 不 close ⇒ 连接泄漏
  （预期**推翻**：CPython 引用计数即时析构并释放句柄，非真泄漏）。
- B3-3 节点成功但 ctx 不可序列化 ⇒ checkpoint 写入抛错 ⇒ run 永久 RUNNING。
- B3-4 ``_resolve_checkpoint_exclude`` 对未注册 workflow 静默返回 []。
- B3-5 ``_prepare_resume_ctx`` 找不到 current_node ⇒ start_index=0 从头重跑。

只写仓外临时目录；零真实 LLM 调用。
"""

from __future__ import annotations

import gc
import json

from probe_b_common import (  # noqa: I001  # pylint: disable=wrong-import-order
    Recorder,
    force_utf8_stdout,
    make_temp_db,
    verify_worktree_import,
)

force_utf8_stdout()
verify_worktree_import()

from packages.core.db import get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402
from packages.core.workflow_runtime.engine import (  # noqa: E402
    WorkflowEngine,
    WorkflowNode,
)


def _run_status(db_path: str, run_id: str) -> str | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    return row["status"] if row else None


def _insert_bare_run(db_path: str, *, status: str, chapter_id: str | None) -> str:
    run_id, wf_id = new_id("wfr"), new_id("wf")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES (?, 'probe-wf', 'v1', '{}', ?, ?)",
            (wf_id, now, now),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, "
            "current_node, checkpoint_json, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, NULL, '{}', ?, ?)",
            (run_id, wf_id, chapter_id, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def main() -> int:
    rec = Recorder("B-3 编排引擎与启动入口")
    db_path, tmp = make_temp_db("b3")
    engine = WorkflowEngine(db_path)

    # ---------------------------------------------------------------- B3-1
    run_done = _insert_bare_run(db_path, status="COMPLETED", chapter_id=None)
    engine._mark_run_running(run_done)  # noqa: SLF001 —— 探针直测判定口径
    status_after = _run_status(db_path, run_done)
    rec.confirm(
        "B3-1 _mark_run_running 的 UPDATE 无状态条件（非 PAUSED 也能翻 RUNNING）",
        f"status=COMPLETED 的 run 实测被翻成 {status_after!r}"
        f"（UPDATE ... WHERE run_id=? 无 AND status='PAUSED'；0017 部分唯一索引"
        f"只拦「同章第二条活跃 run」，拦不住对**同一条** run 的并发 resume ⇒ "
        f"resume 的先查 PAUSED 后翻 RUNNING 是 TOCTOU，双击/双端并发 resume 可双跑）",
    ) if status_after == "RUNNING" else rec.refute(
        "B3-1 _mark_run_running 无状态条件", f"COMPLETED run 实测变为 {status_after!r}",
    )

    # ---------------------------------------------------------------- B3-2
    import sqlite3 as _sq

    def _count_conns() -> int:
        gc.collect()
        return sum(1 for o in gc.get_objects() if isinstance(o, _sq.Connection))

    nodes = [WorkflowNode("n1", "Transform", lambda ctx: {"ok": True})]
    run_leak = _insert_bare_run(db_path, status="RUNNING", chapter_id=None)
    before = _count_conns()
    engine._insert_node_row(run_leak, nodes[0], {"chapter_id": None})  # noqa: SLF001
    after = _count_conns()
    rec.refute(
        "B3-2 _insert_node_row 每个带 agent 的节点泄漏一条连接",
        f"调用前后存活的 sqlite3.Connection 对象数 {before} → {after}（CPython 引用"
        f"计数使临时连接在语句返回后立即析构并释放句柄 ⇒ 非资源泄漏；"
        f"成立的是『违反全仓显式 close 纪律』的卫生问题，非缺陷）",
    ) if after <= before else rec.confirm(
        "B3-2 _insert_node_row 连接泄漏", f"连接数 {before} → {after}",
    )

    # ---------------------------------------------------------------- B3-3
    def _boom(ctx: dict) -> dict:
        return {"bad": object()}  # json.dumps 必炸的非可序列化对象

    run_stuck: str | None = None
    raised = ""
    try:
        run_stuck = engine.start_with_nodes(
            "probe-b3-serial", [WorkflowNode("bad_node", "Transform", _boom)],
            chapter_id=None,
        )
    except Exception as exc:  # noqa: BLE001
        raised = f"{type(exc).__name__}: {exc}"
        # start_with_nodes 在 _run_nodes 里抛错 ⇒ 拿不到返回值；run 行已落库，按名反查。
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT wr.run_id FROM workflow_runs wr "
                "JOIN workflows wf ON wf.workflow_id = wr.workflow_id "
                "WHERE wf.name = 'probe-b3-serial' ORDER BY wr.started_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        run_stuck = row["run_id"] if row else None
    stuck_status = _run_status(db_path, run_stuck) if run_stuck else None
    rec.confirm(
        "B3-3 节点产出不可序列化 ⇒ 节点行更新抛错 ⇒ run 永久停在 RUNNING",
        f"节点成功返回 {{'bad': object()}} ⇒ _update_node_row 的 _dump_json 上抛 "
        f"{raised[:90]}…（成功路径在 try/except 之外 ⇒ 异常穿出 _run_nodes，"
        f"engine.py:934→1036）；run 行实测 status={stuck_status!r}"
        f"（无终态收尾 ⇒ 该章被活跃 run 守卫卡死，后续 start 全 409，"
        f"直至重启自愈/手工修复；async 路径则 daemon 线程静默死亡，同样无终态）",
    ) if (raised and stuck_status == "RUNNING") else rec.refute(
        "B3-3 run 永久 RUNNING", f"raised={raised!r} status={stuck_status!r}",
    )

    # ---------------------------------------------------------------- B3-4
    from packages.core.workflow_runtime.engine import _resolve_checkpoint_exclude

    run_unreg = _insert_bare_run(db_path, status="PAUSED", chapter_id=None)
    excluded = _resolve_checkpoint_exclude(db_path, run_unreg)
    rec.confirm(
        "B3-4 _resolve_checkpoint_exclude 对未注册 workflow 静默返回 []（无日志）",
        f"workflow name='probe-wf' 未在注册表 ⇒ 实测返回 {excluded!r}"
        f"（docstring 声明的降级，但零日志；resume 时 scrub 全失效 ⇒ "
        f"checkpoint_exclude 名单里的键会被写进 checkpoint_json）",
    ) if excluded == [] else rec.refute(
        "B3-4 静默返回 []", f"实测 {excluded!r}",
    )

    # ---------------------------------------------------------------- B3-5
    run_ghost = _insert_bare_run(db_path, status="PAUSED", chapter_id=None)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE workflow_runs SET current_node = 'ghost_node_renamed_away' "
            "WHERE run_id = ?", (run_ghost,),
        )
        conn.commit()
    finally:
        conn.close()
    run_row = json.loads(json.dumps({"run_id": run_ghost, "current_node":
                                     "ghost_node_renamed_away", "checkpoint_json": {}}))
    fresh_engine = WorkflowEngine(db_path)
    ctx_out, start_index = fresh_engine._prepare_resume_ctx(  # noqa: SLF001
        run=run_row,
        nodes=[WorkflowNode("a", "Transform", lambda c: {}), WorkflowNode(
            "b", "Transform", lambda c: {})],
        human_input=None,
        regenerate=False,
    )
    rec.confirm(
        "B3-5 _prepare_resume_ctx 找不到 current_node ⇒ start_index=0 从头重跑（无日志）",
        f"current_node='ghost_node_renamed_away' 不在 nodes 里 ⇒ 实测 "
        f"start_index={start_index}（=0 ⇒ resume 重跑全部节点：AI 节点重复烧调用、"
        f"写节点重复落库；节点改名/迁移后 resume 即触发，零告警）",
    ) if start_index == 0 else rec.refute(
        "B3-5 start_index=0", f"实测 {start_index}",
    )

    print(f"\n（临时库：{tmp}）")
    return rec.summary()


if __name__ == "__main__":
    raise SystemExit(main())
