"""Story State REST 路由（Sprint 2 + Sprint 7）。

端点：
- POST /projects/{pid}/state/init               —— 创建 genesis（v1 快照）
- GET  /projects/{pid}/state                    —— 当前 Canonical State（main）；可选 ?branch_id=
- GET  /projects/{pid}/state/versions/{n}       —— 指定快照
- POST /projects/{pid}/deltas                   —— submit（201 通过；422 校验失败）
                                                   body 可选 branch_id
- POST /projects/{pid}/commits                  —— commit（201 通过；409 乐观锁/审批；404）
                                                   body 可选 branch_id
- POST /commits/{cid}/rollback                  —— 回滚（201）
- GET  /projects/{pid}/commits                  —— 列 commits
- GET  /projects/{pid}/chapters/{cid}/deltas    —— 列 deltas
- POST /projects/{pid}/branches                 —— 创建分支（Sprint 7）
- GET  /projects/{pid}/branches                 —— 列分支
- POST /projects/{pid}/branches/{bid}/promote   —— 分支 promote 到 main
- GET  /projects/{pid}/state/diff?a=&b=&branch_id=  —— 两版本 diff

错误码映射：
- 422：submit 校验失败（detail 含 errors 列表）。
- 404：delta / commit / chapter / project / branch 不存在；**以及 chapter 不属于
  本 project**（单用户自用工具下「不属于」即「不存在」，且不向作者区分这两者）。
- 409：StateConflictError（状态机非法）、OptimisticLockError（乐观锁）、
       ApprovalRequiredError（HIGH 风险无审批）、BranchClosed（分支 MERGED/DISCARDED）、
       branch_name_conflict（重名）、FK 冲突（commit 时 chapter/project 不存在）。
- 500：未捕获异常（detail 仅含类名 + 简短 message）。

设计要点：
- 复用 ``discover_routers`` 自动发现机制：模块顶层定义 ``router`` 即可被 main.py 挂载到 ``/api`` 前缀。
- 2026-10-15 起 ``GET .../state`` 与 ``GET .../state/versions/{n}`` 声明
  ``response_model=SnapshotResponse``（``core/story_state/models.py``）——此前是裸 ``dict``，
  OpenAPI 里没有 schema，前端只能手写欠定义类型并配一层手写归一化，那层归一化把
  ``characters``（list）与 ``events``（dict）的形状拍反，导致概览页两个统计项恒为 0。
  契约补上后，前端归一化层可整层删除（见该模型 docstring）。
- 2026-10-15 批次（六）把**写面**七个端点也补上 ``response_model``（init / deltas /
  commits / rollback / branches / promote / state-diff）。键集全部由**实测**得出
  （临时库 + 合成夹具），不是读源码推的；实测表见
  ``tests/api/test_story_state_write_response_contract.py``。
  ⚠️ 七个端点**一个都没加** ``response_model_exclude_none=True``——``state/diff`` 与
  ``promote`` 的实测形状里都有「键恒在、值为 null」的键，那个端点级开关会把它们抹掉。
  「空则省略」改由模型侧**字段级** ``exclude_if`` 实现。
- 2026-10-15 批次（六）L2 组把**列表端点**也补上 ``response_model``
  （commits / chapters·deltas / branches）。实测口径与结论：列表行的形状
  **不能**从单体端点推——``commits`` 列表行是 12 键裸 DB 行（``POST /commits`` 只有 4 键），
  ``branches`` 列表行与 ``POST /branches`` 键集逐字相同却因 main 的
  ``parent_branch_id`` 为 null 而不可复用。三个端点都**没有条件键**，因此
  **一个都没加** ``response_model_exclude_none`` / ``exclude_unset``（那会把
  「键恒在、值为 null」的键抹掉）。实测表见
  ``tests/api/test_state_list_response_contract.py``。
- 路由内部不开连接：所有 DB 操作委派 ``StoryStateService``（与 S1 services 一致）。
- 路径参数 ``pid`` / ``cid`` 仅作路由层校验：项目不存在时由 service 阶段抛 StateNotFoundError
  转 404；端点 /projects/{pid}/state/init 与 /projects/{pid}/state 在 project 缺失时返回 404，
  便于前端快速定位。
- **章节归属校验（2026-10-16）**：凡同时出现 ``project_id`` 与 ``chapter_id`` 的端点，
  一律补 :func:`_ensure_chapter_in_project`——它们是**两个独立变量**，只校验前者是
  「路由层少一次归属校验」。涉及三个端点：``chapters/{cid}/deltas``（路径参数，
  修前跨项目 **200 + 原样返回同样的行**）、``state/init`` 与 ``branches/{bid}/promote``
  （body 里的 chapter_id，修前跨项目同样 201 并**写入**错配行）。错误码与理由见该函数。
- Sprint 7 新增分支能力：body 中 ``branch_id`` 是路由层透传字段（不属于 delta schema），router
  取出后再传给 service；service 校验分支归属与 ACTIVE 状态；state_deltas 表本身无 branch_id 列
  （沿用 state-delta-v0.md §2.2），仅 commits.branch_id 区分 commit 归属。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.core.story_state import StoryStateService
from packages.core.story_state.exceptions import (
    ApprovalRequiredError,
    BranchClosed,
    BranchNotFound,
    OptimisticLockError,
    StateConflictError,
    StateNotFoundError,
)
from packages.core.story_state.models import (
    BranchCreatedResponse,
    BranchPromoteResponse,
    BranchSummary,
    CommitListItem,
    CommitResponse,
    RollbackResponse,
    SnapshotResponse,
    StateDeltaListItem,
    StateDiffResponse,
    SubmitDeltaResponse,
)

log = get_logger("novelos.routers.story_state")

router = APIRouter(tags=["story_state"])


def _service(request: Request) -> StoryStateService:
    settings = request.app.state.settings
    return StoryStateService(settings.db_path)


def _ensure_project(request: Request, project_id: str) -> None:
    from packages.domain.project.service import ProjectService

    if ProjectService(request.app.state.settings.db_path).get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")


def _ensure_chapter_in_project(request: Request, project_id: str, chapter_id: str) -> None:
    """chapter 必须属于 ``project_id``，否则 **404**。

    **归属判定只此一份**：委派 :func:`workflows.common.check_chapter_in_project`
    （本仓权威实现；``_check_chapter`` 只是它的 Request 薄封装，其 docstring 明写
    「校验逻辑只此一份，两条路径不得分叉」）。

    ⚠️ 2026-10-16 批次十**简化**：本函数原先是「捕获 helper 抛的 400、改写成 404、
    统一 detail」的 workaround——因为当时的权威实现对「不属于本 project」返回 400。
    政策现已收进 helper 本身（两种归属失败都返回 404 + 同一句 detail，理由见
    :func:`check_chapter_in_project`），故这里的 catch-rewrite **成了死代码**：
    它不再改变任何响应，只是把一个 HTTPException 拆包再包一遍。
    ⇒ 删除。**保留它反而有害**：它捕获 `HTTPException` 再重抛，等于给「helper 将来
    新增第三种退出码」留了一个会被静默改写的口子——正是它当初注释里警告的那件事。
    """
    from packages.core.api.routers.workflows.common import check_chapter_in_project

    check_chapter_in_project(request.app.state.settings.db_path, project_id, chapter_id)


# ----------------------------------------------------------------- genesis & state


@router.post(
    "/projects/{project_id}/state/init",
    status_code=status.HTTP_201_CREATED,
    response_model=SnapshotResponse,
)
def init_state(project_id: str, payload: dict, request: Request) -> dict:
    chapter_id = payload.get("chapter_id")
    if not isinstance(chapter_id, str) or not chapter_id:
        raise HTTPException(status_code=422, detail="chapter_id required (non-empty string)")
    _ensure_project(request, project_id)
    # chapter_id 虽在 body 里、project_id 在路径里，但它们是**两个独立变量**：
    # 不校验归属时 `POST /projects/{B}/state/init {"chapter_id": A的章}` 会 201，
    # 给 B 建出 v1 快照的同时留下一条指向 A 的章的 genesis delta 行
    # （`state_deltas.chapter_id` 的 FK 只保证「章存在」，不保证「章属于本项目」）。
    _ensure_chapter_in_project(request, project_id, chapter_id)
    return _service(request).init_genesis(project_id, chapter_id)


@router.get("/projects/{project_id}/state", response_model=SnapshotResponse)
def get_state(project_id: str, request: Request, branch_id: str | None = None) -> dict:
    _ensure_project(request, project_id)
    try:
        return _service(request).get_current_state(project_id, branch_id=branch_id)
    except BranchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/state/versions/{version}",
    response_model=SnapshotResponse,
)
def get_state_version(project_id: str, version: int, request: Request) -> dict:
    _ensure_project(request, project_id)
    snap = _service(request).get_snapshot(project_id, version)
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail=f"snapshot version {version} not found for project {project_id!r}",
        )
    return snap


# ----------------------------------------------------------------- deltas


@router.post(
    "/projects/{project_id}/deltas",
    status_code=status.HTTP_201_CREATED,
    response_model=SubmitDeltaResponse,
)
def submit_delta(project_id: str, payload: dict, request: Request) -> dict:
    _ensure_project(request, project_id)
    branch_id = (payload or {}).get("branch_id") if isinstance(payload, dict) else None
    service_payload = dict(payload or {})
    if branch_id is not None:
        service_payload.pop("branch_id", None)
    try:
        result = _service(request).submit_delta(service_payload, branch_id=branch_id)
    except BranchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BranchClosed as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "branch_closed",
                "message": str(exc),
                "branch_id": exc.branch_id,
                "status": exc.status,
            },
        ) from exc
    if result["status"] == "rejected":
        raise HTTPException(status_code=422, detail={"errors": result["errors"], "delta_id": result["delta_id"]})
    return result


# ----------------------------------------------------------------- commits


@router.post(
    "/projects/{project_id}/commits",
    status_code=status.HTTP_201_CREATED,
    response_model=CommitResponse,
)
def commit_delta(project_id: str, payload: dict, request: Request) -> dict:
    _ensure_project(request, project_id)
    delta_id = payload.get("delta_id")
    author_approval = payload.get("author_approval") or {}
    workflow_run_id = payload.get("workflow_run_id") or ""
    branch_id = payload.get("branch_id")
    if not isinstance(delta_id, str) or not delta_id:
        raise HTTPException(status_code=422, detail="delta_id required")
    if not isinstance(workflow_run_id, str) or not workflow_run_id:
        raise HTTPException(status_code=422, detail="workflow_run_id required")
    try:
        return _service(request).commit_delta(
            delta_id, author_approval, workflow_run_id, branch_id=branch_id,
        )
    except BranchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BranchClosed as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "branch_closed",
                "message": str(exc),
                "branch_id": exc.branch_id,
                "status": exc.status,
            },
        ) from exc
    except StateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OptimisticLockError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "optimistic_lock",
                "message": str(exc),
                "expected_version": exc.expected_version,
                "actual_version": exc.actual_version,
            },
        ) from exc
    except ApprovalRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "approval_required",
                "message": str(exc),
                "high_risk_change_ids": exc.high_risk_change_ids,
            },
        ) from exc
    except StateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/commits/{commit_id}/rollback",
    status_code=status.HTTP_201_CREATED,
    response_model=RollbackResponse,
)
def rollback_commit(commit_id: str, payload: dict, request: Request) -> dict:
    author_approval = payload.get("author_approval") if isinstance(payload, dict) else {}
    try:
        return _service(request).rollback_commit(commit_id, author_approval or {})
    except StateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OptimisticLockError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "optimistic_lock",
                "message": str(exc),
                "expected_version": exc.expected_version,
                "actual_version": exc.actual_version,
            },
        ) from exc
    except ApprovalRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "approval_required",
                "message": str(exc),
                "high_risk_change_ids": exc.high_risk_change_ids,
            },
        ) from exc
    except StateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ----------------------------------------------------------------- lists


@router.get("/projects/{project_id}/commits", response_model=list[CommitListItem])
def list_commits(project_id: str, request: Request) -> list[dict]:
    """列本项目的 commit（每行恒 12 键，实测）。

    行是**裸 DB 行**，与 ``POST /commits`` 的 ``CommitResponse``（4 键动作摘要）不同形，
    故另建 ``CommitListItem``——**不按单体端点的形状推列表行**。
    本端点没有条件键 ⇒ **不加**任何 ``response_model_exclude_*`` 开关。
    """
    _ensure_project(request, project_id)
    return _service(request).list_commits(project_id)


@router.get(
    "/projects/{project_id}/chapters/{chapter_id}/deltas",
    response_model=list[StateDeltaListItem],
)
def list_deltas(project_id: str, chapter_id: str, request: Request) -> list[dict]:
    """列本章的 state delta（每行恒 18 键，实测）。

    ⚠️ 行里**没有** ``payload_json`` 键：``restore_delta_from_row`` 把 7 个 change
    数组摊到了顶层（10 元信息 + 7 数组 + ``status``）。7 数组不逐字段建模的理由见
    ``StateDeltaListItem`` 的 docstring。

    ⚠️ **归属校验（2026-10-16 补）**：此前只 ``_ensure_project(project_id)``，
    而 ``path`` 里的 ``project_id`` 与 ``chapter_id`` 是**两个独立变量**——``queries.list_deltas``
    是裸 ``SELECT * FROM state_deltas WHERE chapter_id = ?``，服务端**完全不看 project**，
    于是用别的 project_id 请求同一章会 **200 并原样返回同样的数据行**（实测 2/2 行）。
    现补 :func:`_ensure_chapter_in_project`（404，见该函数 docstring 的错误码理由）。
    """
    _ensure_project(request, project_id)
    _ensure_chapter_in_project(request, project_id, chapter_id)
    return _service(request).list_deltas(chapter_id)


# ----------------------------------------------------------------- branches (Sprint 7)


@router.post(
    "/projects/{project_id}/branches",
    status_code=status.HTTP_201_CREATED,
    response_model=BranchCreatedResponse,
)
def create_branch(project_id: str, payload: dict, request: Request) -> dict:
    """创建分支（state-delta-v0.md §6.3）。

    body:
      - ``name``: str, 必填，同 project 唯一；禁止 'main'。
      - ``base_state_version``: int, 可选，缺省 = main 最新 version。
    """
    _ensure_project(request, project_id)
    name = (payload or {}).get("name") if isinstance(payload, dict) else None
    base = (payload or {}).get("base_state_version") if isinstance(payload, dict) else None
    if not isinstance(name, str) or not name:
        raise HTTPException(status_code=422, detail="name required (non-empty string)")
    try:
        return _service(request).create_branch(project_id, name, base_state_version=base)
    except StateConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "branch_name_conflict", "message": str(exc)},
        ) from exc


@router.get("/projects/{project_id}/branches", response_model=list[BranchSummary])
def list_branches(project_id: str, request: Request) -> list[dict]:
    """列本项目的分支（每行恒 7 键，实测）。

    ⚠️ 键集与 ``POST /branches`` 的 ``BranchCreatedResponse`` **逐字相同**却**不可复用**：
    列表恒含 ``main`` 分支，而 main 的 ``parent_branch_id`` 是 null（根节点无父），
    ``BranchCreatedResponse`` 声明的是必填非空 ``str`` ⇒ 复用会让本端点恒 500。
    本端点没有条件键 ⇒ **不加**任何 ``response_model_exclude_*`` 开关。
    """
    _ensure_project(request, project_id)
    return _service(request).list_branches(project_id)


@router.post(
    "/projects/{project_id}/branches/{branch_id}/promote",
    status_code=status.HTTP_201_CREATED,
    response_model=BranchPromoteResponse,
    response_model_exclude_unset=True,
)
def promote_branch(project_id: str, branch_id: str, payload: dict, request: Request) -> dict:
    """把分支全部 commits 合并为单个 delta，在 main 上提交（state-delta-v0.md §6.3）。

    ⚠️ 这里带的是 ``response_model_exclude_unset=True``，**不是** ``_exclude_none``——
    两者解决的是相反的问题，都不能互换（详见 ``story_state/models.py`` 里
    ``BranchPromoteResponse`` 的 docstring 与 ``_drop_if_none`` 的 docstring）：

    * 本端点有**两个形状**（空分支 7 键 / 非空分支 8 键），三个键在其中一侧**缺席**。
      不带 ``exclude_unset`` 时 Pydantic 会拿默认值把缺席的键**补成 ``null``**——
      实测空分支响应因此从 7 键变成 9 键（凭空多出 ``promoted_from`` / ``snapshot_ref``），
      那是**加 response_model 本身引入的契约变更**。
    * 而 ``commit_id`` / ``delta_id`` 在空分支形状里是**值为 null 的真键**。
      用 ``_exclude_none`` 会把它们**抹掉**（键从有变无）。

    ``exclude_unset`` 只看「生产方有没有给这个键」，两个需求同时满足。
    """
    _ensure_project(request, project_id)
    chapter_id = (payload or {}).get("chapter_id") if isinstance(payload, dict) else None
    # ``branches.promote_branch`` 里 ``replay_chapter_id = chapter_id or source_chapter_id``
    # —— **参数优先于** commit 自带的 chapter_id，所以传错能真的写出一条指向
    # 别的项目的章的重放 delta。缺省（None）是合法输入（走 commit 自带的兜底），
    # 只在显式给了值时才校验。
    if chapter_id is not None:
        _ensure_chapter_in_project(request, project_id, chapter_id)
    try:
        return _service(request).promote_branch(project_id, branch_id, chapter_id=chapter_id)
    except BranchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BranchClosed as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "branch_closed",
                "message": str(exc),
                "branch_id": exc.branch_id,
                "status": exc.status,
            },
        ) from exc
    except StateConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "promote_conflict", "message": str(exc)},
        ) from exc
    except ApprovalRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "approval_required",
                "message": str(exc),
                "high_risk_change_ids": exc.high_risk_change_ids,
            },
        ) from exc
    except OptimisticLockError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "optimistic_lock",
                "message": str(exc),
                "expected_version": exc.expected_version,
                "actual_version": exc.actual_version,
            },
        ) from exc


@router.get("/projects/{project_id}/state/diff", response_model=StateDiffResponse)
def diff_versions(
    project_id: str,
    request: Request,
    a: int,
    b: int,
    branch_id: str | None = None,
) -> dict:
    """两个 state_version 之间的结构化 diff。

    ⚠️ **刻意不加** ``response_model_exclude_none=True``：六个实体段的「空则省略」
    由模型侧的**字段级** ``exclude_if`` 实现（``story_state/models.py::_drop_if_none``
    写了为什么）。端点级开关是一刀切的，会把恒为 null 的 ``branch_id`` 一起抹掉，
    实测空 diff 的键集从 3 个掉到 2 个——那是既有契约的静默变更。
    """
    _ensure_project(request, project_id)
    try:
        return _service(request).diff_versions(project_id, a, b, branch_id=branch_id)
    except StateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StateConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "diff_conflict", "message": str(exc)},
        ) from exc
