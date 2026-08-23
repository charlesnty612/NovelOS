# workflows.simulation（What-if 推演）

> 职责：在 State 快照副本上跑 What-if 分支推演（Plot Graph + 角色决策分支），不污染主 State；为黄金三章机检（Sprint 11）提供推演环境。
> 状态：空骨架（规划 Sprint 10）。

## 职责与边界
做：基于 `packages/core/versioning/` 创建分支副本、模拟运行、回滚/合并；为参照系与合规（Sprint 11）提供基础。
不做：业务工作流串联（属 chapter_*）；UI 视图（属 Sprint 9）。

## 对外接口（规划中）
- `run(inputs: SimulationInputs) -> SimulationResult`
- 关联：`packages/core/versioning/` 的分支 API

## 依赖
- 上游：`packages/core/versioning/`、`packages/core/workflow_runtime/`（Simulation 节点）

## 使用 / 入口
待实现（Sprint 10）。

## 维护注意点
- 推演过程不写主 State；所有写入限定在副本 namespace。
- 必须可单测证明「推演不污染主 State」（`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 10 DoD）。
- 权威文档：PRD §40；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 10/11。