"""可读性算子的人类基线看守：榜一侯府弧不得被判违规，且登记基线须与实测一致。

背景（2026-09-17 实测，复算入口 ``scripts/readability_audit.py``）：读者反馈
书 ``prj_bcb9d1930bd4``（48 章 / 122350 可见字）「可读性差」，与文风锚点书榜一
《快穿之人渣洗白手册》侯府弧（19 章 / 37806 可见字）对照后新增两条可读性算子
（``AI-LONG-PARA`` / ``AI-DIALOGUE-LOW``）。本文件是它们的**人类侧看守**：

==================== ================== ==================== ==========
指标                 榜一（人类基线）    本仓生成侧            阈值
==================== ================== ==================== ==========
单段 >100 可见字      7 段（每章至多 1）  133 段（最长 212 字） >100 warning
单段 >140 可见字      0 段                16 段                 >140 error
对话占比（全弧）      16.5%               11.4%                 <12% / <8%
==================== ================== ==================== ==========

**两种判定粒度是实测逼出来的，不是随手选的**：

1. 「长段」判**逐章**——它量的是单章内的排版堆积；人类基线每章至多 1 段超 100 字，
   而阈值档位（单章 ≥2 段报警、单段 >140 直接 error）逐章判定下人类侧零命中。
   （把 19 章合并成一整篇再判会让 7 段长段凑成一堆，那样判的不是任何生产单元——
   生产侧 ``scan_ai_patterns`` 永远按章调用。）
2. 「对话占比」判**全弧**——单章方差极大（榜一第 1 章 0.0%、第 19 章 40.6%：
   人类作者写得出整章无对话的动作戏），逐章低于 12% 的章在锚点书里占 8/19。
   故本规则的**章级命中是提示而非违规判定**，弧级配比才是它看守的口径
   （榜一 16.5% vs 我们 11.4%）——噪声上限由下面第 3 条测试钉住。

正文数据在内容仓（三仓分离：正文不进软件仓），按 ``NOVELOS_CONTENT_DIR`` →
软件仓同级 ``NovelOS-Content`` 定位；内容仓不在本机时跳过（环境前提缺失，
非断言软化）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from packages.core.quality.ai_patterns import (
    DEFAULT_DIALOGUE_LOW_ERROR_RATIO,
    DEFAULT_DIALOGUE_LOW_WARN_RATIO,
    DEFAULT_LONG_PARA_ERROR_CHARS,
    DEFAULT_LONG_PARA_MIN_COUNT,
    DEFAULT_LONG_PARA_WARN_CHARS,
    count_long_paragraphs,
    dialogue_ratio,
    scan_ai_patterns,
)
from packages.core.quality.wordcount import visible_chars

# --- 2026-09-17 实测基线（改阈值前先重算这几个数：python scripts/readability_audit.py）---

HUMAN_VISIBLE_CHARS = 37806
HUMAN_PARAGRAPHS = 779
HUMAN_AVG_PARA = 48.5
HUMAN_PARAS_OVER_100 = 7  # 分布在 7 个章里，每章恰 1 段
HUMAN_PARAS_OVER_120 = 2
HUMAN_PARAS_OVER_140 = 0
HUMAN_MAX_PARA_CHARS = 135
HUMAN_DIALOGUE_RATIO = 0.165  # 6229 / 37806

# 生成侧（同一脚本复算）：诊断依据，不参与断言，只在失败消息里作对照。
GENERATED_VISIBLE_CHARS = 122350
GENERATED_PARAS_OVER_100 = 133
GENERATED_PARAS_OVER_140 = 16
GENERATED_DIALOGUE_RATIO = 0.114

# 对话规则在锚点书上的**逐章噪声上限**（实测 8/19 章低于 12%、5 章低于 8%）。
# 这是把已知假阳性显式登记为回归红线：变差即要重新评估该规则的章级可行性。
HUMAN_DIALOGUE_LOW_CHAPTERS_CEILING = 10
HUMAN_DIALOGUE_LOW_ERROR_CHAPTERS_CEILING = 6

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REFERENCE_SUBPATH = Path("reference-books/榜一-快穿之人渣洗白手册/侯府凤凰男")


def _reference_dir() -> Path | None:
    """定位榜一侯府弧正文目录；内容仓不在本机时返回 None。"""
    candidates: list[Path] = []
    env_dir = os.environ.get("NOVELOS_CONTENT_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(_REPO_ROOT.parent / "NovelOS-Content")
    for root in candidates:
        target = root / _REFERENCE_SUBPATH
        if target.is_dir():
            return target
    return None


def _arc_chapters() -> list[tuple[str, str]]:
    """榜一侯府弧全部 19 章 ``(章标签, 正文)``，按章号升序。"""
    ref_dir = _reference_dir()
    if ref_dir is None:  # pragma: no cover - 环境前提缺失
        pytest.skip(f"内容仓参考书目录不存在：{_REFERENCE_SUBPATH}")
    numbered: list[tuple[int, Path]] = []
    for path in ref_dir.glob("*.txt"):
        m = re.search(r"第(\d+)章", path.name)
        if m:
            numbered.append((int(m.group(1)), path))
    numbered.sort(key=lambda item: item[0])
    assert len(numbered) >= 19, f"榜一侯府弧应有 19 章正文，实际 {len(numbered)}"
    return [
        (f"第{no}章", path.read_text(encoding="utf-8", errors="replace"))
        for no, path in numbered
    ]


def _rule_ids(prose: str) -> set[str]:
    return {h["rule_id"] for h in scan_ai_patterns(prose)}


# ---------------------------------------------------------------------------
# 纪律 1：人类基线不得被判违规（逐章 —— 长段）
# ---------------------------------------------------------------------------


def test_human_baseline_long_para_clean_per_chapter():
    """榜一 19 章逐章跑：AI-LONG-PARA 零命中，且任何一章都没有 >140 字段落。

    判据与生产同形（review 按章跑 ``scan_ai_patterns``）。这条是长段阈值的
    人类侧看守：阈值若定到 100/140 之下（例如「单段超 80 就报」），本测转红。
    """
    chapters = _arc_chapters()
    worst = 0
    worst_label = ""
    for label, prose in chapters:
        assert visible_chars(prose) >= 600, "可读性规则有 600 字最小判定长度"
        long_paras = count_long_paragraphs(prose)
        if len(long_paras) > worst:
            worst, worst_label = len(long_paras), label
        over_error = [
            p for p in long_paras if visible_chars(p) > DEFAULT_LONG_PARA_ERROR_CHARS
        ]
        assert not over_error, (
            f"{label} 人类正文出现 >{DEFAULT_LONG_PARA_ERROR_CHARS} 字段落"
            f"（{max(visible_chars(p) for p in over_error)} 字）——"
            f"error 档阈值已低于人类基线最大段长 {HUMAN_MAX_PARA_CHARS}"
        )
        assert "AI-LONG-PARA" not in _rule_ids(prose), (
            f"{label} 人类正文被长段规则误判：该章 {len(long_paras)} 段超"
            f" {DEFAULT_LONG_PARA_WARN_CHARS} 字（堆积门 ≥{DEFAULT_LONG_PARA_MIN_COUNT}）"
        )
    assert worst <= 1, (
        f"榜一单章长段数实测 {worst}（{worst_label}）高于登记「每章至多 1 段」——"
        f"登记基线 {HUMAN_PARAS_OVER_100} 段需重算（改阈值前先重算）"
    )


# ---------------------------------------------------------------------------
# 纪律 2：人类基线不得被判违规（全弧 —— 对话占比）
# ---------------------------------------------------------------------------


def test_human_baseline_dialogue_clean_at_arc_level():
    """榜一全弧合并后跑：AI-DIALOGUE-LOW 零命中，且对话占比 ≈ 登记基线 16.5%。

    弧级是这条规则的有效判定粒度：锚点书 16.5% 远高于 12% 阈值，而我们
    11.4% 低于它——即阈值落在人机两侧之间，不是死规则。
    """
    arc = "\n".join(prose for _, prose in _arc_chapters())
    ratio = dialogue_ratio(arc)
    assert dialogue_ratio(arc) >= DEFAULT_DIALOGUE_LOW_WARN_RATIO, (
        f"榜一全弧对话占比 {ratio:.3f} 低于阈值 {DEFAULT_DIALOGUE_LOW_WARN_RATIO}"
        f"（生成侧 {GENERATED_DIALOGUE_RATIO}）——阈值已高于人类基线量级"
    )
    assert "AI-DIALOGUE-LOW" not in _rule_ids(arc), (
        f"榜一全弧被对话占比规则误判：{ratio:.3f}（阈值"
        f" {DEFAULT_DIALOGUE_LOW_WARN_RATIO}/{DEFAULT_DIALOGUE_LOW_ERROR_RATIO}）"
    )
    assert abs(ratio - HUMAN_DIALOGUE_RATIO) < 0.01, (
        f"榜一全弧对话占比实测 {ratio:.3f} 与登记基线 {HUMAN_DIALOGUE_RATIO} 不符——"
        f"基线数字需重算（复算入口 scripts/readability_audit.py）"
    )
    assert DEFAULT_DIALOGUE_LOW_ERROR_RATIO < HUMAN_DIALOGUE_RATIO, "error 档必须在基线之下"


def test_human_dialogue_chapter_noise_stays_within_registered_ceiling():
    """逐章噪声登记：榜一有 8/19 章低于 12%（人类写得出整章无对话的动作戏）。

    这条不是「允许误报」，而是把已知的**粒度落差**钉成数字：若哪天变成 15/19，
    说明规则已退化为噪声源（AGENTS.md 三种病之「刷屏噪声」），必须重新设计。
    """
    chapters = _arc_chapters()
    warn = 0
    error = 0
    for _, prose in chapters:
        hits = {
            h["rule_id"]: h
            for h in scan_ai_patterns(prose)
            if h["rule_id"] == "AI-DIALOGUE-LOW"
        }
        if hits:
            warn += 1
            error += hits["AI-DIALOGUE-LOW"]["severity"] == "error"
    assert warn <= HUMAN_DIALOGUE_LOW_CHAPTERS_CEILING, (
        f"榜一逐章命中 {warn}/{len(chapters)} 已超登记上限"
        f" {HUMAN_DIALOGUE_LOW_CHAPTERS_CEILING}——规则章级噪声已恶化"
    )
    assert error <= HUMAN_DIALOGUE_LOW_ERROR_CHAPTERS_CEILING, (
        f"榜一逐章 error 档 {error}/{len(chapters)} 已超登记上限"
        f" {HUMAN_DIALOGUE_LOW_ERROR_CHAPTERS_CEILING}"
    )


# ---------------------------------------------------------------------------
# 纪律 3：登记基线必须与实测一致（防「基线过期」这类静默回退）
# ---------------------------------------------------------------------------


def test_registered_baseline_numbers_match_measurement():
    """上表的每个数字都要能被复算出来——基线过期比阈值乱定更难发现。"""
    chapters = _arc_chapters()
    arc = "\n".join(prose for _, prose in chapters)
    lengths = [visible_chars(p) for p in re.split(r"\n+", arc) if p.strip()]
    assert visible_chars(arc) == HUMAN_VISIBLE_CHARS
    assert len(lengths) == HUMAN_PARAGRAPHS
    assert abs(visible_chars(arc) / len(lengths) - HUMAN_AVG_PARA) < 0.1
    assert sum(1 for x in lengths if x > 100) == HUMAN_PARAS_OVER_100
    assert sum(1 for x in lengths if x > 120) == HUMAN_PARAS_OVER_120
    assert sum(1 for x in lengths if x > 140) == HUMAN_PARAS_OVER_140
    assert max(lengths) == HUMAN_MAX_PARA_CHARS
    # 生成侧数字只在失败消息里对照，断言的是「人侧严格优于生成侧」这一方向
    assert HUMAN_PARAS_OVER_100 < GENERATED_PARAS_OVER_100
    assert HUMAN_DIALOGUE_RATIO > GENERATED_DIALOGUE_RATIO
