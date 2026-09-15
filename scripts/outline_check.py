# -*- coding: utf-8 -*-
"""卷纲质量检查 CLI —— 把 `packages.core.quality.outline_check` 跑在真实项目上。

用法::

    # 检查某项目当前大纲（读 chapters.outline_json）
    python scripts/outline_check.py --project prj_xxxxxxxx

    # 指定数据库
    python scripts/outline_check.py --db data/novelos.db --project prj_xxxxxxxx

输出：逐条判据的结论 + **命中证据原文**（本工具是报告器，不做自动 pass/fail——
每个算子匹配的是字面形态，须人工过一遍证据再采信，理由见模块 docstring）。

对手/主角名自动从 actors 表取（antagonist / protagonist），也可用
``--antagonist`` / ``--protagonist`` 手动指定。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.quality.outline_check import check_outline  # noqa: E402


def _names(conn: sqlite3.Connection, project_id: str, role: str) -> list[str]:
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM characters WHERE project_id = ? AND role = ?",
            (project_id, role))]
    except sqlite3.Error:
        return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/novelos.db")
    ap.add_argument("--project", required=True)
    ap.add_argument("--antagonist", action="append", default=[])
    ap.add_argument("--protagonist", action="append", default=[])
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT number, title, outline_json FROM chapters "
        "WHERE project_id = ? ORDER BY number", (args.project,)).fetchall()
    ants = args.antagonist or _names(conn, args.project, "antagonist")
    pros = args.protagonist or _names(conn, args.project, "protagonist")
    conn.close()

    chapters, titles = [], []
    for num, title, raw in rows:
        try:
            d = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            d = {}
        if not d:
            continue
        chapters.append(d)
        titles.append(f"ch{num} {title}")

    if not chapters:
        raise SystemExit("该项目没有 outline_json（先跑 project-init 的 outline 阶段）")

    print(f"项目 {args.project}：{len(chapters)} 章有大纲")
    print(f"主角名 {pros or '（未指定）'}｜反派名 {ants or '（未指定）'}")
    print("=" * 72)

    findings = check_outline(chapters, antagonist_names=ants, protagonist_names=pros)
    alerts = [f for f in findings if f.level == "alert"]
    for f in findings:
        mark = "⚠ " if f.level == "alert" else "  "
        print(f"{mark}[{f.rule_id}] {f.message}")
        for e in f.evidence[:8]:
            print(f"        · {e}")
    print("=" * 72)
    print(f"alert {len(alerts)} 条 / 合计 {len(findings)} 条。"
          f"命中项须人工核对证据原文后再决定是否修改大纲。")


if __name__ == "__main__":
    main()
