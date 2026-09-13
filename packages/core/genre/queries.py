"""题材包 SQL 与行 → dict 投影（题材库 P1a）。

设计要点（与 ``packages/domain/reference/service.py`` 同款分层）：
- 本模块只放 SQL 文本与纯函数投影，不持有连接、不 commit；连接与事务在
  :mod:`packages.core.genre.service`。
- ``payload_json`` 在投影层解析为 dict（``payload`` 键）；解析失败 → 空 dict
  （读路径容忍脏数据，不炸装配/接口）。
- 摘要投影（``pack_row_to_summary``）复用 :class:`GenrePackPayload` 做字段抽取，
  派生计数口径与 schema 对齐：``payoff_type_count`` = payoff_types 条数，
  ``structure_model`` = structure_templates.structure_model，
  ``chapter_words_target`` = pacing.chapter_words.target。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .model import GenrePackPayload, parse_payload

__all__ = [
    "BIND_SQL",
    "COUNT_BINDINGS_SQL",
    "DELETE_PACK_SQL",
    "GET_PACK_SQL",
    "INSERT_PACK_SQL",
    "LIST_PACKS_BY_TAG_SQL",
    "LIST_PACKS_SQL",
    "PROJECT_EXISTS_SQL",
    "SELECT_PROJECT_BINDING_SQL",
    "UNBIND_SQL",
    "UPDATE_PACK_SQL",
    "pack_row_to_dict",
    "pack_row_to_summary",
]

# 公共选择列：pack 全列 + 被绑定项目数（列表页「被 N 个项目使用」与 DELETE 前
# 的 409 判定共用同一口径）。
_SELECT_COLUMNS = """
    gp.pack_id, gp.name, gp.genre_tag, gp.version, gp.payload_json,
    gp.source_path, gp.created_at, gp.updated_at,
    (SELECT COUNT(*) FROM projects pj WHERE pj.genre_pack_id = gp.pack_id)
        AS bound_project_count
"""

LIST_PACKS_SQL = f"""
    SELECT {_SELECT_COLUMNS}
    FROM genre_packs gp
    ORDER BY gp.created_at DESC, gp.pack_id DESC
"""

LIST_PACKS_BY_TAG_SQL = f"""
    SELECT {_SELECT_COLUMNS}
    FROM genre_packs gp
    WHERE gp.genre_tag = ?
    ORDER BY gp.created_at DESC, gp.pack_id DESC
"""

GET_PACK_SQL = f"""
    SELECT {_SELECT_COLUMNS}
    FROM genre_packs gp
    WHERE gp.pack_id = ?
"""

INSERT_PACK_SQL = """
    INSERT INTO genre_packs (
        pack_id, name, genre_tag, version, payload_json, source_path,
        created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

# 更新：提供 payload 时 version + 1（缓存键指纹维度跟随）；未提供则保留原 version。
UPDATE_PACK_SQL = """
    UPDATE genre_packs
    SET name = ?,
        genre_tag = ?,
        payload_json = ?,
        source_path = ?,
        version = ?,
        updated_at = ?
    WHERE pack_id = ?
"""

DELETE_PACK_SQL = "DELETE FROM genre_packs WHERE pack_id = ?"

COUNT_BINDINGS_SQL = (
    "SELECT COUNT(*) AS n FROM projects WHERE genre_pack_id = ?"
)

PROJECT_EXISTS_SQL = "SELECT 1 FROM projects WHERE project_id = ?"

BIND_SQL = "UPDATE projects SET genre_pack_id = ?, updated_at = ? WHERE project_id = ?"

UNBIND_SQL = "UPDATE projects SET genre_pack_id = NULL, updated_at = ? WHERE project_id = ?"

SELECT_PROJECT_BINDING_SQL = """
    SELECT pj.project_id AS project_id, pj.genre_pack_id AS pack_id
    FROM projects pj
    WHERE pj.project_id = ?
"""


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    """``payload_json`` 原文 → dict；非法 / 非对象 → 空 dict（读侧容忍）。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _payload_summary_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """从 payload 抽取摘要派生字段（计数 / 结构模型 / 章目标字数）。"""
    view: GenrePackPayload = parse_payload(payload)
    structure_model: Any = None
    if isinstance(view.structure_templates, dict):
        structure_model = view.structure_templates.get("structure_model")
    chapter_words_target: Any = None
    if isinstance(view.pacing, dict):
        chapter_words = view.pacing.get("chapter_words")
        if isinstance(chapter_words, dict):
            chapter_words_target = chapter_words.get("target")
    return {
        "payoff_type_count": len(view.payoff_types),
        "structure_model": structure_model if isinstance(structure_model, str) else None,
        "chapter_words_target": (
            chapter_words_target if isinstance(chapter_words_target, int) else None
        ),
    }


def pack_row_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """行 → 完整 dict（``payload_json`` 解析为 ``payload``）。"""
    raw = dict(row)
    payload = _parse_json_dict(raw.pop("payload_json", None))
    return {
        "pack_id": raw.get("pack_id"),
        "name": raw.get("name"),
        "genre_tag": raw.get("genre_tag"),
        "version": int(raw.get("version") or 0),
        "payload": payload,
        "source_path": raw.get("source_path"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "bound_project_count": int(raw.get("bound_project_count") or 0),
    }


def pack_row_to_summary(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """行 → 摘要 dict（派生计数 + 结构模型 + 章目标字数）。"""
    full = pack_row_to_dict(row)
    summary = {k: v for k, v in full.items() if k != "payload"}
    summary.update(_payload_summary_fields(full["payload"]))
    return summary
