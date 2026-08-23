# agents.writer（Writer）

> 职责：按 Planner 大纲撰写章节正文（`chapters`），输出符合知识权限的 prose，受 Guardrail 实时约束。
> 状态：空骨架（规划 Sprint 4）。

## 职责与边界
做：基于 Planner 蓝图与 Context Engine 组装的上下文，生成本章 prose 草稿。
不做：审计（属 Critic）；状态提交（属 Integrator）。

## 对外接口（规划中）
- `run(inputs: WriterInputs) -> ChapterDraft`
- 提示词：`docs/agents/prompts/writer.md`

## 依赖
- 上游：`packages/agents/planner/`、`packages/core/context_engine/`、`packages/core/agent_runtime/`
- 下游：`packages/workflows/chapter_write/`

## 使用 / 入口
待实现（Sprint 4）。

## 维护注意点
- Writer 输出必须遵守 `reveal_policies` 与角色 `visibility`；违规由 Guardrail 阻断（PRD §86）。
- 权威文档：PRD §31；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4。