"""workflows 路由：启动 / resume / cancel（含并发守卫与 auto_revise 触发）。

V4.0 模块化重构 V2：自 ``packages/core/api/routers/workflows.py`` 按端点域拆出，
纯搬家零逻辑变更。端点（挂在 ``/api`` 前缀下）：

- ``POST /projects/init`` — 启动 project-init
- ``POST /projects/{project_id}/chapters/{chapter_id}/plan|write|review|commit`` — 章节工作流
- ``POST /runs/{run_id}/resume`` — 恢复 PAUSED run（可能触发 auto_revise 回路）
- ``POST /runs/{run_id}/cancel`` — 协作式取消 RUNNING run（含回路级取消标记）

resume 对 chapter-review 的 rejected-for-revision 终态会在 daemon 线程内触发
auto_revise 回路（``revise._auto_revise_loop``）。control ↔ revise 存在模块级循环
（gate-revise 复用本模块 ``_start_workflow``），故该 import 延迟到线程体内。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.db import get_connection
from packages.core.model_router.profiles import ProfileService
from packages.core.workflow_registry import get_workflow
from packages.core.workflow_runtime.engine import WorkflowRunConflict
from packages.core.workflow_runtime.runs import get_run, get_workflow_name_for_run
from packages.domain.chapter.service import ChapterService

from .common import (
    ProjectInitRequest,
    ResumeRequest,
    StartWorkflowRequest,
    _check_chapter,
    _engine,
    _extract_pause_payload,
    _init_genesis_if_needed,
    _mark_auto_revise_loops_cancelled_for_run,
    log,
)

router = APIRouter(tags=["workflows"])


# ---------------------------------------------------------------------------
# Helpers（守卫与启动实现）
# ---------------------------------------------------------------------------


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


def _latest_completed_review_end(db_path: str, chapter_id: str) -> str | None:
    """返回指定 chapter 最近一次 COMPLETED 状态 chapter-review run 的 ended_at（ISO 字符串）。

    用于 chapter-commit 时序守卫：拿「最近一次审校完成时间」与最新 draft.created_at 比较，
    阻断「审过 v5、改出 v6、commit 定稿 v6」的口子。

    返回：
    - 最近一次 COMPLETED review 的 ended_at（ISO 字符串，now_iso() 产物，可直接字符串比较）；
    - 无 COMPLETED review run → None（调用方视为「无需本守卫兜底，由既有 REVIEWED 校验兜底」）。
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT wr.ended_at FROM workflow_runs wr
            JOIN workflows wf ON wf.workflow_id = wr.workflow_id
            WHERE wr.chapter_id = ?
              AND wf.name = 'chapter-review'
              AND wr.status = 'COMPLETED'
            ORDER BY wr.ended_at DESC
            LIMIT 1
            """,
            (chapter_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["ended_at"] is None:
        return None
    return row["ended_at"]


def _latest_draft_after(db_path: str, chapter_id: str, threshold_iso: str) -> dict[str, Any] | None:
    """返回该 chapter 在 ``threshold_iso`` 之后创建的最新草稿行（version / created_at）；无则 None。

    用于 chapter-commit 时序守卫：threshold 通常为最近一次 COMPLETED review 的 ended_at，
    若最新草稿在其之后 → 说明审校后又人工改稿 / 续写，commit 必须拒收。

    注意：``created_at`` 是 ISO 字符串（now_iso() 产物），可直接字典序比较；调用方需自行
    保证 threshold_iso 来自同一时间体系（workflow_runs.ended_at 写入路径一致）。
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT version, created_at FROM drafts
            WHERE chapter_id = ? AND created_at > ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (chapter_id, threshold_iso),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"version": int(row["version"]), "created_at": row["created_at"]}


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
    # chapter-review 指定草稿版本：仅 chapter-review 节点读取；其他 workflow 收到此字段会被 pipeline 忽略。
    if body.draft_version is not None:
        initial_ctx["draft_version"] = body.draft_version
    # V1.3 deep_review 二审 AI 开关：仅 chapter-review 节点读取；其他 workflow 收到此字段会被 pipeline 忽略。
    # 仅当显式 True 才塞 ctx（None/False 一律不塞，避免下游误读为「未指定」）。
    if body.deep_review is True:
        initial_ctx["deep_review"] = True

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
    except WorkflowRunConflict as exc:
        # 0017 部分唯一索引兜底 TOCTOU：双 start 窄窗口第二个被 SQL 拒绝 → 409。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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

    # 单次 run 级模型档案覆盖：校验 model_profile_id 存在再塞 ctx；不存在 → 400。
    # 校验走 ProfileService.get（与 model_profiles API 共用底层 SQL）；缺省
    # （None）跳过校验 / 不塞 ctx，与「不静默回落」原则一致。
    if body.model_profile_id is not None:
        if ProfileService(db_path).get(body.model_profile_id) is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"model_profile_id {body.model_profile_id!r} not found in "
                    f"model_profiles; create it first or omit the field to use "
                    f"global capability_bindings"
                ),
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
    # 单次 run 级模型档案覆盖：已校验存在 → 塞 ctx；4 个 AI 节点透传给 run_agent。
    if body.model_profile_id is not None:
        initial_ctx["model_profile_id"] = body.model_profile_id

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


def _guard_commit_draft_freshness(request: Request, chapter_id: str) -> None:
    """chapter-commit 时序守卫：draft.created_at > 最近一次 COMPLETED review.ended_at → 409。

    阈值语义：取该 chapter 最近一次 COMPLETED 状态的 chapter-review run 的 ended_at；
    若此时存在 created_at 严格更晚的 draft 行，说明审校完成后又人工改稿 / 续写，
    应在 commit 启动阶段拒收，避免「审过 v5、改出 v6、commit 定稿 v6」。

    不拦场景：
    - 无 COMPLETED review run → 返回（视为「首次 commit」，由既有 REVIEWED 校验兜底）；
    - 草稿 created_at 均 ≤ review.ended_at → 返回（说明审校稿与最新草稿一致）。
    """
    db_path = request.app.state.settings.db_path
    review_end = _latest_completed_review_end(db_path, chapter_id)
    if review_end is None:
        return
    latest_draft = _latest_draft_after(db_path, chapter_id, review_end)
    if latest_draft is None:
        return
    raise HTTPException(
        status_code=409,
        detail=(
            f"最新草稿 v{latest_draft['version']} 晚于最近一次审校完成时间"
            f"（{review_end}），存在未审改动，请先重新审校再提交"
        ),
    )


# ---------------------------------------------------------------------------
# Endpoints（启动 / resume / cancel）
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
    # 时序守卫：最近一次 COMPLETED chapter-review 之后若又产生了更新草稿，commit 必须拒收。
    # 兜底 create_draft 状态降级（REVIEWED→DRAFTED）覆盖不到的口子，例如：
    # - review RUNNING 期间人工改稿（review 还没 COMPLETED 不会触发降级）；
    # - 历史遗留数据（章节已是 REVIEWED 但草稿晚于最近一次审校完成时间）。
    # 无 COMPLETED review 或无草稿时不拦，由既有 REVIEWED 状态机校验兜底。
    _guard_commit_draft_freshness(request, chapter_id)
    return _start_workflow(
        workflow_name="chapter-commit",
        request=request,
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
    )


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
        # 缺陷 2（P0 低）修复：resume_async 抛 ValueError 时按语义分桶映射状态码。
        # - 「not found」：run 在前置校验后被删除（或并发删除）→ 404
        # - 「must be PAUSED」：run 在前置校验后状态被改（并发竞态）→ 409
        # - 其他：兜底 400
        msg = str(exc)
        log.debug("resume_async ValueError: %s", msg)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg) from exc
        if "must be PAUSED" in msg:
            raise HTTPException(status_code=409, detail=msg) from exc
        raise HTTPException(status_code=400, detail=msg) from exc

    # 异步化后：resume_async 立即返回，run 行已是 RUNNING（_prepare_resume_ctx 已
    # 在调用线程完成；_run_nodes 在后台线程推进）。HTTP 响应固定为 RUNNING；
    # 真实终态（COMPLETED / PAUSED / FAILED）由前端通过 GET /runs/{id} 轮询拿到。
    out: dict[str, Any] = {
        "run_id": run_id,
        "status": "RUNNING",
        "current_node": None,
    }

    # P0 自动改稿回路：仅 chapter-review + auto_revise_max>0 时可能触发。
    # 异步化后 resume 不再阻塞到终态——auto_revise 条件判断需要等当前 resume run 走到
    # 终态（FAILED-rejected-for-revision）。把「轮询等终态 → 判 rejected → _auto_revise_loop」
    # 整段搬进 daemon 线程（name=f"auto-revise-{run_id}"）执行；HTTP 立即返回 RUNNING，
    # 前端通过 GET /runs 轮询回路产生的子 run（write / review）。
    auto_revise_max = _resolve_auto_revise_max(body.auto_revise_max)
    # V3.9 检修：原为 log.warning 的 `[DEBUG]` 级排障行，噪音混进告警通道 → 降 debug。
    log.debug("auto_revise entry: workflow=%s auto_revise_max=%s", workflow_name, auto_revise_max)
    if (
        workflow_name == "chapter-review"
        and auto_revise_max > 0
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

                # 解析「回路用 model_overrides」：与 mock_providers 同样的「请求体 > 原
                # run ctx」语义。请求体显式给出（含空 dict）一律以请求体为准；请求体 None
                # 时从原 review run 的 checkpoint_json（即其 ctx）中尝试继承一份 dict。
                # 继承失败 / 非 dict / None → 回路不写 model_overrides（保持现状）。
                effective_model_overrides: dict[str, str] | None
                if body.model_overrides is not None:
                    effective_model_overrides = body.model_overrides
                else:
                    _ckpt_for_ov = run.get("checkpoint_json") or {}
                    _ov_legacy = _ckpt_for_ov.get("model_overrides")
                    if isinstance(_ov_legacy, dict):
                        effective_model_overrides = _ov_legacy
                    else:
                        effective_model_overrides = None

                # 解析「回路用 author_intent」：与 mock_providers / model_overrides 同样的
                # 「请求体 > 原 run ctx」语义。请求体显式给出（去空白后非空）→ 一律以请求体
                # 为准（不再继承）；请求体 None 时才从原 review run 的 checkpoint_json
                # （即其 ctx）继承，且只有非空字符串才认——None / 空串 / 非 str → 回路不写该键
                # （保持既有「缺省不出现键」行为）。
                # 2026-09-16 实证（新书 01 ch2）：回路子 run 只透传 model_overrides，
                # author_intent 丢失 → 改稿轮在无作者约束状态下重写正文，v2 出现内部字段名
                # ``recalled_passages``（v1 干净）。
                effective_author_intent: str | None
                if body.author_intent is not None:
                    effective_author_intent = (
                        body.author_intent if body.author_intent.strip() else None
                    )
                else:
                    _ckpt_for_ai = run.get("checkpoint_json") or {}
                    _ai_legacy = _ckpt_for_ai.get("author_intent")
                    if isinstance(_ai_legacy, str) and _ai_legacy.strip():
                        effective_author_intent = _ai_legacy
                    else:
                        effective_author_intent = None

                # 防御快照：daemon 线程不能持有 Request / Body 引用，避免 GC 后访问异常；
                # db_path / workflow 元数据 / mock_providers / model_overrides / author_intent
                # 都重新解出原始值再传入线程。
                _thread_db_path = str(db_path)
                _thread_engine = engine
                _thread_run_id = run_id
                _thread_project_id = project_id_for_loop
                _thread_chapter_id = chapter_id_for_loop
                _thread_mp = mp
                _thread_auto_revise_max = auto_revise_max
                _thread_model_overrides = effective_model_overrides
                _thread_author_intent = effective_author_intent

                def _auto_revise_runner() -> None:
                    """daemon 线程体：等当前 resume run 终态 → 判 rejected → 调 _auto_revise_loop。

                    轮询 deadline 保留 180s（与原同步实现一致）；失败/异常仅记日志，
                    不向外抛出（HTTP 已返回，调用方拿不到）。子 run 状态由前端轮询
                    GET /runs/{id} / list 端点查看。
                    """
                    import time as _t
                    try:
                        deadline = _t.monotonic() + 180.0
                        cur: dict[str, Any] | None = None
                        while _t.monotonic() < deadline:
                            cur = get_run(_thread_db_path, _thread_run_id)
                            if cur and cur["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
                                break
                            _t.sleep(0.2)
                        log.debug(
                            "resume final (async): run_id=%s status=%s err=%s",
                            _thread_run_id,
                            cur["status"] if cur else None,
                            cur.get("error") if cur else None,
                        )
                        if (
                            cur
                            and cur["status"] == "FAILED"
                            and "rejected-for-revision" in str(cur.get("error") or "")
                        ):
                            # 延迟 import：control ↔ revise 存在模块级循环（gate-revise
                            # 复用 control._start_workflow），线程体内 import 无循环风险。
                            from .revise import _auto_revise_loop

                            _auto_revise_loop(
                                _thread_engine,
                                _thread_db_path,
                                _thread_project_id,
                                _thread_chapter_id,
                                _thread_mp,
                                _thread_auto_revise_max,
                                _thread_model_overrides,
                                _thread_author_intent,
                                parent_run_id=_thread_run_id,
                            )
                    except Exception as exc:  # noqa: BLE001
                        log.exception(
                            "auto_revise daemon thread crashed: run_id=%s err=%s",
                            _thread_run_id, exc,
                        )

                t = threading.Thread(
                    target=_auto_revise_runner,
                    name=f"auto-revise-{run_id}",
                    daemon=True,
                )
                t.start()
                log.info(
                    "auto_revise daemon thread started: run_id=%s thread=%s",
                    run_id, t.name,
                )

    return out


# ---------------------------------------------------------------------------
# 工作流运行取消（协作式）
# ---------------------------------------------------------------------------


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    """协作式取消 RUNNING workflow run。

    状态机（端点语义）：
    - run 不存在 → 404，``detail="run ... not found"``。
    - status=RUNNING → :meth:`WorkflowEngine.cancel_run` UPDATE 为 CANCELLED，
      200 ``{"run_id": ..., "status": "CANCELLED"}``。后台节点循环在下一次探针
      （节点开始前 / checkpoint 前）命中 CANCELLED 即停止推进并丢弃产出。
    - status ∈ {COMPLETED, FAILED, CANCELLED, PAUSED} → 409，detail 含当前
      status。PAUSED run 的取消走 resume 后的驳回/决议路径，不在本端点范围。

    设计要点：
    - 幂等：重复取消已 CANCELLED 的 run 按 409 处理即可（与「终态 409」一致）。
    - engine.cancel_run 内部 WHERE status='RUNNING' 兜底 TOCTOU：API 层校验
      后到 SQL 执行的窄窗口内状态被改 → rowcount=0 → engine 抛 ValueError →
      本端点分桶映射 409。
    - V3.9 4.2：被取消的 run 若属于某个 auto_revise 回路（进程内注册表登记过），
      取消成功后把该回路标记 cancelled —— 回路线程下一轮启动子 run 前检查该标记即
      终止，不再「取消一个子 run 又冒出下一轮」。响应体不变（仍是 {run_id, status}）。
    """
    settings = request.app.state.settings
    db_path = settings.db_path

    run = get_run(db_path, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    if run["status"] != "RUNNING":
        # 终态 / PAUSED 一律 409；body 含当前状态便于客户端区分。
        raise HTTPException(
            status_code=409,
            detail=(
                f"run {run_id!r} status={run['status']!r}, "
                f"must be RUNNING to cancel"
            ),
        )

    engine = _engine(request)
    try:
        engine.cancel_run(run_id)
    except ValueError as exc:
        # 兜底分桶：与 resume_run 端点同口径。
        msg = str(exc)
        log.debug("cancel_run ValueError: %s", msg)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg) from exc
        # "must be RUNNING to cancel" 或 TOCTOU "status changed concurrently" 都 → 409
        raise HTTPException(status_code=409, detail=msg) from exc

    # V3.9 批次 4.2：被取消的 run 若属于某个 auto_revise 回路 → 把回路也标记为取消；
    # 回路线程在下一轮启动子 run 前看到标记即终止（不再启动新的 write/review）。
    cancelled_loops = _mark_auto_revise_loops_cancelled_for_run(run_id)
    if cancelled_loops:
        log.info(
            "cancel_run %s stopped auto_revise loop(s): %s",
            run_id, cancelled_loops,
        )

    return {"run_id": run_id, "status": "CANCELLED"}
