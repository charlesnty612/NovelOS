# agents（智能体角色层）

> 职责：六个 Agent 角色实现——Director / Planner / Writer / Observer / Critic / Integrator；每个角色由 prompt + 输入/输出 schema + 业务编排函数组成，运行底座为 `packages/core/agent_runtime/`。
> 状态：空骨架（规划 Sprint 3 起逐个填充，Sprint 4 全部接入工作流）。

## 职责与边界
做：声明 prompt 路径（`docs/agents/prompts/<role>.md`）、输入/输出 JSON schema、调用 `agent_runtime.run(...)`。
不做：直接调用 Model Router；持久化状态（属 `packages/core/story_state/`）。

## 对外接口（规划中）
- 各角色：`run(inputs) -> outputs`
- 提示词与 schema 权威：`docs/agents/prompts/*.md` + `docs/agents/schemas/*.json`

## 依赖
- 上游：`packages/core/agent_runtime/`、`packages/core/context_engine/`、`packages/domain/*`
- 下游：`packages/workflows/*`

## 使用 / 入口
待实现（Sprint 3 director/writer/observer 端到端，Sprint 4 全部接入）。

## 维护注意点
- 所有 Agent 调用必经 `ai_call_logs` 落库（PRD §93）。
- JSON 输出必须通过 schema 校验；解析失败由 `agent_runtime` 触发重试。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 3/4、PRD §29-36。