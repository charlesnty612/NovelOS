"""v0.1.2 protagonist 补强相关机检（G-sim 递归 + jsonschema 接受/拒绝）。"""

from __future__ import annotations

from typing import Any

import pytest

from packages.workflows.deconstruct_book.pipeline import (
    _collect_strings,
    _g_sim_node,
    _validate_canon_schema,
)


def _base_canon(*, with_protagonist: bool = False) -> dict[str, Any]:
    canon: dict[str, Any] = {
        "logline": "草根主角逆袭 → 家族比试首胜 → 反派出高手 → 主角获跃升",
        "spine": [
            {
                "chapter_index": 1,
                "title_pattern": "主角受辱-偶获宝",
                "function_tag": "hook",
                "summary_pattern": "主角类型 1 出身低微被同族欺压，偶获物品类型 V",
            }
        ],
        "faction_map": {
            "factions": [
                {"faction_id": "fac_hero", "type_pattern": "主角派", "power_layer": "low"},
            ],
            "relations": [],
            "power_layers": [{"layer": "low", "count": 1}],
        },
        "emotion_curve": [{"chapter_index": 1, "valence": 2, "marker_type": "buildup"}],
        "payoff_list": [
            {
                "payoff_id": "payoff_001_o",
                "chapter_index": 1,
                "type": "other",
                "intensity": 1,
                "setup_chapter": 1,
                "payoff_chapter": 1,
            }
        ],
        "techniques": [
            {
                "technique_id": "tech_001",
                "name_pattern": "黄金三章强制钩子",
                "location_pattern": "前三章章末",
                "effect_pattern": "前 300 字冲突前置 + 三章钩子 + 三章内首次小高潮",
            }
        ],
        "rhythm": {
            "mini_climax_interval": {"median": 3, "p25": 2, "p75": 5},
            "major_climax_interval": {"median": 5, "p25": 4, "p75": 7},
            "chapter_end_hook_rate": 0.85,
            "golden_three_compliance": {
                "first_300_chars_conflict": True,
                "ch1_end_hook": True,
                "ch2_end_hook": True,
                "ch3_end_hook": True,
                "mini_climax_in_first_three": True,
            },
        },
        "style_params": {
            "sentence_length_distribution": {"mean": 18.0, "median": 16.0, "max": 80},
            "dialogue_ratio": 0.25,
            "action_ratio": 0.45,
            "pov": "third_limited",
            "paragraph_length_distribution": {"mean": 120.0, "median": 100.0, "max": 600},
            "psychological_ratio": 0.15,
            "environment_ratio": 0.15,
        },
        "metadata": {
            "source_book_title": "测试参照书",
            "deconstruct_date": "2026-08-31T00:00:00Z",
            "deconstruct_version": "deconstruct-book.v0",
            "target_reader_profile": "male_fantasy",
            "license_check_status": {
                "checked": False,
                "license": "unknown",
                "compatible": False,
            },
        },
    }
    if with_protagonist:
        canon["protagonist"] = {
            "identity": "草根逆袭型主角",
            "personality_tags": ["隐忍", "重情", "好强"],
            "core_drive": "打破同辈压制登上巅峰，证明出身不决定上限",
            "foil_techniques": [
                "前期压制-中期对等-后期反压",
                "同辈对照镜映主角成长",
            ],
        }
    return canon


# ---------------------------------------------------------------------------
# jsonschema 校验
# ---------------------------------------------------------------------------


def test_schema_accepts_canon_without_protagonist():
    """存量 canon 不含 protagonist → schema 仍通过。"""
    canon = _base_canon(with_protagonist=False)
    errs = _validate_canon_schema(canon)
    assert errs == [], f"存量 canon 应通过 schema 校验，实际错误：{errs}"


def test_schema_accepts_canon_with_protagonist():
    """含 protagonist → schema 通过。"""
    canon = _base_canon(with_protagonist=True)
    errs = _validate_canon_schema(canon)
    assert errs == [], f"含 protagonist 应通过 schema 校验，实际错误：{errs}"


def test_schema_rejects_unknown_top_level_field():
    """顶层误增字段（与现有 additionalProperties: false 策略一致）→ schema 拒绝。"""
    canon = _base_canon(with_protagonist=False)
    canon["protagonist_typo"] = {"identity": "x"}
    errs = _validate_canon_schema(canon)
    assert errs, "顶层误增字段必须被 schema 拒绝"


def test_schema_rejects_protagonist_with_too_many_tags():
    """personality_tags 超过 6 项 → schema 拒绝。"""
    canon = _base_canon(with_protagonist=False)
    canon["protagonist"] = {
        "identity": "草根逆袭型主角",
        "personality_tags": ["a", "b", "c", "d", "e", "f", "g"],
        "core_drive": "x" * 50,
        "foil_techniques": [],
    }
    errs = _validate_canon_schema(canon)
    assert errs, "personality_tags 超 6 项必须被 schema 拒绝"


def test_schema_rejects_protagonist_identity_too_long():
    """identity 超过 80 字 → schema 拒绝。"""
    canon = _base_canon(with_protagonist=False)
    canon["protagonist"] = {
        "identity": "x" * 81,
        "personality_tags": [],
        "core_drive": "",
        "foil_techniques": [],
    }
    errs = _validate_canon_schema(canon)
    assert errs, "identity 超 80 字必须被 schema 拒绝"


# ---------------------------------------------------------------------------
# G-sim 覆盖（_collect_strings 递归必须拿到 protagonist 子串）
# ---------------------------------------------------------------------------


def test_collect_strings_includes_protagonist_strings():
    """_collect_strings 递归遍历 → 必拿到 protagonist 子串。"""
    canon = _base_canon(with_protagonist=True)
    strings = _collect_strings(canon)
    joined = "".join(strings)
    # 4 子字段任一关键字应出现在拼接结果中
    assert "草根逆袭型主角" in joined
    assert "隐忍" in joined
    assert "打破同辈压制登上巅峰" in joined
    assert "前期压制-中期对等-后期反压" in joined


def test_g_sim_blocks_when_protagonist_overlaps_original_text():
    """protagonist 内某子串与原文 13 字连续重叠 → G-sim 抛 ValueError 阻断。

    这里直接构造一个 13 字重叠短语塞进 protagonist.identity，验证
    _g_sim_node 抛 ValueError；该测试同时验证 _collect_strings 已
    把 protagonist 子串纳入拼接。
    """
    # 原文里塞一段 30 字原文
    overlap_phrase = "主角类型 1 在某场合公开演示并被广泛议论"  # 18 字符
    original_text = (
        "这是参照书正文。" + overlap_phrase + "。后续内容与拆书无关。" * 5
    )
    canon = _base_canon(with_protagonist=False)
    # 把 overlap_phrase 完整塞进 identity（任意子串 ≥13 字连续即可）
    canon["protagonist"] = {
        "identity": overlap_phrase,
        "personality_tags": [],
        "core_drive": "",
        "foil_techniques": [],
    }

    ctx = {"canon_json": canon, "text": original_text}
    with pytest.raises(ValueError, match=r"G-sim 阻断"):
        _g_sim_node(ctx)


def test_g_sim_passes_when_protagonist_does_not_overlap_original_text():
    """protagonist 子串与原文无 13 字连续重叠 → G-sim 通过。"""
    canon = _base_canon(with_protagonist=True)  # 默认 protagonist 不含与原文重叠
    ctx = {
        "canon_json": canon,
        "text": "这是一段与 protagonist 子串无任何重叠的纯抽象说明文本。" * 10,
    }
    out = _g_sim_node(ctx)
    assert out["g_sim_passed"] is True
    assert out["overlaps"] == []