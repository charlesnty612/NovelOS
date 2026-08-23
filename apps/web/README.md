# NovelOS Web（Sprint 5 一期 + 二期）

NovelOS 的本地 Web 前端。

- 一期：项目 / Story Bible / 总览的基础 CRUD 与查看能力。
- 二期（本任务）：章节列表 + 章节详情（计划 / 草稿 / Workflow / 审批）+ AI 设置（模型路由
  + Agent prompt 管理）。

## 职责

- 提供一个面向小说作者的可视化操作面板；
- 通过 `/api` 代理调用 `packages/core/api` 后端（FastAPI）；
- 完整覆盖 Sprint 1～4 已经稳定下来的契约：projects / characters / locations / factions /
  world_rules / events / timeline / state / commits / health。

## 技术栈

| 项 | 选型 | 说明 |
| --- | --- | --- |
| 构建 | Vite 5 | 启动快、配置最小 |
| 语言 | TypeScript 5.5（strict） | `tsc -b` 在 build 中强制类型检查 |
| UI | React 18 | 函数组件 + Hooks |
| 路由 | react-router-dom v6 | `BrowserRouter` + 嵌套路由 |
| 状态 | React 自带 `useState`/`useContext` | **不**引入 redux/zustand，保持轻量 |
| 样式 | 纯 CSS（`src/index.css`） | 设计变量 + 组件 className，**不**引 Tailwind/组件库 |
| 测试 | vitest + @testing-library/react + jsdom | `npm run test` 走 vitest run |

## 开发命令

```bash
# 安装依赖（仅一次）
npm install

# 开发服务器（默认 http://127.0.0.1:5174 ，/api 代理到 http://127.0.0.1:18081 ）
npm run dev

# 类型检查 + 生产构建（CI 上必须通过）
npm run build

# 单元测试（vitest run）
npm run test

# 预览构建产物
npm run preview
```

> 后端开发服务器需单独启动：
>
> - `python -m packages.core.api.main`：入口默认端口 **18081**（8000 开发者本机常被占用），
>   `NOVELOS_PORT` 环境变量覆盖（也可用 `NOVELOS_API_PORT`，向后兼容旧精细变量）；
> - `python scripts/serve.py`：使用 `packages/core/config.py` 的 `settings.api_port`，
>   **默认端口 8000**（优先级 `NOVELOS_PORT` > `NOVELOS_API_PORT` > 8000）；
> - 前端的 `/api` 代理目标固定 `http://127.0.0.1:18081`（见 `vite.config.ts`），
>   因此若用 `scripts/serve.py` 启动需 `NOVELOS_PORT=18081 python scripts/serve.py`。
>
> 前端开发期 `/api` 代理会自动转发到后端。

## 目录结构

```
apps/web/
├── index.html                 # Vite 入口
├── package.json               # 依赖 + scripts
├── tsconfig.json              # 仅 references
├── tsconfig.app.json          # 应用编译（strict）
├── tsconfig.node.json         # vite.config.ts 编译
├── vite.config.ts             # Vite + vitest 配置
├── .gitignore
├── README.md                  # 本文件
└── src/
    ├── main.tsx               # React 入口（BrowserRouter）
    ├── App.tsx                # 路由表
    ├── index.css              # 设计变量 + 全局样式
    ├── api/
    │   ├── client.ts          # fetch 封装 + ApiError + coerceJson
    │   ├── endpoints.ts       # 各业务端点的薄封装
    │   ├── types.ts           # 与后端 Pydantic 对齐的 TS 类型
    │   └── client.test.ts     # api client 错误处理测试
    ├── hooks/
    │   ├── useApiCall.ts      # 通用「加载一段异步数据」hook
    │   ├── usePoll.ts         # 通用 setInterval 轮询 hook（自动停止）
    │   └── usePoll.test.ts
    ├── utils/
    │   ├── format.ts          # 日期/JSON 格式化 + JSON 解析校验
    │   ├── format.test.ts
    │   ├── chapterState.ts    # 章节状态机按钮可用性 + run 过滤（纯函数）
    │   └── chapterState.test.ts
    ├── components/
    │   ├── ErrorBanner.tsx    # 错误/信息条
    │   ├── EmptyState.tsx     # 空态
    │   ├── StatusBadge.tsx    # 项目状态徽标
    │   ├── StatusBadge.test.tsx
    │   ├── EmptyState.test.tsx
    │   ├── ChapterStatusBadge.tsx   # 章节状态 / workflow run 状态徽标
    │   ├── ApprovalCard.tsx          # Human 节点审批卡片（review / high_risk_approval）
    │   └── ApprovalCard.test.tsx
    ├── layout/
    │   └── Layout.tsx         # 左侧导航 + 顶部栏 + Outlet
    └── pages/
        ├── ProjectsListPage.tsx       # /  项目卡片列表 + 新建/编辑/归档
        ├── ProjectOverviewPage.tsx    # /projects/:pid/overview
        ├── StoryBiblePage.tsx         # /projects/:pid/bible（三个 Tab）
        ├── bible/
        │   ├── CharacterTab.tsx       # 角色：列表 + 详情 + 表单
        │   ├── WorldTab.tsx           # 世界：locations/factions/world-rules
        │   └── PlotTab.tsx            # 剧情：events + timeline
        ├── ChaptersPage.tsx           # /projects/:pid/chapters  章节列表 + 新建
        ├── ChapterDetailPage.tsx      # /projects/:pid/chapters/:cid
        │                             # 头部状态机按钮 + plan / drafts / workflow 面板
        ├── AiSettingsPage.tsx         # /projects/:pid/ai  模型配置 + agent prompt
        └── NotFoundPage.tsx
```

## Sprint 5 二期 — 章节工作流（详细说明）

### 状态机按钮可用性（与后端 `ALLOWED_NEXT` 对齐）

| 按钮 | 章节状态 | 备注 |
| --- | --- | --- |
| 生成计划 (plan)   | `PLANNED` | 启动 `chapter-plan`，写 `plan_json`，状态不变 |
| 写正文 (write)    | `PLANNED` / `DRAFTED` | 启动 `chapter-write`，生成 draft，PLANNED→DRAFTED |
| 审校 (review)     | `DRAFTED` | 启动 `chapter-review`：basic_checks → author_review (Human) → mark_reviewed |
| 提交 (commit)     | `REVIEWED` | 启动 `chapter-commit`：observer delta → (HIGH) Human Approval → commit |

所有按钮在「当前有 RUNNING/PENDING run」时禁用，强制一次只跑一个工作流。
完整状态机白名单与按钮可用性逻辑放在 `src/utils/chapterState.ts`（纯函数），
便于单测。

### 轮询策略

章节详情页对 RUNNING run 做 **2s 轮询**：
- 用 `usePoll` hook（`src/hooks/usePoll.ts`）：`enabled=true` + `intervalMs=2000` +
  `stopWhen(run) => run.status !== 'RUNNING'`。
- PAUSED / COMPLETED / FAILED / CANCELLED 时自动停止；resume 由用户在审批卡片里触发。
- 每次成功轮询会顺带刷新 chapter、drafts、runs 三个列表。

### 审批流（Human 节点）

后端 `chapter-review` 与 `chapter-commit.high_risk_approval` 都在 Human 节点抛
`PauseRequested`，把 payload 落到 `checkpoint_json[node_id].__pause_payload__`。
`router.start_*` / `router.resume_*` 把它从 checkpoint 反查出来塞进
`pause_payload` 字段返回。

前端 `ApprovalCard` 根据 `payload.stage` 渲染不同视图：
- `chapter-review`：展示 `review_report`（字数 / 目标 / 偏差 / forbidden_word_hits / warnings）。
- `chapter-commit.high_risk_approval`：展示 `changes.character_changes.length +
  changes.world_changes.length`，并说明「批准会继续 COMMIT，驳回则 workflow 进入 FAILED」。
- 用户点批准 / 驳回 → 调 `POST /runs/{id}/resume` body `{human_input:{approved:true|false}}`。

### 人工改稿（drafts）

后端仅 `DRAFTED / REVIEWED` 状态允许 `POST /chapters/{cid}/drafts`，其它状态 → 409。
前端 UI：
- 「人工改稿」按钮在 DRAFTED/REVIEWED 时可点。
- 选中任一版本 → 载入编辑器 → 编辑后 POST → 创建新版本（version 自增）。
- 409 / 其它错误用 `ErrorBanner` 展示后端 `detail`。

### 测试覆盖

- `src/utils/chapterState.test.ts`（37 例）：按钮可用性 / 状态机白名单 / run 过滤 / 高风险变更计数。
- `src/components/ApprovalCard.test.tsx`（7 例）：review_report 渲染 / commit.high_risk_approval 渲染 /
  warnings 列表 / 提交回调。
- `src/components/QualityPanel.test.tsx`（6 例，Sprint 6 下半）：overall 着色阈值（<70 红色 /
  70-85 黄 / >85 绿）、六子分条形宽度、issue 分组（payoff 单列）、evaluate 按钮回调、空态。
- `src/hooks/usePoll.test.ts`（8 例）：enabled 切换 / stopWhen 触发停止 / 错误处理 / 卸载清理 /
  onResult 回调。

### Sprint 6 下半 — 质量评估面板

章节详情页（`pages/ChapterDetailPage.tsx`）在「计划（plan_json）」面板下方挂载 `QualityPanel`：

- 顶部 `运行质量评估` 按钮触发 `POST /chapters/{cid}/quality/evaluate`；调用成功后
  立即刷新面板 + 重拉 chapter（commit 节点可能已用 enforce 模式触发阻断）。
- 主展示区：
  - `overall` 大数字（按阈值染色：<70 红色 `--color-error` / 70-85 黄色
    `--color-warning` / >85 绿色 `--color-success`）。
  - 六子分条形（`plot/character/continuity/style/pacing/foreshadowing`）：纯 CSS div 宽度
    百分比，0-100 区间。
  - `Issues` 列表：severity 着色徽章 + category + rule_id + message + suggestion；
    `category == 'payoff'` 的 issue 单独组成「爽感问题（H-1 ~ H-5）」段落。
  - 底部 `scoring_version` / `formula_hash` / `evaluated_at` 小字脚注（来自
    `scores_json._meta`）。
- 错误处理：evaluate API 报错显示在顶部 ErrorBanner；GET 404（无 report）视作空态
  （「暂无评估报告」），不算错误。
- 数据流：每次 chapter 切换都重拉 `GET /chapters/{cid}/quality`；evaluate 成功后
  直接用返回值更新本地 state（避免再拉一次）。


## 与后端契约对接说明

所有请求统一走 `src/api/client.ts`：

- `baseURL = "/api"`；开发期由 `vite.config.ts` 的 `server.proxy` 转发到
  `http://127.0.0.1:18081`。
- 错误统一抛 `ApiError{status, detail}`；UI 通过 `error.detail` 直接展示。
- 后端对 `core_json / data_json / time_json / cause / effects` 等字段可能返回「字符串或对象」，
  client 层用 `coerceJson` 做容错解析：
  - 已是对象 → 原样；
  - 是合法 JSON 字符串 → `JSON.parse`；
  - 是字符串但解析失败 → 回退原值（不抛错，由调用方决定如何展示）。
- 类型在 `src/api/types.ts` 与后端 Pydantic 模型（`packages/domain/**/models.py`）逐字段对齐。

### 已覆盖端点

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/projects` | 项目列表 |
| POST | `/projects` | 新建项目 |
| GET | `/projects/{id}` | 项目详情 |
| PATCH | `/projects/{id}` | 更新项目（含 status=ARCHIVED 归档） |
| DELETE | `/projects/{id}` | 删除项目（前端本期未暴露，按需在 UI 加） |
| GET | `/projects/{pid}/characters` | 角色列表 |
| POST | `/projects/{pid}/characters` | 新建角色 |
| GET | `/characters/{id}` | 角色详情（列表项已含 latest_state_json） |
| PATCH | `/characters/{id}` | 更新角色 |
| DELETE | `/characters/{id}` | 删除角色 |
| GET | `/characters/{id}/states` | 角色 state 历史（暂未挂 UI，预留） |
| GET/POST | `/projects/{pid}/locations` | 地点 |
| GET/PATCH/DELETE | `/locations/{id}` | 地点 |
| GET/POST | `/projects/{pid}/factions` | 势力 |
| GET/PATCH/DELETE | `/factions/{id}` | 势力 |
| GET/POST | `/projects/{pid}/world-rules` | 世界规则 |
| GET/PATCH/DELETE | `/world-rules/{id}` | 世界规则 |
| GET/POST | `/projects/{pid}/events` | 剧情事件 |
| GET/PATCH/DELETE | `/events/{id}` | 事件 |
| GET | `/projects/{pid}/timeline` | 时间线 |
| GET | `/projects/{pid}/state` | 当前快照（overview 摘要） |
| GET | `/projects/{pid}/commits` | commit 列表（用于版本号兜底） |
| GET | `/api/health` | 后端健康检查 |
| GET/POST | `/projects/{pid}/chapters` | 章节列表 / 新建（number ASC） |
| GET/PATCH/DELETE | `/chapters/{id}` | 章节详情 / 更新（plan_json 或 status 迁移） |
| GET | `/chapters/{cid}/drafts` | 草稿列表（version DESC） |
| POST | `/chapters/{cid}/drafts` | 新建人工 draft（仅 DRAFTED/REVIEWED → 409） |
| POST | `/projects/{pid}/chapters/{cid}/plan` | 启动 chapter-plan → 201 + run |
| POST | `/projects/{pid}/chapters/{cid}/write` | 启动 chapter-write |
| POST | `/projects/{pid}/chapters/{cid}/review` | 启动 chapter-review |
| POST | `/projects/{pid}/chapters/{cid}/commit` | 启动 chapter-commit |
| GET | `/runs/{run_id}` | 单 run（含节点明细） |
| POST | `/runs/{run_id}/resume` | 恢复 PAUSED run（human_input.approved） |
| GET | `/projects/{pid}/runs` | 项目下全部 run（不含 nodes） |
| GET/POST | `/model-configs` | 模型配置列表 / 新建 |
| GET/PATCH/DELETE | `/model-configs/{id}` | 模型配置详情 / 更新 / 删除 |
| POST | `/model-configs/{id}/test` | 测试连接（latency_ms + preview） |
| GET | `/agents` | 已注册 agent 列表 |
| GET | `/agents/{name}/prompts` | agent 的所有 prompt 版本 |
| POST | `/agents/sync` | 从 `docs/agents/prompts` 同步 |
| GET | `/chapters/{cid}/quality` | 该 chapter 最新一份 QualityReport；尚无报告 → 404 |
| POST | `/chapters/{cid}/quality/evaluate` | 现场组装 ctx + 评估 + 落库，返回 QualityReport（201） |
| GET | `/projects/{pid}/quality` | 项目全部 QualityReport 列表（created_at DESC） |

> 注：本期不修改任何 `packages/` 下 Python 文件。

## 维护注意点

1. **新增页面**统一走 `pages/`，并把对应路由加到 `App.tsx`；左侧导航在 `Layout.tsx` 的
   `projectNav` 数组里维护。
2. **JSON 字段**：任何「后端可能返回字符串也可能返回对象」的字段，调用前请用 `coerceJson`
   包一层；表单回写时用 `tryParseJsonObject` 校验。
3. **状态管理**：本期刻意只引 React 自带 hooks。若未来需要跨页共享（如当前选中项目），可
   新增 `src/contexts/CurrentProjectContext.tsx`，避免一上来就引第三方。
4. **样式**：在 `src/index.css` 维护设计变量与复用类；新页面若需要专属样式，优先复用现有
   `card / btn / tabs / table / form-row / json-block / layout-2col / panel / panel-grid /
   kv-list / prose-block / workflow-actions` 等类。
5. **测试**：至少为新组件 / hook 写一个最小渲染或行为测试；JSON 字段相关的解析逻辑放到
   `utils/format.ts`，状态机 / 按钮可用性放到 `utils/chapterState.ts`，方便单测。
6. **后端 list vs detail 的字段差异**：`/projects/{pid}/runs` 列表只返回 run 主表字段
   （无 nodes），详情 `/runs/{id}` 才有 nodes；前端 UI 选 run 后再拉详情（用 `useApiCall`
   单独 GET），不在列表里展开节点明细。
7. **workflow_name 后端暂未返回**：`workflow_runs.workflow_id` 是 workflow 注册表里的
   主键；前端 UI 用 `run · {run_id 短码}…` + tooltip 展示完整 run_id / workflow_id。
   后续若后端在 list / get 时把 `workflow_name` join 进来，UI 会自动用上（`workflow_name?`
   是可选字段）。
