"""chapter_review.pipeline._critic_review_node 输入 payload 测试。

覆盖：
- critic payload 包含 deterministic_hints 字段。
- deterministic_hints 正确汇总 review_report.ai_pattern_hits。
- 无 AI 腔命中时 summary 为空列表、count 为 0。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_review.pipeline import _critic_review_node

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "项目") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status, "
            "created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str, number: int = 1) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, "
            "visibility, who_knows, created_at, updated_at) VALUES "
            "(?, ?, ?, '', '{}', 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, project_id, number, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_draft(db_path: Path, chapter_id: str, content: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) VALUES "
            "(?, ?, ?, ?, 'test:writer:v1', NULL, NULL, ?)",
            (new_id("drf"), chapter_id, 1, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _critic_output_ok() -> dict:
    return {
        "schema_version": "critic-report.v1",
        "prompt_version": "critic:v1",
        "chapter_id": "ch_xxx",
        "overall_comment": "整体尚可。",
        "strengths": [],
        "issues": [],
    }


def test_critic_payload_includes_deterministic_hints(tmp_path: Path):
    """有 AI 腔命中时，critic payload 含 deterministic_hints 且 summary 非空。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "仿佛命运之手。本章目标完成。")

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "target_word_count": 2000,
        "critic_mode": "always",
        "run_id": new_id("run"),
        "_current_node_run_id": new_id("nr"),
    }

    with patch("packages.workflows.chapter_review.pipeline.run_agent") as mock_run:
        mock_run.return_value = _critic_output_ok()
        _critic_review_node(ctx)

        assert mock_run.called
        _agent_name, payload = mock_run.call_args.args[1], mock_run.call_args.args[2]
        assert "deterministic_hints" in payload
        hints = payload["deterministic_hints"]
        assert hints["ai_pattern_hit_count"] >= 1
        assert any(s["rule_id"] == "AI-FORBIDDEN-WORD" for s in hints["ai_pattern_summary"])


def test_critic_payload_deterministic_hints_empty_when_clean(tmp_path: Path):
    """无 AI 腔命中时，deterministic_hints 的 count 为 0、summary 为空列表。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "风吹过山岗，他站起身，望向远方。")

    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "target_word_count": 2000,
        "critic_mode": "always",
        "run_id": new_id("run"),
        "_current_node_run_id": new_id("nr"),
    }

    with patch("packages.workflows.chapter_review.pipeline.run_agent") as mock_run:
        mock_run.return_value = _critic_output_ok()
        _critic_review_node(ctx)

        payload = mock_run.call_args.args[2]
        hints = payload["deterministic_hints"]
        assert hints["ai_pattern_hit_count"] == 0
        assert hints["ai_pattern_summary"] == []
