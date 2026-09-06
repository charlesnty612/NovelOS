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

V3.9.4 单次 run 级 model_overrides（与 chapter-write 范式一致）：
- ``ctx['model_overrides']`` 携带 ``{"observer": <profile_id>}`` 时，observer 所有
  ``run_agent`` 调用（单次 / 双 leg / 拆分并发 / summary-early / 两条 retry 路径）都
  会把 ``profile_id=`` 透传给 :class:`ModelRouter`，锁定唯一候选（缺/disabled 抛错不回落）。
- ``summarizer`` 节点（独立 summarize 节点 + observer summary-early 早产）的覆盖走
  ``ctx['model_overrides']['light']``（与 ``capability_for('summarizer')`` 对齐）；
  用户覆盖 observer 不会串到 summarizer。
- mock 路径不消费 profile_id（mock 自带脚本），与既有契约一致。
"""

from __future__ import annotations

import json
import os
from typing import Any

from packages.core.quality.models import Issue
from packages.core.quality.models import QualityReport as _QualityReport

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
# token 量减半。V3.9.3 之前双腿硬编码 ``capability_override="light"`` 走 light capability；
# V3.9.3 起 observer 拆为独立环节（见 packages/core/model_router/router.py 的
# ``AGENT_CAPABILITY["observer"] = "observer"``），双腿显式指定 ``capability_override=
# "observer"``，前端 AI 设置页能单独给 observer 分配模型。迁移 0018 负责把 reasoning
# 的当前绑定同步给 observer 行（已有行不动，幂等），线上行为保持不变。
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

# ============================================================================
# V3.10 O-4：可核销白名单（hook / debt 仍可在本章被 resolve / update 的 id 集合）
# ============================================================================
# 根因：observer 反复在 ``resolved_hooks[*].hook_id`` / ``debt_changes[*].debt_id``
# 中引用 snapshot 不存在、本 delta 也未 add 创建的 id，validator 在
# ``packages/core/story_state/validator.py:319-327`` 的 FK 存在性规则处拦截；
# 同时 ``debt_changes[*].status_before=None`` 违反 schema 枚举，导致 commit run
# 在生产连跑三次失败（mini-cap-shape 现场）。
# 设计：
# - pipeline 在构造 observer payload 前，从 observer_input.previous_state（与
#   observer 所见快照一致；**不**直查 DB，避免与 observer 看到的快照错位）收集
#   hooks / debts 的「未结清」id 白名单，注入 payload.config 的
#   ``resolvable_hook_ids`` / ``resolvable_debt_ids``，observer 仅允许引用其中 id
#   做 resolved_hooks / debt_changes.update 类操作；add 类不受限。
# - 白名单只注入 narrative 腿（_OBSERVER_LEG_B_ARRAYS 覆盖 resolved_hooks /
#   debt_changes）；entities 腿不需要。
# - 数据源必须 = observer 看到的 snapshot（ctx["observer_input"]["previous_state"]），
#   与 validator 校验用的 ``snapshot_for_validate`` 同源，确保 observer 与
#   validator 视角一致。
# - snapshot 缺 hooks / debts 集合时返回空列表，绝不抛错（不影响单腿旧路径、
#   空 snapshot 走默认空白名单）。
_HOOK_OPEN_STATUSES = frozenset({"OPEN", "ACTIVE", "ESCALATED"})
_DEBT_OPEN_STATUSES = frozenset({"open", "acknowledged"})


def _collect_resolvable_ids(snapshot: dict[str, Any]) -> dict[str, list[str]]:
    """从 snapshot 的 hooks / debts 集合收集「未结清」条目的 id 白名单。

    返回 ``{"hooks": [<hook_id>, ...], "debts": [<debt_id>, ...]}``；集合缺 / 空
    时返回空列表；snapshot 不是 dict 时返回两组空列表（兜底）。

    字段名以 ``packages/core/story_state/snapshot.py:_load_hooks`` /
    ``_load_debts`` 为准：
    - hooks：``hook_id`` / ``status``（五态枚举：OPEN / ACTIVE / ESCALATED /
      RESOLVED / ABANDONED，「未结清」取 OPEN / ACTIVE / ESCALATED）
    - debts：``debt_id`` / ``status``（四态枚举：open / acknowledged / paid /
      forgiven，「未结清」取 open / acknowledged）

    本函数是纯函数：snapshot 必须是 immutable 视图（dict 引用即可，不就地修改）。
    """
    hooks_out: list[str] = []
    debts_out: list[str] = []
    if not isinstance(snapshot, dict):
        return {"hooks": hooks_out, "debts": debts_out}

    hooks = snapshot.get("hooks")
    if isinstance(hooks, list):
        for h in hooks:
            if not isinstance(h, dict):
                continue
            hid = h.get("hook_id")
            status = h.get("status")
            if isinstance(hid, str) and hid and status in _HOOK_OPEN_STATUSES:
                hooks_out.append(hid)

    debts = snapshot.get("debts")
    if isinstance(debts, list):
        for d in debts:
            if not isinstance(d, dict):
                continue
            did = d.get("debt_id")
            status = d.get("status")
            if isinstance(did, str) and did and status in _DEBT_OPEN_STATUSES:
                debts_out.append(did)

    return {"hooks": hooks_out, "debts": debts_out}


def _inject_resolvable_ids_into_config(
    payload: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """把 resolvable_hook_ids / resolvable_debt_ids 写入 payload['config']。

    - payload 必须有 config 字段（缺则新建 dict）；
    - 白名单数据源 = snapshot（与 validator 校验快照同源）；
    - 已是正确类型（list）时直接覆盖；其它类型降级为新建空 list；
    - 本函数不修改入参 payload，浅拷贝返回。
    """
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    cfg = out.get("config")
    if not isinstance(cfg, dict):
        cfg = {}
    else:
        cfg = dict(cfg)
    ids = _collect_resolvable_ids(snapshot)
    cfg["resolvable_hook_ids"] = list(ids.get("hooks") or [])
    cfg["resolvable_debt_ids"] = list(ids.get("debts") or [])
    out["config"] = cfg
    return out


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
