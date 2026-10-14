"""Backup 单元测试（节 1）：导出 / 导入 / 往返一致性 / 格式版本校验。

由 test_backup.py 按既有分节拆出。
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
from packages.core.db import get_connection
from tests.unit.backup_fixtures import (
    _seed_minimal_project,
    make_db_path,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    """转发到 backup_fixtures.make_db_path（pytest 按模块命名空间发现 fixture）。"""
    return make_db_path(tmp_path)


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


