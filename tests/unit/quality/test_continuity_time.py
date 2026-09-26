"""章内时点矛盾算子（``CONT-CLOCK-DAYBREAK`` / ``CONT-TIME-BACKSTEP``）的判别测试。

正文数据在内容仓（三仓分离：正文不进软件仓）；人类基线只读定位，不在本机时跳过
（环境前提缺失，非断言软化）。生成侧样本一律**逐句引用**生产正文原句，不整章复制。

校准抽样表（27 条候选逐条人工过目；这是「抽样先于采信」的留痕，不是装饰）
----------------------------------------------------------------------------
候选空间的定义是「**加守卫前的**全部候选」，含一个最终被废弃的候选检查。生成侧
92 章 / 263,347 可见字；人类侧（榜一侯府弧 19 章 / 38,566 字符）**候选数为 0**——
该基线书里一个钟点标记都没有（「点」出现 64 次全是「一点/有点/点头」），
故「人类侧 0 命中」不含信息，本算子的人类侧数字不可采信。

==== ============== ============================================ ====== ==========================
#    章节            候选（加守卫前）                             判定    依据 / 排除它的守卫
==== ============== ============================================ ====== ==========================
A01  e642#ch2       凌晨两点十七分(@574) → 晨光(@2864)           真     同章无推进交代（章末「天际线…亮了」）
B01  ebad#ch6       亥时(21h) → 午时(11h) 回退 10h               真     夜读旧档后直接跳「午时，正堂」
B02  ebad#ch14      子时(23h) → 戌时(19h) 回退 4h                误     行内「"**子时之前**。"」＝期限语（段首戳＋右邻守卫）
B03  ebad#ch17      子时(23h) → 戌时三刻(19h) 回退 4h            误     「子时**将近**」「**还有**两个时辰」倒计时（段首戳）
B04  ebad#ch20      午时三刻(11h) → 辰时(7h) 回退 4h             误     章内明写「---//**次日**辰时」（段首戳）
B05  f5a6#ch1       子时(23h) → 卯时(5h)                        误     跨午夜顺行 + 行内「于**明日**卯时」礼书预定（段首戳）
B06  0bd4#ch29      午时三刻(11h) → 辰时(7h) 回退 4h             误     行内「军户们围过来的时候是午时三刻」等（段首戳）
B07  e642#ch3       上午九点(9h) → 凌晨三时四十二分(3h) 回退 6h    误     行内「于**三年前**十一月十九日凌晨三时四十二分」＝起诉书引述
B08  fe5e#ch3       晚上八点(20h) → 下午五点半(17h) 回退 3h      误     行内「**距离**晚上八点的家宴」＋换到另一人的时间线（段首戳）
C01  86aa#ch5       天亮 → 夜里（「**从天黑拨到天亮，再从天亮拨到天黑**」）  误     引语里的作息描写，非场景时钟
C02  86aa#ch5       天亮 → 夜里（同上，另一处）                  误     同上
C03  86aa#ch7       早上 → 子时                                误     引语内「第二天早上我去敲门」＝回述
C04  86aa#ch7       早上 → 夜里                                误     同上（夜里是当夜场景）
C05  0bd4#ch10      早晨 → 夜里                                误     引语内目击者叙述昨夜纵火
C06  0bd4#ch10      早晨 → 半夜                                误     同上
C07  0bd4#ch10      早晨 → 子时                                误     同上
C08  0bd4#ch11      一早 → 夜里                                误     「**明日**一早，上北山」是决定/计划
C09  0bd4#ch30      清晨 → 凌晨                                误     明写「第一日清晨」→「**第二日**凌晨」
C10  0bd4#ch30      清晨 → 凌晨                                误     同上（第三日）
C11  0bd4#ch31      早上 → 夜里                                误     「火在他身后烧了一整夜，**第二天**早上」
C12  0bd4#ch39      早晨 → 夜里                                误     「**第三天**早晨」→「**那天**夜里」
C13  0bd4#ch39      早上 → 夜里                                误     同上（泥是早上刚落的雨＝同一场雨的回指）
C14  e642#ch1       早上 → 夜里                                误     「**明天**早上也可以改成别的」＝假设
D01  e642#ch1       三点四十二分（裸钟点）                        不判   无前缀，且是账目/起诉书里的回述时刻
D02  e642#ch1       十一点四十七分（裸钟点）                      不判   同上
D03  e642#ch1       三点二十六分（裸钟点）                        不判   **歧义实证**：配文「距离明天上午九点的庭，
                                                                     还有十七个小时三十四分」——3:26+17:34=21:00 不成立，
                                                                     15:26+17:34=次日 09:00 才成立 ⇒ 它指下午
D04  e642#ch3       三点多（裸钟点）                            不判   「那晚三点多」＝回述
==== ============== ============================================ ====== ==========================

结论：**C 组检查（白天标记 → 夜晚标记）整体废弃**（14／14 误报——一章从早写到夜是
正常跨日叙事，该检查没有区分力）；**B 组 8 条候选只有 1 条真**（precision 12.5%），
加守卫后只剩 B01；**A 组**保持原样命中 A01；**D 组（裸钟点）不判**。加守卫后生成侧
2 处 / 2 章 = 0.0076/千字。

**本表的局限（照实说）**：B 组守卫是**在同一批 8 条候选上逐层定出来的**，没有留出
验证集；其中「段首戳」一条即可排除本仓全部 7 条误报，其余三条（右邻期限语 / 跨午夜
顺行 / 时间推进标记）是纵深，本仓没有实例证明它们各自必要。A 组是 1 候选 1 真、零拟合。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from packages.core.content_sync.paths import DEFAULT_CONTENT_DIR
from packages.core.quality import continuity_time as ct
from packages.core.quality.ai_patterns import (
    AI_PATTERN_RULES,
    DEFAULT_MAX_GAP_CHARS,
    DEFAULT_MIN_BACKSTEP_HOURS,
    scan_ai_patterns,
)

# ---------------------------------------------------------------------------
# 真实夹具（逐句引用自 data/novelos.db 的生产正文；不整章复制）
# ---------------------------------------------------------------------------

# ch_2d2b57090ecd（prj_2567bb8de642 第 2 章草稿 v2）——真实命中 A01。
_CLOCK_DAYBREAK_REAL = (
    "门铃响了。沈砚抬头看了一眼墙上的钟，凌晨两点十七分。有人在这个时间登门，不可能是邻居。\n\n"
    "他站起来，走向门口。手指搭在门把上停了一秒，然后拉开。\n\n"
    "吴律师转身往门口走，皮鞋踩在地板上，一下，又一下。门开了又关。\n\n"
    "他拿起手机，翻出通讯录，找到一个号码。没有备注名字，只有一串数字。\n\n"
    "他把手机收进口袋，转身往屋里走。\n\n"
    "他走到窗边，把窗户关上。玻璃合上的声音很轻，像一口气呼出来。\n\n"
    "窗外的天际线已经有一层淡淡的亮了，像墨汁洇在宣纸上，正在向四周扩散。\n\n"
    "明天，法院。桌上那份收据的复印件还摆着，付款方一栏的“江氏集团有限公司”几个字在晨光里格外清晰。"
)

# 同一场景，但作者**交代了**时间推进（「他熬到天亮」）——不该报。
_CLOCK_DAYBREAK_ADVANCE = (
    "门铃响了。沈砚抬头看了一眼墙上的钟，凌晨两点十七分。有人在这个时间登门，不可能是邻居。\n\n"
    "他熬到天亮，才把那口气吐出来。\n\n"
    "窗外已经是大白天，付款方一栏的几个字在晨光里格外清晰。"
)

# 同一时钟，但章内没有任何天亮标记——不该报。
_CLOCK_ONLY = (
    "门铃响了。沈砚抬头看了一眼墙上的钟，凌晨两点十七分。有人在这个时间登门，不可能是邻居。\n\n"
    "他站起来，走向门口。窗外还是黑的，路灯把地面照成一块一块的亮色。"
)

# prj_5be9256febad 第 6 章——真实命中 B01（夜读 → 正堂，中间无任何推进交代）。
_BACKSTEP_REAL = (
    "三下。她在告诉他，还有第三个变数。\n\n"
    "亥时，内室只剩一盏油灯。\n\n"
    "林策坐在灯下，摊开从嘉靖三十年旧档里抽出的核销存根，把过户申请表和旧档并排放好。\n\n"
    "“同源的……”他轻声自语，“有意思。”\n\n"
    "午时，正堂。\n\n"
    "两排长案相对而立，当中空出一条过道。族老会来了三位，坐在上首。"
)

# 三条守卫各自的真实误报形态（原句见上表 B03/B08/B05）。为了让每条守卫都是**唯一**
# 排除者，段首戳都摆成段首（B02/B03/B04 在原文里被段首戳挡掉，此处构造得只剩该守卫）。
_BACKSTEP_DEADLINE = (
    "子时将近，账房偏厅的灯火还没有点起来。\n\n"
    "戌时三刻。还有两个时辰。"
)
_BACKSTEP_PLAN = (
    "午时三刻，粮行门口挤满了军户，太阳还挂在营房后面。\n\n"
    "明天辰时，粮行门口排队，一人限两斗。"
)
_BACKSTEP_MIDNIGHT = (
    "晚上十一点四十七分，一笔现金付给了死者家属。\n\n"
    "凌晨三点四十二分，滨江路与翠微巷交叉口。"
)
# 段首戳守卫面对的**行内引用**型真实误报（上表 B07/B08）：两种都不得报。
_BACKSTEP_INLINE_DOC = (
    "审判长核对身份，宣读权利，敲了一下法槌。\n\n"
    "公诉人站起来念起诉书：被告人江迟，于三年前十一月十九日凌晨三时四十二分，酒后驾驶。\n\n"
    "开庭是上午九点。"
)
_BACKSTEP_INLINE_POV = (
    "她站在大楼门口，看着手机屏幕上的时间——下午六点四十分。距离晚上八点的家宴还有不到两个小时。\n\n"
    "陆沉的手机在下午五点半准时响起来。"
)
# 天亮标记出现在时钟**之前**（跨日回照）：规则 A 只看它之后，这类不该由 A 报。
_DAYBREAK_BEFORE_CLOCK = (
    "他熬到天亮才合眼，窗外的光一点点挪进来。\n\n"
    "门铃响了。沈砚抬头看了一眼墙上的钟，凌晨两点十七分。"
)


def _hits(prose: str, rule_id: str) -> list[dict]:
    return [h for h in scan_ai_patterns(prose) if h["rule_id"] == rule_id]


# ---------------------------------------------------------------------------
# 真实夹具：两个算子各一条真命中
# ---------------------------------------------------------------------------


def test_real_chapter_flags_early_clock_against_daybreak():
    """A01：真实缺陷章（凌晨两点十七分 → 晨光）必须命中，severity 恒 warning。"""
    rounds = ct.time_conflicts(_CLOCK_DAYBREAK_REAL)
    daybreak = [h for h in rounds if h.rule_id == "CONT-CLOCK-DAYBREAK"]
    assert len(daybreak) == 1, f"应恰好命中 1 处，实际 {daybreak}"
    assert daybreak[0].first.text == "凌晨两点十七分"
    assert daybreak[0].second.text == "晨光"

    hits = _hits(_CLOCK_DAYBREAK_REAL, "CONT-CLOCK-DAYBREAK")
    assert len(hits) == 1
    assert hits[0]["severity"] == "warning"
    assert hits[0]["count"] == 1
    assert "凌晨两点十七分" in hits[0]["message"]


def test_real_chapter_flags_time_backstep():
    """B01：真实缺陷章（亥时 → 午时，回退 10 小时）必须命中。"""
    hits = _hits(_BACKSTEP_REAL, "CONT-TIME-BACKSTEP")
    assert len(hits) == 1, f"应恰好命中 1 处，实际 {hits}"
    assert "亥时" in hits[0]["samples"][0] and "午时" in hits[0]["samples"][0]
    assert hits[0]["severity"] == "warning"


# ---------------------------------------------------------------------------
# 干净正文负例（守卫的实际判别力）
# ---------------------------------------------------------------------------


def test_advance_marker_between_markers_suppresses_hit():
    """两标记之间出现时间推进标记（「他熬到天亮」）→ 不报。"""
    assert ct.time_conflicts(_CLOCK_DAYBREAK_ADVANCE) == []
    assert not _hits(_CLOCK_DAYBREAK_ADVANCE, "CONT-CLOCK-DAYBREAK")


def test_clock_without_daybreak_marker_is_clean():
    """整章只有深夜时钟、没有任何天亮标记 → 不报。"""
    assert ct.time_conflicts(_CLOCK_ONLY) == []


def test_daybreak_before_the_clock_is_not_rule_a():
    """规则 A 只看**时钟之后**的天亮标记；天亮在前（跨日回照）不由它报。"""
    assert not _hits(_DAYBREAK_BEFORE_CLOCK, "CONT-CLOCK-DAYBREAK")
    assert ct.time_conflicts(_DAYBREAK_BEFORE_CLOCK) == []


def test_deadline_attached_marker_is_not_a_backstep():
    """B03 形态：「子时将近」是期限/倒计时语，不算现场时点戳。"""
    assert not _hits(_BACKSTEP_DEADLINE, "CONT-TIME-BACKSTEP")


def test_planned_future_marker_is_not_a_backstep():
    """B06 形态：「明天辰时」是计划（行内），不构成段首时点戳。"""
    assert not _hits(_BACKSTEP_PLAN, "CONT-TIME-BACKSTEP")
    # 反证：把「明天」去掉后同样的两个时点就是真回退——差别只在那个计划语
    assert _hits("午时三刻，粮行门口挤满了军户。\n\n辰时，粮行门口排队。", "CONT-TIME-BACKSTEP")


def test_inline_reference_markers_are_not_scene_stamps():
    """B07/B08 形态：起诉书里的历史时刻、期限语——行内时点一律不算场景时点戳。"""
    assert not _hits(_BACKSTEP_INLINE_DOC, "CONT-TIME-BACKSTEP")
    assert not _hits(_BACKSTEP_INLINE_POV, "CONT-TIME-BACKSTEP")
    assert not ct.is_paragraph_stamp(
        _BACKSTEP_INLINE_DOC, ct.time_markers(_BACKSTEP_INLINE_DOC)[-2]
    )


def test_midnight_wrap_is_forward_not_backward():
    """B05/D 形态：23:47 → 次日 03:42 字面递减，实际顺行，不得报。

    两个标记都摆在段首（否则会被段首戳挡掉，测不出跨午夜守卫本身）。
    """
    assert not _hits(_BACKSTEP_MIDNIGHT, "CONT-TIME-BACKSTEP")
    # 反证：把两个时点都做成同侧（下午 → 上午）确实该报，说明上面不是「恰好」生效
    assert _hits(
        "下午三点，他合上账本。\n\n上午九点，他重新翻开账本。", "CONT-TIME-BACKSTEP"
    )


def test_planned_future_marker_still_guards_rule_a():
    """规则 A 的计划语守卫：「明天早上」是计划，不构成天亮标记（段首戳不适用于 A）。"""
    prose = (
        "门铃响了。沈砚抬头看了一眼墙上的钟，凌晨两点十七分。\n\n"
        "明天早上还要去法院，他提醒自己。"
    )
    assert not _hits(prose, "CONT-CLOCK-DAYBREAK")


def test_shichen_pairs_are_compared_on_real_hours():
    """时辰按**起始小时**折算：亥时(21) → 子时(23) 是顺行，不得报（防序号化写法）。

    序号化（亥=11 → 子=0）会把这条正常跨午夜的推进算成「回退 11 小时」。
    """
    prose = "亥时的时空管理局，比白天更安静。\n\n子时，交接的人来了。"
    assert not _hits(prose, "CONT-TIME-BACKSTEP")
    # 同一条对子换成真回退方向（子时 23h → 午时 11h）必须报，说明上面不是「时辰对子永不报」
    assert _hits("子时，交接的人来了。\n\n午时，交接的人在门口等着。", "CONT-TIME-BACKSTEP")


def test_one_adverb_does_not_match_a_clock():
    """「一点」副词不抽成钟点（本仓 102 处「一点」全是副词，曾把「一点一点」配成 1:01）。"""
    prose = "他一点也不慌。她一点一点地把纸推过去。差一点点就成功了。"
    assert ct.time_markers(prose) == []


def test_early_window_is_bounded_to_the_small_hours():
    """「深夜档」窗口锁在 00:00–06:00：「凌晨九点」这种前缀写错不进入 A 规则。

    没有这条上界，任何以「凌晨」开头的钟点都会参与天亮配对——那属于另一类缺陷
    （时辰前缀自相矛盾），不该由本算子连带报出。
    """
    prose = "凌晨九点，他推开门，外面已经排起了队。"
    markers = [m for m in ct.time_markers(prose) if m.kind == "clock"]
    assert markers and markers[0].hour == 9.0
    assert not ct.is_early_clock(markers[0])
    assert ct.time_conflicts(prose) == []


def test_bare_clock_is_never_judged_early():
    """裸钟点「三点二十六分」不判早晚——同章实证它指下午 15:26（见模块 docstring D03）。"""
    prose = "沈砚从衬衫口袋里摸出手机，看了一眼时间。三点二十六分。距离明天上午九点的庭，还有十七个小时三十四分钟。"
    markers = [m for m in ct.time_markers(prose) if m.kind == "clock"]
    naked = [m for m in markers if m.period is None]
    assert [m.text for m in naked] == ["三点二十六分"]
    assert naked[0].hour is None
    assert not ct.is_early_clock(naked[0])
    assert ct.time_conflicts(prose) == []


def test_early_clocks_with_minutes_and_fullwidth_colon_are_extracted():
    """三种要求覆盖的钟点形态都能抽到（有前缀/有分/冒号）。"""
    prose = "凌晨三时四十二分，他醒了。下午 3 点 42 分，她走了。09：15，会议开始。"
    texts = [m.text for m in ct.time_markers(prose)]
    assert "凌晨三时四十二分" in texts
    assert any("3 点 42 分" in t for t in texts)
    assert any("09：15" in t for t in texts)


# ---------------------------------------------------------------------------
# 规则登记与阈值常量（防回退）
# ---------------------------------------------------------------------------


def test_rules_registered_as_warning_with_scope_disclaimer():
    """两条规则登记进 AI_PATTERN_RULES，severity=warning，且写明**不覆盖**范围。"""
    for rule_id in ("CONT-CLOCK-DAYBREAK", "CONT-TIME-BACKSTEP"):
        row = next((r for r in AI_PATTERN_RULES if r.rule_id == rule_id), None)
        assert row is not None, f"{rule_id} 未登记"
        assert row.severity == "warning"
        assert "{count}" in row.message
        assert "不覆盖" in row.description, f"{rule_id} 必须写明不覆盖范围（防名实不符）"
        assert "字面" in row.description, f"{rule_id} 必须说明它量的是字面标记"


def test_severity_never_becomes_error():
    """severity 恒 warning——提示而非闸门；本组不含任何 error 路径。"""
    for prose in (_CLOCK_DAYBREAK_REAL, _BACKSTEP_REAL):
        for hit in scan_ai_patterns(prose):
            if hit["rule_id"].startswith("CONT-"):
                assert hit["severity"] == "warning", hit


def test_max_gap_threshold_pinned_with_distribution_note():
    """间距阈值必须停在「一章量级」——本仓唯一真命中 2283 字。

    钉住这个值是因为它**不可由数据分辨**：语料里没有更长的候选，所以任何「按分布
    收紧」的小值（如按 2283 取 2500）都是**对单样本过拟合**。把阈值改到 2283 以下
    本测转红——那是拟合，不是校准。
    """
    assert DEFAULT_MAX_GAP_CHARS == 3000
    assert DEFAULT_MAX_GAP_CHARS > 2283, "阈值不得收紧到唯一真命中的间距之下（单样本拟合）"
    gap = ct.time_conflicts(_CLOCK_DAYBREAK_REAL)[0].gap
    assert gap < DEFAULT_MAX_GAP_CHARS


def test_min_backstep_and_shichen_mapping_pinned():
    """回退阈值 2 小时 + 时辰→起始小时映射：两者共同决定「亥时 → 子时」是顺行。"""
    assert DEFAULT_MIN_BACKSTEP_HOURS == 2.0
    assert ct.SHICHEN_START_HOURS["亥"] == 21.0
    assert ct.SHICHEN_START_HOURS["子"] == 23.0
    assert ct.SHICHEN_START_HOURS["午"] == 11.0
    # 若把时辰改成序号比较（亥=11 → 子=0），下面这条真命中就会消失
    assert ct.time_conflicts(_BACKSTEP_REAL), "真实回退命中不得因换算口径改动而消失"


def test_hits_are_ordered_by_rule_then_position():
    """输出顺序稳定：CONT-CLOCK-DAYBREAK 在前、CONT-TIME-BACKSTEP 在后（确定性）。"""
    prose = _CLOCK_DAYBREAK_REAL + "\n\n" + _BACKSTEP_REAL
    ids = [h.rule_id for h in ct.time_conflicts(prose)]
    assert ids == ["CONT-CLOCK-DAYBREAK", "CONT-TIME-BACKSTEP"]


# ---------------------------------------------------------------------------
# 接线：命中必须自动落到 review_report（沿用 AI-BEAT-REPEAT 的同款落点）
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    from packages.core.db import apply_migrations

    db_path = tmp_path / "test.db"
    apply_migrations(db_path, Path(__file__).resolve().parents[3] / "database" / "migrations")
    return db_path


def _insert_chapter_with_draft(db_path: Path, prose: str) -> str:
    from packages.core.db import get_connection
    from packages.core.ids import new_id, now_iso
    from packages.core.quality.wordcount import visible_chars

    pid, cid, now = new_id("prj"), new_id("ch"), now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status, "
            "created_at, updated_at) VALUES (?, '项目', NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, "
            "visibility, who_knows, created_at, updated_at) VALUES "
            "(?, ?, 1, '', '{}', 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, now, now),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) VALUES (?, ?, 1, ?, 'test:writer:v1', "
            "NULL, NULL, ?)",
            (new_id("drf"), cid, prose, now),
        )
        conn.commit()
    finally:
        conn.close()
    assert visible_chars(prose) > 0
    return cid


def test_hits_land_in_review_report_warnings(tmp_path: Path):
    """命中经 `scan_ai_patterns` 自动进 `review_report`：warning 档 → warnings 文本行。

    这一段守的是「新增规则不需要动 ``chapter_review/pipeline.py``」这条通则
    （basic_checks 按 severity 分流），但**必须实测**——把 severity 改成 error
    就会从 warnings 挪到 errors，作者在评审 UI 上看到的入口随之改变。
    """
    from packages.workflows.chapter_review.pipeline import _basic_checks_node

    prose = _CLOCK_DAYBREAK_REAL + "\n\n" + _BACKSTEP_REAL
    db_path = _fresh_db(tmp_path)
    cid = _insert_chapter_with_draft(db_path, prose)
    report = _basic_checks_node(
        {"db_path": str(db_path), "chapter_id": cid, "target_word_count": 100}
    )["review_report"]

    hit_ids = {h["rule_id"] for h in report["ai_pattern_hits"]}
    assert {"CONT-CLOCK-DAYBREAK", "CONT-TIME-BACKSTEP"} <= hit_ids, hit_ids
    assert any("[CONT-CLOCK-DAYBREAK]" in w for w in report["warnings"]), report["warnings"]
    assert not [e for e in report["errors"] if str(e.get("rule_id", "")).startswith("CONT-")]


# ---------------------------------------------------------------------------
# 人类基线：机会数为 0（把「不可采信」这件事本身钉住，防止被当成零误报证据）
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REFERENCE_SUBPATH = Path("reference-books/榜一-快穿之人渣洗白手册/侯府凤凰男")


def _reference_dir() -> Path | None:
    """定位榜一侯府弧正文目录；内容仓不在本机时返回 None。"""
    candidates: list[Path] = []
    env_dir = os.environ.get("NOVELOS_CONTENT_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(DEFAULT_CONTENT_DIR)
    candidates.append(_REPO_ROOT.parent / "NovelOS-Content")
    for root in candidates:
        target = root / _REFERENCE_SUBPATH
        if target.is_dir():
            return target
    return None


def _reference_chapters() -> list[tuple[str, str]]:
    ref_dir = _reference_dir()
    if ref_dir is None:  # pragma: no cover - 环境前提缺失
        pytest.skip(f"内容仓参考书目录不存在：{_REFERENCE_SUBPATH}")
    numbered: list[tuple[int, Path]] = []
    for path in ref_dir.glob("*.txt"):
        m = re.search(r"第(\d+)章", path.name)
        if m:
            numbered.append((int(m.group(1)), path))
    numbered.sort(key=lambda item: item[0])
    assert len(numbered) >= 19, f"榜一侯府弧应有 19 章，实际 {len(numbered)}"
    return [(f"第{no}章", path.read_text(encoding="utf-8")) for no, path in numbered]


def test_human_baseline_has_no_clock_opportunity_and_zero_hits():
    """人类基线：零命中，且**零机会**——所以这条断言不能读成「对人类正文零误报」。

    人类基线里没有钟点（「点」全是「一点/有点/点头」），时辰只有 2 处且都不是时点，
    因此本算子在该语料上无判别机会。若哪天基线换了（或有人往内容仓塞了带钟点的
    人类样本），本测试会因 markers 数变化而转红——那时必须重算 docstring 里的基线表。
    """
    chapters = _reference_chapters()
    markers = 0
    hits = 0
    for _label, prose in chapters:
        markers += len(ct.time_markers(prose))
        hits += len(ct.time_conflicts(prose))
    assert hits == 0, f"人类基线书出现时点矛盾命中 {hits} 处——需人工复核（真缺陷 or 误报？）"
    assert markers == 2, (
        f"人类基线时点标记数从登记值 2 变为 {markers}——基线书换了，"
        "docstring 的「机会数为 0」结论与人类侧数字必须一并重算"
    )
