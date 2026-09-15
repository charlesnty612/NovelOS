"""Director 输入装配——build_director_input 及其缓存内实现
（拆分自 builders.py，2026-09-06 审查批次三）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.core.db import get_connection

from .builders_common import (
    _DEFAULT_TARGET_WORD_COUNT,
    _DIRECTOR_SUMMARY_TOKEN_BUDGET,
    _attach_assembly_meta,
    _build_trigger_corpus,
    _character_state_excerpts,
    _genre_pack_excerpt,
    _hook_ledger_excerpt,
    _narrative_debt_excerpt,
    _open_foreshadow_list,
    _peek_chapter_context,
    _plot_graph_excerpt,
    _previous_chapter_tail,
    _project_max_state_version,
    _project_overdue_chapters,
    _recent_chapter_summaries,
    _row_to_chapter,
    _row_to_project,
    _truncate_summaries_to_token_budget,
    _world_state_excerpts,
    resolve_chapter_outline,
)
from .cache import (
    _FINGERPRINT_UNCACHED,
    _cache_get,
    _cache_namespace_tag,
    _cache_put,
    _fingerprint_author_intent,
    _fingerprint_outline_json,
    _fingerprint_plan_json,
)
from .canon import (
    _reference_canon_excerpt,
)


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
    *,
    current_chapter_no: int | None = None,
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
            current_chapter_no=current_chapter_no,
        )
    except Exception:  # noqa: BLE001 —— FTS 虚表不存在等降级
        return []


def _build_director_input_uncached(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
    author_intent: str,
    target_word_count: int,
    *,
    plan_json_override: dict[str, Any] | None = None,
    peek: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """无缓存版 director 装配；build_director_input 用本函数 + 缓存包装。

    ``peek``（V3.9 批次 2.3）：调用方已用 :func:`_peek_chapter_context` 预读的上下文
    （单连接 JOIN 结果）。传了就直接复用其 ``state_version``（省掉一次取快照连接），
    不传则退回同连接轻查询 :func:`_project_max_state_version`。
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

        # story_state_snapshot：只要 state_version。
        # V3.9 批次 2.3：旧实现调 StoryStateService.get_current_state 读 + JSON 解析整份
        # snapshot_json，仅为取这个整数；改为 MAX(state_version) 轻查询（口径见
        # _project_max_state_version），并在 peek 已带入时直接复用。
        if peek is not None:
            state_version = int(peek.get("state_version") or 0)
        else:
            state_version = _project_max_state_version(conn, project_id)

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
        # V3.9 批次 2.1：预算从写死 800 改为具名常量（2000，见 _DIRECTOR_SUMMARY_TOKEN_BUDGET
        # 的最坏情况演算）；截断结果（truncated）随 _assembly_meta.summary_truncated 上报。
        recent_summaries, summaries_truncated = _truncate_summaries_to_token_budget(
            recent_summaries_raw, available_tokens=_DIRECTOR_SUMMARY_TOKEN_BUDGET,
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
        hook_excerpt = _hook_ledger_excerpt(
            conn, project_id, current_chapter_no=current_chapter_no or None)
        debt_excerpt = _narrative_debt_excerpt(
            conn, project_id, current_chapter_no=current_chapter_no or None)
        reference_canon_inject, reference_canon_audit = _reference_canon_excerpt(conn, project_id)
        # 题材库 P1a：项目绑定题材包时的 director 注入段（结构模板摘要 / 爽点类型
        # 清单 + 密度约束文本化 / pacing 摘要）；未绑定 → (None, None) 零注入。
        genre_pack_inject, genre_pack_audit = _genre_pack_excerpt(conn, project_id)
    finally:
        conn.close()

    # V2.0 Wave C 任务一：召回混合层（FTS5）
    # 按当前章节 plan_json 关键词从 chapter_fts 召回 top-3 相关历史片段。
    plan_for_recall = plan_json_override if plan_json_override is not None else (chapter.get("plan_json") or {})
    recalled_passages = _recall_passages(
        db_path, project_id, chapter_id, plan_for_recall,
        current_chapter_no=current_chapter_no or None,
    )

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
            # 0028 大纲槽：策展大纲（chapter_goal / core_conflict / turning_point /
            # key_beats / hook_handling / notes_for_planner）。改造前这段信息**从未
            # 进入 planner 输入**，plan_json 只被当 FTS 召回语料 + 缓存指纹用——
            # 于是 planner 只能顺着 story state 惯性自推计划，项目级大纲形同虚设。
            # 两级取值见 resolve_chapter_outline（outline_json 非空 → 用它；否则回落
            # plan_json 同名字段，存量项目零行为突变）。planner **必须以此为准**，
            # 不得把 story state 里的旧线索引到与 outline 相悖的方向。
            "outline": resolve_chapter_outline(chapter),
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
    # 题材库 P1a：题材包注入段 + 溯源审计（双 slot 与 reference_canon 并存互不覆盖）。
    if genre_pack_inject is not None:
        payload["genre_pack"] = genre_pack_inject
    if genre_pack_audit is not None:
        payload["_genre_pack_consumed"] = genre_pack_audit
    # Sprint 14：摘要链 + 前章尾段 + 开放伏笔清单。
    # 沿用 agent-contracts §3.1「不在权威契约内」的扩展键惯例；无 chapter_summaries 行 /
    # 无 planted 状态伏笔 / 无前章 → 给空列表 / 空 dict，调用方按空态处理。
    payload["recent_chapter_summaries"] = recent_summaries
    payload["previous_chapter_tail"] = previous_chapter_tail
    payload["open_foreshadow_list"] = open_foreshadow
    # V2.0 Wave C 任务一：FTS 召回片段。无索引 / 无命中时为空 list。
    payload["recalled_passages"] = recalled_passages

    # V3.9 批次 2.1：附非注入侧元字段（_assembly_meta，含 token 预算兜底告警 +
    # 摘要链是否被截断）；缓存存的是含 meta 的完整 payload，命中与未命中结果一致。
    return _attach_assembly_meta(payload, summary_truncated=summaries_truncated)


def build_director_input(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
    author_intent: str,
    *,
    target_word_count: int = _DEFAULT_TARGET_WORD_COUNT,
    namespace: str = "",
) -> dict[str, Any]:
    """组装 Director 输入（agent-contracts §3.1 + V2.0 Wave C 任务一 召回 + 任务二 缓存）。

    缓存（V2.0 Wave C 任务二）：L0/L1 装配结果按
    ``(project_id, state_version, chapter_no, "director", plan_fp, active_canon_id,
    intent_fp, target_word_count, genre_pack_ref, ns)`` 键做进程内缓存；
    同一 (project, state_version, chapter, plan_json 内容, canon, 作者意图, 目标字数,
    题材包, 命名空间) 第二次调用直接返回缓存条目。

    V2.0 Wave C P1-1 修复：键追加 ``plan_fp``（chapters.plan_json 原文 sha256[:16]），
    解决 plan_json UPDATE 后 state_version 不变时的脏命中。``plan_fp == 'uncached'``
    时跳过缓存（不可序列化场景），避免脏命中。

    V3.9 批次 1.4 修复：``author_intent`` 与 ``target_word_count`` 都进 payload
    （``author_intent.raw`` / ``chapter.target_word_count``），旧键缺这两个维度会
    互相脏命中。现键含 ``intent_fp``（sha256(author_intent)[:16]，None → 'none'）
    与 ``target_word_count`` 原文（int，与 state_version/chapter_no 同款直接入键）。

    题材库 P1a：键再追加 ``genre_pack_ref``（项目绑定题材包的 ``<pack_id>@<version>``
    指纹；未绑定 → ``'__none__'``）。题材包内容进 payload（``genre_pack`` 段），
    换包 / 换版本（PUT 改 payload 时 version 自增）必须各自 miss，否则旧装配脏命中。

    ``namespace``（关键词参数，默认 ``""``）：键尾命名空间标记。``preview_context``
    传 ``"preview"`` 拆开 dry-run 与生产的键空间，避免预览的空意图 / 默认 2200 字
    条目被生产命中。生产调用不传即保持原行为。

    命中返回**深拷贝**，调用方可安全就地改写（见 :mod:`...cache`）。

    V3.9 批次 2.3：键预判从「2 个 ``_peek_*``（其一带 StoryStateService 整份快照解析）」
    收敛为单连接单条 JOIN 的 :func:`_peek_chapter_context`，结果复用给 uncached 装配
    （``state_version`` 直接带入，不再二次取快照）。

    详细说明见 :func:`_build_director_input_uncached`（无缓存装配实现）与模块顶部注释。
    """
    # 单连接 peek：chapter_no / state_version / plan_json 原文 / word_band / active canon
    # / 绑定题材包指纹（不命中再全量装配；peek 结果一并传给 uncached 复用）
    peek = _peek_chapter_context(db_path, chapter_id, project_id=project_id)
    chapter_no = peek["chapter_no"]
    state_version = peek["state_version"]
    plan_fp = _fingerprint_plan_json(peek["plan_json_raw"])
    # 0028 大纲槽：``chapter.outline`` 段进 payload → 单列键维度（硬规则 2）。
    # 缺该维度会让「改大纲后同一 state_version」脏命中旧装配（正是本次缺陷的同类形状）。
    outline_fp = _fingerprint_outline_json(peek.get("outline_json_raw"))
    # F5 修复：缓存键追加 active canon_id；拆书落新 canon 后旧 director 装配缓存自然失效。
    active_canon_id = peek["active_canon_id"] or "__none__"
    # 题材库 P1a：缓存键追加题材包指纹（``<pack_id>@<version>``，未绑定 → ``__none__``）。
    # 未加该维度 → 换题材包 / 升 payload 版本后旧装配脏命中（V3.9 批次 1B 教训：
    # 进 payload 的装配输入必须有对应键维度）。
    genre_pack_ref = peek["genre_pack_ref"] or "__none__"
    intent_fp = _fingerprint_author_intent(author_intent)
    cache_key = (
        project_id, state_version, chapter_no, "director", plan_fp, outline_fp,
        active_canon_id, intent_fp, target_word_count, genre_pack_ref,
        _cache_namespace_tag(namespace),
    )
    cacheable = (
        plan_fp != _FINGERPRINT_UNCACHED and intent_fp != _FINGERPRINT_UNCACHED
    )
    if cacheable:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    payload = _build_director_input_uncached(
        db_path, project_id, chapter_id, author_intent, target_word_count,
        peek=peek,
    )
    if cacheable:
        _cache_put(cache_key, payload)
    return payload
