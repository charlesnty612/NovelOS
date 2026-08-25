"""Style 跨章趋势监测单测（tests.unit.quality.test_style_trend）。

覆盖：

1. **纯函数 :func:`check_style_trend`** —— 正反例 / 边界 / 阈值口径。
2. **engine 集成**：传 ``recent_style_scores`` 时 issues 含 ``RULE_STYLE_TREND_DECAY``；
   不传时 issues 不含该 rule（兼容路径）。
3. **零影响证明**：构造同一 QualityContext 传 / 不传 ``recent_style_scores``，
   断言除**新增的 trend issue**外其余字段（七子分 + overall + _meta.scoring_formula_hash）
   完全相等 —— 证明现有评分路径与 baseline 判定不受影响。

设计约束：

- 与现有 ``test_engine.py`` 共用 ``_pass_ctx`` 风格：构造最小可评估 ctx。
- 不依赖 DB / workflow —— engine 是纯函数。
"""

from __future__ import annotations

from packages.core.quality import (
    DECAY_THRESHOLD,
    RULE_ID,
    WINDOW,
    QualityContext,
    QualityEngine,
    check_style_trend,
)
from packages.core.quality.aggregate import formula_hash

# ============================================================================
# 纯函数：正反例 + 边界
# ============================================================================


def test_trend_none_returns_none():
    assert check_style_trend(None) is None


def test_trend_empty_returns_none():
    assert check_style_trend([]) is None


def test_trend_short_window_returns_none():
    # 不足 WINDOW（3）章 → 不触发
    assert check_style_trend([80]) is None
    assert check_style_trend([80, 70]) is None


def test_trend_flat_does_not_trigger():
    # 平盘（末两章相等）→ 严格单调不成立 → 不触发
    assert check_style_trend([80, 70, 70]) is None
    assert check_style_trend([80, 80, 80]) is None


def test_trend_rising_does_not_trigger():
    # 上升序列 → 不触发
    assert check_style_trend([65, 70, 80]) is None
    assert check_style_trend([60, 70, 75]) is None


def test_trend_non_monotonic_does_not_trigger():
    # 非严格单调（中间反弹）→ 不触发
    assert check_style_trend([60, 80, 70]) is None
    assert check_style_trend([80, 60, 70]) is None
    assert check_style_trend([80, 75, 80]) is None


def test_trend_below_threshold_does_not_trigger():
    # 严格单调但累计降幅不足 15 → 不触发
    assert check_style_trend([80, 75, 70]) is None  # 累计 10
    assert check_style_trend([80, 75, 66]) is None  # 累计 14


def test_trend_boundary_15_triggers():
    # 累计降幅恰好等于阈值 → 触发（>= 语义）
    issue = check_style_trend([80, 70, 65])
    assert issue is not None
    assert issue.severity == "warning"
    assert issue.rule_id == RULE_ID
    assert issue.category == "style"


def test_trend_strict_decline_above_threshold_triggers():
    # 累计降幅超过阈值 → 触发
    issue = check_style_trend([80, 70, 60])
    assert issue is not None
    assert issue.severity == "warning"
    assert issue.rule_id == RULE_ID
    assert issue.category == "style"
    assert "80" in issue.message and "70" in issue.message and "60" in issue.message


def test_trend_window_takes_last_n():
    # 长度 > WINDOW 时仅取末尾 3 章
    assert check_style_trend([100, 80, 70, 60]) is not None  # 末尾 [80,70,60]
    assert check_style_trend([60, 80, 70, 60]) is not None  # 末尾 [80,70,60]
    assert check_style_trend([60, 60, 80, 70]) is None  # 末尾 [60,80,70] 上升


def test_trend_module_constants():
    # 锁定 M2-C 文档口径（阈值 / 窗口 / rule_id）
    assert WINDOW == 3
    assert DECAY_THRESHOLD == 15
    assert RULE_ID == "RULE_STYLE_TREND_DECAY"


# ============================================================================
# Engine 集成：传入 recent_style_scores 时 issues 含 RULE_STYLE_TREND_DECAY
# ============================================================================


def _ctx_for_trend(
    recent_style_scores=None,
    *,
    chapter_id: str = "ch_trend_001",
) -> QualityContext:
    """构造一个最小可评估的 QualityContext（与 test_engine._pass_ctx 等价的最小变体）。"""
    draft = (
        "今天天气真好，阳光洒在青石板上。\n\n"
        + "我们一起去公园散步，远处的鸟儿在枝头鸣唱。\n\n"
        + "午后咖啡馆的音乐若有若无，温暖而安静。\n\n"
        + "傍晚的风带来远山的凉意，人间值得。\n\n"
        + "突然，一道黑影闪过山巅，就在此时不见？"
    )
    delta = {
        "delta_id": "d_test",
        "delta_version": 1,
        "chapter_id": chapter_id,
        "workflow_run_id": "wfr_test",
        "previous_state_version": 1,
        "created_by": "observer:test",
        "created_at": "2026-08-23T00:00:00Z",
        "schema_version": "state-delta-v0",
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [
            {
                "change_id": "e1",
                "op": "add",
                "target_id": "ev_1",
                "event_id": "ev_1",
                "type": "encounter",
                "cause": [],
                "effects": [],
                "participants": ["char_a"],
                "time": {"timeline_day": 5, "in_story_date": None},
                "description": "在山间遭遇山贼",
                "confidence": 1.0,
                "evidence": {"chapter_id": chapter_id, "excerpt": "在山间遭遇山贼"},
                "risk_level": "LOW",
            }
        ],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    snap = {
        "state_version": 1,
        "characters": [],
        "events": {},
        "hooks": [],
        "world": {"world_rules": []},
    }
    fields: dict = dict(
        chapter_id=chapter_id,
        chapter_number=5,
        draft=draft,
        plan={"key_beats": [{"summary": "山间遭遇山贼"}]},
        snapshot_pre=snap,
        delta=delta,
        payoff_history=[1, 1],
        ai_chars=60,
        human_chars=40,
    )
    if recent_style_scores is not None:
        fields["recent_style_scores"] = list(recent_style_scores)
    return QualityContext(**fields)


def test_engine_no_trend_field_no_issue():
    """默认 None 时 issues 不含 RULE_STYLE_TREND_DECAY（兼容路径）。"""
    engine = QualityEngine()
    report = engine.evaluate(_ctx_for_trend())
    assert not any(i.rule_id == RULE_ID for i in report.issues)


def test_engine_short_trend_no_issue():
    """长度 < WINDOW 时不触发。"""
    engine = QualityEngine()
    report = engine.evaluate(_ctx_for_trend(recent_style_scores=[80, 70]))
    assert not any(i.rule_id == RULE_ID for i in report.issues)


def test_engine_trend_declining_emits_issue():
    """严格单调下降 + 累计 >= 阈值 → 触发 warning。"""
    engine = QualityEngine()
    report = engine.evaluate(_ctx_for_trend(recent_style_scores=[80, 70, 60]))
    trend_issues = [i for i in report.issues if i.rule_id == RULE_ID]
    assert len(trend_issues) == 1
    assert trend_issues[0].severity == "warning"
    assert trend_issues[0].category == "style"


def test_engine_trend_rising_no_issue():
    """上升序列 → 不触发。"""
    engine = QualityEngine()
    report = engine.evaluate(_ctx_for_trend(recent_style_scores=[60, 70, 80]))
    assert not any(i.rule_id == RULE_ID for i in report.issues)


# ============================================================================
# 零影响证明（DoD §4）：传/不传 recent_style_scores → 七子分 + overall + formula_hash
# 完全一致（仅 issues 多一条 trend rule）
# ============================================================================


def test_engine_trend_zero_impact_on_subscores_and_overall():
    """同一 QualityContext 传 / 不传 recent_style_scores，七子分 + overall + formula_hash
    完全一致；issues 仅多一条 RULE_STYLE_TREND_DECAY。

    这是 M2-C 「零影响」承诺的可执行证明：保证既有的 golden baseline / 单测中任何
    overall / subscores 断言不会因为新增字段而漂移。
    """
    engine = QualityEngine()

    # 触发严格单调下降 + 累计降幅 20 的退化序列
    scores = [80, 70, 60]

    base_ctx = _ctx_for_trend(recent_style_scores=None)
    trend_ctx = _ctx_for_trend(recent_style_scores=scores)

    base_report = engine.evaluate(base_ctx)
    trend_report = engine.evaluate(trend_ctx)

    # 1) 七子分完全一致
    for name in (
        "plot",
        "character",
        "continuity",
        "style",
        "pacing",
        "foreshadowing",
        "ai_trace",
    ):
        assert getattr(base_report, name) == getattr(trend_report, name), (
            f"subscore {name!r} drifted: "
            f"base={getattr(base_report, name)} trend={getattr(trend_report, name)}"
        )

    # 2) overall 完全一致（warning 不阻断）
    assert base_report.overall == trend_report.overall

    # 3) _meta.scoring_formula_hash 完全一致（公式哈希未变）
    assert (
        base_report.meta["scoring_formula_hash"]
        == trend_report.meta["scoring_formula_hash"]
    )
    # 锁定公式哈希值（与 golden baseline 报告一致）
    assert base_report.meta["scoring_formula_hash"] == formula_hash()

    # 4) issues 差异：仅多一条 trend rule，其余完全一致
    base_rule_ids = sorted(i.rule_id for i in base_report.issues)
    trend_rule_ids = sorted(i.rule_id for i in trend_report.issues)
    assert trend_rule_ids == sorted(base_rule_ids + [RULE_ID]), (
        f"issues drift: base={base_rule_ids} trend={trend_rule_ids}"
    )


def test_engine_trend_zero_impact_default_ctx():
    """旧调用方（不传 recent_style_scores）行为完全一致 —— QualityContext 新增字段
    默认 None 不影响 evaluate 的所有非 issues 输出。

    与既有 ``test_engine_end_to_end_pass`` 对齐：ctx 不含新字段时不应引入任何
    ``RULE_STYLE_TREND_DECAY`` issue，七子分 / overall / _meta 维持既有口径。
    """
    engine = QualityEngine()
    ctx = _ctx_for_trend()
    # recent_style_scores 默认 None：旧调用方口径
    assert ctx.recent_style_scores is None
    report = engine.evaluate(ctx)
    assert not any(i.rule_id == RULE_ID for i in report.issues)
    # 与既有测试口径一致
    assert report.overall >= 0
    assert isinstance(report.meta["scoring_formula_hash"], str)
    assert len(report.meta["scoring_formula_hash"]) == 16
