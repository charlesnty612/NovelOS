"""章节的**审校状态**读侧口径（2026-11-08 批次三十八 · REF-04）。

## 为什么有它

「最近一次 COMPLETED 的 chapter-review」这句话此前有**三份逐字相同**的 SQL：

- ``packages/domain/chapter/service.py::ChapterService.get_last_review_completed_at``
- ``packages/core/api/routers/workflows/control.py::_latest_completed_review_end``
- ``packages/workflows/chapter_commit/commit.py``（内联，注释自陈「避免 workflow → API
  反向依赖，本文件内私有实现」）

以及**两份**「阈值之后有没有更新草稿」：

- ``control.py::_latest_draft_after``
- ``commit.py``（内联）

三份独立实现意味着下一次改口径仍会只改一两处。本模块是它们的单一落点。

## 设计

- **``conn`` 必传、本模块不建连**。三处持有者不同（control 自建连 / service 自建连 /
  commit 已开事务），一个签名同时满足；也避免在事务内另开连接（AGENTS 坑区 M1-a）。
- **不吞异常**：与三处现状一致（均无 ``except``）。

## 明确**不合并**的一份

``packages/workflows/chapter_commit/gate.py::_reviewed_draft_version`` 是**另一套语义**：
它按 ``started_at DESC`` 取 **多行**（``LIMIT _REVIEWED_VERSION_LOOKBACK_RUNS``），再逐条解
checkpoint 取第一个可用的 ``draft_version``。排序键（started_at vs ended_at）、行数
（N vs 1）、产出（draft_version vs ended_at）全不同——合并会直接改变门禁取到的版本
（历史上正是这条导致「导出物按 commits.draft_version 拿到旧稿」）。

它只复用 :data:`REVIEW_VALID_PREDICATE`（「有效评审」这个判据），排序与解析一律不动。
"""

from __future__ import annotations

import sqlite3
from typing import Any

#: 「这一次 review run 有效」的唯一判据（供各处拼进自己的 SQL）。
#:
#: ⚠️ 只含谓词，**不含排序 / 行数 / 产出**——那三样各处不同，不得一并标准化。
REVIEW_VALID_PREDICATE = "wf.name = 'chapter-review' AND wr.status = 'COMPLETED'"


def last_review_ended_at(conn: sqlite3.Connection, chapter_id: str) -> str | None:
    """最近一次 COMPLETED chapter-review 的 ``ended_at``（ISO 字符串）；无则 ``None``。

    返回 ``None`` 的两义性（与三处原实现一致，**未改变**）：
    ① 该章从未有 COMPLETED review；② 该 run 的 ``ended_at`` 为 NULL。
    调用方都按「无需本守卫兜底」处理。
    """
    row = conn.execute(
        f"""
        SELECT wr.ended_at FROM workflow_runs wr
        JOIN workflows wf ON wf.workflow_id = wr.workflow_id
        WHERE wr.chapter_id = ?
          AND {REVIEW_VALID_PREDICATE}
        ORDER BY wr.ended_at DESC
        LIMIT 1
        """,
        (chapter_id,),
    ).fetchone()
    if row is None or row["ended_at"] is None:
        return None
    return str(row["ended_at"])


def latest_draft_after(
    conn: sqlite3.Connection, chapter_id: str, threshold_iso: str
) -> dict[str, Any] | None:
    """``threshold_iso`` 之后创建的最新草稿行（``version`` / ``created_at``）；无则 ``None``。

    ``created_at`` 是 ``now_iso()`` 产物，可直接字典序比较；调用方需保证
    ``threshold_iso`` 来自同一时间体系（``workflow_runs.ended_at`` 写入路径一致）。
    """
    row = conn.execute(
        """
        SELECT version, created_at FROM drafts
        WHERE chapter_id = ? AND created_at > ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (chapter_id, threshold_iso),
    ).fetchone()
    if row is None:
        return None
    # ``int(...)`` 与原实现（``control._latest_draft_after``）逐字同款：
    # sqlite 返回的列类型随驱动/列亲和性变化，这里固定成 int 以免调用方比较时踩类型差。
    return {"version": int(row["version"]), "created_at": row["created_at"]}


__all__ = ["REVIEW_VALID_PREDICATE", "last_review_ended_at", "latest_draft_after"]
