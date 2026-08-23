# core.agent_runtime（Agent 运行时）

> 职责：Prompt 注册（`docs/agents/prompts` → agents / prompts 表）、结构化输出（JSON 提取 + 契约校验 + 重试 1 次）、AI 调用日志（`ai_call_logs` 落库）；为 `packages/agents/*` 与手工触发提供统一执行底座。
> 状态：Sprint 3 MVP 完成。

## 职责与边界
做：
- `PromptRegistry.sync_from_docs(docs_dir)`：扫描 `*-v<N>.md` → upsert `agents` / `prompts` 表（幂等）。
- `PromptRegistry.get_active_prompt(agent_name)`：取该 agent 最高版本号 ACTIVE 行。
- `run_agent(...)`：取 prompt → Model Router 取 Provider → 调用 → 提取 JSON → 契约校验（重试 1 次） → 写 `ai_call_logs`。
- `create_adhoc_run(db_path, name="adhoc")`：保证 `workflows.name='adhoc'` 行存在并插入 `workflow_runs`（status='RUNNING'），返回 `run_id`。
- 结构化输出提取（去围栏 + 取首个 `{` 到末个 `}` + `json.loads`）。
- 三档契约校验：`observer` / `director` / `writer`；`None` 跳过。

不做：
- Workflow 编排（属 `workflow_runtime`，Sprint 4+）。
- State Delta 提交（属 `story_state`）。
- Prompt 内容设计 / 修改（属 `docs/agents/prompts`，由人类编辑）。
- 外网 Provider 真实调用测试（任务书边界；用 `mock_script` 旁路）。

## 对外接口

### PromptRegistry
```python
from packages.core.agent_runtime import PromptRegistry
reg = PromptRegistry(db_path)
result = reg.sync_from_docs("docs/agents/prompts")
# {"scanned": [...], "registered": [...], "updated": [...], "agents": [...]}

prompt_id, version_label, content = reg.get_active_prompt("observer")
# ("prm_xxx", "observer:v1", "<prompt content>")
```

### run_agent / create_adhoc_run
```python
from packages.core.agent_runtime import run_agent, create_adhoc_run
run_id = create_adhoc_run(db_path)
output = run_agent(
    db_path,
    "observer",
    {"chapter_id": "ch_001"},
    run_id,
    expected="observer",           # 三档契约校验
    mock_script=["{...}"],          # 测试旁路；None 走 ModelRouter
)
```

### 异常
- `AgentRuntimeError` ——基类。
- `AgentOutputError(message, raw_output=...)` ——LLM 输出经 1 次重试仍不合规；router 转 502。
- `PromptNotFoundError(agent_name)` ——无 ACTIVE prompt；router 转 404。

## 契约校验口径（对齐 agent-contracts-v0.md）

| `expected` | 校验 |
|---|---|
| `"observer"` | 顶层**恰为** 7 个 change 数组键（`character_changes / world_changes / relationship_changes / new_events / resolved_hooks / new_hooks / debt_changes`），不得含 10 个元信息字段（`delta_id / delta_version / schema_version / chapter_id / workflow_run_id / previous_state_version / created_by / created_at / supersedes / notes`）与 Prompt 辅助字段（`deviations / self_check / unresolved_plan_intents`）；每个数组必须为 list（即便空也须存在为 `[]`）。 |
| `"director"` | 必须含 `schema_version == "director-plan.v1"`。 |
| `"writer"` | 必须含 `schema_version == "writer-output.v1"` + `prose` + `self_report`。 |
| `None` | 仅要求合法 JSON。 |

## 输入 Context IDs 提取
从 `input_payload` 中收集所有 `*_id` 键值（字符串或字符串数组），去重并截断到 100 条，写入 `ai_call_logs.input_context_ids_json`。便于 replay 时定位上下文实体。

## 重试机制（对齐 agent-contracts §6.1）
1. Provider 输出 → `extract_json` 解析失败 → 在 user 末尾追加「上次输出无法解析/不合规：<错误>。请只输出合法 JSON。」再次调用（最多 1 次重试）。
2. 契约校验失败同样进入重试。
3. Provider 错误（HTTP / 网络 / 解析）→ 不重试，直接落库 `error` 字段并向上抛（router 转 502）。
4. 重试仍失败 → 抛 `AgentOutputError`，`retry_count=1` 写入 `ai_call_logs`。

## ai_call_logs 落库字段
| 字段 | 来源 |
|---|---|
| `call_id` | `new_id("aic")` |
| `run_id` / `node_run_id` | 由调用方提供（`create_adhoc_run` / workflow runtime） |
| `agent_id` | `agents.agent_id`（由 `PromptRegistry` 同步后保证存在） |
| `model_id` | `f"{provider}/{model}"`（如 `"mock/mock"` 或 `"openai/gpt-4o"`） |
| `prompt_version` | `f"{agent_name}:v{N}"`（如 `"observer:v1"`） |
| `input_context_ids_json` | 见上 |
| `output_json` / `token_usage_json` | 成功时写；失败为 NULL |
| `latency_ms` | `time.monotonic()` 差值 |
| `error` | 失败原因（中文/英文均可） |
| `retry_count` | 0 / 1 |

## 依赖
- 上游：`packages/core/model_router/`（Provider + capability 路由）、`packages/core/db.py`、`packages/core/ids.py`。
- 下游：`packages/agents/*`（V1+ 调用）、`packages/workflows/*`（Sprint 4+ 正式工作流）。

## REST 端点（`/api/agents`）
- `POST /agents/sync?docs_dir=...` ——扫描 docs_dir 同步所有 prompt；body 可选 `{docs_dir}` 或 query 参数。200。
- `GET /agents` ——列出已注册的 agents（按 name 升序）。200。
- `GET /agents/{name}/prompts` ——列出该 agent 的所有 prompts（按 version 降序）。200。
- `POST /agents/{name}/run` ——执行一次 agent 调用。body：`{input_payload, expected?, mock_script?}`。201 / 404 / 422 / 502。

错误码：
- `404`：无 ACTIVE prompt（`PromptNotFoundError`）。
- `422`：缺字段 / `expected` 非法 / 无 model_config（`ModelNotConfiguredError`）。
- `502`：Provider 错误 / `AgentOutputError`（detail 含 error 类别与 message）。

## 维护注意点
- 所有 LLM 调用必经 `ai_call_logs` 落库（PRD §93）。
- `mock_script` 仅用于测试 / 冒烟；生产调用必须走 `ModelRouter.resolve`。
- `create_adhoc_run` 每次新建一行 `workflow_runs`；Sprint 4 正式工作流复用同一 FK 约束但 run_id 由 workflow runtime 管理。
- Agent 名 → capability 映射独立维护在 `agent_runtime/prompts.py._AGENT_TO_CAPABILITY` 与 `model_router/router.py.AGENT_CAPABILITY`；新增 agent 时两边都要更新。

## 权威文档
- `docs/agents/agent-contracts-v0.md` §2 总览、§3.2 / §4.2 / §5.2 契约、§6 重试原则、§7 Agent 十问。
- `docs/state-model/schemas/state-delta.schema.json`（Observer 输出的字段权威）。
- `docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 3。
- PRD §28-40（Agent 职责）、§93（AI 调用日志）、§94（Prompt Version）、§116（结构化输出）。