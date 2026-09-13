"""题材包读侧投影（题材库 P2 消费点共用）。

职责：把 pack payload 投影成**消费点可直接消费的形态**，纯函数、无 IO、全容错
（读侧宽容：脏数据跳过，不抛错）。三个消费点共用本模块，避免各写一份口径：

- :func:`opening_rules` → signing_check 的题材开篇检查段（黄金三章特化规则）；
- :func:`critic_rubric` → chapter_review 的 critic / deep_review payload 的
  ``genre_rubric`` 段（文本化 + 字符预算，超限截断带标记）；
- :func:`forbidden_words` / :func:`count_forbidden_word_hits` → chapter_review
  ``basic_checks`` 的题材禁词确定性扫描（``[GENRE-FORBIDDEN-WORD] <词> ×N``）。

边界：本模块只做「结构化消费子集 → 消费形态」的投影，不读库（绑定读取由调用方
经 :class:`packages.core.genre.service.GenrePackService` 完成）、不写 payload、
不做题材内容判断——题材正文与审核禁忌清单只进内容仓。
"""

from __future__ import annotations

import json
from typing import Any, Final, Iterable

__all__ = [
    "CRITIC_RUBRIC_MAX_CHARS",
    "CRITIC_RUBRIC_TRUNCATED_KEY",
    "OPENING_RULE_MAX_CHAPTER",
    "count_forbidden_word_hits",
    "critic_rubric",
    "forbidden_words",
    "opening_rules",
]

# 黄金三章窗口上限（opening_rules[].chapter_no 允许范围 1~3，与 schema 对齐）。
OPENING_RULE_MAX_CHAPTER: Final[int] = 3

# genre_rubric 段的总字符预算（JSON 序列化长度）与截断标记键名
# （``__*_truncated__`` 是仓内既有约定：director / scene_planner 注入段的标记同款）。
CRITIC_RUBRIC_MAX_CHARS: Final[int] = 1000
CRITIC_RUBRIC_TRUNCATED_KEY: Final[str] = "__genre_rubric_truncated__"

# 文本被截断时的后缀（与 chapter_review 的 _WORLD_RULE_TRUNCATED_SUFFIX 同款口径）。
_TRUNCATED_SUFFIX: Final[str] = "…（已截断）"

# 禁词表读取上限（schema 侧 maxItems=50；旁路写入超限时读侧同样收紧）。
_FORBIDDEN_WORDS_CAP: Final[int] = 50


def _clean_text(value: Any) -> str:
    """非空字符串 → strip 后原值；其余（None / 数字 / 空串）→ ``""``。"""
    if not isinstance(value, str):
        return ""
    return value.strip()


def _json_len(value: Any) -> int:
    """JSON 序列化字符长度（不可序列化 → 一个不可能满足的大值）。"""

    try:
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return 1 << 30


def opening_rules(payload: Any) -> list[dict[str, Any]]:
    """payload.``opening_rules`` → 规范化规则列表（保序）。

    每条输出 ``{"check_id", "chapter_no", "description", "requirement"}``：
    ``chapter_no`` 为 ``None`` 表示规则在黄金三章（1~3 章）整体窗口上判定。
    非法条目（非 dict / 缺 ``check_id`` / 缺 ``requirement`` / ``chapter_no`` 出
    1~3 或非整数）整条跳过——读侧宽容，脏数据不炸 signing_check。
    """

    if not isinstance(payload, dict):
        return []
    raw_rules = payload.get("opening_rules")
    if not isinstance(raw_rules, list):
        return []
    rules: list[dict[str, Any]] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            continue
        check_id = _clean_text(raw.get("check_id"))
        requirement = _clean_text(raw.get("requirement"))
        if not check_id or not requirement:
            continue
        chapter_no: int | None = None
        raw_no = raw.get("chapter_no")
        if isinstance(raw_no, int) and not isinstance(raw_no, bool):
            if 1 <= raw_no <= OPENING_RULE_MAX_CHAPTER:
                chapter_no = raw_no
            else:
                continue
        elif raw_no is not None:
            continue
        rules.append(
            {
                "check_id": check_id,
                "chapter_no": chapter_no,
                "description": _clean_text(raw.get("description")),
                "requirement": requirement,
            }
        )
    return rules


def forbidden_words(payload: Any) -> list[str]:
    """payload.``style_constraints.forbidden_words`` → 去重词表（保序）。

    非字符串 / 空白项跳过；上限 :data:`_FORBIDDEN_WORDS_CAP`（与 schema maxItems 同口径）。
    缺席 / 非法 → ``[]``（消费方零行为变化）。
    """

    if not isinstance(payload, dict):
        return []
    style = payload.get("style_constraints")
    if not isinstance(style, dict):
        return []
    raw_words = style.get("forbidden_words")
    if not isinstance(raw_words, list):
        return []
    seen: set[str] = set()
    words: list[str] = []
    for raw in raw_words:
        word = _clean_text(raw)
        if not word or word in seen:
            continue
        seen.add(word)
        words.append(word)
        if len(words) >= _FORBIDDEN_WORDS_CAP:
            break
    return words


def count_forbidden_word_hits(
    text: str, words: Iterable[str],
) -> list[tuple[str, int]]:
    """题材禁词子串计数（与 ``quality.ai_flavor.marker_hits_per_kchars`` 同款子串口径）。

    返回 ``[(词, 次数)]``，仅含命中项，顺序 = 词表声明序（确定性）。
    空文本 / 空词表 → ``[]``。
    """

    if not text:
        return []
    hits: list[tuple[str, int]] = []
    for word in words:
        if not isinstance(word, str) or not word:
            continue
        count = text.count(word)
        if count > 0:
            hits.append((word, count))
    return hits


def _focus_line(type_id: str, payoff: dict[str, Any] | None) -> str:
    """爽点聚焦条目 → 文本（``type_id：name｜强度 S｜密度上限 …｜核销提示 …``）。"""

    if not isinstance(payoff, dict):
        # 引用未被 payoff_types 声明的 id：原样保留（仍可作为审查聚焦点）。
        return type_id
    parts: list[str] = [type_id]
    name = _clean_text(payoff.get("name"))
    if name and name != type_id:
        parts.append(name)
    strength = _clean_text(payoff.get("strength"))
    if strength:
        parts.append(f"强度 {strength}")
    density_cap = _clean_text(payoff.get("density_cap"))
    if density_cap:
        parts.append(f"密度上限 {density_cap}")
    interval = payoff.get("min_interval_chapters")
    if isinstance(interval, int) and not isinstance(interval, bool) and interval > 0:
        parts.append(f"同型间隔 {interval} 章")
    verify_hint = _clean_text(payoff.get("verify_hint"))
    if verify_hint:
        parts.append(f"核销提示 {verify_hint}")
    return "：".join([parts[0], "｜".join(parts[1:])]) if len(parts) > 1 else type_id


def _fit_text(base: dict[str, Any], key: str, value: str, max_chars: int) -> dict[str, Any] | None:
    """把 ``base[key] = value`` 压进预算：超限则截断文本 + 后缀；连空串都放不下 → None。"""

    candidate = {**base, key: value}
    if _json_len(candidate) <= max_chars:
        return candidate
    fit = {**base, key: _TRUNCATED_SUFFIX}
    if _json_len(fit) > max_chars:
        return None
    lo, hi = 0, len(value)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        probe = {**base, key: value[:mid] + _TRUNCATED_SUFFIX}
        if _json_len(probe) <= max_chars:
            lo = mid
        else:
            hi = mid - 1
    return {**base, key: value[:lo] + _TRUNCATED_SUFFIX}


def critic_rubric(
    payload: Any, *, max_chars: int = CRITIC_RUBRIC_MAX_CHARS,
) -> dict[str, Any] | None:
    """payload.``critic_rubric`` → critic / deep_review 的 ``genre_rubric`` 段。

    - ``payoff_focus``：引用的 ``type_id`` 逐条文本化（带上对应
      ``payoff_types`` 的 name / strength / density_cap / min_interval_chapters /
      verify_hint，供 critic 逐个核 ``verify_hint``）；未被 ``payoff_types`` 声明的
      id 原样保留；
    - ``taboo_notes`` / ``style_notes``：原文透传；
    - **总预算** ``max_chars``（JSON 序列化长度）：优先级 taboo_notes > style_notes >
      payoff_focus 条目；文本超限按字符截断加 :data:`_TRUNCATED_SUFFIX`，条目超限
      整条不注入；
    - 任何裁剪 / 丢弃 → 打 ``__genre_rubric_truncated__ = true``（仓内 ``__*_truncated__``
      同款标记约定）；
    - 段缺席 / 非法 / 三段皆空 → ``None``（调用方不注入该键，零行为变化）。
    """

    if not isinstance(payload, dict):
        return None
    section = payload.get("critic_rubric")
    if not isinstance(section, dict):
        return None

    focus_ids: list[str] = []
    raw_focus = section.get("payoff_focus")
    if isinstance(raw_focus, list):
        seen: set[str] = set()
        for raw in raw_focus:
            type_id = _clean_text(raw)
            if not type_id or type_id in seen:
                continue
            seen.add(type_id)
            focus_ids.append(type_id)

    taboo_notes = _clean_text(section.get("taboo_notes"))
    style_notes = _clean_text(section.get("style_notes"))
    if not focus_ids and not taboo_notes and not style_notes:
        return None

    payoff_index: dict[str, Any] = {}
    raw_payoffs = payload.get("payoff_types")
    if isinstance(raw_payoffs, list):
        for payoff in raw_payoffs:
            if isinstance(payoff, dict):
                tid = _clean_text(payoff.get("type_id"))
                if tid and tid not in payoff_index:
                    payoff_index[tid] = payoff

    limit = max(int(max_chars), 0)
    out: dict[str, Any] = {}
    truncated = False

    # 先放文本（taboo 优先），再逐条塞 payoff_focus。
    for key, value in (("taboo_notes", taboo_notes), ("style_notes", style_notes)):
        if not value:
            continue
        fitted = _fit_text(out, key, value, limit)
        if fitted is None:
            truncated = True
            continue
        if fitted[key] != value:
            truncated = True
        out = fitted

    focus_lines = [_focus_line(tid, payoff_index.get(tid)) for tid in focus_ids]
    kept_focus: list[str] = []
    for line in focus_lines:
        candidate = {**out, "payoff_focus": [*kept_focus, line]}
        if _json_len(candidate) > limit:
            truncated = True
            break
        kept_focus.append(line)
    if kept_focus:
        out["payoff_focus"] = kept_focus
    elif focus_lines:
        truncated = True

    if not out:
        # 预算小到连一段都放不下：仍返回空段（调用方按 None 处理会丢截断信号，
        # 故返回最小 dict + 截断标记，保证「超截断带标记」的标记不丢）。
        out = {"payoff_focus": []}
        truncated = True

    if truncated:
        out[CRITIC_RUBRIC_TRUNCATED_KEY] = True
        # 标记本身占预算：回退尾部内容直到装得下（标记优先于内容保留）。
        for key in ("style_notes", "taboo_notes"):
            if _json_len(out) <= limit:
                break
            out.pop(key, None)
        while kept_focus and _json_len(out) > limit:
            kept_focus.pop()
            out["payoff_focus"] = kept_focus
        if not kept_focus:
            out["payoff_focus"] = []
        if _json_len(out) > limit and len(out) > 1:
            out = {CRITIC_RUBRIC_TRUNCATED_KEY: True, "payoff_focus": []}
    return out
