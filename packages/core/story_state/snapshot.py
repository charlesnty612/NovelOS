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
import logging
import sqlite3
from typing import Any

from .snapshots import _parse_required_json

logger = logging.getLogger("novelos.story_state.snapshot")

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


ALL_REBUILD_COLLECTIONS: tuple[str, ...] = (
    "characters",
    "locations",
    "factions",
    "world_rules",
    "plot_events",
    "hooks",
    "narrative_debts",
)


def rebuild_snapshot_collections_from_db(
    conn: sqlite3.Connection,
    project_id: str,
    snapshot: dict,
    collections: list[str],
) -> dict:
    """以 DB 为权威重建 snapshot 中指定集合的实体数据,返回新 snapshot。

    V3.1 P1-1:消除 ``story_states.snapshot_json`` 与 DB 实体表的双源漂移。
    在 ``commit_delta`` 事务内,write_through 落库后、新快照持久化前调用——
    对 ``characters / locations / factions / world_rules / plot_events / hooks /
    narrative_debts`` 7 个集合,按 DB 当前实表重建 snapshot 中对应字段(整体替换),
    后续 ``materialize_snapshot`` 写入 story_states 的即为 DB 权威版本。

    设计要点:
    - **DB 为权威**:只重建传入的 ``collections``,未列出的字段(state_version /
      recent_events / active_resources / current_time_in_story 等)保持原值不动;
      调用方后续可能再 mutate(如 rollback 路径的 ``apply_inverse_cleanup_to_state``),
      重建在前保证 inverse 清理仍能正确剔除已被逆路径清理的 event_id。
    - **复用既有口径**:characters / locations / factions / world_rules / hooks /
      narrative_debts 直接调用本模块 ``_load_characters`` / ``_load_world`` /
      ``_load_hooks`` / ``_load_debts``,保证字段名(character_id / world_rule_id /
      hook_id / debt_id 等)与 ``check_state_sync.COLLECTIONS`` 完全一致,两套代码
      不会各写各的字段口径。
    - **plot_events 单独实现**:DB 侧 ``plot_events`` 表的列与 snapshot 侧
      ``events`` dict 的 value 形状不对齐——snapshot 的 value 是
      ``{type, participants, time, description}``(见 ``applier._apply_new_events``),
      DB 列有 ``type / participants_json / time_json / cause_json / effects_json /
      description(V3.1 P1-1.1 迁移 0013 落地)`` 等。
      ``description`` 自 V3.1 P1-1.1 起在 write_through 阶段下沉到 plot_events.description,
      本函数 SELECT 时一并取回——重建后 ``events[eid].description`` 携带 observer 当时
      给出的描述(旧行 description=NULL → None,与 Schema ``description: string|null``
      对齐)。
    - 未知集合名静默跳过(防御:允许调用方传入 COLLECTIONS 子集做按需重建)。
    - 深拷贝 ``snapshot`` 起手,避免 mutate 调用方传入的原 dict。

    Parameters
    ----------
    conn : sqlite3.Connection
        与 ``commit_delta`` 同一事务的连接。
    project_id : str
        项目 id(用于过滤该 project 实体)。
    snapshot : dict
        当前待重建的 snapshot(由 ``apply_delta`` 产生,或由 ``build_initial_state`` 初始化)。
    collections : list[str]
        需要重建的集合名(取 ``ALL_REBUILD_COLLECTIONS`` 子集)。

    Returns
    -------
    dict
        重建后的 snapshot(深拷贝结果,不再与输入共享引用)。
    """
    new_snapshot = copy.deepcopy(snapshot)
    # 复用 _load_world 整体 dict,从中拆出 locations/factions/world_rules 三个子集
    # —— 单一来源保证字段口径一致(load_world 已是 build_initial_state 的复用路径)。
    needs_world_load = any(c in ("locations", "factions", "world_rules") for c in collections)
    world: dict | None = None
    if needs_world_load:
        world = _load_world(conn, project_id)

    for coll in collections:
        if coll == "characters":
            new_snapshot["characters"] = _load_characters(conn, project_id)
        elif coll == "locations":
            if "world" not in new_snapshot or not isinstance(new_snapshot["world"], dict):
                new_snapshot["world"] = {}
            new_snapshot["world"]["locations"] = (world or _load_world(conn, project_id))["locations"]
        elif coll == "factions":
            if "world" not in new_snapshot or not isinstance(new_snapshot["world"], dict):
                new_snapshot["world"] = {}
            new_snapshot["world"]["factions"] = (world or _load_world(conn, project_id))["factions"]
        elif coll == "world_rules":
            if "world" not in new_snapshot or not isinstance(new_snapshot["world"], dict):
                new_snapshot["world"] = {}
            new_snapshot["world"]["world_rules"] = (world or _load_world(conn, project_id))["world_rules"]
        elif coll == "plot_events":
            new_snapshot["events"] = _load_plot_events_dict(conn, project_id)
        elif coll == "hooks":
            new_snapshot["hooks"] = _load_hooks(conn, project_id)
        elif coll == "narrative_debts":
            new_snapshot["debts"] = _load_debts(conn, project_id)
        # 未知集合名静默跳过
    return new_snapshot


def _load_plot_events_dict(conn: sqlite3.Connection, project_id: str) -> dict[str, dict]:
    """重建 snapshot.events:以 DB plot_events 表为权威,返回 ``{event_id: {type, participants, time, description}}``。

    与 ``applier._apply_new_events``(Sprint 2)写入 snapshot 的 value 形状对齐:
    ``applier`` 写入::

        events[eid] = {
            "type": ev.get("type"),
            "participants": ev.get("participants") or [],
            "time": ev.get("time") or {},
            "description": ev.get("description"),
        }

    本函数从 plot_events 行反推:
    - ``type`` → plot_events.type
    - ``participants`` → json.loads(participants_json)(空或解析失败 → [])
    - ``time`` → json.loads(time_json)(空或解析失败 → {})
    - ``description`` → plot_events.description(V3.1 P1-1.1 迁移 0013 落地;旧行 NULL → None)

    返回字典的 key 集合即 plot_events.event_id 全集(去重,与 check_state_sync.COLLECTIONS
    对照口径一致)。
    """
    rows = conn.execute(
        """
        SELECT event_id, type, participants_json, time_json, description,
               visibility, who_knows
        FROM plot_events
        WHERE project_id = ?
        ORDER BY event_id ASC
        """,
        (project_id,),
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        eid = r["event_id"]
        if not eid:
            continue
        # participants_json 解析(可能为 '[]' / JSON 数组 / 缺失)
        participants_raw = r["participants_json"]
        participants: list = []
        if participants_raw:
            try:
                parsed = json.loads(participants_raw)
                if isinstance(parsed, list):
                    participants = parsed
            except (TypeError, json.JSONDecodeError):
                participants = []
        # time_json 解析
        time_raw = r["time_json"]
        time_obj: dict = {}
        if time_raw:
            try:
                parsed_t = json.loads(time_raw)
                if isinstance(parsed_t, dict):
                    time_obj = parsed_t
            except (TypeError, json.JSONDecodeError):
                time_obj = {}
        out[eid] = {
            "type": r["type"],
            "participants": participants,
            "time": time_obj,
            # V3.1 P1-1.1：迁移 0013 给 plot_events 加了 description 列,此处
            # 直接读取并落到 events[eid].description,重建后事件描述不再丢失。
            # 旧行 description=NULL → events[eid].description=None(与 Schema
            # ``description: string|null`` 一致);observer 本次 delta 给出描述
            # 时,write_through 写穿,此处取回即"自愈"语义。
            "description": r["description"],
            # 携带伴随列让 knowledge_leakage guardrail 读到一致字段；
            # 与 hooks/debts 同款口径：DB 缺省 NULL → None（沿用）。
            "visibility": r["visibility"],
            "who_knows": _parse_who_knows(r["who_knows"]),
        }
    return out


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
        relationships = _load_relationships_for(conn, "character", cid)
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


def _load_relationships_for(
    conn: sqlite3.Connection, endpoint_kind: str, endpoint_id: str
) -> list[dict]:
    """按 (endpoint_kind, endpoint_id) 从 relationships 表聚合。

    修复 wfr_6619a7bfa6fa：关系端点既可以是 character_id 也可以是 faction_id，
    0022 移除 FK 后允许两端写入任意 id 字符串。本函数按 endpoint_kind 区分：
    - ``"character"`` → 仅取 ``from_character_id == endpoint_id`` 的行；
    - ``"faction"`` → 仅取 ``from_character_id == endpoint_id`` 且端点类型为
      faction 的行（实现：通过查 factions.faction_id 探测端点是否真为 faction；
      避免 character 端点 id 撞到 faction 桶产生污染）。

    返回 list[dict]，按 relationship_id ASC 排序保证确定性。
    """
    rows = conn.execute(
        """
        SELECT relationship_id, from_character_id, to_character_id, relation_type,
               state_json, visibility, who_knows
        FROM relationships
        WHERE from_character_id = ?
        ORDER BY relationship_id ASC
        """,
        (endpoint_id,),
    ).fetchall()
    if endpoint_kind == "faction":
        # 二次过滤：端点必须是真实 faction（防止 character id 撞到 faction 桶
        # 或历史脏数据混入）。取 1 行即可探测。
        kind_probe = conn.execute(
            "SELECT 1 FROM factions WHERE faction_id = ?", (endpoint_id,)
        ).fetchone()
        if kind_probe is None:
            return []
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "relationship_id": r["relationship_id"],
                "from_character_id": r["from_character_id"],
                "to_character_id": r["to_character_id"],
                "relation_type": r["relation_type"],
                "state_json": _parse_json_column(r["state_json"]),
                "visibility": r["visibility"],
                "who_knows": _parse_who_knows(r["who_knows"]),
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
            # wfr_6619a7bfa6fa：factions[fid] 桶补 relationships 列表——
            # 关系端点为 faction 时（faction↔faction / person↔org）由 applier
            # 写入此处；rebuild_snapshot_collections_from_db 重建快照时也会
            # 从 relationships 表聚合写入（与 characters[cid].relationships
            # 同源数据，DB 为权威）。
            "relationships": _load_relationships_for(conn, "faction", r["faction_id"]),
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
               expected_payoff_chapter_id, payoff_chapter_id, visibility, who_knows
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
                # who_knows：knowledge_leakage guardrail 读取快照此字段做防泄漏校验，
                # 缺失/null → None（与 applier 写时口径一致；guardrail 见
                # guardrails.knowledge_leakage §4.5）。
                "who_knows": _parse_who_knows(r["who_knows"]),
            }
        )
    return out


def _load_debts(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT debt_id, description, created_chapter_id, severity,
               deadline_chapter_id, status, visibility, who_knows
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
                "who_knows": _parse_who_knows(r["who_knows"]),
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


__all__ = [
    "ALL_REBUILD_COLLECTIONS",
    "build_initial_state",
    "materialize_snapshot",
    "rebuild_snapshot_collections_from_db",
    "repair_current_snapshot_world",
]


# ----------------------------------------------------------------------------- production repair


def _summarize_world_entry_shape(entry: Any) -> str:
    """将 world bucket 条目形态映射成可读标签，用于修复前后对比摘要。"""
    if entry is None:
        return "missing"
    if isinstance(entry, str):
        return "str"
    if isinstance(entry, dict):
        keys = sorted(entry.keys())
        return "dict(" + ",".join(keys) + ")"
    return type(entry).__name__


def repair_current_snapshot_world(
    db_path: str,
    project_id: str,
) -> dict:
    """生产数据修复：以 DB 为权威重建该项目最新 ``story_states`` 快照的 world
    三集合（locations / factions / world_rules）。

    修复 wfr_3cb2182a30f6：rollback 逆清理旧形状 hint 整条目替换把
    ``world.factions[fac]`` 污染成 ``str``、``world.locations[loc]`` 污染成
    残壳 dict（丢 name/statement/data_json），后续 commit 在 applier 抛
    TypeError。本函数直接把快照的 world 三集合按当前 DB 实表重建回正确形态，
    并 UPDATE story_states.snapshot_json（同连接，原子）。**只动 world 三集合，
    不动 characters / hooks / debts / events / recent_events / state_version。**

    Parameters
    ----------
    db_path : str
        SQLite 数据库文件路径（与 StoryStateService.db_path 同源）。
    project_id : str
        项目 id。

    Returns
    -------
    dict
        修复摘要 ``{
            "project_id": str,
            "state_version": int,
            "before": {
                "locations": {wid: "<shape>"},
                "factions": {wid: "<shape>"},
                "world_rules": [{"world_rule_id": rid, "shape": "<shape>"}],
            },
            "after": {... 同 before 的结构 ...},
            "repaired": bool,  # 任一条目形状变化即 True
        }``

    Notes
    -----
    设计内副作用（P2-3 备注）：本函数直接覆写最新 ``story_states`` 行的
    ``snapshot_json``，但**不**更新 ``state_version`` / ``commit_id``，
    **不**重算 ``commits.snapshot_ref`` 的 digest/一致性。理由：
    ``story_states`` schema 仅含 ``project_id / state_version /
    snapshot_json / commit_id / created_at``，无 digest 列；
    ``commits.snapshot_ref`` 无一致性校验；下次 commit 即自然覆盖。
    故「最新快照与上一 commit 不完全同构」是修复的预期副作用，
    不破坏任何一致性检查，无需在调用方做额外补偿。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 取该项目最新 story_states 行
        row = conn.execute(
            """
            SELECT state_version, snapshot_json
            FROM story_states
            WHERE project_id = ?
            ORDER BY state_version DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            return {
                "project_id": project_id,
                "state_version": None,
                "before": {"locations": {}, "factions": {}, "world_rules": []},
                "after": {"locations": {}, "factions": {}, "world_rules": []},
                "repaired": False,
                "note": "no story_states row for project",
            }
        state_version = row["state_version"]
        snap = _parse_required_json(row["snapshot_json"], {})
        world = snap.get("world") if isinstance(snap, dict) else {}
        if not isinstance(world, dict):
            world = {}

        # 修复前形状摘要
        before_locations = {
            wid: _summarize_world_entry_shape(v)
            for wid, v in (world.get("locations") or {}).items()
        }
        before_factions = {
            wid: _summarize_world_entry_shape(v)
            for wid, v in (world.get("factions") or {}).items()
        }
        before_rules = []
        for r in (world.get("world_rules") or []):
            if isinstance(r, dict):
                before_rules.append({
                    "world_rule_id": r.get("world_rule_id"),
                    "shape": _summarize_world_entry_shape(r),
                })
            else:
                before_rules.append({
                    "world_rule_id": None,
                    "shape": _summarize_world_entry_shape(r),
                })

        # 以 DB 为权威重建 world 三集合
        rebuilt_world = _load_world(conn, project_id)
        # 仅替换三集合；保留 current_time_in_story / active_resources / world 其它键
        # 防御（P2-2）：world 在上文已强制为 dict，理论上非 dict 不会到达此处；
        # 但仍显式守卫一次：若为非 dict（极端情况下 snap.get('world') 越过了上面的
        # isinstance 判断），按空 dict 路径处理，与函数内 world.get 守卫口径一致。
        if not isinstance(world, dict):
            logger.warning(
                "repair_current_snapshot_world: world 非 dict（类型=%s），"
                "按空 dict 路径处理",
                type(world).__name__,
            )
            world = {}
        new_world = dict(world)
        new_world["locations"] = rebuilt_world["locations"]
        new_world["factions"] = rebuilt_world["factions"]
        new_world["world_rules"] = rebuilt_world["world_rules"]

        # 修复后形状摘要
        after_locations = {
            wid: _summarize_world_entry_shape(v)
            for wid, v in new_world["locations"].items()
        }
        after_factions = {
            wid: _summarize_world_entry_shape(v)
            for wid, v in new_world["factions"].items()
        }
        after_rules = [
            {
                "world_rule_id": r.get("world_rule_id"),
                "shape": _summarize_world_entry_shape(r),
            }
            for r in new_world["world_rules"]
        ]

        # 判定是否真有变化
        repaired = (
            before_locations != after_locations
            or before_factions != after_factions
            or before_rules != after_rules
        )

        if repaired:
            # 写回 story_states.snapshot_json（同连接）
            snap["world"] = new_world
            # state_version 由 commit 维护，本函数不递增；仅覆写 snapshot_json
            conn.execute(
                """
                UPDATE story_states
                SET snapshot_json = ?
                WHERE project_id = ? AND state_version = ?
                """,
                (_dump(snap), project_id, state_version),
            )
            conn.commit()

        return {
            "project_id": project_id,
            "state_version": state_version,
            "before": {
                "locations": before_locations,
                "factions": before_factions,
                "world_rules": before_rules,
            },
            "after": {
                "locations": after_locations,
                "factions": after_factions,
                "world_rules": after_rules,
            },
            "repaired": repaired,
        }
    finally:
        conn.close()
