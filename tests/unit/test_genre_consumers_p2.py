"""题材包读侧投影（packages/core/genre/consumers.py，题材库 P2）单元测试。

覆盖：
1. opening_rules：规范化 / 非法条目跳过 / chapter_no 语义；
2. forbidden_words：去重保序 / 非法项跳过 / 上限；
3. count_forbidden_word_hits：子串计数（命中 / 未命中 / 空文本）；
4. critic_rubric：payoff_focus 文本化（带 verify_hint）/ 未知 type_id 原样保留 /
   缺席返回 None / 预算截断带标记且不超预算。
"""

from __future__ import annotations

import json

from packages.core.genre.consumers import (
    CRITIC_RUBRIC_MAX_CHARS,
    CRITIC_RUBRIC_TRUNCATED_KEY,
    count_forbidden_word_hits,
    critic_rubric,
    forbidden_words,
    opening_rules,
)

_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.1.0",
    "payoff_types": [
        {
            "type_id": "face_slap",
            "name": "打脸",
            "strength": "S",
            "density_cap": "每卷 2~3 次",
            "min_interval_chapters": 2,
            "verify_hint": "打脸后至少三人当场反应",
        }
    ],
    "critic_rubric": {"payoff_focus": ["face_slap"]},
    "opening_rules": [
        {
            "check_id": "sys_bind_ch1",
            "description": "系统绑定不得晚于第 1 章",
            "chapter_no": 1,
            "requirement": "第 1 章必须出现系统绑定",
        },
        {"check_id": "whole_window", "requirement": "开篇必须给出冲突"},
        {"check_id": "", "requirement": "缺少 check_id 的条目"},
        {"check_id": "no_req", "chapter_no": 2},
        {"check_id": "bad_chapter", "chapter_no": 99, "requirement": "越界章号"},
        "not-a-dict",
    ],
}


# ---------------------------------------------------------------------------
# opening_rules
# ---------------------------------------------------------------------------


def test_opening_rules_normalizes_and_skips_invalid():
    rules = opening_rules(_PAYLOAD)
    assert [r["check_id"] for r in rules] == ["sys_bind_ch1", "whole_window"]
    assert rules[0]["chapter_no"] == 1
    # 无 chapter_no → None（语义：黄金三章整体窗口）
    assert rules[1]["chapter_no"] is None
    assert rules[1]["description"] == ""


def test_opening_rules_absent_or_malformed_returns_empty():
    assert opening_rules({}) == []
    assert opening_rules(None) == []
    assert opening_rules({"opening_rules": "x"}) == []
    assert opening_rules({"opening_rules": []}) == []


# ---------------------------------------------------------------------------
# forbidden_words / count_forbidden_word_hits
# ---------------------------------------------------------------------------


def test_forbidden_words_dedupe_and_clean():
    words = forbidden_words(
        {"style_constraints": {"forbidden_words": ["仿佛", "仿佛", "", "  ", 7, "宛如"]}}
    )
    assert words == ["仿佛", "宛如"]


def test_forbidden_words_cap_at_50():
    raw = [f"w{i}" for i in range(80)]
    words = forbidden_words({"style_constraints": {"forbidden_words": raw}})
    assert len(words) == 50
    assert words[0] == "w0"


def test_forbidden_words_absent_returns_empty():
    assert forbidden_words({}) == []
    assert forbidden_words({"style_constraints": {}}) == []
    assert forbidden_words({"style_constraints": {"forbidden_words": "仿佛"}}) == []


def test_count_forbidden_word_hits_substring_counts():
    text = "命运的齿轮转动了，命运的齿轮又一次转动。"
    hits = count_forbidden_word_hits(text, ["命运的齿轮", "仿佛"])
    assert hits == [("命运的齿轮", 2)]


def test_count_forbidden_word_hits_empty_inputs():
    assert count_forbidden_word_hits("", ["仿佛"]) == []
    assert count_forbidden_word_hits("仿佛", []) == []
    assert count_forbidden_word_hits("风中", ["仿佛"]) == []


# ---------------------------------------------------------------------------
# critic_rubric
# ---------------------------------------------------------------------------


def test_critic_rubric_textualizes_payoff_focus_with_verify_hint():
    rubric = critic_rubric(_PAYLOAD)
    assert rubric is not None
    assert CRITIC_RUBRIC_TRUNCATED_KEY not in rubric
    assert "taboo_notes" not in rubric  # 缺席段不塞空壳
    focus = rubric["payoff_focus"]
    assert len(focus) == 1
    line = focus[0]
    assert line.startswith("face_slap：打脸")
    assert "强度 S" in line
    assert "密度上限 每卷 2~3 次" in line
    assert "核销提示 打脸后至少三人当场反应" in line


def test_critic_rubric_unknown_type_id_kept_verbatim():
    rubric = critic_rubric(
        {
            "payoff_types": [{"type_id": "face_slap", "name": "打脸"}],
            "critic_rubric": {"payoff_focus": ["unknown_type"]},
        }
    )
    assert rubric == {"payoff_focus": ["unknown_type"]}


def test_critic_rubric_absent_returns_none():
    assert critic_rubric({}) is None
    assert critic_rubric({"critic_rubric": {}}) is None
    assert critic_rubric({"critic_rubric": {"payoff_focus": []}}) is None
    assert critic_rubric({"critic_rubric": {"taboo_notes": "   "}}) is None
    assert critic_rubric(None) is None


def test_critic_rubric_truncated_key_literal_is_stable():
    """截断标记键名是对外协议（critic-v1.md 里写了字面量），改名即破坏 prompt 契约。"""
    assert CRITIC_RUBRIC_TRUNCATED_KEY == "__genre_rubric_truncated__"
    assert CRITIC_RUBRIC_MAX_CHARS == 1000


def test_critic_rubric_truncates_and_marks_within_budget():
    big = {
        "critic_rubric": {
            "payoff_focus": [f"type_{i:03d}" for i in range(80)],
            "taboo_notes": "禁" * 400,
            "style_notes": "风" * 400,
        }
    }
    rubric = critic_rubric(big)
    assert rubric is not None
    assert rubric[CRITIC_RUBRIC_TRUNCATED_KEY] is True
    encoded = json.dumps(rubric, ensure_ascii=False)
    assert len(encoded) <= CRITIC_RUBRIC_MAX_CHARS, len(encoded)
    # 一票关注点文本优先保留（taboo 先于 style、先于 payoff_focus 条目）
    assert rubric.get("taboo_notes")
    assert len(rubric.get("payoff_focus", [])) < 80


def test_critic_rubric_small_budget_keeps_marker():
    """预算极小时仍带截断标记（标记优先于内容）。"""
    rubric = critic_rubric(
        {"critic_rubric": {"payoff_focus": ["a_1"], "taboo_notes": "长文本" * 10}},
        max_chars=1,
    )
    assert rubric is not None
    assert rubric.get(CRITIC_RUBRIC_TRUNCATED_KEY) is True
