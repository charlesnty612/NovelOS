"""Characters REST 路由（Sprint 1）。

挂在 ``/api`` 前缀下。涉及 ``characters`` + ``character_states`` 两张表。
对齐 PRD §16/§17 Definition 与 State 分离。

端点：
- POST   /projects/{pid}/characters       —— 创建角色（自动写 state v1）
- GET    /projects/{pid}/characters       —— 列某项目下角色（含 latest_state_json）
- GET    /characters/{id}                 —— 取单个角色（含 latest_state_json）
- PATCH  /characters/{id}                 —— 更新定义侧字段
- DELETE /characters/{id}                 —— 级联删除 character_states 行后删除角色
- GET    /characters/{id}/states          —— 列全部 state 历史版本
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.domain.character.models import (
    Character,
    CharacterCreate,
    CharacterState,
    CharacterUpdate,
)
from packages.domain.character.service import CharacterService

log = get_logger("novelos.routers.characters")

router = APIRouter(tags=["characters"])


def _service(request: Request) -> CharacterService:
    settings = request.app.state.settings
    return CharacterService(settings.db_path)


# ------------------------------------------------------------- nested under project
@router.post(
    "/projects/{project_id}/characters",
    response_model=Character,
    status_code=status.HTTP_201_CREATED,
)
def create_character(project_id: str, payload: CharacterCreate, request: Request) -> dict:
    svc = _service(request)
    # 先确认 project 存在，否则后面 FK 错误信息对前端不可读
    from packages.domain.project.service import ProjectService

    if ProjectService(request.app.state.settings.db_path).get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    try:
        return svc.create(project_id, payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc


@router.get(
    "/projects/{project_id}/characters",
    response_model=list[Character],
)
def list_characters(project_id: str, request: Request) -> list[dict]:
    # 校验 project 存在 → 404 早返回（任务书语义：404 由 router 转）
    from packages.domain.project.service import ProjectService

    if ProjectService(request.app.state.settings.db_path).get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return _service(request).list_by_project(project_id)


# --------------------------------------------------------------- direct by id
@router.get("/characters/{character_id}", response_model=Character)
def get_character(character_id: str, request: Request) -> dict:
    row = _service(request).get(character_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"character {character_id!r} not found")
    return row


@router.patch("/characters/{character_id}", response_model=Character)
def update_character(character_id: str, payload: CharacterUpdate, request: Request) -> dict:
    try:
        row = _service(request).update(character_id, payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"character {character_id!r} not found")
    return row


@router.delete("/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_character(character_id: str, request: Request) -> None:
    ok = _service(request).delete(character_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"character {character_id!r} not found")
    return None


# --------------------------------------------------------------- state history
@router.get(
    "/characters/{character_id}/states",
    response_model=list[CharacterState],
)
def list_character_states(character_id: str, request: Request) -> list[dict]:
    rows = _service(request).list_states(character_id)
    if rows is None:
        raise HTTPException(status_code=404, detail=f"character {character_id!r} not found")
    return rows
