# agents.observer（Observer）

> 职责：阅读章节正文，提取事实变更（人物、关系、时间线、伏笔、债务），产出 State Delta 提案（不直接落库）。
> 状态：空骨架（规划 Sprint 3 端到端；Sprint 4 串联到 Commit 工作流）。

## 职责与边界
做：解析 prose → 结构化 delta 提案；与 Story State delta schema 对齐；产出的 delta 交 Integrator 落库。
不做：直接写 DB；不做评估（属 Critic）。

## 对外接口（规划中）
- `run(inputs: ObserverInputs) -> DeltaProposal`
- 输出 schema：`docs/state-model/schemas/state-delta.schema.json`

## 依赖
- 上游：`packages/core/agent_runtime/`、`packages/core/context_engine/`
- 下游：`packages/agents/integrator/`、`packages/workflows/chapter_review/`

## 使用 / 入口
待实现（Sprint 3 端到端）。

## 维护注意点
- delta 必须 schema-validated；违规由 `agent_runtime` 重试。
- 权威文档：PRD §32；`docs/state-model/state-delta-v0.md`；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 3。