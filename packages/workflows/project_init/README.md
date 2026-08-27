# workflows.project_init（项目初始化）

> 职责：从 0 到 1 建立项目——创建 `projects` 记录、生成初始 Story Bible（题材定位 / 世界观 / 核心角色 / 卷纲与章节种子）、初始化 `state_version=0`。
> 状态：V3 已落地（P1 sprint）；可选 step_mode 分段审阅暂停（V3.x）。

## 职责与边界
做：
- 读取 brief → 4 个 AI 节点依次产出 `premise_output` / `world_output` / `character_output` / `outline_output` → `persist_all` 落库（projects / characters / locations / factions / world_rules / volumes / chapters）。
- 可选 step_mode：在 4 个 AI 节点完成后各暂停一次，等待人工修订后回灌。

不做：
- 章节正文写作（属 chapter-write pipeline）。
- state 实体写盘（persist_all 不写 story_state；由 chapter-write 首次触发 init_genesis）。

## 节点图

```
load_brief → premise_designer → world_builder → character_designer → volume_outliner → persist_all
```

节点类型：
- `load_brief`（Transform）：规范化 brief、补默认 `chapter_seed_count=10`。
- `premise_designer` / `world_builder` / `character_designer` / `volume_outliner`（AI）：调 `run_agent`，失败降级（不阻断后续）。
- `persist_all`（State）：复用 domain service 落库，异常直接抛错不降级。

AI 输出在 ctx 键：
- `premise_output`（dict，含 `title`/`genre`/`logline`/`positioning`/`selling_points`/`protagonist`）
- `world_output`（`core_premise`/`rules`/`locations`/`factions`）
- `character_output`（`characters: [...]`，每条含 `name`/`role`/`core_json`）
- `outline_output`（`volume`/`chapter_seeds`）

## step_mode 分段审阅暂停（V3.x 新增）

`POST /api/projects/init` 请求体新增字段 `step_mode: bool | None`：

- `step_mode=true`：4 个 AI 节点各完成一次即抛 `PauseRequested`，run 进入 `PAUSED`，等人工审阅；
- 省略 / `false`：保持一次性跑完（与既有行为一致）。

### 4 个关卡

| stage_index | stage       | 节点               | output_key         |
|-------------|-------------|--------------------|--------------------|
| 0           | premise     | premise_designer   | premise_output     |
| 1           | world       | world_builder      | world_output       |
| 2           | character   | character_designer | character_output   |
| 3           | outline     | volume_outliner    | outline_output     |

### pause_payload 契约

PAUSED 时 `POST /api/projects/init` 与 `POST /api/runs/{run_id}/resume` 响应体附 `pause_payload`：

```json
{
  "stage": "premise",
  "stage_index": 0,
  "stages_total": 4,
  "degraded": false,
  "draft": {
    "title": "...", "genre": "...", "logline": "...",
    "positioning": "...", "selling_points": ["..."],
    "protagonist": {"name": "..."},
    "_degraded": false
  }
}
```

### 人工修订回灌

通过通用端点 `POST /api/runs/{run_id}/resume`，body 为 `human_input`：

```json
{
  "human_input": {
    "revisions": {
      "premise_output": {
        "title": "改后书名", "genre": "玄幻", "logline": "修订后的一句话",
        "positioning": "...", "selling_points": ["..."],
        "protagonist": {"name": "叶尘"}
      }
    }
  }
}
```

`revisions` 是「按 output_key 的完整 dict 替换」，不是补丁。`output_key` ∈ `premise_output`/`world_output`/`character_output`/`outline_output`。

不带 `revisions` 的 resume 也合法——会按 `_resolve_stage_input` 第二层（`ctx[output_key]`）兜底继续。

### 修订生效路径

`pipeline._resolve_stage_input(ctx, stage)` 三层 fallback：

1. `ctx["human_input"]["revisions"][output_key]`（人工修订，dict 才采纳）；
2. `ctx[output_key]`（上一节点产出 / 恢复后已落盘）；
3. `ctx[node_id]["__pause_payload__"]["draft"]`（checkpoint 中的挂起草稿）。

下游 `_world_payload` / `_character_payload` / `_outline_payload` / `_persist_all_node` 全部改走该解析函数，确保修订后的 dict 穿透到下游 AI 节点输入与最终落库（projects.name / characters / chapters 等）。

### 引擎不变

- 复用 `POST /api/runs/{run_id}/resume` 通用端点，未改 engine.py / ResumeRequest。
- `mock_providers` 在 checkpoint 中持久化，跨 resume 仍可用（列表模式耗尽后重复末条）。

## 依赖
- 上游：`packages/domain/*`、`packages/core/agent_runtime/`、`packages/core/workflow_runtime/`。
- 节点 fn 抛 `packages.core.workflow_runtime.engine.PauseRequested`。

## 使用 / 入口
- `POST /api/projects/init`（`packages/core/api/routers/workflows.py` `_start_project_init`）。

## 维护注意点
- 项目 ID 格式 `prj_<ulid>`，由 `ProjectService` 生成。
- AI 节点失败降级不阻断；`persist_all` 失败直接抛 5xx，不降级。
- step_mode 关卡次序与节点次序一致；`stage_index` 0..3。
- 修订必须为完整 dict（不是补丁）；下游不深合并，只替换。
- 权威文档：PRD §67（projects 表）；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 1。