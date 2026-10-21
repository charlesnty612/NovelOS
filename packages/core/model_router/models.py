"""Pydantic 出参模型：模型档案 / 配置 / 环节绑定 / agent 通道（G5 配置面）。

为什么需要这个模块
------------------
本组 13 个端点此前全部返回**裸 dict**、**没有** ``response_model`` ⇒ OpenAPI 里
没有它们的 schema ⇒ 前端只能在 ``apps/web/src/api/types.ts`` 手写契约，而那份手写
契约**没有任何机器校验**——后端加键、删键、改可空性，前端要等到运行时才发现。

字段形状**不是照源码读出来的，是量出来的**
------------------------------------------
``response_model`` 会**过滤**未声明字段：少声明一个，响应就静默少一个，而测试可能
全绿、功能坏掉（AGENTS 坑区「断言了结果 ≠ 判别了机制」的同族）。故本文件每个字段
都来自「临时库 + 合成夹具跑通端点 dump 实际键集」的实测（2026-10-15，
方法记录在 ``tests/api/test_model_config_response_contract.py`` 的 docstring）。
实测结论摘要：

- ``POST/GET/PATCH /model-profiles``：顶层 **9 键恒在**，无 null 键；
  ``params_json`` 已是 **dict**（DB 里是 JSON 字符串，由
  :func:`packages.core.model_router.security._mask_response` 解析并脱敏后才出参）。
- ``POST/GET/PATCH /model-configs``：顶层 **7 键恒在**，无 null 键。
- ``POST /model-profiles/{id}/test``：**5 键恒在**，``hint`` 是**条件键**
  （仅 ``status_code == 400`` 且远端错误文本命中「模型名」标记时才追加）⇒
  路由必须带 ``response_model_exclude_unset=True``，理由见
  :class:`ProfileTestResult` 的注释。
- ``POST /model-configs/{id}/test``：**5 键恒在**，恒无 ``hint``
  （该路由没有 ``_unknown_model_hint`` 分支——两个 ``/test`` 端点此处**不对称**，
  是既有行为，本批不改动）。
- ``POST /model-profiles/available-models``：**1 键** ``models``。
- ``PUT /capability-bindings/{cap}``：**7 键恒在**，其中 ``updated_at`` 在**未绑定时是
  ``null`` 而非省略**（实测 8 个环节首轮全为 ``null``）；``profiles[]`` 每项 4 键。
- ``DELETE /capability-bindings/{cap}``：**2 键**。
- ``POST /agents/sync``：**4 键**，前三项是 ``(agent, version)`` 二元组序列。
- ``POST /agents/{name}/run``：**3 键**，``output`` **随 agent 而异**
  （observer 7 键、writer/director 各有各的契约形状）⇒ 只能声明成
  ``dict[str, Any]``，不得臆造统一字段。

⚡️ **列表端点**（2026-10-15 批次（六）L3 组补齐，形状同样是量出来的，见
``tests/api/test_config_list_response_contract.py``）：
- ``GET /model-profiles``：9 键，与 :class:`ModelProfileOut` **逐字相同** ⇒ 直接复用。
- ``GET /model-configs``：7 键，与 :class:`ModelConfigOut` **逐字相同** ⇒ 直接复用。
- ``GET /capability-bindings``：7 键，与 :class:`CapabilityBindingOut` **逐字相同**
  （含「未绑定时 ``updated_at`` 为 ``null`` 而非省略」——``PUT`` 与 ``GET`` 出自同一个
  ``list_with_profiles()``，故不存在两侧不一致）⇒ 直接复用。
- ``GET /agents``：**6 键**，新建 :class:`AgentListRowOut`；``config_json`` 是
  **JSON 字符串**不是对象（见该模型注释）。
- ``GET /agents/{name}/prompts``：**7 键**，新建 :class:`AgentPromptRowOut`。
  （``GET /ai-call-logs`` 的 12 键摘要在 ``ai_call_logs.py`` 里，紧挨
  :class:`~packages.core.api.routers.ai_call_logs.AiCallLogDetail`——按该文件既有惯例
  放在 router 侧，便于把密钥面讨论与黑名单放在同一处。）

⚠️ 本模块**只描述成功响应的形状**。错误体（422/404/502/409）由 ``HTTPException``
抛出，其中 ``detail`` 或是纯字符串或是结构化 dict；结构化的那几种用
:class:`ProviderErrorEnvelope` / :class:`AgentOutputErrorEnvelope` 建模并挂进路由的
``responses=``（只影响 OpenAPI 文档，不改变运行时行为）。

🔑 密钥面（实测，非本批引入）：``params_json.api_key`` 在**读路径**恒被替换成
``"***"``（:data:`packages.core.model_router.security._MASK`），另在顶层追加
``has_api_key`` 布尔位。**明文密钥不会经任何 GET/POST/PATCH 响应回吐**。本批
照此建模，**未改动该行为**；相关发现见本批汇报。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentListRowOut",
    "AgentOutputErrorDetail",
    "AgentOutputErrorEnvelope",
    "AgentPromptRowOut",
    "AgentPromptSyncResult",
    "AgentRunResult",
    "AvailableModelsOut",
    "BindingProfileOut",
    "CapabilityBindingDeleted",
    "CapabilityBindingOut",
    "ConfigTestResult",
    "ModelConfigOut",
    "ModelProfileOut",
    "ProfileTestResult",
    "ProviderErrorDetail",
    "ProviderErrorEnvelope",
]


# ---------------------------------------------------------------------------
# 模型档案（SELECT * 派生 ⇒ extra="allow"）
# ---------------------------------------------------------------------------


class ModelProfileOut(BaseModel):
    """``model_profiles`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。

    ``extra="allow"``：service 层是 ``SELECT * FROM model_profiles``。将来加一列
    （加列迁移），严格模型会把它**静默丢掉**——症状是「后端返回了、API 取不到、
    零报错」。故本模型宣称「已知这 9 个键」，不宣称封闭。
    """

    model_config = ConfigDict(extra="allow")

    profile_id: str
    name: str
    provider: str
    model: str
    params_json: dict[str, Any] = Field(
        description=(
            "provider 调参（temperature / top_p / base_url / timeout_s …）解析后的对象；"
            "其中 api_key 恒被替换为 '***'，明文不出参"
        ),
    )
    enabled: int = Field(description="0/1 整数（非布尔）；0 的档案不可用于 /test")
    created_at: str
    updated_at: str
    has_api_key: bool = Field(
        description="是否已配置密钥；涵盖 params.api_key 与 secrets.json 两个来源",
    )
    connection_id: Optional[str] = Field(
        default=None,
        description=(
            "引用的连接 id（model_connections）；null = 该档案是「独立配置」"
            "（自带 base_url / api_key，存量档案的默认形态）"
        ),
    )


class ConnectionPresetOut(BaseModel):
    """``GET /model-connections/presets`` 的元素：一档「连接预设」。

    预设 = **一键填表**（provider / base_url / params.headers / params.models_url），
    填完仍可逐项改 ⇒ 预设过期不会把人卡住。
    ⚠️ **不含 api_key** —— 预设只给「连哪里、带什么头」。
    """

    preset_id: str
    label: str
    provider: str
    base_url: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = None


class ModelConnectionOut(BaseModel):
    """``model_connections`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。

    **连接层**（2026-10-21 批次十五）：把「连到哪里 / 怎么认证 / 带哪些头」与
    「用什么模型」分离——同一厂商的多个档案共享一条连接，改一次 key 全局生效。

    ``extra="allow"``：service 层是 ``SELECT * FROM model_connections``，
    与 :class:`ModelProfileOut` 同款口径（将来加列迁移时不被静默丢掉）。
    """

    model_config = ConfigDict(extra="allow")

    connection_id: str
    name: str
    provider: str
    base_url: Optional[str] = Field(
        default=None,
        description="连接级 base_url；anthropic / ollama 可空（用 provider 默认值）",
    )
    params_json: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "连接级调参（api_key / headers / models_url）解析后的对象；"
            "其中 api_key 恒被替换为 '***'，headers 里的敏感头逐键脱敏"
        ),
    )
    enabled: int = Field(description="0/1 整数（非布尔）；0 的连接不可用于 /test")
    created_at: str
    updated_at: str
    has_api_key: bool = Field(
        description="是否已配置密钥；涵盖 params.api_key 与 secrets.json 两个来源",
    )


class ModelConfigOut(BaseModel):
    """``model_configs`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。

    比 :class:`ModelProfileOut` 少 ``created_at`` / ``updated_at``——``model_configs``
    表**根本没有时间戳列**（见 ``database/migrations/0001_init.sql:464``），不是漏声明。
    """

    model_config = ConfigDict(extra="allow")

    config_id: str
    capability: str
    provider: str
    model: str
    params_json: dict[str, Any] = Field(
        description="provider 调参解析后的对象；其中 api_key 恒被替换为 '***'，明文不出参",
    )
    enabled: int = Field(description="0/1 整数（非布尔）；0 的配置不可用于 /test")
    has_api_key: bool


# ---------------------------------------------------------------------------
# 连通性测试（手工拼装的 dict ⇒ 严格模型，不吃 extra）
# ---------------------------------------------------------------------------


class ProfileTestResult(BaseModel):
    """``POST /model-profiles/{id}/test`` 的 200 响应。

    ⚠️ **路由必须声明** ``response_model_exclude_unset=True``。原因是本响应里
    **两种"缺"语义并存**：

    - ``status_code`` **无默认值且可空**——四个 provider 的 ``health_check`` 在网络
      异常时都返回 ``"status_code": None``（``providers.py`` 的 network error 分支），
      而 router 是 ``result.get("status_code")`` 原样透传 ⇒ 该键**恒在**、值可能为
      ``null``；
    - ``hint`` **有默认值**——router 只在 ``status_code == 400`` 且远端错误文本命中
      模型名标记时才 ``payload["hint"] = hint``，不命中时**整个键不存在**（实测：
      mock provider 的响应恰好 5 键，无 ``hint``）。

    ``exclude_none=True`` 会把 ``status_code: null`` 一并删掉（**改变了实测行为**）；
    默认的「全给」又会让 ``hint`` 恒以 ``null`` 出现（凭空多一个键）。只有
    ``exclude_unset=True`` 精确复刻「dict 里没有的键就不出参、值为 null 的键照出」。

    本模型**刻意不用** ``extra="allow"``：payload 是 router 手工拼的字面量，键集
    封闭。多给 ``extra`` 反而会让「删掉 ``hint`` 声明」这类突变**测不出来**（键会以
    extra 身份原样透出，测试恒绿）——见 ``test_profile_test_hint_requires_its_declaration``。
    """

    profile_id: str
    ok: bool = Field(description="false 表示「端点可达但鉴权/健康不过」，仍是 200")
    latency_ms: int
    detail: str = Field(description="恒为字符串（router 用 `or ''` 兜底，不会是 null）")
    status_code: Optional[int] = Field(description="上游 HTTP 码；网络异常时为 null")
    hint: Optional[str] = Field(
        default=None,
        description="仅 400 + 远端错误文本命中模型名标记时出现的中文提示；否则整个键不存在",
    )
    probe: Optional[str] = Field(
        default=None,
        description=(
            "连通性探测走的是哪条路：'models'（GET 模型列表端点，默认）/ "
            "'chat'（模型列表端点 404/405/501 时退化的极小请求探测）。"
            "⚠️ 降级探测会**真的消耗 1 个 token**（上游收了一次请求）——"
            "它只发生在用户手工触发的 /test 路径上"
        ),
    )


class ConnectionTestResult(BaseModel):
    """``POST /model-connections/{id}/test`` 的 200 响应。

    与 :class:`ProfileTestResult` **同构**（字段名换 connection_id）。
    ``status_code`` 同样**无默认值且可空**（网络异常为 ``null``）；
    ``probe`` 是**有默认值**的键 ⇒ 路由必须声明 ``response_model_exclude_unset=True``，
    否则 ``probe`` 恒以 ``null`` 出现（凭空多一个键）。
    """

    connection_id: str
    ok: bool = Field(description="false 表示「端点不可达或健康不过」，仍是 200")
    latency_ms: int
    detail: str = Field(description="恒为字符串（router 用 `or ''` 兜底，不会是 null）")
    status_code: Optional[int] = Field(description="上游 HTTP 码；网络异常时为 null")
    probe: Optional[str] = Field(
        default=None,
        description="探测路径：'models'（模型列表端点）/ 'chat'（退化探测）",
    )


class ConfigTestResult(BaseModel):
    """``POST /model-configs/{id}/test`` 的 200 响应。

    与 :class:`ProfileTestResult` **同构但无** ``hint``：两个 ``/test`` 端点只有档案侧
    接了 ``_unknown_model_hint``（task-28 的形状），配置侧没有。属既有行为，本批不改。
    ``status_code`` 同样**无默认值且可空**（网络异常为 ``null``）。
    """

    config_id: str
    ok: bool
    latency_ms: int
    detail: str
    status_code: Optional[int]


class AvailableModelsOut(BaseModel):
    """``POST /model-profiles/available-models`` 的 200 响应。

    代理拉取各家 ``/models``（或 Ollama ``/api/tags``）后排序去重的模型名数组。
    拉不到是 4xx/5xx（detail 为纯字符串），不是空数组——空数组只在上游真的返回
    空列表时出现。
    """

    models: list[str]


# ---------------------------------------------------------------------------
# 环节绑定（BindingService.list_with_profiles 现场组装 ⇒ extra="allow"）
# ---------------------------------------------------------------------------


class BindingProfileOut(BaseModel):
    """``CapabilityBindingOut.profiles[]`` 的一项。

    **刻意不含** ``params_json``：绑定列表把档案详情裁到 4 个标识字段，
    密钥状态走 ``/model-profiles/{id}`` 的脱敏读路径（见 ``bindings.py`` 的模块 docstring）。
    同样**没有** ``has_api_key``——没在列表里暴露密钥存在性。
    """

    profile_id: str
    name: str
    model: str
    provider: str


class CapabilityBindingOut(BaseModel):
    """``PUT /capability-bindings/{capability}`` 的 200 响应。

    ⚠️ ``updated_at`` **无默认值且可空**：未绑定该环节时实测为 ``null``
    （``bindings.py`` 的 ``binding["updated_at"] if binding else None``），
    而不是省略。给了默认值就变成「可省略」，前端据此写出的类型是错的。

    ``profiles`` 与 ``profile_ids`` 恒在：未绑定时是**空数组**而非省略。
    ``legacy_available`` 恒在：只看 ``model_configs`` 里该 capability 有无 enabled 行。
    """

    model_config = ConfigDict(extra="allow")

    capability: str
    label: str = Field(description="环节中文名，来自 CAPABILITY_LABELS")
    agents: list[str] = Field(description="归属该环节的 agent 名（含 'writer(revise·改稿)' 这类带标注形态）")
    profile_ids: list[str] = Field(description="按 fallback 优先序；未绑定时为空数组")
    profiles: list[BindingProfileOut] = Field(description="按 profile_ids 顺序解析；缺失项跳过")
    legacy_available: bool = Field(description="model_configs 中该 capability 有 enabled 行（迁移引导用）")
    updated_at: Optional[str] = Field(description="未绑定时为 null（不是省略）")


class CapabilityBindingDeleted(BaseModel):
    """``DELETE /capability-bindings/{capability}`` 的 200 响应。"""

    capability: str
    deleted: bool


# ---------------------------------------------------------------------------
# agent 通道
# ---------------------------------------------------------------------------


class AgentPromptSyncResult(BaseModel):
    """``POST /agents/sync`` 的 200 响应。

    前三项是 ``(agent, version)`` 二元组序列（JSON 侧是二元素数组），
    ``agents`` 是去重后的 agent 名升序列表。
    """

    model_config = ConfigDict(extra="allow")

    scanned: list[tuple[str, int]]
    registered: list[tuple[str, int]]
    updated: list[tuple[str, int]] = Field(description="content 发生变化的子集，可能是空数组")
    agents: list[str]


class AgentRunResult(BaseModel):
    """``POST /agents/{name}/run`` 的 201 响应。

    ⚠️ ``output`` **随 agent 而异**：observer 返回 7 个 delta 数组
    （``new_hooks`` / ``new_events`` / ``world_changes`` / ``resolved_hooks`` /
    ``relationship_changes`` / ``debt_changes`` / ``character_changes``），
    writer / director 各有自己的契约。端点只认 ``expected ∈ {observer, director, writer}``，
    没有统一输出形状 ⇒ 声明成 ``dict[str, Any]``，**不得臆造**一个统一字段集。

    ``output`` 失败时走 502（``agent_output_invalid``），不属本模型。
    """

    model_config = ConfigDict(extra="allow")

    run_id: str
    agent: str
    output: dict[str, Any]


class AgentListRowOut(BaseModel):
    """``GET /agents`` 的**一行**（:meth:`PromptRegistry.list_agents` 的 SELECT 6 列）。

    形状来自 2026-10-15 的临时库实测（``POST /agents/sync`` 播种 16 个 agent 后
    dump 实际行键集），不是照源码读出来的。

    ⚠️ ``config_json`` 是 **JSON 字符串，不是对象**：``agents`` 表该列是 TEXT，
    ``list_agents`` 原样透传（写入方 ``_upsert_agent`` 存的是 ``json.dumps(...)`` 的
    结果）。声明成 ``dict[str, Any]`` **不会**让 Pydantic 帮你解析——实测它**直接校验
    失败**（``Input should be a valid dictionary``）⇒ 整个端点 500（2026-10-15 用
    throwaway 模型实测，非推断）。要对象请在前端 ``JSON.parse``；后端这里刻意不解析。

    ``extra="allow"``：SELECT 虽是**显式列清单**，但将来往里加一列时严格模型会把它
    **静默丢掉**——症状是「数据在库里、API 读不到、零报错」（AGENTS 坑区「数据源存在
    ≠ 已进装配」）。故本模型宣称「已知这 6 个键」，不宣称封闭。**代价**：删掉任一字段
    声明不会让响应少一个键（键以 extra 身份透出）⇒ 少声明这类突变**只能**从 OpenAPI
    ``properties`` 侧判别，响应键集断言恒绿（2026-10-15 M20 同族）。
    """

    model_config = ConfigDict(extra="allow")

    agent_id: str
    name: str
    role: str = Field(description="= config_json['capability']（见 PromptRegistry._upsert_agent）")
    config_json: str = Field(description="JSON **字符串**（端点未解析），形如 '{\"capability\": \"reasoning\"}'")
    created_at: str
    updated_at: str = Field(description="每次 sync 都会刷新（内容未变也刷）；created_at 则始终保留原值")


class AgentPromptRowOut(BaseModel):
    """``GET /agents/{name}/prompts`` 的**一行**（:meth:`PromptRegistry.list_prompts` 的 7 列）。

    实测（sync 后 16 个 agent，逐个 dump）：恒 7 键、无 null 键。agent 不存在时端点
    返回 **``[]``（200，不是 404）**——既有行为，本批不改、也不建模成错误体。

    ``version`` 列存的是 ``"v" + 数字``（如 ``"v3"``），**不含 agent 前缀**；
    与 ``ai_call_logs.prompt_version``（``"writer:v3"``）不是同一形态，别混用。

    ``status`` 取自 DB 的 CHECK 约束（``DRAFT / ACTIVE / DEPRECATED``）。声明成 ``str``
    而非 Literal：约束在 DB 层，枚举收敛属于产品决策；前端按字符串处理即可。

    ``content`` 是 prompt 全文（writer 系列可达数十 KB），列在这里是因为端点确实返回
    它——列表端点不做截断（前端自己决定要不要展开）。

    ``extra="allow"``：同 :class:`AgentListRowOut` 的理由（显式 SELECT 将来加列不该被
    静默丢掉），同样意味着「少声明字段」这类突变只能从 OpenAPI 侧抓。
    """

    model_config = ConfigDict(extra="allow")

    prompt_id: str
    agent_id: str
    version: str = Field(description="形如 'v3'（不含 agent 前缀）")
    content: str
    status: str = Field(description="DRAFT / ACTIVE / DEPRECATED（DB CHECK 约束）")
    created_at: str
    updated_at: str = Field(description="**内容变化或新增**时才刷新；内容逐字相同的重复 sync 完全跳过 UPDATE")


# ---------------------------------------------------------------------------
# 错误体（只进 OpenAPI 文档，不改变运行时行为）
# ---------------------------------------------------------------------------


class ProviderErrorDetail(BaseModel):
    """``HTTPException(502, detail={...})`` 的 ``detail`` 部分。"""

    model_config = ConfigDict(extra="allow")

    error: str = Field(description="固定 'provider_error'")
    message: str
    status_code: Optional[int] = Field(description="上游 HTTP 码；provider 未带上来时为 null")


class ProviderErrorEnvelope(BaseModel):
    """FastAPI 把 ``HTTPException(detail=...)`` 包成 ``{"detail": ...}``。"""

    detail: ProviderErrorDetail


class AgentOutputErrorDetail(BaseModel):
    """``AgentOutputError`` 映射出的 ``detail``（**无** ``status_code``）。"""

    error: str = Field(description="固定 'agent_output_invalid'")
    message: str


class AgentOutputErrorEnvelope(BaseModel):
    detail: AgentOutputErrorDetail
