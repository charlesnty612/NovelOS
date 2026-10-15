"""G4（workflows 主链：control / runs / revise）的突变规格 —— 2026-10-15 批次（六）。

纯数据：``MUTATIONS`` 的每一项是 ``(id, 文件相对路径, 锚点, 替换, 测试文件)``，
由 ``tests/mutation_specs/run_spec.py`` 执行（那个执行器**所有人共用、谁都不许改**，
自带四条硬断言：绝对路径起子进程 / 启动失败分桶 / 必须有「跑过了」的证据 /
锚点未命中即中止）。

⚠️ **本组突变有一个反直觉的结论，必须记在这里，否则复核者会误判**：

本组 10 个模型**全部**开了 ``extra="allow"``。2026-10-15 实测（``g4_extra_check``）
确认——**删掉一个字段声明后，响应里照样有那个键**（走 extra 通道透传）。所以：

* 「少声明一个字段」这一类突变，**响应键集断言抓不到它**，抓到它的是
  ``test_openapi_schema_properties_match_hardcoded_expectation``
  （读 OpenAPI schema 的 ``properties`` / ``required``）与
  ``test_models_stay_in_sync_with_expectation``（读 ``model_fields``）。
  若哪天有人删掉后两条测试，本组 6 条字段类突变会**集体存活**，而响应仍然是绿的。
* 反过来，「关掉 ``extra="allow"``」在本组的 11 个端点上**看不出任何变化**（实测键集
  与声明逐字相等，没有一个端点返回未声明的键）。抓到它的是
  ``test_extra_allow_keeps_undeclared_keys``——它直接对模型塞一个未知键来量，
  刻意**不**依赖「当前数据恰好带未声明键」这种巧合（否则就是空断言）。

每条突变都必须**真的改变行为**。已逐条量过（见各项意图），其中 G4M10 / G4M11 /
G4M12 三条专门守 ``response_model_exclude_unset`` 这套条件字段机制——它是本组
最容易被人当成冗余参数删掉的一格。

⚠️ **本组规格未由成员实跑**（并发纪律：本组 9 人并行，``run_spec.py`` 会临时改
源码 ⇒ 污染别人的 pytest）。已完成的只读核验是两条：① 每个锚点**恰好命中 1 次**
且替换后文件字节**确实改变**（排除「变异等于没改」）；② 五类突变的判别机制用
**等价复刻模型**逐类量过，结论见上。**CAUGHT / SURVIVED 的实跑结论留给收尾闸门**
（``run_spec.py --all``）——本组不代跑、不预判。
"""

from __future__ import annotations

TEST_FILE = "tests/api/test_workflow_run_response_contract.py"
MODELS = "packages/core/api/routers/workflows/models.py"
CONTROL = "packages/core/api/routers/workflows/control.py"
RUNS = "packages/core/api/routers/workflows/runs.py"
REVISE = "packages/core/api/routers/workflows/revise.py"

MUTATIONS = [
    # ------------------------------------------------------------------
    # A. 少声明一个字段（**响应里看不出来**，靠 OpenAPI properties / model_fields 抓）
    # ------------------------------------------------------------------
    (
        "G4M01", MODELS,
        '    instance_id: Optional[str] = Field(description="0026 新增的实例身份列；未设置时为 null")\n',
        "",
        TEST_FILE,
    ),
    (
        "G4M02", MODELS,
        "    total_tokens: int\n",
        "",
        TEST_FILE,
    ),
    (
        "G4M03", MODELS,
        # ⚠️ 2026-10-15 订正：原锚点 `    retry_count: int` 在 L2 组新增
        # `WorkflowRunSummary` 之后**命中 2 次**（两个模型各声明一次同形字段），
        # run_spec 会判 [ABORT]。这里用上一行独有的 description 做上下文锚定。
        # 判别力不变：删掉本行仍然让「详情少一个键」的用例红。
        '    error: Optional[str] = Field(description="失败原因；成功 run 为 null")\n'
        "    retry_count: int\n",
        "",
        TEST_FILE,
    ),
    # ------------------------------------------------------------------
    # B. 去掉 response_model（OpenAPI 的具名 $ref 消失 ⇒ 端点契约测试红）
    # ------------------------------------------------------------------
    (
        "G4M04", RUNS,
        '@router.get(\n    "/runs/{run_id}",\n    response_model=WorkflowRunDetail,\n',
        '@router.get(\n    "/runs/{run_id}",\n',
        TEST_FILE,
    ),
    (
        "G4M05", CONTROL,
        '    response_model=WorkflowStartAccepted,\n)\ndef start_plan(',
        ')\ndef start_plan(',
        TEST_FILE,
    ),
    (
        "G4M06", RUNS,
        '@router.get("/chapters/{chapter_id}/context-preview", response_model=ContextPreview)\n',
        '@router.get("/chapters/{chapter_id}/context-preview")\n',
        TEST_FILE,
    ),
    (
        "G4M07", CONTROL,
        '@router.post("/runs/{run_id}/cancel", response_model=RunCancelled)\n',
        '@router.post("/runs/{run_id}/cancel")\n',
        TEST_FILE,
    ),
    (
        "G4M08", CONTROL,
        "    response_model=ProjectInitStarted,\n",
        "",
        TEST_FILE,
    ),
    (
        "G4M09", REVISE,
        "    response_model=WorkflowStartAccepted,\n)\ndef start_gate_revise(",
        ")\ndef start_gate_revise(",
        TEST_FILE,
    ),
    # ------------------------------------------------------------------
    # C. 条件字段机制：exclude_unset ↔ exclude_none ↔ 撤掉（13 键 / 15 键守恒）
    # ------------------------------------------------------------------
    (
        # exclude_unset → exclude_none：chapter_id / error / ended_at / instance_id /
        # workflow_name 这些「键在、值为 null」的键被一起抹掉 ⇒ run 详情从 13 键塌成
        # **更少**（掉几个取决于该 run 哪几个字段为 null；合成载荷实测 13→7）。
        # 判别落点：test_run_detail_keys_match_hardcoded_expectation。
        "G4M10", RUNS,
        "    response_model_exclude_unset=True,\n)\ndef get_run_endpoint(",
        "    response_model_exclude_none=True,\n)\ndef get_run_endpoint(",
        TEST_FILE,
    ),
    (
        # 撤掉开关：pause_payload / stage_models 恒以 null 出现 ⇒ 非 PAUSED 的
        # run 详情从 13 键变 15 键（实测）。
        # 判别落点：test_run_detail_keys_match_hardcoded_expectation。
        "G4M11", RUNS,
        "    response_model_exclude_unset=True,\n)\ndef get_run_endpoint(",
        ")\ndef get_run_endpoint(",
        TEST_FILE,
    ),
    (
        # 同款，project-init 侧：非 PAUSED 从 4 键变 5 键（pause_payload 恒 null）。
        "G4M12", CONTROL,
        "    response_model_exclude_unset=True,\n)\ndef start_project_init(",
        ")\ndef start_project_init(",
        TEST_FILE,
    ),
    # ------------------------------------------------------------------
    # D. 关掉 extra="allow"（**端点响应上看不出来**，靠模型级合成键断言抓）
    # ------------------------------------------------------------------
    (
        "G4M13", MODELS,
        '    model_config = ConfigDict(extra="allow")\n\n'
        '    kind: str = Field(description="条目类型：chapter / project / character / hook / director_plan / …")\n',
        '    kind: str = Field(description="条目类型：chapter / project / character / hook / director_plan / …")\n',
        TEST_FILE,
    ),
    (
        "G4M14", MODELS,
        '    model_config = ConfigDict(extra="allow")\n\n'
        '    run_id: str = Field(description="新建 run 的主键；真终态需轮询 GET /api/runs/{run_id}")\n',
        '    run_id: str = Field(description="新建 run 的主键；真终态需轮询 GET /api/runs/{run_id}")\n',
        TEST_FILE,
    ),
    (
        "G4M15", MODELS,
        '    model_config = ConfigDict(extra="allow")\n\n    run_id: str\n'
        '    status: str = Field(description="成功恒为 CANCELLED；409 冲突体走 FastAPI 的 {detail}")\n',
        '    run_id: str\n'
        '    status: str = Field(description="成功恒为 CANCELLED；409 冲突体走 FastAPI 的 {detail}")\n',
        TEST_FILE,
    ),
    # ------------------------------------------------------------------
    # E. 必填性被悄悄改成可选（响应**完全不变**，只有 OpenAPI required 变）
    # ------------------------------------------------------------------
    (
        # 加 default=None ⇒ 该键从 required 里消失 ⇒ 前端类型生成把它当可选。
        # 响应逐字不变 ⇒ 只有 test_openapi_schema_properties… 的 required 断言能抓。
        "G4M16", MODELS,
        '        description="恒为 null：异步启动返回时还没有节点在跑；给前端留位置",\n',
        '        default=None,\n        description="恒为 null：异步启动返回时还没有节点在跑；给前端留位置",\n',
        TEST_FILE,
    ),
]

__all__ = ["MUTATIONS"]
