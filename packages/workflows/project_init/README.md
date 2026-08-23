# workflows.project_init（项目初始化）

> 职责：从 0 到 1 建立项目——创建 `projects` 记录、生成初始 Story Bible（人物/世界/伏笔/债务空骨架）、初始化 `state_version=0`。
> 状态：空骨架（规划 Sprint 1）。

## 职责与边界
做：原子创建项目 + Story Bible 容器 + 初始 commit。
不做：内容生成（属 LLM 引导流程，暂留人工）。

## 对外接口（规划中）
- `run(inputs: ProjectInitInputs) -> Project`

## 依赖
- 上游：`packages/domain/*`、`packages/core/story_state/`

## 使用 / 入口
待实现（Sprint 1）。

## 维护注意点
- 项目 ID 格式 `prj_<ulid>`，由 Service 生成。
- 权威文档：PRD §67（projects 表）；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 1。