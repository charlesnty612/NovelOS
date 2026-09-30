"""inspect_stalled_runs —— 卡死（PAUSED / RUNNING / PENDING）workflow run 只读巡检。

目的：让主会话在**不写库**的前提下判断哪些 run 可以清理。核心产出不是「有几个
PAUSED」，而是**每个 run 在人工审批节点上到底压着什么实质内容**——一份空的
review_report 与一份带 3 条 blocking error 的 review_report，处置决策完全相反。

默认只读
--------

全程 ``sqlite3.connect('file:<db>?mode=ro', uri=True)``：不 UPDATE / DELETE、不
建表、不写 sidecar、不碰 WAL。``--apply`` **不实现任何删除或取消**（见
``apply_cleanup`` 的 ``NotImplementedError``）——取消是终态操作，由主会话另行裁决。

409 影响口径（实测核对，勿照抄旧说法）
--------------------------------------

本脚本**不**把「存在 PAUSED run」当成「重新发起写章会 409」。真实守卫为
``packages/core/api/routers/workflows/control.py:52`` 的
``_check_active_run_for_chapter``，两条分支的 SQL 谓词都是
``status IN ('RUNNING','PENDING')``（control.py:78 / control.py:89），**不含
PAUSED**；引擎侧兜底的 0017 部分唯一索引同谓词
（``database/migrations/0017_unique_constraints.sql:46-48``，
``WHERE status IN ('RUNNING','PENDING') AND chapter_id IS NOT NULL``，且注释
line 42-45 明确「不覆盖 PAUSED」是刻意设计，为 resume 留出生效空间）。

因此 ``blocks_new_start`` 只有在同章存在 **RUNNING/PENDING** 行时才为真。PAUSED
run 不阻止新 start，只影响 run 列表展示与 resume 决议。

取消可行性（cancel 端点对 PAUSED 返 409）
-------------------------------------------

``POST /runs/{run_id}/cancel`` 只接受 RUNNING：
``packages/core/api/routers/workflows/control.py:780-781`` 与
``packages/core/workflow_runtime/engine.py:644-646`` 均写明 status ∈
{COMPLETED, FAILED, CANCELLED, PAUSED} → 409
（engine 抛 ``ValueError(... "must be RUNNING to cancel")``）。故
``cancel_api_supported`` 对 PAUSED run 恒为 False——**没有「调 API 取消」这条路**，
PAUSED 的唯一出口是 resume 后走驳回/批准决议，或由主会话在只读导出留档后另行处置。

隐私
----

``--export`` 落盘内容含完整 ``checkpoint_json``（正文草稿、角色卡、评审意见）。
导出文件属运行期产物，**不得提交进版本库**；若确需入库须先按项目纪律脱敏。

用法示例
--------

::

    python scripts/inspect_stalled_runs.py
    python scripts/inspect_stalled_runs.py --db data/novelos.db --json
    python scripts/inspect_stalled_runs.py --export tmp/stalled_runs_export.json

退出码
------

* ``0`` —— 巡检完成（无论是否发现卡死 run）；
* ``2`` —— 参数错误 / DB 不存在 / 非法 SQLite。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 「活动」= 未达终态。COMPLETED / FAILED / CANCELLED 是终态，不在巡检范围。
ACTIVE_STATUSES = ("PAUSED", "RUNNING", "PENDING")

# 真实 409 守卫的谓词（control.py:78/89 + 0017:48）：**不含 PAUSED**。
# 名字里带 active 指的是「RUNNING/PENDING」，不是「所有非终态」。
BLOCKING_STATUSES = ("RUNNING", "PENDING")

PAUSE_PAYLOAD_KEY = "__pause_payload__"

# 文本摘要在报告里的最大宽度（超出截断）。
_SUMMARY_TEXT_LIMIT = 180
_MAX_RULE_IDS_SHOWN = 6


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class RunRow:
    """workflow_runs 一行 + LEFT JOIN 出的章节 / 项目上下文。"""

    run_id: str
    workflow_id: str
    chapter_id: str | None
    status: str
    current_node: str | None
    started_at: str | None
    ended_at: str | None
    retry_count: int
    error: str | None
    instance_id: str | None
    checkpoint_json: str | None
    project_name: str | None = None
    chapter_number: int | None = None
    chapter_title: str | None = None
    chapter_status: str | None = None
    project_name_source: str = "join"

    @property
    def chapter_label(self) -> str:
        if self.chapter_id is None:
            return "(无章节 / 非章节任务)"
        if self.chapter_number is None:
            return f"ch? {self.chapter_id}"
        return f"ch{self.chapter_number} {self.chapter_title or '(无标题)'}"


@dataclass
class PauseSummary:
    """人工审批节点上待审批内容的实质摘要。

    存在意义：主会话判断「能不能安全 cancel」的唯一依据就是这里。空报告与有报告
    的差别很大，所以必须给可读内容而不是 true/false。
    """

    payload_found: bool = False
    payload_kind: str = "none"
    has_substantive: bool = False
    headline: str = ""
    facts: list[str] = field(default_factory=list)
    blocking_rule_ids: list[str] = field(default_factory=list)
    warning_rule_ids: list[str] = field(default_factory=list)
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload_found": self.payload_found,
            "payload_kind": self.payload_kind,
            "has_substantive": self.has_substantive,
            "headline": self.headline,
            "facts": self.facts,
            "blocking_rule_ids": self.blocking_rule_ids,
            "warning_rule_ids": self.warning_rule_ids,
            "excerpt": self.excerpt,
        }


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int = _SUMMARY_TEXT_LIMIT) -> str:
    """单行截断（空白压平），用于报告可读性。"""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def _parse_ts(raw: str | None) -> datetime | None:
    """ISO 时间串 → aware datetime；解析失败返回 None（不抛）。

    ``started_at`` 是 ``now_iso()`` 产物（带 ``+00:00``）。naive 串按 UTC 处理——
    库里存的都是 UTC，猜本地时区只会引入错误。
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _humanize_duration(delta_seconds: float) -> str:
    """秒 → 人类可读时长（如 ``29d 05h``）。"""
    total = int(max(0.0, delta_seconds))
    days, rem = divmod(total, 86400)
    hours, minutes = divmod(rem, 3600)
    minutes //= 60
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _rule_ids_from(items: Any) -> list[str]:
    """从 errors / warnings 混合形态列表里抽 rule_id。

    两种形态都见过：结构化 ``{"rule_id": ..., "message": ...}`` 与字符串
    ``"[W-LEN-DEVIATION] ..."``。两种都认，抽不出就退化成短标签。
    """
    out: list[str] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if isinstance(item, dict):
            rid = item.get("rule_id")
            if isinstance(rid, str) and rid:
                out.append(rid)
                continue
            msg = item.get("message")
            if isinstance(msg, str) and msg.startswith("["):
                out.append(msg[1:].split("]", 1)[0])
            else:
                out.append("(未标 rule_id)")
        elif isinstance(item, str):
            if item.startswith("["):
                out.append(item[1:].split("]", 1)[0])
            elif item:
                out.append(_truncate(item, 40))
    # 保序去重
    seen: set[str] = set()
    dedup: list[str] = []
    for rid in out:
        if rid not in seen:
            seen.add(rid)
            dedup.append(rid)
    return dedup


def _count_list(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


# ---------------------------------------------------------------------------
# 审批内容解析
# ---------------------------------------------------------------------------


def _summarize_review_payload(payload: dict[str, Any]) -> PauseSummary:
    """``author_review`` / ``chapter-review`` 审批点的 review_report + critic_report。"""
    summary = PauseSummary(payload_found=True, payload_kind="chapter_review")

    review = payload.get("review_report")
    critic = payload.get("critic_report")
    deep = payload.get("deep_review_report")

    review_ok = isinstance(review, dict) and bool(review)
    critic_issues = critic.get("issues") if isinstance(critic, dict) else None
    critic_ok = isinstance(critic, dict) and bool(critic_issues or critic.get("overall_comment"))
    deep_ok = isinstance(deep, dict) and bool(deep)

    if review_ok:
        wc = review.get("word_count")
        target = review.get("target_word_count")
        band = review.get("word_band")
        within = review.get("within_range")
        dev = review.get("deviation_pct")
        version = review.get("draft_version")

        band_txt = ""
        if isinstance(band, dict) and band.get("low") is not None and band.get("high") is not None:
            band_txt = f"，字数带 {band.get('low')}~{band.get('high')}"
        dev_txt = f"，偏离 {dev:+.1f}%" if isinstance(dev, (int, float)) else ""
        within_txt = "" if within is None else ("，在带内" if within else "，**超带**")
        summary.facts.append(
            f"review_report：被审稿 {wc} 字 / 目标 {target} 字{band_txt}{dev_txt}{within_txt}"
            + (f"，draft v{version}" if version is not None else "")
        )

        errors = review.get("errors")
        warnings = review.get("warnings")
        summary.blocking_rule_ids = _rule_ids_from(errors)
        summary.warning_rule_ids = _rule_ids_from(warnings)
        summary.facts.append(f"errors {len(errors) if isinstance(errors, list) else 0} 条 / "
                             f"warnings {len(warnings) if isinstance(warnings, list) else 0} 条")

        hits = review.get("ai_pattern_hits")
        if isinstance(hits, list) and hits:
            summary.facts.append(f"AI 模式命中 {len(hits)} 条：{_truncate(', '.join(map(str, hits)), 90)}")
        fw = review.get("forbidden_word_hits")
        if isinstance(fw, list) and fw:
            summary.facts.append(f"禁用词命中 {len(fw)} 个：{_truncate(', '.join(map(str, fw)), 90)}")
        genre = review.get("genre_check")
        if isinstance(genre, dict) and genre:
            summary.facts.append(f"题材核销：{_truncate(json.dumps(genre, ensure_ascii=False), 110)}")
    else:
        summary.facts.append("review_report：缺失或为空")

    if isinstance(critic, dict) and critic:
        n_issues = _count_list(critic.get("issues"))
        comment = critic.get("overall_comment")
        status = payload.get("critic_status")
        status_txt = f"，critic_status={status}" if status else ""
        summary.facts.append(f"critic_report：issues {n_issues} 条{status_txt}")
        if comment:
            summary.excerpt = _truncate(comment)
    elif critic is not None:
        summary.facts.append("critic_report：无内容")
    else:
        summary.facts.append("critic_report：不存在")

    if deep is not None:
        summary.facts.append(
            f"deep_review_report：{'有内容' if deep_ok else '空/None'}"
            + (f"（status={payload.get('deep_review_status')}）" if payload.get("deep_review_status") else "")
        )

    if not summary.excerpt and summary.blocking_rule_ids:
        first = review.get("errors")[0] if isinstance(review, dict) and review.get("errors") else None
        if isinstance(first, dict) and first.get("message"):
            summary.excerpt = _truncate(str(first["message"]))

    summary.has_substantive = bool(review_ok or critic_ok or deep_ok)
    if summary.has_substantive:
        n_block = len(summary.blocking_rule_ids)
        summary.headline = (
            f"有实质内容：{len(summary.facts)} 项事实，error 规则 {n_block} 个"
            + (f"（{_truncate(', '.join(summary.blocking_rule_ids[:_MAX_RULE_IDS_SHOWN]))}）" if n_block else "")
        )
    else:
        summary.headline = "**空报告**：无 review_report / 无 critic issues——取消的信息损失极小"
    return summary


def _summarize_init_payload(payload: dict[str, Any]) -> PauseSummary:
    """project-init 类节点（``character_designer`` 等）的阶段产出。"""
    summary = PauseSummary(payload_found=True, payload_kind="project_init_stage")

    stage = payload.get("stage")
    idx = payload.get("stage_index")
    total = payload.get("stages_total")
    stage_txt = str(stage) if stage is not None else "(未知阶段)"
    if idx is not None and total is not None:
        stage_txt = f"{stage_txt}（第 {idx}/{total} 阶段）"
    summary.facts.append(f"阶段：{stage_txt}")

    if payload.get("degraded"):
        summary.facts.append("**降级产出**（degraded=true）：内容质量不可信")

    draft = payload.get("draft")
    if isinstance(draft, dict) and draft:
        items_key = next((k for k in ("characters", "locations", "factions", "world_rules") if draft.get(k)), None)
        if items_key:
            items = draft[items_key]
            names = [
                str(it.get("name") or it.get("title") or "?")
                for it in items
                if isinstance(it, dict)
            ]
            summary.facts.append(f"产出实体 {len(items)} 个（{items_key}）：{_truncate('、'.join(names), 110)}")
            summary.has_substantive = bool(items)
        else:
            summary.facts.append(f"draft 非空，字段：{_truncate(', '.join(sorted(draft.keys())), 110)}")
            summary.has_substantive = True
    else:
        summary.facts.append("draft：缺失或为空")

    if summary.has_substantive:
        summary.headline = f"有实质内容：{summary.facts[0]}" + (
            f"，{summary.facts[1]}" if len(summary.facts) > 1 else ""
        )
    else:
        summary.headline = "**空产出**：无实体草稿"
    return summary


def summarize_pause_payload(node: str | None, payload: Any) -> PauseSummary:
    """按节点类型分派实质内容摘要。未知形态如实报 ``payload_found`` 与键名。

    这里刻意不做「猜」：认不出来的 payload 报 ``unknown`` 并把键名列出来，
    让主会话知道需要人工看，而不是被静默归成「空报告」。
    """
    if not isinstance(payload, dict):
        return PauseSummary(payload_found=False, payload_kind="none", headline="无 __pause_payload__")

    if isinstance(payload.get("review_report"), dict) or payload.get("stage") == "chapter-review":
        return _summarize_review_payload(payload)
    if "draft" in payload or "stages_total" in payload or "stage_index" in payload:
        return _summarize_init_payload(payload)

    summary = PauseSummary(
        payload_found=True,
        payload_kind="unknown",
        headline=f"未识别的审批 payload，键：{_truncate(', '.join(sorted(payload.keys())), 140)}",
    )
    summary.facts.append("需要人工确认该 payload 是否含待审批内容")
    return summary


def summarize_checkpoint(current_node: str | None, checkpoint_json: str | None) -> PauseSummary:
    """解析 checkpoint_json → :class:`PauseSummary`。

    容错：checkpoint 缺失 / 非法 JSON / 当前节点无 payload，都返回可读摘要而不抛。
    """
    if not checkpoint_json:
        return PauseSummary(payload_found=False, payload_kind="none", headline="checkpoint_json 为空")
    try:
        ckpt = json.loads(checkpoint_json)
    except (ValueError, TypeError) as exc:
        return PauseSummary(
            payload_found=False,
            payload_kind="unparseable",
            headline=f"checkpoint_json 解析失败：{_truncate(str(exc), 120)}",
        )
    if not isinstance(ckpt, dict):
        return PauseSummary(payload_found=False, payload_kind="unparseable", headline="checkpoint_json 顶层不是对象")

    node_state = ckpt.get(current_node) if current_node else None
    if not isinstance(node_state, dict):
        keys_txt = _truncate(", ".join(sorted(ckpt.keys())), 100)
        return PauseSummary(
            payload_found=False,
            payload_kind="none",
            headline=f"checkpoint 内无节点 {current_node!r} 状态（键：{keys_txt}）",
        )
    return summarize_pause_payload(current_node, node_state.get(PAUSE_PAYLOAD_KEY))


# ---------------------------------------------------------------------------
# 只读查询
# ---------------------------------------------------------------------------


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """只读连接。``mode=ro`` 由 SQLite 自己保证写不进去。

    用 URI + mode=ro 而不是拿可写连接做体检：万一脚本后续误加写操作，会在
    第一次写时直接抛 ``OperationalError: attempt to write a readonly database``，
    而不是安静地改数据。
    """
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_active_runs(db_path: Path) -> list[RunRow]:
    """取全部 PAUSED / RUNNING / PENDING run（含章节 / 项目上下文），按 started_at 升序。"""
    sql = """
        SELECT r.run_id, r.workflow_id, r.chapter_id, r.status, r.current_node,
               r.started_at, r.ended_at, r.retry_count, r.error, r.instance_id,
               r.checkpoint_json,
               p.name AS project_name,
               c.number AS chapter_number, c.title AS chapter_title, c.status AS chapter_status
        FROM workflow_runs r
        LEFT JOIN chapters c ON c.chapter_id = r.chapter_id
        LEFT JOIN projects p ON p.project_id = c.project_id
        WHERE r.status IN ('PAUSED', 'RUNNING', 'PENDING')
        ORDER BY r.started_at ASC
    """
    conn = connect_readonly(db_path)
    try:
        return [_build_run_row(row) for row in conn.execute(sql)]
    finally:
        conn.close()


def _init_project_hint(checkpoint_json: str | None) -> str | None:
    """project-init 类 run（``chapter_id IS NULL``）的项目名兜底。

    这类 run 无 ``chapter_id``，JOIN ``chapters→projects`` 必然落空；但
    ``checkpoint_json.brief.title`` 存着立项书标题（实机 dev 库实测可还原出
    项目名）。没有它，报告里就是一行「(项目缺失)」——主会话无法判断这条卡死的
    角色设计 run 属于哪本书，也就无从决定它该不该清。

    只取 ``title``：``brief`` 里还有 genre / logline / author_notes，那些是内容
    面，不适合当项目标识。
    """
    if not checkpoint_json:
        return None
    try:
        ckpt = json.loads(checkpoint_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(ckpt, dict):
        return None
    brief = ckpt.get("brief")
    if not isinstance(brief, dict):
        return None
    title = brief.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def _build_run_row(row: sqlite3.Row) -> RunRow:
    """DB 行 → RunRow，含 project-init run 的项目名兜底。"""
    project_name = row["project_name"]
    source = "join"
    if project_name is None:
        hint = _init_project_hint(row["checkpoint_json"])
        if hint:
            project_name = hint
            source = "checkpoint_brief_title"
    return RunRow(
        run_id=row["run_id"],
        workflow_id=row["workflow_id"],
        chapter_id=row["chapter_id"],
        status=row["status"],
        current_node=row["current_node"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        retry_count=row["retry_count"] or 0,
        error=row["error"],
        instance_id=row["instance_id"],
        checkpoint_json=row["checkpoint_json"],
        project_name=project_name,
        chapter_number=row["chapter_number"],
        chapter_title=row["chapter_title"],
        chapter_status=row["chapter_status"],
        project_name_source=source,
    )


def compute_stall(row: RunRow, now: datetime) -> float | None:
    """run 停留时长（秒）。``started_at`` 缺失 / 解析失败 → None。"""
    started = _parse_ts(row.started_at)
    if started is None:
        return None
    return (now - started).total_seconds()


def cancel_api_supported(status: str) -> bool:
    """cancel 端点是否接受该状态（control.py:780-781 + engine.py:644-646：仅 RUNNING）。"""
    return status == "RUNNING"


# ---------------------------------------------------------------------------
# 报告组装
# ---------------------------------------------------------------------------


def build_report(rows: list[RunRow], db_path: Path, now: datetime) -> dict[str, Any]:
    """组装机器可读报告（``--json`` 与 Markdown 同源，保证两个视图不漂移）。"""
    run_reports: list[dict[str, Any]] = []
    for row in rows:
        stall = compute_stall(row, now)
        summary = summarize_checkpoint(row.current_node, row.checkpoint_json)
        run_reports.append(
            {
                "run_id": row.run_id,
                "workflow_id": row.workflow_id,
                "chapter_id": row.chapter_id,
                "status": row.status,
                "current_node": row.current_node,
                "project_name": row.project_name,
                "project_name_source": row.project_name_source,
                "chapter_number": row.chapter_number,
                "chapter_title": row.chapter_title,
                "chapter_label": row.chapter_label,
                "chapter_status": row.chapter_status,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
                "stalled_seconds": stall,
                "stalled_human": _humanize_duration(stall) if stall is not None else None,
                "retry_count": row.retry_count,
                "error": _truncate(row.error, 200) if row.error else None,
                "instance_id": row.instance_id,
                "blocks_new_start": row.status in BLOCKING_STATUSES and row.chapter_id is not None,
                "cancel_api_supported": cancel_api_supported(row.status),
                "pause_summary": summary.to_dict(),
                # 完整 checkpoint_json 原样带出：--export 要求「含完整
                # checkpoint_json」留档。--json 输出也会带（体量大但机器可读优先）。
                "checkpoint_json": row.checkpoint_json,
            }
        )

    # 按 chapter_id 聚合（None 单独成组）——409 影响是「章级」而非「run 级」。
    groups: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
    for item in run_reports:
        groups.setdefault(str(item["chapter_id"]), []).append(item)

    chapter_reports = []
    for chapter_id, items in groups.items():
        blocking = [it for it in items if it["blocks_new_start"]]
        chapter_reports.append(
            {
                "chapter_id": None if chapter_id == "None" else chapter_id,
                "project_name": items[0]["project_name"],
                "project_name_source": items[0]["project_name_source"],
                "chapter_label": items[0]["chapter_label"],
                "chapter_status": items[0]["chapter_status"],
                "run_count": len(items),
                "status_breakdown": {s: sum(1 for it in items if it["status"] == s) for s in ACTIVE_STATUSES
                                     if any(it["status"] == s for it in items)},
                "blocking_runs": [it["run_id"] for it in blocking],
                "blocks_new_start": bool(blocking),
                "any_substantive": any(it["pause_summary"]["has_substantive"] for it in items),
                "run_ids": [it["run_id"] for it in items],
            }
        )

    return {
        "db_path": str(db_path),
        "generated_at": now.isoformat(),
        "read_only": True,
        "guard_semantics": {
            "source": "packages/core/api/routers/workflows/control.py:52 _check_active_run_for_chapter",
            "db_index": "database/migrations/0017_unique_constraints.sql:46 idx_workflow_runs_active",
            "predicate": "status IN ('RUNNING','PENDING')",
            "paused_blocks_new_start": False,
            "cancel_accepts_paused": False,
        },
        "active_statuses": list(ACTIVE_STATUSES),
        "total_active_runs": len(run_reports),
        "chapters_blocking_new_start": [c["chapter_id"] for c in chapter_reports if c["blocks_new_start"]],
        "runs": run_reports,
        "chapters": chapter_reports,
    }


def _proj_cell(r: dict[str, Any]) -> str:
    """项目名单元格。checkpoint 兜底还原的加 ``*`` 标记。

    ``快穿之价签*`` 与 ``快穿之价签`` 不是一回事：前者是从
    ``checkpoint_json.brief.title`` 猜的（project-init run 无 chapter_id，
    JOIN 不出来），未经 projects 表核对。加标记是为了让主会话知道这条依据弱一档。
    """
    name = r.get("project_name")
    if not name:
        return "(项目缺失)"
    if r.get("project_name_source") == "checkpoint_brief_title":
        return f"{name}*"
    return str(name)


def render_markdown(report: dict[str, Any]) -> str:
    """人类可读报告。保持单报告形态：表格 + 逐 run 实质内容 + 409 影响。"""
    out: list[str] = []
    out.append("# 卡死 run 巡检报告（只读）")
    out.append("")
    out.append(f"- 库：`{report['db_path']}`")
    out.append(f"- 生成时刻：{report['generated_at']}")
    out.append(f"- 活动 run 合计：**{report['total_active_runs']}**（口径 {', '.join(report['active_statuses'])}）")
    gs = report["guard_semantics"]
    out.append(f"- 409 守卫真实谓词：`{gs['predicate']}`（{gs['source']}）")
    out.append(f"- **PAUSED 是否阻止新 start：{'是' if gs['paused_blocks_new_start'] else '否'}**"
               f"；DB 索引同谓词（{gs['db_index']}）")
    out.append(f"- **cancel 端点是否接受 PAUSED：{'是' if gs['cancel_accepts_paused'] else '否'}**")
    out.append("")

    if not report["runs"]:
        out.append("**无活动 run。**")
        return "\n".join(out)

    out.append("## 1. 概览")
    out.append("")
    out.append(
        "| run_id | 章 | 状态 | 节点 | 停留 | retry | error | 有实质内容 | 阻止新 start |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|")
    for r in report["runs"]:
        out.append(
            "| `{run_id}` | {proj} / {ch} | {status} | {node} | {stalled} | {retry} | {err} | {sub} | {blk} |".format(
                run_id=r["run_id"],
                proj=_proj_cell(r),
                ch=r["chapter_label"],
                status=r["status"],
                node=r["current_node"] or "-",
                stalled=r["stalled_human"] or "-",
                retry=r["retry_count"],
                err=_truncate(r["error"], 40) if r["error"] else "-",
                sub="是" if r["pause_summary"]["has_substantive"] else "**否（空）**",
                blk="**是**" if r["blocks_new_start"] else "否",
            )
        )
    out.append("")

    out.append("## 2. 逐 run 待审批实质内容")
    out.append("")
    for r in report["runs"]:
        s = r["pause_summary"]
        out.append(f"### `{r['run_id']}` — {r['chapter_label']}（{_proj_cell(r)}）")
        out.append("")
        out.append(f"- 状态 / 节点：`{r['status']}` @ `{r['current_node']}`，停留 {r['stalled_human'] or '-'}，"
                   f"起于 {r['started_at']}")
        if r["error"]:
            out.append(f"- error：`{_truncate(r['error'], 300)}`")
        out.append(f"- 审批内容：**{s['headline']}**")
        for fact in s["facts"]:
            out.append(f"  - {fact}")
        if s["blocking_rule_ids"]:
            out.append(f"  - error rule_id：{', '.join(s['blocking_rule_ids'])}")
        if s["warning_rule_ids"]:
            shown = ", ".join(s["warning_rule_ids"][:_MAX_RULE_IDS_SHOWN])
            more = len(s["warning_rule_ids"]) - _MAX_RULE_IDS_SHOWN
            out.append(f"  - warning rule_id：{shown}" + (f"（+{more} 个）" if more > 0 else ""))
        if s["excerpt"]:
            out.append(f"  - 评审意见摘录：「{s['excerpt']}」")
        out.append(f"- cancel 端点支持：{'是' if r['cancel_api_supported'] else '**否**（PAUSED 取消返 409）'}")
        out.append("")

    out.append("## 3. 对「重新发起写章 409」的影响（按真实守卫口径）")
    out.append("")
    blocking_chapters = report["chapters_blocking_new_start"]
    if blocking_chapters:
        out.append(f"以下章**确实**会让新 start 撞 409：{', '.join(str(c) for c in blocking_chapters)}")
    else:
        out.append("**没有任何章会被 409 拦住**——本次全部活动 run 都是 PAUSED，"
                   "而守卫只判 `RUNNING`/`PENDING`（API 层 SQL + 0017 部分唯一索引同谓词）。")
    out.append("")
    out.append("| 章 | 项目 | 章状态 | 活动 run 数 | 构成 | 阻止新 start | 含实质内容 |")
    out.append("|---|---|---|---|---|---|---|")
    out.append("")
    out.append("> 项目名带 `*` = 由 `checkpoint_json.brief.title` 兜底还原（project-init run 无 "
               "chapter_id，JOIN 不出项目），未经 projects 表核对。")
    for c in report["chapters"]:
        out.append(
            "| {ch} | {proj} | {cst} | {n} | {bd} | {blk} | {sub} |".format(
                ch=c["chapter_label"],
                proj=_proj_cell(c),                cst=c["chapter_status"] or "-",
                n=c["run_count"],
                bd=", ".join(f"{k}×{v}" for k, v in c["status_breakdown"].items()),
                blk="**是**" if c["blocks_new_start"] else "否",
                sub="是" if c["any_substantive"] else "**否**",
            )
        )
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 导出 / apply（不实现）
# ---------------------------------------------------------------------------


def export_rows(report: dict[str, Any], path: Path) -> Path:
    """把活动 run 完整行（含完整 checkpoint_json）导出成 JSON 留档。

    导出内容含正文草稿 / 角色卡 / 评审意见原文。落盘到版本库工作区内且未被
    ``.gitignore`` 覆盖时向 stderr 报警——这不是阻断（留档是正当用途），但必须
    让操作者看见，避免下次 ``git add -A`` 把草稿一起收进去。
    """
    payload = {
        "db_path": report["db_path"],
        "generated_at": report["generated_at"],
        "read_only": True,
        "total_active_runs": report["total_active_runs"],
        "guard_semantics": report["guard_semantics"],
        "chapters": report["chapters"],
        "runs": [
            {
                "run_id": r["run_id"],
                "workflow_id": r["workflow_id"],
                "chapter_id": r["chapter_id"],
                "status": r["status"],
                "current_node": r["current_node"],
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "retry_count": r["retry_count"],
                "error": r["error"],
                "instance_id": r["instance_id"],
                "blocks_new_start": r["blocks_new_start"],
                "cancel_api_supported": r["cancel_api_supported"],
                "pause_summary": r["pause_summary"],
                "checkpoint_json": r.get("checkpoint_json"),
            }
            for r in report["runs"]
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    warn_if_inside_repo(path)
    return path


def warn_if_inside_repo(path: Path) -> None:
    """导出路径落在 git 工作区内且未被忽略时，向 stderr 提醒。

    只提醒不阻断：留档是正当用途，位置由主会话定。但内容是正文草稿与角色卡，
    进版本库就等于内容资产与软件仓混流（违反三仓分离），必须让它在终端露一次脸。
    """
    try:
        resolved = path.resolve()
        repo_root = Path(__file__).resolve().parents[1]
    except OSError:
        return
    try:
        rel = resolved.relative_to(repo_root)
    except ValueError:
        return  # 工作区外，无需提醒
    print(
        f"WARNING: 导出落在版本库工作区内（{rel.as_posix()}）——内含正文草稿 / 角色卡原文，"
        f"确认未被 .gitignore 覆盖或用完即清，避免 git add -A 收进软件仓。",
        file=sys.stderr,
    )


def apply_cleanup(rows: list[RunRow], *, confirmed_by: str | None) -> None:
    """**故意不实现**。

    取消 / 删除是终态操作。本工具的职责边界是「只读巡检 + 留档」：
    主会话读完实质内容摘要后自行决策，走 API 或人工处置。

    在此显式抛 ``NotImplementedError`` 而不是留一个空实现或 ``pass``——那样将来
    有人加一行 ``UPDATE`` 就会静默生效，且看不出这一步是被裁决过的。
    """
    raise NotImplementedError(
        "apply_cleanup 未实现：取消/删除 workflow run 是终态操作，"
        "由主会话依据本报告的「待审批实质内容」另行决策并留痕。"
        "本工具只做只读巡检与 --export 留档。"
        + (f"（收到的 confirmed_by={confirmed_by!r} 不会改变这一点）" if confirmed_by else "")
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="只读巡检卡死（PAUSED/RUNNING/PENDING）workflow run（不实现任何取消/删除）",
    )
    parser.add_argument(
        "--db", type=Path, default=Path("data/novelos.db"), help="SQLite 库路径（默认 data/novelos.db）"
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="输出机器可读 JSON")
    parser.add_argument("--export", type=Path, default=None, help="把活动 run 完整行（含 checkpoint_json）导出到该路径")
    parser.add_argument("--apply", action="store_true", help="保留开关：取消/删除由主会话另行决策，本工具不实现")
    parser.add_argument("--confirmed-by", default=None, help="配合 --apply 的裁决留痕字段（本工具不使用）")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"ERROR: DB 不存在：{args.db}", file=sys.stderr)
        return 2

    try:
        rows = fetch_active_runs(args.db)
    except sqlite3.DatabaseError as exc:
        print(f"ERROR: 打不开 / 非法 SQLite：{exc}", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    report = build_report(rows, args.db, now)

    if args.apply:
        try:
            apply_cleanup(rows, confirmed_by=args.confirmed_by)
        except NotImplementedError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 3

    if args.export:
        path = export_rows(report, args.export)
        print(f"导出完成（只读留档）：{path}  共 {report['total_active_runs']} 条", file=sys.stderr)

    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
