# agents.planner（Planner）

> 职责：把 Director Plan 拆解为可执行的章节大纲（`chapter_outlines`）+ 时间线事件（`timeline_events`）+ 伏笔 / 债务更新提案。
> 状态：空骨架（规划 Sprint 4）。

## 职责与边界
做：在 Director Plan 之下生成结构化大纲、事件序列、Hook/Debt 提案；为 Writer 提供作业蓝图。
不做：写作；状态写入（产出的提案由 workflow 落库）。

## 对外接口（规划中）
- `run(inputs: PlannerInputs) -> PlannerProposal`
- 提示词：`docs/agents/prompts/planner.md`

## 依赖
- 上游：`packages/agents/director/`、`packages/core/agent_runtime/`
- 下游：`packages/workflows/chapter_plan/`

## 使用 / 入口
待实现（Sprint 4）。

## 维护注意点
- PlannerProposal 必含可机检字段：`chapter_outline / events / hook_updates / debt_updates`。
- 权威文档：PRD §30；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4。