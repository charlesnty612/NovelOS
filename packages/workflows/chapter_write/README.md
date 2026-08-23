# workflows.chapter_write（章节写作）

> 职责：按 Director Plan + Scene Plan 调用 Writer 生成本章 prose，写入 `drafts` 表；chapter status PLANNED→DRAFTED。
> 状态：Sprint 4-A 已实现。

## 节点列表

| node_id | kind | 说明 |
|---|---|---|
| `load_plan` | Transform | 读 `chapters.plan_json` 准备 director_plan 输入；若 plan_json 为空 → 抛错 |
| `scene_planner_stub` | Transform | **MVP 占位**：把 `director.key_beats` 逐个映射为 scene，每 scene `slots=[1 个 action]`。**V1 由 Planner Agent 替代** |
| `writer` | AI | 调 Writer agent（`run_agent(..., expected="writer", mock_script=...)`），输出 `writer-output.v1` JSON（prose + self_report） |
| `save_draft` | State | 写 `drafts` 表（version 自增）+ `chapters.status` PLANNED→DRAFTED |

注册名：`chapter-write`

## 输入 / 输出

- 输入：`chapter_id`（必须有 plan_json）。
- 输出：`run_id` + status。`COMPLETED` 时 drafts 表新增 1 行 + chapters.status=DRAFTED。

## 失败语义

- 缺 plan_json → 抛错（run FAILED）。
- LLM 输出不合规 → run FAILED。
- 字数 / 禁用词等 Guardrail 留给后续 Sprint（S6）；本 Sprint 仅做最小契约校验。

## 依赖

- `packages/core/context_engine/builders.build_writer_input`
- `packages/core/agent_runtime/`
- `packages/core/workflow_runtime/`

## 使用 / 入口

API：

```http
POST /api/projects/{project_id}/chapters/{chapter_id}/write
Content-Type: application/json

{
  "mock_providers": { "writer": ["{...writer-output.v1 JSON...}"] }
}
```

## 维护注意点

- Scene Planner 是 **MVP stub**：slots 全为 action 类型，未做对话 / 描写 / 悬念等细分。V1 由 Planner Agent 替换。
- Writer 输出 `prose` 写入 `drafts.content`；同 chapter 多次 write 会追加 version。
- chapters.status PLANNED→DRAFTED 由 Service 走白名单；当前状态非 PLANNED 时不会自动跳变。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4、`docs/agents/agent-contracts-v0.md` §4、`docs/agents/prompts/writer-v1.md`。