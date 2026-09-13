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


# V3.9 批次 5.1 收尾：与 wordcount.DEFAULT_TARGET_WORD_COUNT 收敛同值（3000）。
# 旧值 2200（PRD §124 番茄 2000-2500 中点）与管线层 docstring 承诺的 3000 长期
# 矛盾（三处口径缺陷）；番茄带差异由 projects.word_band_json 项目级配置承载，
# 装配层默认值只管「未配置时的兜底」，统一为 3000。
_DEFAULT_TARGET_WORD_COUNT = 3000

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


# ---------------------------------------------------------------------------
# V3.9 批次 2.1：全局 token 预算兜底（只告警不阻断）
# ---------------------------------------------------------------------------

# 装配结果全局预算上限（token）；与 preview.py 的展示口径同源
# （``preview._TOKEN_BUDGET`` 直接引用本常量，避免两处各写 8000 后漂移）。
_ASSEMBLY_TOKEN_BUDGET = 8000
# 粗略 token 估算除数：4 字节 ≈ 1 token（中文场景保守近似，与 preview 同款）。
_TOKEN_DIVISOR = 4


def _estimate_tokens(value: Any) -> int:
    """粗略 token 估算：``len(json.dumps(value, ensure_ascii=False)) // 4``，至少 1。

    V3.9 批次 2.1：由本函数单点提供口径，``preview._token_estimate`` 与
    ``_assembly_meta.estimate_tokens`` 都引用它（展示 / 装配告警不再各算一套）。
    不可序列化 → 退回 ``str(value)``，不抛错。
    """
    try:
        serialized = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = str(value)
    return max(1, len(serialized) // _TOKEN_DIVISOR)


def _attach_assembly_meta(
    payload: dict[str, Any], *, summary_truncated: bool | None = None,
) -> dict[str, Any]:
    """就地写入 ``_assembly_meta`` 后返回 payload（各装配路径末尾单点调用）。

    字段（下划线前缀 = 非注入侧契约：调用方 / 前端不得当作 prompt 内容消费）：
    - ``estimate_tokens``：对 payload 本体（不含 ``_assembly_meta`` 自身）估算；
    - ``token_budget``：``_ASSEMBLY_TOKEN_BUDGET``；
    - ``token_budget_exceeded``：超预算标记——**只告警，不阻断也不改写已装配内容**
      （真裁剪仍靠各字段 cap 与摘要链 token 预算截断；一刀切割会破坏 JSON 契约）；
    - ``summary_truncated``：可选，摘要链是否被预算截断（仅 director 装配传入）。
    """
    body = {k: v for k, v in payload.items() if k != "_assembly_meta"}
    tokens = _estimate_tokens(body)
    meta: dict[str, Any] = {
        "estimate_tokens": tokens,
        "token_budget": _ASSEMBLY_TOKEN_BUDGET,
        "token_budget_exceeded": tokens > _ASSEMBLY_TOKEN_BUDGET,
    }
    if summary_truncated is not None:
        meta["summary_truncated"] = bool(summary_truncated)
    payload["_assembly_meta"] = meta
    return payload


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


def _hook_ledger_excerpt(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """伏笔台账视图（V3.9 批次 2.2 加上限）：planted 状态全量清单，按 importance DESC + id 稳定序取 20。

    与 :func:`_open_foreshadow_list` 的分工（两者并存，语义不同，不合并）：
    - 本函数 = **台账视图**：`hooks` 表原始状态机字段（status / importance /
      expected_payoff_chapter / payoff_chapter_id），不 join 章节号、不算 overdue、
      不判当前章——回答「项目里还有哪些伏笔挂着」。
    - `_open_foreshadow_list` = **待核销视图**：LEFT JOIN chapters 计算 introduced
      chapter_no 与 overdue，按 overdue 优先排序——回答「这一章该收哪些伏笔」。
    二者字段与排序口径都不同（台账按重要性，待核销按逾期），消费方（director prompt /
    preview）分别取用；合并会丢掉一方语义，故仅各自加 cap。
    """
    rows = conn.execute(
        """
        SELECT hook_id, name, status, importance, expected_payoff_chapter_id, payoff_chapter_id
        FROM hooks
        WHERE project_id = ? AND status IN ('OPEN', 'ACTIVE', 'ESCALATED')
        ORDER BY importance DESC, hook_id ASC
        LIMIT ?
        """,
        (project_id, _HOOK_LEDGER_CAP),
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
    """叙事债务台账（V3.9 批次 2.2 加上限）：severity DESC + id 稳定序取 20。"""
    rows = conn.execute(
        """
        SELECT debt_id, description, severity, deadline_chapter_id, status
        FROM narrative_debts
        WHERE project_id = ? AND status IN ('open', 'acknowledged')
        ORDER BY severity DESC, debt_id ASC
        LIMIT ?
        """,
        (project_id, _DEBT_LEDGER_CAP),
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


# 摘要链最近章数上限；超预算截断时优先砍最旧摘要。
# V3.9 批次 2.1：5 → 40。旧上限 5 × 每 200 字 ≈ 330 token，永远吃不满预算，
# 截断机制实为死代码；放宽后**预算成为真正的约束**（见 _DIRECTOR_SUMMARY_TOKEN_BUDGET
# 的最坏情况演算），条数上限退化为防御性硬顶。
_RECENT_SUMMARY_CAP = 40
# 摘要每条字符上限。
# V3.9 批次 2.1：200 → 400。生产写入端 summary 恒 ≤200 字
# （packages/workflows/chapter_commit/summary.py ``_SUMMARY_MAX_CHARS``，本批不改）；
# 此处放宽是为外部导入 / 历史超长行留余量，避免超长摘要整段直通注入。
_RECENT_SUMMARY_PER_CHARS = 400
# director 摘要链 token 预算（V3.9 批次 2.1：原为调用点写死的 800）。
# 最坏情况演算（证明机制真能触发）：
#   40 条 × (400 字摘要 + ~60 字 JSON 骨架) ≈ 18400 字符 ≈ 4600 token > 2000 → 截断触发；
#   生产口径（摘要 ≤200 字）：40 × 260 ≈ 10400 字符 ≈ 2600 token > 2000 → 长到 31 章
#   以后同样触发；中篇（≤20 章摘要）约 1300 token，不触发（全部保留）。
_DIRECTOR_SUMMARY_TOKEN_BUDGET = 2000
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
    token_divisor: int = _TOKEN_DIVISOR,
) -> tuple[list[dict[str, Any]], bool]:
    """按 token 预算截断摘要链（budget 由调用方传入，不再有模块级死常量）。

    - ``available_tokens``：本链可用的 token 预算（director 传
      ``_DIRECTOR_SUMMARY_TOKEN_BUDGET``；``<= 0`` 时全部砍掉）；
    - ``token_divisor``：与 :func:`_estimate_tokens` 同款口径（4 字节 ≈ 1 token）；
    - 策略：先砍最旧摘要（index 末尾 → 即 chapter_no 最小），每次重算整链成本，
      直到吃进预算或砍空。

    返回 ``(items_kept, truncated_bool)``；``truncated_bool`` 为 True 表示确有内容
    被预算砍掉（含「budget<=0 且原本非空」），供 ``_assembly_meta.summary_truncated``
    暴露给调用方观测。
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

    输出 scene_plan 永远带 ``scenes[*].target_words`` 字段（恒为 int）；
    target_word_count<=0 时不补（无预算可言）。不影响 ``scene_id / purpose /
    characters`` 等既有字段；只做浅拷贝，不破坏原 scene_plan。
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

    # 预算分摊（三条分支，保证 budget 恒为全 int，不出现 None）：
    # 1) 已声明子集和 ∈ [0.9T, 1.1T] → 已声明值原样保留（相对权重可信），
    #    未声明 scene 按「剩余预算（T − 已声明之和）均分」补齐；剩余 ≤ 0
    #    （子集已吃满 / 吃超预算）时给 T/n 的保底值（≥1 字）。
    #    scene_planner 契约不强制 per-scene target_words，部分声明的混合形态生产可达。
    # 2) 其余（全缺省 / 总和越界）→ 全场景等分，余数补首场景。
    valid = [d for d in declared if d is not None]
    if valid and 0.9 * target_word_count <= sum(valid) <= 1.1 * target_word_count:
        missing = n - len(valid)
        if missing:
            remaining = target_word_count - sum(valid)
            if remaining <= 0:
                fill = [max(1, target_word_count // n)] * missing
            else:
                base, rem = divmod(remaining, missing)
                fill = [base] * missing
                fill[0] += rem  # 余数补首个未声明场景（确定性强）
            fill_iter = iter(fill)
            budget = [d if d is not None else next(fill_iter) for d in declared]
        else:
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


# ---------------------------------------------------------------------------
# 题材库 P1a：genre_pack 注入段（director 消费；项目绑定题材包时）
# ---------------------------------------------------------------------------
#
# 架构裁决（docs/roadmap/题材库-评估与落地计划-2026-09-13.md §三/§四）：
# - 题材包是**独立资源**（自有表 / 自有 schema 版本线），与 reference_canon 的双 slot
#   并存：本段与 ``_reference_canon_excerpt`` 互不覆盖、各自独立缺席；
# - 复用既有注入管道（consumer 模式），P1a 只落 director 消费的结构模板摘要 +
#   爽点类型清单（含密度约束文本化）+ pacing 摘要；writer / scene_planner 消费留 P1b；
# - **缺席时零注入**：未绑定 / pack 行缺失 / payload 解析失败 → 不写 payload 键
#   （既有装配行为逐字段不变）。
#
# 缓存键：director 装配键含 pack 指纹（``pack_id@version``，见
# :func:`_peek_chapter_context` 的 ``genre_pack_ref``）——换 pack / 换 version 必 miss
# （照 V3.9 批次 1B「漏键=脏命中」教训）。
_GENRE_PACK_CONSUMER_DIRECTOR = "director"
_GENRE_PACK_CONSUMER_PLANNER = "scene_planner"
_GENRE_PACK_CONSUMER_WRITER = "writer"
# 爽点类型清单条数上限（题材包通常 20~40 型；超出截断并标记）。
_GENRE_PACK_PAYOFF_CAP = 40
# 结构模板节拍条数上限。
_GENRE_PACK_BEAT_CAP = 12
# 单字段字符上限（description / fatigue_risk / verify_hint 等长文本）；
# 与 schema 的 maxLength 同量级，注入层再兜一刀。
_GENRE_PACK_FIELD_CHARS = 200
# pacing 段 JSON 序列化后的字符预算（超出按优先级逐项保留）。
_GENRE_PACK_PACING_MAX_CHARS = 1200
# scene_planner 爽点摘要（payoff_types）JSON 序列化字符预算（P1b 口径 ≤1500 字符）；
# 超预算按条目顺序保留到吃满并打 ``__genre_pack_truncated__``。
_GENRE_PACK_PLANNER_PAYOFF_MAX_CHARS = 1500
# writer 题材体例（style_constraints）JSON 序列化字符预算。
_GENRE_PACK_STYLE_MAX_CHARS = 1500


def _truncate_text(value: Any, cap: int = _GENRE_PACK_FIELD_CHARS) -> str | None:
    """非空字符串 → 去空白 + 截断；其余 → None（缺字段语义）。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:cap]


def _compose_density_text(payoff: dict[str, Any]) -> str | None:
    """把爽点类型的密度约束文本化（注入用的人话口径）。

    口径：``density_cap``（原文文本，如「每卷 2~3 次」）+ ``min_interval_chapters``
    （同型最小间隔章数）；两者都缺 → None（不注入该字段）。
    """
    parts: list[str] = []
    cap = _truncate_text(payoff.get("density_cap"), 120)
    if cap:
        parts.append(f"密度上限 {cap}")
    interval = payoff.get("min_interval_chapters")
    if isinstance(interval, int) and not isinstance(interval, bool):
        if interval > 0:
            parts.append(f"同型最小间隔 {interval} 章")
        else:
            parts.append("同型无最小间隔约束")
    return "；".join(parts) if parts else None


def _textualize_genre_payoff_types(
    payoff_types: list[Any], *, cap: int = _GENRE_PACK_PAYOFF_CAP,
) -> tuple[list[dict[str, Any]], bool]:
    """爽点类型清单 → 注入形态（含密度约束文本化）；返回 ``(items, truncated)``。

    逐条字段：``type_id`` / ``name`` / ``strength``（可选）/ ``density_constraint``
    （文本化密度约束）/ ``applicable`` / ``fatigue_risk`` / ``verify_hint``。
    ``type_id`` 非字符串或空 → 跳过该条（schema 已要求必填，此为装配层兜底）。
    """
    items: list[dict[str, Any]] = []
    for raw in payoff_types:
        if not isinstance(raw, dict):
            continue
        type_id = _truncate_text(raw.get("type_id"), 64)
        if not type_id:
            continue
        entry: dict[str, Any] = {
            "type_id": type_id,
            "name": _truncate_text(raw.get("name"), 80) or type_id,
        }
        strength = _truncate_text(raw.get("strength"), 8)
        if strength:
            entry["strength"] = strength
        density = _compose_density_text(raw)
        if density:
            entry["density_constraint"] = density
        applicable = _truncate_text(raw.get("applicable"), 120)
        if applicable:
            entry["applicable"] = applicable
        fatigue = _truncate_text(raw.get("fatigue_risk"))
        if fatigue:
            entry["fatigue_risk"] = fatigue
        verify = _truncate_text(raw.get("verify_hint"))
        if verify:
            entry["verify_hint"] = verify
        items.append(entry)
    return items[:cap], len(items) > cap


def _summarize_genre_structure_templates(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """结构模板 → 注入摘要（阶段骨架 + 节拍模板 + 卷数期望 + 主线悬念）。

    返回 ``(summary, truncated)``；``truncated`` 表示节拍模板条数超上限被截断
    （``_GENRE_PACK_BEAT_CAP``）。
    """
    out: dict[str, Any] = {}
    truncated = False

    model = _truncate_text(raw.get("structure_model"), 400)
    if model:
        out["structure_model"] = model

    skeleton = raw.get("full_book_skeleton")
    if isinstance(skeleton, dict):
        picked: dict[str, str] = {}
        for key, value in skeleton.items():
            if not isinstance(key, str):
                continue
            text = _truncate_text(value, 400)
            if text:
                picked[key] = text
        if picked:
            out["full_book_skeleton"] = picked

    beats = raw.get("arc_beat_template")
    if isinstance(beats, list):
        items: list[dict[str, Any]] = []
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            entry: dict[str, Any] = {}
            for key, cap in (("beat", 80), ("chapters", 40), ("content", 400), ("must", 300)):
                text = _truncate_text(beat.get(key), cap)
                if text:
                    entry[key] = text
            if entry:
                items.append(entry)
        if items:
            if len(items) > _GENRE_PACK_BEAT_CAP:
                truncated = True
            out["arc_beat_template"] = items[:_GENRE_PACK_BEAT_CAP]

    volume = raw.get("volume_count_expectation")
    if isinstance(volume, dict):
        picked_v: dict[str, Any] = {}
        for vkey, vvalue in volume.items():
            if not isinstance(vkey, str):
                continue
            if isinstance(vvalue, int) and not isinstance(vvalue, bool):
                picked_v[vkey] = vvalue
                continue
            vtext = _truncate_text(vvalue, 200)
            if vtext:
                picked_v[vkey] = vtext
        if picked_v:
            out["volume_count_expectation"] = picked_v

    mainline = raw.get("mainline_suspense")
    if isinstance(mainline, dict):
        picked_m: dict[str, Any] = {}
        role = _truncate_text(mainline.get("role"))
        if role:
            picked_m["role"] = role
        designs = mainline.get("common_designs")
        if isinstance(designs, list):
            texts = [t for t in (_truncate_text(d) for d in designs) if t]
            if texts:
                picked_m["common_designs"] = texts
        cadence = _truncate_text(mainline.get("cadence"))
        if cadence:
            picked_m["cadence"] = cadence
        if picked_m:
            out["mainline_suspense"] = picked_m

    return out, truncated


def _normalize_genre_pacing(pacing: dict[str, Any]) -> dict[str, Any]:
    """pacing 值归一：字符串列表逐条截断（``density_rules`` / ``redlines``）。"""
    out = dict(pacing)
    for key, item_cap, list_cap in (
        ("density_rules", 200, 20),
        ("redlines", 200, 20),
    ):
        value = out.get(key)
        if isinstance(value, list):
            out[key] = [t for t in (_truncate_text(v, item_cap) for v in value) if t][:list_cap]
    return out


def _trim_genre_pacing(
    pacing: dict[str, Any], max_chars: int = _GENRE_PACK_PACING_MAX_CHARS,
) -> tuple[dict[str, Any], bool]:
    """pacing 段字符预算：按优先级逐项保留到 JSON ≤ ``max_chars``。

    优先级（高→低）：章字数带 / 章字数 / 密度规则 / 节奏红线 / 单元预算 / 全书预算 /
    其余键。单键自身超预算 → 跳过该键（不硬截断 JSON 结构）。
    返回 ``(picked, truncated)``。
    """
    priority = (
        "chapter_words", "chapter_word_band", "density_rules", "redlines",
        "arc_words", "book_words",
    )
    ordered = [k for k in priority if k in pacing] + [
        k for k in pacing if k not in priority
    ]
    picked: dict[str, Any] = {}
    for key in ordered:
        candidate = dict(picked)
        candidate[key] = pacing[key]
        try:
            encoded = json.dumps(candidate, ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        if len(encoded) <= max_chars:
            picked = candidate
    return picked, len(picked) < len(pacing)


def _trim_genre_style(
    style: dict[str, Any], max_chars: int = _GENRE_PACK_STYLE_MAX_CHARS,
) -> tuple[dict[str, Any], bool]:
    """writer 用题材体例（``style_constraints``）归一 + 字符预算。

    优先级（高→低）：``forbidden_words`` / ``pov`` / ``sentence_style`` / ``language``
    / ``notes`` / 其余键；列表值逐条截断 120 字、上限 50 条，字符串值截断 300 字；
    整体 JSON 超 :data:`_GENRE_PACK_STYLE_MAX_CHARS` 时从低优先级键开始删减。
    返回 ``(picked, truncated)``。
    """
    priority = ("forbidden_words", "pov", "sentence_style", "language", "notes")
    ordered = [k for k in priority if k in style] + [k for k in style if k not in priority]
    picked: dict[str, Any] = {}
    for key in ordered:
        value = style[key]
        if isinstance(value, list):
            texts = [t for t in (_truncate_text(v, 120) for v in value) if t]
            if texts:
                picked[key] = texts[:50]
        elif isinstance(value, str):
            text = _truncate_text(value, 300)
            if text:
                picked[key] = text
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            picked[key] = value
        elif isinstance(value, dict):
            picked[key] = value
    try:
        encoded = json.dumps(picked, ensure_ascii=False)
    except (TypeError, ValueError):
        return {}, bool(style)
    if len(encoded) <= max_chars:
        return picked, len(picked) < len(style)
    truncated = True
    for key in reversed(ordered):
        if key in picked:
            picked.pop(key)
            try:
                encoded = json.dumps(picked, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            if len(encoded) <= max_chars:
                return picked, truncated
    return {}, truncated


def _textualize_genre_planner_payoffs(
    payoff_types: list[Any],
    *,
    max_chars: int = _GENRE_PACK_PLANNER_PAYOFF_MAX_CHARS,
    cap: int = _GENRE_PACK_PAYOFF_CAP,
) -> tuple[list[dict[str, Any]], bool]:
    """scene_planner 用爽点摘要 → ``(items, truncated)``。

    与 director 的口径差别：这里服务「规划期硬约束」——逐条给出
    ``type_id`` / ``name`` / ``strength`` / ``density_cap``（原文）/
    ``min_interval_chapters``（整数）与文本化的 ``density_constraint``
    （「密度上限 X；同型最小间隔 N 章」，与 director 复用
    :func:`_compose_density_text`），条数上限 :data:`_GENRE_PACK_PAYOFF_CAP`。

    字符预算 :data:`_GENRE_PACK_PLANNER_PAYOFF_MAX_CHARS`（1500）：按条目顺序
    逐条保留到吃满预算；被预算或条数砍掉任何一条 → ``truncated=True``
    （调用方打 ``__genre_pack_truncated__``）。
    """
    items: list[dict[str, Any]] = []
    for raw in payoff_types:
        if len(items) >= cap:
            return items, True
        if not isinstance(raw, dict):
            continue
        type_id = _truncate_text(raw.get("type_id"), 64)
        if not type_id:
            continue
        entry: dict[str, Any] = {
            "type_id": type_id,
            "name": _truncate_text(raw.get("name"), 80) or type_id,
        }
        strength = _truncate_text(raw.get("strength"), 8)
        if strength:
            entry["strength"] = strength
        density_cap = _truncate_text(raw.get("density_cap"), 120)
        if density_cap:
            entry["density_cap"] = density_cap
        interval = raw.get("min_interval_chapters")
        if isinstance(interval, int) and not isinstance(interval, bool):
            entry["min_interval_chapters"] = interval
        density = _compose_density_text(raw)
        if density:
            entry["density_constraint"] = density
        try:
            encoded = json.dumps([*items, entry], ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        if len(encoded) > max_chars:
            return items, True
        items.append(entry)
    # 只按「预算 / 条数砍条」判截断；非法条目（非 dict / 缺 type_id）跳过不算截断
    # （它们本就不会进注入，标 truncated 会误导消费方以为内容被削）。
    return items, False


def _ratio_instruction(
    ratio_declarations: dict[str, Any] | None,
) -> str | None:
    """配比声明 → scene_planner 的配比指令文本；未声明 / 非法 → None。

    文本口径（写死给 prompt，不随实现漂移）：「题材配比声明：<键>=<份额%>…。」+
    「逐 scene 标注 scene_type（取值与配比维度一致），并为每个 scene 给出
    target_words（整数，总和≈本章目标字数；各 scene 字数占比遵守上述配比）。」
    占比按声明原值渲染（schema 保证 ∈ [0,1]）；份额之和 ≠ 1 时附
    「按份额相对比例理解」注记。
    """
    if not isinstance(ratio_declarations, dict) or not ratio_declarations:
        return None
    parts: list[str] = []
    total = 0.0
    for key, value in ratio_declarations.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        parts.append(f"{key.strip()}={float(value):.0%}")
        total += float(value)
    if not parts:
        return None
    text = (
        "题材配比声明："
        + "、".join(parts)
        + "。逐 scene 标注 scene_type（取值与配比维度一致），并为每个 scene 给出 "
        "target_words（整数，总和≈本章目标字数；各 scene 字数占比遵守上述配比）。"
    )
    if abs(total - 1.0) > 0.01:
        text += f"（声明份额之和 {total:.2f}，按份额相对比例理解）"
    return text


def _genre_pack_excerpt(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    consumer: str = _GENRE_PACK_CONSUMER_DIRECTOR,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """读项目绑定的题材包（``projects.genre_pack_id`` → ``genre_packs``）。

    返回 ``(inject, audit)``：
    - inject：写入 payload 的 ``genre_pack`` 键（None = 未绑定 / 无行 / 非法 payload）；
      **writer** consumer 例外——它把题材体例合并进既有 ``style_constraints`` 的
      ``genre_style`` 子键（见 :func:`packages.core.context_engine.writer_input`），
      inject 返回的是待合并的 ``style_constraints`` 段而非顶层 ``genre_pack`` 段；
    - audit：``{"pack_id", "version", "consumed_fields"}``，落到 ctx 顶层
      ``_genre_pack_consumed``（与 ``_reference_canon_consumed`` 同款溯源口径）。

    consumer 口径（P1a director / P1b scene_planner + writer）：
    - ``"director"``：``structure_templates``（结构模板摘要）/ ``payoff_types``
      （爽点类型 + 密度约束文本化）/ ``pacing``（节奏摘要）；
    - ``"scene_planner"``：``payoff_types``（规划期爽点摘要，≤1500 字符预算，
      超限打 ``__genre_pack_truncated__``）/ ``ratio_declarations`` + 由其派生的
      ``ratio_instruction``（配比分摊指令）；
    - ``"writer"``：``style_constraints``（题材体例，供 writer 合并为
      ``style_constraints.genre_style``）；
    - 其它 consumer：不注入（返回 ``(None, None)``）。

    容错：表 / 列缺失（迁移未跑的极老库）或 payload_json 非法 → ``(None, None)``，
    不抛错（既有装配行为不变）。
    """
    if consumer not in (
        _GENRE_PACK_CONSUMER_DIRECTOR,
        _GENRE_PACK_CONSUMER_PLANNER,
        _GENRE_PACK_CONSUMER_WRITER,
    ):
        return None, None
    try:
        row = conn.execute(
            """
            SELECT gp.pack_id, gp.name, gp.genre_tag, gp.version, gp.payload_json
            FROM projects pj
            JOIN genre_packs gp ON gp.pack_id = pj.genre_pack_id
            WHERE pj.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # 0025 未跑过的极老库 → 视为未绑定（与无题材包行为零差异）。
        return None, None
    if row is None:
        return None, None

    payload = _parse_json(row["payload_json"])
    if not isinstance(payload, dict):
        return None, None

    pack_id = row["pack_id"]
    version = _as_int_or(row["version"])
    inject: dict[str, Any] = {
        "pack_id": pack_id,
        "name": row["name"],
        "genre_tag": row["genre_tag"],
        "version": version,
    }
    consumed: list[str] = []

    if consumer == _GENRE_PACK_CONSUMER_DIRECTOR:
        structure = payload.get("structure_templates")
        if isinstance(structure, dict) and structure:
            summary, structure_truncated = _summarize_genre_structure_templates(structure)
            if summary:
                inject["structure_templates"] = summary
                if structure_truncated:
                    inject["__structure_templates_truncated__"] = True
                consumed.append("structure_templates")

        payoff_types = payload.get("payoff_types")
        if isinstance(payoff_types, list) and payoff_types:
            items, payoffs_truncated = _textualize_genre_payoff_types(payoff_types)
            if items:
                inject["payoff_types"] = items
                if payoffs_truncated:
                    inject["__payoff_types_truncated__"] = True
                consumed.append("payoff_types")

        pacing = payload.get("pacing")
        if isinstance(pacing, dict) and pacing:
            picked, pacing_truncated = _trim_genre_pacing(_normalize_genre_pacing(pacing))
            if picked:
                inject["pacing"] = picked
                if pacing_truncated:
                    inject["__pacing_truncated__"] = True
                consumed.append("pacing")
    elif consumer == _GENRE_PACK_CONSUMER_PLANNER:
        payoff_types = payload.get("payoff_types")
        if isinstance(payoff_types, list) and payoff_types:
            items, payoffs_truncated = _textualize_genre_planner_payoffs(payoff_types)
            if items:
                inject["payoff_types"] = items
                if payoffs_truncated:
                    # P1b 口径标记名（与 director 的 __payoff_types_truncated__ 并存，
                    # 供 scene_planner 侧单点判断「爽点摘要被预算/条数截断」）。
                    inject["__genre_pack_truncated__"] = True
                consumed.append("payoff_types")
        ratio = payload.get("ratio_declarations")
        if isinstance(ratio, dict) and ratio:
            inject["ratio_declarations"] = ratio
            consumed.append("ratio_declarations")
            instruction = _ratio_instruction(ratio)
            if instruction:
                inject["ratio_instruction"] = instruction
    elif consumer == _GENRE_PACK_CONSUMER_WRITER:
        style = payload.get("style_constraints")
        if isinstance(style, dict) and style:
            picked, style_truncated = _trim_genre_style(style)
            if picked:
                inject["style_constraints"] = picked
                if style_truncated:
                    inject["__style_constraints_truncated__"] = True
                consumed.append("style_constraints")
        if not consumed:
            # writer 的注入面是「合并进 style_constraints.genre_style」——题材包未声明
            # 文风段时整段不注入（避免往 style_constraints 塞无文风含义的空壳）。
            return None, None

    audit = {"pack_id": pack_id, "version": version, "consumed_fields": consumed}
    return inject, audit


# ---------------------------------------------------------------------------
# 缓存键预判 / uncached 装配共用的轻量预读（V3.9 批次 2.3）
# ---------------------------------------------------------------------------
#
# 旧实现：四个 ``_peek_*`` 各自 ``get_connection``（每连接 3 条 PRAGMA 固定成本），
# 其中两个为拿 state_version 走 ``StoryStateService.get_current_state``（读 + 解析
# 整份 snapshot_json）；writer 缓存命中路径合计 5 连接。现收敛为「1 连接 1 条 JOIN」，
# 结果同时喂给 uncached 装配复用（``peek=`` 参数，见 director/writer 调用点）。
_PEEK_CHAPTER_SQL = """
    SELECT ch.number                           AS chapter_no,
           ch.project_id                       AS project_id,
           ch.plan_json                        AS plan_json,
           (SELECT MAX(sv.state_version) FROM story_states sv
             WHERE sv.project_id = COALESCE(?, ch.project_id))       AS state_version,
           (SELECT rc.canon_id FROM reference_canons rc
             WHERE rc.project_id = COALESCE(?, ch.project_id)
               AND rc.status = 'active'
             ORDER BY rc.created_at DESC, rc.canon_id DESC LIMIT 1)  AS active_canon_id,
           (SELECT pj2.genre_pack_id || '@' || gp.version
              FROM projects pj2
              LEFT JOIN genre_packs gp ON gp.pack_id = pj2.genre_pack_id
             WHERE pj2.project_id = COALESCE(?, ch.project_id))      AS genre_pack_ref,
           pj.word_band_json                   AS word_band_json
    FROM chapters ch
    LEFT JOIN projects pj ON pj.project_id = COALESCE(?, ch.project_id)
    WHERE ch.chapter_id = ?
"""

# 降级 SQL：极老库缺 reference_canons 表 / projects.word_band_json 列时上面的 JOIN 抛
# OperationalError → 退回只读章节三列，项目维度各项独立 try（逐项与旧 _peek_* 同语义）。
_PEEK_CHAPTER_FALLBACK_SQL = """
    SELECT number AS chapter_no, project_id AS project_id, plan_json AS plan_json
    FROM chapters WHERE chapter_id = ?
"""


def _as_int_or(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _empty_chapter_peek(chapter_id: str, project_id: str | None = None) -> dict[str, Any]:
    """无章节行 / 连接打不开时的空态 peek（字段齐全，调用方免判 None）。"""
    return {
        "found": False,
        "chapter_id": chapter_id,
        "project_id": project_id,
        "chapter_no": 0,
        "state_version": 0,
        "plan_json_raw": None,
        "word_band_json": None,
        "active_canon_id": None,
        "genre_pack_ref": None,
    }


def _project_max_state_version(conn: sqlite3.Connection, project_id: str) -> int:
    """``SELECT MAX(state_version) FROM story_states`` 轻查询；无快照 / 表缺失 → 0。

    与 ``StoryStateService.get_current_state`` 的 state_version 口径一致：main 路径取
    ``story_states`` 最新快照版本（``snapshots.latest_snapshot_version`` 即
    ``ORDER BY state_version DESC LIMIT 1``），无快照行时 ``build_initial_state`` 填 0。
    差别只在成本：旧实现为拿这个整数读 + 解析整份 snapshot_json。
    """
    try:
        row = conn.execute(
            "SELECT MAX(state_version) AS max_sv FROM story_states WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None:
        return 0
    return _as_int_or(row["max_sv"])


def _try_project_word_band_json(conn: sqlite3.Connection, project_id: str) -> str | None:
    """单连接读 ``projects.word_band_json`` 原文；列缺失 / 无项目 → None。"""
    try:
        row = conn.execute(
            "SELECT word_band_json FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # 0023 未跑过的极老库 → 列不存在 → 视为无覆盖（与无覆盖行为零差异）。
        return None
    if row is None:
        return None
    val = row["word_band_json"]
    return val if val else None


def _try_active_canon_id(conn: sqlite3.Connection, project_id: str) -> str | None:
    """单连接读最新 active canon_id；表缺失 / 无 canon → None。"""
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


def _try_project_genre_pack_ref(conn: sqlite3.Connection, project_id: str) -> str | None:
    """单连接读项目题材包指纹 ``<pack_id>@<version>``；表/列缺失 / 未绑定 → None。

    指纹形态与 :data:`_PEEK_CHAPTER_SQL` 内联子查询逐字一致（``|| '@' ||``）——
    director 缓存键的题材包维度在两个路径（主 SQL / 降级逐项读）必须同形，
    否则降级路径会把键维度写成另一种形状导致脏命中。
    """
    try:
        row = conn.execute(
            """
            SELECT pj.genre_pack_id || '@' || gp.version AS genre_pack_ref
            FROM projects pj
            LEFT JOIN genre_packs gp ON gp.pack_id = pj.genre_pack_id
            WHERE pj.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # 0025 未跑过的极老库（无 genre_packs 表 / 无 genre_pack_id 列）→ 未绑定。
        return None
    if row is None:
        return None
    ref = row["genre_pack_ref"]
    return ref if ref else None


def _peek_chapter_context_conn(
    conn: sqlite3.Connection, chapter_id: str, *, project_id: str | None = None,
) -> dict[str, Any]:
    """已持有连接时的 peek 实现（不关闭连接；供同连接复用）。"""
    try:
        row = conn.execute(
            _PEEK_CHAPTER_SQL,
            (project_id, project_id, project_id, project_id, chapter_id),
        ).fetchone()
    except sqlite3.Error:
        return _peek_chapter_context_degraded(conn, chapter_id, project_id)
    if row is None:
        return _empty_chapter_peek(chapter_id, project_id)
    return {
        "found": True,
        "chapter_id": chapter_id,
        "project_id": row["project_id"],
        "chapter_no": _as_int_or(row["chapter_no"]),
        "state_version": _as_int_or(row["state_version"]),
        "plan_json_raw": row["plan_json"],
        "word_band_json": row["word_band_json"] or None,
        "active_canon_id": row["active_canon_id"] or None,
        "genre_pack_ref": row["genre_pack_ref"] or None,
    }


def _peek_chapter_context_degraded(
    conn: sqlite3.Connection, chapter_id: str, project_id: str | None,
) -> dict[str, Any]:
    """JOIN 失败时的逐项降级读（语义与收敛前各 ``_peek_*`` 单函数一致）。"""
    try:
        row = conn.execute(_PEEK_CHAPTER_FALLBACK_SQL, (chapter_id,)).fetchone()
    except sqlite3.Error:
        return _empty_chapter_peek(chapter_id, project_id)
    if row is None:
        return _empty_chapter_peek(chapter_id, project_id)
    pid = project_id or row["project_id"]
    info = _empty_chapter_peek(chapter_id, pid)
    info.update({
        "found": True,
        "chapter_no": _as_int_or(row["chapter_no"]),
        "plan_json_raw": row["plan_json"],
    })
    if pid:
        info["state_version"] = _project_max_state_version(conn, pid)
        info["word_band_json"] = _try_project_word_band_json(conn, pid)
        info["active_canon_id"] = _try_active_canon_id(conn, pid)
        info["genre_pack_ref"] = _try_project_genre_pack_ref(conn, pid)
    return info


def _peek_chapter_context(
    db_path: str | Path, chapter_id: str, *, project_id: str | None = None,
) -> dict[str, Any]:
    """单连接轻量预读（缓存键预判 + uncached 装配复用；V3.9 批次 2.3）。

    返回 ``{found, chapter_id, project_id, chapter_no, state_version, plan_json_raw,
    word_band_json, active_canon_id, genre_pack_ref}``：

    - ``state_version`` = ``MAX(story_states.state_version)``（无快照 → 0），口径见
      :func:`_project_max_state_version`；
    - ``plan_json_raw`` / ``word_band_json`` 为 DB 原文（不做解析，避免破坏键稳定性）；
    - ``genre_pack_ref`` = 绑定题材包指纹 ``<pack_id>@<version>``（未绑定 / 极老库 → None），
      供 director 缓存键的题材包维度使用（题材库 P1a）；
    - 任一字段缺失 / 异常 → 该字段安全 fallback，不抛错（连接打不开 → 全空态）；
    - ``project_id`` 显式传入时以其为准（保留 ``_peek_chapter_no_state_version`` 旧语义）；
      传 None 则用章节行自带的 ``project_id``（唯一正确来源）。
    """
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001 —— 打不开库 → 全空态（旧 _peek_project_id_* 同款）
        return _empty_chapter_peek(chapter_id, project_id)
    try:
        return _peek_chapter_context_conn(conn, chapter_id, project_id=project_id)
    finally:
        conn.close()


def _peek_project_context(db_path: str | Path, project_id: str | None) -> dict[str, Any]:
    """单连接读项目维度三标量（word_band_json / active_canon_id / state_version）。"""
    empty = {"word_band_json": None, "active_canon_id": None, "state_version": 0}
    if not project_id:
        return empty
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001
        return empty
    try:
        return {
            "word_band_json": _try_project_word_band_json(conn, project_id),
            "active_canon_id": _try_active_canon_id(conn, project_id),
            "state_version": _project_max_state_version(conn, project_id),
        }
    finally:
        conn.close()


def _peek_chapter_no_state_version(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
) -> tuple[int, int]:
    """轻量读 chapters.number + state_version；用于缓存键预判。

    返回 ``(chapter_no, state_version)``；任一缺失 → (0, 0)。
    V3.9 批次 2.3：薄封装 :func:`_peek_chapter_context`（单连接 JOIN）。
    """
    info = _peek_chapter_context(db_path, chapter_id, project_id=project_id)
    return info["chapter_no"], info["state_version"]


def _peek_chapter_no_state_version_plan(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
) -> tuple[int, int, Any]:
    """V2.0 Wave C P1-1：director 缓存键的轻量预读。

    返回 ``(chapter_no, state_version, plan_json_raw)``。
    ``plan_json_raw`` 是 DB 原文 str（不做解析，避免破坏键稳定性）；
    缺失 → ``None``。任一异常 → 对应字段安全 fallback，不抛错。
    V3.9 批次 2.3：薄封装 :func:`_peek_chapter_context`（单连接 JOIN）。
    """
    info = _peek_chapter_context(db_path, chapter_id, project_id=project_id)
    return info["chapter_no"], info["state_version"], info["plan_json_raw"]


def _peek_project_id_from_chapter(db_path: str | Path, chapter_id: str) -> str | None:
    """轻量读 chapters.project_id；用于缓存键预判（V3.9 批次 2.3：单连接 JOIN）。"""
    return _peek_chapter_context(db_path, chapter_id)["project_id"]


def _peek_project_word_band_json(
    db_path: str | Path, project_id: str | None,
) -> str | None:
    """轻量读 ``projects.word_band_json``；列缺失 / 异常 / 无项目 → None。

    供 build_writer_input 缓存键预判用——返回原文（JSON 字符串）以构造稳定指纹；
    解析失败不在此处处理（uncached 路径由 ``resolve_band_config`` 兜底）。
    """
    if not project_id:
        return None
    return _peek_project_context(db_path, project_id)["word_band_json"]


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
    V3.9 批次 2.3：薄封装 :func:`_peek_project_context`（单连接）。
    """
    if not project_id:
        return None
    return _peek_project_context(db_path, project_id)["active_canon_id"]


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
