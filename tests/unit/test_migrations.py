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
    # V3.1 P1-2：0012_judge_scores 仅 ALTER TABLE quality_reports 加 judge_json 列，
    #   不增表 → 业务表 34，总表 35。
    # V3.1 P1-1.1：0013_plot_events_description 仅 ALTER TABLE plot_events 加 description
    #   列，不增表 → 业务表 34，总表 35。
    # V3.3 P0-2（知识权限补全）：0014_knowledge_reveal 给 relationships/timeline_events/scenes
    #   三表加 visibility+who_knows 列；DROP 旧 reveal_policies（0001 v1.1）后按 v3.3 schema
    #   重建——表数不变，业务表 34，总表 35。
    # V3.4 多卷与规模（组织层）：0015_volumes 加 volumes 业务表 → 业务表 35（34+1），总表 36。
    assert result["tables"] == 36, f"expected 36 (35+_migrations), got {result['tables']}"
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
    # V3.1 P1-2：0012_judge_scores.sql（仅 ALTER TABLE 加 judge_json 列，不增表）
    assert "0012_judge_scores.sql" in result["applied"]
    # V3.1 P1-1.1：0013_plot_events_description.sql（仅 ALTER TABLE 加 description 列，不增表）
    assert "0013_plot_events_description.sql" in result["applied"]
    # V3.3 P0-2（知识权限补全）：0014_knowledge_reveal.sql（三表加列 + reveal_policies 重建，
    #   表数不变；总表仍 35）。
    assert "0014_knowledge_reveal.sql" in result["applied"]
    # V3.4 多卷与规模（组织层）：0015_volumes.sql（volumes 表 + chapters.volume_id；总表 36）。
    assert "0015_volumes.sql" in result["applied"]


def test_apply_migrations_is_idempotent(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    first = apply_migrations(db_path, MIGRATIONS_DIR)
    # V3.4 多卷与规模（组织层）：迁移目录下十五条脚本都应被首次应用
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
        "0012_judge_scores.sql",
        "0013_plot_events_description.sql",
        "0014_knowledge_reveal.sql",
        "0015_volumes.sql",
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
    assert "0012_judge_scores.sql" in second["skipped"]
    assert "0013_plot_events_description.sql" in second["skipped"]
    # V3.3 P0-2（知识权限补全）：0014 也应被幂等跳过
    assert "0014_knowledge_reveal.sql" in second["skipped"]
    # V3.4 多卷与规模（组织层）：0015 也应被幂等跳过
    assert "0015_volumes.sql" in second["skipped"]
    assert second["tables"] == first["tables"]


def test_migrations_table_records_filename(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT filename, applied_at FROM _migrations").fetchall()
    finally:
        conn.close()
    # V3.4 多卷与规模（组织层）：十五条迁移都应记录
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
        "0012_judge_scores.sql",
        "0013_plot_events_description.sql",
        "0014_knowledge_reveal.sql",
        "0015_volumes.sql",
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
    # V3.4 多卷与规模（组织层）：0015_volumes 加 volumes 业务表 → 业务表 35
    assert len(names) == 35, f"expected 35 business tables, got {len(names)}"
    # 抽检：PRD §67 关键表
    for expected in ("projects", "characters", "chapters", "commits", "state_deltas", "ai_call_logs"):
        assert expected in names, f"missing table {expected}"
    assert "quality_reports" in names, "quality_reports table should exist (Sprint 6 下半)"
    assert "reference_canons" in names, "reference_canons table should exist (Sprint 11 上半)"
    assert "canon_extracts" in names, "canon_extracts table should exist (Sprint 11 上半)"
    assert "chapter_summaries" in names, "chapter_summaries table should exist (Sprint 14)"
    assert "author_style_samples" in names, "author_style_samples table should exist (Sprint 15 / V1.3)"
    assert "branch_snapshots" in names, "branch_snapshots table should exist (V2.0 Wave B 任务一)"
    assert "volumes" in names, "volumes table should exist (V3.4 多卷与规模组织层)"


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


def test_0012_quality_reports_has_judge_json_column(tmp_path: Path):
    """V3.1 P1-2：0012 给 quality_reports 加 judge_json TEXT（默认 NULL）。"""
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        cols = conn.execute("PRAGMA table_info(quality_reports)").fetchall()
    finally:
        conn.close()
    col_names = {c["name"] for c in cols}
    assert "judge_json" in col_names, (
        f"quality_reports missing judge_json after 0012; got={col_names}"
    )
    # 验证该列可空（用于「该章节暂未评审」语义；旧行迁移后保持 NULL）
    judge_col = next(c for c in cols if c["name"] == "judge_json")
    assert judge_col["type"] == "TEXT", judge_col
    assert judge_col["notnull"] == 0, judge_col
    assert judge_col["dflt_value"] is None, judge_col


def test_0013_plot_events_has_description_column(tmp_path: Path):
    """V3.1 P1-1.1：0013 给 plot_events 加 description TEXT（默认 NULL）。

    observer 在 ``new_events[]`` 给出的 description 字段经 write_through 落库；
    旧行迁移后保持 NULL（与 Schema ``description: string|null`` 对齐）。
    """
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        cols = conn.execute("PRAGMA table_info(plot_events)").fetchall()
    finally:
        conn.close()
    col_names = {c["name"] for c in cols}
    assert "description" in col_names, (
        f"plot_events missing description after 0013; got={col_names}"
    )
    desc_col = next(c for c in cols if c["name"] == "description")
    assert desc_col["type"] == "TEXT", desc_col
    # 可空：observer 给出 None / 缺失时落库即为 NULL；旧行迁移后保持 NULL。
    assert desc_col["notnull"] == 0, desc_col
    assert desc_col["dflt_value"] is None, desc_col


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


def test_0014_relationships_timeline_events_scenes_have_visibility_columns(tmp_path: Path):
    """V3.3 P0-2（知识权限补全）：0014 给 relationships / timeline_events / scenes
    三表补齐 visibility + who_knows 字段（与既有 9 张实体表对齐）。

    - visibility NOT NULL DEFAULT 'PUBLIC'；
    - who_knows 可空（与 9 张表口径一致；NULL=沿用默认）；
    - 三表旧行迁移后默认值生效。
    """
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        for table in ("relationships", "timeline_events", "scenes"):
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            col_names = {c["name"] for c in cols}
            assert "visibility" in col_names, (
                f"{table} 缺 visibility 列（0014 应补）; got={col_names}"
            )
            assert "who_knows" in col_names, (
                f"{table} 缺 who_knows 列（0014 应补）; got={col_names}"
            )
            vis_col = next(c for c in cols if c["name"] == "visibility")
            assert vis_col["type"] == "TEXT", vis_col
            assert vis_col["notnull"] == 1, vis_col
            assert vis_col["dflt_value"] == "'PUBLIC'", vis_col
            wk_col = next(c for c in cols if c["name"] == "who_knows")
            assert wk_col["type"] == "TEXT", wk_col
            # who_knows 可空（旧行 NULL=沿用默认）
            assert wk_col["notnull"] == 0, wk_col
            assert wk_col["dflt_value"] is None, wk_col
    finally:
        conn.close()


def test_0014_reveal_policies_table_v3_3_schema(tmp_path: Path):
    """V3.3 P0-2（知识权限补全）：0014 重建 reveal_policies 表（按 v3.3 schema 替换 0001 v1.1）。

    - 必含列：policy_id / project_id / target_kind / target_id /
      reveal_by_chapter / audience / status / revealed_chapter / notes /
      created_at / updated_at；
    - target_kind CHECK 枚举 8 种（character/location/faction/world_rule/event/hook/debt/relationship）；
    - status CHECK planned/revealed/cancelled；
    - audience 默认 'reader'；
    - status 默认 'planned'；
    - 索引 idx_rp_target(project_id, target_kind, target_id) 存在；
    - 旧索引 idx_reveal_policies_project_id/target 已删。
    """
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        cols = conn.execute("PRAGMA table_info(reveal_policies)").fetchall()
    finally:
        conn.close()
    col_names = {c["name"] for c in cols}
    expected_cols = {
        "policy_id", "project_id", "target_kind", "target_id",
        "reveal_by_chapter", "audience", "status", "revealed_chapter",
        "notes", "created_at", "updated_at",
    }
    assert expected_cols <= col_names, (
        f"reveal_policies 缺列; got={col_names}, expected⊆={expected_cols}"
    )
    # 必无 v1.1 老字段
    for old_col in ("target_type", "from_chapter_id", "until_chapter_id", "policy", "note"):
        assert old_col not in col_names, (
            f"reveal_policies 不应再有 v1.1 列 {old_col!r}; got={col_names}"
        )

    # status / audience 默认值
    status_col = next(c for c in cols if c["name"] == "status")
    assert status_col["dflt_value"] == "'planned'", status_col
    audience_col = next(c for c in cols if c["name"] == "audience")
    assert audience_col["dflt_value"] == "'reader'", audience_col

    # 索引校验
    conn = get_connection(db_path)
    try:
        idx_names = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='reveal_policies'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert "idx_rp_target" in idx_names, (
        f"reveal_policies 缺 idx_rp_target 索引; got={idx_names}"
    )
    # 旧索引已 DROP
    for old_idx in ("idx_reveal_policies_project_id", "idx_reveal_policies_target"):
        assert old_idx not in idx_names, (
            f"reveal_policies 不应再有旧索引 {old_idx!r}; got={idx_names}"
        )


def test_0014_reveal_policies_target_kind_check_constraint(tmp_path: Path):
    """V3.3 P0-2（知识权限补全）：0014 给 reveal_policies.target_kind 加 CHECK 约束，
    非法值 → IntegrityError。"""
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
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
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO reveal_policies "
                "(policy_id, project_id, target_kind, target_id, "
                "reveal_by_chapter, audience, status, "
                "revealed_chapter, notes, created_at, updated_at) "
                "VALUES (?, ?, 'bogus_kind', 'x', NULL, 'reader', 'planned', "
                "NULL, NULL, ?, ?)",
                (new_id("rp"), pid, now, now),
            )
    finally:
        conn.close()


def test_0014_reveal_policies_status_check_constraint(tmp_path: Path):
    """V3.3 P0-2（知识权限补全）：0014 给 reveal_policies.status 加 CHECK 约束。"""
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
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
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO reveal_policies "
                "(policy_id, project_id, target_kind, target_id, "
                "reveal_by_chapter, audience, status, "
                "revealed_chapter, notes, created_at, updated_at) "
                "VALUES (?, ?, 'character', 'char_x', NULL, 'reader', "
                "'bogus_status', NULL, NULL, ?, ?)",
                (new_id("rp"), pid, now, now),
            )
    finally:
        conn.close()


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


def test_0015_volumes_table_and_chapters_volume_id_column(tmp_path: Path):
    """V3.4 多卷与规模（组织层）：0015_volumes 落地 volumes 表 + chapters.volume_id。

    - volumes 表存在且列齐（volume_id / project_id / number / title / status /
      terminal_snapshot_json / created_at / updated_at）；
    - status 默认 'active'；CHECK 枚举 active/sealed；
    - UNIQUE(project_id, number) 约束生效；
    - chapters.volume_id 列存在、可空、与 volumes.volume_id FK 关联。
    """
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        # 1) volumes 表存在
        cols = conn.execute("PRAGMA table_info(volumes)").fetchall()
    finally:
        conn.close()
    col_names = {c["name"] for c in cols}
    expected = {
        "volume_id", "project_id", "number", "title", "status",
        "terminal_snapshot_json", "created_at", "updated_at",
    }
    assert expected <= col_names, (
        f"volumes 缺列; got={col_names}, expected⊆={expected}"
    )

    # 2) status 默认值与 CHECK（合法值写入成功）
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
        vid = new_id("vol")
        conn.execute(
            """
            INSERT INTO volumes
                (volume_id, project_id, number, title, status,
                 terminal_snapshot_json, created_at, updated_at)
            VALUES (?, ?, 1, '第一卷', 'active', NULL, ?, ?)
            """,
            (vid, pid, now, now),
        )
        conn.commit()

        # 3) CHECK 非法 status → IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO volumes
                    (volume_id, project_id, number, title, status,
                     terminal_snapshot_json, created_at, updated_at)
                VALUES (?, ?, 2, 'x', 'bogus_status', NULL, ?, ?)
                """,
                (new_id("vol"), pid, now, now),
            )

        # 4) UNIQUE(project_id, number) 生效
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO volumes
                    (volume_id, project_id, number, title, status,
                     terminal_snapshot_json, created_at, updated_at)
                VALUES (?, ?, 1, 'dup', 'active', NULL, ?, ?)
                """,
                (new_id("vol"), pid, now, now),
            )
    finally:
        conn.close()

    # 5) chapters.volume_id 列存在
    conn = get_connection(db_path)
    try:
        ch_cols = conn.execute("PRAGMA table_info(chapters)").fetchall()
    finally:
        conn.close()
    ch_col_names = {c["name"] for c in ch_cols}
    assert "volume_id" in ch_col_names, (
        f"chapters 缺 volume_id 列（0015 应补）; got={ch_col_names}"
    )
    vid_col = next(c for c in ch_cols if c["name"] == "volume_id")
    assert vid_col["type"] == "TEXT"
    # 可空（旧章节不强制回填）
    assert vid_col["notnull"] == 0
    assert vid_col["dflt_value"] is None

    # 6) chapters.volume_id FK 存在（FOREIGN KEY(volume_id) REFERENCES volumes(volume_id)）
    conn = get_connection(db_path)
    try:
        fks = conn.execute("PRAGMA foreign_key_list(chapters)").fetchall()
    finally:
        conn.close()
    fk_targets = {(fk["from"], fk["table"], fk["to"]) for fk in fks}
    assert ("volume_id", "volumes", "volume_id") in fk_targets, (
        f"chapters.volume_id FK 到 volumes(volume_id) 缺失; got={fk_targets}"
    )


def test_0015_volumes_status_check_constraint_enforced(tmp_path: Path):
    """V3.4 多卷与规模（组织层）：0015 给 volumes.status 加的 CHECK 约束生效。"""
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
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
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO volumes
                    (volume_id, project_id, number, title, status,
                     terminal_snapshot_json, created_at, updated_at)
                VALUES (?, ?, 1, 'x', 'archived', NULL, ?, ?)
                """,
                (new_id("vol"), pid, now, now),
            )
    finally:
        conn.close()
