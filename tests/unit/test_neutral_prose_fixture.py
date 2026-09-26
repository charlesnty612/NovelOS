"""``tests/unit/neutral_prose.py`` 的**中性性**回归（2026-09-18）。

为什么需要一个测试看守 filler：它是全仓评审用例的公共正文，一旦它自己带上
「重复 / 排版病理 / 时点矛盾」特征，**所有**拿它做断言的用例都会在无关维度上报红
（改造前实测：13 字 shingle 重复率 0.71~0.97，AI-BEAT-REPEAT / trigram 全部命中），
于是要么伪造红灯、要么在用例侧加过滤豁免——两条路都会让门禁失去意义。

本文件把「中性」写成可判定的四条（每条都对着一条真实算子）：

1. 可见字数**逐字精确**（用例断言 ±15% / ±30% 这类百分比，差 1 字就漂）；
2. ``scan_ai_patterns`` 零命中（覆盖 AI-* 与 CONT-* 全部规则）；
3. 13 字 shingle 重复率 ≈ 0（本仓人工章节中位 0.000；改造前 0.94）；
4. 排版与配比不触线：所有段 ≤ 100 可见字、无短段、对话占比 ≥ 12%。

红灯的正确反应：改 filler（换槽位 / 换模板），**不是**调低检测算子阈值——
AGENTS.md 硬纪律「不得为坏 fixture 削弱检测」。
"""

from __future__ import annotations

import pytest

from packages.core.quality.ai_patterns import dialogue_ratio, scan_ai_patterns
from packages.core.quality.ai_trace import _shingle_repetition_ratio
from packages.core.quality.guardrails import _norm
from packages.core.quality.wordcount import visible_chars
from tests.unit.neutral_prose import neutral_prose

# 评审 / 字数带用例实际用到的长度（见 test_chapter_review_* / test_draft_under_review）。
_USED_SIZES = (300, 713, 900, 1200, 1300, 1420, 1530, 1700, 1720, 1900, 2000, 2248, 2500, 3000)

# 13 字 shingle 重复率的可接受上限。本仓 92 章真实草稿：中位 0.000 / p90 0.003 /
# max 0.026；重写前的 6 句循环 filler 是 0.71（300 字）~0.97（3000 字）。
_SHINGLE_MAX = 0.02

# 长段 / 短段阈值（与 ai_patterns 的 DEFAULT_LONG_PARA_WARN_CHARS、
# count_short_paras 的口径同值）。
_PARA_MAX_CHARS = 100
_PARA_SHORT_CHARS = 12

# 对话占比下限（ai_patterns.DEFAULT_DIALOGUE_LOW_WARN_RATIO），且规则只对
# ≥600 可见字的正文生效。
_DIALOGUE_MIN_RATIO = 0.12
_DIALOGUE_MIN_PROSE_CHARS = 600


@pytest.mark.parametrize("chars", _USED_SIZES)
def test_exact_visible_chars(chars: int):
    """恰好 ``chars`` 个可见字（多一个少一个都会让 ±15% 边界用例漂）。"""
    text = neutral_prose(chars)
    assert visible_chars(text) == chars
    assert len(text.split("\n\n")) >= 2, "单段填充会让 AI-LONG-PARA 变成必然命中"


def test_zero_and_negative_return_empty():
    assert neutral_prose(0) == ""
    assert neutral_prose(-5) == ""


@pytest.mark.parametrize("chars", _USED_SIZES)
def test_no_ai_pattern_hits(chars: int):
    """``scan_ai_patterns`` 零命中——含 AI-*（含 AI-BEAT-REPEAT）与 CONT-*。

    改造前此处红：6 句循环填充让 ``AI-BEAT-REPEAT`` 报 9 处、``AI-LONG-PARA`` 也可能中。
    """
    hits = scan_ai_patterns(neutral_prose(chars))
    assert hits == [], [
        (h.get("rule_id"), h.get("message")) for h in hits
    ]


@pytest.mark.parametrize("chars", _USED_SIZES)
def test_shingle_repetition_is_human_baseline_level(chars: int):
    """13 字 shingle 重复率 ≈ 0（这是「中性」二字唯一可测的定义）。

    改造前实测 0.71（300 字）/ 0.94（1300 字）/ 0.97（3000 字）——
    即 filler 自身是重复文本，任何「用重复率判正文」的用例都在测病理样本。
    """
    ratio = _shingle_repetition_ratio(_norm(neutral_prose(chars)), 13)
    assert ratio <= _SHINGLE_MAX, f"{chars} 字填充的 shingle 重复率 {ratio:.4f} > {_SHINGLE_MAX}"


@pytest.mark.parametrize("chars", _USED_SIZES)
def test_paragraph_shape_stays_off_both_rails(chars: int):
    """段长既不 ≥100 长段堆积门，也不 ≤12 短段门。"""
    paragraphs = neutral_prose(chars).split("\n\n")
    lengths = [visible_chars(p) for p in paragraphs]
    assert max(lengths) <= _PARA_MAX_CHARS, lengths
    assert min(lengths) > _PARA_SHORT_CHARS, lengths


@pytest.mark.parametrize("chars", [c for c in _USED_SIZES if c >= _DIALOGUE_MIN_PROSE_CHARS])
def test_dialogue_ratio_above_low_warning_threshold(chars: int):
    """对话占比 ≥12%（``AI-DIALOGUE-LOW`` 的阈值）——否则 filler 自带一条 warning。"""
    assert dialogue_ratio(neutral_prose(chars)) >= _DIALOGUE_MIN_RATIO


@pytest.mark.parametrize("chars", _USED_SIZES)
def test_trigram_rate_never_reaches_confirm_tier(chars: int):
    """trigram 重复率**低于 confirm 档阈值**——filler 不该让任何用例被迫签字。

    2026-09-18 两档重定后仍成立：≤2300 字无 issue、2400~3000 字只到 warn 档
    （``gate=auto``，无后果）。这条把「filler 与阈值耦合」显式钉住：若有人把 confirm
    阈值降到 filler 会命中的高度，这里变红，逼他先改 filler（AGENTS.md 硬纪律：
    不许为坏 fixture 削弱/抬高检测算子）。
    """
    from packages.core.quality.scoring import (
        STYLE_TRIGRAM_CONFIRM_THRESHOLD,
        _trigram_repetition_rate,
    )

    rate = _trigram_repetition_rate(neutral_prose(chars))
    assert rate < STYLE_TRIGRAM_CONFIRM_THRESHOLD, (
        f"{chars} 字填充的 trigram 重复率 {rate:.4f} 已达 confirm 阈值 "
        f"{STYLE_TRIGRAM_CONFIRM_THRESHOLD}"
    )


def test_fixture_is_deterministic():
    """同参数必须给同一正文（用例断言的「字数 / 报告」才有可复现基线）。"""
    assert neutral_prose(1234) == neutral_prose(1234)


def test_fixture_contains_no_time_of_day_markers():
    """**不含任何时点标记**（2026-09-18 实证）。

    原稿用「清晨/晌午/三更/辰时/未时…」当槽位，词序由槽位步长决定 ⇒ 同章出现
    「黄昏 → 破晓」这类不可能的回退，被 ``CONT-TIME-BACKSTEP`` **正确地**命中。
    这是 filler 的构造缺陷（伪样本），故整条时间轴从槽位里移除；本断言把它钉住。
    """
    markers = (
        "清晨", "晌午", "入夜", "三更", "破晓", "黄昏", "午后", "日暮", "五更", "半夜",
        "凌晨", "傍晚", "辰时", "未时", "天明", "掌灯", "鸡鸣", "天明",
    )
    text = neutral_prose(3000)
    hit = [m for m in markers if m in text]
    assert hit == [], hit
