# -*- coding: utf-8 -*-
"""按项目把正文（drafts 最新版）过一遍确定性规整 + 内部标识体检。

为什么需要它：引号漂移与内部字段名入文是**正则可判**的正文事故（2026-09-16
新书 01 全弧实证：119 份 draft 里 109 份有引号变更，1 份含 ``recalled_passages``）。
能算子判定的就不该靠提示词，也不该靠人肉逐章读——本脚本把「体检」与「修复」
分成两步，默认只体检。

用法::

    python scripts/normalize_chapters.py --project prj_xxxx        # 体检（dry-run）
    python scripts/normalize_chapters.py --project prj_xxxx --apply # 写入最新 draft

注意：
- 只动 ``drafts.content`` 的**最新版本**（历史版本保留原样，审计可回溯）。
- ``--apply`` 是原地改库，会绕过工作流状态机——它只做「引号字形替换 + 标识上报」
  这两件语义零变更的事；任何涉及叙述内容的修补都必须走 gate-revise 流水线。
- 内部标识**只报不改**（删了句子就断，见 ``find_internal_identifiers`` docstring）。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.quality.normalize import (  # noqa: E402
    find_internal_identifiers,
    normalize_prose,
)


def _latest_drafts(conn: sqlite3.Connection, project_id: str) -> list[tuple[int, str, str]]:
    """[(章号, draft_id, content)]：每章最新一版。"""
    rows = conn.execute(
        """
        SELECT c.number AS number, d.draft_id AS draft_id, d.content AS content
        FROM chapters c
        JOIN drafts d ON d.chapter_id = c.chapter_id
        WHERE c.project_id = ?
          AND d.version = (
              SELECT MAX(version) FROM drafts WHERE chapter_id = c.chapter_id
          )
        ORDER BY c.number
        """,
        (project_id,),
    ).fetchall()
    return [(r["number"], r["draft_id"], r["content"] or "") for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/novelos.db")
    ap.add_argument("--project", required=True)
    ap.add_argument("--apply", action="store_true", help="写入（缺省 dry-run）")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        drafts = _latest_drafts(conn, args.project)
        if not drafts:
            print(f"项目 {args.project} 无 draft")
            return 1
        total_q = 0
        touched: list[int] = []
        leaks: list[tuple[int, list[str]]] = []
        for number, draft_id, content in drafts:
            fixed, changes = normalize_prose(content)
            ids = find_internal_identifiers(fixed)
            if ids:
                leaks.append((number, ids))
            q = sum(int(c.get("count") or 0) for c in changes)
            if not q:
                continue
            total_q += q
            touched.append(number)
            print(f"ch{number:2d}  引号 {q} 对  →  “”")
            if args.apply and fixed != content:
                conn.execute(
                    "UPDATE drafts SET content = ? WHERE draft_id = ?",
                    (fixed, draft_id),
                )
        if args.apply:
            conn.commit()
        print()
        print(f"{len(touched)}/{len(drafts)} 章有引号变更，合计 {total_q} 对"
              f"（{'已写入' if args.apply else 'dry-run，未写入'}）")
        if leaks:
            print("内部标识命中（只报不改，需人工或 gate-revise 处理）：")
            for number, ids in leaks:
                print(f"  ch{number}: {', '.join(ids)}")
        else:
            print("内部标识：0 命中")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
