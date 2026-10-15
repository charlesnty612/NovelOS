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
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from packages.core.backup import BackupService
from packages.core.logging_config import get_logger

log = get_logger("novelos.routers.backup")

router = APIRouter(tags=["backup"])


def _db_path(request: Request) -> str:
    return str(request.app.state.settings.db_path)


def _service(request: Request) -> BackupService:
    return BackupService(_db_path(request))


# ---------------------------------------------------------------------------
# 出参模型（2026-10-15 批次（六））
# ---------------------------------------------------------------------------
# 形状来源：临时库 + 合成夹具跑通端点后 dump 实际键集，不是照 ``export_project``
# 源码读出来的。实测结论（GET /backup，见 tests/api/test_misc_router_response_contract.py）：
#   顶层恒在 7 键：format / version / exported_at / exported_from_project_id /
#                  metadata / project / tables
#   metadata 恒在 10 键（4 个导出事实 + 6 个排除标志）；project / tables 内部的
#   键集随**迁移**漂移（projects 表 0029 加 writing_bible、0031 加 fast_mode），
#   故一律 ``extra="allow"`` 且声明成不透明容器——把它们硬编成字段等于在
#   「下一个迁移」那天静默截断整段行数据。
#
# ⚠️ **GET /backup 的 response_model 是「文档级」而非「执行级」**：handler 直接返回
# ``JSONResponse``（为带 Content-Disposition 触发浏览器下载），FastAPI 见到
# ``Response`` 实例就跳过序列化与校验，声明的模型只进 OpenAPI。
# 它的判别点因此落在 **OpenAPI schema** 上（响应体那条路对任何字段声明都恒真）。
# POST /import-backup 返回裸 dict，response_model 是**执行级**的，会真的过滤。


class BackupMetadata(BaseModel):
    """备份包 ``metadata`` 段：导出事实 + 安全排除标志。

    6 个 ``*_excluded / *_stripped`` 恒为 True（它们是「导出侧显式声明排除了
    哪些敏感表」的事实记录，见 :data:`packages.core.backup.service._BACKUP_METADATA_FLAGS`），
    不是可配置开关。
    """

    model_config = ConfigDict(extra="allow")

    schema_migrations: list[str]
    table_count_exported: int
    exported_table_names: list[str]
    exported_at_iso: str
    api_keys_stripped: bool
    ai_call_logs_excluded: bool
    evaluations_excluded: bool
    workflow_runs_excluded: bool
    model_configs_excluded: bool
    reference_canons_excluded: bool


class BackupPackage(BaseModel):
    """备份包顶层（``GET /projects/{project_id}/backup`` 的响应体）。

    ``format`` / ``version`` / ``exported_at`` / ``project`` / ``tables`` 是导入端
    强校验的 5 个必填键（:data:`packages.core.backup.schema.REQUIRED_TOP_KEYS`）；
    ``exported_from_project_id`` 与 ``metadata`` 是导入端不强制、导出端恒写的增强键。
    """

    model_config = ConfigDict(extra="allow")

    format: str
    version: int
    exported_at: str
    exported_from_project_id: str
    metadata: BackupMetadata
    # projects 行的完整 dict；列集随迁移变，故不透明
    project: dict[str, Any]
    # 表名 → 行 dict 列表（23 张白名单表，行列集各自不同）
    tables: dict[str, list[dict[str, Any]]]


class ImportedProject(BaseModel):
    """``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。

    ⚠️ 这是**裸 DB 行**（含 ``word_band_json`` 原文），**不是** ``domain`` 层的
    ``Project``（后者把该列解析成 ``word_band`` dict 且不含 ``fast_mode``）——
    两者键集不同，混用会让前端按 ``Project`` 读到 ``word_band === undefined``。

    ``import_warnings`` 是**条件键**：:data:`BackupService._import_warnings` 非空时才
    挂上来（``commits.rollback_of`` 归一化失败的留痕，见 AGENTS 坑区「备份同库重导入」）。
    声明成 ``| None`` 是为了能通过校验，端点侧配 ``response_model_exclude_unset=True``
    才不会把「缺席」渲染成一个凭空的 ``null``——那会让作者以为出了警告。
    """

    model_config = ConfigDict(extra="allow")

    project_id: str
    name: str
    status: str
    created_at: str
    updated_at: str
    premise: str | None = None
    genre: str | None = None
    target_words: int | None = None
    foreshadow_overdue_chapters: int
    word_band_json: str | None = None
    genre_pack_id: str | None = None
    writing_bible: str | None = None
    # 迁移 0031：NULL=未设置 / 0=显式关 / 1=显式开（三态，勿当 bool 用）
    fast_mode: int | None = None
    import_warnings: list[str] | None = None


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/backup —— 下载
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/backup", response_model=BackupPackage)
def download_project_backup(project_id: str, request: Request) -> Response:
    """导出项目备份 JSON。

    返回 dict 由 FastAPI 自动 JSON 序列化；额外通过 ``Content-Disposition``
    让浏览器保存为 ``backup-<project_id>.json`` 文件。

    项目不存在 → 404；包构造异常 → 500。

    ⚠️ 返回的是 ``JSONResponse`` 实例（为挂 Content-Disposition），**不是** dict——
    原注解 ``-> dict`` 是错的（FastAPI 见到 ``Response`` 实例就跳过序列化，
    那个注解从未生效过）。本函数改为 ``response_model=BackupPackage`` + ``-> Response``：
    模型只进 OpenAPI（契约文档），**不做运行时过滤**。判别用例因此断言 OpenAPI 的
    ``properties``，而不是响应体——对响应体的断言在本端点恒真。
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
    response_model=ImportedProject,
    # ⚠️ exclude_unset 是**必需**的，不是洁癖：`import_warnings` 是条件键
    # （BackupService 只在 rollback_of 归一化失败时才挂），默认序列化会把它
    # 渲染成一个凭空的 null ——「没有警告」与「有警告但列表为空」在前端不可区分，
    # 而后者恰恰是要作者看见的。exclude_unset 让「键缺席」保持缺席。
    response_model_exclude_unset=True,
)
def import_project_backup(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """导入备份包为新项目，返回新项目 dict。

    请求体：JSON 对象（顶层 5 字段必填；详见 :mod:`packages.core.backup.schema`）。
    返回：201 + 新项目 dict（裸 DB 行，见 :class:`ImportedProject`）。

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
