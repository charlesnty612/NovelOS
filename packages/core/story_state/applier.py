"""Delta 应用器（Sprint 2）。

职责：
- :func:`apply_delta(state: dict, delta: dict) -> dict`：纯函数，把 Delta 的 7 个数组
  依次应用到 ``state``（深拷贝），返回新 state。不修改入参。

字段路径约定（对齐 ``state-delta-v0.md §2.5``）：
- ``character_changes.field`` 形如 ``state.location`` / ``core.personality[2]``：
  - ``facet=state`` 写入 ``characters[cid].current_state[key]``（顶层 key 替换）。
  - ``facet=definition`` 写入 ``characters[cid].definition[key]``（顶层 key 替换）。
  - 列表整体替换：``knowledge`` / ``beliefs`` 数组整体替换为 ``after``（直接覆盖）。
- ``world_changes``：
  - ``world_kind in {location, faction, rule}`` → ``world.<world_kind>s[id] = merged``
    （顶层 key 替换 ``after``；rule 走 ``world_rules`` 列表，append/remove 同步）。
  - ``world_kind in {politics, economy, event, time}`` → ``world[world_kind] = merged``
    （顶层 key 替换 ``after``，无则创建空 dict/对象）。
- ``relationship_changes``：按 ``from_character_id + to_character_id + relation_type``
  在 ``characters[].relationships`` 与顶层索引中匹配；``after`` 整体替换。
- ``new_events``：
  - ``state.recent_events`` append ``event_id``；超过 50 条截断保留最新 50。
  - ``state.events[event_id] = {type, participants, time, description}``（轻量字典，避免
    重复 event 详情；``event_id`` 即 ``target_id``）。
- ``resolved_hooks``：按 ``hook_id`` 更新 ``state.hooks`` 元素的 ``status`` / ``payoff_summary``。
- ``new_hooks``：append 到 ``state.hooks``（status 默认 OPEN）。
- ``debt_changes``：按 ``op`` 处理 ``state.debts``（add/update/remove）。

设计要点：
- 纯函数：不读不写 DB；只对入参做深拷贝再修改；返回新 state。
- 失败安全：找不到目标对象时（update/remove）静默忽略——与「不污染前一状态」原则一致：
  apply 阶段不抛错，由 validator 与 commit 阶段提前拦截；本函数只保证「给定合法 delta」
  能产出「与领域意图一致」的新 state。
- ``world_changes.field`` 中允许点号路径（如 ``state.location``）；本实现按顶层 key 替换
  （schema §2.5 中 field 是字符串但未限定为 dotted path；与 PRD §17「facet 字段粒度」同
  程度简化——粒度细化属 v1 工作）。
"""

from __future__ import annotations

import copy
import logging
from typing import Any

_logger = logging.getLogger(__name__)

# recent_events 最大保留条数（对齐任务书口径 50）。
RECENT_EVENTS_CAP = 50

# world_kind 属于"基础实体" → 写入对应 dict（key = world_id）。
_LOCATION_KIND = "location"
_FACTION_KIND = "faction"
_RULE_KIND = "rule"

# world_kind 属于"运行时维度" → 写入 world[<world_kind>]（不挂 location_id）。
_RUNTIME_KINDS = {"politics", "economy", "event", "time"}


# ----------------------------------------------------------------------------- public


def apply_delta(state: dict, delta: dict) -> dict:
    """应用 Delta 到 state，返回新 state（不修改入参）。"""
    new_state = copy.deepcopy(state)
    _apply_character_changes(new_state, delta.get("character_changes") or [])
    _apply_world_changes(new_state, delta.get("world_changes") or [])
    _apply_relationship_changes(new_state, delta.get("relationship_changes") or [])
    _apply_new_events(new_state, delta.get("new_events") or [])
    _apply_resolved_hooks(new_state, delta.get("resolved_hooks") or [])
    _apply_new_hooks(new_state, delta.get("new_hooks") or [])
    _apply_debt_changes(new_state, delta.get("debt_changes") or [])
    return new_state


# ----------------------------------------------------------------------------- arrays


def _apply_character_changes(state: dict, items: list[dict]) -> None:
    characters = state.setdefault("characters", [])
    by_id = {c.get("character_id"): c for c in characters}
    for change in items:
        cid = change.get("character_id")
        op = change.get("op")
        field = change.get("field") or ""
        after = change.get("after")
        char = by_id.get(cid)
        if char is None:
            # update/remove 但角色不在快照中：按静默忽略处理（apply 阶段不抛错）。
            continue

        facet = change.get("facet")
        # 兼容顶层字段 knowledge/beliefs：整体替换为 after（after 必须是 list）。
        if field in ("knowledge", "beliefs"):
            if op in ("update", "add"):
                char[field] = list(after) if isinstance(after, list) else []
            elif op == "remove":
                char[field] = []
            continue

        # facet=state → current_state[key] 顶层 key 替换
        # facet=definition → definition[key] 顶层 key 替换（首次出现自动创建 dict）
        target_root = "current_state" if facet == "state" else "definition"
        bucket = char.setdefault(target_root, {}) if target_root in char else char.setdefault(
            target_root, {}
        )
        # 若 char 已存在 current_state 为非 dict（比如初始化时缺省 {}），保证是 dict
        if not isinstance(bucket, dict):
            bucket = {}
            char[target_root] = bucket
        if op in ("add", "update"):
            key = field.split(".", 1)[-1] if field.startswith("state.") or field.startswith("core.") else field
            # 若 field 含点号（如 state.location），取最后一段作为 key（顶层替换语义）。
            # P2-1：op=update 且 after=None 时，删除该 key（保证回滚后与原快照严格等价）；
            # op=add 且 after=None 时保持原行为（写入 None）。
            if op == "update" and after is None:
                bucket.pop(key, None)
            else:
                bucket[key] = after
        elif op == "remove":
            key = field.split(".", 1)[-1] if field.startswith("state.") or field.startswith("core.") else field
            bucket.pop(key, None)


def _apply_world_changes(state: dict, items: list[dict]) -> None:
    world = state.setdefault("world", {})
    for change in items:
        kind = change.get("world_kind")
        op = change.get("op")
        wid = change.get("world_id")
        field = change.get("field") or ""
        after = change.get("after")

        if kind in (_LOCATION_KIND, _FACTION_KIND):
            bucket_key = "locations" if kind == _LOCATION_KIND else "factions"
            bucket = world.setdefault(bucket_key, {})
            entry = bucket.get(wid)
            if op in ("add", "update"):
                # 修复 wfr_3cb2182a30f6：bucket[wid] 非 None 且非 dict 时（被旧形状
                # hint 整条目替换污染过，例如 factions[fac]='some-str' 或
                # locations[loc]={'state': {...}} 残壳），换新 {} 入桶并 log warning，
                # 避免 _set_top_level 在非 dict 上抛 TypeError。
                if entry is not None and not isinstance(entry, dict):
                    _logger.warning(
                        "applier._apply_world_changes: bucket[%r] 被污染（类型=%s），"
                        "换新 dict 入桶（kind=%s, world_id=%r）",
                        wid, type(entry).__name__, kind, wid,
                    )
                    entry = {}
                    bucket[wid] = entry
                if entry is None:
                    # 新建条目：用 after 整体填充（允许 after 为 dict 含 name/statement/...）
                    entry = {}
                    bucket[wid] = entry
                _set_top_level(entry, field, after)
            elif op == "remove":
                bucket.pop(wid, None)
            continue

        if kind == _RULE_KIND:
            rules = world.setdefault("world_rules", [])
            if op == "add":
                rules.append(
                    {
                        "world_rule_id": wid,
                        "name": (after or {}).get("name") if isinstance(after, dict) else None,
                        "statement": (after or {}).get("statement") if isinstance(after, dict) else "",
                        "data_json": (after or {}).get("data_json") if isinstance(after, dict) else {},
                    }
                )
            elif op == "update":
                # 防御（P2-1）：world_rules 列表若被旧形状污染成 str 元素，
                # r.get 会抛 AttributeError；非 dict 元素跳过 + warning，
                # 与 location/faction 桶非 dict 守卫风格一致。
                for r in rules:
                    if not isinstance(r, dict):
                        _logger.warning(
                            "applier._apply_world_changes: world_rules 含非 dict 元素（类型=%s），"
                            "跳过（world_id=%r）",
                            type(r).__name__, wid,
                        )
                        continue
                    if r.get("world_rule_id") == wid:
                        if isinstance(after, dict):
                            _set_top_level(r, "name", after.get("name"))
                            _set_top_level(r, "statement", after.get("statement"))
                            _set_top_level(r, "data_json", after.get("data_json"))
                        break
            elif op == "remove":
                # 防御（P2-1）：同 update，非 dict 元素跳过 + warning。
                world["world_rules"] = []
                for r in rules:
                    if not isinstance(r, dict):
                        _logger.warning(
                            "applier._apply_world_changes: world_rules 含非 dict 元素（类型=%s），"
                            "跳过（world_id=%r）",
                            type(r).__name__, wid,
                        )
                        continue
                    if r.get("world_rule_id") != wid:
                        world["world_rules"].append(r)
            continue

        if kind in _RUNTIME_KINDS:
            slot = world.setdefault(kind, {})
            if op in ("add", "update"):
                if isinstance(after, dict):
                    slot[wid] = after
                else:
                    slot[wid] = {"value": after}
            elif op == "remove":
                slot.pop(wid, None)
            continue

        # 未识别 kind：静默忽略（schema 校验已保证合法枚举）。


def _set_top_level(entry: dict, field: str, value: Any) -> None:
    """设置 entry[field]。

    路径语义：
    - 形如 ``"data_json.population"`` → 进入 entry["data_json"]["population"] 子 dict。
    - 形如 ``"name"`` / ``"statement"`` → 顶层 key 替换。
    - 形如 ``"state.location"`` → 取最后一段 ``location`` 作为顶层 key（与 character_changes
      facet=state 同一语义，对齐 state-delta-v0.md §2.5.1 示例）。
    """
    if not field:
        return
    parts = field.split(".")
    # 显式两层：data_json.<key> → 进入子 dict（保证子 dict 存在）
    if len(parts) == 2 and parts[0] == "data_json" and parts[1]:
        bucket = entry.setdefault("data_json", {})
        if not isinstance(bucket, dict):
            bucket = {}
            entry["data_json"] = bucket
        bucket[parts[1]] = value
        return
    # 顶层 key 替换（field 含点号则取最后一段，与 character_changes 对齐）
    entry[parts[-1]] = value


def _apply_relationship_changes(state: dict, items: list[dict]) -> None:
    """应用 relationship_changes 到 state。

    修复 wfr_6619a7bfa6fa：关系端点 (``from_character_id`` / ``to_character_id``)
    既可指向 character_id，也可指向 faction_id（组织间关系，例如两家典当行商战）。
    端点宿主桶解析按 characters ∪ factions 顺序查找；若端点既不在 characters
    也不在 factions 中（apply 阶段已被 validator 拒），静默忽略。

    设计要点：
    - 关系挂「from 端点所在宿主」上：character 关系挂在
      ``state["characters"][from].relationships``；faction 关系挂在
      ``state["world"]["factions"][from].relationships``（factions 是 dict，
      每个 faction entry 含 ``relationships`` list，对齐 snapshot 聚合口径）。
    - 字段名沿用 ``from_character_id`` / ``to_character_id``（schema/DB 历史
      命名），端点身份对 entry 字段值透明；宿主流（character / faction）的
      区分仅在「写到哪个桶的 relationships 列表」。
    - remove 不区分宿主：按 (from,to,type) 元组遍历两个桶的宿主关系删除
      （避免悬挂；与 rollback 路径 ``apply_inverse_cleanup_to_state`` 行为一致）。
    """
    characters = state.setdefault("characters", [])
    world = state.setdefault("world", {})
    factions = world.setdefault("factions", {}) if isinstance(world, dict) else {}

    for change in items:
        from_id = change.get("from_character_id")
        to_id = change.get("to_character_id")
        rel_type = change.get("relation_type")
        op = change.get("op")
        after = change.get("after")

        if op == "remove":
            # remove：按 (from,to,type) 同时清理两个桶的宿主关系，避免悬挂。
            _rel_key = (from_id, to_id, rel_type)
            for c in characters:
                if not isinstance(c, dict):
                    continue
                rels = c.get("relationships")
                if not isinstance(rels, list):
                    continue
                c["relationships"] = [
                    r for r in rels
                    if not (
                        isinstance(r, dict)
                        and r.get("from_character_id") == _rel_key[0]
                        and r.get("to_character_id") == _rel_key[1]
                        and r.get("relation_type") == _rel_key[2]
                    )
                ]
            for fid, fac in factions.items():
                if not isinstance(fac, dict):
                    continue
                rels = fac.get("relationships")
                if not isinstance(rels, list):
                    continue
                fac["relationships"] = [
                    r for r in rels
                    if not (
                        isinstance(r, dict)
                        and r.get("from_character_id") == _rel_key[0]
                        and r.get("to_character_id") == _rel_key[1]
                        and r.get("relation_type") == _rel_key[2]
                    )
                ]
            continue

        # add / update：定位 from 端点所在宿主桶（characters 先于 factions）
        rels: list | None = None
        if isinstance(from_id, str) and from_id:
            for c in characters:
                if isinstance(c, dict) and c.get("character_id") == from_id:
                    rels = c.setdefault("relationships", [])
                    break
            if rels is None and from_id in factions:
                entry = factions.get(from_id)
                if isinstance(entry, dict):
                    rels = entry.setdefault("relationships", [])
        if rels is None:
            # 端点不在 characters / factions 中（validator 已拒，静默忽略）
            continue
        match_idx = None
        for i, r in enumerate(rels):
            if (
                r.get("from_character_id") == from_id
                and r.get("to_character_id") == to_id
                and r.get("relation_type") == rel_type
            ):
                match_idx = i
                break
        if op in ("add", "update"):
            entry = {
                "relationship_id": change.get("target_id"),
                "from_character_id": from_id,
                "to_character_id": to_id,
                "relation_type": rel_type,
                "state_json": after if isinstance(after, dict) else {},
            }
            if match_idx is None:
                rels.append(entry)
            else:
                rels[match_idx] = entry


def _apply_new_events(state: dict, items: list[dict]) -> None:
    recent = state.setdefault("recent_events", [])
    events = state.setdefault("events", {})
    for ev in items:
        eid = ev.get("event_id")
        if not eid:
            continue
        recent.append(eid)
        events[eid] = {
            "type": ev.get("type"),
            "participants": ev.get("participants") or [],
            "time": ev.get("time") or {},
            "description": ev.get("description"),
            # 携带伴随列（visibility / who_knows）与 write_through 落库口径一致，
            # 让 snapshot 中 events[eid] 也具备 knowledge_leakage 校验所需的
            # who_knows 字段（缺失→None，三态语义保持一致）。
            "visibility": ev.get("visibility") or "RESTRICTED",
            "who_knows": ev.get("who_knows"),
        }
    # 截断：保留最新 RECENT_EVENTS_CAP 条
    if len(recent) > RECENT_EVENTS_CAP:
        del recent[: len(recent) - RECENT_EVENTS_CAP]


def _apply_resolved_hooks(state: dict, items: list[dict]) -> None:
    hooks = state.setdefault("hooks", [])
    by_id = {h.get("hook_id"): h for h in hooks}
    for change in items:
        hid = change.get("hook_id")
        h = by_id.get(hid)
        if h is None:
            continue
        to_status = change.get("to_status")
        if to_status:
            h["status"] = to_status
        if change.get("payoff_summary"):
            h["payoff_summary"] = change["payoff_summary"]
        # 逆 Delta（rollback）哨兵：显式清除 payoff_chapter_id，与领域表写透的置 NULL 对齐
        if "__CLEAR_PAYOFF_CHAPTER__" in (change.get("notes") or ""):
            h.pop("payoff_chapter_id", None)
        else:
            # 兑现章节缺省时回退到 evidence.chapter_id，与领域表写透（payoff_chapter_id=delta.chapter_id）对齐
            payoff_ch = change.get("payoff_chapter_id") or (change.get("evidence") or {}).get("chapter_id")
            if payoff_ch:
                h["payoff_chapter_id"] = payoff_ch


def _apply_new_hooks(state: dict, items: list[dict]) -> None:
    hooks = state.setdefault("hooks", [])
    for change in items:
        hooks.append(
            {
                "hook_id": change.get("hook_id"),
                "name": change.get("name"),
                "introduced_chapter_id": change.get("chapter_id"),
                "status": "OPEN",
                "importance": change.get("importance"),
                "expected_payoff_chapter_id": change.get("expected_payoff_chapter_id"),
                "payoff_chapter_id": None,
                "visibility": change.get("visibility") or "RESTRICTED",
                "description": change.get("description"),
                # who_knows：与 write_through.hooks INSERT 同款三态语义
                # （缺失→None）；knowledge_leakage guardrail 读取快照此字段。
                "who_knows": change.get("who_knows"),
            }
        )


def _apply_debt_changes(state: dict, items: list[dict]) -> None:
    debts = state.setdefault("debts", [])
    by_id = {d.get("debt_id"): i for i, d in enumerate(debts)}
    for change in items:
        op = change.get("op")
        did = change.get("debt_id")
        idx = by_id.get(did)
        if op == "add":
            debts.append(
                {
                    "debt_id": did,
                    "description": change.get("description"),
                    "created_chapter_id": change.get("chapter_id"),
                    "severity": change.get("severity_after"),
                    "deadline_chapter_id": change.get("deadline_chapter_id"),
                    "status": change.get("status_after"),
                    "visibility": change.get("visibility") or "RESTRICTED",
                    # who_knows：与 write_through.narrative_debts INSERT 同款三态语义。
                    "who_knows": change.get("who_knows"),
                }
            )
            by_id[did] = len(debts) - 1
        elif op == "update":
            if idx is None:
                continue
            existing = debts[idx]
            if change.get("severity_after") is not None:
                existing["severity"] = change["severity_after"]
            if change.get("status_after") is not None:
                existing["status"] = change["status_after"]
            if change.get("deadline_chapter_id") is not None:
                existing["deadline_chapter_id"] = change["deadline_chapter_id"]
            if change.get("description") is not None:
                existing["description"] = change["description"]
            # 三态语义：who_knows 缺失=不更新该列（沿用 DB 现值）。
            if "who_knows" in change:
                existing["who_knows"] = change["who_knows"]
        elif op == "remove":
            if idx is not None:
                debts.pop(idx)
                by_id = {d.get("debt_id"): i for i, d in enumerate(debts)}


__all__ = ["apply_delta", "RECENT_EVENTS_CAP"]
