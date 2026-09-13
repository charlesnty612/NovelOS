"""workflows 路由：run 查询 / 详情 / 列表与只读派生（GET 系列）。

V4.0 模块化重构 V2：自 ``packages/core/api/routers/workflows.py`` 按端点域拆出，
纯搬家零逻辑变更。端点（挂在 ``/api`` 前缀下）：

- ``GET /projects/{project_id}/init-status``     — project-init 四环节落库状态
- ``GET /runs/{run_id}``                         — run + 节点明细（PAUSED 附 pause_payload）
- ``GET /projects/{project_id}/runs``            — list runs（附 workflow_name / label）
- ``GET /chapters/{chapter_id}/context-preview`` — dry-run：LLM context 装配预览
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from packages.core.db import get_connection
from packages.core.workflow_runtime.runs import (
    get_run,
    get_workflow_name_for_run,
    list_runs,
)
from packages.domain.chapter.service import ChapterService
from packages.domain.project.service import ProjectService
from packages.domain.volume.service import VolumeService

from .common import _extract_pause_payload

router = APIRouter(tags=["workflows"])


# ---------------------------------------------------------------------------
# Helpers（只读派生）
# ---------------------------------------------------------------------------


def _collect_stage_models(db_path: str, run_id: str) -> dict[str, str]:
    """反查 run 期间各 AI 节点实际调用的模型。

    联查 ``ai_call_logs`` 与 ``agents``，按 ``agents.name``（即 node_id / agent 名）
    分组取该 agent 最新一次「成功」调用的 ``model_id``。判定口径：
    ``error IS NULL`` 或 ``error LIKE 'warn:%'`` 前缀软告警（重试后成功场景）。

    返回 ``{agent_name: model_id}``；无任何成功调用时为空 dict（合法）。
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.name AS agent_name, l.model_id AS model_id, l.created_at AS created_at
            FROM ai_call_logs l
            JOIN agents a ON a.agent_id = l.agent_id
            WHERE l.run_id = ?
              AND (l.error IS NULL OR l.error LIKE 'warn:%')
              AND l.model_id IS NOT NULL
            ORDER BY l.created_at DESC
            """,
            (run_id,),
        ).fetchall()
    seen: dict[str, str] = {}
    for row in rows:
        name = row["agent_name"]
        if name in seen:
            continue
        seen[name] = row["model_id"]
    return seen


def _init_status_for_project(db_path: str, project_id: str) -> dict[str, Any]:
    """读项目下四环节落库数据，推导完成状态（不建新表、不调 AI）。"""
    from packages.domain.character.service import CharacterService
    from packages.domain.world.service import WorldService

    # premise：projects.premise 非空
    proj = ProjectService(db_path).get(project_id)
    if proj is None:
        return None  # type: ignore[return-value]
    premise_done = bool((proj.get("premise") or "").strip())
    premise_detail = (
        "已有 premise 文本" if premise_done else "未生成"
    )

    # world：locations/factions/world_rules 任一表该 project 有行
    world_svc = WorldService(db_path)
    loc_count = len(world_svc.list_locations(project_id))
    fac_count = len(world_svc.list_factions(project_id))
    rule_count = len(world_svc.list_world_rules(project_id))
    world_total = loc_count + fac_count + rule_count
    world_done = world_total > 0
    world_detail_parts: list[str] = []
    if loc_count:
        world_detail_parts.append(f"{loc_count} 个位置")
    if fac_count:
        world_detail_parts.append(f"{fac_count} 个势力")
    if rule_count:
        world_detail_parts.append(f"{rule_count} 条规则")
    world_detail = "已有 " + "、".join(world_detail_parts) if world_done else "未生成"

    # character：characters 表该 project 有行
    char_count = len(CharacterService(db_path).list_by_project(project_id))
    character_done = char_count > 0
    character_detail = f"已有 {char_count} 个角色" if character_done else "未生成"

    # outline：volumes 表有行 且 chapters 有行，且卷有标题、章节有一句话剧情（排除空壳落库）
    volumes = VolumeService(db_path).list(project_id)
    chapters = ChapterService(db_path).list_by_project(project_id)
    has_real_volume = any((v.get("title") or "").strip() for v in volumes)
    has_real_chapters = any((ch.get("plan_json") or {}).get("chapter_goal") for ch in chapters)
    outline_done = has_real_volume and has_real_chapters
    if outline_done:
        outline_detail = f"已有 {len(volumes)} 卷 / {len(chapters)} 章"
    elif bool(volumes) or bool(chapters):
        outline_detail = "已有空壳卷纲（标题/章节剧情为空），需重新生成"
    else:
        outline_detail = "未生成"

    return {
        "stages": [
            {"stage": "premise", "label": "题材定位", "done": premise_done, "detail": premise_detail},
            {"stage": "world", "label": "世界观", "done": world_done, "detail": world_detail},
            {"stage": "character", "label": "核心角色", "done": character_done, "detail": character_detail},
            {"stage": "outline", "label": "卷纲与章节种子", "done": outline_done, "detail": outline_detail},
        ],
        "has_any_data": any([
            premise_done, world_done, character_done, outline_done,
        ]),
    }


# ---------------------------------------------------------------------------
# Endpoints（GET 系列）
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/init-status")
def get_init_status(project_id: str, request: Request) -> dict[str, Any]:
    """返回 project-init 四环节各自的落库完成状态（数据推导，不建新表）。

    - 404：project 不存在。
    - 各环节 ``done`` 判定：
      - premise：``projects.premise`` 非空。
      - world：``locations``/``factions``/``world_rules`` 任一表该 project 有行。
      - character：``characters`` 表该 project 有行。
      - outline：``volumes`` 表有行 且 ``chapters`` 有行。
    """
    settings = request.app.state.settings
    payload = _init_status_for_project(settings.db_path, project_id)
    if payload is None:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )
    return payload


@router.get("/runs/{run_id}")
def get_run_endpoint(run_id: str, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    db_path = settings.db_path
    run = get_run(db_path, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    # 恢复挂起的初始化：PAUSED 时附加 pause_payload + workflow_name，便于前端刷新后直接接 review。
    # 非 PAUSED 状态不附加 pause_payload，保持响应体最小。
    if run.get("status") == "PAUSED":
        run["pause_payload"] = _extract_pause_payload(run)
        # 各 AI 节点本次实际调用的 model_id（按 agent 名聚合），供审阅卡片区分
        # 「下拉显示的全局绑定」与「本次实际使用的模型」，避免误读。
        run["stage_models"] = _collect_stage_models(db_path, run_id)
    run["workflow_name"] = get_workflow_name_for_run(db_path, run_id)
    return run


# workflow_name → 人类可读中文名（list 端点用；与 UI 列表文案对齐）
_WORKFLOW_ZH_LABELS: dict[str, str] = {
    "chapter-plan": "生成计划",
    "chapter-write": "写正文",
    "chapter-review": "审校",
    "chapter-commit": "提交",
    "project-init": "AI 初始化",
}


def _compute_run_label(
    db_path: str,
    workflow_name: str | None,
    run: dict[str, Any],
) -> str | None:
    """为单 run 推导人类可读 label。

    - chapter-write：查 run 期间（[started_at, ended_at|∞)）产出的草稿最大版本
      → 有则 label="写正文 → 草稿 v{v}"，无则 "写正文"。
    - chapter-review：查启动时（<=started_at）最新草稿版本
      → 有则 label="审校（审 v{v}）"，无则 "审校"。
    - 其他（生成计划 / 提交 / AI 初始化）：直接用中文名。
    - 推导异常：兜底为中文名，不抛错。
    """
    zh = _WORKFLOW_ZH_LABELS.get(workflow_name or "")
    if zh is None:
        return None
    chapter_id = run.get("chapter_id")
    started_at = run.get("started_at")
    if not chapter_id or not started_at:
        return zh
    if workflow_name == "chapter-write":
        try:
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    """
                    SELECT MAX(version) AS v FROM drafts
                    WHERE chapter_id = ?
                      AND created_at >= ?
                      AND created_at <= COALESCE(?, '9999-12-31T23:59:59Z')
                    """,
                    (chapter_id, started_at, run.get("ended_at")),
                ).fetchone()
            finally:
                conn.close()
            v = row["v"] if row else None
            return f"{zh} → 草稿 v{v}" if v is not None else zh
        except Exception:
            return zh
    if workflow_name == "chapter-review":
        # 优先从 checkpoint_json（已解析为 ctx dict，顶层即变量键）取用户显式指定的
        # draft_version；与 basic_checks 实际审的版本一致，确保 label 准确。
        ckpt = run.get("checkpoint_json") or {}
        specified = ckpt.get("draft_version") if isinstance(ckpt, dict) else None
        if isinstance(specified, int):
            return f"{zh}（审 v{specified}）"
        try:
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    """
                    SELECT MAX(version) AS v FROM drafts
                    WHERE chapter_id = ?
                      AND created_at <= ?
                    """,
                    (chapter_id, started_at),
                ).fetchone()
            finally:
                conn.close()
            v = row["v"] if row else None
            return f"{zh}（审 v{v}）" if v is not None else zh
        except Exception:
            return zh
    return zh


@router.get("/projects/{project_id}/runs")
def list_runs_endpoint(project_id: str, request: Request) -> list[dict[str, Any]]:
    settings = request.app.state.settings
    db_path = settings.db_path
    runs = list_runs(db_path, project_id)
    # 恢复挂起的初始化：每行附加 workflow_name（get_workflow_name_for_run 反查）；
    # 一行一次查询可接受，list 端点不在热路径上。其余字段保持不变。
    # 同时附加人类可读 label（推导版，不改 DB）。
    for run in runs:
        wf = get_workflow_name_for_run(db_path, run["run_id"])
        run["workflow_name"] = wf
        run["label"] = _compute_run_label(db_path, wf, run)
    return runs


# ---------------------------------------------------------------------------
# Sprint 13 下半：context-preview（dry-run；只读、不调 LLM、不写库）。
# ---------------------------------------------------------------------------


@router.get("/chapters/{chapter_id}/context-preview")
def get_chapter_context_preview(chapter_id: str, request: Request) -> dict[str, Any]:
    """dry-run：返回 chapter 关联的 LLM context 装配预览。

    按 L0/L1/L2 分层，每层包含 ``token_estimate`` + ``items`` 条目清单 +
    ``total_tokens`` + ``token_budget``。**只读**，不调 LLM，不写 ai_call_logs。
    """
    from packages.core.context_engine import preview_context

    settings = request.app.state.settings
    db = settings.db_path

    # V1.5 越层整改：通过 ChapterService 取 chapter.project_id + 404 校验
    project_id = ChapterService(db).get_project_id(chapter_id)
    if project_id is None:
        raise HTTPException(
            status_code=404, detail=f"chapter {chapter_id!r} not found"
        )

    try:
        return preview_context(db, project_id, chapter_id)
    except ValueError as exc:
        # builder 抛的 project/chapter 不存在 → 404
        raise HTTPException(status_code=404, detail=str(exc)) from exc
