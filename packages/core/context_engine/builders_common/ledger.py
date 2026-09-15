"""台账与检索类数据收集（拆分自 builders_common.py，2026-09-13 V4.0）。

- 台账视图：``_hook_ledger_excerpt`` / ``_narrative_debt_excerpt``（V3.9 批次 2.2 加 cap）；
- 待核销视图：``_open_foreshadow_list``（overdue 判定 + 排序全部下推 SQL，项目级阈值可配）；
- 剧情图：``_plot_graph_excerpt``（planned/recorded 事件 + ACTIVE 分支）；
- commit delta 触达实体集合：``_collect_touched_entity_ids``（writer / observer 知识权限用）。

本模块只用 stdlib，不 import 兄弟子模块。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _plot_graph_excerpt(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT event_id, type, status FROM plot_events
        WHERE project_id = ? AND status IN ('planned', 'recorded')
        ORDER BY event_id ASC LIMIT 50
        """,
        (project_id,),
    ).fetchall()
    # P2 Context Engine：未解决分支 = 仍处于 ACTIVE（尚未 MERGED/DISCARDED/ARCHIVED）的分支。
    # branches.status 枚举见 0001_init.sql + 0005_branches_archived_status.sql。
    # V3.9 批次 2.2：原为「全部 ACTIVE 无上限」；补 LIMIT（branch_id 升序 = 确定性稳定序）。
    branch_rows = conn.execute(
        """
        SELECT branch_id, name, parent_branch_id, base_state_version, status
        FROM branches
        WHERE project_id = ? AND status = 'ACTIVE'
        ORDER BY branch_id ASC
        LIMIT ?
        """,
        (project_id, _UNRESOLVED_BRANCHES_CAP),
    ).fetchall()
    return {
        "upcoming_planned_events": [
            {"event_id": r["event_id"], "type": r["type"], "status": r["status"]}
            for r in rows
        ],
        "unresolved_branches": [
            {
                "branch_id": r["branch_id"],
                "name": r["name"],
                "parent_branch_id": r["parent_branch_id"],
                "base_state_version": r["base_state_version"],
                "status": r["status"],
            }
            for r in branch_rows
        ],
    }


def _hook_ledger_excerpt(
    conn: sqlite3.Connection, project_id: str, *, current_chapter_no: int | None = None,
) -> list[dict[str, Any]]:
    """伏笔台账视图（V3.9 批次 2.2 加上限）：planted 状态清单，按 importance DESC + id 稳定序取 20。

    与 :func:`_open_foreshadow_list` 的分工（两者并存，语义不同，不合并）：
    - 本函数 = **台账视图**：`hooks` 表原始状态机字段（status / importance /
      expected_payoff_chapter / payoff_chapter_id），不算 overdue——回答「项目里还有
      哪些伏笔挂着」。
    - `_open_foreshadow_list` = **待核销视图**：LEFT JOIN chapters 计算 introduced
      chapter_no 与 overdue，按 overdue 优先排序——回答「这一章该收哪些伏笔」。
    二者字段与排序口径都不同（台账按重要性，待核销按逾期），消费方（director prompt /
    preview）分别取用；合并会丢掉一方语义，故仅各自加 cap。

    ``current_chapter_no``（2026-09-15 加）：只注入**本章或更早**引入的伏笔。
    传 None 保持原口径（全量台账，preview 等场景用）。
    """
    rows = conn.execute(
        """
        SELECT h.hook_id, h.name, h.status, h.importance,
               h.expected_payoff_chapter_id, h.payoff_chapter_id
        FROM hooks h
        LEFT JOIN chapters ch ON ch.chapter_id = h.introduced_chapter_id
        WHERE h.project_id = ? AND h.status IN ('OPEN', 'ACTIVE', 'ESCALATED')
          -- 位置过滤（2026-09-15）：只给**本章或更早**引入的伏笔（详见
          -- _open_foreshadow_list 同款注释：无此条件时重产早期章会被后文倒灌）。
          AND (? IS NULL OR ch.number IS NULL OR ch.number <= ?)
        ORDER BY h.importance DESC, h.hook_id ASC
        LIMIT ?
        """,
        (project_id, current_chapter_no, current_chapter_no, _HOOK_LEDGER_CAP),
    ).fetchall()
    return [
        {
            "hook_id": r["hook_id"],
            "name": r["name"],
            "status": r["status"],
            "importance": r["importance"],
            "expected_payoff_chapter": r["expected_payoff_chapter_id"],
            "payoff_chapter_id": r["payoff_chapter_id"],
        }
        for r in rows
    ]


def _narrative_debt_excerpt(
    conn: sqlite3.Connection, project_id: str, *, current_chapter_no: int | None = None,
) -> list[dict[str, Any]]:
    """叙事债务台账（V3.9 批次 2.2 加上限）：severity DESC + id 稳定序取 20。

    ``current_chapter_no``（2026-09-15 加）：只注入**本章或更早**挂上的债务，
    避免重产早期章时被后文债务倒灌（口径同 _hook_ledger_excerpt / _open_foreshadow_list）。
    """
    rows = conn.execute(
        """
        SELECT d.debt_id, d.description, d.severity, d.deadline_chapter_id, d.status
        FROM narrative_debts d
        LEFT JOIN chapters ch ON ch.chapter_id = d.created_chapter_id
        WHERE d.project_id = ? AND d.status IN ('open', 'acknowledged')
          AND (? IS NULL OR ch.number IS NULL OR ch.number <= ?)
        ORDER BY d.severity DESC, d.debt_id ASC
        LIMIT ?
        """,
        (project_id, current_chapter_no, current_chapter_no, _DEBT_LEDGER_CAP),
    ).fetchall()
    return [
        {
            "debt_id": r["debt_id"],
            "description": r["description"],
            "severity": r["severity"],
            "deadline_chapter": r["deadline_chapter_id"],
            "status": r["status"],
        }
        for r in rows
    ]


# 台账类注入（hook_ledger_excerpt / narrative_debt_excerpt）条数上限。
# 超限按「重要性降序 + id 兜底」保留头部；与 open_foreshadow_list 的分工见
# ``_hook_ledger_excerpt`` docstring。
_HOOK_LEDGER_CAP = 20
_DEBT_LEDGER_CAP = 20
# plot_graph_excerpt.unresolved_branches 条数上限（按 branch_id 升序稳定序）。
_UNRESOLVED_BRANCHES_CAP = 20
# 开放伏笔清单最大条数（按 overdue 优先 + 重要性降序）。
_OPEN_HOOKS_CAP = 20
# overdue 阈值（fallback 常量）：引入章节距当前 chapter_no > 阈值即视为逾期。
# Sprint 15 升级为项目级可配：见 ``_project_overdue_chapters``；该常量保留为 fallback
# 与 DDL DEFAULT 30 对齐。
_FORESHADOW_OVERDUE_CHAPTERS = 30
# hook 状态机语义分组（planted = OPEN/ACTIVE/ESCALATED；paid_off = RESOLVED）。
# ABANDONED 不进开放清单。
_PLANTED_HOOK_STATUSES = ("OPEN", "ACTIVE", "ESCALATED")


def _project_overdue_chapters(
    conn: sqlite3.Connection, project_id: str,
) -> int:
    """读 ``projects.foreshadow_overdue_chapters``；列缺失 / NULL → fallback 30。

    与 0008 DDL DEFAULT 30 + ``_FORESHADOW_OVERDUE_CHAPTERS`` 对齐：
    旧库升级（0001~0007 已有库）走 0008 ALTER ADD COLUMN DEFAULT 30 → 取数无 NULL。
    """
    try:
        row = conn.execute(
            "SELECT foreshadow_overdue_chapters FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # 极老库（0008 未跑）可能没列 → fallback 常量。
        return _FORESHADOW_OVERDUE_CHAPTERS
    if row is None:
        return _FORESHADOW_OVERDUE_CHAPTERS
    val = row["foreshadow_overdue_chapters"]
    if val is None:
        return _FORESHADOW_OVERDUE_CHAPTERS
    try:
        ival = int(val)
    except (TypeError, ValueError):
        return _FORESHADOW_OVERDUE_CHAPTERS
    return ival if ival > 0 else _FORESHADOW_OVERDUE_CHAPTERS


def _open_foreshadow_list(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    current_chapter_no: int | None,
    overdue_chapters: int | None = None,
) -> list[dict[str, Any]]:
    """开放伏笔清单（planted 状态伏笔）。

    Sprint 15 / V1.3 SQL 修复：
    - overdue 判定 + 排序全部下推 SQL（``CASE WHEN overdue THEN 0 ELSE 1 END``），
      LIMIT 直接取 ``_OPEN_HOOKS_CAP``；不再「预取 60 再内存排序」——伏笔 > 60 条时
      overdue 项不再被截断丢失（V1.2 审查遗留 P2-1）。
    - overdue 阈值 ``overdue_chapters`` 优先从调用方传入（项目级可配）；None → fallback
      ``_FORESHADOW_OVERDUE_CHAPTERS``（30）。

    排序（全部 SQL ORDER BY）：
    1. overdue 优先（逾期伏笔最需要提醒）；
    2. importance DESC；
    3. introduced_chapter_no ASC（埋设更早的优先）；
    4. hook_id ASC（兜底稳定）。

    返回 ``[{"hook_id": str, "name": str, "status": str,
           "introduced_chapter_no": int|None, "importance": float,
           "overdue": bool, "chapters_since_introduced": int|None}]``。
    """
    threshold = (
        int(overdue_chapters)
        if overdue_chapters is not None and int(overdue_chapters) > 0
        else _FORESHADOW_OVERDUE_CHAPTERS
    )
    placeholders = ",".join("?" for _ in _PLANTED_HOOK_STATUSES)
    # 用 CASE 把 overdue 推到 SQL（章节号比较而非 julianday）。
    # SQLite 无 IF 表达式，CASE WHEN 是官方支持的等效语法。
    sql = f"""
        SELECT h.hook_id, h.name, h.status, h.importance, h.introduced_chapter_id,
               ch.number AS introduced_chapter_no,
               CASE
                   WHEN ch.number IS NULL OR ? IS NULL
                       THEN 0
                   WHEN (? - ch.number) > ?
                       THEN 1
                   ELSE 0
               END AS overdue_flag
        FROM hooks h
        LEFT JOIN chapters ch ON ch.chapter_id = h.introduced_chapter_id
        WHERE h.project_id = ?
          AND h.status IN ({placeholders})
          -- 位置过滤（2026-09-15）：只注入**本章或更早**引入的伏笔。
          -- 改造前无此条件 → 重产早期章（如 ch1）时，后文（ch17/ch21）才引入的钩子
          -- 会倒灌进 planner 输入，实证后果：ch1 的 plan 引用了 ch21 的第二世身份钩子，
          -- 正文里出现「三年之约已经兑现，破屋锚点再次确认」这类弧末术语。
          -- introduced_chapter_id 为 NULL（项目级伏笔）时无法判位，保留注入。
          AND (? IS NULL OR ch.number IS NULL OR ch.number <= ?)
        ORDER BY overdue_flag DESC,
                 h.importance DESC,
                 CASE WHEN ch.number IS NULL THEN 1 ELSE 0 END ASC,
                 ch.number ASC,
                 h.hook_id ASC
        LIMIT ?
    """
    params: list[Any] = [
        current_chapter_no, current_chapter_no, threshold,
        project_id, *_PLANTED_HOOK_STATUSES,
        current_chapter_no, current_chapter_no, _OPEN_HOOKS_CAP,
    ]
    rows = conn.execute(sql, params).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        intro_no = d.get("introduced_chapter_no")
        intro_no_int = int(intro_no) if intro_no is not None else None
        chapters_since: int | None = None
        overdue = bool(d.get("overdue_flag"))
        if intro_no_int is not None and current_chapter_no is not None:
            chapters_since = max(0, int(current_chapter_no) - intro_no_int)
        out.append({
            "hook_id": d["hook_id"],
            "name": d.get("name") or d["hook_id"],
            "status": d.get("status") or "OPEN",
            "introduced_chapter_no": intro_no_int,
            "importance": float(d.get("importance") or 0.5),
            "overdue": overdue,
            "chapters_since_introduced": chapters_since,
        })
    return out


def _collect_touched_entity_ids(
    conn: sqlite3.Connection, project_id: str, *, keep_recent_commits: int
) -> dict[str, set[str]]:
    """从最近 ``keep_recent_commits`` 个 commit 的 delta payload 中收集被 touch 的实体 ID。

    返回结构：
        {
            "characters": set[character_id],
            "locations": set[location_id],
            "factions": set[faction_id],
            "world_rules": set[world_rule_id],
            "hooks": set[hook_id],
            "debts": set[debt_id],
            "events": set[event_id],
            "relationships": set[relationship_id],
            "relationship_keys": set[str],   # "from::to::type"
        }

    实现要点：
    - 仅读 ``commits`` + ``state_deltas`` 表（不读 story_states 全文，避免与全量快照
      重复 IO）；无 commits 时返回全空集合。
    - ``state_deltas.payload_json`` 由 commits.py 落库；解析时按 7 个 change 数组遍历。
    - ``world_id`` 按 ``world_kind`` 路由到 locations / factions / world_rules。
    - relationship 用合成 key（``from_character_id::to_character_id::relation_type``）
      ——snapshot 中每个 relationship 条目都有 ``relationship_id``，但 schema 中
      relationship_change 无该字段。两者并行收集。
    """
    out: dict[str, set[str]] = {
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
    if keep_recent_commits <= 0:
        return out
    rows = conn.execute(
        """
        SELECT c.commit_id, c.chapter_id, c.resulting_state_version,
               c.timestamp, d.payload_json
        FROM commits c
        LEFT JOIN state_deltas d ON d.delta_id = c.delta_id
        WHERE c.project_id = ?
        ORDER BY c.resulting_state_version DESC
        LIMIT ?
        """,
        (project_id, keep_recent_commits),
    ).fetchall()

    for r in rows:
        raw = r["payload_json"]
        if not raw:
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        for ch in payload.get("character_changes") or []:
            if not isinstance(ch, dict):
                continue
            cid = ch.get("character_id") or ch.get("target_id")
            if isinstance(cid, str) and cid:
                out["characters"].add(cid)

        for ch in payload.get("world_changes") or []:
            if not isinstance(ch, dict):
                continue
            wid = ch.get("world_id") or ch.get("target_id")
            if not (isinstance(wid, str) and wid):
                continue
            kind = ch.get("world_kind")
            if kind == "location":
                out["locations"].add(wid)
            elif kind == "faction":
                out["factions"].add(wid)
            elif kind == "rule":
                out["world_rules"].add(wid)

        for ch in payload.get("relationship_changes") or []:
            if not isinstance(ch, dict):
                continue
            f = ch.get("from_character_id")
            t = ch.get("to_character_id")
            rt = ch.get("relation_type")
            if not (isinstance(f, str) and isinstance(t, str) and isinstance(rt, str)):
                continue
            key = f"{f}::{t}::{rt}"
            out["relationship_keys"].add(key)
            rid = ch.get("relationship_id")
            if isinstance(rid, str) and rid:
                out["relationships"].add(rid)

        for ch in payload.get("new_events") or []:
            if not isinstance(ch, dict):
                continue
            eid = ch.get("event_id") or ch.get("target_id")
            if isinstance(eid, str) and eid:
                out["events"].add(eid)

        for ch in payload.get("resolved_hooks") or []:
            if not isinstance(ch, dict):
                continue
            hid = ch.get("hook_id") or ch.get("target_id")
            if isinstance(hid, str) and hid:
                out["hooks"].add(hid)
        for ch in payload.get("new_hooks") or []:
            if not isinstance(ch, dict):
                continue
            hid = ch.get("hook_id") or ch.get("target_id")
            if isinstance(hid, str) and hid:
                out["hooks"].add(hid)

        for ch in payload.get("debt_changes") or []:
            if not isinstance(ch, dict):
                continue
            did = ch.get("debt_id") or ch.get("target_id")
            if isinstance(did, str) and did:
                out["debts"].add(did)

    return out


# hook / debt 状态常量：与 snapshot.py _load_hooks/_load_debts 口径一致；
# hooks.status 五态枚举：OPEN/ACTIVE/ESCALATED/RESOLVED/ABANDONED
# narrative_debts.status 四态枚举：open/acknowledged/paid/forgiven
_HOOK_OPEN_STATUSES = frozenset({"OPEN", "ACTIVE", "ESCALATED"})
_DEBT_OPEN_STATUSES = frozenset({"open", "acknowledged"})
