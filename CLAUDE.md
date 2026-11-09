# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **本仓的项目记忆在 [`AGENTS.md`](AGENTS.md)**（硬规则、坑区、一致性矩阵，每条挂实证，体量很大）。
> 本文件只是「上手索引」：常用命令、架构大图、最容易踩的纪律。两者冲突时以 `AGENTS.md` 为准。
> 每次迭代必须在 [`CHANGELOG.md`](CHANGELOG.md) 追加条目（格式见该文件头部）。

## 项目是什么

NovelOS = 本地优先、单用户的**小说写作 OS**：Python(FastAPI) 后端 + React 前端 + SQLite 单一事实源，
前后端同仓，后端直接托管前端构建产物（`apps/web/dist`）。

核心闭环：`chapter-plan`（director 计划）→ `chapter-write`（writer 起草）→ `chapter-review`
（人工审校，可 PAUSED）→ `chapter-commit`（observer 抽状态 delta → 质量门禁 → 提交）。
配套：Story State 快照版本化、质量引擎、伏笔/债务台账、拆书、What-if 推演、导出、备份、
对外可分发的 Agent Skill 包（`skills/novelos/`）。

三仓分离：**本仓只放软件**；内容资产在 `NovelOS-Content`（不推 GitHub），操作痕迹在 `NovelOS-Artifacts`。

## 常用命令

前置：Python ≥3.11、Node ≥18。后端命令一律用**项目 `.venv` 解释器**（系统 python 因 editable
元数据缺失会把版本读成 `dev`，造成假漂移）。

```bash
uv sync --extra dev                # 装/同步依赖；不要裸 uv sync（会剪掉 ruff / pytest-xdist）
python scripts/migrate.py          # 迁移（data/novelos.db：42 张业务表 + _migrations）
python -m packages.core.api.main   # 起后端（默认 127.0.0.1:18081，同时托管 SPA）
python scripts/serve.py            # 等价启动器（同一端口配置源）
```

```bash
cd apps/web && npm run dev      # vite dev（/api 代理到后端）
cd apps/web && npm run test     # vitest
cd apps/web && npm run build    # tsc -b && vite build → dist
```

```bash
python -m ruff check packages scripts tests          # lint（tests 豁免 E501）
python -m pytest -n 20 -q                            # 全量（-n 取 min(核数/2, 20)，约 3~4 分钟）
python -m pytest tests/unit/test_migrations.py -v    # 单文件；单目录同理
python scripts/smoke_e2e.py                          # 端到端（临时库 + 独立端口 + mock provider，自清理）
python scripts/tree_fingerprint.py                   # 源码树指纹：跑测前后各一次，逐位一致才算数
python scripts/check_state_sync.py --db data/novelos.db   # 快照↔集合漂移核查（只读）
```

契约产物（必须用项目 `.venv` 跑；`--check` 漂移时 exit 1）：

```bash
python scripts/export_openapi.py --check        # 校验 apps/web/openapi.json 是否与活 schema 漂移
python scripts/export_openapi.py && python scripts/gen_frontend_types.py   # 漂移后 regen
```

突变规格（`tests/mutation_specs/` 是**变异清单，不是测试**）：

```bash
python tests/mutation_specs/validate_specs.py             # 只读预检（CI 也跑）
python tests/mutation_specs/run_spec.py --all --precheck  # 只读预检（~0.2s，批次收尾必跑）
python tests/mutation_specs/run_spec.py --all --changed   # 增量实跑（会临时改源码，必须串行）
python tests/mutation_specs/run_spec.py --all             # 全量（发版前，≈1 小时，不进提交路径）
```

⚠️ 实跑会临时改写源码：执行器在飞时**禁止任何工作区写操作**（含 `git add/commit`、格式化、codegen）。
⚠️ 并发档位（`--jobs`）会造假 CAUGHT / 判定不可复现，退出码恒为 3，不得作为闸门结论。

## 架构大图

### 分层与依赖方向

```
packages/core/       机制层：api(routers) / workflow_runtime / workflow_registry / agent_runtime /
                     model_router / context_engine / quality / story_state / retrieval / genre /
                     simulation / exporter / backup / content_sync / skill / signing_check / delivery
packages/domain/     领域服务：project / chapter / character / world / plot / volume / ledger / ...
packages/workflows/  工作流组装：chapter_plan / write / review / commit / deconstruct_book /
                     outline_batch / project_init / simulation
apps/web/            前端（React 18 + TS + Vite）
database/migrations/ 唯一 DDL 来源（0001~0038，只加不改）
```

- `core` **不得 import `workflows`**（文本级看守：`tests/unit/test_no_core_to_workflows_dep.py`，
  连 docstring 里出现 `packages.workflows` 全限定字面也算违规——写模块相对描述）。
- 唯一允许的装配入口是 `packages/core/api/main.py` 用 `importlib` 触发一次 `packages.workflows`
  导入（插件发现）。
- 工作流注册是**反向依赖**：`workflows/<name>/__init__.py` 调
  `core.workflow_registry.register_workflow(name, lambda: WORKFLOW)`，core 侧只存惰性 builder，
  避免 core → workflows 的静态依赖。

### 工作流引擎（`packages/core/workflow_runtime/engine.py`）

- 工作流 = `WORKFLOW` dict（`name` / `version` / `nodes` / 可选 `checkpoint_exclude`），
  定义在 `packages/workflows/<name>/pipeline.py`，由同包 `__init__.py` 注册。
- 节点 = `WorkflowNode(node_id, kind, fn, agent_name=None)`；`kind ∈ {AI, State, Transform,
  Human, Simulation}`；`fn(ctx) -> dict`，产出 merge 进 `ctx`（同时按 node_id 存一份镜像）。
- 引擎顺序执行：每节点写 `workflow_run_nodes`，每步落 `workflow_runs.checkpoint_json`
  （体积由 `checkpoint_exclude` 控制）；`Human` 节点抛 `PauseRequested(payload)` ⇒ run `PAUSED`；
  `resume` 从 checkpoint 重建 ctx 继续；`cancel_run` 是协作式探针；
  进程重启由 `recover_interrupted_runs` 把残留 RUNNING 收尾为 FAILED。
- 同章活跃 run 守卫：`packages/core/workflow_runtime/guards.py::check_active_run_for_chapter`
  （只拦 RUNNING/PENDING，**PAUSED 不算锁**）。

### Agent 运行时与提示词

- `agent_runtime/runner.py::run_agent(db_path, agent_name, payload, run_id, ...)`：查 ACTIVE prompt
  → 按 capability 路由模型 → 调 provider → 校验/修复结构化输出 → 写 `ai_call_logs`。
- 提示词源文件：`docs/agents/prompts/<agent>-vN.md`；启动时 `PromptRegistry` 同步进 `prompts`
  表（自愈：DB 落后于仓内同名文件时推进到最新版；人工改过的行不动）。
- **版本取自文件名**。改提示词内容必须**新建版本文件**（如 `writer-v6.md`），**不得原地改**——
  否则两套正文共用一个标签，装配缓存与 `ai_call_logs.prompt_version` 再也分不清来源。
- 模型配置三层（`packages/core/model_router/`）：`capability_bindings`（环节 → 有序档案链，
  顺序即 fallback）→ `model_profiles`（模型 / 协议 / 超时）→ `model_connections`
  （网关 base_url / 认证 / headers）。档案绑定连接后，地址与密钥全部取自连接。

### 上下文装配与缓存（`packages/core/context_engine/`）

- `build_director_input` / `build_writer_input` 产出送模型的 payload；装配缓存是模块级 dict，
  键 = `state_version` + 若干**指纹维度**（plan / outline / 角色 / 写作圣经 / 题材包 / skills /
  volume_scope / prompt_label …）。
- 本仓复发最多的缺陷形状，两条必须同时满足：
  1. **凡进 payload 的装配输入必须进缓存键**（漏键 = 脏命中）；
  2. **写该输入的数据路径必须推 `story_states.state_version`**（否则键再全也不会失效）。
- 判别：改数据 ⇒ `state_version` 变 ⇒ 必然 miss，断言打在 payload 实际内容上。

### 数据与迁移

- SQLite。`packages/core/db.py::get_connection` 开外键、WAL（失败降级 rollback journal 并 warning）；
  `apply_migrations` 幂等，默认迁移目录锚定仓库根（不随 CWD 漂移）。
- 迁移**只加不改**；新列可空兼容存量。新增迁移须同步 `tests/unit/test_migrations.py` 的表数断言
  与 `scripts/smoke_e2e.py` 的 `EXPECTED_BUSINESS_TABLES`。
- 库落点有闸：仓内只允许 `data/` 下的库文件（`RepoLocalDbPathError`）。
- Story State：`story_states` 快照 + `state_deltas`；commit 应用 delta，rollback 做逆清理——
  **回滚必须带派生面清单**（timeline / plot / hooks / summaries / 索引…），主表之外不清就继续被消费。

### API 与前端

- 路由自动发现：`packages/core/api/routers/*.py` 里的模块级 `router` 会被挂到 `/api`；
  工作流端点是子包 `routers/workflows/`（control / runs / revise / start / common）。
- 前端类型：`apps/web/src/api/types.ts` 是**手写面**；凡后端有 `response_model` 的一律由
  codegen 生成到 `types.generated.ts` 并重导出（手写与后端之间**没有任何机器校验** ⇒ 能生成就生成）。
- 端点封装 `apps/web/src/api/endpoints.ts`；页面 `src/pages/`；通用组件 `src/components/`。
- 后端 `GET /` 与 SPA fallback 都 `include_in_schema=False`：**契约产物不得随 `dist` 是否存在而变**
  （有不变式测试看守）。

## 硬性工程纪律（都有看守或有前车之鉴，细节见 AGENTS.md）

1. **内容层零接触**：现存书籍项目（17 条 PAUSED run、48 章书等）都是测试数据，不得改 / 重生成 /
   续写 / 为好看做迁移。任何需要读正文 / 人物 / 设定的验证，一律用**临时库 + 合成夹具**。
2. **改软件时禁真实 LLM 调用**：验证走 mock provider / 单元测试 / 临时库 e2e（`smoke_e2e.py`）。
3. **声称与实测分离**：没有测试看守的结论必须标注为「声称」；新测试要有判别力
   （断言打在真实值上，撤掉修复必红）。
4. **闸门前后的树指纹逐位一致才算数**；`--basetemp` 必须在仓外（`tests/conftest.py` 已强制，
   指向仓内直接 `UsageError`）。
5. **单文件 ≤ 1500 行**（`tests/unit/test_file_size_budget.py`，只减债账）；超限拆「多模块 + 门面」。
   拆前先 `grep monkeypatch.setattr(<模块>`：被测试打点的名字不能迁走。
6. **用例清单守恒**：`scripts/check_test_inventory.py` + `tests/test_inventory_snapshot.json`；
   增删测试要 `--update --reason` 留痕。
7. **改 Pydantic 模型 / 路由函数的 docstring = 改 OpenAPI 契约**（docstring 进 schema description）
   ⇒ regen 属预期动作，不是漂移事故。
8. **fail-closed**：「读不到」≠「没有」；证据不可得 ≠ 合格（质量门与闸门此时必须拦，不许放行）。
9. **闸门必须配出路**：只拦不放（没有获批通道）会把系统做成死路。
10. **同一语义出现第 2 次就问「能否收敛单点」**；副本是「重复的义务」，不是「重复的代码」。
11. **审查时优先找这几种高频形状**：
    - 数据源存在 ≠ 已进装配（字段在表里 / 在 ctx 里，但没进消费方 payload）
    - 测量对象 ≠ 被审对象（读草稿做判定必须带被审版本：`packages/domain/chapter/draft_resolver.py`）
    - 触发面 ≠ 展示面（按集合 A 判定要不要拦、却把集合 B 给人看 ⇒ 盲批）
    - 回滚 / 撤销不清派生面（摘要、索引继续被消费）
    - `INNER JOIN` 到可空外键 ⇒ 静默丢行
12. **发版**：`pyproject.toml` 是版本单源；六处副本对齐 + CHANGELOG 归档 + 轻量 tag
    （完整清单见 AGENTS.md §四「版本纪律」）。

## 改动落点速查

| 要做的事 | 落点 |
|---|---|
| 新增 REST 端点 | `packages/core/api/routers/<domain>.py`（模块级 `router`）；工作流端点在 `routers/workflows/` |
| 新增 / 改工作流 | `packages/workflows/<name>/pipeline.py` 的 `WORKFLOW` + 同包 `__init__.py` 注册 |
| 新增表 / 列 | `database/migrations/00NN_*.sql` + `tests/unit/test_migrations.py` + `scripts/smoke_e2e.py` |
| 新增 / 改提示词 | `docs/agents/prompts/<agent>-vN.md`（**新建版本**，不原地改） |
| 质量规则 / 严重度 | `packages/core/quality/`（`BLOCKING_RULES` 与 severity 矩阵变更须同步三处文档 + `formula_hash` + 回归测试） |
| 前端页面 / 组件 | `apps/web/src/pages/`、`src/components/`；端点封装 `src/api/endpoints.ts`；类型 `src/api/types.ts` |
| 对外 Skill 包 | `skills/novelos/`（三平台清单的 `source` 形状互不兼容，分文件写；改完跑 `check_skill_package.py --selftest`） |

## 测试布局

`tests/unit`（含 `quality/`、`simulation/`）、`tests/api`（契约 / 路由 / 门禁闭环）、
`tests/integration`、`tests/workflow`（引擎 / resume / 四工作流）、`tests/evals`（golden 回归）、
`tests/mutation_specs`（变异清单，不产生 pytest 用例）。
不引入 pytest-asyncio（集成测试用 `httpx.ASGITransport` + `asyncio.run` 自管事件循环）；
`tests/conftest.py` 已把 secrets / data 目录沙箱到临时目录，跑测不碰生产库。

## 环境变量

完整表见 README。最常用：`NOVELOS_DATA_DIR`、`NOVELOS_DB_PATH`、`NOVELOS_PORT`（默认 18081）、
`NOVELOS_QUALITY_GATE`（`enforce` | `report`）、`NOVELOS_PROMPT_SYNC`、
`NOVELOS_DISABLED_MODULES`、`NOVELOS_SECRETS_FILE`。

⚠️ API **无鉴权**、CORS 仅限本机 ⇒ 不要把服务暴露到公网。
