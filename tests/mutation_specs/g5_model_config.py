"""G5 配置面（模型档案 / 配置 / 环节绑定 / agent 通道）的突变规格 —— 纯数据。

被测物
------
``packages/core/model_router/models.py``（新建）+ 四个 router 的 ``response_model=``
声明 + ``POST /model-profiles/{id}/test`` 的 ``response_model_exclude_unset=True``。

执行器：``tests/mutation_specs/run_spec.py``（共用、**谁都不许改**）::

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py tests/mutation_specs/g5_model_config.py

设计原则（全部来自 AGENTS 坑区，且**每条都真改行为**）
----------------------------------------------------
1. **突变必须真的改变行为**，不能是「变异等于没改」（M15 实测存活）。
2. **变异锚点未命中即强制中止**——本文件每条锚点都按「目标文件内恰好命中 1 次」挑选。
3. **每条突变都要被「非自指断言」抓住**。故覆盖面分两层：
   - ``M-FIELD-*``：字段级（少声明 / 少 Optional / 摘 extra="allow"）；
   - ``M-RM-*``：逐端点撤掉 ``response_model=``（13 个端点各一条，覆盖全表）。

⚠️ 特别值得记的一条是 **M-FIELD-01**：``ModelProfileOut`` 是 ``extra="allow"`` 的，
把 ``created_at`` 的声明删掉后**响应照样有该键**（以 extra 身份透出）⇒ 响应键集断言
恒绿、只有 OpenAPI ``properties`` 断言能抓到（M20 同族）。这正是「断言了结果 ≠ 判别了
机制」在本组的落点：**少声明字段这条突变，在 extra="allow" 模型上只能从 schema 侧抓**。
而 ``ConfigTestResult`` 是严格模型，同样的突变响应侧立刻少一键（M-FIELD-02）。

⚠️ **M-FIELD-03**（去掉 ``response_model_exclude_unset``）是本组唯一的「序列化旗标」
突变：留着它 ``hint`` 不出现；去掉它 ``hint`` 变成 ``null`` 凭空多一个键。前端据此会
多写一段永远拿不到值的兜底——而任何**只看 200 状态码**的测试都发现不了。
"""

from __future__ import annotations

TEST_FILE = "tests/api/test_model_config_response_contract.py"

PROFILES = "packages/core/api/routers/model_profiles.py"
CONFIGS = "packages/core/api/routers/model_configs.py"
BINDINGS = "packages/core/api/routers/capability_bindings.py"
AGENTS = "packages/core/api/routers/agents.py"
MODELS = "packages/core/model_router/models.py"

# (id, 文件, 锚点, 替换, 测试文件)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # ================================================================ 字段级
    (
        "G5-M-FIELD-01",
        MODELS,
        # ModelProfileOut 少声明 created_at。它 extra="allow" ⇒ 响应仍有该键，
        # 只有 OpenAPI properties 断言能抓到。
        '    created_at: str\n    updated_at: str\n',
        "    updated_at: str\n",
        TEST_FILE,
    ),
    (
        "G5-M-FIELD-02",
        MODELS,
        # ConfigTestResult 是严格模型：少声明 latency_ms ⇒ 响应直接少一个键。
        "    config_id: str\n    ok: bool\n    latency_ms: int\n",
        "    config_id: str\n    ok: bool\n",
        TEST_FILE,
    ),
    (
        "G5-M-FIELD-03",
        PROFILES,
        # 去掉 exclude_unset ⇒ 未提供的 hint 被序列化成 null ⇒ 5 键断言红。
        "    response_model_exclude_unset=True,\n",
        "    response_model_exclude_unset=False,\n",
        TEST_FILE,
    ),
    (
        "G5-M-FIELD-04",
        MODELS,
        # 给 ProfileTestResult 的 hint「改名」＝等价于少声明：严格模型会丢弃
        # payload 里的 hint ⇒ 400 + 模型名标记那条用例红。
        #
        # ⚠️ 2026-10-16 订正：初版 replacement 写成 `_hint_declaration_removed`，
        # **Pydantic v2 在类定义时就会抛 `NameError: Fields must not use names with
        # leading underscores`**（本机 2.13.4 实测）⇒ 模块压根 import 不了 ⇒ pytest
        # 收集阶段就崩（退出码 2）⇒ runner 判 **INVALID** 而不是 CAUGHT。
        # 换成无前导下划线的名字（正是 Pydantic 自己建议的写法），变异才落在
        # **行为**上而不是**模块可导入性**上。
        # ⚠️ 这条同时是「变异必须真的跑到被测行为」的又一例：**过猛的变异**（改到
        # 源码读不出来）与**不足的变异**（改不到行为）一样拿不到结论。
        "    hint: Optional[str] = Field(",
        "    hint_declaration_removed: Optional[str] = Field(",
        TEST_FILE,
    ),
    (
        "G5-M-FIELD-05",
        MODELS,
        # 可空改成非空：键集与 required 都不变，只有 anyOf 里的 null 消失 ⇒
        # 只有专钉可空性的那条 OpenAPI 断言能抓到。
        "    updated_at: Optional[str] = Field(",
        "    updated_at: str = Field(",
        TEST_FILE,
    ),
    (
        "G5-M-FIELD-06",
        MODELS,
        # BindingProfileOut（严格）少声明 provider ⇒ profiles[0] 少一键。
        "    model: str\n    provider: str\n",
        "    model: str\n",
        TEST_FILE,
    ),
    (
        "G5-M-FIELD-07",
        MODELS,
        # AgentPromptSyncResult（严格）少声明 updated ⇒ 同步响应少一键。
        # ⚠️ 同 G5-M-FIELD-04：replacement **不可**带前导下划线——Pydantic v2 会在
        # 类定义时抛 NameError（字段名不许以 `_` 开头），模块 import 不了 ⇒
        # pytest 收集崩溃 ⇒ 判 INVALID，拿不到任何结论。
        "    updated: list[tuple[str, int]] = Field(",
        "    updated_declaration_removed: list[tuple[str, int]] = Field(",
        TEST_FILE,
    ),
    (
        "G5-M-FIELD-08",
        MODELS,
        # 摘掉 ModelProfileOut 的 extra="allow" ⇒ 注入的未知列被裁掉。
        # 锚点带上下行是为了在六个 extra="allow" 模型里唯一定位。
        '    model_config = ConfigDict(extra="allow")\n\n    profile_id: str\n',
        "    profile_id: str\n",
        TEST_FILE,
    ),
    (
        "G5-M-FIELD-09",
        MODELS,
        # 摘掉 ModelConfigOut 的 extra="allow"（同 M-FIELD-08，换一张表）。
        # 锚点带上下行是为了在六个 extra="allow" 模型里唯一定位。
        '    model_config = ConfigDict(extra="allow")\n\n    config_id: str\n',
        "    config_id: str\n",
        TEST_FILE,
    ),
    # ================================================================ 逐端点撤 response_model
    (
        "G5-M-RM-01",
        PROFILES,
        '@router.post("/model-profiles", status_code=status.HTTP_201_CREATED, response_model=ModelProfileOut)',
        '@router.post("/model-profiles", status_code=status.HTTP_201_CREATED)',
        TEST_FILE,
    ),
    (
        "G5-M-RM-02",
        PROFILES,
        '@router.get("/model-profiles/{profile_id}", response_model=ModelProfileOut)',
        '@router.get("/model-profiles/{profile_id}")',
        TEST_FILE,
    ),
    (
        "G5-M-RM-03",
        PROFILES,
        '@router.patch("/model-profiles/{profile_id}", response_model=ModelProfileOut)',
        '@router.patch("/model-profiles/{profile_id}")',
        TEST_FILE,
    ),
    (
        "G5-M-RM-04",
        PROFILES,
        "    response_model=ProfileTestResult,\n",
        "",
        TEST_FILE,
    ),
    (
        "G5-M-RM-05",
        PROFILES,
        '@router.post("/model-profiles/available-models", response_model=AvailableModelsOut)',
        '@router.post("/model-profiles/available-models")',
        TEST_FILE,
    ),
    (
        "G5-M-RM-06",
        CONFIGS,
        '@router.post("/model-configs", status_code=status.HTTP_201_CREATED, response_model=ModelConfigOut)',
        '@router.post("/model-configs", status_code=status.HTTP_201_CREATED)',
        TEST_FILE,
    ),
    (
        "G5-M-RM-07",
        CONFIGS,
        '@router.get("/model-configs/{config_id}", response_model=ModelConfigOut)',
        '@router.get("/model-configs/{config_id}")',
        TEST_FILE,
    ),
    (
        "G5-M-RM-08",
        CONFIGS,
        '@router.patch("/model-configs/{config_id}", response_model=ModelConfigOut)',
        '@router.patch("/model-configs/{config_id}")',
        TEST_FILE,
    ),
    (
        "G5-M-RM-09",
        CONFIGS,
        "    response_model=ConfigTestResult,\n",
        "",
        TEST_FILE,
    ),
    (
        "G5-M-RM-10",
        BINDINGS,
        '@router.put("/capability-bindings/{capability}", response_model=CapabilityBindingOut)',
        '@router.put("/capability-bindings/{capability}")',
        TEST_FILE,
    ),
    (
        "G5-M-RM-11",
        BINDINGS,
        '@router.delete("/capability-bindings/{capability}", response_model=CapabilityBindingDeleted)',
        '@router.delete("/capability-bindings/{capability}")',
        TEST_FILE,
    ),
    (
        "G5-M-RM-12",
        AGENTS,
        '@router.post("/agents/sync", status_code=status.HTTP_200_OK, response_model=AgentPromptSyncResult)',
        '@router.post("/agents/sync", status_code=status.HTTP_200_OK)',
        TEST_FILE,
    ),
    (
        "G5-M-RM-13",
        AGENTS,
        "    response_model=AgentRunResult,\n",
        "",
        TEST_FILE,
    ),
]
