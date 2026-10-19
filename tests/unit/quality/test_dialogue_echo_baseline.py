"""接词回声的人类基线看守 + 对话规则降级在**真实人类语料**上的复核。

语料：文风锚点书榜一《快穿之人渣洗白手册》侯府弧 19 章 / 37806 可见字（内容仓，
三仓分离：正文不进软件仓）；语料定位与「不可得怎么办」收在
:mod:`unit.quality._human_baseline_gate`——不在本机时**不无声跳过**：写状态记录 +
发一条带 ``HUMAN-BASELINE-CORPUS-UNAVAILABLE`` 标记的警告，并可用
``NOVELOS_REQUIRE_HUMAN_BASELINE=1`` 把它变成**判红**（2026-10-19 改批：此前是裸
``pytest.skip`` ⇒ CI 里恒 skip ⇒ 人类基线看守在 CI 中从未生效过，而 CI 报绿）。

**N 是怎么定出来的（不是拍脑袋）**：跑 ``count_dialogue_echoes`` 逐章量出来的——
实测全弧命中 **1 对 / 1 章**，故登记上限就是 1（本文件用 ``<=`` 断言）。那 1 对
是「…我们明天一早就在城门口等吧」→「好啊，明儿个我们就在城门口等他们。」：信息
递增但用词撞车，属**合法写法**——这正是本规则只判字面、severity 恒 warning 的原因，
也是「对白是否有后果」无法正则化的实证。

**2026-09-27 复核：人侧仍是 1 对 / 1 章**（重叠系数可比性守卫生效前后都是 1 对——
那唯一一对的 min = 15 ≥ 3 且后句 15 < 2×40，两条守卫都碰不到它）。生成侧同口径由
**34 对 / 24 章**（守卫生效前）降为 **23 对 / 18 章**（生效后），见
``packages/core/quality/ai_patterns.py`` 常量注释。

⚠️ **1/19 章这个数字本身是薄样本**：人类侧全弧只有 61 对相邻对白、命中 1 对，
不足以支撑任何回声阈值结论；本文件守的是「人侧不恶化」，不是「人侧零容忍」。

另外两条同语料上的复核（撤回批次的一部分）：

1. ``AI-DIALOGUE-LOW`` 在该语料上逐章命中**全部是 warning**（首章 0.0% 对话，
   人类作者写得出整章无对话的动作戏——原 error 档会判人类违规）；
2. ``AI-LONG-PARA`` 在该语料上逐章零命中（该档阈值未动，防止本批次误伤）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from packages.core.quality.ai_patterns import (
    count_dialogue_echoes,
    dialogue_echo_pairs,
    scan_ai_patterns,
)
from packages.core.quality.wordcount import visible_chars

from . import _human_baseline_gate as gate

#: 标记「本文件是人类基线看守」（故障注入插件据此定位目标用例）。
HUMAN_BASELINE_GUARD = True

# --- 实测基线（2026-09-27 重算；改阈值前先重跑 count_dialogue_echoes） -------
HUMAN_ECHO_PAIRS = 1  # 全弧命中对数（19 章）；守卫生效前后均为 1 对
HUMAN_ECHO_CHAPTERS = 1  # 其中有命中的章数
HUMAN_ECHO_CANDIDATE_PAIRS = 61  # 相邻对白对总数（与阈值无关的语料锚点）
HUMAN_ECHO_VISIBLE_CHARS = 37806  # 与 test_readability_baseline 同源


def _arc_chapters() -> list[tuple[str, str]]:
    """榜一侯府弧全部 19 章 ``(章标签, 正文)``，按章号升序。

    语料定位与「不可得怎么办」由 :mod:`unit.quality._human_baseline_gate` 统一处理
    （本文件**不得**再自带裸 ``pytest.skip``——那会绕开状态记录与警告）。
    """
    ref_dir = gate.require_reference_corpus(__name__)
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


def test_human_echo_hit_has_interpretable_overlap():
    """人侧那唯一一对的 ``overlap`` 读数是**可解释**的（2026-09-27 新增）。

    可比性守卫（后句 ≥2×前句、任一侧 <3 字）若哪天被放宽，人侧本可能开始吸收
    退化读数；本条先证明「当前这一对不是靠退化读数命中的」，即：它的命中来自
    **复述型**（recall 0.73），重叠系数也落在可解释区间内，两条守卫都碰不到它。
    """
    hits = [(label, p) for label, prose in _arc_chapters() for p in count_dialogue_echoes(prose)]
    assert len(hits) == HUMAN_ECHO_PAIRS, f"登记 1 对，实测 {len(hits)}"
    label, pair = hits[0]
    assert pair.overlap_comparable is True, (
        f"{label} 人侧命中靠退化读数（min={min(len(pair.front_chars), len(pair.back_chars))}，"
        f"后句/前句={len(pair.back_chars) / len(pair.front_chars):.2f}×）——"
        "守卫口径已变，基线数字需重算"
    )
    assert pair.recall_ratio == pytest.approx(11 / 15)
    assert pair.recall_ratio >= 0.70, "该对由复述型命中（信息递增但用词撞车）"


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
