"""Story State 写透（write-through）模块（Sprint 2 + Sprint 7 + V2.0 Wave B）。

职责（god-object 拆分后）：
- ``write_through``——把 delta 的 7 数组写透到对应领域表（character / world /
  relationship / new_events / new_hooks / resolved_hooks / debt_changes）。
- ``apply_inverse_cleanup_to_state``——rollback 路径下 mutate state（in place）：
  剔除 ``recent_events`` 中出现在 ``remove_event_ids`` 的 event_id；剔除
  ``events`` 中相同 key；剔除 ``hooks`` 中 ``hook_id`` 在 ``remove_hook_ids``
  里的元素。必须在 ``commit_delta`` 同一事务内调用。
- ``encode_who_knows`` / ``read_who_knows`` / ``read_visibility``——
  who_knows 三态语义编码（对齐 knowledge-permission-v0.md §6）：
  - ``None`` → ``NULL``（沿用实体现状，不参与合并）
  - ``[]`` → ``'[]'``（显式置空）
  - 非空 list → JSON（``ensure_ascii=False``）

设计要点：
- ``write_through`` 是事务内调用（``conn`` 由调用方提供），所有 INSERT/UPDATE
  与 ``commit_delta`` 同一连接同一事务。
- 拆分后与原 ``service._write_through`` **逐字节相同** 的 SQL 语义；仅文件
  位置变更，公开行为 0 变化。
- ``skip_new_events_hooks`` 与 ``skip_all`` 两个开关保留：Sprint 7 修订后
  分支 commit 路径用 ``skip_all=True`` 整体跳过领域表写透（防污染 main）。
- V2.0 Wave B 双写面统一：本模块所有 who_knows / JSON 列写入调用
  ``write_helpers`` 共享助手——保持与 ``packages.domain.*.service`` 一致；
  旧版 ``_dump`` / ``encode_who_knows`` 保留为内部 alias（向下兼容，不外露）。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from packages.core.ids import new_id, now_iso


_logger = logging.getLogger(__name__)

from .snapshots import _parse_required_json
from .write_helpers import (
    dump_json as _dump_json,
)
from .write_helpers import (
    dump_json_or_null as _dump_json_or_null,
)
from .write_helpers import (
    encode_who_knows as _encode_who_knows_shared,
)


def _dump(value: Any) -> str:
    """本模块专用 JSON 序列化（ensure_ascii=False，保持中文可读）。

    V2.0 Wave B 起：转发到 ``write_helpers.dump_json`` 共享实现；保留本名字
    仅供模块内 SQL 片段使用，不外露。
    """
    return _dump_json(value)


def _extract_timeline_day(time: dict | None) -> int | None:
    """从 ``new_events[].time`` 提取 ``timeline_day``，口径对齐 ``PlotService._extract_timeline_day``。

    - time 缺失 / 非 dict / ``timeline_day`` 缺失 → None（不插 timeline 行）。
    - 非 int / 负数 → None（同 PlotService 的静默失败语义：宁可少插也不报错，
      与 commit 写透的「不应阻断 commit」目标一致）。
    """
    if not isinstance(time, dict):
        return None
    td = time.get("timeline_day")
    if td is None:
        return None
    if not isinstance(td, int):
        return None
    if td < 0:
        return None
    return td


def _dump_or_null(value: list | dict | None) -> str | None:
    """``None`` → ``NULL``；list/dict → JSON。

    V2.0 Wave B 起：转发到 ``write_helpers.dump_json_or_null``。
    """
    return _dump_json_or_null(value)


def encode_who_knows(value: list | None) -> str | None:
    """三态语义编码（对齐 knowledge-permission-v0.md §6 / state-delta-v0.md §2.6）：
    - ``None`` → ``NULL``（沿用实体现状，不参与合并）
    - ``[]`` → ``'[]'``（显式置空）
    - 非空 list → JSON（``ensure_ascii=False``）

    V2.0 Wave B 起：本函数是 ``write_helpers.encode_who_knows`` 的 re-export
    （façade 兼容——历史代码 / 测试仍可 ``from write_through import encode_who_knows``）。
    canon（write_through）与 domain（character/world/ledger/plot）统一调用
    ``write_helpers`` 唯一权威实现。
    """
    return _encode_who_knows_shared(value)


def read_who_knows(change: dict) -> list | None:
    """从 change 条目读 who_knows；缺失视为 None（沿用）。"""
    return change.get("who_knows")


def read_visibility(change: dict, default: str | None = None) -> str | None:
    """从 change 条目读 visibility；缺失 = 沿用（None）。"""
    return change.get("visibility") or default


def apply_inverse_cleanup_to_state(state: dict, cleanup: dict) -> None:
    """Rollback 路径下 mutate ``state``（in place）：
    - 剔除 ``recent_events`` 中出现在 ``remove_event_ids`` 的 event_id；
    - 剔除 ``events`` 中相同 key；
    - 剔除 ``hooks`` 中 ``hook_id`` 在 ``remove_hook_ids`` 里的元素；
    - 剔除 ``world.locations`` / ``world.factions`` / ``world.world_rules`` 中
      对应 id 的条目（按 ``remove_location_ids`` / ``remove_faction_ids`` /
      ``remove_world_rule_ids``）；
    - 剔除 ``characters[*].relationships`` 中 ``(from,to,type)`` 匹配
      ``remove_relationship_keys`` 的条目；
    - 剔除 ``debts`` 中 ``debt_id`` 在 ``remove_debt_ids`` 的条目；
    - 恢复 ``restore_*`` hints 中 world/character/relationship/debt 的 before 值
      （update 逆 update 路径）。

    必须在 ``commit_delta`` 同一事务内调用（在 materialize_snapshot 之前），这样落盘
    的 story_states 即「回滚后」语义；rollback 后 GET state 直接拿到该快照，无需 post-facto
    修改。

    注：character facet=state 不在逆清理覆盖范围（character_states 表是 append-only
    版本化设计，rollback 不撤销历史 state 版本——见 ``build_inverse_delta`` 注释）。
    """
    remove_event_ids = set(cleanup.get("remove_event_ids") or [])
    remove_hook_ids = set(cleanup.get("remove_hook_ids") or [])
    if remove_event_ids:
        recent = state.get("recent_events") or []
        if isinstance(recent, list):
            state["recent_events"] = [eid for eid in recent if eid not in remove_event_ids]
        events = state.get("events") or {}
        if isinstance(events, dict):
            for eid in list(events.keys()):
                if eid in remove_event_ids:
                    del events[eid]
            state["events"] = events
    if remove_hook_ids:
        hooks = state.get("hooks") or []
        if isinstance(hooks, list):
            state["hooks"] = [h for h in hooks if not (isinstance(h, dict) and h.get("hook_id") in remove_hook_ids)]

    # world.locations / factions（dict 形态，按 world_id 删 key）
    world = state.get("world") or {}
    if isinstance(world, dict):
        for k, snapshot_key in (
            ("remove_location_ids", "locations"),
            ("remove_faction_ids", "factions"),
        ):
            rm = set(cleanup.get(k) or [])
            if rm and isinstance(world.get(snapshot_key), dict):
                bucket = world[snapshot_key]
                for wid in list(bucket.keys()):
                    if wid in rm:
                        del bucket[wid]
        # world.world_rules（list 形态，按 world_rule_id 过滤）
        rm_rules = set(cleanup.get("remove_world_rule_ids") or [])
        if rm_rules and isinstance(world.get("world_rules"), list):
            world["world_rules"] = [
                r for r in world["world_rules"]
                if not (isinstance(r, dict) and r.get("world_rule_id") in rm_rules)
            ]
        # 逆 update → 恢复 before 状态（locations/factions）
        # 修复 wfr_3cb2182a30f6：hint 形状改为字段级 {world_id, field, value}，
        # 此处按字段级写入（field 取末段为键）；entry 非 dict 或 field 为空时
        # 跳过并 log warning（旧形状 {"world_id", "before"} 不再兼容；如发现
        # 残留旧形状，识别并丢弃避免再次污染快照）。
        for hint_key, snapshot_key in (
            ("restore_location_states", "locations"),
            ("restore_faction_states", "factions"),
        ):
            restores = cleanup.get(hint_key) or []
            if restores and isinstance(world.get(snapshot_key), dict):
                bucket = world[snapshot_key]
                for entry in restores:
                    if not isinstance(entry, dict):
                        _logger.warning(
                            "rollback cleanup: %s 条目非 dict（%r），跳过", hint_key, entry
                        )
                        continue
                    wid = entry.get("world_id")
                    field = entry.get("field") or ""
                    value = entry.get("value")
                    # 旧形状（仅 {world_id, before}）识别：显式丢弃以免污染
                    if "field" not in entry and "value" not in entry and "before" in entry:
                        _logger.warning(
                            "rollback cleanup: %s 收到旧形状 hint（world_id=%r），丢弃",
                            hint_key, wid,
                        )
                        continue
                    if not isinstance(wid, str) or wid not in bucket:
                        continue
                    target = bucket[wid]
                    if not isinstance(target, dict):
                        _logger.warning(
                            "rollback cleanup: %s bucket[%r] 非 dict（实际=%s），跳过字段恢复",
                            hint_key, wid, type(target).__name__,
                        )
                        continue
                    if not field:
                        _logger.warning(
                            "rollback cleanup: %s field 为空（world_id=%r），跳过",
                            hint_key, wid,
                        )
                        continue
                    # 字段级恢复：field 取末段为键写入 value（与 applier._set_top_level 对齐）
                    key = field.split(".")[-1]
                    # data_json.<key> 路径：进入子 dict
                    if field.startswith("data_json.") and field.count(".") == 1 and field.split(".", 1)[1]:
                        sub_key = field.split(".", 1)[1]
                        dj = target.get("data_json")
                        if not isinstance(dj, dict):
                            dj = {}
                            target["data_json"] = dj
                        dj[sub_key] = value
                    else:
                        target[key] = value
        # 逆 update → 恢复 world_rules（list 形态）
        # 修复 wfr_3cb2182a30f6：field 非空 → 该项[field 末段]=value（保留 world_rule_id）；
        # field 为空且 value 是 dict → 逐键 merge（不丢 world_rule_id）；其他跳过。
        restore_rules = cleanup.get("restore_world_rule_states") or []
        if restore_rules and isinstance(world.get("world_rules"), list):
            rule_index = {
                r.get("world_rule_id"): i
                for i, r in enumerate(world["world_rules"])
                if isinstance(r, dict)
            }
            for entry in restore_rules:
                if not isinstance(entry, dict):
                    _logger.warning(
                        "rollback cleanup: restore_world_rule_states 条目非 dict（%r），跳过",
                        entry,
                    )
                    continue
                rid = entry.get("world_id")
                idx = rule_index.get(rid)
                if idx is None:
                    continue
                rule = world["world_rules"][idx]
                if not isinstance(rule, dict):
                    _logger.warning(
                        "rollback cleanup: world_rules[%r] 非 dict（实际=%s），跳过",
                        rid, type(rule).__name__,
                    )
                    continue
                field = entry.get("field") or ""
                value = entry.get("value")
                if "field" not in entry and "value" not in entry and "before" in entry:
                    _logger.warning(
                        "rollback cleanup: restore_world_rule_states 收到旧形状 hint（world_rule_id=%r），丢弃",
                        rid,
                    )
                    continue
                if field:
                    key = field.split(".")[-1]
                    if field.startswith("data_json.") and field.count(".") == 1 and field.split(".", 1)[1]:
                        sub_key = field.split(".", 1)[1]
                        dj = rule.get("data_json")
                        if not isinstance(dj, dict):
                            dj = {}
                            rule["data_json"] = dj
                        dj[sub_key] = value
                    else:
                        rule[key] = value
                elif isinstance(value, dict):
                    # 字段级 value 缺失 + value 是 dict → 逐键 merge（保留 world_rule_id）
                    for k, v in value.items():
                        if k == "world_rule_id":
                            # 防御：不允许 hint 覆盖实体的世界规则 id
                            continue
                        rule[k] = v
                else:
                    _logger.warning(
                        "rollback cleanup: restore_world_rule_states field 为空且 value 非 dict（world_rule_id=%r），跳过",
                        rid,
                    )

    # characters[*].relationships：按 (from,to,type) 删；逆 update 恢复 before
    rm_rel_keys = set(
        tuple(k) for k in (cleanup.get("remove_relationship_keys") or []) if isinstance(k, (list, tuple))
    )
    if rm_rel_keys or cleanup.get("restore_relationship_states"):
        characters = state.get("characters") or []
        if isinstance(characters, list):
            for char in characters:
                if not isinstance(char, dict):
                    continue
                rels = char.get("relationships")
                if not isinstance(rels, list):
                    continue
                if rm_rel_keys:
                    char["relationships"] = [
                        r for r in rels
                        if not (
                            isinstance(r, dict)
                            and (
                                r.get("from_character_id"),
                                r.get("to_character_id"),
                                r.get("relation_type"),
                            ) in rm_rel_keys
                        )
                    ]
                # 逆 update：单角色侧 relationship 整段替换为 before
                restore_rels = cleanup.get("restore_relationship_states") or []
                for entry in restore_rels:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("from_character_id") != char.get("character_id"):
                        continue
                    key = (
                        entry.get("from_character_id"),
                        entry.get("to_character_id"),
                        entry.get("relation_type"),
                    )
                    if key in rm_rel_keys:
                        # 该 (from,to,type) 整段已在 rm_rel_keys 删过；不再覆盖
                        continue
                    # 查找同 (from,to,type) 条目，整段替换为 before
                    for r in char["relationships"]:
                        if isinstance(r, dict) and (
                            r.get("from_character_id"),
                            r.get("to_character_id"),
                            r.get("relation_type"),
                        ) == key:
                            r["state_json"] = entry.get("before")
                            break

    # debts（list 形态）：按 debt_id 删
    rm_debts = set(cleanup.get("remove_debt_ids") or [])
    if rm_debts and isinstance(state.get("debts"), list):
        state["debts"] = [
            d for d in state["debts"]
            if not (isinstance(d, dict) and d.get("debt_id") in rm_debts)
        ]
    # 逆 update：恢复 before（severity/status）
    restore_debts = cleanup.get("restore_debt_states") or []
    if restore_debts and isinstance(state.get("debts"), list):
        by_id = {
            d.get("debt_id"): d
            for d in state["debts"]
            if isinstance(d, dict)
        }
        for entry in restore_debts:
            if not isinstance(entry, dict):
                continue
            did = entry.get("debt_id")
            target = by_id.get(did)
            if target is None:
                continue
            before = entry.get("before") or {}
            if "status" in before:
                target["status"] = before["status"]
            if "severity" in before:
                target["severity"] = before["severity"]


def write_through(
    conn,
    project_id: str,
    delta: dict,
    new_version: int,
    *,
    skip_new_events_hooks: bool = False,
    skip_all: bool = False,
) -> None:
    """把 delta 的 7 数组写透到对应领域表。

    ``skip_new_events_hooks``（Sprint 7 兼容）：仅跳过 ``new_events`` /
    ``new_hooks`` 写主表（plot_events / hooks）。

    ``skip_all``（Sprint 7 修订，Sprint 7 审查 P0 修复引入）：
    分支 commit 路径下为 True，**整体跳过**领域表写透（含 character /
    world / relationship / debt / new_events / new_hooks / resolved_hooks）。
    分支路径不应污染 main 领域表——分支的所有 7 数组副作用由 promote 时
    按序重放在 main commit 路径下统一写入。
    优先级：``skip_all=True`` 时跳过整个方法体；
    ``skip_all=False`` 时再按 ``skip_new_events_hooks`` 决定是否写
    ``new_events`` / ``new_hooks``。
    """
    if skip_all:
        # 分支 commit：领域表写透不在此路径执行；副作用由 promote 按序重放
        # 在 main commit 时统一落库（commit_delta 走 main 路径时不传
        # skip_all）。
        return
    chapter_id = delta["chapter_id"]

    # character_changes
    for ch in delta.get("character_changes") or []:
        cid = ch.get("character_id")
        op = ch.get("op")
        facet = ch.get("facet")
        field = ch.get("field") or ""
        after = ch.get("after")
        who_knows_enc = encode_who_knows(read_who_knows(ch))
        vis_value = read_visibility(ch)
        if facet == "state":
            # P2-2: 若该角色无任何 state 行，先补 v1 行（空 state_json）再追加
            # 否则后续引用 max(state_version)+1 直接落到 (cid, 2) 跳过了 v1。
            seed_row = conn.execute(
                "SELECT 1 FROM character_states WHERE character_id = ? LIMIT 1",
                (cid,),
            ).fetchone()
            if seed_row is None:
                conn.execute(
                    """
                    INSERT INTO character_states
                        (character_id, state_version, state_json, visibility, who_knows, created_at)
                    VALUES (?, 1, '{}', 'VISIBLE', NULL, ?)
                    """,
                    (cid, now_iso()),
                )
            # 找到 max(state_version)
            row = conn.execute(
                "SELECT MAX(state_version) AS v FROM character_states WHERE character_id = ?",
                (cid,),
            ).fetchone()
            next_v = (row["v"] or 0) + 1
            # 复制当前 state_json 作为基础（避免覆盖其他字段）
            cur_row = conn.execute(
                "SELECT state_json FROM character_states "
                "WHERE character_id = ? ORDER BY state_version DESC LIMIT 1",
                (cid,),
            ).fetchone()
            base = _parse_required_json(cur_row["state_json"], {}) if cur_row else {}
            if not isinstance(base, dict):
                base = {}
            key = field.split(".")[-1] if "." in field else field
            if op in ("add", "update"):
                base[key] = after
            elif op == "remove":
                base.pop(key, None)
            # visibility：None → 沿用 VISIBLE；显式值 → 使用
            vis_final = vis_value or "VISIBLE"
            conn.execute(
                """
                INSERT INTO character_states
                    (character_id, state_version, state_json, visibility, who_knows, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (cid, next_v, _dump(base), vis_final, who_knows_enc, now_iso()),
            )
        elif facet == "definition":
            # UPDATE characters.core_json 顶层 key 替换
            cur = conn.execute("SELECT core_json FROM characters WHERE character_id = ?", (cid,)).fetchone()
            if cur is None:
                continue
            base = _parse_required_json(cur["core_json"], {}) or {}
            if not isinstance(base, dict):
                base = {}
            key = field.split(".")[-1] if "." in field else field
            if op in ("add", "update"):
                base[key] = after
            elif op == "remove":
                base.pop(key, None)
            # who_knows：缺失=沿用（不 UPDATE 该列）；非 None=显式覆盖
            if who_knows_enc is not None:
                conn.execute(
                    """
                    UPDATE characters SET core_json = ?, who_knows = ?, updated_at = ?
                    WHERE character_id = ?
                    """,
                    (_dump(base), who_knows_enc, now_iso(), cid),
                )
            else:
                conn.execute(
                    "UPDATE characters SET core_json = ?, updated_at = ? WHERE character_id = ?",
                    (_dump(base), now_iso(), cid),
                )

    # world_changes
    for w in delta.get("world_changes") or []:
        kind = w.get("world_kind")
        op = w.get("op")
        wid = w.get("world_id")
        field = w.get("field") or ""
        after = w.get("after")
        who_knows_enc = encode_who_knows(read_who_knows(w))
        vis_value = read_visibility(w)
        if kind == "location":
            cur = conn.execute(
                "SELECT name, statement, data_json FROM locations WHERE location_id = ?",
                (wid,),
            ).fetchone()
            if cur is None and op == "add":
                base_name = ((after or {}).get("name") if isinstance(after, dict) else None) or wid
                base_stmt = ((after or {}).get("statement") if isinstance(after, dict) else None) or ""
                base_data = ((after or {}).get("data_json") if isinstance(after, dict) else None) or {}
                vis_final = vis_value or "PUBLIC"
                conn.execute(
                    """
                    INSERT INTO locations
                        (location_id, project_id, name, statement, data_json,
                         visibility, who_knows, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (wid, project_id, base_name, base_stmt, _dump(base_data),
                     vis_final, who_knows_enc, now_iso(), now_iso()),
                )
                continue
            if cur is None:
                continue
            base_data = _parse_required_json(cur["data_json"], {}) or {}
            if not isinstance(base_data, dict):
                base_data = {}
            key = field.split(".")[-1] if "." in field else field
            if op in ("add", "update"):
                base_data[key] = after
            elif op == "remove":
                base_data.pop(key, None)
            # who_knows 缺失=沿用（不更新该列）；非 None=显式覆盖
            if who_knows_enc is not None:
                conn.execute(
                    "UPDATE locations SET data_json = ?, who_knows = ?, updated_at = ? WHERE location_id = ?",
                    (_dump(base_data), who_knows_enc, now_iso(), wid),
                )
            else:
                conn.execute(
                    "UPDATE locations SET data_json = ?, updated_at = ? WHERE location_id = ?",
                    (_dump(base_data), now_iso(), wid),
                )
        elif kind == "faction":
            cur = conn.execute(
                "SELECT data_json FROM factions WHERE faction_id = ?", (wid,)
            ).fetchone()
            if cur is None and op == "add":
                base_data = ((after or {}).get("data_json") if isinstance(after, dict) else None) or {}
                base_name = ((after or {}).get("name") if isinstance(after, dict) else None) or wid
                base_stmt = ((after or {}).get("statement") if isinstance(after, dict) else None) or ""
                vis_final = vis_value or "VISIBLE"
                conn.execute(
                    """
                    INSERT INTO factions
                        (faction_id, project_id, name, statement, data_json,
                         visibility, who_knows, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (wid, project_id, base_name, base_stmt, _dump(base_data),
                     vis_final, who_knows_enc, now_iso(), now_iso()),
                )
                continue
            if cur is None:
                continue
            base_data = _parse_required_json(cur["data_json"], {}) or {}
            if not isinstance(base_data, dict):
                base_data = {}
            key = field.split(".")[-1] if "." in field else field
            if op in ("add", "update"):
                base_data[key] = after
            elif op == "remove":
                base_data.pop(key, None)
            if who_knows_enc is not None:
                conn.execute(
                    "UPDATE factions SET data_json = ?, who_knows = ?, updated_at = ? WHERE faction_id = ?",
                    (_dump(base_data), who_knows_enc, now_iso(), wid),
                )
            else:
                conn.execute(
                    "UPDATE factions SET data_json = ?, updated_at = ? WHERE faction_id = ?",
                    (_dump(base_data), now_iso(), wid),
                )
        elif kind == "rule":
            cur = conn.execute(
                "SELECT data_json FROM world_rules WHERE world_rule_id = ?", (wid,)
            ).fetchone()
            if cur is None and op == "add":
                base_data = ((after or {}).get("data_json") if isinstance(after, dict) else None) or {}
                base_name = ((after or {}).get("name") if isinstance(after, dict) else None) or wid
                base_stmt = ((after or {}).get("statement") if isinstance(after, dict) else None) or ""
                vis_final = vis_value or "PUBLIC"
                conn.execute(
                    """
                    INSERT INTO world_rules
                        (world_rule_id, project_id, name, statement, data_json,
                         visibility, who_knows, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (wid, project_id, base_name, base_stmt, _dump(base_data),
                     vis_final, who_knows_enc, now_iso(), now_iso()),
                )
                continue
            if cur is None:
                continue
            base_data = _parse_required_json(cur["data_json"], {}) or {}
            if not isinstance(base_data, dict):
                base_data = {}
            key = field.split(".")[-1] if "." in field else field
            if op in ("add", "update"):
                base_data[key] = after
            elif op == "remove":
                base_data.pop(key, None)
            if who_knows_enc is not None:
                conn.execute(
                    "UPDATE world_rules SET data_json = ?, who_knows = ?, updated_at = ? WHERE world_rule_id = ?",
                    (_dump(base_data), who_knows_enc, now_iso(), wid),
                )
            else:
                conn.execute(
                    "UPDATE world_rules SET data_json = ?, updated_at = ? WHERE world_rule_id = ?",
                    (_dump(base_data), now_iso(), wid),
                )
        elif kind in ("politics", "economy", "event", "time"):
            # 不写领域表（无对应表），仅在快照中体现
            pass

    # relationship_changes
    for rel in delta.get("relationship_changes") or []:
        from_id = rel.get("from_character_id")
        to_id = rel.get("to_character_id")
        rel_type = rel.get("relation_type")
        op = rel.get("op")
        after = rel.get("after")
        # 三态语义：对齐 hooks/debts 既有分支——缺失/None=沿用（不写该列），
        # 非 None=显式覆盖。visibility 缺失默认 'PUBLIC'（与迁移 0014 DDL 默认
        # 值一致；后续 inverse/rollback 阶段 commit 前 latest 行即落入 default）。
        rel_who = encode_who_knows(read_who_knows(rel))
        rel_vis = read_visibility(rel) or "PUBLIC"
        existing = conn.execute(
            """
            SELECT relationship_id FROM relationships
            WHERE from_character_id = ? AND to_character_id = ? AND relation_type = ?
            """,
            (from_id, to_id, rel_type),
        ).fetchone()
        if op in ("add", "update"):
            if existing is None:
                rid = rel.get("target_id") or new_id("rel")
                try:
                    conn.execute(
                        """
                        INSERT INTO relationships
                            (relationship_id, project_id, from_character_id, to_character_id,
                             relation_type, state_json, last_state_version,
                             visibility, who_knows)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rid, project_id, from_id, to_id, rel_type,
                            _dump(after or {}), new_version, rel_vis, rel_who,
                        ),
                    )
                except sqlite3.IntegrityError:
                    # 0017 唯一索引 ``idx_relationships_unique`` 兜底：并发 add 在
                    # SELECT-then-INSERT 窗口内产生冲突 → 转 UPDATE 分支幂等。
                    # 重查现有行（对方事务可能刚提交，existing 行还没在本连接可见）
                    existing = conn.execute(
                        """
                        SELECT relationship_id FROM relationships
                        WHERE project_id = ? AND from_character_id = ?
                          AND to_character_id = ? AND relation_type = ?
                        """,
                        (project_id, from_id, to_id, rel_type),
                    ).fetchone()
                    if existing is None:
                        # 不应发生：唯一索引报错却查不到行 → 让调用方感知
                        raise
                    # 兜底分支同样按三态语义：who_knows 缺失=不更新该列；
                    # visibility 缺失=沿用实体现状。
                    if rel_who is not None:
                        conn.execute(
                            """
                            UPDATE relationships
                            SET state_json = ?, last_state_version = ?,
                                visibility = ?, who_knows = ?
                            WHERE relationship_id = ?
                            """,
                            (
                                _dump(after or {}), new_version, rel_vis, rel_who,
                                existing["relationship_id"],
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE relationships
                            SET state_json = ?, last_state_version = ?, visibility = ?
                            WHERE relationship_id = ?
                            """,
                            (
                                _dump(after or {}), new_version, rel_vis,
                                existing["relationship_id"],
                            ),
                        )
            else:
                # 已存在关系按三态语义 UPDATE：who_knows 缺失=不写该列；
                # visibility 缺失=沿用（与 hooks/debts 同款口径）。
                if rel_who is not None:
                    conn.execute(
                        """
                        UPDATE relationships
                        SET state_json = ?, last_state_version = ?,
                            visibility = ?, who_knows = ?
                        WHERE relationship_id = ?
                        """,
                        (
                            _dump(after or {}), new_version, rel_vis, rel_who,
                            existing["relationship_id"],
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE relationships
                        SET state_json = ?, last_state_version = ?, visibility = ?
                        WHERE relationship_id = ?
                        """,
                        (
                            _dump(after or {}), new_version, rel_vis,
                            existing["relationship_id"],
                        ),
                    )
        elif op == "remove":
            if existing is not None:
                conn.execute("DELETE FROM relationships WHERE relationship_id = ?", (existing["relationship_id"],))

    # new_events
    if not skip_new_events_hooks:
        for ev in delta.get("new_events") or []:
            ev_who = encode_who_knows(read_who_knows(ev))
            ev_vis = read_visibility(ev) or "RESTRICTED"
            # location FK 守卫：observer 输出 location 是自由文本（可能描述性文字或编造
            # id），plot_events.location_id 是外键（→ locations.location_id），直接 INSERT
            # 在 PRAGMA foreign_keys=ON 下会触发 FOREIGN KEY constraint failed。
            # 兜底：locations 表里查不到该值时置 NULL，不阻断提交（与既有的
            # write-through NULL guards 先例一致——见 test_story_state_write_through_null_guard
            # 中 world add 分支的 None 兜底）。原文已通过 effects/notes 可见处保留。
            ev_location = ev.get("location")
            if ev_location:
                loc_row = conn.execute(
                    "SELECT 1 FROM locations WHERE location_id = ?",
                    (ev_location,),
                ).fetchone()
                if loc_row is None:
                    ev_location = None
            conn.execute(
                """
                INSERT INTO plot_events
                    (event_id, project_id, type, cause_json, effects_json, participants_json,
                     location_id, time_json, status, introduced_chapter_id, visibility,
                     who_knows, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'recorded', ?, ?, ?, ?)
                """,
                (
                    ev["event_id"],
                    project_id,
                    ev["type"],
                    _dump(ev.get("cause") or []),
                    _dump(ev.get("effects") or []),
                    _dump(ev.get("participants") or []),
                    ev_location,
                    _dump(ev.get("time") or {"timeline_day": 1}),
                    chapter_id,
                    ev_vis,
                    ev_who,
                    # V3.1 P1-1.1：observer 输出的 ``new_events[].description`` 下沉
                    # 落库（迁移 0013 加 description TEXT 列）；可空——缺失/为 None
                    # 视为「该事件未给出描述」，与 Schema ``description: string|null`` 对齐。
                    ev.get("description"),
                ),
            )

            # V3.1 P1-1.1 修复 B2：commit 写透路径此前只插 plot_events，未同步
            # timeline_events，导致右侧时间线断供（与 ``PlotService.create_event``
            # 既有「time.timeline_day → 同事务插 timeline_events」先例脱节）。
            # 口径对齐 PlotService：
            #   - timeline_day 提取：复用 ``_extract_timeline_day`` 静默失败语义
            #     （非 int / 负数 / None → 不插 timeline 行，与 create_event 同款）。
            #   - id 生成：``tle_`` + 12 位 hex（与 ``_insert_timeline_event`` 同源
            #     ``new_id("tle")`` 口径）。
            #   - 幂等：commit 重放 / 同一 event_id 二次写透不重复插（先 SELECT
            #     event_id 判存）；与迁移 0019 回填口径一致。
            #   - 描述：复用 ``ev.get("description")``（即 plot_events.description 同源），
            #     不重复 observer 字段以免口径漂移。
            ev_time = ev.get("time") or {"timeline_day": 1}
            timeline_day = _extract_timeline_day(ev_time)
            if timeline_day is not None:
                existing_tle = conn.execute(
                    "SELECT 1 FROM timeline_events WHERE event_id = ? LIMIT 1",
                    (ev["event_id"],),
                ).fetchone()
                if existing_tle is None:
                    in_story_date = ev_time.get("in_story_date")
                    time_ref = (
                        str(in_story_date) if in_story_date is not None else None
                    )
                    conn.execute(
                        """
                        INSERT INTO timeline_events
                            (timeline_event_id, project_id, event_id, day_index,
                             time_ref, description, visibility, who_knows)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id("tle"),
                            project_id,
                            ev["event_id"],
                            timeline_day,
                            time_ref,
                            ev.get("description"),
                            ev_vis,
                            ev_who,
                        ),
                    )

    # resolved_hooks
    # 哨兵：notes 含 ``__CLEAR_PAYOFF_CHAPTER__`` → 显式把 hooks.payoff_chapter_id 置 NULL
    # （用于逆 Delta；schema 不允许新字段，只能用 notes 字符串携带标记）。
    # 守卫：ABANDONED 是 hook 终态（HOOK_ALLOWED_NEXT['ABANDONED']={ABANDONED}），
    # 任何 resolved_hooks 回写（包括清空 payoff_chapter_id）都不应触发「复活」或
    # 改变终态——审计 §A1：story_state 数据污染风险。
    for rh in delta.get("resolved_hooks") or []:
        notes = rh.get("notes") or ""
        clear_payoff = "__CLEAR_PAYOFF_CHAPTER__" in notes
        hook_id = rh.get("hook_id")
        existing_row = (
            conn.execute(
                "SELECT status FROM hooks WHERE hook_id = ?",
                (hook_id,),
            ).fetchone()
            if isinstance(hook_id, str) and hook_id
            else None
        )
        current_hook_status = existing_row["status"] if existing_row else None
        if current_hook_status == "ABANDONED":
            _logger.warning(
                "write_through.resolved_hooks: skip ABANDONED hook %s "
                "(to_status=%s, clear_payoff=%s): 终态不可回退",
                hook_id,
                rh.get("to_status"),
                clear_payoff,
            )
            continue
        if clear_payoff:
            conn.execute(
                """
                UPDATE hooks
                SET status = ?, payoff_chapter_id = NULL, updated_at = ?
                WHERE hook_id = ?
                """,
                (rh["to_status"], now_iso(), rh["hook_id"]),
            )
        else:
            conn.execute(
                """
                UPDATE hooks
                SET status = ?, payoff_chapter_id = COALESCE(?, payoff_chapter_id), updated_at = ?
                WHERE hook_id = ?
                """,
                (rh["to_status"], rh.get("payoff_chapter_id") or chapter_id, now_iso(), rh["hook_id"]),
            )

    # new_hooks
    if not skip_new_events_hooks:
        for nh in delta.get("new_hooks") or []:
            nh_who = encode_who_knows(read_who_knows(nh))
            nh_vis = read_visibility(nh) or "RESTRICTED"
            conn.execute(
                """
                INSERT INTO hooks
                    (hook_id, project_id, name, introduced_chapter_id, status, importance,
                     expected_payoff_chapter_id, payoff_chapter_id, visibility, who_knows,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, 'OPEN', ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    nh["hook_id"],
                    project_id,
                    nh["name"],
                    chapter_id,
                    float(nh.get("importance") or 0.5),
                    nh.get("expected_payoff_chapter_id"),
                    nh_vis,
                    nh_who,
                    now_iso(),
                    now_iso(),
                ),
            )

    # debt_changes
    for db in delta.get("debt_changes") or []:
        op = db.get("op")
        did = db.get("debt_id")
        db_who = encode_who_knows(read_who_knows(db))
        db_vis = read_visibility(db) or "RESTRICTED"
        existing = conn.execute("SELECT debt_id FROM narrative_debts WHERE debt_id = ?", (did,)).fetchone()
        if op == "add":
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO narrative_debts
                        (debt_id, project_id, description, created_chapter_id, severity,
                         deadline_chapter_id, status, visibility, who_knows,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        did,
                        project_id,
                        db.get("description") or "",
                        chapter_id,
                        float(db.get("severity_after") or 0.5),
                        db.get("deadline_chapter_id"),
                        db.get("status_after") or "open",
                        db_vis,
                        db_who,
                        now_iso(),
                        now_iso(),
                    ),
                )
        elif op == "update":
            if existing is not None:
                # 3-state who_knows：缺失=不更新该列（沿用），非 None=覆盖
                if db_who is not None:
                    conn.execute(
                        """
                        UPDATE narrative_debts
                        SET severity = COALESCE(?, severity),
                            status = COALESCE(?, status),
                            deadline_chapter_id = COALESCE(?, deadline_chapter_id),
                            who_knows = ?,
                            updated_at = ?
                        WHERE debt_id = ?
                        """,
                        (
                            db.get("severity_after"),
                            db.get("status_after"),
                            db.get("deadline_chapter_id"),
                            db_who,
                            now_iso(),
                            did,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE narrative_debts
                        SET severity = COALESCE(?, severity),
                            status = COALESCE(?, status),
                            deadline_chapter_id = COALESCE(?, deadline_chapter_id),
                            updated_at = ?
                        WHERE debt_id = ?
                        """,
                        (
                            db.get("severity_after"),
                            db.get("status_after"),
                            db.get("deadline_chapter_id"),
                            now_iso(),
                            did,
                        ),
                    )
        elif op == "remove":
            if existing is not None:
                conn.execute("DELETE FROM narrative_debts WHERE debt_id = ?", (did,))


__all__ = [
    "write_through",
    "apply_inverse_cleanup_to_state",
    "encode_who_knows",
    "read_who_knows",
    "read_visibility",
]
