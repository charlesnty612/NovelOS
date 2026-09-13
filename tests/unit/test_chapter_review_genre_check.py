"""``basic_checks`` 的题材核销挂点（题材库 P1b）。

覆盖：
1. 未绑定题材包 → ``review_report`` **无** ``genre_check`` 键（整段跳过，零行为变化）；
2. 绑定 → ``genre_check`` 段含 issues（与 quality Issue 同形），warnings 追加
   ``[GENRE-…]`` 文本行；
3. 绑定但无 issue → 段存在、issues 为空、warnings 不受影响；
4. GENRE- issues **不产生 errors**（informational，不进 BLOCKING_RULES）。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_review.pipeline import _basic_checks_node

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_PACK_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "ratio_declarations": {"action": 0.7, "transition": 0.3},
    "pacing": {"chapter_word_band": {"low": 2400, "high": 3600}},
}


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, 'P', NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, pid: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, 'C', '{}', 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_draft(db_path: Path, cid: str, content: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, 1, ?, 'test:writer:v1', ?)",
            (new_id("drf"), cid, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_write_run(db_path: Path, cid: str, scene_plan: dict) -> None:
    run_id, workflow_id, now = new_id("wfr"), new_id("wf"), now_iso()
    checkpoint = json.dumps({"scene_plan": scene_plan}, ensure_ascii=False)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES (?, 'chapter-write', 'v1', '{}', ?, ?)",
            (workflow_id, now, now),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, "
            "current_node, checkpoint_json, started_at, ended_at) "
            "VALUES (?, ?, ?, 'COMPLETED', 'save_draft', ?, ?, ?)",
            (run_id, workflow_id, cid, checkpoint, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _bind_pack(db_path: Path, pid: str) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO genre_packs (pack_id, name, genre_tag, version, payload_json, "
            "source_path, created_at, updated_at) VALUES ('gp_kc', '男主快穿', '快穿', 1, "
            "?, NULL, ?, ?)",
            (json.dumps(_PACK_PAYLOAD, ensure_ascii=False), now, now),
        )
        conn.execute(
            "UPDATE projects SET genre_pack_id = 'gp_kc' WHERE project_id = ?", (pid,)
        )
        conn.commit()
    finally:
        conn.close()


def test_unbound_project_has_no_genre_check_segment(tmp_path: Path):
    """未绑定 → review_report 无 genre_check 键（零行为变化）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "中" * 2000)

    rep = _basic_checks_node(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    )["review_report"]
    assert "genre_check" not in rep
    assert rep["warnings"] == []
    assert rep["errors"] == []


def test_bound_with_deviation_emits_genre_check_and_warning(tmp_path: Path):
    """绑定 + 配比偏差 → genre_check 段 + warnings 文本行；errors 不新增 GENRE- 条目。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack(db_path, pid)
    _insert_write_run(
        db_path, cid,
        {
            "scenes": [
                {"scene_id": "s1", "purpose": "p", "scene_type": "action", "target_words": 1000},
                {"scene_id": "s2", "purpose": "p", "scene_type": "transition", "target_words": 1000},
            ]
        },
    )
    _insert_draft(db_path, cid, "中" * 3000)

    rep = _basic_checks_node(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": 3000}
    )["review_report"]

    gc = rep["genre_check"]
    assert gc["bound"] is True
    assert gc["pack_id"] == "gp_kc"
    assert gc["issue_count"] == 1
    assert gc["rule_ids"] == ["GENRE-RATIO-DEVIATION"]
    issue = gc["issues"][0]
    assert issue["severity"] == "warning"
    assert issue["category"] == "payoff"
    assert "GENRE-RATIO-DEVIATION" in rep["warnings"][0]
    # informational：errors 通道不因题材核销新增条目
    assert [e.get("rule_id") for e in rep["errors"] if str(e.get("rule_id")).startswith("GENRE-")] == []


def test_bound_without_issues_still_reports_segment(tmp_path: Path):
    """绑定但无 issue → genre_check 段存在、issues 空、warnings 不受影响。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack(db_path, pid)
    _insert_write_run(
        db_path, cid,
        {
            "scenes": [
                {"scene_id": "s1", "purpose": "p", "scene_type": "action", "target_words": 2100},
                {"scene_id": "s2", "purpose": "p", "scene_type": "transition", "target_words": 900},
            ]
        },
    )
    _insert_draft(db_path, cid, "中" * 3000)

    rep = _basic_checks_node(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": 3000}
    )["review_report"]
    assert rep["genre_check"]["checked"] is True
    assert rep["genre_check"]["issues"] == []
    assert rep["warnings"] == []
    assert rep["errors"] == []


def test_genre_check_word_band_issue_is_warning_only(tmp_path: Path):
    """字数带越界（实际 2000 < 2400）→ GENRE-WORD-BAND-DEVIATION 进 warnings，不进 errors。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack(db_path, pid)
    _insert_draft(db_path, cid, "中" * 2000)

    rep = _basic_checks_node(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    )["review_report"]
    assert rep["genre_check"]["rule_ids"] == ["GENRE-WORD-BAND-DEVIATION"]
    assert any("GENRE-WORD-BAND-DEVIATION" in w for w in rep["warnings"])
    assert rep["errors"] == []
