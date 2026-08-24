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
  与 high_risk_approval / commit 是顺序节点；eval golden / 测试默认走
  ``report`` 避免 REQ-Q8 / H-3 等 MVP 阻断规则误伤（任务书拍板）。
- ``high_risk_approval`` (Human) —— **仅当 payload 含 HIGH/definition/rule change 时暂停**，
  payload=change 清单；human_input={"approved": true}。
- ``commit`` (State) —— 调 :meth:`StoryStateService.commit_delta`；
  chapters.status REVIEWED→COMMITTED；若当前 DRAFTED（未过 review）则 run FAILED 提示先跑 review。
"""

from __future__ import annotations

import os
from typing import Any

from packages.core.agent_runtime.runner import run_agent
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
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    payload = build_observer_input(db_path, chapter_id)
    return {"observer_input": payload}


def _observer_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    payload = ctx["observer_input"]
    mock_script = (ctx.get("mock_providers") or {}).get("observer")
    out = run_agent(
        db_path,
        "observer",
        payload,
        run_id,
        node_run_id=ctx.get("_current_node_run_id"),
        expected="observer",
        mock_script=mock_script,
    )
    # out 含 7 个 change 数组（无元信息）
    return {"observer_payload": out}


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
    """注入元信息 → validate_delta（纯函数，无落库副作用）→ 失败带错误重试 observer 一次。

    重试范围（与 deconstruct_book 的 T2/T3 retry 范式对齐）：
    - 校验失败后构造新 payload = ``dict(ctx["observer_input"])`` + ``_retry_hint``；
    - mock_script 取 ``ctx["mock_providers"]["observer"]``：list 且长度 >1 时取 ``mock_script[1]``，
      否则保持原样（让生产 / 单条 mock 走同一响应，retry 仅靠 _retry_hint 修正）；
    - 两次都失败 ⇒ ``raise ValueError("observer delta rejected by validator: errors=...")``。
    - submit_delta 仅在最终通过的 delta 上调一次（service.py:662-680 失败会落 rejected 行，
      重试循环内禁止反复调）。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    run_id = ctx["run_id"]
    observer_payload = ctx.get("observer_payload") or {}

    # 取当前 state_version 作 previous_state_version（不依赖 observer_payload，原口径）
    svc = StoryStateService(db_path)
    project_id = svc._project_id_for_chapter(  # noqa: SLF001
        get_connection(db_path), chapter_id
    )
    if project_id is None:
        raise ValueError(f"chapter {chapter_id!r} not found")
    current_state = svc.get_current_state(project_id)
    previous_state_version = int(current_state.get("state_version") or 1)

    # 第一次：基于 _observer_node 注入的 observer_payload 构造 delta → validate
    delta = _build_delta(
        observer_payload,
        chapter_id=chapter_id,
        run_id=run_id,
        previous_state_version=previous_state_version,
    )
    errors = validate_delta(delta)
    if errors:
        # 构造重试 payload（在 observer_input 副本上注入 _retry_hint）
        retry_payload = dict(ctx.get("observer_input") or {})
        retry_payload["_retry_hint"] = _OBSERVER_RETRY_HINT_TEMPLATE.format(
            errors="; ".join(errors)
        )
        # mock_script list 模式：第二次取下一条以让 MockProvider 返回不同响应。
        # 注意 MockProvider(scripted=str) 会把 str 当 iterable 取字符（runner 测试通用行为），
        # 因此弹出的 str 必须用 list[str]（单元素）包一层，与 deconstruct_book T2 retry
        # 处理一致。
        original_mock_script = (ctx.get("mock_providers") or {}).get("observer")
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
            node_run_id=ctx.get("_current_node_run_id"),
            expected="observer",
            mock_script=retry_mock_script,
        )
        # 第二次：基于 retry 后 observer_payload 重建 delta → 再次 validate
        delta = _build_delta(
            observer_payload,
            chapter_id=chapter_id,
            run_id=run_id,
            previous_state_version=previous_state_version,
        )
        errors = validate_delta(delta)
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


# ============================================================================
# Sprint 6 下半：quality_gate 节点
# ============================================================================


def _quality_gate_mode(ctx: dict[str, Any]) -> str:
    """解析 quality_gate 阻断模式。

    优先级（与现有 NOVELOS_* 环境变量口径对齐）：
    - ``ctx["quality_gate_mode"]``（调用方 / 测试用例可显式注入）。
    - ``NOVELOS_QUALITY_GATE`` 环境变量（``"enforce"`` / ``"report"``，默认 ``"report"``）。
    """
    mode = ctx.get("quality_gate_mode") or os.environ.get("NOVELOS_QUALITY_GATE", "report")
    mode = str(mode).strip().lower()
    return mode if mode in {"enforce", "report"} else "report"


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


def _summarize_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """summarize 节点（Sprint 14）—— commit 成功后追加。

    行为：
    1. 取最新 draft content（已 commit 的草稿正文）；若为空 → summary_status='skipped'。
    2. tail_text = content[-300:]（不调 LLM）。
    3. 调 ``run_agent(..., agent_name='summarizer', mock_script=...)`` 输出 JSON ``{"summary": "..."}``。
    4. 写 ``chapter_summaries`` 行（summary_id / project_id / chapter_id / chapter_no /
       summary ≤ 200 字 / tail_text / created_at）；摘要超长则截断并标记 degraded。
    5. 任何异常（LLM 失败 / JSON 解析失败 / DB 写入失败）→ 降级：warning 日志 +
       summary_status='failed' + 不抛错（保证 chapter 提交不因摘要失败而 FAILED）。

    返回 ``{"summary_status": "ok|failed|skipped", "summary_id": str|None, "degraded": bool}``。
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
            return {"summary_status": "skipped", "summary_id": None, "degraded": False}
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
    # sqlite3.Row 不支持 .get；用 dict(draft_row) 兜底（draft_row 为 None 时返回空 dict）
    content = (dict(draft_row) if draft_row else {}).get("content") or ""
    if not content.strip():
        return {"summary_status": "skipped", "summary_id": None, "degraded": False}

    # 2) tail_text（不调 LLM）
    tail_text = content[-_TAIL_TEXT_CHARS:] if len(content) > _TAIL_TEXT_CHARS else content

    # 3) 构造输入 payload + 调 summarizer
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
            import json as _json
            raw = pj["plan_json"] or "{}"
            try:
                pj_d = _json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (TypeError, ValueError):
                pj_d = {}
            plan_goal = (pj_d or {}).get("chapter_goal") or ""
    except Exception:  # noqa: BLE001 —— 计划读取失败不阻塞 summarize
        plan_goal = ""

    summary_payload = {
        "agent": "summarizer",
        "prompt_version": "summarizer:v1",
        "chapter": {
            "chapter_id": chapter_id,
            "chapter_no": chapter_no,
            "chapter_goal": plan_goal,
        },
        "prose_excerpt": content[:4000],  # 取前 4000 字足够上下文（避免超长 prompt）
        "tail_text": tail_text,
    }

    degraded = False
    summary_text = ""
    summary_status = "failed"
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
        if not isinstance(out, dict):
            raise ValueError(f"summarizer output not dict: {type(out).__name__}")
        candidate = out.get("summary")
        if not isinstance(candidate, str):
            raise ValueError("summarizer output missing 'summary' string")
        summary_text = candidate.strip()
        if not summary_text:
            raise ValueError("summarizer output 'summary' empty")
        if len(summary_text) > _SUMMARY_MAX_CHARS:
            summary_text = summary_text[:_SUMMARY_MAX_CHARS]
            degraded = True
        summary_status = "ok"
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
    summary_id = new_id("sum")
    try:
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
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "chapter_commit.summarize DB insert failed: summary_id=%s err=%s",
            summary_id, exc,
        )
        return {
            "summary_status": "failed",
            "summary_id": None,
            "degraded": False,
            "summary_error": str(exc),
        }

    return {
        "summary_status": summary_status,
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
    ],
}


# 注意（Sprint V1.5）：注册动作统一在 :mod:`packages.workflows.chapter_commit.__init__`
# 调用 :func:`packages.core.workflow_registry.register_workflow`；本模块不再暴露
# ``register_workflow`` 函数。


__all__ = ["WORKFLOW"]
