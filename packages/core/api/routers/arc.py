"""叙事弧光聚合视图 REST 路由。

端点（``main.py`` 已统一挂 ``/api`` 前缀）：

- ``GET /projects/{project_id}/arc`` —— 现场组装五表汇总视图，返回 dict。

错误码映射：
- 404 — project 不存在（service 抛 ``ValueError``，router 转 404）；
- 500 — DB 异常。

设计要点：
- 复用 :func:`packages.core.arc.service.build_arc_view`；
- ``db_path`` 走 ``request.app.state.settings.db_path``（与 quality.py /
  signing_check.py / export.py 一致）；
- ``discover_routers`` 自动发现：模块顶层 ``router`` 即被 ``main.py`` 挂载。
- 注：未给 router 加 ``prefix``，因 ``main.py`` 已统一 prepend ``/api``，
  否则会变成 ``/api/api/...``。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from packages.core.arc.service import build_arc_view
from packages.core.logging_config import get_logger

log = get_logger("novelos.routers.arc")

router = APIRouter(tags=["arc"])


@router.get("/projects/{project_id}/arc")
def get_arc_view(project_id: str, request: Request) -> dict:
    """返回该项目的叙事弧光聚合视图（含 chapters / payoff / hooks / debts / alerts）。

    - project 不存在 → 404 ``{"detail": "project '...' not found"}``；
    - 成功 → 200 + :func:`build_arc_view` 返回的 dict；
    - 字段容错：plan_json 缺失 / 无 quality_report / 无 draft 一律填空值。
    """

    db_path = str(request.app.state.settings.db_path)
    try:
        return build_arc_view(db_path, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("arc view failed: %s", exc)
        raise HTTPException(status_code=500, detail="arc view failed") from exc


__all__ = ["router"]
