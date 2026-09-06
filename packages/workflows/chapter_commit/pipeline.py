"""chapter-commit 工作流门面（2026-09-06 审查批次三拆分）。

历史上本文件是 2200+ 行的单一实现；现已按职责拆分为：

- ``pipeline_common``  特性开关 / mock 过滤 / 修订指导等共享 helper
- ``observer``         Observer 双腿节点与聚合
- ``gate``             质量门禁与高危审批节点
- ``commit``           delta 构建 / 校验注入 / commit 节点
- ``summary``          章节摘要节点

本文件保留工作流组装（``_build_nodes`` / ``WORKFLOW``）与**全部既有
顶层名的再导出**——任何 ``from packages.workflows.chapter_commit.pipeline
import X``（含测试引用的私有名）行为不变。
"""
from __future__ import annotations

from packages.core.workflow_runtime.engine import WorkflowNode

from .commit import (
    _build_delta,  # noqa: F401
    _commit_node,
    _inject_validate_node,
)
from .gate import (
    _high_risk_approval_node,
    _quality_gate_mode,  # noqa: F401
    _quality_gate_node,
)
from .observer import (
    _OBSERVER_RETRY_HINT_TEMPLATE,  # noqa: F401
    _aggregate_observer_split_meta,  # noqa: F401
    _build_observer_ctx_node,
    _extract_leg_payload,  # noqa: F401
    _merge_observer_legs,  # noqa: F401
    _observer_node,
    _pick_retry_mock,  # noqa: F401
    _run_observer_legs_in_parallel,  # noqa: F401
    _run_observer_with_summary_in_parallel,  # noqa: F401
    _summarize_character_for_leg_b,  # noqa: F401
    _summarize_faction_for_leg_b,  # noqa: F401
    _summarize_location_for_leg_b,  # noqa: F401
    _summarize_world_rule_for_leg_b,  # noqa: F401
    _trim_observer_input_for_leg,  # noqa: F401
)
from .pipeline_common import (
    _CATEGORY_REVISION_HINTS,  # noqa: F401
    _DEBT_OPEN_STATUSES,  # noqa: F401
    _HOOK_OPEN_STATUSES,  # noqa: F401
    _OBSERVER_ALL_ARRAYS,  # noqa: F401
    _OBSERVER_LEG_A_ARRAYS,  # noqa: F401
    _OBSERVER_LEG_A_SET,  # noqa: F401
    _OBSERVER_LEG_B_ARRAYS,  # noqa: F401
    _OBSERVER_LEG_B_SET,  # noqa: F401
    _REVISION_SCORE_THRESHOLD,  # noqa: F401
    _RULE_REVISION_HINTS,  # noqa: F401
    _build_revision_guidance,  # noqa: F401
    _classify_validator_errors_to_legs,  # noqa: F401
    _collect_resolvable_ids,  # noqa: F401
    _filter_mock_for_leg,  # noqa: F401
    _has_high_risk_change,  # noqa: F401
    _inject_resolvable_ids_into_config,  # noqa: F401
    _observer_parallel_enabled,  # noqa: F401
    _observer_split_enabled,  # noqa: F401
    _summary_parallel_enabled,  # noqa: F401
)
from .summary import (
    _SUMMARY_MAX_CHARS,  # noqa: F401
    _TAIL_TEXT_CHARS,  # noqa: F401
    _insert_chapter_summary_row,  # noqa: F401
    _prepare_summarizer_call,  # noqa: F401
    _resolve_summary_out,  # noqa: F401
    _summarize_node,
)


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


__all__ = ["WORKFLOW"]
