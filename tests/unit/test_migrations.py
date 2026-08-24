"""迁移 runner 测试（Sprint 0）。"""

from __future__ import annotations

from pathlib import Path

from packages.core.db import apply_migrations, get_connection

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def test_apply_migrations_creates_33_business_tables(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    result = apply_migrations(db_path, MIGRATIONS_DIR)
    # 业务表 = 总表 - _migrations
    # Sprint 11 上半：新增 0004_reference_canon（reference_canons + canon_extracts）→ 业务表 31（29+2），总数 32。
    # Sprint 10：0005 是「表重建」（不改表数），仍 32。
    # Sprint 14：0007_chapter_summaries 加 chapter_summaries 业务表 → 业务表 32（31+1），总数 33。
    # Sprint 15 / V1.3：0008_author_style_samples_and_overdue 加 author_style_samples
    #   业务表 → 业务表 33（32+1），总数 34。
    assert result["tables"] == 34, f"expected 34 (33+_migrations), got {result['tables']}"
    assert "0001_init.sql" in result["applied"]
    assert "0001_init.sql" not in result["skipped"]
    # Sprint 5 review F2：0002_drafts_unique.sql 也应被应用
    assert "0002_drafts_unique.sql" in result["applied"]
    # Sprint 6 下半：0003_quality_reports.sql 也应被应用
    assert "0003_quality_reports.sql" in result["applied"]
    # Sprint 11 上半：0004_reference_canon.sql 也应被应用
    assert "0004_reference_canon.sql" in result["applied"]
    # Sprint 10：0005_branches_archived_status.sql（不增表，扩展 CHECK）也应被应用
    assert "0005_branches_archived_status.sql" in result["applied"]
    # Sprint 12：0006_quality_reports_project_idx.sql（补索引）
    assert "0006_quality_reports_project_idx.sql" in result["applied"]
    # Sprint 14：0007_chapter_summaries.sql
    assert "0007_chapter_summaries.sql" in result["applied"]
    # Sprint 15 / V1.3：0008_author_style_samples_and_overdue.sql
    assert "0008_author_style_samples_and_overdue.sql" in result["applied"]


def test_apply_migrations_is_idempotent(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    first = apply_migrations(db_path, MIGRATIONS_DIR)
    # Sprint 15 / V1.3：迁移目录下八份脚本都应被首次应用
    assert first["applied"] == [
        "0001_init.sql",
        "0002_drafts_unique.sql",
        "0003_quality_reports.sql",
        "0004_reference_canon.sql",
        "0005_branches_archived_status.sql",
        "0006_quality_reports_project_idx.sql",
        "0007_chapter_summaries.sql",
        "0008_author_style_samples_and_overdue.sql",
    ]

    second = apply_migrations(db_path, MIGRATIONS_DIR)
    assert second["applied"] == []
    assert "0001_init.sql" in second["skipped"]
    assert "0002_drafts_unique.sql" in second["skipped"]
    assert "0003_quality_reports.sql" in second["skipped"]
    assert "0004_reference_canon.sql" in second["skipped"]
    assert "0005_branches_archived_status.sql" in second["skipped"]
    assert "0006_quality_reports_project_idx.sql" in second["skipped"]
    assert "0007_chapter_summaries.sql" in second["skipped"]
    assert "0008_author_style_samples_and_overdue.sql" in second["skipped"]
    assert second["tables"] == first["tables"]


def test_migrations_table_records_filename(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT filename, applied_at FROM _migrations").fetchall()
    finally:
        conn.close()
    # Sprint 15 / V1.3：八条迁移都应记录
    filenames = {r["filename"] for r in rows}
    assert filenames == {
        "0001_init.sql",
        "0002_drafts_unique.sql",
        "0003_quality_reports.sql",
        "0004_reference_canon.sql",
        "0005_branches_archived_status.sql",
        "0006_quality_reports_project_idx.sql",
        "0007_chapter_summaries.sql",
        "0008_author_style_samples_and_overdue.sql",
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


def test_business_table_count_is_33(tmp_path: Path):
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
    # Sprint 15 / V1.3：业务表 32 + author_style_samples = 33
    assert len(names) == 33, f"expected 33 business tables, got {len(names)}"
    # 抽检：PRD §67 关键表
    for expected in ("projects", "characters", "chapters", "commits", "state_deltas", "ai_call_logs"):
        assert expected in names, f"missing table {expected}"
    assert "quality_reports" in names, "quality_reports table should exist (Sprint 6 下半)"
    assert "reference_canons" in names, "reference_canons table should exist (Sprint 11 上半)"
    assert "canon_extracts" in names, "canon_extracts table should exist (Sprint 11 上半)"
    assert "chapter_summaries" in names, "chapter_summaries table should exist (Sprint 14)"
    assert "author_style_samples" in names, "author_style_samples table should exist (Sprint 15 / V1.3)"


def test_projects_has_foreshadow_overdue_chapters_default(tmp_path: Path):
    """0008 给 projects 加 foreshadow_overdue_chapters 默认 30。"""
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        # 列存在
        cols = conn.execute("PRAGMA table_info(projects)").fetchall()
    finally:
        conn.close()
    col_names = {c["name"] for c in cols}
    assert "foreshadow_overdue_chapters" in col_names, col_names

    # 新插一行 → 默认值 30
    from packages.core.ids import new_id, now_iso
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, "p", now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT foreshadow_overdue_chapters FROM projects WHERE project_id = ?",
            (pid,),
        ).fetchone()
    finally:
        conn.close()
    assert dict(row)["foreshadow_overdue_chapters"] == 30
