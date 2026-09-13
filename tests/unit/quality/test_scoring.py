"""六子分规则单元测试（tests.unit.quality.test_scoring）。

目标：每个子分至少 1 例典型路径 + 1 例异常路径（若适用）。
"""

from __future__ import annotations

from packages.core.quality.issues import Issue
from packages.core.quality.scoring import (
    score_character,
    score_continuity,
    score_foreshadowing,
    score_pacing,
    score_plot,
    score_style,
)


def _delta(new_events=None, character_changes=None, new_hooks=None, resolved_hooks=None):
    return {
        "new_events": new_events or [],
        "character_changes": character_changes or [],
        "new_hooks": new_hooks or [],
        "resolved_hooks": resolved_hooks or [],
    }


def _snap(characters=None, events=None):
    return {"characters": characters or [], "events": events or {}, "hooks": []}


def _issue(severity="warning", rule_id="RULE_X", category="character_contradiction"):
    return Issue(
        severity=severity,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        rule_id=rule_id,
        message="x",
    )


# ============================================================================
# plot
# ============================================================================


def test_plot_full_pass():
    plan = {"key_beats": [{"summary": "开启战斗"}, {"summary": "撤退"}]}
    delta = _delta(new_events=[
        {"name": "开启战斗", "description": "与敌遭遇"},
        {"name": "撤退", "description": "战略后退"},
    ])
    score, _ = score_plot(plan, delta)
    assert score == 100


def test_plot_missing_beats():
    plan = {"key_beats": [{"summary": "开启战斗"}, {"summary": "撤退"}]}
    delta = _delta(new_events=[{"name": "开启战斗", "description": "与敌遭遇"}])
    score, _ = score_plot(plan, delta)
    assert score == 95  # 缺 1 条 -5


def test_plot_extra_events():
    plan = {"key_beats": [{"summary": "开启战斗"}]}
    delta = _delta(new_events=[
        {"name": "开启战斗", "description": "与敌遭遇"},
        {"name": "意外", "description": "路人"},
        {"name": "幕后", "description": "黑幕"},
        {"name": "更意外", "description": "再路人"},
        {"name": "再幕后", "description": "再黑幕"},
    ])
    score, _ = score_plot(plan, delta)
    # key_beats=1, 新事件=5 => 超额=2 ⇒ -20
    assert score == 80


def test_plot_no_plan_info_neutral():
    score, issues = score_plot({}, _delta())
    assert score == 85
    assert any(i.rule_id == "RULE_PLOT_NO_PLAN" for i in issues)


# ============================================================================
# character
# ============================================================================


def test_character_full_match():
    snap = _snap(characters=[
        {
            "character_id": "c1",
            "current_state": {"location": "山脚"},
        }
    ])
    delta = _delta(character_changes=[
        {
            "op": "update",
            "character_id": "c1",
            "field": "location",
            "before": "山脚",
        }
    ])
    score, _ = score_character(snap, delta)
    assert score == 100


def test_character_partial_match():
    snap = _snap(characters=[
        {"character_id": "c1", "current_state": {"location": "山脚"}},
        {"character_id": "c2", "current_state": {"location": "海角"}},
    ])
    delta = _delta(character_changes=[
        {
            "op": "update",
            "character_id": "c1",
            "field": "location",
            "before": "山脚",
        },
        {
            "op": "update",
            "character_id": "c2",
            "field": "location",
            "before": "错误位置",  # 不一致
        },
    ])
    score, _ = score_character(snap, delta)
    assert 0 <= score <= 100
    assert score < 100  # 至少有一个不一致


# ============================================================================
# continuity
# ============================================================================


def test_continuity_no_issues_full_score():
    score, _ = score_continuity([])
    assert score == 100


def test_continuity_warning_half():
    iss = [_issue("warning", rule_id="RULE_CHAR_DEAD_ACTIVE")]
    # RULE_CHAR_DEAD_ACTIVE 全额 30；warning ×0.5 = 15
    score, _ = score_continuity(iss)
    assert score == 100 - 15


def test_continuity_error_full():
    iss = [_issue("error", rule_id="RULE_WORLD_HARD_RULE_CHANGED")]
    # RULE_WORLD_HARD_RULE_CHANGED 全额 30；error 全额
    score, _ = score_continuity(iss)
    assert score == 70


def test_continuity_same_rule_deduped():
    iss = [
        _issue("error", rule_id="RULE_TIMELINE_NON_MONOTONIC"),
        _issue("error", rule_id="RULE_TIMELINE_NON_MONOTONIC"),
    ]
    # 同 rule_id 一章只扣 1 次（25）
    score, _ = score_continuity(iss)
    assert score == 75


def test_continuity_floor_zero():
    # 多个严重 error，但下限不能跌破 0
    iss = [
        _issue("error", rule_id="RULE_CHAR_DEAD_ACTIVE"),
        _issue("error", rule_id="RULE_WORLD_HARD_RULE_CHANGED"),
        _issue("error", rule_id="RULE_TIMELINE_NON_MONOTONIC"),
        _issue("error", rule_id="RULE_KNOWLEDGE_LEAK"),
    ]
    score, _ = score_continuity(iss)
    assert score >= 0


# ============================================================================
# style
# ============================================================================


def test_style_normal():
    draft = (
        "今天天气真好，阳光洒在青石板上。"
        + "我们一起去公园散步，远处的鸟儿在枝头鸣唱。"
        + "午后咖啡馆的音乐若有若无，温暖而安静。"
        + "傍晚的风带来远山的凉意，人间值得。"
    )
    score, issues = score_style(draft)
    assert 0 <= score <= 100
    # 该 draft 各 trigram 分散，不应触发阈值
    assert not any(i.rule_id == "RULE_STYLE_REPETITION_TRIGRAM" for i in issues)


def test_style_ai_marker_dense():
    sent = "首先其次最后但是不仅正常文字用于测试句子。"
    draft = sent * 30
    score, issues = score_style(draft)
    # AI marker 密度高 ⇒ 触发扣分；但因同样密集可能有 trigram 重复 ⇒ 两条 warning
    assert score < 100


def test_style_trigram_repetition():
    trigram = "测试重复片段"
    draft = (trigram * 50)
    score, issues = score_style(draft)
    assert any(i.rule_id == "RULE_STYLE_REPETITION_TRIGRAM" for i in issues)


def test_style_dialogue_ratio_out_of_range():
    # 全是对话，引号内占 100% ⇒ 出 [0.15, 0.65] ⇒ -5
    draft = '"对话"' * 50
    score, _ = score_style(draft)
    assert score <= 95


# ============================================================================
# pacing
# ============================================================================


def test_pacing_default_pass_mid_score():
    paras = [
        "第一段内容。",
        "第二段对话占比适中。",
        "第三段叙事停顿。",
        "第四段情节推进。",
        "末段竟然有钩子。",
    ]
    draft = "\n\n".join(paras)
    score, _ = score_pacing(draft)
    assert 0 <= score <= 100


def test_pacing_flat_low_score():
    # 五段对话密度都极低且相近 ⇒ 极差 < 0.05 ⇒ -20
    paras = ["平平淡淡无对话。"] * 5
    draft = "\n\n".join(paras)
    score, _ = score_pacing(draft)
    # 末段无钩子 ⇒ 再 -15
    assert score <= 100 - 20


def test_pacing_no_end_hook():
    paras = ["段落" + str(i) + "。" for i in range(5)]
    draft = "\n\n".join(paras)
    score, _ = score_pacing(draft)
    # 末段 "段落4。" 无钩子 marker ⇒ -15
    assert score <= 95


def test_pacing_short_no_op():
    assert score_pacing("")[0] == 100
    assert score_pacing("单段无对话。")[0] >= 0


# ============================================================================
# foreshadowing（V3.9 批次 3.2：埋设/兑现双通道）
# ============================================================================


def _hooks(n: int) -> list[dict]:
    return [{"hook_id": f"h{i}"} for i in range(n)]


def test_foreshadowing_no_hooks_neutral():
    score, issues = score_foreshadowing({}, _delta())
    assert score == 85
    assert any(i.rule_id == "RULE_FORESHADOW_NO_HOOKS" for i in issues)


def test_foreshadowing_all_resolved():
    """只兑不埋 = 100（兑现是满分锚点）。"""
    score, _ = score_foreshadowing(
        {},
        _delta(new_hooks=[], resolved_hooks=_hooks(2)),
    )
    assert score == 100


def test_foreshadowing_mix_new_and_resolved():
    """埋 1 兑 1 = round(100 × (1 + 0.9) / 2) = 95。"""
    score, _ = score_foreshadowing(
        {},
        _delta(new_hooks=_hooks(1), resolved_hooks=_hooks(1)),
    )
    assert score == 95


def test_foreshadowing_plant_only_beats_neutral():
    """铺垫章（只埋不兑）不得低于中性分 85 —— 旧口径的结构性倒挂修复点。

    旧公式为纯兑现率：埋 3 兑 0 ⇒ 0 分（低于「完全不涉及钩子」的 85 中性分，
    诱导作者无视伏笔）；新公式 = round(100 × (R + 0.9N) / (R + N)) ⇒ 90。
    """
    for n in (1, 3, 5, 20):
        score, _ = score_foreshadowing({}, _delta(new_hooks=_hooks(n)))
        assert score == 90, f"埋 {n} 兑 0 应为 90（中性偏上），实际 {score}"
        assert score >= 85


def test_foreshadowing_value_table():
    """双通道公式取值表（对照表见 README §5.2 / 批次 3 报告）。"""
    cases = [
        # (new, resolved, expected)
        (0, 0, 85),  # 不涉及钩子 ⇒ 中性分（info）
        (1, 0, 90),  # 只埋
        (3, 0, 90),  # 铺垫章（旧口径 0 分）
        (0, 1, 100),  # 只兑
        (0, 5, 100),
        (1, 1, 95),  # 埋+兑
        (3, 1, 92),  # round(100 × 3.7 / 4) = round(92.5) = 92（banker's rounding）
        (1, 3, 98),  # round(100 × 3.9 / 4) = round(97.5) = 98
        (3, 3, 95),
        (2, 1, 93),  # round(100 × 2.8 / 3) = 93
        (1, 2, 97),  # round(100 × 2.9 / 3) = 97
        (5, 1, 92),  # round(100 × 5.5 / 6) = round(91.67) = 92
    ]
    for new_n, resolved_n, expected in cases:
        score, _ = score_foreshadowing(
            {}, _delta(new_hooks=_hooks(new_n), resolved_hooks=_hooks(resolved_n))
        )
        assert score == expected, f"new={new_n} resolved={resolved_n} → {score} != {expected}"


def test_foreshadowing_same_involvement_resolved_higher():
    """同为「涉及 2 个钩子」，兑得多者分更高（兑现权重 > 埋设权重）。"""
    plant_heavy, _ = score_foreshadowing({}, _delta(new_hooks=_hooks(2)))
    even, _ = score_foreshadowing(
        {}, _delta(new_hooks=_hooks(1), resolved_hooks=_hooks(1))
    )
    payoff_heavy, _ = score_foreshadowing({}, _delta(resolved_hooks=_hooks(2)))
    assert plant_heavy < even < payoff_heavy
    assert payoff_heavy == 100


def test_foreshadowing_not_dict_delta():
    assert score_foreshadowing({}, None)[0] == 100  # type: ignore[arg-type]
