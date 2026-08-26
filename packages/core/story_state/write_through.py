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

from typing import Any

from packages.core.ids import new_id, now_iso

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
    - 剔除 ``hooks`` 中 ``hook_id`` 在 ``remove_hook_ids`` 里的元素。

    必须在 ``commit_delta`` 同一事务内调用（在 materialize_snapshot 之前），这样落盘
    的 story_states 即「回滚后」语义；rollback 后 GET state 直接拿到该快照，无需 post-facto
    修改。
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
                conn.execute(
                    """
                    INSERT INTO relationships
                        (relationship_id, project_id, from_character_id, to_character_id,
                         relation_type, state_json, last_state_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (rid, project_id, from_id, to_id, rel_type, _dump(after or {}), new_version),
                )
            else:
                conn.execute(
                    """
                    UPDATE relationships SET state_json = ?, last_state_version = ?
                    WHERE relationship_id = ?
                    """,
                    (_dump(after or {}), new_version, existing["relationship_id"]),
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

    # resolved_hooks
    # 哨兵：notes 含 ``__CLEAR_PAYOFF_CHAPTER__`` → 显式把 hooks.payoff_chapter_id 置 NULL
    # （用于逆 Delta；schema 不允许新字段，只能用 notes 字符串携带标记）。
    for rh in delta.get("resolved_hooks") or []:
        notes = rh.get("notes") or ""
        clear_payoff = "__CLEAR_PAYOFF_CHAPTER__" in notes
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
