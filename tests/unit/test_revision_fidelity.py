"""revision_fidelity 单测：preserved_ratio 纯函数 + 尾块剥离 + 常量形状。

对应 2026-09-21「改稿审计断链」检修批次：revise 保真闸门的测量单点。
"""

from __future__ import annotations

import time

from packages.core.agent_runtime.revision_fidelity import (
    REVISE_MIN_PRESERVED_RATIO,
    preserved_ratio,
    strip_revision_checklist_tail,
)


def test_identical_text_returns_one():
    assert preserved_ratio("春水碧于天，画船听雨眠。" * 20, "春水碧于天，画船听雨眠。" * 20) == 1.0


def test_disjoint_text_returns_zero():
    # 无任何公共字符（含标点）⇒ quick_ratio 上界归零 ⇒ 精确短路 0.0
    assert preserved_ratio("甲乙丙丁戊己庚辛" * 30, "子丑寅卯辰巳午未" * 30) == 0.0


def test_nearly_disjoint_text_is_below_threshold():
    # 仅句号公共 ⇒ 极低比值（静默整段重写的形状）
    ratio = preserved_ratio("春风又绿江南岸。" * 40, "月光如水流泻青石板上。" * 40)
    assert 0.0 <= ratio < REVISE_MIN_PRESERVED_RATIO


def test_empty_upstream_returns_one():
    # 无从比较不拦：upstream 为空（fresh_write / 首写形态）恒放行
    assert preserved_ratio("", "任意正文") == 1.0
    assert preserved_ratio("", "") == 1.0


def test_empty_revised_with_nonempty_upstream_returns_zero():
    assert preserved_ratio("上游稿正文", "") == 0.0


def test_non_string_inputs_defensively_pass():
    assert preserved_ratio(None, "x") == 1.0  # type: ignore[arg-type]
    assert preserved_ratio("x", None) == 1.0  # type: ignore[arg-type]
    assert preserved_ratio(None, None) == 1.0  # type: ignore[arg-type]


def test_cjk_long_text_completes_under_one_second():
    """CJK 长文（~2×8000 字）性能可用：<1s（契约层每次 writer 调用至多跑一两次）。"""
    base = (
        "戌时的更鼓从街尾传过来，玉惜轩的窗半掩着，竹影斜斜地落在青石地砖上。"
        "苏婉清坐在窗下，手里那只茶盏已温了许久，她却没喝。林渊立在博古架前，"
        "背对着她，似乎在翻检什么。烛火跳了一下，把两个人的影子投在墙上。"
    )
    upstream = base * 100  # ~3200 字 × 2.5 ≈ 8000 字
    # 模拟定向改稿：中段替换 ~10%，其余保留
    revised = upstream[:4000] + "她忽然抬眼，看见林渊的袖口沾了一点朱砂。" + upstream[4200:]
    start = time.monotonic()
    ratio = preserved_ratio(upstream, revised)
    elapsed = time.monotonic() - start
    assert ratio > REVISE_MIN_PRESERVED_RATIO
    assert elapsed < 1.0, f"preserved_ratio 耗时 {elapsed:.3f}s，超出 1s 预算"


def test_strip_revision_checklist_tail():
    body = "正文第一段。"
    tail = '---REVISION-CHECKLIST---\n[{"item":"a","status":"done","note":"n"}]'
    assert strip_revision_checklist_tail(body + "\n\n" + tail) == "正文第一段。\n\n"
    # 坏 JSON 同样切（契约层只管喂散文，不解析）
    assert strip_revision_checklist_tail(body + "\n\n---REVISION-CHECKLIST---\nnot json") == (
        "正文第一段。\n\n"
    )
    # 无标记原样返回
    assert strip_revision_checklist_tail(body) == body


def test_threshold_constant_shape():
    assert 0.0 < REVISE_MIN_PRESERVED_RATIO < 1.0
