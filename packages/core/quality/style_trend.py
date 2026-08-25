"""Style 跨章趋势监测（packages.core.quality.style_trend）。

目的（M2-C 报告型新增，不改变既有评分路径）：

- ``score_style`` 只反映**单章** style 子分绝对水平；连续退化的告警靠人肉发现。
- 本模块提供纯函数 :func:`check_style_trend`，在 Quality Engine 内对最近若干章
  ``style`` 子分做轻量趋势监测：当**最近 3 章单调下降且累计降幅 >= 15 分**时，
  返回一条 ``severity="warning"`` 的 Issue。
- **约束**：纯报告型 —— 不参与 overall / subscores / formula_hash。

设计要点：

- 与 :mod:`.scoring` / :mod:`.engine` 解耦。``engine.evaluate`` 显式调用并把返回的
  Issue 追加到 issues 列表；调用方对新增字段 ``recent_style_scores`` 默认 None 时完全
  短路（不传即不检测），旧调用方零影响。
- 阈值与窗口（窗口=3、阈值=15）取自 M1 长跑报告 §2（``docs/evaluation/m1-long-run-report.md``）
  的退化样本口径；为常量写在模块顶部，便于后续调优时只改本文件。
- 边界：恰好 3 章、累计降幅恰好 15 分 → 触发（>=）。下降要求**严格单调**
  （``s[-3] > s[-2] > s[-1]``），平盘不触发。
"""

from __future__ import annotations

from typing import Optional

from .issues import Issue, make_issue

# ============================================================================
# 监测参数（M1 长跑报告 §2 退化样本口径）
# ============================================================================

WINDOW: int = 3
"""监测窗口大小（最近 N 章 style 子分做趋势判定；默认 3）。"""

DECAY_THRESHOLD: int = 15
"""累计降幅阈值（最近 WINDOW 章中首末差 >= 该值则触发；默认 15）。"""

RULE_ID: str = "RULE_STYLE_TREND_DECAY"
"""该规则统一 rule_id，便于报告侧 / 规则侧聚合。"""


# ============================================================================
# 公开 API
# ============================================================================


def check_style_trend(
    recent_style_scores: Optional[list[int]],
    *,
    chapter_id: Optional[str] = None,
) -> Optional[Issue]:
    """监测最近若干章 style 子分是否出现连续退化；触发则返回 Issue，否则返回 None。

    参数：
        recent_style_scores: 最近 N 章（章节号升序）的 style 子分序列；None / 空 / 长度
            < ``WINDOW`` → 不触发（返回 None）。
        chapter_id: 用于构造 Issue.location；缺省则退化 ``"<unknown>"``。

    返回：
        - 触发：一条 :class:`Issue`，``severity="warning"`` / ``category="style"``。
        - 未触发：``None``。

    触发条件（严格单调 + 累计降幅门槛）：
        ```
        s = recent_style_scores[-WINDOW:]    # 取最近 WINDOW 章
        s[0] > s[1] > s[2]                   # 严格单调下降（平盘不触发）
        s[0] - s[2] >= DECAY_THRESHOLD       # 累计降幅门槛
        ```
    """
    if recent_style_scores is None:
        return None
    if len(recent_style_scores) < WINDOW:
        return None

    s = list(recent_style_scores[-WINDOW:])
    # 严格单调下降 + 累计降幅门槛
    if not (s[0] > s[1] > s[2]):
        return None
    if (s[0] - s[2]) < DECAY_THRESHOLD:
        return None

    return make_issue(
        severity="warning",
        category="style",
        rule_id=RULE_ID,
        message=(
            f"style 子分近 {WINDOW} 章连续下降："
            f"{s[0]} -> {s[1]} -> {s[2]}（累计降幅 {s[0] - s[2]}，阈值 {DECAY_THRESHOLD}）"
        ),
        chapter_id=chapter_id,
    )


__all__ = [
    "WINDOW",
    "DECAY_THRESHOLD",
    "RULE_ID",
    "check_style_trend",
]
