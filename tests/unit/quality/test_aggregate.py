"""§2.1 聚合公式单元测试（tests.unit.quality.test_aggregate）。

覆盖：
- 加权聚合（六子分 → overall）
- 任意 error ⇒ overall = 0
- 缺失子分 ⇒ scoring_missing_subscore + overall = 0
- formula_hash 长度 = 16
"""

from __future__ import annotations

from packages.core.quality import compute_overall, formula_hash
from packages.core.quality.aggregate import formula_text
from packages.core.quality.issues import Issue


def _issue(severity: str = "warning", category: str = "character_contradiction", rule_id: str = "RULE_X"):
    return Issue(
        severity=severity,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        rule_id=rule_id,
        message="x",
    )


def test_aggregate_weighted_sum_spec_example():
    """spec §1.1 示例数值：plot=91 char=89 cont=94 style=78 pace=82 fore=90。

    计算：0.20*91 + 0.20*89 + 0.20*94 + 0.15*78 + 0.15*82 + 0.10*90
        = 18.2 + 17.8 + 18.8 + 11.7 + 12.3 + 9.0
        = 87.8 ⇒ round = 88
    """
    subscores = dict(plot=91, character=89, continuity=94, style=78, pacing=82, foreshadowing=90)
    overall, issues = compute_overall(subscores, [])
    assert overall == 88
    assert issues == []


def test_aggregate_error_zero():
    subscores = dict(plot=95, character=95, continuity=95, style=95, pacing=95, foreshadowing=95)
    overall, issues = compute_overall(subscores, [_issue("error")])
    assert overall == 0
    # error 自身保留在 issues
    assert any(i.severity == "error" for i in issues)


def test_aggregate_warning_keeps_overall():
    subscores = dict(plot=91, character=89, continuity=94, style=78, pacing=82, foreshadowing=90)
    overall, issues = compute_overall(subscores, [_issue("warning")])
    assert overall == 88


def test_aggregate_missing_subscore_makes_error_and_zero():
    subscores = dict(plot=91, character=89, continuity=94, style=78, pacing=82)
    # foreshadowing 缺
    overall, issues = compute_overall(subscores, [])
    assert overall == 0
    assert any(
        i.severity == "error" and i.rule_id == "scoring_missing_subscore"
        for i in issues
    )


def test_aggregate_all_missing():
    overall, issues = compute_overall({}, [])
    assert overall == 0
    assert sum(1 for i in issues if i.rule_id == "scoring_missing_subscore") == 6


def test_formula_hash_length():
    h = formula_hash()
    assert isinstance(h, str)
    assert len(h) == 16


def test_formula_text_contains_weights():
    text = formula_text()
    assert "0.20*plot" in text
    assert "0.20*character" in text
    assert "0.10*foreshadowing" in text


def test_aggregate_clamp_within_zero_hundred():
    """确认子分被 clamp 到 [0, 100]（防御）。"""
    # 极端：所有子分都是 100 ⇒ overall 必 ≤ 100
    subscores = dict(plot=100, character=100, continuity=100, style=100, pacing=100, foreshadowing=100)
    overall, _ = compute_overall(subscores, [])
    assert 0 <= overall <= 100
    assert overall == 100
