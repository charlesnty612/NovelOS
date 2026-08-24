"""Project Backup / Restore REST 路由（V1.4 Sprint 16 / MVP）。

端点（挂在 ``/api`` 前缀下）：
- ``GET  /projects/{project_id}/backup`` —— 下载项目备份 JSON（Content-Disposition
  attachment）；项目不存在 → 404。
- ``POST /projects/import-backup`` —— 接收 JSON body（备份包 dict），导入为**新项目**；
  返回新项目 dict。坏 format/version / 缺字段 → 422。

错误码映射：
- 404 — project 不存在（GET 下载）；
- 422 — 备份包格式非法（POST 导入）；
- 500 — DB 错误或意外异常。

设计要点：
- 复用 :class:`packages.core.backup.BackupService`（export_project / import_project）。
- GET 端点：响应 ``Content-Type: application/json; charset=utf-8`` + Content-Disposition
  触发浏览器保存；不写入文件，纯内存构造。
- POST 端点：body 必须是 JSON object（FastAPI 自动校验）；FastAPI Pydantic
  ``dict[str, Any]`` 类型即可，无需自定义 schema。
- 安全红线：导出包**不**接触 ``model_configs``（详见 :mod:`packages.core.backup`）；
  导入时白名单闭合（多出任何表即 422）。
- ``discover_routers`` 自动发现：模块顶层 ``router`` 即被 ``main.py`` 挂载。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request, status

from packages.core.backup import BackupService
from packages.core.logging_config import get_logger

log = get_logger("novelos.routers.backup")

router = APIRouter(tags=["backup"])


def _db_path(request: Request) -> str:
    return str(request.app.state.settings.db_path)


def _service(request: Request) -> BackupService:
    return BackupService(_db_path(request))


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/backup —— 下载
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/backup")
def download_project_backup(project_id: str, request: Request) -> dict:
    """导出项目备份 JSON。

    返回 dict 由 FastAPI 自动 JSON 序列化；额外通过 ``Content-Disposition``
    让浏览器保存为 ``backup-<project_id>.json`` 文件。

    项目不存在 → 404；包构造异常 → 500。
    """
    try:
        package = _service(request).export_project(project_id)
    except ValueError as exc:
        # 项目不存在
        raise HTTPException(
            status_code=404,
            detail=f"project {project_id!r} not found",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("backup export failed for %s", project_id)
        raise HTTPException(
            status_code=500,
            detail=f"backup export failed: {exc}",
        ) from exc

    # FastAPI 默认 JSONResponse；通过 response_model 与 media_type 自定义响应头
    from fastapi.responses import JSONResponse

    return JSONResponse(
        content=package,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="backup-{project_id}.json"'
            ),
        },
    )


# ---------------------------------------------------------------------------
# POST /api/projects/import-backup —— 导入
# ---------------------------------------------------------------------------


@router.post(
    "/projects/import-backup",
    status_code=status.HTTP_201_CREATED,
)
def import_project_backup(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """导入备份包为新项目，返回新项目 dict。

    请求体：JSON 对象（顶层 5 字段必填；详见 :mod:`packages.core.backup.schema`）。
    返回：201 + 新项目 dict（与 ``POST /api/projects`` 一致）。

    错误码：
    - 422 — 备份包格式非法 / 表名不在白名单 / 缺必填字段 / 事务失败。
    """
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail="request body must be a JSON object",
        )

    try:
        new_project = _service(request).import_project(payload)
    except ValueError as exc:
        # 格式校验 / 表名非法 / 字段缺失 → 422
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("backup import failed")
        raise HTTPException(
            status_code=500,
            detail=f"backup import failed: {exc}",
        ) from exc

    return new_project


__all__ = ["router"]
