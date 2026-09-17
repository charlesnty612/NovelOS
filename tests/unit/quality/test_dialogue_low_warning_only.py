"""``AI-DIALOGUE-LOW`` **只有 warning 一档**：error 档撤回后的行为断言（2026-09-17）。

为什么撤（用户实测反馈 + 人类基线双证据）：

- 上一批给该规则加了 <8% → error 档，并在题材包与生成驱动写「对话占比 ≥20%」，
  结果**逼模型凑指标**：编造对白、破人设、整章接龙式灌水（实证见
  ``packages/core/quality/ai_patterns.py`` 常量注释）；用户原话——
  「你现在的改法书一点就不好读了，全是重复性的对话灌水，上一句说了啥，
  下一句接着重复一遍」。
- 章级口径本身也会误报人类：榜一侯府弧 19 章里 8 章低于 12%、**首章 0.0%**
  （人类作者写得出整章无对话的动作戏）。

结论：对话占比是**可优化指标**，不是质量判据——只作提示，绝不进 ``errors`` 桶。
症状探针（接词回声）见 ``test_dialogue_echo.py``；人类侧看守见
``test_dialogue_echo_baseline.py``。
"""

from __future__ import annotations

from packages.core.quality import ai_patterns as module
from packages.core.quality.ai_patterns import (
    AI_PATTERN_RULES,
    DEFAULT_DIALOGUE_LOW_WARN_RATIO,
    count_dialogue_visible_chars,
    scan_ai_patterns,
)
from packages.core.quality.wordcount import visible_chars

_NARRATION = (
    "他站在柜台后头，把价签翻了个面。雨点打在油纸伞上，声音又密又匀。",
    "巷口的灯笼晃了两下。",
)


def _prose(dialogue_chars: int, total_chars: int = 1000) -> str:
    """构造成对“…”对话**精确**可控的章级样本（无对话时 dialogue_chars=0）。"""
    body: list[str] = []
    if dialogue_chars:
        body.append("“" + "好" * dialogue_chars + "”")
    while visible_chars("\n\n".join(body)) < total_chars:
        body.append(_NARRATION[len(body) % len(_NARRATION)])
    return "\n\n".join(body)


def _dialogue_hits(prose: str) -> list[dict]:
    return [h for h in scan_ai_patterns(prose) if h["rule_id"] == "AI-DIALOGUE-LOW"]


def test_zero_dialogue_chapter_is_warning_not_error():
    """0% 对话的整章：命中存在但是 **warning**（原判 error 的极端情形）。"""
    prose = _prose(0)
    assert count_dialogue_visible_chars(prose) == 0, "样本必须真的零对话"
    hits = _dialogue_hits(prose)
    assert len(hits) == 1
    assert hits[0]["severity"] == "warning", "error 档已撤回（2026-09-17）"
    assert hits[0]["dialogue_ratio"] == 0.0
    assert "error_ratio" not in hits[0]


def test_no_ratio_can_produce_error_severity():
    """横跨 0% → 50% 的样本扫描：该规则**任何情况**都不返回 error。"""
    for dialogue_chars in (0, 30, 79, 119, 300, 500):
        hits = _dialogue_hits(_prose(dialogue_chars))
        assert all(h["severity"] == "warning" for h in hits), dialogue_chars
        assert not [h for h in hits if h["severity"] == "error"], dialogue_chars


def test_error_tier_constant_is_gone():
    """撤回是**删档**不是改默认值：旧常量不得残留（防止有人把第二档加回来）。"""
    assert not hasattr(module, "DEFAULT_DIALOGUE_LOW_ERROR_RATIO")
    assert DEFAULT_DIALOGUE_LOW_WARN_RATIO == 0.12, "12% 阈值保留"


def test_rule_metadata_declares_warning_only_and_records_retraction():
    """规则元数据：档位恒 warning，说明里留「2026-09-17 撤回」痕迹。"""
    row = next(r for r in AI_PATTERN_RULES if r.rule_id == "AI-DIALOGUE-LOW")
    assert row.severity == "warning"
    assert "撤回" in row.description and "2026-09-17" in row.description


def test_scan_signature_has_no_error_ratio_parameter():
    """``scan_ai_patterns`` 不再接受 error 档参数（调用方无从重新打开闸门）。"""
    import inspect

    params = inspect.signature(scan_ai_patterns).parameters
    assert "dialogue_low_error_ratio" not in params
    assert "dialogue_low_warn_ratio" in params
