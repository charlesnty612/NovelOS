"""Prompt 契约防回删（题材库 P1b/P2）。

P2 的头号价值点是 scene_type 输出契约（决定配比核销的模型遵从度）与 critic 的
题材 rubric 审查节。这两段一旦被回删，机制仍然「能跑」但模型不再遵守——因此用
文档存在性测试钉住关键契约短语（与 test_prompt_docs_no_project_specific 同款
防回归口径）。
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "agents" / "prompts"

# scene_planner：scene_type 输出契约的关键短语（缺失即回删）。
_SCENE_PLANNER_MARKERS: tuple[str, ...] = (
    "可选输入：`genre_pack`",
    # Rule 12 标题（含细则编号，避免与正文里的引用混淆）
    "`scene_type` 输出契约（题材库 P1b 核销口径；P2 显式化）",
    "ratio_declarations",
    "target_words",
    # 三条细则各自的判别短语
    "取值必须取自 `ratio_declarations` 的**键**",
    "核销阈值 ±10%",
    "声明了配比却漏标 `scene_type`",
    "E-SPL-10",
)

# critic：genre_rubric 可选输入节的关键短语（缺失即回删）。
_CRITIC_MARKERS: tuple[str, ...] = (
    "可选输入：`genre_rubric`",
    "payoff_focus",
    "taboo_notes",
    "style_notes",
    "__genre_rubric_truncated__",
    "E-CRT-10",
    # §3.10 职责三条各自的判别短语（防整条被删）
    "维度聚焦（`payoff_focus`）",
    "禁忌一票关注点（`taboo_notes`）",
    "文风要点（`style_notes`）",
    "`__genre_rubric_truncated__ == true`",
)


def _read(name: str) -> str:
    fp = PROMPTS_DIR / name
    assert fp.exists(), f"prompt 缺失：{fp}"
    return fp.read_text(encoding="utf-8")


def test_scene_planner_prompt_keeps_scene_type_contract():
    text = _read("scene_planner-v1.md")
    missing = [m for m in _SCENE_PLANNER_MARKERS if m not in text]
    assert not missing, f"scene_planner-v1.md 丢失契约短语：{missing}"


def test_scene_planner_prompt_output_schema_declares_scene_type():
    text = _read("scene_planner-v1.md")
    # 输出 schema 段里必须出现 scene_type 字段行（可选字段声明）
    schema_section = text.split("## 7. Output Schema", 1)[1].split("## 8.", 1)[0]
    assert "scene_type" in schema_section, "§7 输出 schema 未声明 scene_type 字段"
    assert "可选" in schema_section


def test_critic_prompt_keeps_genre_rubric_input_section():
    text = _read("critic-v1.md")
    missing = [m for m in _CRITIC_MARKERS if m not in text]
    assert not missing, f"critic-v1.md 丢失契约短语：{missing}"


def test_critic_prompt_declares_taboo_is_high_severity():
    """禁忌命中 → high（一票关注点）必须在 prompt 里写死。"""
    text = _read("critic-v1.md")
    assert "severity=high" in text or "severity` **至少**" in text
    assert "一票" in text


def test_prompts_still_registerable_headers_intact():
    """prompt 版本头未被改动（注册表按文件名 + 内容同步，版本号不能漂）。"""
    assert "critic:v1" in _read("critic-v1.md")
    assert "scene_planner:v1" in _read("scene_planner-v1.md")
