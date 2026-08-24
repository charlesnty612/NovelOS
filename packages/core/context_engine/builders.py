"""Context builders（Sprint 4-A + Sprint 15/V1.3 + V2.0 Wave B 任务二 + V2.0 Wave C 任务一/二）。

按 ``docs/agents/agent-contracts-v0.md`` §3.1 / §4.1 / §5.1 组装 Director / Writer / Observer 输入。

设计要点（MVP 简化版）：
- **不做 L0-L9 token 裁剪**：按字段全量塞入 context。完整 token 预算分配与
  层级压缩留待后续 Sprint（README 注明 deviation）。
- ``chapter.target_word_count`` 默认 2200，对齐 PRD §124 番茄单章 2000-2500。
- ``recent_prose`` 取上一章最新 draft 末尾 500 字（无 draft → 空字符串）。
- ``draft_text`` 取该章最新 draft 的 ``content`` 列（drafts 表 DDL line 274）。
- 全部按章节 DB 状态实时组装；L0/L1 装配结果进程内缓存（V2.0 Wave C 任务二，
  见本模块 ``_cache_*`` 内部实现 + README）。

Sprint 15 / V1.3 新增：
- Writer 注入 ``author_style_samples``：取该项目最近创建的 ≤2 篇、每篇截断 ≤1000 字，
  引导 writer 模仿「句式 / 用词 / 节奏」（非内容）。
- Director 注入 ``open_foreshadow_list`` 的 overdue 阈值项目级可配：
  从 ``projects.foreshadow_overdue_chapters`` 读取，取不到 / NULL 回退 30。
- SQL 截断修复：open_foreshadow_list 直接 ``ORDER BY overdue_first, importance DESC,
  introduced ASC LIMIT 20``，把 overdue 判定推到 SQL，去掉旧「预取 60 再内存排序」
  截断边界 bug（>60 条伏笔时 overdue 项不再丢失）。

V2.0 Wave B 任务二新增：
- 设定条件触发动态注入（对标 NovelAI Lorebook 关键词触发 / NovelCrafter Codex 4 态）。
- canon 实体（characters / locations / factions）按 ``inject_mode`` 列三态注入：
  - ``auto`` + 命中实体名/别名 → 完整注入；
  - ``auto`` + 未命中 → 降级为一行摘要（name + role/statement）；
  - ``always`` → 完整注入（无视命中）；
  - ``never`` → 不注入（仅留在 preview 列表中标记 suppressed）。
- 命中检测扫描面：当前章节 plan_json（chapter_goal / core_conflict / turning_point /
  key_beats / character_changes_planned）+ 前一章尾段 300 字（``previous_chapter_tail``）。
- 检测算法：实体 name 或 aliases 任一词面命中（大小写不敏感的子串匹配；中文直接子串）；
  aliases 词面须 ≥2 字符避免误触发（短词如「的」「是」会被忽略）。
- 回退策略：章节 plan_json 为空时（无章节计划），auto 默认全量注入，保兼容；
  always / never 严格按配置执行（即使无计划文本也按模式生效）。
- L0 world_rules 保持常驻不变——只对实体类做条件化（世界规则属硬设定）。

V2.0 Wave C 任务一新增：
- 召回混合层（章节正文 FTS5）：按当前章节计划文本从 chapter_fts 召回 top-3
  相关历史片段，写入 director / writer 输入顶层 ``recalled_passages`` 键。
- 关键词提取口径：中文 2-gram + 实体名整体 token + 英文分词 + 停用词过滤
  （详见 packages/core/retrieval/service.py + README）。
- 召回片段每段截断 ≤300 字，带 ``chapter_id`` / ``chapter_no`` / ``snippet`` /
  ``rank`` 字段。无索引 / 无命中 → 空 list（不阻断装配）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading as _threading
from pathlib import Path
from typing import Any, Iterable

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

# V2.0 Wave B 任务二：触发键相关常量
_INJECT_MODES = ("auto", "always", "never")  # 与 0010 CHECK 对齐
# 别名命中最小长度：避免「的」「是」「了」这种常用词误触发
_MIN_ALIAS_LEN = 2
# 摘要行最大字符数（用于 auto+未命中 降级后的单行 description）
_SUMMARY_LINE_MAX_CHARS = 80


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


def _load_aliases(raw: Any) -> list[str]:
    """解析 ``aliases`` 列（JSON 字符串数组）。失败 / 空 → ``[]``。

    兼容：合法 list / JSON 字符串 / None / 非法值均按"无别名"处理；不抛错。
    """
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if isinstance(x, (str, int, float)) and str(x).strip()]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [
                str(x).strip()
                for x in parsed
                if isinstance(x, (str, int, float)) and str(x).strip()
            ]
    return []


def _load_inject_mode(raw: Any) -> str:
    """解析 ``inject_mode`` 列；非法值回退 ``auto``（保行为一致）。"""
    if isinstance(raw, str) and raw in _INJECT_MODES:
        return raw
    return "auto"


def _is_triggered(
    name: str | None,
    aliases: Iterable[str],
    corpus: str,
    *,
    name_hit: dict[str, bool] | None = None,
) -> bool:
    """实体是否被 corpus 触发（任一关键词命中即可）。

    - 命中：name / aliases 任一词面在 corpus 中出现（大小写不敏感子串匹配；corpus 与
      候选词都被自动 lower()，调用方无须预处理）。
    - aliases 词面长度 < ``_MIN_ALIAS_LEN`` 跳过（避免常用词误命中）。
    - name / aliases 为空 → 未触发。

    副作用：可把每个候选词的命中状态写到 ``name_hit``（调试 / 测试用；生产不读）。
    """
    if not corpus:
        return False
    corpus_lower = corpus.lower()
    candidates: list[str] = []
    if isinstance(name, str) and name.strip():
        candidates.append(name.strip())
    for a in aliases:
        if isinstance(a, str) and len(a.strip()) >= _MIN_ALIAS_LEN:
            candidates.append(a.strip())
    if not candidates:
        return False
    for c in candidates:
        hit = c.lower() in corpus_lower
        if name_hit is not None:
            name_hit[c] = hit
        if hit:
            return True
    return False


def _build_trigger_corpus(
    chapter_row: sqlite3.Row | None,
    *,
    previous_tail_text: str = "",
) -> str:
    """组装触发检测扫描面 = 当前章节 plan_json 关键文本 + 前一章尾段 300 字。

    无章节行 / 无 plan_json → 返回空串（调用方按"无计划文本 → 全注入"回退）。
    """
    parts: list[str] = []
    if chapter_row is not None:
        try:
            plan = json.loads(chapter_row["plan_json"]) if chapter_row["plan_json"] else {}
        except (json.JSONDecodeError, TypeError):
            plan = {}
        if isinstance(plan, dict):
            for key in (
                "chapter_goal",
                "core_conflict",
                "turning_point",
                "notes_for_planner",
            ):
                v = plan.get(key)
                if isinstance(v, str) and v.strip():
                    parts.append(v)
            beats = plan.get("key_beats")
            if isinstance(beats, list):
                for b in beats:
                    if isinstance(b, str) and b.strip():
                        parts.append(b)
            changes = plan.get("character_changes_planned")
            if isinstance(changes, list):
                for c in changes:
                    if isinstance(c, str) and c.strip():
                        parts.append(c)
    if previous_tail_text:
        parts.append(previous_tail_text)
    return "\n".join(parts)


def _summarize_entity(
    *,
    kind: str,
    name: str,
    entity_id: str,
    role: str | None = None,
    statement: str | None = None,
) -> dict[str, Any]:
    """auto + 未命中 时的降级摘要行（保留可识别信息 + 一句话描述）。

    - character: name + role（如 "主角（protagonist）"）
    - location/faction: name + statement（"王城 — 帝国首都"）
    - 截断到 ``_SUMMARY_LINE_MAX_CHARS`` 字符；其余字段剥离。
    - 包含 ``character_id`` / ``location_id`` / ``faction_id`` 便于预览 / 反查。
    """
    id_field = {
        "character": "character_id",
        "location": "location_id",
        "faction": "faction_id",
    }.get(kind, "id")
    line = name or ""
    if kind == "character" and role:
        line = f"{line}（{role}）"
    elif kind in ("location", "faction") and statement:
        st = statement.strip()
        if st:
            line = f"{line} — {st[:_SUMMARY_LINE_MAX_CHARS]}"
    line = line[:_SUMMARY_LINE_MAX_CHARS]
    return {
        id_field: entity_id,
        "name": name,
        "role": role if kind == "character" else None,
        "summary_line": line,
        "injection": "summary",
    }


def _apply_injection_policy(
    *,
    kind: str,
    name: str,
    entity_id: str,
    aliases: list[str],
    inject_mode: str,
    full_excerpt: dict[str, Any],
    summary_overrides: dict[str, Any] | None,
    trigger_corpus_lower: str,
    trigger_corpus_empty: bool,
) -> tuple[str, dict[str, Any]]:
    """对单个 canon 实体应用注入策略，返回 ``(status, payload)``。

    status: ``'full' | 'summary' | 'suppressed'``。
    payload:
    - ``full``: 完整 excerpt（叠加 ``_injection: 'full'`` 字段便于 preview 标记）。
    - ``summary``: 摘要 dict（来自 ``summary_overrides`` 或 :func:`_summarize_entity`）。
    - ``suppressed``: ``{id_field: entity_id, name, injection: 'suppressed'}``
      供 preview 用；不入 ctx 顶层。

    回退策略：trigger_corpus_empty=True（无章节计划文本）→ auto 全部按 full 注入（保兼容）；
    always / never 严格按配置执行。
    """
    if inject_mode == "always":
        out = dict(full_excerpt)
        out["_injection"] = "full"
        return "full", out
    if inject_mode == "never":
        id_field = {
            "character": "character_id",
            "location": "location_id",
            "faction": "faction_id",
        }.get(kind, "id")
        return "suppressed", {id_field: entity_id, "name": name, "injection": "suppressed"}
    # inject_mode == "auto"
    if trigger_corpus_empty:
        # 无章节计划文本：保兼容，全注入（README 注明回退策略）
        out = dict(full_excerpt)
        out["_injection"] = "full"
        return "full", out
    if _is_triggered(name, aliases, trigger_corpus_lower):
        out = dict(full_excerpt)
        out["_injection"] = "full"
        return "full", out
    # 未命中 → 降级为一行摘要
    if summary_overrides is None:
        summary_overrides = {
            "role": full_excerpt.get("role"),
            "statement": full_excerpt.get("statement"),
        }
    summary = _summarize_entity(
        kind=kind,
        name=name,
        entity_id=entity_id,
        role=summary_overrides.get("role"),
        statement=summary_overrides.get("statement"),
    )
    summary["_injection"] = "summary"
    return "summary", summary


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


def _character_state_excerpts(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    trigger_corpus: str | None = None,
) -> list[dict[str, Any]]:
    """characters JOIN 最新 state 行。

    V2.0 Wave B 任务二：
    - 始终读 ``aliases`` / ``inject_mode`` 两列（0010 加的）；
    - 传 ``trigger_corpus`` 时按注入策略三态过滤；不传 → 全部按 full 注入（向后兼容）。
    - 每条目额外带 ``_injection: 'full'|'summary'|'suppressed'`` 字段供 preview 标记。
    """
    rows = conn.execute(
        """
        SELECT c.character_id, c.name, c.role, c.core_json, c.visibility,
               c.aliases, c.inject_mode,
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
    apply_policy = trigger_corpus is not None
    corpus_lower = (trigger_corpus or "").lower() if apply_policy else ""
    corpus_empty = apply_policy and not trigger_corpus  # type: ignore[truthiness-function]
    out: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for r in rows:
        core = _parse_json(r["core_json"]) or {}
        state = _parse_json(r["latest_state_json"]) or {}
        full_excerpt: dict[str, Any] = {
            "character_id": r["character_id"],
            "name": r["name"],
            "role": r["role"],
            "core_traits_summary": core.get("personality") or "",
            "current_state": state,
            "knowledge_scope": r["visibility"],
            "core_json": core,
            "latest_state_version": r["latest_state_version"],
        }
        if not apply_policy:
            out.append(full_excerpt)
            continue
        aliases = _load_aliases(r["aliases"])
        inject_mode = _load_inject_mode(r["inject_mode"])
        status, payload = _apply_injection_policy(
            kind="character",
            name=r["name"],
            entity_id=r["character_id"],
            aliases=aliases,
            inject_mode=inject_mode,
            full_excerpt=full_excerpt,
            summary_overrides={"role": r["role"]},
            trigger_corpus_lower=corpus_lower,
            trigger_corpus_empty=corpus_empty,
        )
        if status == "suppressed":
            suppressed.append(payload)
        else:
            out.append(payload)
    if apply_policy and suppressed:
        out.append({"_suppressed_characters": suppressed})
    return out


def _world_state_excerpts(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    trigger_corpus: str | None = None,
) -> dict[str, Any]:
    """locations / factions / world_rules 全量组装。

    V2.0 Wave B 任务二：
    - locations / factions 应用触发策略（0010 加了 aliases + inject_mode）；
    - world_rules 保持常驻不变（PRD 视世界规则为硬设定）；
    - 不传 ``trigger_corpus`` → 全部 full 注入（向后兼容）。
    - 每条目带 ``_injection`` 字段；suppressed 项统一汇集到顶层 ``_suppressed_{kind}`` 列表。
    """
    locs = conn.execute(
        "SELECT location_id, name, statement, data_json, aliases, inject_mode "
        "FROM locations WHERE project_id = ? ORDER BY location_id ASC",
        (project_id,),
    ).fetchall()
    facs = conn.execute(
        "SELECT faction_id, name, statement, data_json, aliases, inject_mode "
        "FROM factions WHERE project_id = ? ORDER BY faction_id ASC",
        (project_id,),
    ).fetchall()
    rules = conn.execute(
        "SELECT world_rule_id, name, statement, data_json FROM world_rules "
        "WHERE project_id = ? ORDER BY world_rule_id ASC",
        (project_id,),
    ).fetchall()
    apply_policy = trigger_corpus is not None
    corpus_lower = (trigger_corpus or "").lower() if apply_policy else ""
    corpus_empty = apply_policy and not trigger_corpus  # type: ignore[truthiness-function]

    def _loc_payload(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "location_id": r["location_id"],
            "name": r["name"],
            "statement": r["statement"],
            "data_json": _parse_json(r["data_json"]) or {},
        }

    def _fac_payload(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "faction_id": r["faction_id"],
            "name": r["name"],
            "statement": r["statement"],
            "data_json": _parse_json(r["data_json"]) or {},
        }

    loc_out: list[dict[str, Any]] = []
    loc_supp: list[dict[str, Any]] = []
    for r in locs:
        full = _loc_payload(r)
        if not apply_policy:
            loc_out.append(full)
            continue
        aliases = _load_aliases(r["aliases"])
        inject_mode = _load_inject_mode(r["inject_mode"])
        status, payload = _apply_injection_policy(
            kind="location",
            name=r["name"],
            entity_id=r["location_id"],
            aliases=aliases,
            inject_mode=inject_mode,
            full_excerpt=full,
            summary_overrides={"statement": r["statement"]},
            trigger_corpus_lower=corpus_lower,
            trigger_corpus_empty=corpus_empty,
        )
        if status == "suppressed":
            loc_supp.append(payload)
        else:
            loc_out.append(payload)

    fac_out: list[dict[str, Any]] = []
    fac_supp: list[dict[str, Any]] = []
    for r in facs:
        full = _fac_payload(r)
        if not apply_policy:
            fac_out.append(full)
            continue
        aliases = _load_aliases(r["aliases"])
        inject_mode = _load_inject_mode(r["inject_mode"])
        status, payload = _apply_injection_policy(
            kind="faction",
            name=r["name"],
            entity_id=r["faction_id"],
            aliases=aliases,
            inject_mode=inject_mode,
            full_excerpt=full,
            summary_overrides={"statement": r["statement"]},
            trigger_corpus_lower=corpus_lower,
            trigger_corpus_empty=corpus_empty,
        )
        if status == "suppressed":
            fac_supp.append(payload)
        else:
            fac_out.append(payload)

    result: dict[str, Any] = {
        "current_time_in_story": None,  # 初始空；后续可由 world_changes(time) 维护
        "current_location": None,
        "locations": loc_out,
        "active_factions": fac_out,
        "world_rules_relevant": [
            {
                "world_rule_id": r["world_rule_id"],
                "name": r["name"],
                "statement": r["statement"],
            }
            for r in rules
        ],
    }
    if apply_policy:
        if loc_supp:
            result["_suppressed_locations"] = loc_supp
        if fac_supp:
            result["_suppressed_factions"] = fac_supp
    return result


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
# V2.0 Wave C 任务一：召回混合层（章节正文 FTS5）
# ---------------------------------------------------------------------------


def _build_plan_corpus(plan_json: dict[str, Any]) -> str:
    """把 chapters.plan_json 拼成触发召回的纯文本（不含前章尾段）。

    用于 :func:`_recall_passages` 关键词提取；与 ``_build_trigger_corpus`` 的
    区别是后者混入前章尾段用于实体触发键；本函数是 FTS 召回专用，避免
    ``previous_chapter_tail`` 自身变成召回关键词（会让所有片段命中）。
    """
    if not isinstance(plan_json, dict):
        return ""
    parts: list[str] = []
    for key in ("chapter_goal", "core_conflict", "turning_point", "notes_for_planner"):
        v = plan_json.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    beats = plan_json.get("key_beats")
    if isinstance(beats, list):
        for b in beats:
            if isinstance(b, str) and b.strip():
                parts.append(b)
            elif isinstance(b, dict):
                # key_beats 也可能是结构化对象（含 purpose / involved_characters 等）
                for k in ("purpose", "conflict", "turn"):
                    v = b.get(k)
                    if isinstance(v, str) and v.strip():
                        parts.append(v)
    changes = plan_json.get("character_changes_planned")
    if isinstance(changes, list):
        for c in changes:
            if isinstance(c, str) and c.strip():
                parts.append(c)
            elif isinstance(c, dict):
                for k in ("name", "from", "to", "purpose"):
                    v = c.get(k)
                    if isinstance(v, str) and v.strip():
                        parts.append(v)
    return "\n".join(parts)


def _extract_plan_entity_names(plan_json: dict[str, Any]) -> list[str]:
    """从 chapters.plan_json 抽取可能涉及的实体名（角色 / 地点 / 势力）。

    仅作为关键词补充信号；不替代 character/location/faction 表的权威。
    """
    if not isinstance(plan_json, dict):
        return []
    out: list[str] = []
    for c in plan_json.get("character_changes_planned") or []:
        if isinstance(c, dict):
            n = c.get("name")
            if isinstance(n, str) and n.strip():
                out.append(n.strip())
        elif isinstance(c, str) and c.strip():
            out.append(c.strip())
    return out


def _recall_passages(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
    plan_json: dict[str, Any],
) -> list[dict[str, Any]]:
    """按章节计划文本从 chapter_fts 召回 top-3 相关历史片段。

    失败（FTS 虚表不存在 / 表达式非法 / 无索引）→ 空 list，不抛错。
    装配口径：FTS 召回是 best-effort，召回不到时由 ``recent_chapter_summaries``
    + ``previous_chapter_tail`` 兜底。
    """
    corpus = _build_plan_corpus(plan_json)
    entity_names = _extract_plan_entity_names(plan_json)
    if not corpus and not entity_names:
        return []
    try:
        # 懒导入：FTS5 在迁移未落地时 import 失败不应阻断其它装配
        from packages.core.retrieval import extract_keywords, search
    except Exception:  # noqa: BLE001 —— 降级
        return []
    try:
        kws = extract_keywords(corpus, entity_names)
    except Exception:  # noqa: BLE001 —— 降级
        kws = []
    if not kws:
        return []
    query = " ".join(kws)
    try:
        return search(
            db_path,
            project_id,
            query,
            current_chapter_id=chapter_id,
        )
    except Exception:  # noqa: BLE001 —— FTS 虚表不存在等降级
        return []


# ---------------------------------------------------------------------------
# V2.0 Wave C 任务二 + P1-1 修复：L0/L1 装配结果进程内缓存
# ---------------------------------------------------------------------------
#
# 键 = (project_id, state_version, chapter_no, role, content_fp) 其中 role ∈
# {"director", "writer"} 用于区分 L0/L1 不同装配。L2 (observer) 不缓存——
# 它读 draft_text，commit 期间持续变化；缓存它会带来一致性问题。
#
# V2.0 Wave C P1-1：键追加内容指纹（第 5 元），解决脏命中：
#   - director 键 content_fp = sha256(plan_json 原文)[:16]（None → 'none'）；
#     plan_json 被 UPDATE 但 state_version 不变时，键自然失效。
#   - writer 键 content_fp = sha256(json.dumps(scene_plan, sort_keys=True))[:16]；
#     scene_plan 是会话级信号，不同 scene 必须区分缓存。
#   - 指纹计算失败（不可序列化）⇒ content_fp = 'uncached' ⇒ 跳过缓存（直走
#     uncached），避免脏命中（保护脏命中场景的可用性 > 缓存命中）。
#
# 失效策略：
#   - state_version 变化 ⇒ 整键失效（剧情状态推进，state-driven 字段如
#     recent_chapter_summaries / open_foreshadow_list 全部需重算）；
#   - chapter_no 变化 ⇒ 键整体失效（不同章节的 recall / trigger corpus 不同）；
#   - content_fp 变化 ⇒ 键失效（内容维度上的变更）；
#   - commit 后通过 :func:`_invalidate_cache_for_chapter` 显式清掉该 chapter
#     键（兜底：state_version 推进也会带走它——P1-1 修复后兜底主要用于
#     "plan_json 被 UPDATE 但 state_version 未变"的边界场景）。
#
# 上限：默认 256 条（dict 顺序淘汰最近最少写入）；线程安全用 ``threading.Lock``。
# 测试可通过 ``_cache_reset()`` 单测隔离。
#
# 不缓存任何含 sqlite 连接 / 副作用对象（仅纯 dict）。

_CACHE_MAX_SIZE = 256
# V2.0 Wave C P1-1：装配缓存键加入内容指纹维度。
# - director 键第 5 元 = sha256(plan_json 原文)[:16]（plan_json None → 'none'）；
# - writer 键第 5 元 = sha256(json.dumps(scene_plan, sort_keys=True))[:16]（None → 'none'）；
# - 内容指纹计算失败（不可序列化）→ 跳过缓存（直接走 uncached），避免脏命中。
# 失效仍以 state_version + chapter_no 为主线；commit 完成后调
# ``_invalidate_cache_for_chapter`` 显式兜底（state_version 推进也会带走它）。
_assembly_cache: dict[tuple[str, int, int, str, str], dict[str, Any]] = {}
_cache_lock = _threading.Lock()

# 内容指纹长度（sha256 hexdigest 前 16 字符 = 64 bit；冲突概率可忽略）
_FINGERPRINT_LEN = 16
# 「不可序列化 / None」的指纹占位
_FINGERPRINT_NONE = "none"
# 「跳过缓存」的指纹占位（与 'none' 区分；调用方据此判走 uncached）
_FINGERPRINT_UNCACHED = "uncached"


def _fingerprint_plan_json(plan_json_raw: Any) -> str:
    """计算 chapters.plan_json 原文的稳定指纹（sha256 前 16 字符）。

    - 输入是 DB 读出的原文 str（或已解析对象；统一按原文口径处理）。
    - 不可序列化 / 异常 → 返回 ``_FINGERPRINT_UNCACHED``，调用方据此跳过缓存。
    """
    try:
        if plan_json_raw is None:
            return _FINGERPRINT_NONE
        if isinstance(plan_json_raw, (dict, list)):
            normalized = json.dumps(plan_json_raw, sort_keys=True, ensure_ascii=False)
        else:
            normalized = str(plan_json_raw)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]
    except Exception:  # noqa: BLE001 —— 不可序列化时跳过缓存
        return _FINGERPRINT_UNCACHED


def _fingerprint_scene_plan(scene_plan: Any) -> str:
    """计算 scene_plan 的稳定指纹（json.dumps sort_keys=True 的 sha256 前 16）。

    - None → ``_FINGERPRINT_NONE``；scene 是会话级信号——不同 scene 必须区分缓存。
    - 不可序列化 → ``_FINGERPRINT_UNCACHED``，调用方据此跳过缓存。
    """
    try:
        if scene_plan is None:
            return _FINGERPRINT_NONE
        normalized = json.dumps(scene_plan, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]
    except Exception:  # noqa: BLE001
        return _FINGERPRINT_UNCACHED


def _cache_reset() -> None:
    """测试辅助：清空装配缓存。"""
    global _assembly_cache
    with _cache_lock:
        _assembly_cache = {}


def _invalidate_cache_for_chapter(project_id: str, chapter_no: int) -> int:
    """显式失效指定 (project_id, chapter_no) 的所有 role 键。返回失效条数。

    V2.0 Wave C P1-1：5 元键 (project_id, state_version, chapter_no, role, content_fp)，
    第 3 元索引仍是 chapter_no。chapter_commit 成功后兜底调用以避免脏命中。
    """
    global _assembly_cache
    removed = 0
    with _cache_lock:
        keys_to_drop = [
            k for k in _assembly_cache
            if k[0] == project_id and k[2] == chapter_no
        ]
        for k in keys_to_drop:
            del _assembly_cache[k]
            removed += 1
    return removed


def _cache_get(key: tuple[str, int, int, str, str]) -> dict[str, Any] | None:
    with _cache_lock:
        return _assembly_cache.get(key)


def _cache_put(key: tuple[str, int, int, str, str], value: dict[str, Any]) -> None:
    """写入缓存；超过上限按 dict 插入顺序淘汰最旧（dict 有序）。"""
    global _assembly_cache
    with _cache_lock:
        if len(_assembly_cache) >= _CACHE_MAX_SIZE:
            # 淘汰最早写入的键（Python 3.7+ dict 保插入顺序）
            try:
                oldest_key = next(iter(_assembly_cache))
                del _assembly_cache[oldest_key]
            except StopIteration:
                pass
        _assembly_cache[key] = value


# ---------------------------------------------------------------------------
# Reference canon 注入（Sprint 11 下半）
# ---------------------------------------------------------------------------

# 顶层 director_input 注入键 + 截断上限；缺字段容错跳过。
_REFERENCE_CANON_SPINE_CAP = 20
_REFERENCE_CANON_PAYOFF_CAP = 30


def _reference_canon_excerpt(
    conn: sqlite3.Connection, project_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
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


def _build_director_input_uncached(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
    author_intent: str,
    target_word_count: int,
    *,
    plan_json_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """无缓存版 director 装配；build_director_input 用本函数 + 缓存包装。"""
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
        # V2.0 Wave B 任务二：触发检测需 previous_chapter_tail 文本，须先取。
        previous_chapter_tail = _previous_chapter_tail(
            conn, project_id=project_id, current_chapter_no=current_chapter_no,
            length=300,
        )
        # V2.0 Wave B 任务二：触发扫描面 = 当前章节 plan_json + 前一章尾段。
        # 章节行已加载，直接复用 chap_row（节省一次 query）。
        trigger_corpus = _build_trigger_corpus(
            chap_row,
            previous_tail_text=str(previous_chapter_tail.get("tail_text") or ""),
        )
        # 触发检测下放到 character/world excerpt（应用 aliases/inject_mode 策略）。
        character_excerpts = _character_state_excerpts(
            conn, project_id, trigger_corpus=trigger_corpus,
        )
        world_excerpts = _world_state_excerpts(
            conn, project_id, trigger_corpus=trigger_corpus,
        )
        plot_excerpt = _plot_graph_excerpt(conn, project_id)
        hook_excerpt = _hook_ledger_excerpt(conn, project_id)
        debt_excerpt = _narrative_debt_excerpt(conn, project_id)
        reference_canon_inject, reference_canon_audit = _reference_canon_excerpt(conn, project_id)
    finally:
        conn.close()

    # V2.0 Wave C 任务一：召回混合层（FTS5）
    # 按当前章节 plan_json 关键词从 chapter_fts 召回 top-3 相关历史片段。
    plan_for_recall = plan_json_override if plan_json_override is not None else (chapter.get("plan_json") or {})
    recalled_passages = _recall_passages(db_path, project_id, chapter_id, plan_for_recall)

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
            # V2.0 Wave B 任务二：只把真实注入的实体 ID 计入 active_characters；
            # suppressed 项（never 模式）不进 active_characters。
            "active_characters": [
                c["character_id"]
                for c in character_excerpts
                if "character_id" in c
            ],
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
    # V2.0 Wave C 任务一：FTS 召回片段。无索引 / 无命中时为空 list。
    payload["recalled_passages"] = recalled_passages

    return payload


def build_director_input(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
    author_intent: str,
    *,
    target_word_count: int = _DEFAULT_TARGET_WORD_COUNT,
) -> dict[str, Any]:
    """组装 Director 输入（agent-contracts §3.1 + V2.0 Wave C 任务一 召回 + 任务二 缓存）。

    缓存（V2.0 Wave C 任务二）：L0/L1 装配结果按
    ``(project_id, state_version, chapter_no, "director", plan_fp)`` 键做进程内缓存；
    同一 (project, state_version, chapter, plan_json 内容) 第二次调用直接返回缓存 dict。

    V2.0 Wave C P1-1 修复：键追加 ``plan_fp``（chapters.plan_json 原文 sha256[:16]），
    解决 plan_json UPDATE 后 state_version 不变时的脏命中。``plan_fp == 'uncached'``
    时跳过缓存（不可序列化场景），避免脏命中。

    详细说明见 :func:`_build_director_input_uncached`（无缓存装配实现）与模块顶部注释。
    """
    # 先读章节号 + state_version + plan_json 原文用于缓存键（不命中再全量装配）
    chapter_no, state_version, plan_raw = _peek_chapter_no_state_version_plan(
        db_path, project_id, chapter_id,
    )
    plan_fp = _fingerprint_plan_json(plan_raw)
    cache_key = (project_id, state_version, chapter_no, "director", plan_fp)
    if plan_fp != _FINGERPRINT_UNCACHED:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    payload = _build_director_input_uncached(
        db_path, project_id, chapter_id, author_intent, target_word_count,
    )
    if plan_fp != _FINGERPRINT_UNCACHED:
        _cache_put(cache_key, payload)
    return payload


def _build_writer_input_uncached(
    db_path: str | Path,
    chapter_id: str,
    scene_plan: dict[str, Any],
    target_word_count: int,
) -> dict[str, Any]:
    """无缓存版 writer 装配。"""
    conn = get_connection(db_path)
    try:
        chap_row = conn.execute("SELECT * FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if chap_row is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        chapter = _row_to_chapter(chap_row)
        project_id = chapter["project_id"]
        # Sprint 15 / V1.3：取项目最近 ≤2 篇文风样例，每篇截断 ≤1000 字。
        style_samples = _author_style_samples(conn, project_id)
    finally:
        conn.close()

    director_plan = chapter.get("plan_json") or {}
    recent_prose_tail = _recent_prose_tail(db_path, chapter_id, 500)
    # V2.0 Wave B 任务二：Writer 触发扫描面 = chapter plan 关键文本 + scene_plan + 前一章尾段。
    # Writer 不读 previous_chapter_tail（由 director 装配），但 recent_prose_tail 是等价物；
    # scene_plan 是 director 没看过的额外信号（含本场参与角色 / 地点）。
    scene_text_parts: list[str] = []
    if isinstance(scene_plan, dict):
        for key in ("purpose", "location", "conflict", "turn"):
            v = scene_plan.get(key)
            if isinstance(v, str) and v.strip():
                scene_text_parts.append(v)
        chars = scene_plan.get("characters")
        if isinstance(chars, list):
            scene_text_parts.extend([str(x) for x in chars if isinstance(x, (str, int, float))])
    trigger_corpus = _build_trigger_corpus(chap_row, previous_tail_text=recent_prose_tail)
    if scene_text_parts:
        if trigger_corpus:
            trigger_corpus = trigger_corpus + "\n" + "\n".join(scene_text_parts)
        else:
            trigger_corpus = "\n".join(scene_text_parts)

    # 触发检测下放到 character/world excerpt（应用 aliases/inject_mode 策略）。
    conn2 = get_connection(db_path)
    try:
        character_excerpts = _character_state_excerpts(
            conn2, project_id, trigger_corpus=trigger_corpus,
        )
        world_excerpts = _world_state_excerpts(
            conn2, project_id, trigger_corpus=trigger_corpus,
        )
    finally:
        conn2.close()

    # V2.0 Wave C 任务一：writer 也注入 recalled_passages（与 director 共享同一关键词
    # 召回——scene_plan 不参与提取，避免 scene 局部信号污染跨章呼应）。
    recalled_passages = _recall_passages(
        db_path, project_id, chapter_id, director_plan,
    )

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
        # V2.0 Wave C 任务一：FTS 召回片段。无索引 / 无命中时为空 list。
        "recalled_passages": recalled_passages,
        "retrieved_memory": [],
        "knowledge_permissions": {
            "your_visibility": ["WRITER", "PUBLIC", "VISIBLE"],
            "forbidden_kinds": ["HIDDEN"],
        },
        "style_constraints": dict(_DEFAULT_STYLE_CONSTRAINTS),
    }


def _peek_chapter_no_state_version(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
) -> tuple[int, int]:
    """轻量读 chapters.number + story_state.state_version；用于缓存键预判。

    返回 ``(chapter_no, state_version)``；任一缺失 → (0, 0)。
    """
    chapter_no = 0
    state_version = 0
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT number FROM chapters WHERE chapter_id = ?", (chapter_id,),
        ).fetchone()
        if row is not None:
            try:
                chapter_no = int(row["number"] or 0)
            except (TypeError, ValueError):
                chapter_no = 0
        try:
            from packages.core.story_state.service import StoryStateService
            snap = StoryStateService(db_path).get_current_state(project_id)
            state_version = int(snap.get("state_version") or 0)
        except Exception:  # noqa: BLE001 —— 兜底
            state_version = 0
    finally:
        conn.close()
    return chapter_no, state_version


def _peek_chapter_no_state_version_plan(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
) -> tuple[int, int, Any]:
    """V2.0 Wave C P1-1：director 缓存键的轻量预读。

    返回 ``(chapter_no, state_version, plan_json_raw)``。
    ``plan_json_raw`` 是 DB 原文 str（不做解析，避免破坏键稳定性）；
    缺失 → ``None``。任一异常 → 对应字段安全 fallback，不抛错。
    """
    chapter_no = 0
    state_version = 0
    plan_raw: Any = None
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT number, plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,),
        ).fetchone()
        if row is not None:
            try:
                chapter_no = int(row["number"] or 0)
            except (TypeError, ValueError):
                chapter_no = 0
            # DB 原文：保留为字符串（可能是 JSON 字面量或 NULL）
            plan_raw = row["plan_json"]
        try:
            from packages.core.story_state.service import StoryStateService
            snap = StoryStateService(db_path).get_current_state(project_id)
            state_version = int(snap.get("state_version") or 0)
        except Exception:  # noqa: BLE001 —— 兜底
            state_version = 0
    finally:
        conn.close()
    return chapter_no, state_version, plan_raw


def build_writer_input(
    db_path: str | Path,
    chapter_id: str,
    scene_plan: dict[str, Any],
    *,
    target_word_count: int = _DEFAULT_TARGET_WORD_COUNT,
) -> dict[str, Any]:
    """组装 Writer 输入（agent-contracts §4.1 + Sprint 15/V1.3 author_style_samples
    + V2.0 Wave B 任务二 条件触发动态注入 + V2.0 Wave C 任务一 召回 + 任务二 缓存）。

    V2.0 Wave C P1-1 修复：缓存键追加 ``scene_fp``（scene_plan 序列化指纹）；
    不同 scene_plan 不再共享同一缓存条目——避免传不同 scene 时命中陈旧 writer 输入。
    不可序列化时 ``scene_fp == 'uncached'`` → 跳过缓存（直接走 uncached）。
    """
    project_id = _peek_project_id_from_chapter(db_path, chapter_id)
    chapter_no, state_version = _peek_chapter_no_state_version(
        db_path, project_id, chapter_id,
    )
    scene_fp = _fingerprint_scene_plan(scene_plan)
    cache_key = (project_id or "", state_version, chapter_no, "writer", scene_fp)
    if scene_fp != _FINGERPRINT_UNCACHED:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
    payload = _build_writer_input_uncached(
        db_path, chapter_id, scene_plan, target_word_count,
    )
    if scene_fp != _FINGERPRINT_UNCACHED:
        _cache_put(cache_key, payload)
    return payload


def _peek_project_id_from_chapter(db_path: str | Path, chapter_id: str) -> str | None:
    """轻量读 chapters.project_id；用于缓存键预判。"""
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001
        return None
    try:
        row = conn.execute(
            "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,),
        ).fetchone()
        return row["project_id"] if row else None
    finally:
        conn.close()


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
