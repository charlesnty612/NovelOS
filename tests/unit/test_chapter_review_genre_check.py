"""``basic_checks`` 的题材核销挂点（题材库 P1b）。

覆盖：
1. 未绑定题材包 → ``review_report`` **无** ``genre_check`` 键（整段跳过，零行为变化）；
2. 绑定 → ``genre_check`` 段含 issues（与 quality Issue 同形），warnings 追加
   ``[GENRE-…]`` 文本行；
3. 绑定但无 issue → 段存在、issues 为空、warnings 不受影响；
4. GENRE- issues **不产生 errors**（informational，不进 BLOCKING_RULES）；
5. **核销对象 = 被审对象**（2026-09-18 修复）：作者手改出新草稿版本后复审，
   ``genre_check`` 的字数带必须量被审那一版（与 ``report.word_count`` 同数），
   不能用写稿 run 的旧 ``length_report``；指定 ``ctx['draft_version']`` 复审旧版时
   两处同样对齐到该版本。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.workflows.chapter_review.pipeline import _basic_checks_node
from tests.unit.neutral_prose import neutral_prose

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_PACK_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "ratio_declarations": {"action": 0.7, "transition": 0.3},
    # 2026-09-26 收尾批次：verifier 字数带解析收敛到 normalize_word_band 单点
    # （三键齐全且 floor<=low<=high 才算合法带）——夹具补 floor 保持「合法带」身份。
    "pacing": {"chapter_word_band": {"low": 2400, "high": 3600, "floor": 1800}},
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


def _insert_draft(db_path: Path, cid: str, content: str, version: int = 1) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, ?, ?, 'test:writer:v1', ?)",
            (new_id("drf"), cid, version, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_write_run(
    db_path: Path,
    cid: str,
    scene_plan: dict,
    *,
    length_report: dict | None = None,
    draft_version: int | None = None,
) -> None:
    run_id, workflow_id, now = new_id("wfr"), new_id("wf"), now_iso()
    checkpoint: dict = {"scene_plan": scene_plan}
    if length_report is not None:
        checkpoint["length_report"] = length_report
    if draft_version is not None:
        checkpoint["draft_version"] = draft_version
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
            (run_id, workflow_id, cid, json.dumps(checkpoint, ensure_ascii=False), now, now),
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
    _insert_draft(db_path, cid, neutral_prose(2000))

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
    _insert_draft(db_path, cid, neutral_prose(3000))

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
    _insert_draft(db_path, cid, neutral_prose(3000))

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
    _insert_draft(db_path, cid, neutral_prose(2000))

    rep = _basic_checks_node(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": 2000}
    )["review_report"]
    assert rep["genre_check"]["rule_ids"] == ["GENRE-WORD-BAND-DEVIATION"]
    assert any("GENRE-WORD-BAND-DEVIATION" in w for w in rep["warnings"])
    assert rep["errors"] == []


# ---------------------------------------------------------------------------
# 5) 核销对象 = 被审对象（2026-09-18 修复）
# ---------------------------------------------------------------------------


def _incident_chapter(db_path: Path) -> str:
    """事故形状：write run 写 v11（length_report 713），作者手改出 v12 / v13。"""
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack(db_path, pid)
    _insert_write_run(
        db_path, cid,
        {"scenes": [
            {"scene_id": "s1", "purpose": "p", "scene_type": "action", "target_words": 2100},
            {"scene_id": "s2", "purpose": "p", "scene_type": "transition", "target_words": 900},
        ]},
        length_report={"visible_chars": 713},
        draft_version=11,
    )
    _insert_draft(db_path, cid, neutral_prose(713), version=11)
    _insert_draft(db_path, cid, neutral_prose(1674), version=12)
    _insert_draft(db_path, cid, neutral_prose(2248), version=13)
    return cid


def test_genre_word_band_measures_reviewed_draft_not_stale_length_report(tmp_path: Path):
    """手改草稿后复审：genre_check 量的是被审那版，不是写稿 run 的旧 length_report。

    实证（ch_92bac068ff0d / review run wfr_adf71afbb7d9）：同一份报告里
    ``word_count=2248, draft_version=13``，而 ``genre_check`` 报「实际字数 713
    （length_report）」——713 是 v11 的长度，genre 判据量错了正文。
    """
    db_path = _fresh_db(tmp_path)
    cid = _incident_chapter(db_path)

    rep = _basic_checks_node(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": 2248}
    )["review_report"]

    assert rep["draft_version"] == 13
    assert rep["word_count"] == 2248
    wb = rep["genre_check"]["redline_check"]["word_band"]
    assert wb["reviewed_draft_version"] == 13
    assert wb["word_count"] == rep["word_count"]  # 两处口径必须同一版同一数
    assert wb["source"] == "draft"
    # 题材包带 2400~3600：2248 仍越界（warning），但数字是被审版本的 2248
    assert rep["genre_check"]["rule_ids"] == ["GENRE-WORD-BAND-DEVIATION"]
    assert any("实际字数 2248" in w for w in rep["warnings"])
    assert not any("713" in w for w in rep["warnings"])


def test_genre_word_band_follows_explicit_reviewed_version(tmp_path: Path):
    """指定 ctx['draft_version'] 复审旧版 → genre 字数带同样对齐到该版本。"""
    db_path = _fresh_db(tmp_path)
    cid = _incident_chapter(db_path)

    rep = _basic_checks_node(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": 2248,
         "draft_version": 11}
    )["review_report"]

    assert rep["draft_version"] == 11
    assert rep["word_count"] == 713
    wb = rep["genre_check"]["redline_check"]["word_band"]
    assert wb["reviewed_draft_version"] == 11
    assert wb["word_count"] == rep["word_count"]
    assert wb["source"] == "length_report"  # 归属版本与待核版本一致 → 权威值
    assert any("实际字数 713" in w for w in rep["warnings"])
