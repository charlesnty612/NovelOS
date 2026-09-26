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
9. ai_pattern_issues(draft) —— 模式级 AI 味规则（``scan_ai_patterns``；**只补 issues**，
   不参与任何子分；P0-1 接线见模块内注释块）
10. compute_overall(subscores, issues)
11. 组装 QualityReport

约束：
- 全部纯函数：无文件 IO（除 ``validate_delta`` 内部读 schema 路径）、无 DB、无网络。
- 不修改输入对象（ctx / ctx 中的 dict）。但 ``compute_overall`` 会原地追加 issues 到
  我们维护的 list；这是引擎自有副本，对外行为可控。
"""

from __future__ import annotations

from packages.core.ids import new_id, now_iso

from .aggregate import compute_overall_with_gates
from .ai_patterns import scan_ai_patterns
from .ai_trace import compute_ai_trace
from .continuity_taxonomy import DETERMINISTIC_CONTINUITY_RULE_IDS
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
from .issues import BLOCKING_RULES, Issue, issue_gate, make_issue, mvp_max_severity
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

# ---------------------------------------------------------------------------
# AI 模式级规则（ai_patterns）接进质量路径 —— P0-1 接线（2026-09-18）
# ---------------------------------------------------------------------------
# 缺陷（本次实测确认）：``ai_patterns.scan_ai_patterns`` 的命中此前**只**被
# ``chapter_review._basic_checks_node``（评审报告）与 chapter-write 的预检消费，
# ``QualityEngine.evaluate`` 从不调用它 —— 于是 ``issues.CONFIRM_RULES`` 里的
# ``AI-BEAT-REPEAT`` 在**提交链路**上是一条惰性声明：commit 走的
# quality_gate → QualityEngine → quality_reports 里永远不会有这条 rule_id，
# confirm 档「必须显式接受才放行」对它等于不存在（P0-1 事故要防的正是这类静默通过）。
# 本模块是质量路径的唯一汇聚点（报告落库 / 门禁 / API evaluate 都从这里出），
# 故接线放在这里，rule 侧无需改动。
#
# 与既有机制**不重复计数**（逐条核对过）：
#   - rule_id 无交集：``scoring.score_style`` 出的 ``RULE_STYLE_REPETITION_TRIGRAM``
#     量的是 trigram 重复率，``AI-BEAT-REPEAT`` 量的是「远距小句复现」，两条不同算子；
#   - style / ai_trace 的词表与套话信号走的是**子分**（``AI_FLAVOR_MARKERS`` /
#     ``AI_CLICHES`` 的密度阶梯扣分，不产 issue），本次新增的是**条目**（issues）。
#     子分与条目是两根轴：既不重复扣分（本函数不参与任何 score_*），也不重复列条目。
#
# 类别映射只给出「明显不属于 style」的规则，其余（含任何将来新增的规则）回退
# ``_AI_PATTERN_DEFAULT_CATEGORY`` —— **不硬编码新规则 id**，``scan_ai_patterns``
# 返回什么就接什么。
#
# 2026-09-21 检修 M2：``CONT-*``（章内时点矛盾）补上 ``continuity`` 映射。此前它落进
# 默认的 ``style``，与 ``continuity_taxonomy.DETERMINISTIC_CONTINUITY_RULE_IDS``
# 的命名空间声明直接矛盾（「名实不符」）——报告 / 前端按 category 分桶时，连续性命中
# 会跑到「文风」桶里。映射从该稳定名册**派生**（不在本文件手写第二份 id 清单），
# 新增确定性连续性规则时只需改 taxonomy 一处。
_AI_PATTERN_CATEGORY: dict[str, str] = {
    "AI-DIALOGUE-LOW": "pacing",   # 对话配比 → 节奏维度
    "AI-BEAT-REPEAT": "ai_trace",  # 章内远距小句复现 → AI 痕迹维度
    **{rid: "continuity" for rid in sorted(DETERMINISTIC_CONTINUITY_RULE_IDS)},
}
_AI_PATTERN_DEFAULT_CATEGORY = "style"

# 证据摘录的取值键（按序取，命中即止）：这两键承载「作者要看见自己在接受什么」。
# 都没有时退回 ``excerpt``；仍为空则不带证据（门禁侧会提示回看原文）。
_AI_PATTERN_EVIDENCE_KEYS: tuple[str, ...] = ("samples", "words")

_SEVERITY_RANK: dict[str, int] = {"info": 0, "warning": 1, "error": 2}


def _ai_pattern_severity(hit: dict) -> str:
    """单个 AI 模式命中的 severity（检测器档位，按 category 矩阵**封顶**）。

    - 检测器给出的档位不是三档之一 → ``warning``（防御）。
    - rule_id ∈ ``BLOCKING_RULES`` → **原样保留**（白名单规则的 severity 是阻断判据，
      不得被矩阵削掉——否则会出现「已入白名单但永远不阻断」的死规则）。
    - 其余 → 按 category 的 ``mvp_max_severity`` 封顶（style / pacing / ai_trace
      现均封顶 warning）。检测器在评审通道里的 error 档（如 AI-PUNCT-ABUSE 标点滥用
      ≥2×阈值、AI-LONG-PARA 单段 >140 字）在 ``review_report.errors`` 里照旧保留，
      本处只是让**质量报告**的 severity 与规格矩阵同口径（README §4.2）。
    """
    rule_id = str(hit.get("rule_id") or "")
    raw = hit.get("severity")
    if raw not in _SEVERITY_RANK:
        raw = "warning"
    if rule_id in BLOCKING_RULES:
        return raw
    category = _AI_PATTERN_CATEGORY.get(rule_id, _AI_PATTERN_DEFAULT_CATEGORY)
    cap = mvp_max_severity(category)
    return raw if _SEVERITY_RANK[raw] <= _SEVERITY_RANK[cap] else cap


def _ai_pattern_evidence(hit: dict) -> list[str] | None:
    """命中的可核对证据（``samples`` / ``words`` 优先，退回 ``excerpt``）。"""
    refs: list[str] = []
    for key in _AI_PATTERN_EVIDENCE_KEYS:
        vals = hit.get(key)
        if isinstance(vals, (list, tuple)):
            refs.extend(str(v) for v in vals if str(v).strip())
    if not refs:
        excerpt = hit.get("excerpt")
        if isinstance(excerpt, str) and excerpt.strip():
            refs.append(excerpt.strip())
    return refs or None


def ai_pattern_issues(draft: str, *, chapter_id: str | None = None) -> list[Issue]:
    """把 ``scan_ai_patterns`` 的命中转成 quality :class:`Issue` 列表。

    通用转换（不认具体 rule_id，除两张 map 的默认回退）：任何 ``scan_ai_patterns``
    返回的命中都会变成一条 issue，``rule_id`` 原样透传 —— 因此规则侧新增算子后
    **自动**进入 ``quality_reports`` 与提交门禁（``CONFIRM_RULES`` / ``BLOCKING_RULES``
    按 rule_id 判后果，与规则是否已交付无关）。
    """
    out: list[Issue] = []
    for hit in scan_ai_patterns(draft or "") or []:
        if not isinstance(hit, dict):
            continue
        rule_id = str(hit.get("rule_id") or "").strip()
        if not rule_id:
            continue
        category = _AI_PATTERN_CATEGORY.get(rule_id, _AI_PATTERN_DEFAULT_CATEGORY)
        out.append(
            make_issue(
                severity=_ai_pattern_severity(hit),  # type: ignore[arg-type]
                category=category,  # type: ignore[arg-type]
                rule_id=rule_id,
                message=str(hit.get("message") or rule_id),
                chapter_id=chapter_id,
                evidence_refs=_ai_pattern_evidence(hit),
            )
        )
    return out


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
        issues.extend(
            req_q8(ctx.ai_chars, ctx.human_chars, note=ctx.char_stats_note)
        )

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

        # ---- AI 模式级规则（ai_patterns；P0-1 接线，见模块顶部注释块）----
        # 位置：在六子分之后、聚合之前——只补 issues，不参与任何 score_*，
        # 故不影响子分与 overall（AI-* 均不在 BLOCKING_RULES）；后果由 issue_gate
        # 按 CONFIRM_RULES 判定（如 AI-BEAT-REPEAT）。
        issues.extend(ai_pattern_issues(ctx.draft or "", chapter_id=ctx.chapter_id))

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
        overall, issues, gate_summary = compute_overall_with_gates(subscores, issues)

        # P0-1（2026-09-18）：材料化每条 issue 的后果档，让落库报告里 ``gate`` 与解析器
        # ``issues.issue_gate`` 同口径（引擎是唯一汇聚点；规则侧无需各自填字段）。
        # 解析器仍是权威——这里只做回显，见 issues.issue_gate docstring。
        for _issue in issues:
            _issue.gate = issue_gate(_issue)

        # ---- _meta（§2.2 固定 4 字段 + report_id + P0-1 gate_summary）----
        from .aggregate import formula_hash  # 局部延迟导入，避免循环

        meta = {
            "scoring_version": "quality-scoring-v0",
            "llm_judge": "deferred",
            "evaluated_at": now_iso(),
            "scoring_formula_hash": formula_hash(),
            # P0-1：confirm / block 两类后果档的 rule_id 汇总，让「不许静默通过」
            # 在报告侧可见（前端 / API / 门禁都从这里取，不必重算）。
            "gate_summary": gate_summary.as_meta(),
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
            # P1-1：报告标签 = 它实际量过的那份正文的版本（ctx 里与正文同源带出）；
            # ``None``（调用方自行构造 ctx）时由 ``save_report`` 兜底补最新版本。
            draft_version=ctx.draft_version,
        )


__all__ = ["QualityEngine", "ai_pattern_issues"]
