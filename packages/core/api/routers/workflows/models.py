"""workflows 路由的**出参** Pydantic 模型（2026-10-15 批次（六）·G4）。

为什么需要这个模块
------------------
`control` / `runs` / `revise` 三个子模块此前一律 `-> dict[str, Any]` 且**没有**
`response_model` ⇒ OpenAPI 里这批端点的 200/201 响应只有 `{"type": "object"}`，
前端 `apps/web/src/api/types.ts` 里手写的 `WorkflowRun` / `ContextPreview` 等契约
**没有任何机器校验**——后端加键、删键，前端要等运行时才发现。

字段形状是**量出来的，不是照源码读出来的**
----------------------------------------
`response_model` 会**过滤**未声明字段：少声明一个，响应就静默少一个，而用 `>=`
断言的测试照样全绿。所以建模前用临时库 + mock provider 跑通端点、把实际键集 dump
出来（方法与实测结论记在 `tests/api/test_workflow_run_response_contract.py` 的
模块 docstring 里）。实测要点：

1. **起跑类端点（plan/write/review/commit/gate-revise/resume）恒 3 键**：
   `run_id` / `status` / `current_node`；`status` 恒 `"RUNNING"`（异步化后 HTTP
   立即返回，真终态由 `GET /runs/{id}` 轮询拿）。
2. **`POST /projects/init` 是 4 键 + 1 条件键**：多一个 `project_id`，且它**可以
   是 null**（step_mode 下 persist 节点还没跑）；PAUSED 时才附加 `pause_payload`。
3. **`cancel` 恒 2 键**（`run_id` / `status`）——最简的一个，不为了凑字段硬造。
4. **`GET /runs/{id}` 的键集随 status 变**：非 PAUSED 13 键；PAUSED 15 键
   （多 `pause_payload` 与 `stage_models`）。**且同一个响应里 `chapter_id` /
   `error` / `ended_at` 常常「键在、值为 null」**——这三个键不能被任何开关抹掉。

条件字段的序列化口径（**本模块最容易踩的一格**）
-------------------------------------------------
第 4 条决定了两个开关都不能用：

* `response_model_exclude_none=True` 会把 `chapter_id` / `error` / `ended_at` 这类
  **本来就在响应里、值为 null** 的键也抹掉 ⇒ 键数比实测少 ⇒ 契约被改小；
* 不加开关则两个条件键恒以 `null` 出现 ⇒ 非 PAUSED 时凭空多两个键 ⇒ 契约被改大。

正解是 **`response_model_exclude_unset=True`**：FastAPI 把返回的 dict 校验成模型
时，`model_fields_set` 就是该 dict 的键集，故「缺席的键」被排除、「在场但为 null
的键」原样保留——与实测逐字一致。

⚠️ **这个开关不许被当成冗余参数删掉**：`test_run_detail_paused_only_when_paused`
钉住 13 / 15 两个精确键集；去掉开关（任一方向）该用例必红。

`extra="allow"` 的统一口径
--------------------------
本文件**所有**模型都开 `extra="allow"`。理由分两类，都是实证而非偏好：

* ``WorkflowRunDetail`` / ``WorkflowRunNode`` 的键来自 ``SELECT *``——**迁移加列就会
  多一个键**。`instance_id` 就是 0026 给 `workflow_runs` 加的，已实证过一次。关掉
  extra 即意味着「下次加列时这个键静默消失」。
* 起跑 / init-status / context-preview 的 payload 是手写字典，但同包的
  `ArcView` 已有同款约定（见 `packages/core/arc/models.py`）：契约描述**已知的键**，
  不宣称封闭。

代价是「删掉字段声明」在响应里**看不出来**（这正是 M20 实测存活的那一格），所以
测试必须**另外**断言 OpenAPI 里 schema 的 `properties` / `required`，不能只看响应。

依赖方向
--------
本模块只依赖 pydantic。**不得** import ``packages/workflows.*``（§二硬规则 5：
core 不得 import workflows，有专门测试看守），也不得 import 任何 ``core`` 子模块的
领域服务——保持「纯出参模型」定位。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ContextPreview",
    "ContextPreviewItem",
    "ContextPreviewLayer",
    "ProjectInitStage",
    "ProjectInitStarted",
    "ProjectInitStatus",
    "RunCancelled",
    "WorkflowRunDetail",
    "WorkflowRunNode",
    "WorkflowRunSummary",
    "WorkflowStartAccepted",
]


# ---------------------------------------------------------------------------
# 起跑 / 恢复 / 取消（control + revise）
# ---------------------------------------------------------------------------


class WorkflowStartAccepted(BaseModel):
    """章节级起跑端点的统一 201/200 响应。

    覆盖 ``POST .../plan`` / ``.../write`` / ``.../review`` / ``.../commit`` /
    ``.../gate-revise`` 与 ``POST /runs/{run_id}/resume`` —— 实测**六个端点逐字同形**
    （都是同一批 dict 字面量的再包装），故共用一个模型而不是各建一个。

    ⚠️ ``status`` 声明为 ``str`` 而非 ``Literal["RUNNING"]``：实测当前**恒**为
    ``"RUNNING"``（``_start_workflow_core`` 与 ``resume_run`` 都硬编码该值，启动失败
    一律 raise 400/409/422 而非返回），但 ``project-init`` 已经是**同步**启动、
    同一族端点将来也可能改回同步。把「当前取值」写进类型等于宣称一个实现细节是
    契约；本批改为在测试里钉住该不变量（``test_start_endpoints_status_is_running``）。

    ``current_node`` 必填可空、不给默认值：实测该键**恒在**、值恒 ``null``（异步启动
    时还没有节点在跑）。给了默认值就生成成可选键，前端会误以为它可能缺席。
    """

    model_config = ConfigDict(extra="allow")

    run_id: str = Field(description="新建 run 的主键；真终态需轮询 GET /api/runs/{run_id}")
    status: str = Field(description="启动后恒为 RUNNING（同步化的 project-init 除外）")
    current_node: Optional[str] = Field(
        description="恒为 null：异步启动返回时还没有节点在跑；给前端留位置",
    )


class ProjectInitStarted(BaseModel):
    """``POST /api/projects/init`` 的 201 响应。

    与 :class:`WorkflowStartAccepted` 的差别有两条，都是实测的：

    1. **多一个 ``project_id``**，且它**可以是 null**——step_mode 下 persist 节点
       还没执行，``checkpoint_json`` 里取不到（``_start_project_init`` 用
       ``.get("project_id")``）。故必填可空、不给默认值。
    2. **``status`` 真的不是恒 RUNNING**：本端点用同步的 ``start_with_nodes``，
       step_mode 下实测直接返回 ``"PAUSED"``，并附加 ``pause_payload``。

    ``pause_payload`` 的**形状随 workflow 而变**（实测：project-init 的 step 暂停
    6 键 ``degraded/draft/error/stage/stage_index/stages_total``；chapter-review 的
    author_review 暂停 10 键 ``critic_*/deep_review_*/message/review_report/stage``），
    故声明为 ``dict[str, Any]`` 而不是逐字段建模。端点上配
    ``response_model_exclude_unset=True`` 使它**只在 PAUSED 时出现**。
    """

    model_config = ConfigDict(extra="allow")

    run_id: str
    status: str = Field(description="RUNNING / PAUSED / COMPLETED / FAILED；同步启动，非恒 RUNNING")
    current_node: Optional[str]
    project_id: Optional[str] = Field(description="step_mode 暂停时 persist 节点未跑 ⇒ 可为 null")
    pause_payload: Optional[dict[str, Any]] = Field(
        default=None,
        description="仅 status=PAUSED 时出现；形状随 workflow 而变，不逐字段建模",
    )


class RunCancelled(BaseModel):
    """``POST /runs/{run_id}/cancel`` 的 200 响应。

    实测**恒 2 键**——本端点不加 ``current_node``（取消后 run 已在库里，前端拿
    ``run_id`` 去轮询详情即可）。**不为了凑字段硬造**。
    """

    model_config = ConfigDict(extra="allow")

    run_id: str
    status: str = Field(description="成功恒为 CANCELLED；409 冲突体走 FastAPI 的 {detail}")


# ---------------------------------------------------------------------------
# GET /projects/{pid}/init-status（runs）
# ---------------------------------------------------------------------------


class ProjectInitStage(BaseModel):
    """project-init 四环节之一的落库状态（premise / world / character / outline）。

    实测**四个 stage 逐字同形**，各 4 键，无条件键。
    """

    model_config = ConfigDict(extra="allow")

    stage: str = Field(description="premise / world / character / outline")
    label: str = Field(description="人类可读名（题材定位 / 世界观 / 核心角色 / 卷纲与章节种子）")
    done: bool = Field(description="由 DB 现有数据推导，不建新表")
    detail: str = Field(description="人类可读说明；未生成为「未生成」")


class ProjectInitStatus(BaseModel):
    """``GET /projects/{pid}/init-status`` 的 200 响应（实测恒 2 键）。

    四环节的判定口径是**数据推导**（premise 看 ``projects.premise``、world 看三张
    世界表任一有行、character 看 ``characters``、outline 看卷有标题且章有
    ``chapter_goal``），本模型只描述形状，不复制那套判定规则——那是
    ``runs._init_status_for_project`` 的职责。
    """

    model_config = ConfigDict(extra="allow")

    stages: list[ProjectInitStage] = Field(description="恒 4 行，顺序即 premise→world→character→outline")
    has_any_data: bool = Field(description="四环节 done 的或；用于 UI 决定是否提示先跑 project-init")


# ---------------------------------------------------------------------------
# GET /runs/{run_id}（runs）
# ---------------------------------------------------------------------------


class WorkflowRunNode(BaseModel):
    """``workflow_run_nodes`` 的一行（run 详情里的 ``nodes[]``）。

    ⚠️ 键集来自 ``SELECT *`` ⇒ **迁移给该表加列就会多一个键**（实测
    ``instance_id`` 同款：0026 给 ``workflow_runs`` 加了列）。故开 ``extra="allow"``。
    """

    model_config = ConfigDict(extra="allow")

    node_run_id: str
    run_id: str
    node_id: str = Field(description="workflow 定义里的节点 id（不是 agent 名）")
    agent_id: Optional[str] = Field(description="纯逻辑节点无 agent ⇒ null")
    status: str = Field(description="PENDING / RUNNING / COMPLETED / FAILED / SKIPPED")
    input_json: dict[str, Any] = Field(description="节点输入；DB 列 NOT NULL DEFAULT '{}'，故恒为对象")
    output_json: Optional[dict[str, Any]] = Field(description="非结构化输出（SKIPPED / 尚未跑完）时为 null")
    prompt_version: Optional[str] = Field(description="未调 AI 的节点为 null")
    model_id: Optional[str] = Field(description="未调 AI 的节点为 null")
    token_usage_json: Optional[dict[str, Any]] = Field(description="{prompt, completion, total}；未调 AI 为 null")
    latency_ms: Optional[int]
    error: Optional[str]
    started_at: Optional[str] = Field(description="PENDING（尚未开始）为 null")
    ended_at: Optional[str] = Field(description="未到终态为 null")


class WorkflowRunDetail(BaseModel):
    """``GET /runs/{run_id}`` 的 200 响应。

    **键集随 status 变（实测）**：非 PAUSED 13 键；PAUSED 15 键，多出
    ``pause_payload`` 与 ``stage_models``。端点上配 ``response_model_exclude_unset=True``
    来复现这个条件性——**不要**改成 ``exclude_none``（会连带抹掉「键在、值为 null」的
    ``chapter_id`` / ``error`` / ``ended_at``，把 13 键砍成更少，等于改契约）。

    ``checkpoint_json`` 声明为 ``dict[str, Any]`` 且**不逐键建模**：实测它的顶层键是
    「workflow 节点 id + ``_`` 前缀引擎内部键 + ctx 键」的合集（chapter-review 一轮
    实测 14 个，含 ``_current_node_id`` / ``author_review`` / ``review_inputs`` /
    ``mock_providers``），随 workflow 与节点推进而变，逐字段建模只会造出一个必然
    过期的假契约。

    ⚠️ **残余边界（如实登记）**：``pause_payload`` / ``stage_models`` 声明为
    ``Optional`` + 默认 ``None``，因此「PAUSED 但 ``_extract_pause_payload`` 恰好返回
    None」时这两个键会以 ``null`` 出现而非缺席。实测 PAUSED run 恒有真实 payload，
    故该分支在本仓不可达；真出现了也是「键在、值为 null」，与本响应里其它可空键同形。
    """

    model_config = ConfigDict(extra="allow")

    run_id: str
    workflow_id: str = Field(description="workflows 表主键；人类可读名见 workflow_name")
    workflow_name: Optional[str] = Field(description="反查 workflows.name；孤儿 run 为 null")
    chapter_id: Optional[str] = Field(description="project-init 等非章节任务的 run 为 null")
    # 0037（2026-10-24）：**必填可空**（Optional 无默认值 = 键恒在、值可 null），
    # 与 chapter_id / error 同款契约——真实响应由 SELECT * 直出，该列恒在场；
    # null 仅出现在 0037 前历史行中 checkpoint 无 project_id 的（如 adhoc 裸 run）。
    project_id: Optional[str] = Field(
        description=(
            "0037 新增的 run 归属列（outline-batch 项目级并发守卫的过滤依据）；"
            "checkpoint 无 project_id 的历史行（如 adhoc 裸 run）为 null"
        )
    )
    status: str = Field(description="PENDING / RUNNING / PAUSED / COMPLETED / FAILED / CANCELLED")
    current_node: Optional[str]
    checkpoint_json: dict[str, Any] = Field(description="暂停/恢复持久化点；键集随 workflow 演进，不建模")
    error: Optional[str] = Field(description="失败原因；成功 run 为 null")
    retry_count: int
    started_at: str
    ended_at: Optional[str] = Field(description="未到终态为 null")
    instance_id: Optional[str] = Field(description="0026 新增的实例身份列；未设置时为 null")
    nodes: list[WorkflowRunNode] = Field(description="按 (started_at, node_run_id) 升序；无节点时为 []")
    # —— 以下两个是**条件键**，仅 status=PAUSED 时由端点附加 ——
    pause_payload: Optional[dict[str, Any]] = Field(
        default=None,
        description="仅 PAUSED 时出现；各 workflow 的挂起节点不同 ⇒ 形状不固定",
    )
    stage_models: Optional[dict[str, str]] = Field(
        default=None,
        description="仅 PAUSED 时出现；{agent 名: 本次实际调用的 model_id}，无成功调用时为 {}",
    )


# ---------------------------------------------------------------------------
# GET /chapters/{chapter_id}/context-preview（runs）
# ---------------------------------------------------------------------------


class ContextPreviewItem(BaseModel):
    """预览条目——**只有这 3 个键是所有 kind 共有的**。

    实测三层 items 的键并集各不相同：

    * L0 → ``kind / id / name``（4 个条目全是这三键）
    * L1 → ``kind / id / name / role / source / injection / status``（角色带注入策略标记）
    * L2 → ``kind / id / name / beats``（计划条目带 key_beats 计数）

    交集恒为 ``kind`` / ``id`` / ``name``（``_push_item`` 无条件写入这三键，L0 的
    直写 append 同款）。因此**只把这三项建模、其余交给 ``extra="allow"``**：
    既让前端拿到可用的类型，又不把「某一层某一 kind 的专属键」误升格成全局契约。

    ⚠️ 这正是一个**逼不得**的建模点：若图省事统一建模成「所有 kind 都有 role /
    beats」，前端会照着到处写可选兜底；若改成 ``dict[str, Any]`` 又丢掉全部类型信息。
    """

    model_config = ConfigDict(extra="allow")

    kind: str = Field(description="条目类型：chapter / project / character / hook / director_plan / …")
    id: str = Field(description="实体 id；非实体条目用下划线常量（如 _snap / _intent / _kp）")
    name: str = Field(description="人类可读摘要")


class ContextPreviewLayer(BaseModel):
    """预览的单个分层（L0 / L1 / L2）。实测三层**逐字同形**、恒 5 键。"""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="L0 / L1 / L2")
    label: str = Field(description="该层装载内容的人类可读说明")
    token_estimate: int = Field(description="该层的 token 估算")
    items: list[ContextPreviewItem]
    truncated: bool = Field(description="是否因超预算被截断；实测恒 false（截断尚未实现）")


class ContextPreview(BaseModel):
    """``GET /chapters/{chapter_id}/context-preview`` 的 200 响应（实测恒 7 键）。

    **只读 dry-run**：不调 LLM、不写 ``ai_call_logs``，走独立的预览缓存命名空间。
    体积大且随装配内容演进 ⇒ ``layers[].items[]`` 只建模共有键（见
    :class:`ContextPreviewItem`）。
    """

    model_config = ConfigDict(extra="allow")

    chapter_id: str
    project_id: str
    agents: list[str] = Field(description="被预览的 agent 名，实测恒 ['director', 'writer', 'observer']")
    layers: list[ContextPreviewLayer] = Field(description="恒 3 层，顺序 L0→L1→L2")
    total_tokens: int
    token_budget: int
    within_budget: bool = Field(description="total_tokens <= token_budget")


# ---------------------------------------------------------------------------
# GET /projects/{pid}/runs（runs）——列表行，2026-10-15 批次（六）L2 组追加。
# 键集由实测得出（临时库 + 合成夹具），不是照 ``SELECT *`` 推的。
# -----------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /projects/{pid}/runs（runs）——列表行
# ---------------------------------------------------------------------------


class WorkflowRunSummary(BaseModel):
    """``GET /projects/{pid}/runs`` 的**行**（实测恒 13 键）。

    ⚠️ **不能复用 :class:`WorkflowRunDetail`**：两者是不同形状的两个方向。
    详情 = ``workflow_runs`` 的一行 + ``nodes[]`` + PAUSED 时追加的两个条件键（实测
    非 PAUSED 13 / PAUSED 15）；本列表 = ``workflow_runs`` 的一行（11 键）+ **无 nodes**
    + ``workflow_name`` + **``label``**。恰好都是 13 键纯属巧合，键集并不相同：
    详情多 ``nodes``，列表多 ``label``。

    ``label`` 由 ``_compute_run_label`` 从 ``_WORKFLOW_ZH_LABELS`` 推导，**键恒在、
    值可为 null**：实测「workflow 名不在那张映射表里」的 run（探针用 deconstruct-book）
    ``label`` 就是 null，而同页 ``workflow_name`` 是字符串。反过来 ``workflow_name``
    也声明成 Optional——它经 JOIN ``workflows`` 反查，孤儿 run 为 null。

    ``chapter_id`` 可空：project-init 等非章节任务的 run 就是 null（实测）。

    ``checkpoint_json`` 声明成 ``dict[str, Any]`` 不逐键建模，理由同
    :class:`WorkflowRunDetail`（键集随 workflow 与节点推进而变）。

    ``extra="allow"``：行来自 ``SELECT wr.*`` ⇒ 迁移给 ``workflow_runs`` 加列就多一键
    （``instance_id`` 就是 0026 的先例）。
    """

    model_config = ConfigDict(extra="allow")

    run_id: str
    workflow_id: str
    workflow_name: Optional[str] = Field(description="JOIN workflows 反查；孤儿 run 为 null")
    chapter_id: Optional[str] = Field(
        description="列表行专属：project-init 等非章节任务的 run 为 null（实测）"
    )
    # 0037（2026-10-24）：**必填可空**，与 WorkflowRunDetail.project_id 同款契约
    # （行来自 SELECT wr.*，该列恒在场；checkpoint 无 project_id 的历史行为 null）。
    project_id: Optional[str] = Field(
        description="0037 新增的 run 归属列；checkpoint 无 project_id 的历史行为 null"
    )
    status: str = Field(description="PENDING / RUNNING / PAUSED / COMPLETED / FAILED / CANCELLED")
    current_node: Optional[str]
    checkpoint_json: dict[str, Any] = Field(description="键集随 workflow 演进，不逐键建模")
    error: Optional[str] = Field(description="失败原因；成功 run 键在、值为 null")
    retry_count: int
    started_at: str
    ended_at: Optional[str] = Field(description="未到终态时键在、值为 null")
    instance_id: Optional[str] = Field(description="0026 新增的实例身份列")
    label: Optional[str] = Field(description="人类可读标签；workflow 名不在中文映射表里时为 null")
