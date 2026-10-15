"""G1 世界三实体 response_model 契约的突变规格（2026-10-15 批次（六））。

纯数据，不含任何逻辑——执行器是 ``tests/mutation_specs/run_spec.py``（共用，禁改）。

每条突变都必须**真的改变行为**：批次（五）踩过「replacement 是 anchor 的超集、
只插了行注释、逻辑未变」的坑，量具会把这种误报成「存活」，而真因是变异没写对。

覆盖面（任务书要求的四类 + 补的枚举/联合收窄）：

===========================  ==========================================
类别                         条目
===========================  ==========================================
少声明一个字段               G1-1 / G1-2（``extra="allow"`` 下响应键集抓不到，
                             靠 schema.properties / required 两条断言兜）
去掉 response_model          G1-3 / G1-4 / G1-5 / G1-6（覆盖 POST / PATCH /
                             列表 / GET-by-id 四种装饰器形态）
去掉 extra="allow"           G1-7
exclude_none 翻转            G1-8（挂上去会吃掉 ``who_knows``）
============================ ==========================================

判别锚点全是「模型 vs 硬编码」或「OpenAPI schema」侧的断言，
**不是**「响应 vs 模型」的自指比对——后者删字段两侧同时变小、恒真。
"""

MUTATIONS = [
    # ------------------------------------------------------------------ 少声明字段
    # ⚠️ 模型开了 extra="allow"，删掉声明后**响应里那个键照样在**，
    #    只有 OpenAPI 的 properties/required 两条断言能抓到。撤掉任一
    #    ⇒ test_openapi_schema_properties_and_required_match_expectation 必红。
    (
        "G1-1",
        "packages/domain/world/models.py",
        "    aliases: list[str] = Field(\n"
        '        description="别名列表；恒为数组（world_rules 恒为 []，见模块注释）",\n'
        "    )\n",
        "",
        "tests/api/test_world_response_contract.py",
    ),
    (
        "G1-2",
        "packages/domain/world/models.py",
        "    who_knows: list[str] | None = Field(description=\"可见者 ID 列表；null = 全员可见\")\n",
        "",
        "tests/api/test_world_response_contract.py",
    ),
    # ---------------------------------------------------------------- 去掉 response_model
    # 改前这 12 个端点的 schema 全是匿名 {"type":"object","additionalProperties":true}。
    # G1-3 命中单实体 POST 的装饰器（带 status_code 行）。
    (
        "G1-3",
        "packages/core/api/routers/worlds.py",
        '    "/projects/{pid}/locations",\n'
        "    status_code=status.HTTP_201_CREATED,\n"
        "    response_model=WorldEntityOut,\n",
        '    "/projects/{pid}/locations",\n'
        "    status_code=status.HTTP_201_CREATED,\n",
        "tests/api/test_world_response_contract.py",
    ),
    # G1-4 命中 PATCH 形态（锚点带上 @router.patch 才能与 GET 形态区分开）。
    (
        "G1-4",
        "packages/core/api/routers/worlds.py",
        "@router.patch(\n"
        '    "/factions/{faction_id}",\n'
        "    response_model=WorldEntityOut,\n",
        "@router.patch(\n"
        '    "/factions/{faction_id}",\n',
        "tests/api/test_world_response_contract.py",
    ),
    # G1-5 命中列表形态（list[...] 而非单模型）。
    (
        "G1-5",
        "packages/core/api/routers/worlds.py",
        "@router.get(\n"
        '    "/projects/{pid}/world-rules",\n'
        "    response_model=list[WorldEntityOut],\n",
        "@router.get(\n"
        '    "/projects/{pid}/world-rules",\n',
        "tests/api/test_world_response_contract.py",
    ),
    # G1-6 命中 GET-by-id 形态。
    (
        "G1-6",
        "packages/core/api/routers/worlds.py",
        "@router.get(\n"
        '    "/world-rules/{world_rule_id}",\n'
        "    response_model=WorldEntityOut,\n",
        "@router.get(\n"
        '    "/world-rules/{world_rule_id}",\n',
        "tests/api/test_world_response_contract.py",
    ),
    # ---------------------------------------------------------------- 去掉 extra="allow"
    # 撤掉后未声明键被静默丢弃；响应本来只有那 11 个键，所以**只断言响应键集抓不到**
    # （这正是任务书点名的形状），靠 test_extra_allow_lets_unknown_keys_through 判别。
    (
        "G1-7",
        "packages/domain/world/models.py",
        '    model_config = ConfigDict(extra="allow")',
        "    model_config = ConfigDict()",
        "tests/api/test_world_response_contract.py",
    ),
    # ---------------------------------------------------------------- exclude_none 翻转
    # 给 POST /factions 挂上 response_model_exclude_none=True：无数据的
    # who_knows（None）会被整个吃掉 ⇒ 响应少一个键 ⇒ test_who_knows_is_null_not_omitted 必红。
    # ⚠️ 本仓 simulation 端点**确实**需要这个开关（那里有「无数据则省略」的段），
    #    别拿 simulation 的结论套到 world 上——实测 world 的 11 个键恒在，不需要。
    (
        "G1-8",
        "packages/core/api/routers/worlds.py",
        '    "/projects/{pid}/factions",\n'
        "    status_code=status.HTTP_201_CREATED,\n"
        "    response_model=WorldEntityOut,\n",
        '    "/projects/{pid}/factions",\n'
        "    status_code=status.HTTP_201_CREATED,\n"
        "    response_model=WorldEntityOut,\n"
        "    response_model_exclude_none=True,\n",
        "tests/api/test_world_response_contract.py",
    ),
    # ---------------------------------------------------------------- 必填 -> 可省略
    # 给 who_knows 加默认值 = 把契约说成「可省略」，而后端从不省略 ⇒ 前端被迫写
    # `?? []` 兜底，那正是 G10「欠定义类型」的病。required 会少一个键 ⇒ 必红。
    (
        "G1-9",
        "packages/domain/world/models.py",
        '    who_knows: list[str] | None = Field(description="可见者 ID 列表；null = 全员可见")',
        '    who_knows: list[str] | None = Field(\n'
        '        default=None, description="可见者 ID 列表；null = 全员可见"\n'
        "    )",
        "tests/api/test_world_response_contract.py",
    ),
    # aliases 同款：恒非 null 的字段被改成可省略。
    (
        "G1-10",
        "packages/domain/world/models.py",
        "    aliases: list[str] = Field(\n"
        '        description="别名列表；恒为数组（world_rules 恒为 []，见模块注释）",\n'
        "    )",
        "    aliases: list[str] | None = Field(\n"
        '        default=None, description="别名列表"\n'
        "    )",
        "tests/api/test_world_response_contract.py",
    ),
    # ---------------------------------------------------------------- 联合收窄成单形态
    # data 实测两种形态都出现（{} 与 [1,"x"]）。收窄成只容对象 ⇒ 写数组那一步直接 500
    # （ResponseValidationError），实体在库里其实是建成的 ⇒ 「写得进、读不出」的洞。
    (
        "G1-11",
        "packages/domain/world/models.py",
        "    data: dict[str, Any] | list[Any] = Field(\n"
        '        description="data_json 列的反序列化结果；未填时为 {}，也可能是数组",\n'
        "    )",
        '    data: dict[str, Any] = Field(description="data_json 列")',
        "tests/api/test_world_response_contract.py",
    ),
    # ---------------------------------------------------------------- 枚举收窄被拆掉
    # Literal -> str 后 OpenAPI 的 enum 消失 ⇒ 前端拿不到穷举值。
    # （注：这不会让请求失败，只会让契约变松——所以判别点是 schema.enum 而不是响应。）
    (
        "G1-12",
        "packages/domain/world/models.py",
        '    visibility: WorldVisibilityLiteral = Field(description="权限级别")',
        '    visibility: str = Field(description="权限级别")',
        "tests/api/test_world_response_contract.py",
    ),
]
