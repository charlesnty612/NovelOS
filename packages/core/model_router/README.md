# core.model_router（模型路由）

> 职责：抽象 LLM Provider 接口；按 capability 路由到已配置的 Provider；MVP 实现 `mock` + `openai_compatible`（OpenAI / DeepSeek / 通义 / Ollama 等）；Sprint 8 补 Anthropic 原生与本地健康检查。
> 状态：Sprint 3 MVP 完成（Provider 工厂 + Router + Api CRUD + ping）；Sprint 8 补全。

## 职责与边界
做：
- `Provider.complete(messages, **params) -> Completion`（统一返回 `{text, usage}`）。
- `ModelRouter.resolve(capability) -> model_configs row`：取 `enabled=1` 的第一行。
- `ModelRouter.get_provider(config_row, *, scripted=None)`：按 provider 字段工厂化。
- `mock` Provider（测试 / 冒烟）和 `openai_compatible` Provider（同步 HTTP 调用，超时 60s）。
- Agent → Capability 映射（见下）。

不做：
- 业务 prompt 设计（属 `agent_runtime`）。
- 结果解析 / 契约校验 / 重试（属 `agent_runtime`）。
- 实际外网调用的测试覆盖（任务书边界；CI 用 mock）。

## Agent → Capability 映射（对齐 agent-contracts §7）

| Agent | capability |
|---|---|
| director | `reasoning` |
| observer | `reasoning` |
| writer | `creative_writing` |
| arbiter | `reasoning` |
| deconstructor_chapter | `reasoning` |
| deconstructor_aggregate | `reasoning` |
| 其它（critic / planner / integrator 等）| 默认 `reasoning` |

源代码常驻 `AGENT_CAPABILITY` 与函数 `capability_for(agent_name)`；未知 agent 默认 `reasoning`。

## 对外接口

### Provider
```python
from packages.core.model_router import MockProvider, OpenAICompatibleProvider

# Mock：列表 / callable / None
mock = MockProvider(scripted=["first", "second"])
mock.complete([{"role": "user", "content": "hi"}])
# {"text": "first", "usage": {"prompt":0,"completion":0,"total":0}}

# OpenAI 兼容（OpenAI / DeepSeek / Ollama / 通义 等）
provider = OpenAICompatibleProvider(
    base_url="https://api.openai.com/v1",
    api_key="sk-...",   # 或 None（Ollama 本地不需要）
    model="gpt-4o",
)
result = provider.complete(messages, params={"temperature": 0.7})
```

### Router
```python
from packages.core.model_router import ModelRouter
router = ModelRouter(db_path)
row = router.resolve("reasoning")        # 取 model_configs 第一条 enabled=1
provider = router.get_provider(row)      # 工厂化
# 测试旁路：
test_provider = router.get_provider({"provider": "mock", "model": "x", "params_json": "{}"},
                                   scripted=["ok"])
```

### 异常
- `ModelRouterError` ——基类。
- `ModelNotConfiguredError(capability)` ——Router.resolve 无命中。
- `ProviderError(provider, message, status_code=...)` ——HTTP / 解析失败。

### API Key 解析（按任务书口径）
1. `params_json["api_key"]`（明文存于 DB，仅 MVP 演示用，不推荐生产）。
2. 环境变量 `NOVELOS_API_KEY_<PROVIDER大写>`。
3. 都没有 → `None`（Ollama 等本地服务可无 key；OpenAI 类无 key 会 401）。

## 依赖
- 外部：`httpx>=0.26`（项目已有）。
- 上游：`packages/core/db.py`、`packages/core/ids.py`。
- 下游：`packages/core/agent_runtime/`（`runner.py` 用它构造 Provider）。

## REST 端点（`/api/model-configs`）
- `POST /model-configs` ——创建；body `{capability, provider, model, params_json?, enabled?}`，201。
- `GET /model-configs?capability=&provider=` ——列表（可选过滤），200。
- `GET /model-configs/{id}` ——单条；404 不存在。
- `PATCH /model-configs/{id}` ——部分更新（capability / provider / model / params_json / enabled），200 / 404。
- `DELETE /model-configs/{id}` ——删除，204 / 404。
- `POST /model-configs/{id}/test` ——ping；发「回复 ok」返回 `{latency_ms, preview, usage}`；**`enabled=0` 的 config 返回 422**（P2-5 业务规则）；Provider 失败 → 502。

`params_json` 字段接受 dict 或 JSON 字符串（前端友好）；写入时统一 `json.dumps`。

## 维护注意点
- 测试一律用 `MockProvider` 或 `httpx.MockTransport` 注入；不依赖外网（`docs/impl/IMPLEMENTATION-PLAN-v0.md` D-I4）。
- OpenAI 兼容 Provider 必须有 `params_json.base_url`；缺则 `ValueError`（在 `get_provider` 时抛）。
- `enabled=0` 行 Router 不返回；`/test` 端点对 enabled=0 直接返回 422（P2-5 业务规则）。
- Provider 错误透传 status_code；上层 `agents.py` router 转 502。

## Sprint 8 计划
- `AnthropicProvider`（Anthropic 原生 Messages API）。
- `OllamaProvider`（本地服务 + 健康检查）。
- 失败转移（capability 多 model 时按 price / latency 二选）。

## 权威文档
- `docs/agents/agent-contracts-v0.md` §7（Agent 十问中的 capability）。
- `docs/impl/IMPLEMENTATION-PLAN-v0.md` §1 D-I4、§2 Sprint 3/8。
- PRD §51（Model Router）。