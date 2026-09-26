# -*- coding: utf-8 -*-
"""Revise 保真阈值校准（只读 dev 库） + 低比值抽样人工检视。

为 ``packages.core.agent_runtime.revision_fidelity.REVISE_MIN_PRESERVED_RATIO``
提供定锚数据。只读连接（``file:...?mode=ro``），不写库、不改任何文件。

三块能力：
  1. **闸门管辖人群**：从 ``workflow_runs.checkpoint_json`` 还原历史
     ``writer_input``（mode='revise' 且 draft_text / writer_output.prose 齐全的轮次），
     实测 preserved_ratio 分布——这是新契约唯一会拦的人群（运行时 mode='revise'）。
  2. **相邻版本对照**：各章 drafts 相邻版本对（n→n+1）的全量比值分布——混合了
     fresh_write / 欠带翻模（运行时 mode='write'，不归闸门管）与真 revise，
     用于看「静默重写簇」的比值空间；单独列事故章 ch_92bac068ff0d。
  3. **低比值抽样**：比值最低 ≤20 条，打印比值 + 首 60 字，供人工检视
     （对齐「抽样 20 条再采信频率结果」的仓规）。

用法::

    python scripts/revise_fidelity_calibrate.py
    python scripts/revise_fidelity_calibrate.py --db data/novelos.db --samples 20

---------------------------------------------------------------------
2026-09-26 实测结论（data/novelos.db，写死留痕；重跑可复核）：

- 闸门管辖人群（checkpoint 还原 mode='revise'，n=18）：
  min 0.6463 / p10 0.7515 / p25 0.8424 / p50 0.9344 / p75 0.9638 / max 0.9865。
  ——真实定向改稿全部 ≥ 0.64；阈值必须低于 0.6463 并留残差余量。
- 相邻版本对（全章，n=101）：双峰。低簇（整章重写轮）bulk ≤ 0.16（p10 0.0791 /
  p25 0.1752），高簇（定向改稿轮）p50 0.8113 / p75 0.9461 / max 1.0。
- 事故章 ch_92bac068ff0d 逐对：v5→v6=0.5981、v7→v8=0.7417 是欠带翻模逃逸轮
  （+88% / +65% 增幅，writer 规则 20 下不可能是合规 revise，运行时 mode='write'，
  闸门不适用）；v1→v4 的「932→1300 四轮」各对比值 0.887~0.942（事故形态是
  成段复制不是整体重写，preserved_ratio 抓的不是这一形状——重复由
  RULE_STYLE_REPETITION_TRIGRAM 管）。
- 尾块权重：REVISION-CHECKLIST 尾块计入会把 60 字样板的比值从 0.845 拉到 0.417，
  故闸门比对前必须剥尾块（revision_fidelity.strip_revision_checklist_tail）。

⇒ 定值 **REVISE_MIN_PRESERVED_RATIO = 0.50**：低于管辖人群下沿 0.6463 约 0.146
（覆盖运行时残差），高于静默重写簇上沿（bulk 0.16）约 0.32；比初始猜测 0.55 更
保守——契约拒绝会烧一次真 LLM 重试，宁可少拦误报。
---------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.agent_runtime.revision_fidelity import (  # noqa: E402
    preserved_ratio,
    strip_revision_checklist_tail,
)

ACCIDENT_CHAPTER_ID = "ch_92bac068ff0d"
_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "novelos.db"


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """只读连接（URI mode=ro；库不存在直接报错，不意外建库）。"""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return sorted_vals[idx]


def _print_dist(title: str, ratios: list[float]) -> None:
    vals = sorted(ratios)
    print(f"\n== {title} (n={len(vals)}) ==")
    if not vals:
        print("  (无样本)")
        return
    for q in (0.10, 0.25, 0.50, 0.75, 1.00):
        print(f"  p{int(q * 100)}: {_percentile(vals, q):.4f}")
    print(f"  min: {vals[0]:.4f}")


def _gate_population(conn: sqlite3.Connection) -> list[tuple[float, str, int, int]]:
    """闸门管辖人群：checkpoint 里 writer_input.mode='revise' 的历史轮次实测。"""
    rows = conn.execute(
        "SELECT run_id, chapter_id, checkpoint_json FROM workflow_runs "
        "WHERE checkpoint_json LIKE '%writer_input%'"
    ).fetchall()
    out: list[tuple[float, str, int, int]] = []
    for r in rows:
        try:
            ck = json.loads(r["checkpoint_json"])
        except (TypeError, ValueError):
            continue
        wi = ck.get("writer_input") or {}
        wo = ck.get("writer_output") or {}
        if not isinstance(wi, dict) or not isinstance(wo, dict):
            continue
        if wi.get("mode") != "revise":
            continue
        upstream = wi.get("draft_text")
        prose = strip_revision_checklist_tail(wo.get("prose") or "")
        if not isinstance(upstream, str) or not upstream or not prose:
            continue
        ratio = preserved_ratio(upstream, prose)
        out.append((ratio, r["chapter_id"], len(upstream), len(prose)))
    return out


def _adjacent_pairs(conn: sqlite3.Connection) -> list[tuple[float, str, int, int, int, int]]:
    """各章 drafts 相邻版本对（n→n+1）的 preserved_ratio（两端剥尾块）。"""
    chapters = conn.execute(
        "SELECT DISTINCT chapter_id FROM drafts ORDER BY chapter_id"
    ).fetchall()
    out: list[tuple[float, str, int, int, int, int]] = []
    for ch in chapters:
        rows = conn.execute(
            "SELECT version, content FROM drafts WHERE chapter_id=? ORDER BY version",
            (ch["chapter_id"],),
        ).fetchall()
        for a, b in zip(rows, rows[1:]):
            ca = strip_revision_checklist_tail(a["content"] or "")
            cb = strip_revision_checklist_tail(b["content"] or "")
            if not ca or not cb:
                continue
            ratio = preserved_ratio(ca, cb)
            out.append((ratio, ch["chapter_id"], a["version"], len(ca), b["version"], len(cb)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=_DEFAULT_DB, help="dev 库路径（只读）")
    ap.add_argument("--samples", type=int, default=20, help="低比值抽样条数上限")
    args = ap.parse_args()

    conn = _connect_ro(args.db)
    try:
        gate = _gate_population(conn)
        _print_dist("闸门管辖人群 checkpoint 还原 mode=revise", [g[0] for g in gate])
        for g in sorted(gate):
            print(f"  ratio={g[0]:.4f} ch={g[1][-12:]} upstream={g[2]} prose={g[3]}")

        pairs = _adjacent_pairs(conn)
        _print_dist("相邻版本对（全章混合）", [p[0] for p in pairs])
        accident = sorted(p for p in pairs if p[1] == ACCIDENT_CHAPTER_ID)
        _print_dist(f"事故章 {ACCIDENT_CHAPTER_ID} 逐对", [p[0] for p in accident])
        for p in accident:
            print(
                f"  ratio={p[0]:.4f} v{p[2]}({p[3]})→v{p[4]}({p[5]})"
            )

        # 低比值抽样（≤ samples 条，比值 + 首 60 字，供人工检视）
        print(f"\n== 低比值抽样（≤{args.samples} 条，首 60 字）==")
        lowest = sorted(pairs)[: args.samples]
        for p in lowest:
            row = conn.execute(
                "SELECT content FROM drafts WHERE chapter_id=? AND version=?",
                (p[1], p[4]),
            ).fetchone()
            head = (row["content"] or "")[:60].replace("\n", "\\n") if row else "(missing)"
            print(f"  ratio={p[0]:.4f} ch={p[1][-12:]} v{p[2]}→v{p[4]} head={head!r}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
