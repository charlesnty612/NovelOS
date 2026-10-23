"""相邻对白接词回声（``AI-DIALOGUE-ECHO``）用例：口径、命中、误报、边界。

背景（2026-09-17，用户实测反馈）：上一批为解「可读性差」给对话占比加了 error 档、
又在题材包与生成驱动写了「对话占比 ≥20%」硬指标，用户读后反馈「你现在的改法书
一点就不好读了，全是重复性的对话灌水，上一句说了啥，下一句接着重复一遍」。
该指标已撤回（见 ``test_dialogue_low_warning_only.py``），**症状本身**改为算子：

- **复述型** ``recall >= 0.70``：后句实义字中 ≥70% 出现在前句；
- **接词型** ``overlap >= 0.66`` 且**开口连续重合 ≥2 字**且**两侧实义字可比**
  （``DialogueEchoPair.overlap_comparable``）。

实义字 = 汉字与数字（去标点空白）；只比较**相邻两句对白**（都以 “ 起首的段，
取引号内文本）；单侧 <2 字（单字应答）不判；章级另有 200 可见字最小判定长度
（照 ``_MIN_PROSE_CHARS_FOR_DENSITY`` 先例）。severity 恒 warning——算子只看字面
重合，**不判断对白是否有后果**，故它是提示而非闸门。

**2026-09-27：重叠系数的分母缺陷修正**。``overlap = 共同字 ÷ min(前,后)`` 是
**对称**问法（两句用词是不是同一批），分母落在薄的一侧时被机械拉到 1.00——量的
其实是「长句含短句的字」。ch17 实测：`“林昭！”`（实义 2 字）→`“林昭，你跟我走，
茶馆比县仓安全。”`（11 字）得 ``recall 0.15 / overlap 1.00``——recall 说「后句
85% 是新东西」，overlap 却在说「完全重合」，后者被前者证伪。故接词型额外要求
两侧可比（见 ``test_overlap_comparable_*``）。真信号不受影响：2 字齐诵
（`不退。`→`不退！`）本来就走复述型。

实测（复算入口 ``scripts/readability_audit.py`` + 2026-09-27 逐条复算）：本仓
48 章 34 对 / 24 章命中（守卫生效后 23 对 / 18 章），人类锚点书 19 章 1 对——
人类侧看守在 ``test_dialogue_echo_baseline.py``。
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
    """必命中②（接词型接龙）：「够七天。」/「七天之后呢？」/「七天之后，看你。」。

    第 2 句接第 1 句的「七天」（复述率 0.40、重叠系数 2/3、开口 2），第 3 句接
    第 2 句的「七天之后」（重叠系数 4/5、开口 4）——两句都判，即该链**不能只命中一次**。

    **2026-09-27 改第三句长度**：原句 `“七天之后，看你守不守得住。”`（11 实义字 /
    前句 5 字 = 2.20 倍）跨过了重叠系数的可比性边界（后句 ≥2×前句 ⇒ 后句是超集，
    ``overlap`` 分母被前句钉死）。该句是 2026-09-17 自拟的**未入库**样例，从未进过
    语料判档；此处置为 6 字（1.20 倍）以留在可解释区间内，形态与教学点不变。
    边界本身由 ``test_overlap_comparable_rejects_short_call_plus_long_expansion``
    单独钉住，不靠这条用例隐式担保。
    """
    hits = count_dialogue_echoes(
        prose("“够七天。”", "“七天之后呢？”", "“七天之后，看你。”")
    )
    assert [p.sample for p in hits] == [
        "够七天。→七天之后呢？",
        "七天之后呢？→七天之后，看你。",
    ]
    first, second = hits
    assert first.recall_ratio == pytest.approx(0.4)
    assert first.overlap_ratio == pytest.approx(2 / 3) and first.opening_repeat == 2
    assert second.recall_ratio == pytest.approx(4 / 6)
    assert second.overlap_ratio == pytest.approx(4 / 5) and second.opening_repeat == 4
    # 2026-09-27：两条都必须判「重叠系数可比」——守卫不能一刀切掉整个接词型形态。
    # 3 vs 5 与 5 vs 6 字都落在「后句不吞掉前句全部」的区间里。
    assert all(p.overlap_comparable for p in hits), (
        "可比的接词型必须保留；把守卫写成「短侧一律不可比」会误伤这两条"
    )


def test_pickup_chain_link_above_asymmetry_limit_is_not_an_echo():
    """边界另一侧：``三天之后，看你守不守得住。``（2.20 倍）**不再**命中。

    这是 :func:`test_pickup_chain_hits_both_pairs` 里换掉的那一格，单独留一条用例
    钉住边界方向：``overlap`` 读到 0.80、开口 4，数字看着很「像」接词，但后句是
    前句的超集（recall 仅 0.36）⇒ 该读数不携带回声信息，不报。
    """
    text = prose("“七天之后呢？”", "“七天之后，看你守不守得住。”")
    pair = dialogue_echo_pairs(text)[0]
    assert len(pair.back_chars) == 11 and len(pair.front_chars) == 5
    assert pair.overlap_ratio == pytest.approx(0.8) and pair.opening_repeat == 4
    assert pair.recall_ratio == pytest.approx(4 / 11)
    assert pair.overlap_comparable is False
    assert count_dialogue_echoes(text) == []


def test_hits_from_measured_generated_corpus():
    """必命中③：本仓实测命中两例（2026-09-27 逐条复算，入口 ``scripts/readability_audit.py``）。

    - 复述型：`见周账房了？`→`见了。`（ch7，recall 1.00、**开口只有 1 字**）——
      单字开口靠复述型兜住，说明两种形态缺一都会漏。该对 ``overlap_ratio``
      不可比（短侧仅 2 字），但不可比**不妨碍它命中**——守卫只管接词型；
    - 接词型：`他临走前那晚，…借据造假。`→`借据呢？`（ch5，recall 0.67 < 0.70 ⇒
      只能由接词型命中；overlap 0.67、开口 2）。长前句 + 短追问正是守卫**刻意保留**
      的一侧（短后句由 recall 正确兜住），用它当必命中样例比反向的短前句更贴本意。

    **2026-09-27 换样例**：原用例写的是 `你守后队。`→`后队有人殿后，你在那儿。`
    （ch35，4 → 10 实义字 = 2.50 倍）。该对实测 ``recall`` 仅 0.30，**是接词型而非
    复述型**（本仓旧注释把它列为 ``ratio>=0.70`` 复述型样例，2026-09-27 已订正），
    且 2.50 倍跨过可比性边界——2026-09-27 全量判档把它归为合法写法（「复述关键词后
    **补充理由**」），守卫生效后不再命中。故从必命中清单撤下，另见
    ``test_overlap_comparable_rejects_short_call_plus_long_expansion``。
    """
    assert [
        p.sample
        for p in count_dialogue_echoes(prose("“见周账房了？”", "“见了。”"))
    ] == ["见周账房了？→见了。"]

    assert dialogue_echo_pairs(prose("“见周账房了？”", "“见了。”"))[0].overlap_comparable is (
        False
    ), "短侧 2 字 ⇒ overlap 读数不携带信息"

    long_front = "“他临走前那晚，说林昭不是携款跑了，是查到永丰号的借据造假。”"
    pickup = count_dialogue_echoes(prose(long_front, "“借据呢？”"))
    assert [p.sample for p in pickup] == [
        "他临走前那晚，说林昭不是携款跑了，是查到永丰号的借据造假。→借据呢？"
    ]
    assert pickup[0].opening_repeat == 2
    assert pickup[0].overlap_ratio == pytest.approx(2 / 3)
    assert pickup[0].recall_ratio < DEFAULT_DIALOGUE_ECHO_MIN_RATIO, (
        "recall 未过复述阈值 ⇒ 这一对确实只能由接词型命中（缺一即漏）"
    )
    assert pickup[0].overlap_comparable is True, "长前句 + 短追问：守卫刻意保留的一侧"


# ---------------------------------------------------------------------------
# 2026-09-27：重叠系数分母缺陷 —— `overlap_comparable`
# ---------------------------------------------------------------------------


def test_overlap_comparable_rejects_long_back_reusing_front_words():
    """不对称守卫的**独立**判别：两侧都有足够实义字（7 vs 80），仍不可比。

    实测事故对（生成侧 ch13，后句 92 字取自该章原句）：`我说的都是实话。`→`实话？林出纳，我跟你爹做过两回生意。……那五百两是我娘的棺材本。`。`overlap` 读到
    **0.86**、开口 2——数字看着非常「像」接词回声；但 `recall` 只有 **0.07**，
    后句 80 个实义字里 74 个是新内容。0.86 衡量的是「后句复用了前句的字」而不是
    「两句用词是同一批」。

    之所以要与下一条（`林昭！`→13 字）分开写：那一对前句只有 2 个实义字，
    **同时**被「最小可比字数 3」和「2 倍不对称界」两条守卫拦住，撤掉任一条它仍绿，
    因而不构成对其中任一条的独立判别。本对 min = 7 ≥ 3，只有不对称守卫能拦。
    """
    text = prose(
        "“我说的都是实话。”",
        "“实话？林出纳，我跟你爹做过两回生意。第一回他说粮价要涨，我信了，压了两万斤，"
        "第二个月跌了。第二回他说有个门路能让我赚三成，我信了，投进去五百两，半个月后"
        "人没了。那五百两是我娘的棺材本。”",
    )
    pair = dialogue_echo_pairs(text)[0]
    assert (len(pair.front_chars), len(pair.back_chars)) == (7, 80)
    assert pair.overlap_ratio == pytest.approx(6 / 7), "读数很高——这正是它不可信的原因"
    assert pair.opening_repeat == 2
    assert pair.recall_ratio == pytest.approx(6 / 80), "后句 80 字里 74 字是新东西"
    assert pair.overlap_comparable is False
    assert count_dialogue_echoes(text) == [], (
        "两侧都够长时仍须拦——撤掉不对称守卫即红（本对是该守卫的独立判别）"
    )


def test_overlap_comparable_rejects_short_call_plus_long_expansion():
    """缺陷形状：短促称呼 + 长展开 ⇒ ``overlap`` 恒 1.00，却不携带回声信息。

    实测事故对（生成侧 ch17 原文）：前句 `林昭！` = 2 个实义字、后句
    `林昭，你跟我走，茶馆比县仓安全。` = **13** 个，``min()`` 分母恒等于
    前句，只要长句包含短句的字就得 1.00。同期 ``recall`` 只有 0.154——后句
    85% 是新内容。两个指标互相证伪时以 recall 为准，故该对不得凭 overlap 命中。
    """
    text = prose("“林昭！”", "“林昭，你跟我走，茶馆比县仓安全。”")
    pair = dialogue_echo_pairs(text)[0]
    assert (len(pair.front_chars), len(pair.back_chars)) == (2, 13)
    assert pair.overlap_ratio == 1.0, "分母取短侧的机械结果：读数本身没错，但无信息"
    assert pair.recall_ratio == pytest.approx(2 / 13), "recall 说后句 85% 是新东西"
    assert pair.opening_repeat == 2, (
        "开口连续重合是 2 ⇒ 旧的 min_opening 守卫挡不住这一形态（已订正注释）"
    )
    assert pair.overlap_comparable is False
    assert count_dialogue_echoes(text) == [], (
        "不可比的对不得凭重叠系数命中（撤守卫即红：缺陷版会报这一对）"
    )


def test_overlap_comparable_rejects_two_char_sides():
    """另一处退化：两侧都只有 2 字时 ``overlap`` 必为 1.00（分母 = 2）。

    ``_MIN_DIALOGUE_ECHO_CHARS = 2`` 只管**成对资格**，管不住读数退化——所以
    另设可比性下限 3。注意这类对里包含**真信号**（`不退。`→`不退！` 齐诵），故
    守卫必须只掐接词型、不得连复述型一起放过。
    """
    text = prose("“退了。”", "“退了呀。”")
    pair = dialogue_echo_pairs(text)[0]
    assert (len(pair.front_chars), len(pair.back_chars)) == (2, 3)
    assert pair.overlap_ratio == 1.0 and pair.opening_repeat == 2
    assert pair.recall_ratio == pytest.approx(2 / 3)
    assert pair.recall_ratio < DEFAULT_DIALOGUE_ECHO_MIN_RATIO, (
        "复述型不兜底 ⇒ 这一对只能被接词型报出来，正是守卫要拦的读数"
    )
    assert pair.overlap_comparable is False, "任一侧 <3 字 ⇒ overlap 不可解释"
    assert count_dialogue_echoes(text) == [], (
        "只有接词型能命中它，撤守卫即红（缺陷版报「退了。→退了呀。」）"
    )


def test_overlap_comparable_keeps_two_char_verbatim_echo():
    """守卫不得误伤真信号：短句被**完整复述**（recall 1.00）仍必须命中。

    实测生成侧 3 条真信号全部是短句齐诵形态（ch37 `不退。`→`不退！` ×2、ch45
    `他们退了。`→`他们退了。`）。前者 ``overlap_comparable`` 为 False（两侧各 2
    字）、后者为 True（各 4 字）——**可比性管的是读数，不是判定**，两者的结论
    都由复述型给出。这正是守卫只挂在接词型上的理由。
    """
    cases = [
        ("“不退。”", "“不退！”", "不退。→不退！", False),
        ("“他们退了。”", "“他们退了。”", "他们退了。→他们退了。", True),
    ]
    for front, back, expected, comparable in cases:
        text = prose(front, back)
        pair = dialogue_echo_pairs(text)[0]
        assert pair.recall_ratio == 1.0
        assert pair.overlap_comparable is comparable
        assert [p.sample for p in count_dialogue_echoes(text)] == [expected], (
            "真信号由复述型兜住，撤守卫不该改变这一条的判定"
        )


def test_overlap_comparable_is_asymmetric_by_design():
    """守卫只挡「后句是超集」方向——「后句更短」那侧由 recall 正确兜住。

    `见周账房了？`（5 字）→`见了。`（2 字）也是长度悬殊，但后句被前句完整包含时
    ``recall = 1.00`` 恰好是**正确读数**（「直接应答」的定义），且它在册为必命中
    形态。所以守卫若做成对称的（两侧比例越界都不可比），等于把同一现象按方向
    区别对待——这里用断言把「非对称是有理由的」钉住。
    """
    short_back = dialogue_echo_pairs(prose("“见周账房了？”", "“见了。”"))[0]
    assert len(short_back.front_chars) > 2 * len(short_back.back_chars)
    assert short_back.overlap_comparable is False
    assert short_back.recall_ratio == 1.0, "recall 的分母恒为后句，短后侧仍可解释"
    assert short_back in count_dialogue_echoes(prose("“见周账房了？”", "“见了。”"))


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
    """一对相邻对白只报一次；一对同时满足两种形态也只报一次；``samples`` 取前 5 对。

    **2026-09-27 换样例**：原样例交替 `“三百。”`↔`“三百人，吃什么？”`，其中 4 对是
    2 字前句 / 6 字后句（3.00 倍）——正是 2026-09-27 修掉的退化读数（``overlap``
    恒 1.00、recall 0.33），现由可比性守卫正确拦下，故该断言已不成立。改用两侧
    同为 3 实义字的对（recall 1.00、overlap 1.00、开口 3、可比），**两种形态同时
    成立**——比原样例更贴合本测试要验的「同时命中只记一对」。
    """
    chain = []
    for _ in range(4):
        chain += ["“三百人。”", "“三百人！”"]
    text = prose(*chain)
    hit = echo_hit(text)
    assert hit is not None
    assert hit["count"] == 7, "7 对相邻对白就是 7 对，不因两形态同时命中而翻倍"
    assert len(hit["samples"]) == 5
    assert hit["samples"][0] == "三百人。→三百人！"
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
