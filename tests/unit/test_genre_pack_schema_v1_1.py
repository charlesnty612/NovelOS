"""genre-pack schema v1.1.0：向后兼容 + 新段校验（题材库 P2）。

覆盖：
1. 旧 v1.0.0 payload 在新 schema 下继续有效（minor 兼容）；
2. v1.1.0 新段（opening_rules / critic_rubric）合法载荷通过；
3. 版本线越界（v2.0.0）与新段类型错误（chapter_no 出 1~3、payoff_focus 非字符串、
   opening_rules 非数组、requirement 缺失）→ 校验错误；
4. 模型常量与 schema 文件已对齐 v1.1.0。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.genre import (
    GENRE_PACK_SCHEMA_PATH,
    GENRE_PACK_SCHEMA_VERSION,
    GenrePackPayload,
    validate_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

_OLD_V1_0_0_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "payoff_types": [
        {"type_id": "face_slap", "name": "打脸", "strength": "S", "density_cap": "每卷 2~3 次"}
    ],
    "structure_templates": {"structure_model": "单元剧"},
    "pacing": {"chapter_words": {"target": 2500}},
    "ratio_declarations": {"action": 0.7, "transition": 0.3},
    "style_constraints": {"forbidden_words": ["仿佛"], "notes": "短句为主"},
}

_NEW_V1_1_0_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.1.0",
    "payoff_types": [
        {
            "type_id": "face_slap",
            "name": "打脸",
            "strength": "S",
            "density_cap": "每卷 2~3 次",
            "verify_hint": "打脸后至少三人当场反应",
        }
    ],
    "opening_rules": [
        {
            "check_id": "sys_bind_ch1",
            "description": "系统绑定不得晚于第 1 章",
            "chapter_no": 1,
            "requirement": "第 1 章必须出现系统绑定",
        },
        {"check_id": "hook_beat", "requirement": "第 3 章必须出现打脸"},
    ],
    "critic_rubric": {
        "payoff_focus": ["face_slap"],
        "taboo_notes": "不得出现具体平台名",
        "style_notes": "短句为主，对话推进",
    },
}


def test_old_v1_0_0_payload_still_valid():
    """旧 v1.0.0 payload 继续有效（v1 线内 minor 兼容，只加可选段）。"""
    assert validate_payload(_OLD_V1_0_0_PAYLOAD) == []


def test_new_v1_1_0_payload_with_new_sections_valid():
    assert validate_payload(_NEW_V1_1_0_PAYLOAD) == []


def test_minimal_payload_without_new_sections_valid():
    assert validate_payload({"schema_version": "genre-pack.v1.1.0"}) == []


def test_wrong_version_line_still_rejected():
    """v1 线内放宽不等于跨主版本放开：v2.0.0 / v0.9.0 仍拒。"""
    assert validate_payload({"schema_version": "genre-pack.v2.0.0"})
    assert validate_payload({"schema_version": "genre-pack.v0.9.0"})


def test_opening_rules_wrong_types_rejected():
    """新段类型错误必须暴露为 schema 错误（API 侧转 422）。"""
    # chapter_no 出 1~3
    bad_chapter = {
        "schema_version": "genre-pack.v1.1.0",
        "opening_rules": [
            {"check_id": "r1", "chapter_no": 9, "requirement": "第 9 章必须转折"}
        ],
    }
    errs = validate_payload(bad_chapter)
    assert any("opening_rules/0/chapter_no" in e for e in errs), errs

    # opening_rules 非数组
    errs = validate_payload({"schema_version": "genre-pack.v1.1.0", "opening_rules": {}})
    assert any("opening_rules" in e for e in errs), errs

    # 缺 requirement（required）
    errs = validate_payload(
        {
            "schema_version": "genre-pack.v1.1.0",
            "opening_rules": [{"check_id": "r1", "chapter_no": 1}],
        }
    )
    assert any("requirement" in e for e in errs), errs

    # check_id 非 snake_case
    errs = validate_payload(
        {
            "schema_version": "genre-pack.v1.1.0",
            "opening_rules": [{"check_id": "R-1", "requirement": "x"}],
        }
    )
    assert any("check_id" in e for e in errs), errs


def test_critic_rubric_wrong_types_rejected():
    # payoff_focus 元素必须是字符串（type_id 引用）
    errs = validate_payload(
        {"schema_version": "genre-pack.v1.1.0", "critic_rubric": {"payoff_focus": [1, 2]}}
    )
    assert any("critic_rubric/payoff_focus" in e for e in errs), errs

    # taboo_notes 必须是字符串
    errs = validate_payload(
        {"schema_version": "genre-pack.v1.1.0", "critic_rubric": {"taboo_notes": ["x"]}}
    )
    assert any("taboo_notes" in e for e in errs), errs

    # critic_rubric 非对象
    errs = validate_payload({"schema_version": "genre-pack.v1.1.0", "critic_rubric": "x"})
    assert any("critic_rubric" in e for e in errs), errs


def test_unknown_top_level_key_still_rejected():
    """顶层键白名单未被 v1.1.0 放宽。"""
    errs = validate_payload(
        {"schema_version": "genre-pack.v1.1.0", "platform_rules": {"x": 1}}
    )
    assert any("platform_rules" in e for e in errs), errs


def test_schema_version_constant_and_default_aligned():
    assert GENRE_PACK_SCHEMA_VERSION == "genre-pack.v1.1.0"
    assert GenrePackPayload().schema_version == "genre-pack.v1.1.0"
    # 常量必须是 schema 文件 accept 的版本串
    assert validate_payload({"schema_version": GENRE_PACK_SCHEMA_VERSION}) == []


def test_schema_file_declares_new_defs():
    """schema 文件确含 v1.1.0 两个新段（防回删）。"""
    schema = json.loads(GENRE_PACK_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert "opening_rules" in schema["properties"]
    assert "critic_rubric" in schema["properties"]
    assert {"OpeningRule", "CriticRubric"} <= set(schema["$defs"])
    assert schema["properties"]["schema_version"]["pattern"] == r"^genre-pack\.v1\.[0-9]+\.[0-9]+$"
    # 路径常量指向仓库内真实文件
    assert GENRE_PACK_SCHEMA_PATH == REPO_ROOT / "docs" / "state-model" / "schemas" / "genre-pack.schema.json"
