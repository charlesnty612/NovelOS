"""``workflows/repair_policy.py`` 单测：策略表每一行 + 两条硬规矩（未知即停 / F-19）。

覆盖（= 任务书要求逐条可验）：

1. 表每一行的形状 → 动作映射：
   - ``regenerate``：章内重复（三通道各一条）、字数带下限大缺口；
   - ``revise``：局部形态类规则逐条、小缺口长度、超带压缩、作者主观驳回；
   - ``stop``：读不到报告、缺 rule_id、表外 rule_id（连续性 / 设定类）。
2. **未知 / 缺失 rule_id 硬停**，不得静默落 revise。
3. **F-19 守卫**：任何「占比 / 频率」类风格偏好指标（``AI-DIALOGUE-LOW`` 等）不得映射到
   ``revise`` 或 ``regenerate``——只能落 ``stop``。
4. 驱动一致性：``scripts/produce_chapters.py:REVISABLE_RULE_IDS`` 是服务端
   ``REVISE_RULE_IDS`` 的**子集**（驱动侧一律更保守，见该脚本 ``decide_review``）。
5. 轮次耗尽的失败信息**点名**修不动的形状（不是「轮次耗尽」四个字）。

本文件不需要 server / DB：策略是纯函数（不变量 1）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.produce_chapters as pc  # noqa: E402
from packages.core.api.routers.workflows import repair_policy as rp  # noqa: E402

# ---------------------------------------------------------------------------
# fixture：报告切片（字段名与 chapter_review.pipeline._basic_checks_node 落点一致）
# ---------------------------------------------------------------------------


def _report(**over) -> dict:
    base: dict = {
        "word_count": 2500,
        "target_word_count": 2500,
        "within_range": True,
        "word_band": {"low": 2125, "high": 2875},
        "warnings": [],
        "errors": [],
        "ai_pattern_hits": [],
    }
    base.update(over)
    return base


def _short_report(achieved: int = 854, low: int = 2125, high: int = 2875) -> dict:
    """字数带下限缺口形状（``W-LEN-DEVIATION`` 的 error 结构 + 顶层带数据）。"""
    return _report(
        word_count=achieved,
        within_range=False,
        word_band={"low": low, "high": high},
        warnings=[f"[W-LEN-DEVIATION] visible={achieved} band {low}~{high}"],
        errors=[
            {
                "rule_id": "W-LEN-DEVIATION",
                "severity": "error",
                "message": "字数欠带",
                "word_band": {"low": low, "high": high},
                "visible_chars": achieved,
                "deviation_pct": -60.0,
            }
        ],
    )


# ---------------------------------------------------------------------------
# stop：读不到 / 认不出
# ---------------------------------------------------------------------------


def test_missing_report_is_a_hard_stop_not_a_silent_revise():
    """报告缺失 ⇒ stop（不猜）。撤「report None ⇒ stop」→ 本测试红。"""
    for report in (None, {}, "not a dict", []):
        decision = rp.decide_repair(report, remaining_rounds=2)  # type: ignore[arg-type]
        assert decision.action == "stop", decision
        assert decision.reason == "no_review_report", decision


def test_error_without_rule_id_is_a_hard_stop():
    """error 缺 rule_id ⇒ 无法定向 ⇒ stop（不许按 revise 蒙一把）。"""
    decision = rp.decide_repair(
        _report(errors=[{"severity": "error", "message": "没有 rule_id"}]),
        remaining_rounds=2,
    )
    assert decision.action == "stop", decision
    assert decision.reason == "error_without_rule_id", decision


@pytest.mark.parametrize(
    "rule_id",
    [
        "RULE_CHAR_DEAD_ACTIVE",  # 角色状态：已死亡角色被写活动字段
        "RULE_WORLD_HARD_RULE_CHANGED",  # 世界观设定写坏
        "SCHEMA_VALIDATION_FAILED",  # 结构损坏
        "RULE_SOMETHING_BRAND_NEW",  # 表里没有的新规则
    ],
)
def test_unknown_rule_id_is_a_hard_stop(rule_id: str):
    """连续性 / 逻辑 / 设定类与一切表外规则 ⇒ stop，绝不自动修（不猜）。"""
    decision = rp.decide_repair(
        _report(errors=[{"rule_id": rule_id, "severity": "error"}]), remaining_rounds=3
    )
    assert decision.action == "stop", decision
    assert decision.reason == "unknown_rule_id", decision
    assert rule_id in decision.detail, decision


# ---------------------------------------------------------------------------
# regenerate 一行：章内重复（三通道）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "channel",
    ["errors", "ai_pattern_hits", "warnings"],
)
def test_in_chapter_repetition_regenerates_through_every_channel(channel: str):
    """重复类规则经 errors / ai_pattern_hits / warnings 三处任一命中 ⇒ regenerate。

    ``AI-BEAT-REPEAT`` 恒 warning（只在后两处出现），``RULE_STYLE_REPETITION_TRIGRAM``
    走 quality 评分侧（可能作为 error 或 confirm 档 warning 回灌进 errors）。
    **severity 不参与判定**：confirm 档成员本就恒 warning。
    """
    rid = "RULE_STYLE_REPETITION_TRIGRAM" if channel == "errors" else "AI-BEAT-REPEAT"
    if channel == "warnings":
        # warnings 是文本行通道（_basic_checks_node 以 ``[RULE-ID] <message>`` 追加）
        report = _report(warnings=[f"[{rid}] 同章远距小句复现 3 处"])
    else:
        report = _report(**{channel: [{"rule_id": rid, "severity": "warning"}]})
    decision = rp.decide_repair(report, remaining_rounds=2)
    assert decision.action == "regenerate", decision
    assert decision.reason == "in_chapter_repetition", decision
    assert rid in decision.rule_ids, decision


def test_repetition_regenerate_beats_surgical_revise():
    """重复 + 局部形态类同时命中 ⇒ 取重复（revise 正是重复的制造者）。"""
    decision = rp.decide_repair(
        _report(
            errors=[
                {"rule_id": "AI-PUNCT-ABUSE", "severity": "error"},
                {"rule_id": "RULE_STYLE_REPETITION_TRIGRAM", "severity": "warning"},
            ]
        ),
        remaining_rounds=2,
    )
    assert decision.action == "regenerate", decision
    assert decision.reason == "in_chapter_repetition", decision


def test_repetition_channel_lookup_is_not_text_only():
    """重复命中只认结构化字段或 ``[RULE-ID]`` 文本行；正文里出现规则名不算命中。"""
    decision = rp.decide_repair(
        _report(warnings=["本章没有 AI-BEAT-REPEAT 问题的说明文字"]), remaining_rounds=2
    )
    assert decision.action == "revise", decision
    assert decision.reason == "author_requested_revision", decision


# ---------------------------------------------------------------------------
# regenerate 一行：字数带下限大缺口（原 ``_round_needs_fresh_write`` 的转正）
# ---------------------------------------------------------------------------


def test_length_shortfall_beyond_capped_reach_regenerates():
    """854→2125（需 +148.8%）远超声剩余 2 轮可达的 +10.25% ⇒ regenerate。"""
    decision = rp.decide_repair(_short_report(), remaining_rounds=2)
    assert decision.action == "regenerate", decision
    assert decision.reason == "length_shortfall_beyond_revise_cap", decision
    assert decision.shortfall == (854, 2125), decision
    assert "148" in decision.detail, decision  # 依据（需 +148.8%）写进 detail


def test_length_shortfall_within_capped_reach_still_revises():
    """2100→2125（需 +1.19%）在 2 轮可达的 +10.25% 内 ⇒ 仍走 capped revise。"""
    decision = rp.decide_repair(_short_report(achieved=2100), remaining_rounds=2)
    assert decision.action == "revise", decision
    assert decision.reason == "length_shortfall_within_revise_cap", decision


def test_length_cap_boundary_is_strict_greater():
    """缺口 == 可达幅度 ⇒ 不算超（严格大于才逃逸）。撤「> 改 >=」→ 本测试红。

    边界必须用**精确可表示**的比值才测得准：``(1.05**2 - 1)`` 是 0.10250000000000015，
    与字面上的 0.1025 不等（浮点），拿它测边界等于没测。这里显式给
    ``revise_max_net_growth_ratio=0.5`` 且剩余 1 轮 ⇒ 可达幅度恰好 0.5，
    再让缺口恰好等于 0.5（1000→1500）。
    """
    exactly = rp.decide_repair(
        _short_report(achieved=1000, low=1500, high=2000),
        remaining_rounds=1,
        revise_max_net_growth_ratio=0.5,
    )
    assert exactly.action == "revise", exactly
    over = rp.decide_repair(
        _short_report(achieved=1000, low=1501, high=2000),
        remaining_rounds=1,
        revise_max_net_growth_ratio=0.5,
    )
    assert over.action == "regenerate", over


def test_length_escape_shrinks_as_rounds_are_consumed():
    """同一份报告：剩余 3 轮可达 +15.8% ⇒ revise；剩余 1 轮只有 +5% ⇒ regenerate。"""
    report = _short_report(achieved=2000, low=2205, high=2800)  # 需 +10.25%
    assert rp.decide_repair(report, remaining_rounds=3).action == "revise"
    assert rp.decide_repair(report, remaining_rounds=1).action == "regenerate"


def test_over_band_still_revises():
    """超带（压缩方向）⇒ revise：revise 的 +5% cap 只约束增侧。"""
    report = _report(word_count=5000, within_range=False)
    decision = rp.decide_repair(report, remaining_rounds=1)
    assert decision.action == "revise", decision
    assert decision.reason == "length_over_band", decision


def test_length_shortfall_picks_the_largest_gap():
    """两处带数据取缺口最大者（顶层与 errors 条目）——取小者会漏判逃逸。"""
    report = _short_report(achieved=2000, low=2205, high=2800)
    report["word_count"] = 1200
    report["word_band"] = {"low": 2125, "high": 2875}
    assert rp.length_band_shortfall(report) == (1200, 2125)
    assert rp.decide_repair(report, remaining_rounds=2).action == "regenerate"


def test_length_measurement_ignores_over_band_and_non_int():
    """超带数据不产出「下限缺口」；非整数 / 缺字段一律不产（宁缺勿错判）。"""
    assert rp.length_band_shortfall(_report(word_count=5000, within_range=False)) is None
    assert rp.length_band_shortfall(_report(word_count="2500", within_range=False)) is None
    assert rp.length_band_shortfall(_report(word_count=True, within_range=False)) is None
    assert rp.length_band_shortfall({}) is None


# ---------------------------------------------------------------------------
# revise 一行：局部形态类（逐条）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", sorted(rp.REVISE_RULE_IDS))
def test_surgical_style_rule_revises(rule_id: str):
    """局部形态类规则逐条 ⇒ revise（这是 revise 真正擅长的事）。"""
    decision = rp.decide_repair(
        _report(errors=[{"rule_id": rule_id, "severity": "error"}]), remaining_rounds=2
    )
    assert decision.action == "revise", decision
    assert decision.reason == "surgical_style_rules", decision
    assert rule_id in decision.rule_ids, decision


def test_genre_word_band_deviation_routes_through_length_branch():
    """题材字数带（GENRE-WORD-BAND-DEVIATION）走长度分支，不被当未知规则硬停。"""
    report = _report(
        word_count=900,
        within_range=False,
        errors=[
            {
                "rule_id": "GENRE-WORD-BAND-DEVIATION",
                "severity": "warning",
                "visible_chars": 900,
                "word_band": {"low": 2125, "high": 2875},
            }
        ],
    )
    decision = rp.decide_repair(report, remaining_rounds=2)
    assert decision.action == "regenerate", decision
    assert decision.reason == "length_shortfall_beyond_revise_cap", decision


def test_clean_report_revises_on_author_request():
    """报告无 error（作者主观驳回）⇒ revise：改稿意见由 plan_json.revision_note 承载。"""
    decision = rp.decide_repair(_report(), remaining_rounds=2)
    assert decision.action == "revise", decision
    assert decision.reason == "author_requested_revision", decision


# ---------------------------------------------------------------------------
# 不变量：F-19（占比 / 频率类风格偏好指标不得成为任何自动修复动作）
# ---------------------------------------------------------------------------


def test_ratio_metrics_never_map_to_revise_or_regenerate():
    """F-19 守卫：占比类指标只能落 stop。

    撤「表外 ⇒ stop」分支、或把 ``AI-DIALOGUE-LOW`` 塞进 ``REVISE_RULE_IDS`` → 本测试红。
    """
    for rule_id in sorted(rp.RATIO_METRIC_RULE_IDS):
        assert rule_id not in rp.REVISE_RULE_IDS, rule_id
        assert rule_id not in rp.REGENERATE_RULE_IDS, rule_id
        decision = rp.decide_repair(
            _report(errors=[{"rule_id": rule_id, "severity": "error"}]), remaining_rounds=2
        )
        assert decision.action == "stop", (rule_id, decision)
        assert decision.reason == "unknown_rule_id", (rule_id, decision)


def test_dialogue_low_is_excluded_by_name():
    """F-19 的实证对象（对话占比）：历史事故「为凑占比灌对白」——明文禁止。"""
    assert "AI-DIALOGUE-LOW" not in rp.REVISE_RULE_IDS
    assert "AI-DIALOGUE-LOW" not in rp.REGENERATE_RULE_IDS
    assert "AI-DIALOGUE-LOW" in rp.RATIO_METRIC_RULE_IDS


# ---------------------------------------------------------------------------
# 不变量：表结构自洽 + 驱动一致性
# ---------------------------------------------------------------------------


def test_known_rule_ids_cover_every_table_row():
    """KNOWN = 长度 ∪ 重产 ∪ 修订；任何一类漏进 KNOWN 都会把该规则变成「未知」硬停。"""
    assert rp.KNOWN_RULE_IDS == (
        rp.LENGTH_RULE_IDS | rp.REGENERATE_RULE_IDS | rp.REVISE_RULE_IDS
    )
    assert rp.KNOWN_RULE_IDS & rp.RATIO_METRIC_RULE_IDS == frozenset()
    assert rp.REVISE_RULE_IDS & rp.REGENERATE_RULE_IDS == frozenset()


def test_driver_revisable_set_agrees_with_server_policy_table():
    """驱动（``scripts/produce_chapters.py``）的可自动修集合必须落在服务端表内。

    驱动侧只做「驳回 + 交给服务端修」，因此它认的规则必须都是服务端能定向改稿的局部
    形态类规则；反过来不要求相等——驱动对重复类另有更强的闸门（``decide_review`` 第 1 步
    直接按草稿重复率拒批），且服务端的 ``regenerate`` 是服务端自己的重产尝试，驱动不参与。
    """
    auto_repairable = rp.REVISE_RULE_IDS | rp.REGENERATE_RULE_IDS | rp.LENGTH_RULE_IDS
    assert pc.REVISABLE_RULE_IDS <= auto_repairable, sorted(
        pc.REVISABLE_RULE_IDS - auto_repairable
    )
    # 驱动不在可自动修集合里的规则，服务端也不得擅自「修」：凡服务端判 stop 的规则，
    # 驱动同样不认（反之不成立——重复类是服务端 regenerate、驱动拒批的刻意差异）。
    assert pc.REVISABLE_RULE_IDS <= rp.KNOWN_RULE_IDS
    # 占比指标两侧都不得出现（F-19 双端一致）
    assert not (pc.REVISABLE_RULE_IDS & rp.RATIO_METRIC_RULE_IDS)


def test_decide_repair_is_pure(tmp_path: Path):
    """纯函数：同样输入 → 同样输出，且不触碰文件系统 / DB（本测试不建任何库）。"""
    report = _short_report()
    first = rp.decide_repair(report, remaining_rounds=2)
    second = rp.decide_repair(dict(report), remaining_rounds=2)
    assert first == second
    assert list(tmp_path.iterdir()) == []


def test_decision_payload_is_auditable():
    """决策必须自带「为什么」：action / reason / shape / rule_ids / detail 齐全。"""
    decision = rp.decide_repair(_short_report(), remaining_rounds=2)
    payload = decision.as_dict()
    assert payload["action"] == "regenerate"
    assert payload["reason"] == "length_shortfall_beyond_revise_cap"
    assert payload["shape"] == rp.SHAPE_LABELS["length_shortfall_beyond_revise_cap"]
    assert payload["shortfall"] == [854, 2125]
    assert payload["detail"]


# ---------------------------------------------------------------------------
# 轮次耗尽的失败信息
# ---------------------------------------------------------------------------


def test_describe_exhaustion_names_the_shape_it_could_not_fix():
    """耗尽信息必须**点名**形状（任务书：不得只说「轮次耗尽」）。"""
    message = rp.describe_exhaustion(
        ["surgical_style_rules", "surgical_style_rules"], max_iter=2
    )
    assert "surgical_style_rules" in message
    assert rp.SHAPE_LABELS["surgical_style_rules"] in message
    assert "2" in message and "轮次耗尽" in message
    # 去重：同一形状不重复列出
    assert message.count("surgical_style_rules（") == 1


def test_describe_exhaustion_without_decisions_is_still_honest():
    """一轮都没跑（max_iter=0）时不得编造形状，明确写「无决策记录」。"""
    message = rp.describe_exhaustion([], max_iter=0)
    assert "无决策记录" in message
