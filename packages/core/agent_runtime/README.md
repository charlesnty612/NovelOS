# core.agent_runtime（Agent 运行时）

> 职责：Prompt 加载、结构化输出（JSON 提取 + schema 校验 + 重试）、AI 调用日志落库；为 `packages/agents/*` 提供统一执行底座。
> 状态：空骨架（规划 Sprint 3）。

## 职责与边界
做：加载 `docs/agents/prompts/*.md`、调用 Model Router、解析 JSON 输出、按 schema 重试、写入 `ai_call_logs`。
不做：业务编排（属 workflow）；模型路由策略（属 `model_router/`）。

## 对外接口（规划中）
- `AgentRuntime.run(agent_id, inputs, schema) -> Output`
- 关联：`packages/agents/{director,planner,writer,observer,critic,integrator}`

## 依赖
- 上游：`packages/core/model_router/`、`packages/core/logging_config.py`、`packages/core/db.py`
- 下游：`packages/agents/*`、`packages/workflows/*`

## 使用 / 入口
待实现（Sprint 3）。

## 维护注意点
- 所有 LLM 调用必经 `ai_call_logs` 落库（PRD §93）。
- JSON 解析失败时按 schema 重试，重试上限与退避策略由 PRD §28-40 决定。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 3、PRD §28-40。