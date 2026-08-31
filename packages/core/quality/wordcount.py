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

# resolve_band_config 默认值的派生源（与 word_band 默认参数同口径，避免硬编码两份）。
_DEFAULT_LOW_RATIO = 1 - _WARNING_RATIO  # 0.85
_DEFAULT_HIGH_RATIO = 1 + _WARNING_RATIO  # 1.15
_DEFAULT_FLOOR = _MIN_BAND_FLOOR  # 1200

# resolve_band_config 输入约束（单一权威定义，避免消费点各写一份漂移）。
_OVERRIDE_KEYS = frozenset({"low_ratio", "high_ratio", "floor"})


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


def resolve_band_config(overrides: dict | None) -> dict[str, float | int]:
    """把项目级 ``word_band_json`` 覆盖 dict 折叠成 ``word_band`` / ``classify_prose_length``
    可直接 ``**kwargs`` 展开的完整生效配置。

    缺省行为：
      - ``overrides`` 为 ``None`` / 空 dict ⇒ 全默认（与无覆盖项目零行为差异）。
      - 缺某个键 ⇒ 该键走默认（``low_ratio=0.85`` / ``high_ratio=1.15`` / ``floor=1200``）。
      - 未知键：忽略（项目可演进字段，但当前不在契约内）。

    校验规则（非法即抛 ``ValueError``，消息中文 + 指明哪个键）：
      - ``low_ratio`` ∈ (0, 1]（开区间 0、闭区间 1）。
      - ``high_ratio`` ≥ 1（不能小于 1，否则上带落到 target 以下）。
      - ``floor`` ≥ 0 整数（不是 int / 含小数 / 负数都拒）。
      - ``high_ratio`` ≥ ``low_ratio``（带单调性：避免 low > high 倒挂）。

    返回 dict 键固定为 ``{"low_ratio", "high_ratio", "floor"}``，类型分别
    ``float / float / int``——便于消费方直接 ``word_band(target, **cfg)`` 调用。
    """
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise ValueError("word_band 覆盖必须是 dict 或 None")

    cfg: dict[str, float | int] = {
        "low_ratio": _DEFAULT_LOW_RATIO,
        "high_ratio": _DEFAULT_HIGH_RATIO,
        "floor": _DEFAULT_FLOOR,
    }
    for k, v in overrides.items():
        if k not in _OVERRIDE_KEYS:
            # 未知键：忽略（向前兼容：项目侧可演进，DB 不需要再迁移）。
            continue
        if k == "low_ratio":
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError("low_ratio 必须是数字（int 或 float）")
            fv = float(v)
            if fv <= 0 or fv > 1:
                raise ValueError("low_ratio 必须满足 0 < low_ratio ≤ 1")
            cfg["low_ratio"] = fv
        elif k == "high_ratio":
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError("high_ratio 必须是数字（int 或 float）")
            fv = float(v)
            if fv < 1:
                raise ValueError("high_ratio 必须满足 high_ratio ≥ 1")
            cfg["high_ratio"] = fv
        elif k == "floor":
            # bool 是 int 的子类，必须先排除；其它 int/float 接受但要求无小数。
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("floor 必须是非负整数（int）")
            # 拒小数部分：1.5 不是「整数」（int(1.5)=1 但用户可能预期「保留小数」；
            # 此处 floor 必须是离散字符数，整数口径更稳）。
            if isinstance(v, float) and not v.is_integer():
                raise ValueError("floor 必须是非负整数（int），不接受小数")
            iv = int(v)
            if iv < 0:
                raise ValueError("floor 必须满足 floor ≥ 0")
            cfg["floor"] = iv
    # 带单调性：high_ratio ≥ low_ratio（low_ratio > 1 等边界 case 也走同一检查）。
    if cfg["high_ratio"] < cfg["low_ratio"]:
        raise ValueError("high_ratio 必须 ≥ low_ratio（带单调性）")
    return cfg