"""Projects REST 路由（Sprint 1 + V3.7 字数带覆盖 + 题材库 P1b 读侧暴露）。

挂在 ``/api`` 前缀下，由 ``packages/core/api/main.py`` 的 ``discover_routers()`` 自动发现。
对齐 DDL：``projects`` 表（``database/migrations/0001_init.sql`` line 34-44）。

V3.7：PATCH /projects/{project_id} 支持 ``word_band`` 字段——dict 落 ``word_band_json``
列、null 清除覆盖、缺省保留。校验在 router 层走
:func:`packages.core.quality.wordcount.resolve_band_config`，非法值 → 422。

题材库 P1b：GET /projects（列表）与 GET /projects/{project_id}（详情）响应经
``Project`` 模型同步暴露 ``genre_pack_id``（绑定题材包 id；未绑定 / 极老库缺列 →
null）。绑定写路径在 :mod:`packages.core.api.routers.genre`（bind / unbind），
本路由只读。

2026-09-18（0029）：``writing_bible``（项目写作圣经）随既有 create / update / 读模型
一并暴露——自由文本，无需 router 层校验（与 ``premise`` 同口径，空白串由 Service
归一为 NULL）。装配侧优先级见 :mod:`packages.domain.project.models` 的模块 docstring。

2026-10-14（0031）：``fast_mode``（快通道）随既有 create / update / 读模型暴露，
同样无需 router 层校验（布尔三态由 Service 的 ``_coerce_fast_mode`` 归一）。
**它不进任何装配 payload**，故不涉及缓存键指纹；解析与闸门旁路语义见
:func:`packages.core.api.routers.workflows.fast_mode.resolve_fast_mode`，
边界（保留 observer、只关闸门不关判定）见 AGENTS.md §二 硬规则 10。
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from packages.core.api.routers.workflows.fast_mode import resolve_fast_mode_with_source
from packages.core.logging_config import get_logger
from packages.core.quality.wordcount import resolve_band_config
from packages.domain.project.models import Project, ProjectCreate, ProjectUpdate
from packages.domain.project.service import ProjectService

log = get_logger("novelos.routers.projects")

router = APIRouter(tags=["projects"])


def _service(request: Request) -> ProjectService:
    """从 app.state 构造领域 Service（每请求一个实例，便于测试隔离）。"""
    settings = request.app.state.settings
    return ProjectService(settings.db_path)


@router.post("/projects", response_model=Project, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, request: Request) -> dict:
    # 与 PATCH 同口径：word_band 显式提供（非 None）时先过 resolve_band_config 校验。
    if payload.word_band is not None:
        try:
            resolve_band_config(payload.word_band)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"word_band 非法: {exc}",
            ) from exc
    try:
        return _service(request).create(payload)
    except sqlite3.IntegrityError as exc:
        # CHECK 违反或 FK 违反 → 422
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc


@router.get("/projects", response_model=list[Project])
def list_projects(
    request: Request,
    include_archived: bool = False,
) -> list[dict]:
    """默认排除归档项目；``?include_archived=true`` 时全量返回。"""
    return _service(request).list(include_archived=include_archived)


@router.get("/projects/{project_id}", response_model=Project)
def get_project(project_id: str, request: Request) -> dict:
    row = _service(request).get(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return row


class ProjectEffectiveSettings(BaseModel):
    """项目「**已解析**」的设置（只读出口，2026-10-23 批次三十二）。

    为什么要有它：``projects.fast_mode`` 列是**三态**（NULL 未设置 / 1 开 / 0 显式关），
    而 NULL 会被后端解析成「回落 env，再回落默认关」
    （见 :func:`packages.core.api.routers.workflows.fast_mode.resolve_fast_mode`）。
    前端若只拿到列值，就只能把 NULL 显示成「关」——**在 env 开启时那是在撒谎**
    （后端实际在快通道、跳过审校闸门，而作者以为闸门还在）。

    ⚠️ 解析**不在本文件另写一份**：直接复用权威单点
    :func:`resolve_fast_mode_with_source`（含 env 词表），本文件只做 HTTP 出口。
    """

    fast_mode_effective: bool = Field(
        description="快通道有效值（后端权威解析结果：项目列 > env > 默认关）。",
    )
    fast_mode_source: Literal["project", "env", "off"] = Field(
        description=(
            "有效值来源：project=项目列有值 / env=项目列为 NULL 且回落环境变量 / "
            "off=项目列为 NULL 且 env 未设置（默认关）。"
        ),
    )


@router.get(
    "/projects/{project_id}/effective-settings",
    response_model=ProjectEffectiveSettings,
)
def get_project_effective_settings(project_id: str, request: Request) -> dict:
    """返回该项目**当前真正生效**的快通道设置（只读）。

    与 ``GET /projects/{pid}`` 的区别：那个返回**列值**（三态，可能是 NULL），
    这个返回**解析后的有效值 + 来源**——前端用它显示勾选态、判定 ``auto_commit``
    可用性，避免「后端在快通道、前端显示关」这种**不知情的闸门跳过**。
    """
    svc = _service(request)
    if svc.get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    settings = request.app.state.settings
    effective, source = resolve_fast_mode_with_source(
        db_path=str(settings.db_path),
        project_id=project_id,
    )
    return {"fast_mode_effective": effective, "fast_mode_source": source}


@router.patch("/projects/{project_id}", response_model=Project)
def update_project(project_id: str, payload: ProjectUpdate, request: Request) -> dict:
    # V3.7：word_band 显式提供时校验；非法 → 422（与 Pydantic 校验错同等待遇）。
    # model_fields_set 区分「未提供」与「显式 null」——后者不校验（语义：清除覆盖）。
    if "word_band" in payload.model_fields_set and payload.word_band is not None:
        try:
            resolve_band_config(payload.word_band)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"word_band 非法: {exc}",
            ) from exc
    try:
        row = _service(request).update(project_id, payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return row


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, request: Request) -> None:
    svc = _service(request)
    try:
        ok = svc.delete(project_id)
    except sqlite3.IntegrityError as exc:
        # 存在子记录（characters / chapters 等）→ 409
        # 提供 has_children 检出的可读 detail，便于前端展示
        detail = (
            f"project {project_id!r} has dependent rows "
            f"(characters/chapters), cannot delete"
        )
        log.info("delete project conflict: %s", detail)
        raise HTTPException(status_code=409, detail=detail) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return None
