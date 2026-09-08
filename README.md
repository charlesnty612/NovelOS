# NovelOS

> 本地优先的小说写作操作系统（Local-first novel writing OS）。
> 章节生产全链路：计划 → 写作 → 评审 → 提交（plan → write → review → commit），全部数据保存在本机 SQLite。

## 项目简介

NovelOS 面向长篇小说作者：在本地运行完整的小说生产闭环。作者创建项目、人物与章节后，
系统以四段工作流驱动章节生产（director 计划 → writer 写作 → 人工评审 → observer 提取
状态变化并提交），并持续维护一份「Canonical Story State」快照作为全书的唯一事实源。

## 当前能力清单

- **章节生产四工作流**：`chapter-plan`（director 计划）→ `chapter-write`（writer 草稿）→
  `chapter-review`（作者人工评审，Human 节点可暂停/恢复）→ `chapter-commit`（observer 提取
  状态变化 → state delta 校验 → 质量门禁 → 提交）。端点见 `packages/core/api/routers/workflows.py`。
- **状态版本与分支**：Story State 版本化（v1 快照 + delta 提交），支持分支（What-if 推演用
  临时分支，可归档）。见 `packages/core/story_state/` 与 `packages/core/api/routers/story_state.py`。
- **质量引擎与爽感体检**：`QualityEngine` 按规则评分并产出 `quality_reports`；commit 流程内置
  quality gate（`report` 不阻断 / `enforce` 阻断两种模式）。见 `packages/core/quality/`。
- **伏笔债务台账**：hooks（伏笔）与 narrative_debts（叙事债务）的创建、兑现、检索，配套
  REST 端点。见 `packages/core/api/routers/ledger.py`。
- **参照系拆书**：reference canons（参照作品）管理 + `deconstruct-book` 工作流（逐章拆解、
  聚合、schema 校验）。见 `packages/core/api/routers/reference.py`。
- **What-if 推演**：`SimulationService` 在临时分支上跑假设 delta、计算 diff 并归档。
  见 `packages/core/simulation/` 与 `packages/core/api/routers/simulation.py`。
- **多模型路由**：按 capability（`reasoning` / `creative_writing`）路由到 mock / OpenAI 兼容 /
  Anthropic / Ollama provider，支持失败转移链。见 `packages/core/model_router/`。
- **项目导出发布（V1.4 Sprint 16）**：整书 / 单章 txt 与 docx、番茄投稿包（前 ~1 万字正文 + 全书大纲）一键导出。
  docx 用最小 OOXML 手工打包（无 `python-docx` 依赖）；只读、不调 LLM、不改 schema。
  见 `packages/core/exporter/README.md` 与 `packages/core/api/routers/export.py`。
- **番茄签约体检（V2.1）**：纯规则体检（无 LLM / 无新依赖 / 无 schema 变更），覆盖黄金三章（开篇 300 字冲突 / 主角 500 字出场 / 金手指前两章前 1000 字亮相 / 第三章小高潮）、逐章章末钩子、单章字数区间、高频副词堆叠、签约窗口 2万/5万/8万共 3 次机会提示；REST 端点 `GET /api/projects/{project_id}/signing-check`，番茄投稿包导出末尾自动追加摘要段。见 `packages/core/signing_check/`。
- **项目备份 / 恢复（V1.4 Sprint 16 / MVP）**：整项目导出为单 JSON 包（22 张业务表 + metadata 自证字段，不含 API key），支持导入为新项目（不覆盖源项目），单事务整体回环。
  见 `packages/core/backup/` 与 `packages/core/api/routers/backup.py`。
- **续写助手（V2.2）**：章节详情页一键生成多候选续写并排对比、采纳即追加草稿新版本。见 packages/core/api/routers/continuation.py。
- **真实长跑验证（V2.2）**：50+ 章 MiniMax-M3 连续生成验证状态机零衰减；观测报告与 judge 探针见 docs/evaluation/。

## 仓库结构

```
apps/web/                前端（React 18 + TypeScript + Vite；构建产物 dist/ 由后端托管）
packages/core/           后端核心（api / agent_runtime / model_router / quality / story_state /
                         workflow_runtime / simulation / context_engine / evaluation / versioning）
packages/domain/         领域服务（project / character / chapter / world / plot / timeline /
                         relationship / hooks / ledger）
packages/workflows/      工作流（chapter_plan / chapter_write / chapter_review / chapter_commit /
                         deconstruct_book / project_init / simulation）
database/migrations/     唯一 DDL 来源（0001_init.sql ~ 0023_project_word_band.sql，37 张物理业务表 + 1 张虚表 chapter_fts；关键迁移：0009 分支快照 / 0011 FTS5 / 0015 多卷 / 0016 模型档案 / 0017 唯一约束 / 0023 字数带覆盖）
tests/                   pytest（unit / integration / workflow / api / evals）
scripts/                 运维脚本（migrate.py / serve.py / eval_regression.py / smoke_e2e.py）
docs/                    设计文档、PRD、实现计划（docs/impl/IMPLEMENTATION-PLAN-v0.md）
prompts/                 Agent prompt 源文件（agents/sync 会同步到库内）
```



## 快速开始

前置：Python >= 3.11、Node >= 18。后端依赖见 `pyproject.toml`（fastapi / uvicorn / pydantic /
jsonschema / httpx；测试另需 pytest / ruff）。

```bash
# 1) 安装依赖
pip install -e ".[dev]"

# 2) 执行数据库迁移（生成 data/novelos.db；37 张业务表 + _migrations）
python scripts/migrate.py

# 3) 前端构建（构建产物 apps/web/dist，后端会自动托管）
cd apps/web
npm install
npm run build
cd ../..

# 4) 启动后端（默认 http://127.0.0.1:18081）
python -m packages.core.api.main
```

浏览器打开 http://127.0.0.1:18081 即可使用（后端同时托管 SPA 与 `/api`）。

## 开发规范

提交前跑一遍 lint 与测试（ruff 配置已固化在本 pyproject，测试文件豁免 E501）：

```bash
python -m ruff check packages scripts tests
python -m pytest -q
```


端口差异说明（V2.0 Wave C 任务三 统一收敛）：

- **统一默认端口 18081**（8000 在开发者本机常被占用）。单一配置源
  `packages/core/config.py:Settings.api_port`，`python -m packages.core.api.main`
  与 `python scripts/serve.py` 都通过该字段读取。
- 优先级：`NOVELOS_PORT` > `NOVELOS_API_PORT` > 默认 18081（兼容旧变量）。
- 前端 dev 代理从 `NOVELOS_PORT` / `NOVELOS_API_PORT` 读后端端口（见 `apps/web/vite.config.ts`）；
  改端口后需重启 vite dev 才生效。

健康检查：`curl http://127.0.0.1:18081/api/health`（应返回 `tables=37`，业务表数；含 `_migrations` 物理共 38 张）。

## 测试

```bash
# 后端全量（当前基线 1693 passed，2 skipped；-n 4 并行，约 4 分钟）
python -m pytest tests/ -q --ignore=tests/evals -n 4

# Golden 回归 eval（当前 3/3）
python scripts/eval_regression.py

# 端到端 HTTP smoke（真实起服务，临时库，跑完自清理）
python scripts/smoke_e2e.py

# 真实 MiniMax-M3 LLM 端到端验证（需 MINIMAX_API_KEY，会产生调用费用）
python scripts/real_llm_e2e.py

# 前端单测（vitest；当前 416）
cd apps/web && npm run test
```

- OpenAI 兼容 Provider 的请求超时默认 240 秒，可在 `model_configs.params_json.timeout_s` 覆盖为正数秒数。

| 变量 | 默认 | 说明 |
|---|---|---|
| `NOVELOS_DATA_DIR` | `./data` | 数据目录（含 SQLite 文件） |
| `NOVELOS_DB_PATH` | `{data_dir}/novelos.db` | 覆盖默认 db 路径 |
| `NOVELOS_LOG_LEVEL` | `INFO` | 日志级别 |
| `NOVELOS_API_HOST` | `127.0.0.1` | 后端监听地址 |
| `NOVELOS_API_PORT` | `18081` | 后端监听端口（`scripts/serve.py` 使用；优先级低于 `NOVELOS_PORT`） |
| `NOVELOS_PORT` | `18081` | 便捷端口变量（`python -m packages.core.api.main` 使用；优先级高于 `NOVELOS_API_PORT`） |
| `NOVELOS_WEB_DIST` | `apps/web/dist` | SPA 构建产物目录（存在 `index.html` 才启用托管） |
| `NOVELOS_QUALITY_GATE` | `enforce` | quality gate 模式：`enforce`（error 级阻断）/ `report`（不阻断） |
| `NOVELOS_CRITIC_MODE` | `always` | critic 评审模式：`always`（每章必评）/ `sample`（抽样）等；未设置回落到 pipeline 内定 always（每章） |
| `NOVELOS_SUMMARY_PARALLEL` | `on` | commit 三路并发：`on`（summarizer 并入 observer 双腿并发池）/ `off` |
| `NOVELOS_AUTO_REVISE_MAX` | `2` | 自动改稿回路最大轮数：`0` 禁用，正整数生效 |
| `NOVELOS_API_KEY_<PROVIDER>` | — | provider API Key（`<PROVIDER>` 大写，如 `NOVELOS_API_KEY_OPENAI`）；也可在 model_configs 的 `params_json.api_key` 配置 |
| `NOVELOS_DISABLED_MODULES` | — | 禁用模块列表（V3.3 轻量方案），逗号分隔；模块名 = `packages/core/api/routers/` 下的文件名去 `.py`（如 `simulation,reference,arc`）。被禁模块的 HTTP 路由**不挂载**，对应端点返回 404；不影响 workflow 注册（边界见 `docs/roadmap/v3.3-v3.5-candidates-design.md` §四） |

## 版本与更新日志

当前版本 **v3.8.0**（与 `pyproject.toml` 版本号单源一致；`apps/web/package.json` 为 3.5.0，属发版流程待对齐项）。自 V1.0 起，所有迭代必须在 `CHANGELOG.md`
追加条目（格式与分类见文件头部规矩）；已知问题与 V1.x/V2.x 路线登记在同文件
「Known Issues / 路线登记」一节。

逐 Sprint 进度、关键 commit 与测试基线见 `docs/impl/IMPLEMENTATION-PLAN-v0.md` §4.1 台账
（S0 ~ S11 已验收，S12 Tauri 壳经用户拍板关闭——Web 版即交付形态）；已知 deviation 见同文档 §4.2。

## AI 使用声明

- **本地优先**：默认所有数据与运算在本机完成，SQLite 落盘，不依赖云端服务。
- **数据不出本机**：默认不发起任何外部网络调用；仅当作者显式配置外部模型 provider
  （OpenAI 兼容 / Anthropic / Ollama 等，见「多模型路由」）时，模型调用请求才会按作者
  配置发送到对应服务（对齐 PRD §102：默认 Local Only，云端调用需 Explicit Consent）。
- mock provider 开箱即用，无需任何密钥即可跑通全链路与测试。

## 密钥管理（V3.8）

API key 集中在仓库根 `secrets.json`（结构 `{"api_keys": {<档案ID / provider / 环境变量名>: "<key>"}}`）。
解析优先级：`secrets[<profile_id>]` → `secrets[<provider>]` → `secrets[<env_name>]`
→ `params_json.api_key`（存量兼容）→ 环境变量。路径可由 `NOVELOS_SECRETS_FILE` 覆盖；
文件已被 `.gitignore` 屏蔽（含 `secrets.*.json` 通配），请勿提交明文 key。详见
`packages/core/secrets_store.py`。

更多上下文见 `docs/impl/IMPLEMENTATION-PLAN-v0.md`、`#NovelOS.md`。
