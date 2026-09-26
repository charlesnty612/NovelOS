"""交付判定 REST 路由（delivery verdict）。

端点（``main.py`` 已统一挂 ``/api`` 前缀）：

- ``GET /projects/{project_id}/delivery-verdict`` —— 逐章交付判定 + 项目汇总。

错误码映射：
- 404 — project 不存在（service 抛 ``ValueError``，router 转 404）；
- 500 — DB 异常或其它未预期异常。

设计要点：
- 复用 :func:`packages.core.delivery.build_delivery_report`——端点自身不含任何判定
  逻辑（判定是纯函数，见 ``packages/core/delivery/verdict.py``）；
- ``db_path`` 走 ``request.app.state.settings.db_path``（与 quality.py /
  signing_check.py 一致）；
- ``discover_routers`` 自动发现：模块顶层 ``router`` 即被 ``main.py`` 挂载；
- 未给 router 加 ``prefix``，因 ``main.py`` 已统一 prepend ``/api``，否则会变成
  ``/api/api/...``。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from packages.core.delivery.models import DeliveryVerdictResponse
from packages.core.delivery.service import build_delivery_report
from packages.core.logging_config import get_logger

log = get_logger("novelos.routers.delivery")

router = APIRouter(tags=["delivery"])


@router.get(
    "/projects/{project_id}/delivery-verdict",
    response_model=DeliveryVerdictResponse,
)
def get_delivery_verdict(project_id: str, request: Request) -> dict:
    """返回该项目的交付判定报告（逐章 rows + 项目 roll-up）。

    - project 不存在 → 404 ``{"detail": "project '...' not found"}``；
    - 成功 → 200 + ``build_delivery_report`` 的 dict（含 ``project_verdict`` /
      ``roll_up`` / ``evidence_sources`` / ``chapters``）。
    """
    db_path = str(request.app.state.settings.db_path)
    try:
        return build_delivery_report(db_path, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("delivery verdict failed: %s", exc)
        raise HTTPException(status_code=500, detail="delivery verdict failed") from exc


__all__ = ["router"]
