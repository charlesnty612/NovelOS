"""Volumes REST 路由（V3.4 多卷与规模——组织层）。

端点（``main.py`` 已统一挂 ``/api`` 前缀）：

- ``GET    /projects/{pid}/volumes``              列表（含 chapter_count）
- ``POST   /projects/{pid}/volumes``              创建（默认 status='active'）
- ``GET    /projects/{pid}/volumes/{vid}``        详情
- ``PATCH  /projects/{pid}/volumes/{vid}``        部分更新 title / status
- ``POST   /projects/{pid}/volumes/{vid}/seal``   封存（冻结终态快照）
- ``POST   /projects/{pid}/volumes/{vid}/assign`` 挂章节（body {chapter_id}）

错误码映射：
- 404 — project / volume 不存在；
- 409 — number 重复 / 已存在 active 卷 / 重复 seal / sealed 拒绝挂章 /
        sealed → active 反向跳变；
- 422 — 跨项目挂章 / 章节不存在 / CHECK 违反（DB IntegrityError 兜底）；
- 500 — DB 异常（未预期）。

设计要点：
- 复用 :class:`packages.domain.volume.service.VolumeService`；
- ``db_path`` 走 ``request.app.state.settings.db_path``（与 reveal_policies
  / arc / plots / quality 一致）；
- ``discover_routers`` 自动发现：模块顶层 ``router`` 即被 ``main.py`` 挂载；
- router **不带** ``prefix``（main.py 已统一 prepend ``/api``，否则会变
  ``/api/api/...``）。
- 与设计文档 §三一致：**不做按卷拆快照**，seal 时仅把当前 story_states
  最新快照 JSON 写入 terminal_snapshot_json 冻结（归档）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.domain.volume import (
    Volume,
    VolumeAssignRequest,
    VolumeConflictError,
    VolumeCreate,
    VolumeListItem,
    VolumeNotFoundError,
    VolumeSealedError,
    VolumeService,
    VolumeUpdate,
    VolumeValidationError,
)

log = get_logger("novelos.routers.volumes")

router = APIRouter(tags=["volumes"])


def _service(request: Request) -> VolumeService:
    settings = request.app.state.settings
    return VolumeService(settings.db_path)


@router.get(
    "/projects/{project_id}/volumes",
    response_model=list[VolumeListItem],
)
def list_volumes(project_id: str, request: Request) -> list[dict]:
    """列项目下所有卷（含 chapter_count 聚合），按 number ASC 排序。

    - project 不存在 → 404。
    """
    svc = _service(request)
    try:
        return svc.list(project_id)
    except VolumeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/volumes",
    response_model=Volume,
    status_code=status.HTTP_201_CREATED,
)
def create_volume(
    project_id: str,
    payload: VolumeCreate,
    request: Request,
) -> dict:
    """创建卷（默认 status='active'）。

    - 404 — project 不存在；
    - 409 — 同 project 下 number 已存在 / 已存在 active 卷；
    - 422 — DB CHECK 违反（一般不会触发，DDL CHECK 与 pydantic 字段对齐）。
    """
    svc = _service(request)
    try:
        return svc.create(project_id, payload)
    except VolumeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VolumeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VolumeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/volumes/{volume_id}",
    response_model=Volume,
)
def get_volume(project_id: str, volume_id: str, request: Request) -> dict:
    """按主键查卷。

    - 不存在 → 404。
    """
    svc = _service(request)
    row = svc.get(volume_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"volume {volume_id!r} not found",
        )
    if row["project_id"] != project_id:
        # 跨 project 访问 → 404（避免泄露存在性）
        raise HTTPException(
            status_code=404, detail=f"volume {volume_id!r} not found",
        )
    return row


@router.patch(
    "/projects/{project_id}/volumes/{volume_id}",
    response_model=Volume,
)
def update_volume(
    project_id: str,
    volume_id: str,
    payload: VolumeUpdate,
    request: Request,
) -> dict:
    """部分更新（title / status）。

    - 404 — volume 不存在或跨 project；
    - 409 — sealed → active 反向跳变（VolumeSealedError）；
    - 422 — DB IntegrityError 兜底。
    """
    svc = _service(request)
    try:
        row = svc.update(volume_id, payload)
    except VolumeSealedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VolumeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"volume {volume_id!r} not found",
        )
    if row["project_id"] != project_id:
        raise HTTPException(
            status_code=404, detail=f"volume {volume_id!r} not found",
        )
    return row


@router.post(
    "/projects/{project_id}/volumes/{volume_id}/seal",
    response_model=Volume,
    status_code=status.HTTP_200_OK,
)
def seal_volume(project_id: str, volume_id: str, request: Request) -> dict:
    """封存卷（status='sealed' + 冻结 story_states 最新快照 JSON）。

    - 404 — volume 不存在或跨 project；
    - 409 — 已 sealed（幂等失败）。
    """
    svc = _service(request)
    try:
        row = svc.seal(volume_id)
    except VolumeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"volume {volume_id!r} not found",
        )
    if row["project_id"] != project_id:
        raise HTTPException(
            status_code=404, detail=f"volume {volume_id!r} not found",
        )
    return row


@router.post(
    "/projects/{project_id}/volumes/{volume_id}/assign",
    response_model=Volume,
    status_code=status.HTTP_200_OK,
)
def assign_chapter_to_volume(
    project_id: str,
    volume_id: str,
    payload: VolumeAssignRequest,
    request: Request,
) -> dict:
    """将 chapter 挂到指定卷（chapters.volume_id = volume_id）。

    - 404 — volume 不存在或跨 project；
    - 409 — sealed 卷拒绝挂章；
    - 422 — chapter 不存在 / chapter 与 volume 不属同 project。
    - 幂等：chapter 已在该卷上视为成功。
    """
    svc = _service(request)
    try:
        row = svc.assign_chapter(volume_id, payload.chapter_id)
    except VolumeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VolumeSealedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VolumeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row["project_id"] != project_id:
        raise HTTPException(
            status_code=404, detail=f"volume {volume_id!r} not found",
        )
    return row


__all__ = ["router"]
