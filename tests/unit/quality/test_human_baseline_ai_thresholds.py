"""阈值不得再回到「死规则」：以文风锚点书（榜一）的人类基线为看守。

背景（2026-09-16 实测，复算入口 ``scripts/ai_tone_calibrate.py``）：

==================== ===================== ==================== ==========
算子                 榜一侯府弧（人类基线） 新书 01 全弧（生成侧） 旧阈值
==================== ===================== ==================== ==========
``AI-PUNCT-ABUSE``   0.19 / 千字            1.73 / 千字           3.5
``AI-SHORT-PARA``    0.00 / 千字            2.62 / 千字           5.5
==================== ===================== ==================== ==========

旧阈值是按**我们自己旧语料的 p85/p90** 定的，落在人机两侧实测分布**之外**——
连生成侧自己的均值都够不到，即 AGENTS.md 点名的「阈值落在实测分布之外 = 死规则」
形状。2026-09-16 按人类基线收紧至 1.0 / 1.0。两条纪律写进本文件防回退：

1. **人类基线不得被判违规**：榜一侯府弧前 3 章正文跑默认阈值，两条规则零命中；
2. **阈值必须落在实测分布之内**：高于「人类基线 × 约 5 倍」即等于放弃判定，
   故给两个常量钉死上限——改回 3.5 / 5.5 本文件必红。

正文数据在内容仓（三仓分离：正文不进软件仓），按
``NOVELOS_CONTENT_DIR`` → ``DEFAULT_CONTENT_DIR`` → 软件仓同级目录 的顺序定位；
内容仓不在本机时跳过（环境前提缺失，非断言软化）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from packages.core.content_sync.paths import DEFAULT_CONTENT_DIR
from packages.core.quality.ai_patterns import (
    DEFAULT_DASH_THRESHOLD_PER_1K,
    DEFAULT_SHORT_PARA_RATE_PER_1K,
    scan_ai_patterns,
)
from packages.core.quality.wordcount import visible_chars

# --- 2026-09-16 实测基线（上表来源，改阈值前先重算这几个数） -------------------

HUMAN_DASH_PER_1K = 0.19  # 榜一侯府弧 19 章 / 37806 可见字
HUMAN_SHORT_PARA_PER_1K = 0.00  # 榜一侯府弧全弧零出现
GENERATED_DASH_PER_1K = 1.73  # 新书 01 全弧 63625 字
GENERATED_SHORT_PARA_PER_1K = 2.62

# 人类基线的宽容上限：0.19 × 5 ≈ 0.95 向上取整到 1.0。
# 高于它 = 阈值脱离人类基线量级；同时必须低于生成侧实测均值，否则永不触发。
_MAX_TOLERANCE_PER_1K = 1.0

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REFERENCE_SUBPATH = Path("reference-books/榜一-快穿之人渣洗白手册/侯府凤凰男")


def _reference_dir() -> Path | None:
    """定位榜一侯府弧正文目录；内容仓不在本机时返回 None。"""
    candidates: list[Path] = []
    env_dir = os.environ.get("NOVELOS_CONTENT_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(DEFAULT_CONTENT_DIR)
    candidates.append(_REPO_ROOT.parent / "NovelOS-Content")
    for root in candidates:
        target = root / _REFERENCE_SUBPATH
        if target.is_dir():
            return target
    return None


def _first_chapters(n: int = 3) -> list[tuple[str, str]]:
    """按章号取前 n 章 ``(章号标签, 正文)``。"""
    ref_dir = _reference_dir()
    if ref_dir is None:  # pragma: no cover - 环境前提缺失
        pytest.skip(f"内容仓参考书目录不存在：{_REFERENCE_SUBPATH}")
    numbered: list[tuple[int, Path]] = []
    for path in ref_dir.glob("*.txt"):
        m = re.search(r"第(\d+)章", path.name)
        if m:
            numbered.append((int(m.group(1)), path))
    numbered.sort(key=lambda item: item[0])
    assert len(numbered) >= n, f"榜一侯府弧应有 ≥{n} 章正文，实际 {len(numbered)}"
    return [(f"第{no}章", path.read_text(encoding="utf-8")) for no, path in numbered[:n]]


def _measured_rates(prose: str) -> tuple[float, float]:
    """取两条密度规则的实测值（每千字）。

    走公开入口的「阈值 0 探针」，不复制算子正则（复制即两套口径）：
    阈值 0 时凡有命中必触发，返回的 ``rate`` 就是算子自己算出的密度。
    """
    probes = scan_ai_patterns(
        prose, dash_threshold_per_1k=0.0, short_para_threshold_per_1k=0.0
    )
    dash = next((h["rate"] for h in probes if h["rule_id"] == "AI-PUNCT-ABUSE"), 0.0)
    short_para = next((h["rate"] for h in probes if h["rule_id"] == "AI-SHORT-PARA"), 0.0)
    return dash, short_para


# ---------------------------------------------------------------------------
# 纪律 1：人类基线不得被判违规
# ---------------------------------------------------------------------------


def test_human_baseline_chapters_do_not_trigger_density_rules():
    """榜一侯府弧前 3 章跑默认阈值：破折号 / 短句独立成段两条都必须零命中。

    这是「人类写的正文不该被判 AI 腔」的最小看守——阈值若把人类基线也判违规，
    它衡量的就不是 AI 味（判别：定阈值前先看人侧是否干净）。逐章判定，
    与生产口径一致（review 按章跑算子）。
    """
    chapters = _first_chapters(3)
    worst: dict[str, float] = {"dash": 0.0, "short_para": 0.0}

    for label, prose in chapters:
        wc = visible_chars(prose)
        assert wc >= 200, "密度类规则有 200 字最小判定长度，样本必须够长"
        dash_rate, short_para_rate = _measured_rates(prose)
        worst["dash"] = max(worst["dash"], dash_rate)
        worst["short_para"] = max(worst["short_para"], short_para_rate)

        rule_ids = {h["rule_id"] for h in scan_ai_patterns(prose)}
        assert "AI-PUNCT-ABUSE" not in rule_ids, (
            f"{label} 人类正文被破折号规则误判：{dash_rate:.2f}/千字"
            f"（阈值 {DEFAULT_DASH_THRESHOLD_PER_1K}），可见字 {wc}"
        )
        assert "AI-SHORT-PARA" not in rule_ids, (
            f"{label} 人类正文被短段规则误判：{short_para_rate:.2f}/千字"
            f"（阈值 {DEFAULT_SHORT_PARA_RATE_PER_1K}），可见字 {wc}"
        )
        assert dash_rate <= DEFAULT_DASH_THRESHOLD_PER_1K, (
            f"{label} 破折号 {dash_rate:.2f}/千字 已越过阈值"
            f" {DEFAULT_DASH_THRESHOLD_PER_1K}——阈值已脱离人类基线"
        )
        assert short_para_rate <= DEFAULT_SHORT_PARA_RATE_PER_1K, (
            f"{label} 短段 {short_para_rate:.2f}/千字 已越过阈值"
            f" {DEFAULT_SHORT_PARA_RATE_PER_1K}——阈值已脱离人类基线"
        )

    assert worst["dash"] <= HUMAN_DASH_PER_1K * 5, (
        f"榜一破折号实测 {worst['dash']:.2f}/千字 与登记基线 {HUMAN_DASH_PER_1K} 量级不符——"
        "基线数字需重算（改阈值前先重算上表三个数）"
    )
    assert worst["short_para"] <= HUMAN_SHORT_PARA_PER_1K, (
        f"榜一短段实测 {worst['short_para']:.2f}/千字 高于登记基线"
        f" {HUMAN_SHORT_PARA_PER_1K}——基线数字需重算"
    )


# ---------------------------------------------------------------------------
# 纪律 2：阈值必须落在实测分布之内（防「改回旧值」）
# ---------------------------------------------------------------------------


def test_dash_threshold_within_measured_distribution():
    """破折号阈值必须贴人类基线量级，且低于生成侧实测均值。

    旧值 3.5 两条都不满足（0.19 的 18 倍；> 生成侧 1.73）——即死规则。
    """
    assert DEFAULT_DASH_THRESHOLD_PER_1K <= _MAX_TOLERANCE_PER_1K, (
        f"破折号阈值 {DEFAULT_DASH_THRESHOLD_PER_1K} 超出人类基线"
        f" {HUMAN_DASH_PER_1K}/千字 的宽容上限 {_MAX_TOLERANCE_PER_1K}"
    )
    assert DEFAULT_DASH_THRESHOLD_PER_1K < GENERATED_DASH_PER_1K, (
        f"破折号阈值 {DEFAULT_DASH_THRESHOLD_PER_1K} 高于生成侧实测均值"
        f" {GENERATED_DASH_PER_1K}/千字——等于永不触发（死规则）"
    )


def test_short_para_threshold_within_measured_distribution():
    """短段阈值同理：榜一为 0.00，生成侧 2.62——阈值必须落在两者之间。"""
    assert DEFAULT_SHORT_PARA_RATE_PER_1K <= _MAX_TOLERANCE_PER_1K, (
        f"短段阈值 {DEFAULT_SHORT_PARA_RATE_PER_1K} 超出人类基线"
        f" {HUMAN_SHORT_PARA_PER_1K}/千字 的宽容上限 {_MAX_TOLERANCE_PER_1K}"
    )
    assert DEFAULT_SHORT_PARA_RATE_PER_1K < GENERATED_SHORT_PARA_PER_1K, (
        f"短段阈值 {DEFAULT_SHORT_PARA_RATE_PER_1K} 高于生成侧实测均值"
        f" {GENERATED_SHORT_PARA_PER_1K}/千字——等于永不触发（死规则）"
    )
