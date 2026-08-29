"""Workflow REST 路由（Sprint 4-A + V1.5 架构整理 + P0 自动改稿回路）。

端点（挂在 ``/api`` 前缀下）：
- ``POST /projects/{project_id}/chapters/{chapter_id}/plan``    — 启动 chapter-plan
- ``POST /projects/{project_id}/chapters/{chapter_id}/write``   — 启动 chapter-write
- ``POST /projects/{project_id}/chapters/{chapter_id}/review``  — 启动 chapter-review
- ``POST /projects/{project_id}/chapters/{chapter_id}/commit``  — 启动 chapter-commit
- ``GET  /runs/{run_id}``                                          — run + 节点明细
- ``POST /runs/{run_id}/resume``                                   — 恢复 PAUSED run（P0 支持 auto_revise 自动改稿回路）
- ``GET  /projects/{project_id}/runs``                             — list runs
- ``GET  /chapters/{chapter_id}/context-preview``                  — Sprint 13 下半 dry-run

请求体：
- ``{author_intent?: str, mock_providers?: {agent_name: [str, ...]}}`` — start
- ``{human_input?: dict, auto_revise_max?: int, mock_providers?: {...}}`` — resume

返回：
- 201（start）→ ``{run_id, status, ...}``；若 PAUSED 则附加 ``pause_payload``
- 200（get/list/resume）→ run dict（含 nodes 数组）或 list

V1.5 越层整改：
- 路由不再直接写 SQL——chapter 校验走 :class:`ChapterService.get_project_id`，
  resume 的 workflow_name 反查走 :func:`packages.core.workflow_runtime.runs.get_workflow_name_for_run`。

P0 自动改稿回路：
- ``resume`` chapter-review 时若 human_input 决议为 revise，且 ``auto_revise_max > 0``，
  则自动依次重跑 chapter-write → chapter-review，直到 approved（COMPLETED）或达到上限。
- 上限由请求体 ``auto_revise_max`` 或环境变量 ``NOVELOS_AUTO_REVISE_MAX`` 决定，默认 ``2``，
  ``0`` 表示禁用（保持现状）。
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, Field

from packages.core.logging_config import get_logger
from packages.core.workflow_registry import get_workflow
from packages.core.db import get_connection
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import (
    get_run,
    get_workflow_name_for_run,
    list_runs,
)
from packages.domain.chapter.service import ChapterService
from packages.domain.project.service import ProjectService
from packages.domain.volume.service import VolumeService

log = get_logger("novelos.routers.workflows")

router = APIRouter(tags=["workflows"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StartWorkflowRequest(BaseModel):
    author_intent: str | None = None
    expected_role: str | None = None
    target_word_count: int | None = Field(default=None, ge=100, le=100_000)
    mock_providers: dict[str, list[str]] | None = None
    # Sprint 6 下半：chapter_commit pipeline 的 quality_gate 节点拦截模式
    # （"enforce" 阻断 / "report" 不阻断）；任务书拍板默认 report；
    # 显式注入便于测试覆盖两种模式。
    quality_gate_mode: str | None = None
    # V3 P0-1：chapter-review critic 采样模式（off / sample / always）。
    # 仅 chapter-review 节点读取；其他 workflow 忽略。优先级高于 NOVELOS_CRITIC_MODE 环境变量。
    critic_mode: str | None = Field(default=None, pattern="^(off|sample|always)$")
    # 单次 run 级别模型档案覆盖：key=capability 名（如 creative_writing/reasoning/light），
    # value=model_profiles.profile_id。本次 run 对应 capability 的 agent 调用强制使用该档案，
    # 不影响全局 capability_bindings。None 或缺省 → 不覆盖（走既有 capability_bindings / model_configs）。
    model_overrides: dict[str, str] | None = None
    # chapter-write 全章重写：True 时忽略 plan_json.revision_note 与最新 draft，
    # 强制走 write 模式（用于跨模型文风对比）。None/False → 维持既有 revise 判定。
    fresh_write: bool | None = None


class ProjectInitRequest(BaseModel):
    """project-init 触发请求体。

    - ``brief`` 必填：genre / logline / platform / target_words / title / author_notes。
    - ``project_id`` 可选：传入则挂载到已有项目并更新；不传则创建新项目。
    - ``chapter_seed_count`` 可选：默认 10 章。
    - ``mock_providers`` 可选：测试用脚本化 LLM 输出。
    - ``selected_stages`` 可选：仅跑白名单内的环节；未选环节从落库数据重建为
      下游 AI 输入，不调 AI、不抛 PauseRequested。省略时全选（与既有行为一致）。
    """

    brief: dict[str, Any]
    project_id: str | None = None
    # 上限 500：与 pipeline 推导 clamp（[10,500]）对齐，支持百万字 ÷ 单章 3000 ≈ 333 章的推导结果
    chapter_seed_count: int | None = Field(default=None, ge=1, le=500)
    mock_providers: dict[str, list[str]] | None = None
    # 分段审阅：True 时按 4 关卡（题材定位 → 世界观 → 核心角色 → 卷纲与章节种子）
    # 暂停等待人工审阅修订，通过 resume 端点 human_input.revisions 回灌并生效；
    # 省略/False 时保持一次性跑完（与既有行为一致）。
    step_mode: bool | None = None
    # 环节可选复用：仅跑白名单内的环节；未选环节从落库数据重建为下游 AI 输入。
    # 省略或 null 时等价于 ["premise", "world", "character", "outline"]。
    # 非法值（不在白名单内）→ 422。
    selected_stages: list[str] | None = None


PROJECT_INIT_STAGES: tuple[str, ...] = ("premise", "world", "character", "outline")


def _validate_selected_stages(stages: list[str] | None) -> list[str] | None:
    """校验 selected_stages：None 通过；空列表视为「全部跳过」，422；
    任一元素不在 PROJECT_INIT_STAGES → 422。
    """
    if stages is None:
        return None
    if not isinstance(stages, list) or not stages:
        raise HTTPException(
            status_code=422,
            detail="selected_stages 不能为空列表；省略则全选",
        )
    invalid = [s for s in stages if not isinstance(s, str) or s not in PROJECT_INIT_STAGES]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"selected_stages 含非法值 {invalid!r}；允许 {list(PROJECT_INIT_STAGES)}",
        )
    # 去重并按既定顺序输出，行为稳定便于测试
    seen: set[str] = set()
    ordered: list[str] = []
    for s in stages:
        if s in seen:
            continue
        seen.add(s)
        ordered.append(s)
    return ordered


class ResumeRequest(BaseModel):
    human_input: dict[str, Any] | None = None
    # P0 自动改稿回路：resume chapter-review 被 revise 驳回时，可自动重跑 write→review。
    # 默认 2；0 表示禁用（保持原有 FAILED 终态）。
    auto_revise_max: int | None = Field(default=None, ge=0, le=10)
    # 自动回路中新的 write / review run 需要 mock 脚本时传入；不传则从原 run checkpoint 继承。
    mock_providers: dict[str, list[str]] | None = None
    # 重生成：true 时重跑当前挂起节点自身（而非下一节点）；可与 human_input.regenerate_note
    # 配合传一条重生成意见，pipeline 会注入到对应 AI 节点的 payload。
    regenerate: bool | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine(request: Request) -> WorkflowEngine:
    settings = request.app.state.settings
    return WorkflowEngine(settings.db_path)


def _check_chapter(request: Request, project_id: str, chapter_id: str) -> tuple[str, str]:
    """校验 chapter 属于该 project；返回 (project_id, chapter_id)。

    V1.5 起：通过 :class:`ChapterService.get_project_id` 取 project_id；
    chapter 不存在 → 404；chapter 不属于该 project → 400。
    """
    settings = request.app.state.settings
    chapter_pid = ChapterService(settings.db_path).get_project_id(chapter_id)
    if chapter_pid is None:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    if chapter_pid != project_id:
        raise HTTPException(
            status_code=400,
            detail=f"chapter {chapter_id!r} does not belong to project {project_id!r}",
        )
    return chapter_pid, chapter_id


def _check_active_run_for_chapter(
    db_path: str,
    *,
    chapter_id: str,
    exclude_run_id: str | None = None,
) -> dict[str, Any] | None:
    """同 chapter 下是否存在 RUNNING/PENDING 的活跃 run。

    用于 API 层的并发防护：
    - ``_start_workflow``：启动新 workflow 前查同 chapter 是否有活跃 run；有则 409。
    - ``resume_run``：resume 自身（PAUSED）前查同 chapter 是否还有**其他**RUNNING/PENDING
      run；有则 409，避免两条 run 并行修改 chapter 状态/数据。

    ``exclude_run_id``：resume 场景下传当前 run_id，把自身从判定里排除（自身已 RUNNING
    之前不会 PAUSED，但保留参数对称便于复用）。

    返回首个匹配的活跃 run 行 dict；无活跃 run → None。
    """
    conn = get_connection(db_path)
    try:
        if exclude_run_id:
            row = conn.execute(
                """
                SELECT run_id, status FROM workflow_runs
                WHERE chapter_id = ?
                  AND run_id != ?
                  AND status IN ('RUNNING', 'PENDING')
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (chapter_id, exclude_run_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT run_id, status FROM workflow_runs
                WHERE chapter_id = ?
                  AND status IN ('RUNNING', 'PENDING')
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (chapter_id,),
            ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"run_id": row["run_id"], "status": row["status"]}


def _init_genesis_if_needed(db_path: str, project_id: str, chapter_id: str) -> None:
    """如无快照则调 init_genesis 创建 v1 快照；供 chapter-write / commit 等依赖 state 的节点使用。"""
    from packages.core.story_state.service import StoryStateService

    svc = StoryStateService(db_path)
    state_version = int((svc.get_current_state(project_id) or {}).get("state_version") or 0)
    if state_version < 1:
        svc.init_genesis(project_id, chapter_id)


def _resolve_auto_revise_max(request_max: int | None) -> int:
    """解析自动改稿上限。

    优先级：请求体 ``auto_revise_max`` > 环境变量 ``NOVELOS_AUTO_REVISE_MAX`` > 默认 ``2``。
    ``0`` 表示禁用（保持原有 FAILED 终态）。
    """
    if request_max is not None:
        return int(request_max)
    env = os.environ.get("NOVELOS_AUTO_REVISE_MAX", "2").strip()
    try:
        return int(env)
    except (TypeError, ValueError):
        return 2


def _extract_pause_payload(run: dict[str, Any]) -> dict[str, Any] | None:
    """从 run.checkpoint_json 中抽取 PENDING 节点的 __pause_payload__。

    优先取与 ``current_node`` 对应节点的 payload；当前挂起节点不存在时回退到
    checkpoint 中任意 PENDING 节点的 payload（兼容极端场景）。
    """
    checkpoint = run.get("checkpoint_json") or {}
    current = run.get("current_node")
    if current and isinstance(checkpoint.get(current), dict) and "__pause_payload__" in checkpoint[current]:
        return checkpoint[current]["__pause_payload__"]
    for node_id, val in checkpoint.items():
        if isinstance(val, dict) and "__pause_payload__" in val:
            return val["__pause_payload__"]
    return None


def _run_workflow_return_payload(
    engine: WorkflowEngine,
    db_path: str,
    workflow_name: str,
    project_id: str,
    chapter_id: str,
    mock_providers: dict[str, list[str]] | None,
    initial_ctx_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """启动指定 workflow 并返回标准响应 payload（run_id / status / current_node / pause_payload）。"""
    workflow = get_workflow(workflow_name)
    if workflow is None:
        raise HTTPException(status_code=500, detail=f"workflow {workflow_name!r} not registered")

    if workflow_name in ("chapter-write", "chapter-commit", "chapter-review"):
        _init_genesis_if_needed(db_path, project_id, chapter_id)

    initial_ctx: dict[str, Any] = {
        "db_path": str(db_path),
        "project_id": project_id,
        "chapter_id": chapter_id,
    }
    if initial_ctx_extra:
        initial_ctx.update(initial_ctx_extra)

    run_id = engine.start_with_nodes_async(
        workflow_name,
        workflow["nodes"],
        chapter_id=chapter_id,
        initial_ctx=initial_ctx,
        mock_providers=mock_providers,
        checkpoint_exclude=workflow.get("checkpoint_exclude"),
    )

    # 异步启动后本函数仅被 auto_revise 回路内部使用：调用方依赖返回的
    # status 判断本轮 write/review 是否 COMPLETED/PAUSED/FAILED，因此必须
    # 同步等到终态（引擎已在后台线程推进，这里只轮询 run 行）。上限 600s
    # 覆盖真实 write（几分钟量级）；超时按 FAILED 语义返回，由调用方兜底。
    import time as _wait_t

    deadline = _wait_t.monotonic() + 600.0
    run: dict[str, Any] | None = None
    while _wait_t.monotonic() < deadline:
        run = get_run(db_path, run_id)
        if run is not None and run["status"] in ("COMPLETED", "PAUSED", "FAILED", "CANCELLED"):
            break
        _wait_t.sleep(0.5)
    if run is None:
        raise HTTPException(status_code=500, detail="run disappeared after start")
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": run["status"],
        "current_node": run.get("current_node"),
    }
    if run["status"] == "PAUSED":
        payload["pause_payload"] = _extract_pause_payload(run)
    return payload


def _auto_revise_loop(
    engine: WorkflowEngine,
    db_path: str,
    project_id: str,
    chapter_id: str,
    mock_providers: dict[str, list[str]] | None,
    max_iter: int,
) -> dict[str, Any]:
    """P0 自动改稿回路：重跑 chapter-write → chapter-review 直到 approved 或达上限。

    返回最终 run 的标准 payload。write 失败或 review 非 revise 失败时直接返回。
    review 达到 PAUSED（待人工审批）时直接返回 PAUSED。
    review 继续 revise 失败时进入下一轮，最多 ``max_iter`` 轮。
    """
    final_payload: dict[str, Any] | None = None
    for iteration in range(1, max_iter + 1):
        log.info(
            "auto_revise loop iteration %d/%d for chapter %s",
            iteration, max_iter, chapter_id,
        )
        # 1) 重跑 chapter-write：revision_note 已在 plan_json 中由上一轮 load_plan 带上
        write_payload = _run_workflow_return_payload(
            engine, db_path, "chapter-write", project_id, chapter_id, mock_providers
        )
        if write_payload["status"] != "COMPLETED":
            return write_payload

        # 2) 重跑 chapter-review
        review_payload = _run_workflow_return_payload(
            engine, db_path, "chapter-review", project_id, chapter_id, mock_providers
        )
        if review_payload["status"] == "COMPLETED":
            return review_payload
        if review_payload["status"] == "PAUSED":
            return review_payload

        # FAILED：检查是否为 rejected-for-revision，是则继续下一轮
        run = get_run(db_path, review_payload["run_id"])
        error = (run or {}).get("error") or ""
        if "rejected-for-revision" not in str(error):
            return review_payload
        final_payload = review_payload

    # 达到上限仍未 approved：返回最后一轮 review 的 FAILED payload
    return final_payload or {
        "run_id": "",
        "status": "FAILED",
        "current_node": None,
    }


def _start_workflow(
    *,
    workflow_name: str,
    request: Request,
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
) -> dict[str, Any]:
    _check_chapter(request, project_id, chapter_id)
    settings = request.app.state.settings
    db_path = settings.db_path

    # 并发防护：同 chapter 已有 RUNNING/PENDING run 时拒绝再启动。
    # 原因：章节级工作流（write/review/commit）会并发改 chapter 行 + plan_json + story_state，
    # 并行运行会产生数据竞态。前端 UX：用户先等当前 run 走到 PAUSED/COMPLETED/FAILED
    # 再触发下一轮（前端 list 端点会过滤活跃行）。
    active = _check_active_run_for_chapter(db_path, chapter_id=chapter_id)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"chapter {chapter_id!r} already has an active workflow run "
                f"(run_id={active['run_id']!r}, status={active['status']!r}); "
                f"wait for it to reach a terminal state before starting a new one"
            ),
        )

    # plan 不依赖 story_state；write/commit/review 需要 init_genesis（如尚未）
    if workflow_name in ("chapter-write", "chapter-commit", "chapter-review"):
        _init_genesis_if_needed(db_path, project_id, chapter_id)

    workflow = get_workflow(workflow_name)
    if workflow is None:
        raise HTTPException(status_code=500, detail=f"workflow {workflow_name!r} not registered")

    initial_ctx: dict[str, Any] = {
        "db_path": str(db_path),
        "project_id": project_id,
        "chapter_id": chapter_id,
    }
    if body.author_intent is not None:
        initial_ctx["author_intent"] = body.author_intent
    if body.expected_role is not None:
        initial_ctx["expected_role"] = body.expected_role
    if body.target_word_count is not None:
        initial_ctx["target_word_count"] = body.target_word_count
    if body.mock_providers:
        initial_ctx["mock_providers"] = body.mock_providers
    if body.quality_gate_mode is not None:
        initial_ctx["quality_gate_mode"] = body.quality_gate_mode
    # V3 P0-1：仅 chapter-review 节点读取；其他 workflow 收到此字段会被 pipeline 忽略。
    if body.critic_mode is not None:
        initial_ctx["critic_mode"] = body.critic_mode
    # 单次 run 级模型档案覆盖：仅当请求体显式提供（即使为空 dict）才塞 ctx；
    # 缺省（None）保持 ctx 不出现该键，避免被下游误读为「空覆盖」。
    if body.model_overrides is not None:
        initial_ctx["model_overrides"] = body.model_overrides
    # chapter-write 全新重写：仅 chapter-write 节点读取；其他 workflow 收到此字段会被 pipeline 忽略。
    if body.fresh_write:
        initial_ctx["fresh_write"] = True

    engine = _engine(request)
    try:
        run_id = engine.start_with_nodes_async(
            workflow_name,
            workflow["nodes"],
            chapter_id=chapter_id,
            initial_ctx=initial_ctx,
            mock_providers=body.mock_providers,
            checkpoint_exclude=workflow.get("checkpoint_exclude"),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 异步化后：start_with_nodes_async 立刻返回，run 行已落库（status=RUNNING）。
    # 不再有「同步执行中第一个节点就 PauseRequested 立即置 PAUSED」的窗口；HTTP 响应
    # 始终是 RUNNING。前端通过 GET /runs/{id} / list 端点轮询拿真实进度。
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": "RUNNING",
        "current_node": None,
    }
    return payload


def _start_project_init(
    *,
    request: Request,
    body: ProjectInitRequest,
) -> dict[str, Any]:
    """启动 project-init workflow。

    若 body.project_id 存在则校验并挂载；否则由 persist_all 节点创建新项目。
    """
    settings = request.app.state.settings
    db_path = settings.db_path

    workflow = get_workflow("project-init")
    if workflow is None:
        raise HTTPException(status_code=500, detail="workflow 'project-init' not registered")

    # 校验现有项目存在性
    if body.project_id is not None:
        from packages.domain.project.service import ProjectService

        if ProjectService(db_path).get(body.project_id) is None:
            raise HTTPException(
                status_code=404, detail=f"project {body.project_id!r} not found"
            )

    initial_ctx: dict[str, Any] = {
        "db_path": str(db_path),
        "brief": body.brief,
    }
    if body.project_id is not None:
        initial_ctx["project_id"] = body.project_id
    if body.chapter_seed_count is not None:
        initial_ctx["chapter_seed_count"] = body.chapter_seed_count
    if body.mock_providers:
        initial_ctx["mock_providers"] = body.mock_providers
    if body.step_mode is not None:
        initial_ctx["step_mode"] = body.step_mode
    # selected_stages：None → 全部缺省；非空时塞进 ctx 供 pipeline 各 AI 节点读取。
    selected_stages = _validate_selected_stages(body.selected_stages)
    if selected_stages is not None:
        initial_ctx["selected_stages"] = selected_stages

    engine = _engine(request)
    try:
        run_id = engine.start_with_nodes(
            "project-init",
            workflow["nodes"],
            initial_ctx=initial_ctx,
            mock_providers=body.mock_providers,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run = get_run(db_path, run_id)
    if run is None:
        raise HTTPException(status_code=500, detail="run disappeared after start")
    out: dict[str, Any] = {
        "run_id": run_id,
        "status": run["status"],
        "current_node": run.get("current_node"),
        "project_id": (run.get("checkpoint_json") or {}).get("project_id"),
    }
    if run["status"] == "PAUSED":
        out["pause_payload"] = _extract_pause_payload(run)
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/projects/init",
    status_code=status.HTTP_201_CREATED,
)
def start_project_init(
    body: ProjectInitRequest,
    request: Request,
) -> dict[str, Any]:
    """project-init：从题材 brief 生成 Story Bible 并落库。

    ``brief`` 字段示例::

        {
          "genre": "玄幻",
          "logline": "少年得古籍，逆天改命",
          "platform": "起点",
          "target_words": 300000,
          "title": "九天星辰诀",
          "author_notes": "快节奏，爽文"
        }
    """
    return _start_project_init(request=request, body=body)


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


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/plan",
    status_code=status.HTTP_201_CREATED,
)
def start_plan(
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
    request: Request,
) -> dict[str, Any]:
    return _start_workflow(
        workflow_name="chapter-plan",
        request=request,
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
    )


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/write",
    status_code=status.HTTP_201_CREATED,
)
def start_write(
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
    request: Request,
) -> dict[str, Any]:
    return _start_workflow(
        workflow_name="chapter-write",
        request=request,
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
    )


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/review",
    status_code=status.HTTP_201_CREATED,
)
def start_review(
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
    request: Request,
) -> dict[str, Any]:
    return _start_workflow(
        workflow_name="chapter-review",
        request=request,
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
    )


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/commit",
    status_code=status.HTTP_201_CREATED,
)
def start_commit(
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
    request: Request,
) -> dict[str, Any]:
    return _start_workflow(
        workflow_name="chapter-commit",
        request=request,
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
    )


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
    run["workflow_name"] = get_workflow_name_for_run(db_path, run_id)
    return run


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str, body: ResumeRequest, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    db_path = settings.db_path

    run = get_run(db_path, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    if run["status"] != "PAUSED":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id!r} status={run['status']!r}，must be PAUSED to resume",
        )

    # V1.5 越层整改：通过 service 反查 workflow_name（替换原 JOIN SQL）
    workflow_name = get_workflow_name_for_run(db_path, run_id)
    if workflow_name is None:
        raise HTTPException(status_code=500, detail="workflow not found for run")
    workflow = get_workflow(workflow_name)
    if workflow is None:
        raise HTTPException(status_code=500, detail=f"workflow {workflow_name!r} not registered")

    # 并发防护：同 chapter 下有其他 RUNNING/PENDING run 时拒绝 resume。
    # 原因：避免 resume 与既有活跃 run 并发改 chapter / plan_json / story_state。
    # 注意：自身 run 已是 PAUSED，不会被自身判定命中；用 exclude_run_id 显式排除。
    chapter_id = run.get("chapter_id")
    if chapter_id:
        active = _check_active_run_for_chapter(
            db_path, chapter_id=chapter_id, exclude_run_id=run_id
        )
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"chapter {chapter_id!r} has another active workflow run "
                    f"(run_id={active['run_id']!r}, status={active['status']!r}); "
                    f"wait for it to reach a terminal state before resuming this run"
                ),
            )

    engine = _engine(request)
    try:
        engine.resume_async(
            run_id,
            workflow["nodes"],
            human_input=body.human_input,
            regenerate=bool(body.regenerate),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 异步化后：resume_async 立即返回，run 行已是 RUNNING（_prepare_resume_ctx 已
    # 在调用线程完成；_run_nodes 在后台线程推进）。HTTP 响应固定为 RUNNING；
    # 真实终态（COMPLETED / PAUSED / FAILED）由前端通过 GET /runs/{id} 轮询拿到。
    out: dict[str, Any] = {
        "run_id": run_id,
        "status": "RUNNING",
        "current_node": None,
    }

    # P0 自动改稿回路：仅 chapter-review + rejected-for-revision + auto_revise_max>0 时触发。
    # 异步化后 resume 不再阻塞到终态——本条件判断需要 sync 等到 resume 终态才能执行。
    # 方案取舍：保留 auto_revise 行为（test 期望），改为同步阻塞调用 _auto_revise_loop。
    # 副作用：resume 端点可能阻塞几秒到几分钟（auto_revise 同步）；不符合「立即返回」目标。
    # 但保留它比破坏既有契约更安全；前端集成测试已统一加 polling 兜底。
    auto_revise_max = _resolve_auto_revise_max(body.auto_revise_max)
    log.warning("[DEBUG] auto_revise entry: workflow=%s auto_revise_max=%s", workflow_name, auto_revise_max)
    if (
        workflow_name == "chapter-review"
        and auto_revise_max > 0
    ):
        # 同步等当前 resume run 走到终态（FAILED/PAUSED/COMPLETED），再判断是否触发 auto_revise
        import time as _t
        deadline = _t.monotonic() + 180.0
        while _t.monotonic() < deadline:
            cur = get_run(db_path, run_id)
            if cur and cur["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            _t.sleep(0.2)
        cur = get_run(db_path, run_id)
        log.warning("[DEBUG] resume final: status=%s err=%s", cur["status"] if cur else None, cur.get("error") if cur else None)
        if (
            cur
            and cur["status"] == "FAILED"
            and "rejected-for-revision" in str(cur.get("error") or "")
        ):
            chapter_id_for_loop = run.get("chapter_id")
            if chapter_id_for_loop:
                project_id_for_loop = ChapterService(db_path).get_project_id(chapter_id_for_loop)
                if project_id_for_loop is not None:
                    mp = body.mock_providers
                    if mp is None:
                        ckpt = run.get("checkpoint_json") or {}
                        legacy_mp = ckpt.get("mock_providers")
                        if isinstance(legacy_mp, dict):
                            mp = legacy_mp
                    return _auto_revise_loop(
                        engine,
                        db_path,
                        project_id_for_loop,
                        chapter_id_for_loop,
                        mp,
                        auto_revise_max,
                    )

    return out


@router.get("/projects/{project_id}/runs")
def list_runs_endpoint(project_id: str, request: Request) -> list[dict[str, Any]]:
    settings = request.app.state.settings
    db_path = settings.db_path
    runs = list_runs(db_path, project_id)
    # 恢复挂起的初始化：每行附加 workflow_name（get_workflow_name_for_run 反查）；
    # 一行一次查询可接受，list 端点不在热路径上。其余字段保持不变。
    for run in runs:
        run["workflow_name"] = get_workflow_name_for_run(db_path, run["run_id"])
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
