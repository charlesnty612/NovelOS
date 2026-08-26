"""M1 长跑一致性衰减观测报告 —— 数据分析脚本。

读取 M1 长跑的落盘数据（progress.json / chapters/chNNN.json / 可选 novelos.db），
生成《一致性衰减观测报告》的数据分析部分（Markdown 表格与统计），供主控撰写
最终报告使用。纯只读，不修改任何源文件。

用法：
    python scripts/m1_report.py --data-dir data/m1_run
    python scripts/m1_report.py --data-dir data/m1_run --db data/m1_run/novelos.db --out docs/evaluation/m1-data.md

输出：stdout 打印 Markdown，同时写入 --out 指定的文件。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 数据加载（纯读取 + 容错）
# ---------------------------------------------------------------------------


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    """安全加载 JSON；缺文件/解析失败返回 None。"""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _safe_get(d: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    """链式安全 get：d[*keys]。d 为 None 或缺键时返回 default。"""
    if d is None:
        return default
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def load_progress(data_dir: Path) -> dict[str, Any]:
    """加载 progress.json；缺文件返回空 dict（脚本可继续，仅打印告警）。"""
    p = data_dir / "progress.json"
    data = _safe_load_json(p)
    return data if data is not None else {}


def load_chapters(data_dir: Path) -> tuple[dict[int, dict[str, Any]], list[str]]:
    """加载所有 chNNN.json；返回 (按章号索引的 dict, 跳过的文件清单)。"""
    chapters_dir = data_dir / "chapters"
    out: dict[int, dict[str, Any]] = {}
    skipped: list[str] = []
    if not chapters_dir.is_dir():
        return out, [str(chapters_dir)]
    for path in sorted(chapters_dir.glob("ch*.json")):
        data = _safe_load_json(path)
        if data is None:
            skipped.append(path.name)
            continue
        ch_no = data.get("chapter_no")
        if not isinstance(ch_no, int):
            skipped.append(path.name)
            continue
        out[ch_no] = data
    return out, skipped


def load_quality_reports(db_path: Path | None) -> tuple[list[dict[str, Any]], str | None]:
    """只读打开 SQLite；返回 (每条 issue 一行 dict 的列表, db_err)。

    db_err 非空时表示数据库不可用，调用方需降级显示。
    """
    if db_path is None:
        return [], "未指定 --db，跳过 issue 频次统计"
    if not db_path.exists():
        return [], f"DB 不存在: {db_path}"

    rows_iter: list[tuple[Any, ...]] = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='quality_reports'"
            )
            if cur.fetchone() is None:
                return [], "quality_reports 表不存在，跳过 issue 频次统计"

            # 探测 chapters 表的章号列名（number / chapter_no / 其它）
            ch_no_col: str | None = None
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='chapters'"
            )
            if cur.fetchone() is not None:
                cols = {row[1] for row in con.execute("PRAGMA table_info(chapters)").fetchall()}
                for cand in ("number", "chapter_no", "no"):
                    if cand in cols:
                        ch_no_col = cand
                        break

            if ch_no_col:
                sql = (
                    "SELECT qr.report_id, qr.issues_json, qr.created_at, "
                    f"c.{ch_no_col}, qr.chapter_id "
                    "FROM quality_reports qr "
                    "LEFT JOIN chapters c ON c.chapter_id = qr.chapter_id "
                    "ORDER BY qr.created_at"
                )
            else:
                sql = (
                    "SELECT qr.report_id, qr.issues_json, qr.created_at, NULL, qr.chapter_id "
                    "FROM quality_reports qr ORDER BY qr.created_at"
                )
            rows_iter = list(con.execute(sql).fetchall())
        finally:
            con.close()
    except sqlite3.Error as e:
        return [], f"DB 读取失败: {e}"

    raw_records: list[tuple[str, str, str, int | None, str]] = []
    for row in rows_iter:
        report_id, issues_json, created_at = row[0], row[1], row[2]
        chapter_no = row[3] if len(row) > 3 and isinstance(row[3], int) else None
        chapter_id = row[4] if len(row) > 4 else None
        raw_records.append((report_id, issues_json, created_at, chapter_no, chapter_id))

    rows: list[dict[str, Any]] = []
    for report_id, issues_json, created_at, chapter_no, chapter_id in raw_records:
        try:
            issues = json.loads(issues_json) if issues_json else []
        except json.JSONDecodeError:
            issues = []
        for it in issues:
            if not isinstance(it, dict):
                continue
            rows.append(
                {
                    "report_id": report_id,
                    "chapter_no": chapter_no,
                    "chapter_id": chapter_id,
                    "rule_id": it.get("rule_id") or "<unknown>",
                    "severity": it.get("severity") or "<unknown>",
                    "created_at": created_at,
                }
            )
    return rows, None


def load_story_state_growth(db_path: Path | None) -> tuple[list[tuple[int, int]], str | None]:
    """取 story_states 按 state_version 排序的 (state_version, snapshot_bytes)；DB 不可用时降级。"""
    if db_path is None:
        return [], "未指定 --db，跳过快照体积增长"
    if not db_path.exists():
        return [], f"DB 不存在: {db_path}"
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = con.execute(
                "SELECT state_version, length(snapshot_json) FROM story_states ORDER BY state_version"
            )
            rows = [(int(v), int(b)) for v, b in cur.fetchall() if v is not None]
        finally:
            con.close()
    except sqlite3.Error as e:
        return [], f"DB 读取失败: {e}"
    return rows, None


# --- 7 节：按 agent 聚合 token / 调用 / 延迟 / 重试 ---
AgentTokenRow = dict[str, Any]


def load_agent_token_stats(
    db_path: Path | None,
) -> tuple[list[AgentTokenRow], int, str | None]:
    """JOIN ``ai_call_logs`` 与 ``agents``，按 ``agent.name`` 分组聚合 token。

    返回 ``(rows, null_skipped, err)``：
    - rows：按 ``total`` 降序的列表，每行字段见 :data:`AgentTokenRow`。
      agent_id 为 NULL / 空的行**不进**聚合，其条数记入 ``null_skipped``。
    - err：DB 不可用 / 表缺失时的告警。
    """
    empty: list[AgentTokenRow] = []
    if db_path is None:
        return empty, 0, "未指定 --db，跳过按 agent 计量"
    if not db_path.exists():
        return empty, 0, f"DB 不存在: {db_path}"

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            tbls = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "ai_call_logs" not in tbls:
                return empty, 0, "ai_call_logs 表不存在，跳过按 agent 计量"
            if "agents" not in tbls:
                return empty, 0, "agents 表不存在，跳过按 agent 计量"
            log_cols = {
                r[1]
                for r in con.execute("PRAGMA table_info(ai_call_logs)").fetchall()
            }
            for col in ("agent_id", "token_usage_json", "latency_ms", "retry_count"):
                if col not in log_cols:
                    return empty, 0, f"ai_call_logs 缺列 {col}"

            # 1) 统计被过滤行数（NULL / 空）
            null_skipped = int(
                con.execute(
                    "SELECT COUNT(*) FROM ai_call_logs "
                    "WHERE agent_id IS NULL OR TRIM(agent_id) = ''"
                ).fetchone()[0]
            )

            # 2) JOIN 聚合（NULL name 退化为 "<无 agent 行>"）
            sql = (
                "SELECT "
                "  COALESCE(a.name, '<无 agent 行>') AS agent_name, "
                "  COUNT(*) AS calls, "
                "  SUM(CAST(json_extract(l.token_usage_json, '$.prompt')     AS INTEGER)), "
                "  SUM(CAST(json_extract(l.token_usage_json, '$.completion') AS INTEGER)), "
                "  SUM(CAST(json_extract(l.token_usage_json, '$.total')      AS INTEGER)), "
                "  CAST(AVG(CAST(l.latency_ms AS REAL)) AS INTEGER), "
                "  SUM(CAST(COALESCE(l.retry_count, 0) AS INTEGER)) "
                "FROM ai_call_logs l "
                "LEFT JOIN agents a ON a.agent_id = l.agent_id "
                "WHERE l.agent_id IS NOT NULL AND TRIM(l.agent_id) != '' "
                "GROUP BY agent_name "
                "ORDER BY 4 DESC, 2 DESC"
            )
            rows: list[AgentTokenRow] = []
            for r in con.execute(sql).fetchall():
                name = r[0] if isinstance(r[0], str) and r[0] else "<无 agent 行>"
                rows.append(
                    {
                        "agent_name": name,
                        "calls": int(r[1] or 0),
                        "prompt": int(r[2] or 0),
                        "completion": int(r[3] or 0),
                        "total": int(r[4] or 0),
                        "avg_latency_ms": int(r[5] or 0),
                        "retry_sum": int(r[6] or 0),
                    }
                )
        finally:
            con.close()
    except sqlite3.Error as e:
        return empty, 0, f"DB 读取失败: {e}"

    return rows, null_skipped, None


# ---------------------------------------------------------------------------
# 章节行规整（从 chapters dict 提取每章一行）
# ---------------------------------------------------------------------------


def _ch_rows(
    chapters: dict[int, dict[str, Any]],
    progress: dict[str, Any],
) -> list[dict[str, Any]]:
    """把 chapters dict 整理为逐章行表（统一字段名，便于后续聚合）。"""
    completed = _safe_get(progress, "completed", default={}) or {}
    rows: list[dict[str, Any]] = []
    for ch_no in sorted(chapters):
        ch = chapters[ch_no]
        metrics = _safe_get(ch, "metrics", default={}) or {}
        quality = _safe_get(ch, "quality", default={}) or {}
        subs = _safe_get(quality, "seven_subs", default={}) or {}
        state = _safe_get(ch, "state", default={}) or {}
        workflows = _safe_get(ch, "workflows", default={}) or {}
        wf_attempts = _safe_get(ch, "wf_attempts", default={}) or {}

        max_attempt = 0
        for k in ("plan", "write", "review", "commit"):
            v = workflows.get(k, {}).get("attempt") if isinstance(workflows.get(k), dict) else None
            if isinstance(v, int):
                max_attempt = max(max_attempt, v)
        # wf_attempts 也作为来源之一（更可靠）
        for k in ("plan", "write", "review", "commit"):
            v = wf_attempts.get(k) if isinstance(wf_attempts, dict) else None
            if isinstance(v, int):
                max_attempt = max(max_attempt, v)

        # 重试：从 metrics.retry_sum 与 chapter_attempts 取较大者
        retry_sum = metrics.get("retry_sum") if isinstance(metrics.get("retry_sum"), int) else 0
        ch_attempts = ch.get("chapter_attempts") if isinstance(ch.get("chapter_attempts"), int) else 1
        retry_proxy = max(retry_sum, max(0, ch_attempts - 1))

        prog_entry = completed.get(str(ch_no)) or {}
        rows.append(
            {
                "chapter_no": ch_no,
                "prose_chars": int(ch.get("prose_chars") or 0),
                # V3.7：新增字数观测列（与 m1_long_run 采集结构对齐）
                "word_status": ch.get("word_status") or "unknown",
                "deviation_pct": float(ch.get("deviation_pct") or 0.0),
                "prompt_tokens": int(metrics.get("prompt_tokens") or 0),
                "completion_tokens": int(metrics.get("completion_tokens") or 0),
                "total_tokens": int(metrics.get("total_tokens") or prog_entry.get("total_tokens") or 0),
                "call_count": int(metrics.get("call_count") or 0),
                "retry_sum": retry_proxy,
                "max_attempt": max_attempt,
                "overall": int(quality.get("overall") if quality.get("overall") is not None
                               else prog_entry.get("quality_overall") or 0),
                "plot": int(subs.get("plot") or 0),
                "character": int(subs.get("character") or 0),
                "continuity": int(subs.get("continuity") or 0),
                "style": int(subs.get("style") or 0),
                "pacing": int(subs.get("pacing") or 0),
                "foreshadowing": int(subs.get("foreshadowing") or 0),
                "ai_trace": int(quality.get("ai_trace") or 0),
                "state_version": int(state.get("state_version") or prog_entry.get("state_version") or 0),
                "snapshot_bytes": int(state.get("snapshot_bytes") or 0),
                "wall_total_s": float(prog_entry.get("wall_total_s") or ch.get("wall_total_s") or 0.0),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_float(x: float, digits: int = 1) -> str:
    return f"{x:,.{digits}f}"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _band_label(start: int, end: int) -> str:
    return f"{start}-{end}"


# ---------------------------------------------------------------------------
# 6 节 Markdown 生成
# ---------------------------------------------------------------------------


def section_overall(rows: list[dict[str, Any]], progress: dict[str, Any]) -> str:
    target = int(progress.get("total_chapters") or len(rows))
    done = len(rows)
    total_tokens = sum(r["total_tokens"] for r in rows)
    prompt_tokens = sum(r["prompt_tokens"] for r in rows)
    completion_tokens = sum(r["completion_tokens"] for r in rows)
    wall_total = sum(r["wall_total_s"] for r in rows)
    avg_tokens = _mean([r["total_tokens"] for r in rows])
    avg_wall = _mean([r["wall_total_s"] for r in rows])

    out = [
        "## 1. 总体进度",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 已完成章数 / 目标 | {done} / {target} |",
        f"| 总耗时 (s) | {_fmt_float(wall_total, 1)} |",
        f"| Token 总量 | {_fmt_int(total_tokens)} |",
        f"| Token 中 prompt | {_fmt_int(prompt_tokens)} |",
        f"| Token 中 completion | {_fmt_int(completion_tokens)} |",
        f"| 平均单章 token | {_fmt_float(avg_tokens, 0)} |",
        f"| 平均单章耗时 (s) | {_fmt_float(avg_wall, 1)} |",
        "",
    ]
    return "\n".join(out)


def section_per_chapter(rows: list[dict[str, Any]]) -> str:
    cols = [
        ("章号", "{no}"),
        ("字数", "{pc}"),
        # V3.7：新增字数状态/偏离度列
        ("status", "{ws}"),
        ("dev%", "{dv}"),
        ("total_tokens (p/c)", "{tt} ({p}/{c})"),
        ("调用数", "{cc}"),
        ("重试", "{rs}"),
        ("overall", "{ov}"),
        ("plot", "{pl}"),
        ("char", "{ch}"),
        ("cont", "{cn}"),
        ("style", "{st}"),
        ("pace", "{pa}"),
        ("fore", "{fo}"),
        ("ai_trace", "{ai}"),
        ("sv", "{sv}"),
        ("快照字节", "{sb}"),
    ]
    align = [":---", "---:", ":---", ":---"] + [":---"] + ["---:"] * 12
    head_cells = [c[0] for c in cols]
    sep_cells = align
    header_line = "| " + " | ".join(head_cells) + " |"
    sep_line = "| " + " | ".join(sep_cells) + " |"

    def fmt_row(r: dict[str, Any]) -> str:
        return "| " + " | ".join(
            [
                c[1].format(
                    no=r["chapter_no"],
                    pc=_fmt_int(r["prose_chars"]),
                    ws=r.get("word_status") or "-",
                    dv=f"{float(r.get('deviation_pct') or 0.0):+.1f}",
                    tt=_fmt_int(r["total_tokens"]),
                    p=_fmt_int(r["prompt_tokens"]),
                    c=_fmt_int(r["completion_tokens"]),
                    cc=r["call_count"],
                    rs=r["retry_sum"],
                    ov=r["overall"],
                    pl=r["plot"],
                    ch=r["character"],
                    cn=r["continuity"],
                    st=r["style"],
                    pa=r["pacing"],
                    fo=r["foreshadowing"],
                    ai=r["ai_trace"],
                    sv=r["state_version"],
                    sb=_fmt_int(r["snapshot_bytes"]),
                )
                for c in cols
            ]
        ) + " |"

    out = [
        "## 2. 逐章明细",
        "",
        header_line,
        sep_line,
    ]
    for r in rows:
        out.append(fmt_row(r))
    out += ["", "（sv = state_version；p/c = prompt/completion；dev% = 偏离 target 百分比；status = under/in_band/over）", ""]
    return "\n".join(out)


def section_buckets(rows: list[dict[str, Any]]) -> str:
    """每 10 章分段均值。"""
    bucket_size = 10
    if not rows:
        return "## 3. 趋势分段统计\n\n无数据。\n"
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        idx = (r["chapter_no"] - 1) // bucket_size
        buckets[idx].append(r)

    header = [
        "## 3. 趋势分段统计（每 10 章一段）",
        "",
        "| 段 | 章数 | 字数均值 | total_tokens 均值 | overall | continuity | style | pacing | ai_trace | 快照字节 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines: list[str] = []
    for idx in sorted(buckets):
        members = buckets[idx]
        start = idx * bucket_size + 1
        end = (idx + 1) * bucket_size
        lines.append(
            "| {lbl} | {n} | {pc} | {tt} | {ov} | {cn} | {st} | {pa} | {ai} | {sb} |".format(
                lbl=_band_label(start, end),
                n=len(members),
                pc=_fmt_float(_mean([m["prose_chars"] for m in members]), 0),
                tt=_fmt_float(_mean([m["total_tokens"] for m in members]), 0),
                ov=_fmt_float(_mean([m["overall"] for m in members]), 1),
                cn=_fmt_float(_mean([m["continuity"] for m in members]), 1),
                st=_fmt_float(_mean([m["style"] for m in members]), 1),
                pa=_fmt_float(_mean([m["pacing"] for m in members]), 1),
                ai=_fmt_float(_mean([m["ai_trace"] for m in members]), 1),
                sb=_fmt_float(_mean([m["snapshot_bytes"] for m in members]), 0),
            )
        )
    out = header + lines + [
        "",
        "提示：用于肉眼判断连续性、风格、节奏、AI 痕迹、快照体积是否随章号衰减。",
        "",
    ]
    return "\n".join(out)


def section_issues(
    issue_rows: list[dict[str, Any]],
    db_err: str | None,
) -> str:
    if db_err:
        return f"## 4. 质量 issue 频次演化\n\n> 跳过：{db_err}\n\n"

    bucket_size = 10
    by_bucket_rule: dict[int, Counter[str]] = defaultdict(Counter)
    overall_counter: Counter[str] = Counter()
    for it in issue_rows:
        rid = it["rule_id"]
        overall_counter[rid] += 1
        ch = it["chapter_no"]
        if ch is None:
            continue
        idx = (ch - 1) // bucket_size
        by_bucket_rule[idx][rid] += 1

    out = [
        "## 4. 质量 issue 频次演化",
        "",
        f"采样：{len(issue_rows)} 条 issue（来自 quality_reports.issues_json）。",
        "",
        "### 4.1 总览 Top10（按 rule_id 出现次数）",
        "",
        "| rule_id | 出现次数 |",
        "| --- | ---: |",
    ]
    for rid, cnt in overall_counter.most_common(10):
        out.append(f"| {rid} | {cnt} |")
    out.append("")

    out.append("### 4.2 分段 Top10（每 10 章一段）")
    out.append("")
    out.append("| 段 | rule_id | 次数 |")
    out.append("| --- | --- | ---: |")
    for idx in sorted(by_bucket_rule):
        members = by_bucket_rule[idx]
        start = idx * bucket_size + 1
        end = (idx + 1) * bucket_size
        for rid, cnt in members.most_common(10):
            out.append(f"| {_band_label(start, end)} | {rid} | {cnt} |")
    out.append("")
    return "\n".join(out)


def section_anomalies(rows: list[dict[str, Any]]) -> str:
    flagged: list[tuple[dict[str, Any], list[str]]] = []
    for r in rows:
        reasons: list[str] = []
        if r["overall"] == 0:
            reasons.append("overall=0")
        if r["max_attempt"] > 1:
            reasons.append(f"max_attempt={r['max_attempt']}")
        if r["prose_chars"] < 1000:
            reasons.append(f"prose_chars<1000 ({r['prose_chars']})")
        if reasons:
            flagged.append((r, reasons))

    out = [
        "## 5. 异常清单",
        "",
        f"异常章节数：{len(flagged)} / {len(rows)}。",
        "",
        "| 章号 | overall | max_attempt | 字数 | 原因 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    if not flagged:
        out.append("| — | — | — | — | 无异常 |")
    else:
        for r, reasons in flagged:
            out.append(
                f"| {r['chapter_no']} | {r['overall']} | {r['max_attempt']} | "
                f"{_fmt_int(r['prose_chars'])} | {'; '.join(reasons)} |"
            )
    out.append("")
    return "\n".join(out)


def section_cost(rows: list[dict[str, Any]]) -> str:
    n = len(rows)
    if n == 0:
        return "## 6. 成本外推\n\n无数据可外推。\n"
    avg_tokens = _mean([r["total_tokens"] for r in rows])
    avg_prompt = _mean([r["prompt_tokens"] for r in rows])
    avg_completion = _mean([r["completion_tokens"] for r in rows])
    avg_wall = _mean([r["wall_total_s"] for r in rows])

    def extrapolate(chs: int) -> tuple[int, int, int, float]:
        return (
            int(round(avg_tokens * chs)),
            int(round(avg_prompt * chs)),
            int(round(avg_completion * chs)),
            round(avg_wall * chs, 1),
        )

    t50, p50, c50, w50 = extrapolate(50)
    t500, p500, c500, w500 = extrapolate(500)

    out = [
        "## 6. 成本外推",
        "",
        f"基线：已完成 {n} 章的均值（avg_total_tokens={_fmt_float(avg_tokens, 0)}，"
        f"avg_prompt={_fmt_float(avg_prompt, 0)}，avg_completion={_fmt_float(avg_completion, 0)}，"
        f"avg_wall_s={_fmt_float(avg_wall, 1)}）。",
        "",
        "线性外推：当前均值乘以目标章数。**仅作量级参考，未考虑上下文窗口膨胀、缓存命中、并发优化等非线性因素**。",
        "",
        "| 规模 | total_tokens | prompt | completion | 预计总耗时 (s) | 预计总耗时 (h) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| 50 章 | {_fmt_int(t50)} | {_fmt_int(p50)} | {_fmt_int(c50)} | {_fmt_float(w50, 1)} | "
        f"{_fmt_float(w50 / 3600, 2)} |",
        f"| 500 章 | {_fmt_int(t500)} | {_fmt_int(p500)} | {_fmt_int(c500)} | {_fmt_float(w500, 1)} | "
        f"{_fmt_float(w500 / 3600, 2)} |",
        "",
    ]
    return "\n".join(out)


def section_agent_tokens(
    agent_rows: list[AgentTokenRow],
    null_skipped: int,
    err: str | None,
) -> str:
    """第 7 节：按环节（agent.name）聚合 token / 调用 / 延迟 / 重试。

    表按 total 降序。底部给出一行结论："占总 token 最高的环节是 X，
    占整体 Y%"，便于一眼定位烧钱环节。
    """
    head = "## 7. 按环节 token 计量"
    if err:
        return f"{head}\n\n> 跳过：{err}\n\n"
    if not agent_rows:
        note = (
            f"（另有 {null_skipped} 条 agent_id 为空/NULL 的日志被过滤）"
            if null_skipped else ""
        )
        return f"{head}\n\n无 agent 聚合数据。{note}\n\n"

    grand_total = sum(int(r["total"] or 0) for r in agent_rows)
    out: list[str] = [
        head,
        "",
        "数据源：``ai_call_logs`` × ``agents``，按 ``agents.name`` 聚合；"
        "agent_id 为 NULL / 空的日志已过滤（条数见底部）。",
        "",
        "| agent | calls | prompt | completion | total | total 占比 | avg_latency_ms | retry_sum |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in agent_rows:
        total = int(r["total"] or 0)
        pct = (total / grand_total * 100.0) if grand_total > 0 else 0.0
        out.append(
            "| {name} | {calls} | {p} | {c} | {tt} | {pct:.1f}% | {lat} | {rs} |".format(
                name=r["agent_name"],
                calls=r["calls"],
                p=_fmt_int(r["prompt"]),
                c=_fmt_int(r["completion"]),
                tt=_fmt_int(total),
                pct=pct,
                lat=int(r["avg_latency_ms"] or 0),
                rs=int(r["retry_sum"] or 0),
            )
        )

    # 结论行
    top = agent_rows[0]
    top_total = int(top["total"] or 0)
    top_pct = (top_total / grand_total * 100.0) if grand_total > 0 else 0.0
    out += [
        "",
        f"**结论**：占总 token 最高的环节是 `{top['agent_name']}`，"
        f"占整体 {top_pct:.1f}%（{_fmt_int(top_total)} / {_fmt_int(grand_total)} token）。",
    ]
    if null_skipped:
        out.append(
            f"另有 {null_skipped} 条 agent_id 为空 / NULL 的 ai_call_logs 行被过滤，未计入聚合。"
        )
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def build_report(
    data_dir: Path,
    db_path: Path | None,
) -> tuple[str, dict[str, Any]]:
    progress = load_progress(data_dir)
    chapters, skipped = load_chapters(data_dir)
    rows = _ch_rows(chapters, progress)

    issue_rows, issue_err = load_quality_reports(db_path)
    _state_rows, state_err = load_story_state_growth(db_path)
    agent_rows, agent_null_skipped, agent_err = load_agent_token_stats(db_path)

    project_id = progress.get("project_id", "<unknown>")
    started = progress.get("started_at", "<unknown>")
    updated = progress.get("updated_at", "<unknown>")

    parts: list[str] = []
    parts.append("# M1 一致性衰减观测报告 —— 数据分析")
    parts.append("")
    parts.append(f"- 项目 ID：`{project_id}`")
    parts.append(f"- 起始时间：`{started}`")
    parts.append(f"- 最近更新：`{updated}`")
    parts.append(f"- 数据目录：`{data_dir}`")
    parts.append(
        f"- 已加载章节文件：{len(chapters)} 个；跳过：{len(skipped)} 个"
        + (f"（{', '.join(skipped)}）" if skipped else "")
    )
    if db_path is not None:
        parts.append(f"- DB：`{db_path}`")
        if issue_err:
            parts.append(f"  - issue 统计：{issue_err}")
        if state_err:
            parts.append(f"  - 快照体积：{state_err}")
        if agent_err:
            parts.append(f"  - agent 计量：{agent_err}")
        else:
            parts.append(
                f"  - agent 计量：{len(agent_rows)} 个环节，已过滤"
                f" {agent_null_skipped} 条空 agent_id 行"
            )
    parts.append("")
    parts.append("---")
    parts.append("")

    parts.append(section_overall(rows, progress))
    parts.append(section_per_chapter(rows))
    parts.append(section_buckets(rows))
    parts.append(section_issues(issue_rows, issue_err))
    parts.append(section_anomalies(rows))
    parts.append(section_cost(rows))
    parts.append(section_agent_tokens(agent_rows, agent_null_skipped, agent_err))

    diag: dict[str, Any] = {
        "project_id": project_id,
        "loaded_chapters": len(chapters),
        "skipped_files": skipped,
        "db_path": str(db_path) if db_path else None,
        "issue_err": issue_err,
        "state_err": state_err,
        "agent_token_err": agent_err,
        "agent_token_rows": len(agent_rows),
        "agent_token_null_skipped": agent_null_skipped,
    }
    return "\n".join(parts), diag


def main() -> int:
    parser = argparse.ArgumentParser(
        description="M1 长跑一致性衰减观测报告 —— 数据分析",
    )
    parser.add_argument(
        "--data-dir",
        default="data/m1_run",
        help="M1 长跑数据目录（默认 data/m1_run）",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="novelos.db 路径（可选，未指定则跳过 DB 相关统计）",
    )
    parser.add_argument(
        "--out",
        default="docs/evaluation/m1-data.md",
        help="报告输出文件（默认 docs/evaluation/m1-data.md）",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    db_path = Path(args.db).resolve() if args.db else None
    out_path = Path(args.out).resolve()

    text, diag = build_report(data_dir, db_path)

    # 写入文件（仅写 --out 指定路径）
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    # 同时打印到 stdout
    print(text)
    print("---")
    print(f"diag: {json.dumps(diag, ensure_ascii=False, default=str)}")
    print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
