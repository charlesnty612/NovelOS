# workflows.chapter_write（章节写作）

> 职责：按 Planner 大纲调用 Writer 生成本章 prose 草稿，临时存草稿到 `chapters`（status=DRAFT），等待 Review。
> 状态：空骨架（规划 Sprint 4）。

## 职责与边界
做：组装 Context Engine 输入、调 Writer agent、写草稿；不评估、不提交。
不做：质量审计（属 chapter_review）；状态提交（属 chapter_commit）。

## 对外接口（规划中）
- `run(inputs: ChapterWriteInputs) -> ChapterDraftHandle`

## 依赖
- 上游：`packages/agents/writer/`、`packages/core/context_engine/`、`packages/domain/plot/`（大纲）

## 使用 / 入口
待实现（Sprint 4）。

## 维护注意点
- Writer 输出受 Guardrail 实时约束（PRD §86），违规立即阻断。
- 权威文档：PRD §40；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4。