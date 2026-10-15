"""G2（ledger 钩子/债 + plot 事件/时间线）的突变规格（2026-10-15 批次（六））。

纯数据文件——执行器是 ``tests/mutation_specs/run_spec.py``（所有人共用，不许改）。
用法::

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py tests/mutation_specs/g2_ledger_plot.py

⚠️ **会临时改源码** ⇒ 禁与任何 pytest 并发跑。

本组的量具纪律（每一条都对应 AGENTS 坑区里已登记的一次实测存活）
--------------------------------------------------------------
1. **每条突变都真的改变行为**。只插注释 / 只改排版 = 等于没改，脚本按设计判 ``[ABORT]``
   （SHA256 未变）。G2-8 特意用「**加上** ``response_model_exclude_none``」而不是
   「翻掉」——本组端点**本来就没有**那个开关，撤一个不存在的开关是空变异；加它才真会
   把「键恒在、值为 null」的契约改成「键可能缺席」。
2. **锚点恰好命中 1 次**。多处同形的字段行（``visibility: str`` /
   ``who_knows: list[str] | None`` / ``created_at: str`` 在 ``Hook`` 与 ``Debt`` 里
   **逐字相同**）必须靠带上一行区分（``importance: float`` 只在 ``Hook`` 出现）。
3. **响应侧的断言不碰 ``model_fields``**。四个模型都 ``extra="allow"``，所以
   「少声明一个字段」**响应照样有那个键**（作为 extra 透出）⇒ 只有
   ``test_openapi_schema_properties_and_required_match_expectation`` 能抓到。
   G2-1 / G2-2 / G2-7 就是为钉这条而存在的。
"""

from __future__ import annotations

_TEST = "tests/api/test_ledger_plot_response_contract.py"
_LEDGER_MODELS = "packages/domain/ledger/models.py"
_PLOT_MODELS = "packages/domain/plot/models.py"
_LEDGER_ROUTER = "packages/core/api/routers/ledger.py"
_PLOT_ROUTER = "packages/core/api/routers/plots.py"

MUTATIONS = [
    # ---------------------------------------------------------------- models
    (
        "G2-1",
        _LEDGER_MODELS,
        # `importance: float`（非 Optional、无 Field）只在 Hook 里出现 ⇒ 锚点唯一。
        "    status: HookStatus\n"
        "    importance: float\n"
        "    expected_payoff_chapter_id: str | None\n"
        "    payoff_chapter_id: str | None\n",
        "    status: HookStatus\n"
        "    importance: float\n"
        "    expected_payoff_chapter_id: str | None\n",
        _TEST,
    ),
    (
        "G2-2",
        _LEDGER_MODELS,
        "    debt_id: str\n    project_id: str\n    description: str\n    created_chapter_id: str | None\n",
        "    debt_id: str\n    project_id: str\n    created_chapter_id: str | None\n",
        _TEST,
    ),
    (
        "G2-3",
        _LEDGER_MODELS,
        # Hook 的 extra="allow" 消失 ⇒ 新列被 response_model 静默裁掉。
        '    model_config = ConfigDict(extra="allow")\n\n    hook_id: str\n',
        "    hook_id: str\n",
        _TEST,
    ),
    (
        "G2-4",
        _LEDGER_MODELS,
        '    model_config = ConfigDict(extra="allow")\n\n    debt_id: str\n',
        "    debt_id: str\n",
        _TEST,
    ),
    (
        "G2-5",
        _PLOT_MODELS,
        # ⚠️ 锚点含中文注释行，命中 1 次（TimelineEventOut 的 model_config 后面接的是 id: str）。
        # ⚠️ 注释行必须**写全**：只写半行（截到 ``event_id`` 就换行）在文件里不存在。
        '    model_config = ConfigDict(extra="allow")\n\n    # ⚠️ 键名是 ``id`` 不是 ``event_id``（DB 主键列名是 event_id，to_dict 改过名）。\n',
        "    # ⚠️ 键名是 ``id`` 不是 ``event_id``（DB 主键列名是 event_id，to_dict 改过名）。\n",
        _TEST,
    ),
    (
        "G2-6",
        _PLOT_MODELS,
        '    model_config = ConfigDict(extra="allow")\n\n    id: str\n    project_id: str\n    event_id: str\n',
        "    id: str\n    project_id: str\n    event_id: str\n",
        _TEST,
    ),
    (
        "G2-7",
        _PLOT_MODELS,
        # 迁移 0013 的 description 列（曾因模型里没有而对 API 隐形，修复 B1）被撤掉。
        '    who_knows: Optional[list[str]] = Field(description="知情者 character_id 列表；NULL=沿用默认，[]=显式置空")\n'
        '    description: Optional[str] = Field(description="迁移 0013 的列；本字段曾因「模型里没有」而对 API 隐形")\n',
        '    who_knows: Optional[list[str]] = Field(description="知情者 character_id 列表；NULL=沿用默认，[]=显式置空")\n',
        _TEST,
    ),
    # ------------------------------------------------------------- routers
    (
        "G2-8",
        _PLOT_ROUTER,
        # **加上** response_model_exclude_none（不是撤掉一个不存在的开关）。
        # 实测这四个实体的可空列是「键恒在、值为 null」；加了它 FastAPI 会把每个 None
        # 抹掉，响应凭空少 4~6 个键。
        '@router.post(\n    "/projects/{pid}/events",\n    status_code=status.HTTP_201_CREATED,\n    response_model=PlotEventOut,\n)\n',
        '@router.post(\n    "/projects/{pid}/events",\n    status_code=status.HTTP_201_CREATED,\n    response_model=PlotEventOut,\n    response_model_exclude_none=True,\n)\n',
        _TEST,
    ),
    (
        "G2-9",
        _LEDGER_ROUTER,
        '@router.post(\n    "/projects/{pid}/hooks",\n    status_code=status.HTTP_201_CREATED,\n    response_model=Hook,\n)\n',
        '@router.post(\n    "/projects/{pid}/hooks",\n    status_code=status.HTTP_201_CREATED,\n)\n',
        _TEST,
    ),
    (
        "G2-10",
        _LEDGER_ROUTER,
        '@router.get("/hooks/{hook_id}", response_model=Hook)\ndef get_hook(hook_id: str, request: Request) -> dict[str, Any]:\n',
        '@router.get("/hooks/{hook_id}")\ndef get_hook(hook_id: str, request: Request) -> dict[str, Any]:\n',
        _TEST,
    ),
    (
        "G2-11",
        _LEDGER_ROUTER,
        '@router.patch("/hooks/{hook_id}", response_model=Hook)\ndef update_hook(\n',
        '@router.patch("/hooks/{hook_id}")\ndef update_hook(\n',
        _TEST,
    ),
    (
        "G2-12",
        _LEDGER_ROUTER,
        '@router.post(\n    "/projects/{pid}/debts",\n    status_code=status.HTTP_201_CREATED,\n    response_model=Debt,\n)\n',
        '@router.post(\n    "/projects/{pid}/debts",\n    status_code=status.HTTP_201_CREATED,\n)\n',
        _TEST,
    ),
    (
        "G2-13",
        _LEDGER_ROUTER,
        '@router.get("/debts/{debt_id}", response_model=Debt)\ndef get_debt(debt_id: str, request: Request) -> dict[str, Any]:\n',
        '@router.get("/debts/{debt_id}")\ndef get_debt(debt_id: str, request: Request) -> dict[str, Any]:\n',
        _TEST,
    ),
    (
        "G2-14",
        _LEDGER_ROUTER,
        '@router.patch("/debts/{debt_id}", response_model=Debt)\ndef update_debt(\n',
        '@router.patch("/debts/{debt_id}")\ndef update_debt(\n',
        _TEST,
    ),
    (
        "G2-15",
        _PLOT_ROUTER,
        '@router.post(\n    "/projects/{pid}/events",\n    status_code=status.HTTP_201_CREATED,\n    response_model=PlotEventOut,\n)\n',
        '@router.post(\n    "/projects/{pid}/events",\n    status_code=status.HTTP_201_CREATED,\n)\n',
        _TEST,
    ),
    (
        "G2-16",
        _PLOT_ROUTER,
        '@router.get("/events/{event_id}", response_model=PlotEventOut)\ndef get_event(event_id: str, request: Request) -> dict[str, Any]:\n',
        '@router.get("/events/{event_id}")\ndef get_event(event_id: str, request: Request) -> dict[str, Any]:\n',
        _TEST,
    ),
    (
        "G2-17",
        _PLOT_ROUTER,
        '@router.patch("/events/{event_id}", response_model=PlotEventOut)\ndef update_event(\n',
        '@router.patch("/events/{event_id}")\ndef update_event(\n',
        _TEST,
    ),
    (
        "G2-18",
        _PLOT_ROUTER,
        '@router.post(\n    "/projects/{pid}/timeline",\n    status_code=status.HTTP_201_CREATED,\n    response_model=TimelineEventOut,\n)\n',
        '@router.post(\n    "/projects/{pid}/timeline",\n    status_code=status.HTTP_201_CREATED,\n)\n',
        _TEST,
    ),
]
