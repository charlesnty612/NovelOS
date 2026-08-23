# workflows.chapter_plan（章节规划）

> 职责：Director Plan → Planner 大纲 → 落库到 `chapter_outlines` / `timeline_events` / `hooks` / `narrative_debts`。
> 状态：空骨架（规划 Sprint 4）。

## 职责与边界
做：调用 Director、Planner agent，把 PlannerProposal 落库；Human Node 让人工审批大纲。
不做：写作；评估。

## 对外接口（规划中）
- `run(inputs: ChapterPlanInputs) -> ChapterPlanHandle`

## 依赖
- 上游：`packages/agents/director/`、`packages/agents/planner/`、`packages/domain/plot/`、`packages/domain/hooks/`

## 使用 / 入口
待实现（Sprint 4）。

## 维护注意点
- Human Node 挂起点必须显式标注（AI Panel 审批）。
- 权威文档：PRD §40、§109；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4。