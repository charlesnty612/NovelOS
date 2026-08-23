# agents.integrator（Integrator）

> 职责：把 Observer 的 delta 提案整合并落库——按 `state-delta.schema.json` 校验、调用 `packages/core/story_state/` 提交、写入 `state_deltas` 与 `commits`；处理冲突与去重。
> 状态：空骨架（规划 Sprint 4）。

## 职责与边界
做：delta 验证 + commit 编排 + 冲突检测；与 Story State 协同维护 `state_version` 严格 +1。
不做：直接读 prose（属 Observer）；不做评估（属 Critic）。

## 对外接口（规划中）
- `commit(deltas: list[Delta]) -> Commit`
- 关联：`packages/core/story_state/` 的 `Delta.apply`

## 依赖
- 上游：`packages/agents/observer/`、`packages/core/story_state/`
- 下游：`packages/workflows/chapter_commit/`

## 使用 / 入口
待实现（Sprint 4）。

## 维护注意点
- 同一 chapter 的 delta 必须按 `delta_index` 顺序提交；不允许跳跃。
- 冲突检测：相同字段在同一 commit 内多 delta 出现时按「最新覆盖 + 记录冲突」策略。
- 权威文档：PRD §34；`docs/state-model/state-delta-v0.md` §6.2；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4。