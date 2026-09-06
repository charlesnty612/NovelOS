"""Writer 输入装配——build_writer_input / _build_writer_input_paged
（拆分自 builders.py，2026-09-06 审查批次三）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.quality.wordcount import resolve_band_config, word_band

from .builders_common import (
    _AUTHOR_STYLE_SAMPLES_INSTRUCTION,
    _DEFAULT_STYLE_CONSTRAINTS,
    _DEFAULT_TARGET_WORD_COUNT,
    _HOOK_OPEN_STATUSES,
    _author_style_samples,
    _build_trigger_corpus,
    _character_state_excerpts,
    _collect_touched_entity_ids,
    _fingerprint_word_band_json,
    _inject_scene_word_budget,
    _peek_active_canon_id,
    _peek_chapter_no_state_version,
    _peek_project_id_from_chapter,
    _peek_project_word_band_json,
    _recent_prose_tail,
    _row_to_chapter,
    _safe_copy,
    _world_state_excerpts,
)
from .cache import (
    _FINGERPRINT_UNCACHED,
    _cache_get,
    _cache_put,
    _fingerprint_scene_plan,
)
from .canon import (
    _REFERENCE_CANON_CONSUMER_WRITER,
    _reference_canon_excerpt,
)
from .director_input import (
    _recall_passages,
)
from .relevance import (
    _apply_relevance_trim,
    _resolve_relevance_trim,
)


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
        # V3.7：项目级字数带覆盖（projects.word_band_json）。
        # 列缺失 / 异常 / 解析失败 → 视为无覆盖（与模块默认一致，零行为变化）。
        word_band_overrides: dict | None = None
        try:
            prow = conn.execute(
                "SELECT word_band_json FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            prow = None
        if prow is not None and prow["word_band_json"]:
            try:
                parsed = json.loads(prow["word_band_json"])
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                word_band_overrides = parsed
        # Reference Canon：writer 消费 style_params（文风参数）。与 director 共用连接。
        reference_canon_inject, reference_canon_audit = _reference_canon_excerpt(
            conn, project_id, consumer=_REFERENCE_CANON_CONSUMER_WRITER,
        )
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

    # V3.7：writer payload 注入字数带——支持项目级覆盖（projects.word_band_json）。
    # 无覆盖（None / 列缺失 / JSON 非法）→ resolve_band_config(None) 返回模块默认，
    # word_band(...) 输出与原硬编码 word_band(target_word_count) 逐字段一致。
    _wb_low, _wb_high = word_band(target_word_count, **resolve_band_config(word_band_overrides))
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
        "scene_plan": _inject_scene_word_budget(scene_plan, target_word_count),
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

    # Reference Canon 注入：writer 消费 style_params（文风参数）。
    # 与 director 一致——无 canon 时不出现 reference_canon / _reference_canon_consumed。
    if reference_canon_inject is not None:
        payload["reference_canon"] = reference_canon_inject
    if reference_canon_audit is not None:
        payload["_reference_canon_consumed"] = reference_canon_audit

    # P2 Context Engine：章节级相关性裁剪。默认开启，可在调用层 / 环境变量关闭。
    _apply_relevance_trim(
        payload,
        relevance_trim=relevance_trim,
        plan_json=director_plan,
        scene_plan=scene_plan,
    )
    return payload


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
    # V3.7：缓存键追加 wb_fp（项目字数带覆盖指纹）——同一项目改 word_band_json 后
    # payload.chapter.word_band 会变；旧键命中会拿到陈旧 word_band。无覆盖项目指纹恒为
    # "none"，与现状行为完全一致。
    wb_raw = _peek_project_word_band_json(db_path, project_id)
    wb_fp = _fingerprint_word_band_json(wb_raw)
    # F5 修复：缓存键追加 active canon_id；拆书落新 canon 后旧 writer 装配缓存自然失效。
    active_canon_id = _peek_active_canon_id(db_path, project_id) or "__none__"
    cache_key = (
        project_id or "", state_version, chapter_no, "writer",
        scene_fp, context_mode, relevance_flag, wb_fp, active_canon_id,
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
