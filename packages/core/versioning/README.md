# core.versioning（章节与 State 版本管理）

> 职责：基于 `packages/core/story_state/` Snapshot 机制实现章节与 State 的分支、diff、回滚（**非 git**，自建轻量版本管理，PRD §75「迁移纪律」+ S7 任务）。
> 状态：空骨架（规划 Sprint 7）。

## 职责与边界
做：在 `state_snapshots` 表之上提供 branch / diff / restore API；为 Simulation（Sprint 10）提供基础。
不做：UI 视图（属 Sprint 9）；与外部 git 集成（`docs/impl/IMPLEMENTATION-PLAN-v0.md` 明确非 git）。

## 对外接口（规划中）
- `Version.branch(state_version) -> Branch`
- `Version.diff(a, b) -> Delta`
- `Version.restore(branch, target_version)`

## 依赖
- 上游：`packages/core/story_state/`、`packages/core/db.py`

## 使用 / 入口
待实现（Sprint 7）。

## 维护注意点
- 严禁引入 git / pygit2 等外部 VCS 库；Snapshot 机制即版本真相。
- 分支推演必须不污染主 State（Sprint 10 仿真场景）。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 7/10。