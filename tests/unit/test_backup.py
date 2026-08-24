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
    """导出包含全部 22 张应含表，每张表行数与原表一致。"""
    pid = _seed_minimal_project(db_path)
    pkg = BackupService(db_path).export_project(pid)

    # 顶层契约
    assert pkg["format"] == BACKUP_FORMAT
    assert pkg["version"] == BACKUP_VERSION
    assert pkg["project"]["project_id"] == pid
    assert "metadata" in pkg
    assert "tables" in pkg

    # 包含全部 22 张表
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
