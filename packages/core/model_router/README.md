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

- `OpenAICompatibleProvider.complete` 的 HTTP 请求默认 timeout 为 240 秒；可在 `model_configs.params_json.timeout_s` 配置正数秒数。

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
- `POST /model-configs/{id}/test` ——ping；调 `Provider.health_check` 返回 `{config_id, ok, latency_ms, detail, status_code?}`；**`enabled=0` 的 config 返回 422**（P2-5 业务规则）；Provider 错误 → 502。

`params_json` 字段接受 dict 或 JSON 字符串（前端友好）；写入时统一 `json.dumps`。

## ModelConfigService（V1.5 数据访问层）

V1.5 起把 `model_configs` 表的 SQL 从 router 下沉到独立 service（`packages/core/model_router/configs.py`），与既有 chapter/character 等 service 层风格一致。

### 职责

- `model_configs` 表 CRUD：`get` / `list` / `create` / `update_partial` / `delete`。
- 写入语义：`create` 接收调用方已剥离 mask/空 `api_key` 后的纯 `params` dict，仅做 `json.dumps`；
  `update_partial` 接收调用方已按 PATCH 合并语义处理过的 `fields` dict，`params_json` 字段值
  直接落库不再合并。
- 读路径统一返回 `sqlite3.Row` 转 dict；**不脱敏**——脱敏由 router 层 `_mask_*` 负责（保持
  service 语义纯粹、路由层负责对外契约）。
- 异常：`IntegrityError` 由调用方按业务映射（router 转 422）。

### 分层

- **service**（`configs.py`）：全部 SQL、id 生成（`new_id("mcf")`）、json 序列化。
- **router**（`routers/model_configs.py`）：仅做参数校验（Pydantic schema）、脱敏
  （`_mask_response` 派生 `api_key=***` + `has_api_key`）、错误映射（IntegrityError→422、
  ProviderError→502）、HTTP 状态码。
- **API 契约零变化**：对外端点、请求体、响应体与 V1.4 完全一致。

### 脱敏边界

- service 层 `get` / `list` / `create` / `update_partial` 返回的 dict **含明文 `api_key`**
  （或由调用方传入的形态）；脱敏是 router 层 `_mask_response` 的职责，不在 service。
- 这样 service 既能被 router 调用，也能被未来需要明文的链路（如备份排除、调试工具）
  安全使用，避免脱敏逻辑分散到多处。
- router 对入参 `api_key` 也做 mask 剥离：mask 哨兵 `***` → 不改 DB；空串 `""` → 清空 DB；
  这层逻辑仍在 router（属于请求体语义），service 只看最终 dict。

## 维护注意点
- 测试一律用 `MockProvider` 或 `httpx.MockTransport` 注入；不依赖外网（`docs/impl/IMPLEMENTATION-PLAN-v0.md` D-I4）。
- OpenAI 兼容 Provider 必须有 `params_json.base_url`；缺则 `ValueError`（在 `get_provider` 时抛）。
- `enabled=0` 行 Router 不返回；`/test` 端点对 enabled=0 直接返回 422（P2-5 业务规则）。
- Provider 错误透传 status_code；上层 `agents.py` router 转 502。
- **脱敏边界（V1.5 起）**：`model_configs` 读路径脱敏（`api_key` → `***` + `has_api_key` 布尔）
  只在 router 层 `_mask_response` 做；service 层返回明文。备份链路有意绕过脱敏取原始行。
  改 service 时不要顺手加脱敏，否则备份/导入会丢 key（虽然备份刻意排除该表，但同口径要保留）。

## Sprint 8 计划
- `AnthropicProvider`（Anthropic 原生 Messages API）—— 已完成，详见上文。
- `OllamaProvider`（本地服务 + 健康检查）—— 已完成，详见上文。
- 失败转移（`call_with_fallback`，按 `rowid` 顺序逐个尝试，全失败聚合）—— 已完成，详见上文。

## V3.7 模型档案 + 环节绑定（两层架构）

把"模型档案"与"环节绑定"解耦：同一条模型可被多个环节复用，不必复制多份。

```
profile (model_profiles)            binding (capability_bindings)        resolver (ModelRouter._candidates)
┌─────────────────────────┐         ┌─────────────────────────┐         ┌──────────────────────────────┐
│ profile_id (mprof_xxx)  │◀────┐   │ capability (PK)         │         │ 1. bindings 命中             │
│ name / provider / model │     │   │ profile_ids (JSON 数组, │────┐    │    → profile_ids 顺序即       │
│ params_json / enabled   │     │   │   顺序即 fallback 序)   │    │    │      fallback 链            │
└─────────────────────────┘     │   └─────────────────────────┘    │    │ 2. 无 binding → 回落          │
                                │                                  │    │      model_configs（旧）      │
                                └──────────────────────────────────┘    └──────────────────────────────┘
```

### `_candidates(capability)` 优先级
1. `capability_bindings` 有该 capability → 取 `profile_ids` JSON 数组，逐个解析 `model_profiles.enabled=1` 行；缺失或 disabled 跳过。返回行键名与 `model_configs` **完全一致**（`config_id ← profile_id`、`capability ← 本 capability`），保证 `get_provider` 与 runner 把 `config_id` 写 `ai_call_logs` 不需要 schema 改动。
2. 无 binding → 直接查 `model_configs` 中 `capability` 匹配 `enabled=1` 的全部行（按 rowid ASC，V3.6 旧行为）。

### CAPABILITY_LABELS（七环节）
有序 dict，前端 / GET bindings 用，每项含 `label` 与 `agents`（从 `AGENT_CAPABILITY` 反推）：

| capability | label | agents |
|---|---|---|
| `premise_design` | 题材定位 | `premise_designer` |
| `world_building` | 世界观 | `world_builder` |
| `character_design` | 角色设计 | `character_designer` |
| `volume_outline` | 卷纲 | `volume_outliner` |
| `creative_writing` | 正文写作 | `writer` / `polisher` / `scene_planner` |
| `reasoning` | 推理规划 | `director` / `observer` / `arbiter` / `deconstructor_chapter` / `deconstructor_aggregate` |
| `light` | 轻量评审 | `summarizer` / `critic` / `writer(revise·改稿，V3.9.2 起 revise 模式局部修改走 light)` |

### 绑定 / fallback 语义
- `light` capability：V3 P0-2 的回退到 `reasoning` 行为在 `call_with_fallback` 中保留。
  - 有显式 binding（即使只有 disabled / missing profile）→ 不回退；
  - 无 binding + `model_configs` 也无 light 行 → 走 reasoning 候选链，并把 `used_config_row['capability']` 改写为 `'reasoning'` 便于下游审计。
- 其它 capability：无 binding → 直接查 `model_configs`，无命中抛 `ModelNotConfiguredError`。
- 全部候选失败：`ModelNotConfiguredError`（resolve）/ `AggregateProviderError`（call_with_fallback）。

### 新增 API
- `GET /model-profiles[?include_enabled_only=true]` / `POST /model-profiles` / `GET|PATCH|DELETE /model-profiles/{id}` / `POST /model-profiles/{id}/test`
- `GET /capability-bindings`（返回全 7 项，含 label/agents/profile_ids/profiles/legacy_available/updated_at）
- `PUT /capability-bindings/{capability}`（body `{profile_ids: [...]}`，至少 1 个、须都存在且 enabled=1；未知 capability → 404）
- `DELETE /capability-bindings/{capability}`（解除 binding → 回落旧行为）
- 旧 `/model-configs` 端点保留不动（**只读兼容期**，仅 model_configs 写入仍走 `/model-configs`；新代码优先用两层 API；详见 README「弃用说明」）。

### 共享脱敏
`packages/core/model_router/security.py` 集中 `_MASK` / `_mask_response` / `_prepare_post_params` / `_prepare_patch_params` 等参数掩码工具，`/model-configs` / `/model-profiles` 路由共同 import 使用，避免重复粘贴。

### PATCH `params_json` 合并语义（V3.8+）
- 缺键 → DB 原值保留（部分更新；`api_key="***"` 掩码合并依赖此语义）；
- 键显式 `null` → 从结果中删除该键（思考档位「回默认」等场景；`api_key` 的 `null` 仍走清空分支）；
- `api_key` 特殊值：`"***"` 保留原值、`""` 清空、其它字符串覆盖。

### 弃用说明（V3.7 起）
- `/model-configs` 端点进入只读兼容期：仍可读、可改；不建议再向该端点写入新行（新建档案请改走 `/model-profiles`）。
- `ModelConfigService` 保留供 `ModelRouter._candidates` 的「无 binding 回落」路径使用；新代码不应再直接调用它。
- 下一里程碑将下线 `/model-configs` 写入语义；仅 `/model-profiles` + `/capability-bindings` 是受支持入口。

## 权威文档
- `docs/agents/agent-contracts-v0.md` §7（Agent 十问中的 capability）。
- `docs/impl/IMPLEMENTATION-PLAN-v0.md` §1 D-I4、§2 Sprint 3/8。
- PRD §51（Model Router）。