"""L2 状态/run **列表**端点：``response_model`` 契约的突变规格（2026-10-15 批次（六））。

执行器：``tests/mutation_specs/run_spec.py``（共用，勿改）::

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py tests/mutation_specs/l2_state_list.py

本组的判别口径
--------------
``list[dict]`` 在 OpenAPI 里 items 是**匿名 object** ⇒ 前端拿到的仍是
``Record<string, unknown>``。所以本组的第一道网是「items 是否有具名 ``$ref``」，
而不是「响应里有没有那个键」——后者在 ``extra="allow"`` 下一律恒真。

四条实测得来的本组特有风险（突变就是照它们设计的）
---------------------------------------------------
1. **「键集相同 ⇒ 可复用」**（L2-9）：``branches`` 列表行与 ``BranchCreatedResponse``
   **逐字同形 7 键**，但 main 的 ``parent_branch_id`` 是 null，后者声明的是必填非空
   ``str`` ⇒ 复用会让端点恒 500。任务书把「逐字比对后再决定复用」交给执行者，
   实测结论是**不可复用**。
2. **「列表行 = 单体响应」**（L2-5 / L2-10）：``commits`` 列表行是 12 键裸 DB 行，
   ``POST /commits`` 只有 4 键；把两者统一成一个模型是这条契约最容易犯的错。
3. **「键恒在、值为 null」**（L2-11 / L2-12 / L2-14）：本组**没有条件键**，
   ``rollback_of`` / ``parent_branch_id`` / ``label`` / ``chapter_id`` / ``ended_at`` /
   ``error`` 全是「键在、值可为 null」。两种错法各钉一条：
   声明成非空 ``str`` ⇒ 整页 500（L2-11 / L2-14）；给端点加
   ``response_model_exclude_none`` ⇒ 键被整个抹掉（L2-12）。
4. **「7 数组是 dict」**（L2-13）：任务书写「用 ``dict[str, Any]``」，实测它们是
   **list**（``restore_delta_from_row`` 的 ``payload.get(k, [])``）。照任务书写会 500。

覆盖面（对照任务书要求的三类 + 本组实测风险）
--------------------------------------------
* **撤掉某端点 ``response_model``** → L2-1 / L2-2 / L2-3 / L2-4（四条，逐个端点）。
* **少声明一个字段** → L2-5（``rollback_of``）/ L2-6（``parent_branch_id``）/ L2-7（``label``）。
* **去掉 ``extra="allow"``** → L2-8。
* **把「键恒在、值为 null」声明成非空** → L2-10 / L2-11 / L2-14（三处）。
* **复用既有模型（另一种候选修法）** → L2-9（``parent_branch_id`` 收窄成 ``str``，
  等价于「拿 ``BranchCreatedResponse`` 那套类型来用」）。
* **加端点级 ``response_model_exclude_none``** → L2-12。
* **7 数组声明成 dict** → L2-13。
* **chapter 归属校验（2026-10-16 批次）** → L2-15 ~ L2-19。与上面不同类：这组是
  **路由层作用域**而非契约形状，但落在同一批端点上，故留在本文件。撤掉修复
  （L2-15/16/17）之外，另钉两种「看起来也修了」的方向——**半修**（L2-18）与
  **过修**（L2-19）；只写「撤掉修复」的话，一个「一律 404」的实现能全绿。

三条自查（写完必做，勿跳过）
--------------------------
1. **锚点恰好命中 1 次**——用 ``grep -c`` 逐条数；命中 ≠ 1 时 ``run_spec.py`` 会
   ``[ABORT]`` 强制中止（不是记「跳过」）。⚠️ ``story_state.py`` 是 **CRLF**，
   而 ``run_spec.py`` 走 ``read_text``（通用换行 ⇒ 内存里是 LF）⇒ **锚点里的换行
   必须写成 ``\\n`` 两个字符**，写成真换行会命中 0 次而 ``[ABORT]``。
2. **每条都真的改行为**——``run_spec.py`` 比对写入前后的 SHA256，没变即 ``[ABORT]``。
3. **突变存活 ≠ 修复无效**：先怀疑量具（锚点写错 / 用例不在断言目标上），再怀疑被测物。
   ⚠️ 本组最容易「存活却以为抓到了」的两处：L2-5/6/7（``extra="allow"`` 让响应侧抓不到，
   靠 OpenAPI ``properties`` 与「模型 vs 硬编码」两道网）、L2-1~4（响应体本身**完全不变**，
   只有 OpenAPI 变——所以判别点必须在 OpenAPI 断言上，不能在响应断言上）。
4. **交接前清点突变残留**——``grep -rn "L2-MUTATION\\|MUTATION-PROBE-"``（须区分大小写；
   ``MUTATION`` 本身是**正常测试函数名**的一部分，用裸词搜会误报）。
"""

MUTATIONS = [
    # ---------------------------------------------- 撤掉 response_model（四个端点逐个）
    (
        "L2-1",
        "packages/core/api/routers/story_state.py",
        '@router.get("/projects/{project_id}/commits", response_model=list[CommitListItem])',
        '@router.get("/projects/{project_id}/commits")',
        "tests/api/test_state_list_response_contract.py",
    ),
    (
        "L2-2",
        "packages/core/api/routers/story_state.py",
        "    response_model=list[StateDeltaListItem],\n",
        "",
        "tests/api/test_state_list_response_contract.py",
    ),
    (
        "L2-3",
        "packages/core/api/routers/story_state.py",
        '@router.get("/projects/{project_id}/branches", response_model=list[BranchSummary])',
        '@router.get("/projects/{project_id}/branches")',
        "tests/api/test_state_list_response_contract.py",
    ),
    (
        "L2-4",
        "packages/core/api/routers/workflows/runs.py",
        '@router.get("/projects/{project_id}/runs", response_model=list[WorkflowRunSummary])',
        '@router.get("/projects/{project_id}/runs")',
        "tests/api/test_state_list_response_contract.py",
    ),
    # ---------------------------------------------------------- 少声明一个字段（extra 通道抓不到）
    (
        "L2-5",
        "packages/core/story_state/models.py",
        '    rollback_of: Optional[str] = Field(description="只有回滚产生的 commit 非 null；其余恒为 null")',
        "    # L2-MUTATION: 故意删掉 rollback_of 的字段声明（extra='allow' 下响应照样有该键）",
        "tests/api/test_state_list_response_contract.py",
    ),
    (
        "L2-6",
        "packages/core/story_state/models.py",
        '    parent_branch_id: Optional[str] = Field(description="⚠️ main 分支恒为 null（根节点）；子分支指向父分支")',
        '    parent_branch_id: Optional[str] = Field(\n'
        '        default=None, description="L2-MUTATION: 从必填可空改成可省略"\n'
        "    )",
        "tests/api/test_state_list_response_contract.py",
    ),
    (
        "L2-7",
        "packages/core/api/routers/workflows/models.py",
        '    label: Optional[str] = Field(description="人类可读标签；workflow 名不在中文映射表里时为 null")',
        "    # L2-MUTATION: 故意删掉 label 的字段声明",
        "tests/api/test_state_list_response_contract.py",
    ),
    # ------------------------------------------------------------------ 去掉 extra="allow"
    (
        "L2-8",
        "packages/core/story_state/models.py",
        '    model_config = ConfigDict(extra="allow")\n'
        "\n"
        "    commit_id: str\n"
        "    project_id: str\n",
        "    commit_id: str\n"
        "    project_id: str\n",
        "tests/api/test_state_list_response_contract.py",
    ),
    # -------------------------- 复用既有模型 / 键集相同 ⇒ 类型收窄（等价于拿 BranchCreatedResponse 用）
    (
        "L2-9",
        "packages/core/story_state/models.py",
        '    parent_branch_id: Optional[str] = Field(description="⚠️ main 分支恒为 null（根节点）；子分支指向父分支")',
        '    parent_branch_id: str = Field(description="L2-MUTATION: 收窄成非空 —— main 行会让整页 500")',
        "tests/api/test_state_list_response_contract.py",
    ),
    (
        "L2-10",
        "packages/core/story_state/models.py",
        '    rollback_of: Optional[str] = Field(description="只有回滚产生的 commit 非 null；其余恒为 null")',
        '    rollback_of: str = Field(description="L2-MUTATION: 收窄成非空 —— 普通 commit 行会让整页 500")',
        "tests/api/test_state_list_response_contract.py",
    ),
    (
        "L2-11",
        "packages/core/api/routers/workflows/models.py",
        '    label: Optional[str] = Field(description="人类可读标签；workflow 名不在中文映射表里时为 null")',
        '    label: str = Field(description="L2-MUTATION: 收窄成非空 —— 映射表外的 workflow 会让整页 500")',
        "tests/api/test_state_list_response_contract.py",
    ),
    # ------------------------------------- 加端点级 exclude_none（另一种候选修法：少一个「键恒在」）
    (
        "L2-12",
        "packages/core/api/routers/story_state.py",
        '@router.get("/projects/{project_id}/branches", response_model=list[BranchSummary])',
        "@router.get(\n"
        '    "/projects/{project_id}/branches",\n'
        "    response_model=list[BranchSummary],\n"
        "    response_model_exclude_none=True,\n"
        ")",
        "tests/api/test_state_list_response_contract.py",
    ),
    # ------------------------------------------------ 7 数组声明成 dict（照任务书原话写的错法）
    (
        "L2-13",
        "packages/core/story_state/models.py",
        "    character_changes: list[dict[str, Any]]\n",
        "    character_changes: dict[str, Any]  # L2-MUTATION: 实测是 list，声明成 dict 会 500\n",
        "tests/api/test_state_list_response_contract.py",
    ),
    (
        "L2-14",
        "packages/core/api/routers/workflows/models.py",
        "    chapter_id: Optional[str] = Field(\n"
        '        description="列表行专属：project-init 等非章节任务的 run 为 null（实测）"\n'
        "    )",
        "    chapter_id: str = Field(  # L2-MUTATION: 收窄成非空 —— project-init 行会让整页 500\n"
        '        description="列表行专属"\n'
        "    )",
        "tests/api/test_state_list_response_contract.py",
    ),
    # ====================== chapter 归属校验（2026-10-16 批次：跨项目读 / 写洞）
    #
    # 缺陷形状：路径里的 project_id 与 chapter_id 是两个独立变量，而
    # queries.list_deltas 是裸 SELECT ... WHERE chapter_id = ? —— 服务端完全不看
    # project，用别的 project_id 请求同一章会 200 且原样返回同样的行。
    # 同一形状另有两处（chapter_id 在 body 而不是路径）：state/init 与
    # branches/{bid}/promote，修前跨项目同样 201 并写入错配行。
    #
    # 本段五条各钉一种失败方向，缺一条就会漏：
    #   L2-15/16/17 = 撤掉修复（回归原缺陷，三个端点各一条）；
    #   L2-18       = 半修（只换状态码，detail 仍带归属线索 => 泄露面漏回来）；
    #   L2-19       = 过修（去掉 promote 的 chapter_id is None 豁免 => 缺省调用被打成 404）。
    # 后两条是本段的判别核心：只钉「撤掉修复」的话，一个「一律 404」的实现照样能全绿。
    (
'L2-15',
'packages/core/api/routers/story_state.py',
'    _ensure_project(request, project_id)\n    _ensure_chapter_in_project(request, project_id, chapter_id)\n    return _service(request).list_deltas(chapter_id)',
'    _ensure_project(request, project_id)\n    return _service(request).list_deltas(chapter_id)',
'tests/api/test_state_list_response_contract.py',
    ),
    (
'L2-16',
'packages/core/api/routers/story_state.py',
'    _ensure_chapter_in_project(request, project_id, chapter_id)\n    return _service(request).init_genesis(project_id, chapter_id)',
'    return _service(request).init_genesis(project_id, chapter_id)',
'tests/api/test_state_list_response_contract.py',
    ),
    (
'L2-17',
'packages/core/api/routers/story_state.py',
'    if chapter_id is not None:\n        _ensure_chapter_in_project(request, project_id, chapter_id)\n    try:\n',
'    try:\n',
'tests/api/test_state_list_response_contract.py',
    ),
    # ⚠️ L2-18 已于 2026-10-16 批次十**删除**（不是失效后放着不管）。
    #
    # 原条目守的是 story_state.py 里那层「捕获 400 → 改写成 404」的 workaround
    # （锚点 = `raise HTTPException(..., detail=f"chapter ... not found") from exc`）。
    # 批次十把「两种归属失败返回同一形状 404」这条政策**收进了权威实现**
    # `workflows/common.py::check_chapter_in_project`，那层 workaround 随之删除
    # ⇒ **锚点归零**。这正是本文件 docstring 登记的那个形状的**反向版本**：
    # 锚点不只会因**别人新增**同名声明而重复命中，也会因**机制被删**而命中 0 次。
    #
    # 删它不是丢覆盖：它守的性质（「404 的 detail 不得泄露归属」）现在由
    # `tests/mutation_specs/e6_chapter_ownership_status.py` 的 OWN-1（权威 helper
    # 退回 400+泄露）与 OWN-2（continuation 第二处泄露）覆盖，且那两条**在权威位置**，
    # 比原来守一处 workaround 更贴近机制；另有一条全仓源码形状守卫兜住新增端点。

    (
'L2-19',
'packages/core/api/routers/story_state.py',
'    if chapter_id is not None:\n        _ensure_chapter_in_project(request, project_id, chapter_id)\n',
'    _ensure_chapter_in_project(request, project_id, chapter_id or "ch_zzz")\n',
'tests/api/test_state_list_response_contract.py',
    ),
]
