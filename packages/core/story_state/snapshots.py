"""Story State 快照读写 / 序列化 / Diff 工具（Sprint 2 + Sprint 7 V1.1）。

职责（god-object 拆分后）：
- ``strip_state_version`` / ``diff_snapshots`` / ``_diff_lists`` / ``_diff_dicts``——
  纯函数：快照 diff 工具集；供 ``queries.diff_versions`` 与外部模块复用。
- ``_dump`` / ``_parse_json`` / ``_parse_required_json``——JSON 列读写的最小工具集
  （与 snapshot.py 中的 ``_dump`` 独立；本模块负责「读路径 + diff 工具」的 JSON
  序列化；snapshot.py 负责「写快照」语义，互不交叉）。
- ``_latest_snapshot_version``——取 ``story_states`` 最新 (version, snapshot_json)，
  用于 get_current_state / commit_delta / create_branch / promote_branch 共用。

设计要点：
- 本模块**只读** ``story_states`` 表（不写；写由 snapshot.materialize_snapshot
  与 commits.commit_delta 完成）。
- ``diff_snapshots`` 与 ``strip_state_version`` 是公开 helper——外部模块（如
  ``packages.core.simulation``）直接 import 用。
- 拆分后保持与原 ``service.py`` **逐字节相同** 的 SQL / 返回值 / 异常语义。
"""

from __future__ import annotations

import json
from typing import Any


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _parse_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return {} if isinstance(raw, str) and raw.startswith(("{", "[")) else None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _parse_required_json(raw: Any, default: Any = None) -> Any:
    parsed = _parse_json(raw)
    if parsed is None:
        return default
    return parsed


def strip_state_version(snap: dict) -> dict:
    """去掉 ``state_version`` 字段以便 diff。"""
    if isinstance(snap, dict) and "state_version" in snap:
        out = dict(snap)
        out.pop("state_version", None)
        return out
    return snap


def _list_key(item: dict) -> str | None:
    """提取列表元素的稳定 key（id/name/hook_id/debt_id/event_id 等）。"""
    if not isinstance(item, dict):
        return None
    for k in ("character_id", "location_id", "faction_id", "world_rule_id",
              "hook_id", "debt_id", "event_id", "relationship_id", "id", "name"):
        v = item.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _diff_lists(a: list, b: list) -> dict:
    """按稳定 key 配对 diff 两个列表。

    返回 ``{"added": [...], "removed": [...], "changed": [{...}, ...]}``；仅在确有差异时返回非空。
    """
    a_keys = {_list_key(x): x for x in a if isinstance(x, dict)}
    b_keys = {_list_key(x): x for x in b if isinstance(x, dict)}
    a_set = set(a_keys.keys())
    b_set = set(b_keys.keys())
    added_keys = b_set - a_set
    removed_keys = a_set - b_set
    common = a_set & b_set
    changed: list = []
    for k in sorted(common):
        if a_keys[k] != b_keys[k]:
            sub = _diff_dicts(a_keys[k], b_keys[k])
            changed.append({"id": k, **({"changes": sub} if sub else {})})
    out: dict = {}
    if added_keys:
        out["added"] = [b_keys[k] for k in sorted(added_keys)]
    if removed_keys:
        out["removed"] = [a_keys[k] for k in sorted(removed_keys)]
    if changed:
        out["changed"] = changed
    return out


def _diff_dicts(a: dict, b: dict) -> dict:
    """递归 diff 两个 dict：仅返回 b 中与 a 不同（含 key 存在/值不同）的字段。

    list 元素按稳定 key 配对（``_list_key``）；非 dict/list 直接 ``{before, after}``。
    """
    diff: dict = {}
    keys = set(a.keys()) | set(b.keys())
    for k in keys:
        av = a.get(k)
        bv = b.get(k)
        if isinstance(av, dict) and isinstance(bv, dict):
            sub = _diff_dicts(av, bv)
            if sub:
                diff[k] = sub
        elif isinstance(av, list) and isinstance(bv, list):
            ld = _diff_lists(av, bv)
            if ld:
                diff[k] = ld
        elif av != bv:
            diff[k] = {"before": av, "after": bv}
    return diff


def diff_snapshots(a: dict, b: dict, *, version_a: int, version_b: int, branch_id: str | None) -> dict:
    """按 snapshot 结构组装 diff。"""
    out: dict = {
        "version_a": version_a,
        "version_b": version_b,
        "branch_id": branch_id,
    }
    chars_diff = _diff_lists(a.get("characters") or [], b.get("characters") or [])
    if chars_diff:
        out["characters"] = chars_diff
    world_a = a.get("world") or {}
    world_b = b.get("world") or {}
    world_diff = _diff_dicts(world_a, world_b)
    if world_diff:
        out["world"] = world_diff
    hooks_diff = _diff_lists(a.get("hooks") or [], b.get("hooks") or [])
    if hooks_diff:
        out["hooks"] = hooks_diff
    debts_diff = _diff_lists(a.get("debts") or [], b.get("debts") or [])
    if debts_diff:
        out["debts"] = debts_diff
    events_a = a.get("events") or {}
    events_b = b.get("events") or {}
    events_diff = _diff_dicts(events_a, events_b)
    if events_diff:
        out["events_changed"] = events_diff
    recent_diff = _diff_lists(a.get("recent_events") or [], b.get("recent_events") or [])
    if recent_diff:
        out["recent_events"] = recent_diff
    return out


def latest_snapshot_version(conn, project_id: str) -> tuple[int, dict] | tuple[int, None]:
    """返回 ``(version, snapshot_json_or_None)``，无快照时 version=0, snapshot=None。

    与 ``service.py`` 原 ``_latest_snapshot_version`` 同语义；本模块以无下划线
    命名暴露供跨模块调用（commits / branches / queries 都需要它）。
    """
    row = conn.execute(
        "SELECT state_version, snapshot_json FROM story_states "
        "WHERE project_id = ? ORDER BY state_version DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        return 0, None
    return row["state_version"], _parse_required_json(row["snapshot_json"], {})


__all__ = [
    "strip_state_version",
    "diff_snapshots",
    "latest_snapshot_version",
]
