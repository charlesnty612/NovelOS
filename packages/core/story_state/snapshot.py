"""Canonical Story State 快照构建与落库（Sprint 2）。

职责：
- :func:`build_initial_state(conn, project_id) -> dict`：从领域表组装初始 Canonical State JSON。
- :func:`materialize_snapshot(...)`：在 commit 流程中把新状态 JSON 写入 ``story_states`` 表。

形状（对齐 ``#NovelOS.md`` Observer Prompt 中 ``previous_state``）：

.. code-block:: json

    {
      "state_version": 1,
      "characters": [
        {
          "character_id": "char_xxx",
          "name": "林夕",
          "current_state": {"location": "..."},
          "knowledge": [...],
          "beliefs": [...],
          "relationships": [...],
          "facet": "state"
        }
      ],
      "world": {
        "current_time_in_story": null,
        "locations": {},
        "factions": {},
        "world_rules": [],
        "active_resources": {}
      },
      "hooks": [],
      "debts": [],
      "recent_events": [],
      "events": {}
    }

设计要点：
- 函数 ``build_initial_state(conn, project_id)`` 接收 ``sqlite3.Connection`` 参数，便于
  Service 层把它放进同一事务；不接收 ``db_path``，避免隐式开新连接打破事务边界。
- ``materialize_snapshot(...)`` 把 state JSON 落库到 ``story_states``，返回 ``(snapshot_ref,
  hash)``，由 Service 用于填充 commit 的 ``previous_state.snapshot_ref/hash``。
- ``state_version`` 由调用方显式传入（本模块不负责自增）；Service 层负责管理递增语义
  （对齐 ``state-delta-v0.md §6.2``：每次 commit 严格 +1）。
- JSON 列读写用 ``json.dumps(ensure_ascii=False)`` / ``json.loads``，与 S1 模式一致。
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from typing import Any

# ----------------------------------------------------------------------------- helpers


def _dump(value: Any) -> str:
    """``snapshot_json`` 列写入：ensure_ascii=False（保持中文可读）。"""
    return json.dumps(value, ensure_ascii=False)


def _parse_json_column(raw: Any) -> Any:
    """统一解析 JSON 列：None/空 → {}；字符串 → loads。"""
    if raw is None:
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _parse_who_knows(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def _sha256_hex(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------- initial


def build_initial_state(conn: sqlite3.Connection, project_id: str) -> dict:
    """从领域表组装初始 Canonical State JSON。

    不创建任何 ``story_states`` 行；调用方决定是否落库。``state_version`` 字段由调用方
    视场景填入（``init_genesis`` 路径填 1；``get_current_state`` 无快照返回时填 0）。

    字段取值口径：
    - ``characters[].current_state`` = 该角色 ``MAX(state_version)`` 行的 ``state_json``。
    - ``knowledge`` / ``beliefs`` = 取自上述 ``state_json`` 同名 key（缺失 → []）。
    - ``relationships`` = 从 ``relationships`` 表聚合：``[from_character_id == character_id]``
      的全部关系（``to_character_id`` / ``relation_type`` / ``state_json``），按
      ``relationship_id`` ASC 排序保证确定性。
    - ``world.locations`` / ``world.factions`` = key 为 location_id/faction_id 的 dict，
      value 为 {name, statement, data_json, visibility}。
    - ``world.world_rules`` = 列表（含 world_rule_id / name / statement / data_json）。
    - ``hooks`` = 全部 ``hooks`` 行（按 hook_id ASC）；``debts`` = 全部 ``narrative_debts``
      行（按 debt_id ASC）。
    - ``recent_events`` = 空；``events`` = {}（无事件时）。
    """
    characters = _load_characters(conn, project_id)
    world = _load_world(conn, project_id)
    hooks = _load_hooks(conn, project_id)
    debts = _load_debts(conn, project_id)

    return {
        "state_version": 0,  # 调用方可覆盖
        "characters": characters,
        "world": world,
        "hooks": hooks,
        "debts": debts,
        "recent_events": [],
        "events": {},
    }


def _load_characters(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.character_id, c.name, c.core_json,
               cs.state_version AS latest_state_version,
               cs.state_json   AS latest_state_json
        FROM characters c
        LEFT JOIN character_states cs
          ON cs.character_id = c.character_id
         AND cs.state_version = (
              SELECT MAX(state_version) FROM character_states WHERE character_id = c.character_id
         )
        WHERE c.project_id = ?
        ORDER BY c.character_id ASC
        """,
        (project_id,),
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        cid = r["character_id"]
        state_json = _parse_json_column(r["latest_state_json"])
        # core_json：定义侧，保留原样供下游 prompt 参考（facet 仅 state 一层语义；definition 单独存）
        _ = _parse_json_column(r["core_json"])  # 暂未注入 current_state.facet；保留 raw 字段供未来
        knowledge = state_json.get("knowledge", [])
        if not isinstance(knowledge, list):
            knowledge = []
        beliefs = state_json.get("beliefs", [])
        if not isinstance(beliefs, list):
            beliefs = []
        relationships = _load_relationships_for(conn, cid)
        out.append(
            {
                "character_id": cid,
                "name": r["name"],
                "current_state": state_json,
                "knowledge": knowledge,
                "beliefs": beliefs,
                "relationships": relationships,
                "facet": "state",
            }
        )
    return out


def _load_relationships_for(conn: sqlite3.Connection, character_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT relationship_id, from_character_id, to_character_id, relation_type, state_json
        FROM relationships
        WHERE from_character_id = ?
        ORDER BY relationship_id ASC
        """,
        (character_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "relationship_id": r["relationship_id"],
                "from_character_id": r["from_character_id"],
                "to_character_id": r["to_character_id"],
                "relation_type": r["relation_type"],
                "state_json": _parse_json_column(r["state_json"]),
            }
        )
    return out


def _load_world(conn: sqlite3.Connection, project_id: str) -> dict:
    loc_rows = conn.execute(
        """
        SELECT location_id, name, statement, data_json, visibility
        FROM locations
        WHERE project_id = ?
        ORDER BY location_id ASC
        """,
        (project_id,),
    ).fetchall()
    locations: dict[str, dict] = {
        r["location_id"]: {
            "name": r["name"],
            "statement": r["statement"],
            "data_json": _parse_json_column(r["data_json"]),
            "visibility": r["visibility"],
        }
        for r in loc_rows
    }

    fac_rows = conn.execute(
        """
        SELECT faction_id, name, statement, data_json, visibility
        FROM factions
        WHERE project_id = ?
        ORDER BY faction_id ASC
        """,
        (project_id,),
    ).fetchall()
    factions: dict[str, dict] = {
        r["faction_id"]: {
            "name": r["name"],
            "statement": r["statement"],
            "data_json": _parse_json_column(r["data_json"]),
            "visibility": r["visibility"],
        }
        for r in fac_rows
    }

    rule_rows = conn.execute(
        """
        SELECT world_rule_id, name, statement, data_json, visibility
        FROM world_rules
        WHERE project_id = ?
        ORDER BY world_rule_id ASC
        """,
        (project_id,),
    ).fetchall()
    world_rules: list[dict] = [
        {
            "world_rule_id": r["world_rule_id"],
            "name": r["name"],
            "statement": r["statement"],
            "data_json": _parse_json_column(r["data_json"]),
            "visibility": r["visibility"],
        }
        for r in rule_rows
    ]

    return {
        # 当前故事内时间由 world.time 维护；初始为 null
        "current_time_in_story": None,
        "locations": locations,
        "factions": factions,
        "world_rules": world_rules,
        # active_resources 由 world_changes(politics/economy/event/time) 维护，初始空
        "active_resources": {},
    }


def _load_hooks(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT hook_id, name, introduced_chapter_id, status, importance,
               expected_payoff_chapter_id, payoff_chapter_id, visibility
        FROM hooks
        WHERE project_id = ?
        ORDER BY hook_id ASC
        """,
        (project_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "hook_id": r["hook_id"],
                "name": r["name"],
                "introduced_chapter_id": r["introduced_chapter_id"],
                "status": r["status"],
                "importance": r["importance"],
                "expected_payoff_chapter_id": r["expected_payoff_chapter_id"],
                "payoff_chapter_id": r["payoff_chapter_id"],
                "visibility": r["visibility"],
            }
        )
    return out


def _load_debts(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT debt_id, description, created_chapter_id, severity,
               deadline_chapter_id, status, visibility
        FROM narrative_debts
        WHERE project_id = ?
        ORDER BY debt_id ASC
        """,
        (project_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "debt_id": r["debt_id"],
                "description": r["description"],
                "created_chapter_id": r["created_chapter_id"],
                "severity": r["severity"],
                "deadline_chapter_id": r["deadline_chapter_id"],
                "status": r["status"],
                "visibility": r["visibility"],
            }
        )
    return out


# ----------------------------------------------------------------------------- snapshot persist


def materialize_snapshot(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    state_version: int,
    snapshot_json: dict,
    commit_id: str,
    created_at: str,
) -> tuple[str, str]:
    """把新状态 JSON 落库到 ``story_states``，返回 ``(snapshot_ref, sha256)``。

    设计要点：
    - ``snapshot_ref`` 用 ``state-v<n>-<commit_id>`` 形式，简化版即可（不写对象存储）。
    - ``hash`` 为 snapshot 序列化后的 SHA-256，commit schema 要求 ``sha256:<64hex>``。
    - 由调用方负责 state_version 唯一性；本模块不做最大 version 检测。
    - 深拷贝 ``snapshot_json``，避免后续 ``apply_delta`` 之类的副作用影响已落库内容。
    """
    payload = copy.deepcopy(snapshot_json)
    payload["state_version"] = state_version
    serialized = _dump(payload)
    snapshot_ref = f"story_states/{project_id}/state-v{state_version}-{commit_id}.json"
    digest = _sha256_hex(serialized)
    conn.execute(
        """
        INSERT INTO story_states (project_id, state_version, snapshot_json, commit_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project_id, state_version, serialized, commit_id, created_at),
    )
    return snapshot_ref, digest


__all__ = ["build_initial_state", "materialize_snapshot"]
