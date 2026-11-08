"""工作流运行时守卫（2026-11-08 批次三十八 · R1-break-cycle）。

本模块从 ``packages/core/api/routers/workflows/control.py`` **下沉**「同 chapter 是否有
活跃 run」的判定。下沉理由不是「文件太长」，而是依赖方向：

- 该判定只依赖 ``get_connection``，与 HTTP 无关；
- 它却被**两个 router 模块**使用（``workflows/control`` 与 ``chapters``），
  其中 ``chapters.py`` 直接 ``from ...workflows.control import _check_active_run_for_chapter``
  ⇒ 形成 **router → 另一个 router 的私有符号** 依赖。

下沉后两侧都向下依赖运行时层，`chapters` 不再需要认识 `workflows/control`。

兼容：``control._check_active_run_for_chapter`` 保留为同名再导出（既有 import 契约、
``workflows/__init__`` 的 re-export 与相关测试零改动）。
"""

from __future__ import annotations

from typing import Any

from packages.core.db import get_connection


def check_active_run_for_chapter(
    db_path: str,
    *,
    chapter_id: str,
    exclude_run_id: str | None = None,
) -> dict[str, Any] | None:
    """同 chapter 下是否存在 RUNNING/PENDING 的活跃 run。

    用于 API 层的并发防护：
    - 启动新 workflow 前查同 chapter 是否有活跃 run；有则 409。
    - resume 自身（PAUSED）前查同 chapter 是否还有**其他**RUNNING/PENDING run；
      有则 409，避免两条 run 并行修改 chapter 状态/数据。

    ``exclude_run_id``：resume 场景下传当前 run_id，把自身从判定里排除（自身已 RUNNING
    之前不会 PAUSED，但保留参数对称便于复用）。

    返回首个匹配的活跃 run 行 dict；无活跃 run → None。
    """
    conn = get_connection(db_path)
    try:
        if exclude_run_id:
            row = conn.execute(
                """
                SELECT run_id, status FROM workflow_runs
                WHERE chapter_id = ?
                  AND run_id != ?
                  AND status IN ('RUNNING', 'PENDING')
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (chapter_id, exclude_run_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT run_id, status FROM workflow_runs
                WHERE chapter_id = ?
                  AND status IN ('RUNNING', 'PENDING')
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (chapter_id,),
            ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"run_id": row["run_id"], "status": row["status"]}


__all__ = ["check_active_run_for_chapter"]
