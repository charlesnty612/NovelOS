# workflows（业务流程编排层）

> 职责：把 `packages/agents/*` 的角色与 `packages/core/*` 的基础设施组合成可执行的业务工作流；底层依赖 `packages/core/workflow_runtime/`（DAG 执行器）。
> 状态：空骨架（规划 Sprint 4 首批：chapter_plan / chapter_write / chapter_review / chapter_commit；Sprint 1 含 project_init；Sprint 10 含 simulation）。

## 职责与边界
做：声明 DAG 节点（AI / State / Transform / Human / Simulation 五类）、节点输入/输出契约、Human Node 挂起点。
不做：DAG 引擎实现（属 `packages/core/workflow_runtime/`）；节点内部逻辑（属 agents / core 子包）。

## 对外接口（规划中）
- 每条工作流：`Workflow.run(inputs) -> RunHandle`
- 子包：`project_init / chapter_plan / chapter_write / chapter_review / chapter_commit / simulation`

## 依赖
- 上游：`packages/core/workflow_runtime/`、`packages/core/agent_runtime/`、`packages/agents/*`、`packages/domain/*`
- 下游：`tests/workflow/`（无头跑测试）

## 使用 / 入口
待实现（Sprint 1 project_init；Sprint 4 章节四件套；Sprint 10 simulation）。

## 维护注意点
- 工作流节点不写业务规则，只编排；所有规则在对应模块实现。
- Human Node 必须显式挂起点，便于 Sprint 5 Workflow Panel 接管。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 1/4/10、PRD §40、§55-65、§109。