"""爽感维度 H-1~H-5 单元测试（tests.unit.quality.test_payoff）。

目标：每条 H 至少 1 例触发 + 1 例不触发。
"""

from __future__ import annotations

from packages.core.quality.payoff import PayoffContext
from packages.core.quality.payoff import evaluate as payoff_evaluate


def _ctx(
    chapter_number: int = 1,
    draft: str = "",
    payoff_history=None,
    snapshot: dict = None,
    delta: dict = None,
):
    return PayoffContext(
        chapter_number=chapter_number,
        draft=draft,
        payoff_history=list(payoff_history or []),
        snapshot=snapshot or {},
        delta=delta or {},
    )


# ============================================================================
# H-1
# ============================================================================


def test_h1_no_end_hook_warning():
    # 末 200 字不含任何钩子 marker
    draft = "正文：" + ("这是一段没有钩子的普通叙述。" * 20)
    iss = payoff_evaluate(_ctx(draft=draft))
    assert any(i.rule_id == "RULE_H1_NO_END_HOOK" for i in iss)


def test_h1_pass_when_hook_marker():
    draft = "正文：" + ("这是一段普通叙述。" * 10) + "突然，事情发生了！"
    iss = payoff_evaluate(_ctx(draft=draft))
    assert not any(i.rule_id == "RULE_H1_NO_END_HOOK" for i in iss)


# ============================================================================
# H-2
# ============================================================================


def test_h2_no_climax_warning():
    iss = payoff_evaluate(
        _ctx(
            draft="末段突然发生了变故？",
            payoff_history=[0, 0, 0],
            delta={},  # 本章无 resolved / paid
        )
    )
    assert any(i.rule_id == "RULE_H2_NO_CLIMAX_3CH" for i in iss)


def test_h2_pass_with_this_chapter_payoff():
    iss = payoff_evaluate(
        _ctx(
            draft="末段突然发生了变故？",
            payoff_history=[0, 0, 0],
            delta={"resolved_hooks": [{"hook_id": "h1"}]},
        )
    )
    # delta 包含 resolved_hooks ⇒ 本章有 payoff ⇒ H-2 不触发
    assert not any(i.rule_id == "RULE_H2_NO_CLIMAX_3CH" for i in iss)


# ============================================================================
# H-3
# ============================================================================


def test_h3_filler_2ch_warning():
    iss = payoff_evaluate(
        _ctx(
            draft="末段竟然有钩子？",
            payoff_history=[1, 1, 0, 0],  # 最近连续 2 章为 0
            delta={"resolved_hooks": []},
        )
    )
    assert any(i.rule_id == "RULE_H3_FILLER_2CH" for i in iss)


def test_h3_filler_3ch_error():
    iss = payoff_evaluate(
        _ctx(
            draft="末段竟然有钩子？",
            payoff_history=[1, 0, 0, 0],  # 最近连续 3 章为 0
            delta={"resolved_hooks": []},
        )
    )
    assert any(i.severity == "error" and i.rule_id == "RULE_H3_FILLER_3CH" for i in iss)


def test_h3_pass_with_chapter_payoff():
    iss = payoff_evaluate(
        _ctx(
            draft="末段竟然有钩子？",
            payoff_history=[1, 0, 0, 0, 0],
            delta={"resolved_hooks": [{"hook_id": "h1"}]},
        )
    )
    # 本章有 payoff ⇒ streak 不增长 ⇒ 不触发 H-3
    assert not any(i.rule_id.startswith("RULE_H3_FILLER") for i in iss)


# ============================================================================
# H-4（仅 chapter 1/2/3）
# ============================================================================


def test_h4_no_early_conflict_chapter_1():
    draft = "风和日丽，我们漫步在小镇上。看到许多商店，人们都很友善。" * 5
    iss = payoff_evaluate(_ctx(chapter_number=1, draft=draft))
    assert any(i.rule_id == "RULE_H4_NO_EARLY_CONFLICT" for i in iss)


def test_h4_chapter_4_skip():
    iss = payoff_evaluate(_ctx(chapter_number=4, draft="任意无冲突文字"))
    assert not any(i.rule_id.startswith("RULE_H4_") for i in iss)


def test_h4_no_climax_at_3():
    iss = payoff_evaluate(
        _ctx(
            chapter_number=3,
            draft="战斗开启，敌人出现，杀意浓浓？",  # 有冲突 + 钩子
            payoff_history=[0, 0, 0],
            delta={},
        )
    )
    assert any(i.rule_id == "RULE_H4_NO_CLIMAX" for i in iss)


# ============================================================================
# H-5
# ============================================================================


def test_h5_realm_inconsistent():
    snapshot = {
        "world": {
            "world_rules": [
                {
                    "world_rule_id": "w1",
                    "name": "筑基境",
                    "statement": "筑基境界划分",
                    "data_json": {"hard": True},
                }
            ]
        }
    }
    delta = {
        "world_changes": [
            {
                "op": "update",
                "world_kind": "rule",
                "world_id": "w1",
                "field": "statement",
                "before": "筑基境界划分",
                "after": {
                    "name": "筑基境",
                    "statement": "筑基境界改动了描述",
                },
                "evidence": {"chapter_id": "ch_001"},
            }
        ]
    }
    iss = payoff_evaluate(_ctx(snapshot=snapshot, delta=delta))
    assert any(i.rule_id == "RULE_H5_REALM_INCONSISTENT" for i in iss)


def test_h5_pass_consistent_statement():
    snapshot = {
        "world": {
            "world_rules": [
                {
                    "world_rule_id": "w1",
                    "name": "筑基境",
                    "statement": "筑基境界划分",
                    "data_json": {"hard": True},
                }
            ]
        }
    }
    delta = {
        "world_changes": [
            {
                "op": "update",
                "world_kind": "rule",
                "world_id": "w1",
                "field": "statement",
                "before": "筑基境界划分",
                "after": {
                    "name": "筑基境",
                    "statement": "筑基境界划分",
                },
                "evidence": {"chapter_id": "ch_001"},
            }
        ]
    }
    iss = payoff_evaluate(_ctx(snapshot=snapshot, delta=delta))
    assert not any(i.rule_id == "RULE_H5_REALM_INCONSISTENT" for i in iss)


def test_payoff_evaluate_accepts_dict():
    """字典形式的 ctx 也能被接受（engine 仍走 PayoffContext）。"""
    iss = payoff_evaluate(
        {
            "chapter_number": 4,
            "draft": "末段突然有了钩子？",
            "payoff_history": [],
            "snapshot": {},
            "delta": {},
        }
    )
    assert isinstance(iss, list)
