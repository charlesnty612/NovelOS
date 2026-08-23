"""Context builders（Sprint 4-A）。

按 ``docs/agents/agent-contracts-v0.md`` §3.1 / §4.1 / §5.1 组装 Director / Writer / Observer 输入。

设计要点（MVP 简化版）：
- **不做 L0-L9 token 裁剪**：按字段全量塞入 context。完整 token 预算分配与
  层级压缩留待后续 Sprint（README 注明 deviation）。
- ``chapter.target_word_count`` 默认 2200，对齐 PRD §124 番茄单章 2000-2500。
- ``recent_prose`` 取上一章最新 draft 末尾 500 字（无 draft → 空字符串）。
- ``draft_text`` 取该章最新 draft 的 ``content`` 列（drafts 表 DDL line 274）。
- 全部按章节 DB 状态实时组装；不缓存。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_DEFAULT_TARGET_WORD_COUNT = 2200  # PRD §124 番茄单章 2000-2500

_DEFAULT_STYLE_CONSTRAINTS = {
    "language": "zh-Hans",
    "pov": "third_limited",
    "dialogue_ratio": 0.4,
    "forbidden_words": ["仿佛", "如同", "本章目标"],
}


def _parse_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _row_to_project(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _row_to_chapter(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["plan_json"] = _parse_json(d.get("plan_json")) or {}
    return d


def _latest_draft(conn: sqlite3.Connection, chapter_id: str) -> dict[str, Any] | None:
    """取该章最新 draft 行（按 created_at DESC）。"""
    row = conn.execute(
        """
        SELECT * FROM drafts
        WHERE chapter_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (chapter_id,),
    ).fetchone()
    return dict(row) if row else None


def _chapter_project_id(conn: sqlite3.Connection, chapter_id: str) -> str | None:
    row = conn.execute(
        "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)
    ).fetchone()
    return row["project_id"] if row else None


def _character_state_excerpts(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """characters JOIN 最新 state 行。"""
    rows = conn.execute(
        """
        SELECT c.character_id, c.name, c.role, c.core_json, c.visibility,
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
    out: list[dict[str, Any]] = []
    for r in rows:
        core = _parse_json(r["core_json"]) or {}
        state = _parse_json(r["latest_state_json"]) or {}
        out.append(
            {
                "character_id": r["character_id"],
                "name": r["name"],
                "role": r["role"],
                "core_traits_summary": core.get("personality") or "",
                "current_state": state,
                "knowledge_scope": r["visibility"],
                "core_json": core,
                "latest_state_version": r["latest_state_version"],
            }
        )
    return out


def _world_state_excerpts(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    locs = conn.execute(
        "SELECT location_id, name, statement, data_json FROM locations WHERE project_id = ? ORDER BY location_id ASC",
        (project_id,),
    ).fetchall()
    facs = conn.execute(
        "SELECT faction_id, name, statement, data_json FROM factions WHERE project_id = ? ORDER BY faction_id ASC",
        (project_id,),
    ).fetchall()
    rules = conn.execute(
        "SELECT world_rule_id, name, statement, data_json FROM world_rules "
        "WHERE project_id = ? ORDER BY world_rule_id ASC",
        (project_id,),
    ).fetchall()
    return {
        "current_time_in_story": None,  # 初始空；后续可由 world_changes(time) 维护
        "current_location": None,
        "locations": [
            {
                "location_id": r["location_id"],
                "name": r["name"],
                "statement": r["statement"],
                "data_json": _parse_json(r["data_json"]) or {},
            }
            for r in locs
        ],
        "active_factions": [
            {
                "faction_id": r["faction_id"],
                "name": r["name"],
                "statement": r["statement"],
                "data_json": _parse_json(r["data_json"]) or {},
            }
            for r in facs
        ],
        "world_rules_relevant": [
            {
                "world_rule_id": r["world_rule_id"],
                "name": r["name"],
                "statement": r["statement"],
            }
            for r in rules
        ],
    }


def _plot_graph_excerpt(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT event_id, type, status FROM plot_events
        WHERE project_id = ? AND status IN ('planned', 'recorded')
        ORDER BY event_id ASC LIMIT 50
        """,
        (project_id,),
    ).fetchall()
    return {
        "upcoming_planned_events": [
            {"event_id": r["event_id"], "type": r["type"], "status": r["status"]}
            for r in rows
        ],
        "unresolved_branches": [],
    }


def _hook_ledger_excerpt(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT hook_id, name, status, importance, expected_payoff_chapter_id, payoff_chapter_id
        FROM hooks
        WHERE project_id = ? AND status IN ('OPEN', 'ACTIVE', 'ESCALATED')
        ORDER BY hook_id ASC
        """,
        (project_id,),
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


def _narrative_debt_excerpt(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT debt_id, description, severity, deadline_chapter_id, status
        FROM narrative_debts
        WHERE project_id = ? AND status IN ('open', 'acknowledged')
        ORDER BY debt_id ASC
        """,
        (project_id,),
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


def _recent_prose_tail(db_path: str | Path, chapter_id: str, length: int = 500) -> str:
    """取上一章（chapter number 小一号的）最新 draft 末尾 length 字符；无则返回 ""。"""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "SELECT number, project_id FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        if cur is None:
            return ""
        prev_row = conn.execute(
            """
            SELECT chapter_id FROM chapters
            WHERE project_id = ? AND number < ?
            ORDER BY number DESC LIMIT 1
            """,
            (cur["project_id"], cur["number"]),
        ).fetchone()
        if prev_row is None:
            return ""
        draft_row = conn.execute(
            """
            SELECT content FROM drafts
            WHERE chapter_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (prev_row["chapter_id"],),
        ).fetchone()
        if draft_row is None:
            return ""
        text = draft_row["content"] or ""
        return text[-length:] if len(text) > length else text
    finally:
        conn.close()


def _director_plan_summary(plan_json: dict[str, Any]) -> dict[str, Any]:
    """从 chapters.plan_json 抽取 director_plan_summary（给 observer 用）。"""
    return {
        "chapter_goal": plan_json.get("chapter_goal"),
        "key_beats": plan_json.get("key_beats", []),
        "character_changes_planned": plan_json.get("character_changes_planned", []),
        "hook_handling": plan_json.get("hook_handling", []),
        "debt_handling": plan_json.get("debt_handling", []),
    }


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_director_input(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
    author_intent: str,
    *,
    target_word_count: int = _DEFAULT_TARGET_WORD_COUNT,
) -> dict[str, Any]:
    """组装 Director 输入（agent-contracts §3.1）。

    返回 dict 可直接 ``json.dumps`` 后作为 user message 传给 :func:`run_agent`。
    """
    conn = get_connection(db_path)
    try:
        proj_row = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        chap_row = conn.execute("SELECT * FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if proj_row is None:
            raise ValueError(f"project {project_id!r} not found")
        if chap_row is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        project = _row_to_project(proj_row)
        chapter = _row_to_chapter(chap_row)

        # story_state_snapshot
        from packages.core.story_state.service import StoryStateService

        snap = StoryStateService(db_path).get_current_state(project_id)
        state_version = int(snap.get("state_version") or 0)

        character_excerpts = _character_state_excerpts(conn, project_id)
        world_excerpts = _world_state_excerpts(conn, project_id)
        plot_excerpt = _plot_graph_excerpt(conn, project_id)
        hook_excerpt = _hook_ledger_excerpt(conn, project_id)
        debt_excerpt = _narrative_debt_excerpt(conn, project_id)
    finally:
        conn.close()

    return {
        "agent": "director",
        "prompt_version": "director:v1",
        "chapter": {
            "chapter_id": chapter_id,
            "title": chapter.get("title"),
            "target_word_count": target_word_count,
            "expected_role": "setup",
        },
        "project": {
            "project_id": project["project_id"],
            "name": project["name"],
            "genre": project.get("genre"),
            "premise": project.get("premise"),
        },
        "author_intent": {
            "raw": author_intent,
            "structured": None,
        },
        "story_state_snapshot": {
            "current_chapter": chapter.get("number"),
            "current_state_version": state_version,
            "active_characters": [c["character_id"] for c in character_excerpts],
            "primary_location": world_excerpts.get("current_location"),
            "recent_chapter_summaries": [],
        },
        "character_state_excerpts": character_excerpts,
        "world_state_excerpts": world_excerpts,
        "plot_graph_excerpt": plot_excerpt,
        "hook_ledger_excerpt": hook_excerpt,
        "narrative_debt_excerpt": debt_excerpt,
        "knowledge_permissions": {
            "your_visibility": ["AUTHOR", "DIRECTOR"],
            "forbidden_kinds": ["HIDDEN"],
        },
        "constraints": {
            "forbidden_topics": [],
            "must_include": [],
            "style_constraints_id": None,
        },
    }


def build_writer_input(
    db_path: str | Path,
    chapter_id: str,
    scene_plan: dict[str, Any],
    *,
    target_word_count: int = _DEFAULT_TARGET_WORD_COUNT,
) -> dict[str, Any]:
    """组装 Writer 输入（agent-contracts §4.1）。"""
    conn = get_connection(db_path)
    try:
        chap_row = conn.execute("SELECT * FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if chap_row is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        chapter = _row_to_chapter(chap_row)
        project_id = chapter["project_id"]
        character_excerpts = _character_state_excerpts(conn, project_id)
        world_excerpts = _world_state_excerpts(conn, project_id)
    finally:
        conn.close()

    director_plan = chapter.get("plan_json") or {}
    recent_prose_tail = _recent_prose_tail(db_path, chapter_id, 500)
    return {
        "agent": "writer",
        "prompt_version": "writer:v1",
        "chapter": {
            "chapter_id": chapter_id,
            "title": chapter.get("title"),
            "target_word_count": target_word_count,
            "expected_role": director_plan.get("expected_role") or "setup",
        },
        "director_plan": {
            "chapter_goal": director_plan.get("chapter_goal"),
            "core_conflict": director_plan.get("core_conflict"),
            "turning_point": director_plan.get("turning_point"),
            "key_beats": director_plan.get("key_beats", []),
            "notes_for_planner": director_plan.get("notes_for_planner"),
        },
        "scene_plan": scene_plan,
        "character_state_excerpts": character_excerpts,
        "world_state_excerpts": world_excerpts,
        "recent_prose": {
            "last_chapter_excerpt": recent_prose_tail,
            "last_scene_excerpt": "",
        },
        "retrieved_memory": [],
        "knowledge_permissions": {
            "your_visibility": ["WRITER", "PUBLIC", "VISIBLE"],
            "forbidden_kinds": ["HIDDEN"],
        },
        "style_constraints": dict(_DEFAULT_STYLE_CONSTRAINTS),
    }


def build_observer_input(
    db_path: str | Path,
    chapter_id: str,
    *,
    min_excerpt_chars_low_confidence: int = 80,
    max_changes_per_array: int = 50,
) -> dict[str, Any]:
    """组装 Observer 输入（agent-contracts §5.1）。"""
    conn = get_connection(db_path)
    try:
        chap_row = conn.execute("SELECT * FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if chap_row is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        chapter = _row_to_chapter(chap_row)
        project_id = chapter["project_id"]
        draft_row = _latest_draft(conn, chapter_id)
    finally:
        conn.close()

    from packages.core.story_state.service import StoryStateService

    snap = StoryStateService(db_path).get_current_state(project_id)
    state_version = int(snap.get("state_version") or 0)

    plan_json = chapter.get("plan_json") or {}
    return {
        "agent": "observer",
        "prompt_version": "observer:v1",
        "chapter": {
            "chapter_id": chapter_id,
            "title": chapter.get("title"),
            "draft_text": (draft_row or {}).get("content") or "",
            "scene_ids": [],
        },
        "previous_state_version": state_version,
        "previous_state": snap,
        "director_plan_summary": _director_plan_summary(plan_json),
        "knowledge_permissions": {
            "your_visibility": ["AUTHOR", "DIRECTOR"],
            "forbidden_kinds": ["HIDDEN"],
        },
        "config": {
            "min_excerpt_chars_low_confidence": min_excerpt_chars_low_confidence,
            "max_changes_per_array": max_changes_per_array,
        },
    }


__all__ = [
    "build_director_input",
    "build_writer_input",
    "build_observer_input",
]
