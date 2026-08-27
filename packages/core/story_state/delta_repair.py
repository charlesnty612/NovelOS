"""Story State Delta 确定性自动修复（arbiter-lite）。

职责：
- :func:`repair_delta` 在 ``validate_delta`` 之前对 observer 产出的 delta 做
  一层确定性、无副作用的自动修复，只修「信息可唯一确定」的结构性错误：

  1. **fill-before**：``op='update'`` 时 ``before``（或 debt 的 ``status_before`` /
     ``severity_before``、hook 的 ``from_status``）缺失，且能从 snapshot / DB
     查到当前值，则补齐。
  2. **insert-to-update**：``op='add'`` 的目标 id 在 snapshot / DB 中已存在，
     降级为 ``op='update'`` 并用现有行填 before（或对应状态字段）。
  3. **drop-duplicate**：insert-to-update 后 ``after`` 与当前值完全一致，
     直接丢弃该 change；``new_events`` / ``new_hooks`` 的 id 已存在时也直接丢弃。

- 修不了的不动，继续走 ``validate_delta`` 与既有按腿重试逻辑。
- 所有修复以 ``list[dict]`` 形式返回，供 pipeline 写入 ctx / 质量审计。

设计取舍：
- 纯函数优先：核心修复逻辑只读注入的 ``snapshot`` 与预建索引；``db_path`` 仅用于
  snapshot 缺失时的只读兜底（查询当前 project_id 下对应实体表）。
- 不修改输入 delta，返回新 dict。
- 只处理「id / before 语义」明确可修的数组；不尝试语义推断（如猜测缺失字段、
  重命名冲突 id 等），避免引入不确定性。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from packages.core.db import get_connection

from .snapshots import _parse_required_json


def _project_id_for_chapter(db_path: str | os.PathLike | Any, chapter_id: str) -> str | None:
    if not db_path or not isinstance(db_path, (str, bytes, os.PathLike)):
        return None
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
        return row["project_id"] if row else None
    finally:
        conn.close()


def _deep_equal(a: Any, b: Any) -> bool:
    return a == b


def _field_key(field: Any) -> str:
    """把 'state.location' / 'data_json.climate' 等字段路径转成写透用的 key。"""
    if not isinstance(field, str):
        return ""
    return field.split(".")[-1] if "." in field else field


def _build_entity_index(
    snapshot: dict[str, Any] | None,
    db_path: str | os.PathLike | Any | None,
    chapter_id: str,
) -> dict[str, dict[str, Any]]:
    """从 snapshot 与可选 DB 构建统一实体索引。

    返回结构：
        {
            "characters": {character_id: {"current_state": ..., "core_json": ..., "exists": True}},
            "locations":  {location_id:  {"data_json": ..., "exists": True}},
            "factions":   {faction_id:   {"data_json": ..., "exists": True}},
            "world_rules":{world_rule_id:{"data_json": ..., "exists": True}},
            "relationships": {relationship_id: {"state_json": ..., "from_id": ..., "to_id": ..., "rel_type": ..., "exists": True}},
            "hooks":      {hook_id:      {"status": ..., "exists": True}},
            "debts":      {debt_id:      {"status": ..., "severity": ..., "exists": True}},
            "events":     {event_id:     {"exists": True}},
        }
    """
    index: dict[str, dict[str, Any]] = {
        "characters": {},
        "locations": {},
        "factions": {},
        "world_rules": {},
        "relationships": {},
        "hooks": {},
        "debts": {},
        "events": {},
    }

    def _ensure(bucket: str, eid: str, data: dict[str, Any]) -> None:
        existing = index[bucket].get(eid)
        if existing is None:
            index[bucket][eid] = {"exists": True, **data}
        else:
            # snapshot 已存在时以 snapshot 为准，但 JSON 容器字段若 snapshot 给出空值
            # 而 DB 有更完整数据，则用 DB 兜底（trimmed snapshot 常把未 touch 实体摘要为空）。
            for k, v in data.items():
                if k not in existing:
                    existing[k] = v
                elif existing[k] is None:
                    existing[k] = v
                elif isinstance(existing[k], dict) and isinstance(v, dict):
                    if not existing[k] and v:
                        existing[k] = v
                elif isinstance(existing[k], list) and isinstance(v, list):
                    if not existing[k] and v:
                        existing[k] = v

    # ---- 1. snapshot 层 ----
    if isinstance(snapshot, dict):
        chars = snapshot.get("characters") or []
        if isinstance(chars, list):
            for c in chars:
                if not isinstance(c, dict):
                    continue
                cid = c.get("character_id")
                if not isinstance(cid, str) or not cid:
                    continue
                current_state = c.get("current_state")
                if not isinstance(current_state, dict):
                    current_state = {}
                _ensure("characters", cid, {"current_state": current_state})
                rels = c.get("relationships")
                if isinstance(rels, list):
                    for rel in rels:
                        if not isinstance(rel, dict):
                            continue
                        rid = rel.get("relationship_id")
                        if not isinstance(rid, str) or not rid:
                            continue
                        _ensure(
                            "relationships",
                            rid,
                            {
                                "state_json": rel.get("state_json") or {},
                                "from_id": rel.get("from_character_id"),
                                "to_id": rel.get("to_character_id"),
                                "rel_type": rel.get("relation_type"),
                            },
                        )

        world = snapshot.get("world") or {}
        if isinstance(world, dict):
            locs = world.get("locations") or {}
            if isinstance(locs, dict):
                for lid, loc in locs.items():
                    if isinstance(loc, dict):
                        _ensure("locations", lid, {"data_json": loc.get("data_json") or {}})
                    else:
                        _ensure("locations", lid, {"data_json": {}})
            facs = world.get("factions") or {}
            if isinstance(facs, dict):
                for fid, fac in facs.items():
                    if isinstance(fac, dict):
                        _ensure("factions", fid, {"data_json": fac.get("data_json") or {}})
                    else:
                        _ensure("factions", fid, {"data_json": {}})
            rules = world.get("world_rules") or []
            if isinstance(rules, list):
                for r in rules:
                    if isinstance(r, dict):
                        rid = r.get("world_rule_id")
                        if isinstance(rid, str) and rid:
                            _ensure("world_rules", rid, {"data_json": r.get("data_json") or {}})

        hooks = snapshot.get("hooks") or []
        if isinstance(hooks, list):
            for h in hooks:
                if isinstance(h, dict):
                    hid = h.get("hook_id")
                    if isinstance(hid, str) and hid:
                        _ensure("hooks", hid, {"status": h.get("status")})

        debts = snapshot.get("debts") or []
        if isinstance(debts, list):
            for d in debts:
                if isinstance(d, dict):
                    did = d.get("debt_id")
                    if isinstance(did, str) and did:
                        _ensure("debts", did, {"status": d.get("status"), "severity": d.get("severity")})

        events = snapshot.get("events") or {}
        if isinstance(events, dict):
            for eid in events.keys():
                if isinstance(eid, str) and eid:
                    _ensure("events", eid, {})
        recent_events = snapshot.get("recent_events") or []
        if isinstance(recent_events, list):
            for eid in recent_events:
                if isinstance(eid, str) and eid:
                    _ensure("events", eid, {})

    # ---- 2. DB 兜底（snapshot 未覆盖的实体）----
    if db_path:
        project_id = _project_id_for_chapter(db_path, chapter_id)
        if project_id:
            conn = get_connection(db_path)
            try:
                # characters + latest state_json + core_json
                rows = conn.execute(
                    """
                    SELECT c.character_id, c.core_json,
                           cs.state_json AS latest_state_json
                    FROM characters c
                    LEFT JOIN character_states cs
                      ON cs.character_id = c.character_id
                     AND cs.state_version = (
                          SELECT MAX(state_version) FROM character_states
                          WHERE character_id = c.character_id
                     )
                    WHERE c.project_id = ?
                    """,
                    (project_id,),
                ).fetchall()
                for r in rows:
                    cid = r["character_id"]
                    current_state = _parse_required_json(r["latest_state_json"], {})
                    core_json = _parse_required_json(r["core_json"], {})
                    _ensure(
                        "characters",
                        cid,
                        {
                            "current_state": current_state if isinstance(current_state, dict) else {},
                            "core_json": core_json if isinstance(core_json, dict) else {},
                        },
                    )

                # locations
                rows = conn.execute(
                    "SELECT location_id, data_json FROM locations WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                for r in rows:
                    data_json = _parse_required_json(r["data_json"], {})
                    _ensure("locations", r["location_id"], {"data_json": data_json if isinstance(data_json, dict) else {}})

                # factions
                rows = conn.execute(
                    "SELECT faction_id, data_json FROM factions WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                for r in rows:
                    data_json = _parse_required_json(r["data_json"], {})
                    _ensure("factions", r["faction_id"], {"data_json": data_json if isinstance(data_json, dict) else {}})

                # world_rules
                rows = conn.execute(
                    "SELECT world_rule_id, data_json FROM world_rules WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                for r in rows:
                    data_json = _parse_required_json(r["data_json"], {})
                    _ensure("world_rules", r["world_rule_id"], {"data_json": data_json if isinstance(data_json, dict) else {}})

                # relationships
                rows = conn.execute(
                    """
                    SELECT relationship_id, from_character_id, to_character_id,
                           relation_type, state_json
                    FROM relationships
                    WHERE project_id = ?
                    """,
                    (project_id,),
                ).fetchall()
                for r in rows:
                    state_json = _parse_required_json(r["state_json"], {})
                    _ensure(
                        "relationships",
                        r["relationship_id"],
                        {
                            "state_json": state_json if isinstance(state_json, dict) else {},
                            "from_id": r["from_character_id"],
                            "to_id": r["to_character_id"],
                            "rel_type": r["relation_type"],
                        },
                    )

                # hooks
                rows = conn.execute(
                    "SELECT hook_id, status FROM hooks WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                for r in rows:
                    _ensure("hooks", r["hook_id"], {"status": r["status"]})

                # debts
                rows = conn.execute(
                    "SELECT debt_id, status, severity FROM narrative_debts WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                for r in rows:
                    _ensure("debts", r["debt_id"], {"status": r["status"], "severity": r["severity"]})

                # events
                rows = conn.execute(
                    "SELECT event_id FROM plot_events WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                for r in rows:
                    _ensure("events", r["event_id"], {})
            finally:
                conn.close()

    return index


def _repair_character_changes(
    items: list[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repaired: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            repaired.append(item)
            continue
        op = item.get("op")
        cid = item.get("character_id")
        target_id = cid
        facet = item.get("facet")
        field = item.get("field")
        entity = index["characters"].get(cid) if isinstance(cid, str) and cid else None

        if op == "update" and item.get("before") is None and entity is not None:
            key = _field_key(field)
            current: Any = None
            if facet == "state":
                current_state = entity.get("current_state") or {}
                current = current_state.get(key)
            elif facet == "definition":
                core_json = entity.get("core_json") or {}
                current = core_json.get(key)
            if current is not None:
                item = {**item, "before": copy.deepcopy(current)}
                repairs.append(
                    {
                        "array": "character_changes",
                        "index": idx,
                        "rule": "fill-before",
                        "target_id": target_id,
                        "facet": facet,
                        "field": field,
                    }
                )
            elif item.get("after") is not None and facet in ("state", "definition"):
                # 实体存在但字段无现值（新增子键）：before=None 是诚实旧值，
                # 但校验器不允许 update+before=None → 转为字段级 add（validator 接受）。
                item = {**item, "op": "add"}
                repairs.append(
                    {
                        "array": "character_changes",
                        "index": idx,
                        "rule": "update-to-add-field",
                        "target_id": target_id,
                        "facet": facet,
                        "field": field,
                    }
                )

        elif op == "add" and entity is not None:
            key = _field_key(field)
            current: Any = None
            if facet == "state":
                current_state = entity.get("current_state") or {}
                current = current_state.get(key)
            elif facet == "definition":
                core_json = entity.get("core_json") or {}
                current = core_json.get(key)
            after = item.get("after")
            if current is not None and _deep_equal(after, current):
                repairs.append(
                    {
                        "array": "character_changes",
                        "index": idx,
                        "rule": "drop-duplicate",
                        "target_id": target_id,
                        "facet": facet,
                        "field": field,
                    }
                )
                continue
            # 实体存在且字段已有现值：降级为 update 并填 before。
            # 实体存在但字段无现值：保持 add（字段级新增），降级为 update 会导致
            # before=None 触发校验失败；对 character/world 的 add 已存在 id 走写透的
            # UPDATE/INSERT state_version 路径，无 UNIQUE 风险。
            if current is not None:
                new_item = {**item, "op": "update", "before": copy.deepcopy(current)}
                repairs.append(
                    {
                        "array": "character_changes",
                        "index": idx,
                        "rule": "insert-to-update",
                        "target_id": target_id,
                        "facet": facet,
                        "field": field,
                    }
                )
                item = new_item

        repaired.append(item)
    return repaired, repairs


def _repair_world_changes(
    items: list[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repaired: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    kind_bucket = {
        "location": "locations",
        "faction": "factions",
        "rule": "world_rules",
    }
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            repaired.append(item)
            continue
        op = item.get("op")
        wid = item.get("world_id")
        kind = item.get("world_kind")
        target_id = wid
        field = item.get("field")
        bucket = kind_bucket.get(kind)
        entity = None
        if bucket and isinstance(wid, str) and wid:
            entity = index[bucket].get(wid)

        if op == "update" and item.get("before") is None and entity is not None:
            key = _field_key(field)
            data_json = entity.get("data_json") or {}
            current = data_json.get(key)
            if current is not None:
                item = {**item, "before": copy.deepcopy(current)}
                repairs.append(
                    {
                        "array": "world_changes",
                        "index": idx,
                        "rule": "fill-before",
                        "target_id": target_id,
                        "world_kind": kind,
                        "field": field,
                    }
                )
            elif item.get("after") is not None:
                # 实体存在但字段无现值（新增 data_json 子键）：before=None 是诚实旧值，
                # 但校验器不允许 update+before=None → 转为字段级 add（validator 接受，
                # write_through 对已存在 id 的 add 走 UPDATE，安全）。
                item = {**item, "op": "add"}
                repairs.append(
                    {
                        "array": "world_changes",
                        "index": idx,
                        "rule": "update-to-add-field",
                        "target_id": target_id,
                        "world_kind": kind,
                        "field": field,
                    }
                )

        elif op == "add" and entity is not None:
            key = _field_key(field)
            data_json = entity.get("data_json") or {}
            current = data_json.get(key)
            after = item.get("after")
            if current is not None and _deep_equal(after, current):
                repairs.append(
                    {
                        "array": "world_changes",
                        "index": idx,
                        "rule": "drop-duplicate",
                        "target_id": target_id,
                        "world_kind": kind,
                        "field": field,
                    }
                )
                continue
            # 字段有现值才降级为 update；字段无现值保持 add（写透对已存在 world_id
            # 的 add 会走 UPDATE 路径，不会触发 UNIQUE）。
            if current is not None:
                new_item = {**item, "op": "update", "before": copy.deepcopy(current)}
                repairs.append(
                    {
                        "array": "world_changes",
                        "index": idx,
                        "rule": "insert-to-update",
                        "target_id": target_id,
                        "world_kind": kind,
                        "field": field,
                    }
                )
                item = new_item

        repaired.append(item)
    return repaired, repairs


def _repair_relationship_changes(
    items: list[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repaired: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            repaired.append(item)
            continue
        op = item.get("op")
        rid = item.get("target_id")
        target_id = rid
        entity = index["relationships"].get(rid) if isinstance(rid, str) and rid else None

        if op == "update" and item.get("before") is None and entity is not None:
            current = entity.get("state_json") or {}
            item = {**item, "before": copy.deepcopy(current)}
            repairs.append(
                {
                    "array": "relationship_changes",
                    "index": idx,
                    "rule": "fill-before",
                    "target_id": target_id,
                }
            )

        elif op == "add" and entity is not None:
            current = entity.get("state_json") or {}
            after = item.get("after") or {}
            if _deep_equal(after, current):
                repairs.append(
                    {
                        "array": "relationship_changes",
                        "index": idx,
                        "rule": "drop-duplicate",
                        "target_id": target_id,
                    }
                )
                continue
            new_item = {**item, "op": "update", "before": copy.deepcopy(current)}
            repairs.append(
                {
                    "array": "relationship_changes",
                    "index": idx,
                    "rule": "insert-to-update",
                    "target_id": target_id,
                }
            )
            item = new_item

        repaired.append(item)
    return repaired, repairs


def _repair_debt_changes(
    items: list[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """debt_changes 无 ``before``/``after`` 字段，用 ``status_before`` / ``severity_before`` 语义兜底。"""
    repaired: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            repaired.append(item)
            continue
        op = item.get("op")
        did = item.get("debt_id")
        target_id = did
        entity = index["debts"].get(did) if isinstance(did, str) and did else None

        if op == "update" and entity is not None:
            new_item = dict(item)
            filled = False
            if item.get("status_before") is None:
                current_status = entity.get("status")
                if current_status is not None:
                    new_item["status_before"] = current_status
                    repairs.append(
                        {
                            "array": "debt_changes",
                            "index": idx,
                            "rule": "fill-before",
                            "target_id": target_id,
                            "field": "status_before",
                        }
                    )
                    filled = True
            if item.get("severity_before") is None:
                current_severity = entity.get("severity")
                if current_severity is not None:
                    new_item["severity_before"] = current_severity
                    repairs.append(
                        {
                            "array": "debt_changes",
                            "index": idx,
                            "rule": "fill-before",
                            "target_id": target_id,
                            "field": "severity_before",
                        }
                    )
                    filled = True
            if filled:
                item = new_item

        repaired.append(item)
    return repaired, repairs


def _repair_resolved_hooks(
    items: list[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """resolved_hooks 的 ``from_status`` 语义等价于 before；schema 允许 null（沿用现状）。"""
    repaired: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            repaired.append(item)
            continue
        hid = item.get("hook_id")
        target_id = hid
        entity = index["hooks"].get(hid) if isinstance(hid, str) and hid else None

        if item.get("from_status") is None and entity is not None:
            current_status = entity.get("status")
            if current_status is not None:
                item = {**item, "from_status": current_status}
                repairs.append(
                    {
                        "array": "resolved_hooks",
                        "index": idx,
                        "rule": "fill-before",
                        "target_id": target_id,
                        "field": "from_status",
                    }
                )

        repaired.append(item)
    return repaired, repairs


def _repair_new_events(
    items: list[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """new_events 语义固定为 add；id 已存在时只能丢弃（不能降级为 update）。"""
    repaired: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        eid = item.get("event_id") or item.get("target_id")
        if isinstance(eid, str) and eid and eid in index["events"]:
            repairs.append(
                {
                    "array": "new_events",
                    "index": idx,
                    "rule": "drop-duplicate",
                    "target_id": eid,
                }
            )
            continue
        repaired.append(item)
    return repaired, repairs


def _repair_new_hooks(
    items: list[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """new_hooks 语义固定为 add；id 已存在时只能丢弃。"""
    repaired: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        hid = item.get("hook_id") or item.get("target_id")
        if isinstance(hid, str) and hid and hid in index["hooks"]:
            repairs.append(
                {
                    "array": "new_hooks",
                    "index": idx,
                    "rule": "drop-duplicate",
                    "target_id": hid,
                }
            )
            continue
        repaired.append(item)
    return repaired, repairs


def repair_delta(
    delta: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
    db_path: str | os.PathLike | Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """对 delta 做确定性自动修复。

    参数：
        delta：待修复 delta（完整 schema 形态，含 10 元信息字段 + 7 数组）。
        snapshot：可选，observer 看到的 previous_state 快照；优先使用。
        db_path：可选，当 snapshot 缺失/不足时从 DB 查当前实体做兜底。

    返回：
        ``(repaired_delta, repairs)``。``repairs`` 为修复记录列表，每条至少含
        ``array`` / ``index`` / ``rule`` / ``target_id``。

    说明：
    - 不修改输入 ``delta``，返回新 dict。
    - 修复不了的不动，继续走 ``validate_delta``。
    - 本函数只做只读查询（db_path 提供时），无写操作。
    """
    if not isinstance(delta, dict):
        return delta, []

    chapter_id = delta.get("chapter_id")
    if not isinstance(chapter_id, str) or not chapter_id:
        # 缺少 chapter_id 无法建立实体索引；直接返回原 delta
        return copy.deepcopy(delta), []

    index = _build_entity_index(snapshot, db_path, chapter_id)

    repaired = copy.deepcopy(delta)
    all_repairs: list[dict[str, Any]] = []

    char_items, char_repairs = _repair_character_changes(
        list(repaired.get("character_changes") or []), index
    )
    repaired["character_changes"] = char_items
    all_repairs.extend(char_repairs)

    world_items, world_repairs = _repair_world_changes(
        list(repaired.get("world_changes") or []), index
    )
    repaired["world_changes"] = world_items
    all_repairs.extend(world_repairs)

    rel_items, rel_repairs = _repair_relationship_changes(
        list(repaired.get("relationship_changes") or []), index
    )
    repaired["relationship_changes"] = rel_items
    all_repairs.extend(rel_repairs)

    debt_items, debt_repairs = _repair_debt_changes(
        list(repaired.get("debt_changes") or []), index
    )
    repaired["debt_changes"] = debt_items
    all_repairs.extend(debt_repairs)

    rh_items, rh_repairs = _repair_resolved_hooks(
        list(repaired.get("resolved_hooks") or []), index
    )
    repaired["resolved_hooks"] = rh_items
    all_repairs.extend(rh_repairs)

    ne_items, ne_repairs = _repair_new_events(
        list(repaired.get("new_events") or []), index
    )
    repaired["new_events"] = ne_items
    all_repairs.extend(ne_repairs)

    nh_items, nh_repairs = _repair_new_hooks(
        list(repaired.get("new_hooks") or []), index
    )
    repaired["new_hooks"] = nh_items
    all_repairs.extend(nh_repairs)

    return repaired, all_repairs


__all__ = ["repair_delta"]
