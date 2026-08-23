# agents.director（Director）

> 职责：章节规划决策——基于项目状态与上下文输出本章目标、约束、风险评估；为 `workflows/chapter_plan` 提供决策输入。
> 状态：空骨架（规划 Sprint 3）。

## 职责与边界
做：读取项目状态 + Story Bible 摘要，产出 Director Plan（章节目标 / 约束 / 风险）。
不做：写作（属 Writer）；审计（属 Critic）。

## 对外接口（规划中）
- `run(inputs: DirectorInputs) -> DirectorPlan`
- 提示词：`docs/agents/prompts/director.md`
- 输入/输出 schema：`docs/agents/schemas/director.{in,out}.json`

## 依赖
- 上游：`packages/core/agent_runtime/`、`packages/core/context_engine/`
- 下游：`packages/workflows/chapter_plan/`

## 使用 / 入口
待实现（Sprint 3）。

## 维护注意点
- Director Plan 必须可被人工审批/驳回（Sprint 5 AI Panel Human Node）。
- 权威文档：PRD §29；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 3。