"""RevealPolicy REST 路由（V3.3 P0-2 知识权限补全）。

端点（``main.py`` 已统一挂 ``/api`` 前缀）：

- ``GET    /projects/{pid}/reveal-policies``        列表，支持 ?status= 过滤
- ``POST   /projects/{pid}/reveal-policies``        创建
- ``PATCH  /projects/{pid}/reveal-policies/{policy_id}``  更新 status / revealed_chapter / notes
- ``DELETE /projects/{pid}/reveal-policies/{policy_id}``  删除

错误码映射：
- 404 — project / reveal_policy 不存在；
- 422 — 字段非法（target_kind / status / audience / revealed_chapter / 实体不存在）；
- 500 — DB 异常。

设计要点：
- 复用 :class:`packages.domain.knowledge.service.RevealPolicyService`；
- ``db_path`` 走 ``request.app.state.settings.db_path``（与 arc / plots / quality 一致）；
- ``discover_routers`` 自动发现：模块顶层 ``router`` 即被 ``main.py`` 挂载；
- router **不带** ``prefix``（main.py 已统一 prepend ``/api``，否则会变 ``/api/api/...``）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from packages.core.logging_config import get_logger
from packages.domain.knowledge import (
    NotFoundError,
    RevealPolicyService,
    ValidationError,
)

log = get_logger("novelos.routers.reveal_policies")

router = APIRouter(tags=["reveal-policies"])


def _service(request: Request) -> RevealPolicyService:
    settings = request.app.state.settings
    return RevealPolicyService(settings.db_path)


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/projects/{pid}/reveal-policies")
def list_reveal_policies(
    pid: str,
    request: Request,
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    """列项目下所有 reveal_policies，可选 ``?status=planned|revealed|cancelled`` 过滤。

    - project 不存在 → 422（与 PlotService 风格一致：service 抛 ValidationError）；
    - 非法 status 过滤值 → 422。
    """
    svc = _service(request)
    try:
        items = svc.list(pid, status=status_filter)
    except (NotFoundError, ValidationError) as exc:
        raise _handle(exc) from exc
    return [p.to_dict() for p in items]


@router.post(
    "/projects/{pid}/reveal-policies",
    status_code=status.HTTP_201_CREATED,
)
def create_reveal_policy(
    pid: str,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """创建 reveal_policy。

    必填字段：``target_kind``、``target_id``。
    可选字段：``reveal_by_chapter``（正整数）、``audience``（默认 'reader'）、
    ``status``（默认 'planned'）、``revealed_chapter``（status='revealed' 时必填）、
    ``notes``。

    - 422 触发条件：target_kind 非法 / status 非法 / status='revealed' 但缺
      revealed_chapter / target 实体不存在 / project 不存在。
    """
    svc = _service(request)
    try:
        policy = svc.create(
            project_id=pid,
            target_kind=payload.get("target_kind", ""),
            target_id=payload.get("target_id", ""),
            reveal_by_chapter=payload.get("reveal_by_chapter"),
            audience=payload.get("audience"),
            status=payload.get("status"),
            revealed_chapter=payload.get("revealed_chapter"),
            notes=payload.get("notes"),
        )
    except (NotFoundError, ValidationError) as exc:
        raise _handle(exc) from exc
    return policy.to_dict()


@router.patch("/projects/{pid}/reveal-policies/{policy_id}")
def update_reveal_policy(
    pid: str,
    policy_id: str,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """部分更新 reveal_policy（仅允许改 status / revealed_chapter / notes）。

    - policy_id 不存在 → 404；
    - 422 触发条件：status 非法 / status='revealed' 但缺 revealed_chapter。
    """
    svc = _service(request)
    try:
        updated = svc.update(
            policy_id,
            status=payload.get("status"),
            revealed_chapter=payload.get("revealed_chapter"),
            notes=payload.get("notes"),
        )
    except (NotFoundError, ValidationError) as exc:
        raise _handle(exc) from exc
    return updated.to_dict()


@router.delete(
    "/projects/{pid}/reveal-policies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_reveal_policy(
    pid: str,
    policy_id: str,
    request: Request,
) -> Response:
    """删除 reveal_policy；不存在 → 404。"""
    svc = _service(request)
    try:
        svc.delete(policy_id)
    except (NotFoundError, ValidationError) as exc:
        raise _handle(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
