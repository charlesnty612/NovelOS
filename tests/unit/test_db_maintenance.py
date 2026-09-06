"""scripts/db_maintenance.py 单元测试（V3.9 C1）。

覆盖：
1. ``_find_stale(minutes=30)`` 只命中陈旧的 RUNNING 行（新鲜 RUNNING /
   PAUSED / COMPLETED 不命中）。
2. ``_fix_stale`` 把陈旧 RUNNING 行批量置 FAILED，写入 ``stale cleanup``
   error 标记与非空 ``ended_at``（UTC ISO ``+00:00``），其余行原状不变。
3. ``_fix_stale`` 幂等：第二次执行返回 0。
4. CLI 入口 ``list`` / ``fix [--apply]`` 冒烟：list 打印格式正确；
   fix dry-run 不动库；fix --apply 真正写入。
5. ``prune-logs``：``_find_old_logs`` 只命中超保留期行；``_prune_logs``
   幂等；``--export`` 归档 JSON 全列可回读；CLI dry-run 不删 / --apply
   真删；ai_call_logs 表不存在时 fail-soft 返回 0。
6. ``vacuum``：``_vacuum`` 返回前后页统计且页数不增。

测试策略
--------

* 用 ``tmp_path`` 建真实 SQLite 文件，手工 ``CREATE TABLE workflow_runs``
  对齐列名（不必跑 migrations），避免依赖 apply_migrations 与 migrations/
  路径解析；db_maintenance 的 SQL 只读 4 列 + UPDATE 3 列，无需其它表存在。
* 通过 ``importlib.util.spec_from_file_location("dbm", <script path>)``
  把脚本当模块加载，避免脚本 ``sys.path.insert`` 与 tests/ 的 path
  设置冲突；仓库当前 tests/unit/ 内没有该写法的先例，故直接照任务描述。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "db_maintenance.py"


# --------------------------------------------------------------------------- #
# module loader
# --------------------------------------------------------------------------- #


def _load_module():
    """通过 spec_from_file_location 加载脚本为模块名 ``dbm``。"""
    spec = importlib.util.spec_from_file_location("dbm", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dbm"] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _open_conn(db_path: Path) -> sqlite3.Connection:
    """与 ``packages.core.db.get_connection`` 行为对齐的最小测试连接。

    db_maintenance.py 内部使用 ``get_connection``，这里测试里只需要 PRAGMA
    foreign_keys=ON（脚本不写其它表，但保留与生产一致）。row_factory=Row
    让 ``_find_stale`` 返回 sqlite3.Row，便于下标取值。
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _make_schema(db_path: Path) -> None:
    """建最小 workflow_runs 表（对齐 database/migrations/0001_init.sql 第 21 节）。"""
    conn = _open_conn(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE workflow_runs (
                run_id          TEXT PRIMARY KEY,
                workflow_id     TEXT NOT NULL,
                chapter_id      TEXT,
                status          TEXT NOT NULL DEFAULT 'PENDING',
                current_node    TEXT,
                checkpoint_json TEXT NOT NULL DEFAULT '{}',
                error           TEXT,
                retry_count     INTEGER NOT NULL DEFAULT 0,
                started_at      TEXT NOT NULL,
                ended_at        TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_run(
    db_path: Path,
    *,
    run_id: str,
    status: str,
    started_offset_minutes: int,
    chapter_id: str | None = "ch_x",
    ended_at: str | None = None,
    error: str | None = None,
) -> None:
    started_at = (
        datetime.now(timezone.utc) - timedelta(minutes=started_offset_minutes)
    ).isoformat()
    conn = _open_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, chapter_id, status, current_node,
                 checkpoint_json, error, retry_count, started_at, ended_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "wf_demo",
                chapter_id,
                status,
                "node_a",
                "{}",
                error,
                0,
                started_at,
                ended_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# _find_stale / _fix_stale
# --------------------------------------------------------------------------- #


def test_find_stale_only_matches_old_running(tmp_path: Path) -> None:
    db = tmp_path / "novelos.db"
    _make_schema(db)
    # 新鲜 RUNNING：10 分钟前，未达 30 分钟阈值
    _insert_run(db, run_id="wfr_fresh", status="RUNNING", started_offset_minutes=10)
    # 陈旧 RUNNING：2 小时前
    _insert_run(db, run_id="wfr_old", status="RUNNING", started_offset_minutes=120)
    # 终态 PAUSED / COMPLETED（即使是老的也不应被命中）
    _insert_run(
        db,
        run_id="wfr_paused",
        status="PAUSED",
        started_offset_minutes=120,
        ended_at=None,
    )
    _insert_run(
        db,
        run_id="wfr_done",
        status="COMPLETED",
        started_offset_minutes=120,
        ended_at=_now_iso(),
    )

    mod = _load_module()
    conn = _open_conn(db)
    try:
        rows = mod._find_stale(conn, minutes=30)
    finally:
        conn.close()

    ids = [r["run_id"] for r in rows]
    assert ids == ["wfr_old"], f"expected only the stale RUNNING row, got {ids}"


def test_fix_stale_updates_only_old_running(tmp_path: Path) -> None:
    db = tmp_path / "novelos.db"
    _make_schema(db)
    _insert_run(db, run_id="wfr_fresh", status="RUNNING", started_offset_minutes=10)
    _insert_run(db, run_id="wfr_old", status="RUNNING", started_offset_minutes=120)
    _insert_run(
        db,
        run_id="wfr_paused",
        status="PAUSED",
        started_offset_minutes=120,
        ended_at=None,
    )
    _insert_run(
        db,
        run_id="wfr_done",
        status="COMPLETED",
        started_offset_minutes=120,
        ended_at=_now_iso(),
    )

    mod = _load_module()
    conn = _open_conn(db)
    try:
        affected = mod._fix_stale(conn, minutes=30)
        assert affected == 1

        # 第二次：幂等，0 行
        affected2 = mod._fix_stale(conn, minutes=30)
        assert affected2 == 0
    finally:
        conn.close()

    # 复核各行最终状态
    conn = _open_conn(db)
    try:
        rows = {
            r["run_id"]: dict(r)
            for r in conn.execute(
                "SELECT run_id, status, error, ended_at FROM workflow_runs"
            ).fetchall()
        }
    finally:
        conn.close()

    # 陈旧 RUNNING -> FAILED + stale marker + ended_at 非空
    old = rows["wfr_old"]
    assert old["status"] == "FAILED"
    assert old["ended_at"] is not None and old["ended_at"].endswith("+00:00")
    assert "stale cleanup" in (old["error"] or "")

    # 其它三行不动
    fresh = rows["wfr_fresh"]
    assert fresh["status"] == "RUNNING"
    assert fresh["ended_at"] is None
    assert fresh["error"] is None

    paused = rows["wfr_paused"]
    assert paused["status"] == "PAUSED"
    assert paused["ended_at"] is None
    assert paused["error"] is None

    done = rows["wfr_done"]
    assert done["status"] == "COMPLETED"
    # COMPLETED 的 ended_at 是 fixture 写的 _now_iso()，不该被脚本改写
    assert done["ended_at"] is not None and done["ended_at"].endswith("+00:00")


# --------------------------------------------------------------------------- #
# CLI 冒烟（list / fix dry-run / fix --apply / 缺 --db）
# --------------------------------------------------------------------------- #


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """调 ``python scripts/db_maintenance.py ...`` 子进程，捕获输出。"""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd) if cwd else None,
        check=False,
    )


def test_cli_list_prints_stale_rows(tmp_path: Path) -> None:
    db = tmp_path / "novelos.db"
    _make_schema(db)
    _insert_run(db, run_id="wfr_fresh", status="RUNNING", started_offset_minutes=10)
    _insert_run(db, run_id="wfr_old", status="RUNNING", started_offset_minutes=120)

    proc = _run_cli("--db", str(db), "list")
    assert proc.returncode == 0, proc.stderr
    assert "wfr_old" in proc.stdout
    assert "wfr_fresh" not in proc.stdout


def test_cli_fix_dry_run_does_not_mutate(tmp_path: Path) -> None:
    db = tmp_path / "novelos.db"
    _make_schema(db)
    _insert_run(db, run_id="wfr_old", status="RUNNING", started_offset_minutes=120)

    proc = _run_cli(
        "--db",
        str(db),
        "fix",
        "--older-than-minutes",
        "30",
    )
    assert proc.returncode == 0, proc.stderr
    assert "[dry-run]" in proc.stdout
    assert "would update 1" in proc.stdout
    assert "wfr_old" in proc.stdout

    # DB 应当原状：仍是 RUNNING、ended_at NULL
    conn = _open_conn(db)
    try:
        row = conn.execute(
            "SELECT status, ended_at FROM workflow_runs WHERE run_id='wfr_old'"
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "RUNNING"
    assert row["ended_at"] is None


def test_cli_fix_apply_mutates(tmp_path: Path) -> None:
    db = tmp_path / "novelos.db"
    _make_schema(db)
    _insert_run(db, run_id="wfr_old", status="RUNNING", started_offset_minutes=120)

    proc = _run_cli(
        "--db",
        str(db),
        "fix",
        "--older-than-minutes",
        "30",
        "--apply",
    )
    assert proc.returncode == 0, proc.stderr
    assert "[apply]" in proc.stdout
    assert "updated 1" in proc.stdout

    conn = _open_conn(db)
    try:
        row = conn.execute(
            "SELECT status, error, ended_at FROM workflow_runs WHERE run_id='wfr_old'"
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "FAILED"
    assert "stale cleanup" in (row["error"] or "")
    assert row["ended_at"] is not None and row["ended_at"].endswith("+00:00")


def test_cli_missing_db_returns_exit_2(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.db"
    proc = _run_cli("--db", str(missing), "list")
    assert proc.returncode == 2
    assert "not found" in proc.stderr.lower() or "not found" in proc.stdout.lower()


# --------------------------------------------------------------------------- #
# prune-logs / vacuum（2026-09-06 审查批次二）
# --------------------------------------------------------------------------- #


def _make_ai_call_logs_schema(db_path: Path) -> None:
    """建最小 ai_call_logs 表（列名对齐 0001_init.sql）。"""
    conn = _open_conn(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE ai_call_logs (
                call_id                TEXT PRIMARY KEY,
                run_id                 TEXT,
                node_run_id            TEXT,
                agent_id               TEXT,
                model_id               TEXT,
                prompt_version         TEXT,
                input_context_ids_json TEXT,
                output_json            TEXT,
                token_usage_json       TEXT,
                latency_ms             INTEGER,
                cost                   REAL,
                error                  TEXT,
                retry_count            INTEGER NOT NULL DEFAULT 0,
                created_at             TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_log(db_path: Path, *, call_id: str, age_days: int) -> None:
    created_at = (
        datetime.now(timezone.utc) - timedelta(days=age_days)
    ).isoformat()
    conn = _open_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO ai_call_logs (call_id, created_at) VALUES (?, ?)",
            (call_id, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def test_find_old_logs_only_matches_expired(tmp_path: Path) -> None:
    db = tmp_path / "novelos.db"
    _make_ai_call_logs_schema(db)
    _insert_log(db, call_id="aic_fresh", age_days=10)
    _insert_log(db, call_id="aic_old", age_days=120)

    mod = _load_module()
    conn = _open_conn(db)
    try:
        rows = mod._find_old_logs(conn, days=90)
    finally:
        conn.close()
    assert [r["call_id"] for r in rows] == ["aic_old"]


def test_prune_logs_idempotent_and_keeps_fresh(tmp_path: Path) -> None:
    db = tmp_path / "novelos.db"
    _make_ai_call_logs_schema(db)
    _insert_log(db, call_id="aic_fresh", age_days=10)
    _insert_log(db, call_id="aic_old", age_days=120)

    mod = _load_module()
    conn = _open_conn(db)
    try:
        assert mod._prune_logs(conn, days=90) == 1
        assert mod._prune_logs(conn, days=90) == 0  # 幂等
        remaining = [
            r["call_id"]
            for r in conn.execute("SELECT call_id FROM ai_call_logs").fetchall()
        ]
    finally:
        conn.close()
    assert remaining == ["aic_fresh"]


def test_export_old_logs_writes_full_columns(tmp_path: Path) -> None:
    db = tmp_path / "novelos.db"
    _make_ai_call_logs_schema(db)
    _insert_log(db, call_id="aic_old", age_days=120)

    mod = _load_module()
    export_path = tmp_path / "archive" / "logs.json"
    conn = _open_conn(db)
    try:
        n = mod._export_old_logs(conn, days=90, export_path=export_path)
    finally:
        conn.close()

    assert n == 1
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["count"] == 1
    assert payload["rows"][0]["call_id"] == "aic_old"
    # 全列导出：created_at 在列集中
    assert "created_at" in payload["rows"][0]


def test_cli_prune_logs_dry_run_and_apply(tmp_path: Path) -> None:
    db = tmp_path / "novelos.db"
    _make_ai_call_logs_schema(db)
    _make_schema(db)
    _insert_log(db, call_id="aic_old", age_days=120)

    # dry-run 不删
    proc = _run_cli("--db", str(db), "prune-logs", "--days", "90")
    assert proc.returncode == 0, proc.stderr
    assert "[dry-run] would delete 1" in proc.stdout
    assert "aic_old" in proc.stdout
    conn = _open_conn(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM ai_call_logs").fetchone()[0]
    finally:
        conn.close()
    assert n == 1

    # --apply 真删
    proc = _run_cli("--db", str(db), "prune-logs", "--days", "90", "--apply")
    assert proc.returncode == 0, proc.stderr
    assert "[apply] deleted 1" in proc.stdout
    conn = _open_conn(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM ai_call_logs").fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_cli_prune_logs_missing_table_is_fail_soft(tmp_path: Path) -> None:
    db = tmp_path / "novelos.db"
    _make_schema(db)  # 只有 workflow_runs，无 ai_call_logs

    proc = _run_cli("--db", str(db), "prune-logs", "--days", "90")
    assert proc.returncode == 0, proc.stderr
    assert "[skip]" in proc.stdout


def test_cli_vacuum_runs(tmp_path: Path) -> None:
    db = tmp_path / "novelos.db"
    _make_schema(db)

    proc = _run_cli("--db", str(db), "vacuum")
    assert proc.returncode == 0, proc.stderr
    assert "[vacuum]" in proc.stdout
