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
from packages.core.quality.wordcount import visible_chars  # noqa: E402

DEFAULT_DB = "data/novelos.db"
DEFAULT_AI_PROJECTS = ("prj_f26ec5d3f03c", "prj_c1c1eaa4fe5e")

# 对齐来源研究的 11 项里可正则化的部分；本仓另有 6 条历史规则在 ai_patterns 内。
OPERATORS = {
    "对举结构(不是A而是B)": count_contrast_pairs,
    "短句独立成段": count_short_paras,
    "拟人化喻体(职业类)": count_anthro_vehicles,
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--human-file", action="append", default=[],
                    help="人类侧文本文件或目录（可多次传入）")
    ap.add_argument("--ai-dir", action="append", default=[],
                    help="生成侧文本文件或目录（可多次传入）")
    ap.add_argument("--no-samples", action="store_true", help="只出频率表")
    args = ap.parse_args()

    if args.ai_dir:
        ai_items = _file_texts(args.ai_dir)
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
