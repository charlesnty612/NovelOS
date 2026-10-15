"""G3 快照写面：``response_model`` 契约的突变规格（2026-10-15 批次（六））。

执行器：``tests/mutation_specs/run_spec.py``（共用，勿改）::

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py tests/mutation_specs/g3_story_state.py

⚠️ **本组的突变方向与任务书原文相反，先验在此留痕**
--------------------------------------------------
任务书写「``state/diff`` **必须**带 ``response_model_exclude_none=True``」。实测那条是错的：
``diff_snapshots`` 产出的 ``branch_id`` **恒为 null**（``queries.diff_versions`` 把它写死成
``None``，传非 None 一律 409），而端点级 ``_exclude_none`` 是一刀切的——加了之后
**空 diff 的键集从 3 个掉到 2 个**，``branch_id`` 被抹掉。前端
``StateDiffResponse.branch_id?: string | null`` 正是按「键在、值为 null」声明的。
实测端到端确认（2026-10-15）。⇒ 本组用**字段级** ``exclude_if`` 实现「空则省略」，
并把「加端点级 ``_exclude_none``」本身登记成突变（G3-7 / G3-8）——**另一种候选修法
必须被测试抓住**，否则后来者会把这条先验重新踩一遍。

所以「去掉 ``response_model_exclude_none=True``」在本组**没有对应突变**
（本组从未加过它）；等价的功能位是六个实体段上的 ``exclude_if``，
去掉它的突变是 G3-3 / G3-4 / G3-12。

三条自查
--------
1. **每条都真的改行为**：``run_spec.py`` 会比对写入前后的 SHA256，写入后没变就 ``[ABORT]``。
2. **锚点恰好命中 1 次**：``run_spec.py`` 同样硬断言（命中 ≠ 1 强制中止，不记「跳过」）。
3. **突变存活 ≠ 修复无效**：先怀疑量具（锚点写错 / 用例不在断言目标上），再怀疑被测物。

覆盖面（对照任务书要求的四类）
------------------------------
* **少声明一个字段** → G3-1（``RollbackResponse.rollback_of``）。注意 ``extra="allow"``
  之下响应侧抓不到它，靠「模型 vs 硬编码」+「OpenAPI schema properties vs 硬编码」两道网。
* **去掉 ``response_model``** → G3-2（commits）/ G3-11（state/init）。
* **去掉「空则省略」的开关** → G3-3 / G3-4 / G3-12（三处 ``exclude_if``）。
* **去掉 ``extra="allow"``** → G3-5。
* 外加本组三条**实测得来的**特有风险：G3-6（``snapshot_ref`` 声明成 ``str`` 会让分支
  commit 500）、G3-9（去掉 ``exclude_unset`` ⇒ 缺席键被补成 null，**加 response_model 本身
  引入契约变更**）、G3-10（``changed`` 元素退化成裸 dict）。
"""

MUTATIONS = [
    # ---------------------------------------------------------------- 少声明一个字段
    (
        "G3-1",
        "packages/core/story_state/models.py",
        '    rollback_of: str = Field(description="被回滚的 commit_id（= 本次 commit 的来源）")',
        "    # G3-MUTATION: 故意删掉 rollback_of 的字段声明",
        "tests/api/test_story_state_write_response_contract.py",
    ),
    # ---------------------------------------------------------------- 去掉 response_model
    (
        "G3-2",
        "packages/core/api/routers/story_state.py",
        "    response_model=CommitResponse,\n",
        "",
        "tests/api/test_story_state_write_response_contract.py",
    ),
    (
        "G3-11",
        "packages/core/api/routers/story_state.py",
        '    "/projects/{project_id}/state/init",\n'
        "    status_code=status.HTTP_201_CREATED,\n"
        "    response_model=SnapshotResponse,\n",
        '    "/projects/{project_id}/state/init",\n'
        "    status_code=status.HTTP_201_CREATED,\n",
        "tests/api/test_story_state_write_response_contract.py",
    ),
    # ---------------------------------------------------------------- 去掉「空则省略」
    (
        "G3-3",
        "packages/core/story_state/models.py",
        "    characters: Optional[ListDiffResponse] = Field(default=None, exclude_if=_drop_if_none)",
        "    characters: Optional[ListDiffResponse] = None",
        "tests/api/test_story_state_write_response_contract.py",
    ),
    (
        "G3-4",
        "packages/core/story_state/models.py",
        "    changed: Optional[list[ChangedDiffItemResponse]] = Field(\n"
        "        default=None, exclude_if=_drop_if_none\n"
        "    )",
        "    changed: Optional[list[ChangedDiffItemResponse]] = None",
        "tests/api/test_story_state_write_response_contract.py",
    ),
    (
        "G3-12",
        "packages/core/story_state/models.py",
        "    events_changed: Optional[dict[str, Any]] = Field(default=None, exclude_if=_drop_if_none)",
        "    events_changed: Optional[dict[str, Any]] = None",
        "tests/api/test_story_state_write_response_contract.py",
    ),
    # ---------------------------------------------------------------- 去掉 extra="allow"
    (
        "G3-5",
        "packages/core/story_state/models.py",
        '    model_config = ConfigDict(extra="allow")\n'
        "\n"
        '    commit_id: Optional[str] = Field(default=None, description="空分支时为 null（键恒在）")',
        '    commit_id: Optional[str] = Field(default=None, description="空分支时为 null（键恒在）")',
        "tests/api/test_story_state_write_response_contract.py",
    ),
    # ------------------------------------------- 可空键被声明成非空（分支 commit 会 500）
    (
        "G3-6",
        "packages/core/story_state/models.py",
        "    snapshot_ref: Optional[str] = Field(\n"
        "        default=None,\n"
        '        description="main commit 才有；分支 commit 恒为 null（键本身恒在）",\n'
        "    )",
        '    snapshot_ref: str = Field(description="main commit 才有；分支 commit 恒为 null")',
        "tests/api/test_story_state_write_response_contract.py",
    ),
    # ------------------------------------ 加端点级 exclude_none（任务书原方案 / 另一侧反例）
    (
        "G3-7",
        "packages/core/api/routers/story_state.py",
        "    response_model=BranchPromoteResponse,\n",
        "    response_model=BranchPromoteResponse,\n    response_model_exclude_none=True,\n",
        "tests/api/test_story_state_write_response_contract.py",
    ),
    (
        "G3-8",
        "packages/core/api/routers/story_state.py",
        '@router.get("/projects/{project_id}/state/diff", response_model=StateDiffResponse)',
        '@router.get(\n'
        '    "/projects/{project_id}/state/diff",\n'
        "    response_model=StateDiffResponse,\n"
        "    response_model_exclude_none=True,\n"
        ")",
        "tests/api/test_story_state_write_response_contract.py",
    ),
    # ------------------------------------ 去掉 exclude_unset（缺席键被补成 null 的形状变更）
    (
        "G3-9",
        "packages/core/api/routers/story_state.py",
        "    response_model=BranchPromoteResponse,\n    response_model_exclude_unset=True,\n",
        "    response_model=BranchPromoteResponse,\n",
        "tests/api/test_story_state_write_response_contract.py",
    ),
    # ------------------------------- changed 元素不再建模到 ChangedItem 那一层（前端退化成 unknown）
    (
        "G3-10",
        "packages/core/story_state/models.py",
        "    changed: Optional[list[ChangedDiffItemResponse]] = Field(\n"
        "        default=None, exclude_if=_drop_if_none\n"
        "    )",
        "    changed: Optional[list[dict[str, Any]]] = Field(default=None, exclude_if=_drop_if_none)",
        "tests/api/test_story_state_write_response_contract.py",
    ),
]
