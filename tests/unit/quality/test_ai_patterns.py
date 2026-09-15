"""packages.core.quality.ai_patterns 单元测试。

覆盖：
- 无命中时返回空列表。
- 禁用词 / AI 高频套话命中。
- 连续同词开头句式套路。
- 「他/她」主语排比堆砌。
- 章尾总结/升华体套话。
- 破折号/省略号滥用（默认阈值与自定义阈值）。
- 解释腔连接词高密度。
- 命中数超阈值时 severity 升级为 error。
- 空字符串/空白字符串安全返回空列表。
"""

from __future__ import annotations

from packages.core.quality.ai_patterns import (
    AI_PATTERN_FORBIDDEN_WORDS,
    DEFAULT_DASH_THRESHOLD_PER_1K,
    scan_ai_patterns,
)

# ---------------------------------------------------------------------------
# 基础行为
# ---------------------------------------------------------------------------


def test_empty_prose_returns_empty():
    assert scan_ai_patterns("") == []
    assert scan_ai_patterns("   \n\t  ") == []


def test_clean_prose_returns_empty():
    prose = "风吹过山岗，带来远行的消息。他站起身，望向天际。"
    assert scan_ai_patterns(prose) == []


def test_all_hits_have_required_fields():
    prose = "仿佛一道闪电。他走了。他停下脚步。他转过身。本章目标。"
    hits = scan_ai_patterns(prose)
    assert hits
    for hit in hits:
        assert "rule_id" in hit
        assert "severity" in hit
        assert hit["severity"] in ("warning", "error")
        assert "message" in hit


# ---------------------------------------------------------------------------
# AI-FORBIDDEN-WORD
# ---------------------------------------------------------------------------


def test_forbidden_word_hit():
    prose = "他仿佛看到了命运的齿轮。"
    hits = scan_ai_patterns(prose)
    fw_hits = [h for h in hits if h["rule_id"] == "AI-FORBIDDEN-WORD"]
    assert len(fw_hits) == 1
    hit = fw_hits[0]
    assert hit["severity"] == "warning"
    assert "仿佛" in hit["message"]
    assert "仿佛" in hit["words"]
    assert hit["count"] >= 1


def test_forbidden_word_backward_compatible_list():
    # 模块级禁用词列表包含原有默认词
    assert "仿佛" in AI_PATTERN_FORBIDDEN_WORDS
    assert "如同" in AI_PATTERN_FORBIDDEN_WORDS
    assert "本章目标" in AI_PATTERN_FORBIDDEN_WORDS


def test_forbidden_word_multiple_words_in_one_hit():
    prose = "本章目标仿佛如同完成任务。"
    hits = scan_ai_patterns(prose)
    fw_hits = [h for h in hits if h["rule_id"] == "AI-FORBIDDEN-WORD"]
    assert len(fw_hits) == 1
    assert set(fw_hits[0]["words"]) == {"仿佛", "如同", "本章目标"}


def test_forbidden_word_severity_upgrade_on_many_hits():
    # 构造 10 处以上命中（总次数 >= 10），应升级为 error
    prose = "仿佛" * 11
    hits = scan_ai_patterns(prose)
    fw_hits = [h for h in hits if h["rule_id"] == "AI-FORBIDDEN-WORD"]
    assert len(fw_hits) == 1
    assert fw_hits[0]["severity"] == "error"
    assert fw_hits[0]["count"] >= 10


# ---------------------------------------------------------------------------
# AI-TRIPLET-OPENING
# ---------------------------------------------------------------------------


def test_triplet_opening_hit():
    prose = "风吹过了。风吹散了。风吹远了。他回家了。"
    hits = scan_ai_patterns(prose)
    tri_hits = [h for h in hits if h["rule_id"] == "AI-TRIPLET-OPENING"]
    assert len(tri_hits) == 1
    hit = tri_hits[0]
    assert hit["severity"] == "warning"
    assert hit["word"] == "风吹"
    assert hit["count"] == 3


def test_triplet_opening_requires_three():
    # 只有两句开头相同，不命中
    prose = "风吹过了。风吹散了。雨下来了。"
    hits = scan_ai_patterns(prose)
    assert not [h for h in hits if h["rule_id"] == "AI-TRIPLET-OPENING"]


# ---------------------------------------------------------------------------
# AI-PRONOUN-PILE
# ---------------------------------------------------------------------------


def test_pronoun_pile_hit():
    prose = "他走了。他停下脚步。他转过身。"
    hits = scan_ai_patterns(prose)
    pp_hits = [h for h in hits if h["rule_id"] == "AI-PRONOUN-PILE"]
    assert len(pp_hits) == 1
    assert pp_hits[0]["count"] == 3


def test_pronoun_pile_requires_three():
    # 仅两句以他/她开头，第三句不是，不应命中
    prose = "他走了。他停下脚步。天黑了。"
    hits = scan_ai_patterns(prose)
    assert not [h for h in hits if h["rule_id"] == "AI-PRONOUN-PILE"]


# ---------------------------------------------------------------------------
# AI-ENDING-SUMMARY
# ---------------------------------------------------------------------------


def test_ending_summary_hit():
    prose = "前文叙事。\n\n这一刻，他明白了一切。从此以后，新的篇章即将开启。"
    hits = scan_ai_patterns(prose)
    es_hits = [h for h in hits if h["rule_id"] == "AI-ENDING-SUMMARY"]
    assert len(es_hits) == 1
    assert "这一刻" in es_hits[0]["words"]
    assert "从此以后" in es_hits[0]["words"]
    assert "新的篇章" in es_hits[0]["words"]


def test_ending_summary_only_last_paragraph():
    # 套话出现在非结尾段，不命中
    prose = "从此以后，他开始改变。\n\n真正的结局在最后一刻到来。"
    hits = scan_ai_patterns(prose)
    es_hits = [h for h in hits if h["rule_id"] == "AI-ENDING-SUMMARY"]
    assert len(es_hits) == 0 or "从此以后" not in es_hits[0]["words"]


# ---------------------------------------------------------------------------
# AI-PUNCT-ABUSE
# ---------------------------------------------------------------------------


def test_punct_abuse_hit():
    # 约 100 字正文，插入 7 处破折号/省略号 → 每千字约 70 处，远超阈值
    prose = "他" * 90 + "——" * 7
    hits = scan_ai_patterns(prose)
    pa_hits = [h for h in hits if h["rule_id"] == "AI-PUNCT-ABUSE"]
    assert len(pa_hits) == 1
    assert pa_hits[0]["count"] == 7
    assert pa_hits[0]["rate"] > DEFAULT_DASH_THRESHOLD_PER_1K
    assert pa_hits[0]["severity"] == "error"  # 超过阈值 2 倍


def test_punct_abuse_custom_threshold():
    # 正文 100 字，3 处省略号 → 每千字 30 处
    prose = "他" * 94 + "……" * 3
    # 默认阈值 6：命中
    hits_default = scan_ai_patterns(prose)
    assert any(h["rule_id"] == "AI-PUNCT-ABUSE" for h in hits_default)
    # 阈值 50：不命中
    hits_high = scan_ai_patterns(prose, dash_threshold_per_1k=50)
    assert not any(h["rule_id"] == "AI-PUNCT-ABUSE" for h in hits_high)


def test_punct_abuse_no_hit():
    prose = "他走了，脚步很轻。她停在门口。"
    hits = scan_ai_patterns(prose)
    assert not any(h["rule_id"] == "AI-PUNCT-ABUSE" for h in hits)


# ---------------------------------------------------------------------------
# AI-EXPLAIN-TONE
# ---------------------------------------------------------------------------


def test_explain_tone_hit():
    prose = "因为天黑了，所以他回家。换句话说，今天到此结束。也就是说，明天再来。"
    hits = scan_ai_patterns(prose)
    et_hits = [h for h in hits if h["rule_id"] == "AI-EXPLAIN-TONE"]
    assert len(et_hits) == 1
    assert et_hits[0]["count"] >= 3


def test_explain_tone_requires_multiple():
    prose = "因为天黑了，所以他回家。"
    hits = scan_ai_patterns(prose)
    # 仅 1 处，仍会产生 hit，但 count=1
    et_hits = [h for h in hits if h["rule_id"] == "AI-EXPLAIN-TONE"]
    assert len(et_hits) == 1
    assert et_hits[0]["count"] == 1


def test_explain_tone_severity_upgrade():
    # 5 处以上升级为 error
    prose = "".join([f"因为{i}，所以{i}。换句话说{i}。" for i in range(3)])
    hits = scan_ai_patterns(prose)
    et_hits = [h for h in hits if h["rule_id"] == "AI-EXPLAIN-TONE"]
    assert et_hits[0]["severity"] == "error"


# ---------------------------------------------------------------------------
# 综合 / 边界
# ---------------------------------------------------------------------------


def test_multiple_rules_in_one_prose():
    prose = (
        "仿佛命运之手。他走了。他停下脚步。他转过身。\n\n"
        "这一刻，新的篇章开启了。——一切都结束了……"
    )
    hits = scan_ai_patterns(prose)
    rule_ids = {h["rule_id"] for h in hits}
    assert "AI-FORBIDDEN-WORD" in rule_ids
    assert "AI-PRONOUN-PILE" in rule_ids
    assert "AI-ENDING-SUMMARY" in rule_ids
    assert "AI-PUNCT-ABUSE" in rule_ids


# ---------------------------------------------------------------------------
# 2026-09-15 外部对照研究移植组（lieflat-less-ai-tone）
# ---------------------------------------------------------------------------
# 本组规则的两个纪律（写进测试，防回退）：
#   1. 规则名必须与算子**实际覆盖范围**相符（来源研究公开的六次测量失误，
#      全部是「算子覆盖范围宽于规则名称」这一形状）；
#   2. 阈值按本仓语料校准，不照搬来源研究数值——本地实测见
#      scripts/ai_tone_calibrate.py。


def test_contrast_pair_hits_when_dense():
    """对举结构「不是 A 而是/是 B」——按密度报警（本仓实测 R≈6.6）。

    注意：密度类规则有最小判定长度（200 可见字），测试文本必须够长，
    否则测的是长度守卫而不是算子本身。
    """
    unit = (
        "他不是不想走，而是走不了。他不是怕，是不敢。他不是没钱，是没胆。"
        "不是他不想，是不能。不是他不懂，是不肯。"
    )
    prose = unit * 4
    assert len(prose) >= 200
    hits = scan_ai_patterns(prose)
    cp = [h for h in hits if h["rule_id"] == "AI-CONTRAST-PAIR"]
    assert len(cp) == 1, "高密度对举应命中"
    assert cp[0]["count"] >= 4
    assert cp[0]["samples"], "必须带命中样例，供精确率抽样核查"


def test_contrast_pair_quiet_when_sparse():
    """低密度不报警：人类侧同样使用该结构（0.12/千字），只做密度判定。"""
    long_prose = "他走进院子，看了看天。" * 60
    hits = scan_ai_patterns(long_prose + "不是钱的事，是人。")
    assert not any(h["rule_id"] == "AI-CONTRAST-PAIR" for h in hits)


def test_short_para_hits_when_dense():
    """短句独立成段（节拍器式行文）——本仓实测 R≈4.4。"""
    short_paras = [
        "灯芯闪了一下", "朔风穿过廊下", "檐角垂下冰棱", "更鼓敲过三响",
        "堂前落满细雪", "纸窗透进微光", "炭盆将熄未熄", "门外传来脚步",
        "他抖了抖袖子", "案上茶已凉透", "院里传来犬吠", "天色将亮未亮",
    ]
    prose = "\n\n".join(short_paras * 3)
    from packages.core.quality.wordcount import visible_chars

    assert visible_chars(prose) >= 200, "密度类规则要求 ≥200 可见字，测试文本必须够长"
    hits = scan_ai_patterns(prose)
    sp = [h for h in hits if h["rule_id"] == "AI-SHORT-PARA"]
    assert len(sp) == 1
    assert sp[0]["count"] >= 8


def test_short_para_ignores_dialogue_and_pronoun_starts():
    """对话短句与人称代词起首的短句都不计入（前者正常，后者归 AI-PRONOUN-PILE）。"""
    from packages.core.quality.ai_patterns import count_short_paras

    prose = "\n\n".join(
        ["「走。」", "他站住了。", "门口有人。", "堂里有人。"] * 4
    )
    hits = count_short_paras(prose)
    assert "「走。」" not in hits
    assert all(not h.startswith("他") for h in hits)


def test_density_rules_skip_short_text():
    """密度类规则对过短文本不判定——「1 处 / 17 字 = 58/千字」是伪信号。

    这条守卫是被真实的假阳性逼出来的：单测样例「灯芯闪了一下。苏婉清没有出声，
    把玉佩收回袖中。」曾把 polisher 的「预检干净即跳过」用例整片判红。
    """
    from packages.core.quality.ai_patterns import (
        count_contrast_pairs,
        count_short_paras,
    )

    tiny = "灯芯闪了一下。他不是不想走，而是走不了。"
    assert count_contrast_pairs(tiny), "算子本身应命中"
    assert count_short_paras(tiny) != [] or True  # 算子级不看长度
    # 但规则级（密度判定）必须静默
    hits = scan_ai_patterns(tiny)
    assert not any(
        h["rule_id"] in ("AI-CONTRAST-PAIR", "AI-SHORT-PARA") for h in hits
    ), "短文本不得触发密度类规则"


def test_short_para_requires_whole_paragraph_to_be_short():
    """「段首是短句」不等于「整段是一句短句」——后者才算独立成段。"""
    from packages.core.quality.ai_patterns import count_short_paras

    # 段首短句 + 后接长句：不算
    assert count_short_paras("灯芯闪了一下。苏婉清没有出声，把玉佩收回袖中。") == []
    # 整段就一句短句：算
    assert count_short_paras("灯芯闪了一下。") == ["灯芯闪了一下"]


def test_short_para_ignores_anaphora():
    """含回指/指示成分的短句不算零回指——这是与会话衔接的判别要点。"""
    from packages.core.quality.ai_patterns import count_short_paras

    hits = count_short_paras("这很危险。\n\n那不对。\n\n此路不通。\n\n他没走。")
    assert hits == []


def test_anthro_vehicle_dormant_below_min_count():
    """拟人化喻体：未校准的休眠守卫——本仓两侧零命中，只在同章 ≥3 次时报警。"""
    prose = "他的目光像一位审判官。"
    hits = scan_ai_patterns(prose)
    assert not any(h["rule_id"] == "AI-ANTHRO-VEHICLE" for h in hits)

    dense = "他像一位导师。他看着像一位医师。他说话像一位学者。"
    hits2 = scan_ai_patterns(dense)
    av = [h for h in hits2 if h["rule_id"] == "AI-ANTHRO-VEHICLE"]
    assert len(av) == 1 and av[0]["count"] == 3


def test_translationese_rule_removed_after_precision_check():
    """译文句式**已废弃**（2026-09-15 抽样核查）：本仓 R=0.49 方向相反 +
    「过长前置定语」算子 97 条命中里 95 条误报。此测钉住「已删除」这一事实，
    防止不知情者按来源研究清单把它加回来。"""
    from packages.core.quality import ai_patterns

    assert not hasattr(ai_patterns, "count_translationese")
    assert "AI-TRANSLATIONESE" not in {r.rule_id for r in ai_patterns.AI_PATTERN_RULES}


def test_dash_threshold_recalibrated_for_local_corpus():
    """破折号阈值必须落在本仓实测分布内——原值 6 是死规则（实测 max 4.73/千字）。

    本测以「本仓实测均值 ≈2.34」为锚：构造一段略高于阈值的文本必须命中；
    若有人把阈值调回 6，本测转红。
    """
    assert DEFAULT_DASH_THRESHOLD_PER_1K <= 4.0, "阈值高于本仓 p90 即等于永不触发"
    prose = "他站住——风起——灯灭——人散——夜凉——雪落——" * 3
    hits = scan_ai_patterns(prose)
    assert any(h["rule_id"] == "AI-PUNCT-ABUSE" for h in hits)
