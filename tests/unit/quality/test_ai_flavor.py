"""V3.9 批次 3.4：「AI 味」四算合一的共享词表回归。

覆盖：
- 词表单一来源：guardrails.AI_MARKERS / scoring.AI_MARKERS 引用 ai_flavor.AI_FLAVOR_MARKERS；
  ai_trace.AI_CLICHES 引用 ai_flavor.AI_CLICHES；
- 行为不变：AI_CLICHES 多重集与共享化之前的表逐条相等（下方 LEGACY 常量是改造前原文）；
- 密度工具等价：marker_hits_per_kchars == 旧内联式 ``sum(count) / (len/1000)``；
- 分数锁定：style / ai_trace 在同一 fixture 上的分数与共享化前一致（对照值见 docstring）。
"""

from __future__ import annotations

import pytest

from packages.core.quality import ai_flavor, scoring
from packages.core.quality import guardrails as g
from packages.core.quality.ai_patterns import scan_ai_patterns
from packages.core.quality.ai_trace import AI_CLICHES as AI_TRACE_CLICHES
from packages.core.quality.ai_trace import cliche_density, compute_ai_trace
from packages.core.quality.scoring import score_style

# 改造前（V3.9.2）ai_trace.AI_CLICHES 原文——作为行为锁：共享化后内容必须逐条一致。
_LEGACY_CLICHES: tuple[str, ...] = (
    "不禁",
    "仿佛",
    "嘴角勾起",
    "嘴角微微上扬",
    "眼中闪过一丝",
    "眼中闪过一抹",
    "眼底闪过",
    "眼底深处",
    "眸子微微一缩",
    "眉头微皱",
    "眉头紧锁",
    "神色微变",
    "深吸一口气",
    "深吸了一口",
    "倒吸一口凉气",
    "倒吸了一口凉气",
    "心情复杂",
    "心下一凛",
    "心中一震",
    "心底涌起",
    "空气仿佛凝固",
    "空气骤然凝固",
    "空气瞬间凝固",
    "凝固了一般",
    "时间仿佛停止",
    "仿佛凝固",
    "落针可闻",
    "一片死寂",
    "整个空间",
    "然而",
    "但是",
    "不仅",
    "更重要的是",
    "值得注意的是",
    "由此可见",
    "总而言之",
    "综上所述",
    "不置可否",
    "嗤笑一声",
    "冷冷一笑",
    "淡淡开口",
    "淡淡说道",
    "淡淡地开口",
    "沉声开口",
    "沉声说道",
    "声音低沉",
    "一字一句",
)


def test_ai_markers_single_source():
    """style / req_q7 的 AI 标记词都是 ai_flavor 同一对象（不再各写一份）。"""
    assert g.AI_MARKERS is ai_flavor.AI_FLAVOR_MARKERS
    assert scoring.AI_MARKERS is ai_flavor.AI_FLAVOR_MARKERS


def test_ai_cliches_content_matches_legacy_exactly():
    """共享化不改词表内容：与改造前原文逐条（多重集）相等。

    顺序不参与计数（``sum(text.count(c))``），此处按排序比较集合与重复度；
    任何词的增删都会让本测试变红。
    """
    assert sorted(AI_TRACE_CLICHES) == sorted(_LEGACY_CLICHES)
    assert sorted(ai_flavor.AI_CLICHES) == sorted(_LEGACY_CLICHES)
    assert ai_flavor.AI_CLICHES == ai_flavor.AI_CLICHE_DESCRIPTORS + ai_flavor.AI_CLICHE_CONNECTIVES
    assert len(ai_flavor.AI_CLICHES) == 47


def test_connectives_are_subset_of_flavor_markers():
    """转折/议论类套话与 style 词表由同一常量派生（不会各自漂移）。"""
    assert set(ai_flavor.AI_CLICHE_CONNECTIVES) <= set(ai_flavor.AI_FLAVOR_MARKERS)


def test_scan_ai_patterns_is_reexported_authoritative_source():
    """权威信号源 scan_ai_patterns 从 ai_flavor 单点可达，且与 ai_patterns 是同一函数。"""
    assert ai_flavor.scan_ai_patterns is scan_ai_patterns
    hits = ai_flavor.scan_ai_patterns("首先，他嘴角勾起一抹冷笑。其次，仿佛一切都没发生。")
    assert any(h["rule_id"] == "AI-FORBIDDEN-WORD" for h in hits)


def test_marker_hits_per_kchars_matches_inline_formula():
    text = "首先其次最后然而但是总而言之综上所述。"
    markers = g.AI_MARKERS
    expected = sum(text.count(m) for m in markers) / (len(text) / 1000.0)
    assert ai_flavor.marker_hits_per_kchars(text, markers) == expected
    assert ai_flavor.marker_hits_per_kchars("", markers) == 0.0


# ---------------------------------------------------------------------------
# 分数锁定（行为不变）
# ---------------------------------------------------------------------------


def _fixtures() -> dict[str, str]:
    normal = (
        "今天天气真好，阳光洒在青石板上。\n\n"
        + "我们一起去公园散步，远处的鸟儿在枝头鸣唱。\n\n"
        + "午后咖啡馆的音乐若有若无，温暖而安静。\n\n"
        + "傍晚的风带来远山的凉意，人间值得。\n\n"
        + "突然，一道黑影闪过山巅，就在此时不见？"
    )
    return {
        "normal": normal,
        "marker_dense": "首先其次最后但是不仅正常文字用于测试句子。" * 30,
        "trigram_rep": "测试重复片段" * 50,
        "cliche_mix": ("然而，他嘴角勾起一抹冷笑。" * 8)
        + ("深吸一口气，心情复杂。" * 6)
        + ("淡淡开口，一字一句。" * 6),
        "long_ai": "这是一整章由写作代理生成的正文内容。" * 5,
    }


@pytest.mark.parametrize(
    "name,expected_style,expected_ai_trace",
    [
        # 改造前（旧三模块）实测值 —— 共享化后必须逐项一致。
        ("normal", 100, 100),
        ("marker_dense", 70, 73),
        ("trigram_rep", 75, 80),
        ("cliche_mix", 60, 73),
        ("long_ai", 85, 80),
    ],
)
def test_style_and_ai_trace_scores_unchanged(name, expected_style, expected_ai_trace):
    text = _fixtures()[name]
    assert score_style(text)[0] == expected_style
    assert compute_ai_trace(text)[0] == expected_ai_trace


def test_cliche_density_uses_shared_table():
    """cliche_density 默认词表 = 共享 AI_CLICHES；阶梯扣分不变（≈2.0/千字 → 12）。

    阶梯语义按 `_CLICHE_BUCKETS` 实现语义：< 1.5/千字 → 0；1.5-3.0 → 12；≥3.0 → 28。
    """
    text = "然而然而" + "正常文字" * 250  # 1004 字，2 次命中 ≈ 2.0 / 千字
    per_k, deduct = cliche_density(text)
    expected_per_k = sum(text.count(c) for c in AI_TRACE_CLICHES) / (len(text) / 1000.0)
    assert per_k == expected_per_k
    assert deduct == 12  # 阶梯不因共享化而变
