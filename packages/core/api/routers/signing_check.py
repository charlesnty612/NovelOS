"""番茄签约体检 REST 路由。

端点（``main.py`` 已统一挂 ``/api`` 前缀）：

- ``GET /projects/{project_id}/signing-check`` —— 现场组装检查项 + summary，返回 dict。

错误码映射：
- 404 — project 不存在（service 抛 ``ValueError``，router 转 404）；
- 500 — DB 异常。

设计要点：
- 复用 :func:`packages.core.signing_check.service.run_signing_check`；
- ``db_path`` 走 ``request.app.state.settings.db_path``（与 quality.py / export.py 一致）；
- ``discover_routers`` 自动发现：模块顶层 ``router`` 即被 ``main.py`` 挂载。
- 注：未给 router 加 ``prefix``，因 ``main.py`` 已统一 prepend ``/api``，否则会变成 ``/api/api/...``。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from packages.core.logging_config import get_logger
from packages.core.signing_check.service import run_signing_check

log = get_logger("novelos.routers.signing_check")

router = APIRouter(tags=["signing-check"])


@router.get("/projects/{project_id}/signing-check")
def get_signing_check(project_id: str, request: Request) -> dict:
    """返回该项目的签约体检结果（含 items / summary）。

    - project 不存在 → 404 ``{"detail": "project '...' not found"}``；
    - 成功 → 200 + ``run_signing_check`` 返回的 dict。
    """

    db_path = str(request.app.state.settings.db_path)
    try:
        return run_signing_check(db_path, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("signing check failed: %s", exc)
        raise HTTPException(status_code=500, detail="signing check failed") from exc


__all__ = ["router"]
