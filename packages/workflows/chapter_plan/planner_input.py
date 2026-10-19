"""director_planner 输入装配（P1 规划合并，2026-09-14）。

一次调用承载**双契约**（导演计划 + 场景计划），因此输入 = director 装配
（:func:`packages.core.context_engine.build_director_input`）∪ **规划期项目上下文**
（:func:`collect_planner_context`：可用角色 / 地点、文风约束、题材包配比声明）。

依赖方向：本模块属 chapter_plan 包；chapter_write 的降级 scene_planner 节点复用
:func:`collect_planner_context`（单向往 chapter_plan）——两处装配口径逐字一致，
避免「计划期」与「写作期」两条链的角色 / 地点 / 文风清单漂移。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from packages.core.agent_runtime.prompts import active_prompt_label
from packages.core.context_engine import build_director_input
from packages.core.context_engine.builders_common import (
    _GENRE_PACK_CONSUMER_PLANNER,
    _SKILL_CONSUMER_DIRECTOR,
    _SKILL_CONSUMER_PLANNER,
    _genre_pack_excerpt,
    _skill_excerpt,
)
from packages.core.db import get_connection, is_missing_schema_error
from packages.core.quality.wordcount import DEFAULT_TARGET_WORD_COUNT

# PromptRegistry 的 ACTIVE 版本标签（docs/agents/prompts/director_planner-v2.md）。
# 2026-09-27 起本常量**降级为 fallback**（2026-09-30 落库侧补齐，三面全同源）：
# payload["prompt_version"]（本文件 build_director_planner_input）、
# chapters.plan_json.prompt_version 与 chapter_scene_plans.prompt_version
# （后两者同在 chapter_plan/pipeline.py 的 _save_plan_node，开事务前算出一个
# planner_prompt_label 复用）均取 ACTIVE 真值，与 ai_call_logs.prompt_version
# （runner 从 registry 读出）同源；表空 / 未 sync / 库不可读时回落到本常量，
# 装配行为与旧实现零差异。
# v2（2026-09-15）：输入契约新增 chapter.outline（策展大纲槽）+ 规则 0「大纲优先」。
DIRECTOR_PLANNER_PROMPT_VERSION = "director_planner:v2"

# scene_planner 输入契约的本地默认文风（项目未配置 style_constraints 时使用；
# 原 chapter_write 局部常量，P1 上移到本模块供两条链共用，取值逐字不变）。
SCENE_PLANNER_DEFAULT_STYLE: dict[str, Any] = {
    "language": "zh-Hans",
    "pov": "third_person_limited",
    "dialogue_ratio": 0.4,
    "forbidden_words": ["仿佛", "如同", "本章目标"],
}


def load_project_style_constraints(db_path: str, project_id: str) -> dict[str, Any]:
    """读 ``projects.style_constraints_id`` → ``style_constraints.config_json``；缺失则返回默认。

    对老库（无 style_constraints 表 / 无列）做防御性捕获，避免 DDL 差异阻断装配。
    （P1 前为 ``chapter_write.pipeline._load_project_style_constraints``，逻辑逐字搬移。）

    ⚠️ 2026-10-16 批次十：**降级范围按本 docstring 声称的收窄到「缺表 / 缺列」**。
    原先两处裸 ``except sqlite3.OperationalError`` + 一处 ``except Exception`` 把
    ``database is locked`` / ``disk I/O error`` / ``database disk image is malformed``
    一起读成「项目没配文风约束」⇒ 装配回落 ``SCENE_PLANNER_DEFAULT_STYLE``，
    **正文没带项目文风而作者与模型都看不出来**（零告警）。判别：
    ``tests/unit/test_db_error_scope_e4.py``。

    收窄必须**连开连接这一半一起做**（本函数的既有实测）：``get_connection`` 里
    ``PRAGMA journal_mode = WAL`` 排在 ``PRAGMA busy_timeout = 5000`` **之前**，
    且非 WAL 库（= 老库形态，正是本降级声称要覆盖的那批）上只要有并发写事务，
    **开连接本身就抛** ``database is locked``——锁压根到不了下面两条 SELECT。
    探针实测见 ``tests/unit/test_db_error_scope_e4.py`` 的文档字符串。
    """
    try:
        conn = get_connection(db_path)
    except sqlite3.OperationalError as exc:
        # 非结构故障不得降级成「默认文风」：那正是「静默降级被当成正常」的形状
        # （DB 整体不可用 ≠ 这个项目没配文风）。判别单点 = packages.core.db。
        if not is_missing_schema_error(exc):
            raise
        return dict(SCENE_PLANNER_DEFAULT_STYLE)
    except Exception:  # noqa: BLE001 —— 非 DB 失败（路径类型错等）仍按默认文风
        return dict(SCENE_PLANNER_DEFAULT_STYLE)
    try:
        row = conn.execute(
            "SELECT style_constraints_id FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        # 先关连接再判定：上抛路径也要归还连接（降级路径的行为不变）。
        conn.close()
        if not is_missing_schema_error(exc):
            raise
        return dict(SCENE_PLANNER_DEFAULT_STYLE)
    style_id = row["style_constraints_id"] if row else None
    if style_id:
        try:
            sc_row = conn.execute(
                "SELECT config_json FROM style_constraints WHERE style_constraints_id = ?",
                (style_id,),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            conn.close()
            if not is_missing_schema_error(exc):
                raise
            return dict(SCENE_PLANNER_DEFAULT_STYLE)
        if sc_row and sc_row["config_json"]:
            try:
                cfg = json.loads(sc_row["config_json"])
                if isinstance(cfg, dict):
                    conn.close()
                    return cfg
            except (TypeError, ValueError):
                pass
    conn.close()
    return dict(SCENE_PLANNER_DEFAULT_STYLE)


def collect_planner_context(db_path: str, chapter_id: str) -> dict[str, Any]:
    """装配「规划期项目上下文」（不调 LLM、不读题材包 / canon）。

    返回：

    - ``project_id`` / ``chapter_id`` / ``chapter_number`` / ``chapter_title``：章节元信息；
    - ``available_characters`` / ``available_locations``：项目全部角色 / 地点（id + name，
      按 name 升序）——director_planner 与 scene_planner 的 ID 合法性白名单来源；
    - ``style_constraints``：项目级文风配置，缺则 :data:`SCENE_PLANNER_DEFAULT_STYLE`；
    - ``recent_prose``：留空占位（与 chapter_write 既有口径一致：规划期不依赖前章尾段原文，
      director 侧的 ``previous_chapter_tail`` 已提供衔接锚点）。

    章节行缺席（chapter_id 不存在）→ 元信息字段为 None / 空列表，不抛错
    （装配层不承担存在性校验）。
    """
    conn = get_connection(db_path)
    try:
        chap_row = conn.execute(
            "SELECT project_id, number, title FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        char_rows = conn.execute(
            """
            SELECT character_id, name FROM characters
            WHERE project_id = (SELECT project_id FROM chapters WHERE chapter_id = ?)
            ORDER BY name
            """,
            (chapter_id,),
        ).fetchall()
        loc_rows = conn.execute(
            """
            SELECT location_id, name FROM locations
            WHERE project_id = (SELECT project_id FROM chapters WHERE chapter_id = ?)
            ORDER BY name
            """,
            (chapter_id,),
        ).fetchall()
    finally:
        conn.close()

    project_id = chap_row["project_id"] if chap_row else None
    style_constraints = (
        load_project_style_constraints(db_path, project_id)
        if project_id
        else dict(SCENE_PLANNER_DEFAULT_STYLE)
    )
    return {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "chapter_number": (
            int(chap_row["number"]) if chap_row and chap_row["number"] is not None else 1
        ),
        "chapter_title": chap_row["title"] if chap_row else None,
        "available_characters": [
            {"character_id": r["character_id"], "name": r["name"]} for r in char_rows
        ],
        "available_locations": [
            {"location_id": r["location_id"], "name": r["name"]} for r in loc_rows
        ],
        "style_constraints": style_constraints,
        "recent_prose": {"last_chapter_excerpt": "", "last_scene_excerpt": ""},
    }


def _merge_genre_pack_planner_fields(payload: dict[str, Any], db_path: str, project_id: str) -> None:
    """把题材包的 **planner 消费段**并入 director 装配的 ``genre_pack`` 段（就地改写）。

    director 段含 ``structure_templates`` / ``payoff_types`` / ``pacing``（本卷 / 本章规划
    用），planner 段含配比声明 ``ratio_declarations`` + 派生 ``ratio_instruction``
    （逐 scene 标注 ``scene_type`` 与字数分摊用）——合并调用两者都要，缺一则 prompt
    的 Rule 28 / E-SPL-10 无法执行。

    溯源：``_genre_pack_consumed.consumed_fields`` 取两段消费字段的并集（顺序：director →
    planner），与 A/B 驱动 ``build_payload`` 的合并口径一致（该驱动实测值 =
    ``[structure_templates, payoff_types, pacing, ratio_declarations]``）。
    """
    if not project_id:
        return
    conn = get_connection(db_path)
    try:
        planner_inject, planner_audit = _genre_pack_excerpt(
            conn, project_id, consumer=_GENRE_PACK_CONSUMER_PLANNER
        )
    finally:
        conn.close()
    # ⚠️ 2026-10-16 批次九：此处的 ``except sqlite3.OperationalError`` 已删。
    # ``_genre_pack_excerpt`` 内部**自己**把「缺表/缺列」降级为 ``(None, None)``，
    # 外层无差别再吞一次会把内层刚收窄的范围原样放大（锁 / I/O 故障又变静默丢题材包）。
    # 「老库 / 缺表 → 未绑定」的语义由内层唯一承载，行为对本例零变化。
    if not isinstance(planner_inject, dict):
        return
    merged = dict(payload.get("genre_pack") or {})
    for key, value in planner_inject.items():
        # pack_id / name / genre_tag / version 等共用键取 planner 段同值（同一绑定行），
        # 覆盖无副作用；director 独有的 structure_templates / pacing 保留。
        merged[key] = value
    payload["genre_pack"] = merged

    director_audit = payload.get("_genre_pack_consumed")
    if isinstance(director_audit, dict):
        consumed = list(director_audit.get("consumed_fields") or [])
        for field in (planner_audit or {}).get("consumed_fields") or []:
            if field not in consumed:
                consumed.append(field)
        director_audit["consumed_fields"] = consumed
        payload["_genre_pack_consumed"] = director_audit


def _merge_skill_planner_fields(payload: dict[str, Any], db_path: str, project_id: str) -> None:
    """把 **合并角色**（director + scene_planner）适用的 skill 段写回 payload（就地改写）。

    为什么需要这一步：``build_director_planner_input`` 继承 director 装配，而 director
    装配的 ``skills`` 段只含 ``applies_to`` 命中 ``director`` 的 skill；本合并体同时
    扮演「场景规划」角色 ⇒ 命中 ``scene_planner`` 的 skill 也该对它可见。做法与题材包
    的合并口径同款（``_merge_genre_pack_planner_fields`` 把 director 段与 planner 段
    **合并**进同一个 ``genre_pack`` 键），区别是 skill 段按「合并角色」**重算**一次
    （``applies_to`` 命中 director **或** scene_planner 即注入），而不是拼接两段——
    拼接会让同一条 skill 出现两次，且字符预算（2000）会被两份相同内容吃掉。

    溯源：``_skills_consumed`` 同步为合并角色口径（重算，不合并两份审计）。
    """
    if not project_id:
        return
    conn = get_connection(db_path)
    try:
        inject, audit = _skill_excerpt(
            conn, project_id,
            consumer=(_SKILL_CONSUMER_DIRECTOR, _SKILL_CONSUMER_PLANNER),
        )
    finally:
        conn.close()
    # 降级语义由 ``_skill_excerpt`` 内部唯一承载（缺表/缺列 → 未绑定）；
    # 外层不再套一层无差别 except（同 ``_merge_genre_pack_planner_fields`` 的理由：
    # 批次九的教训——外层再吞一次会把内层刚收窄的范围放大）。
    if inject is None:
        # 本角色无适用 skill ⇒ 移除继承自 director 段的 skills（避免留下一个
        # 「只含 director 适用项」的半截段，让合并体看到的口径与计算口径不一致）。
        payload.pop("skills", None)
        payload.pop("_skills_consumed", None)
        return
    payload["skills"] = inject
    if audit is not None:
        payload["_skills_consumed"] = audit
    else:
        payload.pop("_skills_consumed", None)


def build_director_planner_input(
    db_path: str | Path,
    project_id: str,
    chapter_id: str,
    author_intent: str,
    *,
    target_word_count: int = DEFAULT_TARGET_WORD_COUNT,
    expected_role: str = "setup",
) -> dict[str, Any]:
    """组装 director_planner（P1 合并调用）输入 payload。

    组成 = :func:`~packages.core.context_engine.build_director_input` 的 director 段
    （story state / 角色 / 世界观 / hook / debt / canon / 前章尾段 / 召回……）
    ∪ :func:`collect_planner_context` 的规划期段（可用角色 / 地点 / 文风 / 前章散文占位）
    ∪ 题材包 planner 段（配比声明 + 配比指令）
    ∪ **合并角色**（director + scene_planner）适用的 skill 段（独立新段 ``skills``）。

    与 A/B 驱动 ``p1_ab/run_merged_ab_v2.build_payload`` 的合并口径一致；差别仅为
    生产侧从 DB 现装配（驱动复用首轮重建产物）。
    """
    payload = build_director_input(
        db_path,
        project_id,
        chapter_id,
        author_intent,
        target_word_count=target_word_count,
    )
    context = collect_planner_context(str(db_path), chapter_id)
    payload["agent"] = "director_planner"
    payload["prompt_version"] = active_prompt_label(
        db_path, "director_planner", fallback=DIRECTOR_PLANNER_PROMPT_VERSION,
    )
    chapter_section = dict(payload.get("chapter") or {})
    chapter_section["expected_role"] = expected_role
    chapter_section["number"] = context["chapter_number"]
    payload["chapter"] = chapter_section
    payload["available_characters"] = context["available_characters"]
    payload["available_locations"] = context["available_locations"]
    payload["style_constraints"] = context["style_constraints"]
    payload["recent_prose"] = context["recent_prose"]
    _merge_genre_pack_planner_fields(payload, str(db_path), project_id)
    _merge_skill_planner_fields(payload, str(db_path), project_id)
    return payload


__all__ = [
    "DIRECTOR_PLANNER_PROMPT_VERSION",
    "SCENE_PLANNER_DEFAULT_STYLE",
    "build_director_planner_input",
    "collect_planner_context",
    "load_project_style_constraints",
]
