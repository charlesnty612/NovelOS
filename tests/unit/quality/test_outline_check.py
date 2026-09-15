"""`packages.core.quality.outline_check` 的单元测试。

覆盖两件事：
1. **每条判据真的会命中**（召回侧）——否则判据形同虚设；
2. **干净大纲不被误报**（精确率侧）——工具的价值全在信噪比上。

阈值全部按本仓语料校准（见模块常量注释），测试钉住的是**行为**不是数字，
改阈值时看这里的断言是否仍在守同一件事。
"""

from __future__ import annotations

from packages.core.quality.outline_check import check_outline


def _ch(goal="", beats=None, role="setup", hook=None):
    return {"chapter_goal": goal, "key_beats": beats or [], "expected_role": role,
            "hook_handling": hook or []}


def _ids(findings):
    return {f.rule_id for f in findings}


def test_empty_outline_reports_alert():
    assert _ids(check_outline([])) == {"OUTLINE-EMPTY"}


def test_clean_outline_has_no_alerts():
    """六章健康大纲：每章有主动推进、有代价、反派出招、钩子具体 —— 不应有 alert。"""
    chs = [
        _ch("林策主动设局，用假账引出裴府管事", ["他主动设局", "当场对质", "自己折了三百两作饵"], "setup",
            ["裴元绍连夜派人来查"]),
        _ch("裴元绍下套拘人，林策失了一日自由", ["裴元绍下套拘人", "林策失去一日", "周伯变卖薄田凑保金"], "escalation",
            ["保金送到时，差役提了一句'裴府点了名'"]),
        _ch("林策主动投状，反被夺去递状资格", ["他主动投状", "被夺资格", "欠下人情"], "turn",
            ["堂上有人问：这份状子谁写的"]),
        _ch("裴秉出手截留奏本，林策损失一次机会", ["裴秉截留奏本", "林策失去上达机会", "抵押了铺面换盘缠"], "escalation",
            ["奏本原封退回，封皮上多了一道朱批"]),
        _ch("林策主动夜访，拿到新证人", ["主动夜访", "重金买通更夫", "失去仅剩的玉佩"], "setup",
            ["更夫认出那枚玉佩是他家旧物"]),
        _ch("裴元绍断他粮道，林策改走水路", ["裴元绍断了粮道", "林策损失两成货", "改走水路"], "turn",
            ["水路上有人在等他"]),
    ]
    findings = check_outline(chs, antagonist_names=["裴元绍", "裴秉"])
    assert not [f for f in findings if f.level == "alert"], \
        f"健康大纲被误报：{[(f.rule_id, f.message[:40]) for f in findings if f.level == 'alert']}"


def test_role_mix_flags_climax_overload():
    """climax 占比过高要报——事故版大纲曾达 38%，榜一 spine 里 climax 为 0。"""
    chs = [_ch("x", ["他主动设局", "代价三百两"], "climax") for _ in range(4)] + \
          [_ch("y", ["他主动设局", "代价"], "setup") for _ in range(6)]
    f = [x for x in check_outline(chs) if x.rule_id == "OUTLINE-ROLE-MIX"][0]
    assert f.level == "alert" and "40%" in f.message


def test_conjure_evidence_flags_missing_provenance():
    """金手指直接交出原件/底稿要报（上一版 ch11 的实证事故）。"""
    chs = [_ch("林策呈上考官的程文作为证据", ["呈上所存的程文原件"], "turn")]
    f = [x for x in check_outline(chs) if x.rule_id == "OUTLINE-CONJURE-EVIDENCE"][0]
    assert f.level == "alert" and "ch1" in f.evidence[0]


def test_bad_hook_patterns_flagged():
    """「此人不可小觑」「一双眼睛」这类钩子要报。"""
    for tail in ("裴元绍退走。此人不可小觑", "书房窗后有一双眼睛正看着他"):
        chs = [_ch("x", ["他主动设局", "代价"], "setup", [tail])]
        assert "OUTLINE-BAD-HOOK" in _ids(check_outline(chs)), tail


def test_passive_antagonist_run_flagged():
    """连续 >3 章没有反派行动要报——**且全 setup（无高潮位）时不豁免**。"""
    chs = [_ch("他主动设局，代价是三百两", ["主动设局", "代价"], "setup", ["新线索出现"])
           for _ in range(5)]
    assert "OUTLINE-PASSIVE-ANTAGONIST" in _ids(
        check_outline(chs, antagonist_names=["裴元绍"]))


def test_beats_count_thresholds():
    """过少（<2）报 alert；本仓 outliner 稳定产 5 条，上限校准到 5。"""
    thin = [_ch("x", ["只有一条"], "setup")]
    assert "OUTLINE-THIN-BEATS" in _ids(check_outline(thin))

    five = [_ch("他主动设局，代价三百两", ["a", "b", "c", "d", "e"], "setup", ["具体新事态"])]
    assert "OUTLINE-FAT-BEATS" not in _ids(check_outline(five))

    six = [_ch("他主动设局，代价三百两", list("abcdef"), "setup", ["具体新事态"])]
    assert "OUTLINE-FAT-BEATS" in _ids(check_outline(six))


def test_no_cost_outline_flagged():
    """全书零代价语义要报——这是「零成本通关」的直接检测。"""
    chs = [_ch("他主动设局，一路顺利", ["主动设局", "顺利拿下", "众人叹服"], "setup", ["新事态"])
           for _ in range(4)]
    f = [x for x in check_outline(chs) if x.rule_id == "OUTLINE-NO-COST"]
    assert f and f[0].level == "alert"
