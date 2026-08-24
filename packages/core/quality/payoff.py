"""爽感维度 H-1~H-5（packages.core.quality.payoff）。

对齐 ``docs/evaluation/quality-scoring-v0.md`` §3.7，男频爽感体检报告。
**MVP 收窄**：H-1~H-5 全部 warning，仅 H-3 连续 ≥3 章与 H-5 越级碾压可升 error；
本文件按 H-1~H-5 命中条件返回对应 severity。

调用签名：

- :func:`evaluate(ctx) -> list[Issue]`，ctx 是 SimpleNamespace 或 dict，
  字段：``draft / chapter_number / payoff_history / snapshot / delta``。
- 全部为纯函数；不在子分中再额外写 issue（与 guardrails / scoring 无关）。

越级碾压（H-5 升级 error 路径）MVP 不实现——只实现境界名词一致性 warning。
"""

from __future__ import annotations

from dataclasses import dataclass

from .guardrails import CONFLICT_MARKERS, HOOK_MARKERS, REALM_KEYWORDS, _paragraphs, _world_rules_by_name
from .issues import Issue, make_issue

# ============================================================================
# 上下文（轻量；不强制使用 pydantic）
# ============================================================================


@dataclass
class PayoffContext:
    """爽感评估的输入快照。

    字段：
        chapter_number: 章节序号。
        draft: 章节正文。
        payoff_history: 最近若干章每章 payoff 计数（resolved + paid 之和）；
            末尾元素可不代表本章（由调用方对齐惯例）。
        snapshot: 提交前 Canonical State dict（用于境界实体查找）。
        delta: 本章 Observer Delta dict。
    """

    chapter_number: int
    draft: str
    payoff_history: list[int]
    snapshot: dict
    delta: dict


# ============================================================================
# helper
# ============================================================================


def _tail(text: str, n: int) -> str:
    if not text:
        return ""
    return text[-n:]


def _head(text: str, n: int) -> str:
    if not text:
        return ""
    return text[:n]


def _contains_any(text: str, markers) -> bool:
    return any(m in (text or "") for m in markers)


def _delta_paid_debt_count(delta: dict) -> int:
    """本章 payoff 中"paid debts" = debt_changes 中 status_after == 'paid' 的条数。"""
    if not isinstance(delta, dict):
        return 0
    debts = delta.get("debt_changes") or []
    if not isinstance(debts, list):
        return 0
    return sum(
        1
        for d in debts
        if isinstance(d, dict) and d.get("status_after") == "paid"
    )


def _delta_resolved_hooks(delta: dict) -> int:
    if not isinstance(delta, dict):
        return 0
    rh = delta.get("resolved_hooks") or []
    if not isinstance(rh, list):
        return 0
    return len(rh)


def _has_payoff_this_chapter(ctx: PayoffContext) -> bool:
    """本章 delta 是否含 payoff（resolved_hooks 或 paid debts）。"""
    return _delta_resolved_hooks(ctx.delta) > 0 or _delta_paid_debt_count(ctx.delta) > 0


def _is_realm_name(name: str) -> bool:
    return any(k in (name or "") for k in REALM_KEYWORDS)


# ============================================================================
# H-1~H-5
# ============================================================================


def _h1_end_hook(ctx: PayoffContext) -> list[Issue]:
    """H-1：末 200 字含钩子标记词 ⇒ pass；否则 warning。"""
    issues: list[Issue] = []
    tail = _tail(ctx.draft or "", 200)
    if not _contains_any(tail, HOOK_MARKERS):
        issues.append(
            make_issue(
                severity="warning",
                category="payoff",
                rule_id="RULE_H1_NO_END_HOOK",
                message="末 200 字未命中任何钩子标记",
            )
        )
    return issues


def _h2_no_climax_3ch(ctx: PayoffContext) -> list[Issue]:
    """H-2：最近 3 章 payoff 全 0 且本章无 resolved/debt paid ⇒ warning。"""
    issues: list[Issue] = []
    hist = list(ctx.payoff_history or [])
    last3 = hist[-3:]
    if len(last3) == 3 and all(h == 0 for h in last3) and not _has_payoff_this_chapter(ctx):
        issues.append(
            make_issue(
                severity="warning",
                category="payoff",
                rule_id="RULE_H2_NO_CLIMAX_3CH",
                message="最近 3 章 payoff 全 0 且本章无高潮点",
            )
        )
    return issues


def _h3_filler(ctx: PayoffContext) -> list[Issue]:
    """H-3：连续 2 章 0 payoff ⇒ warning；连续 ≥3 章 ⇒ error。"""
    issues: list[Issue] = []
    hist = list(ctx.payoff_history or [])
    has_now = _has_payoff_this_chapter(ctx)
    streak = 0
    # 从末尾倒推：遇到首个 >0 即停；本章若 has_now 则不计入 streak
    cur_has = has_now
    for h in reversed(hist):
        if h == 0 and not cur_has:
            streak += 1
        else:
            break
        cur_has = False

    if streak >= 3:
        issues.append(
            make_issue(
                severity="error",
                category="payoff",
                rule_id="RULE_H3_FILLER_3CH",
                message=f"连续 {streak}+ 章 payoff 为 0",
            )
        )
    elif streak >= 2:
        issues.append(
            make_issue(
                severity="warning",
                category="payoff",
                rule_id="RULE_H3_FILLER_2CH",
                message=f"连续 {streak} 章 payoff 为 0",
            )
        )
    return issues


def _h4_golden_three(ctx: PayoffContext) -> list[Issue]:
    """H-4：仅前 3 章启用。前 300 字冲突词 / 末段钩子 / chapter==3 三章 payoff。"""
    issues: list[Issue] = []
    n = int(ctx.chapter_number or 0)
    if n not in (1, 2, 3):
        return issues

    head300 = _head(ctx.draft or "", 300)
    if not _contains_any(head300, CONFLICT_MARKERS):
        issues.append(
            make_issue(
                severity="warning",
                category="payoff",
                rule_id="RULE_H4_NO_EARLY_CONFLICT",
                message="前 300 字未命中任何冲突词（H-4 黄金三章）",
            )
        )

    paras = _paragraphs(ctx.draft or "")
    last = paras[-1] if paras else ""
    if last and not _contains_any(last, HOOK_MARKERS):
        issues.append(
            make_issue(
                severity="warning",
                category="payoff",
                rule_id="RULE_H4_NO_HOOK",
                message="前 3 章末段无钩子（H-4）",
            )
        )

    if n == 3:
        hist = list(ctx.payoff_history or [])
        last3 = hist[-3:]
        if len(last3) == 3 and all(h == 0 for h in last3) and not _has_payoff_this_chapter(ctx):
            issues.append(
                make_issue(
                    severity="warning",
                    category="payoff",
                    rule_id="RULE_H4_NO_CLIMAX",
                    message="黄金三章内首个小高潮未触发（H-4）",
                )
            )
    return issues


def _h5_realm_consistency(ctx: PayoffContext) -> list[Issue]:
    """H-5：境界类实体（delta 新增 / 修改）与 snapshot 同名实体 statement 不一致 ⇒ warning。

    MVP 不实现越级碾压（error 升级路径）。README 声明。
    """
    issues: list[Issue] = []
    if not isinstance(ctx.delta, dict):
        return issues
    rules_by_name = _world_rules_by_name(ctx.snapshot or {})

    world_changes = ctx.delta.get("world_changes") or []
    if not isinstance(world_changes, list):
        return issues

    for ch in world_changes:
        if not isinstance(ch, dict):
            continue
        op = ch.get("op")
        if op not in ("add", "update"):
            continue
        after = ch.get("after")
        if not isinstance(after, dict):
            continue
        name = (after.get("name") or ch.get("world_id") or "").strip()
        if not _is_realm_name(name):
            continue
        snap_rule = rules_by_name.get(name)
        if not isinstance(snap_rule, dict):
            continue
        snap_stmt = (snap_rule.get("statement") or "").strip()
        after_stmt = (after.get("statement") or "").strip()
        if not snap_stmt or not after_stmt:
            continue
        if snap_stmt != after_stmt:
            issues.append(
                make_issue(
                    severity="warning",
                    category="payoff",
                    rule_id="RULE_H5_REALM_INCONSISTENT",
                    message=f"境界 '{name}' 与快照 statement 不一致",
                    evidence_refs=[name],
                )
            )
    return issues


# ============================================================================
# evaluate 入口
# ============================================================================


def evaluate(ctx) -> list[Issue]:
    """爽感 H-1~H-5 综合评估，按规格返回 list[Issue]。"""
    if isinstance(ctx, dict):
        snapshot = ctx.get("snapshot") or {}
        delta = ctx.get("delta") or {}
        c = PayoffContext(
            chapter_number=int(ctx.get("chapter_number") or 0),
            draft=ctx.get("draft") or "",
            payoff_history=list(ctx.get("payoff_history") or []),
            snapshot=snapshot,
            delta=delta,
        )
    else:
        c = ctx

    issues: list[Issue] = []
    issues.extend(_h1_end_hook(c))
    issues.extend(_h2_no_climax_3ch(c))
    issues.extend(_h3_filler(c))
    issues.extend(_h4_golden_three(c))
    issues.extend(_h5_realm_consistency(c))
    return issues


__all__ = ["PayoffContext", "evaluate"]
