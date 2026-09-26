"""P0-1（2026-09-18）：后果轴 ``Gate`` —— severity 之外的第二根轴。

覆盖：
- ``issue_gate`` 解析顺序（block > confirm > 字段上修 > auto）与不可降级性；
- ``CONFIRM_RULES`` 内容快照（只有确定性可复算的重复算子，无任何占比/比值算子）；
- **量级依赖的规则走单条上修**：``RULE_STYLE_REPETITION_TRIGRAM`` 2026-09-18 撤出静态
  表（阈值 0.08 未被分布支撑，人类锚点书 9/19 章都超 ⇒ 门禁变橡皮图章），改由
  ``scoring.score_style`` 在超 confirm 阈值时给该条打 ``gate="confirm"``；
- confirm 档**不**把 overall 归零，但必须出现在 ``GateSummary`` / 报告 ``_meta`` 里；
- ``severity_config_fingerprint`` / ``formula_hash`` 覆盖 confirm 白名单（口径变更可追溯）。

事故背景：某章 9 段逐字重复（章内重复 20.3% / trigram 30.37%）照常提交，review
``errors: []``、quality ``overall: 90``。根因不是「重复不严重」，而是 severity 三档
之下后果只有「过」一档。
"""

from __future__ import annotations

import pytest

from packages.core.quality import issues as issues_mod
from packages.core.quality.aggregate import (
    compute_overall,
    compute_overall_with_gates,
    formula_hash,
    summarize_gates,
)
from packages.core.quality.issues import (
    CONFIRM_RULES,
    Issue,
    issue_gate,
    make_issue,
)
from packages.core.quality.models import Issue as IssueModel

# 全子分 95 的 fixture：方便区分「归零」(0) 与「保留部分分」(95)。
_GOOD_SUBSCORES = dict(
    plot=95, character=95, continuity=95, style=95,
    pacing=95, foreshadowing=95, ai_trace=95,
)


def _trigram_issue(severity: str = "warning") -> Issue:
    """trigram **confirm 档**样本（30.37% = 事故实测值）。

    2026-09-18 起该规则不在 ``CONFIRM_RULES`` 内：超 confirm 阈值的单条由产出侧
    （``scoring.score_style``）显式上修 ``gate="confirm"``，本 helper 复刻该形态。
    """
    return make_issue(
        severity=severity,  # type: ignore[arg-type]
        category="style",
        rule_id="RULE_STYLE_REPETITION_TRIGRAM",
        message="trigram 重复率 30.37% > 确认阈值 25%",
        evidence_refs=["破屋的×12"],
        gate="confirm",
    )


def _bare_trigram_issue(severity: str = "warning") -> Issue:
    """**未上修**的 trigram 条目（产量级 21% 那一档）：rule_id 本身不再是 confirm 依据。"""
    return make_issue(
        severity=severity,  # type: ignore[arg-type]
        category="style",
        rule_id="RULE_STYLE_REPETITION_TRIGRAM",
        message="trigram 重复率 20.15% > 警告阈值 16%",
        evidence_refs=["破屋的×7"],
    )


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------


def test_confirm_rules_snapshot_is_repetition_operators_only():
    """确认白名单快照 + F-19 守卫：只有确定性可复算的重复算子，无占比类算子。

    快照式断言（变更必须显式改测试）。**2026-09-18 变更**：
    ``RULE_STYLE_REPETITION_TRIGRAM`` 撤出本表——它的阈值 0.08 落在实测分布内部
    （人类锚点书 9/19 章、生成侧 90/92 章都超），命中即要求签字等于橡皮图章；
    现改为「超过 confirm 阈值的**那一条**」由产出侧单条上修（本文件另有专测）。
    后半段是形状守卫——AGENTS.md F-19：「占比/频率类指标进硬门禁会被凑指标」。
    """
    assert CONFIRM_RULES == frozenset({"AI-BEAT-REPEAT"})
    banned_fragments = ("RATIO", "DIALOGUE", "PARA", "LENGTH", "PCT", "FREQ")
    offenders = [
        rid for rid in CONFIRM_RULES if any(f in rid.upper() for f in banned_fragments)
    ]
    assert offenders == [], f"占比/长度类算子不得进 CONFIRM_RULES（F-19）：{offenders}"


def test_confirm_rule_id_resolves_to_confirm_regardless_of_severity():
    """``rule_id ∈ CONFIRM_RULES`` ⇒ confirm；warning 级同样不许静默通过。

    关键点：confirm 与 severity 正交——节拍复现在矩阵里是 warning（矩阵不变），
    但后果档由 ``CONFIRM_RULES`` 决定。
    """
    issue = make_issue(
        severity="warning",  # type: ignore[arg-type]
        category="style",  # type: ignore[arg-type]
        rule_id="AI-BEAT-REPEAT",
        message="同章远距小句复现 5 处",
    )
    assert issue_gate(issue) == "confirm"
    # ``make_issue`` 材料化字段，落库报告里直接可读
    assert issue.gate == "confirm"


def test_trigram_rule_id_alone_is_no_longer_confirm():
    """撤表后：**光有 rule_id** 的 trigram 条目不再是 confirm（产量级那档只该提示）。

    这是本次阈值重定的关键回归点：confirm 与否取决于**测出来的量级**（产出侧上修
    字段），不再取决于规则名。若有人把该 rule_id 加回 ``CONFIRM_RULES``，本用例变红。
    """
    issue = _bare_trigram_issue("warning")
    assert issue_gate(issue) == "auto"
    assert issue.gate == "auto"
    # 上修的那一条仍然是 confirm（同一 rule_id、两种量级）
    assert issue_gate(_trigram_issue("warning")) == "confirm"


def test_beat_repeat_rule_id_is_confirm_before_module_ships():
    """规则 id 先于规则实现入表：直接用裸 Issue 构造也解析为 confirm。"""
    raw = IssueModel(
        severity="warning",  # type: ignore[arg-type]
        category="style",  # type: ignore[arg-type]
        rule_id="AI-BEAT-REPEAT",
        message="同一节拍在章内逐字复现 3 次",
    )
    assert issue_gate(raw) == "confirm"


def test_block_rule_still_resolves_to_block():
    """既有阻断白名单不受影响：error + 白名单 ⇒ block（且优先于 confirm）。"""
    issue = make_issue(
        severity="error",  # type: ignore[arg-type]
        category="character_contradiction",  # type: ignore[arg-type]
        rule_id="RULE_CHAR_DEAD_ACTIVE",
        message="角色已死亡仍被设置活动字段",
    )
    assert issue_gate(issue) == "block"


def test_unknown_rule_is_auto_and_informational_error_stays_auto():
    """默认档：白名单外的 error（informational）与普通 warning 都是 auto（不发明新后果）。"""
    informational = make_issue(
        severity="error",  # type: ignore[arg-type]
        category="payoff",  # type: ignore[arg-type]
        rule_id="RULE_H3_FILLER_3CH",
        message="连续 3+ 章 payoff 为 0",
    )
    warning = make_issue(
        severity="warning",  # type: ignore[arg-type]
        category="pacing",  # type: ignore[arg-type]
        rule_id="RULE_PACING_FLAT",
        message="段落长度极差 < 5%",
    )
    assert issue_gate(informational) == "auto"
    assert issue_gate(warning) == "auto"
    assert issue_gate(None) == "auto"


def test_explicit_field_can_escalate_but_not_downgrade():
    """字段只能上修：表内规则不可被字段降回 auto；block 也不接受字段指定。"""
    escalated = make_issue(
        severity="warning",  # type: ignore[arg-type]
        category="style",  # type: ignore[arg-type]
        rule_id="RULE_SOME_NEW_OPERATOR",
        message="x",
        gate="confirm",
    )
    assert issue_gate(escalated) == "confirm"

    downgraded = IssueModel(
        severity="warning",  # type: ignore[arg-type]
        category="style",  # type: ignore[arg-type]
        rule_id="AI-BEAT-REPEAT",
        message="x",
        gate="auto",
    )
    assert issue_gate(downgraded) == "confirm", "表内规则不接受字段降级"

    forged_block = IssueModel(
        severity="warning",  # type: ignore[arg-type]
        category="style",  # type: ignore[arg-type]
        rule_id="RULE_SOME_NEW_OPERATOR",
        message="x",
        gate="block",
    )
    assert issue_gate(forged_block) == "auto", "block 只由 severity+BLOCKING_RULES 推出"


def test_trigram_issue_carries_repeated_fragment_evidence_at_both_tiers():
    """要求显式接受的档位必须带可核对证据：``score_style`` 附最高频 trigram。

    没有这一条，阻断消息就只剩一个比率数字——调用方在读不到「重复了什么」的情况下
    被迫批准。实测样本：同一句重复 12 次（confirm 档，率≈1.00）与
    ``neutral_prose(2400)``（warn 档，率 0.1631）**两档都带**证据摘录。
    """
    from packages.core.quality.scoring import score_style
    from tests.unit.neutral_prose import neutral_prose

    draft = "破屋的灯还亮着，他把窗纸又糊了一遍。" * 12
    _score, issues = score_style(draft)
    trigram = [i for i in issues if i.rule_id == "RULE_STYLE_REPETITION_TRIGRAM"]
    assert len(trigram) == 1, issues
    refs = trigram[0].evidence_refs or []
    assert refs, "要求显式接受的档位必须带证据摘录（否则放行声明无从核对）"
    assert all("×" in ref for ref in refs), refs
    assert trigram[0].gate == "confirm"

    # warn 档（未上修）同样带证据——档位随改稿变化，证据不该时有时无
    _score2, issues2 = score_style(neutral_prose(2400))
    warn_tier = [i for i in issues2 if i.rule_id == "RULE_STYLE_REPETITION_TRIGRAM"]
    assert len(warn_tier) == 1, issues2
    assert warn_tier[0].gate == "auto"
    assert warn_tier[0].evidence_refs, "warn 档也要能看见重复了什么"


def test_make_issue_materializes_gate_field():
    for issue in (
        _trigram_issue("warning"),
        make_issue(
            severity="error",  # type: ignore[arg-type]
            category="compliance",  # type: ignore[arg-type]
            rule_id="RULE_Q6_OVERLAP_RATE",
            message="y",
        ),
        make_issue(
            severity="info",  # type: ignore[arg-type]
            category="pacing",  # type: ignore[arg-type]
            rule_id="RULE_PACING_NOTE",
            message="z",
        ),
    ):
        assert issue.gate == issue_gate(issue)
    # pydantic 缺省值仍是 auto（直接构造的 Issue 由解析器兜底）
    assert IssueModel(severity="warning", category="style", rule_id="X", message="m").gate == "auto"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 聚合 / 报告可见性
# ---------------------------------------------------------------------------


def test_confirm_issue_does_not_zero_overall_but_is_visible():
    """confirm 档不归零 overall，但必须出现在聚合摘要里（能看见才叫有后果）。"""
    issues = [_trigram_issue("warning")]
    overall, out, gate_summary = compute_overall_with_gates(dict(_GOOD_SUBSCORES), issues)

    assert overall == 95, "confirm 不是结构性损坏，不压死 overall"
    assert gate_summary.confirm_rule_ids == ("RULE_STYLE_REPETITION_TRIGRAM",)
    assert gate_summary.confirm_count == 1
    assert gate_summary.blocking_rule_ids == ()
    assert gate_summary.as_meta() == {
        "blocking_rule_ids": [],
        "confirm_rule_ids": ["RULE_STYLE_REPETITION_TRIGRAM"],
        "blocking_count": 0,
        "confirm_count": 1,
    }
    assert any(i.gate == "confirm" for i in out)


def test_block_still_zeroes_and_wins_over_confirm():
    """同一份列表里 block 与 confirm 混在时：block 归零，两类都在摘要里。"""
    issues = [
        _trigram_issue("warning"),
        make_issue(
            severity="error",  # type: ignore[arg-type]
            category="compliance",  # type: ignore[arg-type]
            rule_id="RULE_Q6_OVERLAP_RATE",
            message="y",
        ),
    ]
    overall, _, gate_summary = compute_overall_with_gates(dict(_GOOD_SUBSCORES), issues)
    assert overall == 0
    assert gate_summary.blocking_rule_ids == ("RULE_Q6_OVERLAP_RATE",)
    assert gate_summary.confirm_rule_ids == ("RULE_STYLE_REPETITION_TRIGRAM",)


def test_compute_overall_signature_unchanged_for_confirm():
    """既有两元组入口零影响：confirm issue 仍返回 (overall, issues)。"""
    overall, out = compute_overall(dict(_GOOD_SUBSCORES), [_trigram_issue("warning")])
    assert overall == 95
    assert len(out) == 1


def test_summarize_gates_dedups_and_sorts_rule_ids():
    """同一规则命中多条 ⇒ rule_id 去重；顺序稳定（门禁的覆盖判定按逐字比较）。"""
    issues = [
        _trigram_issue("warning"),
        _trigram_issue("warning"),
        make_issue(
            severity="warning",  # type: ignore[arg-type]
            category="style",  # type: ignore[arg-type]
            rule_id="AI-BEAT-REPEAT",
            message="b",
        ),
    ]
    gate_summary = summarize_gates(issues)
    assert gate_summary.confirm_rule_ids == ("AI-BEAT-REPEAT", "RULE_STYLE_REPETITION_TRIGRAM")
    assert len(gate_summary.confirm_issues) == 3


def test_engine_report_meta_exposes_gate_summary():
    """引擎报告 ``_meta.gate_summary`` 可见 + 每条 issue 的 ``gate`` 已材料化。"""
    from packages.core.quality import QualityContext, QualityEngine

    draft = "破屋的灯亮着。破屋的灯亮着。" * 4
    report = QualityEngine().evaluate(
        QualityContext(chapter_id="ch_x", chapter_number=1, draft=draft)
    )
    assert "gate_summary" in report.meta
    assert set(report.meta["gate_summary"]) == {
        "blocking_rule_ids",
        "confirm_rule_ids",
        "blocking_count",
        "confirm_count",
    }
    for issue in report.issues:
        assert issue.gate == issue_gate(issue)


# ---------------------------------------------------------------------------
# 口径指纹
# ---------------------------------------------------------------------------


def test_severity_fingerprint_covers_confirm_rules():
    """指纹覆盖确认白名单；撤表后 trigram 不再出现（口径变更可追溯）。"""
    from packages.core.quality.issues import severity_config_fingerprint

    text = severity_config_fingerprint()
    assert "confirm:AI-BEAT-REPEAT" in text
    assert "confirm:AI-BEAT-REPEAT,RULE_STYLE_REPETITION_TRIGRAM" not in text
    assert "RULE_STYLE_REPETITION_TRIGRAM" not in text


def test_formula_hash_changes_when_confirm_rules_change(monkeypatch: pytest.MonkeyPatch):
    """突变验证：改确认白名单 ⇒ formula_hash 必须变（口径变更可追溯）。"""
    before = formula_hash()
    monkeypatch.setattr(
        issues_mod,
        "CONFIRM_RULES",
        issues_mod.CONFIRM_RULES | {"RULE_NEW_CONFIRM_RULE"},
    )
    assert formula_hash() != before
