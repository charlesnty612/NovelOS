# workflows.chapter_review（章节评审）

> 职责：Observer 抽取 delta 提案 + Critic 评审 + Evaluation Harness 评分 → 输出 review verdict（pass / warn / block）；为 chapter_commit 提供放行决策。
> 状态：空骨架（规划 Sprint 4 端到端，Sprint 6 评分管线完整）。

## 职责与边界
做：调 Observer / Critic，组装 verdict；Human Node 可驳回重写。
不做：状态提交（属 chapter_commit）。

## 对外接口（规划中）
- `run(inputs: ChapterReviewInputs) -> ReviewVerdict`

## 依赖
- 上游：`packages/agents/observer/`、`packages/agents/critic/`、`packages/core/evaluation/`

## 使用 / 入口
待实现（Sprint 4 端到端，Sprint 6 完整评分）。

## 维护注意点
- verdict 必含可机检字段：`guardrail_violations / quality_scores / delta_proposal`。
- 五硬门槛（PRD §86）阻断不可绕过。
- 权威文档：PRD §40、§82-86；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4/6。