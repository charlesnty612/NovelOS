"""skill SQL 与行 → dict 投影。

设计要点（与 ``packages/core/genre/queries.py`` 同款分层）：
- 本模块只放 SQL 文本与纯函数投影，不持有连接、不 commit；连接与事务在
  :mod:`packages.core.skill.service`。
- ``payload_json`` 在投影层解析为 dict（``payload`` 键）；解析失败 → 空 dict
  （读路径容忍脏数据，不炸装配 / 接口）。
- 项目绑定是**多 slot 有序**（关联表 ``project_skills`` + ``ord``），
  与题材包的「单 slot」语义不同：列表按 ``ord, skill_id`` 排序。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .model import SkillPayload, parse_payload

__all__ = [
    "BIND_SQL",
    "COUNT_BINDINGS_SQL",
    "DELETE_PROJECT_BINDINGS_SQL",
    "DELETE_SKILL_SQL",
    "GET_SKILL_SQL",
    "INSERT_SKILL_SQL",
    "LIST_PROJECT_SKILLS_SQL",
    "LIST_SKILLS_BY_NAME_SQL",
    "LIST_SKILLS_SQL",
    "PROJECT_EXISTS_SQL",
    "SKILL_EXISTS_SQL",
    "UNBIND_SQL",
    "UPDATE_SKILL_SQL",
    "skill_row_to_dict",
    "skill_row_to_summary",
]

# 公共选择列：skill 全列 + 被绑定项目数（列表页「被 N 个项目使用」与 DELETE 前
# 的 409 判定共用同一口径）。
_SELECT_COLUMNS = """
    sk.skill_id, sk.name, sk.description, sk.version, sk.payload_json,
    sk.source_path, sk.created_at, sk.updated_at,
    (SELECT COUNT(*) FROM project_skills ps WHERE ps.skill_id = sk.skill_id)
        AS bound_project_count
"""

LIST_SKILLS_SQL = f"""
    SELECT {_SELECT_COLUMNS}
    FROM skills sk
    ORDER BY sk.created_at DESC, sk.skill_id DESC
"""

LIST_SKILLS_BY_NAME_SQL = f"""
    SELECT {_SELECT_COLUMNS}
    FROM skills sk
    WHERE sk.name = ?
    ORDER BY sk.created_at DESC, sk.skill_id DESC
"""

GET_SKILL_SQL = f"""
    SELECT {_SELECT_COLUMNS}
    FROM skills sk
    WHERE sk.skill_id = ?
"""

INSERT_SKILL_SQL = """
    INSERT INTO skills (
        skill_id, name, description, version, payload_json, source_path,
        created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

# 更新：提供 payload 时 version + 1（缓存键指纹维度跟随）；未提供则保留原 version。
UPDATE_SKILL_SQL = """
    UPDATE skills
    SET name = ?,
        description = ?,
        payload_json = ?,
        source_path = ?,
        version = ?,
        updated_at = ?
    WHERE skill_id = ?
"""

DELETE_SKILL_SQL = "DELETE FROM skills WHERE skill_id = ?"

COUNT_BINDINGS_SQL = "SELECT COUNT(*) AS n FROM project_skills WHERE skill_id = ?"

PROJECT_EXISTS_SQL = "SELECT 1 FROM projects WHERE project_id = ?"

SKILL_EXISTS_SQL = "SELECT 1 FROM skills WHERE skill_id = ?"

# 绑定（覆盖式）：先删该项目的既有绑定，再按给定顺序整体写入。
DELETE_PROJECT_BINDINGS_SQL = "DELETE FROM project_skills WHERE project_id = ?"

BIND_SQL = """
    INSERT INTO project_skills (project_id, skill_id, ord)
    VALUES (?, ?, ?)
"""

UNBIND_SQL = "DELETE FROM project_skills WHERE project_id = ? AND skill_id = ?"

# 项目当前绑定集合（有序）：ord 小者在前，同 ord 按 skill_id 稳定排序。
LIST_PROJECT_SKILLS_SQL = f"""
    SELECT {_SELECT_COLUMNS}
    FROM project_skills ps
    JOIN skills sk ON sk.skill_id = ps.skill_id
    WHERE ps.project_id = ?
    ORDER BY ps.ord ASC, ps.skill_id ASC
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
    """从 payload 抽取摘要派生字段（适用环节 / 参考材料数 / 检查项数）。"""
    view: SkillPayload = parse_payload(payload)
    return {
        "applies_to": list(view.applies_to),
        "reference_count": len(view.references),
        "check_count": len(view.checks),
    }


def skill_row_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """行 → 完整 dict（``payload_json`` 解析为 ``payload``）。"""
    raw = dict(row)
    payload = _parse_json_dict(raw.pop("payload_json", None))
    return {
        "skill_id": raw.get("skill_id"),
        "name": raw.get("name"),
        "description": raw.get("description"),
        "version": int(raw.get("version") or 0),
        "payload": payload,
        "source_path": raw.get("source_path"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "bound_project_count": int(raw.get("bound_project_count") or 0),
    }


def skill_row_to_summary(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """行 → 摘要 dict（派生计数 + 适用环节）。"""
    full = skill_row_to_dict(row)
    summary = {k: v for k, v in full.items() if k != "payload"}
    summary.update(_payload_summary_fields(full["payload"]))
    return summary
