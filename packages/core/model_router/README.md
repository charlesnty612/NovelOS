# core.model_router（模型路由）

> 职责：抽象 LLM Provider 接口；MVP 仅实现 `openai_compatible`（覆盖 OpenAI/DeepSeek/通义/Ollama），`mock` Provider 供测试；Sprint 8 补 Anthropic 原生 + Ollama 本地 + 健康检查。
> 状态：空骨架（规划 Sprint 3 MVP，Sprint 8 补全）。

## 职责与边界
做：Provider 注册、能力声明（context window / 价格 / 支持特性）、请求路由与失败转移。
不做：业务 prompt 设计（属 agent）；结果解析（属 `agent_runtime`）。

## 对外接口（规划中）
- `Provider.complete(messages, **opts) -> Completion`
- `Router.route(capability_requirements) -> Provider`
- MVP：`MockProvider` / `OpenAICompatibleProvider`；Sprint 8：`AnthropicProvider` / `OllamaProvider`

## 依赖
- 外部：HTTP 客户端（`httpx` 已在依赖中）
- 下游：`packages/core/agent_runtime/`

## 使用 / 入口
待实现（Sprint 3 首批 MVP，Sprint 8 补全）。

## 维护注意点
- 测试一律用 `MockProvider`，不依赖外网（`docs/impl/IMPLEMENTATION-PLAN-v0.md` D-I4）。
- Provider 适配器需对请求/响应做严格类型校验，失败计入 `ai_call_logs`。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §1 D-I4、§2 Sprint 3/8、PRD §51。