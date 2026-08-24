"""ReferenceService（V1.5 架构整理 / V1.0 越层整改）。

职责：
- 把 :mod:`packages.core.api.routers.reference` 中的直接 SQL 收敛到本 service：
  - :meth:`list_active_summaries` —— 列出项目下 active canon 摘要。
  - :meth:`get_canon_detail` —— 取单条 canon 全文 + extracts 列表；不存在 → None。
  - :meth:`delete_canon_cascade` —— 事务级联删 extracts + canons；不存在 → False。
- :func:`summary_of` ——从 canon row（sqlite3.Row 或 dict）解析 logline/spine_count/
  rhythm_chapter_count 等摘要字段；解析失败保留 row 字段。

设计要点：
- 构造接收 ``db_path``；每个方法内部 ``packages.core.db.get_connection`` + try/finally。
- ``canon_json`` 列：service 不解析——返回原始字符串由调用方按需 json.loads；
  摘要字段已就地解析（避免重复）。
- :meth:`delete_canon_cascade` 单事务删除两个表，事务失败整体回滚（无残留半成品）。
- 与既有 service 层（chapter/character 等）保持风格一致。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from packages.core.db import get_connection

__all__ = ["ReferenceService", "summary_of"]


def summary_of(canon_row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """从 canon row 抽取摘要字段；解析失败 → 保留 row 字段，summary 计数=0/logline=空。

    输入：``sqlite3.Row`` 或 dict（来自 service 直查）。
    输出：``canon_id / project_id / title / reader_profile / status / created_at /
    logline / spine_count / rhythm_chapter_count`` 完整 dict。
    """
    raw = dict(canon_row)
    summary: dict[str, Any] = {
        "canon_id": raw.get("canon_id"),
        "project_id": raw.get("project_id"),
        "title": raw.get("title"),
        "reader_profile": raw.get("reader_profile"),
        "status": raw.get("status"),
        "created_at": raw.get("created_at"),
        "logline": "",
        "spine_count": 0,
        "rhythm_chapter_count": 0,
    }
    raw_canon_json = raw.get("canon_json") or "{}"
    try:
        cj = json.loads(raw_canon_json)
        if isinstance(cj, dict):
            logline = cj.get("logline")
            if isinstance(logline, str):
                summary["logline"] = logline
            spine = cj.get("spine")
            if isinstance(spine, list):
                summary["spine_count"] = len(spine)
            emo = cj.get("emotion_curve")
            if isinstance(emo, list):
                summary["rhythm_chapter_count"] = len(emo)
    except (TypeError, ValueError):
        pass
    return summary


class ReferenceService:
    """``reference_canons`` + ``canon_extracts`` 表 CRUD。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _safe_parse(raw: Any) -> Any:
        """解析 JSON 字符串；失败返回原值。用于 extracts.extract_json。"""
        if not isinstance(raw, str):
            return raw
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    # ------------------------------------------------------- list summaries
    def list_active_summaries(self, project_id: str) -> list[dict[str, Any]]:
        """列出项目下全部 active canon（按 created_at DESC, canon_id DESC），摘要形态。"""
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT canon_id, project_id, title, reader_profile, status,
                       canon_json, created_at
                FROM reference_canons
                WHERE project_id = ? AND status = 'active'
                ORDER BY created_at DESC, canon_id DESC
                """,
                (project_id,),
            ).fetchall()
        finally:
            conn.close()
        return [summary_of(r) for r in rows]

    # ------------------------------------------------------------- get detail
    def get_canon_detail(self, canon_id: str) -> dict[str, Any] | None:
        """取单条 canon 全文 + extracts 列表；不存在 → None。

        返回 dict 含 ``canon_id / project_id / title / reader_profile / status /
        canon_json(已解析) / report_md / created_at / extracts``。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT canon_id, project_id, title, reader_profile, status,
                       canon_json, report_md, created_at
                FROM reference_canons WHERE canon_id = ?
                """,
                (canon_id,),
            ).fetchone()
            if row is None:
                return None
            extracts_rows = conn.execute(
                """
                SELECT extract_id, chapter_index, extract_json, created_at
                FROM canon_extracts WHERE canon_id = ?
                ORDER BY chapter_index ASC
                """,
                (canon_id,),
            ).fetchall()
        finally:
            conn.close()

        canon_json_raw = row["canon_json"]
        try:
            canon_json_obj = json.loads(canon_json_raw) if canon_json_raw else {}
        except (TypeError, ValueError):
            canon_json_obj = {}

        return {
            "canon_id": row["canon_id"],
            "project_id": row["project_id"],
            "title": row["title"],
            "reader_profile": row["reader_profile"],
            "status": row["status"],
            "canon_json": canon_json_obj,
            "report_md": row["report_md"],
            "created_at": row["created_at"],
            "extracts": [
                {
                    "extract_id": e["extract_id"],
                    "chapter_index": e["chapter_index"],
                    "extract_json": self._safe_parse(e["extract_json"]),
                    "created_at": e["created_at"],
                }
                for e in extracts_rows
            ],
        }

    # ------------------------------------------------------ delete (cascade)
    def delete_canon_cascade(self, canon_id: str) -> bool:
        """级联删除 canon + 关联 extracts（单事务）。

        - 不存在 → False（router 转 404）。
        - 事务成功 → True。
        """
        conn = get_connection(self.db_path)
        try:
            cur = conn.execute(
                "SELECT canon_id FROM reference_canons WHERE canon_id = ?",
                (canon_id,),
            ).fetchone()
            if cur is None:
                return False
            conn.execute(
                "DELETE FROM canon_extracts WHERE canon_id = ?", (canon_id,)
            )
            conn.execute(
                "DELETE FROM reference_canons WHERE canon_id = ?", (canon_id,)
            )
            conn.commit()
        finally:
            conn.close()
        return True