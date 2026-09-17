"""可读性算子（AI-LONG-PARA / AI-DIALOGUE-LOW）边界与触发用例。

背景（2026-09-17，读者反馈「可读性差」后量化）：书 ``prj_bcb9d1930bd4`` 全弧
48 章 / 122350 可见字 与文风锚点书榜一《快穿之人渣洗白手册》侯府弧 19 章 /
37806 可见字 对照，两项指标**此前无任何算子看守**：

==================== ==================== ======================
指标                 榜一（人类基线）      本仓生成侧
==================== ==================== ======================
单段 >120 可见字      2 段（779 段的 0.26%） 46 段
单段 >100 可见字      7 段（每章至多 1 段）  133 段（最长 212 字）
单段 >140 可见字      0 段                  16 段
对话占比              16.5%                 11.4%
==================== ==================== ======================

本文件钉**长度口径与档位**：段按 ``\\n+`` 切、字数走 ``visible_chars``、
对话＝成对“…”内的可见字；两条规则都有 600 可见字最小判定长度。
人类基线的看守在 ``test_readability_baseline.py``。

**2026-09-17 撤回 ``AI-DIALOGUE-LOW`` 的 error 档**（<8%）：逼模型凑对话指标产出
灌水对白，且人类锚点书写得出整章无对话。本文件相应改为「只有 warning 一档」的
断言；症状探针 ``AI-DIALOGUE-ECHO`` 的用例见 ``test_dialogue_echo.py``。
"""

from __future__ import annotations

from packages.core.quality.ai_patterns import (
    DEFAULT_DIALOGUE_LOW_WARN_RATIO,
    DEFAULT_LONG_PARA_ERROR_CHARS,
    DEFAULT_LONG_PARA_MIN_COUNT,
    DEFAULT_LONG_PARA_WARN_CHARS,
    count_dialogue_visible_chars,
    count_long_paragraphs,
    dialogue_ratio,
    scan_ai_patterns,
)
from packages.core.quality.wordcount import visible_chars

# ---------------------------------------------------------------------------
# 样例构造：真实中文句子拼到**精确**可见字长
# ---------------------------------------------------------------------------
# 边界用例测的是「恰好在阈值上/阈值外」的判定方向，长度必须精确到 1 字，
# 故池子里是真实句子、末句按需截断（不掺 ASCII 填充字符）。

_POOL = (
    "他站在柜台后头，把价签翻了个面。",
    "雨点打在油纸伞上，声音又密又匀。",
    "巷口的灯笼晃了两下，火光歪向一边。",
    "她把铜钱一枚一枚码进抽屉，数到第七个停下。",
)


def _exact(chars: int) -> str:
    """恰好 ``chars`` 个可见字的段落（真实中文句子拼接，末句截断）。"""
    text = ""
    while visible_chars(text) < chars:
        text += _POOL[len(text) % len(_POOL)]
    out = text[:chars]
    assert visible_chars(out) == chars
    return out


def _paragraphs(*lengths: int) -> str:
    """按给定可见字长拼出多段正文（段间空行，与生产 draft 同形）。"""
    return "\n\n".join(_exact(n) for n in lengths)


# 中性填充：8 段 × 90 字 = 720 可见字（本身不含长段，只用来越过最小判定长度）
_FILLER = _paragraphs(*([90] * 8))


def _readability_hits(prose: str) -> dict[str, dict]:
    return {
        h["rule_id"]: h
        for h in scan_ai_patterns(prose)
        if h["rule_id"] in ("AI-LONG-PARA", "AI-DIALOGUE-LOW")
    }


# ---------------------------------------------------------------------------
# AI-LONG-PARA：段长口径与两个档位
# ---------------------------------------------------------------------------


def test_paragraph_split_is_newline_based():
    """段切分口径 = 任意换行序列（外部锚点书一行一段，生产 draft 空行分段）。"""
    # 空行分段与单换行分段在两种文本形态下都必须切出同样多的段
    assert len(count_long_paragraphs("")) == 0
    prose = "短句。\n\n" + _exact(101) + "\n单换行也算新段。"
    assert count_long_paragraphs(prose) == [_exact(101)]


def test_long_para_thresholds_are_registered():
    """阈值与依据数字一起登记：>100 warning 档、>140 error 档、单章 ≥2 段才报警。"""
    assert DEFAULT_LONG_PARA_WARN_CHARS == 100
    assert DEFAULT_LONG_PARA_ERROR_CHARS == 140
    assert DEFAULT_LONG_PARA_MIN_COUNT == 2
    assert DEFAULT_LONG_PARA_WARN_CHARS < DEFAULT_LONG_PARA_ERROR_CHARS


def test_long_para_quiet_at_exactly_warn_chars():
    """刚好不触发：单段**恰好** 100 可见字不算长段（判据是严格大于）。"""
    prose = _FILLER + "\n\n" + _paragraphs(100, 100)
    hits = _readability_hits(prose)
    assert "AI-LONG-PARA" not in hits, "100 字是闭区间上界，不该计入长段"


def test_long_para_quiet_with_single_long_paragraph():
    """堆积门：人类基线每章至多 1 段超 100 字——单段超长不报。"""
    prose = _FILLER + "\n\n" + _exact(101)
    assert count_long_paragraphs(prose) == [_exact(101)], "算子本身应命中 1 段"
    assert "AI-LONG-PARA" not in _readability_hits(prose), (
        "单段超长是正常写作，攒够 2 段才算排版病"
    )


def test_long_para_triggers_at_min_count():
    """刚好触发：单章 2 段恰好 101 字（≥ 堆积门 2、无 error 档）→ warning。"""
    prose = _FILLER + "\n\n" + _paragraphs(101, 101)
    hit = _readability_hits(prose).get("AI-LONG-PARA")
    assert hit is not None, "2 段 101 字应命中"
    assert hit["severity"] == "warning"
    assert hit["count"] == 2
    assert hit["error_count"] == 0
    assert hit["max_chars"] == 101
    assert len(hit["samples"]) == 2, "必须带命中样例，供精确率抽样核查"
    assert all(len(s) <= 30 for s in hit["samples"])


def test_long_para_warning_tier_at_exactly_error_chars():
    """140 字仍属 warning 档（error 判据是严格大于 140）。"""
    prose = _FILLER + "\n\n" + _paragraphs(140, 140)
    hit = _readability_hits(prose).get("AI-LONG-PARA")
    assert hit is not None and hit["severity"] == "warning"
    assert hit["error_count"] == 0


def test_long_para_error_tier_single_paragraph():
    """跨到 error 档：单段 141 字（>140）即 error——榜一 779 段里零出现。"""
    prose = _FILLER + "\n\n" + _exact(141)
    hit = _readability_hits(prose).get("AI-LONG-PARA")
    assert hit is not None, ">140 字段落单段即报（不受堆积门限制）"
    assert hit["severity"] == "error"
    assert hit["error_count"] == 1
    assert hit["max_chars"] == 141


def test_long_para_error_tier_mixes_with_warning_tier():
    """2 段 110 字 + 1 段 200 字 → error 档，count 记全部长段。"""
    prose = _FILLER + "\n\n" + _paragraphs(110, 110, 200)
    hit = _readability_hits(prose).get("AI-LONG-PARA")
    assert hit is not None
    assert hit["severity"] == "error"
    assert hit["count"] == 3
    assert hit["error_count"] == 1


def test_long_para_skips_short_fragments():
    """最小判定长度：<600 可见字的片段不判（单测样例/引文的统计无意义）。"""
    tiny = _paragraphs(101, 101, 101)  # 303 可见字
    assert visible_chars(tiny) < 600
    assert count_long_paragraphs(tiny), "算子本身命中 3 段"
    assert "AI-LONG-PARA" not in _readability_hits(tiny), (
        "短片段不得触发排版类规则"
    )


def test_long_para_dialogue_paragraph_counts():
    """长对话段同样计入——本规则量的是「一段多长」，不区分叙述/对话。"""
    long_dialogue = "“" + _exact(150) + "”"
    prose = _FILLER + "\n\n" + long_dialogue
    hit = _readability_hits(prose).get("AI-LONG-PARA")
    assert hit is not None and hit["max_chars"] >= 150


# ---------------------------------------------------------------------------
# AI-DIALOGUE-LOW：对话口径与两个档位
# ---------------------------------------------------------------------------


def test_dialogue_chars_counts_pairs_only():
    """对话口径：成对“…”内的可见字；未成对/其它形态引号不计。"""
    assert count_dialogue_visible_chars("“走吧。”") == 3
    assert count_dialogue_visible_chars("「走吧。」") == 0, "「」在落库前已规整为“”"
    assert count_dialogue_visible_chars("“漏了右引号。") == 0
    assert count_dialogue_visible_chars("“一”二“三”") == 2
    assert dialogue_ratio("") == 0.0


def test_dialogue_ratio_thresholds_are_registered():
    """阈值与依据一起登记：<12% warning（**只有这一档**）；榜一 16.5%、我们 11.4%。

    error 档（<8%）2026-09-17 撤回——本测试同时钉住「常量已删除」，防止有人
    悄悄把第二档加回来（判别：``test_dialogue_low_warning_only.py`` 另做行为断言）。
    """
    assert DEFAULT_DIALOGUE_LOW_WARN_RATIO == 0.12
    from packages.core.quality import ai_patterns as module

    assert not hasattr(module, "DEFAULT_DIALOGUE_LOW_ERROR_RATIO"), (
        "对话占比的 error 档已撤回（2026-09-17）：常量不该再存在"
    )


def _prose_with_dialogue(dialogue_chars: int, total_chars: int = 1000) -> str:
    """构造对话占比**精确**可控的正文（总可见字 = 引号 2 + 叙述 + 对话）。"""
    narration = total_chars - dialogue_chars - 2
    return _exact(narration) + "\n\n" + "“" + _exact(dialogue_chars) + "”"


def test_dialogue_quiet_at_exactly_warn_ratio():
    """刚好不触发：对话占比**恰好** 12%（120/1000）不报——判据是严格小于。"""
    prose = _prose_with_dialogue(120)
    assert visible_chars(prose) == 1000
    assert dialogue_ratio(prose) == DEFAULT_DIALOGUE_LOW_WARN_RATIO
    assert "AI-DIALOGUE-LOW" not in _readability_hits(prose)


def test_dialogue_triggers_just_below_warn_ratio():
    """刚好触发：119/1000 = 11.9% → warning 档。"""
    prose = _prose_with_dialogue(119)
    hit = _readability_hits(prose).get("AI-DIALOGUE-LOW")
    assert hit is not None, "低于 12% 应命中"
    assert hit["severity"] == "warning"
    assert hit["dialogue_chars"] == 119
    assert hit["total_visible_chars"] == 1000
    assert hit["dialogue_ratio"] == 0.119


def test_dialogue_at_old_error_ratio_is_still_warning():
    """80/1000 = 8.0% 落在**已撤回的旧 error 界**上 → 现在仍是 warning。"""
    prose = _prose_with_dialogue(80)
    assert dialogue_ratio(prose) == 0.08
    hit = _readability_hits(prose).get("AI-DIALOGUE-LOW")
    assert hit is not None and hit["severity"] == "warning"


def test_dialogue_below_old_error_ratio_is_only_warning():
    """79/1000 = 7.9% 原判 error → 现在**只有 warning**（error 档已撤回）。

    行为断言在 ``test_dialogue_low_warning_only.py`` 里覆盖 0% 章的极端情形。
    """
    prose = _prose_with_dialogue(79)
    hit = _readability_hits(prose).get("AI-DIALOGUE-LOW")
    assert hit is not None, "低于 12% 仍须提示"
    assert hit["severity"] == "warning", "error 档已撤回（2026-09-17）"
    assert hit["dialogue_ratio"] == 0.079
    assert "error_ratio" not in hit, "error 档相关字段随档位一起删除"


def test_dialogue_quiet_above_warn_ratio():
    """榜一量级（16.5%）与番茄主流（25~40%）都远在阈值之上，不得报。"""
    for ratio_chars in (165, 250, 400):
        prose = _prose_with_dialogue(ratio_chars)
        assert "AI-DIALOGUE-LOW" not in _readability_hits(prose), ratio_chars


def test_dialogue_skips_short_fragments():
    """最小判定长度：<600 可见字不判对话占比（小样例「4/17 = 24%」之类是伪信号）。"""
    tiny = "“走吧。”" + _exact(300)  # 约 305 可见字，对话占比约 1%
    assert visible_chars(tiny) < 600
    assert dialogue_ratio(tiny) < DEFAULT_DIALOGUE_LOW_WARN_RATIO
    assert "AI-DIALOGUE-LOW" not in _readability_hits(tiny)


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------


def test_scan_ai_patterns_exposes_both_rules_with_thresholds():
    """两条规则可被调用方按参数重定阈值（与既有规则同款关键词参数）。"""
    prose = _prose_with_dialogue(119)
    assert not any(
        h["rule_id"] == "AI-DIALOGUE-LOW"
        for h in scan_ai_patterns(prose, dialogue_low_warn_ratio=0.10)
    ), "显式阈值 10% 下 11.9% 不报"
    long_prose = _FILLER + "\n\n" + _paragraphs(101, 101)
    assert not any(
        h["rule_id"] == "AI-LONG-PARA"
        for h in scan_ai_patterns(long_prose, long_para_warn_chars=1000)
    ), "显式阈值 1000 字下 101 字段落不报"


def test_both_rules_registered_in_rule_metadata():
    """规则元数据表必须收录可读性三条规则（供评审 UI / 文档索引读取）。"""
    from packages.core.quality.ai_patterns import AI_PATTERN_RULES

    ids = {r.rule_id for r in AI_PATTERN_RULES}
    assert {"AI-LONG-PARA", "AI-DIALOGUE-LOW", "AI-DIALOGUE-ECHO"} <= ids
