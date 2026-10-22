"""Backup 单元测试（节 1）：导出 / 导入 / 往返一致性 / 格式版本校验。

由 test_backup.py 按既有分节拆出。
"""

from __future__ import annotations

import json
import re
import tempfile
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
    """导出包含全部 25 张应含表，每张表行数与原表一致。"""
    pid = _seed_minimal_project(db_path)
    pkg = BackupService(db_path).export_project(pid)

    # 顶层契约
    assert pkg["format"] == BACKUP_FORMAT
    assert pkg["version"] == BACKUP_VERSION
    assert pkg["project"]["project_id"] == pid
    assert "metadata" in pkg
    assert "tables" in pkg

    # 包含全部 25 张表（含 V3.9 补入白名单的 volumes 与批次二十六补的两张）
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
# F-1（2026-10-23 审计 C）：白名单补漏 —— 非自指看守 + 往返一致性
# ---------------------------------------------------------------------------


#: 迁移后真实 schema 里带 ``project_id`` 却**不导出**的表 → 理由（逐条必填）。
#:
#: ⚠️ 这张表是**显式豁免清单**，不是「跑一下看看缺什么」的自动补丁：加表必须
#: 写清「为什么它不该随项目包走」。空理由 = 添加即失败（下面有断言）。
_PROJECT_SCOPED_EXEMPTIONS: dict[str, str] = {
    "projects": (
        "项目根行由包顶层 ``project`` 字段承载（service._insert_new_project_row 专门路径），"
        "不走 tables 逐表导入；tables 白名单闭合校验另有一条。"
    ),
    "reference_canons": (
        "拆书参照系语料（v1.4 MVP 起就不导出，见 README「已知边界」）；"
        "体量大、属内容仓派生资产，且导出会与 canon_extracts 产生跨表引用。"
    ),
}


def _project_scoped_tables_from_migrations() -> set[str]:
    """从**迁移后的真实 schema**派生「带 project_id 的业务表」（非自指口径）。

    为什么不能用 ``set(EXPORTED_TABLES)`` 做期望：那是自指断言——白名单怎么漂都绿
    （2026-10-23 审计 C 实证：dev 库 ``chapter_scene_plans`` 65 行时它照样绿）。
    这里 apply_migrations 到临时库后读 ``sqlite_master`` + ``PRAGMA table_info``，
    口径 = 真实最终 schema（已 RENAME 掉的迁移替身表自然不出现）。
    """
    with tempfile.TemporaryDirectory(prefix="novelos_bkp_schema_") as td:
        settings = Settings(data_dir=Path(td), log_level="WARNING")
        apply_migrations(settings.db_path)
        conn = get_connection(str(settings.db_path))
        try:
            names = [
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'chapter_fts%'"
                )
            ]
            flagged: set[str] = set()
            for table in names:
                cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                if "project_id" in cols:
                    flagged.add(table)
            return flagged
        finally:
            conn.close()


def test_project_scoped_migration_tables_are_exported_or_exempt() -> None:
    """**非自指看守**：迁移后带 project_id 的表必须 ⊆ 白名单 ∪ 显式豁免（逐条理由）。"""
    flagged = _project_scoped_tables_from_migrations()
    allow = set(EXPORTED_TABLES) | set(_PROJECT_SCOPED_EXEMPTIONS)
    missing = sorted(flagged - allow)
    assert not missing, (
        "以下带 project_id 的业务表既未导出、也未显式豁免（导出→导入会静默丢数据）："
        f"{missing}；若确属全局/运行时资产，请在 _PROJECT_SCOPED_EXEMPTIONS 写明理由"
    )
    # 豁免清单反过来不得出现「迁移里根本没有 project_id」的幽灵项（防借豁免名单藏表）
    ghost = sorted(set(_PROJECT_SCOPED_EXEMPTIONS) - flagged)
    assert not ghost, f"豁免清单有幽灵表（迁移后无 project_id）：{ghost}"
    # 每条豁免必须有非空理由（防「加个名字就算豁免」）
    empty = [t for t, reason in _PROJECT_SCOPED_EXEMPTIONS.items() if not reason.strip()]
    assert not empty, f"豁免项理由为空：{empty}"

    # 反方向：白名单里的表必须真实存在于迁移后的 schema（防 typo / 幽灵表名）
    with tempfile.TemporaryDirectory(prefix="novelos_bkp_names_") as td:
        settings = Settings(data_dir=Path(td), log_level="WARNING")
        apply_migrations(settings.db_path)
        conn = get_connection(str(settings.db_path))
        try:
            all_tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }
        finally:
            conn.close()
    absent = sorted(set(EXPORTED_TABLES) - all_tables)
    assert not absent, f"EXPORTED_TABLES 含 schema 里不存在的表：{absent}"


def _seed_scene_plan_and_binding(db_path: str, pid: str) -> dict:
    """给项目塞 1 行 chapter_scene_plans + 1 个 skill + 1 行 project_skills。"""
    conn = get_connection(db_path)
    try:
        cid = conn.execute(
            "SELECT chapter_id FROM chapters WHERE project_id = ?", (pid,)
        ).fetchone()["chapter_id"]
        char_id = conn.execute(
            "SELECT character_id FROM characters WHERE project_id = ?", (pid,)
        ).fetchone()["character_id"]
        now = now_iso()
        scene_plan_id = new_id("csp")
        payload = json.dumps(
            {
                "schema_version": "scene-plan.v1",
                "prompt_version": "director_planner:v4",
                "chapter_id": cid,
                "scenes": [
                    {
                        "scene_id": "scene_001",
                        "purpose": "开场",
                        "characters": [char_id],
                        "conflict": "冲突",
                    }
                ],
                "notes_for_writer": "",
                "deviations": [],
            },
            ensure_ascii=False,
        )
        conn.execute(
            "INSERT INTO chapter_scene_plans "
            "(scene_plan_id, chapter_id, project_id, run_id, source, prompt_version, "
            " scene_count, payload_json, created_at, updated_at) "
            "VALUES (?, ?, ?, 'wfr_fixture', 'director_planner', 'director_planner:v4', "
            "        1, ?, ?, ?)",
            (scene_plan_id, cid, pid, payload, now, now),
        )
        skill_id = new_id("skl")
        conn.execute(
            "INSERT INTO skills (skill_id, name, description, version, payload_json, "
            "source_path, created_at, updated_at) "
            "VALUES (?, '测试手艺', NULL, 1, '{}', NULL, ?, ?)",
            (skill_id, now, now),
        )
        conn.execute(
            "INSERT INTO project_skills (project_id, skill_id, ord) VALUES (?, ?, 0)",
            (pid, skill_id),
        )
        conn.commit()
        return {"chapter_id": cid, "char_id": char_id,
                "scene_plan_id": scene_plan_id, "skill_id": skill_id}
    finally:
        conn.close()


def test_roundtrip_preserves_scene_plans_and_skill_bindings(db_path: str) -> None:
    """F-1：export→import 后两张新表的行数 / 内容 / 重映射全部一致。"""
    pid = _seed_minimal_project(db_path)
    ids = _seed_scene_plan_and_binding(db_path, pid)

    pkg = BackupService(db_path).export_project(pid)
    assert len(pkg["tables"]["chapter_scene_plans"]) == 1, "白名单漏 chapter_scene_plans"
    assert len(pkg["tables"]["project_skills"]) == 1, "白名单漏 project_skills"
    assert "skills" not in pkg["tables"], "skills 是跨作品资产，不应随项目包导出"

    new_project = BackupService(db_path).import_project(pkg)
    new_pid = new_project["project_id"]
    assert new_pid != pid
    assert "import_warnings" not in new_project, new_project.get("import_warnings")

    conn = get_connection(db_path)
    try:
        # scene_plan：1 行、主键/章节 id 已重映射、溯源列原样、payload 内嵌 id 已重映射
        rows = conn.execute(
            "SELECT * FROM chapter_scene_plans WHERE project_id = ?", (new_pid,)
        ).fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row["scene_plan_id"] != ids["scene_plan_id"]
        assert row["chapter_id"] != ids["chapter_id"]
        assert row["source"] == "director_planner"
        assert row["prompt_version"] == "director_planner:v4"
        assert row["scene_count"] == 1
        payload = json.loads(row["payload_json"])
        assert payload["chapter_id"] == row["chapter_id"], "payload 内嵌 chapter_id 未重映射"
        assert payload["scenes"][0]["characters"][0] != ids["char_id"], (
            "payload 内嵌 character_id 未重映射"
        )
        new_char = conn.execute(
            "SELECT character_id FROM characters WHERE project_id = ?", (new_pid,)
        ).fetchone()["character_id"]
        assert payload["scenes"][0]["characters"] == [new_char]

        # skill 绑定：1 行、project_id 换新、skill_id 保留（跨作品资产）
        bindings = conn.execute(
            "SELECT * FROM project_skills WHERE project_id = ?", (new_pid,)
        ).fetchall()
        assert len(bindings) == 1
        assert bindings[0]["skill_id"] == ids["skill_id"]
        assert bindings[0]["ord"] == 0
    finally:
        conn.close()


def test_import_skips_binding_with_locatable_warning_when_skill_missing(db_path: str) -> None:
    """F-1 导入侧：目标库缺该 skill ⇒ 绑定跳过 + warning 带 project_id / skill_id。

    ``skills`` 不随项目包导出 ⇒ 跨库导入时目标可能没有该 skill。此处导出后**删掉**
    源 skill（CASCADE 会同时删源绑定，但包已在内存中），再导入 ⇒ 必须跳过而不是
    插入悬挂 FK（否则提交前 foreign_key_check 会整包拒绝），且 warning 必须可定位。
    """
    pid = _seed_minimal_project(db_path)
    ids = _seed_scene_plan_and_binding(db_path, pid)
    pkg = BackupService(db_path).export_project(pid)

    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM skills WHERE skill_id = ?", (ids["skill_id"],))
        conn.commit()
    finally:
        conn.close()

    new_project = BackupService(db_path).import_project(pkg)
    new_pid = new_project["project_id"]
    warnings = new_project.get("import_warnings") or []
    assert warnings, "缺 skill 时必须留 import_warnings（否则作者看不出绑定没进来）"
    text = "\n".join(warnings)
    assert pid in text, f"warning 缺 project_id：{warnings}"
    assert ids["skill_id"] in text, f"warning 缺 skill_id：{warnings}"

    conn = get_connection(db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM project_skills WHERE project_id = ?", (new_pid,)
        ).fetchone()["c"]
        assert n == 0, "缺 skill 的绑定必须跳过（不得插入悬挂 FK）"
    finally:
        conn.close()


