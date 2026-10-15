"""Ledger REST API 路由（Sprint 9）。

自动发现：本模块顶层定义名为 ``router`` 的 ``APIRouter``，
由 ``packages.core.api.routers.discover_routers`` 自动挂载到 FastAPI 应用。

端点清单
========
- POST/GET   /api/projects/{pid}/hooks          创建 / 列表（支持 ?status= 过滤）
- GET/PATCH  /api/hooks/{id}                    详情 / 更新（含状态机迁移校验）
- DELETE     /api/hooks/{id}                    删除
- POST/GET   /api/projects/{pid}/debts          创建 / 列表
- GET/PATCH  /api/debts/{id}                    详情 / 更新
- DELETE     /api/debts/{id}                    删除

出参契约（G2，2026-10-15 批次（六））
------------------------------------
8 个**读/写**端点已挂 ``response_model``：单体 6 个用 ``Hook`` / ``Debt``，
列表 2 个用 ``list[Hook]`` / ``list[Debt]``（形状见
``packages/domain/ledger/models.py`` 的 docstring）。

列表行形状**单独量过**（2026-10-15，临时库 + 合成夹具，见
``tests/api/test_ledger_plot_list_response_contract.py``）：``hooks`` **12 键**、
``narrative_debts`` **11 键**，与单体端点逐字相同，且 ≡ 两张表的列集（读路径是
``SELECT *``）——但**这是量出来的结论，不是照 DDL 推的**：Lead 实测到过反例
（``GET /commits`` 列表行 12 键而 ``POST /commits`` 响应只有 4 键）。

⚠️ 两个 **DELETE** 端点仍返回 204（无 body，本就无契约可挂）。

错误码
------
- 201 / 200 / 204：成功
- 404：hook 或 debt 不存在 / 引用的 chapter_id 不存在
- 409：状态机非法跳变
- 422：pydantic 校验失败 / visibility 非法 / name 空等
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from packages.domain.ledger import (
    ChapterNotFound,
    Debt,
    DebtCreate,
    DebtUpdate,
    Hook,
    HookCreate,
    HookUpdate,
    LedgerService,
    LedgerTransitionError,
    NotFoundError,
    ValidationError,
)

router = APIRouter(tags=["ledger"])


def _service(request: Request) -> LedgerService:
    """每请求新建 ``LedgerService``。"""
    settings = request.app.state.settings
    return LedgerService(settings.db_path)


def _handle(exc: Exception) -> HTTPException:
    """统一异常 → HTTPException 映射。"""
    if isinstance(exc, LedgerTransitionError):
        return HTTPException(
            status_code=409,
            detail=f"illegal {exc.kind} status transition {exc.current!r} -> {exc.target!r}",
        )
    if isinstance(exc, (NotFoundError, ChapterNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# ===========================================================================
# Hooks
# ===========================================================================


@router.post(
    "/projects/{pid}/hooks",
    status_code=status.HTTP_201_CREATED,
    response_model=Hook,
)
def create_hook(
    pid: str, payload: HookCreate, request: Request
) -> dict[str, Any]:
    svc = _service(request)
    try:
        row = svc.create_hook(pid, payload)
    except (LedgerTransitionError, NotFoundError, ChapterNotFound, ValidationError) as exc:
        raise _handle(exc) from exc
    return row


@router.get("/projects/{pid}/hooks", response_model=list[Hook])
def list_hooks(
    pid: str,
    request: Request,
    status: str | None = None,
) -> list[dict[str, Any]]:
    svc = _service(request)
    try:
        return svc.list_hooks_by_project(pid, status=status)
    except (NotFoundError, ChapterNotFound, ValidationError) as exc:
        raise _handle(exc) from exc


@router.get("/hooks/{hook_id}", response_model=Hook)
def get_hook(hook_id: str, request: Request) -> dict[str, Any]:
    svc = _service(request)
    row = svc.get_hook(hook_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"hook {hook_id!r} not found")
    return row


@router.patch("/hooks/{hook_id}", response_model=Hook)
def update_hook(
    hook_id: str, payload: HookUpdate, request: Request
) -> dict[str, Any]:
    svc = _service(request)
    try:
        row = svc.update_hook(hook_id, payload)
    except (LedgerTransitionError, NotFoundError, ChapterNotFound, ValidationError) as exc:
        raise _handle(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"hook {hook_id!r} not found")
    return row


@router.delete("/hooks/{hook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_hook(hook_id: str, request: Request) -> Response:
    svc = _service(request)
    try:
        ok = svc.delete_hook(hook_id)
    except (NotFoundError, ChapterNotFound, ValidationError) as exc:
        raise _handle(exc) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"hook {hook_id!r} not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Narrative Debts
# ===========================================================================


@router.post(
    "/projects/{pid}/debts",
    status_code=status.HTTP_201_CREATED,
    response_model=Debt,
)
def create_debt(
    pid: str, payload: DebtCreate, request: Request
) -> dict[str, Any]:
    svc = _service(request)
    try:
        row = svc.create_debt(pid, payload)
    except (LedgerTransitionError, NotFoundError, ChapterNotFound, ValidationError) as exc:
        raise _handle(exc) from exc
    return row


@router.get("/projects/{pid}/debts", response_model=list[Debt])
def list_debts(
    pid: str,
    request: Request,
    status: str | None = None,
) -> list[dict[str, Any]]:
    svc = _service(request)
    try:
        return svc.list_debts_by_project(pid, status=status)
    except (NotFoundError, ChapterNotFound, ValidationError) as exc:
        raise _handle(exc) from exc


@router.get("/debts/{debt_id}", response_model=Debt)
def get_debt(debt_id: str, request: Request) -> dict[str, Any]:
    svc = _service(request)
    row = svc.get_debt(debt_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"debt {debt_id!r} not found")
    return row


@router.patch("/debts/{debt_id}", response_model=Debt)
def update_debt(
    debt_id: str, payload: DebtUpdate, request: Request
) -> dict[str, Any]:
    svc = _service(request)
    try:
        row = svc.update_debt(debt_id, payload)
    except (LedgerTransitionError, NotFoundError, ChapterNotFound, ValidationError) as exc:
        raise _handle(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"debt {debt_id!r} not found")
    return row


@router.delete("/debts/{debt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_debt(debt_id: str, request: Request) -> Response:
    svc = _service(request)
    try:
        ok = svc.delete_debt(debt_id)
    except (NotFoundError, ChapterNotFound, ValidationError) as exc:
        raise _handle(exc) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"debt {debt_id!r} not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
