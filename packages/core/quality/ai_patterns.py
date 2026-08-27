"""AI 味/AI 腔确定性检测（去 AI 味）。

纯函数模块，不调用 LLM，全部基于正则与统计。规则对标 oh-story 项目的
``check-ai-patterns.js`` 思路，针对中文长篇小说的章节审校场景做了本地化扩展。

主要 API：
- :func:`scan_ai_patterns`：扫描正文，返回命中规则列表。
- :data:`AI_PATTERN_RULES` / :data:`AI_PATTERN_FORBIDDEN_WORDS`：模块级规则数据，便于扩展。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from packages.core.quality.wordcount import visible_chars

# ---------------------------------------------------------------------------
# 模块级规则数据（易扩展）
# ---------------------------------------------------------------------------

# 向后兼容：原 chapter_review.pipeline.DEFAULT_FORBIDDEN_WORDS 中的词必须包含在内
AI_PATTERN_FORBIDDEN_WORDS: list[str] = [
    # 原有默认禁用词
    "仿佛",
    "如同",
    "本章目标",
    # 新增 AI 高频套话/腔调词
    "宛如",
    "似乎",
    "好像",
    "不禁",
    "骤然",
    "猛然",
    "忽然",
    "突然",
    "与此同时",
    "值得一提的是",
    "总而言之",
    "总的来说",
    "综上所述",
    "空气中弥漫着",
    "冥冥之中",
]

# 章尾总结/升华体套话（只在最后一段检测）
_AI_ENDING_SUMMARY_PHRASES: list[str] = [
    "这一刻",
    "从此以后",
    "新的篇章",
    "命运的齿轮",
    "故事的结局",
    "画上了句号",
    "落下了帷幕",
    "一切都结束了",
    "新的旅程",
    "未来可期",
    "时光荏苒",
    "岁月如梭",
]

# 解释腔连接词/句式
_AI_EXPLAIN_PATTERNS: list[tuple[str, str | None]] = [
    # (regex, 简化标签)
    (r"因为[^。]{0,30}所以", "因为……所以……"),
    (r"换句话说", None),
    (r"也就是说", None),
    (r"换言之", None),
    (r"究其原因", None),
    (r"这意味着", None),
    (r"不难发现", None),
    (r"众所周知", None),
]

DEFAULT_DASH_THRESHOLD_PER_1K = 6

# 命中数超阈值时 severity 由 warning 升级为 error
_SEVERITY_UPGRADE_LIMITS: dict[str, int | float] = {
    "AI-FORBIDDEN-WORD": 10,  # 总命中次数
    "AI-EXPLAIN-TONE": 5,
    "AI-PRONOUN-PILE": 5,
    "AI-TRIPLET-OPENING": 5,
}


@dataclass(frozen=True)
class AiPatternRule:
    """单条规则元数据，用于文档化与可扩展配置。"""

    rule_id: str
    severity: str  # 'warning' | 'error'，默认等级
    message: str
    description: str = ""


AI_PATTERN_RULES: list[AiPatternRule] = [
    AiPatternRule(
        rule_id="AI-FORBIDDEN-WORD",
        severity="warning",
        message="AI 高频套话/禁用词命中：{words}",
        description="检测仿佛、宛如、不禁、骤然、与此同时、值得一提的是、总而言之、空气中弥漫着等高频 AI 腔词。",
    ),
    AiPatternRule(
        rule_id="AI-TRIPLET-OPENING",
        severity="warning",
        message="连续 {count} 句以相同两字词「{word}」开头，疑似句式套路",
        description="连续 3 句及以上以同一个两字词开头。",
    ),
    AiPatternRule(
        rule_id="AI-PRONOUN-PILE",
        severity="warning",
        message="「他/她」主语排比堆砌 {count} 句",
        description="连续 3 句及以上以「他」或「她」开头，提示主语单调。",
    ),
    AiPatternRule(
        rule_id="AI-ENDING-SUMMARY",
        severity="warning",
        message="章尾出现总结/升华体套话：{words}",
        description="结尾段含这一刻、从此以后、新的篇章、命运的齿轮等。",
    ),
    AiPatternRule(
        rule_id="AI-PUNCT-ABUSE",
        severity="warning",
        message="破折号/省略号滥用：每千字 {rate:.1f} 处（阈值 {threshold}）",
        description="每千字「——」或「……」超过阈值（默认 6）。",
    ),
    AiPatternRule(
        rule_id="AI-EXPLAIN-TONE",
        severity="warning",
        message="解释腔高密度：{count} 处（{labels}）",
        description="「因为……所以……」「换句话说」「也就是说」等解释连接词高频出现。",
    ),
]


# ---------------------------------------------------------------------------
# 内部 helpers
# ---------------------------------------------------------------------------


def _leading_chinese_bigram(sentence: str) -> str | None:
    """取句子开头（去空白/前导标点后）的前两个连续中文字符。"""
    cleaned = sentence.lstrip()
    if not cleaned:
        return None
    m = re.match(r"[\u4e00-\u9fff]{2}", cleaned)
    return m.group(0) if m else None


def _sentences(prose: str) -> list[str]:
    """按中文句末标点切分句子，过滤空句。"""
    parts = re.split(r"[。！？；\n]+", prose)
    return [p.strip() for p in parts if p.strip()]


def _last_paragraph(prose: str) -> str:
    """取正文最后一段（按空行分隔），若无空行则取全文。"""
    paragraphs = [p.strip() for p in prose.split("\n\n") if p.strip()]
    return paragraphs[-1] if paragraphs else prose


def _maybe_upgrade_severity(rule_id: str, count: int | float, base_severity: str) -> str:
    """命中数超过规则阈值时，把 warning 升级为 error。"""
    if base_severity != "warning":
        return base_severity
    limit = _SEVERITY_UPGRADE_LIMITS.get(rule_id)
    if limit is not None and count >= limit:
        return "error"
    return "warning"


# ---------------------------------------------------------------------------
# 各规则扫描器
# ---------------------------------------------------------------------------


def _scan_forbidden_words(prose: str) -> list[dict[str, Any]]:
    """AI 高频套话/禁用词扫描。"""
    words_found: list[str] = []
    total_count = 0
    for word in AI_PATTERN_FORBIDDEN_WORDS:
        cnt = prose.count(word)
        if cnt:
            words_found.append(word)
            total_count += cnt
    if not words_found:
        return []
    severity = _maybe_upgrade_severity("AI-FORBIDDEN-WORD", total_count, "warning")
    return [
        {
            "rule_id": "AI-FORBIDDEN-WORD",
            "severity": severity,
            "message": f"AI 高频套话/禁用词命中：{','.join(words_found)}",
            "count": total_count,
            "words": words_found,
            "excerpt": prose[:120],
        }
    ]


def _scan_triplet_opening(prose: str) -> list[dict[str, Any]]:
    """连续 3 句以上以相同两字词开头。"""
    sents = _sentences(prose)
    if len(sents) < 3:
        return []
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(sents) - 2:
        word = _leading_chinese_bigram(sents[i])
        if word is None:
            i += 1
            continue
        j = i + 1
        while j < len(sents):
            nxt = _leading_chinese_bigram(sents[j])
            if nxt == word:
                j += 1
            else:
                break
        run_len = j - i
        if run_len >= 3:
            severity = _maybe_upgrade_severity("AI-TRIPLET-OPENING", run_len, "warning")
            result.append(
                {
                    "rule_id": "AI-TRIPLET-OPENING",
                    "severity": severity,
                    "message": f"连续 {run_len} 句以相同两字词「{word}」开头，疑似句式套路",
                    "count": run_len,
                    "word": word,
                    "excerpt": " | ".join(sents[i:j])[:160],
                }
            )
            i = j
        else:
            i += 1
    return result


_PRONOUN_START_RE = re.compile(r"^[他她][\u4e00-\u9fff]")


def _scan_pronoun_pile(prose: str) -> list[dict[str, Any]]:
    """「他/她」开头句子排比堆砌。"""
    sents = _sentences(prose)
    if len(sents) < 3:
        return []
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(sents) - 2:
        if not _PRONOUN_START_RE.match(sents[i]):
            i += 1
            continue
        j = i + 1
        while j < len(sents) and _PRONOUN_START_RE.match(sents[j]):
            j += 1
        run_len = j - i
        if run_len >= 3:
            severity = _maybe_upgrade_severity("AI-PRONOUN-PILE", run_len, "warning")
            result.append(
                {
                    "rule_id": "AI-PRONOUN-PILE",
                    "severity": severity,
                    "message": f"「他/她」主语排比堆砌 {run_len} 句",
                    "count": run_len,
                    "excerpt": " | ".join(sents[i:j])[:160],
                }
            )
            i = j
        else:
            i += 1
    return result


def _scan_ending_summary(prose: str) -> list[dict[str, Any]]:
    """章尾总结/升华体套话检测。"""
    last_para = _last_paragraph(prose)
    if not last_para:
        return []
    words_found: list[str] = []
    for phrase in _AI_ENDING_SUMMARY_PHRASES:
        if phrase in last_para and phrase not in words_found:
            words_found.append(phrase)
    if not words_found:
        return []
    return [
        {
            "rule_id": "AI-ENDING-SUMMARY",
            "severity": "warning",
            "message": f"章尾出现总结/升华体套话：{','.join(words_found)}",
            "count": len(words_found),
            "words": words_found,
            "excerpt": last_para[-160:],
        }
    ]


_DASH_OR_ELLIPSIS_RE = re.compile(r"——|…{2,}")


def _scan_punct_abuse(
    prose: str, *, threshold_per_1k: int = DEFAULT_DASH_THRESHOLD_PER_1K
) -> list[dict[str, Any]]:
    """破折号/省略号滥用：按 visible_chars 计算每千字出现次数。"""
    matches = list(_DASH_OR_ELLIPSIS_RE.finditer(prose))
    if not matches:
        return []
    wc = max(1, visible_chars(prose))
    rate = len(matches) / (wc / 1000.0)
    if rate <= threshold_per_1k:
        return []
    base_severity = "error" if rate >= threshold_per_1k * 2 else "warning"
    return [
        {
            "rule_id": "AI-PUNCT-ABUSE",
            "severity": base_severity,
            "message": f"破折号/省略号滥用：每千字 {rate:.1f} 处（阈值 {threshold_per_1k}）",
            "count": len(matches),
            "rate": round(rate, 2),
            "threshold": threshold_per_1k,
            "excerpt": prose[:120],
        }
    ]


def _scan_explain_tone(prose: str) -> list[dict[str, Any]]:
    """解释腔连接词/句式高密度检测。"""
    labels: list[str] = []
    total = 0
    for pattern, label in _AI_EXPLAIN_PATTERNS:
        found = list(re.finditer(pattern, prose))
        if found:
            total += len(found)
            if label and label not in labels:
                labels.append(label)
    if total == 0:
        return []
    severity = _maybe_upgrade_severity("AI-EXPLAIN-TONE", total, "warning")
    label_text = ",".join(labels) if labels else "解释连接词"
    return [
        {
            "rule_id": "AI-EXPLAIN-TONE",
            "severity": severity,
            "message": f"解释腔高密度：{total} 处（{label_text}）",
            "count": total,
            "labels": labels,
            "excerpt": prose[:120],
        }
    ]


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------


def scan_ai_patterns(
    prose: str,
    *,
    dash_threshold_per_1k: int = DEFAULT_DASH_THRESHOLD_PER_1K,
) -> list[dict[str, Any]]:
    """扫描正文中的 AI 味/AI 腔模式。

    返回命中列表，每条包含：
      - rule_id: 规则标识
      - severity: 'warning' | 'error'
      - message: 人类可读摘要
      - count?: 命中次数
      - excerpt?: 正文片段（用于定位）
      - 以及规则相关额外字段（words / word / rate / labels 等）

    全部为确定性正则/统计规则，不调用 LLM。
    """
    if not prose:
        return []
    hits: list[dict[str, Any]] = []
    hits.extend(_scan_forbidden_words(prose))
    hits.extend(_scan_triplet_opening(prose))
    hits.extend(_scan_pronoun_pile(prose))
    hits.extend(_scan_ending_summary(prose))
    hits.extend(_scan_punct_abuse(prose, threshold_per_1k=dash_threshold_per_1k))
    hits.extend(_scan_explain_tone(prose))
    return hits


__all__ = [
    "AI_PATTERN_FORBIDDEN_WORDS",
    "AI_PATTERN_RULES",
    "DEFAULT_DASH_THRESHOLD_PER_1K",
    "scan_ai_patterns",
]
