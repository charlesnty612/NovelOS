"""``scripts/check_state_sync`` 的漂移计数口径（2026-09-21 检修 m8）。

缺陷形状（亲验，dev 库复现）：从未 commit 过的项目（``story_states`` 无行）此前会把
DB 里的每个实体都报成「仅在 DB」，单项目 29 个实体全列进漂移明细、结论 ``DRIFT: 5 处``。
这类项目没有 state 版本化基线，「DB 有实体而快照没有」是**未开始提交**而不是数据不一致。
修法：无快照项目整项不计入 DRIFT，单列报告。

本文件用临时库构造两种形态并断言计数口径：
1. 无快照 + 有实体 ⇒ 不计漂移；
2. 有快照 + 单向漂移（DB 多一个实体）⇒ 计 1 处；
3. 无快照项目混在多个项目里也不污染总计数。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.check_state_sync import (
    _count_drifts,
    collect_reports,
    inspect_project,
    open_readonly_db,
    render_json,
)


def _make_db(tmp_path: Path) -> Path:
    """建一个只含 check_state_sync 所需最小表结构的测试库。"""
    db_path = tmp_path / "sync_test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE projects (project_id TEXT PRIMARY KEY, created_at TEXT);
        CREATE TABLE characters (character_id TEXT PRIMARY KEY, project_id TEXT);
        CREATE TABLE story_states (
            project_id TEXT, state_version INTEGER, snapshot_json TEXT,
            commit_id TEXT, created_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _add_project(conn: sqlite3.Connection, pid: str) -> None:
    conn.execute(
        "INSERT INTO projects (project_id, created_at) VALUES (?, '2026-09-21T00:00:00Z')",
        (pid,),
    )


def _add_character(conn: sqlite3.Connection, pid: str, cid: str) -> None:
    conn.execute(
        "INSERT INTO characters (character_id, project_id) VALUES (?, ?)", (cid, pid)
    )


def _add_snapshot(conn: sqlite3.Connection, pid: str, version: int, char_ids: list[str]) -> None:
    snap = {"characters": [{"character_id": c} for c in char_ids]}
    conn.execute(
        "INSERT INTO story_states (project_id, state_version, snapshot_json, commit_id, created_at) "
        "VALUES (?, ?, ?, 'cmt_x', '2026-09-21T00:00:00Z')",
        (pid, version, json.dumps(snap)),
    )


def test_no_snapshot_project_counts_no_drift(tmp_path: Path):
    """无快照 + 有实体 ⇒ 不计漂移（m8 的契约本体）。"""
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    _add_project(conn, "prj_nosnap")
    _add_character(conn, "prj_nosnap", "char_a")
    _add_character(conn, "prj_nosnap", "char_b")
    conn.commit()
    conn.close()

    ro = open_readonly_db(db)
    try:
        report = inspect_project(ro, "prj_nosnap")
    finally:
        ro.close()
    assert report.no_snapshot is True
    # 明细仍如实记录「仅在 DB」（可观测性不丢），但不计入 DRIFT
    char_result = next(r for r in report.results if r.name == "characters")
    assert char_result.only_in_db == ["char_a", "char_b"]
    assert _count_drifts([report]) == 0


def test_real_one_sided_drift_still_counts(tmp_path: Path):
    """有快照 + DB 多一个实体 ⇒ 计 1 处（真漂移没被 m8 一起关掉）。"""
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    _add_project(conn, "prj_drift")
    _add_character(conn, "prj_drift", "char_a")
    _add_character(conn, "prj_drift", "char_orphan")  # 快照里没有
    _add_snapshot(conn, "prj_drift", 1, ["char_a"])
    conn.commit()
    conn.close()

    ro = open_readonly_db(db)
    try:
        report = inspect_project(ro, "prj_drift")
    finally:
        ro.close()
    assert report.no_snapshot is False
    assert _count_drifts([report]) == 1
    char_result = next(r for r in report.results if r.name == "characters")
    assert char_result.only_in_db == ["char_orphan"]


def test_no_snapshot_does_not_pollute_total(tmp_path: Path):
    """混合场景：一个无快照项目 + 一个有一处漂移的项目 ⇒ 总数只算后者。"""
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    _add_project(conn, "prj_nosnap")
    _add_character(conn, "prj_nosnap", "char_n1")
    _add_character(conn, "prj_nosnap", "char_n2")
    _add_project(conn, "prj_drift")
    _add_character(conn, "prj_drift", "char_b")
    _add_character(conn, "prj_drift", "char_orphan")
    _add_snapshot(conn, "prj_drift", 1, ["char_b"])
    conn.commit()
    conn.close()

    ro = open_readonly_db(db)
    try:
        reports = collect_reports(ro, None)
    finally:
        ro.close()
    assert _count_drifts(reports) == 1

    payload = json.loads(render_json(reports))
    assert payload["conclusion"] == "DRIFT: 1 处"
    assert payload["exit_code"] == 1
    assert payload["projects_without_snapshot"] == ["prj_nosnap"]
    assert [d["project_id"] for d in payload["drifts"]] == ["prj_drift"]


def test_all_clean_reports_sync_ok(tmp_path: Path):
    """有快照且两侧一致 ⇒ SYNC OK / exit 0。"""
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    _add_project(conn, "prj_ok")
    _add_character(conn, "prj_ok", "char_a")
    _add_snapshot(conn, "prj_ok", 1, ["char_a"])
    conn.commit()
    conn.close()

    ro = open_readonly_db(db)
    try:
        reports = collect_reports(ro, None)
    finally:
        ro.close()
    payload = json.loads(render_json(reports))
    assert payload["conclusion"] == "SYNC OK"
    assert payload["exit_code"] == 0
    assert payload["drifts"] == []
