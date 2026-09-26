# -*- coding: utf-8 -*-
"""AI 味算子本地校准 + 精确率抽样核查。

为什么需要这个脚本（对齐来源研究的操作规程）：
  ``lieflat-less-ai-tone`` 公开的六次测量失误，全部是同一形状——**算子实际覆盖
  范围宽于规则名称给的外延，而频率数字本身不提示这个落差**。它给出的对策是：
  任何算子在采信频率结果前，**先抽样检视 20 条命中实例**。
  本仓的测试只钉「能不能命中」（召回侧），从来没有量过「命中的是不是真货」
  （精确率侧）——本脚本补的就是这一侧。

两块能力：
  1. **校准**：按每千可见字算各算子频率，输出 R = 生成侧 ÷ 人类侧（口径同来源研究）。
     人类侧样本不足时（本仓 style sample 仅数千字），R 只作方向参考，不作阈值依据。
  2. **精确率抽样**：逐算子打印命中实例（默认 20 条），人工过一遍再决定是否采信。
     精确率差的算子应改窄或废弃，不得仅凭 R 值入册。
     其中 ``AI-BEAT-REPEAT``（同章远距小句复现）另有专项块，逐例打印**两处片段**——
     只看共享串分不出「专名复指」（误报）与「同一节拍重播」（真命中）。

用法::

    python scripts/ai_tone_calibrate.py                      # 默认：本库两本书 vs 样章
    python scripts/ai_tone_calibrate.py --samples 20
    python scripts/ai_tone_calibrate.py --human-file D:/path/human.txt --ai-dir D:/path/ai
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.quality.ai_patterns import (  # noqa: E402
    count_anthro_vehicles,
    count_contrast_pairs,
    count_short_paras,
)
from packages.core.quality.beat_repeat import (  # noqa: E402
    beat_repeats,
    count_beat_repeats,
)
from packages.core.quality.continuity_time import (  # noqa: E402
    count_time_conflicts,
    time_conflicts,
)
from packages.core.quality.wordcount import visible_chars  # noqa: E402

DEFAULT_DB = "data/novelos.db"
DEFAULT_AI_PROJECTS = ("prj_f26ec5d3f03c", "prj_c1c1eaa4fe5e")

# 对齐来源研究的 11 项里可正则化的部分；本仓其余规则在 ``packages.core.quality.ai_patterns``
# （禁用词 / 破折号 / 长段 / 对话占比 / 接词回声 / 同章远距小句复现等），不在本表内。
OPERATORS = {
    "对举结构(不是A而是B)": count_contrast_pairs,
    "短句独立成段": count_short_paras,
    "拟人化喻体(职业类)": count_anthro_vehicles,
    "同章远距小句复现": count_beat_repeats,
    "破折号": lambda t: re.findall(r"——", t),
    "省略号": lambda t: re.findall(r"…{2,}", t),
    "顿号并列(≥3项)": lambda t: re.findall(
        r"[^，。！？；、\n]{1,12}、[^，。；\n]{1,12}、[^，。；\n]{1,12}、", t
    ),
    "起首语(说白了/值得注意的是)": lambda t: re.findall(
        r"(?:说白了|值得注意的是|不得不说|客观来讲)", t
    ),
    "正文设问(？结尾)": lambda t: re.findall(r"[^。！？\n]{4,}？", t),
    "正文序数词(首先/其次/最后)": lambda t: re.findall(r"(?:首先|其次|最后)[，,]", t),
    # 连续性侧（非 AI 味）：章内时点矛盾，机制见 packages.core.quality.continuity_time。
    # 它是稀有事件算子（本仓 92 章只 2 处），频率列仅供留痕，**不要**按 R 判定。
    "章内时点矛盾(时钟↔天亮/时点回退)": count_time_conflicts,
}


def _db_chapters(db: str, projects: tuple[str, ...]) -> list[tuple[str, str]]:
    """生成侧语料：各项目每章的最新草稿。"""
    conn = sqlite3.connect(db)
    out: list[tuple[str, str]] = []
    try:
        for pid in projects:
            for num, cid in conn.execute(
                "SELECT number, chapter_id FROM chapters WHERE project_id=? ORDER BY number",
                (pid,),
            ):
                r = conn.execute(
                    "SELECT content FROM drafts WHERE chapter_id=? "
                    "ORDER BY version DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                if r:
                    out.append((f"{pid[-4:]}#ch{num}", r[0]))
    finally:
        conn.close()
    return out


def _db_all_chapters(db: str) -> list[tuple[str, str]]:
    """生成侧语料：库内**全部**项目 × 每章最新草稿（``--all-projects``）。

    与 :func:`_db_chapters` 的差别只有取数范围：本仓时点矛盾是**稀有事件**
    （92 章里 2 处），只看默认两本样书会全数漏掉，复算必须用全库。
    """
    conn = sqlite3.connect(db)
    out: list[tuple[str, str]] = []
    try:
        rows = conn.execute(
            """
            SELECT ch.project_id, ch.number, d.content
            FROM chapters ch JOIN drafts d ON d.chapter_id = ch.chapter_id
            WHERE d.version = (
                SELECT MAX(version) FROM drafts WHERE chapter_id = ch.chapter_id
            )
            ORDER BY ch.project_id, ch.number
            """
        )
        for pid, num, content in rows:
            out.append((f"{pid[-4:]}#ch{num}", content))
    finally:
        conn.close()
    return out


def _db_human_samples(db: str) -> list[tuple[str, str]]:
    """人类侧语料：**只取 ``author_style_samples``**（明确标注的人类原文节选）。

    为什么不用别的项目的 chapters：本库其它项目的 drafts 同样是**生成侧产出**
    （旧废案、拆书实验），拿它们当人类侧会把对照彻底做反——首版脚本踩过这个坑。
    同一篇样章被多个项目复制时按内容去重。
    """
    conn = sqlite3.connect(db)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    try:
        for title, content in conn.execute(
            "SELECT title, content FROM author_style_samples ORDER BY sample_id"
        ):
            key = hashlib.sha1(content.encode("utf-8")).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            out.append((f"样章:{str(title)[:20]}", content))
    finally:
        conn.close()
    return out


def _file_texts(paths: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for f in sorted(path.glob("*.txt")):
                out.append((f.name, f.read_text(encoding="utf-8", errors="replace")))
        elif path.is_file():
            out.append((path.name, path.read_text(encoding="utf-8", errors="replace")))
    return out


def _report(side: str, items: list[tuple[str, str]]) -> dict[str, float]:
    total = sum(visible_chars(t) for _, t in items) or 1
    print(f"\n=== {side}：{len(items)} 篇 / {total} 可见字 ===")
    rates: dict[str, float] = {}
    for name, fn in OPERATORS.items():
        n = sum(len(fn(t)) for _, t in items)
        rates[name] = n / (total / 1000.0)
    return rates


def _precision_samples(
    side: str, items: list[tuple[str, str]], samples: int
) -> None:
    print(f"\n{'=' * 72}\n精确率抽样（{side}）—— 逐算子看命中实例，判断有无误报\n{'=' * 72}")
    for name, fn in OPERATORS.items():
        pool: list[tuple[str, str]] = []
        for label, text in items:
            for hit in fn(text):
                pool.append((label, hit.strip()))
                if len(pool) >= samples:
                    break
            if len(pool) >= samples:
                break
        print(f"\n--- {name}（前 {len(pool)} 例）")
        if not pool:
            print("    （无命中）")
            continue
        for label, hit in pool:
            print(f"    [{label}] {hit[:70]}")

    _beat_repeat_samples(side, items, samples)
    _time_conflict_samples(side, items, samples)


def _time_conflict_samples(
    side: str, items: list[tuple[str, str]], samples: int
) -> None:
    """``CONT-*`` 专项抽样：必须打印**两个标记的间距与全部守卫字段**。

    只看 ``前标记→后标记`` 判断不了精确率——「子时之前」是期限语（误报），
    「亥时，内室只剩一盏油灯。」是场景时点戳（真命中），字面上分不出来；
    本算子（``continuity_time``）对它们的判别正是**守卫**，故抽样要把守卫依据
    一并打出来，人工才能复核「被挡掉的到底该不该挡」。
    """
    print(f"\n--- 章内时点矛盾（CONT-*，{side}前 {samples} 例，含守卫依据）")
    shown = 0
    for label, text in items:
        for hit in time_conflicts(text):
            print(f"    [{label}] {hit.rule_id} :: {hit.detail}")
            print(f"        first={hit.first.text!r}(@{hit.first.start}) "
                  f"second={hit.second.text!r}(@{hit.second.start}) gap={hit.gap}")
            shown += 1
            if shown >= samples:
                return
    if shown == 0:
        print("    （无命中）")


def _beat_repeat_samples(
    side: str, items: list[tuple[str, str]], samples: int
) -> None:
    """``AI-BEAT-REPEAT`` 专项抽样：必须打印**两处片段**。

    只看共享串判断不了精确率——「族老会的决议」是专名复指（误报），
    「椅子腿蹭着地面」是同一节拍重播（真命中），字面上看不出区别；
    要人工过目就得看到两处上下文（见该算子模块 docstring 的抽样口径）。
    """
    print(f"\n--- 同章远距小句复现（AI-BEAT-REPEAT，{side}前 {samples} 例，两处片段对照）")
    shown = 0
    for label, text in items:
        for hit in beat_repeats(text):
            print(
                f"    [{label}] 「{hit.text}」 {hit.length}字 / 相距 {hit.gap} 字"
                f" / 出现 {hit.occurrences} 次"
            )
            print(f"        A: {hit.first}")
            print(f"        B: {hit.second}")
            shown += 1
            if shown >= samples:
                return
    if shown == 0:
        print("    （无命中）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--human-file", action="append", default=[],
                    help="人类侧文本文件或目录（可多次传入）")
    ap.add_argument("--ai-dir", action="append", default=[],
                    help="生成侧文本文件或目录（可多次传入）")
    ap.add_argument("--no-samples", action="store_true", help="只出频率表")
    ap.add_argument(
        "--all-projects",
        action="store_true",
        help="生成侧取全库所有项目的最新草稿（时点矛盾等稀有事件算子复算必用）",
    )
    args = ap.parse_args()

    if args.ai_dir:
        ai_items = _file_texts(args.ai_dir)
    elif args.all_projects:
        ai_items = _db_all_chapters(args.db)
    else:
        ai_items = _db_chapters(args.db, DEFAULT_AI_PROJECTS)
    if args.human_file:
        human_items = _file_texts(args.human_file)
    else:
        human_items = _db_human_samples(args.db)

    if not ai_items or not human_items:
        raise SystemExit("生成侧或人类侧语料为空，检查 --ai-dir / --human-file / 库内容")

    ai_rates = _report("生成侧", ai_items)
    hu_rates = _report("人类侧", human_items)

    print(f"\n{'=' * 72}\n频率与 R（R = 生成侧 ÷ 人类侧；口径同来源研究）\n{'=' * 72}")
    print(f"{'算子':<32}{'生成/千字':>10}{'人类/千字':>10}{'R':>8}  判定")
    for name in OPERATORS:
        a, h = ai_rates[name], hu_rates[name]
        if h == 0:
            r_s, verdict = "inf", "人类侧无命中（样本小，勿据此定阈）"
        else:
            r = a / h
            r_s = f"{r:.2f}"
            verdict = "生成侧偏高" if r >= 2.0 else ("无区分" if 0.8 <= r < 1.25 else "人类侧偏高")
        print(f"{name:<32}{a:>10.2f}{h:>10.2f}{r_s:>8}  {verdict}")

    if not args.no_samples:
        _precision_samples("生成侧", ai_items, args.samples)
        _precision_samples("人类侧", human_items, args.samples)


if __name__ == "__main__":
    main()
