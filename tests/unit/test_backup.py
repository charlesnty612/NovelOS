"""Backup 模块单元测试（V1.4 Sprint 16 / MVP）。

覆盖（任务书 V1.4 §100/§101）：
- 导出：含全部应含表 + metadata 自证字段；坏包 422 / 坏项目 ValueError。
- 导入：回环一致性 + id 重映射无残留 + 事务回滚不留半成品。
- 安全红线：包内不出现 api_key 字段值；不含运行时表（ai_call_logs / workflow_runs）。
- 错误格式 / 版本：直接 ValueError（router 转 422）。

测试模式与 ``tests/api/test_quality.py`` 一致：
``Settings(data_dir=tmp_path)`` 拉临时 db；直连数据库构造多表数据；
对包 dict 做递归扫描（``json.dumps`` 后正则）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from packages.core.backup import (
    BACKUP_FORMAT,
    BACKUP_VERSION,
    EXPORTED_TABLES,
    BackupService,
    validate_backup,
)
from packages.core.backup.json_ids import JSON_ID_COLUMNS
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    """临时 SQLite db（已应用全部迁移）。"""
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path)


def _insert_row(conn, sql: str, params: dict) -> None:
    conn.execute(sql, params)
    conn.commit()


def _seed_minimal_project(db_path: str) -> str:
    """构造一个最小可工作的项目：projects + character + chapter + draft + hook。

    返回 project_id。
    """
    now = now_iso()
    pid = new_id("prj")
    conn = get_connection(db_path)
    try:
        _insert_row(
            conn,
            """
            INSERT INTO projects
                (project_id, name, premise, genre, target_words, status,
                 foreshadow_overdue_chapters, created_at, updated_at)
            VALUES
                (:project_id, :name, :premise, :genre, :target_words,
                 'ACTIVE', 30, :created_at, :updated_at)
            """,
            {
                "project_id": pid,
                "name": "测试项目",
                "premise": "单元测试",
                "genre": "科幻",
                "target_words": 100_000,
                "created_at": now,
                "updated_at": now,
            },
        )
        cid_char = new_id("char")
        _insert_row(
            conn,
            """
            INSERT INTO characters
                (character_id, project_id, name, role, core_json, visibility,
                 who_knows, created_at, updated_at)
            VALUES
                (:character_id, :project_id, :name, 'supporting', '{}',
                 'PUBLIC', NULL, :created_at, :updated_at)
            """,
            {
                "character_id": cid_char,
                "project_id": pid,
                "name": "测试角色",
                "created_at": now,
                "updated_at": now,
            },
        )
        # character_states（复合主键）
        _insert_row(
            conn,
            """
            INSERT INTO character_states
                (character_id, state_version, state_json, visibility,
                 who_knows, created_at)
            VALUES
                (:character_id, 1, '{}', 'VISIBLE', NULL, :created_at)
            """,
            {"character_id": cid_char, "created_at": now},
        )

        cid_chapter = new_id("ch")
        _insert_row(
            conn,
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES
                (:chapter_id, :project_id, 1, '第一章', '{}', 'PLANNED',
                 'VISIBLE', NULL, :created_at, :updated_at)
            """,
            {
                "chapter_id": cid_chapter,
                "project_id": pid,
                "created_at": now,
                "updated_at": now,
            },
        )

        # draft（带超长 content 模拟真实数据）
        _insert_row(
            conn,
            """
            INSERT INTO drafts
                (draft_id, chapter_id, version, content, created_by,
                 prompt_version, model_id, created_at)
            VALUES
                (:draft_id, :chapter_id, 1, :content, 'human', NULL, NULL,
                 :created_at)
            """,
            {
                "draft_id": new_id("dr"),
                "chapter_id": cid_chapter,
                "content": "一段测试草稿内容，用于验证备份回环一致性。",
                "created_at": now,
            },
        )

        # hook
        _insert_row(
            conn,
            """
            INSERT INTO hooks
                (hook_id, project_id, name, introduced_chapter_id, status,
                 importance, expected_payoff_chapter_id, payoff_chapter_id,
                 visibility, who_knows, created_at, updated_at)
            VALUES
                (:hook_id, :project_id, '测试伏笔', :intro_chap, 'OPEN', 0.5,
                 NULL, NULL, 'RESTRICTED', NULL, :created_at, :updated_at)
            """,
            {
                "hook_id": new_id("hook"),
                "project_id": pid,
                "intro_chap": cid_chapter,
                "created_at": now,
                "updated_at": now,
            },
        )

        # author_style_sample
        _insert_row(
            conn,
            """
            INSERT INTO author_style_samples
                (sample_id, project_id, title, content, created_at, updated_at)
            VALUES
                (:sample_id, :project_id, '测试文风', '一段散文样例文本。',
                 :created_at, :updated_at)
            """,
            {
                "sample_id": new_id("asty"),
                "project_id": pid,
                "created_at": now,
                "updated_at": now,
            },
        )

        # memory
        _insert_row(
            conn,
            """
            INSERT INTO memories
                (memory_id, project_id, kind, content, embedding_ref,
                 source_ids_json, created_at)
            VALUES
                (:memory_id, :project_id, 'semantic', '测试记忆内容', NULL,
                 '[]', :created_at)
            """,
            {"memory_id": new_id("mem"), "project_id": pid, "created_at": now},
        )

        # location
        _insert_row(
            conn,
            """
            INSERT INTO locations
                (location_id, project_id, name, statement, data_json,
                 visibility, who_knows, created_at, updated_at)
            VALUES
                (:location_id, :project_id, '测试地点', '一句话', '{}',
                 'PUBLIC', NULL, :created_at, :updated_at)
            """,
            {
                "location_id": new_id("loc"),
                "project_id": pid,
                "created_at": now,
                "updated_at": now,
            },
        )

        # branch
        _insert_row(
            conn,
            """
            INSERT INTO branches
                (branch_id, project_id, name, parent_branch_id,
                 base_state_version, status, created_at)
            VALUES
                (:branch_id, :project_id, 'main', NULL, 0, 'ACTIVE',
                 :created_at)
            """,
            {"branch_id": new_id("br"), "project_id": pid, "created_at": now},
        )

        # quality_report
        _insert_row(
            conn,
            """
            INSERT INTO quality_reports
                (report_id, project_id, chapter_id, commit_id, run_id,
                 overall, scores_json, issues_json, created_at)
            VALUES
                (:report_id, :project_id, :chapter_id, NULL, NULL, 80,
                 '{}', '[]', :created_at)
            """,
            {
                "report_id": new_id("qr"),
                "project_id": pid,
                "chapter_id": cid_chapter,
                "created_at": now,
            },
        )
    finally:
        conn.close()
    return pid


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


def test_export_contains_expected_tables(db_path: str) -> None:
    """导出包含全部 23 张应含表，每张表行数与原表一致。"""
    pid = _seed_minimal_project(db_path)
    pkg = BackupService(db_path).export_project(pid)

    # 顶层契约
    assert pkg["format"] == BACKUP_FORMAT
    assert pkg["version"] == BACKUP_VERSION
    assert pkg["project"]["project_id"] == pid
    assert "metadata" in pkg
    assert "tables" in pkg

    # 包含全部 23 张表（含 V3.9 补入白名单的 volumes）
    exported = set(pkg["tables"].keys())
    expected = set(EXPORTED_TABLES)
    assert exported == expected, (
        f"missing={expected - exported} extra={exported - expected}"
    )

    # 行数：构造的项目有几行就几行
    assert len(pkg["tables"]["characters"]) == 1
    assert len(pkg["tables"]["chapters"]) == 1
    assert len(pkg["tables"]["drafts"]) == 1
    assert len(pkg["tables"]["hooks"]) == 1
    assert len(pkg["tables"]["author_style_samples"]) == 1
    assert len(pkg["tables"]["memories"]) == 1
    assert len(pkg["tables"]["locations"]) == 1
    assert len(pkg["tables"]["branches"]) == 1
    assert len(pkg["tables"]["quality_reports"]) == 1
    # character_states 通过 JOIN 过滤，仍有 1 行
    assert len(pkg["tables"]["character_states"]) == 1
    # 没有数据的表应为空数组
    assert pkg["tables"]["factions"] == []
    assert pkg["tables"]["world_rules"] == []
    assert pkg["tables"]["volumes"] == []
    assert pkg["tables"]["narrative_debts"] == []


def test_export_metadata_has_api_keys_stripped(db_path: str) -> None:
    """包 metadata 显式声明 api_keys_stripped；递归扫描包体无 api_key 字段值。"""
    pid = _seed_minimal_project(db_path)
    pkg = BackupService(db_path).export_project(pid)

    # metadata 自证
    md = pkg["metadata"]
    assert md["api_keys_stripped"] is True
    assert md["ai_call_logs_excluded"] is True
    assert md["evaluations_excluded"] is True
    assert md["model_configs_excluded"] is True
    assert md["workflow_runs_excluded"] is True
    assert md["reference_canons_excluded"] is True

    # 递归扫描：包体内不能出现名为 api_key 的字段
    blob = json.dumps(pkg, ensure_ascii=False)
    # 任意包含 "api_key" 的 key 都不允许
    assert '"api_key"' not in blob, "backup contains api_key field"
    # 也禁止出现 api_key 字段值的痕迹（防止误存）
    assert not re.search(r"api_key\s*:\s*[\"']", blob), (
        "backup contains api_key value"
    )


def test_export_strips_ai_call_logs_and_evaluations(db_path: str) -> None:
    """导出不含运行时表与敏感表。"""
    pid = _seed_minimal_project(db_path)
    pkg = BackupService(db_path).export_project(pid)
    forbidden = {
        "ai_call_logs",
        "workflow_runs",
        "workflow_run_nodes",
        "evaluations",
        "model_configs",
        "agents",
        "prompts",
        "workflows",
        "reference_canons",
        "canon_extracts",
    }
    leaked = forbidden & set(pkg["tables"].keys())
    assert not leaked, f"forbidden tables leaked into backup: {leaked}"


def test_export_404_like_for_missing_project(db_path: str) -> None:
    """项目不存在 → ValueError。"""
    with pytest.raises(ValueError, match="not found"):
        BackupService(db_path).export_project("prj_not_exists")


def test_roundtrip_consistency(db_path: str) -> None:
    """export → import → 比对新项目与原项目内容一致。"""
    pid = _seed_minimal_project(db_path)
    pkg = BackupService(db_path).export_project(pid)

    # 导入为新项目
    new_project = BackupService(db_path).import_project(pkg)
    new_pid = new_project["project_id"]
    assert new_pid != pid
    assert new_project["name"] == "测试项目（导入）"
    assert new_project["status"] == "ACTIVE"

    # 比对新项目内容
    conn = get_connection(db_path)
    try:
        # 章节数
        ch_orig = conn.execute(
            "SELECT COUNT(*) AS c FROM chapters WHERE project_id = ?",
            (pid,),
        ).fetchone()["c"]
        ch_new = conn.execute(
            "SELECT COUNT(*) AS c FROM chapters WHERE project_id = ?",
            (new_pid,),
        ).fetchone()["c"]
        assert ch_orig == ch_new == 1

        # draft 内容（验证内容字符完全一致）
        draft_orig = conn.execute(
            """
            SELECT d.content FROM drafts d
            JOIN chapters c ON c.chapter_id = d.chapter_id
            WHERE c.project_id = ?
            """,
            (pid,),
        ).fetchone()["content"]
        draft_new = conn.execute(
            """
            SELECT d.content FROM drafts d
            JOIN chapters c ON c.chapter_id = d.chapter_id
            WHERE c.project_id = ?
            """,
            (new_pid,),
        ).fetchone()["content"]
        assert draft_orig == draft_new

        # hooks
        hook_orig = conn.execute(
            "SELECT COUNT(*) AS c FROM hooks WHERE project_id = ?",
            (pid,),
        ).fetchone()["c"]
        hook_new = conn.execute(
            "SELECT COUNT(*) AS c FROM hooks WHERE project_id = ?",
            (new_pid,),
        ).fetchone()["c"]
        assert hook_orig == hook_new == 1

        # author_style_samples
        sty_orig = conn.execute(
            "SELECT COUNT(*) AS c FROM author_style_samples WHERE project_id = ?",
            (pid,),
        ).fetchone()["c"]
        sty_new = conn.execute(
            "SELECT COUNT(*) AS c FROM author_style_samples WHERE project_id = ?",
            (new_pid,),
        ).fetchone()["c"]
        assert sty_orig == sty_new == 1

        # character_states 数量
        cs_orig = conn.execute(
            """
            SELECT COUNT(*) AS c FROM character_states cs
            JOIN characters c ON c.character_id = cs.character_id
            WHERE c.project_id = ?
            """,
            (pid,),
        ).fetchone()["c"]
        cs_new = conn.execute(
            """
            SELECT COUNT(*) AS c FROM character_states cs
            JOIN characters c ON c.character_id = cs.character_id
            WHERE c.project_id = ?
            """,
            (new_pid,),
        ).fetchone()["c"]
        assert cs_orig == cs_new == 1
    finally:
        conn.close()


def test_import_remaps_all_ids(db_path: str) -> None:
    """导入后无残留旧 project_id；新项目 id 全部是新 namespace。"""
    pid = _seed_minimal_project(db_path)
    pkg = BackupService(db_path).export_project(pid)

    # 收集原项目所有 entity id（直接从导出包拿）
    old_ids: set[str] = {pid}
    for rows in pkg["tables"].values():
        for row in rows:
            for v in row.values():
                if isinstance(v, str) and v.startswith(
                    ("char_", "loc_", "fac_", "wrule_", "hook_", "debt_",
                     "ch_", "sc_", "dr_", "event_", "tle_", "rel_",
                     "br_", "mem_", "pol_", "asty", "sum_", "dlt_",
                     "cmt_", "qr_")
                ):
                    old_ids.add(v)

    # 导入
    new_project = BackupService(db_path).import_project(pkg)
    new_pid = new_project["project_id"]
    assert new_pid not in old_ids

    # 验证数据库中存在原项目（未覆盖）+ 新项目（导入后）
    conn = get_connection(db_path)
    try:
        # 1) projects 表行数从 1 → 2（原项目 + 新项目）
        n_projects = conn.execute(
            "SELECT COUNT(*) AS c FROM projects"
        ).fetchone()["c"]
        assert n_projects == 2, f"projects should be 2, got {n_projects}"

        # 2) 原项目数据完整保留（表里 seed 过的字段必有对应行）
        tables_seed_in_test = [
            "characters", "locations", "chapters", "branches",
            "hooks", "memories", "author_style_samples",
            "quality_reports",
        ]
        for t in tables_seed_in_test:
            n_orig = conn.execute(
                f"SELECT COUNT(*) AS c FROM {t} WHERE project_id = ?",
                (pid,),
            ).fetchone()["c"]
            assert n_orig >= 1, f"{t} source rows disappeared"

        # 3) 新项目对应行数 > 0（与原项目至少对等）
        for t in tables_seed_in_test:
            n_new = conn.execute(
                f"SELECT COUNT(*) AS c FROM {t} WHERE project_id = ?",
                (new_pid,),
            ).fetchone()["c"]
            assert n_new >= 1, f"{t} new project missing"
    finally:
        conn.close()


def test_import_rejects_bad_format(db_path: str) -> None:
    """format 错误 → ValueError。"""
    bad = {"format": "other", "version": 1, "exported_at": "x",
           "project": {}, "tables": {}}
    with pytest.raises(ValueError, match="unsupported backup format"):
        validate_backup(bad)
    with pytest.raises(ValueError, match="unsupported backup format"):
        BackupService(db_path).import_project(bad)


def test_import_rejects_bad_version(db_path: str) -> None:
    """version 错误 → ValueError。"""
    bad = {
        "format": BACKUP_FORMAT,
        "version": 2,
        "exported_at": "x",
        "project": {"project_id": "p", "name": "n", "created_at": "c"},
        "tables": {},
    }
    with pytest.raises(ValueError, match="unsupported backup version"):
        validate_backup(bad)
    with pytest.raises(ValueError, match="unsupported backup version"):
        BackupService(db_path).import_project(bad)


def test_import_rolls_back_on_failure(db_path: str, monkeypatch) -> None:
    """中途失败 → 整体回滚；projects 表行数等于导入前。"""
    pid = _seed_minimal_project(db_path)
    pkg = BackupService(db_path).export_project(pid)

    # 记录导入前 projects 行数
    conn = get_connection(db_path)
    try:
        before_count = conn.execute(
            "SELECT COUNT(*) AS c FROM projects"
        ).fetchone()["c"]
        before_per_table: dict[str, int] = {}
        for t in EXPORTED_TABLES:
            if t in ("character_states", "scenes", "drafts",
                     "timeline_events"):
                continue  # 间接过滤表
            before_per_table[t] = conn.execute(
                f"SELECT COUNT(*) AS c FROM {t}"
            ).fetchone()["c"]
    finally:
        conn.close()

    # 让 import_project 中途失败 —— monkeypatch _import_table_rows 在 characters
    # 阶段抛异常
    from packages.core.backup import service as backup_service

    original = backup_service.BackupService._import_table_rows

    def broken(self, conn, table, rows, project_id_map):
        if table == "characters":
            raise RuntimeError("simulated mid-import failure")
        return original(self, conn, table, rows, project_id_map)

    monkeypatch.setattr(
        backup_service.BackupService, "_import_table_rows", broken
    )

    with pytest.raises(RuntimeError, match="simulated"):
        BackupService(db_path).import_project(pkg)

    # 验证回滚：projects 行数不变
    conn = get_connection(db_path)
    try:
        after_count = conn.execute(
            "SELECT COUNT(*) AS c FROM projects"
        ).fetchone()["c"]
        assert after_count == before_count, (
            f"projects row count changed: {before_count} -> {after_count}"
        )
        # 新导入项目不残留（在所有含 project_id 的表中按 project_id 找不到新项目）
        for t, n_before in before_per_table.items():
            n_after = conn.execute(
                f"SELECT COUNT(*) AS c FROM {t}"
            ).fetchone()["c"]
            assert n_after == n_before, (
                f"table {t} row count changed: {n_before} -> {n_after}"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# P2-2 补测：自引用外键（state_deltas.supersedes）+ 间接过滤（timeline_events）
# ---------------------------------------------------------------------------


def _seed_branch_with_parent(db_path: str, pid: str) -> tuple[str, str]:
    """在源项目下造一对分支（parent + child），child.parent_branch_id 指向 parent；
    返回 (parent_branch_id, child_branch_id)。验证自引用第二轮改写。
    """
    now = now_iso()
    parent_id = new_id("br")
    child_id = new_id("br")
    conn = get_connection(db_path)
    try:
        _insert_row(
            conn,
            """
            INSERT INTO branches
                (branch_id, project_id, name, parent_branch_id,
                 base_state_version, status, created_at)
            VALUES
                (:branch_id, :project_id, 'parent', NULL, 0, 'ACTIVE',
                 :created_at)
            """,
            {
                "branch_id": parent_id,
                "project_id": pid,
                "created_at": now,
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO branches
                (branch_id, project_id, name, parent_branch_id,
                 base_state_version, status, created_at)
            VALUES
                (:branch_id, :project_id, 'child', :parent_id, 0, 'ACTIVE',
                 :created_at)
            """,
            {
                "branch_id": child_id,
                "project_id": pid,
                "parent_id": parent_id,
                "created_at": now,
            },
        )
    finally:
        conn.close()
    return parent_id, child_id


def _seed_second_independent_project(db_path: str) -> str:
    """构造第二个项目（无关项目），用于 timeline_events 间接过滤的阴性对照。

    返回第二个项目 id；在该项目下插一条 plot_events + 一条 timeline_events，
    验证导出时不会混入源项目包。
    """
    now = now_iso()
    other_pid = new_id("prj")
    conn = get_connection(db_path)
    try:
        _insert_row(
            conn,
            """
            INSERT INTO projects
                (project_id, name, premise, genre, target_words, status,
                 foreshadow_overdue_chapters, created_at, updated_at)
            VALUES
                (:project_id, :name, :premise, :genre, :target_words,
                 'ACTIVE', 30, :created_at, :updated_at)
            """,
            {
                "project_id": other_pid,
                "name": "无关项目",
                "premise": "不应混入导出包",
                "genre": "科幻",
                "target_words": 100_000,
                "created_at": now,
                "updated_at": now,
            },
        )
        # 无关项目的 plot_event + timeline_event（带 distinct 字符串，便于断言"不在包内"）
        other_event_id = "event_other_uniq_marker_xyz"
        _insert_row(
            conn,
            """
            INSERT INTO plot_events
                (event_id, project_id, type, cause_json, effects_json,
                 participants_json, time_json, status, visibility,
                 who_knows)
            VALUES
                (:event_id, :project_id, 'other', '[]', '[]', '[]',
                 '{"timeline_day":1}', 'planned', 'RESTRICTED', NULL)
            """,
            {"event_id": other_event_id, "project_id": other_pid},
        )
        _insert_row(
            conn,
            """
            INSERT INTO timeline_events
                (timeline_event_id, project_id, event_id, day_index, time_ref,
                 description)
            VALUES
                (:tle_id, :project_id, :event_id, 1, NULL, :desc)
            """,
            {
                "tle_id": "tle_other_uniq_marker_xyz",
                "project_id": other_pid,
                "event_id": other_event_id,
                "desc": "无关项目时间线 - 不应被导出",
            },
        )
    finally:
        conn.close()
    return other_pid


def test_import_remaps_branches_self_reference(db_path: str) -> None:
    """branches.parent_branch_id 自引用：导出 → 导入后，新项目两条分支都在，
    child.parent_branch_id 必须指向新映射后的 parent（不是旧 parent，也不是 NULL）。
    """
    pid = _seed_minimal_project(db_path)
    parent_id, child_id = _seed_branch_with_parent(db_path, pid)

    pkg = BackupService(db_path).export_project(pid)
    # 导出包内含 3 条分支（_seed_minimal_project 的 main + parent + child）
    assert len(pkg["tables"]["branches"]) == 3

    new_project = BackupService(db_path).import_project(pkg)
    new_pid = new_project["project_id"]

    conn = get_connection(db_path)
    try:
        new_branches = conn.execute(
            "SELECT branch_id, name, parent_branch_id FROM branches "
            "WHERE project_id = ? ORDER BY name ASC, branch_id ASC",
            (new_pid,),
        ).fetchall()
        names = sorted([r["name"] for r in new_branches])
        assert names == ["child", "main", "parent"], names

        # 找到新 child / parent（按 name 匹配）
        new_child = next(r for r in new_branches if r["name"] == "child")
        new_parent = next(r for r in new_branches if r["name"] == "parent")
        new_main = next(r for r in new_branches if r["name"] == "main")

        # 主键重映射：new id ≠ old id
        assert new_child["branch_id"] != child_id
        assert new_parent["branch_id"] != parent_id

        # 第二轮改写：new_child.parent_branch_id == new_parent.branch_id（且 ≠ 旧 id）
        assert new_child["parent_branch_id"] == new_parent["branch_id"], (
            f"second-pass rewrite failed: "
            f"new_child.parent_branch_id={new_child['parent_branch_id']!r} "
            f"expected new_parent.branch_id={new_parent['branch_id']!r} "
            f"(old parent_id={parent_id!r})"
        )
        assert new_child["parent_branch_id"] != parent_id

        # main 是根分支（源 parent_branch_id=NULL），导入后应仍为 NULL
        assert new_main["parent_branch_id"] is None
    finally:
        conn.close()


def test_import_remaps_state_deltas_self_reference(db_path: str) -> None:
    """state_deltas 自引用外键（supersedes）：导出 → 导入后，新项目两条 deltas 存在，
    且后一条的 supersedes 指向新映射后的 id（不是旧 id，也不是 NULL）。
    """
    now = now_iso()
    pid = _seed_minimal_project(db_path)
    conn = get_connection(db_path)
    try:
        cid_chapter = conn.execute(
            "SELECT chapter_id FROM chapters WHERE project_id = ?",
            (pid,),
        ).fetchone()["chapter_id"]

        # workflow_runs 行（满足 state_deltas.workflow_run_id FK）
        # 先插 workflows 根（workflow_id FK）
        _insert_row(
            conn,
            """
            INSERT INTO workflows
                (workflow_id, name, version, definition_json, created_at,
                 updated_at)
            VALUES
                ('wf_chapter_commit', 'chapter-commit', 'v1', '{}',
                 :created_at, :updated_at)
            """,
            {"created_at": now, "updated_at": now},
        )
        run_id = new_id("wfr")
        _insert_row(
            conn,
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, chapter_id, status, current_node,
                 checkpoint_json, error, retry_count, started_at)
            VALUES
                (:run_id, 'wf_chapter_commit', :chapter_id, 'COMPLETED', NULL,
                 '{}', NULL, 0, :started_at)
            """,
            {
                "run_id": run_id,
                "chapter_id": cid_chapter,
                "started_at": now,
            },
        )

        # 两条 state_deltas：后一条 supersedes 前一条
        d1_id = new_id("dlt")
        d2_id = new_id("dlt")
        _insert_row(
            conn,
            """
            INSERT INTO state_deltas
                (delta_id, chapter_id, workflow_run_id, previous_state_version,
                 delta_version, schema_version, payload_json, status,
                 supersedes, created_by, created_at)
            VALUES
                (:delta_id, :chapter_id, :run_id, 0, 1, 'state-delta-v0',
                 '{}', 'proposed', NULL, 'tester', :created_at)
            """,
            {
                "delta_id": d1_id,
                "chapter_id": cid_chapter,
                "run_id": run_id,
                "created_at": now,
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO state_deltas
                (delta_id, chapter_id, workflow_run_id, previous_state_version,
                 delta_version, schema_version, payload_json, status,
                 supersedes, created_by, created_at)
            VALUES
                (:delta_id, :chapter_id, :run_id, 0, 1, 'state-delta-v0',
                 '{}', 'proposed', :super_id, 'tester', :created_at)
            """,
            {
                "delta_id": d2_id,
                "chapter_id": cid_chapter,
                "run_id": run_id,
                "super_id": d1_id,
                "created_at": now,
            },
        )
    finally:
        conn.close()

    # 导出 → 导入
    pkg = BackupService(db_path).export_project(pid)
    # 导出包内 state_deltas 必须含 2 行
    assert len(pkg["tables"]["state_deltas"]) == 2
    # 旧 d2.supersedes = d1_id（源 id）
    d2_old = next(
        r for r in pkg["tables"]["state_deltas"]
        if r["delta_id"] == d2_id
    )
    assert d2_old["supersedes"] == d1_id

    new_project = BackupService(db_path).import_project(pkg)
    new_pid = new_project["project_id"]

    # 新项目下 state_deltas 应有 2 行
    conn = get_connection(db_path)
    try:
        # 新 delta 主键由 uuid4 随机生成，导出端按 delta_id ASC 排序后可能让
        # d2 出现在 d1 之前，导致导入后 rowid ASC 不再与源 INSERT 顺序一致。
        # 这里用语义识别：d1.supersedes=NULL（源行就是 NULL），d2 有 supersedes。
        new_deltas = conn.execute(
            "SELECT delta_id, supersedes FROM state_deltas "
            "WHERE chapter_id IN (SELECT chapter_id FROM chapters "
            "WHERE project_id = ?)",
            (new_pid,),
        ).fetchall()
        assert len(new_deltas) == 2
        null_rows = [r for r in new_deltas if r["supersedes"] is None]
        non_null_rows = [r for r in new_deltas if r["supersedes"] is not None]
        assert len(null_rows) == 1, (
            f"expected exactly 1 delta with NULL supersedes, got {len(null_rows)}"
        )
        assert len(non_null_rows) == 1, (
            f"expected exactly 1 delta with non-NULL supersedes, got {len(non_null_rows)}"
        )
        # 源库 d1.supersedes=NULL，d2.supersedes=d1_id；语义识别 new_d1/new_d2
        new_d1_id = null_rows[0]["delta_id"]
        new_d2_row = non_null_rows[0]
        new_d2_id = new_d2_row["delta_id"]

        # 关键断言：新 id 不等于旧 id（确认发生了重映射）
        assert new_d1_id != d1_id
        assert new_d2_id != d2_id

        # 第二轮改写：d2.supersedes 必须是新 d1.id，**不是**旧 d1.id，也**不是**NULL
        assert new_d2_row["supersedes"] == new_d1_id, (
            f"second-pass rewrite failed: "
            f"new_d2.supersedes={new_d2_row['supersedes']!r} "
            f"expected new_d1_id={new_d1_id!r} (old d1_id={d1_id!r})"
        )
        assert new_d2_row["supersedes"] != d1_id
    finally:
        conn.close()


def test_export_filters_timeline_events_by_plot_event_project(db_path: str) -> None:
    """timeline_events 间接过滤：源项目 plot_event 关联的 timeline_events 应在包内，
    无关项目的 timeline_events 应被剔除。
    """
    pid = _seed_minimal_project(db_path)

    # 源项目造一个 plot_event + 两条 timeline_events（都挂到源 plot_event）
    conn = get_connection(db_path)
    try:
        src_event_id = "event_src_marker_abc"
        _insert_row(
            conn,
            """
            INSERT INTO plot_events
                (event_id, project_id, type, cause_json, effects_json,
                 participants_json, time_json, status, visibility,
                 who_knows)
            VALUES
                (:event_id, :project_id, 'other', '[]', '[]', '[]',
                 '{"timeline_day":1}', 'planned', 'RESTRICTED', NULL)
            """,
            {"event_id": src_event_id, "project_id": pid},
        )
        # 两条 timeline_events，distinct 描述便于断言
        for idx, desc in enumerate(["源项目 tle 1", "源项目 tle 2"]):
            _insert_row(
                conn,
                """
                INSERT INTO timeline_events
                    (timeline_event_id, project_id, event_id, day_index,
                     time_ref, description)
                VALUES
                    (:tle_id, :project_id, :event_id, :day, NULL, :desc)
                """,
                {
                    "tle_id": f"tle_src_marker_{idx}",
                    "project_id": pid,
                    "event_id": src_event_id,
                    "day": idx + 1,
                    "desc": desc,
                },
            )
    finally:
        conn.close()

    # 造一个无关项目（带自己的 plot_event + timeline_event）
    other_pid = _seed_second_independent_project(db_path)

    # 导出源项目包
    pkg = BackupService(db_path).export_project(pid)
    tle_rows = pkg["tables"]["timeline_events"]

    # 关键断言 1：只含源项目 timeline_events（2 行），不含无关项目
    assert len(tle_rows) == 2, (
        f"expected 2 timeline_events for source project, got {len(tle_rows)}"
    )
    assert all(r["project_id"] == pid for r in tle_rows)
    # 无关项目的 marker 必须不在包内
    for r in tle_rows:
        assert "tle_other_uniq_marker_xyz" not in r["timeline_event_id"]
        assert r["description"] != "无关项目时间线 - 不应被导出"

    # 关键断言 2：导入后新项目 timeline_events 行数=2，且事件 id 已重映射
    new_project = BackupService(db_path).import_project(pkg)
    new_pid = new_project["project_id"]

    conn = get_connection(db_path)
    try:
        new_tle = conn.execute(
            "SELECT timeline_event_id, project_id, event_id FROM timeline_events "
            "WHERE project_id = ? ORDER BY timeline_event_id ASC",
            (new_pid,),
        ).fetchall()
        assert len(new_tle) == 2

        # 新 event_id ≠ 旧 event_id（plot_events 也被重映射）
        for r in new_tle:
            assert r["event_id"] != src_event_id, (
                "timeline_events.event_id should be remapped to new plot_events.event_id"
            )
            # 新 event_id 必须是新的 event_ 前缀
            assert r["event_id"].startswith("event_")
            assert r["event_id"] != "event_src_marker_abc"

        # 无关项目仍存在且不受影响
        other_count = conn.execute(
            "SELECT COUNT(*) AS c FROM timeline_events WHERE project_id = ?",
            (other_pid,),
        ).fetchone()["c"]
        assert other_count == 1, (
            f"unrelated project timeline_events disturbed: {other_count}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# V3.9 全量检修：F1 JSON 内嵌 id 重映射 / F2 volumes 入白名单 /
#                  F3 提交前 FK 校验 / F4 projects 后加列动态写
# ---------------------------------------------------------------------------


def _seed_cross_reference_project(db_path: str) -> dict[str, str]:
    """构造跨表引用 fixture（F1/F2/F4 共用）。

    覆盖链路：项目（含 word_band_json / genre_pack_id）→ 角色 ↔ 关系 ↔ 事件 ↔
    钩子 ↔ 卷 → 章节（挂卷）→ 场景 / 草稿 → 两版本快照（story_states v1/v2）+ 两条
    delta，且实体 id 同时出现在 JSON 列的**值**与**键**位置。

    返回 dict：``pid`` 与各实体旧 id（键名 = 实体角色）。
    """
    now = now_iso()
    ids = {k: new_id(prefix) for k, prefix in [
        ("c1", "char"), ("c2", "char"), ("loc1", "loc"), ("wr1", "wrule"),
        ("debt1", "debt"), ("hook1", "hook"), ("ch1", "ch"), ("ch2", "ch"),
        ("vol1", "vol"), ("branch1", "br"), ("ev1", "event"), ("ev2", "event"),
        ("tle1", "tle"), ("rel1", "rel"), ("d1", "dlt"), ("d2", "dlt"),
        ("cmt1", "cmt"), ("cmt2", "cmt"), ("sc1", "sc"), ("dr1", "dr"),
        ("mem1", "mem"), ("qr1", "qr"),
    ]}
    pid = new_id("prj")
    ids["pid"] = pid
    pack_id = "gp_cross_ref"

    conn = get_connection(db_path)
    try:
        _insert_row(
            conn,
            """
            INSERT INTO genre_packs
                (pack_id, name, genre_tag, version, payload_json, source_path,
                 created_at, updated_at)
            VALUES
                (:pack_id, '跨引用题材包', '测试', 1, '{"schema_version":"x"}',
                 NULL, :created_at, :updated_at)
            """,
            {"pack_id": pack_id, "created_at": now, "updated_at": now},
        )
        _insert_row(
            conn,
            """
            INSERT INTO projects
                (project_id, name, premise, genre, target_words, status,
                 foreshadow_overdue_chapters, word_band_json, genre_pack_id,
                 created_at, updated_at)
            VALUES
                (:project_id, '跨引用项目', 'F1 fixture', '科幻', 90000, 'ACTIVE',
                 30, '{"low_ratio":0.9,"high_ratio":1.1,"floor":1500}',
                 :pack_id, :created_at, :updated_at)
            """,
            {
                "project_id": pid,
                "pack_id": pack_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        for key, name in (("c1", "角色甲"), ("c2", "角色乙")):
            _insert_row(
                conn,
                """
                INSERT INTO characters
                    (character_id, project_id, name, role, core_json, visibility,
                     who_knows, aliases, inject_mode, created_at, updated_at)
                VALUES
                    (:character_id, :project_id, :name, 'supporting',
                     '{"personality":["沉稳"]}', 'PUBLIC', :who_knows, '[]',
                     'auto', :created_at, :updated_at)
                """,
                {
                    "character_id": ids[key],
                    "project_id": pid,
                    "name": name,
                    "who_knows": json.dumps([ids["c1"]], ensure_ascii=False),
                    "created_at": now,
                    "updated_at": now,
                },
            )
        # character_states：state_json 内嵌 location 与来源事件 id
        _insert_row(
            conn,
            """
            INSERT INTO character_states
                (character_id, state_version, state_json, visibility, who_knows,
                 created_at)
            VALUES
                (:character_id, 1, :state_json, 'VISIBLE', :who_knows, :created_at)
            """,
            {
                "character_id": ids["c1"],
                "state_json": json.dumps(
                    {
                        "location": ids["loc1"],
                        "knowledge": [{"source_event_id": ids["ev1"]}],
                    },
                    ensure_ascii=False,
                ),
                "who_knows": json.dumps([ids["c2"]], ensure_ascii=False),
                "created_at": now,
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO locations
                (location_id, project_id, name, statement, data_json, visibility,
                 who_knows, aliases, inject_mode, created_at, updated_at)
            VALUES
                (:location_id, :project_id, '旧城', '陈述', '{}', 'PUBLIC',
                 NULL, '[]', 'auto', :created_at, :updated_at)
            """,
            {"location_id": ids["loc1"], "project_id": pid,
             "created_at": now, "updated_at": now},
        )
        _insert_row(
            conn,
            """
            INSERT INTO world_rules
                (world_rule_id, project_id, name, statement, data_json,
                 visibility, who_knows, created_at, updated_at)
            VALUES
                (:world_rule_id, :project_id, '规则一', '陈述', '{}', 'PUBLIC',
                 NULL, :created_at, :updated_at)
            """,
            {"world_rule_id": ids["wr1"], "project_id": pid,
             "created_at": now, "updated_at": now},
        )
        _insert_row(
            conn,
            """
            INSERT INTO volumes
                (volume_id, project_id, number, title, status,
                 terminal_snapshot_json, arc_summary, created_at, updated_at)
            VALUES
                (:volume_id, :project_id, 1, '第一卷', 'active', NULL,
                 '卷摘要', :created_at, :updated_at)
            """,
            {"volume_id": ids["vol1"], "project_id": pid,
             "created_at": now, "updated_at": now},
        )
        _insert_row(
            conn,
            """
            INSERT INTO branches
                (branch_id, project_id, name, parent_branch_id,
                 base_state_version, status, created_at)
            VALUES
                (:branch_id, :project_id, 'main', NULL, 0, 'ACTIVE',
                 :created_at)
            """,
            {"branch_id": ids["branch1"], "project_id": pid,
             "created_at": now},
        )
        for key, number, title in (("ch1", 1, "第一章"), ("ch2", 2, "第二章")):
            _insert_row(
                conn,
                """
                INSERT INTO chapters
                    (chapter_id, project_id, number, title, plan_json, status,
                     visibility, who_knows, volume_id, created_at, updated_at)
                VALUES
                    (:chapter_id, :project_id, :number, :title, :plan_json,
                     'PLANNED', 'VISIBLE', NULL, :volume_id, :created_at,
                     :updated_at)
                """,
                {
                    "chapter_id": ids[key],
                    "project_id": pid,
                    "number": number,
                    "title": title,
                    "plan_json": json.dumps(
                        {"chapter_goal": "目标", "scenes": [ids["sc1"]]},
                        ensure_ascii=False,
                    ),
                    "volume_id": ids["vol1"],
                    "created_at": now,
                    "updated_at": now,
                },
            )
        _insert_row(
            conn,
            """
            INSERT INTO scenes
                (scene_id, chapter_id, order_index, plan_json, visibility,
                 who_knows)
            VALUES
                (:scene_id, :chapter_id, 1, :plan_json, 'VISIBLE', NULL)
            """,
            {
                "scene_id": ids["sc1"],
                "chapter_id": ids["ch1"],
                "plan_json": json.dumps(
                    {"characters": [ids["c1"], ids["c2"]],
                     "location": ids["loc1"]},
                    ensure_ascii=False,
                ),
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO drafts
                (draft_id, chapter_id, version, content, created_by,
                 prompt_version, model_id, created_at)
            VALUES
                (:draft_id, :chapter_id, 1, '正文内容。', 'human', NULL, NULL,
                 :created_at)
            """,
            {"draft_id": ids["dr1"], "chapter_id": ids["ch1"],
             "created_at": now},
        )
        _insert_row(
            conn,
            """
            INSERT INTO hooks
                (hook_id, project_id, name, introduced_chapter_id, status,
                 importance, expected_payoff_chapter_id, payoff_chapter_id,
                 visibility, who_knows, created_at, updated_at)
            VALUES
                (:hook_id, :project_id, '跨引用伏笔', :intro_chap, 'OPEN', 0.5,
                 :payoff_chap, NULL, 'RESTRICTED', :who_knows, :created_at,
                 :updated_at)
            """,
            {
                "hook_id": ids["hook1"],
                "project_id": pid,
                "intro_chap": ids["ch1"],
                "payoff_chap": ids["ch2"],
                "who_knows": json.dumps([ids["c1"]], ensure_ascii=False),
                "created_at": now,
                "updated_at": now,
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO narrative_debts
                (debt_id, project_id, description, created_chapter_id,
                 severity, deadline_chapter_id, status, visibility, who_knows,
                 created_at, updated_at)
            VALUES
                (:debt_id, :project_id, '债务描述', :created_chapter, 0.5,
                 :deadline_chapter, 'open', 'RESTRICTED', :who_knows,
                 :created_at, :updated_at)
            """,
            {
                "debt_id": ids["debt1"],
                "project_id": pid,
                "created_chapter": ids["ch1"],
                "deadline_chapter": ids["ch2"],
                "who_knows": json.dumps([ids["c1"], ids["c2"]],
                                        ensure_ascii=False),
                "created_at": now,
                "updated_at": now,
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO relationships
                (relationship_id, project_id, from_character_id,
                 to_character_id, relation_type, state_json, last_state_version,
                 visibility, who_knows)
            VALUES
                (:relationship_id, :project_id, :from_id, :to_id, 'ally',
                 '{"intensity":0.5}', 1, 'VISIBLE', :who_knows)
            """,
            {
                "relationship_id": ids["rel1"],
                "project_id": pid,
                "from_id": ids["c1"],
                "to_id": ids["c2"],
                "who_knows": json.dumps([ids["c1"]], ensure_ascii=False),
            },
        )
        # plot_events：cause / effects / participants / who_knows 四列都内嵌 id
        for key, causes, effects, day in (
            ("ev1", [ids["ev2"]], [], 1),
            ("ev2", [], [ids["ev1"]], 2),
        ):
            _insert_row(
                conn,
                """
                INSERT INTO plot_events
                    (event_id, project_id, type, cause_json, effects_json,
                     participants_json, location_id, time_json, status,
                     introduced_chapter_id, visibility, who_knows, description)
                VALUES
                    (:event_id, :project_id, 'conflict', :cause_json,
                     :effects_json, :participants_json, :location_id,
                     '{"timeline_day":1}', 'recorded', :introduced_chapter_id,
                     'RESTRICTED', :who_knows, '事件描述')
                """,
                {
                    "event_id": ids[key],
                    "project_id": pid,
                    "cause_json": json.dumps(causes),
                    "effects_json": json.dumps(effects),
                    "participants_json": json.dumps([ids["c1"], ids["c2"]]),
                    "location_id": ids["loc1"],
                    "introduced_chapter_id": ids["ch1"],
                    "who_knows": json.dumps([ids["c1"]], ensure_ascii=False),
                },
            )
        _insert_row(
            conn,
            """
            INSERT INTO timeline_events
                (timeline_event_id, project_id, event_id, day_index, time_ref,
                 description, visibility, who_knows)
            VALUES
                (:tle_id, :project_id, :event_id, 1, NULL, '时间线描述',
                 'VISIBLE', :who_knows)
            """,
            {
                "tle_id": ids["tle1"],
                "project_id": pid,
                "event_id": ids["ev1"],
                "who_knows": json.dumps([ids["c1"]], ensure_ascii=False),
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO memories
                (memory_id, project_id, kind, content, embedding_ref,
                 source_ids_json, created_at)
            VALUES
                (:memory_id, :project_id, 'semantic', '记忆内容', NULL,
                 :source_ids_json, :created_at)
            """,
            {
                "memory_id": ids["mem1"],
                "project_id": pid,
                "source_ids_json": json.dumps([ids["c1"], ids["ev1"]]),
                "created_at": now,
            },
        )
        # state_deltas（payload_json 内嵌 character/world/event/hook id）
        for key, version in (("d1", 0), ("d2", 1)):
            _insert_row(
                conn,
                """
                INSERT INTO state_deltas
                    (delta_id, chapter_id, workflow_run_id,
                     previous_state_version, delta_version, schema_version,
                     payload_json, status, supersedes, created_by, created_at)
                VALUES
                    (:delta_id, :chapter_id, 'wfr_cross_ref', :prev, 1,
                     'state-delta-v0', :payload_json, 'applied', NULL,
                     'tester', :created_at)
                """,
                {
                    "delta_id": ids[key],
                    "chapter_id": ids["ch1"],
                    "prev": version,
                    "payload_json": json.dumps(
                        {
                            "character_changes": [{"character_id": ids["c1"]}],
                            "world_changes": [
                                {"world_id": ids["loc1"],
                                 "world_kind": "location"}
                            ],
                            "new_events": [{"event_id": ids["ev1"]}],
                            "resolved_hooks": [{"hook_id": ids["hook1"]}],
                        },
                        ensure_ascii=False,
                    ),
                    "created_at": now,
                },
            )
        # commits（story_states.commit_id 的前向引用目标）
        for key, delta_key, version in (("cmt1", "d1", 1), ("cmt2", "d2", 2)):
            _insert_row(
                conn,
                """
                INSERT INTO commits
                    (commit_id, project_id, branch_id, chapter_id,
                     previous_state_version, resulting_state_version, delta_id,
                     validation_json, author_approval_json, timestamp,
                     workflow_run_id, rollback_of)
                VALUES
                    (:commit_id, :project_id, :branch_id, :chapter_id,
                     :prev, :resulting, :delta_id, :validation_json,
                     :author_approval_json, :timestamp, 'wfr_cross_ref', NULL)
                """,
                {
                    "commit_id": ids[key],
                    "project_id": pid,
                    "branch_id": ids["branch1"],
                    "chapter_id": ids["ch1"],
                    "prev": version - 1,
                    "resulting": version,
                    "delta_id": ids[delta_key],
                    "validation_json": json.dumps(
                        {"schema_valid": True,
                         "guardrail_results": [],
                         "validator_agent": "validator:v1"},
                        ensure_ascii=False,
                    ),
                    "author_approval_json": json.dumps(
                        {"required": True, "status": "approved",
                         "high_risk_change_ids": [ids["ev1"]]},
                        ensure_ascii=False,
                    ),
                    "timestamp": now,
                },
            )
        # story_states：两版本快照，id 出现在 list 元素字段 / dict 键 / 嵌套数组
        for key, version in (("version1", 1), ("version2", 2)):
            snapshot = {
                "state_version": version,
                "characters": [
                    {
                        "character_id": ids["c1"],
                        "name": "角色甲",
                        "current_state": {"location": ids["loc1"]},
                        "relationships": [
                            {
                                "relationship_id": ids["rel1"],
                                "to_character_id": ids["c2"],
                            }
                        ],
                    },
                    {
                        "character_id": ids["c2"],
                        "name": "角色乙",
                        "current_state": {},
                        "relationships": [],
                    },
                ],
                # dict 键 = 实体 id（只改 value 会让 check_state_sync 报 DRIFT）
                "world": {
                    "current_time_in_story": None,
                    "locations": {ids["loc1"]: {"name": "旧城"}},
                    "factions": {},
                    "world_rules": [{"world_rule_id": ids["wr1"]}],
                    "active_resources": {ids["loc1"]: {"resource": "粮草"}},
                },
                "hooks": [
                    {"hook_id": ids["hook1"],
                     "introduced_chapter_id": ids["ch1"]}
                ],
                "debts": [
                    {"debt_id": ids["debt1"],
                     "created_chapter_id": ids["ch1"]}
                ],
                "recent_events": [ids["ev1"]],
                "events": {
                    ids["ev1"]: {"participants": [ids["c1"], ids["c2"]]},
                    ids["ev2"]: {"participants": []},
                },
            }
            _insert_row(
                conn,
                """
                INSERT INTO story_states
                    (project_id, state_version, snapshot_json, commit_id,
                     created_at)
                VALUES
                    (:project_id, :state_version, :snapshot_json, :commit_id,
                     :created_at)
                """,
                {
                    "project_id": pid,
                    "state_version": version,
                    "snapshot_json": json.dumps(snapshot, ensure_ascii=False),
                    "commit_id": ids["cmt1"] if version == 1 else ids["cmt2"],
                    "created_at": now,
                },
            )
        _insert_row(
            conn,
            """
            INSERT INTO quality_reports
                (report_id, project_id, chapter_id, commit_id, run_id, overall,
                 scores_json, issues_json, judge_json, draft_version,
                 created_at)
            VALUES
                (:report_id, :project_id, :chapter_id, :commit_id, NULL, 80,
                 '{"overall":80}', :issues_json, NULL, 1, :created_at)
            """,
            {
                "report_id": ids["qr1"],
                "project_id": pid,
                "chapter_id": ids["ch1"],
                "commit_id": ids["cmt1"],
                "issues_json": json.dumps(
                    [{"rule_id": "x", "location": ids["ch1"],
                      "evidence_refs": [ids["ev1"]]}],
                    ensure_ascii=False,
                ),
                "created_at": now,
            },
        )
    finally:
        conn.close()
    return ids


def _collect_json_strings(value) -> set[str]:
    """递归收集已解析 JSON 结构里出现的全部字符串（含 dict 的 key）。"""
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        out: set[str] = set()
        for item in value:
            out |= _collect_json_strings(item)
        return out
    if isinstance(value, dict):
        out = set()
        for k, v in value.items():
            out |= _collect_json_strings(k)
            out |= _collect_json_strings(v)
        return out
    return set()


def _new_project_rows(db_path: str, new_pid: str) -> dict:
    """取导入副本的各表行（按业务键定位，便于与源 fixture 对照）。"""
    conn = get_connection(db_path)
    try:
        def one(sql: str, params: tuple = ()) -> dict:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row is not None else {}

        chars = {
            r["name"]: r["character_id"]
            for r in conn.execute(
                "SELECT character_id, name FROM characters WHERE project_id = ?",
                (new_pid,),
            ).fetchall()
        }
        chapter1 = one(
            "SELECT * FROM chapters WHERE project_id = ? AND number = 1", (new_pid,)
        )
        # ev1 / ev2 按 effects_json 区分：ev1 无 effect，ev2 的 effects 指向 ev1
        events = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM plot_events WHERE project_id = ?", (new_pid,)
            ).fetchall()
        ]
        ev1 = next(e for e in events if e["effects_json"] == "[]")
        ev2 = next(e for e in events if e is not ev1)
        return {
            "chars": chars,
            "project": one("SELECT * FROM projects WHERE project_id = ?", (new_pid,)),
            "volume": one("SELECT * FROM volumes WHERE project_id = ?", (new_pid,)),
            "chapter1": chapter1,
            "chapter2": one(
                "SELECT * FROM chapters WHERE project_id = ? AND number = 2",
                (new_pid,),
            ),
            "hook": one("SELECT * FROM hooks WHERE project_id = ?", (new_pid,)),
            "ev1": ev1,
            "ev2": ev2,
            "delta": one(
                "SELECT * FROM state_deltas WHERE chapter_id = ?",
                (chapter1["chapter_id"],),
            ),
            "snapshot": one(
                "SELECT * FROM story_states WHERE project_id = ? AND state_version = 2",
                (new_pid,),
            ),
            "memory": one("SELECT * FROM memories WHERE project_id = ?", (new_pid,)),
            "scene": one(
                "SELECT * FROM scenes WHERE chapter_id = ?",
                (chapter1["chapter_id"],),
            ),
            "relationship": one(
                "SELECT * FROM relationships WHERE project_id = ?", (new_pid,)
            ),
            "char_state": one(
                "SELECT * FROM character_states WHERE character_id = ?",
                (chars["角色甲"],),
            ),
            "quality": one(
                "SELECT * FROM quality_reports WHERE project_id = ?", (new_pid,)
            ),
            "commit_ids": sorted(
                r["commit_id"]
                for r in conn.execute(
                    "SELECT commit_id FROM commits WHERE project_id = ?", (new_pid,)
                ).fetchall()
            ),
        }
    finally:
        conn.close()


def test_json_id_columns_cover_schema(tmp_path: Path) -> None:
    """json_ids 列清单必须与迁移后的真实 schema 对齐（漂移看守）。

    口径：导出表的每个 ``*_json`` 列与 ``who_knows`` 列都必须在
    ``JSON_ID_COLUMNS`` 里登记，反之清单里不得出现不存在的列。新增 JSON 列
    若忘记登记，本测试即红（防止内嵌 id 重映射悄悄漏列）。
    """
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    conn = get_connection(str(settings.db_path))
    try:
        for table in EXPORTED_TABLES:
            actual_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            expected = {
                c for c in actual_cols if c.endswith("_json") or c == "who_knows"
            }
            listed = set(JSON_ID_COLUMNS.get(table, ()))
            assert listed == expected, (
                f"{table}: JSON_ID_COLUMNS 与 schema 不一致 "
                f"(缺少={sorted(expected - listed)}, 多余={sorted(listed - expected)})"
            )
            # 清单里的列必须真实存在（防改名列 / 删列后清单变孤儿）
            assert listed <= actual_cols, f"{table}: 清单列不存在 {listed - actual_cols}"
    finally:
        conn.close()


def test_roundtrip_remaps_json_embedded_ids(db_path: str) -> None:
    """F1：导出 → 导入后，JSON 列内嵌 id（值 + dict 键）全部指向新 namespace。

    覆盖 story_states.snapshot_json / state_deltas.payload_json / plot_events 四列 /
    hooks.who_knows / character_states.state_json / relationships / scenes.plan_json /
    memories.source_ids_json / quality_reports.issues_json，并断言无旧 id 残留 +
    check_state_sync 对副本项目 SYNC OK。
    """
    from scripts.check_state_sync import main as check_main

    ids = _seed_cross_reference_project(db_path)
    pkg = BackupService(db_path).export_project(ids["pid"])
    assert len(pkg["tables"]["volumes"]) == 1

    new_pid = BackupService(db_path).import_project(pkg)["project_id"]
    assert new_pid != ids["pid"]
    rows = _new_project_rows(db_path, new_pid)
    new_c1 = rows["chars"]["角色甲"]
    new_c2 = rows["chars"]["角色乙"]
    new_ev1 = rows["ev1"]["event_id"]
    new_ev2 = rows["ev2"]["event_id"]

    # --- story_states.snapshot_json：字符串值 / dict 键 / 嵌套数组三层都换新 ---
    snapshot_raw = rows["snapshot"]["snapshot_json"]
    assert isinstance(snapshot_raw, str), "snapshot_json 必须保持 TEXT 形态"
    snapshot = json.loads(snapshot_raw)
    assert snapshot["characters"][0]["character_id"] == new_c1
    assert snapshot["characters"][0]["current_state"]["location"] in (
        snapshot["world"]["locations"]
    )
    assert list(snapshot["world"]["locations"]) != [ids["loc1"]], (
        "world.locations 的 dict 键（= location_id）未重映射"
    )
    assert set(snapshot["events"]) == {new_ev1, new_ev2}, (
        "events 的 dict 键（= event_id）未重映射"
    )
    assert set(snapshot["world"]["active_resources"]) == set(
        snapshot["world"]["locations"]
    )
    assert snapshot["recent_events"] == [new_ev1]
    assert snapshot["world"]["world_rules"][0]["world_rule_id"] != ids["wr1"]
    assert snapshot["hooks"][0]["hook_id"] == rows["hook"]["hook_id"]
    assert snapshot["hooks"][0]["introduced_chapter_id"] == rows["chapter1"]["chapter_id"]
    assert snapshot["debts"][0]["debt_id"] != ids["debt1"]
    assert snapshot["characters"][0]["relationships"][0]["relationship_id"] == (
        rows["relationship"]["relationship_id"]
    )
    assert snapshot["characters"][0]["relationships"][0]["to_character_id"] == new_c2

    # --- story_states.commit_id 前向引用（commits 在 story_states 之后导入）---
    assert rows["snapshot"]["commit_id"] in rows["commit_ids"], (
        "story_states.commit_id 未重映射到本包 commits（前向引用漏映射）"
    )

    # --- state_deltas.payload_json ---
    payload = json.loads(rows["delta"]["payload_json"])
    assert payload["character_changes"][0]["character_id"] == new_c1
    assert payload["world_changes"][0]["world_id"] in snapshot["world"]["locations"]
    assert payload["new_events"][0]["event_id"] == new_ev1
    assert payload["resolved_hooks"][0]["hook_id"] == rows["hook"]["hook_id"]

    # --- plot_events 四列 / hooks.who_knows ---
    assert json.loads(rows["ev1"]["participants_json"]) == [new_c1, new_c2]
    assert json.loads(rows["ev2"]["participants_json"]) == [new_c1, new_c2]
    assert json.loads(rows["ev1"]["cause_json"]) == [new_ev2]
    assert json.loads(rows["ev1"]["effects_json"]) == []
    assert json.loads(rows["ev2"]["cause_json"]) == []
    assert json.loads(rows["ev2"]["effects_json"]) == [new_ev1]
    assert rows["ev1"]["location_id"] in snapshot["world"]["locations"]
    assert rows["ev1"]["introduced_chapter_id"] == rows["chapter1"]["chapter_id"]
    assert json.loads(rows["ev1"]["who_knows"]) == [new_c1]
    assert json.loads(rows["hook"]["who_knows"]) == [new_c1]

    # --- character_states.state_json / scenes.plan_json / memories / issues ---
    state_json = json.loads(rows["char_state"]["state_json"])
    assert state_json["location"] in snapshot["world"]["locations"]
    assert state_json["knowledge"][0]["source_event_id"] == new_ev1
    assert json.loads(rows["char_state"]["who_knows"]) == [new_c2]
    plan = json.loads(rows["scene"]["plan_json"])
    assert plan["characters"] == [new_c1, new_c2]
    assert plan["location"] == state_json["location"]
    assert set(json.loads(rows["memory"]["source_ids_json"])) == {new_c1, new_ev1}
    issues = json.loads(rows["quality"]["issues_json"])
    assert issues[0]["location"] == rows["chapter1"]["chapter_id"]
    assert issues[0]["evidence_refs"] == [new_ev1]

    # --- 无旧 id 残留：扫副本项目各行的 JSON 序列化文本 ---
    old_ids = {k: v for k, v in ids.items() if k != "pid"}
    blob = json.dumps(
        [rows[r] for r in (
            "project", "volume", "chapter1", "chapter2", "hook", "ev1", "ev2",
            "delta", "snapshot", "memory", "scene", "relationship",
            "char_state", "quality",
        )],
        ensure_ascii=False,
    )
    residue = sorted(f"{k}={v}" for k, v in old_ids.items() if v in blob)
    assert residue == [], f"导入副本 JSON 残留旧 id: {residue}"
    assert ids["pid"] not in blob

    # --- check_state_sync：副本项目 SYNC OK（快照集合 == DB 实体集合）---
    assert check_main(["--db", db_path, "--project", new_pid]) == 0, (
        "check_state_sync 对导入副本报漂移"
    )


def test_roundtrip_keeps_volumes_and_chapter_link(db_path: str) -> None:
    """F2：volumes 入白名单后，带卷项目 roundtrip 卷行与 chapters.volume_id 完整。"""
    ids = _seed_cross_reference_project(db_path)
    pkg = BackupService(db_path).export_project(ids["pid"])

    # 导出含卷行，且不含无关项目
    vol_rows = pkg["tables"]["volumes"]
    assert [r["volume_id"] for r in vol_rows] == [ids["vol1"]]
    assert vol_rows[0]["project_id"] == ids["pid"]

    new_pid = BackupService(db_path).import_project(pkg)["project_id"]
    conn = get_connection(db_path)
    try:
        new_vol = conn.execute(
            "SELECT * FROM volumes WHERE project_id = ?", (new_pid,)
        ).fetchone()
        assert new_vol is not None, "导入副本丢了卷行"
        assert new_vol["volume_id"] != ids["vol1"]
        assert new_vol["number"] == 1
        assert new_vol["arc_summary"] == "卷摘要"

        new_chapters = conn.execute(
            "SELECT chapter_id, volume_id FROM chapters WHERE project_id = ? "
            "ORDER BY number ASC",
            (new_pid,),
        ).fetchall()
        assert len(new_chapters) == 2
        for ch in new_chapters:
            assert ch["volume_id"] == new_vol["volume_id"], (
                "chapters.volume_id 未重映射到副本卷（悬空 / 跨项目指向）"
            )
        # 源项目卷与章节未被扰动
        orig_vol = conn.execute(
            "SELECT volume_id FROM volumes WHERE project_id = ?", (ids["pid"],)
        ).fetchone()
        assert orig_vol["volume_id"] == ids["vol1"]
        orig_ch = conn.execute(
            "SELECT volume_id FROM chapters WHERE chapter_id = ?", (ids["ch1"],)
        ).fetchone()
        assert orig_ch["volume_id"] == ids["vol1"]
    finally:
        conn.close()


def test_import_preserves_projects_late_columns(db_path: str) -> None:
    """F4：projects 后加列（word_band_json / genre_pack_id）roundtrip 保留。"""
    ids = _seed_cross_reference_project(db_path)
    pkg = BackupService(db_path).export_project(ids["pid"])
    assert pkg["project"]["word_band_json"] == (
        '{"low_ratio":0.9,"high_ratio":1.1,"floor":1500}'
    )
    assert pkg["project"]["genre_pack_id"] == "gp_cross_ref"

    new_pid = BackupService(db_path).import_project(pkg)["project_id"]
    conn = get_connection(db_path)
    try:
        new_project = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (new_pid,)
        ).fetchone()
        assert new_project["word_band_json"] == (
            '{"low_ratio":0.9,"high_ratio":1.1,"floor":1500}'
        )
        assert new_project["genre_pack_id"] == "gp_cross_ref"
        # 其余既有列语义不变
        assert new_project["target_words"] == 90000
        assert new_project["foreshadow_overdue_chapters"] == 30
        assert new_project["status"] == "ACTIVE"
        assert new_project["name"] == "跨引用项目（导入）"
    finally:
        conn.close()


def test_import_drops_project_keys_outside_schema(db_path: str) -> None:
    """F4 反向：包内 project 多出的键（非目标库列）被丢弃，不炸导入。"""
    pid = _seed_minimal_project(db_path)
    pkg = BackupService(db_path).export_project(pid)
    pkg["project"]["not_a_real_column"] = "注入尝试"
    pkg["project"]["project_id); DROP TABLE projects;--"] = "注入尝试"

    new_pid = BackupService(db_path).import_project(pkg)["project_id"]
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (new_pid,)
        ).fetchone()
        assert row is not None
        assert "not_a_real_column" not in row.keys()
    finally:
        conn.close()


def _make_dangling_fk_package(db_path: str) -> tuple[dict, str]:
    """构造带悬空 FK 的包：scenes.chapter_id 指向不存在的章节。"""
    ids = _seed_cross_reference_project(db_path)
    pkg = BackupService(db_path).export_project(ids["pid"])
    assert len(pkg["tables"]["scenes"]) == 1
    pkg["tables"]["scenes"][0]["chapter_id"] = "ch_missing_chapter_f3"
    return pkg, ids["pid"]


def test_import_rejects_dangling_fk_and_rolls_back(db_path: str) -> None:
    """F3：包内悬空 FK → 提交前 foreign_key_check 拦截，整体回滚 + 明确报错。"""
    pkg, src_pid = _make_dangling_fk_package(db_path)

    conn = get_connection(db_path)
    try:
        before = {
            t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
            for t in ("projects", "scenes", "chapters", "volumes", "hooks",
                      "characters", "story_states", "state_deltas", "commits")
        }
    finally:
        conn.close()

    with pytest.raises(ValueError, match="dangling foreign keys"):
        BackupService(db_path).import_project(pkg)

    # 报错消息须指出具体列（便于定位坏包）
    try:
        BackupService(db_path).import_project(pkg)
    except ValueError as exc:
        msg = str(exc)
        assert "scenes.chapter_id" in msg, msg
        assert "chapters" in msg, msg

    conn = get_connection(db_path)
    try:
        for table, n_before in before.items():
            n_after = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            assert n_after == n_before, (
                f"悬空 FK 导入未回滚：{table} {n_before} -> {n_after}"
            )
        # 源项目仍在，且没有半成品项目
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM projects WHERE project_id = ?", (src_pid,)
        ).fetchone()["c"] == 1
    finally:
        conn.close()


def test_import_ignores_pre_existing_fk_violations(db_path: str) -> None:
    """F3 边界：库内既有（非本次导入引入）的悬挂引用不应阻断干净包导入。"""
    pid = _seed_minimal_project(db_path)
    conn = get_connection(db_path)
    try:
        # 手工制造一条历史遗留违例：scenes.chapter_id 指向不存在的章节
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            INSERT INTO scenes (scene_id, chapter_id, order_index, plan_json,
                                visibility, who_knows)
            VALUES ('sc_legacy_dangling', 'ch_legacy_missing', 1, '{}',
                    'VISIBLE', NULL)
            """
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()

    pkg = BackupService(db_path).export_project(pid)
    new_pid = BackupService(db_path).import_project(pkg)["project_id"]
    assert new_pid != pid

    # 历史违例仍在（导入不负责清理），但不影响导入本身
    conn = get_connection(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM projects WHERE project_id = ?", (new_pid,)
        ).fetchone()["c"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM scenes WHERE scene_id = 'sc_legacy_dangling'"
        ).fetchone()["c"] == 1
    finally:
        conn.close()
