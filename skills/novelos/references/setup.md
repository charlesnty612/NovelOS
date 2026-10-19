# setup —— 建项目 / 题材包 / 模型配置 / 写作圣经

> **读法**：本文件是「开新书」阶段的唯一依据。**必须读到 EOF**（含末尾「本轮约束」）。
> 端点名与请求体字段**逐字**取自本仓 `packages/core/api/routers/`。

## 0. 阶段地图

```
doctor/ensure → 建项目 → （可选）绑题材包 → 配模型 → 写写作圣经 → 建卷/章 → plan
```

**顺序不是强制的**，但先配模型再跑任何 AI workflow——否则 model_router 找不到
可用档案，run 会以 provider 错误收场（`run status` 的 `error` 字段会写明）。

---

## 1. 探测服务

```bash
python skills/novelos/scripts/novelos.py doctor
python skills/novelos/scripts/novelos.py ensure     # 不在跑时拉起
```

`doctor` 输出 `{"ok":true,"health":{"status":"ok","version":"3.9.0","tables":39}}`。
`tables` 是**业务表数**（已扣除 `_migrations`），不是物理表数。

---

## 2. 建项目

**REST**：`POST /api/projects` → 201，响应是 project 行（含 `project_id`）。

**CLI**：

```bash
python skills/novelos/scripts/novelos.py project create \
  --name "新书名" \
  --genre "玄幻" \
  --premise "少年得古籍，逆天改命" \
  --target-words 300000 \
  --writing-bible "第一人称；禁用「的命运」；灵石一律用「灵石」不用「元石」" \
  --fast-mode
```

`ProjectCreate` 可选字段（全部可省）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `name` | str(1~200) | **必填**，书名 |
| `premise` | str | 一句话简介 |
| `genre` | str(≤80) | 题材 |
| `target_words` | int | 全书目标字数 |
| `writing_bible` | str | **项目级写作圣经**（跨章长期铁律） |
| `fast_mode` | bool? | 快通道三态：`true`开 / `false`显式关 / 省略=未设置 |
| `word_band` | dict? | 字数带覆盖：`low_ratio` / `high_ratio` / `floor` 任选 |
| `foreshadow_overdue_chapters` | int(≥1) | 伏笔 overdue 阈值（章数），默认 30 |

`POST` 之外还有 `GET /api/projects`（默认不含归档；`?include_archived=true` 全量）、
`GET /api/projects/{id}`、`PATCH /api/projects/{id}`、`DELETE /api/projects/{id}`
（有子记录时 409）。

### 写作圣经 vs 运行期 author_intent（**别搞混**）

- `projects.writing_bible` = **项目级基线**，跨章长期有效。
- 请求体的 `author_intent` = **本次运行的增量**（单次）。
- 两者同时非空时，服务端**拼接**（圣经在前、增量在后，并声明冲突以增量为准）。
- 两者都空 ⇒ 装配 payload 里**不出现** `author_intent` 段。

⇒ **作者的铁律应该写进 `writing_bible`**，而不是每次 `plan`/`write` 手动粘贴。
用 `project show --project prj_x` 可以核对当前圣经。

---

## 3. 题材包（genre pack）

题材包是**可复用的体裁约束包**（爽点节奏、桥段公式、禁写项…），绑定到项目后
才会进 writer 的装配 payload。

| 动作 | REST | CLI |
|---|---|---|
| 列出全部 | `GET /api/genre-packs`（`?genre_tag=` 精确过滤） | 走 `run_in_terminal` 之外的裸调用即可（`novelos.py` 未封装） |
| 看全文 | `GET /api/genre-packs/{pack_id}` | 同上 |
| 绑定到项目 | `POST /api/projects/{id}/genre-pack/bind`，body `{"pack_id":"..."}` | 同上 |
| 解绑 | `POST /api/projects/{id}/genre-pack/unbind`（幂等） | 同上 |
| 看绑定 | `GET /api/projects/{id}/genre-pack` → `{"project_id","pack_id","bound","pack"}` | 同上 |

> **注意**：`novelos.py` **没有** genre 子命令——用 `curl` 或在 UI 里做。
> 这是刻意的：题材包是**低频**操作，而薄客户端只封装高频闭环。
> 端点已逐字核对：`packages/core/api/routers/genre.py`。

绑定后，`GET /api/projects/{id}` 会带上 `genre_pack_id`。

---

## 4. 模型配置（**先做这步，否则 AI 环节必失败**）

### 4.1 建模型档案

**REST**：`POST /api/model-profiles`，body：

```json
{
  "name": "主力写作模型",
  "provider": "openai_compatible",
  "model": "your-model-id",
  "params_json": {"base_url": "https://…", "api_key": "sk-…", "temperature": 0.8}
}
```

必填 `name / provider / model`；`params_json`（旧契约）或 `params`（新契约）二选一。
`enabled` 省略 = 1。
**密钥只写不读**：`api_key` 落库后所有 GET 返回 `***`，另给 `has_api_key` 布尔位。

其它端点：`GET /api/model-profiles`（`?include_enabled_only=true`）、
`GET|PATCH|DELETE /api/model-profiles/{profile_id}`、
`POST /api/model-profiles/{profile_id}/test`（**真发一次模型请求，会烧额度**）、
`POST /api/model-profiles/available-models`（列该 key 实际可用的模型）。

### 4.2 绑定到「环节」（capability）

**REST**：`GET /api/capability-bindings`（列出全部 8 个环节，含未绑定的）、
`PUT /api/capability-bindings/{capability}` body `{"profile_ids":["mprofile_x"]}`、
`DELETE /api/capability-bindings/{capability}`。

8 个环节（`packages/core/model_router/router.py::CAPABILITY_LABELS`）：

| capability | 中文 | 覆盖的 agent |
|---|---|---|
| `premise_design` | 题材定位 | `premise_designer` |
| `world_building` | 世界观 | `world_builder` |
| `character_design` | 角色设计 | `character_designer` |
| `volume_outline` | 卷纲 | `volume_outliner` |
| `creative_writing` | 正文写作 | `writer` / `polisher` / `scene_planner` / `director_planner` |
| `reasoning` | 推理规划 | `director` / `arbiter` / `deconstructor_*` / `deep_reviewer` |
| `observer` | 状态提取 | `observer` |
| `light` | 轻量评审 | `summarizer` / `critic` / `writer(revise·改稿)` |

`PUT` 时 `profile_ids` 至少 1 个、必须存在且 `enabled=1`；未知 capability → 404。

> **没有绑定的环节**在 `light` 上会**自动回退**到 `reasoning` 链；其它环节没绑会
> 直接失败（run 报 provider 错误）。⇒ **至少把 `creative_writing` 绑上**。

### 4.3 旧接口（兼容期）

`POST|GET|PATCH|DELETE /api/model-configs`、`POST /api/model-configs/{id}/test`。
`GET /api/capability-bindings` 的每一行带 `legacy_available` 字段提示是否还有旧配置。
**新配置一律走 model_profiles + capability_bindings。**

### 4.4 单次运行覆盖

任何起跑端点可带 `model_overrides={"creative_writing":"mprofile_x"}`（`novelos.py`
**未封装**这一项；需要时用 `run_in_terminal` 之外的手写请求）。
只影响本次 run，不改全局绑定。

---

## 5. AI 初始化（project-init）

**REST**：`POST /api/projects/init`，body：

```json
{
  "brief": {
    "genre": "玄幻", "logline": "少年得古籍，逆天改命", "platform": "起点",
    "target_words": 300000, "title": "九天星辰诀", "author_notes": "快节奏，爽文"
  },
  "chapter_seed_count": 10,
  "step_mode": false,
  "selected_stages": ["premise", "world", "character", "outline"]
}
```

- 同步执行；响应 `{"run_id","status","current_node","project_id"[,"pause_payload"]}`。
  `status` 可能是 `RUNNING` / `PAUSED` / `COMPLETED` / `FAILED`。
- `step_mode=true` ⇒ 跑完一个环节就 `PAUSED` 等人工审阅；用
  `POST /api/runs/{run_id}/resume`，`human_input={"revisions":{"<output_key>":{…}}}` 回灌修订。
- `selected_stages` 白名单：`premise` / `world` / `character` / `outline`；
  未选环节**从落库数据重建**为下游输入（不调 AI）。
- `chapter_seed_count` clamp 到 `[10,500]`（下限 10 是实现约束，低于 10 会被抬到 10）。
- `model_profile_id` ⇒ 本次初始化 4 个 AI 节点统一用它。

**查各环节是否已落库**：`GET /api/projects/{id}/init-status` → 4 行
（`premise` / `world` / `character` / `outline`），各带 `done` / `detail`。
**这是数据推导，不是新表** ⇒ 判据是「表里有没有行」。

> `project-init` 会**真的调模型**（4 个 agent）⇒ **烧额度**。
> 这是产品正常用途，但**由作者决定要不要跑**。

---

## 6. 建卷与章节

- 建卷：`POST /api/projects/{id}/volumes`（`novelos.py` 未封装）。
- 建章：`POST /api/projects/{id}/chapters`，body `{"number":3,"title":"…","plan_json":{}}`。
  `number` ≥ 1，同项目内**重复章号 409**。

**CLI**（查表，不建）：

```bash
python skills/novelos/scripts/novelos.py chapter list --project prj_x
```

> `plan_json` = **生成面**（planner 覆盖写）；`outline_json` = **策展面**（人/脚本写，
> chapter-plan **不写**它）。人工大纲请写 `outline_json` ⇒ 它会进 planner 的输入
> （`chapters.outline_json` 有独立的缓存键维度，改它必定 miss 缓存）。

---

## 本轮约束

1. **先 `doctor` 后动手**；不通就 `ensure`；`ensure` 失败 ⇒ 把错误原文交给用户，不要猜。
2. **先配模型再跑 AI 环节**；至少绑 `creative_writing`。
3. **铁律写进 `writing_bible`**，不要每次手贴 `author_intent`（漏一次即静默丢失）。
4. **人工大纲写 `outline_json`，不写 `plan_json`**（后者会被 planner 覆盖销毁）。
5. **project-init 会烧额度**（4 个真实 LLM 调用）⇒ 跑之前确认作者要跑。
6. `POST /api/model-profiles/{id}/test` **会发真实模型请求** ⇒ 别拿它当健康检查。
7. **不要用 `novelos.py` 之外的手拼请求去写业务规则**；需要未封装的端点时，
   照着 `packages/core/api/routers/` 的源码逐字核对路径与请求体。
