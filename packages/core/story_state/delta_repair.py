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
  4. **id-prefix-reconcile**（F3 修复）：``char_xxx`` / ``fac_xxx`` 前缀的 id
     若在原前缀表里不存在，但**同一 hex 后缀**在对方表里唯一存在 → 改写为正确
     前缀。仅处理 char↔fac 双桶（location / hook / debt 等其它前缀不做）。
     覆盖位置：``character_changes.character_id`` / ``world_changes.world_id``
     （kind=faction 时）/ ``relationship_changes.from_character_id`` /
     ``to_id`` / ``new_events[*].participants[]``。
     零命中 / 多命中 → 不动（让 validator 报错，避免猜测）。
  5. **change-id-uniquify**（F4 修复）：delta 内每条 change 的 ``change_id`` 必须
     在**同一数组内唯一**（七类数组：``character_changes`` / ``world_changes`` /
     ``relationship_changes`` / ``new_events`` / ``resolved_hooks`` / ``new_hooks``
     / ``debt_changes``）。生产事故 wfr_6765f6de4d76 现场：observer 照抄示例
     字面量 ``cc:01HXXXXXXXX`` 到多条 change，validator 以「delta 内 change_id
     重复」拒绝。规则：
     - 占位符检测：change_id 含 ≥ 3 个连续 ``X`` / ``x`` → 视为占位符；
     - 重复检测：change_id 与本数组先前已见 change_id 重复 → 视为重复。
     任一命中 → 重写为 ``<原前缀>:<uuid4 hex 前 12 位>``（原前缀=冒号前部分；
     无冒号则用数组默认前缀），repairs 留痕 ``rule="change_id_uniquify"``，
     含 ``from_id`` / ``to_id`` / ``array`` / ``index``。

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
import re
import uuid
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


def _split_id_prefix(value: Any) -> tuple[str, str] | None:
    """把 'char_xxx' / 'fac_xxx' 切成 (prefix, suffix) 用于前缀调和；非 char_/fac_ 返回 None。"""
    if not isinstance(value, str) or "_" not in value:
        return None
    prefix, _, suffix = value.partition("_")
    if prefix not in ("char", "fac") or not suffix:
        return None
    return prefix, suffix


def _reconcile_id_prefix(
    value: Any,
    index: dict[str, dict[str, Any]],
) -> tuple[Any, dict[str, Any] | None]:
    """字符↔组织 ID 前缀调和（F3 修复）。

    生产事故 wfr_6d77d905b4b1：observer 引用既有实体时把 ``fac_e98a1ca56b66`` 错写为
    ``char_e98a1ca56b66``，validator 在 ``character_changes.character_id`` / 关系端点 /
    事件参与者等位置上以「id 不在 snapshot」为由拒绝，导致 run FAILED。

    规则：对 char/ / fac/ 前缀的 id：
    1. 原前缀表里**已存在** → 不动。
    2. 原前缀表里**不存在**，但**同一 hex 后缀**在对方表（characters↔factions）里
       **唯一存在** → 改写为正确前缀并返回 ``id_prefix_reconcile`` 修复记录。
    3. 原前缀表里不存在且对方表里零命中 / 多命中 → 不动（让 validator 报错，
       避免猜测制造幻觉）。

    仅处理 char_ ↔ fac_ 双桶调和；location / hook / debt 等其它前缀不做。

    返回：``(new_value, repair_record_or_None)``。
    """
    parts = _split_id_prefix(value)
    if parts is None:
        return value, None
    prefix, suffix = parts
    own_bucket = "characters" if prefix == "char" else "factions"
    other_bucket = "factions" if prefix == "char" else "characters"
    other_prefix = "fac" if prefix == "char" else "char"

    # 原前缀表里已存在 → 不动
    if value in index.get(own_bucket, {}):
        return value, None

    # 在对方表里按后缀唯一定位
    other_matches: list[str] = []
    for other_id in index.get(other_bucket, {}).keys():
        if not isinstance(other_id, str):
            continue
        other_parts = _split_id_prefix(other_id)
        if other_parts is None:
            continue
        if other_parts[1] == suffix:
            other_matches.append(other_id)

    if len(other_matches) != 1:
        # 零命中或多命中：让 validator 报错（避免猜测制造幻觉）
        return value, None

    new_value = other_matches[0]
    repair = {
        "rule": "id_prefix_reconcile",
        "from_id": value,
        "to_id": new_value,
        "from_prefix": prefix,
        "to_prefix": other_prefix,
    }
    return new_value, repair


def _apply_id_reconcile(
    item: dict[str, Any],
    field: str,
    index: dict[str, dict[str, Any]],
    *,
    extra_record: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """对 item[field] 做前缀调和；返回 (new_item, repairs)。无变化时 repairs 为空。"""
    if field not in item:
        return item, []
    new_value, repair = _reconcile_id_prefix(item.get(field), index)
    if repair is None:
        return item, []
    new_item = {**item, field: new_value}
    # 配套字段同步：character_id / world_id / hook_id / debt_id / target_id 等别名同源时一起改
    if field == "character_id" and item.get("target_id") == item.get(field):
        new_item["target_id"] = new_value
    elif field == "world_id" and item.get("target_id") == item.get(field):
        new_item["target_id"] = new_value
    elif field == "event_id" and item.get("target_id") == item.get(field):
        new_item["target_id"] = new_value
    elif field == "hook_id" and item.get("target_id") == item.get(field):
        new_item["target_id"] = new_value
    elif field == "debt_id" and item.get("target_id") == item.get(field):
        new_item["target_id"] = new_value
    rec = {"field": field, **repair}
    if extra_record:
        rec.update(extra_record)
    return new_item, [rec]


# 数组 → change_id 默认前缀对照（与 observer-v1.md §6-26 一致）。
_ARRAY_CHANGE_ID_PREFIX: dict[str, str] = {
    "character_changes": "cc",
    "world_changes": "wc",
    "relationship_changes": "rc",
    "new_events": "ev",
    "resolved_hooks": "rh",
    "new_hooks": "nh",
    "debt_changes": "dc",
}

# 占位符模式：含 ≥3 个连续 X / x（如 cc:01HXXXXX / cc:01Hxxx / cc:01HXXXXXXXX）。
_PLACEHOLDER_X_RE = re.compile(r"[Xx]{3,}")


def _is_placeholder_change_id(value: Any) -> bool:
    """判定 change_id 是否为占位符：含 ≥3 个连续 X / x 视为占位符。

    生产事故 wfr_6765f6de4d76：observer 照抄示例字面量 ``cc:01HXXXXXXXX``
    到多条 change。占位符被自动重写是确定性的（信息可唯一确定）。
    """
    if not isinstance(value, str) or not value:
        return False
    return bool(_PLACEHOLDER_X_RE.search(value))


def _normalize_change_id(
    value: Any,
    default_prefix: str,
) -> tuple[str | None, str]:
    """从 change_id 字符串里提取（prefix, suffix）；无冒号则 prefix 视作 default_prefix。

    返回 ``(prefix, suffix)``：
    - 解析成功 → ``(prefix, suffix)``；
    - 非字符串 / 空串 / 无法解析 → ``(None, "")``（调用方应跳过重写）。
    """
    if not isinstance(value, str) or not value:
        return None, ""
    if ":" in value:
        prefix, _, suffix = value.partition(":")
        prefix = prefix.strip()
        suffix = suffix.strip()
        if prefix and suffix:
            return prefix, suffix
    # 无冒号或解析异常：把整串当 suffix，前缀用数组默认前缀
    return default_prefix, value


def _generate_unique_change_id(default_prefix: str, seen: set[str]) -> str:
    """生成 ``<prefix>:<uuid4 hex 前 12 位>`` 并保证不被 ``seen`` 命中。

    极小概率（≈ 2^-48）下 uuid4 前 12 位会与已见冲突，循环重试一次（最多两次）。
    """
    for _ in range(4):
        candidate = f"{default_prefix}:{uuid.uuid4().hex[:12]}"
        if candidate not in seen:
            seen.add(candidate)
            return candidate
    # 极端兜底：追加后缀扰动
    candidate = f"{default_prefix}:{uuid.uuid4().hex[:12]}{uuid.uuid4().hex[:2]}"
    seen.add(candidate)
    return candidate


def _uniquify_change_ids(
    items: list[Any],
    array_name: str,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """F4 修复：对数组内每条 change 的 ``change_id`` 做去重 + 占位符检测。

    规则：
    1. 遍历数组维护 ``seen`` 集合；
    2. 对每条 change：
       - 非字典 / 无 ``change_id`` 字段 → 原样保留（不属于本规则处理范围）；
       - ``change_id`` 含 ≥3 个连续 X / x → 占位符，触发重写；
       - ``change_id`` 已在 ``seen`` → 重复，触发重写；
       - 唯一且非占位符 → 原样保留。
    3. 重写规则：``<原前缀>:<uuid4 hex 前 12 位>``，原前缀=冒号前部分（无冒号则用
       数组默认前缀），写入 ``change_id`` 并把新值加入 ``seen``；同时生成一条
       ``{"rule": "change_id_uniquify", "array": ..., "index": ..., "from_id": ..., "to_id": ...}``
       修复记录。
    """
    if not isinstance(items, list) or not items:
        return items, []
    default_prefix = _ARRAY_CHANGE_ID_PREFIX.get(array_name, "cc")
    seen: set[str] = set()
    repaired: list[Any] = []
    repairs: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict) or "change_id" not in item:
            repaired.append(item)
            continue
        old_value = item.get("change_id")
        if not isinstance(old_value, str) or not old_value:
            repaired.append(item)
            continue
        # 命中占位符 或 已在 seen → 重写
        if _is_placeholder_change_id(old_value) or old_value in seen:
            original_prefix, _ = _normalize_change_id(old_value, default_prefix)
            # 占位符场景下若解析不出有意义的前缀，回退到数组默认前缀
            prefix_for_new = original_prefix or default_prefix
            new_value = _generate_unique_change_id(prefix_for_new, seen)
            new_item = {**item, "change_id": new_value}
            repairs.append(
                {
                    "array": array_name,
                    "index": idx,
                    "rule": "change_id_uniquify",
                    "from_id": old_value,
                    "to_id": new_value,
                }
            )
            repaired.append(new_item)
            continue
        # 唯一且非占位符 → 不动
        seen.add(old_value)
        repaired.append(item)
    return repaired, repairs


def _reconcile_participants(
    participants: Any,
    index: dict[str, dict[str, Any]],
    *,
    extra_record: dict[str, Any] | None = None,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """对 participants 列表逐项做前缀调和；返回 (new_list, repairs)。"""
    if not isinstance(participants, list):
        return participants if isinstance(participants, list) else [], []
    new_list: list[Any] = []
    repairs: list[dict[str, Any]] = []
    for j, p in enumerate(participants):
        new_value, repair = _reconcile_id_prefix(p, index)
        if repair is None:
            new_list.append(p)
            continue
        new_list.append(new_value)
        rec = {"field": f"participants[{j}]", **repair}
        if extra_record:
            rec.update(extra_record)
        repairs.append(rec)
    return new_list, repairs


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

        # F3：先做 character_id ↔ faction_id 前缀调和（op 无关）
        if isinstance(cid, str) and cid:
            new_item, reconcile_repairs = _apply_id_reconcile(
                item, "character_id", index,
                extra_record={"array": "character_changes", "index": idx},
            )
            if reconcile_repairs:
                repairs.extend(reconcile_repairs)
                item = new_item
                cid = item.get("character_id")
                target_id = cid
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

        # F3：faction 前缀调和（仅 faction kind；location/rule 不做 char↔fac 调和）
        if kind == "faction" and isinstance(wid, str) and wid:
            new_item, reconcile_repairs = _apply_id_reconcile(
                item, "world_id", index,
                extra_record={"array": "world_changes", "index": idx},
            )
            if reconcile_repairs:
                repairs.extend(reconcile_repairs)
                item = new_item
                wid = item.get("world_id")
                target_id = wid
                entity = index["factions"].get(wid) if isinstance(wid, str) and wid else None

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

        # F3：关系端点 from/to 是 char_/fac_ 前缀时做前缀调和（端点身份透明）
        for ep_field in ("from_character_id", "to_character_id"):
            ep_value = item.get(ep_field)
            if not isinstance(ep_value, str) or not ep_value:
                continue
            new_item, reconcile_repairs = _apply_id_reconcile(
                item, ep_field, index,
                extra_record={"array": "relationship_changes", "index": idx},
            )
            if reconcile_repairs:
                repairs.extend(reconcile_repairs)
                item = new_item

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
            # 守卫：ABANDONED 是 hook 终态，fill-before 不补，避免把
            # ABANDONED 钩子的 to_status=RESOLVED 抹平为合法迁移——
            # 显式让 validator 在 ABANDONED→非ABANDONED 上抛错，保留语义。
            if current_status is not None and current_status != "ABANDONED":
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

        # F3：participants 列表逐项做 char↔fac 前缀调和（event_id 本身是新增 id，不调和）
        if "participants" in item:
            new_parts, part_repairs = _reconcile_participants(
                item.get("participants"), index,
                extra_record={"array": "new_events", "index": idx},
            )
            if part_repairs:
                repairs.extend(part_repairs)
                item = {**item, "participants": new_parts}

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

    # F4 修复：delta 内 change_id 唯一性 + 占位符检测（change_id_uniquify）。
    # 在所有 _repair_* 之后统一跑一次，按数组独立维护 seen 集合；其它 _repair_*
    # 可能因 drop-duplicate / insert-to-update 删 / 改 change，但不会影响本规则
    # （占位符形态与数组内唯一性是 change_id 自身属性，与 target_id 等无关）。
    for arr_name in (
        "character_changes",
        "world_changes",
        "relationship_changes",
        "new_events",
        "resolved_hooks",
        "new_hooks",
        "debt_changes",
    ):
        uni_items, uni_repairs = _uniquify_change_ids(
            list(repaired.get(arr_name) or []), arr_name,
        )
        repaired[arr_name] = uni_items
        all_repairs.extend(uni_repairs)

    return repaired, all_repairs


__all__ = ["repair_delta"]
