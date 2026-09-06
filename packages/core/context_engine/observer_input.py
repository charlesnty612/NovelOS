"""Observer 输入装配——build_observer_input 与快照裁剪
（拆分自 builders.py，2026-09-06 审查批次三）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection

from .builders_common import (
    _DEBT_OPEN_STATUSES,
    _HOOK_OPEN_STATUSES,
    _collect_touched_entity_ids,
    _director_plan_summary,
    _latest_draft,
    _row_to_chapter,
)

# V3.1.1 O-1：observer events 滚动窗口默认大小（章）。
# 出窗事件从 payload 整体移除（DB plot_events 可检索）；窗口内事件 + 被
# 最近 N 个 commit touched 的事件保留。窗口值越小，payload 越轻；6 章约等于
# 真实项目两周剧情密度，足以覆盖常见 delta 涉及的事件上下文。
_DEFAULT_EVENTS_WINDOW_CHAPTERS = 6


def _compact_open_hook(h: dict) -> dict:
    """对 open 状态 hook 做字段压缩（V3.1.1 O-1）。

    保留 ``{hook_id, name, status, visibility, created_chapter}``，其余字段
    （description / importance / expected_payoff_chapter_id / created_at /
    updated_at 等）整段移出。created_chapter 优先取 introduced_chapter_id
    （即事件首次出现的章节），便于 observer 判断 hook 起源。

    设计依据：open hook 在 payload 中仅用于「observer 检视其进度时知道还有
    哪些未兑现的伏笔」，描述与时间字段对 observer 决策无直接价值，但每条
    description 默认有 30+ 字符，乘以 62 条 → ~2KB 开销。
    """
    created = h.get("introduced_chapter_id")
    if created is None:
        created = h.get("created_chapter_id")  # 兼容字段名
    return {
        "hook_id": h.get("hook_id"),
        "name": h.get("name"),
        "status": h.get("status"),
        "visibility": h.get("visibility"),
        "created_chapter": created,
    }


def _compact_open_debt(d: dict) -> dict:
    """对 open/acknowledged 状态 debt 做字段压缩（V3.1.1 O-1）。

    保留 ``{debt_id, description, status, visibility, created_chapter}``；
    severity / deadline_chapter_id / who_knows 等字段整体移除（observer 不需要
    据此决定 delta 的写入内容）。description 保留是因为 open debt 的描述
    通常很短（≤80 字符），且是 observer 判断「债务当前主题」的唯一线索。
    """
    created = d.get("created_chapter_id")
    return {
        "debt_id": d.get("debt_id"),
        "description": d.get("description"),
        "status": d.get("status"),
        "visibility": d.get("visibility"),
        "created_chapter": created,
    }


def _compact_event(e: dict) -> dict:
    """事件单条压缩（V3.1.1 O-1 窗口内保留形态）。

    保留 ``{type, description}``；participants / time 字段整体移除。
    设计：observer 在 trimmed 路径下关注「事件是什么 + 大致内容」即可，
    participants 由相关 character_changes 体现，time 由 chapter 元信息
    体现——但这两类字段乘以 200 条事件占 ~25KB，纯属体积噪声。
    """
    return {
        "type": e.get("type"),
        "description": e.get("description"),
    }


def _load_event_chapter_no_map(
    conn: sqlite3.Connection, project_id: str,
) -> dict[str, int]:
    """从 plot_events + chapters 表反查 ``event_id → chapter_no`` 映射。

    用于 V3.1.1 O-1 滚动窗口：snapshot.events 的 value 不携带 introduced
    chapter 信息，必须走一次轻量 SQL（单 project 累计 plot_events 通常
    ≤500 条，单次查询 P99 < 5ms）。plot_events.introduced_chapter_id 为
    NULL 的事件（DB 旧行 / 未挂章）映射为 ``-1``，永远不会落在窗口内——与
    「未挂章事件不出现在 observer payload」语义一致。

    返回：``{event_id: chapter_no}``，缺 mapping 的 event_id 不在 dict 中。
    """
    out: dict[str, int] = {}
    rows = conn.execute(
        """
        SELECT pe.event_id AS eid, pe.introduced_chapter_id AS cid, c.number AS cno
        FROM plot_events pe
        LEFT JOIN chapters c
          ON c.chapter_id = pe.introduced_chapter_id
        WHERE pe.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    for r in rows:
        eid = r["eid"]
        cno = r["cno"]
        if not eid:
            continue
        if cno is None:
            # 未挂章事件：用 -1 标记，永不出现在窗口内（设计上等价出窗）
            out[eid] = -1
        else:
            try:
                out[eid] = int(cno)
            except (TypeError, ValueError):
                out[eid] = -1
    return out


def _summarize_character(char: dict) -> dict:
    return {
        "character_id": char.get("character_id"),
        "name": char.get("name"),
        "facet": char.get("facet"),
    }


def _summarize_relationship(rel: dict) -> dict:
    return {
        "relationship_id": rel.get("relationship_id"),
        "from_character_id": rel.get("from_character_id"),
        "to_character_id": rel.get("to_character_id"),
        "relation_type": rel.get("relation_type"),
    }


def _summarize_location(loc_val: dict) -> dict:
    return {"name": loc_val.get("name") if isinstance(loc_val, dict) else None}


def _summarize_faction(fac_val: dict) -> dict:
    return {"name": fac_val.get("name") if isinstance(fac_val, dict) else None}


def _summarize_world_rule(rule: dict) -> dict:
    return {
        "world_rule_id": rule.get("world_rule_id"),
        "name": rule.get("name"),
    }


def _summarize_hook(h: dict) -> dict:
    return {
        "hook_id": h.get("hook_id"),
        "name": h.get("name"),
        "status": h.get("status"),
    }


def _summarize_debt(d: dict) -> dict:
    return {
        "debt_id": d.get("debt_id"),
        "description": d.get("description"),
        "status": d.get("status"),
    }


def _trim_snapshot_for_observer(
    snap: dict[str, Any],
    *,
    keep_recent_commits: int = 3,
    resolved_history_keep: int = 5,
    touched: dict[str, set[str]] | None = None,
    events_window_chapters: int | None = None,
    current_chapter_no: int | None = None,
    event_chapter_no_map: dict[str, int] | None = None,
    compact_open_hooks_debts: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    """对 observer 输入快照做「分代裁剪」。

    参数：
        snap：全量 Canonical State 快照。
        keep_recent_commits：扫描 commits 表的最近 N 个 delta。仅当 ``touched is None``
            时使用；若调用方已自行计算 ``touched``，可直接传入以避免重复 IO。
        resolved_history_keep：resolved/abandoned hooks 与 paid/forgiven debts
            的「保留最近多少条」上限（按 hook_id/debt_id 字典序取末尾 N 条）。
        touched：可选预计算的「被 touch 的实体 ID 集合」（结构同
            ``_collect_touched_entity_ids`` 返回值）。
        events_window_chapters（V3.1.1 O-1）：events 滚动窗口大小（章）。None
            表示不裁剪 events（原样保留），与 M3 老行为逐字节一致；正整数
            启用窗口裁剪——保留满足 ``introduced_chapter_no >= current - N``
            的事件 + 被 ``touched["events"]`` 引用的事件，其余整体移出。
            旧 callers（不传新参数）行为 0 变化。
        current_chapter_no（V3.1.1 O-1）：当前章节号，用于窗口下界计算。
        event_chapter_no_map（V3.1.1 O-1）：可选预计算的
            ``event_id → chapter_no`` 映射（见 ``_load_event_chapter_no_map``）；
            调用方传 ``None`` 时函数不启动窗口（与 events_window_chapters=None
            等价）。这是为了避免纯函数做 DB IO。
        compact_open_hooks_debts（V3.1.1 O-1）：True 时 open/active/escalated
            hooks 与 open/acknowledged debts 在 payload 中以压缩字段形式
            出现（见 ``_compact_open_hook`` / ``_compact_open_debt``）；False
            保持全量（与 M3 老行为一致）。

    返回：
        (trimmed_snapshot, stats_dict)

    口径（与任务书一致，字段以 snapshot 实际结构为准）：
    - characters：touched 全量；其他仅 {character_id, name, facet}。
    - characters[].relationships：touched 全量；其他仅摘要。
    - world.locations / .factions（dict）：touched value 全量；其他仅 {name}。
    - world.world_rules（list）：touched 全量；其他仅 {world_rule_id, name}。
    - hooks（V3.1.1 O-1）：open/active/escalated 走压缩字段（默认）；resolved/
      abandoned 仅保留最近 N 条摘要；touched 的 resolved hook 强制保留（即便
      超出 N 条上限）。
    - debts（V3.1.1 O-1）：open/acknowledged 走压缩字段（默认）；paid/forgiven
      仅保留最近 N 条摘要；touched 强制保留。
    - events（V3.1.1 O-1）：仅当 events_window_chapters 非 None 且
      current_chapter_no 与 event_chapter_no_map 都提供时启用滚动窗口；
      否则原样保留（与 M3 老行为一致）。
    - state_version / recent_events / world.current_time_in_story /
      world.active_resources：原样保留。
    """
    stats: dict[str, int] = {
        "characters_full": 0,
        "characters_summary": 0,
        "relationships_full": 0,
        "relationships_summary": 0,
        "locations_full": 0,
        "locations_summary": 0,
        "factions_full": 0,
        "factions_summary": 0,
        "world_rules_full": 0,
        "world_rules_summary": 0,
        "hooks_open": 0,
        "hooks_open_compacted": 0,
        "hooks_resolved_kept": 0,
        "hooks_resolved_trimmed": 0,
        "debts_open": 0,
        "debts_open_compacted": 0,
        "debts_resolved_kept": 0,
        "debts_resolved_trimmed": 0,
        "events_total": 0,
        "events_kept_window": 0,
        "events_kept_touched": 0,
        "events_dropped": 0,
        "events_bytes_before": 0,
        "events_bytes_after": 0,
    }

    if not isinstance(snap, dict):
        return {"snapshot_mode": "trimmed"}, stats

    touched = touched or {
        "characters": set(),
        "locations": set(),
        "factions": set(),
        "world_rules": set(),
        "hooks": set(),
        "debts": set(),
        "events": set(),
        "relationships": set(),
        "relationship_keys": set(),
    }

    touched_chars = touched.get("characters", set())
    touched_locs = touched.get("locations", set())
    touched_facs = touched.get("factions", set())
    touched_rules = touched.get("world_rules", set())
    touched_hooks = touched.get("hooks", set())
    touched_debts = touched.get("debts", set())
    touched_rel_ids = touched.get("relationships", set())
    touched_rel_keys = touched.get("relationship_keys", set())
    touched_events = touched.get("events", set())

    trimmed: dict[str, Any] = {"snapshot_mode": "trimmed"}
    for k in ("state_version", "recent_events"):
        if k in snap:
            trimmed[k] = snap[k]

    # ---- events（V3.1.1 O-1 滚动窗口）----
    # 决策：仅当调用方显式传齐三件套（窗口大小 + 当前章号 + event→chapter
    # 映射）才启用窗口；任意缺失则保留老行为（原样写 snap["events"]），
    # 保证纯函数签名向后兼容、单测无需 DB 也能跑通。
    events_in = snap.get("events")
    if (
        events_window_chapters is not None
        and isinstance(events_window_chapters, int)
        and events_window_chapters >= 1
        and current_chapter_no is not None
        and isinstance(current_chapter_no, int)
        and isinstance(event_chapter_no_map, dict)
        and isinstance(events_in, dict)
    ):
        window_floor = current_chapter_no - events_window_chapters
        # 体积量化：先量原大小，便于 stats 观测
        try:
            stats["events_bytes_before"] = len(
                json.dumps(events_in, ensure_ascii=False)
            )
        except (TypeError, ValueError):
            stats["events_bytes_before"] = 0
        events_out: dict[str, dict] = {}
        for eid, eval_ in events_in.items():
            if not isinstance(eid, str) or not isinstance(eval_, dict):
                continue
            stats["events_total"] += 1
            kept = False
            e_cno = event_chapter_no_map.get(eid)
            if isinstance(e_cno, int) and e_cno >= window_floor and e_cno <= current_chapter_no:
                events_out[eid] = _compact_event(eval_)
                stats["events_kept_window"] += 1
                kept = True
            elif eid in touched_events:
                # 窗口外但被最近 commit 引入的事件——保留（确保 delta 完整性）
                events_out[eid] = _compact_event(eval_)
                stats["events_kept_touched"] += 1
                kept = True
            if not kept:
                stats["events_dropped"] += 1
        try:
            stats["events_bytes_after"] = len(
                json.dumps(events_out, ensure_ascii=False)
            )
        except (TypeError, ValueError):
            stats["events_bytes_after"] = 0
        trimmed["events"] = events_out
    elif isinstance(events_in, dict):
        # 默认/兼容路径：events 原样保留（M3 老行为）
        trimmed["events"] = events_in

    # ---- characters ----
    chars_in = snap.get("characters") or []
    chars_out: list[dict] = []
    if isinstance(chars_in, list):
        for c in chars_in:
            if not isinstance(c, dict):
                continue
            cid = c.get("character_id")
            is_full = isinstance(cid, str) and cid in touched_chars
            if is_full:
                rels_in = c.get("relationships") or []
                rels_out: list[dict] = []
                if isinstance(rels_in, list):
                    for rel in rels_in:
                        if not isinstance(rel, dict):
                            continue
                        rid = rel.get("relationship_id")
                        rkey = None
                        f = rel.get("from_character_id")
                        t = rel.get("to_character_id")
                        rt = rel.get("relation_type")
                        if isinstance(f, str) and isinstance(t, str) and isinstance(rt, str):
                            rkey = f"{f}::{t}::{rt}"
                        rel_touched = (
                            (isinstance(rid, str) and rid in touched_rel_ids)
                            or (rkey is not None and rkey in touched_rel_keys)
                        )
                        if rel_touched:
                            rels_out.append(rel)
                            stats["relationships_full"] += 1
                        else:
                            rels_out.append(_summarize_relationship(rel))
                            stats["relationships_summary"] += 1
                new_c = dict(c)
                new_c["relationships"] = rels_out
                chars_out.append(new_c)
                stats["characters_full"] += 1
            else:
                chars_out.append(_summarize_character(c))
                stats["characters_summary"] += 1
    trimmed["characters"] = chars_out

    # ---- world ----
    world_in = snap.get("world") or {}
    world_out: dict[str, Any] = {}
    if isinstance(world_in, dict):
        for k in ("current_time_in_story", "active_resources"):
            if k in world_in:
                world_out[k] = world_in[k]

        locs_in = world_in.get("locations") or {}
        locs_out: dict[str, dict] = {}
        if isinstance(locs_in, dict):
            for lid, lval in locs_in.items():
                if lid in touched_locs:
                    locs_out[lid] = lval
                    stats["locations_full"] += 1
                else:
                    locs_out[lid] = _summarize_location(lval)
                    stats["locations_summary"] += 1
        world_out["locations"] = locs_out

        facs_in = world_in.get("factions") or {}
        facs_out: dict[str, dict] = {}
        if isinstance(facs_in, dict):
            for fid, fval in facs_in.items():
                if fid in touched_facs:
                    facs_out[fid] = fval
                    stats["factions_full"] += 1
                else:
                    facs_out[fid] = _summarize_faction(fval)
                    stats["factions_summary"] += 1
        world_out["factions"] = facs_out

        rules_in = world_in.get("world_rules") or []
        rules_out: list[dict] = []
        if isinstance(rules_in, list):
            for r in rules_in:
                if not isinstance(r, dict):
                    continue
                rid = r.get("world_rule_id")
                if isinstance(rid, str) and rid in touched_rules:
                    rules_out.append(r)
                    stats["world_rules_full"] += 1
                else:
                    rules_out.append(_summarize_world_rule(r))
                    stats["world_rules_summary"] += 1
        world_out["world_rules"] = rules_out

    trimmed["world"] = world_out

    # ---- hooks ----
    hooks_in = snap.get("hooks") or []
    hooks_open_out: list[dict] = []
    hooks_resolved_out: list[dict] = []
    if isinstance(hooks_in, list):
        for h in hooks_in:
            if not isinstance(h, dict):
                continue
            status = h.get("status")
            if isinstance(status, str) and status in _HOOK_OPEN_STATUSES:
                if compact_open_hooks_debts:
                    hooks_open_out.append(_compact_open_hook(h))
                    stats["hooks_open_compacted"] += 1
                else:
                    hooks_open_out.append(h)
            elif isinstance(status, str) and status in ("RESOLVED", "ABANDONED"):
                hooks_resolved_out.append(_summarize_hook(h))
    if hooks_resolved_out:
        hooks_resolved_out.sort(key=lambda x: x.get("hook_id") or "")
        if len(hooks_resolved_out) > resolved_history_keep:
            kept = hooks_resolved_out[-resolved_history_keep:]
            trimmed_count = len(hooks_resolved_out) - resolved_history_keep
        else:
            kept = hooks_resolved_out
            trimmed_count = 0
        for h in hooks_in:
            if not isinstance(h, dict):
                continue
            status = h.get("status")
            if not (isinstance(status, str) and status in ("RESOLVED", "ABANDONED")):
                continue
            hid = h.get("hook_id")
            if not (isinstance(hid, str) and hid in touched_hooks):
                continue
            if not any(k.get("hook_id") == hid for k in kept):
                kept.append(h)
        kept.sort(key=lambda x: x.get("hook_id") or "")
        hooks_resolved_out = kept
        stats["hooks_resolved_kept"] = len(hooks_resolved_out)
        stats["hooks_resolved_trimmed"] = trimmed_count
    else:
        stats["hooks_resolved_kept"] = 0
        stats["hooks_resolved_trimmed"] = 0
    stats["hooks_open"] = len(hooks_open_out)
    trimmed["hooks"] = hooks_open_out + hooks_resolved_out

    # ---- debts ----
    debts_in = snap.get("debts") or []
    debts_open_out: list[dict] = []
    debts_resolved_out: list[dict] = []
    if isinstance(debts_in, list):
        for d in debts_in:
            if not isinstance(d, dict):
                continue
            status = d.get("status")
            if isinstance(status, str) and status in _DEBT_OPEN_STATUSES:
                if compact_open_hooks_debts:
                    debts_open_out.append(_compact_open_debt(d))
                    stats["debts_open_compacted"] += 1
                else:
                    debts_open_out.append(d)
            elif isinstance(status, str) and status in ("paid", "forgiven"):
                debts_resolved_out.append(_summarize_debt(d))
    if debts_resolved_out:
        debts_resolved_out.sort(key=lambda x: x.get("debt_id") or "")
        if len(debts_resolved_out) > resolved_history_keep:
            kept = debts_resolved_out[-resolved_history_keep:]
            trimmed_count = len(debts_resolved_out) - resolved_history_keep
        else:
            kept = debts_resolved_out
            trimmed_count = 0
        for d in debts_in:
            if not isinstance(d, dict):
                continue
            status = d.get("status")
            if not (isinstance(status, str) and status in ("paid", "forgiven")):
                continue
            did = d.get("debt_id")
            if not (isinstance(did, str) and did in touched_debts):
                continue
            if not any(k.get("debt_id") == did for k in kept):
                kept.append(d)
        kept.sort(key=lambda x: x.get("debt_id") or "")
        debts_resolved_out = kept
        stats["debts_resolved_kept"] = len(debts_resolved_out)
        stats["debts_resolved_trimmed"] = trimmed_count
    else:
        stats["debts_resolved_kept"] = 0
        stats["debts_resolved_trimmed"] = 0
    stats["debts_open"] = len(debts_open_out)
    trimmed["debts"] = debts_open_out + debts_resolved_out

    return trimmed, stats


# V3.1.1 O-3：observer recent_event_ids 白名单默认上限（条）。
# 调用方可在 chapter_commit/pipeline.py 里显式覆盖；DB 读取按 rowid DESC 倒序
# 取最近 N 条 event_id 注入 payload["config"]["recent_event_ids"]，让 observer
# 在生成 new_events[*].event_id 时主动避开白名单中的既有 id，避免与 DB 已有
# event 主键冲突（ch063 历史现场：UNIQUE constraint failed）。
_DEFAULT_RECENT_EVENT_IDS_LIMIT = 30


def _load_recent_event_ids(
    db_path: str | Path, project_id: str, *, limit: int,
) -> list[str]:
    """从 plot_events 取最近 ``limit`` 条 event_id（按 rowid DESC；不依赖 created_at 列）。

    返回 ``[event_id, ...]``（最新在前）；DB IO 异常或 limit≤0 → 空 list。
    plot_events 表无 created_at 列（schema 见 0009），按 rowid 倒序等价「最新 N 条」。
    """
    if limit is None or not isinstance(limit, int) or limit <= 0:
        return []
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001
        return []
    try:
        rows = conn.execute(
            """
            SELECT event_id FROM plot_events
            WHERE project_id = ?
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (project_id, int(limit)),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: list[str] = []
    for r in rows:
        if not isinstance(r, sqlite3.Row):
            d = dict(r) if hasattr(r, "keys") else {"event_id": r[0]}
        else:
            d = dict(r)
        eid = d.get("event_id")
        if isinstance(eid, str) and eid:
            out.append(eid)
    return out


def build_observer_input(
    db_path: str | Path,
    chapter_id: str,
    *,
    min_excerpt_chars_low_confidence: int = 80,
    max_changes_per_array: int = 24,
    snapshot_mode: str = "full",
    keep_recent_commits: int = 3,
    resolved_history_keep: int = 5,
    events_window_chapters: int | None = _DEFAULT_EVENTS_WINDOW_CHAPTERS,
    recent_event_ids: list[str] | None = None,
    recent_event_ids_limit: int = _DEFAULT_RECENT_EVENT_IDS_LIMIT,
) -> dict[str, Any]:
    """组装 Observer 输入（agent-contracts §5.1 + M3 快照分代裁剪 + V3.1.1 O-1 + O-3）。

    设计决策（V3.3 P0-2 知识权限补全）：
    - observer 是作者视角（PRD §4 角色矩阵：observer 对应 AUTHOR/DIRECTOR 全可见档位），
      必须能看到所有 HIDDEN 实体与未到期 reveal_policy，才能给出符合策略的 delta；
    - 因此 observer 路径**不应用** ``_filter_hidden_by_reveal_policies``（与 writer
      路径相反）；writer 视角按 reveal_policy + visibility 双重过滤；
    - reveal_policies 统计只在 ``build_arc_view`` 与 ``arc.alerts`` 暴露给作者。

    参数新增（M3）：
        snapshot_mode："full"（默认，与旧行为字节级一致）或 "trimmed"。
            - "full"：payload["previous_state"] 是全量快照，stats 不写入。
            - "trimmed"：payload["previous_state"] 经过 ``_trim_snapshot_for_observer``
              裁剪；同时 payload["snapshot_trim_stats"] 记录各集合裁剪前后数量。
        keep_recent_commits：trimmed 模式下识别「被 touch 过实体」时扫描的最近
            commit 数（默认 3）。≥1 才生效；≤0 等价未 touch（全部走摘要）。
        resolved_history_keep：trimmed 模式下 resolved/abandoned hooks 与
            paid/forgiven debts 的保留上限（默认 5）。
        events_window_chapters（V3.1.1 O-1）：trimmed 模式下 events 滚动窗口
            大小（章，默认 6）。None / 0 / 负数等价禁用窗口——events 全部保留
            （兼容旧行为）。窗口保留规则：introduced_chapter_no ∈
            ``[current - N, current]`` 的事件 + 被最近 N commit touched 的事件；
            其余整体移出 payload（DB plot_events 可检索，DB 不受影响）。
        max_changes_per_array：注入 payload["config"]["max_changes_per_array"]
            的上限值（默认 **24**）。**实测依据**：原默认值 50 会在 observer 提示
            下诱发模型在 character_changes / world_changes 等数组上「穷举微变化」，
            单章 observer 输出 completion tokens 实测 2.9 万-5.8 万（正常 delta
            仅 2-3 千）。收紧到 24 既覆盖典型章节的原子变化条数（≤20 条），又能
            显著抑制模型「宁滥勿缺」的扩展倾向，将单章 observer 输出压缩至
            可接受范围。
        recent_event_ids（V3.1.1 O-3）：可选显式传入的「最近 event_id 白名单」；
            注入到 payload["config"]["recent_event_ids"]，observer 在生成
            ``new_events[*].event_id`` 时应主动避开白名单中 id，避免与 plot_events
            主键 UNIQUE 约束冲突（ch063 历史现场）。
        recent_event_ids_limit（V3.1.1 O-3）：当 ``recent_event_ids=None`` 时，
            从 plot_events 按 rowid DESC 自动取最近 N 条注入白名单（默认 30）。

    向后兼容：
        既有调用方（chapter_commit/pipeline.py:242、preview.py:443）零改动；
        ``snapshot_mode`` 默认值 "full" 保证行为完全等价。``max_changes_per_array``
        仅影响注入 prompt 的 config 字段值；调用方显式传参时按调用方为准。
        ``events_window_chapters`` 默认 6，仅影响 trimmed 模式；传 None 即可
        关闭窗口回归旧行为。
        ``recent_event_ids`` 默认 None → 按 ``recent_event_ids_limit`` 自动取数；
        老调用方未传该参数时，config 键会自动出现（注入空 list 也行——不破坏 schema，
        observer prompt 后续可识别并据白名单避让）。
    """
    if snapshot_mode not in ("full", "trimmed"):
        raise ValueError(
            f"snapshot_mode must be 'full' or 'trimmed', got {snapshot_mode!r}"
        )

    conn = get_connection(db_path)
    try:
        chap_row = conn.execute("SELECT * FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if chap_row is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        chapter = _row_to_chapter(chap_row)
        project_id = chapter["project_id"]
        # 当前章节号（V3.1.1 O-1 用于 events 窗口下界）
        try:
            current_chapter_no = int(chap_row["number"])
        except (KeyError, TypeError, ValueError):
            current_chapter_no = None
        draft_row = _latest_draft(conn, chapter_id)
    finally:
        conn.close()

    from packages.core.story_state.service import StoryStateService

    snap = StoryStateService(db_path).get_current_state(project_id)
    state_version = int(snap.get("state_version") or 0)

    plan_json = chapter.get("plan_json") or {}

    payload: dict[str, Any] = {
        "agent": "observer",
        "prompt_version": "observer:v1",
        "knowledge_permissions": {
            "your_visibility": ["AUTHOR", "DIRECTOR"],
            "forbidden_kinds": ["HIDDEN"],
        },
        "config": {
            "min_excerpt_chars_low_confidence": min_excerpt_chars_low_confidence,
            "max_changes_per_array": max_changes_per_array,
            # V3.1.1 O-3：recent_event_ids 白名单。调用方显式传则按调用方；
            # 否则按 recent_event_ids_limit 从 plot_events 自动取最近 N 条。
            # 注入 payload 让 observer 在生成 new_events[*].event_id 时主动避让，
            # 避免与 plot_events.event_id 主键 UNIQUE 约束冲突。
            "recent_event_ids": list(recent_event_ids)
            if isinstance(recent_event_ids, list)
            else _load_recent_event_ids(
                db_path, project_id, limit=recent_event_ids_limit,
            ),
        },
        "chapter": {
            "title": chapter.get("title"),
            "scene_ids": [],
            "draft_text": (draft_row or {}).get("content") or "",
            "chapter_id": chapter_id,
        },
        "previous_state_version": state_version,
        "previous_state": snap,
        "director_plan_summary": _director_plan_summary(plan_json),
    }

    if snapshot_mode == "trimmed":
        # 收集最近 N 个 commit 中被 touch 的实体 ID；DB IO 失败时回退到空 touched
        # 集合（退化等价于「未 touch 过」→ 全实体走摘要），保证裁剪路径不阻断装配。
        conn2 = get_connection(db_path)
        try:
            touched = _collect_touched_entity_ids(
                conn2, project_id, keep_recent_commits=keep_recent_commits,
            )
        except sqlite3.Error:
            touched = None
        finally:
            conn2.close()

        # V3.1.1 O-1：events 窗口所需的 event_id → chapter_no 映射。
        # 走同一连接避免额外 IO；DB 出错时回退到 None（窗口自动降级为关闭）。
        event_chapter_no_map: dict[str, int] | None = None
        if (
            events_window_chapters is not None
            and isinstance(events_window_chapters, int)
            and events_window_chapters >= 1
            and current_chapter_no is not None
        ):
            try:
                conn3 = get_connection(db_path)
                try:
                    event_chapter_no_map = _load_event_chapter_no_map(
                        conn3, project_id,
                    )
                finally:
                    conn3.close()
            except sqlite3.Error:
                event_chapter_no_map = None

        trimmed_snap, stats = _trim_snapshot_for_observer(
            snap,
            keep_recent_commits=keep_recent_commits,
            resolved_history_keep=resolved_history_keep,
            touched=touched,
            events_window_chapters=events_window_chapters,
            current_chapter_no=current_chapter_no,
            event_chapter_no_map=event_chapter_no_map,
        )
        # 体积量化（before/after JSON 字节数）
        try:
            stats["total_size_bytes_before"] = len(json.dumps(snap, ensure_ascii=False))
            stats["total_size_bytes_after"] = len(
                json.dumps(trimmed_snap, ensure_ascii=False)
            )
        except (TypeError, ValueError):
            stats["total_size_bytes_before"] = 0
            stats["total_size_bytes_after"] = 0

        payload["previous_state"] = trimmed_snap
        payload["snapshot_trim_stats"] = stats

    return payload
