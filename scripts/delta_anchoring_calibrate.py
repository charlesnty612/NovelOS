"""delta 源锚定阈值校准脚本（只读，2026-09-26 批次）。

用途：为 ``packages/core/story_state/delta_anchoring.py`` 的
``DELTA_BIGRAM_COVERAGE_MIN`` 提供实测分布依据——阈值必须来自本仓数据，禁止拍脑袋。

口径（与生产接线同构）：
- 每条 state_deltas.payload_json 的 7 数组条目取 after（op ∈ {add, update}、
  含 CJK bigram）；
- corpus = 该 delta 所属章的**最新草稿**（drafts ORDER BY created_at DESC，与
  observer 输入装配 ``_latest_draft`` 同口径）；
- coverage = |after_bigrams ∩ corpus_bigrams| / |after_bigrams|。

输出：
- 覆盖率分布（n / p10 / p25 / p50 / p85 / max / 均值）；
- 低覆盖样本 ≤20 条（path + coverage + 首 60 字）供人工检视（判别「确属可疑」
  还是「正常概括」——检测算子只匹配字面形态，样本必须人工过一遍）；
- excerpt 逐字违规计数（OBS-EXCERPT-NOT-VERBATIM 同口径顺带统计）。

用法：``python scripts/delta_anchoring_calibrate.py [--db data/novelos.db]``
只读打开（SQLite URI mode=ro），不写库、不改任何数据。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# 保证可从仓库根直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.core.story_state.delta_anchoring import (  # noqa: E402
    _MIN_BIGRAMS,
    _cjk_bigrams,
    _normalize_verbatim,
)

_DEFAULT_DB = Path("data/novelos.db")

# 与 delta_anchoring 一致的 7 数组与 op 集（脚本独立声明，避免 import 私有面）。
_DELTA_ARRAYS = (
    "character_changes", "world_changes", "relationship_changes",
    "new_events", "resolved_hooks", "new_hooks", "debt_changes",
)
_ANCHORED_OPS = {"add", "update"}


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """只读连接（URI mode=ro；库不存在直接报错，不静默建库）。"""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _quantile(sorted_vals: list[float], q: float) -> float:
    """线性插值分位数（q ∈ [0,1]）。"""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def main() -> int:
    parser = argparse.ArgumentParser(description="delta 源锚定阈值校准（只读）")
    parser.add_argument("--db", type=Path, default=_DEFAULT_DB, help="dev 库路径")
    parser.add_argument("--samples", type=int, default=20, help="低覆盖样本条数上限")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: db not found: {args.db}", file=sys.stderr)
        return 2

    conn = _connect_ro(args.db)
    try:
        # 各章最新 draft（created_at DESC，与 observer_input._latest_draft 同口径）
        draft_by_chapter: dict[str, str] = {}
        for row in conn.execute(
            "SELECT chapter_id, content FROM drafts "
            "WHERE chapter_id IN (SELECT DISTINCT chapter_id FROM state_deltas) "
            "ORDER BY created_at ASC"
        ):
            draft_by_chapter[row["chapter_id"]] = row["content"] or ""

        samples: list[dict] = []  # (delta_id, path, coverage, after)
        coverages: list[float] = []
        total_entries = 0
        skipped_no_bigram = 0
        skipped_short = 0
        excerpt_violations = 0
        excerpt_checked = 0
        deltas_without_draft = 0

        for drow in conn.execute(
            "SELECT delta_id, chapter_id, payload_json FROM state_deltas "
            "ORDER BY created_at ASC"
        ):
            draft = draft_by_chapter.get(drow["chapter_id"]) or ""
            if not draft.strip():
                deltas_without_draft += 1
                continue
            corpus = _cjk_bigrams(draft)
            try:
                payload = json.loads(drow["payload_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            normalized_draft = _normalize_verbatim(draft)
            for arr_name in _DELTA_ARRAYS:
                arr = payload.get(arr_name)
                if not isinstance(arr, list):
                    continue
                for i, item in enumerate(arr):
                    if not isinstance(item, dict):
                        continue
                    total_entries += 1
                    evidence = item.get("evidence")
                    excerpt = (
                        evidence.get("excerpt")
                        if isinstance(evidence, dict) else None
                    )
                    if isinstance(excerpt, str) and excerpt.strip():
                        excerpt_checked += 1
                        norm_ex = _normalize_verbatim(excerpt)
                        if not norm_ex or norm_ex not in normalized_draft:
                            excerpt_violations += 1
                    op = item.get("op")
                    after = item.get("after")
                    if op not in _ANCHORED_OPS or not isinstance(after, str):
                        continue
                    after_bigrams = _cjk_bigrams(after)
                    if not after_bigrams:
                        skipped_no_bigram += 1
                        continue
                    if len(after_bigrams) < _MIN_BIGRAMS:
                        skipped_short += 1
                        continue
                    cov = (
                        len(after_bigrams & corpus) / len(after_bigrams)
                        if corpus else 0.0
                    )
                    coverages.append(cov)
                    samples.append({
                        "delta_id": drow["delta_id"],
                        "path": f"{arr_name}[{i}]",
                        "coverage": round(cov, 4),
                        "after": after,
                    })
    finally:
        conn.close()

    if not coverages:
        print("no anchored after texts found (nothing to calibrate)")
        return 0

    coverages.sort()
    n = len(coverages)
    stats = {
        "n": n,
        "p10": round(_quantile(coverages, 0.10), 4),
        "p25": round(_quantile(coverages, 0.25), 4),
        "p50": round(_quantile(coverages, 0.50), 4),
        "p85": round(_quantile(coverages, 0.85), 4),
        "max": round(coverages[-1], 4),
        "mean": round(sum(coverages) / n, 4),
    }
    print("== bigram 覆盖率分布（每条 after vs 所属章最新 draft，生产同构口径） ==")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  delta 条目总数: {total_entries}；无 CJK bigram 跳过: {skipped_no_bigram}"
          f"；短状态词（bigram<{_MIN_BIGRAMS}）跳过: {skipped_short}"
          f"；无草稿章跳过的 delta 数: {deltas_without_draft}")
    print(f"  excerpt 归一化核验: 检查 {excerpt_checked} 条 / 违规 {excerpt_violations} 条"
          f"（背景违规率见 delta_anchoring 模块 docstring）")

    print(f"\n== 低覆盖样本（升序，≤{args.samples} 条；人工检视：可疑 or 正常概括） ==")
    low = sorted(samples, key=lambda s: s["coverage"])[: args.samples]
    for s in low:
        head = s["after"][:60].replace("\n", " ")
        print(f"  {s['delta_id']} {s['path']} cov={s['coverage']:.4f} | {head}")

    print("\n== 阈值建议 ==")
    p10 = stats["p10"]
    print(f"  正常质量带下沿（p10）= {p10}；阈值应取其下方、且与事故带拉开距离。")
    print("  事故构造形态（第一代老镖头式捏造）实测覆盖率 ≈ 0.23（见单元测试）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
