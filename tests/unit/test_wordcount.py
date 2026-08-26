"""packages.core.quality.wordcount（V3.7）单元测试。

覆盖：
- visible_chars：空串 / 中文 / 空白混排；
- word_band：低带 floor 保护、target<=0 fallback；
- classify_prose_length：三态（under / in_band / over）、deviation_pct 精度。
"""

from __future__ import annotations

from packages.core.quality.wordcount import (
    classify_prose_length,
    visible_chars,
    word_band,
)


# ---------------------------------------------------------------------------
# visible_chars
# ---------------------------------------------------------------------------


def test_visible_chars_empty_string():
    """空串 → 0。"""
    assert visible_chars("") == 0


def test_visible_chars_none_safe():
    """None / 其它假值 → 0（容错）。"""
    assert visible_chars(None) == 0  # type: ignore[arg-type]


def test_visible_chars_pure_chinese():
    """纯中文无空白 → 长度等于字符数。"""
    assert visible_chars("你好世界") == 4


def test_visible_chars_whitespace_mixed():
    """中英文 + 各类空白混排：折叠后取长度。"""
    text = "你好 世界\n  hello\tworld\r\nfoo"
    # 折叠后 = "你好世界helloworldfoo" = 4 + 5 + 5 + 3 = 17
    assert visible_chars(text) == 17
    # 单独：中英文短串折叠
    assert visible_chars("你好\n世界  hello") == 9  # 你好世界hello


def test_visible_chars_only_whitespace():
    """全空白串 → 0。"""
    assert visible_chars("   \n\t\r\n  ") == 0


# ---------------------------------------------------------------------------
# word_band
# ---------------------------------------------------------------------------


def test_word_band_basic_target():
    """target=2000：低带=1700、高带=2300（默认 0.85/1.15）。"""
    low, high = word_band(2000)
    assert low == 1700
    assert high == 2300


def test_word_band_low_floor_protects():
    """target=1000：低带 raw=850 经 floor=1200 抬升；高带 raw=1150 < low → 同步抬到 1200。
    语义：floor 单调性保护——「带」不能为空集（low > high）。
    """
    low, high = word_band(1000)
    assert low == 1200
    assert high == 1200  # V3.7：与 low 对齐，避免 low>high 倒挂
    assert low <= high  # 单调性


def test_word_band_low_floor_lifts_high_too():
    """更小 target：floor 单调性更显性——target=100，raw_low=85/floor=1200，raw_high=115<1200 → high=1200。"""
    low, high = word_band(100)
    assert low == 1200
    assert high == 1200


def test_word_band_floor_does_not_overflow_high_when_target_large():
    """target=10000：low=8500, high=11500（远超 floor=1200），保持原值。"""
    low, high = word_band(10000)
    assert low == 8500
    assert high == 11500


def test_word_band_zero_target_fallback():
    """target<=0：返回 (floor, floor)，避免除零。"""
    assert word_band(0) == (1200, 1200)
    assert word_band(-5) == (1200, 1200)


def test_word_band_custom_ratios():
    """自定义 ratio 生效。"""
    low, high = word_band(2000, low_ratio=0.9, high_ratio=1.1)
    assert low == 1800
    assert high == 2200


def test_word_band_custom_floor_lower_than_default():
    """显式 floor=1000 低于默认值仍生效（target=1000 时 low=1000，不再是 1200）。"""
    low, _ = word_band(1000, floor=1000)
    assert low == 1000


# ---------------------------------------------------------------------------
# classify_prose_length
# ---------------------------------------------------------------------------


def test_classify_in_band_center():
    """visible_chars 接近 target 时 in_band。"""
    prose = "中" * 2000  # 2000 chars
    out = classify_prose_length(prose, target_word_count=2000)
    assert out["visible_chars"] == 2000
    assert out["target"] == 2000
    assert out["band_low"] == 1700
    assert out["band_high"] == 2300
    assert out["status"] == "in_band"
    assert out["deviation_pct"] == 0.0


def test_classify_in_band_within_15pct():
    """轻微偏离（±14%） → 仍在 band 内。"""
    prose = "中" * 1720  # 偏离 -14%
    out = classify_prose_length(prose, target_word_count=2000)
    assert out["status"] == "in_band"
    # 偏离 = (1720-2000)/2000*100 = -14.0
    assert out["deviation_pct"] == -14.0


def test_classify_under_band():
    """低于 band_low → under。"""
    prose = "中" * 1000  # visible=1000, band_low=1700
    out = classify_prose_length(prose, target_word_count=2000)
    assert out["status"] == "under"
    assert out["visible_chars"] == 1000
    assert out["deviation_pct"] == -50.0


def test_classify_over_band():
    """高于 band_high → over。"""
    prose = "中" * 3000  # visible=3000, band_high=2300
    out = classify_prose_length(prose, target_word_count=2000)
    assert out["status"] == "over"
    assert out["visible_chars"] == 3000
    assert out["deviation_pct"] == 50.0


def test_classify_deviation_pct_rounded_to_1_decimal():
    """deviation_pct 保留 1 位小数。"""
    # visible=1818, target=2000 → 偏差 = -9.1%（精确 -9.1）
    out = classify_prose_length("中" * 1818, target_word_count=2000)
    assert out["deviation_pct"] == -9.1


def test_classify_zero_target_safe():
    """target<=0 → deviation_pct=0.0, band=(floor, floor)，visible=0→in_band（0 在 band 内）。"""
    out = classify_prose_length("anything here", target_word_count=0)
    assert out["target"] == 0
    assert out["band_low"] == 1200
    assert out["band_high"] == 1200
    assert out["deviation_pct"] == 0.0
    # visible_chars = 14, 但 band_low=1200, band_high=1200, 14 < 1200 → under
    assert out["status"] == "under"


def test_classify_prose_with_whitespace_visible_chars():
    """classify 内部 visible_chars 已折叠空白。"""
    prose = "你好\n世界  hello"
    out = classify_prose_length(prose, target_word_count=10)
    # 折叠后 = "你好世界hello" = 9
    assert out["visible_chars"] == 9
    # 9 / 10 = -10%（在 ±15% 内 → in_band）
    # 但 target=10 时 floor 仍生效：low=max(int(10*0.85)=8, 1200)=1200
    # visible=9 < 1200 → under
    assert out["status"] == "under"
    assert out["deviation_pct"] == -10.0


def test_classify_payload_uses_word_band_consistent():
    """builders / wordcount 口径一致：band_low / band_high 与 word_band 直接调用一致。"""
    target = 2200
    out = classify_prose_length("中" * 2200, target_word_count=target)
    assert (out["band_low"], out["band_high"]) == word_band(target)