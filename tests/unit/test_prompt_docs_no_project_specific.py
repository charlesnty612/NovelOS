"""P2-补洞防回归：docs/agents/prompts/*.md 必须不含项目专属关键词。

2026-08-30 V-Prompt-Migration 触发：
- writer-v1.md 规则 18/19、director-v1.md 规则 15、scene_planner-v1.md 规则 11
  曾混入「火影」项目专属设定（火影/死当共用 prompt，污染其他项目）。
- 已迁移到火影项目 prj_8365f42af5a6 的 world_rules 表，本测试防回归。

关键词选择口径：两字及以上、明确绑定到具体作品的术语。「瑞」单字过宽不列。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "agents" / "prompts"

# 跨项目必清的「项目专属关键词」（两字及以上）。新增需在此追加。
FORBIDDEN_TERMS: tuple[str, ...] = (
    "辉夜",
    "陈瑞",
    "大筒木",
    "桃式",
    "浦式",
    "金式",
    "芝居",
    "魂穿",
    "夺舍",
    "日式神话时代",
    "忍界",
    "火之国",
)

# 必须命中「泛化后」的关键短语（保证泛化是真泛化、不是直接清空）。
# 命中不区分大小写。
EXPECTED_GENERIC_MARKERS: tuple[str, ...] = (
    "语感基线",
    "称谓",
)


def _collect_prompt_files() -> list[Path]:
    if not PROMPTS_DIR.is_dir():
        return []
    return sorted(PROMPTS_DIR.glob("*.md"))


@pytest.mark.parametrize("term", FORBIDDEN_TERMS)
def test_no_project_specific_term_in_prompts(term: str) -> None:
    files = _collect_prompt_files()
    assert files, f"prompts dir empty: {PROMPTS_DIR}"
    offenders: list[str] = []
    for fp in files:
        text = fp.read_text(encoding="utf-8")
        if term in text:
            for lineno, line in enumerate(text.splitlines(), start=1):
                if term in line:
                    offenders.append(f"{fp.name}:{lineno}: {line.strip()[:80]}")
    assert not offenders, (
        f"项目专属关键词 {term!r} 出现在全局 prompt 文档，应迁回项目 world_rules:\n"
        + "\n".join(offenders)
    )


def test_writer_v1_has_generic_rules_18_19() -> None:
    """writer-v1.md 必须保留泛化后的规则 18（语感基线）+ 规则 19（称谓与名讳禁忌），
    且必须是「跨项目通用版」，不得残留日式神话时代具体词。
    """
    writer = PROMPTS_DIR / "writer-v1.md"
    if not writer.exists():
        pytest.skip("writer-v1.md not found")
    text = writer.read_text(encoding="utf-8")

    # 泛化后必须保留的关键短语
    for marker in EXPECTED_GENERIC_MARKERS:
        assert marker in text, f"writer-v1.md missing generic marker {marker!r}"

    # 残留拦截：日式神话时代是火影专属词，不得再出现在 writer 全局 prompt。
    assert "日式神话时代" not in text, (
        "writer-v1.md 仍残留「日式神话时代」——应迁回项目 world_rules"
    )
    # 主角本名 / 穿越锚点也不能留
    assert "陈瑞" not in text, "writer-v1.md 仍残留「陈瑞」"


def test_director_and_scene_planner_have_generic_original_lock() -> None:
    """director-v1.md 与 scene_planner-v1.md 必须保留泛化后的「原著要素锁」标题，
    但不得再绑定具体角色（辉夜/桃式/浦式/金式）。
    """
    for fname in ("director-v1.md", "scene_planner-v1.md"):
        fp = PROMPTS_DIR / fname
        if not fp.exists():
            pytest.skip(f"{fname} not found")
        text = fp.read_text(encoding="utf-8")
        assert "原著要素锁" in text, f"{fname} missing '原著要素锁' heading"
        for term in ("辉夜", "桃式", "浦式", "金式", "大筒木"):
            assert term not in text, f"{fname} 仍残留 {term!r}"