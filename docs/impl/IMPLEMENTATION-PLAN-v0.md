# NovelOS 实现总计划 v0

> 主会话设计定稿，2026-08-23。本文档是 PRD v1.2 → 代码实现的总指挥方案：所有 Sprint 派发以此为准。
> 上游权威：`#NovelOS.md`（PRD v1.2）、`docs/` 下全部 v0 设计文档与 Schema。

---

## 1. 关键决策（主控拍板）

### D-I1 产品形态：本地优先桌面应用，分两阶段交付

PRD §71 已定技术栈（Tauri + React/TS + FastAPI + SQLite + LanceDB/Chroma），§102 定 Local Only。用户授权形态自行决断，拍板如下：

- **Phase A（本次实现目标）**：本地 Web 应用——FastAPI 后端（localhost 服务）+ React/TS 前端（Vite 构建，后端托管静态文件）。浏览器访问 `http://127.0.0.1:<port>` 即用；数据全部落本地 SQLite。**理由**：本机无 Rust 工具链，Tauri 无法构建；且 Web 形态开发与验证成本最低，功能与桌面壳完全等价。
- **Phase B（后续）**：Tauri 桌面壳打包，包裹同一前端产物 + sidecar 后端。需用户安装 Rust 工具链后进行，记 ADR-0002。

### D-I2 目录结构：遵循 PRD §70

严格按 PRD §70 monorepo 结构落地。`packages/` 下各子目录为 Python 包（后端代码），`apps/desktop/` 为 React+TS 前端。后端通过根目录 `pyproject.toml` 统一管理依赖与包发现。

### D-I3 Workflow 引擎：内置轻量 Runtime + Adapter 隔离

DeterminFlow 为 AGPL-3.0 且处于 v0.1 早期（调研已确认），**不集成、不复制**。MVP 在 `packages/core/workflow-runtime/` 自研最小 DAG 执行器（节点五类：AI / State / Transform / Human / Simulation，对齐 PRD §55-60），对外只暴露 `Runtime Adapter` 接口（PRD §72），未来可替换。

### D-I4 模型接入：OpenAI-Compatible 优先

MVP 实现 `openai_compatible` 一个 Provider（覆盖 OpenAI / DeepSeek / 通义 / Ollama 等绝大多数服务，Ollama 本身提供 OpenAI 兼容端点），Anthropic 原生协议留 Sprint 8。测试一律用内置 `mock` Provider（确定性回显），不依赖外网。

### D-I5 验证策略：每 Sprint 有 DoD + pytest

- 后端：pytest（unit / integration / workflow / evals 四级目录对齐 PRD §70 tests/）。
- Schema 权威：`docs/**/schemas/*.json` 直接作为运行时代码的校验基准（jsonschema 库），代码不另造结构定义。
- 数据库：`database/migrations/0001_init.sql`（28 表，已验证）由迁移 runner 执行，作为唯一 DDL 来源。
- 每个 Sprint 完成后：smart 审查（Standards + Spec 双轴）→ 主控验收 → 才进入下一 Sprint。

### D-I6 执行协议（派发纪律）

- 主控：方案设计、Sprint 任务书（含给死口径）、验收定夺。
- general：按任务书执行，禁止自行发明设计。
- smart：只读审查每个 Sprint 的产出。
- 铁律：**禁止 Python 整文件重写做块级修改**（2026-08-23 事故教训），编辑一律用 Edit 锚点替换；跨文件块移动前先备份。
- 模块 README 强制（用户要求 2026-08-23）：每个被创建或实质改动的模块目录必须自带 `README.md`（职责 / 对外接口 / 依赖 / 使用入口 / 维护注意点），列入每个 Sprint 的交付清单与 DoD，smart 审查加「README 真实性」一项；Sprint 0 已建模块回溯补齐。

---

## 2. Sprint 计划（对齐 PRD §109，含依赖与 DoD）

| Sprint | 范围 | 依赖 | DoD（可机检） |
|---|---|---|---|
| S0 基础设施 | git 仓库、§70 目录、pyproject、配置/日志、SQLite 迁移 runner（跑通 0001_init.sql 28 表）、pytest 骨架、FastAPI 健康检查端点、Vite+React 骨架 | 无 | `pytest` 绿；`uvicorn` 启动 `/api/health` 200；迁移后 sqlite 库 28 表；前端 `npm run build` 成功 |

> S0 说明：PRD §70 中的 `database/schemas/`、`docs/ui/`、`docs/workflows/` 为预留目录，非 S0 交付（schema 权威按 D-I5 在 `docs/**/schemas/*.json`）。
| S1 Story Domain | Project/Character/World/Plot/Chapter 五个领域 Service + REST API + 28 表中相关表的 CRUD | S0 | 五实体 CRUD API 集成测试绿；UI 不经过 DB（Service 层隔离）有测试证明 |
| S2 Story State | State Delta 应用器（Delta→Validate→Commit→Rollback→Snapshot），Schema 用 state-delta.schema.json 校验，state_version 单调递增 | S1 | delta 提交/回滚/快照恢复单测绿；schema 违规 delta 被拒测试绿；关库重开状态恢复 |
| S3 Agent Runtime | Prompt 加载（docs/agents/prompts/*.md）、Model Router（mock + openai_compatible）、结构化输出（JSON 提取 + schema 校验 + 重试）、ai_call_logs 落库 | S2 | mock provider 下 director/writer/observer 三 prompt 端到端调用测试绿；调用日志落库可查 |
| S4 Workflow 主流程 | chapter-plan / chapter-write / chapter-review / chapter-commit 四条工作流 + Observer→Delta→Commit 串联 + Evaluation Harness 骨架（golden 数据集 + 回归 runner） | S3 | PRD §110 验收链路前 12 步在无头模式（API 级）跑通；golden 回归 runner 可执行 |
| S5 Workbench UI | 项目管理、Story Bible（人物/世界/伏笔/债务）、章节编辑器、AI Panel（Director Plan 审批）、Workflow Panel（运行状态/Human Node 处理） | S4 | 浏览器内完成 §110 全链路人工走查；构建产物由后端托管 |
| S6 Quality | quality-scoring-v0 六子分 + MVP Guardrail 五条（3 硬 + Q6/Q8）+ 爽感 H-1~H-5 | S4 | 评分管线单测绿；Guardrail 阻断/警告行为测试绿 |
| S7 Version | 章节/State 的分支、diff、回滚（基于 S2 Snapshot 机制，非 git） | S2 | 分支/回滚 API 测试绿 |
| S8 Model Router 补全 | Anthropic 原生、Ollama 本地、Provider 健康检查与路由策略 | S3 | 各 Provider 适配器单测绿（mock 服务器） |
| S9 Hooks/Debts UI 深化 | Hook Ledger 时间线视图、Narrative Debt 面板、expected_payoff 校准（参照系） | S5 | UI 走查通过 |
| S10 Simulation | What-if 分支推演（Plot Graph + State 快照副本） | S7 | 推演不污染主 State 的测试绿 |
| S11 参照系与合规 | deconstruct-book 工作流（deconstructor-chapter/aggregate）+ G-sim 双轨检测 + 黄金三章机检 | S4, S6 | 对一本测试书跑通拆书工作流；REQ-Q6/Q7/Q8 三 Guardrail 生效 |
| S12 Tauri 壳（Phase B） | 需 Rust 工具链，用户确认后进行 | S5 | 桌面窗口启动 |

> 顺序说明：S11 提前于 PRD §109 之外补入（PRD v1.2 §123/§125 是合规矩阵的一部分，发布番茄前必须可用）；S10 按 PRD 顺序。S7/S8 可按依赖情况与 S5/S6 部分并行派发。

---

## 3. 风险登记

| 风险 | 缓解 |
|---|---|
| Sprint 任务书口径不够死导致返工 | 每 Sprint 派发前主控 Read 相关设计文档，把字段/路径/验收命令写死 |
| 子代理改坏既有文件 | 铁律 D-I6；git 仓库每 Sprint 结束打 commit，损坏可回滚 |
| LLM 无外网 key 时无法真机验证 | mock Provider 全覆盖；真实 provider 留用户配置后手动验证项 |
| LanceDB/Chroma 依赖重 | MVP 向量检索用 SQLite + numpy 余弦兜底，LanceDB 作可选增强（Sprint 3 定） |
| 前端工作量膨胀 | UI 以「能用」为先：管理台风格，不做复杂可视化（PRD §111 禁止项） |

---

## 4. 当前状态

- [x] 计划定稿（本文档）
- [x] S0-S12 逐 Sprint 执行：S0-S11 全部验收通过；S12 由用户拍板关闭（2026-08-23，Web 版交付即目标达成）

### 4.1 Sprint 进度台账（2026-08-23）

| Sprint | 状态 | 关键 commit | 测试基线 |
|---|---|---|---|
| S0 基础设施 | ✅ 验收通过 | 5556e6c / 0976b60 | 12 |
| S1 Story Domain | ✅ 验收通过 | 461e203 / bf13c75 | 42 |
| S2 Story State | ✅ 验收通过 | fd791d7 / 0160274 / 2585f26 | 84 |
| S3 Agent Runtime | ✅ 验收通过 | 90c9275 / eb3e945 | 149 |
| S4 Workflow + Eval | ✅ 验收通过 | c563bba / e243564 | 161；golden 回归 1/1 |
| S5 Workbench UI | ✅ 验收通过 | 34b23c4 / 90937f7 | 后端 184 + 前端 vitest 77 |
| S6 Quality | ✅ 验收通过 | 90f4d6a / 4a05a5e | 后端 270 + 前端 83 |
| S7 Version 分支 | ✅ 验收通过（P0×2 修复后复验） | aa21985 / 6d4b321 | 后端 330 |
| S8 Model Router 补全 | ✅ 验收通过 | aa21985 | 后端 326（合并基线） |
| S9 Hooks/Debts UI | ✅ 验收通过 | aa21985 | 前端 124（合并基线） |
| S10 Simulation | ✅ 验收通过 | e614002 / cce0abf | 后端 369 |
| S11 参照系与合规 | ✅ 验收通过 | 4b47582 / e614002 / cce0abf | 后端 369 + 前端 133 |
| S12 Tauri 壳（Phase B） | ❎ 已关闭（用户拍板 2026-08-23：Web 版交付即达成目标，不做桌面壳；日后需要可重开） | — | — |

### 4.2 已知 deviation 登记（随版本关闭）

1. ~~**review reject 即终局**~~（S4 审查 P1-1）✅ **已关闭**：PRD §59/§87 的「人工修改后重审」闭环已实现——review 的 Human 节点 resume 支持三态：`{approved:true}`（REVIEWED）、`{approved:false}`（FAILED，保持 DRAFTED）、`{approved:false, revise:true, note?}`（run FAILED + error=`rejected-for-revision`，chapter 保持 DRAFTED，note 落 `plan_json.revision_note` 供下次 write 参考；随后可人工改稿或重跑 write 后再 review）。实现口径：引擎 `workflow_runs.status` CHECK 与 `_finalize_run` 虽已支持 `CANCELLED`，但 `engine._run_nodes` 无产生 CANCELLED 的触发路径（节点成功必 COMPLETED、异常必 FAILED），且不修改 engine/DDL，故沿用 FAILED 终态以 error 字段区分（详见 `packages/workflows/chapter_review/README.md`）。
2. **Context Engine MVP 全量装配**（S4）：L0-L9 裁剪/token 预算未实现，`plot_graph_excerpt.unresolved_branches` 恒空、`world_state_excerpts` 缺 sensory_anchors。记录于 `packages/core/context_engine/README.md`。
3. **eval 内容正确性断言缺口**（S4 审查 P2-3）：golden runner 目前做流程+结构断言，observer 内容语义（before/after 与正文一致性、HIGH 误标对抗用例）归入 S6 范围。
4. **model-configs 列表返回 params_json 明文**（S5 审查记录）：本地单用户 MVP 可接受；发布前（S8 或 S12）加脱敏。
5. **连续两次 Human pause 的 human_input 覆盖语义**（S4 审查 P2-4）：`dict.update` 语义已写入 README，连续 pause 场景缺专项测试，后续补。
6. **promote 按序重放而非单合并 commit**（S7 审查 P0 修复）：state-delta-v0 §6.3 字面为「单一合并 commit」，因 applier 固定应用顺序会破坏分支内 add→resolve 时序，改为逐 commit 重放（main state_version 单次跳 N）。记录于 `packages/core/story_state/README.md` §6.5。
7. **quality_gate 默认 report 模式**（S6）：commit 门禁默认只落库不阻断；`NOVELOS_QUALITY_GATE=enforce` 或请求体 `quality_gate_mode:"enforce"` 才硬阻断。接入真实模型生产使用前建议切 enforce 并先跑一轮校准。
8. **deconstruct-book MVP defer 清单**（S11）：epub 不支持、T1 失败无 Human 补切分、多参照系加权（OV-2）、embedding 轨道、同步长任务无超时/异步队列。记录于 `packages/workflows/deconstruct_book/README.md`。
9. **checkpoint_exclude 与 resume 的交互**（S11 修复遗留）：被剔除键（如拆书原文 text）在 resume 时不恢复；deconstruct 无 Human 节点暂不显现，未来加 Human 节点需注入机制。
10. **Simulation 限制**（S10）：纯状态推演（无 LLM 叙事推演）；`_skip_approval` 仅 simulation 路径可用且有审计字段；chapter_id 必填。记录于 `packages/core/simulation/README.md`。
11. **§6 Regression 基线判定为结构签名 MVP 子集**（quality-scoring-v0 §6 落地，2026-08-23）：PRD §85「不能直接上线」的分数级判定（overall 容差、关键子分 ≥ baseline-3、Guardrail hit_rate 恶化）依赖 LLM judge 分数，MVP 阶段映射为**结构签名比对**（state_version / delta_arrays / hooks 漂移 + guardrails_pass 由 pass 变 fail 即 BLOCK），见 `tests/evals/regression_baseline.py` 与 `tests/evals/README.md` §5；基线 `docs/evaluation/baseline/last_passing_run.json` 标注 `baseline_semantics: "structural-signature-mvp"`，LLM judge 接入后升级语义并重建基线。
