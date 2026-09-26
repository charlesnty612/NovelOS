"""交付判定纯推导层单测（packages/core/delivery/verdict.py）。

覆盖每种「判定形状」，每条断言都钉住**具体理由的 rule_id + status**——判定是推导出来的，
所以撤掉任一条分支（降级集合 / 提前返回 / 接受留痕判定）都必须让某一条测试变红
（突变验证记录见交付汇报）。

测试不碰 DB、不碰 LLM：证据全部由本文件的小工厂函数按真实口径构造
（字数带的 tier 直接调 ``classify_length_tier``，不手写期望档位）。
"""

from __future__ import annotations

from packages.core.delivery.verdict import (
    DELIVERABLE,
    NEEDS_WORK,
    NOT_A_CANDIDATE,
    NOT_DELIVERABLE,
    RULE_CHAPTER_NOT_WRITTEN,
    RULE_LENGTH_BEYOND_BAND_EDGE,
    RULE_LENGTH_OUT_OF_BAND,
    RULE_NOT_COMMITTED,
    RULE_QUALITY_REPORT_MISSING,
    RULE_QUALITY_REPORT_STALE,
    RULE_SIGNING_OUT_OF_SCOPE,
    RULE_SIGNING_UNMEASURED,
    ChapterEvidence,
    LengthEvidence,
    QualityEvidence,
    SigningEvidence,
    classify_length_tier,
    derive_chapter_verdict,
    issue_gate_rows,
    project_verdict_of,
)
from packages.core.quality.wordcount import classify_prose_length

_TARGET = 2500
#: plan_json.expected_word_count=2500 口径下的默认带（±15%）：2125 ~ 2875
#: （带边缘到目标距离 = 375，即 error 档阈值 = 偏离 ±30%）。


# --------------------------------------------------------------------------- 工厂


def _length_evidence(visible: int, target: int = _TARGET) -> LengthEvidence:
    """按真实口径造字数证据（tier 由 ``classify_length_tier`` 推导，不手写）。"""
    classified = classify_prose_length("字" * visible, target)
    return LengthEvidence(
        visible_chars=int(classified["visible_chars"]),
        target_word_count=int(classified["target"]),
        band_low=int(classified["band_low"]),
        band_high=int(classified["band_high"]),
        deviation_pct=float(classified["deviation_pct"]),
        status=str(classified["status"]),
        tier=classify_length_tier(classified, target),
    )


def _quality_evidence(
    issues: list[dict] | None = None,
    *,
    draft_version: int | None = None,
    accepted_override: dict | None = None,
) -> QualityEvidence:
    return QualityEvidence(
        report_id="qr_test",
        overall=90,
        evaluated_at="2026-09-18T00:00:00+00:00",
        draft_version=draft_version,
        gate_summary=None,
        accepted_override=accepted_override,
        issues=tuple(issues or []),
    )


def _trigram_issue() -> dict:
    """confirm 档的 trigram 命中（30.37% = 事故实测值）。

    2026-09-18 起该规则不在 ``CONFIRM_RULES`` 内——只有超过 confirm 阈值的那一条
    被产出侧显式上修 ``gate="confirm"``，落库报告按该字段回显（历史报告无此字段，
    重推后为 auto，见 ``test_issue_gate_rows_rederive_gate_for_reports_without_gate_field``）。
    """
    return {
        "severity": "warning",
        "category": "style",
        "rule_id": "RULE_STYLE_REPETITION_TRIGRAM",
        "message": "trigram 重复率 30.37% > 确认阈值 25%",
        "evidence_refs": ["他抬起眼×12", "破屋的×9"],
        "gate": "confirm",
    }


def _beat_repeat_issue() -> dict:
    """静态确认表内的命中（``AI-BEAT-REPEAT``）：**不依赖** ``gate`` 字段回显。"""
    return {
        "severity": "warning",
        "category": "ai_trace",
        "rule_id": "AI-BEAT-REPEAT",
        "message": "同章远距小句复现 5 处",
        "evidence_refs": ["他抬起眼｜他抬起眼看着"],
    }


def _block_issue() -> dict:
    """block 档：severity=error 且 rule_id 在 BLOCKING_RULES 内。"""
    return {
        "severity": "error",
        "category": "character_contradiction",
        "rule_id": "RULE_CHAR_DEAD_ACTIVE",
        "message": "已死亡角色被写活动字段",
    }


def _warning_issue() -> dict:
    """gate=auto 的 warning：展示但不降级。"""
    return {
        "severity": "warning",
        "category": "payoff",
        "rule_id": "RULE_H1_NO_END_HOOK",
        "message": "末 200 字未命中任何钩子标记",
    }


def _signing(*items: tuple[str, str], scope: str = "in_scope", note: str = "n") -> SigningEvidence:
    return SigningEvidence(
        scope=scope,
        note=note,
        items=tuple({"key": k, "level": level, "detail": f"{k} detail", "advice": f"{k} advice"} for k, level in items),
    )


def _chapter(
    *,
    status: str = "COMMITTED",
    number: int = 1,
    length: LengthEvidence | None | str = "clean",
    quality: QualityEvidence | None | str = "clean",
    signing: SigningEvidence | None = None,
    draft_version: int | None = 3,
) -> ChapterEvidence:
    """默认构造「证据齐备的干净章」；显式传 ``None`` 表示该项证据不可得。"""
    if length == "clean":
        length = _length_evidence(2250)
    if quality == "clean":
        quality = _quality_evidence(draft_version=3)
    if signing is None:
        signing = _signing(("chapter_hooks_ch1", "warn"))
    return ChapterEvidence(
        chapter_id="ch_test",
        number=number,
        title="测试章",
        status=status,
        draft_version=draft_version,
        quality=quality,  # type: ignore[arg-type]
        length=length,  # type: ignore[arg-type]
        signing=signing,
    )


def _statuses(result) -> dict[str, str]:
    return {r.rule_id: r.status for r in result.reasons}


# --------------------------------------------------------------------------- 干净章


def test_clean_in_band_chapter_is_deliverable():
    """证据齐备 + 字数在带内 + 无 confirm/block ⇒ deliverable（warn 不降级）。"""
    ev = _chapter(quality=_quality_evidence([_warning_issue()], draft_version=3))
    result = derive_chapter_verdict(ev)

    assert result.verdict == DELIVERABLE
    assert result.blocking_reason is None
    # warning 级 issue 仍在理由里可见（但不降级）
    assert _statuses(result)["RULE_H1_NO_END_HOOK"] == "warn"
    assert result.degrading_reasons == ()


def test_clean_chapter_with_open_hook_warning_still_deliverable():
    """章末钩子缺失（signing warn + quality warning）不降级——gate=auto 允许静默通过。"""
    ev = _chapter(
        quality=_quality_evidence([_warning_issue()], draft_version=3),
        signing=_signing(("chapter_hooks_ch1", "warn"), ("chapter_length_ch1", "warn")),
    )
    assert derive_chapter_verdict(ev).verdict == DELIVERABLE


# --------------------------------------------------------------------------- 字数带


def test_length_under_band_is_needs_work():
    """出带（未超带边缘距离）⇒ needs_work + DELIVERY_LENGTH_OUT_OF_BAND。"""
    ev = _chapter(length=_length_evidence(1791))
    result = derive_chapter_verdict(ev)

    assert result.verdict == NEEDS_WORK
    assert _statuses(result)[RULE_LENGTH_OUT_OF_BAND] == "length_out_of_band"
    assert result.blocking_reason is not None
    assert result.blocking_reason.rule_id == RULE_LENGTH_OUT_OF_BAND
    assert "1791" in result.blocking_reason.evidence


def test_length_beyond_band_edge_is_not_deliverable():
    """偏离超过「带边缘到目标的距离」⇒ 硬停（与 review 侧 error 级同式）。"""
    ev = _chapter(length=_length_evidence(900))
    result = derive_chapter_verdict(ev)

    assert result.verdict == NOT_DELIVERABLE
    assert _statuses(result)[RULE_LENGTH_BEYOND_BAND_EDGE] == "length_beyond_band_edge"
    assert result.blocking_reason is not None
    assert result.blocking_reason.rule_id == RULE_LENGTH_BEYOND_BAND_EDGE


def test_length_over_band_high_uses_same_two_tier_rule():
    """带上侧同样两档：2708（+8.3%）在带内，3200（+28%）出带但不硬停。"""
    assert derive_chapter_verdict(_chapter(length=_length_evidence(2708))).verdict == DELIVERABLE
    over = derive_chapter_verdict(_chapter(length=_length_evidence(3200)))
    assert over.verdict == NEEDS_WORK
    assert _statuses(over)[RULE_LENGTH_OUT_OF_BAND] == "length_out_of_band"


def test_classify_length_tier_matches_review_two_tier_arithmetic():
    """tier 分界：带边缘到目标距离 = 375（=2500-2125）⇒ 1750 仍 warn，1749 直接算 error 侧。"""
    assert classify_length_tier(classify_prose_length("字" * 2125, _TARGET), _TARGET) == "in_band"
    assert classify_length_tier(classify_prose_length("字" * 1751, _TARGET), _TARGET) == "warn"
    # 1750 → edge_deviation = 375 == edge_distance 375 → 不超（>）⇒ 仍 warn
    assert classify_length_tier(classify_prose_length("字" * 1750, _TARGET), _TARGET) == "warn"
    assert classify_length_tier(classify_prose_length("字" * 1749, _TARGET), _TARGET) == "error"


# --------------------------------------------------------------------------- gate 分档


def test_confirm_tier_issue_without_acceptance_is_needs_work():
    """confirm 档（trigram 重复）未获显式接受 ⇒ needs_work，理由带证据片段。"""
    ev = _chapter(quality=_quality_evidence([_trigram_issue()], draft_version=3))
    result = derive_chapter_verdict(ev)

    assert result.verdict == NEEDS_WORK
    reason = next(r for r in result.reasons if r.rule_id == "RULE_STYLE_REPETITION_TRIGRAM")
    assert reason.status == "confirm"
    assert "须显式接受才放行" in reason.message
    assert "他抬起眼×12" in (reason.evidence or "")
    assert reason.degrading is True


def test_confirm_tier_issue_with_acceptance_is_deliverable():
    """同一命中 + 报告内接受留痕覆盖该 rule_id ⇒ 不降级（仅展示 accepted）。"""
    accepted = {
        "rule_ids": ["RULE_STYLE_REPETITION_TRIGRAM"],
        "reason": "作者认了这个重复，属于刻意的排比",
        "accepted_at": "2026-09-18T08:00:00+00:00",
    }
    ev = _chapter(
        quality=_quality_evidence(
            [_trigram_issue()], draft_version=3, accepted_override=accepted
        )
    )
    result = derive_chapter_verdict(ev)

    assert result.verdict == DELIVERABLE
    reason = next(r for r in result.reasons if r.rule_id == "RULE_STYLE_REPETITION_TRIGRAM")
    assert reason.status == "accepted"
    assert "刻意的排比" in reason.message
    assert result.degrading_reasons == ()


def test_block_tier_issue_is_not_deliverable():
    """block 档（BLOCKING_RULES 命中）⇒ 硬停，优先级高于一切待办。"""
    ev = _chapter(
        quality=_quality_evidence([_block_issue(), _trigram_issue()], draft_version=3),
        length=_length_evidence(1791),
    )
    result = derive_chapter_verdict(ev)

    assert result.verdict == NOT_DELIVERABLE
    assert _statuses(result)["RULE_CHAR_DEAD_ACTIVE"] == "block"
    assert result.blocking_reason is not None
    assert result.blocking_reason.rule_id == "RULE_CHAR_DEAD_ACTIVE"
    assert "硬停" in result.blocking_reason.message


def test_issue_gate_rows_rederive_gate_for_reports_without_gate_field():
    """落库报告无 ``gate`` 字段时，分档由 ``issue_gate`` 权威重推（历史报告口径）。

    2026-09-18 附加断言：**历史** trigram 命中（阈值 0.08 时代落库、无 ``gate`` 字段）
    重推后是 ``auto`` —— 那批命中按新口径大多是正常波动，不该继续要求签字。
    """
    legacy_trigram = {k: v for k, v in _trigram_issue().items() if k != "gate"}
    rows = issue_gate_rows(
        [legacy_trigram, _beat_repeat_issue(), _block_issue(), _warning_issue()]
    )
    by_rule = {r["rule_id"]: r["gate"] for r in rows}
    assert by_rule["AI-BEAT-REPEAT"] == "confirm"
    assert by_rule["RULE_STYLE_REPETITION_TRIGRAM"] == "auto"
    assert by_rule["RULE_CHAR_DEAD_ACTIVE"] == "block"
    assert by_rule["RULE_H1_NO_END_HOOK"] == "auto"


def test_escalated_trigram_row_keeps_confirm_gate():
    """带上修字段的那一条（事故量级）重推仍是 confirm —— 量级依赖的后果不走规则表。"""
    rows = issue_gate_rows([_trigram_issue()])
    assert rows[0]["gate"] == "confirm"


# --------------------------------------------------------------------------- 证据缺失


def test_missing_quality_report_is_not_evaluated_not_a_pass():
    """无质量报告 ⇒ 显式 not_evaluated 理由 + 不能是 deliverable。"""
    ev = _chapter(quality=None)
    result = derive_chapter_verdict(ev)

    assert result.verdict == NEEDS_WORK
    assert result.verdict != DELIVERABLE
    assert _statuses(result)[RULE_QUALITY_REPORT_MISSING] == "not_evaluated"
    reason = next(r for r in result.reasons if r.rule_id == RULE_QUALITY_REPORT_MISSING)
    assert "未评估" in reason.message


def test_missing_length_is_not_evaluated_not_a_pass():
    """量不出字数 ⇒ not_evaluated（不得当作「字数合格」）。"""
    ev = _chapter(length=None)
    result = derive_chapter_verdict(ev)

    assert result.verdict == NEEDS_WORK
    statuses = _statuses(result)
    assert statuses["DELIVERY_LENGTH_UNMEASURED"] == "not_evaluated"


def test_missing_all_evidence_is_needs_work_with_three_not_evaluated_reasons():
    """全空证据（有正文的章）⇒ 三条 not_evaluated，绝不 deliverable。"""
    ev = _chapter(quality=None, length=None, signing=_signing(scope="unavailable"))
    result = derive_chapter_verdict(ev)

    assert result.verdict == NEEDS_WORK
    statuses = _statuses(result)
    assert statuses[RULE_QUALITY_REPORT_MISSING] == "not_evaluated"
    assert statuses["DELIVERY_LENGTH_UNMEASURED"] == "not_evaluated"
    assert statuses[RULE_SIGNING_UNMEASURED] == "not_evaluated"


def test_stale_quality_report_is_not_evaluated():
    """报告评的是 v6、当前正文 v13 ⇒ 报告不能代表交付文本（not_evaluated）。"""
    ev = _chapter(quality=_quality_evidence(draft_version=6), draft_version=13)
    result = derive_chapter_verdict(ev)

    assert result.verdict == NEEDS_WORK
    assert _statuses(result)[RULE_QUALITY_REPORT_STALE] == "not_evaluated"


def test_same_draft_version_is_not_stale():
    """报告版本与当前正文一致 ⇒ 无 stale 理由。"""
    ev = _chapter(quality=_quality_evidence(draft_version=13), draft_version=13)
    assert RULE_QUALITY_REPORT_STALE not in _statuses(derive_chapter_verdict(ev))


def test_needs_work_before_commit_gate():
    """DRAFTED / REVIEWED（有正文但未过提交门）⇒ needs_work + DELIVERY_NOT_COMMITTED。"""
    for status in ("DRAFTED", "REVIEWED"):
        result = derive_chapter_verdict(_chapter(status=status))
        assert result.verdict == NEEDS_WORK
        assert _statuses(result)[RULE_NOT_COMMITTED] == "not_committed"


def test_released_chapter_is_deliverable():
    """RELEASED 与 COMMITTED 同档（已过提交门）。"""
    assert derive_chapter_verdict(_chapter(status="RELEASED")).verdict == DELIVERABLE


# --------------------------------------------------------------------------- 签约体检


def test_signing_fail_degrades_to_needs_work():
    """黄金三章硬规则 fail（开篇 300 字无冲突）⇒ 待办——此前这条命中无人消费。"""
    ev = _chapter(signing=_signing(("ch1_conflict_300", "fail")))
    result = derive_chapter_verdict(ev)

    assert result.verdict == NEEDS_WORK
    reason = next(r for r in result.reasons if r.rule_id == "ch1_conflict_300")
    assert reason.status == "signing_fail"
    assert reason.source == "signing_check"
    assert reason.degrading is True


def test_signing_warn_does_not_degrade():
    """体检 warn 档（字数落点 / 章末钩子建议）只展示，不降级。"""
    ev = _chapter(signing=_signing(("chapter_length_ch1", "warn")))
    result = derive_chapter_verdict(ev)

    assert result.verdict == DELIVERABLE
    assert _statuses(result)["chapter_length_ch1"] == "warn"


def test_signing_out_of_scope_is_explicit_and_non_degrading():
    """第 4 章及以后：签约体检标记 out_of_scope（显式说明，不是静默跳过）。"""
    ev = _chapter(number=40, signing=_signing(scope="out_of_scope"))
    result = derive_chapter_verdict(ev)

    assert result.verdict == DELIVERABLE
    reason = next(r for r in result.reasons if r.rule_id == RULE_SIGNING_OUT_OF_SCOPE)
    assert reason.status == "out_of_scope"
    assert reason.degrading is False


def test_signing_unavailable_degrades_to_needs_work():
    """体检器不可得 ⇒ 1~3 章按未评估处理（不能因为看不到就当合格）。"""
    ev = _chapter(signing=_signing(scope="unavailable"))
    result = derive_chapter_verdict(ev)

    assert result.verdict == NEEDS_WORK
    assert _statuses(result)[RULE_SIGNING_UNMEASURED] == "not_evaluated"


# --------------------------------------------------------------------------- PLANNED


def test_planned_chapter_is_not_a_candidate_not_not_deliverable():
    """PLANNED ⇒ not_a_candidate（明确与 not_deliverable 区分）。"""
    ev = _chapter(status="PLANNED", quality=None, length=None, draft_version=None)
    result = derive_chapter_verdict(ev)

    assert result.verdict == NOT_A_CANDIDATE
    assert result.verdict != NOT_DELIVERABLE
    assert _statuses(result)[RULE_CHAPTER_NOT_WRITTEN] == "not_a_candidate"
    assert len(result.reasons) == 1
    assert "还不是交付对象" in result.reasons[0].message


# --------------------------------------------------------------------------- 项目汇总


def test_project_verdict_is_worst_of_chapters():
    assert project_verdict_of([DELIVERABLE, DELIVERABLE]) == DELIVERABLE
    assert project_verdict_of([DELIVERABLE, NEEDS_WORK]) == NEEDS_WORK
    assert project_verdict_of([NEEDS_WORK, NOT_DELIVERABLE]) == NOT_DELIVERABLE
    assert project_verdict_of([NOT_DELIVERABLE, NOT_A_CANDIDATE]) == NOT_DELIVERABLE


def test_project_verdict_counts_unwritten_chapters_as_needs_work():
    """3 章干净 + 27 章未写 ≠ 可交付：书没写完。"""
    assert project_verdict_of([DELIVERABLE] * 3 + [NOT_A_CANDIDATE] * 27) == NEEDS_WORK


def test_project_verdict_all_unwritten_or_empty_is_not_a_candidate():
    assert project_verdict_of([NOT_A_CANDIDATE, NOT_A_CANDIDATE]) == NOT_A_CANDIDATE
    assert project_verdict_of([]) == NOT_A_CANDIDATE
