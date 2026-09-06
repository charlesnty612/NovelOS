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


def _extract_sensory_anchors(locs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 locations 已解析的 data_json 中提取感官锚点（不加 DDL 的兜底方案）。

    优先级：
    1. ``data_json.sensory_anchors`` 为 list 时直接透传（标准扩展点，未来可在
       locations.data_json 中维护专用感官字段而不改 schema）。
    2. 否则从 ``data_json`` 中常见感官字段（sensory_details / atmosphere / smell /
       sound / light / texture / temperature）组合成一段 anchor_text。
    3. 仍无则回退到 ``statement`` 一句话陈述。

    当前 schema（0001_init.sql）locations 表无专用感官列，因此本函数是
    「零 DDL」约束下的兼容实现：有则取、无则空 list，不抛错。
    """
    anchors: list[dict[str, Any]] = []
    for loc in locs:
        if not isinstance(loc, dict):
            continue
        lid = loc.get("location_id")
        name = loc.get("name")
        data = loc.get("data_json") or {}
        if not isinstance(data, dict):
            data = {}
        raw_anchors = data.get("sensory_anchors")
        if isinstance(raw_anchors, list):
            for item in raw_anchors:
                entry: dict[str, Any] = {"location_id": lid, "location_name": name}
                if isinstance(item, dict):
                    entry.update(item)
                elif isinstance(item, str):
                    entry["anchor_text"] = item
                else:
                    continue
                anchors.append(entry)
            continue
        parts: list[str] = []
        for key in (
            "sensory_details",
            "atmosphere",
            "smell",
            "sound",
            "light",
            "texture",
            "temperature",
        ):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(f"{key}: {val.strip()}")
        statement = loc.get("statement")
        if not parts and isinstance(statement, str) and statement.strip():
            parts.append(f"statement: {statement.strip()}")
        if parts:
            anchors.append(
                {
                    "location_id": lid,
                    "location_name": name,
                    "anchor_text": "；".join(parts),
                }
            )
    return anchors


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

    P2 Context Engine：
    - ``sensory_anchors`` 从 locations.data_json 解析（零 DDL 方案）。
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
        # P2 Context Engine：感官锚点零 DDL 方案——从已注入 locations 的 data_json 解析。
        # 未命中/降级为 summary 的 location 因 data_json 为空而不贡献锚点。
        "sensory_anchors": _extract_sensory_anchors(loc_out),
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
    # P2 Context Engine：未解决分支 = 仍处于 ACTIVE（尚未 MERGED/DISCARDED/ARCHIVED）的分支。
    # branches.status 枚举见 0001_init.sql + 0005_branches_archived_status.sql。
    branch_rows = conn.execute(
        """
        SELECT branch_id, name, parent_branch_id, base_state_version, status
        FROM branches
        WHERE project_id = ? AND status = 'ACTIVE'
        ORDER BY branch_id ASC
        """,
        (project_id,),
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


def _inject_scene_word_budget(
    scene_plan: dict[str, Any] | None, target_word_count: int,
) -> dict[str, Any]:
    """为每个 scene 注入 ``target_words`` 预算，透传给 writer。

    来源：scene_planner-v1 §6 Rule 7 要求每个 scene 必填 ``target_words``（整数，
    总和 = target_word_count 的 90~100%）。但降级 stub / 旧版 prompt 可能不填，
    这里做兜底：缺值场景按等分补齐（首场景补余数），已填则按"总数 90~110% 内
    归一化"重算——避免模型被自己瞎填的总和误导。

    输出 scene_plan 永远带 ``scenes[*].target_words`` 字段；target_word_count<=0
    时不补（无预算可言）。不影响 ``scene_id / purpose / characters`` 等既有
    字段；只做浅拷贝，不破坏原 scene_plan。
    """
    if not isinstance(scene_plan, dict):
        return {"scenes": []} if not isinstance(scene_plan, dict) else scene_plan  # type: ignore[return-value]
    scenes_raw = scene_plan.get("scenes")
    if not isinstance(scenes_raw, list) or not scenes_raw:
        return scene_plan
    if target_word_count <= 0:
        # 无预算：原样返回（让 writer 走默认 ±15% 硬带）。
        return scene_plan

    scenes: list[dict[str, Any]] = [s for s in scenes_raw if isinstance(s, dict)]
    n = len(scenes)
    # 已声明的 target_words 收集（视作相对权重）
    declared: list[int | None] = []
    for s in scenes:
        tw = s.get("target_words")
        if isinstance(tw, int) and tw > 0:
            declared.append(tw)
        elif isinstance(tw, float) and tw.is_integer() and int(tw) > 0:
            declared.append(int(tw))
        else:
            declared.append(None)

    # 总和归一化：已填总和 > target*1.1 或 < target*0.9 → 等分；否则按等分。
    valid = [d for d in declared if d is not None]
    if valid and 0.9 * target_word_count <= sum(valid) <= 1.1 * target_word_count:
        budget = list(declared)  # type: ignore[assignment]
    else:
        base, rem = divmod(target_word_count, n)
        budget = [base] * n
        # 余数补到首场景（确定性强）
        if rem and n > 0:
            budget[0] = budget[0] + rem

    out_scenes: list[dict[str, Any]] = []
    for idx, src in enumerate(scenes):
        new_scene = dict(src)
        new_scene["target_words"] = int(budget[idx])
        out_scenes.append(new_scene)
    out_plan = dict(scene_plan)
    out_plan["scenes"] = out_scenes
    return out_plan


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


def _peek_project_word_band_json(
    db_path: str | Path, project_id: str | None,
) -> str | None:
    """轻量读 ``projects.word_band_json``；列缺失 / 异常 / 无项目 → None。

    供 build_writer_input 缓存键预判用——返回原文（JSON 字符串）以构造稳定指纹；
    解析失败不在此处处理（uncached 路径由 ``resolve_band_config`` 兜底）。
    """
    if not project_id:
        return None
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001
        return None
    try:
        try:
            row = conn.execute(
                "SELECT word_band_json FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            # 0023 未跑过的极老库 → 列不存在 → 视为无覆盖（与无覆盖行为零差异）。
            return None
    finally:
        conn.close()
    if row is None:
        return None
    val = row["word_band_json"]
    return val if val else None


def _fingerprint_word_band_json(raw: str | None) -> str:
    """``projects.word_band_json`` 轻量指纹（用于 writer 缓存键）。

    None / 空串 → ``"none"``（稳定指纹）；非空 → sha256[:16]（与 plan_fp 同款风格）。
    """
    if not raw:
        return "none"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return h[:16]


def _peek_active_canon_id(
    db_path: str | Path, project_id: str | None,
) -> str | None:
    """轻量读该项目最新 active reference_canon 的 ``canon_id``；无 → None。

    表缺失（老库 / 迁移未跑）→ 返回 ``None``；与无 canon 行为一致。
    """
    if not project_id:
        return None
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001
        return None
    try:
        try:
            row = conn.execute(
                """
                SELECT canon_id FROM reference_canons
                WHERE project_id = ? AND status = 'active'
                ORDER BY created_at DESC, canon_id DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return row["canon_id"] if row else None
    finally:
        conn.close()


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


def _safe_copy(value: Any) -> Any:
    """对 writer payload 的 list 字段做浅拷贝（dict 元素逐个 dict() 拷贝）。

    payload 中的字符字段（如 ``chapter`` / ``project``）不会被裁剪，不需要深拷贝；
    这里只需在裁剪前快照出被裁剪的 3 个键，防止后续 in-place 修改后无法量化
    before 体积。``copy.deepcopy`` 在 SQLite Row 等不可序列化对象上会失败，故
    采用「list 包浅拷贝 + dict 元素逐个 dict()」的折中：list 顶层新建避免共享
    引用；dict 元素新建避免子项共享。
    """
    import copy as _copy

    try:
        return _copy.copy(value)
    except Exception:  # noqa: BLE001
        if isinstance(value, list):
            return [dict(x) if isinstance(x, dict) else x for x in value]
        if isinstance(value, dict):
            return dict(value)
        return value
