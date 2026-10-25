"""workflows 路由：run 查询 / 详情 / 列表与只读派生（GET 系列）。

V4.0 模块化重构 V2：自 ``packages/core/api/routers/workflows.py`` 按端点域拆出，
纯搬家零逻辑变更。端点（挂在 ``/api`` 前缀下）：

- ``GET /projects/{project_id}/init-status``     — project-init 四环节落库状态
- ``GET /runs/{run_id}``                         — run + 节点明细（PAUSED 附 pause_payload）
- ``GET /projects/{project_id}/runs``            — list runs（附 workflow_name / label）
- ``GET /chapters/{chapter_id}/context-preview`` — dry-run：LLM context 装配预览
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from packages.core.db import get_connection
from packages.core.logging_config import get_logger
from packages.core.workflow_runtime.runs import (
    get_run,
    get_workflow_name_for_run,
    list_runs,
)
from packages.domain.chapter.service import ChapterService
from packages.domain.project.service import ProjectService
from packages.domain.volume.service import VolumeService

from .common import _extract_pause_payload
from .models import (
    ContextPreview,
    ProjectInitStatus,
    WorkflowRunDetail,
    WorkflowRunSummary,
)

log = get_logger("novelos.routers.workflows")

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


@router.get("/projects/{project_id}/init-status", response_model=ProjectInitStatus)
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


@router.get(
    "/runs/{run_id}",
    response_model=WorkflowRunDetail,
    # ⚠️ exclude_unset 而非 exclude_none：run 详情里 `chapter_id` / `error` /
    # `ended_at` / `current_node` / `instance_id` 常常**键在、值为 null**（成功 run 的
    # error、未到终态的 ended_at、project-init 的 chapter_id 都是如此）。用
    # exclude_none 会把它们全抹掉 ⇒ 13 键变 9 键，等于把契约改小。
    # 而 `pause_payload` / `stage_models` 只在 PAUSED 时由端点附加，不加开关又会恒以
    # null 出现。exclude_unset 精确复现实测（非 PAUSED 13 键 / PAUSED 15 键）。
    # 详见 models.py 的 WorkflowRunDetail 注释。
    response_model_exclude_unset=True,
)
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
            # 降级语义（2026-09-27 A-007）：label 是**装饰性派生字段**（run 列表展示），
            # 草稿版本号取不到不影响 run 的任何判定，故回落成不带版本号的 `zh` 是安全的。
            # 但必须显式留痕——静默回落会让「取号失败」与「该 run 真没有草稿」读数相同，
            # 而两者排障方向完全相反。
            log.warning(
                "runs.label: 草稿版本取号失败，label 降级为不带版本号（run_id=%s chapter_id=%s）",
                run.get("run_id"),
                chapter_id,
                exc_info=True,
            )
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
            # 降级语义同上（A-007，chapter-review 分支）：被评审的草稿版本号取不到时
            # 回落成不带版本号的 `zh`。这只影响展示，**不改变评审实际审的版本**
            # ——后者由 basic_checks 从 checkpoint_json 的 draft_version 决定。
            log.warning(
                "runs.label: 被评审草稿版本取号失败，label 降级为不带版本号（run_id=%s chapter_id=%s）",
                run.get("run_id"),
                chapter_id,
                exc_info=True,
            )
            return zh
    return zh


def _run_visible_in_project(
    run: dict[str, Any],
    *,
    chapter_service: ChapterService,
    project_id: str,
) -> bool:
    """单个 run 是否属于 ``project_id``（M7 修复的**判定单点**）。

    判定口径（按权威性从高到低）：

    1. **归属列** ``workflow_runs.project_id``（0037 迁移）——引擎写 run 行时带出，
       0037 再用 ``json_extract(checkpoint_json, '$.project_id')`` 回填存量 ⇒
       非空即**权威归属**，直接比字符串。
    2. **回落 chapter→project 反查**——归属列为空但 run 带 chapter_id 时
       （0037 前的存量行）：按章归属判，与既有可见性一致。
    3. **无从归属 ⇒ 保持全可见**——归属列为空**且**无 chapter_id（adhoc 裸 run /
       checkpoint 里没有 project_id 因而回填不出的存量行）：**判不出来** ⇒
       不排除（见下方「NULL 归属行的语义」）。

    ⚠️ **NULL 归属行的语义（结构性事实，不是「历史遗留」）**：归属列为空**不等于**
    「这条 run 属于所有项目」，而是**判不出来** ⇒ 不排除。产生 NULL 的路径有两条，
    必须分开说：

    * **project-init「新建项目」模式**——run 行是在项目**还不存在**时插进去的
      （引擎按起跑时已知的 project_id 写归属列 ⇒ 该路径**建项时恒为 NULL**）；
      **2026-10-25 起在建项成功后回写归属**（见
      ``packages/workflows/project_init/pipeline.py::_claim_run_ownership``）
      ⇒ **新建路径不再产生 NULL 归属行**。
    * **真正判不出来的存量行**（0037 的回填依据是 ``checkpoint_json`` 顶层
      ``project_id``；checkpoint 为 ``{}`` 或无该键就回填不出——dev 库实测 2 条，
      2026-10-24 只读查询）⇒ **保持全可见**（跨项目可见的代价被显式登记在此，
      而不是藏在 SQL 的 ``OR chapter_id IS NULL`` 里）。

    ⇒ **两条路径的可见性后果相同**（「不可判 ⇒ 不排除」），但**成因不同**：
    前者已被回写堵住，后者是判不出来的既有事实。

    判别：``tests/api/test_run_list_project_scope.py``（A 的列表不得含 B 的
    项目级 run；撤掉过滤 ⇒ 必红）。
    """
    owner = run.get("project_id")
    if owner is not None:
        return owner == project_id
    chapter_id = run.get("chapter_id")
    if chapter_id is None:
        return True  # 无从归属 ⇒ 保持全可见（口径见上）
    return chapter_service.get_project_id(chapter_id) == project_id


def _filter_runs_by_project(
    db_path: str | Path,
    project_id: str,
    runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按项目归属过滤 run 列表（M7 修复，2026-10-24）。

    **为什么数据层不拦**：``workflow_runtime.runs.list_runs`` 的 SQL 口径是
    「chapter→project 反查 **OR** ``chapter_id IS NULL``」——后半句让**每个**
    项目的项目级 run（project-init / outline-batch 等无 chapter 的 run）出现在
    **所有**项目的列表里。0037 落地归属列后，这里按归属列收口。

    带 chapter 的 run 仍由数据层的 chapter 反查负责（其归属列与章归属一致），
    本函数对「归属列为空的老行」再兜一次 chapter 反查，两侧口径一致 ⇒
    **有 chapter 的 run 可见性零变化**（既有测试全绿）。
    """
    chapter_service = ChapterService(db_path)
    return [
        run
        for run in runs
        if _run_visible_in_project(
            run, chapter_service=chapter_service, project_id=project_id
        )
    ]


@router.get("/projects/{project_id}/runs", response_model=list[WorkflowRunSummary])
def list_runs_endpoint(project_id: str, request: Request) -> list[dict[str, Any]]:
    """列 project 下的 run（每行 13 键，实测）。

    ``response_model`` 只在**数组的 items** 上生效——``list[dict]`` 在 OpenAPI 里
    是匿名 object，前端拿到的仍是 ``Record<string, unknown>``。加了
    ``list[WorkflowRunSummary]`` 后 items 才有具名 ``$ref``。

    ⚠️ **刻意不加** ``response_model_exclude_none`` / ``exclude_unset``：本端点
    **没有条件键**——``chapter_id`` / ``error`` / ``ended_at`` / ``label`` 都是
    「键恒在、值可为 null」，任一开关都会把它们抹掉，把 13 键砍成更少，等于改契约。
    判别：``tests/api/test_state_list_response_contract.py::test_list_endpoints_declare_no_exclude_switches``

    ⚠️ **项目隔离（M7，2026-10-24）**：本端点只列**本项目**的 run。
    项目级 run（project-init / outline-batch，无 chapter）的归属取 0037 的
    ``workflow_runs.project_id`` 列；归属列为空的行保持**全项目可见**
    （**判不出来 ⇒ 不排除**，口径与成因见 :func:`_run_visible_in_project`）。
    判别：``tests/api/test_run_list_project_scope.py``
    """
    settings = request.app.state.settings
    db_path = settings.db_path
    runs = list_runs(db_path, project_id)
    # M7（2026-10-24）：按项目归属过滤——数据层 SQL 的「OR chapter_id IS NULL」
    # 会把**所有**项目的项目级 run（project-init / outline-batch 等）泄进每个
    # 项目的列表。口径见 :func:`_run_visible_in_project`（含「归属判不出来 ⇒
    # 不排除」的语义登记）。过滤放在补 workflow_name / label **之前**，
    # 免得为看不见的行白跑反查查询。
    runs = _filter_runs_by_project(db_path, project_id, runs)
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


@router.get("/chapters/{chapter_id}/context-preview", response_model=ContextPreview)
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
