"""``_init_status_for_project`` 的 outline 判定必须同时认 ``plan_json`` 与 ``outline_json``
（B-3，2026-11-08）。

缺陷形状：判据只读 ``chapters.plan_json.chapter_goal``，而 ``outline-batch``
刻意**只写 outline_json、不写 plan_json**（见 ``outline_batch/pipeline.py`` 的建章段）
⇒ 它产出的章被判成「空壳卷纲」⇒ 前端把 project-init 的「生成卷纲」默认勾成未完成
⇒ 用户点一次重跑就进入按卷重建，而重建预检恰好放行「status='PLANNED' 且无草稿」
的章 ⇒ 已生成的大纲被静默销毁。

判别面（3 例）：
1. 只有 ``outline_json.chapter_goal`` ⇒ outline 完成（原实现判未完成）；
2. 只有 ``plan_json.chapter_goal`` ⇒ 仍判完成（防修一个坏一个）；
3. 两列都没有 ⇒ 仍判未完成（防判据被写成恒真）。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.api.routers.workflows.runs import _init_status_for_project
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from tests._db_fixtures import migrated_db


def _seed(tmp_path: Path, *, plan_json: dict, outline_json: dict) -> tuple[str, str]:
    db_path = tmp_path / "novelos.db"
    migrated_db(tmp_path)
    pid = new_id("prj")
    vid = new_id("vol")
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, 'b3', 'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.execute(
            "INSERT INTO volumes (volume_id, project_id, number, title, status, "
            "created_at, updated_at) VALUES (?, ?, 1, '第一卷', 'active', ?, ?)",
            (vid, pid, now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "outline_json, status, visibility, created_at, updated_at, volume_id) "
            "VALUES (?, ?, 1, '第一章', ?, ?, 'PLANNED', 'VISIBLE', ?, ?, ?)",
            (cid, pid, json.dumps(plan_json, ensure_ascii=False),
             json.dumps(outline_json, ensure_ascii=False), now, now, vid),
        )
        conn.commit()
    finally:
        conn.close()
    return str(db_path), pid


def _outline_stage(status: dict) -> dict:
    return next(s for s in status["stages"] if s["stage"] == "outline")


def test_outline_json_alone_marks_outline_done(tmp_path):
    """outline-batch 的产物（只有 outline_json）必须被认作「卷纲已完成」。"""
    db_path, pid = _seed(
        tmp_path,
        plan_json={},
        outline_json={"chapter_goal": "主角被赶出家门"},
    )
    assert _outline_stage(_init_status_for_project(db_path, pid))["done"] is True


def test_plan_json_alone_still_marks_outline_done(tmp_path):
    """反向对照：chapter-plan 的产物（只有 plan_json）照旧算完成。"""
    db_path, pid = _seed(
        tmp_path,
        plan_json={"chapter_goal": "主角得到第一笔钱"},
        outline_json={},
    )
    assert _outline_stage(_init_status_for_project(db_path, pid))["done"] is True


def test_empty_both_columns_is_not_done(tmp_path):
    """反向对照：两列都没有 chapter_goal ⇒ 仍是「空壳卷纲」，不得判完成。"""
    db_path, pid = _seed(tmp_path, plan_json={}, outline_json={})
    assert _outline_stage(_init_status_for_project(db_path, pid))["done"] is False
