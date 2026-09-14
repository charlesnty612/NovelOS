# workflows.chapter_plan（章节规划）

> 职责：调 **director_planner** agent 一次产出「导演计划 + 场景计划」双契约，导演段写入
> `chapters.plan_json`、场景段写入 `chapter_scene_plans`（同 run 同事务）。
> 状态：Sprint 4-A 已实现；P1 规划合并（2026-09-14）把原 director 单节点升级为
> director_planner 合并调用（原 chapter-write 的 scene_planner 一次调用被吸收）。

## 节点列表

| node_id | kind | 说明 |
|---|---|---|
| `build_ctx` | Transform | 调 `planner_input.build_director_planner_input` 组装合并输入（director 段 `build_director_input` ∪ 规划期段 `collect_planner_context`：available_characters / available_locations / style_constraints / recent_prose ∪ 题材包 planner 段 ratio_declarations + ratio_instruction） |
| `director_planner` | AI | 调 director_planner agent（`run_agent(..., expected="director_planner", mock_script=...)`），一次输出导演计划 + `scene_plan` 子对象；子契约 / 输入 ID 白名单命中 → 既有 output-invalid 重试 |
| `save_plan` | State | 导演段 → `chapters.plan_json`；`scene_plan` → `chapter_scene_plans`（UPSERT，1 章 1 面）；本轮无 scene_plan 时删除旧行（防陈旧场景配新计划）；chapters.status 不动（保持 PLANNED） |

注册名：`chapter-plan`

## 输入 / 输出

- 输入：`chapter_id` + `author_intent`（POST `/api/projects/{pid}/chapters/{cid}/plan` body）。
- 输出：`run_id` + status。`COMPLETED` 时 `chapters.plan_json` 已落库（status 仍 PLANNED）；
  若本轮输出含 scene_plan，则 `chapter_scene_plans` 同步落库（chapter-write 命中即跳过
  自身 scene_planner 调用）。

## 失败语义

- LLM 输出不合规（run_agent 内部重试 1 次后仍失败）→ run FAILED（与改造前 director 同口径）。
- **计划-only 降级**：输出缺 `scene_plan`（prompt 要求双契约，但计划可用）→ 计划照常落库、
  不写 scene 行、节点标 `scene_plan_status='missing_in_output'`
  （chapter-write 走既有 scene_planner 路径补齐场景）——不把用户的一次「生成计划」判失败。
- 节点异常 → run FAILED。

## 依赖

- `packages/core/context_engine/`（director 段装配）
- `packages/core/agent_runtime/`（run_agent / 契约校验 / PromptRegistry）
- `packages/core/workflow_runtime/`
- `packages/workflows/chapter_write/pipeline.py`（scene_planner 降级节点复用本包
  `collect_planner_context`，保证两条链装配口径一致）

## 使用 / 入口

API：

```http
POST /api/projects/{project_id}/chapters/{chapter_id}/plan
Content-Type: application/json

{
  "author_intent": "让女主第一次怀疑男主隐瞒父亲死因",
  "expected_role": "escalation",
  "target_word_count": 3000,
  "mock_providers": { "director_planner": ["{...director-plan.v1 + scene_plan JSON...}"] }
}
```

mock 通道：键 `director_planner` 优先；**旧键 `director` 只读兼容**（既有测试 / eval
golden / smoke 脚本以 `director` 提供计划脚本，计划-only 形态走上面的降级路径）。

## 维护注意点

- 输出顶层必须含 `schema_version: "director-plan.v1"`；`scene_plan` 子对象在场时必须满足
  场景契约（`schema_version: "scene-plan.v1"`）。契约校验在
  `agent_runtime.structured_output._validate_director_planner`。
- `hook_handling[].hook_id` / `debt_handling[].debt_id` 必须 ∈ 输入
  `hook_ledger_excerpt` / `narrative_debt_excerpt`；**输入为空 ⇒ 输出必须 `[]`**（白名单
  命中即重试）。beats / scenes / slots 的 character / location ID 同理受 `available_*` 约束。
- chapters.status 由 chapter-write 推到 DRAFTED，不在本工作流改变。
- 权威文档：`docs/agents/prompts/director_planner-v1.md`（合并 prompt 定稿）、
  `docs/agents/agent-contracts-v0.md` §3（导演契约）、`docs/impl/IMPLEMENTATION-PLAN-v0.md`
  §2 Sprint 4（历史计划）。
