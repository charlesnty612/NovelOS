# workflows.chapter_commit（章节提交）

> 职责：把 Observer 产出的 delta 提案经 Integrator 落库，提交到 `state_deltas` 与 `commits`；维护 `state_version` 严格 +1/commit。
> 状态：空骨架（规划 Sprint 4）。

## 职责与边界
做：调 Integrator 提交；校验 review verdict 为 pass/warn 才放行；记录 `commits` 字段（PRD §91）。
不做：写作、评估；自动改写 prose。

## 对外接口（规划中）
- `run(inputs: ChapterCommitInputs) -> Commit`

## 依赖
- 上游：`packages/agents/integrator/`、`packages/core/story_state/`、`packages/workflows/chapter_review/`

## 使用 / 入口
待实现（Sprint 4）。

## 维护注意点
- block 状态的 verdict 一律不放行；warn 可由人工 override。
- `state_version` 严格 +1（`docs/state-model/state-delta-v0.md` §6.2）。
- 权威文档：PRD §40、§91；`docs/state-model/state-delta-v0.md`；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4。