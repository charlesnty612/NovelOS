# workflows.chapter_plan（章节规划）

> 职责：调 Director agent 生成章节导演计划，写入 `chapters.plan_json`。
> 状态：Sprint 4-A 已实现。

## 节点列表

| node_id | kind | 说明 |
|---|---|---|
| `build_ctx` | Transform | 调 `context_engine.build_director_input` 组装 Director 输入（含 author_intent / story_state_snapshot / character_excerpts 等） |
| `director` | AI | 调 Director agent（`run_agent(..., expected="director", mock_script=...)`），输出 `director-plan.v1` JSON |
| `save_plan` | State | 把 Director 输出写入 `chapters.plan_json`；chapters.status 不动（保持 PLANNED） |

注册名：`chapter-plan`

## 输入 / 输出

- 输入：`chapter_id` + `author_intent`（POST `/api/projects/{pid}/chapters/{cid}/plan` body）。
- 输出：`run_id` + status。`COMPLETED` 时 chapters.plan_json 已落库；status 仍 PLANNED。

## 失败语义

- LLM 输出不合规（run_agent 内部重试 1 次后仍失败）→ run FAILED。
- 节点异常 → run FAILED。

## 依赖

- `packages/core/context_engine/`
- `packages/core/agent_runtime/`
- `packages/core/workflow_runtime/`

## 使用 / 入口

API：

```http
POST /api/projects/{project_id}/chapters/{chapter_id}/plan
Content-Type: application/json

{
  "author_intent": "让女主第一次怀疑男主隐瞒父亲死因",
  "expected_role": "escalation",
  "target_word_count": 2200,
  "mock_providers": { "director": ["{...director-plan.v1 JSON...}"] }
}
```

## 维护注意点

- Director 输出顶层必须含 `schema_version: "director-plan.v1"` 与 `prompt_version: "director:v1"`；契约校验在 `agent_runtime.structured_output._validate_director`。
- chapters.status 由 chapter-write 推到 DRAFTED，不在本工作流改变。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4、`docs/agents/agent-contracts-v0.md` §3、`docs/agents/prompts/director-v1.md`。