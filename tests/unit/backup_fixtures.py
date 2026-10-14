"""Backup 测试共享 fixture / helper（由 test_backup.py 按节拆出）。

db_path fixture、_insert_row、_seed_minimal_project 供三个分节模块共用。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def make_db_path(tmp_path: Path) -> str:
    """临时 SQLite db（已应用全部迁移）——``db_path`` fixture 的共享实现。

    不在这里直接定义 ``@pytest.fixture db_path`` 的原因：各分节模块的用例形参
    就叫 ``db_path``，若用 ``from ... import db_path`` 把它 import 进来，模块
    命名空间里的 fixture 会与用例形参同名（ruff F811）。pytest 的 fixture 是
    **按模块命名空间**发现的，所以各分节模块各自定义一个同名薄 fixture 转发到
    本函数即可（见各分节模块顶部注释）。
    """
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
