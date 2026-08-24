"""Context Engine dry-run 预览（Sprint 13 下半 + V2.0 Wave C 任务一/二）。

- :func:`preview_context` —— 复用 ``build_director_input`` /
  ``build_writer_input`` / ``build_observer_input`` 的纯装配结果，按 L0/L1/L2
  分层汇总 token 估算与条目清单；**只读、不调 LLM、不写库**。

设计要点（MVP）：
- 不重做 token 预算分配（MVP 沿用 builder 的"按字段全量塞入"口径；
  完整 L0-L9 留后续 Sprint，详见 ``packages/core/context_engine/README.md``）。
- ``token_estimate`` 用 ``len(json.dumps(value, ensure_ascii=False)) // 4``（粗略：
  4 字节 ≈ 1 token；中文场景粗略近似，足以做上限告警）。
- ``token_budget`` 硬编码 8000（与 PRD §124 target_word_count=2200 × ~3.6 对齐，
  MVP 估算口径；后续若引入真实分词器再替换）。
- V2.0 Wave C 任务二：preview dry-run 同样受益于 builders 的进程内缓存；
  连续两次预览第二次命中缓存，token 估算完全一致。
- V2.0 Wave C 任务一：L1 增加 ``recalled_passage`` 新 kind（FTS5 召回片段）展示。
- 异常透传：chapter / project 不存在 → ``ValueError``（由 router 转 404）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .builders import (
    build_director_input,
    build_observer_input,
    build_writer_input,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# MVP token 预算上限（详细分词留后续 Sprint）。
_TOKEN_BUDGET = 8000

# 中英文混合文本粗略估算：4 字节 ≈ 1 token；空对象保证至少 1 token 便于 UI 显式标 0 vs 1。
_TOKEN_DIVISOR = 4


def _token_estimate(value: Any) -> int:
    """粗略 token 估算：序列化字节数 / 4，至少 1。"""
    try:
        serialized = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = str(value)
    return max(1, len(serialized) // _TOKEN_DIVISOR)


def _push_item(
    items: list[dict[str, Any]],
    *,
    kind: str,
    id_: str,
    name: str,
    **extra: Any,
) -> None:
    items.append({"kind": kind, "id": id_, "name": name, **extra})


# ---------------------------------------------------------------------------
# 层抽取
# ---------------------------------------------------------------------------


def _extract_l0(director: dict[str, Any]) -> list[dict[str, Any]]:
    """L0 项目元数据：chapter / project / knowledge_permissions / constraints。"""
    items: list[dict[str, Any]] = []
    chap = director.get("chapter") or {}
    proj = director.get("project") or {}
    if chap.get("chapter_id"):
        items.append({
            "kind": "chapter",
            "id": str(chap.get("chapter_id")),
            "name": f"第 {chap.get('chapter_id')} 章（target={chap.get('target_word_count')}）",
        })
    if proj.get("project_id"):
        items.append({
            "kind": "project",
            "id": str(proj.get("project_id")),
            "name": str(proj.get("name") or proj.get("project_id")),
        })
    kp = director.get("knowledge_permissions") or {}
    if kp:
        items.append({
            "kind": "knowledge_permissions",
            "id": "_kp",
            "name": f"visibility={kp.get('your_visibility')}",
        })
    constraints = director.get("constraints") or {}
    if constraints:
        items.append({
            "kind": "constraints",
            "id": "_constraints",
            "name": f"forbidden_topics={len(constraints.get('forbidden_topics') or [])}",
        })
    return items


def _extract_l1(
    director: dict[str, Any],
    writer: dict[str, Any],
) -> list[dict[str, Any]]:
    """L1 业务摘要：角色 / 世界 / 伏笔 / 债务 / 参照系。

    V2.0 Wave B 任务二：每条目带 ``injection`` 状态标记（full / summary / suppressed）。
    - ``full``：完整注入（标记同原文）；
    - ``summary``：仅一行摘要（name + role/statement），不展开 data_json / current_state；
    - ``suppressed``：never 模式不注入（仅 preview 列表保留可见）。

    suppressed 项被 ``_apply_injection_policy`` 统一汇集到顶层 ``_suppressed_*`` 列表；
    这里也展示（kind=suppressed_character / suppressed_location / suppressed_faction）。
    """
    items: list[dict[str, Any]] = []

    # 角色：director 与 writer 同源；以 director 为准（保留 source 标记）。
    seen_chars: set[str] = set()
    for src_name, src in (("director", director), ("writer", writer)):
        for c in (src.get("character_state_excerpts") or []):
            cid = str(c.get("character_id") or "")
            if not cid or cid in seen_chars:
                continue
            seen_chars.add(cid)
            injection = c.get("_injection", "full")
            if injection == "summary":
                _push_item(
                    items,
                    kind="character",
                    id_=cid,
                    name=str(c.get("name") or cid),
                    role=c.get("role"),
                    source=src_name,
                    injection="summary",
                    summary_line=str(c.get("summary_line") or "")[:80],
                )
            else:
                _push_item(
                    items,
                    kind="character",
                    id_=cid,
                    name=str(c.get("name") or cid),
                    role=c.get("role"),
                    source=src_name,
                    injection="full",
                )
    # suppressed 角色（director 集中后转发）
    world = director.get("world_state_excerpts") or {}
    char_world = writer.get("character_state_excerpts") or []
    # suppressed 角色可能在任一 source 中；以 director 为准（其 trigger corpus 更广）
    for c in char_world:
        if c.get("_injection") == "suppressed":
            _push_item(
                items,
                kind="suppressed_character",
                id_=str(c.get("character_id") or ""),
                name=str(c.get("name") or ""),
                injection="suppressed",
            )
    # 兼容 director 写入 _suppressed_characters 列表
    for c in director.get("character_state_excerpts") or []:
        for sub in (c.get("_suppressed_characters") or []):
            _push_item(
                items,
                kind="suppressed_character",
                id_=str(sub.get("character_id") or sub.get("name") or ""),
                name=str(sub.get("name") or ""),
                injection="suppressed",
            )

    for loc in world.get("locations") or []:
        injection = loc.get("_injection", "full")
        if injection == "summary":
            _push_item(
                items, kind="location", id_=str(loc.get("location_id")),
                name=str(loc.get("name") or loc.get("location_id")),
                injection="summary",
                summary_line=str(loc.get("summary_line") or "")[:80],
            )
        else:
            _push_item(
                items, kind="location", id_=str(loc.get("location_id")),
                name=str(loc.get("name") or loc.get("location_id")),
                injection="full",
            )
    for fac in world.get("active_factions") or []:
        injection = fac.get("_injection", "full")
        if injection == "summary":
            _push_item(
                items, kind="faction", id_=str(fac.get("faction_id")),
                name=str(fac.get("name") or fac.get("faction_id")),
                injection="summary",
                summary_line=str(fac.get("summary_line") or "")[:80],
            )
        else:
            _push_item(
                items, kind="faction", id_=str(fac.get("faction_id")),
                name=str(fac.get("name") or fac.get("faction_id")),
                injection="full",
            )
    # suppressed 地点 / 势力（director 顶层 _suppressed_*）
    for sub in world.get("_suppressed_locations") or []:
        _push_item(
            items,
            kind="suppressed_location",
            id_=str(sub.get("location_id") or sub.get("name") or ""),
            name=str(sub.get("name") or ""),
            injection="suppressed",
        )
    for sub in world.get("_suppressed_factions") or []:
        _push_item(
            items,
            kind="suppressed_faction",
            id_=str(sub.get("faction_id") or sub.get("name") or ""),
            name=str(sub.get("name") or ""),
            injection="suppressed",
        )
    for rule in world.get("world_rules_relevant") or []:
        _push_item(
            items, kind="world_rule", id_=str(rule.get("world_rule_id")),
            name=str(rule.get("name") or rule.get("world_rule_id")),
            injection="full",
        )

    plot = director.get("plot_graph_excerpt") or {}
    for ev in plot.get("upcoming_planned_events") or []:
        _push_item(
            items, kind="plot_event", id_=str(ev.get("event_id")),
            name=str(ev.get("event_id")), type=ev.get("type"),
        )

    for h in director.get("hook_ledger_excerpt") or []:
        _push_item(
            items, kind="hook", id_=str(h.get("hook_id")),
            name=str(h.get("name") or h.get("hook_id")),
            status=h.get("status"), importance=h.get("importance"),
        )

    for d in director.get("narrative_debt_excerpt") or []:
        _push_item(
            items, kind="debt", id_=str(d.get("debt_id")),
            name=str(d.get("description") or d.get("debt_id"))[:80],
            severity=d.get("severity"), status=d.get("status"),
        )

    canon = director.get("reference_canon")
    if isinstance(canon, dict):
        logline = canon.get("logline") or "(无 logline)"
        _push_item(
            items, kind="reference_canon",
            id_=str(canon.get("canon_id") or "_canon"),
            name=str(logline)[:80],
            spine_count=len(canon.get("spine") or []),
            payoff_count=len(canon.get("payoff_list") or []),
        )

    # Sprint 14：摘要链（Sprint 14-A）+ 前章尾段（L1 「上一章结尾」）。
    for s in director.get("recent_chapter_summaries") or []:
        _push_item(
            items,
            kind="chapter_summary",
            id_=f"sum_{s.get('chapter_id')}",
            name=f"第 {s.get('chapter_no')} 章摘要：{str(s.get('summary') or '')[:60]}",
            chapter_no=s.get("chapter_no"),
        )
    prev_tail = director.get("previous_chapter_tail") or {}
    if prev_tail.get("tail_text"):
        _push_item(
            items,
            kind="previous_chapter_tail",
            id_=f"tail_{prev_tail.get('chapter_id', '_none')}",
            name=f"第 {prev_tail.get('chapter_no')} 章结尾 {len(prev_tail.get('tail_text') or '')} 字",
            source_len=len(prev_tail.get("tail_text") or ""),
        )

    # Sprint 14：开放伏笔清单（含 overdue 标注）。
    for h in director.get("open_foreshadow_list") or []:
        _push_item(
            items,
            kind="open_foreshadow",
            id_=str(h.get("hook_id")),
            name=str(h.get("name") or h.get("hook_id"))[:60],
            status=h.get("status"),
            overdue=bool(h.get("overdue")),
            importance=h.get("importance"),
        )

    # V2.0 Wave C 任务一：FTS5 召回片段（章节正文跨长程呼应）。
    # director 与 writer 共享同一关键词召回，preview 在 L1 各展示一次（去重）。
    seen_recall: set[str] = set()
    for src in (director, writer):
        for r in src.get("recalled_passages") or []:
            cid = str(r.get("chapter_id") or "")
            if not cid or cid in seen_recall:
                continue
            seen_recall.add(cid)
            _push_item(
                items,
                kind="recalled_passage",
                id_=cid,
                name=f"第 {r.get('chapter_no')} 章命中片段（rank={r.get('rank', 0):.2f}）",
                chapter_no=r.get("chapter_no"),
                snippet_len=len(str(r.get("snippet") or "")),
            )

    return items


def _extract_l2(
    director: dict[str, Any],
    writer: dict[str, Any],
    observer: dict[str, Any],
) -> list[dict[str, Any]]:
    """L2 章节专属：作者意图 / 故事状态快照 / 计划 / 草稿 / 最近正文 / 风格约束。"""
    items: list[dict[str, Any]] = []

    intent = director.get("author_intent") or {}
    if intent.get("raw"):
        _push_item(
            items, kind="author_intent", id_="_intent",
            name=str(intent.get("raw"))[:120],
        )

    snap = director.get("story_state_snapshot") or {}
    if snap:
        _push_item(
            items, kind="story_state_snapshot", id_="_snap",
            name=f"state_version={snap.get('current_state_version')} chapter={snap.get('current_chapter')}",
        )

    plan = writer.get("director_plan") or {}
    if plan:
        _push_item(
            items, kind="director_plan", id_="_plan",
            name=str(plan.get("chapter_goal") or "(无 chapter_goal)")[:80],
            beats=len(plan.get("key_beats") or []),
        )

    scene_plan = writer.get("scene_plan")
    if scene_plan:
        _push_item(
            items, kind="scene_plan", id_="_scene",
            name=f"scene_plan keys={sorted(map(str, scene_plan.keys()))}",
        )

    recent = writer.get("recent_prose") or {}
    tail = recent.get("last_chapter_excerpt") or ""
    if tail:
        _push_item(
            items, kind="recent_prose", id_="_recent",
            name="上一章末尾 500 字", source_len=len(tail),
        )

    # Sprint 15 / V1.3：作者文风样例注入 writer；preview 在 L2 同步展示。
    style_samples = writer.get("author_style_samples") or {}
    if isinstance(style_samples, dict):
        for s in (style_samples.get("samples") or []):
            if not isinstance(s, dict):
                continue
            _push_item(
                items,
                kind="author_style_sample",
                id_=str(s.get("sample_id") or "_sty"),
                name=str(s.get("title") or s.get("sample_id") or "文风样例"),
                excerpt_len=len(s.get("excerpt") or ""),
            )

    style = writer.get("style_constraints")
    if style:
        _push_item(
            items, kind="style_constraints", id_="_style",
            name=f"pov={style.get('pov')} dialogue_ratio={style.get('dialogue_ratio')}",
        )

    chap = observer.get("chapter") or {}
    draft = chap.get("draft_text") or ""
    if draft:
        _push_item(
            items, kind="draft_text", id_="_draft",
            name=f"本章草稿 {len(draft)} 字", source_len=len(draft),
        )

    dp_sum = observer.get("director_plan_summary") or {}
    if dp_sum:
        _push_item(
            items, kind="director_plan_summary", id_="_dpsum",
            name=str(dp_sum.get("chapter_goal") or "(无 chapter_goal)")[:80],
        )

    prev_state = observer.get("previous_state")
    if prev_state:
        _push_item(
            items, kind="previous_state", id_="_prevstate",
            name=f"version={observer.get('previous_state_version')}",
        )

    return items


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


def preview_context(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
    *,
    author_intent: str = "",
    scene_plan: dict[str, Any] | None = None,
    target_word_count: int = 2200,
) -> dict[str, Any]:
    """dry-run：返回 chapter 关联的 LLM context 装配预览（只读）。

    返回结构：
        {
            "chapter_id": str,
            "project_id": str,
            "agents": ["director", "writer", "observer"],
            "layers": [
                {"id": "L0", "label": ..., "token_estimate": int,
                "items": [{kind, id, name, ...}], "truncated": False},
                ...
            ],
            "total_tokens": int,
            "token_budget": 8000,
            "within_budget": bool,
        }

    异常：
        ``ValueError`` —— chapter 或 project 不存在（由 router 转 404）。
    """
    director = build_director_input(
        db_path, project_id, chapter_id, author_intent,
        target_word_count=target_word_count,
    )
    writer = build_writer_input(
        db_path, chapter_id, scene_plan or {},
        target_word_count=target_word_count,
    )
    observer = build_observer_input(db_path, chapter_id)

    l0_items = _extract_l0(director)
    l1_items = _extract_l1(director, writer)
    l2_items = _extract_l2(director, writer, observer)

    layers: list[dict[str, Any]] = [
        {
            "id": "L0",
            "label": "项目元数据（chapter / project / 知识权限 / 约束）",
            "token_estimate": _token_estimate({
                "chapter": director.get("chapter"),
                "project": director.get("project"),
                "knowledge_permissions": director.get("knowledge_permissions"),
                "constraints": director.get("constraints"),
            }),
            "items": l0_items,
            "truncated": False,
        },
        {
            "id": "L1",
            "label": "业务摘要（角色 / 世界 / 伏笔 / 债务 / 参照系 / 摘要链 / 前章尾段 / 开放伏笔 / 召回片段）",
            "token_estimate": _token_estimate({
                "character_state_excerpts": director.get("character_state_excerpts"),
                "world_state_excerpts": director.get("world_state_excerpts"),
                "plot_graph_excerpt": director.get("plot_graph_excerpt"),
                "hook_ledger_excerpt": director.get("hook_ledger_excerpt"),
                "narrative_debt_excerpt": director.get("narrative_debt_excerpt"),
                "reference_canon": director.get("reference_canon"),
                "recent_chapter_summaries": director.get("recent_chapter_summaries"),
                "previous_chapter_tail": director.get("previous_chapter_tail"),
                "open_foreshadow_list": director.get("open_foreshadow_list"),
                "recalled_passages": director.get("recalled_passages"),
            }),
            "items": l1_items,
            "truncated": False,
        },
        {
            "id": "L2",
            "label": "章节专属（计划 / 草稿 / 最近正文 / 文风样例 / 风格约束）",
            "token_estimate": _token_estimate({
                "author_intent": director.get("author_intent"),
                "story_state_snapshot": director.get("story_state_snapshot"),
                "director_plan": writer.get("director_plan"),
                "scene_plan": writer.get("scene_plan"),
                "recent_prose": writer.get("recent_prose"),
                "author_style_samples": writer.get("author_style_samples"),
                "style_constraints": writer.get("style_constraints"),
                "chapter_draft": (observer.get("chapter") or {}).get("draft_text"),
                "director_plan_summary": observer.get("director_plan_summary"),
            }),
            "items": l2_items,
            "truncated": False,
        },
    ]

    total_tokens = sum(layer["token_estimate"] for layer in layers)

    return {
        "chapter_id": chapter_id,
        "project_id": project_id,
        "agents": ["director", "writer", "observer"],
        "layers": layers,
        "total_tokens": total_tokens,
        "token_budget": _TOKEN_BUDGET,
        "within_budget": total_tokens <= _TOKEN_BUDGET,
    }


__all__ = ["preview_context"]