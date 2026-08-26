"""db_maintenance —— workflow_runs stale 行清理工具（V3.9 C1）。

背景
----

实测中服务进程异常死亡（OOM、被 kill、宿主机宕机等）会在
``workflow_runs`` 留下永久 ``status='RUNNING'`` 的悬挂行：

* 引擎只在「异常出口」（异常向上抛）置 ``status='FAILED'``，并写入
  ``ended_at`` 与 ``error``；
* 进程被 SIGKILL / OOM kill / 宿主机断电时，无人收尾，``status`` 永远停在
  ``RUNNING``、``ended_at`` 为 ``NULL``。
* ``WorkflowRuntime.resume()`` 仅对 ``PAUSED`` 行放行，``RUNNING`` 行直接
  抛 ``ValueError``，死 RUNNING 行无法自愈——只能手动改库。

本工具提供一个幂等的 CLI，把这些死 RUNNING 行收口成 ``FAILED``（带
``error='stale cleanup: process died mid-run (db_maintenance)'`` 与
``ended_at=now``），方便运维与本地调试。

用法
----

::

    python scripts/db_maintenance.py --db <path> list
    python scripts/db_maintenance.py --db <path> fix --older-than-minutes N [--apply]

* ``list`` 子命令：列出疑似 stale 的 RUNNING 行（不修改库）。
* ``fix`` 子命令：默认 dry-run，只打印将改的行数与样例；``--apply`` 才真正
  执行 UPDATE。重复执行第二次 0 行（幂等）。

参数
----

* ``--db <path>``：必填；SQLite 文件路径。文件不存在退出码 2。
* ``--older-than-minutes <int>``：stale 阈值（默认 30）。

退出码
------

* ``0`` —— 成功（list 或 fix 都视为成功）。
* ``2`` —— 参数错误 / DB 文件不存在 / 内部异常。

设计要点
--------

* 时间戳统一 UTC ISO-8601（``+00:00`` 偏移），复用
  ``packages.core.ids.now_iso``，与表内 ``started_at`` / ``ended_at``
  写入口径完全一致。
* 核心逻辑拆成两个模块级函数 ``_find_stale`` / ``_fix_stale`` 便于单测：
  接受已打开的 ``sqlite3.Connection``，不耦合 argparse / 路径检查。
* 通过 ``packages.core.db.get_connection`` 复用既有连接配置（外键 PRAGMA、
  WAL、busy_timeout），与全仓惯例保持一致。
* 不动其它任何表、字段、迁移或真实数据。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# 让脚本可直接运行（python scripts/db_maintenance.py --db ...）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.db import get_connection  # noqa: E402
from packages.core.ids import now_iso  # noqa: E402


STALE_ERROR_MARKER = "stale cleanup: process died mid-run (db_maintenance)"
"""UPDATE 时写入 ``workflow_runs.error`` 的固定标记，便于事后审计识别。"""

DEFAULT_OLDER_THAN_MINUTES = 30
"""默认 stale 阈值：started_at 距 now 超过 30 分钟的 RUNNING 视为悬挂。"""


def _find_stale(conn: sqlite3.Connection, minutes: int) -> list[sqlite3.Row]:
    """返回所有疑似 stale 的 RUNNING 行（started_at 早于 now - minutes）。

    参数
    ----
    conn : sqlite3.Connection
        已打开的数据库连接（建议使用 ``packages.core.db.get_connection``）。
    minutes : int
        stale 阈值（分钟）。started_at 早于 ``now - minutes`` 的行被命中。

    返回
    ----
    list[sqlite3.Row]
        命中的行，按 ``started_at`` 升序排列（最早最可能 stale 的在前）。
        每行包含 ``run_id`` / ``workflow_id`` / ``chapter_id`` /
        ``started_at``。
    """
    cutoff = _cutoff_iso(minutes)
    return conn.execute(
        """
        SELECT run_id, workflow_id, chapter_id, started_at
          FROM workflow_runs
         WHERE status = 'RUNNING'
           AND started_at < ?
         ORDER BY started_at ASC
        """,
        (cutoff,),
    ).fetchall()


def _fix_stale(conn: sqlite3.Connection, minutes: int) -> int:
    """把 stale RUNNING 行批量更新为 FAILED，返回受影响行数。

    幂等：第二次执行 ``minutes`` 不变时返回 0（已被上一轮置为 FAILED）。
    """
    cutoff = _cutoff_iso(minutes)
    ended_at = now_iso()
    cur = conn.execute(
        """
        UPDATE workflow_runs
           SET status    = 'FAILED',
               error     = ?,
               ended_at  = ?
         WHERE status = 'RUNNING'
           AND started_at < ?
        """,
        (STALE_ERROR_MARKER, ended_at, cutoff),
    )
    conn.commit()
    return cur.rowcount


def _cutoff_iso(minutes: int) -> str:
    """返回 ``now - minutes`` 的 UTC ISO 字符串，用作 started_at 的 cutoff。

    之所以不用 ``now_iso() - timedelta(...)`` 后再 ``.isoformat()``，是因为
    ``now_iso()`` 内部使用的 ``datetime.now(timezone.utc).isoformat()`` 已经
    与表内 ``started_at`` 写入口径对齐，这里复用同一条路径避免漂移。
    """
    from datetime import datetime, timedelta, timezone

    if minutes < 0:
        raise ValueError(f"minutes must be >= 0, got {minutes}")
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _print_list(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("(no stale RUNNING rows)")
        return
    print(f"stale RUNNING rows: {len(rows)}")
    for r in rows:
        chapter = r["chapter_id"] if r["chapter_id"] is not None else "-"
        print(
            f"  run_id={r['run_id']} workflow_id={r['workflow_id']} "
            f"chapter_id={chapter} started_at={r['started_at']}"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="db_maintenance",
        description="清理 workflow_runs 中因进程异常死亡留下的 stale RUNNING 行。",
    )
    parser.add_argument(
        "--db",
        required=True,
        type=Path,
        help="SQLite 文件路径（必填）。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "list",
        help="列出疑似 stale 的 RUNNING 行（只读，不修改库）。",
    )

    fix_p = sub.add_parser(
        "fix",
        help="把 stale RUNNING 行更新为 FAILED（默认 dry-run，加 --apply 才真正写入）。",
    )
    fix_p.add_argument(
        "--older-than-minutes",
        type=int,
        default=DEFAULT_OLDER_THAN_MINUTES,
        metavar="N",
        help=f"stale 阈值（分钟，默认 {DEFAULT_OLDER_THAN_MINUTES}）。",
    )
    fix_p.add_argument(
        "--apply",
        action="store_true",
        help="真正写入数据库；缺省时为 dry-run，只打印将影响的行数与样例。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    db_path: Path = args.db
    if not db_path.exists():
        print(f"error: DB file not found: {db_path}", file=sys.stderr)
        return 2

    conn = get_connection(db_path)
    try:
        if args.command == "list":
            rows = _find_stale(conn, minutes=DEFAULT_OLDER_THAN_MINUTES)
            _print_list(rows)
            return 0

        # args.command == "fix"
        minutes: int = args.older_than_minutes
        rows = _find_stale(conn, minutes=minutes)
        if args.apply:
            affected = _fix_stale(conn, minutes=minutes)
            print(f"[apply] updated {affected} stale RUNNING rows -> FAILED")
        else:
            print(f"[dry-run] would update {len(rows)} stale RUNNING rows -> FAILED")
        if rows:
            print("samples:")
            for r in rows[:5]:
                chapter = r["chapter_id"] if r["chapter_id"] is not None else "-"
                print(
                    f"  run_id={r['run_id']} workflow_id={r['workflow_id']} "
                    f"chapter_id={chapter} started_at={r['started_at']}"
                )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
