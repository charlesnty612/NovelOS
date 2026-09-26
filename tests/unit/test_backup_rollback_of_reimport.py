"""R-1 回归：备份同库重导入含 ``#dup`` 后缀的 commits.rollback_of 撞唯一索引。

事故（AGENTS.md 坑区 R-1，本批次修复）：
- 0022 步骤 0 给重复 rollback_of 的历史行追加 ``'#dup' || rowid`` 后缀（审计行保留）；
- 该带后缀值不是导入 id 映射（project_id_map）的键 → 备份导入时**原样透传**；
- 同一备份同库导入两次 → 两行同 rollback_of 值（不同 project）→ 修前撞 0022 的
  **全库**唯一索引 idx_commits_rollback_of → IntegrityError，导入整体回滚。

修复（本批次，两道防线）：
1. backup/service.py：rollback_of 纳入自引用处理（_SELF_REF_COLS）——先按原值查
   映射；查不到且含 ``#`` 后缀 → 剥后缀再查，命中 → 新 id；仍查不到 → 置 None
   并在导入结果 ``import_warnings`` 里记一行（路径 + 原值）。
2. 0030 迁移：唯一索引改项目域 ``(project_id, rollback_of)``——「同一 commit 至多
   被回滚一次」的原约束力在项目内保持，同时放行跨项目共存（防御纵深：即便归一化
   被绕过，跨项目同值也不再互撞）。

突变验证：撤掉 service 的 ``#dup`` 剥除归一化 → 本文件用例红（rollback_of 未按
映射重写）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.backup import BackupService
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path)


def _seed_project_with_rollback_commits(db_path: str) -> str:
    """构造含 rollback_of 的 commits 集：
    - cmt_a：正常行（rollback_of=NULL）
    - cmt_b：rollback_of = f"{cmt_a}#dup{rowid}"（0022 去重后缀形态）
    - cmt_c：rollback_of = cmt_a（无后缀的正常回滚）
    """
    now = now_iso()
    pid = new_id("prj")
    br = new_id("br")
    ch = new_id("ch")
    d_a, d_b, d_c = new_id("dlt"), new_id("dlt"), new_id("dlt")
    cmt_a, cmt_b, cmt_c = new_id("cmt"), new_id("cmt"), new_id("cmt")
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, foreshadow_overdue_chapters, created_at, updated_at) VALUES "
            "(:pid, '回滚测试项目', NULL, NULL, 100000, 'ACTIVE', 30, :now, :now)",
            {"pid": pid, "now": now},
        )
        conn.execute(
            "INSERT INTO branches (branch_id, project_id, name, parent_branch_id, "
            "base_state_version, status, created_at) VALUES "
            "(:br, :pid, 'main', NULL, 0, 'ACTIVE', :now)",
            {"br": br, "pid": pid, "now": now},
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, created_at, updated_at) VALUES "
            "(:ch, :pid, 1, 'ch1', '{}', 'COMMITTED', :now, :now)",
            {"ch": ch, "pid": pid, "now": now},
        )
        for delta_id, version in ((d_a, 1), (d_b, 2), (d_c, 3)):
            conn.execute(
                "INSERT INTO state_deltas (delta_id, chapter_id, workflow_run_id, "
                "previous_state_version, delta_version, schema_version, payload_json, "
                "status, supersedes, created_by, created_at) VALUES "
                "(:did, :ch, 'wfr_seed', :prev, 1, 'state-delta-v0', :payload, "
                "'applied', NULL, 'tester', :now)",
                {
                    "did": delta_id, "ch": ch, "prev": version - 1,
                    "payload": json.dumps({"character_changes": []}, ensure_ascii=False),
                    "now": now,
                },
            )

        def _commit(commit_id: str, delta_id: str, version: int, rollback_of: str | None) -> None:
            conn.execute(
                "INSERT INTO commits (commit_id, project_id, branch_id, chapter_id, "
                "previous_state_version, resulting_state_version, delta_id, "
                "validation_json, author_approval_json, timestamp, workflow_run_id, "
                "rollback_of) VALUES "
                "(:cid, :pid, :br, :ch, :prev, :res, :did, :validation, :approval, "
                ":now, 'wfr_seed', :rollback)",
                {
                    "cid": commit_id, "pid": pid, "br": br, "ch": ch,
                    "prev": version - 1, "res": version, "did": delta_id,
                    "validation": json.dumps({"schema_valid": True}),
                    "approval": json.dumps({"required": False, "status": "approved"}),
                    "now": now, "rollback": rollback_of,
                },
            )

        _commit(cmt_a, d_a, 1, None)
        _commit(cmt_b, d_b, 2, f"{cmt_a}#dup42")
        _commit(cmt_c, d_c, 3, cmt_a)
        conn.commit()
    finally:
        conn.close()
    return pid


def _imported_commits(db_path: str, project_id: str) -> dict[str, str | None]:
    """取某导入项目的 commits：{resulting_state_version: (commit_id, rollback_of)}。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT commit_id, rollback_of, resulting_state_version FROM commits "
            "WHERE project_id = ? ORDER BY resulting_state_version ASC",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return {r["commit_id"]: r["rollback_of"] for r in rows}


def test_reimport_with_dup_suffix_twice_succeeds_and_remaps(db_path: str):
    """同一备份同库导入两次：修前撞 IntegrityError（红留证）；修后两次成功。

    map 语义（0022 真实形态 = 同 commit 双回滚：裸值保留行 + #dup 审计行同包）：
    - 裸值保留行（v3, cmt_c）占位 → rollback_of 重映射为**本次导入** cmt_a 新 id；
    - #dup 审计行（v2, cmt_b）归一化后与保留行同指，0030 项目域唯一约束
      （同一 commit 至多被回滚一次）下置 NULL + import_warnings 留痕
      （0022 注释：后缀值永不被查询，业务无意义）；
    - 两批次命名空间互不串线、不残留 #dup 后缀。
    """
    pid = _seed_project_with_rollback_commits(db_path)
    package = BackupService(db_path).export_project(pid)

    svc = BackupService(db_path)
    first = svc.import_project(package)
    first_commits = _imported_commits(db_path, first["project_id"])
    # 3 行都进来了
    assert len(first_commits) == 3, first_commits

    second = BackupService(db_path).import_project(package)
    second_commits = _imported_commits(db_path, second["project_id"])
    assert len(second_commits) == 3, second_commits

    # commits 按 resulting_state_version 排序：v1=cmt_a、v2=#dup 行、v3=裸值保留行
    first_ids = list(first_commits.keys())
    second_ids = list(second_commits.keys())
    first_a, first_dup, first_plain = first_ids
    second_a, second_dup, second_plain = second_ids

    # 裸值保留行占位：rollback_of == 本次导入的 cmt_a 新 id（map 语义）
    assert first_commits[first_plain] == first_a, (
        f"第一次导入：裸值保留行 rollback_of 应重映射为本批次 cmt_a 新 id；"
        f"实得 {first_commits}"
    )
    assert second_commits[second_plain] == second_a, (
        f"第二次导入：裸值保留行 rollback_of 应重映射为**本次** cmt_a 新 id；"
        f"实得 {second_commits}"
    )
    # #dup 审计行：与保留行同指冲突 → 置 NULL（不残留后缀值透传）
    assert first_commits[first_dup] is None, first_commits
    assert second_commits[second_dup] is None, second_commits

    # 两批次各自命名空间：回滚指向互不串线（原值透传时两批会同值）
    assert first_commits[first_plain] != second_commits[second_plain]
    # 全部导入行不残留 #dup 后缀
    for rid in (
        first_commits[first_plain], second_commits[second_plain],
        first_commits[first_dup], second_commits[second_dup],
    ):
        assert "#" not in str(rid), rid

    # #dup 行的让位留痕：import_warnings 含路径 + 原值（两次导入各记一条）
    for imported, dup_id, plain_id in (
        (first, first_dup, first_plain),
        (second, second_dup, second_plain),
    ):
        warnings = imported.get("import_warnings")
        assert isinstance(warnings, list) and warnings, (
            f"#dup 冲突置 NULL 应记 import_warnings；实得 {warnings!r}"
        )
        joined = json.dumps(warnings, ensure_ascii=False)
        assert "commits.rollback_of" in joined, joined
        # 原值 = 源库 cmt_a id + #dup42 后缀（seed 固定后缀）
        assert "#dup42" in joined, joined


def test_unmapped_rollback_of_nulled_with_warning(db_path: str):
    """rollback_of 剥后缀后仍无映射（指向包外 commit）→ 置 None + import_warnings 留痕。"""
    now = now_iso()
    pid = new_id("prj")
    br = new_id("br")
    ch = new_id("ch")
    d1 = new_id("dlt")
    cmt_x = new_id("cmt")
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, "
            "foreshadow_overdue_chapters, created_at, updated_at) VALUES "
            "(:pid, 'ghost 项目', 'ACTIVE', 30, :now, :now)",
            {"pid": pid, "now": now},
        )
        conn.execute(
            "INSERT INTO branches (branch_id, project_id, name, base_state_version, "
            "status, created_at) VALUES (:br, :pid, 'main', 0, 'ACTIVE', :now)",
            {"br": br, "pid": pid, "now": now},
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, created_at, updated_at) VALUES "
            "(:ch, :pid, 1, 'ch1', '{}', 'COMMITTED', :now, :now)",
            {"ch": ch, "pid": pid, "now": now},
        )
        conn.execute(
            "INSERT INTO state_deltas (delta_id, chapter_id, workflow_run_id, "
            "previous_state_version, delta_version, schema_version, payload_json, "
            "status, supersedes, created_by, created_at) VALUES "
            "(:did, :ch, 'wfr_seed', 0, 1, 'state-delta-v0', '[]', 'applied', "
            "NULL, 'tester', :now)",
            {"did": d1, "ch": ch, "now": now},
        )
        conn.execute(
            "INSERT INTO commits (commit_id, project_id, branch_id, chapter_id, "
            "previous_state_version, resulting_state_version, delta_id, "
            "validation_json, author_approval_json, timestamp, workflow_run_id, "
            "rollback_of) VALUES "
            "(:cid, :pid, :br, :ch, 0, 1, :did, '{}', '{}', :now, 'wfr_seed', "
            "'cmt_ghost#dup99')",
            {"cid": cmt_x, "pid": pid, "br": br, "ch": ch, "did": d1, "now": now},
        )
        conn.commit()
    finally:
        conn.close()

    package = BackupService(db_path).export_project(pid)
    result = BackupService(db_path).import_project(package)

    rows = _imported_commits(db_path, result["project_id"])
    assert len(rows) == 1
    (rollback_of,) = rows.values()
    assert rollback_of is None, rows

    warnings = result.get("import_warnings")
    assert isinstance(warnings, list) and warnings, (
        f"包外 rollback_of 应记 import_warnings；实得 {warnings!r}"
    )
    joined = json.dumps(warnings, ensure_ascii=False)
    assert "commits.rollback_of" in joined
    assert "cmt_ghost#dup99" in joined
