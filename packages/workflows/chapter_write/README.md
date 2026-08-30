# workflows.chapter_write（章节写作）

> 职责：按 Director Plan + Scene Plan 调用 Writer 生成本章 prose，写入 `drafts` 表；chapter status PLANNED→DRAFTED。
> 状态：Sprint 4-A 已实现；P0 新增 Scene Planner AI 节点替代原 stub。

## 节点列表

| node_id | kind | 说明 |
|---|---|---|
| `load_plan` | Transform | 读 `chapters.plan_json` 准备 director_plan 输入；若 plan_json 为空 → 抛错 |
| `scene_planner` | AI | P0 新增：调 `scene_planner` agent 把 `director_plan` 翻译为结构化 Scene Plan（含 `scenes[]` / slots / conflict / turn / information_boundary / ending_hook）。支持 `mock_providers['scene_planner']`；任何失败降级到原 stub 机械映射逻辑，**不**阻断 writer run |
| `writer` | AI | 调 Writer agent（`run_agent(..., expected="writer", mock_script=...)`），输出 `writer-output.v1` JSON（prose + self_report） |
| `save_draft` | State | 写 `drafts` 表（version 自增）+ `chapters.status` PLANNED→DRAFTED |

注册名：`chapter-write`

## 输入 / 输出

- 输入：`chapter_id`（必须有 plan_json）。
- 输出：`run_id` + status。`COMPLETED` 时 drafts 表新增 1 行 + chapters.status=DRAFTED。

## 失败语义

- 缺 plan_json → 抛错（run FAILED）。
- scene_planner 失败（prompt 缺失 / provider 异常 / 输出不合规）→ 降级到 stub，writer 继续执行；run 终态不受影响。
- writer LLM 输出不合规 → run FAILED。
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
  "mock_providers": {
    "scene_planner": ["{...scene-plan.v1 JSON...}"],
    "writer": ["{...writer-output.v1 JSON...}"]
  }
}
```

## 维护注意点

- Scene Planner 输出 schema 为 `{"scenes": [...]}`，字段与旧 stub 兼容（`scene_id` / `purpose` / `characters` / `location` / `conflict` / `turn` / `time_in_story` / `pov` / `pov_character_id` / `slots`），下游 `build_writer_input` 无需改动。
- Writer 输出 `prose` 写入 `drafts.content`；同 chapter 多次 write 会追加 version。
- chapters.status PLANNED→DRAFTED 由 Service 走白名单；当前状态非 PLANNED 时不会自动跳变。
- 允许对 `DRAFTED` 章节重跑 `write` 以追加新 draft 版本（支撑人工改稿后重写的最小闭环）；`REVIEWED` / `COMMITTED` 章节重跑 `write` 会被拒绝（run FAILED，见 `packages/workflows/chapter_write/pipeline.py` `_save_draft_node` 硬校验）。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4、`docs/agents/agent-contracts-v0.md` §4、`docs/agents/prompts/writer-v1.md`、`docs/agents/prompts/scene_planner-v1.md`。

## 模型路由（能力 → capability）

| 节点 | 默认 capability | 说明 |
|---|---|---|
| `writer`（revise 模式：revision_note + 既有 draft） | `light` | 与 critic / summarizer 共用轻量模型，省创作额度；适用于定向局部修改 |
| `writer`（write / fresh_write 模式） | `creative_writing`（默认） | 全章重写走创作型模型，保持文风上限 |
| `polisher` | `creative_writing`（默认） | 不做能力覆盖，保持原行为 |
| `scene_planner` | `creative_writing`（默认） | 不做能力覆盖，保持原行为 |

`writer` 节点的 `revise` / `write` 由 `_writer_node` 在算出 `mode` 后决定是否传 `capability_override="light"`——`None` 时维持原 `capability_for("writer")` 链路，行为对其他节点零影响。

注意：run 级 `model_overrides`（profile_id）始终优先于 capability 绑定；profile_id 传的是单档案钉死，优先级最高（见 `packages/core/agent_runtime/runner.py` `run_agent(..., profile_id=...)`）。`mock_script` 路径不消费 `capability_override`，mock 测试链路不受影响。
