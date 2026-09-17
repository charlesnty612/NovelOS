"""相邻对白接词回声（``AI-DIALOGUE-ECHO``）用例：口径、命中、误报、边界。

背景（2026-09-17，用户实测反馈）：上一批为解「可读性差」给对话占比加了 error 档、
又在题材包与生成驱动写了「对话占比 ≥20%」硬指标，用户读后反馈「你现在的改法书
一点就不好读了，全是重复性的对话灌水，上一句说了啥，下一句接着重复一遍」。
该指标已撤回（见 ``test_dialogue_low_warning_only.py``），**症状本身**改为算子：

- **复述型** ``recall >= 0.70``：后句实义字中 ≥70% 出现在前句；
- **接词型** ``overlap >= 0.66`` 且**开口连续重合 ≥2 字**：后句开口就接对方的词。

实义字 = 汉字与数字（去标点空白）；只比较**相邻两句对白**（都以 “ 起首的段，
取引号内文本）；单侧 <2 字（单字应答）不判；章级另有 200 可见字最小判定长度
（照 ``_MIN_PROSE_CHARS_FOR_DENSITY`` 先例）。severity 恒 warning——算子只看字面
重合，**不判断对白是否有后果**，故它是提示而非闸门。

实测（复算入口 ``scripts/readability_audit.py``，只读）：本仓 48 章 26 对 / 20 章
命中，人类锚点书 19 章 1 对——人类侧看守在 ``test_dialogue_echo_baseline.py``。
"""

from __future__ import annotations

import pytest

from packages.core.quality.ai_patterns import (
    AI_PATTERN_RULES,
    DEFAULT_DIALOGUE_ECHO_MIN_OPENING,
    DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP,
    DEFAULT_DIALOGUE_ECHO_MIN_RATIO,
    DialogueEchoPair,
    count_dialogue_echoes,
    dialogue_echo_pairs,
    scan_ai_patterns,
)
from packages.core.quality.wordcount import visible_chars

# 中性叙述段（不以 “ 起首，不参与成对比较），用来把片段抬过章级最小判定长度。
_NARRATION = (
    "他站在柜台后头，把价签翻了个面。雨点打在油纸伞上，声音又密又匀。",
    "巷口的灯笼晃了两下。",
)
# 与实现内的 _MIN_PROSE_CHARS_FOR_DENSITY 同值（照该先例）；测试里写死以防静默改动。
_ECHO_MIN_JUDGE_CHARS = 200


def prose(*dialogue_lines: str) -> str:
    """对白行按段拼接，尾部补中性叙述直到越过章级最小判定长度。"""
    parts = list(dialogue_lines)
    i = 0
    while visible_chars("\n\n".join(parts)) <= _ECHO_MIN_JUDGE_CHARS + 20:
        parts.append(_NARRATION[i % len(_NARRATION)])
        i += 1
    return "\n\n".join(parts)


def echo_hit(text: str, **kwargs) -> dict | None:
    """只取回声规则的章级命中（其余规则的命中与本文件无关）。"""
    return next(
        (h for h in scan_ai_patterns(text, **kwargs) if h["rule_id"] == "AI-DIALOGUE-ECHO"),
        None,
    )


# ---------------------------------------------------------------------------
# 必命中样例（任务书三条，均取自真实语料）
# ---------------------------------------------------------------------------


def test_repeat_reply_hits():
    """必命中①（复述型）：「三百人，够不够守城？」→「三百。」。

    后句 2 个实义字全部来自前句 ⇒ 复述率 1.00；同时满足接词型（开口 2 字）。
    两种形态都命中时**只记一对**。
    """
    pair = count_dialogue_echoes(prose("“三百人，够不够守城？”", "“三百。”"))
    assert [p.sample for p in pair] == ["三百人，够不够守城？→三百。"]
    assert pair[0].recall_ratio == 1.0
    assert pair[0].opening_repeat == 2


def test_pickup_chain_hits_both_pairs():
    """必命中②（接词型接龙）：「够七天。」/「七天之后呢？」/「七天之后，看你守不守得住。」。

    第 2 句接第 1 句的「七天」（复述率 0.40、重叠系数 2/3、开口 2），第 3 句接
    第 2 句的「七天之后」（重叠系数 0.80、开口 4）——两句都判，即该链**不能只命中一次**。
    """
    hits = count_dialogue_echoes(
        prose("“够七天。”", "“七天之后呢？”", "“七天之后，看你守不守得住。”")
    )
    assert [p.sample for p in hits] == [
        "够七天。→七天之后呢？",
        "七天之后呢？→七天之后，看你守不守得住。",
    ]
    first, second = hits
    assert first.recall_ratio == pytest.approx(0.4)
    assert first.overlap_ratio == pytest.approx(2 / 3) and first.opening_repeat == 2
    assert second.overlap_ratio == pytest.approx(4 / 5) and second.opening_repeat == 4


def test_hits_from_measured_generated_corpus():
    """必命中③：本仓实测命中两例（复算入口 ``scripts/readability_audit.py``）。

    - 复述型：「见周账房了？」→「见了。」（ch7，复述率 1.00、**开口只有 1 字**）——
      单字开口靠复述型兜住，说明两种形态缺一都会漏；
    - 接词型：「你守后队。」→「后队有人殿后，你在那儿。」（ch35，重叠 0.75、开口 2）。
    """
    assert [
        p.sample
        for p in count_dialogue_echoes(prose("“见周账房了？”", "“见了。”"))
    ] == ["见周账房了？→见了。"]

    pickup = count_dialogue_echoes(
        prose("“你守后队。”", "“后队有人殿后，你在那儿。”")
    )
    assert [p.sample for p in pickup] == ["你守后队。→后队有人殿后，你在那儿。"]
    assert pickup[0].opening_repeat == 2 and pickup[0].overlap_ratio == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# 不得误报（三种形状）
# ---------------------------------------------------------------------------


def test_new_number_is_not_an_echo():
    """不得误报①：后句是**新**数字而非复述——「今天多少？」→「六百。」。

    这是本规则最要防的一类：数字是接词回声的关键信号，但「新数字」恰恰是
    信息递增的正常对白（实测两个方向的比例都是 0）。
    """
    text = prose("“今天多少？”", "“六百。”")
    pair = dialogue_echo_pairs(text)[0]
    assert (pair.recall_ratio, pair.overlap_ratio, pair.opening_repeat) == (0.0, 0.0, 0)
    assert count_dialogue_echoes(text) == []
    assert echo_hit(text) is None


def test_question_without_front_words_is_not_an_echo():
    """不得误报②：提问且不含前句实义字——「你怎么知道？」。"""
    text = prose("“单子在这儿。”", "“你怎么知道？”")
    assert count_dialogue_echoes(text) == []
    assert echo_hit(text) is None


def test_information_increasing_reply_is_not_an_echo():
    """不得误报③：信息递增的长回答——用词撞车但**不是**接对方的词。

    这条同时钉住「开口 ≥2 字」守卫的必要性：该对的**重叠系数是 1.00**（前句 4 个
    实义字全在长回答里），只按重叠系数判会误报；开口连续重合为 0（回答以「我跟你
    爹」起头），故正确地不判。
    """
    text = prose(
        "“粮价要涨。”",
        "“我跟你爹做过两回生意，头一回他说粮价要涨，我信了压了两万斤，第二个月就跌了。”",
    )
    pair = dialogue_echo_pairs(text)[0]
    assert pair.overlap_ratio == 1.0 and pair.opening_repeat == 0, (
        "误报形状：短句被长回答整句包含——开口守卫是唯一的判别依据"
    )
    assert count_dialogue_echoes(text) == []
    assert echo_hit(text) is None


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------


def test_single_dialogue_line_has_no_pair():
    """单句对白构不成「相邻两句」，不判。"""
    text = prose("“三百人，够不够守城？”")
    assert dialogue_echo_pairs(text) == []
    assert echo_hit(text) is None


def test_single_char_dialogue_is_not_compared():
    """单字对白（「嗯。」「好。」）不含信息，单侧 <2 实义字不参与成对比较。"""
    assert dialogue_echo_pairs(prose("“嗯。”", "“好。”")) == []
    # 一侧单字也不判（否则「嗯。」会被算成前句的 100% 复述）
    assert dialogue_echo_pairs(prose("“三百人，够不够守城？”", "“嗯。”")) == []
    assert echo_hit(prose("“三百人，够不够守城？”", "“嗯。”")) is None


def test_chapter_without_dialogue_is_quiet():
    """无对白章：没有对白句，自然没有回声对。"""
    text = prose()
    assert dialogue_echo_pairs(text) == []
    assert echo_hit(text) is None


def test_narration_between_dialogue_lines_keeps_adjacency():
    """相邻 = 对白句序列中的前后两项；中间夹一段叙述不改变判定。"""
    with_narration = prose("“见周账房了？”", _NARRATION[0], "“见了。”")
    assert [p.sample for p in count_dialogue_echoes(with_narration)] == ["见周账房了？→见了。"]


def test_fragment_below_judge_length_is_not_reported():
    """章级最小判定长度：片段（<200 可见字）不判，但成对算子本身照常命中。"""
    tiny = "“三百人，够不够守城？”\n\n“三百。”"
    assert visible_chars(tiny) < _ECHO_MIN_JUDGE_CHARS
    assert len(count_dialogue_echoes(tiny)) == 1, "算子本身命中"
    assert echo_hit(tiny) is None, "片段没有统计意义，章级不判"


def test_samples_are_capped_and_one_pair_counts_once():
    """一对相邻对白只报一次；一对同时满足两种形态也只报一次；``samples`` 取前 5 对。"""
    # 每对都同时满足两种形态（「三百。」↔「三百人，吃什么？」），7 对
    chain = []
    for _ in range(4):
        chain += ["“三百。”", "“三百人，吃什么？”"]
    text = prose(*chain)
    hit = echo_hit(text)
    assert hit is not None
    assert hit["count"] == 7, "7 对相邻对白就是 7 对，不因两形态同时命中而翻倍"
    assert len(hit["samples"]) == 5
    assert hit["samples"][0] == "三百。→三百人，吃什么？"
    assert "→" in hit["excerpt"]


def test_severity_is_warning_and_thresholds_can_be_overridden():
    """severity 恒 warning（提示非闸门）；阈值是关键词参数（突变验证/校准入口）。"""
    text = prose("“三百人，够不够守城？”", "“三百。”")
    hit = echo_hit(text)
    assert hit is not None and hit["severity"] == "warning"

    disabled = scan_ai_patterns(
        text, dialogue_echo_min_ratio=1.01, dialogue_echo_min_overlap=1.01
    )
    assert not [h for h in disabled if h["rule_id"] == "AI-DIALOGUE-ECHO"], (
        "阈值抬到不可能达到的值后必须零命中（突变验证用）"
    )


def test_rule_metadata_registered():
    """规则元数据表收录 AI-DIALOGUE-ECHO，且默认档位是 warning。"""
    row = next((r for r in AI_PATTERN_RULES if r.rule_id == "AI-DIALOGUE-ECHO"), None)
    assert row is not None
    assert row.severity == "warning"
    assert "{count}" in row.message


def test_thresholds_are_registered():
    """阈值常量登记：复述 0.70 / 接词 0.66（下取整自必命中样例 2/3）/ 开口 2。"""
    assert DEFAULT_DIALOGUE_ECHO_MIN_RATIO == 0.70
    assert DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP == 0.66
    assert DEFAULT_DIALOGUE_ECHO_MIN_OPENING == 2
    assert DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP < DEFAULT_DIALOGUE_ECHO_MIN_RATIO, (
        "接词型阈值必须低于复述型：它补的正是「后句字数多于一半来自前句、但开口接词」"
        "这一类（必命中样例 2/3 = 0.67 落在这条缝里）"
    )


def test_pair_fields_document_the_metrics():
    """``DialogueEchoPair`` 暴露三个指标 + ``sample``，供抽样核对（人工过目用）。"""
    pair = dialogue_echo_pairs(prose("“够七天。”", "“七天之后呢？”"))[0]
    assert isinstance(pair, DialogueEchoPair)
    assert pair.front_chars == "够七天" and pair.back_chars == "七天之后呢"
    assert pair.sample == "够七天。→七天之后呢？"
