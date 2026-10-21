"""L3 组（配置与日志的**列表**端点）的突变规格 —— 纯数据。

被测物
------
六个列表端点的 ``response_model=`` 声明 + 三个新行模型
（``AgentListRowOut`` / ``AgentPromptRowOut`` 追加在
:mod:`packages.core.model_router.models`；``AiCallLogSummary`` 在
``ai_call_logs.py`` 内，与既有的 ``AiCallLogDetail`` 放一起）。

执行器：``tests/mutation_specs/run_spec.py``（共用、**谁都不许改**）::

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py tests/mutation_specs/l3_config_list.py

⚠️ **写完只写不跑**（并发批次约束）：执行器会临时改源码，与任何 pytest 并发都会污染
别人的树。本文件的每条锚点都已用「目标文件内恰好命中 1 次」挑选（执行器的第 4 条硬断言
会在不满足时 ``[ABORT]`` 中止，而不是记成「跳过」）。

覆盖矩阵（每条都真改行为）
--------------------------
=====================  ==============================================================
维度                   条目
=====================  ==============================================================
撤 ``response_model``   L3M01~L3M06（6 个端点各一条，覆盖全表）
少声明字段              L3M07/08（``extra="allow"`` 模型）／L3M09（严格模型）
去掉 ``extra="allow"``  L3M10/11
类型改错                L3M12（可空→有默认值）／L3M13（string→object）
🔑 密钥面               L3M14/15（列表路径的 ``_mask_response`` 被摘掉）
=====================  ==============================================================

本组最值得记的两条
----------------
**L3M07/L3M08（extra="allow" 模型上少声明字段）**：把 ``config_json`` / ``content``
的声明删掉后，响应**照样有该键**（以 extra 身份透出），于是「响应键集 == 实测基准」那条
主断言**恒绿**——只有 OpenAPI ``properties`` 断言能抓到（M20 同族，g5 已记）。这正是
「断言了结果 ≠ 判别了机制」在列表侧的落点。

**L3M09（严格模型上少声明字段）**：同样的突变在 ``AiCallLogSummary`` 上**响应立刻少一个
键**。两条并排说明「有没有 OpenAPI 侧断言」不是可选项，取决于模型选了哪种 extra 策略。

**L3M14/15（🔑 密钥）**：把列表端点里的 ``_mask_response(r)`` 换成 ``list(rows)``，
⇒ ``test_list_rows_never_leak_the_plaintext_api_key`` 必红。⚠️ 刻意突变**调用点**
（在本组独占的 router 文件里）而不是 ``model_router/security.py::_mask_params``
（根因所在，但不是本组独占文件）：并发批次里改别人的文件有覆盖风险。

⚠️ **2026-10-21 批次十五订正（原描述「明文 api_key 直接出现在响应里」不准）**：
实测（``tmp/probe_l3m15.py``，2026-10-21）撤掉出口后红的原因**不是**明文出参，
而是**端点 500** —— DB 里的 ``params_json`` 是 **JSON 字符串**，没走掩码就进不了
``ModelProfileOut.params_json: dict[str, Any]`` ⇒ ``ResponseValidationError``。
⇒ 规格的**判别力不变**（确实红），但**失败机制**是「端点挂掉」而非「明文泄漏」。

**覆盖面已实测闭合**（``tmp/probe_mask_inner.py``，2026-10-21）：把**整个出口**撤掉
⇒ 两条用例都红（``test_model_profiles_list_returns_the_measured_ten_keys`` 因 500、
``test_list_rows_never_leak_the_plaintext_api_key``）；把**内部那一步**
``out["api_key"] = _MASK`` 撤掉（保留 dict 化）⇒ **恰好 1 条**红
（``test_list_rows_never_leak_the_plaintext_api_key``，此处端点不 500、明文真出参），
其余 27 条全绿 ⇒ 两个场景各有判别点，**无缺口**。
"""

from __future__ import annotations

TEST_FILE = "tests/api/test_config_list_response_contract.py"

CONFIGS = "packages/core/api/routers/model_configs.py"
PROFILES = "packages/core/api/routers/model_profiles.py"
BINDINGS = "packages/core/api/routers/capability_bindings.py"
AGENTS = "packages/core/api/routers/agents.py"
LOGS = "packages/core/api/routers/ai_call_logs.py"
MODELS = "packages/core/model_router/models.py"

# (id, 文件, 锚点, 替换, 测试文件)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # ================================================================ 逐端点撤 response_model
    (
        "L3M01-model-configs-no-response-model",
        CONFIGS,
        '@router.get("/model-configs", response_model=list[ModelConfigOut])',
        '@router.get("/model-configs")',
        TEST_FILE,
    ),
    (
        "L3M02-model-profiles-no-response-model",
        PROFILES,
        '@router.get("/model-profiles", response_model=list[ModelProfileOut])',
        '@router.get("/model-profiles")',
        TEST_FILE,
    ),
    (
        "L3M03-capability-bindings-no-response-model",
        BINDINGS,
        '@router.get("/capability-bindings", response_model=list[CapabilityBindingOut])',
        '@router.get("/capability-bindings")',
        TEST_FILE,
    ),
    (
        "L3M04-agents-no-response-model",
        AGENTS,
        '@router.get("/agents", response_model=list[AgentListRowOut])',
        '@router.get("/agents")',
        TEST_FILE,
    ),
    (
        "L3M05-agent-prompts-no-response-model",
        AGENTS,
        '@router.get("/agents/{name}/prompts", response_model=list[AgentPromptRowOut])',
        '@router.get("/agents/{name}/prompts")',
        TEST_FILE,
    ),
    (
        "L3M06-ai-call-logs-no-response-model",
        LOGS,
        '@router.get("/ai-call-logs", response_model=list[AiCallLogSummary])',
        '@router.get("/ai-call-logs")',
        TEST_FILE,
    ),
    # ================================================================ 少声明字段
    (
        "L3M07-agent-row-missing-config-json-declaration",
        MODELS,
        # AgentListRowOut 是 extra="allow" ⇒ 删掉声明后响应**仍有** config_json，
        # 只有 OpenAPI properties 断言能抓到（响应键集断言恒绿）。
        "    config_json: str = Field(",
        "    config_json_declaration_removed: str = Field(",
        TEST_FILE,
    ),
    (
        "L3M08-prompt-row-missing-content-declaration",
        MODELS,
        # 同上（AgentPromptRowOut，extra="allow"）：content 走 extra 透出。
        "    content: str\n",
        "",
        TEST_FILE,
    ),
    (
        "L3M09-log-summary-missing-cost-declaration",
        LOGS,
        # AiCallLogSummary 是**严格**模型：少声明 cost ⇒ 响应直接少一个键
        # （与 L3M07/08 并排，说明两种 extra 策略需要两种断言）。
        "    token_usage: dict[str, Any] | None\n    cost: float | None\n",
        "    token_usage: dict[str, Any] | None\n",
        TEST_FILE,
    ),
    # ================================================================ 去掉 extra="allow"
    (
        "L3M10-agent-row-drops-extra-allow",
        MODELS,
        # 锚点带下一行是为了在多个 extra="allow" 模型里唯一定位。
        '    model_config = ConfigDict(extra="allow")\n\n    agent_id: str\n',
        "    agent_id: str\n",
        TEST_FILE,
    ),
    (
        "L3M11-prompt-row-drops-extra-allow",
        MODELS,
        '    model_config = ConfigDict(extra="allow")\n\n    prompt_id: str\n',
        "    prompt_id: str\n",
        TEST_FILE,
    ),
    # ================================================================ 类型改错
    (
        "L3M12-log-summary-node-run-id-gets-default",
        LOGS,
        # 加默认值 = 从 required 掉出去（键集与响应完全不变，只有 OpenAPI 抓得到）。
        # 「可空但键恒在」被改成「可省略」，前端会据此写出错误的类型。
        "    node_run_id: str | None\n    agent_id: str | None\n",
        "    node_run_id: str | None = None\n    agent_id: str | None\n",
        TEST_FILE,
    ),
    (
        "L3M13-agent-row-config-json-becomes-object",
        MODELS,
        # 声明成 dict ⇒ 实测**不是**「把字符串解析成对象」，而是 Pydantic 校验失败
        # （Input should be a valid dictionary）⇒ 端点 500。由「行里 config_json 是
        # 字符串」与「schema 里 type 是 string」两条一起抓。
        "    config_json: str = Field(",
        "    config_json: dict[str, Any] = Field(",
        TEST_FILE,
    ),
    # ================================================================ 🔑 密钥面
    (
        "L3M14-model-configs-list-drops-masking",
        CONFIGS,
        # 列表路径不脱敏 ⇒ 明文 api_key 直接出参。
        "    return [_mask_response(r) for r in rows]",
        "    return list(rows)",
        TEST_FILE,
    ),
    (
        "L3M15-model-profiles-list-drops-masking",
        PROFILES,
        # ⚠️ 2026-10-21 批次十五：档案列表的脱敏出口从内联 `[_mask_response(r) …]`
        # 改成了具名函数 `_mask_profile_rows`（绑定连接的档案其 has_api_key 须由
        # 连接派生，见该函数 docstring）⇒ 旧锚点在 PROFILES 里命中 0 次。
        # 判别意图不变：**列表路径不脱敏 ⇒ 明文 api_key 出参**
        # （守 `test_list_rows_never_leak_the_plaintext_api_key` 必红）。
        "    out = []\n    for r in rows:\n        cid = r.get(\"connection_id\")\n        out.append(\n            _mask_response(r, connection_row=conns.get(cid)) if cid else _mask_response(r)\n        )\n    return out\n",
        "    out = []\n    for r in rows:\n        cid = r.get(\"connection_id\")\n        out.append(r)\n    return out\n",
        TEST_FILE,
    ),
]
