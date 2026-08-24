"""迁移 runner 测试（Sprint 0）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def test_apply_migrations_creates_34_business_tables(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    result = apply_migrations(db_path, MIGRATIONS_DIR)
    # 业务表 = 总表 - _migrations
    # Sprint 11 上半：新增 0004_reference_canon（reference_canons + canon_extracts）→ 业务表 31（29+2），总数 32。
    # Sprint 10：0005 是「表重建」（不改表数），仍 32。
    # Sprint 14：0007_chapter_summaries 加 chapter_summaries 业务表 → 业务表 32（31+1），总数 33。
    # Sprint 15 / V1.3：0008_author_style_samples_and_overdue 加 author_style_samples
    #   业务表 → 业务表 33（32+1），总数 34。
    # V2.0 Wave B 任务二：0010_trigger_keys 只加列，不增表 → 业务表 33，总数 34。
    # V2.0 Wave B 任务一：0009_branch_snapshots 加 branch_snapshots
    #   业务表 → 业务表 34（33+1），总数 35。
    # V2.0 Wave C 任务一：0011_fts_index 加 FTS5 虚表（chapter_fts + 4 内部表），但
    #   count_tables 口径排除 ``chapter_fts%`` 前缀 → 业务表仍 34，总表仍 35。
    assert result["tables"] == 35, f"expected 35 (34+_migrations), got {result['tables']}"
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
    # V2.0 Wave B 任务一：0009_branch_snapshots.sql（加 branch_snapshots 表）
    assert "0009_branch_snapshots.sql" in result["applied"]
    # V2.0 Wave B 任务二：0010_trigger_keys.sql（仅 ALTER TABLE 加 aliases + inject_mode）
    assert "0010_trigger_keys.sql" in result["applied"]
    # V2.0 Wave C 任务一：0011_fts_index.sql（建 FTS5 虚表 chapter_fts，不进业务表计数）
    assert "0011_fts_index.sql" in result["applied"]


def test_apply_migrations_is_idempotent(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    first = apply_migrations(db_path, MIGRATIONS_DIR)
    # V2.0 Wave C 任务一：迁移目录下十一条脚本都应被首次应用
    assert first["applied"] == [
        "0001_init.sql",
        "0002_drafts_unique.sql",
        "0003_quality_reports.sql",
        "0004_reference_canon.sql",
        "0005_branches_archived_status.sql",
        "0006_quality_reports_project_idx.sql",
        "0007_chapter_summaries.sql",
        "0008_author_style_samples_and_overdue.sql",
        "0009_branch_snapshots.sql",
        "0010_trigger_keys.sql",
        "0011_fts_index.sql",
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
    assert "0009_branch_snapshots.sql" in second["skipped"]
    assert "0010_trigger_keys.sql" in second["skipped"]
    assert "0011_fts_index.sql" in second["skipped"]
    assert second["tables"] == first["tables"]


def test_migrations_table_records_filename(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT filename, applied_at FROM _migrations").fetchall()
    finally:
        conn.close()
    # V2.0 Wave C 任务一：十一条迁移都应记录
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
        "0009_branch_snapshots.sql",
        "0010_trigger_keys.sql",
        "0011_fts_index.sql",
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


def test_business_table_count_is_34(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            # V2.0 Wave C 任务一：FTS5 虚表 chapter_fts + 4 个内部表
            # (chapter_fts_config/data/docsize/idx) 均以 chapter_fts 为前缀。
            # 业务表口径排除 ``chapter_fts%`` 前缀，与 packages.core.db.count_tables 对齐。
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name <> '_migrations' "
            "AND name NOT LIKE 'chapter_fts%'"
        ).fetchall()
    finally:
        conn.close()
    names = {r["name"] for r in rows}
    # Sprint 15 / V1.3：业务表 32 + author_style_samples = 33
    # V2.0 Wave B 任务一：0009_branch_snapshots 加 branch_snapshots → 业务表 34
    # V2.0 Wave C 任务一：0011_fts_index 加 FTS5 虚表，但口径排除 → 业务表仍 34
    assert len(names) == 34, f"expected 34 business tables, got {len(names)}"
    # 抽检：PRD §67 关键表
    for expected in ("projects", "characters", "chapters", "commits", "state_deltas", "ai_call_logs"):
        assert expected in names, f"missing table {expected}"
    assert "quality_reports" in names, "quality_reports table should exist (Sprint 6 下半)"
    assert "reference_canons" in names, "reference_canons table should exist (Sprint 11 上半)"
    assert "canon_extracts" in names, "canon_extracts table should exist (Sprint 11 上半)"
    assert "chapter_summaries" in names, "chapter_summaries table should exist (Sprint 14)"
    assert "author_style_samples" in names, "author_style_samples table should exist (Sprint 15 / V1.3)"
    assert "branch_snapshots" in names, "branch_snapshots table should exist (V2.0 Wave B 任务一)"


def test_0011_chapter_fts_virtual_table_exists(tmp_path: Path):
    """V2.0 Wave C 任务一：0011 落地 FTS5 虚表 chapter_fts。"""
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT name, type FROM sqlite_master WHERE name = 'chapter_fts'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "chapter_fts virtual table should exist after 0011"
    assert row["type"] == "table", f"chapter_fts should be registered as table, got {row['type']}"


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


def test_0010_trigger_keys_columns_exist_with_defaults(tmp_path: Path):
    """V2.0 Wave B 任务二：0010 给 characters/locations/factions 加 aliases + inject_mode。

    - aliases: TEXT NOT NULL DEFAULT '[]'
    - inject_mode: TEXT NOT NULL DEFAULT 'auto' CHECK in (auto, always, never)
    """
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        for table in ("characters", "locations", "factions"):
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            col_names = {c["name"] for c in cols}
            assert "aliases" in col_names, f"{table} 缺 aliases 列; got={col_names}"
            assert "inject_mode" in col_names, f"{table} 缺 inject_mode 列; got={col_names}"
            # 默认值校验
            row = next(c for c in cols if c["name"] == "aliases")
            assert row["dflt_value"] == "'[]'"
            row2 = next(c for c in cols if c["name"] == "inject_mode")
            assert row2["dflt_value"] == "'auto'"
    finally:
        conn.close()

    # 新插一行 → 默认值生效
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
        cid = new_id("char")
        conn.execute(
            "INSERT INTO characters (character_id, project_id, name, role, "
            "core_json, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, '测试角色', 'supporting', '{}', 'PUBLIC', NULL, ?, ?)",
            (cid, pid, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT aliases, inject_mode FROM characters WHERE character_id = ?",
            (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert dict(row)["aliases"] == "[]"
    assert dict(row)["inject_mode"] == "auto"


def test_0010_inject_mode_check_constraint_enforced(tmp_path: Path):
    """0010 给 inject_mode 加的 CHECK 约束生效：非法值 → IntegrityError。"""
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    from packages.core.ids import new_id, now_iso
    pid = new_id("prj")
    now = now_iso()
    cid = new_id("char")
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, "p", now, now),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO characters (character_id, project_id, name, role, "
                "core_json, visibility, who_knows, created_at, updated_at, "
                "inject_mode) VALUES (?, ?, 'x', 'supporting', '{}', 'PUBLIC', "
                "NULL, ?, ?, 'bogus')",
                (cid, pid, now, now),
            )
    finally:
        conn.close()
