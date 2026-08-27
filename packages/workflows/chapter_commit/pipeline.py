"""chapter_commit 工作流（Sprint 4-A + Sprint 6 下半 quality_gate）。

节点列表：
- ``build_observer_ctx`` (Transform) —— 调 :func:`build_observer_input` 组装 observer 输入。
- ``observer`` (AI) —— 调 observer agent 输出 7 个 change 数组（业务载荷）。
- ``inject_validate`` (Transform) —— 注入 10 元信息字段（delta_id=新 ID, schema_version="state-delta-v0",
  workflow_run_id=run_id, created_by="observer:v1", created_at=now_iso 等）；
  调 :meth:`StoryStateService.submit_delta` 校验；校验失败 → run FAILED。
- ``quality_gate`` (State) —— **Sprint 6 新增**：现场组装 :class:`QualityContext`，
  调 :class:`QualityEngine` 评估并落 ``quality_reports``；
  **模式**由环境变量 ``NOVELOS_QUALITY_GATE``（或 ``ctx["quality_gate_mode"]``）控制：
  - ``"enforce"``（默认）—— 任一 ``severity == 'error'`` ⇒ 抛
    ``ValueError("quality gate blocked: ...")`；run 收尾 FAILED，chapter 保持 REVIEWED。
  - ``"report"`` —— error 只落库不阻断；run 收尾 COMPLETED（评审展示用，便于 ``evals/runner`` 通过）。
  eval golden / 测试需显式传入 ``quality_gate_mode="report"`` 避免 REQ-Q8 / H-3 等 MVP 阻断规则误伤。
- ``high_risk_approval`` (Human) —— **仅当 payload 含 HIGH/definition/rule change 时暂停**，
  payload=change 清单；human_input={"approved": true}。
- ``commit`` (State) —— 调 :meth:`StoryStateService.commit_delta`；
  chapters.status REVIEWED→COMMITTED；若当前 DRAFTED（未过 review）则 run FAILED 提示先跑 review。
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
from typing import Any

from packages.core.agent_runtime.runner import _update_workflow_run, run_agent
from packages.core.context_engine import build_observer_input
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.engine import QualityEngine
from packages.core.quality.models import Issue
from packages.core.quality.models import QualityReport as _QualityReport
from packages.core.quality.service import (
    QualityService,
    build_quality_context,
    capture_reference_consumption,
)
from packages.core.story_state.delta_repair import repair_delta
from packages.core.story_state.service import StoryStateService
from packages.core.story_state.validator import validate_delta
from packages.core.workflow_runtime.engine import PauseRequested, WorkflowNode

# Observer delta 校验失败重试提示模板（注入 payload._retry_hint 引导 LLM 修正）。
# 真实 LLM（如 MiniMax-M3）曾出现 ``character_changes[0].op='update' 但 before 为 None``
# 这类业务校验失败：让 observer 修正后重新完整输出 7 个 change 数组 JSON。
_OBSERVER_RETRY_HINT_TEMPLATE = (
    "\n\n[Validation note] 上一次输出的 delta 未通过业务校验：{errors}。"
    "请按反馈修正后重新完整输出 7 个 change 数组的合法 JSON（保持 schema_version="
    "state-delta-v0 外的其它元信息字段由后续节点注入，无需在本次输出中包含）。"
    "特别注意：凡 snapshot 中不存在前值的实体（本章首次出现的人物/地点/设定），"
    "必须用 add 而非 update；update 必须给出与 snapshot 一致的 before。"
)


# ============================================================================
# V3.1.1 O-2：observer 双腿拆分（entities / narrative）—— 拆分依据与开关
# ============================================================================
# 根因：单次 observer 大请求（输入 ~31k 字符 + 输出 1.6-5.8万 token）在 provider 拥堵
# 窗口下反复 480s 超时（ch060/ch063 多轮实证）。两条轻量腿各自只覆盖对应 scope，输出
# token 量减半并可走 light capability（V3.0 P0-2 已有；缺失自动回退 reasoning）。
#
# 两腿划分（与 docs/agents/prompts/observer-v1.md §11 extraction_scope 对齐）：
# - leg_a (entities)：character_changes + relationship_changes + world_changes
# - leg_b (narrative)：new_events + new_hooks + resolved_hooks + debt_changes
#
# 合并：leg_a 优先（同 key 冲突时 entity leg 覆盖，避免 narrative leg 误覆盖
# canonical 状态字段）。同 key 由 change_id 判定；change_id 缺失时按数组内
# 出现顺序（保留 leg_a 的全部条目，再追加 leg_b 中不冲突的条目）。
#
# 重试语义：解析 validator errors 中出现的数组名，把仅与某一腿关联的 errors
# 路由到对应腿（其它腿保持首次响应）。无法归类（如 errors 涉及跨腿的字段）
# 时回退到「双腿都重试」——既有 retry 预算（最多 1 次）保持不变。
#
# Mock 兼容：golden regression 的 mock_script 是完整 7 数组 mock。两腿消费同一
# mock_script 时（MockProvider list 模式按调用顺序取元素），会让 leg B 拿到 leg A
# 已消耗的元素。两腿需各自拿到全量 mock 输出，再在 merge 阶段按 scope 过滤——
# 由 ``_filter_mock_for_leg`` 在 pipeline 入口处预处理 mock_script。
#
# ============================================================================
# V3.1.1 O-3：按腿裁剪 previous_state + recent_event_ids 白名单
# ============================================================================
# 根因（O-2 遗留）：
# ①两条腿各自携带全量 trimmed previous_state（各 ~30k 字符），合计 ~60k；
#   observer 阶段总输入翻倍（~120k tokens），重 leg 仍偏重。
# ②observer 在 narrative 腿生成 new_events[*].event_id 时可能与 plot_events.event_id
#   主键撞车，触发 UNIQUE constraint failed（ch063 现场）。
# 设计：
# - leg_a (entities)：只保留实体集合（characters / locations / factions /
#   world_rules / relationships）+ 顶层元信息；移除 events / hooks / debts 集合。
# - leg_b (narrative)：保留 events（trimmed 滚动窗口）+ hooks（压缩口径）+ debts；
#   实体集合降级为标识性摘要（{id, name, status}），便于 new_events[*].participants
#   引用；world_rules 保留 name/statement 摘要（事件可能触发规则变化引用）。
# - 两腿各自注入 ``snapshot_trim_stats``（按腿统计）。
# - recent_event_ids 白名单由 pipeline 从 DB 取最近 30 条注入 payload.config。
# - per-leg 字符数计入 observer_split_meta.leg_a_chars/leg_b_chars，便于量化收益。
_OBSERVER_LEG_A_ARRAYS = ("character_changes", "relationship_changes", "world_changes")
_OBSERVER_LEG_B_ARRAYS = ("new_events", "new_hooks", "resolved_hooks", "debt_changes")
_OBSERVER_ALL_ARRAYS = _OBSERVER_LEG_A_ARRAYS + _OBSERVER_LEG_B_ARRAYS
_OBSERVER_LEG_A_SET = frozenset(_OBSERVER_LEG_A_ARRAYS)
_OBSERVER_LEG_B_SET = frozenset(_OBSERVER_LEG_B_ARRAYS)


def _observer_split_enabled() -> bool:
    """环境开关 ``NOVELOS_OBSERVER_SPLIT``；默认 on；off 走旧单次路径。

    优先级：``ctx['observer_split']``（测试 / 调用方显式覆盖）> 环境变量。
    """
    from_env = os.environ.get("NOVELOS_OBSERVER_SPLIT", "on").strip().lower()
    return from_env not in ("0", "false", "off", "no")


def _observer_parallel_enabled(ctx: dict[str, Any] | None = None) -> bool:
    """V3.5：observer 双腿并发开关。

    环境变量 ``NOVELOS_OBSERVER_PARALLEL`` 默认 on；off 回退串行提交。

    优先级：
    - ``ctx['observer_parallel']=False/True``（测试 / 调用方显式覆盖，最高优先级）；
    - 否则读环境变量 ``NOVELOS_OBSERVER_PARALLEL``，缺省视为 on；
    - off 取值：``0 / false / off / no``。

    设计动机（V3.5 提速调研结论）：见 ``docs/roadmap/v3-plan.md`` 已知问题节——
    service_tier=priority 已启用，但 provider 拥堵窗口仍可能饿死双腿；
    并发提交能让 wall_time 接近 max(leg_a, leg_b) 而非 sum（每腿实测 60-180s）。
    """
    if ctx is not None and "observer_parallel" in ctx:
        override = ctx.get("observer_parallel")
        if isinstance(override, bool):
            return override
        if isinstance(override, str):
            return override.strip().lower() not in ("0", "false", "off", "no")
        return bool(override)
    from_env = os.environ.get("NOVELOS_OBSERVER_PARALLEL", "on").strip().lower()
    return from_env not in ("0", "false", "off", "no")


def _summary_parallel_enabled(ctx: dict[str, Any] | None = None) -> bool:
    """V3.7：summarizer 与 observer 双腿同池并发开关。

    环境变量 ``NOVELOS_SUMMARY_PARALLEL`` 默认 on；off 时 observer 节点不提前调
    summarizer，下游 ``summarize`` 节点走原 prepare+run_agent 路径。

    优先级：
    - ``ctx['summary_parallel']=False/True``（测试 / 调用方显式覆盖，最高优先级）；
    - 否则读环境变量 ``NOVELOS_SUMMARY_PARALLEL``，缺省视为 on；
    - off 取值：``0 / false / off / no``。

    设计动机（V3.7 提速调研结论）：summarizer 单次调用实测 25-80s，与 observer 双腿
    并发跑可省墙钟。失败语义保持不变：summary 异常仅丢 ``summary_early``，observer
    节点自身不抛错，summarize 节点能自愈（重走 ``_prepare_summarizer_call`` +
    ``run_agent``）完成。
    """
    if ctx is not None and "summary_parallel" in ctx:
        override = ctx.get("summary_parallel")
        if isinstance(override, bool):
            return override
        if isinstance(override, str):
            return override.strip().lower() not in ("0", "false", "off", "no")
        return bool(override)
    from_env = os.environ.get("NOVELOS_SUMMARY_PARALLEL", "on").strip().lower()
    return from_env not in ("0", "false", "off", "no")


def _filter_mock_for_leg(mock_script: Any, leg: str) -> Any:
    """按 leg 过滤 mock_script：让两条腿各自拿到「只含本 leg 范围」mock。

    golden regression 的 mock 是完整 7 数组 mock；不做过滤的话两条腿会按 list
    顺序消费不同元素，leg B 会拿到 leg A 已用过的响应。本函数把每条 mock JSON
    解析后只保留本 leg 的数组（其它数组置为空列表），两条腿各自消费同一份
    全量 mock 但只看到本 leg 的内容——merge 后等价于单次大调用。
    """
    if mock_script is None:
        return None
    if callable(mock_script):
        # callable 模式：调用方按 i 取响应；过滤留给调用方。
        return mock_script
    if isinstance(mock_script, str):
        items = [mock_script]
    elif isinstance(mock_script, list):
        items = mock_script
    else:
        return mock_script

    keep = _OBSERVER_LEG_A_SET if leg == "entities" else _OBSERVER_LEG_B_SET
    out: list[str] = []
    for item in items:
        try:
            data = json.loads(item)
        except (TypeError, ValueError):
            # 非 JSON 透传：保留原样（runner 内部 extract_json 会再尝试一次）。
            out.append(item)
            continue
        if not isinstance(data, dict):
            out.append(item)
            continue
        filtered: dict[str, Any] = {}
        for arr_name in _OBSERVER_ALL_ARRAYS:
            if arr_name in keep:
                # 保留本 leg 的数组：原样拿；若不存在则置空列表，保证 7 数组齐全
                filtered[arr_name] = data.get(arr_name) or []
            else:
                filtered[arr_name] = []
        out.append(json.dumps(filtered, ensure_ascii=False))
    return out if not isinstance(mock_script, str) else out[0]


def _merge_observer_legs(leg_a: dict[str, Any], leg_b: dict[str, Any]) -> dict[str, Any]:
    """合并两条腿的 7 数组输出。

    规则：
    1. 7 数组齐全；任一腿缺某数组 → 视为空列表。
    2. 同数组内：leg_a 优先；leg_b 中与 leg_a change_id 冲突的条目丢弃。
    3. change_id 缺失或非字符串 → 按数组内出现顺序追加（保留双方全部）。
    """
    merged: dict[str, Any] = {}
    for arr_name in _OBSERVER_ALL_ARRAYS:
        a_list = leg_a.get(arr_name) if isinstance(leg_a, dict) else None
        b_list = leg_b.get(arr_name) if isinstance(leg_b, dict) else None
        if not isinstance(a_list, list):
            a_list = []
        if not isinstance(b_list, list):
            b_list = []
        if arr_name in _OBSERVER_LEG_A_SET:
            # leg_a 负责：直接取 a_list，b_list 丢弃（按 scope 不会出现冲突）
            merged[arr_name] = list(a_list)
        else:
            # leg_b 负责：a_list 应为空；防御性兜底时仍按 leg_b 优先
            merged[arr_name] = list(b_list) if not a_list else (list(b_list) + list(a_list))
    return merged


def _classify_validator_errors_to_legs(errors: list[str]) -> tuple[bool, bool]:
    """把 validator errors 解析为「需要重跑的腿」。

    返回 ``(need_leg_a, need_leg_b)``：
    - errors 涉及 ``character_changes`` / ``relationship_changes`` / ``world_changes`` → need_leg_a = True
    - errors 涉及 ``new_events`` / ``new_hooks`` / ``resolved_hooks`` / ``debt_changes`` → need_leg_b = True
    - 无法归类（errors 不含数组名）→ 双腿都重试（兜底）
    """
    import re as _re

    array_pat = _re.compile(r"\b(" + "|".join(_OBSERVER_ALL_ARRAYS) + r")\b")
    a_hit = False
    b_hit = False
    any_array_hit = False
    for err in errors or []:
        if not isinstance(err, str):
            continue
        m = array_pat.search(err)
        if not m:
            continue
        any_array_hit = True
        name = m.group(1)
        if name in _OBSERVER_LEG_A_SET:
            a_hit = True
        elif name in _OBSERVER_LEG_B_SET:
            b_hit = True
    if not any_array_hit:
        # 兜底：errors 不可归类 → 双腿都重试
        return True, True
    return a_hit, b_hit

# -----------------------------------------------------------------------
# Sprint V1.4：enforce 改稿引导（revision_guidance）
# -----------------------------------------------------------------------

# 维度阈值（与 docs/evaluation/quality-scoring-v0.md §1.1 八字段对齐）。
# 任一子分 < _REVISION_SCORE_THRESHOLD ⇒ 进入 revision_guidance。
_REVISION_SCORE_THRESHOLD = 60
# 阻断 rule_id / category → 可执行改稿建议（纯规则映射；不调 LLM，按 task spec）。
# 缺失时退化为通用「按 issue.message / suggestion 改稿」兜底。
_RULE_REVISION_HINTS: dict[str, str] = {
    # 顶层规范（REQ-Q6/Q7/Q8）
    "REQ-Q6": "降低与参照书重合的句段；优先替换高频套用词为角色专属表达；保留设定但改叙事结构。",
    "REQ-Q7": "补齐缺失的章节/伏笔/角色状态；对照 plan_json.key_beats 检查是否覆盖每个 beat。",
    "REQ-Q8": "调整 AI / 人类作者字符占比；避免全章由 AI 单调产出，关键转折需 human 改稿痕迹。",
    # Payoff（H-*）
    "H-1": "本章 payoff 不足——为已 resolved_hooks / paid debts 留出可感知的展示窗口（不是仅后台登记）。",
    "H-2": "本章 hook 兑现节奏失衡——把过密 hook 拆到后续章节，或在前文补充兑现铺垫。",
    "H-3": "本章角色变化过于剧烈——拆解为多章过渡；每章 character_changes 控制在 1-2 项。",
    "H-4": "本章世界规则变更未铺垫——先埋 rule change 的前因后果，再正式推进到本章。",
    "H-5": "本章与前章状态衔接断裂——补一段承接句或回忆钩，确保读者认知连续。",
    # Guardrail rule_id 兜底（与 packages/core/quality/guardrails.py 对齐）
    "RULE_CHAR_DEAD_ACTIVE": "不要让已死亡角色在本章发生 action/location/goal 等活跃状态变更；先在故事层处理复活情节，或换其它角色承担该情节。",  # noqa: E501
    "RULE_CHAR_BEFORE_MISMATCH": "character_changes[*].before 必须与 snapshot 当前 canonical state 一致；不要在没有先写状态变更的情况下直接 update。",  # noqa: E501
    "RULE_WORLD_BEFORE_MISMATCH": "world_changes[*].before 必须与 snapshot 当前 world 状态一致；先核对当前 location/faction/rule 再 update。",  # noqa: E501
    "RULE_TIMELINE_REGRESSION": "本章 effective_at 不能早于上一个已 commit state 的 effective_at；调整时序或在更早章节埋点。",  # noqa: E501
    "RULE_KNOWLEDGE_LEAK": "actor.knowledge 不能引用 visibility<HIDDEN 的知识；改用 actor 自身可见的线索。",  # noqa: E501
    "RULE_SCHEMA_VALIDATION_FAILED": "Observer delta payload 不通过业务校验；按 payload._retry_hint / 阻断信息修正字段后再提交。",  # noqa: E501
    "scoring_missing_subscore": "至少 1 个子分未算出（plan / snapshot / delta 不完整）；补全 chapters.plan_json 或 StoryStateService.submit_delta 后重跑。",  # noqa: E501
}
# guardrail category → 可执行改稿建议（覆盖 RULE_* 没在 _RULE_REVISION_HINTS 命中的情形）
_CATEGORY_REVISION_HINTS: dict[str, str] = {
    "character_contradiction": "character_changes 与 canonical character state 冲突；先核对角色当前 status/location/goal 再下发 update。",  # noqa: E501
    "world_rule_contradiction": "world_changes 与 world canonical state 冲突；先核对当前 world rule / faction / location 再下发 update。",  # noqa: E501
    "timeline_consistency": "本章 effective_at 或事件顺序与已 commit state 矛盾；调整时序或在更早章节先埋。",
    "knowledge_leakage": "actor 引用的 knowledge 超出其 visibility；改用 actor 自身可见的线索，或先提升 actor.visibility。",  # noqa: E501
    "schema_validity": "Observer delta payload 字段不合法；按错误信息逐条修复后重跑。",
    "payoff": "本章 payoff 计数偏低（<H-1 阈值）；对照 plan.debt_handling / hook_handling 检查是否漏兑现。",
    "consistency": "本章与前章状态衔接断裂；补一段承接句或回忆钩，确保读者认知连续。",
    "continuity": "本章与前章状态衔接断裂；补一段承接句或回忆钩，确保读者认知连续。",
    "completeness": "plan.key_beats / character_changes_planned 未覆盖；对照 plan 与 delta 补齐。",
}


def _build_revision_guidance(report: _QualityReport, issues: list[Issue]) -> list[dict[str, Any]]:
    """从 quality_report 生成结构化 revision_guidance。

    返回 ``[{"dimension", "score", "threshold", "top_issues", "rule_hint"}]``。

    生成规则（V1.4）：
    1. 任一子分 < ``_REVISION_SCORE_THRESHOLD`` ⇒ 进入列表（按低分子分维度）。
    2. 任一 ``severity == 'error'`` issue 的 rule_id 在 ``_RULE_REVISION_HINTS`` 内
       ⇒ 进入列表（标记 dimension='guardrail'），保证 enforce 阻断时一定有可执行建议。
    3. 1+2 同 key 时合并 top_issues，按 dimension 去重。

    设计动机：subscore 不一定随单个 error 落到阈值以下（rule-based scoring 与
    guardrail 是两条独立通道），enforce 阻断时如果只看低分子分会得到空列表——但
    作者实际需要的是「按 blocking rule 怎么改稿」。所以同时携带 guardrail 维度。
    """
    subscores: dict[str, int] = {
        "plot": int(report.plot),
        "character": int(report.character),
        "continuity": int(report.continuity),
        "style": int(report.style),
        "pacing": int(report.pacing),
        "foreshadowing": int(report.foreshadowing),
        "ai_trace": int(report.ai_trace),
    }

    # 按 category 反推所属子分维度（用于「低分子分 → 关联 error」粗匹配）。
    issues_by_dim: dict[str, list[dict[str, Any]]] = {k: [] for k in subscores}
    error_issues: list[Issue] = [i for i in issues if i.severity == "error"]

    for iss in error_issues:
        entry = {
            "rule_id": iss.rule_id,
            "category": iss.category,
            "message": iss.message,
            "suggestion": iss.suggestion,
            "location": iss.location,
        }
        # 已知规则的 guardrail 维度先归入 "guardrails" 桶（与下面 step 2 合并）
        if iss.rule_id in _RULE_REVISION_HINTS:
            issues_by_dim.setdefault("guardrails", []).append(entry)
        # 按 category 做粗匹配，落到具体子分
        cat = (iss.category or "").lower()
        for dim in ("plot", "character", "continuity", "style", "pacing", "foreshadowing"):
            if dim in cat:
                issues_by_dim.setdefault(dim, []).append(entry)
                break
        # continuity 子分涵盖 guardrail 大类
        if iss.category in {
            "character_contradiction", "world_rule_contradiction",
            "timeline_consistency", "knowledge_leakage", "schema_validity",
        }:
            issues_by_dim.setdefault("continuity", []).append(entry)
        # plot 子分覆盖 payoff 类
        if iss.category == "payoff":
            issues_by_dim.setdefault("plot", []).append(entry)

    guidance: list[dict[str, Any]] = []
    seen_dims: set[str] = set()

    # Step 1: 低分子分优先进入
    for dim, score in subscores.items():
        if score >= _REVISION_SCORE_THRESHOLD:
            continue
        if dim in seen_dims:
            continue
        seen_dims.add(dim)
        dim_issues = issues_by_dim.get(dim, [])
        # 兜底默认建议（每条要有可执行建议动作）
        hint = _RULE_REVISION_HINTS.get(
            dim,
            f"{dim} 子分={score}，低于阈值{_REVISION_SCORE_THRESHOLD}；"
            "请按 issues 清单逐条修复后重跑 quality_gate。",
        )
        guidance.append({
            "dimension": dim,
            "score": int(score),
            "threshold": int(_REVISION_SCORE_THRESHOLD),
            "top_issues": dim_issues[:5],
            "rule_hint": hint,
        })

    # Step 2: blocking rule 维度（即便 subscore 没低于阈值也要进；保证 enforce 阻断时
    # 必有可执行建议）。优先按 rule_id 命中；rule_id 未命中时按 category 兜底。
    # 同 (rule_id, category) 只生成一条；同一 rule_id 重复出现在 step 1 中已被合并。
    if error_issues:
        seen_keys: set[tuple[str, str]] = set()
        guardrail_entries: list[dict[str, Any]] = []
        for iss in error_issues:
            key = (iss.rule_id, iss.category or "")
            if key in seen_keys:
                continue
            hint = _RULE_REVISION_HINTS.get(iss.rule_id)
            if hint is None:
                hint = _CATEGORY_REVISION_HINTS.get(iss.category or "")
            if hint is None:
                # 既不在 rule 也不在 category 表，跳过；下一步兜底统一加 generic
                continue
            seen_keys.add(key)
            guardrail_entries.append({
                "rule_id": iss.rule_id,
                "category": iss.category,
                "message": iss.message,
                "suggestion": iss.suggestion,
                "location": iss.location,
                "rule_hint": hint,
            })
        # 兜底：若所有 blocking issue 都没命中任何 hint（极少见），仍输出一条
        # 通用 guardrail 条目，让作者至少知道有阻断；top_issues 列出所有阻断 issue。
        if not guardrail_entries and error_issues:
            first = error_issues[0]
            guardrail_entries.append({
                "rule_id": first.rule_id,
                "category": first.category,
                "message": first.message,
                "suggestion": first.suggestion,
                "location": first.location,
                "rule_hint": "本章 quality_gate 触发 error 阻断；按 issue.message / suggestion 修复后重跑。",
            })
        if guardrail_entries and "guardrails" not in seen_dims:
            # 用第 1 条的 hint 作 guardrails 主 hint（list 形式供前端展开）
            primary_hint = guardrail_entries[0]["rule_hint"]
            guidance.append({
                "dimension": "guardrails",
                "score": 0,
                "threshold": int(_REVISION_SCORE_THRESHOLD),
                "top_issues": guardrail_entries[:5],
                "rule_hint": primary_hint,
            })
            seen_dims.add("guardrails")

    return guidance


# quality_gate 节点抛 ValueError 阻断时一并把 revision_guidance 注入到 WorkflowEngine
# 写进 runs.error 的字符串里（后端 format）；让前端 QualityPanel / 错误提示能直接拿到
# 结构化改稿引导。阻断时无法走节点返回 dict，故用 error_info + checkpoint 两条路径：
# - runs.error = "quality gate blocked: <rule_ids> | guidance=<json>"
# - ctx['quality_gate']={'blocked': True, 'revision_guidance': [...]}（已被 _update_run_checkpoint
#   落盘到 checkpoint_json）


def _build_observer_ctx_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """装配 observer 输入。

    改用 ``snapshot_mode="trimmed"``（M1/M2 实证：全量快照 >110KB 导致 LLM 超时；
    trimmed 仅保留最近 ``keep_recent_commits`` 个 commit 中 touch 过的实体全量字段 +
    open/active/escalated/acknowledged 状态 hook/debt 全量 + 其余仅摘要），让
    observer 在大快照场景下也能稳定完成。trim 口径与 stats 写入由
    :func:`build_observer_input` 负责；本节点仅做模式选择。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    payload = build_observer_input(db_path, chapter_id, snapshot_mode="trimmed")
    return {"observer_input": payload}


def _observer_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Observer 节点（V3.1.1 O-2：双 leg 拆分）。

    V3.1.1 O-2 之前：本节点单次调 ``run_agent("observer", payload)`` 输出 7 数组。
    V3.1.1 O-2 之后：
    - 默认（``NOVELOS_OBSERVER_SPLIT=on``）：分两条轻量 leg 调 observer：
      - leg_a (entities)：character_changes + relationship_changes + world_changes
      - leg_b (narrative)：new_events + new_hooks + resolved_hooks + debt_changes
      两腿各自走 ``light`` capability（V3 P0-2；缺失自动回退 reasoning）；payload
      注入 ``extraction_scope`` 字段让 observer 只输出对应 scope 的数组（见
      ``docs/agents/prompts/observer-v1.md`` §11）。两腿响应合并后走既有
      ``_inject_validate_node`` 校验链不变。
    - off（``NOVELOS_OBSERVER_SPLIT=off``）：走旧单次大调用路径——保留原代码
      路径分支，保证可一键回退。

    输出：
    - ``observer_payload``：合并后的 7 数组（dict）。
    - ``observer_split_meta``：可观测性元数据——
      ``{"enabled": bool, "leg_a": {...}, "leg_b": {...}, "merged_at": iso}``；
      每条 leg 含 ``{"tokens": int, "latency_ms": int, "retry_count": int}``，
      数值从 ai_call_logs 聚合（chapter-commit run + node_run_id + agent='observer'）。
    """
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    node_run_id = ctx.get("_current_node_run_id")
    base_payload = ctx["observer_input"]
    mock_script = (ctx.get("mock_providers") or {}).get("observer")

    # env 开关 + ctx 显式覆盖
    if ctx.get("observer_split") is False or not _observer_split_enabled():
        # 旧单次路径（V3.1.1 O-2 之前；保证回退兼容）
        out = run_agent(
            db_path,
            "observer",
            base_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=mock_script,
        )
        return {
            "observer_payload": out,
            "observer_split_meta": {
                "enabled": False,
                "leg_a": None,
                "leg_b": None,
                "merged_at": now_iso(),
            },
        }

    # 新拆分路径：两腿分别调 observer agent，按 scope 注入 payload 指令
    # V3.1.1 O-3：先按 leg 裁剪 previous_state，让两腿各自只看到本 leg 需要的集合；
    # 同时 base_payload["config"]["recent_event_ids"] 已由 build_observer_input 自动注入。
    leg_a_payload, leg_a_trim_stats = _trim_observer_input_for_leg(base_payload, "entities")
    leg_a_payload["extraction_scope"] = "entities"
    leg_b_payload, leg_b_trim_stats = _trim_observer_input_for_leg(base_payload, "narrative")
    leg_b_payload["extraction_scope"] = "narrative"

    # Mock 兼容：golden regression 的 mock_script 是完整 7 数组；按 leg 过滤，
    # 让两腿各自看到「只含本 leg 范围」mock——merge 后等价于单次大调用。
    leg_a_mock = _filter_mock_for_leg(mock_script, "entities")
    leg_b_mock = _filter_mock_for_leg(mock_script, "narrative")

    # 量化 per-leg 字符数（便于 O-3 收益对比）；量的是 trim 后的 JSON 序列化字节数。
    try:
        leg_a_chars = len(json.dumps(leg_a_payload, ensure_ascii=False))
    except (TypeError, ValueError):
        leg_a_chars = 0
    try:
        leg_b_chars = len(json.dumps(leg_b_payload, ensure_ascii=False))
    except (TypeError, ValueError):
        leg_b_chars = 0

    parallel_enabled = _observer_parallel_enabled(ctx)
    summary_parallel = _summary_parallel_enabled(ctx)
    # wall_time：仅并发路径记录（便于测试断言「真并发」）；off 回退串行时不统计。
    parallel_wall_ms: int | None = None
    summary_early: dict[str, Any] | None = None
    if parallel_enabled and summary_parallel:
        # V3.7 P0：observer 双腿 + summarizer 三路同池并发（ThreadPoolExecutor,
        # max_workers=3）。summary 与双腿互不依赖（不读 observer_payload），可同跑。
        # summary_early 走 observer 返回 dict 顶层 key，引擎在 _run_nodes 里
        # ``ctx.update(output)`` 自动合入下游 ctx，summarize 节点直接 ``ctx.get('summary_early')``
        # 短路消费（参考 packages/core/workflow_runtime/engine.py 行 336-337）。
        leg_a_out, leg_b_out, parallel_wall_ms, summary_early = (
            _run_observer_with_summary_in_parallel(
                db_path=db_path,
                run_id=run_id,
                node_run_id=node_run_id,
                leg_a_payload=leg_a_payload,
                leg_b_payload=leg_b_payload,
                leg_a_mock=leg_a_mock,
                leg_b_mock=leg_b_mock,
                ctx=ctx,
            )
        )
    elif parallel_enabled:
        # V3.5：只开 observer_parallel，未开 summary_parallel → 保持现双腿并发形态。
        leg_a_out, leg_b_out, parallel_wall_ms = _run_observer_legs_in_parallel(
            db_path=db_path,
            run_id=run_id,
            node_run_id=node_run_id,
            leg_a_payload=leg_a_payload,
            leg_b_payload=leg_b_payload,
            leg_a_mock=leg_a_mock,
            leg_b_mock=leg_b_mock,
        )
    else:
        # off / 兼容回退：原串行提交，保持既有行为
        leg_a_out = run_agent(
            db_path,
            "observer",
            leg_a_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_a_mock,
            capability_override="light",
        )
        leg_b_out = run_agent(
            db_path,
            "observer",
            leg_b_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_b_mock,
            capability_override="light",
        )

    merged = _merge_observer_legs(leg_a_out, leg_b_out)
    meta = _aggregate_observer_split_meta(
        db_path,
        run_id=run_id,
        node_run_id=node_run_id,
        # 两腿各 1 次成功调用（首次即通过；如失败将由 _inject_validate_node 重试，
        # 该节点会消费同一 ai_call_logs 行做聚合，本节点只关心成功首调）。
        expected_calls=2,
        merged_at=now_iso(),
    )
    # V3.1.1 O-3：把 per-leg payload 字符数 + 按腿 trim stats 挂到 observer_split_meta，
    # 便于量化 O-3 收益（与 O-2 时的 ~120k tokens / 全量 payload 对比）。
    if isinstance(meta, dict):
        meta["leg_a_chars"] = leg_a_chars
        meta["leg_b_chars"] = leg_b_chars
        meta["leg_a_total_chars"] = leg_a_chars + leg_b_chars
        meta["leg_a_trim_stats"] = leg_a_trim_stats
        meta["leg_b_trim_stats"] = leg_b_trim_stats
        # V3.5：并发模式标记 + wall_time（仅并发路径有值；off 路径为 None）。
        meta["parallel"] = bool(parallel_enabled)
        # V3.7：summarizer 并入并发池标记（与 observer_parallel 双开关独立）。
        meta["summary_parallel"] = bool(summary_parallel)
        if parallel_wall_ms is not None:
            meta["parallel_wall_ms"] = int(parallel_wall_ms)
            # V3.7：summary 早产成功时复用同 wall，便于观测同池收益；早产失败/未触发时省略字段。
            if summary_early is not None and not summary_early.get("skipped"):
                meta["summary_parallel_wall_ms"] = int(parallel_wall_ms)
            # SQLite rowid 在并发 commit 下不保证 leg_a 先 leg_b 后——记录此点
            # 让观测者明确 leg_a / leg_b 字段可能交换（不影响业务正确性）。
            meta["ordering_note"] = (
                "concurrent_legs_rowid_order_unstable"
                if parallel_enabled
                else "serial"
            )
        # recent_event_ids 注入条数（base_payload.config 已在 build_observer_input 完成）
        base_config = base_payload.get("config") or {}
        if isinstance(base_config, dict):
            reid = base_config.get("recent_event_ids")
            if isinstance(reid, list):
                meta["recent_event_ids_count"] = len(reid)
    result: dict[str, Any] = {
        "observer_payload": merged,
        "observer_split_meta": meta,
    }
    # V3.7：summary 早产结果以顶层 key 暴露；引擎 ``ctx.update(output)`` 自动合入下游 ctx。
    # 仅当 third-leg 跑过（即使是 skipped）才返回该 key，让下游明确语义。
    if summary_early is not None:
        result["summary_early"] = summary_early
    return result


def _run_observer_legs_in_parallel(
    *,
    db_path: Any,
    run_id: str,
    node_run_id: str | None,
    leg_a_payload: dict[str, Any],
    leg_b_payload: dict[str, Any],
    leg_a_mock: Any,
    leg_b_mock: Any,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """V3.5：双腿并发提交到 run_agent（ThreadPoolExecutor，max_workers=2）。

    并发语义：
    - 两条腿同时入队；任一腿抛异常会让该 future 携带异常被 ``future.result()`` 重抛——
      ``_observer_node`` 调用方未捕获，将直接冒泡（与原串行路径异常语义一致）。
    - 两腿各自 ``run_agent`` 内部独立 get_connection / insert ai_call_logs；
      SQLite WAL + busy_timeout 5s 保证并发 INSERT 不锁死（先到先 commit，rowid 顺序
      不可预测但业务正确性不依赖 leg_a/leg_b 提交顺序，merge 按 key 区分）。
    - 收集后返回 ``(leg_a_out, leg_b_out, wall_ms)``。wall_ms 用 ``time.monotonic()``
      度量，仅供测试 / 观测断言「真并发」。

    设计动机（V3.5 提速调研结论）：见 ``docs/roadmap/v3-plan.md`` 已知问题——双腿
    并发让 wall_time ≈ max(t_leg_a, t_leg_b)，单次路径 ≈ sum(t_leg_a, t_leg_b)。
    实测 m1_run（service_tier=priority 启用）：单腿 60-180s ⇒ 并发收益 30-50%。
    """
    import time as _time

    def _run_leg_a() -> dict[str, Any]:
        return run_agent(
            db_path,
            "observer",
            leg_a_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_a_mock,
            capability_override="light",
        )

    def _run_leg_b() -> dict[str, Any]:
        return run_agent(
            db_path,
            "observer",
            leg_b_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_b_mock,
            capability_override="light",
        )

    # max_workers=2：恰好容纳两条腿；不再扩张，避免 provider 侧被并发请求压垮。
    # ThreadPoolExecutor 默认 shutdown 语义是 with-block 退出时 wait 全部完成——
    # 即使 future.result() 抛异常，两腿都会被 join 后才退出。
    start = _time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="observer-leg",
    ) as pool:
        future_a = pool.submit(_run_leg_a)
        future_b = pool.submit(_run_leg_b)
        # as_completed：任一腿完成就返回，但需为每条腿 .result() 检查异常——这里直接
        # 按「submit 顺序」取结果：leg_a / leg_b 的归属由调用方按变量绑定恢复，不依赖
        # 完成时间。
        leg_a_out = future_a.result()
        leg_b_out = future_b.result()
    wall_ms = int((_time.monotonic() - start) * 1000)
    return leg_a_out, leg_b_out, wall_ms


def _run_observer_with_summary_in_parallel(
    *,
    db_path: Any,
    run_id: str,
    node_run_id: str | None,
    leg_a_payload: dict[str, Any],
    leg_b_payload: dict[str, Any],
    leg_a_mock: Any,
    leg_b_mock: Any,
    ctx: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], int, dict[str, Any] | None]:
    """V3.7 P0：observer 双腿 + summarizer 三路同池并发（ThreadPoolExecutor, max_workers=3）。

    与 :func:`_run_observer_legs_in_parallel` 的关系：
    - 本函数保留原双腿函数不动；调用方按需选择（summary_parallel 开关）。
    - 第三 future ``_run_summary`` 内部 try/except 捕获一切异常，绝不让 summary
      失败炸掉 observer 节点；失败时返回 ``{"skipped": True}`` 让下游走兜底。

    返回 ``(leg_a_out, leg_b_out, wall_ms, summary_early)``：
    - summary_early 形态：
      - ``{"skipped": True}`` ——prepare 阶段判定为可跳过（章节/draft 缺失）。
      - ``{"skipped": False, "output": out, "project_id": ..., "chapter_no": ..., "tail_text": ...}``
        —— LLM 调成功，供下游 summarize 节点短路消费。
      - ``None`` ——summary 异常被吞掉，让下游 summarize 节点自愈重跑。
    """
    import logging as _logging
    import time as _time

    def _run_leg_a() -> dict[str, Any]:
        return run_agent(
            db_path,
            "observer",
            leg_a_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_a_mock,
            capability_override="light",
        )

    def _run_leg_b() -> dict[str, Any]:
        return run_agent(
            db_path,
            "observer",
            leg_b_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_b_mock,
            capability_override="light",
        )

    def _recover_run_status() -> None:
        """早产失败→恢复 run 状态防污染。

        runner 内部（packages/core/agent_runtime/runner.py:303-323 / 388）
        会在异常路径里 ``_update_workflow_run(..., status='FAILED')`` 标记 run
        行。本函数把这一步强制改回 ``COMPLETED``（error=None），避免 observer
        三路并发吞掉 summary 异常后，workflow_runs 行被错误地标 FAILED。
        最终终态（PAUSED/COMPLETED/FAILED）由 ``engine._run_nodes`` 在所有节点
        完成后经 ``_finalize_run`` 覆盖——这里只是中间兜底。
        """
        try:
            _update_workflow_run(db_path, run_id, status="COMPLETED", error=None)
        except Exception as exc:  # noqa: BLE001 —— 恢复本身失败仅记日志
            _logging.getLogger(__name__).warning(
                "chapter_commit.observer summary early failure: workflow_run "
                "status recovery failed: run_id=%s err=%s", run_id, exc,
            )

    def _run_summary() -> dict[str, Any] | None:
        """第三路：summarizer LLM 早产。

        任一异常（prepare 缺失 / run_agent 失败 / 其它）→ log warning +
        ``_recover_run_status()`` 防 runner 内部 FAILED 污染 +
        返回 None，让下游 summarize 节点按原 prepare+run_agent 路径自愈完成。
        """
        try:
            prepared = _prepare_summarizer_call(ctx)
        except Exception as exc:  # noqa: BLE001 —— prepare 阶段容错
            _logging.getLogger(__name__).warning(
                "chapter_commit.observer early summarize prepare failed: "
                "chapter_id=%s err=%s", ctx.get("chapter_id"), exc,
            )
            # 注意：prepare 阶段不调 run_agent，不会有 runner 兜底的 FAILED 状态。
            # 仍调一次恢复函数做幂等的 noop，保证两条路径走向完全对称。
            _recover_run_status()
            return None
        if prepared is None:
            return {"skipped": True}
        try:
            out = run_agent(
                db_path,
                "summarizer",
                prepared["payload"],
                run_id,
                node_run_id=node_run_id,
                expected="summarizer",
                mock_script=prepared["mock"],
            )
        except Exception as exc:  # noqa: BLE001 —— LLM 失败兜底，不炸 observer
            _logging.getLogger(__name__).warning(
                "chapter_commit.observer early summarize run_agent failed: "
                "chapter_id=%s err=%s", ctx.get("chapter_id"), exc,
            )
            # runner 内部异常路径已 _update_workflow_run(FAILED)（runner.py:321/364/396）。
            # 在本吞异常分支里强制恢复为 COMPLETED，避免 run 状态被污染。
            _recover_run_status()
            return None
        return {
            "skipped": False,
            "output": out,
            "project_id": prepared["project_id"],
            "chapter_no": prepared["chapter_no"],
            "tail_text": prepared["tail_text"],
        }

    # max_workers=3：恰好容纳两腿 + summary；不再扩张，避免 provider 侧并发请求过多。
    start = _time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=3, thread_name_prefix="observer-leg+sum",
    ) as pool:
        future_a = pool.submit(_run_leg_a)
        future_b = pool.submit(_run_leg_b)
        future_s = pool.submit(_run_summary)
        leg_a_out = future_a.result()
        leg_b_out = future_b.result()
        # summary future 的异常已由 _run_summary 内部吞掉；此处再兜一层确保异常
        # 不会以 future.result() 形式冒泡炸掉 observer 节点。
        try:
            summary_early: dict[str, Any] | None = future_s.result()
        except Exception as exc:  # noqa: BLE001
            _logging.getLogger(__name__).warning(
                "chapter_commit.observer summary future unexpected exception: "
                "chapter_id=%s err=%s", ctx.get("chapter_id"), exc,
            )
            # future.result() 自身冒泡的异常：通常是 _run_summary 之外的代码 bug。
            # 同样恢复 run 状态防污染（幂等）。
            _recover_run_status()
            summary_early = None
    wall_ms = int((_time.monotonic() - start) * 1000)
    return leg_a_out, leg_b_out, wall_ms, summary_early


def _aggregate_observer_split_meta(
    db_path: Any,
    *,
    run_id: str,
    node_run_id: str | None,
    expected_calls: int,
    merged_at: str,
) -> dict[str, Any]:
    """聚合 observer 双 leg 的 tokens / latency_ms / retry_count。

    路径：``ai_call_logs`` WHERE ``run_id=? AND node_run_id=? AND agent='observer'``
    取最近 ``expected_calls`` 条（按 created_at DESC 倒序后回正为 leg_a 先 leg_b 后）；
    单次大调用时（off 路径）不会调用本函数，故此处的「按 created_at 排序 +
    假定 leg_a 先 leg_b 后」足以区分两腿。极端情况下两腿几乎同时落库（毫秒级
    差异），仍可按 ``rowid`` 倒序稳定回放顺序。
    """
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001 —— 观测失败不阻断 observer 节点
        return {
            "enabled": True,
            "leg_a": None,
            "leg_b": None,
            "merged_at": merged_at,
        }
    try:
        # 取本节点 observer 全部调用（按 rowid ASC；同一 run_id+node_run_id 下
        # 两腿调用按代码顺序落库，rowid 顺序 = 调用顺序）
        rows = conn.execute(
            """
            SELECT a.call_id, a.token_usage_json, a.latency_ms, a.retry_count, a.created_at
            FROM ai_call_logs a
            JOIN agents ag ON ag.agent_id = a.agent_id
            WHERE a.run_id = ? AND ag.name = 'observer'
              AND (? IS NULL OR a.node_run_id = ?)
            ORDER BY a.rowid ASC
            """,
            (run_id, node_run_id, node_run_id),
        ).fetchall()
    finally:
        conn.close()

    legs: list[dict[str, Any]] = []
    for r in rows[-2:] if len(rows) >= 2 else rows:
        try:
            usage = json.loads(r["token_usage_json"]) if r["token_usage_json"] else {}
        except (TypeError, ValueError):
            usage = {}
        legs.append({
            "tokens": int(usage.get("total") or 0),
            "latency_ms": int(r["latency_ms"] or 0),
            "retry_count": int(r["retry_count"] or 0),
            "call_id": r["call_id"],
        })

    leg_a = legs[0] if len(legs) >= 1 else None
    leg_b = legs[1] if len(legs) >= 2 else None
    return {
        "enabled": True,
        "leg_a": leg_a,
        "leg_b": leg_b,
        "merged_at": merged_at,
    }


def _has_high_risk_change(observer_payload: dict[str, Any]) -> bool:
    """扫描 7 数组，risk_level=HIGH / character facet=definition / world_kind=rule 任一命中即 True。"""
    for ch in observer_payload.get("character_changes") or []:
        if isinstance(ch, dict) and (
            ch.get("risk_level") == "HIGH" or ch.get("facet") == "definition"
        ):
            return True
    for w in observer_payload.get("world_changes") or []:
        if isinstance(w, dict) and (
            w.get("risk_level") == "HIGH" or w.get("world_kind") == "rule"
        ):
            return True
    for key in ("relationship_changes", "new_events", "resolved_hooks", "new_hooks", "debt_changes"):
        for item in observer_payload.get(key) or []:
            if isinstance(item, dict) and item.get("risk_level") == "HIGH":
                return True
    return False


def _build_delta(
    observer_payload: dict[str, Any],
    *,
    chapter_id: str,
    run_id: str,
    previous_state_version: int,
) -> dict[str, Any]:
    """由 observer 业务载荷注入 10 元信息字段构造完整 delta（不含 created_at 之外的 DB 行为）。"""
    return {
        "delta_id": new_id("dlt"),
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": run_id,
        "previous_state_version": previous_state_version,
        "created_by": "observer:v1",
        "created_at": now_iso(),
        "supersedes": None,
        "notes": None,
        # 7 数组
        "character_changes": observer_payload.get("character_changes", []),
        "world_changes": observer_payload.get("world_changes", []),
        "relationship_changes": observer_payload.get("relationship_changes", []),
        "new_events": observer_payload.get("new_events", []),
        "resolved_hooks": observer_payload.get("resolved_hooks", []),
        "new_hooks": observer_payload.get("new_hooks", []),
        "debt_changes": observer_payload.get("debt_changes", []),
    }


def _inject_validate_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """注入元信息 → validate_delta（纯函数，无落库副作用）→ 失败按 leg 重试。

    重试范围（V3.1.1 O-2 拆分后）：
    - 校验失败后解析 errors 中出现的数组名，把仅与某 leg 关联的 errors 路由到对应腿：
      - character_changes / relationship_changes / world_changes → leg_a 重试
      - new_events / new_hooks / resolved_hooks / debt_changes → leg_b 重试
    - errors 不可归类（不含数组名）→ 双腿都重试（兜底，保持向后兼容）。
    - 重试 payload（V3.1.1 O-3）：``_trim_observer_input_for_leg(base, leg)`` 后的
      leg 专用 payload + ``_retry_hint`` + ``extraction_scope``——按腿裁剪 previous_state
      与首次调用口径一致。
    - mock_script：首次按 leg 过滤；重试时取 ``mock_script[idx+1]`` 对应 leg 的元素。
    - 重试预算：每个 leg 最多 1 次（与单次路径的 1 次重试预算对齐——双腿都重试场景下
      总调用次数上限 = 2 首次 + 2 重试 = 4 次 ai_call_logs 行）。
    - 重试后再次 merge → validate；仍失败 ⇒ ``raise ValueError(...)``。
    - submit_delta 仅在最终通过的 delta 上调一次（service.py:662-680 失败会落 rejected 行，
      重试循环内禁止反复调）。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    run_id = ctx["run_id"]
    node_run_id = ctx.get("_current_node_run_id")
    split_meta = ctx.get("observer_split_meta") or {}
    split_enabled = bool(split_meta.get("enabled"))

    # 取当前 state_version 作 previous_state_version（不依赖 observer_payload，原口径）
    svc = StoryStateService(db_path)
    project_id = svc._project_id_for_chapter(  # noqa: SLF001
        get_connection(db_path), chapter_id
    )
    if project_id is None:
        raise ValueError(f"chapter {chapter_id!r} not found")
    current_state = svc.get_current_state(project_id)
    previous_state_version = int(current_state.get("state_version") or 1)

    # 快照：observer_input.previous_state（trimmed 模式）+ 引用存在性校验
    snapshot_for_validate = (ctx.get("observer_input") or {}).get("previous_state")

    # 当前累积的 observer_payload（首次 = _observer_node 输出；后续 = merge 结果）
    observer_payload = ctx.get("observer_payload") or {}
    # leg 维度的输出缓存（按 leg 存最近一次响应，便于只重跑出错 leg）
    leg_outputs: dict[str, dict[str, Any]] = {}
    if split_enabled:
        leg_outputs["entities"] = _extract_leg_payload(observer_payload, "entities")
        leg_outputs["narrative"] = _extract_leg_payload(observer_payload, "narrative")
    else:
        # off 路径：把 observer_payload 视为「完整 7 数组」（与单次大调用一致）；
        # 重试时整次重跑。
        leg_outputs["all"] = dict(observer_payload) if isinstance(observer_payload, dict) else {}

    # 首次校验（先确定性自动修复，再 validate；修不了的留给重试）
    delta = _build_delta(
        observer_payload,
        chapter_id=chapter_id,
        run_id=run_id,
        previous_state_version=previous_state_version,
    )
    delta, repairs = repair_delta(delta, snapshot=snapshot_for_validate, db_path=db_path)
    if repairs:
        ctx["delta_repairs"] = repairs
        logging.info("observer delta repaired before first validation: %s", repairs)
    errors = validate_delta(delta, snapshot=snapshot_for_validate)

    # 重试预算：每腿 1 次
    if errors:
        # mock_script 原始形态（list / str / callable / None）
        original_mock_script = (ctx.get("mock_providers") or {}).get("observer")
        if split_enabled:
            need_a, need_b = _classify_validator_errors_to_legs(errors)
            legs_to_retry: list[str] = []
            if need_a:
                legs_to_retry.append("entities")
            if need_b:
                legs_to_retry.append("narrative")
            for leg in legs_to_retry:
                # V3.1.1 O-3：retry 也按 leg 裁剪 previous_state；与首次调用口径一致。
                base_retry_payload = dict(ctx.get("observer_input") or {})
                retry_payload, _ = _trim_observer_input_for_leg(
                    base_retry_payload, leg,
                )
                retry_payload["_retry_hint"] = _OBSERVER_RETRY_HINT_TEMPLATE.format(
                    errors="; ".join(errors)
                )
                retry_payload["extraction_scope"] = (
                    "entities" if leg == "entities" else "narrative"
                )
                # mock_script：取下一条响应（list 模式弹 idx+1），单条/字符串保持原样
                retry_mock = _pick_retry_mock(original_mock_script, leg)
                retry_out = run_agent(
                    db_path,
                    "observer",
                    retry_payload,
                    run_id,
                    node_run_id=node_run_id,
                    expected="observer",
                    mock_script=retry_mock,
                    capability_override="light",
                )
                leg_outputs[leg] = retry_out if isinstance(retry_out, dict) else {}
            # 重新合并双腿
            observer_payload = _merge_observer_legs(
                leg_outputs.get("entities", {}),
                leg_outputs.get("narrative", {}),
            )
        else:
            # off 路径：整次重跑（与单次大调用一致）
            retry_payload = dict(ctx.get("observer_input") or {})
            retry_payload["_retry_hint"] = _OBSERVER_RETRY_HINT_TEMPLATE.format(
                errors="; ".join(errors)
            )
            if isinstance(original_mock_script, list) and len(original_mock_script) > 1:
                picked = original_mock_script[1]
                retry_mock_script = [picked] if isinstance(picked, str) else picked
            else:
                retry_mock_script = original_mock_script
            observer_payload = run_agent(
                db_path,
                "observer",
                retry_payload,
                run_id,
                node_run_id=node_run_id,
                expected="observer",
                mock_script=retry_mock_script,
            )
            leg_outputs["all"] = (
                dict(observer_payload) if isinstance(observer_payload, dict) else {}
            )

        # 二次校验（重试后）：同样先 repair 再 validate
        delta = _build_delta(
            observer_payload,
            chapter_id=chapter_id,
            run_id=run_id,
            previous_state_version=previous_state_version,
        )
        delta, repairs = repair_delta(delta, snapshot=snapshot_for_validate, db_path=db_path)
        if repairs:
            ctx["delta_repairs"] = repairs
            logging.info("observer delta repaired after retry: %s", repairs)
        errors = validate_delta(delta, snapshot=snapshot_for_validate)
        if errors:
            raise ValueError(
                f"observer delta rejected by validator: errors={errors}"
            )

    submit_result = svc.submit_delta(delta)
    if submit_result.get("status") != "validated":
        # 防御保留：理论上 validate_delta 通过后 service 也会通过；若仍失败按原口径报错
        raise ValueError(
            f"observer delta rejected by validator: errors={submit_result.get('errors')}"
        )

    # 标记是否需 high_risk 审批（基于最终采用的 observer_payload 计算）
    needs_high_risk_approval = _has_high_risk_change(observer_payload)
    return {
        "delta_id": delta["delta_id"],
        "delta": delta,
        "observer_payload": observer_payload,
        "snapshot_pre": current_state,
        "project_id": project_id,
        "needs_high_risk_approval": needs_high_risk_approval,
        "submit_result": submit_result,
    }


def _extract_leg_payload(observer_payload: dict[str, Any], leg: str) -> dict[str, Any]:
    """从合并后的 observer_payload 中按 leg 抽取对应数组（用于 per-leg 重试缓存）。"""
    keep = _OBSERVER_LEG_A_SET if leg == "entities" else _OBSERVER_LEG_B_SET
    out: dict[str, Any] = {}
    if not isinstance(observer_payload, dict):
        return out
    for arr_name in _OBSERVER_ALL_ARRAYS:
        if arr_name in keep:
            arr = observer_payload.get(arr_name)
            out[arr_name] = list(arr) if isinstance(arr, list) else []
    return out


# ============================================================================
# V3.1.1 O-3：按 leg 裁剪 observer_input.previous_state
# ============================================================================
#
# 入口供 ``_observer_node`` 与 per-leg retry 共用：拿到完整 ``base_payload``（已含
# ``snapshot_mode='trimmed'`` 的 previous_state + snapshot_trim_stats + config +
# chapter + director_plan_summary + knowledge_permissions 等）后，按 leg 范围裁剪
# ``previous_state`` 各集合，并按腿独立重算 ``snapshot_trim_stats``。
#
# 公共部分（不动）：``chapter`` / ``director_plan_summary`` / ``config``
# / ``knowledge_permissions`` / ``previous_state_version`` / ``previous_state`` 顶层
# ``state_version`` / ``recent_events`` / ``world.current_time_in_story`` /
# ``world.active_resources``。
#
# leg_a (entities) previous_state：保留实体集合全量（已是 O-1 trimmed 口径）；
#   移除 ``events`` / ``hooks`` / ``debts`` 集合。
# leg_b (narrative) previous_state：保留 events（trimmed 口径）+ hooks（压缩口径）+
#   debts；实体集合降级为 ``{id, name, status/role}`` 标识性摘要；world_rules 保留
#   ``{world_rule_id, name, statement}`` 摘要（事件可能触发规则变化引用）。
#
# 返回 ``(trimmed_payload, per_leg_trim_stats)``：trimmed_payload 是可直接喂给
# ``run_agent("observer", ...)`` 的完整 payload；stats 写到顶层 ``snapshot_trim_stats``
# 供观测 / 测试断言。
# ============================================================================


def _summarize_character_for_leg_b(char: dict[str, Any]) -> dict[str, Any]:
    """leg_b narrative 用：实体性 character 降级为标识性摘要（仅 id/name/role/status）。"""
    if not isinstance(char, dict):
        return {}
    state = char.get("current_state")
    status = None
    if isinstance(state, dict):
        status = state.get("status")
    return {
        "character_id": char.get("character_id"),
        "name": char.get("name"),
        "role": char.get("role"),
        "status": status,
        "summary_marker": "leg_b_narrative",
    }


def _summarize_location_for_leg_b(loc: Any, lid: str | None = None) -> dict[str, Any]:
    """leg_b narrative 用：location 降级为 ``{location_id, name}``。"""
    if isinstance(loc, dict):
        return {
            "location_id": loc.get("location_id") or lid,
            "name": loc.get("name"),
            "summary_marker": "leg_b_narrative",
        }
    return {"location_id": lid, "name": None, "summary_marker": "leg_b_narrative"}


def _summarize_faction_for_leg_b(fac: Any, fid: str | None = None) -> dict[str, Any]:
    if isinstance(fac, dict):
        return {
            "faction_id": fac.get("faction_id") or fid,
            "name": fac.get("name"),
            "summary_marker": "leg_b_narrative",
        }
    return {"faction_id": fid, "name": None, "summary_marker": "leg_b_narrative"}


def _summarize_world_rule_for_leg_b(rule: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(rule, dict):
        return {}
    return {
        "world_rule_id": rule.get("world_rule_id"),
        "name": rule.get("name"),
        "statement": rule.get("statement"),
        "summary_marker": "leg_b_narrative",
    }


def _trim_observer_input_for_leg(
    base_payload: dict[str, Any], leg: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """V3.1.1 O-3：按 leg 裁剪 observer_input.previous_state。

    输入 ``base_payload`` 是 :func:`build_observer_input` 输出的完整 payload（已含
    ``snapshot_mode='trimmed'`` 的 previous_state）；输出 ``(trimmed_payload, stats)``。

    公共部分（两腿都保留）：``chapter`` / ``director_plan_summary`` / ``config``
    （含 recent_event_ids 白名单）/ ``knowledge_permissions`` / ``previous_state_version`` /
    ``previous_state.snapshot_mode`` / ``previous_state.state_version`` /
    ``previous_state.recent_events`` / ``previous_state.world.current_time_in_story`` /
    ``previous_state.world.active_resources``。

    leg_a (entities) previous_state 保留：
    - ``characters``（已是 O-1 trimmed：touched 全量 / 其他仅 {character_id, name, facet}）
    - ``characters[].relationships``（touched 全量 / 其他仅摘要）
    - ``world.locations`` / ``world.factions``（touched value 全量 / 其他仅 {name}）
    - ``world.world_rules``（touched 全量 / 其他仅 {world_rule_id, name}）
    leg_a 移除：``events`` / ``hooks`` / ``debts``（leg_a 不读）。

    leg_b (narrative) previous_state 保留：
    - ``events``（O-1 窗口口径，原样）
    - ``hooks``（open 压缩口径，resolved 仅摘要，与 O-1 对齐）
    - ``debts``（open 压缩口径，resolved 仅摘要，与 O-1 对齐）
    leg_b 实体集合降级：
    - ``characters`` → ``{character_id, name, role, status}`` 摘要
    - ``world.locations`` → ``{location_id, name}`` 摘要
    - ``world.active_factions`` 或 ``world.factions`` → ``{faction_id, name}`` 摘要
    - ``world.world_rules`` → ``{world_rule_id, name, statement}`` 摘要（事件可能引用规则变化）

    stats 记录裁剪前后体积（按 leg 统计）：
    - ``leg`` / ``previous_state_bytes_before`` / ``previous_state_bytes_after``
    - ``characters_kept`` / ``locations_kept`` / ``factions_kept`` / ``world_rules_kept``
    - ``events_kept`` / ``hooks_kept`` / ``debts_kept``
    """
    if leg not in ("entities", "narrative"):
        raise ValueError(f"leg must be 'entities' or 'narrative', got {leg!r}")

    prev_state = base_payload.get("previous_state") or {}
    if not isinstance(prev_state, dict):
        prev_state = {}

    try:
        bytes_before = len(json.dumps(prev_state, ensure_ascii=False))
    except (TypeError, ValueError):
        bytes_before = 0

    stats: dict[str, Any] = {
        "leg": leg,
        "previous_state_bytes_before": bytes_before,
        "characters_kept": 0,
        "locations_kept": 0,
        "factions_kept": 0,
        "world_rules_kept": 0,
        "events_kept": 0,
        "hooks_kept": 0,
        "debts_kept": 0,
    }

    # 顶层元信息（两腿都保留）
    new_state: dict[str, Any] = {}
    for k in ("snapshot_mode", "state_version", "recent_events"):
        if k in prev_state:
            new_state[k] = prev_state[k]
    # 如果原 snapshot 没有 snapshot_mode 但 snapshot_mode='trimmed'，补一个标识
    if "snapshot_mode" not in new_state:
        new_state["snapshot_mode"] = (
            prev_state.get("snapshot_mode") or "trimmed"
        )

    if leg == "entities":
        # ---- characters（保留 O-1 裁剪后的形态）----
        chars_in = prev_state.get("characters") or []
        chars_out: list[dict[str, Any]] = []
        if isinstance(chars_in, list):
            for c in chars_in:
                if isinstance(c, dict):
                    chars_out.append(c)
        stats["characters_kept"] = len(chars_out)
        new_state["characters"] = chars_out

        # ---- world（保留 locations / factions / world_rules；current_time 等不动）----
        world_in = prev_state.get("world") or {}
        world_out: dict[str, Any] = {}
        if isinstance(world_in, dict):
            for k in ("current_time_in_story", "active_resources"):
                if k in world_in:
                    world_out[k] = world_in[k]

            locs_in = world_in.get("locations") or {}
            if isinstance(locs_in, dict):
                world_out["locations"] = dict(locs_in)
                stats["locations_kept"] = len(locs_in)
            elif isinstance(locs_in, list):
                # list 形态：每条带 location_id 的项原样保留（与 O-1 兼容）
                world_out["locations"] = [
                    x for x in locs_in if isinstance(x, dict)
                ]
                stats["locations_kept"] = len(world_out["locations"])

            facs_in = world_in.get("factions") or {}
            if isinstance(facs_in, dict):
                world_out["factions"] = dict(facs_in)
                stats["factions_kept"] = len(facs_in)
            elif isinstance(facs_in, list):
                world_out["factions"] = [
                    x for x in facs_in if isinstance(x, dict)
                ]
                stats["factions_kept"] = len(world_out["factions"])
            # 兼容：有的 snapshot 把 factions 放在 active_factions
            active_facs_in = world_in.get("active_factions")
            if active_facs_in is not None and "factions" not in world_out:
                if isinstance(active_facs_in, list):
                    world_out["factions"] = [
                        x for x in active_facs_in if isinstance(x, dict)
                    ]
                    stats["factions_kept"] = len(world_out["factions"])

            rules_in = world_in.get("world_rules") or []
            if isinstance(rules_in, list):
                world_out["world_rules"] = list(rules_in)
                stats["world_rules_kept"] = len(rules_in)

        new_state["world"] = world_out
        # 明确移除 leg_a 不读的集合（即便原 snapshot_mode=trimmed 也移除——保证 payload 字节级一致）
        # events / hooks / debts 全部置空 list，避免 observer 误读
        new_state["events"] = {}
        new_state["hooks"] = []
        new_state["debts"] = []

    else:  # leg == "narrative"
        # ---- 实体集合降级为标识性摘要 ----
        chars_in = prev_state.get("characters") or []
        chars_out: list[dict[str, Any]] = []
        if isinstance(chars_in, list):
            for c in chars_in:
                chars_out.append(_summarize_character_for_leg_b(c))
        stats["characters_kept"] = len(chars_out)
        new_state["characters"] = chars_out

        world_in = prev_state.get("world") or {}
        world_out: dict[str, Any] = {}
        if isinstance(world_in, dict):
            for k in ("current_time_in_story", "active_resources"):
                if k in world_in:
                    world_out[k] = world_in[k]

            locs_in = world_in.get("locations") or {}
            if isinstance(locs_in, dict):
                world_out["locations"] = {
                    lid: _summarize_location_for_leg_b(lval, lid)
                    for lid, lval in locs_in.items()
                }
                stats["locations_kept"] = len(locs_in)
            elif isinstance(locs_in, list):
                world_out["locations"] = [
                    _summarize_location_for_leg_b(
                        x, x.get("location_id") if isinstance(x, dict) else None
                    )
                    for x in locs_in if isinstance(x, dict)
                ]
                stats["locations_kept"] = len(world_out["locations"])

            facs_in = world_in.get("factions") or {}
            if isinstance(facs_in, dict):
                world_out["factions"] = {
                    fid: _summarize_faction_for_leg_b(fval, fid)
                    for fid, fval in facs_in.items()
                }
                stats["factions_kept"] = len(facs_in)
            elif isinstance(facs_in, list):
                world_out["factions"] = [
                    _summarize_faction_for_leg_b(
                        x, x.get("faction_id") if isinstance(x, dict) else None
                    )
                    for x in facs_in if isinstance(x, dict)
                ]
                stats["factions_kept"] = len(world_out["factions"])
            active_facs_in = world_in.get("active_factions")
            if active_facs_in is not None and "factions" not in world_out:
                if isinstance(active_facs_in, list):
                    world_out["factions"] = [
                        _summarize_faction_for_leg_b(
                            x, x.get("faction_id") if isinstance(x, dict) else None
                        )
                        for x in active_facs_in if isinstance(x, dict)
                    ]
                    stats["factions_kept"] = len(world_out["factions"])

            rules_in = world_in.get("world_rules") or []
            if isinstance(rules_in, list):
                world_out["world_rules"] = [
                    _summarize_world_rule_for_leg_b(r)
                    for r in rules_in if isinstance(r, dict)
                ]
                stats["world_rules_kept"] = len(world_out["world_rules"])

        new_state["world"] = world_out

        # ---- events / hooks / debts 原样保留（O-1 口径）----
        events_in = prev_state.get("events")
        if isinstance(events_in, dict):
            new_state["events"] = events_in
            stats["events_kept"] = len(events_in)
        else:
            new_state["events"] = {}

        hooks_in = prev_state.get("hooks") or []
        if isinstance(hooks_in, list):
            new_state["hooks"] = list(hooks_in)
            stats["hooks_kept"] = len(hooks_in)
        else:
            new_state["hooks"] = []

        debts_in = prev_state.get("debts") or []
        if isinstance(debts_in, list):
            new_state["debts"] = list(debts_in)
            stats["debts_kept"] = len(debts_in)
        else:
            new_state["debts"] = []

    try:
        bytes_after = len(json.dumps(new_state, ensure_ascii=False))
    except (TypeError, ValueError):
        bytes_after = 0
    stats["previous_state_bytes_after"] = bytes_after
    stats["previous_state_bytes_delta"] = bytes_before - bytes_after

    trimmed = dict(base_payload)
    trimmed["previous_state"] = new_state
    # 按腿的 stats 写到 payload 顶层，命名 leg_<x>_snapshot_trim_stats
    # 让两腿各自读各自的 stats，便于后续观测与测试断言。
    trimmed[f"snapshot_trim_stats_leg_{'a' if leg == 'entities' else 'b'}"] = stats
    return trimmed, stats


def _pick_retry_mock(mock_script: Any, leg: str) -> Any:
    """按 leg 选取「下一条」mock 响应；非 list 模式保持原样。

    与 _observer_node 首次调用的口径对齐：list[str] 模式按 leg 过滤（取下一条
    元素再按 scope 过滤），让 mock 测试可以分别控制双腿的首次 / 重试响应。
    """
    if mock_script is None or callable(mock_script) or isinstance(mock_script, str):
        # 字符串 / callable / None：透传（重试仅靠 _retry_hint 修正）
        return mock_script
    if not isinstance(mock_script, list) or len(mock_script) <= 1:
        return mock_script
    # list 模式 + 多条：弹下一条以让 MockProvider 返回不同响应
    picked = mock_script[1]
    retry_list = [picked] if isinstance(picked, str) else picked
    return _filter_mock_for_leg(retry_list, leg)


# ============================================================================
# Sprint 6 下半：quality_gate 节点
# ============================================================================


def _quality_gate_mode(ctx: dict[str, Any]) -> str:
    """解析 quality_gate 阻断模式。

    优先级（与现有 NOVELOS_* 环境变量口径对齐）：
    - ``ctx["quality_gate_mode"]``（调用方 / 测试用例可显式注入）。
    - ``NOVELOS_QUALITY_GATE`` 环境变量（``"enforce"`` / ``"report"``，默认 ``"enforce"``）。
    """
    mode = ctx.get("quality_gate_mode") or os.environ.get("NOVELOS_QUALITY_GATE", "enforce")
    mode = str(mode).strip().lower()
    return mode if mode in {"enforce", "report"} else "enforce"


def _quality_gate_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """quality_gate 节点。

    - 现场组装 :class:`QualityContext`（复用 :func:`packages.core.quality.service.build_quality_context`）；
    - 调 :class:`QualityEngine.evaluate`；
    - 落 ``quality_reports`` 表（与 ``commit`` 不在同一事务——见 :mod:`packages.core.quality.service` 注释）；
    - 任一 ``severity == 'error'`` + ``enforce`` 模式 ⇒ 抛 :class:`ValueError` 阻断。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    project_id = ctx.get("project_id")
    if not project_id:
        svc = StoryStateService(db_path)
        project_id = svc._project_id_for_chapter(  # noqa: SLF001
            get_connection(db_path), chapter_id
        )
    if not project_id:
        raise ValueError(f"chapter {chapter_id!r} not found")

    delta = ctx.get("delta") or {}
    snapshot_pre = ctx.get("snapshot_pre") or {}

    # Sprint V1.4：参照系消费可观测——quality_gate 现场采集本节点消费的 *.txt 清单
    # 写到节点返回 dict，供 checkpoint_json 暴露给前端；与 quality 评估同一时机，
    # 阻断时也会随 ctx 落盘（_update_run_checkpoint 在每节点完成后写一次）。
    reference_consumption = capture_reference_consumption(db_path, project_id)

    quality_ctx = build_quality_context(
        db_path,
        project_id=project_id,
        chapter_id=chapter_id,
        delta=delta,
        snapshot_pre=snapshot_pre,
        run_id=ctx.get("run_id"),
    )
    report = QualityEngine().evaluate(quality_ctx)
    # Sprint V1.4：把参照系消费清单持久化到 quality_reports._meta.reference_consumption，
    # 让 /api/chapters/{cid}/quality 端点直接返回，UI 不必再回查 runs.checkpoint_json。
    meta = dict(report.meta or {})
    meta["reference_consumption"] = reference_consumption
    report.meta = meta
    QualityService(db_path).save_report(
        report,
        project_id=project_id,
        chapter_id=chapter_id,
        run_id=ctx.get("run_id"),
    )

    mode = _quality_gate_mode(ctx)
    error_issues = [i for i in report.issues if i.severity == "error"]
    error_rule_ids = [i.rule_id for i in error_issues]

    # Sprint V1.4：enforce 模式阻断时，把每低分维度的可执行改稿建议结构化带上。
    # revision_guidance 写进 ctx['quality_gate']（checkpoint_json 会自动收录），
    # 同时把整段结构化 payload JSON 化追加到 ValueError 信息里——run FAILED 时
    # runs.error 已包含它，前端 QualityPanel 可直接从错误字符串里解析。
    revision_guidance: list[dict[str, Any]] = []
    if error_issues:
        revision_guidance = _build_revision_guidance(report, report.issues)
    ctx["quality_gate"] = {
        "blocked": bool(error_issues) and mode == "enforce",
        "mode": mode,
        "reference_consumption": reference_consumption,
        "revision_guidance": revision_guidance,
    }

    if error_issues and mode == "enforce":
        # 与现有 _commit_node 失败语义一致：抛 ValueError 让 run FAILED，
        # chapter 保持当前状态（当前章节 status=REVIEWED；error 阻断不会推到 COMMITTED）。
        # 在错误信息里把 revision_guidance 序列化为可解析段：
        #   "quality gate blocked: <rule_ids> | guidance=<json>"
        # 前端 / 测试可按 "| guidance=" 分隔；JSON 解析失败也不影响主信息。
        import json as _json
        try:
            guidance_json = _json.dumps(revision_guidance, ensure_ascii=False)
        except (TypeError, ValueError):
            guidance_json = "[]"
        raise ValueError(
            f"quality gate blocked: {error_rule_ids} | guidance={guidance_json}"
        )

    return {
        "quality_report": report.model_dump(by_alias=True),
        "quality_gate_mode": mode,
        "quality_report_id": report.report_id,
        "quality_overall": int(report.overall),
        "quality_error_count": len(error_issues),
        "reference_consumption": reference_consumption,
        "revision_guidance": revision_guidance,
    }


def _high_risk_approval_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Human 节点：仅在 observer_payload 含 HIGH / definition / rule change 时需要人工审批。
    human_input={"approved": true} → 通过。
    """
    needs = bool(ctx.get("needs_high_risk_approval"))
    hi = ctx.get("human_input") or {}
    approved = bool(hi.get("approved")) if isinstance(hi, dict) else False
    ctx["_high_risk_approved"] = approved

    if not needs:
        # 不需要审批：直接通过
        return {"high_risk_required": False, "human_input": hi}

    if approved:
        return {"high_risk_required": True, "human_input": hi}

    payload = {
        "stage": "chapter-commit.high_risk_approval",
        "message": "Observer 检测到 HIGH 风险 / definition / world_kind=rule change，请人工审批",
        "delta_id": ctx.get("delta_id"),
        "changes": {
            "character_changes": ctx.get("observer_payload", {}).get("character_changes", []),
            "world_changes": ctx.get("observer_payload", {}).get("world_changes", []),
        },
    }
    raise PauseRequested(payload)


def _commit_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    run_id = ctx["run_id"]
    delta_id = ctx.get("delta_id")
    if not delta_id:
        raise ValueError("inject_validate 节点未产出 delta_id")

    needs_high_risk = bool(ctx.get("needs_high_risk_approval"))
    # resume 场景：human_input 是权威源；_high_risk_approved 仅作为 first-pass 标记
    if needs_high_risk:
        hi = ctx.get("human_input") or {}
        approved = bool(hi.get("approved")) if isinstance(hi, dict) else bool(ctx.get("_high_risk_approved"))
    else:
        approved = True
    if not approved:
        raise ValueError("HIGH 风险变更未通过 author approval，无法 commit")

    conn = get_connection(db_path)
    try:
        cur = conn.execute("SELECT status FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
    finally:
        conn.close()
    if cur is None:
        raise ValueError(f"chapter {chapter_id!r} not found")
    # 状态机：必须先 REVIEWED 才能 COMMITTED；DRAFTED 直接 commit 拒绝
    if cur["status"] != "REVIEWED":
        raise ValueError(
            f"chapter {chapter_id!r} status={cur['status']!r}；请先跑 chapter-review 把它推到 REVIEWED"
        )

    svc = StoryStateService(db_path)
    author_approval = {
        "approved": True,
        "approver": "human" if needs_high_risk else "system:workflow",
        "notes": "chapter-commit workflow auto-approved" if not needs_high_risk else "human approved HIGH risk",
    }
    commit_result = svc.commit_delta(delta_id, author_approval, run_id)

    # chapters.status REVIEWED→COMMITTED
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET status = 'COMMITTED', updated_at = ? WHERE chapter_id = ?",
            (now_iso(), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()

    # V2.0 Wave C P1-1：commit 成功后显式失效本章装配缓存（兜底）。
    # 正常情况下 state_version 已推进，缓存键自然失效；此处对"plan_json
    # 被 UPDATE 但 state_version 未变"的边界场景提供最后一道防线。
    # 任何异常均吞掉——失效失败绝不能阻断 commit。
    try:
        from packages.core.context_engine.builders import (
            _invalidate_cache_for_chapter,
            _peek_project_id_from_chapter,
        )
        _proj_id = _peek_project_id_from_chapter(db_path, chapter_id)
        _chap_row = get_connection(db_path).execute(
            "SELECT number FROM chapters WHERE chapter_id = ?", (chapter_id,),
        ).fetchone()
        if _proj_id is not None and _chap_row is not None:
            _invalidate_cache_for_chapter(_proj_id, int(_chap_row["number"] or 0))
    except Exception:  # noqa: BLE001 —— 失效失败不阻断 commit
        pass

    # V2.0 Wave C 任务一：commit 成功后 upsert 本章正文进 chapter_fts（FTS5 全文检索虚表）。
    # 失败按 summarize 节点相同语义降级：log warning，不阻断 commit。
    # 兜底触发是因为 0011_fts_index.sql 的 trigger 在某些边界场景下可能被 SQLite
    # 跳过（例如 EXTERNAL content 模式 + UPDATE 时 old.content 为 NULL）；显式
    # upsert_chapter 保证下次召回能拿到最新章节。
    fts_upsert_ok: bool = True
    fts_upsert_error: str | None = None
    try:
        from packages.core.retrieval import upsert_chapter
        ok = upsert_chapter(db_path, chapter_id)
        if not ok:
            fts_upsert_ok = False
            fts_upsert_error = "upsert_chapter returned False"
    except Exception as exc:  # noqa: BLE001 —— 降级：失败不抛
        fts_upsert_ok = False
        fts_upsert_error = str(exc)
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "chapter_commit.fts_upsert degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )

    return {
        "commit_result": commit_result,
        "status_after": "COMMITTED",
        "fts_upsert_ok": fts_upsert_ok,
        "fts_upsert_error": fts_upsert_error,
    }


# ============================================================================
# Sprint 14：summarize 节点（commit 成功后追加，失败降级不阻塞）
# ============================================================================


# 摘要最大字符数（中文按字符）；超长由 service 层截断 + 标 degraded。
_SUMMARY_MAX_CHARS = 200
# tail_text 取已提交正文末尾字符数（不调 LLM）。
_TAIL_TEXT_CHARS = 300


def _prepare_summarizer_call(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """抽取 summarizer LLM 调用所需的全部准备产物（V3.7）。

    把 ``_summarize_node`` 中「调 LLM 之前」的步骤下沉：取章节 + 草稿 + 计划 goal +
    组装 ``summary_payload`` + 取 mock_script。供 observer 节点提前并发调用，
    命中后下游 ``_summarize_node`` 短路消费 ``ctx['summary_early']``。

    返回 ``None`` 表示必需输入缺失（章节不存在 / 草稿为空），调用方应跳过本次早产调用
    而非抛错。键序与原 ``_summarize_node`` 内联构造时保持一致——尤其是 ``chapter`` 子
    dict 的 ``chapter_goal / chapter_no / chapter_id`` 顺序（缓存重排对齐）。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    mock_script = (ctx.get("mock_providers") or {}).get("summarizer")

    # 1) 章节 + 项目 + chapter_no
    conn = get_connection(db_path)
    try:
        chap_row = conn.execute(
            "SELECT project_id, number FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        if chap_row is None:
            return None
        project_id = chap_row["project_id"]
        chapter_no = int(chap_row["number"] or 0)
        draft_row = conn.execute(
            """
            SELECT content FROM drafts
            WHERE chapter_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (chapter_id,),
        ).fetchone()
    finally:
        conn.close()
    content = (dict(draft_row) if draft_row else {}).get("content") or ""
    if not content.strip():
        return None

    # 2) tail_text（不调 LLM）
    tail_text = content[-_TAIL_TEXT_CHARS:] if len(content) > _TAIL_TEXT_CHARS else content

    # 3) plan_goal（读取失败不阻塞）
    plan_goal = ""
    try:
        conn = get_connection(db_path)
        try:
            pj = conn.execute(
                "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,),
            ).fetchone()
        finally:
            conn.close()
        if pj:
            raw = pj["plan_json"] or "{}"
            try:
                pj_d = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (TypeError, ValueError):
                pj_d = {}
            plan_goal = (pj_d or {}).get("chapter_goal") or ""
    except Exception:  # noqa: BLE001 —— 计划读取失败不阻塞 summarize
        plan_goal = ""

    # 键序固定：agent → prompt_version → chapter{chapter_goal, chapter_no, chapter_id}
    # → prose_excerpt → tail_text（缓存重排对齐）。
    summary_payload: dict[str, Any] = {
        "agent": "summarizer",
        "prompt_version": "summarizer:v1",
        "chapter": {
            "chapter_goal": plan_goal,
            "chapter_no": chapter_no,
            "chapter_id": chapter_id,
        },
        "prose_excerpt": content[:4000],  # 取前 4000 字足够上下文（避免超长 prompt）
        "tail_text": tail_text,
    }
    return {
        "payload": summary_payload,
        "mock": mock_script,
        "project_id": project_id,
        "chapter_no": chapter_no,
        "content": content,
        "tail_text": tail_text,
    }


def _resolve_summary_out(
    out: Any,
    *,
    summary_max_chars: int,
) -> tuple[str, bool]:
    """把 summarizer LLM 输出 dict 解析为 ``(summary_text, degraded)``。

    解析失败抛 ``ValueError``，由调用方按已有 degraded 分支处理。
    """
    if not isinstance(out, dict):
        raise ValueError(f"summarizer output not dict: {type(out).__name__}")
    candidate = out.get("summary")
    if not isinstance(candidate, str):
        raise ValueError("summarizer output missing 'summary' string")
    summary_text = candidate.strip()
    if not summary_text:
        raise ValueError("summarizer output 'summary' empty")
    degraded = False
    if len(summary_text) > summary_max_chars:
        summary_text = summary_text[:summary_max_chars]
        degraded = True
    return summary_text, degraded


def _insert_chapter_summary_row(
    db_path: Any,
    *,
    project_id: str,
    chapter_id: str,
    chapter_no: int,
    summary_text: str,
    tail_text: str,
) -> str:
    """落库 ``chapter_summaries`` 行，返回新生成的 ``summary_id``。"""
    summary_id = new_id("sum")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapter_summaries
                (summary_id, project_id, chapter_id, chapter_no,
                 summary, tail_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary_id,
                project_id,
                chapter_id,
                chapter_no,
                summary_text,
                tail_text,
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return summary_id


def _summarize_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """summarize 节点（Sprint 14；V3.7：可选短路消费 ``summary_early``）—— commit 成功后追加。

    行为：
    1. 若 ``ctx['summary_early']`` 存在且 ``not early.get('skipped')``：跳过 ``run_agent``，
       直接复用「解析 out → 截断 → 落库」段。
    2. 否则走原路径：先 :func:`_prepare_summarizer_call` 取准备产物（缺失则 skipped）→
       ``run_agent('summarizer', ...)`` → 解析 ``{"summary"}`` → 写 ``chapter_summaries`` 表
       → 返回 ``{"summary_status","summary_id","degraded"}``。内部已有 PromptNotFoundError
       等降级路径（degraded=True 不抛错）。
    3. 任何异常（LLM 失败 / JSON 解析失败 / DB 写入失败）→ 降级：warning 日志 +
       summary_status='failed' + 不抛错（保证 chapter 提交不因摘要失败而 FAILED）。

    返回 ``{"summary_status": "ok|failed|skipped", "summary_id": str|None, "degraded": bool}``。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]

    # V3.7：短路消费 observer 提前并发产出的 summary_early。
    early = ctx.get("summary_early")
    if isinstance(early, dict) and not early.get("skipped"):
        out = early.get("output")
        try:
            summary_text, degraded = _resolve_summary_out(
                out, summary_max_chars=_SUMMARY_MAX_CHARS,
            )
        except Exception as exc:  # noqa: BLE001
            # V3.7 修复 A1：短路路径解析失败 → fallthrough 自愈（重新 prepare +
            # run_agent），与「缺字段分支」一致；自然走到原路径的 degraded 兜底
            # （run_agent 异常 → summary_status='failed'）。
            import logging
            logging.getLogger(__name__).warning(
                "chapter_commit.summarize early output malformed, fallback to "
                "self-heal: chapter_id=%s err=%s", chapter_id, exc,
            )
            early = None  # fallthrough 到下方原路径 self-heal
        else:
            # 解析成功 → 落库所需字段从 early 附带（observer 节点透传 prepared 上下文）。
            project_id = early.get("project_id")
            chapter_no = int(early.get("chapter_no") or 0)
            tail_text = early.get("tail_text") or ""
            if not project_id or not tail_text:
                # 早产数据缺关键字段 → 走自愈路径（重新自己 prepare）
                early = None  # fallthrough 到下方原路径 self-heal
            else:
                try:
                    summary_id = _insert_chapter_summary_row(
                        db_path,
                        project_id=project_id,
                        chapter_id=chapter_id,
                        chapter_no=chapter_no,
                        summary_text=summary_text,
                        tail_text=tail_text,
                    )
                except Exception as exc:  # noqa: BLE001
                    import logging
                    logging.getLogger(__name__).warning(
                        "chapter_commit.summarize early DB insert failed: "
                        "chapter_id=%s err=%s", chapter_id, exc,
                    )
                    return {
                        "summary_status": "failed",
                        "summary_id": None,
                        "degraded": False,
                        "summary_error": str(exc),
                    }
                return {
                    "summary_status": "ok",
                    "summary_id": summary_id,
                    "degraded": degraded,
                }

    # 原路径：自己 prepare + run_agent + 写库。
    prepared = _prepare_summarizer_call(ctx)
    if prepared is None:
        return {"summary_status": "skipped", "summary_id": None, "degraded": False}

    mock_script = prepared["mock"]
    summary_payload = prepared["payload"]

    degraded = False
    summary_text = ""
    try:
        # summarizer 无 ACTIVE prompt 时 runner 会抛 PromptNotFoundError → 降级。
        out = run_agent(
            db_path,
            "summarizer",
            summary_payload,
            ctx.get("run_id") or "",
            node_run_id=ctx.get("_current_node_run_id"),
            expected="summarizer",
            mock_script=mock_script,
        )
        summary_text, degraded = _resolve_summary_out(
            out, summary_max_chars=_SUMMARY_MAX_CHARS,
        )
    except Exception as exc:  # noqa: BLE001 —— 降级：任何失败不抛
        import logging
        logging.getLogger(__name__).warning(
            "chapter_commit.summarize degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "summary_status": "failed",
            "summary_id": None,
            "degraded": False,
            "summary_error": str(exc),
        }

    # 4) 落库 chapter_summaries
    try:
        summary_id = _insert_chapter_summary_row(
            db_path,
            project_id=prepared["project_id"],
            chapter_id=chapter_id,
            chapter_no=int(prepared["chapter_no"]),
            summary_text=summary_text,
            tail_text=prepared["tail_text"],
        )
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "chapter_commit.summarize DB insert failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "summary_status": "failed",
            "summary_id": None,
            "degraded": False,
            "summary_error": str(exc),
        }

    return {
        "summary_status": "ok",
        "summary_id": summary_id,
        "degraded": degraded,
    }


def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("build_observer_ctx", "Transform", _build_observer_ctx_node),
        WorkflowNode("observer", "AI", _observer_node, agent_name="observer"),
        WorkflowNode("inject_validate", "Transform", _inject_validate_node),
        WorkflowNode("quality_gate", "State", _quality_gate_node),
        WorkflowNode("high_risk_approval", "Human", _high_risk_approval_node),
        WorkflowNode("commit", "State", _commit_node),
        WorkflowNode("summarize", "State", _summarize_node),
    ]


WORKFLOW = {
    "name": "chapter-commit",
    "version": "v1",
    "description": (
        "Observer → inject metadata → submit_delta → quality_gate → "
        "(HIGH) Human Approval → commit_delta; status REVIEWED→COMMITTED"
    ),
    "nodes": _build_nodes(),
    # V1.0 checkpoint 写放大优化（Sprint V1.5）：high_risk_approval 是 Human 节点（仅当
    # observer_payload 含 HIGH/definition/rule change 时 PAUSE）。PAUSE 时 checkpoint
    # 落盘，下游 commit 节点**只读** delta_id / needs_high_risk_approval / human_input /
    # _high_risk_approved。observer_input（含 previous_state 完整快照 1-10KB）、
    # observer_payload（observer 7 数组 0.5-2KB）、delta（与 observer_payload 同步 7 数组）、
    # submit_result / snapshot_pre 均不被 commit 节点读——可安全 exclude。
    # 必保留：delta_id（commit 节点调 StoryStateService.commit_delta 用作入参；service 自己
    # 按 delta_id 从 state_deltas 表读回 delta 行，不依赖 ctx['delta'] 内容）。
    "checkpoint_exclude": [
        "observer_input",
        "observer_payload",
        "delta",
        "submit_result",
        "snapshot_pre",
        # V3.7：observer 节点提前并发生成的 summarizer 早产结果。
        # 排除理由：
        # 1. PAUSED→resume 场景下，commit 节点之后才轮到 summarize；engine
        #    ``resume``（packages/core/workflow_runtime/engine.py:264-279）从
        #    high_risk_approval 节点的 PENDING 状态续跑，**不会重放** observer 节点
        #    → summary_early 无源头生成，必须靠 summarize 节点走自愈（重新 prepare
        #    + run_agent）才能完成，与 V3.7 前语义完全一致、幂等。
        # 2. checkpoint 体积：summary_early 含 LLM 输出（典型 <1KB），虽然小但属于
        #    可由 _summarize_node 重新产生的派生数据，没必要进 checkpoint 增加回放、
        #    audit export 复杂度。
        # 3. 不影响「completed 路径上的端到端确定性」：非 PAUSED 场景下 observer 与
        #    summarize 在同一 run 里串行执行，observer 写完 ctx 后 engine 立即到
        #    summarize 节点读到 summary_early，checkpoint 落盘已晚 → exclude 是
        #    「未引用到的派生数据」性质的清理，不破坏任何运行时行为。
        "summary_early",
    ],
}


# 注意（Sprint V1.5）：注册动作统一在 :mod:`packages.workflows.chapter_commit.__init__`
# 调用 :func:`packages.core.workflow_registry.register_workflow`；本模块不再暴露
# ``register_workflow`` 函数。


__all__ = ["WORKFLOW"]
