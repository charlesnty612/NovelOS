"""Context builders（Sprint 4-A + Sprint 15/V1.3）。

按 ``docs/agents/agent-contracts-v0.md`` §3.1 / §4.1 / §5.1 组装 Director / Writer / Observer 输入。

设计要点（MVP 简化版）：
- **不做 L0-L9 token 裁剪**：按字段全量塞入 context。完整 token 预算分配与
  层级压缩留待后续 Sprint（README 注明 deviation）。
- ``chapter.target_word_count`` 默认 2200，对齐 PRD §124 番茄单章 2000-2500。
- ``recent_prose`` 取上一章最新 draft 末尾 500 字（无 draft → 空字符串）。
- ``draft_text`` 取该章最新 draft 的 ``content`` 列（drafts 表 DDL line 274）。
- 全部按章节 DB 状态实时组装；不缓存。

Sprint 15 / V1.3 新增：
- Writer 注入 ``author_style_samples``：取该项目最近创建的 ≤2 篇、每篇截断 ≤1000 字，
  引导 writer 模仿「句式 / 用词 / 节奏」（非内容）。
- Director 注入 ``open_foreshadow_list`` 的 overdue 阈值项目级可配：
  从 ``projects.foreshadow_overdue_chapters`` 读取，取不到 / NULL 回退 30。
- SQL 截断修复：open_foreshadow_list 直接 ``ORDER BY overdue_first, importance DESC,
  introduced ASC LIMIT 20``，把 overdue 判定推到 SQL，去掉旧「预取 60 再内存排序」
  截断边界 bug（>60 条伏笔时 overdue 项不再丢失）。
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


# ---------------------------------------------------------------------------
# Sprint 14：摘要链 + 前章尾段 + 开放伏笔清单（Context Engine L1 扩展）
# ---------------------------------------------------------------------------

# 摘要链最近章数；超预算截断时优先砍最旧摘要。
_RECENT_SUMMARY_CAP = 5
# 摘要每条字符上限（与 chapter_summaries.summary 列口径对齐）。
_RECENT_SUMMARY_PER_CHARS = 200
# 开放伏笔清单最大条数（按 overdue 优先 + 重要性降序）。
_OPEN_HOOKS_CAP = 20
# overdue 阈值（fallback 常量）：引入章节距当前 chapter_no > 阈值即视为逾期。
# Sprint 15 升级为项目级可配：见 ``_project_overdue_chapters``；该常量保留为 fallback
# 与 DDL DEFAULT 30 对齐。
_FORESHADOW_OVERDUE_CHAPTERS = 30
# hook 状态机语义分组（planted = OPEN/ACTIVE/ESCALATED；paid_off = RESOLVED）。
# ABANDONED 不进开放清单。
_PLANTED_HOOK_STATUSES = ("OPEN", "ACTIVE", "ESCALATED")


# ---------------------------------------------------------------------------
# Sprint 15 / V1.3：项目级 overdue 阈值读取
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Sprint 15 / V1.3：作者文风样例注入 writer
# ---------------------------------------------------------------------------

# writer 输入注入样例条数（取最近 N 篇）；每篇截断上限。
_STYLE_SAMPLES_CAP = 2
_STYLE_SAMPLE_PER_CHARS = 1000
# 引导语：注入 writer 输入时前缀；常量便于对齐测试与未来 i18n。
_AUTHOR_STYLE_SAMPLES_INSTRUCTION = (
    "以下为作者本人散文样例，请模仿其句式、用词与节奏（非内容）。"
)


def _recent_chapter_summaries(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    current_chapter_no: int | None,
) -> list[dict[str, Any]]:
    """取当前章之前最近 ``_RECENT_SUMMARY_CAP`` 章的摘要（chapter_no 倒序）。

    返回 ``[{"chapter_no": int, "summary": str, "chapter_id": str}]``。
    ``current_chapter_no`` 用于排除当前章自身（避免「自己摘要自己」）；传 None 时不过滤。
    超预算截断在调用方（按总 token 配额）执行，本函数只负责取数。
    """
    sql = """
        SELECT chapter_id, chapter_no, summary
        FROM chapter_summaries
        WHERE project_id = ?
          AND {extra}
        ORDER BY chapter_no DESC
        LIMIT ?
    """.format(extra=("(chapter_no < ?)" if current_chapter_no is not None else "1=1"))
    params: list[Any] = [project_id]
    if current_chapter_no is not None:
        params.append(int(current_chapter_no))
    params.append(_RECENT_SUMMARY_CAP)
    rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row_d = dict(r)
        text = (row_d.get("summary") or "").strip()
        if not text:
            continue
        out.append({
            "chapter_id": row_d["chapter_id"],
            "chapter_no": int(row_d["chapter_no"]),
            "summary": text[:_RECENT_SUMMARY_PER_CHARS],
        })
    return out


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
        ORDER BY overdue_flag DESC,
                 h.importance DESC,
                 CASE WHEN ch.number IS NULL THEN 1 ELSE 0 END ASC,
                 ch.number ASC,
                 h.hook_id ASC
        LIMIT ?
    """
    params: list[Any] = [
        current_chapter_no, current_chapter_no, threshold,
        project_id, *_PLANTED_HOOK_STATUSES, _OPEN_HOOKS_CAP,
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


def _author_style_samples(
    conn: sqlite3.Connection,
    project_id: str,
) -> list[dict[str, Any]]:
    """取该项目最近 ``_STYLE_SAMPLES_CAP`` 篇文风样例，每篇截断 ≤ ``_STYLE_SAMPLE_PER_CHARS`` 字。

    返回 ``[{"sample_id": str, "title": str, "excerpt": str}]``；
    无样例时返回空 list（writer 装配按空态处理）。
    """
    rows = conn.execute(
        "SELECT sample_id, title, content FROM author_style_samples "
        "WHERE project_id = ? "
        "ORDER BY created_at DESC, sample_id DESC "
        "LIMIT ?",
        (project_id, _STYLE_SAMPLES_CAP),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        content = d.get("content") or ""
        excerpt = content[:_STYLE_SAMPLE_PER_CHARS] if len(content) > _STYLE_SAMPLE_PER_CHARS else content
        out.append({
            "sample_id": d["sample_id"],
            "title": d.get("title") or d["sample_id"],
            "excerpt": excerpt,
        })
    return out


def _previous_chapter_tail(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    current_chapter_no: int,
    length: int = 300,
) -> dict[str, Any]:
    """取前一章（chapter_no 小一号）的最新 draft 末尾 length 字（用于 L1 "前章尾段原文"）。

    返回 ``{"chapter_no": int, "chapter_id": str, "tail_text": str}``；
    无前章 → 空 dict（与 _recent_prose_tail 行为对齐，避免上游判空复杂度）。
    """
    prev = conn.execute(
        """
        SELECT chapter_id, number FROM chapters
        WHERE project_id = ? AND number < ?
        ORDER BY number DESC LIMIT 1
        """,
        (project_id, current_chapter_no),
    ).fetchone()
    if prev is None:
        return {}
    pd = dict(prev)
    draft = conn.execute(
        """
        SELECT content FROM drafts
        WHERE chapter_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (pd["chapter_id"],),
    ).fetchone()
    if draft is None:
        return {}
    text = (dict(draft).get("content") or "")
    if not text:
        return {}
    return {
        "chapter_no": int(pd["number"]),
        "chapter_id": pd["chapter_id"],
        "tail_text": text[-length:] if len(text) > length else text,
    }


def _truncate_summaries_to_token_budget(
    items: list[dict[str, Any]],
    *,
    available_tokens: int,
    token_divisor: int = 4,
) -> tuple[list[dict[str, Any]], bool]:
    """按 token 预算截断摘要链。

    策略：先砍最旧摘要（index 末尾 → 即 chapter_no 最小）。
    返回 ``(items_kept, truncated_bool)``。
    """
    if available_tokens <= 0:
        return [], bool(items)
    kept = list(items)
    while kept:
        cost = max(1, len(json.dumps(kept, ensure_ascii=False)) // token_divisor)
        if cost <= available_tokens:
            return kept, len(kept) < len(items)
        kept.pop()  # 砍最旧（最末尾）
    return kept, bool(items)


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
# Reference canon 注入（Sprint 11 下半）
# ---------------------------------------------------------------------------

# 顶层 director_input 注入键 + 截断上限；缺字段容错跳过。
_REFERENCE_CANON_SPINE_CAP = 20
_REFERENCE_CANON_PAYOFF_CAP = 30


def _reference_canon_excerpt(conn: sqlite3.Connection, project_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """查该项目最新 active reference_canon（按 created_at DESC）。

    返回 (director_inject, audit_payload) 元组：
    - director_inject：注入到 director_input 的 reference_canon 键（None 表示无 canon）。
    - audit_payload：溯源审计 dict（含 canon_id + consumed_fields），落到 ctx 顶层
      ``_reference_canon_consumed``，随 ctx 进入 workflow run 的 checkpoint_json。

    设计：
    - MVP 单参照系（多书加权合并策略见 docs/reference-canon/reference-canon-v0.md §4.1 OV-2，defer）。
    - canon_json 解析失败 → 容错返回 (None, None)，不抛错。
    - 缺字段（logline/spine/payoff_list/rhythm）→ 跳过该字段，consumed_fields 不计。
    """
    row = conn.execute(
        """
        SELECT canon_id, canon_json
        FROM reference_canons
        WHERE project_id = ? AND status = 'active'
        ORDER BY created_at DESC, canon_id DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None, None

    canon_id = row["canon_id"]
    raw_canon_json = row["canon_json"] or "{}"
    try:
        cj = json.loads(raw_canon_json)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(cj, dict):
        return None, None

    consumed: list[str] = []
    inject: dict[str, Any] = {"canon_id": canon_id}

    logline = cj.get("logline")
    if isinstance(logline, str) and logline.strip():
        inject["logline"] = logline
        consumed.append("logline")

    spine = cj.get("spine")
    if isinstance(spine, list):
        inject["spine"] = spine[:_REFERENCE_CANON_SPINE_CAP]
        consumed.append("spine")

    payoff_list = cj.get("payoff_list")
    if isinstance(payoff_list, list):
        inject["payoff_list"] = payoff_list[:_REFERENCE_CANON_PAYOFF_CAP]
        consumed.append("payoff_list")

    rhythm = cj.get("rhythm")
    if isinstance(rhythm, dict):
        inject["rhythm"] = rhythm
        consumed.append("rhythm")

    audit = {"canon_id": canon_id, "consumed_fields": consumed}
    return inject, audit


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

    Sprint 11 下半扩展（不在 contracts §3.1 权威契约内；参见
    ``docs/reference-canon/reference-canon-v0.md`` §4.1）：

    - 若该项目存在 active reference_canon（按 created_at DESC 取 1），则 director
      输入 JSON 增加顶层键 ``reference_canon``：含 ``canon_id`` / ``logline`` /
      ``spine``（截前 20 条）/ ``payoff_list``（截前 30 条）/ ``rhythm``。
      缺字段容错跳过，无 canon 时行为与现状完全一致（不加键）。
    - 顶层键 ``_reference_canon_consumed`` 记溯源审计 ``{canon_id, consumed_fields}``，
      由 chapter_plan pipeline 把整个 ctx dict 写入 workflow run 的
      ``checkpoint_json``，对齐 §6.3 溯源审计要求。
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
        reference_canon_inject, reference_canon_audit = _reference_canon_excerpt(conn, project_id)
        # Sprint 14：摘要链 + 前章尾段 + 开放伏笔清单（任务书 §A / §B）。
        # recent_chapter_summaries 按 chapter_no 倒序，最多 _RECENT_SUMMARY_CAP；
        # open_foreshadow_list 按 overdue 优先 + importance DESC；含 overdue 计算属性。
        # previous_chapter_tail 是 L1 「前章尾段原文」补充（与 writer.recent_prose 互补，
        # director 用以规划下章衔接）。三源均按 token 预算截断：摘要链按"先砍最旧"。
        current_chapter_no = int(chapter.get("number") or 0)
        # Sprint 15 / V1.3：项目级 overdue 阈值（projects.foreshadow_overdue_chapters）。
        overdue_threshold = _project_overdue_chapters(conn, project_id)
        recent_summaries_raw = _recent_chapter_summaries(
            conn, project_id, current_chapter_no=current_chapter_no or None,
        )
        # MVP token 预算：摘要链单独按 800 token 上限截断（≈ 3200 字符；保守避免抢 L2 配额）。
        recent_summaries, _ = _truncate_summaries_to_token_budget(
            recent_summaries_raw, available_tokens=800,
        )
        open_foreshadow = _open_foreshadow_list(
            conn, project_id,
            current_chapter_no=current_chapter_no or None,
            overdue_chapters=overdue_threshold,
        )
        previous_chapter_tail = _previous_chapter_tail(
            conn, project_id=project_id, current_chapter_no=current_chapter_no,
            length=300,
        )
    finally:
        conn.close()

    payload: dict[str, Any] = {
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

    if reference_canon_inject is not None:
        payload["reference_canon"] = reference_canon_inject
    if reference_canon_audit is not None:
        payload["_reference_canon_consumed"] = reference_canon_audit
    # Sprint 14：摘要链 + 前章尾段 + 开放伏笔清单。
    # 沿用 agent-contracts §3.1「不在权威契约内」的扩展键惯例；无 chapter_summaries 行 /
    # 无 planted 状态伏笔 / 无前章 → 给空列表 / 空 dict，调用方按空态处理。
    payload["recent_chapter_summaries"] = recent_summaries
    payload["previous_chapter_tail"] = previous_chapter_tail
    payload["open_foreshadow_list"] = open_foreshadow

    return payload


def build_writer_input(
    db_path: str | Path,
    chapter_id: str,
    scene_plan: dict[str, Any],
    *,
    target_word_count: int = _DEFAULT_TARGET_WORD_COUNT,
) -> dict[str, Any]:
    """组装 Writer 输入（agent-contracts §4.1 + Sprint 15/V1.3 author_style_samples）。"""
    conn = get_connection(db_path)
    try:
        chap_row = conn.execute("SELECT * FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if chap_row is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        chapter = _row_to_chapter(chap_row)
        project_id = chapter["project_id"]
        character_excerpts = _character_state_excerpts(conn, project_id)
        world_excerpts = _world_state_excerpts(conn, project_id)
        # Sprint 15 / V1.3：取项目最近 ≤2 篇文风样例，每篇截断 ≤1000 字。
        style_samples = _author_style_samples(conn, project_id)
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
        # Sprint 15 / V1.3：作者文风样例注入。空 list 时 writer 按空态处理。
        # 引导语作为顶层 instruction，与 sample 列表解耦，便于测试 / 未来 i18n。
        "author_style_samples": {
            "instruction": _AUTHOR_STYLE_SAMPLES_INSTRUCTION,
            "samples": style_samples,
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
