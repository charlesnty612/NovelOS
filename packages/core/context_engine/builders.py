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
import os
import sqlite3
import threading as _threading
from pathlib import Path
from typing import Any, Iterable

from packages.core.db import get_connection
from packages.core.quality.wordcount import word_band

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


# ---------------------------------------------------------------------------
# P2 Context Engine：章节级相关性裁剪（Relevance Trim）
# ---------------------------------------------------------------------------
# 对标 bishu-novel trimmer：按本章 plan / scene_plan 明确涉及的字符 / 地点
# 过滤角色与世界观条目，降低无关 token 与噪音。默认开启，可通过环境变量
# ``NOVELOS_CONTEXT_RELEVANCE=off`` 全局关闭，或通过 ``relevance_trim=False`` 单次关闭。
# ---------------------------------------------------------------------------


def _resolve_relevance_trim(relevance_trim: bool | None) -> bool:
    """解析 ``relevance_trim`` 参数：显式传值优先，否则读环境变量。

    - ``NOVELOS_CONTEXT_RELEVANCE=off``（大小写不敏感）→ 关闭；
    - 未设置 / 其他值 → 默认开启。
    """
    if relevance_trim is not None:
        return bool(relevance_trim)
    env = (os.environ.get("NOVELOS_CONTEXT_RELEVANCE") or "").strip().lower()
    return env != "off"


def _extract_involved_entities(
    plan_json: dict[str, Any],
    scene_plan: dict[str, Any],
) -> tuple[set[str], set[str]]:
    """从章节 plan + scene_plan 中提取涉及的角色 / 地点标识集合。

    扫描面（与 chapter_write.pipeline 中 scene_plan 构造口径对齐）：
    - ``plan_json.key_beats[*].involved_characters / involved_locations``
    - ``plan_json.character_changes_planned[*].name / character_id``
    - ``scene_plan.characters / location``
    - ``scene_plan.beats[*].involved_characters / involved_locations``

    返回 ``(involved_characters, involved_locations)``，元素为 id 或 name（字符串）。
    """
    chars: set[str] = set()
    locs: set[str] = set()

    if isinstance(plan_json, dict):
        for beat in plan_json.get("key_beats") or []:
            if isinstance(beat, dict):
                for c in beat.get("involved_characters") or []:
                    if isinstance(c, str):
                        chars.add(c.strip())
                for l in beat.get("involved_locations") or []:
                    if isinstance(l, str):
                        locs.add(l.strip())
        for change in plan_json.get("character_changes_planned") or []:
            if isinstance(change, dict):
                name = change.get("name")
                if isinstance(name, str) and name.strip():
                    chars.add(name.strip())
                cid = change.get("character_id")
                if isinstance(cid, str) and cid.strip():
                    chars.add(cid.strip())
            elif isinstance(change, str) and change.strip():
                chars.add(change.strip())

    if isinstance(scene_plan, dict):
        for c in scene_plan.get("characters") or []:
            if isinstance(c, str):
                chars.add(c.strip())
        loc = scene_plan.get("location")
        if isinstance(loc, str) and loc.strip():
            locs.add(loc.strip())
        for beat in scene_plan.get("beats") or []:
            if isinstance(beat, dict):
                for c in beat.get("involved_characters") or []:
                    if isinstance(c, str):
                        chars.add(c.strip())
                for l in beat.get("involved_locations") or []:
                    if isinstance(l, str):
                        locs.add(l.strip())

    return chars, locs


def _is_character_relevant(char: dict[str, Any], involved: set[str]) -> bool:
    """角色是否属于本章核心相关：主角 / always 模式 / id 或 name 命中 involved。"""
    if not isinstance(char, dict):
        return False
    if char.get("role") == "protagonist":
        return True
    if char.get("inject_mode") == "always":
        return True
    cid = char.get("character_id")
    name = char.get("name")
    if isinstance(cid, str) and cid in involved:
        return True
    if isinstance(name, str) and name in involved:
        return True
    return False


def _is_location_relevant(loc: dict[str, Any], involved: set[str]) -> bool:
    """地点是否属于本章核心相关：always 模式 / id 或 name 命中 involved。"""
    if not isinstance(loc, dict):
        return False
    if loc.get("inject_mode") == "always":
        return True
    lid = loc.get("location_id")
    name = loc.get("name")
    if isinstance(lid, str) and lid in involved:
        return True
    if isinstance(name, str) and name in involved:
        return True
    return False


def _is_faction_relevant(fac: dict[str, Any], involved: set[str]) -> bool:
    """势力是否属于本章核心相关：always 模式 / id 或 name 命中 involved。"""
    if not isinstance(fac, dict):
        return False
    if fac.get("inject_mode") == "always":
        return True
    fid = fac.get("faction_id")
    name = fac.get("name")
    if isinstance(fid, str) and fid in involved:
        return True
    if isinstance(name, str) and name in involved:
        return True
    return False


def _summarize_entity_for_relevance(entity: dict[str, Any], *, id_field: str) -> dict[str, Any]:
    """未涉及实体的极简降级：仅保留 id + name + 标记位。"""
    return {
        id_field: entity.get(id_field),
        "name": entity.get("name"),
        "relevance_summary": True,
    }


def _apply_relevance_trim(
    payload: dict[str, Any],
    *,
    relevance_trim: bool,
    plan_json: dict[str, Any],
    scene_plan: dict[str, Any],
) -> dict[str, Any]:
    """按本章 plan/scene 对 writer payload 做相关性裁剪（纯函数）。

    - 主角（role=protagonist）与 ``inject_mode='always'`` 的实体始终完整保留；
    - 其余实体若 id 或 name 命中 ``involved_characters / involved_locations`` 则保留完整；
    - 未命中实体降级为 ``{id, name, relevance_summary: True}``，不直接剔除，便于调用方
      / preview 仍识别到存在；
    - 注入 ``_relevance_trim_enabled`` 与 ``_relevance_trim_stats`` 用于观测。

    注意：本函数会原地修改 ``payload`` 并返回它。
    """
    payload["_relevance_trim_enabled"] = relevance_trim
    if not relevance_trim:
        return payload

    involved_chars, involved_locs = _extract_involved_entities(plan_json, scene_plan)

    chars_in = payload.get("character_state_excerpts") or []
    chars_out: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "characters_full": 0,
        "characters_summary": 0,
        "locations_full": 0,
        "locations_summary": 0,
        "factions_full": 0,
        "factions_summary": 0,
        "involved_characters": sorted(involved_chars),
        "involved_locations": sorted(involved_locs),
    }

    for c in chars_in:
        if not isinstance(c, dict):
            continue
        if _is_character_relevant(c, involved_chars):
            chars_out.append(c)
            stats["characters_full"] += 1
        else:
            chars_out.append(
                _summarize_entity_for_relevance(c, id_field="character_id")
            )
            stats["characters_summary"] += 1

    world_in = payload.get("world_state_excerpts") or {}
    if isinstance(world_in, dict):
        locs_in = world_in.get("locations") or []
        locs_out: list[dict[str, Any]] = []
        for loc in locs_in:
            if not isinstance(loc, dict):
                continue
            if _is_location_relevant(loc, involved_locs):
                locs_out.append(loc)
                stats["locations_full"] += 1
            else:
                locs_out.append(
                    _summarize_entity_for_relevance(loc, id_field="location_id")
                )
                stats["locations_summary"] += 1
        world_in["locations"] = locs_out

        facs_in = world_in.get("active_factions") or []
        facs_out: list[dict[str, Any]] = []
        for fac in facs_in:
            if not isinstance(fac, dict):
                continue
            if _is_faction_relevant(fac, involved_locs):
                facs_out.append(fac)
                stats["factions_full"] += 1
            else:
                facs_out.append(
                    _summarize_entity_for_relevance(fac, id_field="faction_id")
                )
                stats["factions_summary"] += 1
        world_in["active_factions"] = facs_out

        # 感官锚点已经只在完整注入的 location 上生成；relevance_trim 后若某 location
        # 被降级，它的 data_json 为空，不会贡献锚点。这里不需要重新提取。
        payload["world_state_excerpts"] = world_in

    payload["character_state_excerpts"] = chars_out
    payload["_relevance_trim_stats"] = stats
    return payload


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
        "project": {
            "project_id": project["project_id"],
            "name": project["name"],
            "genre": project.get("genre"),
            "premise": project.get("premise"),
        },
        "knowledge_permissions": {
            "your_visibility": ["AUTHOR", "DIRECTOR"],
            "forbidden_kinds": ["HIDDEN"],
        },
        "constraints": {
            "forbidden_topics": [],
            "must_include": [],
            "style_constraints_id": None,
        },
        "chapter": {
            "title": chapter.get("title"),
            "target_word_count": target_word_count,
            "expected_role": "setup",
            "chapter_id": chapter_id,
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
    *,
    relevance_trim: bool = True,
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

    # V3.7：writer payload 注入字数带（不含 floor，prompt Rule 15 已静态声明 1200 下限）
    _wb_low, _wb_high = word_band(target_word_count)
    payload: dict[str, Any] = {
        "agent": "writer",
        "prompt_version": "writer:v1",
        "knowledge_permissions": {
            "your_visibility": ["WRITER", "PUBLIC", "VISIBLE"],
            "forbidden_kinds": ["HIDDEN"],
        },
        "style_constraints": dict(_DEFAULT_STYLE_CONSTRAINTS),
        "chapter": {
            "title": chapter.get("title"),
            "target_word_count": target_word_count,
            "expected_role": director_plan.get("expected_role") or "setup",
            "chapter_id": chapter_id,
            "word_band": {
                "low": _wb_low,
                "high": _wb_high,
            },
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
    }

    # P2 Context Engine：章节级相关性裁剪。默认开启，可在调用层 / 环境变量关闭。
    _apply_relevance_trim(
        payload,
        relevance_trim=relevance_trim,
        plan_json=director_plan,
        scene_plan=scene_plan,
    )
    return payload


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
    context_mode: str = "full",
    relevance_trim: bool | None = None,
) -> dict[str, Any]:
    """组装 Writer 输入（agent-contracts §4.1 + Sprint 15/V1.3 author_style_samples
    + V2.0 Wave B 任务二 条件触发动态注入 + V2.0 Wave C 任务一 召回 + 任务二 缓存
    + V3.2 P2-1 分页模式 L0/L1/L2 裁剪 + P2 Context Engine 相关性裁剪）。

    参数新增（V3.2 P2-1）：
        context_mode：
            - ``"full"``（默认行为零变化）：返回与历史版本逐字段一致的完整 payload。
            - ``"paged"``：按 L0/L1/L2 分层裁剪——
                * L0 常驻：``world_rules`` 全量（硬设定）；
                * L1 近窗：``characters / locations / factions`` 按「最近
                  ``keep_recent_commits``（默认 3）个 commit 触达的全量 + 其余仅
                  id/name/status 摘要」裁剪；``hooks`` open 全量、resolved 仅留
                  最近 5 条摘要（与 observer trimmed 同口径）；``plot_events`` 保持
                  现有摘要链机制不变。
                * payload 顶层追加 ``context_mode="paged"`` 与 ``context_paging_stats``
                  裁剪统计。

    参数新增（P2 Context Engine）：
        relevance_trim：
            - ``True`` / ``False`` 显式开关本章相关性裁剪；
            - ``None``（默认）时读环境变量 ``NOVELOS_CONTEXT_RELEVANCE``：
              值为 ``off`` 时关闭，其他值开启。
            - 按本章 plan_json / scene_plan 中的 ``involved_characters`` /
              ``involved_locations`` 过滤角色与世界观条目；主角（protagonist）与
              ``inject_mode='always'`` 的实体始终完整保留；未命中实体降级为
              ``{id, name, relevance_summary: True}``。

    V2.0 Wave C P1-1 修复：缓存键追加 ``scene_fp``（scene_plan 序列化指纹）；
    不同 scene_plan 不再共享同一缓存条目——避免传不同 scene 时命中陈旧 writer 输入。
    不可序列化时 ``scene_fp == 'uncached'`` → 跳过缓存（直接走 uncached）。

    V3.2 P2-1：缓存键追加第 6 元 ``mode``（``"full"`` / ``"paged"``）——
    防止 paged/full 模式共享同一缓存条目而命中陈旧结构。

    P2 Context Engine：缓存键追加第 7 元 ``relevance``（``"on"`` / ``"off"``）——
    防止 relevance_trim 开关/环境变量变化导致脏命中。
    """
    if context_mode not in ("full", "paged"):
        raise ValueError(
            f"context_mode must be 'full' or 'paged', got {context_mode!r}"
        )
    relevance_trim_final = _resolve_relevance_trim(relevance_trim)
    project_id = _peek_project_id_from_chapter(db_path, chapter_id)
    chapter_no, state_version = _peek_chapter_no_state_version(
        db_path, project_id, chapter_id,
    )
    scene_fp = _fingerprint_scene_plan(scene_plan)
    relevance_flag = "on" if relevance_trim_final else "off"
    cache_key = (
        project_id or "", state_version, chapter_no, "writer",
        scene_fp, context_mode, relevance_flag,
    )
    if scene_fp != _FINGERPRINT_UNCACHED:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
    if context_mode == "paged":
        payload = _build_writer_input_paged(
            db_path, chapter_id, scene_plan, target_word_count,
            relevance_trim=relevance_trim_final,
        )
    else:
        payload = _build_writer_input_uncached(
            db_path, chapter_id, scene_plan, target_word_count,
            relevance_trim=relevance_trim_final,
        )
    if scene_fp != _FINGERPRINT_UNCACHED:
        _cache_put(cache_key, payload)
    return payload


# ---------------------------------------------------------------------------
# V3.2 P2-1：writer 分页模式（L0 world_rules 常驻 + L1 实体近窗 + L2 章节专属）
# ---------------------------------------------------------------------------
# 设计要点：
# - L0：world_rules 全量（PRD 视世界规则为硬设定；observer 亦保留全量）。
# - L1：characters/locations/factions 按「最近 keep_recent_commits 个 commit 触达
#   的全量 + 其余仅 {id, name, status/role} 摘要」裁剪；hooks open/active/escalated
#   全量、resolved/abandoned 仅留最近 5 条摘要（与 observer 同口径）。
# - L2：director_plan / scene_plan / recent_prose / author_style_samples /
#   recalled_passages / retrieved_memory / knowledge_permissions / style_constraints
#   保持不变（已是章节专属信号）。
# - plot_events：保持现有摘要链机制不重复处理（V2.0 Wave C 任务二已注入；
#   writer 当前不读 plot_graph_excerpt，留作未来扩展；不强行塞入）。
# - 顶层追加 ``context_mode="paged"`` + ``context_paging_stats`` 体积量化与裁剪计数，
#   与 observer 的 ``snapshot_trim_stats`` 命名对齐。
# - 摘要构造器独立实现（不复用 observer 摘要构造器——observer 处理的是 snapshot
#   Canonical State 结构，writer 处理的是 excerpt dict 集合；语义层差异显著，
#   强行复用会让两组函数耦合到同一份输入契约，违背「摘要构造器按口径独立」原则）。
# ---------------------------------------------------------------------------


# writer 分页模式常量
_WRITER_KEEP_RECENT_COMMITS = 3  # 默认扫描 commit 数；与 observer 对齐
_WRITER_RESOLVED_HOOKS_KEEP = 5  # resolved hooks 保留上限；与 observer 对齐
# writer 分页触达实体「未命中 touched」的极简摘要口径（与 observer 区分——observer
# 处理 snapshot 内嵌结构，writer 处理 excerpt dict 列表）。
_SUMMARY_KEYS_CHARACTER = ("character_id", "name", "role")
_SUMMARY_KEYS_LOCATION = ("location_id", "name")
_SUMMARY_KEYS_FACTION = ("faction_id", "name")
_SUMMARY_KEYS_HOOK = ("hook_id", "name", "status")


def _summarize_character_for_writer(char: dict[str, Any]) -> dict[str, Any]:
    """writer 分页模式：未触达 character 的极简摘要（仅 id/name/role）。"""
    return {
        "character_id": char.get("character_id"),
        "name": char.get("name"),
        "role": char.get("role"),
        "summary_marker": True,  # 标记摘要项，便于测试与未来 i18n
    }


def _summarize_location_for_writer(loc: dict[str, Any]) -> dict[str, Any]:
    return {
        "location_id": loc.get("location_id"),
        "name": loc.get("name"),
        "summary_marker": True,
    }


def _summarize_faction_for_writer(fac: dict[str, Any]) -> dict[str, Any]:
    return {
        "faction_id": fac.get("faction_id"),
        "name": fac.get("name"),
        "summary_marker": True,
    }


def _summarize_hook_for_writer(h: dict[str, Any]) -> dict[str, Any]:
    return {
        "hook_id": h.get("hook_id"),
        "name": h.get("name"),
        "status": h.get("status"),
    }


def _audience_blocks_writer(audience: str) -> bool:
    """V3.3 P0-2：判断 reveal_policy.audience 是否对 writer 视角构成「不可见」。

    设计决策（任务书口径）：
    - 任务书定义：对 ``visibility='HIDDEN'`` + ``status='planned'`` + ``audience``
      含 ``'reader'`` 的 reveal_policy → 实体从 writer 裁剪后集合移除（连摘要
      也不留，避免 prompt 注入时泄露）。
    - writer 视角 = 通用读者（无角色绑定），按 audience 中是否含 ``reader`` 判定
      「这条 policy 的受众是否覆盖 writer」：
        * audience 含 ``reader`` → writer 属于受众 → 触发过滤（HIDDEN 实体不
          应在 writer payload 中泄露）；
        * audience 仅含 ``character:<id>``（无 reader）→ writer 不属于该受众
          → 不触发过滤（writer 不需为此策略担忧；但若实体 visibility=HIDDEN
          且无任何 reader-audience policy，则仍按既有逻辑处理）；
        * 空 / 未知 → 保守按"不触发"处理。

    返回 True 表示该 policy 对 writer 构成可见性阻断。
    """
    if not isinstance(audience, str) or not audience.strip():
        return False
    parts = [p.strip() for p in audience.split(",") if p.strip()]
    if not parts:
        return False
    has_reader = any(p == "reader" for p in parts)
    return has_reader


def _filter_hidden_by_reveal_policies(
    db_path: str | Path,
    project_id: str | None,
    payload: dict[str, Any],
) -> int:
    """V3.3 P0-2 知识权限补全：HIDDEN 实体按 reveal_policies 二次过滤（writer 上下文）。

    规则：
    - ``reveal_policies`` 中存在 ``status='planned'`` 且 ``audience`` 含 ``'reader'``
      的策略，策略对应实体 ``visibility='HIDDEN'`` 时，该实体从裁剪后集合中**移除**（连
      摘要也不留——摘要仍会泄露名字/id，可能触发 writer 误用）。
    - 适用范围：writer paged 装配下的 ``character_state_excerpts`` 与
      ``world_state_excerpts.locations / .active_factions``（L0 world_rules 与 L2
      信号不动）。
    - **observer 路径不动**：observer 是作者视角，需要看到 HIDDEN 实体；详见
      ``build_observer_input`` docstring「设计决策」节。
    - **零破坏**：无任何 planned reader-policy 时，函数快速返回 0，payload 不变。

    返回：被移除的实体数（用于 stats.hidden_filtered）。
    """
    if not project_id:
        return 0
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001
        return 0
    try:
        rows = conn.execute(
            """
            SELECT target_kind, target_id, audience FROM reveal_policies
            WHERE project_id = ? AND status = 'planned'
            """,
            (project_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        # 极老库（0014 未跑）→ 表不存在 → 不阻断装配
        return 0
    finally:
        conn.close()

    # 收集「planned reader-audience policy」对应的实体 ID；按 kind 分桶
    # audience 字段语义（0014 DDL DEFAULT 'reader'）：
    #   - 'reader' / 包含 'reader' 子串 → 全 reader 视角可见 → writer 不应注入
    #   - 'character:<id>' / 包含 'character:<id>' 子串 → 仅该角色视角可见
    #     → writer（无角色绑定）同样不应注入（视为对 writer 不可见，与 reader
    #     等价的"非 writer"读者视角；保守策略：含 reader 或 character:* 任一即过滤）
    #   - 复杂混合由 comma-split 后逐项判断
    planned_chars: set[str] = set()
    planned_locs: set[str] = set()
    planned_facs: set[str] = set()
    for r in rows:
        kind = r["target_kind"]
        tid = r["target_id"]
        aud = r["audience"] or ""
        if not isinstance(kind, str) or not isinstance(tid, str):
            continue
        if not _audience_blocks_writer(aud):
            continue
        if kind == "character":
            planned_chars.add(tid)
        elif kind == "location":
            planned_locs.add(tid)
        elif kind == "faction":
            planned_facs.add(tid)
        # world_rule/event/hook/debt/relationship 在 writer 装配无 excerpt 输出，不参与

    if not (planned_chars or planned_locs or planned_facs):
        return 0

    # 校验实体本身 visibility='HIDDEN'（planned policy 不一定作用于 HIDDEN 实体——
    # 这里只过滤「既被 policy 约束 planned+reader 又是 HIDDEN」的子集，避免误删）
    try:
        conn2 = get_connection(db_path)
    except Exception:  # noqa: BLE001
        return 0
    try:
        hidden_chars: set[str] = set()
        if planned_chars:
            placeholders = ",".join("?" for _ in planned_chars)
            for r in conn2.execute(
                f"SELECT character_id FROM characters "
                f"WHERE visibility='HIDDEN' AND character_id IN ({placeholders})",
                list(planned_chars),
            ).fetchall():
                hidden_chars.add(r["character_id"])
        hidden_locs: set[str] = set()
        if planned_locs:
            placeholders = ",".join("?" for _ in planned_locs)
            for r in conn2.execute(
                f"SELECT location_id FROM locations "
                f"WHERE visibility='HIDDEN' AND location_id IN ({placeholders})",
                list(planned_locs),
            ).fetchall():
                hidden_locs.add(r["location_id"])
        hidden_facs: set[str] = set()
        if planned_facs:
            placeholders = ",".join("?" for _ in planned_facs)
            for r in conn2.execute(
                f"SELECT faction_id FROM factions "
                f"WHERE visibility='HIDDEN' AND faction_id IN ({placeholders})",
                list(planned_facs),
            ).fetchall():
                hidden_facs.add(r["faction_id"])
    except sqlite3.OperationalError:
        return 0
    finally:
        conn2.close()

    if not (hidden_chars or hidden_locs or hidden_facs):
        return 0

    removed = 0

    # character_state_excerpts：移除 hidden chars（含 touched/summary 两种形态）
    chars_in = payload.get("character_state_excerpts")
    if isinstance(chars_in, list) and hidden_chars:
        kept = []
        for c in chars_in:
            if isinstance(c, dict) and c.get("character_id") in hidden_chars:
                removed += 1
                continue
            kept.append(c)
        payload["character_state_excerpts"] = kept

    # world_state_excerpts.locations
    world_in = payload.get("world_state_excerpts")
    if isinstance(world_in, dict) and hidden_locs:
        locs_in = world_in.get("locations")
        if isinstance(locs_in, list):
            kept = []
            for loc in locs_in:
                if isinstance(loc, dict) and loc.get("location_id") in hidden_locs:
                    removed += 1
                    continue
                kept.append(loc)
            world_in["locations"] = kept

    # world_state_excerpts.active_factions
    if isinstance(world_in, dict) and hidden_facs:
        facs_in = world_in.get("active_factions")
        if isinstance(facs_in, list):
            kept = []
            for f in facs_in:
                if isinstance(f, dict) and f.get("faction_id") in hidden_facs:
                    removed += 1
                    continue
                kept.append(f)
            world_in["active_factions"] = kept

    return removed


def _build_writer_input_paged(
    db_path: str | Path,
    chapter_id: str,
    scene_plan: dict[str, Any],
    target_word_count: int,
    *,
    keep_recent_commits: int = _WRITER_KEEP_RECENT_COMMITS,
    resolved_history_keep: int = _WRITER_RESOLVED_HOOKS_KEEP,
    relevance_trim: bool = True,
) -> dict[str, Any]:
    """writer 分页模式装配（L0/L1/L2 裁剪）。

    复用 :func:`_build_writer_input_uncached` 取得 full payload 后，按 touched 集合
    与 hook 状态机裁剪 character/world_state_excerpts 与 hook_ledger_excerpt，
    再注入 ``context_mode`` 与 ``context_paging_stats``。

    P2 Context Engine：通过 ``relevance_trim`` 参数让分页模式同样经过/跳过
    章节级相关性裁剪；裁剪顺序在分页裁剪之前（``_build_writer_input_uncached``
    内部已完成），因此分页 stats 统计的是 relevance_trim 之后的二次裁剪。
    """
    full_payload = _build_writer_input_uncached(
        db_path, chapter_id, scene_plan, target_word_count,
        relevance_trim=relevance_trim,
    )
    # 裁剪前快照：仅保留被裁剪的 3 个键，便于 stats 体积量化
    # （深拷贝防止后续 in-place 修改干扰）。
    _snapshot_pre_trim: dict[str, Any] = {
        "character_state_excerpts": _safe_copy(full_payload.get("character_state_excerpts")),
        "world_state_excerpts": _safe_copy(full_payload.get("world_state_excerpts")),
        "hook_ledger_excerpt": _safe_copy(full_payload.get("hook_ledger_excerpt")),
    }

    # 1. 收集 touched 实体（DB IO 失败 → 空集合 → 全部走摘要路径，保安全）
    project_id = _peek_project_id_from_chapter(db_path, chapter_id)
    touched: dict[str, set[str]] | None = None
    if project_id:
        conn = get_connection(db_path)
        try:
            touched = _collect_touched_entity_ids(
                conn, project_id, keep_recent_commits=keep_recent_commits,
            )
        except sqlite3.Error:
            touched = None
        finally:
            conn.close()
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

    stats: dict[str, Any] = {
        "context_mode": "paged",
        "keep_recent_commits": keep_recent_commits,
        "resolved_history_keep": resolved_history_keep,
        "characters_full": 0,
        "characters_summary": 0,
        "locations_full": 0,
        "locations_summary": 0,
        "factions_full": 0,
        "factions_summary": 0,
        "world_rules_full": 0,
        "world_rules_summary": 0,
        "hooks_open": 0,
        "hooks_resolved_kept": 0,
        "hooks_resolved_trimmed": 0,
        "total_size_bytes_before": 0,
        "total_size_bytes_after": 0,
        # V3.3 P0-2：被 reveal_policies planned+reader-audience 规则移除的
        # HIDDEN 实体数（character / location / faction）。无任何匹配 policy 时
        # 保持 0，行为与既有实现完全一致（零破坏）。
        "hidden_filtered": 0,
    }

    # 2. character_state_excerpts：touched 全量 + 其余摘要
    chars_in = full_payload.get("character_state_excerpts") or []
    chars_out: list[dict[str, Any]] = []
    if isinstance(chars_in, list):
        for c in chars_in:
            if not isinstance(c, dict):
                continue
            cid = c.get("character_id")
            if isinstance(cid, str) and cid in touched_chars:
                chars_out.append(c)
                stats["characters_full"] += 1
            else:
                chars_out.append(_summarize_character_for_writer(c))
                stats["characters_summary"] += 1
    full_payload["character_state_excerpts"] = chars_out

    # 3. world_state_excerpts.locations / .active_factions / .world_rules_relevant
    world_in = full_payload.get("world_state_excerpts") or {}
    if isinstance(world_in, dict):
        touched_locs = touched.get("locations", set())
        locs_in = world_in.get("locations") or []
        locs_out: list[dict[str, Any]] = []
        if isinstance(locs_in, list):
            for loc_item in locs_in:
                if not isinstance(loc_item, dict):
                    continue
                lid = loc_item.get("location_id")
                if isinstance(lid, str) and lid in touched_locs:
                    locs_out.append(loc_item)
                    stats["locations_full"] += 1
                else:
                    locs_out.append(_summarize_location_for_writer(loc_item))
                    stats["locations_summary"] += 1
        world_in["locations"] = locs_out

        touched_facs = touched.get("factions", set())
        facs_in = world_in.get("active_factions") or []
        facs_out: list[dict[str, Any]] = []
        if isinstance(facs_in, list):
            for f in facs_in:
                if not isinstance(f, dict):
                    continue
                fid = f.get("faction_id")
                if isinstance(fid, str) and fid in touched_facs:
                    facs_out.append(f)
                    stats["factions_full"] += 1
                else:
                    facs_out.append(_summarize_faction_for_writer(f))
                    stats["factions_summary"] += 1
        world_in["active_factions"] = facs_out

        # world_rules_relevant 全量保留（L0 硬设定；统计 full=总数 summary=0）
        rules_in = world_in.get("world_rules_relevant") or []
        if isinstance(rules_in, list):
            stats["world_rules_full"] = len(rules_in)
        full_payload["world_state_excerpts"] = world_in

    # V3.3 P0-2 知识权限补全：HIDDEN 实体按 reveal_policies 二次过滤。
    # 仅当存在「status='planned' 且 audience 含 'reader'」的 policy 时，从
    # character_state_excerpts / world_state_excerpts.locations / .active_factions
    # 中**移除**该实体（连摘要也不留——摘要仍会泄露名字/id 触发 prompt 注入）。
    # 无任何 planned reader-policy 时行为与原实现完全一致（零破坏）。
    hidden_filtered = _filter_hidden_by_reveal_policies(
        db_path, project_id, full_payload,
    )
    stats["hidden_filtered"] = hidden_filtered

    # 4. hook_ledger_excerpt：writer 装配当前仅含 OPEN/ACTIVE/ESCALATED
    # 状态（``_hook_ledger_excerpt`` 函数本就只查 planted 状态），等价于
    # 任务书「open 全量」。resolved hooks 在 writer 不直接注入（伏笔管理归
    # director，writer 透过 director_plan.hook_handling 间接获取）——故本
    # 函数对 hook_ledger_excerpt 不做 resolved 截断（与 observer 的
    # ``previous_state.hooks`` 口径不同：observer 处理全量 Canonical State，
    # writer 处理 director 提炼后的摘要）。仅统计 open hooks 数量。
    hooks_in_raw = full_payload.get("hook_ledger_excerpt") or []
    if isinstance(hooks_in_raw, list):
        stats["hooks_open"] = sum(
            1 for h in hooks_in_raw
            if isinstance(h, dict) and isinstance(h.get("status"), str)
            and h.get("status") in _HOOK_OPEN_STATUSES
        )
    else:
        stats["hooks_open"] = 0
    stats["hooks_resolved_kept"] = 0
    stats["hooks_resolved_trimmed"] = 0

    # 5. 注入 context_mode + stats；体积量化（before/after）
    # 体积仅统计被裁剪的 3 个键（character_state_excerpts +
    # world_state_excerpts + hook_ledger_excerpt），与 observer 「previous_state」
    # 口径一致；其他键（director_plan / scene_plan / recent_prose /
    # author_style_samples / recalled_passages / style_constraints 等）属 L2
    # 章节专属信号，不在裁剪范围。
    try:
        before_bytes = sum(
            len(json.dumps(c, ensure_ascii=False))
            for c in (
                _snapshot_pre_trim.get("character_state_excerpts"),
                _snapshot_pre_trim.get("world_state_excerpts"),
                _snapshot_pre_trim.get("hook_ledger_excerpt"),
            )
        )
        after_bytes = sum(
            len(json.dumps(full_payload.get(k), ensure_ascii=False))
            for k in ("character_state_excerpts", "world_state_excerpts", "hook_ledger_excerpt")
        )
    except (TypeError, ValueError):
        before_bytes = 0
        after_bytes = 0
    stats["total_size_bytes_before"] = before_bytes
    stats["total_size_bytes_after"] = after_bytes

    full_payload["context_mode"] = "paged"
    full_payload["context_paging_stats"] = stats
    return full_payload


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


# ---------------------------------------------------------------------------
# Observer 输入快照分代裁剪（M3 引擎包：解决 ch056 110KB+ 全量快照阻塞 LLM）
# ---------------------------------------------------------------------------
# 设计要点：
# - ``snapshot_mode='full'``（默认）保持现有行为零变化；调用方零感知。
# - ``snapshot_mode='trimmed'`` 在 payload["previous_state"] 中只保留「最近 N 个
#   commit 的 delta 中被 touch 过的实体全量字段」+「其余实体仅保留摘要」；
#   hooks/debts 仅保留 open/active/escalated/acknowledged 全量，resolved/paid
#   类只留最近 5 条（按 hook_id/debt_id 字典序模拟「最近」）。
# - 顶层元信息（state_version 等）不动。
# - 裁剪 stats 附在 config 同级 payload["snapshot_trim_stats"]，便于观测；
#   并在 previous_state 同级注入 ``snapshot_mode="trimmed"`` 提示 observer
#   当前上下文是裁剪版（不能依赖旧的全量结构假设）。
# ---------------------------------------------------------------------------


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


__all__ = [
    "build_director_input",
    "build_writer_input",
    "build_observer_input",
    "_load_recent_event_ids",
    "_DEFAULT_RECENT_EVENT_IDS_LIMIT",
]
