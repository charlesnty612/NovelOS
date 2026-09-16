"""packages.core.quality.normalize 单元测试。

覆盖（用例语料取自新书01 全弧真实章节片段，见模块 docstring 的实证来源）：

- 引号归一化：``「」`` / 英文直引号 / 已是 ``“”`` 三种形态，含单章内混用；
- 安全边界（反例）：不配对 / 嵌套 / 落单的引号逐字不动，代码块与 JSON 片段行不动，
  英制符号与英文对白不动；
- 幂等性：规整两次 == 规整一次，第二次零变更；
- 变更清单：``rule`` / ``source`` / ``count`` / ``samples`` 的形状与计数；
- 内部标识检出：``recalled_passages`` / ``story_state`` / ``chapter_goal`` 命中，
  ``OK`` / ``PPT`` / ``iPhone`` / ``AiStation`` 不命中，重复命中只报一次且保序；
- 空串 / 纯空白 / 超短文本不崩。
"""

from __future__ import annotations

import pytest

from packages.core.quality.normalize import (
    find_internal_identifiers,
    normalize_prose,
)

# ---------------------------------------------------------------------------
# 三种引号 → “”
# ---------------------------------------------------------------------------


def test_corner_quotes_become_curly():
    """角括号对 → 弯双引号（ch1 用「」、锚点用 “”，目标统一到锚点）。"""
    src = "「转过去。」她说。"
    out, changes = normalize_prose(src)
    assert out == "“转过去。”她说。"
    assert changes == [
        {
            "rule": "NORM-QUOTE",
            "source": "「」",
            "count": 1,
            "samples": ["「转过去。」→“转过去。”"],
        }
    ]


def test_ascii_quotes_become_curly():
    """英文直引号对（ch8/9/11/16/17 的形态）→ 弯双引号。"""
    src = '"背伤三天不能下床，脚踝另算。"她把纱布尾端扎紧。'
    out, changes = normalize_prose(src)
    assert out == "“背伤三天不能下床，脚踝另算。”她把纱布尾端扎紧。"
    assert changes[0]["source"] == '"'
    assert changes[0]["count"] == 1


def test_dialogue_closing_quote_after_digit_still_converts():
    """引号内以数字收尾是正常对话（真实语料：电子屏显示「待穿局工单：3」）。"""
    src = "通道口悬着一块电子屏，上面显示着\"待穿局工单：3\"。"
    out, changes = normalize_prose(src)
    assert out == "通道口悬着一块电子屏，上面显示着“待穿局工单：3”。"
    assert changes[0]["count"] == 1


def test_curly_quotes_already_target_are_noop():
    """已是 “…” 的章节（ch6/10/18）零变更——目标形态即现状。"""
    src = "“转过去。”她说，“别动。”"
    out, changes = normalize_prose(src)
    assert out == src
    assert changes == []


def test_mixed_families_in_one_line():
    """单章内混用（ch22/ch24 实形）：同一行的角括号对与直引号对各自归一化。"""
    src = "「茶在哪儿？」他问的不是\"能不能卖\"，他问的是\"在哪儿\"。"
    out, changes = normalize_prose(src)
    assert out == "“茶在哪儿？”他问的不是“能不能卖”，他问的是“在哪儿”。"
    assert [c["source"] for c in changes] == ["「」", '"']
    assert [c["count"] for c in changes] == [1, 2]


def test_three_source_forms_converge_to_curly():
    """一本 24 章里三种引号形态的段落在规整后收敛到同一目标形态。"""
    src = "\n\n".join(
        [
            "「殿下，您醒了？」一名侍从跪在帘外。",
            '"殿下讳一个瑞字，是火之国大名。"',
            "“殿下，吉时快到了。”内侍低声催了一句。",
        ]
    )
    out, changes = normalize_prose(src)
    assert "「" not in out and "」" not in out and '"' not in out
    assert out.count("“") == out.count("”") == 3
    assert sum(c["count"] for c in changes) == 2


# ---------------------------------------------------------------------------
# 安全边界：逐字不动（反例）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        # 同族嵌套：外层开、内层开、内层闭、外层闭
        "「他说。「再来一次。」」",
        # 落单开引号（行末未配对）
        "他说：「来吧。",
        # 落单闭引号（没有开引号）
        "「来吧。」她说完，又补了一句」",
        # 真实语料的不配对行（跨段开引号，3 开 2 闭）
        "「不知道。」陈来娣摇了摇头，「出面的人遮得严实，但我听讲——她压低了声音，「——我听说，跟汇通脱不了干系。」",
        # 直引号奇数：行末未配对
        '"你来了。她说。',
        # 已有配对、但行末还挂着一个未配对的开引号（对话跨段续写形状）→ 整行不动
        "「先走。」她说。「那我呢。",
        '"先走。"她说。"那我呢。',
    ],
)
def test_unpaired_or_nested_quotes_untouched(src):
    """不配对 / 嵌套 / 落单 → 该行逐字不动（安全边界）。"""
    out, changes = normalize_prose(src)
    assert out == src
    assert changes == []


def test_dialogue_starting_with_ascii_label_line_untouched():
    """真实语料保守案例：对话内容以 ASCII 字母起头（"B级…"）时判不出开引号，
    整行不动——宁可漏改，也不让后面的引号错位配对。"""
    src = '"旧档副本的擦边球。"林策说，"B级业务权限够用，不用过审批，不用上系统。"'
    out, changes = normalize_prose(src)
    assert out == src
    assert changes == []


def test_inch_mark_and_english_dialogue_untouched():
    """英制符号与英文对白不参与配对（直引号只在引号内起头是中文时才认作对话）。"""
    src = '他量了量，屏幕有 27" 宽，够用。'
    assert normalize_prose(src) == (src, [])
    src2 = '老周说，He said "hello" 就走了。'
    assert normalize_prose(src2) == (src2, [])


def test_cross_family_nesting_line_untouched():
    """跨族嵌套：角括号包着直引号对，变换后会得到嵌套的 “…” → 整行回退。"""
    src = "「他说。\"你好\"」"
    out, changes = normalize_prose(src)
    assert out == src
    assert changes == []


def test_code_fence_and_json_lines_untouched():
    """代码围栏内的行与 JSON 键值形态行不属于正文，原样保留。"""
    src = (
        "他打开日志。\n\n"
        '{"chapter_goal": "拿走玉牌", "status": "ok"}\n\n'
        "```json\n"
        '{"note": "「示例」与\\"直引号\\""}\n'
        "```\n\n"
        "然后关掉。"
    )
    out, changes = normalize_prose(src)
    assert out == src
    assert changes == []


def test_book_title_and_dash_untouched():
    """《》与破折号不归规整算子（归风格算子），只有引号被替换。"""
    src = "他翻着《玉牌记》，第二章写着「三天」——字迹很淡。"
    out, changes = normalize_prose(src)
    assert out == "他翻着《玉牌记》，第二章写着“三天”——字迹很淡。"
    assert changes[0]["count"] == 1


# ---------------------------------------------------------------------------
# 行结构保留 / 幂等 / 变更清单
# ---------------------------------------------------------------------------


def test_line_structure_preserved():
    """只有引号字符被替换：换行、空行、行尾换行全部保留。"""
    src = "「第一句。」\n\n「第二句。」\n"
    out, _ = normalize_prose(src)
    assert out == "“第一句。”\n\n“第二句。”\n"
    assert out.count("\n") == src.count("\n")


def test_idempotent_on_mixed_passage():
    """幂等：对已规整文本再跑一次 → 文本不变且零变更。"""
    src = (
        "「殿下，您醒了？」一名侍从跪在帘外，声音压得很轻。\n\n"
        '"卯时前须沐浴更衣，奴才这就去备热水。"\n\n'
        "他撑着坐起来，环顾四周。「这是哪？」侍从愣了愣。\n\n"
        "电子屏上滚动着\"待穿局工单：3\"。\n"
    )
    once, changes_once = normalize_prose(src)
    assert changes_once
    twice, changes_twice = normalize_prose(once)
    assert twice == once
    assert changes_twice == []


def test_change_report_counts_and_samples():
    """变更清单形状：rule / source / count / samples，且按源形态固定顺序登记。"""
    src = "「茶在哪儿？」他问的不是\"能不能卖\"。\n「走吧。」她说。\n\"别回头。\"他说。\n"
    _out, changes = normalize_prose(src)
    assert [c["rule"] for c in changes] == ["NORM-QUOTE", "NORM-QUOTE"]
    assert [c["source"] for c in changes] == ["「」", '"']
    assert [c["count"] for c in changes] == [2, 2]
    assert changes[0]["samples"] == ["「茶在哪儿？」→“茶在哪儿？”", "「走吧。」→“走吧。”"]
    assert changes[1]["samples"] == ['"能不能卖"→“能不能卖”', '"别回头。"→“别回头。”']


def test_change_report_samples_capped():
    """样例最多 3 条（报告是给人定位用的，不把整章塞进去）。"""
    src = "「一。」「二。」「三。」「四。」"
    _out, changes = normalize_prose(src)
    assert changes[0]["count"] == 4
    assert len(changes[0]["samples"]) == 3


def test_no_change_returns_empty_report():
    src = "他站起身，望向天际。\n\n风从窗缝里钻进来。"
    assert normalize_prose(src) == (src, [])


def test_empty_whitespace_and_short_text_do_not_crash():
    assert normalize_prose("") == ("", [])
    assert normalize_prose("   \n\t  ") == ("   \n\t  ", [])
    assert normalize_prose("嗯。") == ("嗯。", [])
    assert normalize_prose("「」") == ("“”", [{"rule": "NORM-QUOTE", "source": "「」", "count": 1, "samples": ["「」→“”"]}])


# ---------------------------------------------------------------------------
# 内部标识检出
# ---------------------------------------------------------------------------


def test_internal_identifier_real_accident_sentence():
    """实证事故句（ch2 v2）：上下文键名 recalled_passages 漏进正文。"""
    prose = (
        "他盯着桌上那盏快灭的灯，忽然想起recalled_passages里自己的那句话："
        "价签给了他三天后的报价，却没给他三天后的剧本。"
    )
    assert find_internal_identifiers(prose) == ["recalled_passages"]


def test_internal_identifier_hits_all_three_samples():
    prose = "把story_state快照存下来，再去改chapter_goal，别让recalled_passages混进来。"
    assert find_internal_identifiers(prose) == ["story_state", "chapter_goal", "recalled_passages"]


def test_internal_identifier_upper_snake_env_var():
    prose = "日志第一行写着 NOVELOS_INSTANCE_ID，他没看懂。"
    assert find_internal_identifiers(prose) == ["NOVELOS_INSTANCE_ID"]


def test_internal_identifier_ignores_normal_english():
    """正常中文里的英文词不报：单个词（OK / PPT）与 camelCase（iPhone / AiStation）。"""
    prose = "他用 OK 结束了对话，PPT 还没做完，iPhone 屏幕亮着，AiStation 在跑模型。"
    assert find_internal_identifiers(prose) == []


def test_internal_identifier_dedupes_and_keeps_order():
    prose = "先把chapter_goal写清楚，再看story_state，最后回到chapter_goal。"
    assert find_internal_identifiers(prose) == ["chapter_goal", "story_state"]


def test_internal_identifier_empty_and_short_text():
    assert find_internal_identifiers("") == []
    assert find_internal_identifiers("   \n\t ") == []
    assert find_internal_identifiers("嗯。") == []
    assert find_internal_identifiers("他翻开《玉牌记》。") == []


def test_both_functions_are_deterministic():
    """纯函数：同一输入重复调用结果一致（不读库/不读环境/不打日志）。"""
    src = "「殿下，您醒了？」他想着recalled_passages，没有出声。"
    assert normalize_prose(src) == normalize_prose(src)
    assert find_internal_identifiers(src) == find_internal_identifiers(src)
