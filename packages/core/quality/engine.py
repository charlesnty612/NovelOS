"""QualityEngine 主入口（packages.core.quality.engine）。

编排顺序（确保 guardrail 先写 issues、subscore 后算 continuity 用到 issues）：

1. schema_validity(delta)
2. character_contradiction(snapshot, delta)
3. world_rule_contradiction(snapshot, delta)
4. timeline_consistency(snapshot, delta)
5. knowledge_leakage(snapshot, delta)
6. compliance: req_q6 / req_q7 / req_q8
7. payoff (H-1~H-5)
8. score_plot / score_character / score_continuity(已收集 issues) /
   score_style / score_pacing / score_foreshadowing
9. compute_overall(subscores, issues)
10. 组装 QualityReport

约束：
- 全部纯函数：无文件 IO（除 ``validate_delta`` 内部读 schema 路径）、无 DB、无网络。
- 不修改输入对象（ctx / ctx 中的 dict）。但 ``compute_overall`` 会原地追加 issues 到
  我们维护的 list；这是引擎自有副本，对外行为可控。
"""

from __future__ import annotations

from packages.core.ids import new_id, now_iso

from .aggregate import compute_overall
from .ai_trace import compute_ai_trace
from .guardrails import (
    character_contradiction,
    knowledge_leakage,
    req_q6,
    req_q7,
    req_q8,
    schema_validity,
    timeline_consistency,
    world_rule_contradiction,
)
from .issues import Issue
from .models import QualityContext, QualityReport
from .payoff import PayoffContext
from .payoff import evaluate as payoff_evaluate
from .scoring import (
    score_character,
    score_continuity,
    score_foreshadowing,
    score_pacing,
    score_plot,
    score_style,
)
from .style_trend import check_style_trend


class QualityEngine:
    """Quality Engine 纯核心。

    使用示例：

    .. code-block:: python

        engine = QualityEngine()
        report = engine.evaluate(
            QualityContext(
                chapter_id="ch_001",
                chapter_number=1,
                draft="...",
                plan={"key_beats": [...]},
                snapshot_pre={...},
                delta={...},
            )
        )
        print(report.overall, len(report.issues))

    类本身无状态；实例化仅为对齐 service-style 入口惯例。
    """

    def evaluate(self, ctx: QualityContext) -> QualityReport:
        issues: list[Issue] = []

        # ---- Guardrails（顺序固定，先于子分）----
        issues.extend(schema_validity(ctx.delta or {}))
        issues.extend(character_contradiction(ctx.snapshot_pre, ctx.delta or {}))
        issues.extend(world_rule_contradiction(ctx.snapshot_pre, ctx.delta or {}))
        issues.extend(timeline_consistency(ctx.snapshot_pre, ctx.delta or {}))
        issues.extend(knowledge_leakage(ctx.snapshot_pre, ctx.delta or {}))

        # ---- Compliance（REQ-Q6 / Q7 / Q8）----
        issues.extend(req_q6(ctx.draft or "", ctx.reference_texts, ctx.whitelist))
        issues.extend(req_q7(ctx.draft or ""))
        issues.extend(req_q8(ctx.ai_chars, ctx.human_chars))

        # ---- Payoff（H-1~H-5，独立 push issues）----
        payoff_ctx = PayoffContext(
            chapter_number=ctx.chapter_number,
            draft=ctx.draft or "",
            payoff_history=list(ctx.payoff_history or []),
            snapshot=ctx.snapshot_pre or {},
            delta=ctx.delta or {},
        )
        issues.extend(payoff_evaluate(payoff_ctx))

        # ---- 六子分（rule-based）----
        plot_score, plot_issues = score_plot(ctx.plan or {}, ctx.delta or {})
        issues.extend(plot_issues)

        char_score, char_issues = score_character(ctx.snapshot_pre, ctx.delta or {})
        issues.extend(char_issues)

        # continuity 子分用上所有已收集的 guardrail issues
        cont_score, _ = score_continuity(issues)

        style_score, style_issues = score_style(ctx.draft or "")
        issues.extend(style_issues)

        pacing_score, pacing_issues = score_pacing(ctx.draft or "")
        issues.extend(pacing_issues)

        fore_score, fore_issues = score_foreshadowing(ctx.snapshot_pre, ctx.delta or {})
        issues.extend(fore_issues)

        # ---- ai_trace 子分（章内/跨章重复 + AI 套话命中；详见 ai_trace.py）----
        ai_trace_score, _ = compute_ai_trace(
            ctx.draft or "",
            list(ctx.previous_drafts or []),
        )

        # ---- M2-C style 跨章趋势监测（报告型；不参与 subscores / overall / formula_hash）----
        # ctx.recent_style_scores 默认 None：不传则完全短路，零影响。
        trend_issue = check_style_trend(ctx.recent_style_scores, chapter_id=ctx.chapter_id)
        if trend_issue is not None:
            issues.append(trend_issue)

        # ---- 聚合（compute_overall 会原地补 missing + error 阻断）----
        subscores = {
            "plot": plot_score,
            "character": char_score,
            "continuity": cont_score,
            "style": style_score,
            "pacing": pacing_score,
            "foreshadowing": fore_score,
            "ai_trace": ai_trace_score,
        }
        overall, issues = compute_overall(subscores, issues)

        # ---- _meta（§2.2 固定 4 字段 + report_id）----
        from .aggregate import formula_hash  # 局部延迟导入，避免循环

        meta = {
            "scoring_version": "quality-scoring-v0",
            "llm_judge": "deferred",
            "evaluated_at": now_iso(),
            "scoring_formula_hash": formula_hash(),
        }

        return QualityReport(
            report_id=new_id("qr"),
            overall=overall,
            plot=plot_score,
            character=char_score,
            continuity=cont_score,
            style=style_score,
            pacing=pacing_score,
            foreshadowing=fore_score,
            ai_trace=ai_trace_score,
            issues=issues,
            meta=meta,
            chapter_id=ctx.chapter_id,
            chapter_number=ctx.chapter_number,
            commit_id=ctx.commit_id,
            run_id=ctx.run_id,
        )


__all__ = ["QualityEngine"]
