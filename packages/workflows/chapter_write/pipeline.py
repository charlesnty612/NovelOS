"""chapter_write 工作流（Sprint 4-A + P0 ScenePlanner 真实化 + P1 Polisher + P1 规划合并消费）。

节点列表：
- ``load_plan`` (Transform) —— 读 chapters.plan_json 准备 director_plan 输入。
- ``scene_planner`` (AI) —— P0 新增：调 scene_planner agent 把 director_plan 翻译为
  结构化 Scene Plan（含 slots / 冲突 / 信息边界 / 结尾钩子）。
  **P1（2026-09-14）起优先读 chapter-plan 落库的 scene_plan**
  （``chapter_scene_plans``，迁移 0027）：命中即跳过 LLM 调用（``scene_planner_status=
  'from_plan'``）；未命中（老章 / 合并调用只回了计划段 / 0027 未迁移的老库）保留本节点
  的单次调用路径。任何失败（prompt 缺失 / provider 异常 / 输出不合规）→ 降级到原 stub
  机械映射逻辑，**不**阻断 writer run（ch3 实证过该风险真实，降级路径必须保留）。
- ``writer`` (AI) —— 调 writer agent 生成本章 prose。
- ``polisher`` (AI) —— P1 新增：以 writer 产出的 prose + scan_ai_patterns(prose)
  确定性命中为输入，做去 AI 腔的文风级润色；不改情节 / 人物 / 事实。
  任何失败（prompt 缺失 / provider 异常 / 输出不合规 / 长度守恒失败）→ 兜底回退
  writer 原始 prose，**不**阻断 save_draft。
  mock 直通：若 ``ctx["mock_providers"]`` 含 'writer' 但不含 'polisher'，跳过润色
  原样透传（既有 mock 测试不受影响）。
  P0 提速条件触发：确定性预检（``scan_ai_patterns`` + 题材禁词 + ``req_q7``）三源
  全零命中 ⇒ ``polisher_status='skipped_clean'``，prose 原样透传不调 LLM；
  任一命中 ⇒ 走上述原润色路径（行为零变化）。
- ``length_check`` (Transform) —— 字数闭环实测节点（V3.7+ P1）：
  用 :func:`packages.core.quality.wordcount.classify_prose_length` 权威口径实测
  polished_prose（缺则 writer_output.prose）字数，读项目级 word_band_json 覆盖
  （与 chapter_review._basic_checks_node 同口径）。
  输出 ``length_report = {visible_chars, target, band_low, band_high, status,
  within_band}`` + ``target_word_count`` + ``word_band_cfg`` 三个独立 key
  （便于 condense 节点与 save_draft 复用），并把 ``length_check_passed`` 布尔
  写入 ctx 供 condense 路由判定。
- ``condense`` (AI) —— 仅在 length_check 未通过、payload 超带上限且未超过 2 轮时执行。
  复用 polisher 的 capability 绑定（``capability_for('polisher')``），prompt
  携带实测字数、目标带上下限、需净减比例，遵循 writer-v1 规则 20 的压缩纪律
  （优先砍铺垫/重复意象/冗词，不砍节拍、不删场景、保持文风与既有设定用语）。
  P0 提速压缩预算门：payload 未超 ``band_high``（``_CONDENSE_PAYLOAD_TRIGGER_RATIO``
  × 带上限）⇒ ``condense_status='skipped_below_budget'`` + ``condense_skipped=True``，
  不调 LLM（欠字 / 陈旧 report 形态：压缩无余量，且只会把正文压得更短）。
  「≤2 轮」压缩循环在节点内部兑现：调 LLM → ``visible_chars`` 实测 → 仍超
  ``band_high`` 且轮数未满 → 再调（每轮 payload 都用最新实测数字重建）。
  mock 守卫与 polisher（``_polish_node``）同款语义：``mock_providers`` 为空
  （生产）⇒ ``mock_script=None`` 走真实 provider；mock 流（有 mock_providers
  但没配 ``condense`` 脚本）⇒ 原样透传 ``skipped_no_mock``。
  任何失败 → 兜底保留当前 polished_prose 放行，``condense_status='failed'``，
  **不**阻断 save_draft。两轮后仍超带 → ``condense_status='over_band_after_2_rounds'``
  放行，save_draft 在 deviations 中标注压缩后实测字数与偏差。
  节点回写压缩后的 ``length_report`` / ``length_check_passed``（覆盖上游
  length_check 的旧值），下游 save_draft 因此读到压缩后真相。
- ``save_draft`` (State) —— 写 drafts 表 + chapters.status PLANNED→DRAFTED。
  优先取 ``polished_prose``（无则回落 ``writer_output.prose``）。
  word_count 直接对最终 prose 取权威实测（``visible_chars``）：既不读 condense
  之前的 ``length_report`` 旧值（length_check 在 condense 上游、压缩后不再重跑），
  也不采信 self_report.word_count（实证 ch4-6：writer 自报 3008-3172
  vs 实际 4721-5842，偏差 50%+）。超带偏差注记追加到 chapter plan_json.deviations。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.agent_runtime.structured_output import strip_think_blocks
from packages.core.context_engine import build_writer_input
from packages.core.context_engine.builders import (
    _REFERENCE_CANON_CONSUMER_PLANNER,
    _reference_canon_excerpt,
)
from packages.core.context_engine.builders_common import (
    _GENRE_PACK_CONSUMER_PLANNER,
    _genre_pack_excerpt,
)
from packages.core.db import get_connection
from packages.core.genre.consumers import (
    count_forbidden_word_hits,
    forbidden_words,
)
from packages.core.genre.service import GenrePackService
from packages.core.ids import new_id, now_iso
from packages.core.model_router.router import capability_for
from packages.core.quality.ai_patterns import scan_ai_patterns
from packages.core.quality.guardrails import req_q7
from packages.core.quality.wordcount import (
    DEFAULT_TARGET_WORD_COUNT,
    classify_prose_length,
    resolve_band_config,
    visible_chars,
)
from packages.core.workflow_runtime.engine import WorkflowNode
from packages.workflows.chapter_plan.planner_input import collect_planner_context
from packages.workflows.chapter_plan.scene_plan_store import load_scene_plan

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# P0 提速：确定性门常量（集中维护）
# ---------------------------------------------------------------------------
#
# 背景：单章 6 次 LLM 调用 ≈12 分钟，其中 polisher（去 AI 腔二遍）与 condense
# （字数压缩）存在「没有可修项 / 没有压缩余量也要调 LLM」的浪费。两个门都只做
# **跳过**判定（确定性、零 LLM）；判据全部复用既有检测器与既有阈值常量，
# 不新造规则、不新造魔法数。

# polisher 条件触发门（_polish_node）：预检三源全部零命中才跳过润色。
# 1. scan_ai_patterns（quality.ai_patterns）：AI 腔模式级权威信号源；
# 2. 题材禁词（genre.consumers.count_forbidden_word_hits × 题材包
#    style_constraints.forbidden_words）：与 chapter_review._basic_checks 同源同口径；
# 3. guardrails.req_q7：AI marker 密度 / 「然而|但是」段首占比 / 句长标准差——
#    阈值常量 Q7_MARKER_PER_KCHARS / Q7_PARA_OPENER_THRESHOLD / Q7_FLAT_SENTENCE_STD
#    全部来自 quality.guardrails（单一权威），本模块不覆写、不调参。
# 说明：三源任一非零命中即走原润色路径（保守口径），因此无需在本模块新增数值常量。

# condense 压缩预算门（_condense_node）：payload（待压缩正文，visible_chars 口径）
# 必须**严格超过**「带上限 × 本系数」才调 LLM 压缩。
# 取值依据：condense 的唯一用途 = 把超带上限（band_high）的正文压回带内
# （writer-v1 §6.1 规则 20），band_high 即「已无压缩余量」的判据边界，故取 1.0
# （不放大、不收紧）——低于该线时既无余量可压，内循环（status != 'over' 即收工）
# 还会把欠字正文压得更短，属纯浪费 + 有害调用。
# 任务书举例的「预算 50%」是本门的真子集：0.5×band_high 必然同时低于 band_low，
# 已被本门覆盖（更强条件包含更弱条件），故不单列该门。
# 调大本值 = 容忍「小幅超带」放走（字数闭环政策变更，按 V3.7/3.9 口径默认不启用）。
_CONDENSE_PAYLOAD_TRIGGER_RATIO = 1.0


def _load_plan_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"chapter {chapter_id!r} not found")
    plan_json_raw = row["plan_json"]
    plan_json = json.loads(plan_json_raw) if plan_json_raw else {}
    if not plan_json.get("chapter_goal"):
        raise ValueError(
            f"chapter {chapter_id!r} has empty plan_json; run chapter-plan first"
        )
    return {"loaded_plan": plan_json}


_SCENE_PLANNER_PROMPT_VERSION = "scene_planner:v1"


def _scene_planner_fallback(ctx: dict[str, Any]) -> dict[str, Any]:
    """降级路径：把 director.key_beats 逐个映射为 scene；每 scene slots=[1 个 action]。

    字段与 AI scene_planner 输出保持一致（scene_id / purpose / characters / location /
    conflict / turn / time_in_story / pov / pov_character_id / slots），下游
    build_writer_input 无需改动。
    """
    plan = ctx.get("loaded_plan") or {}
    beats = plan.get("key_beats") or []
    scenes: list[dict[str, Any]] = []
    for i, beat in enumerate(beats, start=1):
        if not isinstance(beat, dict):
            continue
        scene_id = f"scene_{i:03d}"
        slot_id = f"slot_{i:03d}"
        scenes.append(
            {
                "scene_id": scene_id,
                "purpose": beat.get("purpose") or "",
                "characters": beat.get("involved_characters", []),
                "location": (beat.get("involved_locations") or [None])[0],
                "conflict": "",
                "turn": "",
                "time_in_story": "",
                "pov": "third_person_limited",
                "pov_character_id": None,
                "information_boundary": [],
                "ending_hook": None,
                "slots": [
                    {
                        "slot_id": slot_id,
                        "type": "action",
                        "purpose": beat.get("purpose") or "",
                        "characters": beat.get("involved_characters", []),
                        "target_mood": None,
                        "constraints": [],
                    }
                ],
            }
        )
    if not scenes:
        # 兜底：若 key_beats 为空则建 1 个空 scene
        scenes = [
            {
                "scene_id": "scene_001",
                "purpose": "本章内容（Director plan 未提供 beats，兜底规划）",
                "characters": [],
                "location": None,
                "conflict": "",
                "turn": None,
                "time_in_story": "",
                "pov": "third_person_limited",
                "pov_character_id": None,
                "information_boundary": [],
                "ending_hook": None,
                "slots": [
                    {
                        "slot_id": "slot_001",
                        "type": "action",
                        "purpose": "本章主要内容",
                        "characters": [],
                        "target_mood": None,
                        "constraints": [],
                    }
                ],
            }
        ]
    return {"scene_plan": {"scenes": scenes}}


def _collect_scene_planner_inputs(
    db_path: str, chapter_id: str, plan: dict[str, Any]
) -> dict[str, Any]:
    """组装 scene_planner agent 输入（最小可运行口径）。

    - chapter 元信息、director_plan 来自 load_plan。
    - available_characters / available_locations / style_constraints / recent_prose 经
      :func:`...chapter_plan.planner_input.collect_planner_context` 取（P1 规划合并后与
      chapter-plan 的 director_planner 装配**同源同口径**——两条链的角色 / 地点 / 文风
      清单不得漂移）。
    - 题材库 P1b：项目绑定题材包时注入 ``genre_pack`` 段（爽点类型摘要 + 配比声明 +
      配比指令，见 :func:`...builders_common._genre_pack_excerpt` 的 scene_planner
      consumer 口径）+ ``_genre_pack_consumed`` 溯源审计；未绑定 → 两键都不出现。
    """
    context = collect_planner_context(db_path, chapter_id)
    project_id = context["project_id"]

    # Reference Canon 注入：scene_planner 消费 emotion_curve（张力曲线）+ payoff_list
    # （Scene 级爽点排布）。与 director / writer 共用同一 excerpt 函数（consumer=planner
    # 走 emotion_curve + payoff_list 分支）。无 active canon → 不注入 reference_canon 键，
    # 与 director 缺席语义保持一致。
    reference_canon_inject: dict | None = None
    reference_canon_audit: dict | None = None
    if project_id:
        conn3 = get_connection(db_path)
        try:
            reference_canon_inject, reference_canon_audit = _reference_canon_excerpt(
                conn3, project_id, consumer=_REFERENCE_CANON_CONSUMER_PLANNER,
            )
        except sqlite3.OperationalError:
            # 老库 / 缺表 → 视为无 canon，不阻断 Scene Planner 装配。
            reference_canon_inject = None
            reference_canon_audit = None
        finally:
            conn3.close()

    # 题材库 P1b：scene_planner 消费题材包（规划期硬约束）——
    # payoff_types（爽点类型摘要 + 密度约束文本化，≤1500 字符预算，
    # 超限打 ``__genre_pack_truncated__``）+ ratio_declarations（配比声明）+
    # 由其派生的 ``ratio_instruction``（逐 scene 标注 scene_type + 字数分摊遵守配比）。
    # 未绑定 / 题材包未声明相应段 → 不注入该键（与 director 缺席语义一致）。
    genre_pack_inject: dict | None = None
    genre_pack_audit: dict | None = None
    if project_id:
        conn_g = get_connection(db_path)
        try:
            genre_pack_inject, genre_pack_audit = _genre_pack_excerpt(
                conn_g, project_id, consumer=_GENRE_PACK_CONSUMER_PLANNER,
            )
        except sqlite3.OperationalError:
            # 老库 / 缺表 → 视为未绑定，不阻断 Scene Planner 装配。
            genre_pack_inject = None
            genre_pack_audit = None
        finally:
            conn_g.close()

    # world_state_excerpts：复用既有字段结构（P2-补洞），让 Scene Planner 能看到
    # world_rules_relevant（项目级硬设定，如原著要素锁），保证"沿传 Director 约束"
    # 不会因本节点缺 world_rules 而退化为凭印象。L0 world_rules 全量常驻，与
    # director/writer 装配口径保持一致。无项目 / 异常 → 返回最小结构，不阻断。
    world_state_excerpts: dict[str, Any] = {
        "current_time_in_story": None,
        "current_location": None,
        "locations": [],
        "active_factions": [],
        "world_rules_relevant": [],
        "sensory_anchors": [],
    }
    if project_id:
        conn2 = get_connection(db_path)
        try:
            rule_rows = conn2.execute(
                "SELECT world_rule_id, name, statement FROM world_rules "
                "WHERE project_id = ? ORDER BY world_rule_id ASC",
                (project_id,),
            ).fetchall()
            world_state_excerpts["world_rules_relevant"] = [
                {
                    "world_rule_id": r["world_rule_id"],
                    "name": r["name"],
                    "statement": r["statement"],
                }
                for r in rule_rows
            ]
        except sqlite3.OperationalError:
            # 老库 / 缺表 → 保留空列表，不阻断 Scene Planner 装配。
            pass
        finally:
            conn2.close()

    payload: dict[str, Any] = {
        "agent": "scene_planner",
        "prompt_version": _SCENE_PLANNER_PROMPT_VERSION,
        "chapter": {
            "chapter_id": chapter_id,
            "title": context["chapter_title"],
            "number": context["chapter_number"],
            # plan_json 存的是 expected_word_count；target_word_count 为兼容旧字段。
            # 兜底取全仓单源常量 DEFAULT_TARGET_WORD_COUNT（3000）。
            "target_word_count": int(
                plan.get("expected_word_count")
                or plan.get("target_word_count")
                or DEFAULT_TARGET_WORD_COUNT
            ),
            "expected_role": plan.get("expected_role"),
        },
        "director_plan": {
            "chapter_goal": plan.get("chapter_goal"),
            "core_conflict": plan.get("core_conflict"),
            "turning_point": plan.get("turning_point"),
            "key_beats": plan.get("key_beats") or [],
            "notes_for_planner": plan.get("notes_for_planner"),
            "revision_note": plan.get("revision_note"),
        },
        "available_characters": context["available_characters"],
        "available_locations": context["available_locations"],
        "world_state_excerpts": world_state_excerpts,
        "style_constraints": context["style_constraints"],
        "recent_prose": context["recent_prose"],
    }

    # Reference Canon（emotion_curve + payoff_list）。无 canon 时不注入该键——
    # 与 director 缺席语义保持一致（scene_planner 不得索要 canon）。
    if reference_canon_inject is not None:
        payload["reference_canon"] = reference_canon_inject
    if reference_canon_audit is not None:
        payload["_reference_canon_consumed"] = reference_canon_audit
    # 题材库 P1b：题材包注入段（爽点摘要 + 配比声明 + 配比指令）+ 溯源审计
    # （与 reference_canon 双 slot 并存互不覆盖；无题材包时两键都不出现）。
    if genre_pack_inject is not None:
        payload["genre_pack"] = genre_pack_inject
    if genre_pack_audit is not None:
        payload["_genre_pack_consumed"] = genre_pack_audit
    return payload


def _scene_planner_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Scene Planner AI 节点（P1 起兼任「读落库 scene_plan」入口）。

    行为：
    1. **P1 优先**：读 ``chapter_scene_plans``（迁移 0027，由 chapter-plan 的
       director_planner 合并调用落库）。命中（有行且 ``scenes`` 非空）→ 直接透出
       ``scene_plan``，``scene_planner_status='from_plan'``，**不调 LLM**。
    2. 未命中（老章 / 合并调用只回了计划段 / 0027 未迁移的老库）→ 走原路径：
       取 loaded_plan + chapter 元信息 + 项目角色/地点，组装 scene_planner payload。
    3. 调 ``run_agent(..., agent_name='scene_planner', expected='scene_planner',
       mock_script=...)``；runner 内部走 parse_json → validate_contract("scene_planner")
       → 写 ai_call_logs。
    4. 任何异常（prompt 缺失 / provider 异常 / 契约校验失败 / 输出字段缺失）→ 降级
       到 ``_scene_planner_fallback``，**不**抛错、**不**阻断 writer。
    5. 输出顶层保留 ``scene_plan`` key（与 stub 兼容），同时把原始 AI 输出放入
       ``scene_planner_output`` 供观测。

    支持 mock：``ctx["mock_providers"]["scene_planner"]`` 与 writer/critic 同机制。
    """
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    chapter_id = ctx["chapter_id"]
    plan = ctx.get("loaded_plan") or {}
    mock_script = (ctx.get("mock_providers") or {}).get("scene_planner")

    # P1：chapter-plan 已落库 scene_plan（迁移 0027）→ 命中即跳过本次 LLM 调用。
    # 只透出 scenes（与既有 scene_planner 路径同形：下游 build_writer_input 只消费
    # 该键），provenance 单独放 scene_planner_source 供 run 详情审计。
    persisted = load_scene_plan(db_path, chapter_id)
    if persisted is not None:
        persisted_scenes = (persisted.get("scene_plan") or {}).get("scenes")
        if isinstance(persisted_scenes, list) and persisted_scenes:
            return {
                "scene_plan": {"scenes": persisted_scenes},
                "scene_planner_output": None,
                "scene_planner_status": "from_plan",
                "scene_planner_source": {
                    "source": persisted.get("source"),
                    "prompt_version": persisted.get("prompt_version"),
                    "run_id": persisted.get("run_id"),
                    "updated_at": persisted.get("updated_at"),
                },
            }
        _log.warning(
            "chapter_write.scene_planner persisted scene_plan unusable "
            "(empty/invalid scenes) → fallback to LLM path: chapter_id=%s", chapter_id,
        )

    payload = _collect_scene_planner_inputs(db_path, chapter_id, plan)
    # 题材库 P1b：本节点产出透出题材包消费审计键（供 run detail / 测试观测）；
    # 直接从已装配的 payload 取（不额外查库），非 dict → None。
    genre_pack_audit = payload.get("_genre_pack_consumed")
    genre_pack_audit = genre_pack_audit if isinstance(genre_pack_audit, dict) else None
    try:
        out = run_agent(
            db_path,
            "scene_planner",
            payload,
            run_id,
            node_run_id=ctx.get("_current_node_run_id"),
            expected="scene_planner",
            mock_script=mock_script,
            profile_id=(ctx.get("model_overrides") or {}).get(
                capability_for("scene_planner")
            ),
        )
        if not isinstance(out, dict):
            raise ValueError(f"scene_planner output not dict: {type(out).__name__}")
        scenes = out.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("scene_planner output missing non-empty 'scenes'")
        # 只把 scene_planner 的核心 scenes 包装成 scene_plan；保留完整输出供观测。
        # F4 修复：scene_planner 节点输出透出 ``_reference_canon_consumed`` 审计键
        # 到 ctx（→ checkpoint_json），与 director / writer 节点的审计闭环对齐。
        # 仅透出审计键（{canon_id, consumed_fields}），不把 reference_canon 业务
        # 数据塞进 checkpoint（payload 内的 reference_canon 业务键仅供 scene_planner
        # 本节点消费，不进入 ctx）。
        scene_planner_audit: dict[str, Any] | None = None
        project_id_for_audit = None
        conn_sp = get_connection(db_path)
        try:
            chap_row = conn_sp.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)
            ).fetchone()
            project_id_for_audit = chap_row["project_id"] if chap_row else None
        except sqlite3.OperationalError:
            project_id_for_audit = None
        finally:
            conn_sp.close()
        if project_id_for_audit:
            try:
                conn_sp2 = get_connection(db_path)
                try:
                    _, scene_planner_audit = _reference_canon_excerpt(
                        conn_sp2,
                        project_id_for_audit,
                        consumer=_REFERENCE_CANON_CONSUMER_PLANNER,
                    )
                except sqlite3.OperationalError:
                    scene_planner_audit = None
                finally:
                    conn_sp2.close()
            except Exception:  # noqa: BLE001 —— 审计键获取失败不阻断主流程
                scene_planner_audit = None
        ret: dict[str, Any] = {
            "scene_plan": {"scenes": scenes},
            "scene_planner_output": out,
            "scene_planner_status": "ok",
        }
        if scene_planner_audit is not None:
            ret["_reference_canon_consumed"] = scene_planner_audit
        if genre_pack_audit is not None:
            ret["_genre_pack_consumed"] = genre_pack_audit
        return ret
    except Exception as exc:  # noqa: BLE001 —— 任何失败均降级，不阻断 writer
        _log.warning(
            "chapter_write.scene_planner degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        fallback = _scene_planner_fallback(ctx)
        # scene_planner 失败/降级时也尝试透出审计键（与 writer 失败兜底语义对齐），
        # 便于「该章生成消费了哪份 canon」在审计层可观测。
        scene_planner_audit_fb: dict[str, Any] | None = None
        project_id_for_audit = None
        conn_sp3 = get_connection(db_path)
        try:
            chap_row = conn_sp3.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)
            ).fetchone()
            project_id_for_audit = chap_row["project_id"] if chap_row else None
        except sqlite3.OperationalError:
            project_id_for_audit = None
        finally:
            conn_sp3.close()
        if project_id_for_audit:
            try:
                conn_sp4 = get_connection(db_path)
                try:
                    _, scene_planner_audit_fb = _reference_canon_excerpt(
                        conn_sp4,
                        project_id_for_audit,
                        consumer=_REFERENCE_CANON_CONSUMER_PLANNER,
                    )
                except sqlite3.OperationalError:
                    scene_planner_audit_fb = None
                finally:
                    conn_sp4.close()
            except Exception:  # noqa: BLE001
                scene_planner_audit_fb = None
        ret_fb: dict[str, Any] = {
            **fallback,
            "scene_planner_output": None,
            "scene_planner_status": "failed",
            "scene_planner_error": str(exc),
        }
        if scene_planner_audit_fb is not None:
            ret_fb["_reference_canon_consumed"] = scene_planner_audit_fb
        # 题材包审计键在降级路径同样透出（payload 在 try 之前已装配，降级不改变
        # 「该章 scene 规划消费了哪份题材包」这一事实）。
        if genre_pack_audit is not None:
            ret_fb["_genre_pack_consumed"] = genre_pack_audit
        return ret_fb


def _resolve_writer_context_mode(ctx: dict[str, Any]) -> str:
    """解析 writer context_mode（V3.2 P2-1）。

    优先级：
    1. workflow 上下文 ``writer_context_mode`` 字段（显式传参 > 一切）；
    2. 环境变量 ``NOVELOS_WRITER_CONTEXT_MODE``（全局开关，便于回归测试一键切换）；
    3. 默认 ``"paged"``（V3.2 行为变更：writer 默认按 L0/L1/L2 分页注入）。

    返回值仅做白名单校验，非合法值回退默认 ``"paged"`` 并发出警告（不抛错——
    装配阶段抛错会让运行中的 writer workflow 失败，违反「上下文裁剪是性能
    优化、不应是阻断级」的初衷）。
    """
    raw = ctx.get("writer_context_mode")
    if raw is None:
        raw = os.environ.get("NOVELOS_WRITER_CONTEXT_MODE")
    if raw is None:
        return "paged"
    if raw in ("full", "paged"):
        return raw
    # 非法值：兜底 paged + 不抛错（仅开发期日志可见）
    _log.warning(
        "writer_context_mode=%r is invalid; falling back to 'paged'", raw,
    )
    return "paged"


def _latest_draft_text(db_path: str, chapter_id: str) -> str:
    """读该章最新 draft content（drafts 表，按 version DESC LIMIT 1）。

    无 draft 行或 content 为空 → 返回空串。DB 异常（缺失表/列）→ 返回空串，
    与既有"上下文缺失不阻断 writer run"口径一致。
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (chapter_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return ""
    finally:
        conn.close()
    if row is None:
        return ""
    return row["content"] or ""


# 修订模式机读核销表（writer-v1.md §6.1 第 11 条）尾块标记。
# 约定：writer revise 模式输出 prose 末尾追加独占一行的
# "---REVISION-CHECKLIST---" 分隔行 + 一行 JSON 数组。管线按此分隔行切分，
# 前半 = 真正正文（落 drafts.content），后半 = 核销表（落 run 节点产出）。
_REVISION_CHECKLIST_MARKER = "---REVISION-CHECKLIST---"


def _parse_revision_checklist(prose: str) -> tuple[str, list[dict[str, Any]] | None]:
    """从 prose 末尾剥离 REVISION-CHECKLIST 尾块；返回 (clean_prose, checklist_or_None)。

    行为契约（与 writer-v1.md §6.1 第 11 条 + 测试断言对齐）：
    - 仅当 prose 含 `---REVISION-CHECKLIST---` 分隔行（独占一行）时进入切分逻辑；
      缺失 → 返回 ``(prose.rstrip(), None)``（不报错，revise 模式允许模型漏写）。
    - 切分后，分隔行后的剩余文本先 rstrip（剥掉模型可能留的尾部空白），
      再尝试 ``json.loads``：成功且为 list → 返回核销表；失败/非 list → ``None``（fail-soft）。
    - 前半（正文侧）rstrip 一次，去掉分隔行前的多余空行；不改变正文内容本身。

    调用方须依据 ``checklist is None`` 决定是否在 ctx 写 ``revision_checklist=None``
    + log warning（不阻断 writer run）。
    """
    if not isinstance(prose, str):
        return ("", None)
    if _REVISION_CHECKLIST_MARKER not in prose:
        return (prose.rstrip(), None)
    # 仅在分隔行**独占一行**时切分（避免误切正文内偶然出现的同名行；不过核销表
    # 约定就是独占一行，故首处命中即认定为契约边界）。
    head, _, tail = prose.partition(_REVISION_CHECKLIST_MARKER)
    checklist_text = tail.strip()
    if not checklist_text:
        # 分隔行后为空 → 模型把分隔行写出但忘了 JSON → 当作缺失，fail-soft
        return (head.rstrip(), None)
    try:
        parsed = json.loads(checklist_text)
    except (TypeError, ValueError):
        return (head.rstrip(), None)
    if not isinstance(parsed, list):
        return (head.rstrip(), None)
    # 仅保留 dict 元素；非 dict 元素丢弃（不阻断）
    items: list[dict[str, Any]] = [it for it in parsed if isinstance(it, dict)]
    return (head.rstrip(), items)


def _writer_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    chapter_id = ctx["chapter_id"]
    scene_plan = ctx["scene_plan"]
    context_mode = _resolve_writer_context_mode(ctx)
    payload = build_writer_input(
        db_path,
        chapter_id,
        scene_plan,
        # 目标字数优先取 plan 的 expected_word_count；ctx 覆盖其次；
        # 兜底取全仓单源常量 DEFAULT_TARGET_WORD_COUNT（3000）。
        target_word_count=ctx.get("target_word_count")
        or int((ctx.get("loaded_plan") or {}).get("expected_word_count") or 0)
        or DEFAULT_TARGET_WORD_COUNT,
        context_mode=context_mode,
    )

    # 修订模式（基于现有 draft 局部修改）注入：读最新 draft content +
    # plan_json.revision_note，二者皆非空 → mode='revise'。
    if ctx.get("fresh_write"):
        # 全新重写：忽略旧稿与改稿意见（用于跨模型文风对比）
        draft_text = ""
        revision_note = ""
    else:
        draft_text = _latest_draft_text(db_path, chapter_id)
        revision_note = (ctx.get("loaded_plan") or {}).get("revision_note") or ""
    mode = "revise" if (revision_note and draft_text) else "write"
    payload["mode"] = mode
    payload["draft_text"] = draft_text
    if revision_note:
        # revision_note 是 writer 输入契约的**顶层**字段（writer-v1.md §4.1：mode /
        # draft_text / revision_note 同级）；build_writer_input 只产出 director_plan
        # 结构、不注入该键，故此处顶层写入即为落点（无嵌套副本）。
        payload["revision_note"] = revision_note

    mock_script = (ctx.get("mock_providers") or {}).get("writer")
    # 能力路由：revise=定向局部修改，走 light（与 critic/summarizer 同源，省创作额度）；
    # write / fresh_write 走默认 creative_writing。mock 路径不走 capability，
    # capability_override 仅在真实链路下被 ModelRouter 使用，不影响 mock 行为。
    writer_capability_override: str | None = "light" if mode == "revise" else None
    out = run_agent(
        db_path,
        "writer",
        payload,
        run_id,
        node_run_id=ctx.get("_current_node_run_id"),
        expected="writer",
        mock_script=mock_script,
        capability_override=writer_capability_override,
        profile_id=(ctx.get("model_overrides") or {}).get(
            capability_for("writer")
        ),
    )

    # 修订模式机读核销表（writer-v1.md §6.1 第 11 条）：从 prose 末尾剥离
    # `---REVISION-CHECKLIST---` 尾块，前半 = 真正正文（落 drafts.content），
    # 后半 = 核销表（落 run 节点 revision_checklist 产出，便于审查改稿意见是否
    # 被真正执行）。fail-soft：尾块缺失/JSON 坏 → checklist=None + log warning，
    # 正文保持原样不阻断。
    revision_checklist: list[dict[str, Any]] | None = None
    if mode == "revise" and isinstance(out, dict):
        raw_prose = out.get("prose") or ""
        clean_prose, revision_checklist = _parse_revision_checklist(raw_prose)
        if revision_checklist is None:
            _log.warning(
                "chapter_write.writer revision_checklist parse failed: "
                "chapter_id=%s mode=revise (missing marker or bad JSON)",
                chapter_id,
            )
        # 无论剥离成功与否，都用 clean_prose 覆盖 out["prose"]——保证下游
        # polisher / save_draft 不会把核销表尾块混进草稿正文 / word_count 统计。
        out["prose"] = clean_prose
        # word_count 只统计剥离后正文：模型在 self_report.word_count 里通常按
        # 全文长度（含核销表 JSON 行）统计，会拉高 word_count 字段；此处覆写为
        # clean_prose 的字符数，确保 save_draft 落库的 word_count 与
        # drafts.content 实际长度一致。
        sr = out.get("self_report")
        if isinstance(sr, dict):
            sr["word_count"] = len(clean_prose)
            out["self_report"] = sr

    # V3.1.1 V-P0：writer 本节点真实落库模型 id（mock 路径无 ai_call_logs 行 → None）。
    # 用于 drafts.model_id 记录真实 provider/model，避免列表页无法区分模型。
    writer_model_id: str | None = None
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT model_id FROM ai_call_logs "
            "WHERE run_id = ? AND node_run_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (run_id, ctx.get("_current_node_run_id")),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    if row is not None:
        writer_model_id = row["model_id"]
    # writer_input 透出到 ctx（→ checkpoint_json）供测试断言；生产仅作为可观测钩子。
    # revision_checklist：仅 revise 模式且尾块解析成功时为 list；其余情况为 None
    # （write 模式 / revise 但尾块缺失或 JSON 坏）。进 ctx → checkpoint_json / run
    # 节点产出，run detail 可见，便于审查改稿意见是否被真正执行。
    return {
        "writer_output": out,
        "_writer_context_mode": context_mode,
        "writer_input": payload,
        "writer_model_id": writer_model_id,
        "revision_checklist": revision_checklist,
    }


def _polish_precheck_forbidden_words(db_path: str, chapter_id: str) -> list[str]:
    """项目绑定题材包的禁词表（题材库 P1b 读侧投影，与 chapter_review 同口径）。

    路径：chapters.project_id → ``GenrePackService.get_project_binding`` →
    ``forbidden_words(pack.payload)``（= ``payload.style_constraints.forbidden_words``，
    genre.consumers 单点解析）。未绑定 / 读库失败 / 极老库 → ``[]``（预检退化为
    「题材禁词零命中」，不阻断润色、不改变既有行为）。
    """
    try:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 —— 预检失败不阻断润色
        _log.warning(
            "chapter_write.polish_precheck chapter lookup failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return []
    project_id = row["project_id"] if row is not None else None
    if not project_id:
        return []
    try:
        binding = GenrePackService(db_path).get_project_binding(project_id)
    except Exception as exc:  # noqa: BLE001 —— 题材包读失败不阻断润色
        _log.warning(
            "chapter_write.polish_precheck genre_pack read failed: project_id=%s err=%s",
            project_id, exc,
        )
        return []
    if not binding or not binding.get("bound"):
        return []
    return forbidden_words(((binding.get("pack") or {}).get("payload")) or {})


def _polish_precheck(raw_prose: str, db_path: str, chapter_id: str) -> dict[str, Any]:
    """polisher 条件触发的确定性预检（零 LLM、零新规则）。

    三源全部复用既有检测器与既有阈值（见模块顶部常量区注释）：

    1. ``scan_ai_patterns``：AI 腔模式级命中（权威信号源，含禁用词 / 三连句式 /
       他她排比 / 章尾升华 / 标点滥用 / 解释腔，severity 与阈值内置）；
    2. ``count_forbidden_word_hits`` × 题材包禁词表：题材禁词子串计数
       （与 chapter_review._basic_checks 同源）；
    3. ``req_q7``：AI marker 密度 / 「然而|但是」段首占比 / 句长标准差三子指标
       （阈值常量在 quality.guardrails，单一权威）。

    返回值全部为 JSON 原生类型（进 ctx → checkpoint_json 可序列化；``req_q7``
    的 Issue（pydantic）在此投影为 dict）。
    """
    ai_hits = scan_ai_patterns(raw_prose)
    forbidden = _polish_precheck_forbidden_words(db_path, chapter_id)
    forbidden_hits = count_forbidden_word_hits(raw_prose, forbidden)
    q7_issues = req_q7(raw_prose)
    return {
        "ai_pattern_hits": ai_hits,
        "ai_pattern_hit_count": len(ai_hits),
        "forbidden_words": forbidden,
        "forbidden_word_hits": [
            {"word": word, "count": count} for word, count in forbidden_hits
        ],
        "forbidden_word_hit_count": sum(count for _, count in forbidden_hits),
        "q7_issues": [
            {
                "rule_id": issue.rule_id,
                "severity": issue.severity,
                "message": issue.message,
            }
            for issue in q7_issues
        ],
        "q7_issue_count": len(q7_issues),
    }


def _polish_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """P1 Polisher AI 节点（去 AI 腔文风润色）。

    行为：
    1. 取 ``writer_output.prose``，跑确定性预检 ``_polish_precheck``
       （scan_ai_patterns + 题材禁词 + req_q7 三源）。
    2. mock 直通判定：若 ``ctx["mock_providers"]`` 含 'writer' 但不含 'polisher'，
       跳过润色原样透传（既有用例零回归；优先级高于下方的条件触发门）。
    3. **P0 提速条件触发**：预检三源全零命中 → 跳过润色（``polisher_status=
       'skipped_clean'`` + ``polish_skipped=True`` + ``polish_precheck`` 命中统计），
       ``polished_prose`` 沿用 writer 产出；任一命中 → 走原润色路径（行为零变化）。
    4. 组装 polisher payload（draft_text + ai_findings + style_constraints）。
    5. 调 ``run_agent(..., agent_name='polisher', expected='polisher', mock_script=...)``。
    6. 长度守恒：``polished_text`` 与原文 ``visible_chars`` 差距 >10% → 兜底回退原文。
    7. 任何异常（prompt 缺失 / provider 异常 / 契约校验失败）→ 兜底回退原文，
       写 ``polisher_status='failed'``，**不**阻断 save_draft。
    8. 输出 ``polished_prose`` 进 ctx，``save_draft_node`` 优先取。
    """
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    chapter_id = ctx["chapter_id"]
    writer_output = ctx.get("writer_output") or {}
    raw_prose = strip_think_blocks(writer_output.get("prose") or "")
    mock_providers = ctx.get("mock_providers") or {}

    # mock 直通：既有 mock 流（仅含 writer、不含 polisher）原样透传，
    # 保证 test_chapter_write_revise_mode / test_chapter_write_scene_planner 等
    # 既有测试零回归；优先级高于下方条件触发门（mock 语义不抢）。
    if mock_providers and ("writer" in mock_providers) and ("polisher" not in mock_providers):
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": "mock 直通：未配置 polisher mock，原样透传 writer prose",
            "polisher_status": "passthrough",
            "ai_findings": [],
        }

    # P0 提速：条件触发预检（确定性检测器，零 LLM）。三源全零命中 ⇒ 无 AI 腔 /
    # 无题材禁词 / 句长与 marker 密度均在既有阈值内 ⇒ 跳过润色省一次 LLM 往返，
    # ``polished_prose`` 直接沿用 writer 产出（save_draft 的「polished_prose 优先、
    # 缺失回落 writer_output.prose」逻辑已就位，数据形态与 mock 直通路径同形）。
    # 任一命中 ⇒ 落回下方原润色路径，行为零变化。
    precheck = _polish_precheck(raw_prose, db_path, chapter_id)
    if (
        precheck["ai_pattern_hit_count"] == 0
        and precheck["forbidden_word_hit_count"] == 0
        and precheck["q7_issue_count"] == 0
    ):
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": "确定性预检零命中：跳过润色（沿用 writer 产出）",
            "polisher_status": "skipped_clean",
            "polish_skipped": True,
            "polish_skip_reason": (
                "预检三源零命中：ai_pattern_hits=0 / forbidden_word_hits=0 / "
                f"q7_issues=0（payload visible_chars={visible_chars(raw_prose)}）"
            ),
            "ai_findings": [],
            "polish_precheck": precheck,
        }

    ai_findings = precheck["ai_pattern_hits"]
    payload: dict[str, Any] = {
        "agent": "polisher",
        "prompt_version": "polisher:v1",
        "draft_text": raw_prose,
        "ai_findings": ai_findings,
        "style_constraints": {
            "language": "zh-Hans",
            "pov": "third_person_limited",
            "forbidden_words": ["仿佛", "如同", "宛如"],
        },
    }

    mock_script = mock_providers.get("polisher")
    try:
        out = run_agent(
            db_path,
            "polisher",
            payload,
            run_id,
            node_run_id=ctx.get("_current_node_run_id"),
            expected="polisher",
            mock_script=mock_script,
            profile_id=(ctx.get("model_overrides") or {}).get(
                capability_for("polisher")
            ),
        )
    except Exception as exc:  # noqa: BLE001 —— 任何失败均兜底回退原文
        _log.warning(
            "chapter_write.polisher degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": f"润色失败已回退原文：{exc}",
            "polisher_status": "failed",
            "polisher_error": str(exc),
            "ai_findings": ai_findings,
        }

    if not isinstance(out, dict):
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": "润色输出非 dict，已回退原文",
            "polisher_status": "failed",
            "ai_findings": ai_findings,
        }
    polished = out.get("polished_text")
    if not isinstance(polished, str):
        return {
            "polished_prose": raw_prose,
            "polish_changes_summary": "润色输出 polished_text 非字符串，已回退原文",
            "polisher_status": "failed",
            "ai_findings": ai_findings,
        }

    # 长度守恒：polished 与原文 visible_chars 差距 >10% → 回退原文（兜底）
    orig_len = visible_chars(raw_prose)
    new_len = visible_chars(polished)
    if orig_len > 0:
        delta = abs(new_len - orig_len) / orig_len
        if delta > 0.10:
            _log.warning(
                "chapter_write.polisher length drift: chapter_id=%s orig=%d new=%d delta=%.2f",
                chapter_id, orig_len, new_len, delta,
            )
            return {
                "polished_prose": raw_prose,
                "polish_changes_summary": (
                    f"润色超长度限制（Δ{delta:.0%}），已回退原文"
                ),
                "polisher_status": "length_drift",
                "ai_findings": ai_findings,
            }

    changes_summary = out.get("changes_summary") or ""
    return {
        "polished_prose": polished,
        "polish_changes_summary": changes_summary,
        "polisher_status": "ok",
        "ai_findings": ai_findings,
        "polisher_output": out,
    }


# ---------------------------------------------------------------------------
# 字数闭环：length_check + condense（V3.7+ P1 字数闭环节点）
# ---------------------------------------------------------------------------

# condense 最大重试轮次（含 condense 节点的执行次数）；超过则放行并标注偏差。
_MAX_CONDENSE_ROUNDS = 2


def _resolve_chapter_word_band(
    db_path: str, chapter_id: str
) -> tuple[int, dict[str, Any]]:
    """读 chapter → project → projects.word_band_json，覆盖 → resolve_band_config。

    返回 ``(chapter_project_id_or_empty, band_cfg)``。band_cfg 始终是合法可
    ``**word_band(...)`` 展开的 dict（无覆盖 = 模块默认 0.85/1.15/1200）。

    与 chapter_review/pipeline.py _basic_checks_node L111-156 同口径（共用
    ``resolve_band_config``），保证 review 侧与 write 侧字数带读源唯一。

    防御：``resolve_band_config`` 在 ratios/floor 非法值时会抛 ``ValueError``
    （如 low_ratio > high_ratio 倒挂、floor=负数等）。此处捕获并落默认带 + 告警，
    与项目内「非法 JSON → 视为无覆盖」的 fail-soft 风格一致。chapter_review 侧
    ``_basic_checks_node`` 是独立函数、未复用本 helper，行为差异不在本任务范围
    （已记 Known Issue / 后续可一并覆盖）。
    """
    chapter_project_id = ""
    try:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT project_id FROM chapters WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None and row["project_id"]:
            chapter_project_id = row["project_id"]
    except Exception:  # noqa: BLE001
        return chapter_project_id, resolve_band_config(None)

    if not chapter_project_id:
        return chapter_project_id, resolve_band_config(None)

    try:
        conn2 = get_connection(db_path)
        try:
            try:
                band_row = conn2.execute(
                    "SELECT word_band_json FROM projects WHERE project_id = ?",
                    (chapter_project_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                band_row = None
        finally:
            conn2.close()
    except Exception:  # noqa: BLE001
        band_row = None

    if band_row is None or not band_row["word_band_json"]:
        return chapter_project_id, resolve_band_config(None)
    try:
        parsed = json.loads(band_row["word_band_json"])
    except (TypeError, ValueError):
        return chapter_project_id, resolve_band_config(None)
    if not isinstance(parsed, dict):
        return chapter_project_id, resolve_band_config(None)
    try:
        return chapter_project_id, resolve_band_config(parsed)
    except ValueError as exc:
        # 非法值（low_ratio > high_ratio 倒挂 / floor 负数等）→ 落默认 + 告警。
        _log.warning(
            "chapter_write._resolve_chapter_word_band: invalid word_band_json "
            "project_id=%s err=%s; fall back to default band",
            chapter_project_id, exc,
        )
        return chapter_project_id, resolve_band_config(None)


def _resolve_target_word_count(ctx: dict[str, Any], plan: dict[str, Any]) -> int:
    """解析本章目标字数（与 _writer_node / review._basic_checks 同口径）。

    优先级：ctx 显式传入 → plan_json.expected_word_count → plan_json.target_word_count
    → 兜底 ``DEFAULT_TARGET_WORD_COUNT``（3000，全仓单源）。
    """
    raw = ctx.get("target_word_count")
    if raw:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    expected = plan.get("expected_word_count") if isinstance(plan, dict) else None
    if expected:
        try:
            return max(1, int(expected))
        except (TypeError, ValueError):
            pass
    legacy = plan.get("target_word_count") if isinstance(plan, dict) else None
    if legacy:
        try:
            return max(1, int(legacy))
        except (TypeError, ValueError):
            pass
    return DEFAULT_TARGET_WORD_COUNT


def _length_check_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """字数闭环：权威口径实测 polished_prose 字数，断是否在项目字数带内。

    输入优先级：``polished_prose``（polisher/fallback 后）→ ``writer_output.prose``。
    不调 LLM，pure Transform；纯依赖 ``visible_chars`` / ``classify_prose_length``。

    输出字段（ctx → checkpoint_json / run 节点产出均可见）：
    - ``length_report``：classify_prose_length 完整 dict（visible_chars/target/
      band_low/band_high/deviation_pct/status）外加 ``within_band`` bool
      （=status == 'in_band'）。
    - ``target_word_count``：本节点选用的目标字数，供 condense / save_draft 复用。
    - ``word_band_cfg``：本节点选用的 band 配置 dict，供 condense prompt 注入。
    - ``length_check_passed``：bool，True ⇒ condense 跳过，False ⇒ condense 触发。

    不抛错：DB 异常 / 空 prose 走保守路径（status='under', visible=0，
    within_band=False），下游 condense 若无可压缩文本会走 0 长度不动。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    plan = ctx.get("loaded_plan") or {}
    target = _resolve_target_word_count(ctx, plan)
    _, band_cfg = _resolve_chapter_word_band(db_path, chapter_id)

    writer_output = ctx.get("writer_output") or {}
    raw_prose = ctx.get("polished_prose")
    if not isinstance(raw_prose, str) or not raw_prose.strip():
        raw_prose = writer_output.get("prose") or ""
    if not isinstance(raw_prose, str):
        raw_prose = ""

    report = classify_prose_length(
        raw_prose, target, **band_cfg,
    )
    report["within_band"] = report["status"] == "in_band"
    # condense_rounds 已跑几轮（ctx 累积；首轮 length_check 默认 0）。
    rounds_used = int(ctx.get("condense_rounds") or 0)
    return {
        "length_report": report,
        "target_word_count": target,
        "word_band_cfg": band_cfg,
        "length_check_passed": report["within_band"],
        # 主会话约定：唯一计数键 ctx['condense_rounds']，length_check 仅 echo 不改写。
        "condense_rounds": rounds_used,
    }


def _condense_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """字数闭环：超带时调 polisher capability 做压缩（节点内自循环，最多 2 轮）。

    触发条件：``length_check_passed`` False 且 ``condense_rounds < _MAX_CONDENSE_ROUNDS (2)``
    **且 payload 超带上限**（``visible_chars(payload) > _CONDENSE_PAYLOAD_TRIGGER_RATIO
    × band_high``；P0 提速压缩预算门，见模块顶部常量区）。
    不触发条件：带内 / 已达 2 轮 / payload 未超带上限（``skipped_below_budget``）
    → 直接放行（均不调 LLM、不计轮）。

    mock 守卫（与 ``_polish_node`` 同款语义，V3.9 批次 1.1 修复生产不可达）：
    - ``mock_providers`` 为 None / {}（生产）⇒ ``mock_script=None`` 走真实 provider；
    - ``mock_providers`` 非空但未配 ``condense`` 脚本（writer-only 等 mock 流）
      ⇒ 原样透传 ``condense_status='skipped_no_mock'``，既有测试零回归。

    内循环（兑现「≤2 轮」承诺）：调 LLM → ``visible_chars`` 实测 → 仍超
    ``band_high`` 且轮数未满 → 再调；每轮 payload 都用最新实测数字重建。
    任一轮产出被拒收 / 调用失败即终止循环（不空转消耗轮次预算）。

    计数器约定（smart 审查 P2 一致性收口）：
    - **唯一计数键** ``ctx['condense_rounds']``（length_check / condense /
      save_draft / 偏差注记 全部读同一把 key）。
    - 计轮规则：每发起一次 LLM 调用（mock 或真实；异常 / 产出被拒收同样计）
      → ``condense_rounds + 1``；
      skipped_in_band / skipped_empty / skipped_no_mock / skipped_below_budget /
      over_band_after_2_rounds **均不计轮**（不 +1；早返回路径 preserve rounds_used 不动）。
    - 返回值补 ``condense_status`` 供观测（早返回路径也带，便于 run detail 一眼看清）。

    输出确定性守卫（防 LLM 返回空 / 更长 / 非字符串把坏草灌进 drafts）：
    - 空串 / 非 str / visible_chars >= 压缩前 visible_chars → 拒收，保留当前 prose，
      ``condense_status='failed_no_shrink'``，**计 1 轮**（调用过 LLM 但产出无效）。

    行为契约：
    - payload 必须携带实测数字与目标带（**不**让 LLM 自报字数自决目标）。
    - prompt 注入 writer-v1 §6.1 规则 20 压缩纪律（优先砍铺垫/重复意象/冗词，
      不砍节拍、不删场景、保持文风与既有设定用语）。
    - 任何失败（prompt 缺失 / provider 异常 / 契约失败）→ 兜底保留当前
      polished_prose 不动；``condense_status='failed'``，**不**阻断 save_draft。
    - 输出 ``polished_prose``（压缩后正文）+ 真实轮数 + **重算后的
      ``length_report`` / ``length_check_passed``**（节点产出 merge 进 ctx，覆盖
      上游 length_check 的旧值，使 save_draft 读到压缩后真相）。
    """
    db_path = ctx["db_path"]
    run_id = ctx.get("run_id", "")
    chapter_id = ctx["chapter_id"]
    mock_providers = ctx.get("mock_providers") or {}

    report = ctx.get("length_report") or {}
    rounds_used = int(ctx.get("condense_rounds") or 0)
    within = bool(ctx.get("length_check_passed"))

    # ---- 早返回路径：均不计轮，preserve rounds_used ----
    if within:
        return {
            "polished_prose": ctx.get("polished_prose") or "",
            "condense_status": "skipped_in_band",
            "condense_rounds": rounds_used,
        }

    if rounds_used >= _MAX_CONDENSE_ROUNDS:
        # 已达 2 轮上限仍超带：放行 + 标注偏差（save_draft 会写到 deviations）。
        return {
            "polished_prose": ctx.get("polished_prose") or "",
            "condense_status": "over_band_after_2_rounds",
            "condense_rounds": rounds_used,
            "condense_report": report,
            "length_report": report,
            "length_check_passed": False,
        }

    current_prose = ctx.get("polished_prose")
    if not isinstance(current_prose, str) or not current_prose.strip():
        # 没东西可压缩（length_check 走兜底 0 字路径）→ 跳过不调 LLM；不计轮。
        return {
            "polished_prose": current_prose or "",
            "condense_status": "skipped_empty",
            "condense_rounds": rounds_used,
        }

    # mock 守卫（同 _polish_node 语义）：mock 流（mock_providers 非空但没配
    # condense 脚本）→ 原样透传，既有 writer-only mock 测试零回归；生产链路
    # （mock_providers 为 None / {}）⇒ condense_mock=None ⇒ run_agent 走真实 provider。
    condense_mock = (
        mock_providers.get("condense") if isinstance(mock_providers, dict) else None
    )
    if mock_providers and not condense_mock:
        # 没调 LLM，不占轮次预算（preserve rounds_used）。
        return {
            "polished_prose": current_prose,
            "condense_status": "skipped_no_mock",
            "condense_rounds": rounds_used,
        }

    # ---- P0 提速·压缩预算门：payload 未超带上限 ⇒ 无压缩余量 → 跳过 ----
    # 判据 = ``_CONDENSE_PAYLOAD_TRIGGER_RATIO`` × band_high（模块顶部常量，取值依据
    # 见常量区注释：band_high 是「已无压缩余量」的既有权威边界）。带上限优先取
    # length_check 写回的 ``length_report.band_high``；直接调节点（单测 / 绕过
    # length_check）时按同源口径回读项目字数带，与下方 prompt 装配逐字同口径。
    # 位置：在 mock 守卫之后（mock 流的既有透传语义零变化）、真实 provider 路径之前。
    band_high = int(report.get("band_high") or 0)
    if band_high <= 0:
        band_target = int(
            report.get("target")
            or ctx.get("target_word_count")
            or DEFAULT_TARGET_WORD_COUNT
        )
        band_cfg_gate = ctx.get("word_band_cfg")
        if not isinstance(band_cfg_gate, dict):
            _, band_cfg_gate = _resolve_chapter_word_band(db_path, chapter_id)
        band_high = int(
            classify_prose_length(current_prose, band_target, **band_cfg_gate)[
                "band_high"
            ]
        )
    payload_chars = visible_chars(current_prose)
    trigger_chars = int(band_high * _CONDENSE_PAYLOAD_TRIGGER_RATIO)
    if payload_chars <= trigger_chars:
        # 未超带（含 status='under' 欠字形态：压缩只会更短，内循环又以
        # status != 'over' 为收工判据 ⇒ 白调一次 LLM 还把正文压坏）。不计轮：
        # 没调 LLM，preserve rounds_used（与其余早返回路径同款）。
        return {
            "polished_prose": current_prose,
            "condense_status": "skipped_below_budget",
            "condense_rounds": rounds_used,
            "condense_skipped": True,
            "condense_skip_reason": (
                f"payload {payload_chars} 字 ≤ 压缩预算阈值 {trigger_chars} 字"
                f"（带上限 {band_high} × {_CONDENSE_PAYLOAD_TRIGGER_RATIO:.2f}）："
                "未超带，无压缩余量"
            ),
            "condense_payload_chars": payload_chars,
            "condense_budget_chars": trigger_chars,
        }

    if not run_id:
        # 单测路径可能没传 run_id；安全降级为「不入库」占位
        run_id = ""

    target = int(report.get("target") or ctx.get("target_word_count") or DEFAULT_TARGET_WORD_COUNT)
    band_cfg = ctx.get("word_band_cfg")
    if not isinstance(band_cfg, dict):
        # 直接调节点（单测 / 绕过 length_check）时 ctx 无 word_band_cfg →
        # 回读项目级覆盖，与 length_check 同源同口径。
        _, band_cfg = _resolve_chapter_word_band(db_path, chapter_id)

    def _measure(text: str) -> dict[str, Any]:
        measured = classify_prose_length(text, target, **band_cfg)
        measured["within_band"] = measured["status"] == "in_band"
        return measured

    # 每轮都以「当前 prose 的实测」重建 payload：内循环第二轮必须携带压缩后的
    # 真实数字（不能让 LLM 拿上一轮的旧字数自决目标）。
    final_report = _measure(current_prose)
    rounds = rounds_used
    status = "ok"
    error_msg: str | None = None
    changes_summary = ""

    # ---- 内循环：调 LLM → visible_chars 实测 → 仍超 band_high 且轮数未满 → 再调 ----
    while rounds < _MAX_CONDENSE_ROUNDS:
        visible = int(final_report.get("visible_chars") or 0)
        band_low = int(final_report.get("band_low") or 0)
        band_high = int(final_report.get("band_high") or target)
        excess = max(0, visible - band_high)
        excess_pct = round(visible / target * 100, 1) if target > 0 else 0.0
        target_pct = round(target / visible * 100, 1) if visible > 0 else 0.0

        payload: dict[str, Any] = {
            "agent": "condense",
            "prompt_version": "condense:v1",
            "draft_text": current_prose,
            "length_context": {
                "visible_chars": visible,
                "target": target,
                "band_low": band_low,
                "band_high": band_high,
                "excess_chars": excess,
                "excess_pct": excess_pct,
                "target_pct": target_pct,
                "round": rounds + 1,
                "max_rounds": _MAX_CONDENSE_ROUNDS,
            },
            "condense_directive": (
                "实测当前正文「{visible} 字」，目标字数带 [low={low}, high={high}] "
                "（target={target}）。当前超出 band_high 约 {excess_pct}%（约 {excess} 字），"
                "需净减至 band_high 以内（净减至 target 的 {target_pct}% 量级）。\n\n"
                "压缩纪律（writer-v1 §6.1 规则 20）：\n"
                "1. 优先砍铺垫、重复意象、冗词、装饰性修辞；\n"
                "2. 不砍节拍、不删场景、不改情节线、不删关键人物动作；\n"
                "3. 保持既有文风与人称设定；\n"
                "4. 保持既有设定用语（角色名、地名、专有名词不替换）。\n\n"
                "输出 JSON：{{\"polished_text\": <压缩后正文>, "
                "\"changes_summary\": <改动说明>}}"
            ).format(
                visible=visible,
                low=band_low,
                high=band_high,
                target=target,
                excess_pct=excess_pct,
                excess=excess,
                target_pct=target_pct,
            ),
        }

        # 计轮先于调用：只要意图调 LLM 就算 1 轮（异常 / 拒收同样计），
        # 与既有计数器语义一致（旧实现 next_rounds = rounds_used + 1 在 try 之前）。
        rounds += 1

        try:
            out = run_agent(
                db_path,
                "polisher",  # 复用 polisher capability 绑定（capability_for('polisher')=creative_writing）
                payload,
                run_id,
                node_run_id=ctx.get("_current_node_run_id"),
                expected="polisher",
                mock_script=condense_mock,  # None（生产）⇒ run_agent 走真实 provider
                profile_id=(ctx.get("model_overrides") or {}).get(
                    capability_for("polisher")
                ),
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "chapter_write.condense degraded: chapter_id=%s err=%s",
                chapter_id, exc,
            )
            status = "failed"
            error_msg = str(exc)
            break

        if not isinstance(out, dict):
            status = "failed"
            break
        new_prose = out.get("polished_text")
        if not isinstance(new_prose, str):
            status = "failed"
            break

        # ---- 输出确定性守卫：拒空 / 拒更长 ----
        # 0 字 / 空串显然无效；visible_chars 没缩短（>= 原 visible）意味着 LLM 没做
        # 实压缩或反而扩写 —— 一律拒收，避免坏草灌进 drafts。
        new_visible = visible_chars(new_prose)
        if new_visible == 0 or new_visible >= visible:
            _log.warning(
                "chapter_write.condense rejected output: chapter_id=%s "
                "orig_visible=%d new_visible=%d",
                chapter_id, visible, new_visible,
            )
            status = "failed_no_shrink"
            error_msg = f"condense output visible_chars={new_visible} (orig={visible})"
            break

        # 采纳本轮压缩结果，并以压缩后文本重测（不再信任 length_check 旧值）。
        current_prose = new_prose
        changes_summary = str(out.get("changes_summary") or "")
        final_report = _measure(current_prose)
        if final_report["status"] != "over":
            # 已落回 band_high 以内（in_band / under）→ 压缩闭环达成，收工。
            status = "ok"
            break
        if rounds >= _MAX_CONDENSE_ROUNDS:
            # 轮次已耗尽仍超 band_high → 放行（save_draft 写偏差注记）。
            status = "over_band_after_2_rounds"
            break

    result: dict[str, Any] = {
        "polished_prose": current_prose,
        "condense_status": status,
        "condense_rounds": rounds,
        "condense_report": final_report,
        # 压缩后真相：覆盖上游 length_check 的旧 length_report / length_check_passed，
        # 使 save_draft 的 word_count 与偏差注记都按最终 prose 口径。
        "length_report": final_report,
        "length_check_passed": bool(final_report.get("within_band")),
    }
    if changes_summary:
        result["condense_changes_summary"] = changes_summary
    if error_msg:
        result["condense_error"] = error_msg
    return result


def _save_draft_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    run_id = ctx["run_id"]
    writer_output = ctx.get("writer_output") or {}
    # P1：polished_prose 优先；缺失（passthrough / 失败兜底）则回落 writer 原始 prose。
    prose = strip_think_blocks(
        ctx.get("polished_prose") or writer_output.get("prose") or ""
    )
    # V3.9 批次 1.2 字数断链修复：word_count 直接对最终 prose 取权威实测
    # （visible_chars，去空白口径）。既不读 condense 之前的 length_report.visible_chars
    # （length_check 在 condense 上游、压缩后不再重跑，旧值会污染落库字数），
    # 也不采信 self_report.word_count（实证 ch4-6：自报 3008-3172 vs 实测 4721-5842，
    # 偏差 50%+）。
    word_count = visible_chars(prose)
    # 偏差注记的实测口径：condense 节点已把压缩后的 length_report merge 回 ctx
    # （批次 1.1 回写），此处读到的是压缩后真相，不是 length_check 的旧值。
    length_report = ctx.get("length_report") or {}
    prompt_version = writer_output.get("prompt_version") or "writer:v1"

    draft_id = new_id("dr")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        # 字数闭环偏差注记：仅当 condense_rounds >= 2（已穷尽压缩预算）且仍超带
        # 时才写「2 轮后仍超带」注记（smart 审查 P2：避免 round=0 / mock 流跳过等
        # 早返回路径也错误触发此文案）。批次 1.1 把「≤2 轮」循环收进 condense 节点
        # 后该分支真实可达（生产链路不再被 skipped_no_mock 截断）；注记中的
        # visible_chars 取压缩后 length_report，与落库 word_count（最终 prose 实测）
        # 同口径。其他场景（带内 / 单轮已落带）不写 deviations，避免污染 W-LEN 报告。
        condense_rounds_now = int(ctx.get("condense_rounds") or 0)
        if (
            condense_rounds_now >= _MAX_CONDENSE_ROUNDS
            and not bool(ctx.get("length_check_passed"))
            and isinstance(length_report, dict)
            and length_report.get("status") in ("over", "under")
        ):
            dev_entry = {
                "from": "word_count_target",
                "to": (
                    f"实测 {int(length_report.get('visible_chars') or word_count)} 字 "
                    f"(target={int(length_report.get('target') or 0)}, "
                    f"band=[{int(length_report.get('band_low') or 0)},"
                    f"{int(length_report.get('band_high') or 0)}], "
                    f"deviation_pct={float(length_report.get('deviation_pct') or 0.0)}%, "
                    f"status={length_report.get('status')})"
                ),
                "reason": (
                    "字数闭环 condense 2 轮后仍超带，按生产实证 ch4-6 规律"
                    "（writer 原始产出 4721-5842 字 vs 自报 3008-3172，偏差 50%+）"
                    "标记为 report-only 兜底；下游评审 W-LEN 报告应据此评估。"
                ),
                "source": "chapter_write.length_check",
                "round": condense_rounds_now,
                "created_at": now,
            }
            try:
                chap_row = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?",
                    (chapter_id,),
                ).fetchone()
                plan_obj: dict[str, Any] = {}
                if chap_row is not None and chap_row["plan_json"]:
                    try:
                        parsed_plan = json.loads(chap_row["plan_json"])
                        if isinstance(parsed_plan, dict):
                            plan_obj = parsed_plan
                    except (TypeError, ValueError):
                        plan_obj = {}
                deviations = plan_obj.get("deviations")
                if not isinstance(deviations, list):
                    deviations = []
                deviations.append(dev_entry)
                plan_obj["deviations"] = deviations
                conn.execute(
                    "UPDATE chapters SET plan_json = ?, updated_at = ? "
                    "WHERE chapter_id = ?",
                    (json.dumps(plan_obj, ensure_ascii=False), now, chapter_id),
                )
            except sqlite3.OperationalError:
                # 缺列 / 缺表：兜底不阻断 save_draft
                pass
        # 先看是否有 draft 行（按 chapter + version 累计）
        max_row = conn.execute(
            "SELECT MAX(version) AS v FROM drafts WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        next_v = (max_row["v"] or 0) + 1
        conn.execute(
            """
            INSERT INTO drafts (draft_id, chapter_id, version, content, created_by,
                                prompt_version, model_id, created_at)
            VALUES (?, ?, ?, ?, 'writer:v1', ?, ?, ?)
            """,
            (
                draft_id,
                chapter_id,
                next_v,
                prose,
                prompt_version,
                ctx.get("writer_model_id") or "mock/mock",
                now,
            ),
        )
        # chapters.status 推进/回退：
        # - PLANNED→DRAFTED：首写；
        # - DRAFTED：重跑追加新 draft 版本（人工改稿循环）；
        # - REVIEWED→DRAFTED：批准后改稿（双层评审 SOP 的 smart 二审可能推翻
        #   critic 批准）——新草稿使旧批准失效，回退 DRAFTED 强制重审；
        # 与 domain chapter 服务 _DRAFT_ALLOWED_STATUS={DRAFTED, REVIEWED} 口径一致；
        # COMMITTED/RELEASED 仍硬拒（须先 rollback commit）。
        cur = conn.execute("SELECT status FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if cur is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        status = cur["status"]
        if status == "PLANNED":
            conn.execute(
                "UPDATE chapters SET status = 'DRAFTED', updated_at = ? WHERE chapter_id = ?",
                (now, chapter_id),
            )
        elif status == "REVIEWED":
            conn.execute(
                "UPDATE chapters SET status = 'DRAFTED', updated_at = ? WHERE chapter_id = ?",
                (now, chapter_id),
            )
        elif status != "DRAFTED":
            raise ValueError(
                f"chapter {chapter_id} status={status} 不允许 write，"
                "仅 PLANNED/DRAFTED/REVIEWED 可写"
            )
        conn.commit()
    finally:
        conn.close()
    return {
        "draft_id": draft_id,
        "draft_version": next_v,
        "word_count": word_count,
        "prompt_version": prompt_version,
        "chapter_id": chapter_id,
        "workflow_run_id": run_id,
    }


def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("load_plan", "Transform", _load_plan_node),
        WorkflowNode(
            "scene_planner", "AI", _scene_planner_node, agent_name="scene_planner"
        ),
        WorkflowNode("writer", "AI", _writer_node, agent_name="writer"),
        WorkflowNode("polisher", "AI", _polish_node, agent_name="polisher"),
        WorkflowNode("length_check", "Transform", _length_check_node),
        WorkflowNode("condense", "AI", _condense_node, agent_name="polisher"),
        WorkflowNode("save_draft", "State", _save_draft_node),
    ]


WORKFLOW = {
    "name": "chapter-write",
    "version": "v1",
    "description": (
        "Director Plan → Scene Planner(AI) → Writer prose → Polisher(AI) → "
        "length_check(权威实测+项目字数带) → condense(超带≤2轮,polisher capability) → "
        "drafts table; chapter status PLANNED→DRAFTED; "
        "scene_planner / polisher / condense 失败均降级不阻断 writer"
    ),
    "nodes": _build_nodes(),
    # V3.9 批次 2.4 checkpoint 写放大治理：此前本 workflow 无 exclude 清单，引擎
    # 每节点完成都把整份 ctx 序列化落 workflow_runs.checkpoint_json（engine.py:712）。
    # 生产实测：单章 checkpoint 25 万~28 万 UTF-8 字节（其中 writer_input 19K~23K 字符
    # ≈57~69KB），整章 7 节点 ⇒ 7 次该量级的重复序列化 UPDATE。
    # 语义：engine._scrub_ctx_for_checkpoint（engine.py:73-84）只在**落盘前**浅拷贝
    # 剔除顶层键，run 内 ctx 对象不受影响——同 run 上下游节点（writer → polisher →
    # length_check → condense → save_draft）照常读到全量键，执行结果零变化。
    #
    # resume 安全性（批次 2.4 第二步核对结论）：
    # - 本链 7 节点全是 Transform / AI / State，**无 Human 节点**，没有任何节点抛
    #   PauseRequested（本模块不 import 该异常）→ run 永不 PAUSED → engine.resume
    #   （engine.py:398-438，唯一从 checkpoint_json 反序列化重建 ctx 的路径）不可达；
    # - 崩溃恢复 recover_interrupted_runs（engine.py:923-1015）只把残留 RUNNING run /
    #   节点行标 FAILED，**不读也不重建 checkpoint 里的 ctx** → 无「恢复后键缺失」场景；
    # - checkpoint 因此只服务运行详情审计展示。下方「必保留」组已覆盖各节点
    #   ctx.get(...) 兜底所需的键，未来若新增 Human 节点也不会静默缺键。
    # 口径提醒：本清单只对 chapter-write 成立，勿照搬到有 Human 节点 / resume 消费者的
    # workflow（如 chapter-commit 的 checkpoint_json['quality_gate'] 是前端消费契约）。
    "checkpoint_exclude": [
        # ---- 体积大头：writer 装配 payload ----
        # 生产实测 19KB（最大 125KB 的 run 里占 23KB）。仅作测试断言 / 可观测钩子
        # （_writer_node 注释自陈），run 内后续节点一律不读它——polisher /
        # length_check / condense / save_draft 读 writer_output / polished_prose。
        "writer_input",
        # ---- 节点镜像键：引擎除顶层 merge 外还会把整套节点产出存 ctx[node_id]
        #      （engine.py:670）→ 与 workflow_run_nodes.output_json 逐字节重复 ----
        # writer 镜像内含 writer_input，不剔除则上一条形同虚设（payload 仍会经镜像键
        # 落盘）；scene_planner / polisher / condense 镜像同理内含各自的大产出。
        # 7 个 node_id 全列，口径统一为「节点产出的权威副本在 workflow_run_nodes，
        # checkpoint 只留顶层 merge 的可读视图」。
        "load_plan",
        "scene_planner",
        "writer",
        "polisher",
        "length_check",
        "condense",
        "save_draft",
        # ---- 派生大对象：与顶层权威视图重复 ----
        # scene_planner_output：scene_planner agent 原始输出（含全部 scene slots /
        # 冲突 / 信息边界），顶层 scene_plan 已是下游消费的权威结构。
        "scene_planner_output",
        # polisher_output：polisher agent 原始输出，polished_text 与顶层
        # polished_prose 逐字重复（生产实测 8KB）。
        "polisher_output",
        # ---- 必保留（run 详情可读性 + 未来 resume 路径的节点兜底输入）----
        # 不在此清单：polished_prose / writer_output（最终正文与 writer 结构化产出，
        # 单章 3~10KB，run 详情主要可读产物，也是 save_draft 的 prose 来源）；
        # scene_plan / loaded_plan（writer 硬依赖 ctx["scene_plan"] 与章计划原文）；
        # length_report / length_check_passed / condense_rounds / condense_status /
        # target_word_count / word_band_cfg（字数闭环控制流小键，合计 <1KB）；
        # _reference_canon_consumed（参照系消费审计链，canon.py 明示随 ctx 落 checkpoint）；
        # db_path / project_id / chapter_id / run_id / writer_model_id 等执行控制键。
    ],
}


# 注意（Sprint V1.5）：注册动作统一在 :mod:`packages.workflows.chapter_write.__init__`
# 调用 :func:`packages.core.workflow_registry.register_workflow`；本模块不再暴露
# ``register_workflow`` 函数。


__all__ = ["WORKFLOW"]
