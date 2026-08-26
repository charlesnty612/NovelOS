"""章节字数度量与字数带（V3.7）。

口径权威定义：**visible_chars** = 去除所有空白字符后的字符数
（与 :func:`packages.core.signing_check.checks._count_chars` 同式）。

全仓 prose 字数一律引用本模块，避免出现 "len(prose)"、"len(text.replace(...))"
等口径漂移。band 计算与三态分类由 :func:`word_band` / :func:`classify_prose_length`
统一出口。
"""

from __future__ import annotations

from typing import Any

# writer 纪律 Rule 15：计划低于此值时下限保护到 1200。
_MIN_BAND_FLOOR = 1200
# warning 阈值（偏离 target 比例绝对值超过此值即升 warning）。
_WARNING_RATIO = 0.15
# error 阈值（偏离 target 比例绝对值超过此值即记 error；与 band 外缘对齐）。
_ERROR_RATIO = 0.30


def visible_chars(text: str) -> int:
    """正文可见字符数（剔除所有空白）。

    与 :func:`packages.core.signing_check.checks._count_chars` 同式：
    用 ``"".join(text.split())`` 把任意空白序列（空格/换行/制表/回车）折叠
    后取长度。``text=None`` / 空串 → 0。
    """
    return len("".join((text or "").split()))


def word_band(
    target_word_count: int,
    *,
    low_ratio: float = 1 - _WARNING_RATIO,
    high_ratio: float = 1 + _WARNING_RATIO,
    floor: int = _MIN_BAND_FLOOR,
) -> tuple[int, int]:
    """返回 ``(band_low, band_high)``。

    低带受 :data:`_MIN_BAND_FLOOR` 保护（对齐 writer 纪律 Rule 15：计划低于
    1200 字时以 1200 为下限）。若 floor 抬升后导致 ``low > high``（如
    target=1000 时 raw_low=850 经 floor=1200 抬升，但 raw_high=1150 仍 < 1200），
    则把 high 同步抬到 ``max(high, low)``，保证 ``low <= high`` 单调性，避免
    下游按 band 判定「字数合理区间」时出现空集或负区间。``target<=0`` 返回
    ``(floor, floor)``（保底，避免除零）。
    """
    if target_word_count <= 0:
        return floor, floor
    low = max(int(target_word_count * low_ratio), floor)
    high = int(target_word_count * high_ratio)
    # 保单调：floor 抬升后 high 不能低于 low
    if high < low:
        high = low
    return low, high


def classify_prose_length(
    prose: str,
    target_word_count: int,
    *,
    low_ratio: float = 1 - _WARNING_RATIO,
    high_ratio: float = 1 + _WARNING_RATIO,
    floor: int = _MIN_BAND_FLOOR,
) -> dict[str, Any]:
    """一字数三态分类。

    返回字段：

    - ``visible_chars`` (int)
    - ``target`` (int)
    - ``band_low`` / ``band_high`` (int) — 与 :func:`word_band` 同源
    - ``deviation_pct`` (float) — ``(visible - target) / target * 100``，保留 1 位小数；
      ``target<=0`` 时为 ``0.0``
    - ``status`` — ``"under"`` (visible < band_low) / ``"in_band"`` (含 ±15%) /
      ``"over"`` (visible > band_high)
    """
    vc = visible_chars(prose)
    band_low, band_high = word_band(
        target_word_count, low_ratio=low_ratio, high_ratio=high_ratio, floor=floor
    )
    if target_word_count > 0:
        deviation_pct = round((vc - target_word_count) / target_word_count * 100, 1)
    else:
        deviation_pct = 0.0
    if vc < band_low:
        status = "under"
    elif vc > band_high:
        status = "over"
    else:
        status = "in_band"
    return {
        "visible_chars": vc,
        "target": target_word_count,
        "band_low": band_low,
        "band_high": band_high,
        "deviation_pct": deviation_pct,
        "status": status,
    }