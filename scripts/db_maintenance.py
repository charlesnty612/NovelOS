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
    python scripts/db_maintenance.py --db <path> prune-logs --days N [--export PATH] [--apply]
    python scripts/db_maintenance.py --db <path> vacuum

* ``list`` 子命令：列出疑似 stale 的 RUNNING 行（不修改库）。
* ``fix`` 子命令：默认 dry-run，只打印将改的行数与样例；``--apply`` 才真正
  执行 UPDATE。重复执行第二次 0 行（幂等）。
* ``prune-logs`` 子命令（2026-09-06 审查批次二）：清理 ai_call_logs 中
  ``created_at`` 早于保留期（``--days``，默认 90 天）的行；默认 dry-run，
  ``--apply`` 才删除；``--export PATH`` 在删除前把将删的行全列导出为 JSON
  归档（dry-run 下亦可单独用作预览导出）。幂等：第二次执行 0 行。
* ``vacuum`` 子命令：执行 ``VACUUM`` 回收已删除行占用的磁盘空间，打印前后
  页统计。建议在 ``prune-logs --apply`` 之后执行。

参数
----

* ``--db <path>``：必填；SQLite 文件路径。文件不存在退出码 2。
* ``--older-than-minutes <int>``：stale 阈值（默认 30）。

退出码
------

* ``0`` —— 成功（list 或 fix 都视为成功）。
* ``2`` —— 参数错误 / DB 文件不存在 / 内部异常 / **待写路径被守卫拒绝**。

落盘约束（D-19 / 写路径守卫）
---------------------------

``prune-logs --export PATH`` 是本脚本唯一的落盘口（把将删的 ``ai_call_logs``
全列行导出为 JSON 归档）。**软件仓内只允许写 ``data/`` 与 ``tmp/`` 两个子树**
（均已被 ``.gitignore`` 覆盖）：``--export`` 指到软件仓内其它位置（仓库根、
``docs/`` …）⇒ **明确拒绝**（退出码 2，不产生导出文件）——脚本不得把产物写回
软件仓（落进去 = 进 GitHub 历史，**不可撤回**），也**不得**静默改写到别处。
逃生口 ``--allow-software-repo-out``（默认关）须由用户显式承担风险。

设计要点
--------

* 时间戳统一 UTC ISO-8601（``+00:00`` 偏移），复用
  ``packages.core.ids.now_iso``，与表内 ``started_at`` / ``ended_at``
  写入口径完全一致。
* 核心逻辑拆成模块级函数 ``_find_stale`` / ``_fix_stale`` / ``_find_old_logs``
  / ``_export_old_logs`` / ``_prune_logs`` / ``_vacuum`` 便于单测：
  接受已打开的 ``sqlite3.Connection``，不耦合 argparse / 路径检查。
* 通过 ``packages.core.db.get_connection`` 复用既有连接配置（外键 PRAGMA、
  WAL、busy_timeout），与全仓惯例保持一致。
* ``fix`` 只动 workflow_runs；``prune-logs`` 只动 ai_call_logs；
  ``vacuum`` 不改任何行。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# 让脚本可直接运行（python scripts/db_maintenance.py --db ...）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.db import get_connection, is_missing_schema_error  # noqa: E402
from packages.core.ids import now_iso  # noqa: E402

try:  # 正常路径：仓库根在 sys.path（包导入 / pytest pythonpath=["."]）
    from scripts.guard_repo_write_path import (
        EXIT_WRITE_PATH_REJECTED,
        RepoWritePathError,
        check_write_path,
        is_inside_repo,
    )
except ImportError:  # pragma: no cover —— 直接 `python scripts/db_maintenance.py` 时
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from guard_repo_write_path import (  # type: ignore[no-redef]
        EXIT_WRITE_PATH_REJECTED,
        RepoWritePathError,
        check_write_path,
        is_inside_repo,
    )

STALE_ERROR_MARKER = "stale cleanup: process died mid-run (db_maintenance)"
"""UPDATE 时写入 ``workflow_runs.error`` 的固定标记，便于事后审计识别。"""

DEFAULT_OLDER_THAN_MINUTES = 30
"""默认 stale 阈值：started_at 距 now 超过 30 分钟的 RUNNING 视为悬挂。"""

DEFAULT_LOG_RETENTION_DAYS = 90
"""默认 ai_call_logs 保留天数：created_at 早于 now - 90 天的行可清理。

背景（2026-09-06 审查批次二）：ai_call_logs 每次成功/失败 agent 调用全量落库
（含 writer 全文 output_json），此前无任何清理机制，长跑新书后 DB 无界膨胀。
本工具提供显式 CLI 清理（不自动跑，lifespan 不挂钩），保留策略由运维决定。
"""


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


# ---------------------------------------------------------------------------
# prune-logs：ai_call_logs 保留策略清理（2026-09-06 审查批次二）
# ---------------------------------------------------------------------------


def _cutoff_days_iso(days: int) -> str:
    """返回 ``now - days`` 的 UTC ISO 字符串，用作 created_at 的 cutoff。"""
    from datetime import datetime, timedelta, timezone

    if days < 0:
        raise ValueError(f"days must be >= 0, got {days}")
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _find_old_logs(conn: sqlite3.Connection, days: int) -> list[sqlite3.Row]:
    """返回 created_at 早于 ``now - days`` 的 ai_call_logs 行（升序）。

    只取元数据列（不含 output_json / token_usage_json 大字段），供 list /
    dry-run 样例打印与导出元信息；``_prune_logs`` 删除时按同一 cutoff 全列删。
    """
    cutoff = _cutoff_days_iso(days)
    return conn.execute(
        """
        SELECT call_id, run_id, node_run_id, agent_id, model_id, prompt_version,
               latency_ms, error, retry_count, created_at
          FROM ai_call_logs
         WHERE created_at < ?
         ORDER BY created_at ASC
        """,
        (cutoff,),
    ).fetchall()


def _export_old_logs(
    conn: sqlite3.Connection,
    days: int,
    export_path: Path,
) -> int:
    """把将删除的 ai_call_logs 全列行导出为 JSON 归档文件，返回导出行数。

    JSON 结构：``{"exported_at", "cutoff", "days", "count", "rows": [...]}``。
    用于删除前留档；文件由调用方自行保管，工具不再跟踪。
    """
    cutoff = _cutoff_days_iso(days)
    rows = conn.execute(
        "SELECT * FROM ai_call_logs WHERE created_at < ? ORDER BY created_at ASC",
        (cutoff,),
    ).fetchall()
    payload = {
        "exported_at": now_iso(),
        "cutoff": cutoff,
        "days": days,
        "count": len(rows),
        "rows": [dict(r) for r in rows],
    }
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(rows)


def _prune_logs(conn: sqlite3.Connection, days: int) -> int:
    """删除 created_at 早于 ``now - days`` 的 ai_call_logs 行，返回删除行数。

    幂等：第二次执行 ``days`` 不变时返回 0（旧行已被上一轮删除）。
    """
    cutoff = _cutoff_days_iso(days)
    cur = conn.execute("DELETE FROM ai_call_logs WHERE created_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def _vacuum(conn: sqlite3.Connection) -> dict[str, int]:
    """执行 ``VACUUM`` 回收已删除行占用的磁盘空间，返回前后页统计。

    返回 ``{"page_size", "pages_before", "pages_after"}``。VACUUM 不能在事务内
    执行——调用前先 ``conn.commit()`` 确保无未决事务（Python sqlite3 在写后
    会隐式开事务）。
    """
    conn.commit()
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    pages_before = conn.execute("PRAGMA page_count").fetchone()[0]
    conn.execute("VACUUM")
    pages_after = conn.execute("PRAGMA page_count").fetchone()[0]
    return {
        "page_size": page_size,
        "pages_before": pages_before,
        "pages_after": pages_after,
    }


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
    parser.add_argument(
        "--allow-software-repo-out",
        action="store_true",
        help="逃生口：允许把导出产物写回软件仓（默认关；风险自负——可能被误提交 ⇒ "
             "进 GitHub 历史不可撤回）。**放在子命令之前**。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser(
        "list",
        help="列出疑似 stale 的 RUNNING 行（只读，不修改库）。",
    )
    list_p.add_argument(
        "--older-than-minutes",
        type=int,
        default=DEFAULT_OLDER_THAN_MINUTES,
        metavar="N",
        help=f"stale 阈值（分钟，默认 {DEFAULT_OLDER_THAN_MINUTES}）。",
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

    prune_p = sub.add_parser(
        "prune-logs",
        help="清理 ai_call_logs 中早于保留期的行（默认 dry-run，--apply 才删除）。",
    )
    prune_p.add_argument(
        "--days",
        type=int,
        default=DEFAULT_LOG_RETENTION_DAYS,
        metavar="N",
        help=f"保留天数（默认 {DEFAULT_LOG_RETENTION_DAYS}）：created_at 早于 now-N 天的行被清理。",
    )
    prune_p.add_argument(
        "--export",
        type=Path,
        default=None,
        metavar="PATH",
        help="删除前把将删的行（全列）导出为 JSON 归档文件（dry-run 下同样可用作预览导出）。",
    )
    prune_p.add_argument(
        "--apply",
        action="store_true",
        help="真正删除；缺省时为 dry-run，只打印将删除的行数与样例。",
    )

    sub.add_parser(
        "vacuum",
        help="执行 VACUUM 回收已删除行占用的磁盘空间（打印前后页统计）。",
    )
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    db_path: Path = args.db
    if not db_path.exists():
        print(f"error: DB file not found: {db_path}", file=sys.stderr)
        return 2

    # 守卫（D-19）：待写路径不得落在软件仓根下非 data/tmp 处。
    # ⚠️ ``--export`` 只存在于 ``prune-logs`` 子命令 ⇒ 用 getattr 兜底其它子命令。
    allow_repo_out = bool(getattr(args, "allow_software_repo_out", False))
    export_path = getattr(args, "export", None)
    if export_path is not None:
        try:
            check_write_path(export_path, allow_repo_root=allow_repo_out)
        except RepoWritePathError as exc:
            print(f"[拒绝写入] {exc}", file=sys.stderr)
            return EXIT_WRITE_PATH_REJECTED
        if allow_repo_out and is_inside_repo(export_path):
            print(
                f"[警告] --allow-software-repo-out 已开启：导出产物将落软件仓 {export_path}"
                "（可能被误提交 ⇒ 进 GitHub 历史不可撤回）。",
                file=sys.stderr,
            )

    conn = get_connection(db_path)
    try:
        if args.command == "list":
            rows = _find_stale(conn, minutes=args.older_than_minutes)
            _print_list(rows)
            return 0

        if args.command == "fix":
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

        if args.command == "prune-logs":
            days: int = args.days
            # 表可能不存在（极老库 / 测试最小 schema）：fail-soft 提示后按 0 行处理
            try:
                rows = _find_old_logs(conn, days=days)
            except sqlite3.OperationalError as exc:
                # 收窄（2026-10-16 批次十）：上面注释声称的范围就是「表不存在」，
                # 故只放结构缺失。锁 / I/O / 损坏 / 只读 / 磁盘满必须上抛——
                # 否则 `prune-logs` 会把「**读不到** ai_call_logs」当成
                # 「**没有**过期日志」打印 `[skip] … nothing to prune` 并**退出码 0**，
                # 运维以为清理完成、实际一行没查（dry-run 的整个用途是提前看见）。
                if not is_missing_schema_error(exc):
                    raise
                print(f"[skip] ai_call_logs unavailable ({exc}); nothing to prune")
                return 0
            if export_path is not None:
                exported = _export_old_logs(conn, days=days, export_path=export_path)
                print(f"[export] wrote {exported} rows -> {export_path}")
            if args.apply:
                deleted = _prune_logs(conn, days=days)
                print(f"[apply] deleted {deleted} ai_call_logs rows older than {days}d")
            else:
                print(
                    f"[dry-run] would delete {len(rows)} ai_call_logs rows "
                    f"older than {days}d"
                )
            if rows:
                print("samples:")
                for r in rows[:5]:
                    print(
                        f"  call_id={r['call_id']} agent_id={r['agent_id']} "
                        f"model_id={r['model_id']} created_at={r['created_at']}"
                    )
            return 0

        # args.command == "vacuum"
        stats = _vacuum(conn)
        mb = stats["page_size"] * stats["pages_before"] / (1024 * 1024)
        ma = stats["page_size"] * stats["pages_after"] / (1024 * 1024)
        print(
            f"[vacuum] pages {stats['pages_before']} -> {stats['pages_after']} "
            f"(page_size={stats['page_size']}); approx {mb:.2f} MB -> {ma:.2f} MB"
        )
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：内部异常统一收口为「单行诊断 + 退出码 2」（F-4，2026-10-23）。

    模块顶部「退出码」节承诺 ``2 —— 参数错误 / DB 文件不存在 / 内部异常``；此前
    ``main()`` 没有顶层 except ⇒ 未捕获异常 exit 1 + 裸 traceback，运维看到的是
    堆栈而不是一行诊断。这里只把**异常出口**改成契约形态，正常路径（0）与「缺库 2」
    逐字不变；argparse 的参数错误走 ``SystemExit(2)``（``SystemExit`` 不继承
    ``Exception``，不会被本 wrapper 改写）。
    """
    try:
        return _main(argv)
    except Exception as exc:  # noqa: BLE001 —— CLI 顶层兜底（契约承诺 exit 2）
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
