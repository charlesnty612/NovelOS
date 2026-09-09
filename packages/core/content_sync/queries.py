"""内容同步器的 DB 读取层（只读）。

按 :data:`packages.core.backup.schema.EXPORTED_TABLES` 的口径精确选定
本任务需要的几张表（characters / world_rules / plot_events / timeline_events /
volumes / chapters / drafts / story_states），全部 ``SELECT *`` 风格拉行后
以 dict 列表返回；调用方负责序列化。

设计要点：

- 不修改 schema / 不写 migration。
- 全部走 :func:`packages.core.db.get_connection`，自带 row_factory 与外键开关。
- ``snapshots`` 字段（如 ``characters.core_json`` / ``world_rules.data_json`` /
  ``plot_events.cause_json`` / ``plot_events.effects_json`` /
  ``plot_events.participants_json`` / ``plot_events.time_json`` /
  ``chapters.plan_json`` / ``volumes.terminal_snapshot_json`` /
  ``scenes.plan_json`` / ``story_states.snapshot_json``）按 JSON 字符串原样
  返回；JSON 解析失败由调用方决定容错（service 层会 ``json.loads``，失败
  fallback 原串）。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from packages.core.db import get_connection

__all__ = [
    "fetch_project",
    "fetch_chapters",
    "fetch_volumes",
    "fetch_drafts",
    "fetch_characters",
    "fetch_world_rules",
    "fetch_plot_events",
    "fetch_timeline_events",
    "fetch_story_states",
    "fetch_volumes_for_book_txt",
]


def _rows_to_dicts(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [{k: r[k] for k in r.keys()} for r in rows]


# ---------------------------------------------------------------------------
# 项目根
# ---------------------------------------------------------------------------


def fetch_project(db_path: str, project_id: str) -> dict[str, Any] | None:
    """取 projects 行（None = 不存在）。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# 内容 / 大纲类
# ---------------------------------------------------------------------------


def fetch_chapters(db_path: str, project_id: str) -> list[dict[str, Any]]:
    """chapters（按 number ASC）；供 build_txt 复用与单章 txt 输出。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM chapters
            WHERE project_id = ?
            ORDER BY number ASC
            """,
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return _rows_to_dicts(conn, rows)


def fetch_volumes(db_path: str, project_id: str) -> list[dict[str, Any]]:
    """volumes（按 number ASC）；含 ``terminal_snapshot_json``。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM volumes
            WHERE project_id = ?
            ORDER BY number ASC
            """,
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return _rows_to_dicts(conn, rows)


def fetch_drafts(db_path: str, project_id: str) -> list[dict[str, Any]]:
    """drafts：通过 chapter_id 间接过滤 project；按 chapter_id ASC, version DESC。

    用途：manifest 计数 + 内容审计；不动 build_txt 的取数路径。
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT d.* FROM drafts d
            JOIN chapters c ON c.chapter_id = d.chapter_id
            WHERE c.project_id = ?
            ORDER BY d.chapter_id ASC, d.version ASC
            """,
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return _rows_to_dicts(conn, rows)


# ---------------------------------------------------------------------------
# Canon 类（直接按 project_id 拉）
# ---------------------------------------------------------------------------


def fetch_characters(db_path: str, project_id: str) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM characters WHERE project_id = ? ORDER BY name ASC",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return _rows_to_dicts(conn, rows)


def fetch_world_rules(db_path: str, project_id: str) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM world_rules WHERE project_id = ? ORDER BY name ASC",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return _rows_to_dicts(conn, rows)


def fetch_plot_events(db_path: str, project_id: str) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM plot_events WHERE project_id = ? ORDER BY event_id ASC",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return _rows_to_dicts(conn, rows)


def fetch_timeline_events(db_path: str, project_id: str) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM timeline_events WHERE project_id = ? ORDER BY timeline_event_id ASC",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return _rows_to_dicts(conn, rows)


# ---------------------------------------------------------------------------
# Story state（按 state_version ASC 拉全部快照）
# ---------------------------------------------------------------------------


def fetch_story_states(db_path: str, project_id: str) -> list[dict[str, Any]]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM story_states
            WHERE project_id = ?
            ORDER BY state_version ASC
            """,
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return _rows_to_dicts(conn, rows)


# ---------------------------------------------------------------------------
# 复合辅助：卷 → 章 映射（供 service 层卷合并 txt 拼装）
# ---------------------------------------------------------------------------


def fetch_volumes_for_book_txt(db_path: str, project_id: str) -> list[dict[str, Any]]:
    """返回 ``[{"volume": <vol row>, "chapters": [<ch row>, ...]}, ...]``。

    ``chapters`` 按 number ASC；卷内排序与 ``build_txt`` 内部一致。
    """
    conn = get_connection(db_path)
    try:
        vols = conn.execute(
            """
            SELECT * FROM volumes
            WHERE project_id = ?
            ORDER BY number ASC
            """,
            (project_id,),
        ).fetchall()
        chs = conn.execute(
            """
            SELECT * FROM chapters
            WHERE project_id = ?
            ORDER BY number ASC
            """,
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    bucket: dict[str, list[dict[str, Any]]] = {}
    no_volume: list[dict[str, Any]] = []
    for ch in chs:
        d = {k: ch[k] for k in ch.keys()}
        vid = d.get("volume_id")
        if vid:
            bucket.setdefault(vid, []).append(d)
        else:
            no_volume.append(d)
    out: list[dict[str, Any]] = []
    for v in vols:
        out.append(
            {
                "volume": {k: v[k] for k in v.keys()},
                "chapters": bucket.get(v["volume_id"], []),
            }
        )
    # 未挂卷的章节（理论上 0015_volumes 后项目层应不再发生）单独一组，标记 volume=None
    if no_volume:
        out.append({"volume": None, "chapters": no_volume})
    return out
