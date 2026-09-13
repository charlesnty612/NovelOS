"""§2.1 聚合公式单元测试（tests.unit.quality.test_aggregate）。

覆盖：
- 七维平均（七子分 → overall）
- V3.9 批次 3.1：blocking error ⇒ overall = 0；informational error ⇒ 保留部分分
- 缺失子分 ⇒ scoring_missing_subscore + overall = 0（blocking）
- formula_hash 长度 = 16，且覆盖 severity 矩阵 / 阻断白名单 / 规则级覆盖
- formula_text 含七维平均口径与 blocking 口径
"""

from __future__ import annotations

import pytest

from packages.core.quality import compute_overall, formula_hash
from packages.core.quality import issues as issues_mod
from packages.core.quality.aggregate import formula_text, severity_config_text
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


def test_aggregate_blocking_error_zero():
    """blocking error（白名单内 rule_id）⇒ overall = 0。"""
    subscores = dict(
        plot=95, character=95, continuity=95, style=95,
        pacing=95, foreshadowing=95, ai_trace=95,
    )
    issues = [
        _issue("error", category="character_contradiction", rule_id="RULE_CHAR_DEAD_ACTIVE"),
    ]
    overall, out = compute_overall(subscores, issues)
    assert overall == 0
    # error 自身保留在 issues
    assert any(i.severity == "error" for i in out)


def test_aggregate_informational_error_keeps_partial_score():
    """V3.9 批次 3.1：informational error（不在阻断白名单）不压死 overall。

    同一 fixture：error 级 issue 只进 issues 列表，overall 仍为七维平均。
    """
    subscores = dict(
        plot=95, character=95, continuity=95, style=95,
        pacing=95, foreshadowing=95, ai_trace=95,
    )
    issues = [
        _issue("error", category="payoff", rule_id="RULE_H3_FILLER_3CH"),
    ]
    overall, out = compute_overall(subscores, issues)
    assert overall == 95, "informational error 应保留部分分"
    assert any(i.severity == "error" and i.rule_id == "RULE_H3_FILLER_3CH" for i in out)


def test_aggregate_blocking_and_informational_mixed():
    """blocking 与 informational 混在时：blocking 归零优先。"""
    subscores = dict(
        plot=95, character=95, continuity=95, style=95,
        pacing=95, foreshadowing=95, ai_trace=95,
    )
    issues = [
        _issue("error", category="payoff", rule_id="RULE_H3_FILLER_3CH"),
        _issue("error", category="compliance", rule_id="RULE_Q6_OVERLAP_RATE"),
    ]
    overall, _ = compute_overall(subscores, issues)
    assert overall == 0


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
    """formula_text 应含七维平均口径 + blocking/informational 口径。"""
    text = formula_text()
    assert "ai_trace" in text
    assert "/7" in text or "/ 7" in text
    assert "BLOCKING_RULES" in text


def test_formula_hash_covers_severity_matrix(monkeypatch: pytest.MonkeyPatch):
    """突变验证：改 severity 矩阵内容 ⇒ formula_hash 必须变（矩阵变更=公式变更）。"""
    before = formula_hash()
    fake_matrix = {k: dict(v) for k, v in issues_mod.MVP_SEVERITY_MATRIX.items()}
    fake_matrix["timeline_consistency"]["mvp_max"] = "error"
    monkeypatch.setattr(issues_mod, "MVP_SEVERITY_MATRIX", fake_matrix)
    assert formula_hash() != before


def test_formula_hash_covers_blocking_whitelist(monkeypatch: pytest.MonkeyPatch):
    """突变验证：改阻断白名单 ⇒ formula_hash 必须变。"""
    before = formula_hash()
    monkeypatch.setattr(
        issues_mod,
        "BLOCKING_RULES",
        issues_mod.BLOCKING_RULES | {"RULE_H3_FILLER_3CH"},
    )
    assert formula_hash() != before


def test_formula_hash_covers_rule_overrides(monkeypatch: pytest.MonkeyPatch):
    """突变验证：改规则级默认 severity ⇒ formula_hash 必须变。"""
    before = formula_hash()
    fake_overrides = dict(issues_mod.MVP_RULE_OVERRIDES)
    fake_overrides["RULE_Q8_HUMAN_RATIO_LOW"] = "error"
    monkeypatch.setattr(issues_mod, "MVP_RULE_OVERRIDES", fake_overrides)
    assert formula_hash() != before


def test_severity_config_text_mentions_blocking_rules():
    text = severity_config_text()
    assert "blocking:" in text
    assert "RULE_CHAR_DEAD_ACTIVE" in text


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
