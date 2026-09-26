"""P0-1 接线：「AI 模式级规则」必须真的进质量路径（2026-09-18）。

改造前实测（本文件第 2 条用例即该缺陷的回归）：``ai_patterns.scan_ai_patterns``
的命中只被 ``chapter_review._basic_checks_node``（评审报告）与 chapter-write 预检
消费，``QualityEngine.evaluate`` 从不调用它 ⇒ ``issues.CONFIRM_RULES`` 里的
``AI-BEAT-REPEAT`` 在**提交链路**上是一条惰性声明：commit 走的
quality_gate → QualityEngine → ``quality_reports`` 里永远不会有这条 rule_id，
而 confirm 档的判定完全依赖报告里的 issue（``summarize_gates``）——「必须显式
接受才放行」对它等于不存在（P0-1 要防的正是这类静默通过）。

接线点：``packages.core.quality.engine.ai_pattern_issues``（引擎是质量路径的唯一
汇聚点：报告落库 / 门禁 / API evaluate 都从这里出）。
"""

from __future__ import annotations

import pytest

from packages.core.quality import QualityContext, QualityEngine
from packages.core.quality import engine as engine_mod
from packages.core.quality.aggregate import summarize_gates
from packages.core.quality.engine import ai_pattern_issues
from packages.core.quality.issues import BLOCKING_RULES, CONFIRM_RULES
from tests.unit.quality.test_engine import _full_delta, _pass_ctx  # noqa: F401

# 触发 ``AI-BEAT-REPEAT`` 的最小正文：同一段素材隔着 ~200 字写了两次
# （口径见 packages/core/quality/beat_repeat.py；样例取自该模块 docstring 的真实
# 命中，2026-09-18 复算仍然命中）。
_REPEAT_CLAUSE = (
    "沈砚低头看着桌上那张收据的复印件，付款方一栏的江氏集团有限公司几个字"
    "在台灯下显得格外清楚。"
)
_OTHER = "窗外传来一阵脚步声，走廊尽头的灯忽明忽暗，值班的人把外套裹紧了些。"
_REPEAT_DRAFT = (
    _REPEAT_CLAUSE
    + "\n\n"
    + (_REPEAT_CLAUSE + _OTHER) * 3
    + "\n\n"
    + "桌上那份收据的复印件还摆着，付款方一栏的江氏集团有限公司几个字在晨光里格外清晰。"
)


def _ctx(draft: str) -> QualityContext:
    return _pass_ctx({"draft": draft})


# ---------------------------------------------------------------------------
# 1) 命中真的进报告
# ---------------------------------------------------------------------------


def test_beat_repeat_reaches_the_quality_report():
    """``AI-BEAT-REPEAT`` 命中必须出现在 ``QualityReport.issues`` 里（改造前为空）。"""
    report = QualityEngine().evaluate(_ctx(_REPEAT_DRAFT))
    rule_ids = [i.rule_id for i in report.issues]
    assert "AI-BEAT-REPEAT" in rule_ids, rule_ids


def test_confirm_rule_is_no_longer_inert_on_the_commit_path():
    """confirm 档的判定输入（``summarize_gates``）必须看得见这条命中。

    ``gate.py`` 的放行条件就是 ``GateSummary.confirm_rule_ids``——报告里没有这条
    issue，门禁就无从要求「显式接受」。故此处断言到 GateSummary 这一层。
    """
    report = QualityEngine().evaluate(_ctx(_REPEAT_DRAFT))
    summary = summarize_gates(report.issues)
    assert "AI-BEAT-REPEAT" in summary.confirm_rule_ids
    assert "AI-BEAT-REPEAT" in CONFIRM_RULES  # 规则表侧的声明

    # 逐条 issue 的 gate 字段与解析器同口径（落库报告直接给前端读）
    hit = next(i for i in report.issues if i.rule_id == "AI-BEAT-REPEAT")
    assert hit.gate == "confirm"
    # confirm 档必须带可核对证据（作者要看见自己在接受什么）
    assert hit.evidence_refs, hit


# ---------------------------------------------------------------------------
# 2) 通用接线：不认具体 rule_id（并行交付的规则也能自动进闸门）
# ---------------------------------------------------------------------------


def test_wiring_is_generic_over_rule_ids(monkeypatch: pytest.MonkeyPatch):
    """``scan_ai_patterns`` 返回什么 rule_id 就接什么——不硬编码新规则名。

    模拟「规则侧刚上线、质量路径还没人改过」的场景：注入一条**全新** rule_id，
    断言语义不变地进了 issue 列表（类别走默认回退、后果档由 issue_gate 判）。
    """
    fake = [
        {
            "rule_id": "AI-BRAND-NEW-RULE",
            "severity": "warning",
            "message": "新算子命中",
            "count": 2,
            "samples": ["样例一", "样例二"],
        }
    ]
    monkeypatch.setattr(engine_mod, "scan_ai_patterns", lambda *a, **k: fake)
    report = QualityEngine().evaluate(_ctx("正文"))
    hits = [i for i in report.issues if i.rule_id == "AI-BRAND-NEW-RULE"]
    assert len(hits) == 1
    assert hits[0].category == "style"  # 未登记规则回退默认类别
    assert hits[0].evidence_refs == ["样例一", "样例二"]
    assert hits[0].gate == "auto"  # 不在 CONFIRM_RULES / BLOCKING_RULES 里 ⇒ 无后果


def test_unknown_categories_fall_back_instead_of_raising(monkeypatch: pytest.MonkeyPatch):
    """命中缺字段 / 字段非法时不抛错（规则侧演进不得炸掉质量路径）。"""
    monkeypatch.setattr(
        engine_mod,
        "scan_ai_patterns",
        lambda *a, **k: [
            {},  # 无 rule_id → 跳过
            {"rule_id": "AI-WEIRD", "severity": "catastrophic"},  # 非法 severity → warning
            "not-a-dict",  # 非法元素 → 跳过
        ],
    )
    issues = ai_pattern_issues("正文")
    assert [i.rule_id for i in issues] == ["AI-WEIRD"]
    assert issues[0].severity == "warning"


# ---------------------------------------------------------------------------
# 3) 不重复计分：条目 ≠ 子分
# ---------------------------------------------------------------------------


def test_pattern_hits_do_not_move_subscores_or_overall(monkeypatch: pytest.MonkeyPatch):
    """AI 条目**只进 issues**，不参与任何子分 / overall / formula_hash。

    与既有机制的分工（README §5.5）：style / ai_trace 的词表信号走**密度阶梯扣分**
    （子分），本接线新增的是**条目**；两轴并存 ⇒ 其余字段必须逐字节相同。
    """
    with_hits = QualityEngine().evaluate(_ctx(_REPEAT_DRAFT))

    monkeypatch.setattr(engine_mod, "scan_ai_patterns", lambda *a, **k: [])
    without_hits = QualityEngine().evaluate(_ctx(_REPEAT_DRAFT))

    for name in (
        "overall", "plot", "character", "continuity", "style", "pacing",
        "foreshadowing", "ai_trace",
    ):
        assert getattr(with_hits, name) == getattr(without_hits, name), name
    assert (
        with_hits.meta["scoring_formula_hash"]
        == without_hits.meta["scoring_formula_hash"]
    )
    assert {i.rule_id for i in with_hits.issues} == (
        {i.rule_id for i in without_hits.issues} | {"AI-BEAT-REPEAT"}
    )


# ---------------------------------------------------------------------------
# 4) severity：按 category 矩阵封顶，但白名单规则不被削
# ---------------------------------------------------------------------------


def test_severity_is_capped_by_the_category_matrix(monkeypatch: pytest.MonkeyPatch):
    """检测器的 error 档按 category 矩阵封顶（style/pacing/ai_trace ≤ warning）。

    依据 README §4.2 / §8.5：质量路径里 category 的 severity 上限由
    ``MVP_SEVERITY_MATRIX`` 决定；检测器在**评审通道**（``review_report.errors``）
    里的 error 档不受本处影响。
    """
    monkeypatch.setattr(
        engine_mod,
        "scan_ai_patterns",
        lambda *a, **k: [
            {"rule_id": "AI-X", "severity": "error", "message": "m"},
        ],
    )
    assert ai_pattern_issues("正文")[0].severity == "warning"


def test_blocking_whitelist_rule_keeps_its_severity(monkeypatch: pytest.MonkeyPatch):
    """白名单规则的 severity 是阻断判据，**不得**被矩阵削掉（否则成死规则）。"""
    blocking_rule = sorted(BLOCKING_RULES)[0]
    monkeypatch.setattr(
        engine_mod,
        "scan_ai_patterns",
        lambda *a, **k: [
            {"rule_id": blocking_rule, "severity": "error", "message": "m"},
        ],
    )
    issue = ai_pattern_issues("正文")[0]
    assert issue.severity == "error"
    assert issue.gate == "block"


def test_full_delta_fixture_is_the_shared_one():
    """本文件复用的 delta fixture 与 ``test_engine`` 同源（避免两处漂移）。"""
    assert _full_delta()["schema_version"] == "state-delta-v0"


# ---------------------------------------------------------------------------
# 5) 连续性规则的 category 必须与 taxonomy 命名空间一致（2026-09-21 检修 M2）
# ---------------------------------------------------------------------------


def test_deterministic_continuity_rules_are_categorized_as_continuity():
    """``CONT-*`` 命中必须归 ``continuity`` 类别，而不是默认的 ``style``。

    缺陷形状（M1 亲验复现）：``_AI_PATTERN_CATEGORY`` 未登记 ``CONT-*``，它们落进
    默认回退 ``style``——与 ``continuity_taxonomy.DETERMINISTIC_CONTINUITY_RULE_IDS``
    的命名空间声明矛盾（「名实不符」），报告 / 前端按 category 分桶时连续性命中会跑进
    「文风」桶。撤掉 engine 里的派生映射，本用例必红。
    """
    from tests.unit.quality.test_continuity_time import _CLOCK_DAYBREAK_REAL

    issues = ai_pattern_issues(_CLOCK_DAYBREAK_REAL, chapter_id="ch_x")
    hits = [i for i in issues if i.rule_id.startswith("CONT-")]
    assert hits, [i.rule_id for i in issues]
    for hit in hits:
        assert hit.category == "continuity", (hit.rule_id, hit.category)
    # severity / 后果档不受本次归类修复影响（连续性算子恒 warning、恒 auto）
    assert all(hit.severity == "warning" for hit in hits)
    assert all(hit.gate == "auto" for hit in hits)


def test_continuity_category_mapping_is_derived_from_taxonomy():
    """映射必须从 taxonomy 名册派生——新增确定性连续性规则时只需改一处。

    若将来有人在 taxonomy 里加第三条 ``CONT-*``，本用例会要求 engine 的映射同步跟上
    （不靠人手记得），否则报「名册与映射不一致」。
    """
    from packages.core.quality.continuity_taxonomy import (
        DETERMINISTIC_CONTINUITY_RULE_IDS,
    )

    for rid in DETERMINISTIC_CONTINUITY_RULE_IDS:
        assert engine_mod._AI_PATTERN_CATEGORY.get(rid) == "continuity", rid


# ---------------------------------------------------------------------------
# 6) evidence 摘录必须与规则语义相关（2026-09-21 检修 m3）
# ---------------------------------------------------------------------------


def test_dialogue_low_evidence_carries_the_ratio_not_prose_head():
    """``AI-DIALOGUE-LOW`` 的证据必须是占比摘要，而不是正文前 120 字。

    缺陷形状（m3 亲验复现）：该命中的 dict 没有 ``samples`` / ``words`` 键，
    ``_ai_pattern_evidence`` 只能退回 ``excerpt``（正文开头）——作者看到一段与
    「对话占比 8%」无关的原文，等于没有证据。撤掉 ``samples`` 键本用例必红。
    """
    prose = ("他站起来，走向门口。手指搭在门把上停了一秒，然后拉开。" * 30)
    hits = [i for i in ai_pattern_issues(prose, chapter_id="ch_x")
            if i.rule_id == "AI-DIALOGUE-LOW"]
    assert hits, "低对白正文应命中 AI-DIALOGUE-LOW"
    refs = hits[0].evidence_refs
    assert refs and any("对话占比" in r for r in refs), refs
    # 证据里没有正文开头（否则又回到 excerpt 兜底）
    assert all(not r.startswith("他站起来") for r in refs), refs
