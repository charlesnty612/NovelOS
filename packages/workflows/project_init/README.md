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

PAUSED 时 `GET /api/runs/{run_id}` 响应体同时附 `stage_models`：按 agent 名（`premise_designer` / `world_builder` / `character_designer` / `volume_outliner`）聚合 `ai_call_logs` 最新一次成功调用的 `model_id`（`error IS NULL` 或 `warn:` 前缀软告警，如重试成功/observer 越权剥离），用于审阅卡片区分「下拉显示的全局绑定」与「本次实际使用的模型」。非 PAUSED 不携带。

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

## 单次 run 模型档案覆盖

`POST /api/projects/init` 请求体支持可选字段 `model_profile_id: str | None`：

```json
{
  "brief": { "genre": "玄幻", "logline": "故事简介" },
  "model_profile_id": "mprof_x"
}
```

- 显式提供 `model_profile_id`：启动时校验该档案存在于 `model_profiles` 表；本次初始化的 4 个 AI 节点（`premise_designer`、`world_builder`、`character_designer`、`volume_outliner`）统一使用该档案调用 LLM；不存在返回 400。
- 省略、`null` 或空字符串：不注入覆盖字段，4 个节点继续走全局 `capability_bindings` / `model_configs`。
- 该字段是 run 级局部上下文，不修改 `capability_bindings`，也不改变其他工作流或其他 run 的模型选择。
- mock_script 存在时，LLM 仍由 mock 输出驱动；模型档案选择不会改变既有 mock 行为。

## 环节完成状态查询（V3.x init-status）

`GET /api/projects/{project_id}/init-status` 返回四环节各自完成状态（数据推导，不建新表）：

```json
{
  "stages": [
    {"stage": "premise",   "label": "题材定位",       "done": true,  "detail": "已有 premise 文本"},
    {"stage": "world",     "label": "世界观",          "done": true,  "detail": "已有 1 个位置、1 个势力、1 条规则"},
    {"stage": "character", "label": "核心角色",        "done": true,  "detail": "已有 3 个角色"},
    {"stage": "outline",   "label": "卷纲与章节种子", "done": true,  "detail": "已有 1 卷 / 10 章"}
  ],
  "has_any_data": true
}
```

判定口径：

| stage      | done 条件                                                                          |
|------------|------------------------------------------------------------------------------------|
| premise    | `projects.premise` 非空                                                              |
| world      | `locations` / `factions` / `world_rules` 任一表该 project 有行                       |
| character  | `characters` 表该 project 有行                                                      |
| outline    | `volumes` 表有行 且 `chapters` 有行                                                  |

`project_id` 不存在 → 404。

## 环节可选复用（V3.x selected_stages）

`POST /api/projects/init` 请求体新增字段 `selected_stages: list[str] | None`：

- 省略 / `null`：等价于 `["premise", "world", "character", "outline"]`，全选（与既有行为一致）。
- 传入非空列表：仅跑白名单内的 AI 节点；**未选环节不调 AI、不抛 `PauseRequested`**，
  从落库数据**重建为下游 AI 节点的输入**（同构结构，含 `_degraded` 字段）。
- 非法值（含不在白名单的元素）→ 422；空列表 → 422。

重建口径（与 AI 产出同构）：

| stage      | 重建来源                                                                                  |
|------------|-------------------------------------------------------------------------------------------|
| premise    | `projects.name / genre / premise / target_words`                                            |
| world      | `locations` / `factions` / `world_rules`（按 `created_at` 顺序）                            |
| character  | `characters`（按 `created_at` 顺序，核心字段为 `name / role / core_json`）                  |
| outline    | `volumes`（取 `number` 最小的）+ `chapters`（按 `number` 升序；`plan_json.chapter_goal → one_sentence`、`plan_json.expected_role → role`、`plan_json.key_beats → key_beats`） |

适用场景：

- 第一轮初始化完成四环节但因故放弃（生成内容已落库）→ 重发起 `selected_stages=["outline"]`
  仅重跑 outline，其余三关从落库重建为下游 AI 的输入；
- 与 `step_mode` 不冲突：被选的环节在 `step_mode=true` 时仍按规则暂停。

## 部分生成与占位数据跳过

部分生成（`selected_stages` 仅含子集环节）时，未选环节的输出走
`_rebuild_stage_from_db` 从落库数据重建为下游 AI 输入。当重建结果
`_degraded=True`（DB 里也没有可重建内容）时，`_persist_all_node` 主动跳过对应写入：

- `outline._degraded=True`：跳过 volume upsert / chapters 重建序列 / plot_event。
  返回结构 `outline_skipped=True`、`volume_id=None`、`chapter_ids=[]`、`event_id=None`；
  DB 该 project 的 `volumes` / `chapters` / `plot_events` 表行数保持 0，绝不写
  占位空卷 / 占位章节 / 占位 plot_event。`_normalize_chapter_seeds` 的
  `_fallback_chapter_seeds` 兜底仍保留用于全量生成 AI 输出为空时的兼容路径。
- `premise._degraded=True` 且 `ctx["project_id"]` 已存在：跳过 `projects` 行
  update（保留用户原始 premise / name）。`project_id` 缺失的新建项目分支
  仍按 brief 创建挂载点（character / world 也需要 project_id 挂载）。
- `selected_stages` 不含 `premise` 时**无论重建结果如何**都跳过 `projects`
  行 update（防复合文本套娃污染）。理由：`_rebuild_premise_from_db` 从
  `projects.premise`（库中已存的「定位：…卖点：…一句话：…」复合文本）重建
  的 `positioning` 是整段复合文本，若再走 `_build_premise_text` 落库会叠一
  层「定位：」+「一句话：」前缀形成套娃。仅当 premise 环节本次真跑了
  （`_stage_selected(ctx, "premise") is True`）才允许 `project_svc.update`。
  配合 `_build_premise_text` 内层防御（positioning 已带「定位：」则剥一层），
  任何残余路径都不会再叠前缀。
- character / world 重建结果空（`characters` / `locations` / `factions` /
  `world_rules` 无行）：循环本身为 no-op，无副作用，无需特判。

`apps/web/src/components/ProjectInitPanel.tsx` 的「本次生成的环节」多选框对所有
环节开放勾选/取消：未 done 默认勾选、done 默认不勾，用户可按需取消；提交时
`payload.selected_stages` 始终透传给后端。

## 依赖
- 上游：`packages/domain/*`、`packages/core/agent_runtime/`、`packages/core/workflow_runtime/`。
- 节点 fn 抛 `packages.core.workflow_runtime.engine.PauseRequested`。

## 使用 / 入口
- `POST /api/projects/init`（`packages/core/api/routers/workflows.py` `_start_project_init`）。
- `GET /api/projects/{project_id}/init-status`。

## 维护注意点
- 项目 ID 格式 `prj_<ulid>`，由 `ProjectService` 生成。
- AI 节点失败降级不阻断；`persist_all` 失败直接抛 5xx，不降级。
- step_mode 关卡次序与节点次序一致；`stage_index` 0..3。
- 修订必须为完整 dict（不是补丁）；下游不深合并，只替换。
- selected_stages 跳过分支直接 `return`（不进 run_agent 也不抛 PauseRequested），
  因此 `workflow_run_nodes.output_json` 在重建节点处缺空（端到端用例的副产物）。
- 权威文档：PRD §67（projects 表）；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 1。