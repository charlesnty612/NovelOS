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

### Sprint 8：Provider 配置示例（`params_json`）

### Anthropic（`provider="anthropic"`）
原生 Messages API（`POST {base_url}/v1/messages`），鉴权用 `x-api-key` + `anthropic-version: 2023-06-01`，响应文本取 `content[0].text`，usage 字段名 `input_tokens` / `output_tokens`。

```jsonc
{
  "base_url": "https://api.anthropic.com",  // 可省，省略时走官方默认
  "api_key": "sk-ant-..."                   // 可省，见下方解析顺序
}
```

字段说明：

| 字段 | 必填 | 默认值 / 行为 |
|---|---|---|
| `params_json.base_url` | 否 | 默认 `https://api.anthropic.com`（`AnthropicProvider.DEFAULT_BASE_URL`） |
| `params_json.api_key` | 否 | 解析顺序见下；缺省时由 `resolve_api_key` 退回环境变量；都没有则 `None`（调用时会回 401，`/test` 端点仍可探活） |
| `model` | 是 | 必填非空（如 `claude-3-5-sonnet-20241022`），Provider 构造时若空串直接 `ValueError` |
| `params.max_tokens` | 否 | Provider 默认 4096；调用 `complete(messages, params=...)` 时 `params["max_tokens"]` 覆盖默认 |
| `params.temperature` / `top_p` / ... | 否 | 透传至请求体顶层 |

`api_key` 解析顺序（`resolve_api_key`，provider 字段大写）：
1. `params_json["api_key"]`（明文存于 DB，仅 MVP 演示用，不推荐生产）；
2. 环境变量 `NOVELOS_API_KEY_ANTHROPIC`；
3. 都没有 → `None`。

### Ollama（`provider="ollama"`）
本地 `/api/chat` 同步调用器，无需 `api_key`。

```jsonc
{
  "base_url": "http://127.0.0.1:11434",  // 可省，省略时走本地默认
  // 无 api_key 字段 —— Ollama 本地服务不需要
}
```

字段说明：

| 字段 | 必填 | 默认值 / 行为 |
|---|---|---|
| `params_json.base_url` | 否 | 默认 `http://127.0.0.1:11434`（`OllamaProvider.DEFAULT_BASE_URL`） |
| `params_json.api_key` | — | 不解析也不发送；请求头不带 `Authorization` |
| `model` | 是 | 必填非空（如 `llama3`） |
| `params`（除 `format` 外） | 否 | 透传至请求体 `options` 子对象（`temperature` / `top_p` / `seed` 等） |
| `params.format` | 否 | 透传至请求体顶层（Ollama 结构化输出） |

> 注：Ollama 也兼容 OpenAI Chat Completions 协议；选 `provider="openai_compatible"` + `base_url=http://127.0.0.1:11434/v1` 也可（沿用 Sprint 3 路径），按需取舍。

### `api_key` 解析顺序（`resolve_api_key`，所有 provider 共用）
1. `params_json["api_key"]`（明文存于 DB，仅 MVP 演示用，不推荐生产）；
2. 环境变量 `NOVELOS_API_KEY_<PROVIDER大写>`（如 `NOVELOS_API_KEY_ANTHROPIC`、`NOVELOS_API_KEY_OPENAI_COMPATIBLE`）；
3. 都没有 → `None`。`OllamaProvider` 构造时根本不读 key；`OpenAICompatibleProvider` 无 key 通常仍可访问本地 / Ollama 服务，由 Provider 自行决定是否 401。

## Sprint 8：健康检查（`/model-configs/{id}/test` → `Provider.health_check`）

各 provider 实现一个 HTTP 探针，返回统一结构 `{ok, status_code, detail, latency_ms}`：

| Provider | 探针 | OK 判定 |
|---|---|---|
| `MockProvider` | 不发请求 | 永远 `ok=True`、`status_code=200`、`latency_ms=0` |
| `OpenAICompatibleProvider` | `GET {base_url}/models` | 任意 2xx → `ok=True`；其它 4xx/5xx 或网络异常 → `ok=False`，`status_code` 取响应码或 `None` |
| `AnthropicProvider` | `POST {base_url}/v1/messages`，`max_tokens=1`、`messages=[{"role":"user","content":" "}]` | `200` 或 `401`（鉴权失败但 endpoint 通）→ `ok=True`；其它 4xx/5xx 或网络异常 → `ok=False`；401 时 `detail` 标注 `reachable (401 auth failed, but endpoint reachable)` |
| `OllamaProvider` | `GET {base_url}/api/tags` | 任意 2xx → `ok=True`；其它 4xx/5xx 或网络异常 → `ok=False` |

实现位置：`packages/core/model_router/providers.py` 各 Provider 的 `health_check(*, timeout=None)`。

## Sprint 8：失败转移链（`ModelRouter.call_with_fallback`）

```python
completion, used_config_row = router.call_with_fallback(
    "reasoning",
    [{"role": "user", "content": "..."}],
    params={"temperature": 0.7},   # 可选，透传给每条候选的 Provider
    scripted=["ok"],                # 可选，仅 mock 候选使用
)
```

行为规则（与 `router.py:192-247` 逐条对应）：
1. 候选集：`router.list_enabled(capability)`，即 `model_configs` 表 `enabled=1` 且 `capability` 匹配的所有行，**按 `rowid ASC` 排序**（创建顺序；不是按 `created_at` 时间排序——SQLite 中两者通常一致，但实现以 `rowid` 为准）。
2. 逐个调用：先 `router.get_provider(row, scripted=scripted)` 构造 Provider，再 `provider.complete(messages, params=params)`；任一步抛 `Exception`（含 `ProviderError`、网络异常、构造错误）均记 warn 并尝试下一个候选。
3. 首个成功 → 立即返回 `(completion, used_config_row)`，**不再尝试后续候选**。
4. 全部失败 → 抛 `AggregateProviderError(capability, attempts)`，`attempts` 是有序 `[(config_id, error_repr), ...]`，原因为最后一次异常（`raise ... from last_exc`）。
5. 无任何候选 → 抛 `ModelNotConfiguredError(capability)`（与 `resolve` 行为一致，不进入循环）。
6. 单配置场景：与 `resolve` + `get_provider` 旧路径等价（直走第一个即返回）。

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
- `AnthropicProvider`（Anthropic 原生 Messages API）—— 已完成，详见上文。
- `OllamaProvider`（本地服务 + 健康检查）—— 已完成，详见上文。
- 失败转移（`call_with_fallback`，按 `rowid` 顺序逐个尝试，全失败聚合）—— 已完成，详见上文。

## 权威文档
- `docs/agents/agent-contracts-v0.md` §7（Agent 十问中的 capability）。
- `docs/impl/IMPLEMENTATION-PLAN-v0.md` §1 D-I4、§2 Sprint 3/8。
- PRD §51（Model Router）。