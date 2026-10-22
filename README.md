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
  状态变化 → state delta 校验 → 质量门禁 → 提交）。端点见 `packages/core/api/routers/workflows/`（按域拆包：control/runs/revise）。
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
- **交付判定（delivery）**：「这一章 / 这本书能不能交」的单一出口——把质量报告（含 confirm / block 后果档）、字数带、签约体检（黄金三章口径）、章状态聚合成闭合四档判定（`deliverable` / `needs_work` / `not_deliverable` / `not_a_candidate`）+ 机器可读理由；证据不可得一律落 `not_evaluated` 并降级（绝不静默当通过）。REST 端点 `GET /api/projects/{project_id}/delivery-verdict`。见 `packages/core/delivery/README.md`。
- **项目备份 / 恢复（V1.4 Sprint 16 / MVP）**：整项目导出为单 JSON 包（22 张业务表 + metadata 自证字段，不含 API key），支持导入为新项目（不覆盖源项目），单事务整体回环。
  见 `packages/core/backup/` 与 `packages/core/api/routers/backup.py`。
- **续写助手（V2.2）**：章节详情页一键生成多候选续写并排对比、采纳即追加草稿新版本。见 packages/core/api/routers/continuation.py。
- **真实长跑验证（V2.2）**：50+ 章 MiniMax-M3 连续生成验证状态机零衰减；观测报告与 judge 探针见 docs/evaluation/。

## 对外：Agent Skill 包（`skills/novelos/`）

本仓另附一份**可分发的 Agent Skill 包**——装进任意支持 Agent Skills 规范的宿主
（Claude Code / Codex / Cursor …），即可**用自然语言驱动本机 NovelOS**：
起章节、跑质量体检、查交付判定。

| 装哪儿 | 清单文件 | `source` 形状 |
|---|---|---|
| Claude Code | `.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` | **字符串**，且以 `"./"` 开头 |
| Codex | `.codex-plugin/plugin.json` | `{"source": "local"}` |
| Cursor | `.cursor-plugin/plugin.json` + `.cursor-plugin/marketplace.json` | `"."`；`additionalProperties:false` **真的实现**（未知键直接判红） |
| Agent Skills 标准市场 | `.agents/plugins/marketplace.json` | — |

> **三平台的 `source` 形状互不兼容**——照抄任一家的形状到另一家 ⇒ 装不上
> ⇒ **分文件写，不共用一份**。

- **零第三方依赖**：客户端 `skills/novelos/scripts/novelos.py` 只用标准库；
  `--help` 在**离线**状态可跑（自检里会打掉 `socket` 来证明这一点）。
- **契约自检**：`python skills/novelos/scripts/check_skill_package.py --selftest`
  —— 46 项检查 + 7 条变异自检（把包改坏 ⇒ 对应检查必须报红）。
- **前提**：本机 NovelOS 服务在跑（默认 `http://127.0.0.1:18081`）。
  ⚠️ 本仓 API **无鉴权**、CORS 仅限本机 ⇒ 该 skill 只适用于**本机自用**，
  不要把服务暴露到公网。

## 仓库结构

```
apps/web/                前端（React 18 + TypeScript + Vite；构建产物 dist/ 由后端托管）
packages/core/           后端核心（api / agent_runtime / model_router / quality / story_state /
                         workflow_runtime / simulation / context_engine / evaluation / versioning）
packages/domain/         领域服务（project / character / chapter / world / plot / timeline /
                         relationship / hooks / ledger）
packages/workflows/      工作流（chapter_plan / chapter_write / chapter_review / chapter_commit /
                         deconstruct_book / project_init / simulation）
database/migrations/     唯一 DDL 来源（0001_init.sql ~ 0033_model_connections.sql，共 33 个迁移，42 张业务表 + 1 张虚表 chapter_fts；关键迁移：0009 分支快照 / 0011 FTS5 / 0015 多卷 / 0016 模型档案 / 0017 唯一约束 / 0023 字数带覆盖 / 0025 题材包 / 0026 run 实例归属 / 0027 场景计划 / 0028 大纲槽 / 0029 写作圣经 / 0030 回滚索引项目内收窄 / 0031 项目级快通道 / 0032 内部消费外部 skill / 0033 模型连接层）
skills/novelos/           对外可分发的 Agent Skill 包（SKILL.md + 5 份 references + stdlib 薄客户端
                         + 契约自检 46 项；三平台清单见 .claude-plugin/ / .codex-plugin/ /
                         .cursor-plugin/ / .agents/）
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

# 2) 执行数据库迁移（生成 data/novelos.db；42 张业务表 + _migrations 物理共 43 张）
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

## 授权状态

本仓库采用 **GNU Affero General Public License v3.0（AGPL-3.0-only）**，
许可证全文见 [`LICENSE`](LICENSE)（逐字取自 gnu.org，未作任何改动）。

- **你可以**：自由使用、修改、再分发（含商业使用）。
- **必须**：改动后**以 AGPL-3.0 继续分发**；**通过网络提供服务时**（§13），
  必须向使用者提供**完整对应源码**——这是 AGPL 相对 GPL 的核心差异。
- **保留**：作者保留著作权；本项目不提供任何担保。

> **决策记录（2026-10-19 订正 2026-10-16 的裁决）**
>
> - 2026-10-16 曾就「补一份 LICENSE」征询，裁决为**暂不添加**，理由是
>   「许可文本是一份有法律后果的授权，不应由工具代为签署」。
> - **推翻理由**：该裁决的前提是「**纯自用工具**」（2026-09-11 裁决）。而 2026-10-19
>   事实已变——**本软件已开始给他人使用**，「保留一切权利」从此不再是中性的默认态，
>   而是**实际的法律暴露**（别人在无许可的情况下使用一份有版权的公开仓库）。
>   `docs/roadmap/v3.9-业务逻辑优化与治理.md` 的 R3 条目本就把 LICENSE 列为
>   「转公开发布的硬阻塞，届时重估」——**重估条件已触发**。
> - **选型理由（AGPL-3.0 而非 MIT）**：授权**不可撤回**（MIT 一旦授出即对所有既有副本
>   永久生效），而**收紧随时可做**（著作权人可再许可）⇒ **风险不对称时取严**。
>   AGPL-3.0 亦与同路线的最大同类项目（InkOS，AGPL-3.0）一致。
> - **本裁决由主会话在用户授权「全量修复 + 期间自行裁决」下作出并留痕**；
>   改判只需重写 `LICENSE` 并同步本节与 `pyproject.toml` 的分类器。

## 更新到最新版（克隆安装的用户看这里）

仓库的 `data/` 与 `apps/web/dist/` **都不在版本库里**（见 `.gitignore`）——
你的书稿与本地库因此不会被 `git pull` 碰到，但也意味着**产物要自己重建**。

```bash
git pull
# 依赖变了才需要（有输出才做）：
git diff --stat HEAD@{1}..HEAD -- pyproject.toml uv.lock apps/web/package.json
#   ↑ 有输出 ⇒ cd apps/web && npm install && npm run build && cd ..
#   ↑ 无输出 ⇒ 跳过

# 重启服务。启动时若检测到代码已更新，会在日志里打印新旧 commit；
#   若同时提示 apps/web/src 有变更，务必重建前端（见下）
python -m packages.core.api.main
```

### 三条必须知道的规则

1. **改了前端的版本，`git pull` 之后界面还是旧的。** 后端从磁盘托管
   `apps/web/dist`，而 `dist/` 不入库 ⇒ 克隆用户必须自己 `npm run build`，
   否则得到「**新后端 + 旧界面**」。**这不会报错**，只会行为诡异
   （后端返回了新字段界面不认，或反之）。启动时的版本检查会针对这一条单独提示。
2. **数据库迁移自动跑。** 新增迁移随 `git pull` 过来，服务启动时
   `apply_migrations` 补上（日志：`migrations applied=N skipped=M`）。
   本仓迁移纪律是**只加不改**，存量库不会被打断。
3. **改了 `pyproject.toml` 要 `uv sync --extra dev`**（不是裸 `uv sync`——
   裸的会把 ruff / pytest / xdist 一起 pruning 掉）。

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

健康检查：`curl http://127.0.0.1:18081/api/health`（应返回 `tables=41`，业务表数；含 `_migrations` 物理共 42 张）。

## 测试

```bash
# 后端全量（当前基线 3578 passed / 1 skipped / 1 xfail，3580 collected；-n 4 并行，约 7~10 分钟）
# --ignore=tests/unit/test_m1_lock.py 已于 2026-10-16 **撤销**：它曾挂的理由
# 「该文件单跑会硬杀整个进程树、真因未定位」经实测证伪——那是一条从未成立的现象
# （原记录把「我复现不出来」当成了「我还没找到真因」）。顺此查出并修掉了一个**真缺陷**
# （`os.kill(pid,0)` 在 Windows 非稳定探测 ⇒ M1 单实例锁静默失效），并重写了两条
# 本来都是空壳的 cmd_run 用例。现在该文件既无副作用也已进入全量并行验证
# （3578 那轮即含它）。详见 scripts/tree_fingerprint.py docstring 与 AGENTS.md 坑区。
# 注：**不带任何 --ignore**（`tests/evals` 的 16 例评估回归同样计入基线，自包含且全绿，
# 5.68s）⇒ 本命令产出即上面那个 3578；此前此处带 `--ignore=tests/evals`，产出 3561，
# 与基线对不上，已撤销。
python -m pytest tests/ -q -n 4

# Golden 回归 eval（当前 3/3）
python scripts/eval_regression.py

# 端到端 HTTP smoke（真实起服务，临时库，跑完自清理）
python scripts/smoke_e2e.py

# 真实 MiniMax-M3 LLM 端到端验证（需 MINIMAX_API_KEY，会产生调用费用）
python scripts/real_llm_e2e.py

# 前端单测（vitest；当前 755 用例 / 56 文件）
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
| `NOVELOS_QUALITY_GATE` | `enforce` | quality gate 模式：`enforce`（blocking error 级阻断）/ `report`（不阻断）；blocking 白名单见 `packages/core/quality/issues.py:BLOCKING_RULES` |
| `NOVELOS_QUALITY_Q8_STRICT` | 未设置 | 置 `1` 时 Q8 人工占比红线从默认 warning 升回 error（V3.9 批次 3.3 裁决：AI 写作工具默认不阻断） |
| `NOVELOS_CRITIC_MODE` | `always` | critic 评审模式：`always`（每章必评）/ `sample`（抽样）等；未设置回落到 pipeline 内定 always（每章） |
| `NOVELOS_SUMMARY_PARALLEL` | `on` | commit 三路并发：`on`（summarizer 并入 observer 双腿并发池）/ `off` |
| `NOVELOS_OBSERVER_SPLIT` | `on` | observer 双腿拆分开关：`on` 分 entities / narrative 两条轻量腿；`off` 走旧单次大调用路径 |
| `NOVELOS_OBSERVER_PARALLEL` | `on` | observer 双腿并发提交开关：`off` 回退串行（wall time ≈ 两腿之和） |
| `NOVELOS_WRITER_CONTEXT_MODE` | `paged` | writer 上下文注入模式：`paged`（L0/L1/L2 分页）/ `full`（全量）；非法值回落 `paged`（不抛错） |
| `NOVELOS_CONTEXT_RELEVANCE` | 启用（未设置=开） | 置 `off` 关闭相关性裁剪（按 plan / scene_plan 的 involved 实体裁剪角色 / 地点 / 势力摘要） |
| `NOVELOS_AUTO_REVISE_MAX` | `2` | 自动改稿回路最大轮数：`0` 禁用，正整数生效 |
| `NOVELOS_WRITER_LENGTH_RETRIES` | `2` | chapter-write 生成期字数闭环：writer 产出低于字数带下限时**节点内**以 `mode='write'` 整章重写的最大额外尝试次数；`0` 关闭（只写一次）；非法值回落默认 |
| `NOVELOS_API_KEY_<PROVIDER>` | — | provider API Key（`<PROVIDER>` 大写，如 `NOVELOS_API_KEY_OPENAI`）；也可在 model_configs 的 `params_json.api_key` 配置 |
| `NOVELOS_DISABLED_MODULES` | — | 禁用模块列表（V3.3 轻量方案），逗号分隔；模块名 = `packages/core/api/routers/` 下的文件名去 `.py`（如 `simulation,reference,arc`）。被禁模块的 HTTP 路由**不挂载**，对应端点返回 404；不影响 workflow 注册（边界见内容仓 NovelOS-Content `docs/roadmap/v3.3-v3.5-candidates-design.md` §四） |
| `NOVELOS_PROMPT_SYNC` | `on` | 启动时自动同步 `docs/agents/prompts` → agents/prompts 表（幂等，内容未变不刷 `updated_at`）；`off` 跳过（测试/调试场景） |
| `NOVELOS_DEBUG_PROVIDER_DUMP_DIR` | 未设置 | 生产排障：设置目录后每次成功的 OpenAI 兼容调用把**模型回复全文**落盘（仅保留最新 10 个文件、单文件 ≤2MB）。**落盘内容即用户创作内容全文，慎用** |
| `NOVELOS_SECRETS_FILE` | `<repo>/secrets.json` | 覆盖密钥文件路径（见「密钥管理」一节） |

## 版本与更新日志

当前版本 **v3.10.0**（`pyproject.toml` 为版本单源；`apps/web/package.json`、`apps/web/package-lock.json`
与 `uv.lock` 随发版对齐。当前基线：pytest **3796 passed / 1 skipped / 1 xfail**（3798 collected；全量命令**不再带**任何 `--ignore`——含 `test_m1_lock.py`，见上文「测试」一节），vitest **780**（57 个测试文件））。自 V1.0 起，
所有迭代必须在 `CHANGELOG.md` 追加条目（格式与分类见文件头部规矩）；已知问题与 V1.x/V2.x
路线登记在同文件「Known Issues / 路线登记」一节。

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
