# core.story_state（Story State Delta 应用器）

> 职责：状态增量的 Validate→Commit→Rollback→Snapshot，应用 `state-delta.schema.json`，维护 `state_version` 严格 +1/commit。
> 状态：空骨架（规划 Sprint 2）。

## 职责与边界
做：接收 State Delta JSON，按 schema 校验、合并到内存状态、原子写入 `state_deltas` 与 `commits`、按需回滚与快照恢复。
不做：直接触发 LLM；不渲染 UI。

## 对外接口（规划中）
- `Delta.apply(state, delta) -> Commit`：单次提交，递增 state_version
- `Delta.rollback(state, commit_id)` / `Delta.snapshot(state)` / `Delta.restore(snapshot)`
- 权威 schema：`docs/state-model/schemas/state-delta.schema.json` / `state-commit.schema.json`

## 依赖
- 上游：`packages/core/db.py`（事务与连接）、`packages/core/logging_config.py`
- 下游：`packages/agents/observer/`（产出 delta）、`packages/workflows/chapter_commit/`

## 使用 / 入口
待实现（Sprint 2）。

## 维护注意点
- `state_version` 严格 +1（`docs/state-model/state-delta-v0.md` §6.2）；不允许跳跃与回退。
- 不引入 ORM，SQL 一律走 `packages/core/db.py:get_connection`。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 2、PRD §13-23。