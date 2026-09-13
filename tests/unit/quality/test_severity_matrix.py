"""V3.9 批次 3.1：severity 矩阵 / 阻断白名单 / blocking 判定。

覆盖：
- 阻断白名单内容快照（变更必须显式改测试，避免静默漂移）；
- ``is_blocking_issue`` 的 blocking / informational 判定；
- ``rule_default_severity`` 规则级覆盖（Q8 → warning）与矩阵回退；
- 矩阵一致性：产出 error 的规则要么在阻断白名单、要么是显式 informational（Q8 默认 warning）。
"""

from __future__ import annotations

from packages.core.quality.issues import (
    BLOCKING_RULES,
    MVP_RULE_OVERRIDES,
    MVP_SEVERITY_MATRIX,
    Issue,
    is_blocking_issue,
    make_issue,
    mvp_max_severity,
    rule_default_severity,
    severity_config_fingerprint,
)


def _issue(severity: str, rule_id: str, category: str = "compliance") -> Issue:
    return make_issue(
        severity=severity,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        rule_id=rule_id,
        message="x",
    )


def test_blocking_whitelist_snapshot():
    """白名单快照：结构性损坏 5 条 + 系统级子分缺失（V3.9 批次 3.1/3.3 裁决）。"""
    assert BLOCKING_RULES == frozenset(
        {
            "SCHEMA_VALIDATION_FAILED",
            "RULE_CHAR_DEAD_ACTIVE",
            "RULE_WORLD_HARD_RULE_CHANGED",
            "RULE_Q6_OVERLAP_RATE",
            "RULE_Q8_HUMAN_RATIO_LOW",
            "scoring_missing_subscore",
        }
    )


def test_is_blocking_issue_true_for_whitelisted_error():
    assert is_blocking_issue(_issue("error", "SCHEMA_VALIDATION_FAILED"))
    assert is_blocking_issue(
        _issue("error", "RULE_CHAR_DEAD_ACTIVE", category="character_contradiction")
    )


def test_is_blocking_issue_false_for_error_outside_whitelist():
    """informational error：severity=error 但不在白名单 ⇒ 不阻断。"""
    assert not is_blocking_issue(_issue("error", "RULE_H3_FILLER_3CH", category="payoff"))
    assert not is_blocking_issue(_issue("error", "RULE_STYLE_REPETITION_TRIGRAM", category="style"))


def test_is_blocking_issue_false_for_warning_or_info():
    assert not is_blocking_issue(_issue("warning", "RULE_CHAR_DEAD_ACTIVE"))
    assert not is_blocking_issue(_issue("info", "RULE_Q6_NO_REFERENCES"))
    assert not is_blocking_issue(None)


def test_q8_default_severity_override_is_warning():
    """Q8 规则级默认值 = warning（V3.9 批次 3.3 裁决）；category 矩阵仍是 error 上限。"""
    assert MVP_RULE_OVERRIDES["RULE_Q8_HUMAN_RATIO_LOW"] == "warning"
    assert rule_default_severity("RULE_Q8_HUMAN_RATIO_LOW", "compliance") == "warning"
    assert mvp_max_severity("compliance") == "error"


def test_rule_default_severity_falls_back_to_matrix():
    assert rule_default_severity("RULE_H1_NO_END_HOOK", "payoff") == "warning"
    assert rule_default_severity("SCHEMA_VALIDATION_FAILED", "schema_validity") == "error"


def test_matrix_covers_all_categories_with_known_values():
    for cat, row in MVP_SEVERITY_MATRIX.items():
        assert row["mvp_max"] in ("error", "warning", "info"), cat


def test_severity_fingerprint_is_deterministic_and_sensitive():
    a = severity_config_fingerprint()
    assert a == severity_config_fingerprint()
    assert "blocking:" in a
    assert "compliance=error" in a
