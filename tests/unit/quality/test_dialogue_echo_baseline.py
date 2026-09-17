"""接词回声的人类基线看守 + 对话规则降级在**真实人类语料**上的复核。

语料：文风锚点书榜一《快穿之人渣洗白手册》侯府弧 19 章 / 37806 可见字（内容仓，
三仓分离：正文不进软件仓）；不在本机时 ``pytest.skip``。

**N 是怎么定出来的（不是拍脑袋）**：跑 ``count_dialogue_echoes`` 逐章量出来的——
实测全弧命中 **1 对 / 1 章**，故登记上限就是 1（本文件用 ``<=`` 断言；生成侧同口径
实测 26 对 / 20 章，见 ``packages/core/quality/ai_patterns.py`` 常量注释）。那 1 对
是「…我们明天一早就在城门口等吧」→「好啊，明儿个我们就在城门口等他们。」：信息
递增但用词撞车，属**合法写法**——这正是本规则只判字面、severity 恒 warning 的原因，
也是「对白是否有后果」无法正则化的实证。

另外两条同语料上的复核（撤回批次的一部分）：

1. ``AI-DIALOGUE-LOW`` 在该语料上逐章命中**全部是 warning**（首章 0.0% 对话，
   人类作者写得出整章无对话的动作戏——原 error 档会判人类违规）；
2. ``AI-LONG-PARA`` 在该语料上逐章零命中（该档阈值未动，防止本批次误伤）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from packages.core.quality.ai_patterns import (
    count_dialogue_echoes,
    dialogue_echo_pairs,
    scan_ai_patterns,
)
from packages.core.quality.wordcount import visible_chars

# --- 2026-09-17 实测基线（改阈值前先重算：python scripts/readability_audit.py）---
HUMAN_ECHO_PAIRS = 1  # 全弧命中对数（19 章）
HUMAN_ECHO_CHAPTERS = 1  # 其中有命中的章数
HUMAN_ECHO_CANDIDATE_PAIRS = 61  # 相邻对白对总数（与阈值无关的语料锚点）
HUMAN_ECHO_VISIBLE_CHARS = 37806  # 与 test_readability_baseline 同源

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


def test_human_echo_pairs_stay_within_registered_ceiling():
    """人侧回声对数登记为 1：命中章 ≤1、总对数 ≤1（实测值，不是宽容值）。

    人侧一旦超过它，说明阈值已把「正常对白里的用词撞车」当成病（AGENTS.md 三种病
    之「全是误报」），必须重新设计。同时核对语料锚点（可见字 / 相邻对白对总数），
    防止基线在语料换代后静默过期。
    """
    chapters = _arc_chapters()
    total_pairs = 0
    hit_chapters = 0
    samples: list[str] = []
    for label, prose in chapters:
        hits = count_dialogue_echoes(prose)
        total_pairs += len(hits)
        hit_chapters += bool(hits)
        samples += [f"[{label}] {h.sample}" for h in hits]

    assert total_pairs <= HUMAN_ECHO_PAIRS, (
        f"榜一全弧回声对数实测 {total_pairs}，已超登记上限 {HUMAN_ECHO_PAIRS}：{samples}"
    )
    assert hit_chapters <= HUMAN_ECHO_CHAPTERS, (
        f"榜一命中章数实测 {hit_chapters}，已超登记上限 {HUMAN_ECHO_CHAPTERS}：{samples}"
    )

    arc = "\n".join(prose for _, prose in chapters)
    assert visible_chars(arc) == HUMAN_ECHO_VISIBLE_CHARS, (
        "语料已换代：可见字数与登记值不符，基线数字需重算"
    )
    candidates = sum(len(dialogue_echo_pairs(prose)) for _, prose in chapters)
    assert candidates == HUMAN_ECHO_CANDIDATE_PAIRS, (
        f"相邻对白对总数实测 {candidates} ≠ 登记 {HUMAN_ECHO_CANDIDATE_PAIRS}"
        "——语料或对白段提取口径已变，基线需重算"
    )


def test_human_dialogue_low_hits_are_never_error():
    """该语料上 ``AI-DIALOGUE-LOW`` 逐章命中**只可能是 warning**（error 档已撤回）。

    首章对话占比 0.0%——人类正文。若该规则又有 error 档，本测必红。
    """
    chapters = _arc_chapters()
    flagged = 0
    for label, prose in chapters:
        hits = [
            h
            for h in scan_ai_patterns(prose)
            if h["rule_id"] == "AI-DIALOGUE-LOW"
        ]
        flagged += bool(hits)
        for hit in hits:
            assert hit["severity"] == "warning", (
                f"{label} 人类正文被对话占比规则判 {hit['severity']}——"
                "error 档已于 2026-09-17 撤回，该规则只作提示"
            )
    assert flagged >= 5, (
        f"榜一逐章低对话命中只剩 {flagged} 章（登记 8/19）——"
        "样本口径或语料变了，本的看守对象已失效"
    )


def test_human_long_para_still_clean():
    """本批次未动长段档位：该语料上 ``AI-LONG-PARA`` 逐章仍零命中。"""
    for label, prose in _arc_chapters():
        ids = {h["rule_id"] for h in scan_ai_patterns(prose)}
        assert "AI-LONG-PARA" not in ids, f"{label} 人类正文被长段规则误判"
