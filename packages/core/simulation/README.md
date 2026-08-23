# What-if Simulation（Sprint 10）

> 状态：**已实现并通过测试**（Sprint 10）。核心入口 ``SimulationService.simulate``。
>
> 关联：**PRD §90**（Plot Simulation 不修改主线）+ **state-delta-v0.md §6.3**（Branch 隔离，Simulation 即 Branch）。

本目录是 NovelOS 的 What-if 推演引擎。给定一组假设 delta，在临时分支上顺序应用，返回与 main 当前状态的结构化 diff，随后归档分支；**main 快照、领域表、commit 数全程零变化**。

---

## 1. 职责

- 创建临时分支（``name`` 默认 ``sim-<ts>``，base_state_version = main 当前 version）。
- 在该分支上顺序走 ``submit_delta → commit_delta(branch_id=temp, _skip_approval=True)``。
- 计算分支终态 vs base 快照的结构化 diff（复用 ``story_state._diff_snapshots``）。
- 归档分支（``status='ARCHIVED'``），保留 commits 供 ``GET /simulations/{id}`` 重放。
- 失败时同样归档分支、抛 ``SimulationError``（含已应用步数 + 错误上下文）。

不在本模块范围（**MVP 限制**）：
- **不做 LLM 推演**——不调用 Observer / Writer；只做状态推演。
- **不做 narrative 生成 / quality score**——对齐 quality-scoring-v0.md §3 算法仅针对生产态 draft；模拟态 Quality Score 暂未决。
- **不做批量 / 长时间模拟**——单次 simulate 调用一个或多个 delta；超时 / token 成本基线见 R11 风险（V2 前解决）。

---

## 2. 模块结构

```
packages/core/simulation/
├── __init__.py        # 公共 API（SimulationService / SimulationError / SimulationResult）
├── service.py         # SimulationService：simulate / list_simulations / get_simulation
└── README.md          # 本文件
```

API 路由：``packages/core/api/routers/simulation.py``（自动发现挂载到 ``/api``）。

---

## 3. 与 Story State 分支的关系

Simulation 完全复用 S7 已落地的「分支 commit 整体跳过领域表写透（``skip_all=True``）」语义——Simulation 即「一次性、不 promote」的分支。Sprint 10 在此基础上加：

| 维度 | 常规分支（promote 路径） | Simulation 分支（一次性） |
|------|------------------------|-------------------------|
| 写 main 快照 | 否（promote 时重放） | 否（根本不 promote） |
| 写领域表 | 否（promote 时重放） | 否（``skip_all=True``，永不重放） |
| HIGH 风险审批门 | 触发，``author_approval.approved=True`` 必须 | 显式 ``_skip_approval=True`` 绕过（理由见下） |
| 分支终态 | ``MERGED`` 或 ``DISCARDED`` | ``ARCHIVED``（Sprint 10 新增枚举值；0005 迁移扩展） |
| 留痕 | commits / deltas 全部保留 | commits / deltas 全部保留（供重放） |

### 3.1 审批门绕过理由

``commit_delta(..., _skip_approval=True)`` 是 ``story_state/service.py`` 的**私有**入参（Sprint 10 新增，仅 Simulation 路径使用）。理由：

- **不产生真实状态**：S7 ``skip_all=True`` 保证领域表不被污染；分支最终 ``ARCHIVED``；main 快照不变。
- **人工审批无意义**：审批门是为「阻止 HIGH 风险变更落入生产状态」设计的；推演是假设场景，运行结果是「看看会发生什么」，不应被审批门卡住。
- **审计字段如实保留**：`author_approval_json.status = 'simulation_bypassed'`、`high_risk_change_ids` 仍列出，便于审计回溯「这条 HIGH 风险是 Simulation 路径带的，不是人工批的」。这是**新增状态值**，与已有 `approved` / `not_required` 并列。

如果未来 Simulation 被扩展为「持久化模拟态」（如 §47 V2），则需要重新评审这个绕过——但当前 Sprint 10 MVP 不涉及。

### 3.2 清理策略

- 不删除 branches 行（保留 commits 供重放、供审计追溯）。
- 分支 status 设为 ``ARCHIVED``；``_resolve_branch`` 已校验 ``status == 'ACTIVE'`` 才接受写入，ARCHIVED 自然拒绝再写入。
- 不存任何 snapshot（分支本就不写 story_states）。
- 失败的推演同样归档——避免遗留 ACTIVE 状态干扰后续。

---

## 4. 公共 API

### 4.1 ``SimulationService(db_path, *, state_service=None)``

| 方法 | 输入 | 输出 | 失败语义 |
|------|------|------|---------|
| ``simulate(project_id, deltas, *, name=None)`` | project_id, list[delta dict], 可选 name | :class:`SimulationResult` | ``SimulationError(reason='empty_deltas' / 'validation_failed' / 'commit_failed' / 'branch_create_failed')`` |
| ``list_simulations(project_id)`` | project_id | list[dict]（name LIKE 'sim-%'） | 无 |
| ``get_simulation(project_id, simulation_id)`` | project_id, branch_id | :class:`SimulationResult` / None（找不到） | 无 |

入参要求：
- ``deltas``：完整 delta dict 列表（与 ``submit_delta`` 入参一致）。
- **每项 delta 必含 ``chapter_id``**——``commit_delta`` 通过 chapter 反查 project_id；缺 ``chapter_id`` → ``StateNotFoundError``（被包成 ``SimulationError(reason='commit_failed')``）。
- 这与生产路径行为一致（schema ``chapter_id`` 本就 required）；task 书允许 ``chapter_id`` 缺省的口径在 simulation 路径下不可行——README 此处明确约束。

### 4.2 ``SimulationError``

字段：
- ``reason``：失败分类。
- ``applied``：已成功提交到临时分支的 delta 数（0..len(deltas)）。
- ``issues``：逐 delta 的错误列表（``[{"index": int, "errors": list[str]}]``，仅 ``validation_failed`` 时填充）。
- ``branch_id``：临时分支 ID（即便归档，保留引用便于审计）。
- ``simulation_id``：推演标识（== branch_id）。

### 4.3 ``SimulationResult``

字段：
- ``simulation_id`` / ``branch_id``：推演标识 == 临时分支 ID。
- ``name``：分支名（``sim-<ts>`` 或参数指定）。
- ``base_version``：main 当前 version（推演期间不变）。
- ``base_state``：推演基线 state JSON（diff 的 a 侧）。
- ``final_state``：推演后 state JSON（diff 的 b 侧）。
- ``applied``：成功提交的 delta 数。
- ``diff``：结构化 diff dict（与 ``diff_versions`` 形态一致，但 version_a/b 取 main vs branch 版本）。
- ``issues``：校验错误列表。
- ``branch_status``：固定 ``'ARCHIVED'``。
- ``created_at``：推演开始时间（ISO-8601）。

---

## 5. REST 端点

自动发现：本模块挂载到 ``packages/core/api/routers/simulation.py``，由 ``discover_routers()`` 挂到 ``/api``。

- ``POST /projects/{pid}/simulate`` —— body: ``{"deltas": [...], "name": str?}``；201 → :class:`SimulationResult`（dict 形态）；422 → 校验失败（issues 列表）；404 → project 不存在；409 → branch 名冲突。
- ``GET /projects/{pid}/simulations`` —— 列历史推演（按 created_at DESC）。
- ``GET /projects/{pid}/simulations/{simulation_id}`` —— 重放某次推演，返回完整 SimulationResult；404 → 找不到。

---

## 6. 迁移

新增 ``database/migrations/0005_branches_archived_status.sql``：把 ``branches.status`` 的 CHECK 从 ``('ACTIVE','MERGED','DISCARDED')`` 扩展为 ``('ACTIVE','MERGED','DISCARDED','ARCHIVED')``。SQLite 不支持 ``ALTER CHECK``，用标准 12-step 重建表手法；幂等由 ``_migrations`` 表保证。

---

## 7. 限制 / 已知风险

- **不实现 PRD §47 V2 范围**：仅状态推演；不调用 Observer / Writer / Quality Engine。
- **chapter_id 必填**：依赖 schema 已约束；调用方需保证每项 delta 都有合法 chapter_id。
- **APPLY 失败语义**：commit 路径若发生非预期异常（DB 断开 / IO 错），归档分支可能也失败——``_safe_archive`` 吞掉异常避免阻塞原异常，详见 ``service.py`` 的 ``_safe_archive`` 注释。
- **历史查询的零成本**：list / get 通过 ``branches.name LIKE 'sim-%'`` 过滤；如未来 sim-* 数量爆炸，可加索引（暂不必要）。
