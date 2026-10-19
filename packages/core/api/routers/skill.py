"""skill REST 路由（内部消费外部 skill）。

挂在 ``/api`` 前缀下（``discover_routers`` 自动发现，参照 ``routers/genre.py``）。

端点：
- ``POST   /skills``                              —— 创建 skill；201
- ``GET    /skills``                              —— 列出 skill 摘要；``?name=`` 精确过滤
- ``GET    /skills/{skill_id}``                   —— skill 全文（payload 已解析）
- ``PUT    /skills/{skill_id}``                   —— 更新；提供 payload 时 version 自增
- ``DELETE /skills/{skill_id}``                   —— 删除；被项目绑定 → 409
- ``GET    /projects/{project_id}/skills``         —— 项目当前绑定集合（有序 + 全文）
- ``POST   /projects/{project_id}/skills/bind``    —— 绑定（**多 slot 有序**，覆盖式）
- ``POST   /projects/{project_id}/skills/unbind``  —— 解绑（幂等）

错误码映射：
- 404 —— project / skill 不存在；
- 409 —— 删除被项目绑定的 skill（先解绑再删）；
- 422 —— payload 不合 ``docs/state-model/schemas/skill.schema.json``
  （``detail={"errors": [...]}``，错误串格式与题材包 / canon 校验器同款），
  或 skill_id 冲突 / 请求体字段非法；
- 500 —— 未捕获异常。

设计要点：
- 路由层不写 SQL：全部数据访问走
  :class:`packages.core.skill.service.SkillService`；路由只做参数校验 + 调用 +
  错误映射。
- skill 是**跨作品资源**（与题材包同款）：创建端点挂在 ``/skills`` 下，绑定端点
  才带项目语义。
- 绑定是**多 slot 有序**（关联表 ``project_skills`` + ``ord``），与题材包的
  「单 slot」语义不同：bind 请求体给的是 **skill_id 列表**，列表顺序即注入顺序。
- ⚠️ **response_model 必声明**（本仓已全站收口）：建模型前先量实际返回的键集
  ——``response_model`` 会过滤未声明字段，量错就是静默丢字段。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from packages.core.logging_config import get_logger
from packages.core.skill import (
    BindStatus,
    Skill,
    SkillCreate,
    SkillService,
    SkillSummary,
    SkillUpdate,
)
from packages.domain.project.service import ProjectService

log = get_logger("novelos.routers.skill")

router = APIRouter(tags=["skill"])


class SkillBindRequest(BaseModel):
    """绑定请求体：``{"skill_ids": ["skl_a", "skl_b"]}``。

    **列表顺序即注入顺序**（写入 ``project_skills.ord``）；重复 id 去重（保留首次
    出现的位置）。**覆盖式**：绑定会先清掉该项目既有绑定。
    """

    skill_ids: list[str] = Field(default_factory=list, max_length=50)


class SkillUnbindRequest(BaseModel):
    """解绑请求体：``{"skill_id": "skl_a"}``（幂等：未绑定也返回 200）。"""

    skill_id: str = Field(..., min_length=1, max_length=120)


def _service(request: Request) -> SkillService:
    """从 app.state 构造 service（每请求一个实例，便于测试隔离）。"""
    return SkillService(request.app.state.settings.db_path)


def _ensure_project(request: Request, project_id: str) -> None:
    """项目存在性校验（绑定 / 解绑端点用；skill 本身是跨作品资源）。"""
    svc = ProjectService(request.app.state.settings.db_path)
    if svc.get(project_id) is None:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )


def _validate_or_422(svc: SkillService, payload: Any) -> None:
    """payload 过 jsonschema；不合规 → 422（``detail={"errors": [...]}``）。"""
    errors = svc.validate_payload(payload)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})


# ---------------------------------------------------------------------------
# POST /skills
# ---------------------------------------------------------------------------


@router.post("/skills", response_model=Skill, status_code=status.HTTP_201_CREATED)
def create_skill(payload: SkillCreate, request: Request) -> dict[str, Any]:
    """创建 skill（201）；``payload`` 不合 schema → 422；``skill_id`` 冲突 → 422。

    ⚠️ **``payload.references` 不是渐进披露**：本仓模型**没有 tool use**，无法「按需
    读取」——references 在**装配期**随 instructions 一起注入（受 ``skills`` 段 2000
    字符预算约束）。API 消费方**不应**假定「模型能自己取参考材料」。
    """
    svc = _service(request)
    _validate_or_422(svc, payload.payload)
    try:
        return svc.create(payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc


# ---------------------------------------------------------------------------
# GET /skills
# ---------------------------------------------------------------------------


@router.get("/skills", response_model=list[SkillSummary])
def list_skills(
    request: Request, name: str | None = Query(default=None, max_length=200),
) -> list[dict[str, Any]]:
    """列出 skill 摘要（``created_at DESC``；``?name=`` 精确过滤）。"""
    return _service(request).list_skills(name=name)


# ---------------------------------------------------------------------------
# GET /skills/{skill_id}
# ---------------------------------------------------------------------------


@router.get("/skills/{skill_id}", response_model=Skill)
def get_skill(skill_id: str, request: Request) -> dict[str, Any]:
    """skill 全文（payload 已解析为 dict）。

    ⚠️ **``payload.references` 不是渐进披露**：本仓模型**没有 tool use**，无法「按需
    读取」——references 在**装配期**随 instructions 一起注入（受 ``skills`` 段 2000
    字符预算约束）。API 消费方**不应**假定「模型能自己取参考材料」。
    """
    skill = _service(request).get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"skill {skill_id!r} not found")
    return skill


# ---------------------------------------------------------------------------
# PUT /skills/{skill_id}
# ---------------------------------------------------------------------------


@router.put("/skills/{skill_id}", response_model=Skill)
def update_skill(
    skill_id: str, payload: SkillUpdate, request: Request,
) -> dict[str, Any]:
    """更新 skill；提供 ``payload`` 时 version 自增（装配缓存键指纹跟随）。"""
    svc = _service(request)
    if payload.payload is not None:
        _validate_or_422(svc, payload.payload)
    updated = svc.update(skill_id, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"skill {skill_id!r} not found")
    return updated


# ---------------------------------------------------------------------------
# DELETE /skills/{skill_id}
# ---------------------------------------------------------------------------


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(skill_id: str, request: Request) -> None:
    """删除 skill；仍被项目绑定 → 409（先 unbind 再删）。"""
    svc = _service(request)
    bound = svc.count_bindings(skill_id)
    if bound > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"skill {skill_id!r} is bound to {bound} project(s); "
                f"unbind before deleting"
            ),
        )
    if not svc.delete(skill_id):
        raise HTTPException(status_code=404, detail=f"skill {skill_id!r} not found")
    return None


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/skills
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/skills", response_model=list[Skill])
def list_project_skills(project_id: str, request: Request) -> list[dict[str, Any]]:
    """项目当前绑定的 skill 集合（**有序**：``ord`` 小者在前）+ 全文 payload。"""
    _ensure_project(request, project_id)
    return _service(request).list_project_skills(project_id)


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/skills/bind
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/skills/bind", response_model=list[Skill])
def bind_project_skills(
    project_id: str, payload: SkillBindRequest, request: Request,
) -> list[dict[str, Any]]:
    """绑定 skill 到项目（**多 slot 有序**，覆盖式）。

    404：项目或任一 skill 不存在（**整体失败，不留半套绑定**）。响应含绑定后的
    有序集合（全文）。
    """
    code, skills = _service(request).bind(project_id, payload.skill_ids)
    if code == BindStatus.PROJECT_NOT_FOUND:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )
    if code == BindStatus.SKILL_NOT_FOUND:
        raise HTTPException(
            status_code=404, detail="one or more skills not found"
        )
    return skills or []


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/skills/unbind
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/skills/unbind", response_model=list[Skill])
def unbind_project_skill(
    project_id: str, payload: SkillUnbindRequest, request: Request,
) -> list[dict[str, Any]]:
    """解绑项目的某个 skill（幂等：未绑定也返回 200，集合照常返回）。

    404：项目不存在。
    """
    code, skills = _service(request).unbind(project_id, payload.skill_id)
    if code == BindStatus.PROJECT_NOT_FOUND:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )
    log.info("skill unbound: project=%s skill=%s", project_id, payload.skill_id)
    return skills or []


__all__ = ["router"]
