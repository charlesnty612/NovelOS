# workflows.chapter_commit（章节提交）

> 职责：Observer 抽取 State Delta 业务载荷 → 注入元信息 → submit_delta → (HIGH) Human Approval → commit_delta。chapters.status REVIEWED→COMMITTED。
> 状态：Sprint 4-A 已实现。

## 节点列表

| node_id | kind | 说明 |
|---|---|---|
| `build_observer_ctx` | Transform | 调 `context_engine.build_observer_input` 组装 Observer 输入（含 previous_state / draft_text / director_plan_summary） |
| `observer` | AI | 调 Observer agent（`run_agent(..., expected="observer", mock_script=...)`），输出 7 个 change 数组（无元信息） |
| `inject_validate` | Transform | 注入 10 元信息字段（delta_id / schema_version / workflow_run_id / previous_state_version / created_by="observer:v1" / created_at 等）；调 `StoryStateService.submit_delta`；校验失败 → run FAILED |
| `high_risk_approval` | Human | **仅当 observer_payload 含 HIGH / character facet=definition / world_kind=rule 任一时暂停**；payload 含 change 清单；`human_input={"approved": true}` 通过 |
| `commit` | State | 调 `StoryStateService.commit_delta`；chapters.status REVIEWED→COMMITTED；当前 DRAFTED（未过 review）→ run FAILED |

注册名：`chapter-commit`

## 输入 / 输出

- 输入：`chapter_id`（必须 chapters.status=REVIEWED）。
- 输出：`run_id` + status。无 HIGH 时直接 COMPLETED；含 HIGH 时 PAUSED（payload 含 delta_id + change 清单），resume approved → COMPLETED + status=COMMITTED。

## 失败语义

- chapters.status 非 REVIEWED → run FAILED。
- Observer 输出 7 数组缺一或 schema 校验失败 → run FAILED。
- HIGH 风险变更未通过 author_approval → run FAILED。
- 乐观锁冲突 → run FAILED。

## 依赖

- `packages/core/context_engine/builders.build_observer_input`
- `packages/core/agent_runtime/`
- `packages/core/story_state/`
- `packages/core/workflow_runtime/`

## 使用 / 入口

API：

```http
POST /api/projects/{project_id}/chapters/{chapter_id}/commit
Content-Type: application/json

{
  "mock_providers": {
    "observer": ["{...7 数组 JSON（无元信息）...}"]
  }
}
```

无 HIGH 时返回 `{run_id, status: "COMPLETED"}`；含 HIGH 时返回 `{run_id, status: "PAUSED", pause_payload: {...}}`。

## 维护注意点

- Observer 输出必须仅含 7 数组（`character_changes` / `world_changes` / `relationship_changes` / `new_events` / `resolved_hooks` / `new_hooks` / `debt_changes`）；元信息由本工作流 `inject_validate` 节点统一注入。
- HIGH 触发条件：任意 change `risk_level == "HIGH"`，或 character_changes 中 `facet == "definition"`，或 world_changes 中 `world_kind == "rule"`。
- `state_version` 严格 +1/commit（`docs/state-model/state-delta-v0.md` §6.2）；任何异常路径不会污染 state。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4、`docs/agents/agent-contracts-v0.md` §5、`docs/state-model/state-delta-v0.md`。