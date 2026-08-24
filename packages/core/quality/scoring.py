"""六子分 rule-based 计算（packages.core.quality）。

对齐权威文档 ``docs/evaluation/quality-scoring-v0.md`` §3.1-§3.6。

> **MVP 收窄**：六个子分均只实现 rule-based 部分并归一化到 [0, 100]；
> **LLM judge 部分按规格标记 deferred**（不参与数值，仅 README / _meta 声明）。
> §3.5 pacing MVP 阶段无 Scene 张力曲线标注，使用简化代理实现（末段钩子 + 极差）。

每个 ``score_*`` 函数签名：

- 输入字段以纯参数传入；plan/snapshot/delta 都是 dict。
- 返回 ``tuple[int, list[Issue]]``：score 与新追加的 issues。
- 纯函数；不依赖 pydantic / engine。
"""

from __future__ import annotations

from .guardrails import (
    AI_MARKERS,
    HOOK_MARKERS,
    _dialogue_chars,
    _marker_count,
    _norm,
    _paragraphs,
    _sentence_lengths,
)
from .issues import Issue, make_issue

# ============================================================================
# 常量（建议值待校准）
# ============================================================================


# ---- plot (§3.1) -----
PLOT_BEAT_MISSING_DEDUCTION: int = 5
"""每条 key_beats 在 delta.new_events 中无任何子串命中即扣此分。"""

PLOT_UNAUTHORIZED_EVENT_DEDUCTION: int = 10
"""每条超出 plan 的 event 扣此分（阈值：超过 plan 数 + 2 即开始扣）。"""

PLOT_NO_PLAN_NEUTRAL: int = 85
"""plan 无 key_beats 时的中性分。"""

PLOT_EXTRA_SLACK: int = 2
"""len(new_events) > len(key_beats) + PLOT_EXTRA_SLACK 才算"超额"。"""

# ---- continuity (§3.3) -----
# 按 rule_id 给定扣分；按 1 章只扣 1 次；warning ×0.5。
_CONTINUITY_DEDUCTIONS: dict[str, float] = {
    "RULE_CHAR_DEAD_ACTIVE": 30.0,
    "RULE_CHAR_BEFORE_MISMATCH": 5.0,
    "RULE_WORLD_HARD_RULE_CHANGED": 30.0,
    "RULE_WORLD_SOFT_RULE_CHANGED": 15.0,
    "RULE_TIMELINE_NON_MONOTONIC": 25.0,
    "RULE_TIMELINE_NEGATIVE_DAY": 12.0,
    "RULE_KNOWLEDGE_LEAK": 25.0,
}
_CONTINUITY_CATEGORIES = frozenset(
    {
        "character_contradiction",
        "world_rule_contradiction",
        "timeline_consistency",
        "knowledge_leakage",
    }
)

# ---- style (§3.4) -----
STYLE_SENTENCE_LEN_RANGE: tuple[int, int] = (12, 28)
STYLE_SENTENCE_LEN_DEDUCTION: int = 10
STYLE_TRIGRAM_REPETITION_THRESHOLD: float = 0.08
STYLE_TRIGRAM_DEDUCTION: int = 15
STYLE_AI_MARKER_PER_KCHARS: float = 5.0
STYLE_AI_MARKER_DEDUCTION: int = 15
STYLE_DIALOGUE_RATIO_RANGE: tuple[float, float] = (0.15, 0.65)
STYLE_DIALOGUE_DEDUCTION: int = 5

# ---- pacing (§3.5) -----
PACING_FLAT_DEDUCTION: int = 20
PACING_NO_END_HOOK_DEDUCTION: int = 15
PACING_FLAT_RANGE_THRESHOLD: float = 0.05
PACING_PARAGRAPH_TARGET: int = 5

# ---- foreshadowing (§3.6) -----
FORESHADOW_NO_HOOKS_NEUTRAL: int = 85


# ============================================================================
# plot (§3.1)
# ============================================================================


def score_plot(plan: dict, delta: dict) -> tuple[int, list[Issue]]:
    """§3.1 plot 规则子分。

    起点 100；plan.key_beats 中每条未命中扣 5；超额事件（>key_beats + 2）每个扣 10。
    plan 无 key_beats ⇒ 85 中性 + info issue。
    """
    key_beats = (plan or {}).get("key_beats") if isinstance(plan, dict) else None
    new_events = (delta or {}).get("new_events") if isinstance(delta, dict) else None
    new_events = new_events if isinstance(new_events, list) else []

    issues: list[Issue] = []
    if not isinstance(key_beats, list) or not key_beats:
        issues.append(
            make_issue(
                severity="info",
                category="plot",
                rule_id="RULE_PLOT_NO_PLAN",
                message="plan 无 key_beats，返回中性分",
            )
        )
        return PLOT_NO_PLAN_NEUTRAL, issues

    # 构造新事件的命中语料：name + description 汇总为一个大字符串池
    event_corpus: list[str] = []
    for ev in new_events:
        if isinstance(ev, dict):
            name = ev.get("name") or ""
            desc = ev.get("description") or ""
            text = f"{name}\n{desc}"
            event_corpus.append(_norm(text))
    corpus_text = "\n".join(event_corpus)

    score = 100
    missing = 0
    for beat in key_beats:
        if not isinstance(beat, dict):
            continue
        summary = beat.get("summary")
        if not isinstance(summary, str) or not summary:
            continue
        if _norm(summary) not in corpus_text:
            missing += 1

    if missing:
        score -= PLOT_BEAT_MISSING_DEDUCTION * missing

    # 超额事件
    extra = max(0, len(new_events) - len(key_beats) - PLOT_EXTRA_SLACK)
    if extra:
        score -= PLOT_UNAUTHORIZED_EVENT_DEDUCTION * extra

    return max(0, int(score)), issues


# ============================================================================
# character (§3.2)
# ============================================================================


def score_character(snapshot: dict, delta: dict) -> tuple[int, list[Issue]]:
    """§3.2 character 子分（一半来自 rule-based；此处先实现 rule 部分）。

    score = 一致率 × 100；rate = (total - mismatch) / total。
    mismatch 与 guardrail 的 ``RULE_CHAR_BEFORE_MISMATCH`` 共用判定，不重复 push issue。
    """
    issues: list[Issue] = []
    if not isinstance(delta, dict):
        return 100, issues
    changes = delta.get("character_changes") or []
    if not isinstance(changes, list) or not changes:
        return 100, issues

    snap_char_by_id: dict[str, dict] = {}
    for c in (snapshot or {}).get("characters") or []:
        if isinstance(c, dict) and isinstance(c.get("character_id"), str):
            snap_char_by_id[c["character_id"]] = c

    total = 0
    mismatch = 0
    for ch in changes:
        if not isinstance(ch, dict):
            continue
        op = ch.get("op")
        if op != "update":
            continue
        before = ch.get("before")
        field = ch.get("field")
        cid = ch.get("character_id") or ch.get("target_id")
        if not (isinstance(field, str) and isinstance(cid, str)):
            continue
        snap_char = snap_char_by_id.get(cid)
        if not isinstance(snap_char, dict):
            continue
        snap_state = snap_char.get("current_state") or {}
        if field not in snap_state:
            continue
        total += 1
        if not isinstance(before, (str, int, float, bool)):
            continue
        if snap_state.get(field) != before:
            mismatch += 1

    if total == 0:
        return 100, issues
    rate = (total - mismatch) / total
    return max(0, int(rate * 100)), issues


# ============================================================================
# continuity (§3.3)
# ============================================================================


def score_continuity(issues_in: list[Issue]) -> tuple[int, list[Issue]]:
    """§3.3 continuity 子分。

    起点 100；按 rule_id 扣分（warning ×0.5、error 全额），同 rule_id 一章只扣一次。
    只看 guardrail 已写入的 issues；不重复 push 自己的 issue。
    """
    score = 100
    seen: set[str] = set()
    for it in issues_in or []:
        if not isinstance(it, Issue):
            continue
        cat = it.category
        if cat not in _CONTINUITY_CATEGORIES:
            continue
        rid = it.rule_id
        if rid in seen:
            continue
        if rid not in _CONTINUITY_DEDUCTIONS:
            continue
        seen.add(rid)
        full = _CONTINUITY_DEDUCTIONS[rid]
        amount = full if it.severity == "error" else full * 0.5
        score -= amount

    return max(0, int(score)), []


# ============================================================================
# style (§3.4)
# ============================================================================


def _trigram_repetition_rate(text: str) -> float:
    """返回 trigram 重复率 = ``(sum max(0, count-1)) / total_trigrams``。"""
    t = _norm(text)
    n = 3
    if len(t) < n:
        return 0.0
    counts: dict[str, int] = {}
    total = 0
    for i in range(len(t) - n + 1):
        s = t[i : i + n]
        counts[s] = counts.get(s, 0) + 1
        total += 1
    if total == 0:
        return 0.0
    extras = sum(c - 1 for c in counts.values() if c > 1)
    return extras / total


def _dialogue_ratio(draft: str) -> float:
    total = len(draft or "")
    if total == 0:
        return 0.0
    return _dialogue_chars(draft) / total


def _avg_sentence_length(draft: str) -> float:
    lens = _sentence_lengths(draft)
    if not lens:
        return 0.0
    return sum(lens) / len(lens)


def score_style(draft: str) -> tuple[int, list[Issue]]:
    """§3.4 style 子分（rule-based 部分）。"""
    issues: list[Issue] = []
    score = 100

    avg = _avg_sentence_length(draft or "")
    if avg > 0 and not (
        STYLE_SENTENCE_LEN_RANGE[0] <= avg <= STYLE_SENTENCE_LEN_RANGE[1]
    ):
        score -= STYLE_SENTENCE_LEN_DEDUCTION

    trigram_rate = _trigram_repetition_rate(draft or "")
    if trigram_rate > STYLE_TRIGRAM_REPETITION_THRESHOLD:
        score -= STYLE_TRIGRAM_DEDUCTION
        issues.append(
            make_issue(
                severity="warning",
                category="style",
                rule_id="RULE_STYLE_REPETITION_TRIGRAM",
                message=f"trigram 重复率 {trigram_rate:.2%} > {STYLE_TRIGRAM_REPETITION_THRESHOLD:.0%}",
            )
        )

    n_chars = len(draft or "")
    if n_chars > 0:
        marker_per_k = _marker_count(draft or "", AI_MARKERS) / (n_chars / 1000.0)
        if marker_per_k >= STYLE_AI_MARKER_PER_KCHARS:
            score -= STYLE_AI_MARKER_DEDUCTION

    dr = _dialogue_ratio(draft or "")
    if dr > 0 and not (
        STYLE_DIALOGUE_RATIO_RANGE[0] <= dr <= STYLE_DIALOGUE_RATIO_RANGE[1]
    ):
        score -= STYLE_DIALOGUE_DEDUCTION

    return max(0, int(score)), issues


# ============================================================================
# pacing (§3.5)
# ============================================================================


def _contains_hook(text: str) -> bool:
    return any(m in (text or "") for m in HOOK_MARKERS)


def _last_n_chars(s: str, n: int) -> str:
    if not s:
        return ""
    return s[-n:]


def score_pacing(draft: str) -> tuple[int, list[Issue]]:
    """§3.5 pacing 子分（MVP 代理实现）。

    - 5 段对话密度极差 < 0.05 ⇒ -20（平铺）。
    - 末段无钩子 ⇒ -15。
    - 段数 < 1 时不扣分（视为空文本）。
    """
    issues: list[Issue] = []
    if not draft:
        return 100, issues

    paras = _paragraphs(draft)
    if not paras:
        return 100, issues

    score = 100

    # 均分 5 段（按段落数；段落 < 5 时按段落数）
    n_seg = min(PACING_PARAGRAPH_TARGET, len(paras))
    seg_size = max(1, len(paras) // n_seg) if n_seg > 0 else 1
    densities: list[float] = []
    for i in range(n_seg):
        start = i * seg_size
        end = (i + 1) * seg_size if i < n_seg - 1 else len(paras)
        seg_text = "\n".join(paras[start:end])
        d = _dialogue_chars(seg_text) / max(1, len(seg_text))
        densities.append(d)

    if densities and (max(densities) - min(densities)) < PACING_FLAT_RANGE_THRESHOLD:
        score -= PACING_FLAT_DEDUCTION

    last_para = paras[-1] if paras else ""
    if last_para and not _contains_hook(last_para):
        score -= PACING_NO_END_HOOK_DEDUCTION

    return max(0, int(score)), issues


# ============================================================================
# foreshadowing (§3.6)
# ============================================================================


def score_foreshadowing(snapshot: dict, delta: dict) -> tuple[int, list[Issue]]:
    """§3.6 foreshadowing 子分。

    涉及钩子数 = ``len(new_hooks) + len(resolved_hooks)``；兑现率 = ``resolved / 涉及 * 100``。
    涉及 0 ⇒ 85 中性 + info。
    """
    issues: list[Issue] = []
    if not isinstance(delta, dict):
        return 100, issues

    new_hooks = delta.get("new_hooks") or []
    resolved = delta.get("resolved_hooks") or []
    if not isinstance(new_hooks, list):
        new_hooks = []
    if not isinstance(resolved, list):
        resolved = []

    involved = len(new_hooks) + len(resolved)
    if involved == 0:
        issues.append(
            make_issue(
                severity="info",
                category="foreshadowing",
                rule_id="RULE_FORESHADOW_NO_HOOKS",
                message="本章未涉及任何钩子",
            )
        )
        return FORESHADOW_NO_HOOKS_NEUTRAL, issues

    rate = len(resolved) / involved
    return max(0, int(rate * 100)), issues


__all__ = [
    "score_plot",
    "score_character",
    "score_continuity",
    "score_style",
    "score_pacing",
    "score_foreshadowing",
]
