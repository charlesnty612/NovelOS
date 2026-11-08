"""章节级 workflow 的**启动实现**（2026-11-08 批次三十八 · R1-break-cycle）。

## 为什么从 ``control.py`` 拆出来

``control.py`` 与 ``revise.py`` 之间存在**模块级循环**：gate-revise 复用
``_start_workflow``，而 resume 后的自动改稿回路又要在 daemon 线程里调
``revise._auto_revise_loop``（原实现只能把 import 延迟到线程体内掩盖）。

本模块把「启动实现」迁到**中性位置**：``control`` 与 ``revise`` 都**向下**依赖它，
两者之间不再有模块级 import 边。``control.py`` 继续再导出 ``_start_workflow`` /
``_start_workflow_core`` / ``_guard_plan_chapter_status``（``workflows/__init__``
的 re-export 与既有测试 import 路径零改动）。

## 保持不动的契约

- ``_start_workflow`` 仍需经 ``_engine(request)`` 取引擎（``tests/api/test_workflow_cancel.py``
  在 ``control._engine`` 上打 monkeypatch）；本模块 import 的是 ``.common._engine``，
  而 ``control`` 自己也把它 import 进命名空间——两条路径指向同一个函数对象，
  monkeypatch 打在 ``control._engine`` 上只影响 ``control`` 的命名空间，故
  **cancel 路径的 patch 契约不受影响**（cancel_run 仍留在 control）。
- 章节级起跑的 ctx 组装仍只在本文件一处（快通道 auto-commit 线程复用
  ``_start_workflow_core``，不重拼 ctx）。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException, Request

from packages.core.workflow_registry import get_workflow
from packages.core.workflow_runtime.engine import WorkflowEngine, WorkflowRunConflict
from packages.core.workflow_runtime.guards import check_active_run_for_chapter
from packages.domain.chapter.service import ChapterService

from .common import (
    StartWorkflowRequest,
    _engine,
    _init_genesis_if_needed,
    check_chapter_in_project,
)
from .fast_mode import resolve_fast_mode

#: ``chapter-plan`` 允许的**启动前**章节状态（与前端
#: ``apps/web/src/utils/chapterState.ts::EXPECTED_STATUS.plan = ['PLANNED']`` 逐字对齐）。
#: 为什么需要服务端闸门（C-8，批次三十六）：``chapter-plan`` 此前**两头都没有校验**——
#: write/commit 有节点级/API 级状态校验，plan 则完全没有 ⇒ API 直调可对 COMMITTED
#: 章节重写 ``plan_json``（顺带覆盖 ``revision_note`` / ``gate_blocked`` 等派生标记）。
_PLAN_ALLOWED_STATUSES: frozenset[str] = frozenset({"PLANNED"})


def _guard_plan_chapter_status(db_path: str, workflow_name: str, chapter_id: str) -> None:
    """章节状态闸门（仅 ``chapter-plan`` 生效）：非 ``PLANNED`` ⇒ 409。

    形态照 commit 的 API 级守卫（``control._guard_commit_draft_freshness``）：
    **在起 run 之前**拒绝，错误消息带「当前状态 + 允许状态」——作者据此知道该点哪个
    动作（如 REVIEWED 应先回退/重开，而不是重生成计划）。

    为什么在服务端而不是只靠前端：前端 ``EXPECTED_STATUS`` 只是 UI 可用性判断，
    API 直调（脚本 / 其他客户端）完全绕过它；而 plan 的落库面会**覆盖**
    ``plan_json``（含 ``revision_note`` / ``gate_blocked``）⇒ 对已定稿章节重跑是数据损坏。

    只放行 ``PLANNED`` 而不是「PLANNED + 空 plan 的其他状态」：状态机是唯一权威，
    空 plan 的 DRAFTED/REVIEWED 章该走什么动作由作者决定，服务端不替它猜。
    """
    if workflow_name != "chapter-plan":
        return
    chapter = ChapterService(db_path).get(chapter_id)
    status = (chapter or {}).get("status")
    if status in _PLAN_ALLOWED_STATUSES:
        return
    raise HTTPException(
        status_code=409,
        detail=(
            f"chapter {chapter_id!r} status={status!r}，"
            f"chapter-plan 仅允许状态 {sorted(_PLAN_ALLOWED_STATUSES)}；"
            f"已写/已定稿章节不可重生成计划（会覆盖 plan_json）"
        ),
    )


def _start_workflow(
    *,
    workflow_name: str,
    request: Request,
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
) -> dict[str, Any]:
    """从 HTTP Request 起步的薄封装：取 settings / engine 后交给 :func:`_start_workflow_core`。

    拆出 core 是为了让快通道的守护线程能起 commit run——线程里没有 Request 对象，
    只有 db_path 与 engine。**引擎必须经模块级 ``_engine(request)`` 查找**，
    不能内联 ``WorkflowEngine(...)``（``tests/api/test_workflow_cancel.py`` 在该名字上
    打 monkeypatch，虽然它测的是 cancel 路径，保持同款查找形态可避免两处漂移）。
    """
    settings = request.app.state.settings
    return _start_workflow_core(
        workflow_name=workflow_name,
        engine=_engine(request),
        db_path=str(settings.db_path),
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
    )


def _start_workflow_core(
    *,
    workflow_name: str,
    engine: WorkflowEngine,
    db_path: str,
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
) -> dict[str, Any]:
    """启动一个章节级 workflow run 的**唯一实现**（HTTP 与守护线程共用）。

    ctx 组装只在这里做一次——快通道的 auto-commit 线程复用本函数而不是重拼 ctx，
    否则两条路径会各自演化出不同的 ctx（坑区「两条路径拼同一件事」的形状）。
    """
    check_chapter_in_project(db_path, project_id, chapter_id)

    # 并发防护：同 chapter 已有 RUNNING/PENDING run 时拒绝再启动。
    # 原因：章节级工作流（write/review/commit）会并发改 chapter 行 + plan_json + story_state，
    # 并行运行会产生数据竞态。前端 UX：用户先等当前 run 走到 PAUSED/COMPLETED/FAILED
    # 再触发下一轮（前端 list 端点会过滤活跃行）。
    active = check_active_run_for_chapter(db_path, chapter_id=chapter_id)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"chapter {chapter_id!r} already has an active workflow run "
                f"(run_id={active['run_id']!r}, status={active['status']!r}); "
                f"wait for it to reach a terminal state before starting a new one"
            ),
        )

    # 章节状态闸门（C-8，批次三十六）：chapter-plan 仅放行 PLANNED。
    # 位置刻意在「活跃 run 检查」之后——并发的活跃 run 是更前置的冲突（409 语义不同），
    # 且此时章节行必然存在（上面的 check_chapter_in_project 已 404 兜底）。
    _guard_plan_chapter_status(db_path, workflow_name, chapter_id)

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
    # chapter-review 指定草稿版本。**真实读者有两个**（C-36 注释订正，批次三十六）：
    #   ① ``chapter_review`` 节点——「本次审哪一版」；
    #   ② **``chapter_commit`` 的 ``quality_gate`` 节点**（``chapter_commit/gate.py``
    #      的 ``_reviewed_draft_version``）——「门禁量哪一版」。
    # ②的优先级是「显式（本字段）> 最近一次 review 的被审版本 > 最新版」，裁决见
    # 2026-10-23 B4（「审过 v5、改出 v6、commit 定稿 v6」事故的修法）。
    # 原注释写「仅 chapter-review 读取」，按「注释与代码冲突以代码为准」订正。
    if body.draft_version is not None:
        initial_ctx["draft_version"] = body.draft_version
    # V1.3 deep_review 二审 AI 开关：仅 chapter-review 节点读取；其他 workflow 收到此字段会被 pipeline 忽略。
    # 仅当显式 True 才塞 ctx（None/False 一律不塞，避免下游误读为「未指定」）。
    if body.deep_review is True:
        initial_ctx["deep_review"] = True
    # P0-1（2026-09-18）：confirm 档规则的放行声明。仅 chapter-commit 的 quality_gate
    # 节点读取（须覆盖本次全部 confirm rule_id 且 reason 非空，否则照样阻断）；
    # 其他 workflow 收到此字段会被忽略。缺省 → 不塞 ctx（= 不声明）。
    if body.gate_override is not None:
        initial_ctx["gate_override"] = body.gate_override

    # 快通道（2026-10-14）：本次运行是否跳过人工闸门。三档优先级在
    # fast_mode.resolve_fast_mode 收口（请求体 > projects.fast_mode > env > 关）。
    #
    # **注入顺序必须在请求体字段之后**：fast 为真时要压过请求体的
    # quality_gate_mode="enforce"（作者显式传 enforce 说明他这章想守门，
    # 但项目开了快通道=整本书都要快，故 fast 胜出）。反向的逃生舱是请求体
    # 显式传 fast_mode=false——那在 resolve 阶段就返回 False，不进本分支。
    fast = resolve_fast_mode(
        db_path=str(db_path),
        project_id=project_id,
        request_value=body.fast_mode,
    )
    if fast:
        initial_ctx["fast_mode"] = True
        # 门禁照算、报告照落库，只是不阻断（既有 report 语义，0 行为变更）。
        # 只被 chapter-commit 的 quality_gate 节点读；review 里没有该节点。
        initial_ctx["quality_gate_mode"] = "report"
        # 预置批准：high_risk_approval 节点读到 approved=True 直接放行不 PAUSE。
        # approver 标 "system:fast_mode"（**不是** "human"）——commit 节点会把
        # 它写进 commits 表，标成 human 就是在审计里留一条假的审批人。
        #
        # ⚠️ **必须按 workflow_name 收口到 chapter-commit**（2026-10-15 修复）：
        # `ctx["human_input"]` 在全仓有**两个**读者，它们语义不同——
        #   - `chapter_commit/gate.py::_high_risk_approval_node`（预置「放行本闸门」）
        #   - `chapter_review/pipeline.py::_author_review_node`（resume 时读「人已
        #     做出的决议」，其 docstring 明写「本 fn 总是抛 PauseRequested」）
        # 同一个键既当「已决」又当「预先放行」，一旦对 plan/write/review 也注入，
        # 作者手动点「审校」就会**一句不问地自动批准**、章节直推 REVIEWED（实测
        # 快通道开→COMPLETED/REVIEWED，关→PAUSED/DRAFTED）。
        # 快通道的语义是「不跑 chapter-review」，不是「跑但自动批」——
        # 所以 review 必须拿不到这个键。判别：
        # `tests/api/test_fast_mode_review_gate.py::test_manual_review_still_pauses_under_fast_mode`
        if workflow_name == "chapter-commit":
            initial_ctx["human_input"] = {
                "approved": True,
                "approver": "system:fast_mode",
            }

    try:
        run_id = engine.start_with_nodes_async(
            workflow_name,
            workflow["nodes"],
            chapter_id=chapter_id,
            # 0037（2026-10-24）：顺带写 run 归属列（chapter 级行为不变，
            # 守卫仍走 0017 唯一索引 + 本函数的 chapter 级守卫）。
            project_id=project_id,
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


__all__ = ["_guard_plan_chapter_status", "_start_workflow", "_start_workflow_core"]
