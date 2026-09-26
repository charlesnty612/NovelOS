"""同章远距小句复现（``AI-BEAT-REPEAT``）用例：机制、两个真实正例、误报形状、边界。

背景（2026-09-18，实测缺口）：本仓已有两条章内重复算子都只抓**复制粘贴**——
13 字滑动 shingle 率（``ai_trace``）与 trigram 率（``scoring``，阈值 0.08）。两者都按
「字面重合占全章的比例」计分，而「同一个节拍换一套词再播一遍」在比例上几乎不留痕：
``prj_2567bb8de642`` 的 ch2（shingle 比 0.0030）与 ch3 作者改稿 v1（shingle 比 0.0006）
都被判为干净，人眼却一眼看出重复。

机制（规则名即机制名，字面算子）：句读片段之间**最长公共子串** ≥6 字、同时覆盖两段
片段各自 ≥60% 字面、两处相距 ≥200 字 ⇒ 记一次「小句复现」；章级要求 ≥2 处且
>1.0/千字。**不覆盖**语义改写型重复、数字/计量串复现、跨章复现、宏观结构重复。
阈值与抽样口径见 ``packages/core/quality/beat_repeat.py`` 模块 docstring。
"""

from __future__ import annotations

from packages.core.quality.ai_patterns import AI_PATTERN_RULES, scan_ai_patterns
from packages.core.quality.beat_repeat import (
    DEFAULT_BEAT_MIN_CHARS,
    DEFAULT_BEAT_MIN_COUNT,
    DEFAULT_BEAT_MIN_COVER,
    DEFAULT_BEAT_MIN_GAP_CHARS,
    DEFAULT_BEAT_NUMERAL_MAX_RATIO,
    DEFAULT_BEAT_RATE_PER_1K,
    MIN_PROSE_CHARS_FOR_BEATS,
    beat_repeats,
    count_beat_repeats,
    scan_beat_repeats,
)
from packages.core.quality.wordcount import visible_chars

# ---------------------------------------------------------------------------
# 两个**真实**正例（原文取自 data/novelos.db，项目 prj_2567bb8de642，只读）
# ---------------------------------------------------------------------------

# 正例①：ch2（``ch_2d2b57090ecd``，最新草稿）从「沈砚把那张纸拿起来」到章末。
# 章内 shingle 比 0.0030（判为干净），但章尾把同一个收束画面写了三遍。
_CH2_RECEIPT_TAIL = """沈砚把那张纸拿起来，看了一遍。字迹工整，没有涂改，格式正确。付款方、金额、用途、时间，每一栏都填满了。

江氏集团四个字写在付款方一栏的正中间，笔划很稳。

他放下收据，拿起笔，在签名栏里写了自己的名字。两个字，一笔一划，写完后搁下笔。

“收据我留一份。”

吴律师把文件夹收好，合上公文包，站起来。“沈先生，明天见。”

沈砚没动，只看着他。

吴律师转身往门口走，皮鞋踩在地板上，一下，又一下。沈砚的目光跟着他的背影移动，记住了那个步态。不快，左脚落地时稍微重一点。门开了又关。

沈砚坐在原地，低头看着桌上那张收据的复印件。付款方一栏的“江氏集团有限公司”几个字在台灯下显得格外清楚。

他把那张纸拿起来，折了两下，收进口袋。

然后他站起来，走到窗边，把窗户推开一条缝。风从外面进来，带着一点凌晨特有的凉意。

他闭上眼睛。

账本自动浮现了。不是用眼睛看的，是直接出现在意识里的。账页摊开，上面写着几行字，墨迹像是刚干的。

第一笔是江迟的案子，顶罪，三年。第二笔也是顶罪，但时间更早，主体不是江迟。

沈砚的目光停在那个名字上。周宏。司机。两笔账，同一个人，欠方都是江曜。第一笔三年前，第二笔五年前。

他盯着那个时间戳看了一秒。五年前，比江迟这案子还早两年。

周宏沉默不是因为他不知道，而是因为他身上背着同样的事。两次。

他睁开眼睛，把账本收回去。窗外的天还是黑的，但他知道快亮了。

他拿起手机，翻出通讯录，找到一个号码。没有备注名字，只有一串数字。

他盯着那串数字看了一秒，然后按下去。

铃声响了一下，两下，三下。

那边接了，但没人先开口。背景里有一阵很轻的电流声，嗡嗡的，像空调外机。

沈砚开口了。“你身上还有一笔账。”五个字，说完他就停了。

电话那头的沉默持续了很长时间。十秒，二十秒，三十秒。

沈砚没有挂，也没有催。他把手机换到另一只手上，然后把它放在窗台上，屏幕朝上。

窗外的那条路还是空的，路灯还亮着，把地面照成一块一块的亮色。有一辆车从远处开过来，车灯打在路面上，移动得很慢，像一只耐心的眼睛。

车驶过后，路上又空了。

电话那头终于有声音了。“明天去。”三个字，说完那边就挂了。

沈砚听到一短促的忙音，然后是死寂。

他把手机拿起来，看了一眼通话记录。三个字，通话时长四分十七秒。

他把手机收进口袋，转身往屋里走。

桌上的那份收据复印件还摆着，旁边是明天要用的文件。台灯还亮着，光打在那些纸页上，把边缘照得很白。

他走到窗边，把窗户关上。玻璃合上的声音很轻，像一口气呼出来。

窗外的天际线已经有一层淡淡的亮了，像墨汁洇在宣纸上，正在向四周扩散。

明天，法院。桌上那份收据的复印件还摆着，付款方一栏的“江氏集团有限公司”几个字在晨光里格外清晰。"""

# 正例②：ch3（``ch_a48a1d708046`` **version 1**，作者改稿前）从「沈砚转过身，走回被告席」
# 到章末。章内 shingle 比 0.0006（判为干净），但「江曜坐在原告席上」这句舞台指示
# 在本章出现三次，且两处尾句只差几个字。
_CH3_COURTROOM = """沈砚转过身，走回被告席。他的步子不快不慢，脚步声在法庭里一下一下地响。江曜坐在原告席上，手按在桌面上，指节在发白。他的嘴唇张了张，想说什么，最后什么也没说出来。

法官敲了一下法槌，宣布休庭十五分钟。

沈砚坐下来，把牛皮纸袋的封口折好。账本在口袋里，他没有拿出来。但账本上多了一行字，他看得很清楚。

江曜。江氏第三顺位继承人。已扣除。

他合上账本，闭了一下眼睛。

十五分钟后，法官重新开口：“现在继续审理。”

沈砚站起来，走到被告席前。法官示意他可以继续陈述。证人在证人席上，没有离开。检察官在公诉席上，笔尖悬在本子上。江曜坐在原告席上，目光落在桌面上，手指按在桌面上，指节在发白。

他开口了，声音不大，全场都能听见。

账本翻过一页。墨迹渗进纸里，像一笔新鲜的债。

他走出法院大门的时候，阳光砸在脸上，白晃晃的。台阶上站着几个人，都是江家那边的，远远看着他，没人走近。

沈砚在台阶上站了一会儿，把手机从口袋里摸出来，看了一眼。没有新消息，屏幕是空的。他把手机收回去，往下走。

账本在口袋里，薄薄的一本，封皮磨得发白。他走下台阶，走到停车场边缘，没有停，继续往路口走。身后有人叫他，声音很远，他没回头。"""

# 两个正例里被引用的原句（用例直接断言它们在场，防止 fixture 被改写后测试失去意义）。
_CH2_SENTENCES = (
    "沈砚坐在原地，低头看着桌上那张收据的复印件。付款方一栏的“江氏集团有限公司”几个字在台灯下显得格外清楚。",
    "明天，法院。桌上那份收据的复印件还摆着，付款方一栏的“江氏集团有限公司”几个字在晨光里格外清晰。",
)
_CH3_SENTENCES = (
    "江曜坐在原告席上，手按在桌面上，指节在发白。",
    "江曜坐在原告席上，目光落在桌面上，手指按在桌面上，指节在发白。",
)

# ---------------------------------------------------------------------------
# 中性填充（各段互不重复、段内无 ≥6 字远距复现，故自身零命中）
# ---------------------------------------------------------------------------

_FILLER_A = (
    "雨停了，屋檐的水珠一颗一颗往下掉。老周把摊子上的布卷起来，用绳子捆了三道。巷口的灯笼被人取走了，"
    "只剩一根竹竿立在墙边。阿苗蹲在门槛上削一支竹签，削完吹了吹木屑，说今年的柿子比去年甜。"
    "板车过了石桥，轮子碾在青石上，一路响得厉害。天快黑的时候他们才到集市口。"
)
_FILLER_B = (
    "卖盐的老头正在收秤，看见来人只抬了一下眼皮。阿苗把筐卸下来，摆在最靠里的那排摊位后头。"
    "油灯点着以后，两个人分头去问价，回来时手里的纸片上各记了一串新数字。风从街口灌进来，"
    "吹得灯芯歪向一侧。守夜的人敲了三下梆子，声音从东边一直滚到西边。"
)
_FILLER_C = (
    "第二天清早下了霜，地上白了一层。老周先去码头看船，回来的时候带了一封口信。"
    "阿苗把信拆开看了两遍，折好塞进袖口。她没说什么，只把昨夜的账页重新翻了出来，"
    "对着灯逐条核了一遍，又在末尾添了半行小字。灶上的水开了，壶盖噗噗地跳。"
)
_FILLER_D = (
    "阿苗把算盘珠子拨了两遍，抬头说这笔对不上。老周把单子接过去，就着灯一行一行往下看，"
    "最后用指甲在第三行划了一道。屋外的狗叫了两声，又安静下来。"
    "巷子那头传来更夫的梆子声，一下一下地近了，又一下一下地远了。"
    "阿苗把灯芯剪短，火苗缩成一小团，把两个人的影子按在墙上。"
)
# 每个用例里**最多各用一次**的垫片：两段合计 >200 字，用于把待测片段推过最小间距。
_SPACER_1 = _FILLER_A + "\n\n" + _FILLER_B
_SPACER_2 = _FILLER_C + "\n\n" + _FILLER_D


def _beat_hit(text: str, **kwargs) -> dict | None:
    """只取 ``AI-BEAT-REPEAT`` 的章级命中（其余规则的命中与本文件无关）。"""
    return next(
        (h for h in scan_ai_patterns(text, **kwargs) if h["rule_id"] == "AI-BEAT-REPEAT"),
        None,
    )


# ---------------------------------------------------------------------------
# 必命中：两个真实正例
# ---------------------------------------------------------------------------


def test_receipt_closing_image_written_twice_hits():
    """正例①（ch2 真实章尾）：同一收束画面写了两遍，且都是**换了一部分词**的复现。

    关键命中「付款方一栏的江氏集团有限公司几个字在」18 字——正是 shingle 抓不到、
    人一眼看出的那种：两处只差尾句（「台灯下显得格外清楚」/「晨光里格外清晰」）。
    """
    for sentence in _CH2_SENTENCES:
        assert sentence in _CH2_RECEIPT_TAIL, "fixture 必须保持与库内原文一致"

    hits = beat_repeats(_CH2_RECEIPT_TAIL)
    assert [h.text for h in hits] == [
        "把那张纸拿起来",
        "付款方一栏的江氏集团有限公司几个字在",
    ]
    receipt = hits[1]
    assert (receipt.length, receipt.gap) == (18, 860)

    hit = _beat_hit(_CH2_RECEIPT_TAIL)
    assert hit is not None, "章级必须报警（2 处 / 1.84 每千字）"
    assert hit["count"] == 2 and hit["severity"] == "warning"
    assert hit["samples"][1] == "付款方一栏的江氏集团有限公司几个字在"


def test_courtroom_posture_beat_three_times_hits():
    """正例②（ch3 v1 真实章节片段）：同一句舞台指示出现两次，尾句只差几个字。

    两处原文：
      A「…江曜坐在原告席上，手按在桌面上，指节在发白。」
      B「…江曜坐在原告席上，目光落在桌面上，手指按在桌面上，指节在发白。」
    共享串只有「江曜坐在原告席上」（8 字）——因为两处其余部分都被改写了；
    这正说明算子补的是**骨架复用**，而不是复制粘贴。
    """
    for sentence in _CH3_SENTENCES:
        assert sentence in _CH3_COURTROOM, "fixture 必须保持与库内原文一致"

    hits = {h.text: h for h in beat_repeats(_CH3_COURTROOM)}
    assert set(hits) == {"江曜坐在原告席上", "账本在口袋里"}
    posture = hits["江曜坐在原告席上"]
    assert (posture.length, posture.gap) == (8, 230)
    assert posture.occurrences == 2

    hit = _beat_hit(_CH3_COURTROOM)
    assert hit is not None and hit["count"] == 2 and hit["rate"] == 4.1


def test_actual_measured_chapters_are_reported_by_the_scan():
    """两处正例都经**统一扫描入口**可达（不是只有直接调模块才命中）。"""
    for prose in (_CH2_RECEIPT_TAIL, _CH3_COURTROOM):
        assert any(h["rule_id"] == "AI-BEAT-REPEAT" for h in scan_ai_patterns(prose))


# ---------------------------------------------------------------------------
# 不得误报
# ---------------------------------------------------------------------------


def test_clean_prose_is_quiet():
    """中性散文（四段互不重复的日常叙述）零命中。"""
    clean = "\n\n".join((_FILLER_A, _FILLER_B, _FILLER_C, _FILLER_D))
    assert visible_chars(clean) >= MIN_PROSE_CHARS_FOR_BEATS
    assert beat_repeats(clean) == []
    assert scan_ai_patterns(clean) == [], "该 fixture 也不应触发任何其它规则"


def test_ledger_figures_are_excluded_by_numeral_guard():
    """账目数字/计量串的复现**不计**（数字守卫）——抽样实测这类全是正常复述。

    放开守卫（``numeral_max_ratio=1.01``）后必须命中，即守卫是唯一判别依据
    （突变验证用）。
    """
    prose = (
        "入账一千二百斤，每斤一钱六分。\n\n"
        + _SPACER_1
        + "\n\n林策把账册翻过来：入账一千二百斤，每斤一钱六分。"
    )
    assert beat_repeats(prose) == []
    assert _beat_hit(prose) is None

    unguarded = [h.text for h in beat_repeats(prose, numeral_max_ratio=1.01)]
    assert unguarded == ["入账一千二百斤", "每斤一钱六分"]


def test_local_repetition_within_min_gap_is_not_counted():
    """近距重复是**复制粘贴**（已由 shingle / trigram / 接词回声覆盖），不算远距复现。"""
    prose = (
        "他把那张纸拿起来，看了一遍。\n\n檐下的水滴在石阶上。\n\n灶膛里的火只剩一层红。\n\n"
        "他把那张纸拿起来，折了两下。"
    )
    assert beat_repeats(prose) == []
    assert [h.text for h in beat_repeats(prose, min_gap_chars=0)] == ["他把那张纸拿起来"]


def test_shared_frame_with_different_object_is_a_known_false_positive():
    """**已知误报形状**（照实登记，不假装算子能分辨）。

    「他看了一眼通话时长」/「他把手机拿起来，看了一眼通话记录」共用 6 字骨架而宾语
    不同，字面算子分不出这是「同一节拍」还是「同一动词用了两次」——抽样里这类占误报的
    三成。用例把它**钉成已知行为**：若将来收窄（例如要求共享串更长），本用例必须先改，
    改的时候要一并更新 beat_repeat 模块 docstring 的抽样口径。
    """
    prose = (
        "他看了一眼通话时长，四十七秒。\n\n"
        + _SPACER_1
        + "\n\n他把手机拿起来，看了一眼通话记录。"
    )
    assert [h.text for h in beat_repeats(prose)] == ["看了一眼通话"]


def test_entity_reference_repeat_is_a_known_false_positive():
    """第二类已知误报：专名/文件名的复指（同一实体被两次提名的正常写法）。

    取自实测样本（``prj_ebad..`` ch7）：对白里「来推翻族老会的决议」与旁白里
    「族老会的决议。三日验产。」——共享的是**实体名**，不是节拍。
    """
    prose = (
        "“你是要拿一块田的佃户名字，来推翻族老会的决议？”\n\n"
        + _SPACER_1
        + "\n\n族老会的决议。三日验产。逾期视为放弃。"
    )
    assert [h.text for h in beat_repeats(prose)] == ["族老会的决议"]


# ---------------------------------------------------------------------------
# 机制细节（每条都对应实现里的一个守卫）
# ---------------------------------------------------------------------------


def test_partial_overlap_inside_long_clauses_is_not_a_beat():
    """双侧覆盖率门：长片段里共有的**名词短语**不算节拍。

    「低头看着桌上那张收据的复印件」/「桌上那份收据的复印件还摆着」共 6 字
    （「收据的复印件」），但只占前一句的 6/14——那是两句不同的话碰巧都提到了同一件
    东西，不是同一段素材被搬两遍。撤掉覆盖率门这段就会变成一条命中（突变验证用）。
    """
    prose = (
        "低头看着桌上那张收据的复印件。\n\n"
        + _SPACER_1
        + "\n\n桌上那份收据的复印件还摆着。"
    )
    assert beat_repeats(prose) == []


def test_longer_shared_clause_absorbs_the_shorter_one():
    """最大性去重：被更长命中串包含的短串不重复计。

    「那张纸拿起来」（来自 P4/P5）被「他把那张纸拿起来又」（来自 P1/P2）包含，
    只报后者一次——否则同一处节拍会按子串拆成好几条。
    """
    prose = (
        "他把那张纸拿起来又放下。\n\n"
        + _SPACER_1
        + "\n\n他把那张纸拿起来又翻开。\n\n那张纸拿起来，他就走了。\n\n"
        + _SPACER_2
        + "\n\n那张纸拿起来，他没再动。"
    )
    assert [h.text for h in beat_repeats(prose)] == ["他把那张纸拿起来又"]


def test_repeated_character_clause_is_not_missed_by_the_trigram_prune():
    """DP 前的 3-gram 剪枝必须用**多重集**哨兵。

    「哈哈哈哈哈啊哈」只有少量**不同**的 3-gram；若用集合取交（≤3 < 6-2）就会把
    这一对误剪掉。本用例钉住多重集口径（改成集合必红）。
    """
    prose = "哈哈哈哈哈啊哈。\n\n" + _SPACER_1 + "\n\n哈哈哈哈哈啊哈。"
    assert [h.text for h in beat_repeats(prose)] == ["哈哈哈哈哈啊哈"]


def test_same_clause_outside_min_gap_and_counted_once_with_occurrences():
    """同一小句出现多次：章级只算**一处**复现，``occurrences`` 记出现次数。"""
    prose = (
        "江曜坐在原告席上，手按在桌面上，指节在发白。\n\n"
        + _SPACER_1
        + "\n\n江曜坐在原告席上，目光落在桌面上。\n\n"
        + _SPACER_2
        + "\n\n江曜坐在原告席上，没有说话。"
    )
    hits = beat_repeats(prose)
    assert [h.text for h in hits] == ["江曜坐在原告席上"]
    assert hits[0].occurrences == 3


def test_fragment_below_min_prose_chars_is_not_reported():
    """章级最小判定长度（照 ``_MIN_PROSE_CHARS_FOR_DENSITY`` 先例）：片段不判密度。"""
    tiny = (
        "江曜坐在原告席上，手按在桌面上。\n\n檐下的水滴在石阶上。\n\n"
        "江曜坐在原告席上，目光落在桌面上。"
    )
    assert visible_chars(tiny) < MIN_PROSE_CHARS_FOR_BEATS
    assert len(beat_repeats(tiny, min_gap_chars=0)) == 1, "算子本身命中"
    assert scan_beat_repeats(tiny, min_gap_chars=0) == [], "片段没有统计意义，章级不判"


def test_pile_up_gate_requires_two_hits():
    """堆积门（照 AI-LONG-PARA「单章 ≥2 段」先例）：单章单处不报。

    人类基线 19 章全部 ≤1 处，故该门在人类侧零误报；单处更可能只是正常复指。
    """
    single = "江曜坐在原告席上，手按在桌面上，指节在发白。\n\n" + _SPACER_1 + "\n\n江曜坐在原告席上，目光落在桌面上。"
    assert len(beat_repeats(single)) == 1
    assert _beat_hit(single) is None, "只有一处不报"
    assert _beat_hit(single, beat_min_count=1) is not None, "阈值降到 1 即报（阈值生效）"

    two = _CH3_COURTROOM
    assert len(beat_repeats(two)) == 2
    assert _beat_hit(two) is not None


def test_count_beat_repeats_matches_the_calibration_entry():
    """``count_beat_repeats``（校准脚本的抽样入口）与 ``beat_repeats`` 同源。"""
    assert count_beat_repeats(_CH3_COURTROOM) == [
        h.text for h in beat_repeats(_CH3_COURTROOM)
    ]


# ---------------------------------------------------------------------------
# 规则登记与阈值常量
# ---------------------------------------------------------------------------


def test_rule_metadata_registered_and_reachable():
    """规则元数据表收录 AI-BEAT-REPEAT，默认 warning；命中经统一扫描入口可达。"""
    row = next((r for r in AI_PATTERN_RULES if r.rule_id == "AI-BEAT-REPEAT"), None)
    assert row is not None
    assert row.severity == "warning"
    assert "{count}" in row.message
    assert "字面" in row.description, "名字必须说明它量的是字面复现（防名实不符）"
    assert "不覆盖" in row.description, "必须写明不覆盖范围"

    hit = _beat_hit(_CH2_RECEIPT_TAIL)
    assert hit is not None and hit["rule_id"] == row.rule_id


def test_severity_is_always_warning():
    """severity 恒 warning——提示而非闸门；本模块不含任何 error 路径。"""
    for prose in (_CH2_RECEIPT_TAIL, _CH3_COURTROOM):
        hit = _beat_hit(prose)
        assert hit is not None and hit["severity"] == "warning"
    # 阈值抬到不可能达到的值后必须零命中（突变验证用）
    assert _beat_hit(_CH2_RECEIPT_TAIL, beat_rate_per_1k=999.0) is None


def test_thresholds_are_pinned():
    """阈值常量登记（校准依据见 beat_repeat 模块 docstring）。

    改这几个数就等于改规则口径：6 字是抽样去掉「同前缀不同谓语」误报后的下界；
    0.6 双侧覆盖率门挡掉「长片段里的局部重合」；200 字把复制粘贴让给既有算子；
    0.6 数字占比守卫排除账目复述；≥2 处 / >1.0 每千字是章级堆积门（人类侧零误报）。
    """
    assert DEFAULT_BEAT_MIN_CHARS == 6
    assert DEFAULT_BEAT_MIN_COVER == 0.6
    assert DEFAULT_BEAT_MIN_GAP_CHARS == 200
    assert DEFAULT_BEAT_NUMERAL_MAX_RATIO == 0.6
    assert DEFAULT_BEAT_MIN_COUNT == 2
    assert DEFAULT_BEAT_RATE_PER_1K == 1.0
    assert MIN_PROSE_CHARS_FOR_BEATS == 200
