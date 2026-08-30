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
    # V3.7 模型档案 + 环节绑定：0016 加 model_profiles + capability_bindings → 业务表 37（35+2），总表 38。
    assert result["tables"] == 38, f"expected 38 (37+_migrations), got {result['tables']}"
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
    # V3.7 模型档案 + 环节绑定：0016_model_profiles.sql（model_profiles + capability_bindings；总表 38）。
    assert "0016_model_profiles.sql" in result["applied"]
    # 0017 关键表 UNIQUE 兜底（relationships / workflow_runs 部分索引）
    assert "0017_unique_constraints.sql" in result["applied"]
    # V3.9.3 observer 独立 capability 绑定：0018_observer_capability_binding.sql
    assert "0018_observer_capability_binding.sql" in result["applied"]
    # V3.1 P1-1.1 B3 修复：0019_backfill_timeline_events.sql（不回填新表，仅补数据）
    assert "0019_backfill_timeline_events.sql" in result["applied"]
    # V3.10 init 空壳 plot_event 修复：0020_backfill_init_plot_event_description.sql
    # （仅 ALTER TABLE 加列 + UPDATE 回填，不增表；总表 38 不变）
    assert "0020_backfill_init_plot_event_description.sql" in result["applied"]


def test_apply_migrations_is_idempotent(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    first = apply_migrations(db_path, MIGRATIONS_DIR)
    # V3.7 模型档案 + 环节绑定：0016 加入；V3.9.3 observer 独立 capability 绑定
    # 0018 也要首次应用；V3.1 P1-1.1 B3 修复 0019 回填 timeline_events 也要首次应用。
    # 迁移目录下一共 19 个脚本都应被首次应用。
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
        "0016_model_profiles.sql",
        "0017_unique_constraints.sql",
        "0018_observer_capability_binding.sql",
        "0019_backfill_timeline_events.sql",
        "0020_backfill_init_plot_event_description.sql",
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
    # V3.7 模型档案 + 环节绑定：0016 也应被幂等跳过
    assert "0016_model_profiles.sql" in second["skipped"]
    # 0017 关键表 UNIQUE 兜底：也应被幂等跳过
    assert "0017_unique_constraints.sql" in second["skipped"]
    # V3.9.3 observer 拆为独立 capability 绑定：0018 也应被幂等跳过
    assert "0018_observer_capability_binding.sql" in second["skipped"]
    # V3.1 P1-1.1 B3 修复：0019 回填 timeline_events 也应被幂等跳过
    assert "0019_backfill_timeline_events.sql" in second["skipped"]
    # V3.10 init 空壳 plot_event 修复：0020 也应被幂等跳过
    assert "0020_backfill_init_plot_event_description.sql" in second["skipped"]
    assert second["tables"] == first["tables"]


def test_migrations_table_records_filename(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT filename, applied_at FROM _migrations").fetchall()
    finally:
        conn.close()
    # V3.7 模型档案 + 环节绑定：十六条迁移都应记录
    # 0017 关键表 UNIQUE 兜底（relationships / workflow_runs）
    # 0018 V3.9.3 observer 独立 capability 绑定（profile_ids 继承 reasoning）
    # 0019 V3.1 P1-1.1 B3 回填 timeline_events 索引
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
        "0016_model_profiles.sql",
        "0017_unique_constraints.sql",
        "0018_observer_capability_binding.sql",
        "0019_backfill_timeline_events.sql",
        "0020_backfill_init_plot_event_description.sql",
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
    # V3.7 模型档案 + 环节绑定：0016 加 model_profiles + capability_bindings → 业务表 37
    assert len(names) == 37, f"expected 37 business tables, got {len(names)}"
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
    assert "model_profiles" in names, "model_profiles table should exist (V3.7 模型档案 + 环节绑定)"
    assert "capability_bindings" in names, "capability_bindings table should exist (V3.7 模型档案 + 环节绑定)"


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


def test_0020_volumes_has_arc_summary_column(tmp_path: Path):
    """V3.10 init 空壳修复：0020 给 volumes 加 arc_summary TEXT（可空）。"""
    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        cols = conn.execute("PRAGMA table_info(volumes)").fetchall()
    finally:
        conn.close()
    col_names = {c["name"] for c in cols}
    assert "arc_summary" in col_names, (
        f"volumes missing arc_summary after 0020; got={col_names}"
    )
    arc_col = next(c for c in cols if c["name"] == "arc_summary")
    assert arc_col["type"] == "TEXT", arc_col
    assert arc_col["notnull"] == 0, arc_col
    assert arc_col["dflt_value"] is None, arc_col


def test_0020_backfills_empty_shell_plot_event_description(tmp_path: Path):
    """V3.10 init 空壳修复：0020 把 workflow_runs.checkpoint_json 里的
    volume.arc_summary 反向写入 volumes.arc_summary，并用其回填 plot_events
    与 timeline_events 空壳事件的 description。

    - volumes.arc_summary 被回填（取 workflow_runs.checkpoint_json 中
      $.volume.arc_summary，按 chapter_id → chapters.project_id 关联，
      number 匹配）；
    - plot_events 中 type='other' AND status='planned' AND description 空
      AND introduced_chapter_id IS NULL 的空壳被回填；
    - timeline_events 对应行（event_id 匹配且 description IS NULL）被回填；
    - 已正常描述的事件 / 已 commit 的事件 / 非空壳事件均不被覆盖。
    """
    from packages.core.ids import new_id, now_iso
    import json

    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)

    pid = new_id("prj")
    vid = new_id("vol")
    eid = new_id("event")
    tid = new_id("tle")
    cid = new_id("ch")
    run_id = new_id("wfr")
    now = now_iso()
    arc = "叶尘从废脉少年踏上星辰之路"

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, "
            "target_words, status, created_at, updated_at) VALUES "
            "(?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, "p", now, now),
        )
        # volumes.arc_summary 留 NULL（让 0020 从 workflow_runs 回填）
        conn.execute(
            "INSERT INTO volumes (volume_id, project_id, number, title, "
            "status, terminal_snapshot_json, created_at, updated_at) "
            "VALUES (?, ?, 1, '星落青石', 'active', NULL, ?, ?)",
            (vid, pid, now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, "
            "updated_at, volume_id) VALUES (?, ?, 1, 'ch1', '{}', "
            "'PLANNED', 'VISIBLE', NULL, ?, ?, ?)",
            (cid, pid, now, now, vid),
        )
        # workflow_runs.workflow_id 需先在 workflows 落行（FK 约束）
        wf_id = new_id("wf")
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, "
            "definition_json, created_at, updated_at) VALUES (?, 'wf', "
            "'v1', '{}', ?, ?)",
            (wf_id, now, now),
        )
        # workflow_runs.checkpoint_json 含 volume.arc_summary
        ck = json.dumps(
            {"volume": {"number": 1, "title": "星落青石", "arc_summary": arc}},
            ensure_ascii=False,
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, "
            "status, current_node, checkpoint_json, error, retry_count, "
            "started_at, ended_at) VALUES (?, ?, ?, 'COMPLETED', NULL, "
            "?, NULL, 0, ?, ?)",
            (run_id, wf_id, cid, ck, now, now),
        )
        # 空壳 plot_event（description=NULL, introduced_chapter_id=NULL）
        conn.execute(
            "INSERT INTO plot_events (event_id, project_id, type, "
            "cause_json, effects_json, participants_json, location_id, "
            "time_json, status, introduced_chapter_id, visibility, "
            "who_knows, description) VALUES (?, ?, 'other', '[]', '[]', "
            "'[]', NULL, '{\"timeline_day\": 1, \"in_story_date\": null}', "
            "'planned', NULL, 'RESTRICTED', NULL, NULL)",
            (eid, pid),
        )
        # timeline_events 对应索引行（description=NULL）
        conn.execute(
            "INSERT INTO timeline_events (timeline_event_id, project_id, "
            "event_id, day_index, time_ref, description, visibility, "
            "who_knows) VALUES (?, ?, ?, 1, NULL, NULL, 'PUBLIC', NULL)",
            (tid, pid, eid),
        )
        # 正常事件（已有 description、已 commit、引入过 chapter）— 不应被覆盖
        normal_eid = new_id("event")
        normal_desc = "已写好的事件描述，不应被覆盖"
        conn.execute(
            "INSERT INTO plot_events (event_id, project_id, type, "
            "cause_json, effects_json, participants_json, location_id, "
            "time_json, status, introduced_chapter_id, visibility, "
            "who_knows, description) VALUES (?, ?, 'revelation', '[]', "
            "'[]', '[]', NULL, '{\"timeline_day\": 2}', 'recorded', "
            "?, 'RESTRICTED', NULL, ?)",
            (normal_eid, pid, cid, normal_desc),
        )
        # type='other' 但 status='recorded'（不应被当作空壳）
        committed_other = new_id("event")
        conn.execute(
            "INSERT INTO plot_events (event_id, project_id, type, "
            "cause_json, effects_json, participants_json, location_id, "
            "time_json, status, introduced_chapter_id, visibility, "
            "who_knows, description) VALUES (?, ?, 'other', '[]', '[]', "
            "'[]', NULL, '{\"timeline_day\": 3}', 'recorded', ?, "
            "'RESTRICTED', NULL, NULL)",
            (committed_other, pid, cid),
        )
        conn.commit()
    finally:
        conn.close()

    # 此时 apply_migrations(db_path, MIGRATIONS_DIR) 已记录 0020 为已应用；
    # _migrations 跳过 0020 → UPDATE 部分不会自动跑。
    # 拆出 0020 的 UPDATE 语句手工执行（模拟「首次执行 0020」语义）：
    # - volumes UPDATE：从 workflow_runs.checkpoint_json 反向写 volumes.arc_summary
    # - plot_events UPDATE：空壳 description 回填
    # - timeline_events UPDATE：对应索引行 description 回填
    # 这些 UPDATE 都用 WHERE 守卫，重跑幂等。
    update_sql = """
    UPDATE volumes
    SET arc_summary = (
        SELECT json_extract(wr.checkpoint_json, '$.volume.arc_summary')
        FROM workflow_runs AS wr
        JOIN chapters AS ch ON ch.chapter_id = wr.chapter_id
        WHERE ch.project_id = volumes.project_id
          AND json_extract(wr.checkpoint_json, '$.volume.arc_summary') IS NOT NULL
          AND trim(json_extract(wr.checkpoint_json, '$.volume.arc_summary')) != ''
          AND json_extract(wr.checkpoint_json, '$.volume.number') = volumes.number
        ORDER BY wr.started_at DESC
        LIMIT 1
    )
    WHERE EXISTS (
        SELECT 1
        FROM workflow_runs AS wr
        JOIN chapters AS ch ON ch.chapter_id = wr.chapter_id
        WHERE ch.project_id = volumes.project_id
          AND json_extract(wr.checkpoint_json, '$.volume.arc_summary') IS NOT NULL
          AND trim(json_extract(wr.checkpoint_json, '$.volume.arc_summary')) != ''
          AND json_extract(wr.checkpoint_json, '$.volume.number') = volumes.number
    );

    UPDATE plot_events
    SET description = (
        SELECT v.arc_summary
        FROM volumes AS v
        WHERE v.project_id = plot_events.project_id
          AND v.arc_summary IS NOT NULL
          AND trim(v.arc_summary) != ''
        ORDER BY v.number ASC
        LIMIT 1
    )
    WHERE type = 'other'
      AND status = 'planned'
      AND (description IS NULL OR trim(description) = '')
      AND introduced_chapter_id IS NULL
      AND EXISTS (
          SELECT 1
          FROM volumes AS v
          WHERE v.project_id = plot_events.project_id
            AND v.arc_summary IS NOT NULL
            AND trim(v.arc_summary) != ''
      );

    UPDATE timeline_events
    SET description = (
        SELECT v.arc_summary
        FROM plot_events AS pe
        JOIN volumes AS v
          ON v.project_id = pe.project_id
         AND v.arc_summary IS NOT NULL
         AND trim(v.arc_summary) != ''
        WHERE pe.event_id = timeline_events.event_id
        ORDER BY v.number ASC
        LIMIT 1
    )
    WHERE description IS NULL
      AND EXISTS (
          SELECT 1
          FROM plot_events AS pe
          JOIN volumes AS v
            ON v.project_id = pe.project_id
           AND v.arc_summary IS NOT NULL
           AND trim(v.arc_summary) != ''
          WHERE pe.event_id = timeline_events.event_id
      );
    """
    conn = get_connection(db_path)
    try:
        conn.executescript(update_sql)
        conn.commit()
    finally:
        conn.close()

    conn = get_connection(db_path)
    try:
        v_row = conn.execute(
            "SELECT arc_summary FROM volumes WHERE volume_id = ?", (vid,)
        ).fetchone()
        assert dict(v_row)["arc_summary"] == arc, (
            f"volumes.arc_summary 应被回填；实得 {dict(v_row)['arc_summary']!r}"
        )

        pe_row = conn.execute(
            "SELECT description FROM plot_events WHERE event_id = ?", (eid,)
        ).fetchone()
        assert dict(pe_row)["description"] == arc, (
            f"空壳 plot_events.description 应被回填；实得 {dict(pe_row)['description']!r}"
        )

        te_row = conn.execute(
            "SELECT description FROM timeline_events WHERE timeline_event_id = ?",
            (tid,),
        ).fetchone()
        assert dict(te_row)["description"] == arc, (
            f"对应 timeline_events.description 应被回填；实得 {dict(te_row)['description']!r}"
        )

        normal_row = conn.execute(
            "SELECT description FROM plot_events WHERE event_id = ?",
            (normal_eid,),
        ).fetchone()
        assert dict(normal_row)["description"] == normal_desc, (
            "正常事件 description 不应被覆盖"
        )

        committed_row = conn.execute(
            "SELECT description FROM plot_events WHERE event_id = ?",
            (committed_other,),
        ).fetchone()
        assert dict(committed_row)["description"] is None, (
            "type='other' 但 status='recorded' 的事件不视作空壳，"
            "description 应保留 NULL"
        )
    finally:
        conn.close()


def test_0020_idempotent_rerun_no_changes(tmp_path: Path):
    """V3.10 init 空壳修复：0020 重跑 SQL 不应改写已回填的行（幂等）。"""
    from packages.core.ids import new_id, now_iso
    import json

    db_path = _fresh_db(tmp_path)
    apply_migrations(db_path, MIGRATIONS_DIR)

    pid = new_id("prj")
    vid = new_id("vol")
    eid = new_id("event")
    tid = new_id("tle")
    cid = new_id("ch")
    run_id = new_id("wfr")
    now = now_iso()
    arc = "回填后的 arc_summary"

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, "
            "target_words, status, created_at, updated_at) VALUES "
            "(?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, "p", now, now),
        )
        conn.execute(
            "INSERT INTO volumes (volume_id, project_id, number, title, "
            "status, terminal_snapshot_json, created_at, updated_at) "
            "VALUES (?, ?, 1, 'vol', 'active', NULL, ?, ?)",
            (vid, pid, now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, "
            "updated_at, volume_id) VALUES (?, ?, 1, 'ch1', '{}', "
            "'PLANNED', 'VISIBLE', NULL, ?, ?, ?)",
            (cid, pid, now, now, vid),
        )
        wf_id = new_id("wf")
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, "
            "definition_json, created_at, updated_at) VALUES (?, 'wf', "
            "'v1', '{}', ?, ?)",
            (wf_id, now, now),
        )
        ck = json.dumps(
            {"volume": {"number": 1, "title": "vol", "arc_summary": arc}},
            ensure_ascii=False,
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, "
            "status, current_node, checkpoint_json, error, retry_count, "
            "started_at, ended_at) VALUES (?, ?, ?, 'COMPLETED', NULL, "
            "?, NULL, 0, ?, ?)",
            (run_id, wf_id, cid, ck, now, now),
        )
        conn.execute(
            "INSERT INTO plot_events (event_id, project_id, type, "
            "cause_json, effects_json, participants_json, location_id, "
            "time_json, status, introduced_chapter_id, visibility, "
            "who_knows, description) VALUES (?, ?, 'other', '[]', '[]', "
            "'[]', NULL, '{\"timeline_day\": 1, \"in_story_date\": null}', "
            "'planned', NULL, 'RESTRICTED', NULL, NULL)",
            (eid, pid),
        )
        conn.execute(
            "INSERT INTO timeline_events (timeline_event_id, project_id, "
            "event_id, day_index, time_ref, description, visibility, "
            "who_knows) VALUES (?, ?, ?, 1, NULL, NULL, 'PUBLIC', NULL)",
            (tid, pid, eid),
        )
        conn.commit()
    finally:
        conn.close()

    # 0020 的 UPDATE 部分（不含 ALTER TABLE——apply_migrations 已记录 0020，
    # _migrations 跳过 0020 → 重跑文件会先 ALTER 报 duplicate column）。
    # 直接 exec UPDATE 部分验证幂等。
    update_sql = """
    UPDATE volumes
    SET arc_summary = (
        SELECT json_extract(wr.checkpoint_json, '$.volume.arc_summary')
        FROM workflow_runs AS wr
        JOIN chapters AS ch ON ch.chapter_id = wr.chapter_id
        WHERE ch.project_id = volumes.project_id
          AND json_extract(wr.checkpoint_json, '$.volume.arc_summary') IS NOT NULL
          AND trim(json_extract(wr.checkpoint_json, '$.volume.arc_summary')) != ''
          AND json_extract(wr.checkpoint_json, '$.volume.number') = volumes.number
        ORDER BY wr.started_at DESC
        LIMIT 1
    )
    WHERE EXISTS (
        SELECT 1
        FROM workflow_runs AS wr
        JOIN chapters AS ch ON ch.chapter_id = wr.chapter_id
        WHERE ch.project_id = volumes.project_id
          AND json_extract(wr.checkpoint_json, '$.volume.arc_summary') IS NOT NULL
          AND trim(json_extract(wr.checkpoint_json, '$.volume.arc_summary')) != ''
          AND json_extract(wr.checkpoint_json, '$.volume.number') = volumes.number
    );

    UPDATE plot_events
    SET description = (
        SELECT v.arc_summary
        FROM volumes AS v
        WHERE v.project_id = plot_events.project_id
          AND v.arc_summary IS NOT NULL
          AND trim(v.arc_summary) != ''
        ORDER BY v.number ASC
        LIMIT 1
    )
    WHERE type = 'other'
      AND status = 'planned'
      AND (description IS NULL OR trim(description) = '')
      AND introduced_chapter_id IS NULL
      AND EXISTS (
          SELECT 1
          FROM volumes AS v
          WHERE v.project_id = plot_events.project_id
            AND v.arc_summary IS NOT NULL
            AND trim(v.arc_summary) != ''
      );

    UPDATE timeline_events
    SET description = (
        SELECT v.arc_summary
        FROM plot_events AS pe
        JOIN volumes AS v
          ON v.project_id = pe.project_id
         AND v.arc_summary IS NOT NULL
         AND trim(v.arc_summary) != ''
        WHERE pe.event_id = timeline_events.event_id
        ORDER BY v.number ASC
        LIMIT 1
    )
    WHERE description IS NULL
      AND EXISTS (
          SELECT 1
          FROM plot_events AS pe
          JOIN volumes AS v
            ON v.project_id = pe.project_id
           AND v.arc_summary IS NOT NULL
           AND trim(v.arc_summary) != ''
          WHERE pe.event_id = timeline_events.event_id
      );
    """
    # 第一次跑
    conn = get_connection(db_path)
    try:
        conn.executescript(update_sql)
        conn.commit()
    finally:
        conn.close()

    # 第二次跑：应当影响 0 行（WHERE 条件已不再匹配）
    conn = get_connection(db_path)
    try:
        before_v = conn.execute(
            "SELECT arc_summary FROM volumes WHERE volume_id = ?", (vid,)
        ).fetchone()["arc_summary"]
        before_pe = conn.execute(
            "SELECT description FROM plot_events WHERE event_id = ?", (eid,)
        ).fetchone()["description"]
        before_te = conn.execute(
            "SELECT description FROM timeline_events WHERE timeline_event_id = ?",
            (tid,),
        ).fetchone()["description"]
    finally:
        conn.close()

    conn = get_connection(db_path)
    try:
        conn.executescript(update_sql)
        conn.commit()
    finally:
        conn.close()

    conn = get_connection(db_path)
    try:
        after_v = conn.execute(
            "SELECT arc_summary FROM volumes WHERE volume_id = ?", (vid,)
        ).fetchone()["arc_summary"]
        after_pe = conn.execute(
            "SELECT description FROM plot_events WHERE event_id = ?", (eid,)
        ).fetchone()["description"]
        after_te = conn.execute(
            "SELECT description FROM timeline_events WHERE timeline_event_id = ?",
            (tid,),
        ).fetchone()["description"]
    finally:
        conn.close()

    assert after_v == before_v == arc
    assert after_pe == before_pe == arc
    assert after_te == before_te == arc
