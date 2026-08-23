# core.workflow_runtime（工作流 Runtime）

> 职责：自研最小 DAG 执行器（节点五类：AI / State / Transform / Human / Simulation）；对外只暴露 `Runtime Adapter` 接口（PRD §72），未来可替换外部引擎。
> 状态：空骨架（规划 Sprint 4）。

## 职责与边界
做：解析 DAG、拓扑执行、节点结果路由、人机节点挂起 / 恢复、记录 `workflow_runs` 与 `workflow_run_nodes`。
不做：内置具体业务工作流（属 `packages/workflows/*`）；不集成外部工作流引擎（DeterminFlow 评估后不集成，见 `docs/impl/IMPLEMENTATION-PLAN-v0.md` D-I3）。

## 对外接口（规划中）
- `RuntimeAdapter.run(dag, inputs) -> RunHandle`
- 节点基类：`AINode / StateNode / TransformNode / HumanNode / SimulationNode`

## 依赖
- 上游：`packages/core/db.py`、`packages/core/logging_config.py`
- 下游：`packages/workflows/*`

## 使用 / 入口
待实现（Sprint 4）。

## 维护注意点
- 严格按 PRD §55-65 五类节点设计；不为某条业务工作流开特例。
- `Runtime Adapter` 抽象层必须稳定，便于未来替换。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §1 D-I3、§2 Sprint 4、PRD §55-65、§72。