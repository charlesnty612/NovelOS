# 更新日志（CHANGELOG）

> **留痕规矩（自 V1.0 起强制）**：此后所有迭代——功能、修复、迁移、行为变更——合并前必须在本文件追加条目。
> 格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)：版本号 + 日期 + 分类小节
> （Added 新增 / Changed 变更 / Fixed 修复 / Removed 移除 / Migration 迁移 / Known Issues 已知问题）。
> 版本号语义化：破坏性变更升 major，新功能升 minor，修复升 patch。

## [1.4.0] - 2026-08-24

发布链路与可观测包（路线图：`docs/roadmap/v1.3-v2.x-plan.md`）。

### Added

- 导出发布链路：`GET /api/projects/{pid}/export` 支持整书/单章导出 txt（utf-8-sig）与 docx（手工 OOXML zip，零新依赖），以及番茄投稿包（前 ~1 万字按章节边界截断 + 分隔线 + 全书大纲）；文件名 RFC 5987 双写法；项目总览页新增「导出」面板。新模块 `packages/core/exporter/`。
- 项目备份/恢复 MVP（PRD §100/§101）：`GET /api/projects/{pid}/backup` 导出 22 张业务表 JSON 包；`POST /api/projects/import-backup` 导入为新项目（单事务、id 全量重映射、自引用外键两轮写入、源项目永不覆盖、坏包 422）。api_key 彻底剔除（`model_configs` 不入白名单 + metadata 六项自证 + 测试递归扫描）；运行时/敏感表 10 张不导出。新模块 `packages/core/backup/`。
- 参照系消费可观测：`quality_gate` 捕获 `reference_consumption`（消费了哪些参照文件 + 字数），checkpoint 与 `quality_reports._meta` 双路径；QualityPanel 新增「本章消费参照系」区块。
- enforce 改稿引导：质量门禁阻断时生成结构化 `revision_guidance`（低分维度 + 阻断规则 → 可执行建议，16 条规则模板 + 9 条类目模板，纯规则零 LLM），写 checkpoint 与 `runs.error`；QualityPanel 新增「改稿引导」区块。
- summarizer 注册验证固化为正式集成测试（`tests/integration/test_summarizer_prompt_registration.py`，3 例）。

### Fixed

- 前端备份测试类型对齐（HealthResponse.tables Record 形态、快照 mock 注解修正）；ExportPanel 下载补 `credentials: 'same-origin'` 并去重组件内 URL 构造（改走 `endpoints.ts` 的 `exportApi.url`）。
- 备份导入自引用外键（`state_deltas.supersedes` / `branches.parent_branch_id`）第二轮改写空操作修复：原实现按 `WHERE col IS NOT NULL` 扫描库，但第一轮 INSERT 已统一置 NULL，永远查不到，导入后自引用列仍为 NULL；改为第一轮把 `(table, pk_col, new_pk_value, old_target)` 记入 `BackupService._self_ref_rewrites` 内存清单，第二轮按该清单 + 全局 `project_id_map` 直接 UPDATE 新行（详见 `packages/core/backup/README.md` §5.2）。

### Known Issues / 路线登记（新增）

- 备份快照内嵌 id 不重映射：`story_states.snapshot_json` 等 `*_json` 列内嵌实体 id 保持源项目命名空间（行级 MVP 口径，详见 `packages/core/backup/README.md`「已知边界」）；深度重映射留待需要时用稳定 id 前缀浅层替换。
- `reference_canons` / `canon_extracts` 暂不随备份导出（V1.5 升级路径见 backup README）。

## [1.3.0] - 2026-08-24

审稿与文风包（路线图：`docs/roadmap/v1.3-v2.x-plan.md`）。

### Added

- LLM 评审员 critic：chapter-review 在人工审批前生成建议性结构化审稿报告（总评/亮点/问题清单，category+severity 枚举校验，quote 溯源软校验），经 pause payload 的 critic_report 键展示在审批卡；advisory only 不拦截；非 JSON/无 prompt/provider 异常全降级为 critic_status='failed'，审批照常。新增 docs/agents/prompts/critic-v1.md（agent 注册 7→8）。
- 个人文风样例库：迁移 0008 新增 author_style_samples 表；CRUD API /api/projects/{pid}/style-samples（每篇 ≤5000 字、每项目 ≤10 篇）；writer 上下文注入最近 ≤2 篇 × ≤1000 字并带模仿引导语（与 Q7 爆款目标风格互补：个人声音+目标风格双轨）；context-preview 与前端面板同步展示；项目总览页新增 StyleSamplesPanel。
- 伏笔 overdue 阈值项目级可配：projects.foreshadow_overdue_chapters（默认 30，迁移 0008 加列），builders 读取并多重回退 30。

### Fixed

- 开放伏笔清单截断边界：overdue 判定下推 SQL（ORDER BY overdue_flag DESC ... LIMIT 20），去掉预取 60+内存排序，伏笔超 60 条时逾期项不再被丢弃。
- summarize 节点真实降级路径测试补齐（不注册 agent → PromptNotFoundError → failed 且 chapter COMMITTED）。
- ContextPreviewPanel 空态文案口径修正；ContextPreviewItem 类型补 excerpt_len 字段。
- critic 字数默认值复用共享常量；项目更新端点消费 foreshadow_overdue_chapters。

### Known Issues / 路线登记（新增）

- critic 输入的伏笔摘要暂以 hook name 充当（hooks 表无 description 列，加列后回填）。
- PromptNotFoundError 等 provider 前异常不落 ai_call_logs（既有 runner 行为，运维可观测性缺口，V1.4+ 处理）。

## [1.2.0] - 2026-08-24

竞品对标迭代：基于海外（Sudowrite / NovelAI / NovelCrafter）、国内（蛙蛙 / 彩云小梦 / 平台 AI 政策）与开源社区（13 个代表项目）三路调研的优化落地。分析文档：`docs/analysis/competitive-analysis-2026-08-24.md`。

### Added

- **质量系统第七维 `ai_trace`（AI 痕迹）**：章内 shingle 重复率 + 跨章重复率（最近 3 章）+ AI 套话命中三子信号合成；`overall` 升级为七维平均；前端 `QualityPanel` 增加「AI 痕迹」标签。（应对番茄低质 AI 文治理：重复率 / 文风机械性检测）
- **上下文装配可见化**：`GET /chapters/{id}/context-preview` dry-run API（零 LLM 调用，返回分层 token 估算与纳入条目清单）+ 章节详情页「AI 上下文明细」面板（`ContextPreviewPanel`）。
- **AI 调用日志查看器**：`GET /api/ai-call-logs` 列表（分页 / 过滤）与详情 API + 前端 `/ai-logs` 页面；敏感字段黑名单三重防御（schema 无 key 字段 + 路由黑名单 + 前端类型不声明）。
- **章节摘要链 + 前章尾段**：chapter_commit 新增 `summarize` 节点（≤200 字摘要 + 末 300 字尾段，迁移 `0007_chapter_summaries` 表；摘要失败降级不阻断 commit）；L1 装配注入最近 5 章摘要与前一章尾段，超预算先砍最旧摘要。
- **伏笔状态机强化**：observer 写透路径增加 hooks 状态迁移校验（前进制结算、拒绝 ABANDONED 复活）；`overdue` 计算属性（planted 超 30 章未收）；L1 新增「开放伏笔清单」装配（overdue 优先）。
- **summarizer agent prompt 注册**（`summarizer-v1.md`，capability=reasoning）。

### Changed

- `context-preview` 与 `ContextPreviewPanel` 同步展示摘要链 / 尾段 / 开放伏笔三类新内容源。
- 伏笔 / 开放线索由纯实体升级为带迁移校验的状态机（双口径说明见 `story_state` README §7.5.3）。

### Known Issues / 路线登记（新增候选）

- **V1.3+**：LLM 评审员（建议性审稿报告，人工裁决）；docx 导出与番茄投稿链路；任务分模型路由；个人文风样例注入（对标 Sudowrite Style Examples）；伏笔 overdue 阈值项目级可配。
- **V2.x**：向量召回混合层（状态库定事实 + 向量供呼应）；设定条件触发动态注入（关键词 / 关系链 / 场景绑定，对标 Lorebook / Codex）。
- V1.3+ 遗留（审查登记）：开放伏笔清单 SQL 预取 LIMIT 60 与 overdue 排序键不一致的截断边界（伏笔超 60 条时逾期项可能进不了清单）；summarize 节点 PromptNotFoundError 真实降级路径暂无测试覆盖（现测试用 mock 绕过）；ContextPreviewPanel 空态文案边界细节。

## [1.1.0] - 2026-08-24

PRD 全量符合性核对（§1–§125 逐节追溯）+ 全维度检修（安全 / 数据完整性 / 测试盲区 /
文档一致性，叠加 V1.0 的性能 / 业务闭环 / 架构三轴）后的修复版本。
§110 MVP 验收链由 12/13 补齐为 **13/13**。

### Added

- **Q8 人工占比 CSV 导出**（PRD §125 合规自证）：`GET /api/projects/{pid}/quality/q8-export`
  按章导出 ai_chars/human_chars/human_ratio（utf-8-sig BOM，Excel 兼容）；数字口径与评估链路
  同源（`compute_char_stats`）。合规立场已调研定调并写入 `packages/core/quality/README.md` §12：
  《人工智能生成合成内容标识办法》（2025-09-01 施行）的显式/隐式标识义务主体是面向公众的
  生成服务提供者与传播平台（番茄已在投稿环节要求作者勾选是否使用 AI）；NovelOS 是本地单机
  工具，V1 不在正文内嵌标识，产品侧提供统计与导出自证，投稿声明义务在作者侧。
- smoke_e2e §110 验收链补齐第 3 步「创建世界」断言（9 组断言全绿）。

### Fixed

- **API key 读路径脱敏**：model_configs 的 GET/PATCH/POST 响应统一脱敏（`api_key` → `***` +
  `has_api_key` 布尔）；PATCH 传 `***` 保留原 key、空串清空；前端编辑弹窗不再回填明文，
  留空=不修改。DB 写路径保持明文存储不变。
- **零外呼回归锁定**：无 model_configs 时断言 `ModelNotConfiguredError` 且 ai_call_logs 零新增；
  deconstruct 端点未配置模型时返回 422（原漏成 500），并前置 capability 预检。
- **拆书文本上限**：deconstruct `text` 超过 5MB → 422。
- **草稿体长上限**：`DraftCreate.content` max_length=500_000。
- **references 路径防护**：`load_reference_texts` 对 project_id 做字符白名单校验（防路径穿越）。
- **文档修正**：`packages/core/api/README.md` 健康端点表数 28→31（三处）。

### Known Issues / 路线登记更新

- V1.x 清单移除「model-configs 列表 api_key 脱敏」（本版已完成）；其余 V1.x/V2.x 项维持
  V1.0.0 登记不变。新增登记：前端无「清空密钥」UI 入口（仅能覆盖/保留）；`***` 为保留哨兵值；
  PRD 符合性核对发现的中长期缺口（Intent Engine §41、Impact Analysis §42、Event System §68、
  reveal_policies 运行时 §23.2、Timeline 冲突检测 §20、Dashboard 健康度 §14、Workflow DAG
  可视化 §61、Style System §97、Export/备份 §100/§101 等）按 PRD 自标的 V1/V2 阶段推进，
  明细见检修结论（各节判定矩阵已在审查记录归档）。

## [1.0.0] - 2026-08-24

首个正式版本。交付形态：**本地部署 Web 应用**（FastAPI 托管 React SPA，SQLite 落盘，单端口 18081）。
S0–S12 全部收官（S12 Tauri 壳经用户拍板关闭，Web 版即交付形态）；真实 LLM（MiniMax-M3）
端到端验证通过。

### Added（S0–S11 累计能力）

- 章节生产四工作流：chapter-plan（director）→ chapter-write（writer）→ chapter-review
  （Human 三态审批：批准 / 驳回 / 驳回附改稿意见）→ chapter-commit（observer 提取 state delta
  → 校验 → 质量门禁 → 提交）。
- Canonical Story State：快照 + delta 状态机、乐观锁、逆 delta 回滚、分支（What-if）与
  按序重放 promote。
- 质量引擎：6 子分加权、8 条 guardrail、H-1~H-5 爽感体检；quality gate 支持
  report（默认）/ enforce 双模式。
- 伏笔与叙事债务台账（hooks / narrative_debts）及 REST 端点。
- 参照系拆书 deconstruct-book（T1 切分 → T2 逐章拆解 → T3 聚合校验 → G-sim 相似度阻断 → T4 落库），
  参照 canon 自动注入 director 上下文。
- What-if 推演（simulation）：临时分支零污染推演 + diff + 归档。
- 多模型路由：mock / OpenAI 兼容 / Anthropic / Ollama，失败转移链，健康检查。
- 前端工作台：项目列表 / 项目总览 / Story Bible（人物·世界·情节·台账·Canon 五 tab）/
  章节详情（计划·草稿·工作流·质量面板）/ AI 设置。
- 回归基线门禁（结构签名比对）与 HTTP 全链路 smoke。
- 真实 LLM 验证：MiniMax-M3 全链路 PASS（报告 `docs/evaluation/runs/real-llm-minimax-m3-2026-08-24.md`，
  质量分 overall 87）。

### Fixed（V1.0 检修：性能 / 业务闭环 / 架构三路审计）

- **[P0] 审批卡片 UI 断头**：后端把 `__pause_payload__` 写在 `checkpoint_json[节点名]` 下，
  前端误从顶层读取导致审批卡片（review / HIGH 风险审批）永不渲染。新增纯函数
  `extractPausePayload` 按真实结构取值（`apps/web/src/utils/pausePayload.ts`）。
- **observer 校验重试**：commit 流程对 observer 输出先经 `validate_delta` 纯函数预检，
  失败带 `_retry_hint` 重试一次（共 2 次），`submit_delta` 仅在通过的 delta 上调用一次
  （真实 MiniMax-M3 运行中该重试实际救回一次 commit）。
- **写透 NULL 兜底**：world add 分支（location/faction/rule）对缺失的 name/statement/data_json
  做 None 兜底，消除 `NOT NULL constraint failed`。
- **归档过滤**：项目列表默认排除 ARCHIVED（`?include_archived=true` 可含），行为与 UI 文案对齐。
- **模型测试连接字段对齐**：前端 `ModelConfigTestResult` 对齐后端 `{ok, latency_ms, detail, status_code}`。
- **轮询节流**：`usePoll` 在页面隐藏（document.hidden）时跳过 tick。
- **SQLite 韧性**：连接统一 `PRAGMA busy_timeout = 5000`。
- **e2e 脚本**：HTTP 客户端超时 120s→1200s（推理模型 observer 调用合法耗时可达 300s）、
  sqlite row_factory、临时库清理重试。
- **架构**：`_diff_snapshots` / `_strip_state_version` / `_shingles` 提为公共 API
  （`diff_snapshots` / `strip_state_version` / `compute_shingles`），消除跨模块私有引用与
  simulation 内的复制函数。

### Migration

- `0006_quality_reports_project_idx.sql`：`quality_reports(project_id)` 索引（list_reports 查询路径）。

### Known Issues / 路线登记（V1.x / V2.x 候选）

- **V1.x**：simulation/branches 仅 API 无 UI 入口；ai_call_logs 无查看通道（PRD §93）；
  enforce 阻断后的改稿引导；参照系消费可观测（`_reference_canon_consumed` 未进 checkpoint）；
  ~~model-configs 列表 api_key 脱敏~~（V1.1.0 已完成）；checkpoint_exclude 推广到章节工作流
  （需先论证 resume 影响）；
  workflow 注册表下沉 core；router 越层 SQL（reference/workflows/model_configs）抽 service。
- **V2.x**：story_state/service.py（2300+ 行）按职责拆分；分支读路径 O(N²) 重放的快照物化；
  story_state 写透与 domain CRUD 的双写面统一；context engine 装配缓存；端口变量收敛。
- 不计划做：packages/agents 空骨架强填充（角色逻辑当前内联于各 pipeline，已在 README 标注）。
