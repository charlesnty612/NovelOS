"""迁移 runner 测试（Sprint 0）。"""

from __future__ import annotations

from pathlib import Path

from packages.core.db import apply_migrations, get_connection

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def test_apply_migrations_creates_29_business_tables(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    result = apply_migrations(db_path, MIGRATIONS_DIR)
    # 业务表 = 总表 - _migrations
    # Sprint 6 下半：新增 0003_quality_reports → 业务表 29（28+1），总数 30。
    assert result["tables"] == 30, f"expected 30 (29+_migrations), got {result['tables']}"
    assert "0001_init.sql" in result["applied"]
    assert "0001_init.sql" not in result["skipped"]
    # Sprint 5 review F2：0002_drafts_unique.sql 也应被应用
    assert "0002_drafts_unique.sql" in result["applied"]
    # Sprint 6 下半：0003_quality_reports.sql 也应被应用
    assert "0003_quality_reports.sql" in result["applied"]


def test_apply_migrations_is_idempotent(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    first = apply_migrations(db_path, MIGRATIONS_DIR)
    # Sprint 6 下半：迁移目录下三份脚本都应被首次应用
    assert first["applied"] == [
        "0001_init.sql",
        "0002_drafts_unique.sql",
        "0003_quality_reports.sql",
    ]

    second = apply_migrations(db_path, MIGRATIONS_DIR)
    assert second["applied"] == []
    assert "0001_init.sql" in second["skipped"]
    assert "0002_drafts_unique.sql" in second["skipped"]
    assert "0003_quality_reports.sql" in second["skipped"]
    assert second["tables"] == first["tables"]


def test_migrations_table_records_filename(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT filename, applied_at FROM _migrations").fetchall()
    finally:
        conn.close()
    # Sprint 6 下半：三条迁移都应记录
    filenames = {r["filename"] for r in rows}
    assert filenames == {
        "0001_init.sql",
        "0002_drafts_unique.sql",
        "0003_quality_reports.sql",
    }
    for r in rows:
        assert r["applied_at"]


def test_get_connection_enables_foreign_keys(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    conn = get_connection(db_path)
    try:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        conn.close()
    assert fk == 1


def test_business_table_count_is_29(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> '_migrations'"
        ).fetchall()
    finally:
        conn.close()
    names = {r["name"] for r in rows}
    # Sprint 6 下半：Sprint 0 的 28 张业务表 + 新增 quality_reports = 29
    assert len(names) == 29, f"expected 29 business tables, got {len(names)}"
    # 抽检：PRD §67 关键表
    for expected in ("projects", "characters", "chapters", "commits", "state_deltas", "ai_call_logs"):
        assert expected in names, f"missing table {expected}"
    assert "quality_reports" in names, "quality_reports table should exist (Sprint 6 下半)"
