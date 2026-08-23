"""SQLite 迁移 runner（Sprint 0）。

职责：
- ``get_connection(db_path)``：开启外键的 sqlite3 连接。
- ``apply_migrations(db_path, migrations_dir=None)``：按文件名升序执行
  ``migrations_dir`` 下所有 ``*.sql``；通过 ``_migrations`` 表幂等追踪。

幂等策略：执行前先查 ``_migrations``，跳过已记录的脚本。脚本执行成功
后才写入记录。重复执行不会重复跑 DDL。
已知限制：DDL 脚本自身不带 ``IF NOT EXISTS``，若脚本执行成功但写记录
前进程崩溃，重跑会因「表已存在」报错，需人工处置（删除半成品 db 重来）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_DEFAULT_MIGRATIONS_DIR = Path("database") / "migrations"
_TABLE_COUNT_QUERY = "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"


def get_connection(db_path: Path | str) -> sqlite3.Connection:
    """开启外键 PRAGMA 的连接；row_factory 设为 Row 便于查询。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            filename   TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            checksum   TEXT
        )
        """
    )
    conn.commit()


def _list_sql_files(migrations_dir: Path) -> list[Path]:
    if not migrations_dir.exists():
        return []
    return sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())


def _already_applied(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute("SELECT filename FROM _migrations").fetchall()
        return {r["filename"] for r in rows}
    except sqlite3.OperationalError:
        return set()


def apply_migrations(
    db_path: Path | str,
    migrations_dir: Path | str | None = None,
) -> dict[str, list[str] | int]:
    """执行 migrations_dir 下所有 SQL，按文件名升序，幂等。

    返回 ``{"applied": [...], "skipped": [...], "tables": <int>}``。
    """
    db_path = Path(db_path)
    if migrations_dir is None:
        migrations_dir = _DEFAULT_MIGRATIONS_DIR
    migrations_dir = Path(migrations_dir)

    conn = get_connection(db_path)
    try:
        _ensure_migrations_table(conn)
        already = _already_applied(conn)

        applied: list[str] = []
        skipped: list[str] = []
        for sql_file in _list_sql_files(migrations_dir):
            name = sql_file.name
            if name in already:
                skipped.append(name)
                continue
            script = sql_file.read_text(encoding="utf-8")
            # executescript 内部会做隐式提交；外层无需再 commit
            conn.executescript(script)
            # 表单记录（applied_at 用 UTC ISO-8601）
            from datetime import datetime, timezone

            ts = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO _migrations(filename, applied_at) VALUES (?, ?)",
                (name, ts),
            )
            conn.commit()
            applied.append(name)

        tables = conn.execute(_TABLE_COUNT_QUERY).fetchone()[0]
        return {"applied": applied, "skipped": skipped, "tables": tables}
    finally:
        conn.close()


def count_tables(db_path: Path | str) -> int:
    """查询总表数量（不含 sqlite_*，含 _migrations）。

    业务表固定 28 张；含 _migrations 时总数为 29，调用方按需减一。
    """
    conn = get_connection(db_path)
    try:
        return conn.execute(_TABLE_COUNT_QUERY).fetchone()[0]
    finally:
        conn.close()
