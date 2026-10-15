"""跨端契约看守的突变规格（2026-10-15 批次（七）· 主会话自办）。

这批守卫（`apps/web/src/utils/__crossBoundary__/*.backend.test.ts`）读**后端源文件**
当第三方基准，钉住三处前端复刻的私有约定。它们的突变有四种形状：

1. **改后端文案**（标识值）⇒ 守卫必须红（这正是要防的漂移）；
2. **改后端状态机表**（加一条迁移）⇒ 守卫必须红；
3. **改后端写入布局**（键名）⇒ 守卫必须红；
4. **把守卫的基准改回硬编码** ⇒ 它就会重新变成一条「声明」而非断言——
   这一条突变**故意不可执行**（它改的是前端测试自身，跑了也没意义），
   故不列入本表；它由 `backendSource.ts` 的设计约束保证：读取器一律从源文件解析。

⚠️ 本表的定向用例是**前端 vitest**（`src/...` 前缀），执行器会按前缀自动选 runner。
⚠️ 突变目标是**后端源**，与 `g*/l*` 系列（改后端 API 层）不重叠；但**仍不得与
pytest 并发**——改 `engine.py` / `pipeline.py` 会波及任何 import 它们的用例。
"""

MUTATIONS = [
    # ---- ① error 文案协议：改标识值 ----
    (
        "CB-1",
        "packages/workflows/chapter_review/pipeline.py",
        'super().__init__("rejected-for-revision")',
        'super().__init__("rejected_for_revision")',
        "src/utils/__crossBoundary__/chapterState.backend.test.ts",
    ),
    (
        # 改法二：把「纯驳回」改成 revision 的前缀形态 ⇒ 两条路径撞一起
        # （前端注释里的「顺序陷阱」失效，必须被守卫 3 抓到）
        "CB-2",
        "packages/workflows/chapter_review/pipeline.py",
        'super().__init__("rejected")',
        'super().__init__("rejected-for-revision")',
        "src/utils/__crossBoundary__/chapterState.backend.test.ts",
    ),
    (
        "CB-3",
        "packages/core/workflow_runtime/engine.py",
        'error="cancelled by user",',
        'error="cancelled-by-user",',
        "src/utils/__crossBoundary__/chapterState.backend.test.ts",
    ),
    # ---- ② 状态机表 ----
    (
        "CB-4",
        "packages/domain/ledger/models.py",
        '"ACTIVE": {"ACTIVE", "ESCALATED", "RESOLVED", "ABANDONED"},',
        '"ACTIVE": {"ACTIVE", "ESCALATED", "RESOLVED", "ABANDONED", "PAUSED"},',
        "src/utils/__crossBoundary__/ledgerState.backend.test.ts",
    ),
    (
        # 后端**删**一条合法迁移：前端多放行一条 ⇒ UI 会给出非法选项
        "CB-5",
        "packages/domain/ledger/models.py",
        '"ESCALATED": {"ESCALATED", "RESOLVED", "ABANDONED"},',
        '"ESCALATED": {"ESCALATED"},',
        "src/utils/__crossBoundary__/ledgerState.backend.test.ts",
    ),
    # ---- ③ 暂停载荷布局 ----
    (
        "CB-6",
        "packages/core/workflow_runtime/engine.py",
        'ctx[node.node_id] = {"__pause_payload__": payload}',
        'ctx[node.node_id] = {"pause_payload": payload}',
        "src/utils/__crossBoundary__/pausePayload.backend.test.ts",
    ),
]
