"""Workflow runs 查询辅助（Sprint 4-A + V1.5 越层整改）。

- :func:`list_runs(db_path, project_id)` → 列出某项目的所有 workflow_runs（按 started_at DESC）。
- :func:`get_run(db_path, run_id)` → 单 run + 节点明细（input/output/prompt_version/latency/error）。
- :func:`get_workflow_name_for_run(db_path, run_id)` → 反查 run 对应的 workflow 名
  （JOIN workflow_runs + workflows）；被 workflows router 的 resume 端点调用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _parse_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _get_connection(db_path: str | Path):
    from packages.core.db import get_connection

    return get_connection(db_path)


def list_runs(db_path: str | Path, project_id: str) -> list[dict]:
    """返回 project 下全部 workflow_runs（按 started_at DESC），不含节点明细。"""
    conn = _get_connection(db_path)
    try:
        # 通过 chapter→project 反查
        rows = conn.execute(
            """
            SELECT wr.*
            FROM workflow_runs wr
            LEFT JOIN chapters c ON c.chapter_id = wr.chapter_id
            WHERE c.project_id = ? OR wr.chapter_id IS NULL
            ORDER BY wr.started_at DESC
            """,
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_run(r) for r in rows]


def get_run(db_path: str | Path, run_id: str) -> dict | None:
    """返回单 run（含节点明细）；不存在返回 None。"""
    conn = _get_connection(db_path)
    try:
        run_row = conn.execute(
            "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run_row is None:
            return None
        node_rows = conn.execute(
            """
            SELECT * FROM workflow_run_nodes
            WHERE run_id = ?
            ORDER BY started_at ASC, node_run_id ASC
            """,
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    out = _row_to_run(run_row)
    out["nodes"] = [_row_to_node(r) for r in node_rows]
    return out


def _row_to_run(row) -> dict[str, Any]:
    d = dict(row)
    d["checkpoint_json"] = _parse_json(d.get("checkpoint_json")) or {}
    return d


def _row_to_node(row) -> dict[str, Any]:
    d = dict(row)
    d["input_json"] = _parse_json(d.get("input_json")) or {}
    d["output_json"] = _parse_json(d.get("output_json"))
    d["token_usage_json"] = _parse_json(d.get("token_usage_json"))
    return d


def get_workflow_name_for_run(db_path: str | Path, run_id: str) -> str | None:
    """返回 run 对应的 workflow.name；run/workflow 不存在 → None。

    注：workflow_runs 表存的是 workflow_id；本函数负责 JOIN workflows 反查 name。
    被 workflows router 的 resume 端点调用；V1.5 越层整改后路由层不再直接写 SQL。
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT w.name FROM workflow_runs wr
            JOIN workflows w ON w.workflow_id = wr.workflow_id
            WHERE wr.run_id = ?
            """,
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    return row["name"] if row else None


__all__ = ["list_runs", "get_run", "get_workflow_name_for_run"]
