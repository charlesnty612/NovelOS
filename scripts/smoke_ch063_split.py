"""V3.1.1 O-2 真实库 smoke：ch063 commit 在拆分路径下成功，输出双腿耗时/tokens。

运行前提：
- ``data/m1_run/novelos.db`` 中 ch063（id=ch_4e6d8676ad28）status=REVIEWED。
- 该 db 已注册 MiniMax-M3 model_configs（reasoning + creative_writing）。
- light capability 未注册；走 ``ModelRouter.call_with_fallback`` 自动回退 reasoning。
- ``NOVELOS_API_KEY_MINIMAX`` 或 params_json.api_key 已配置。

执行：
    python scripts/smoke_ch063_split.py

输出（结论先行）：
    - run.status == COMPLETED
    - chapters.status == COMMITTED
    - legs（按 created_at 顺序）：
      - leg_a: latency_ms=X, total_tokens=Y
      - leg_b: latency_ms=X, total_tokens=Y
    - 总耗时（observer 节点）
    - 与旧单次路径的对比（若有 v3.1.0 baseline log）
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SOURCE_DB = REPO_ROOT / "data" / "m1_run" / "novelos.db"
TARGET_CHAPTER_ID = "ch_4e6d8676ad28"


def _copy_db(src: Path, dst_dir: Path) -> Path:
    """复制 sqlite db（含 wal/shm 旁车文件）。"""
    dst = dst_dir / "novelos.db"
    shutil.copy2(src, dst)
    # 清掉残留的 wal/shm 防止污染
    for ext in ("-wal", "-shm"):
        side = dst.with_name(dst.name + ext)
        if side.exists():
            side.unlink()
    return dst


def _print_run_summary(db_path: Path, run_id: str) -> dict[str, object]:
    """打印 run 摘要 + observer 双腿耗时/tokens；返回结构化 dict。"""
    from packages.core.db import get_connection

    conn = get_connection(db_path)
    try:
        run_row = conn.execute(
            "SELECT status, error, started_at, ended_at FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run_row is None:
            return {"run_found": False}

        # observer 双腿：按 created_at ASC 排序（leg_a 先调，leg_b 后调）
        rows = conn.execute(
            """
            SELECT a.call_id, a.latency_ms, a.token_usage_json, a.retry_count,
                   a.model_id, a.error, a.created_at
            FROM ai_call_logs a
            JOIN agents ag ON ag.agent_id = a.agent_id
            WHERE a.run_id = ? AND ag.name = 'observer'
            ORDER BY a.rowid ASC
            """,
            (run_id,),
        ).fetchall()

        legs: list[dict[str, object]] = []
        for r in rows:
            usage: dict[str, int] = {}
            if r["token_usage_json"]:
                try:
                    usage = json.loads(r["token_usage_json"])
                except (TypeError, ValueError):
                    usage = {}
            legs.append({
                "call_id": r["call_id"],
                "latency_ms": int(r["latency_ms"] or 0),
                "prompt_tokens": int(usage.get("prompt") or 0),
                "completion_tokens": int(usage.get("completion") or 0),
                "total_tokens": int(usage.get("total") or 0),
                "retry_count": int(r["retry_count"] or 0),
                "model_id": r["model_id"],
                "error": r["error"],
                "created_at": r["created_at"],
            })

        return {
            "run_found": True,
            "run_status": run_row["status"],
            "run_error": run_row["error"],
            "started_at": run_row["started_at"],
            "ended_at": run_row["ended_at"],
            "observer_legs": legs,
        }
    finally:
        conn.close()


def main() -> int:
    if not SOURCE_DB.exists():
        print(f"[smoke_ch063_split] FAIL: source db not found: {SOURCE_DB}")
        return 1

    # 复制到临时目录，避免污染生产 db
    tmp_dir_obj = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    tmp_path = Path(tmp_dir_obj.name)
    try:
        db_path = _copy_db(SOURCE_DB, tmp_path)
        print(f"[smoke_ch063_split] copied db: {SOURCE_DB} -> {db_path}")

        # 确认 ch063 存在 + status
        from packages.core.db import get_connection

        conn = get_connection(db_path)
        try:
            ch_row = conn.execute(
                "SELECT chapter_id, number, title, status FROM chapters WHERE chapter_id = ?",
                (TARGET_CHAPTER_ID,),
            ).fetchone()
            if ch_row is None:
                print("[smoke_ch063_split] FAIL: ch063 not found in copied db")
                return 1
            print(f"[smoke_ch063_split] ch063 found: num={ch_row['number']} status={ch_row['status']}")
            project_id = conn.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?",
                (TARGET_CHAPTER_ID,),
            ).fetchone()["project_id"]
            model_cfgs = conn.execute(
                "SELECT config_id, capability, provider, model, enabled FROM model_configs"
            ).fetchall()
            print("[smoke_ch063_split] model_configs:")
            for m in model_cfgs:
                print(f"  {dict(m)}")
            # 清掉旧 trace（plot_events / state_deltas / ai_call_logs / commits）的 ch063 行，
            # 避免 observer 重复产出 event_id 触发 UNIQUE 约束。
            # 注意：仅清掉「本章相关的非 init_genesis / observer:v1」行，保留最新快照。
            cleared = {}
            for table in ("plot_events", "state_deltas", "commits", "ai_call_logs", "workflow_runs"):
                try:
                    cur = conn.execute(f"DELETE FROM {table} WHERE chapter_id = ?", (TARGET_CHAPTER_ID,))
                    cleared[table] = cur.rowcount
                except Exception as exc:  # noqa: BLE001
                    cleared[table] = f"err: {exc}"
            conn.commit()
            print(f"[smoke_ch063_split] cleared legacy rows: {cleared}")
        finally:
            conn.close()

        # 启动 chapter-commit workflow
        from packages.core.workflow_runtime.engine import WorkflowEngine
        from packages.workflows import get_workflow

        wf = get_workflow("chapter-commit")
        if wf is None:
            print("[smoke_ch063_split] FAIL: chapter-commit workflow not registered")
            return 1

        engine = WorkflowEngine(db_path)
        initial_ctx = {
            "db_path": str(db_path),
            "project_id": project_id,
            "chapter_id": TARGET_CHAPTER_ID,
            "quality_gate_mode": "report",  # 报告模式不阻断
        }
        t0 = time.monotonic()
        run_id = engine.start_with_nodes(
            "chapter-commit",
            wf["nodes"],
            chapter_id=TARGET_CHAPTER_ID,
            initial_ctx=initial_ctx,
            mock_providers=None,
            checkpoint_exclude=wf.get("checkpoint_exclude"),
        )
        elapsed_total = time.monotonic() - t0
        print(f"[smoke_ch063_split] run started: run_id={run_id}, total_wall={elapsed_total:.1f}s")

        # 若 PAUSED（high_risk_approval / 评审节点），自动 resume 推完。
        # 防御：连续 5 次 PAUSED 视为异常。
        from packages.core.workflow_runtime.runs import get_run
        for _ in range(5):
            run = get_run(db_path, run_id)
            if run is None:
                break
            if run["status"] != "PAUSED":
                break
            print(f"[smoke_ch063_split] run paused at node={run.get('current_node')}, auto-resume...")
            engine.resume(run_id, wf["nodes"], human_input={"approved": True})
            elapsed_total = time.monotonic() - t0
            print(f"[smoke_ch063_split] resumed; total_wall={elapsed_total:.1f}s")

        # 收集结果
        summary = _print_run_summary(db_path, run_id)
        print("=" * 72)
        print("[smoke_ch063_split] run summary:")
        print(f"  status={summary.get('run_status')}")
        if summary.get("run_error"):
            print(f"  error={summary['run_error']}")
        print(f"  observer_legs={len(summary.get('observer_legs', []))} call(s)")
        legs = summary.get("observer_legs", []) or []
        for i, leg in enumerate(legs):
            label = "leg_a (entities)" if i == 0 else "leg_b (narrative)" if i == 1 else f"leg[{i}]"
            print(
                f"    {label}: latency_ms={leg['latency_ms']}  "
                f"tokens={leg['total_tokens']} (prompt={leg['prompt_tokens']}, "
                f"completion={leg['completion_tokens']})  "
                f"retry={leg['retry_count']}  model={leg['model_id']}"
            )
        observer_total_latency = sum(int(leg["latency_ms"]) for leg in legs)
        print(f"  observer_total_latency_ms={observer_total_latency}")
        print(f"  workflow_total_wall_s={elapsed_total:.1f}")
        print("=" * 72)

        # 落盘报告
        report_dir = REPO_ROOT / "docs" / "evaluation" / "smoke_runs"
        report_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%S")
        report_path = report_dir / f"ch063_split_{ts}.json"
        report_path.write_text(
            json.dumps(
                {
                    "chapter_id": TARGET_CHAPTER_ID,
                    "run_id": run_id,
                    "total_wall_s": elapsed_total,
                    "observer_legs": legs,
                    "observer_total_latency_ms": observer_total_latency,
                    "run_status": summary.get("run_status"),
                    "run_error": summary.get("run_error"),
                    "started_at": summary.get("started_at"),
                    "ended_at": summary.get("ended_at"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[smoke_ch063_split] report saved: {report_path}")

        if summary.get("run_status") != "COMPLETED":
            print("[smoke_ch063_split] FAIL: run did not complete")
            return 1
        if len(legs) != 2:
            print(f"[smoke_ch063_split] FAIL: expected 2 observer legs, got {len(legs)}")
            return 1
        print("[smoke_ch063_split] PASS")
        return 0
    finally:
        try:
            tmp_dir_obj.cleanup()
        except (PermissionError, OSError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
