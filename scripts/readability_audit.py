# -*- coding: utf-8 -*-
"""可读性审计：长段 / 对话占比两个指标的只读回放与人类基线对照。

为什么需要这个脚本（对齐本仓「检测规则三种病」纪律）：
  算子只看字面统计，不看内容语义——阈值定在哪、命中是不是真货，都必须拿**实测
  分布**说话。本脚本把两类语料摆在同一张表上：文风锚点书（人类基线）与本地库
  指定项目的生成侧全弧，逐章跑 ``scan_ai_patterns`` 的两条可读性规则，并把命中
  实例抽样打出来供人工过一遍（抽样检视是硬纪律，见 AGENTS.md 坑区）。

口径（与 ``packages/core/quality/ai_patterns.py`` 内常量注释一致）：
  - 段：按任意换行序列切段（外部锚点书是一行一段，生产 draft 是空行分段）；
  - 字数：``wordcount.visible_chars``（去空白）；
  - 对话：成对 “…”（U+201C/U+201D）内的可见字 ÷ 全章可见字。

只读：``sqlite3`` 以 ``mode=ro`` 打开库，不写任何文件（``--out`` 只是可选的
stdout 副本）。

用法::

    python scripts/readability_audit.py                     # 本仓默认项目 + 榜一对照
    python scripts/readability_audit.py --project prj_xxx
    python scripts/readability_audit.py --samples 10        # 抽样打印命中实例
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.quality.ai_patterns import (  # noqa: E402
    DEFAULT_DIALOGUE_LOW_WARN_RATIO,
    DEFAULT_LONG_PARA_ERROR_CHARS,
    DEFAULT_LONG_PARA_WARN_CHARS,
    count_dialogue_visible_chars,
    count_long_paragraphs,
    dialogue_ratio,
    scan_ai_patterns,
)
from packages.core.quality.wordcount import visible_chars  # noqa: E402

DEFAULT_DB = "data/novelos.db"
# 读者反馈「可读性差」的那本书（全弧 48 章 / 122350 可见字）。
DEFAULT_PROJECT = "prj_bcb9d1930bd4"
# 文风锚点书（榜一）侯府弧：人类基线，内容仓内（三仓分离，正文不进软件仓）。
REFERENCE_SUBPATH = Path("reference-books/榜一-快穿之人渣洗白手册/侯府凤凰男")

_READABILITY_RULE_IDS = ("AI-LONG-PARA", "AI-DIALOGUE-LOW")


def _reference_dir() -> Path | None:
    """定位榜一侯府弧正文目录；内容仓不在本机时返回 None。"""
    candidates: list[Path] = []
    env_dir = os.environ.get("NOVELOS_CONTENT_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path(__file__).resolve().parent.parent.parent / "NovelOS-Content")
    for root in candidates:
        target = root / REFERENCE_SUBPATH
        if target.is_dir():
            return target
    return None


def _reference_chapters() -> list[tuple[str, str]]:
    """榜一侯府弧全部章节 ``(章标签, 正文)``，按章号升序。"""
    ref_dir = _reference_dir()
    if ref_dir is None:
        return []
    numbered: list[tuple[int, Path]] = []
    for path in ref_dir.glob("*.txt"):
        m = re.search(r"第(\d+)章", path.name)
        if m:
            numbered.append((int(m.group(1)), path))
    numbered.sort(key=lambda item: item[0])
    return [
        (f"第{no}章", path.read_text(encoding="utf-8", errors="replace"))
        for no, path in numbered
    ]


def _db_chapters(db: str, project_id: str) -> list[tuple[str, str]]:
    """生成侧语料：该项目每章的最新草稿（只读连接）。"""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: list[tuple[str, str]] = []
    try:
        for number, chapter_id in conn.execute(
            "SELECT number, chapter_id FROM chapters WHERE project_id=? ORDER BY number",
            (project_id,),
        ):
            row = conn.execute(
                "SELECT content FROM drafts WHERE chapter_id=? ORDER BY version DESC LIMIT 1",
                (chapter_id,),
            ).fetchone()
            if row:
                out.append((f"ch{number}", row[0]))
    finally:
        conn.close()
    return out


def _paragraph_stats(prose: str) -> dict[str, float]:
    long_paras = count_long_paragraphs(prose)
    lengths = [visible_chars(p) for p in re.split(r"\n+", prose) if p.strip()]
    total = visible_chars(prose)
    return {
        "visible_chars": total,
        "paragraphs": len(lengths),
        "avg_para": (total / len(lengths)) if lengths else 0.0,
        "over100": len(long_paras),
        "over120": sum(1 for x in lengths if x > 120),
        "over140": sum(1 for x in lengths if x > DEFAULT_LONG_PARA_ERROR_CHARS),
        "max_para": max(lengths) if lengths else 0,
        "dialogue_ratio": dialogue_ratio(prose),
    }


def _report_side(label: str, chapters: list[tuple[str, str]], samples: int) -> None:
    """"逐章跑两条规则 + 汇总一行的对照表。"""
    print(f"\n{'=' * 88}\n{label}：{len(chapters)} 章\n{'=' * 88}")
    print(
        f"{'章':<8}{'可见字':>8}{'段数':>6}{'均段长':>8}{'>100段':>7}{'>120段':>7}"
        f"{'>140段':>7}{'最长段':>7}{'对话占比':>9}  命中"
    )
    totals = {
        "visible_chars": 0,
        "paragraphs": 0,
        "over100": 0,
        "over120": 0,
        "over140": 0,
        "dialogue_chars": 0,
    }
    long_para_hits = 0
    dialogue_hits = 0
    long_para_errors = 0
    dialogue_errors = 0
    for name, prose in chapters:
        stats = _paragraph_stats(prose)
        hits = {
            h["rule_id"]: h
            for h in scan_ai_patterns(prose)
            if h["rule_id"] in _READABILITY_RULE_IDS
        }
        if "AI-LONG-PARA" in hits:
            long_para_hits += 1
            long_para_errors += hits["AI-LONG-PARA"]["severity"] == "error"
        if "AI-DIALOGUE-LOW" in hits:
            dialogue_hits += 1
            dialogue_errors += hits["AI-DIALOGUE-LOW"]["severity"] == "error"
        marks = ",".join(
            f"{rid}({hits[rid]['severity'][:3]})" for rid in sorted(hits)
        )
        print(
            f"{name:<8}{stats['visible_chars']:>8}{stats['paragraphs']:>6}"
            f"{stats['avg_para']:>8.1f}{stats['over100']:>7}{stats['over120']:>7}"
            f"{stats['over140']:>7}{stats['max_para']:>7}{stats['dialogue_ratio']:>9.3f}"
            f"  {marks}"
        )
        for key in ("visible_chars", "paragraphs", "over100", "over120", "over140"):
            totals[key] += stats[key]  # type: ignore[operator]
        totals["dialogue_chars"] += count_dialogue_visible_chars(prose)

    wc = totals["visible_chars"] or 1
    print(
        f"\n汇总：{totals['visible_chars']} 可见字 / {totals['paragraphs']} 段 / "
        f"均段长 {totals['visible_chars'] / max(1, totals['paragraphs']):.1f} 字 / "
        f">100 字 {totals['over100']} 段 / >120 字 {totals['over120']} 段 / "
        f">140 字 {totals['over140']} 段 / "
        f"对话占比 {totals['dialogue_chars'] / wc:.3f}"
    )
    print(
        f"命中章数：AI-LONG-PARA {long_para_hits}/{len(chapters)}"
        f"（其中 error {long_para_errors}）· "
        f"AI-DIALOGUE-LOW {dialogue_hits}/{len(chapters)}"
        f"（其中 error {dialogue_errors}）"
    )

    if samples:
        _print_samples(chapters, samples)


def _print_samples(chapters: list[tuple[str, str]], samples: int) -> None:
    """抽样检视：把长段开头与低对话章的数字打出来，人工过一遍。"""
    print(f"\n--- 抽样：长段命中（前 {samples} 段，开头 60 字）---")
    shown = 0
    for name, prose in chapters:
        for para in count_long_paragraphs(prose):
            print(f"    [{name} {visible_chars(para)}字] {para[:60]}")
            shown += 1
            if shown >= samples:
                break
        if shown >= samples:
            break
    if shown == 0:
        print("    （无命中）")

    print(f"\n--- 抽样：对话占比最低的 {samples} 章 ---")
    rows = sorted(
        ((dialogue_ratio(prose), name, prose) for name, prose in chapters),
        key=lambda item: item[0],
    )
    for ratio, name, prose in rows[:samples]:
        print(
            f"    [{name}] 对话占比 {ratio:.3f}"
            f"（{count_dialogue_visible_chars(prose)}/{visible_chars(prose)}）"
        )


class _Tee:
    """把 stdout 同时写一份到文件（``--out`` 用；脚本本身不写库）。"""

    def __init__(self, stream, path: Path) -> None:
        self._stream = stream
        self._file = path.open("w", encoding="utf-8")

    def write(self, data: str) -> int:
        self._file.write(data)
        return self._stream.write(data)

    def flush(self) -> None:
        self._file.flush()
        self._stream.flush()

    def close(self) -> None:
        self._file.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--samples", type=int, default=5, help="抽样条数；0 = 不抽样")
    ap.add_argument("--out", default=None, help="stdout 的 UTF-8 副本路径")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tee: _Tee | None = None
    if args.out:
        tee = _Tee(sys.stdout, Path(args.out))
        sys.stdout = tee  # type: ignore[assignment]

    print(
        "阈值：长段 >{w} 字（单章 ≥{n} 段）warning / >{e} 字 error；"
        "对话占比 <{dw:.0%} warning / <{de:.0%} error；可见字 <600 不判".format(
            w=DEFAULT_LONG_PARA_WARN_CHARS,
            n=2,
            e=DEFAULT_LONG_PARA_ERROR_CHARS,
            dw=DEFAULT_DIALOGUE_LOW_WARN_RATIO,
            de=0.08,
        )
    )

    generated = _db_chapters(args.db, args.project)
    if not generated:
        raise SystemExit(f"库 {args.db} 里项目 {args.project} 无草稿")
    _report_side(f"生成侧 · {args.project}", generated, args.samples)

    reference = _reference_chapters()
    if reference:
        _report_side("人类基线 · 榜一侯府弧", reference, 0)
    else:
        print(f"\n（内容仓未挂载：跳过人类基线对照 {REFERENCE_SUBPATH}）")

    if tee is not None:
        tee.close()


if __name__ == "__main__":
    main()
