"""§2.1 聚合公式单元测试（tests.unit.quality.test_aggregate）。

覆盖：
- 七维平均（七子分 → overall）
- 任意 error ⇒ overall = 0
- 缺失子分 ⇒ scoring_missing_subscore + overall = 0
- formula_hash 长度 = 16
- formula_text 含七维平均口径
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


def test_aggregate_seven_dim_average():
    """七维平均示例：plot=91 char=89 cont=94 style=78 pace=82 fore=90 ai_trace=85。

    计算：(91+89+94+78+82+90+85)/7 = 609/7 = 87.0 ⇒ round = 87
    """
    subscores = dict(
        plot=91,
        character=89,
        continuity=94,
        style=78,
        pacing=82,
        foreshadowing=90,
        ai_trace=85,
    )
    overall, issues = compute_overall(subscores, [])
    assert overall == 87
    assert issues == []


def test_aggregate_error_zero():
    subscores = dict(
        plot=95, character=95, continuity=95, style=95,
        pacing=95, foreshadowing=95, ai_trace=95,
    )
    overall, issues = compute_overall(subscores, [_issue("error")])
    assert overall == 0
    # error 自身保留在 issues
    assert any(i.severity == "error" for i in issues)


def test_aggregate_warning_keeps_overall():
    subscores = dict(
        plot=91, character=89, continuity=94, style=78,
        pacing=82, foreshadowing=90, ai_trace=85,
    )
    overall, issues = compute_overall(subscores, [_issue("warning")])
    assert overall == 87


def test_aggregate_missing_subscore_makes_error_and_zero():
    subscores = dict(
        plot=91, character=89, continuity=94, style=78, pacing=82, foreshadowing=90,
    )
    # ai_trace 缺
    overall, issues = compute_overall(subscores, [])
    assert overall == 0
    assert any(
        i.severity == "error" and i.rule_id == "scoring_missing_subscore"
        for i in issues
    )


def test_aggregate_all_missing():
    overall, issues = compute_overall({}, [])
    assert overall == 0
    assert sum(1 for i in issues if i.rule_id == "scoring_missing_subscore") == 7


def test_formula_hash_length():
    h = formula_hash()
    assert isinstance(h, str)
    assert len(h) == 16


def test_formula_text_contains_seven_dim_average():
    """formula_text 应含七维平均口径。"""
    text = formula_text()
    assert "ai_trace" in text
    assert "/7" in text or "/ 7" in text


def test_aggregate_clamp_within_zero_hundred():
    """确认子分被 clamp 到 [0, 100]（防御）。"""
    # 极端：所有子分都是 100 ⇒ overall 必 ≤ 100
    subscores = dict(
        plot=100, character=100, continuity=100, style=100,
        pacing=100, foreshadowing=100, ai_trace=100,
    )
    overall, _ = compute_overall(subscores, [])
    assert 0 <= overall <= 100
    assert overall == 100