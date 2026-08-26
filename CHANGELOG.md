# 更新日志（CHANGELOG）

> **留痕规矩（自 V1.0 起强制）**：此后所有迭代——功能、修复、迁移、行为变更——合并前必须在本文件追加条目。
> 格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)：版本号 + 日期 + 分类小节
> （Added 新增 / Changed 变更 / Fixed 修复 / Removed 移除 / Migration 迁移 / Known Issues 已知问题）。
> 版本号语义化：破坏性变更升 major，新功能升 minor，修复升 patch。

## [3.8.0] - 2026-08-27

### Fixed（V3.8「配置参数透传修复 + 思考模式灰度定稿」）
- **ModelRouter.call_with_fallback 配置参数丢失 bug（潜伏缺陷）**：`model_configs.params_json` 中除 `base_url/timeout_s/api_key` 外的所有键（`thinking`/`service_tier`/`temperature` 等）此前从未透传到上游请求体——配置写了 `thinking:disabled` 与 `service_tier:priority` 实际从未生效。现按行解析非构造键并入请求体，调用方显式 params 同名覆盖。`resolve()` 判定无需修（无生产调用方）。

### Added
- 思考模式灰度定稿（证据驱动，三章实测）：`reasoning`(director/critic) 与 `light`(observer 双腿+summarizer) 配置 `{"thinking":{"type":"disabled"}}`；`creative_writing`(writer) 保持思考开启。
- provider 原始响应采样开关（V3.7 引入）用于本版取证：completion 字符的 **78%~94% 为 `<think>` 思考串**。

### 实测对比（m1 ch069/ch070/ch071）

| 阶段 | 思考开 | 禁思考 |
|---|---|---|
| observer 双腿(合计 wall) | ~185s | **13~39s**（腿间缓存命中达 98%） |
| summarizer | 15~24s | **3~4s** |
| director plan | 104~224s | **22s** |
| writer | 125~135s（字数带内 +8.8%） | 10s 但字数 -40% 跌破 band |

单章全链：V3.6 基线 658~866s → 仅 light 禁思考 320s → 全禁 63s（writer 破带）→ **定稿组合 198s 且 prose 在带内、quality=82**。

### Known Issues
- 全禁思考下 writer 产出偏短（实测 -40% 跌破 band 下限）；若未来要全速模式需配套 revision 重写闭环。
- ch069 首次 commit 三连秒失败为本次配置误操作（light 行 base_url 被整串替换）所致现场，已从备份恢复；相关 error 记录留 DB 作审计。

## [3.7.0] - 2026-08-27

### Added（V3.7「字数带硬约束与口径统一」）
- **权威字数模块** `packages/core/quality/wordcount.py`：`visible_chars`（去空白口径）/ `word_band`（[0.7,1.3] 带 + 1200 下限保护）/ `classify_prose_length`。
- writer payload `chapter.word_band` 注入（置于 chapter_id 之后零破坏缓存键序，paged 模式原样继承）；review `_basic_checks_node` 升格产出 `W-LEN-DEVIATION`：>±15% warning、>±30% 进 errors（报告型：随 pause payload 展示），前端 ApprovalCard 红色渲染 errors 区块；m1 metrics 新增 `word_status`/`deviation_pct`。
- provider 原始响应采样开关 `NOVELOS_DEBUG_PROVIDER_DUMP_DIR`（默认关；保留最新 10 个文件、单文件 2MB 截断、异常不阻主流程）。
- `scripts/db_maintenance.py`：stale RUNNING 清理工具（list / fix --older-than-minutes N [--apply]，dry-run 默认，幂等）。（提前交付自迭代计划 V3.9 C1）
- 迭代计划文档 docs/roadmap/v3.7-v3.9-迭代计划.md（含分段写作实验否决记录与候选项闭环映射表）。

### Changed
- 全仓 prose 字数口径统一：signing_check/_count_chars、arc/service、m1_long_run._latest_prose_chars、chapter_review 四处收敛引用 wordcount.visible_chars。

### Fixed
- word_band 低目标 floor 抬升导致 low>high 倒挂：high 钳制 `max(high, low)` 并锚定边界测试（恰 ±15%/±30% 判定方向用例固定）。


### Added（V3.6「流式根治挂起 + 前缀缓存重排 + commit 三路并发」）
- **OpenAI 兼容 Provider 流式化**：`complete()` 改 `client.stream` + SSE 逐行解析（`data: {...}` / `[DONE]`），并加 `time.monotonic()` 总时长硬顶 deadline——根治非流式下整连接挂起而 read timeout（字节间隔语义）不生效的问题。`health_check` 保持非流式。
- **commit 三路并发**：summarizer 的 LLM 调用提前并入 observer 双腿并发池（`NOVELOS_SUMMARY_PARALLEL` 默认 on，ctx/env 可关），下游 summarize 节点短路消费 `summary_early`，缺字段/失败自愈重调；`summary_early` 列入 checkpoint_exclude 保证 PAUSED→resume 幂等；早产失败恢复 run 状态防污染。
- **竞品对标与单章提速决策文档**：docs/roadmap/v3.6-单章提速与竞品对标.md（oh-story / determinFlow / bishu-novel 管线对比、思考 token 数据真相、V3.8 方向）。

### Fixed
- 流式响应 usage 全丢：请求强制注入 `stream_options: {"include_usage": true}`（OpenAI 流式协议默认不下发 usage；实测 MiniMax 如此）。
- prompt 前缀缓存几乎零命中：5 个 agent 的 user payload 键序重排——稳定块（project/knowledge_permissions/constraints 等）前置、每章动态块（chapter 及其 chapter_id）后置。ch067 实测 plan 二次装配 cached_tokens=16896/17981=94%、summarizer=100%。
- serve.py 把通用 `MINIMAX_API_KEY` 映射到 provider 期望的 `NOVELOS_API_KEY_OPENAI_COMPATIBLE`（此前缺映射导致 401 秒失败）。

### Changed
- tests：SSE mock 全面适配（test_model_router / test_model_router_providers / test_cached_tokens_extraction）；空流语义统一为"零 content chunk 即抛错"；新增三路并发 13 个测试。全量 707 passed, 2 skipped。

### Known Issues
- writer 单次 completion 达 42~49k tokens，其中 ~95% 为 MiniMax-M2 内部思考 token（正文仅 ~2KB）；未启用 max_tokens 硬顶（截断风险破坏 JSON 契约）。V3.8 方向：字数带硬约束 + writer 分段写作（见 docs/roadmap/v3.6 文档 §四）。
- 实测本章剩余耗时由 MiniMax 服务端解码主导（plan+write ≈ 550~580s/章）；本地环节 <15s。

## [3.5.0] - 2026-08-26

### Added（V3.5「并发化与缓存观测」）
- **observer 双腿并发**：split 路径下腿 A/腿 B 经 ThreadPoolExecutor 并行提交（`NOVELOS_OBSERVER_PARALLEL` 开关默认 on，ctx/env 可关），单腿异常仍走原 per-leg 重试归类；实测两腿几乎同时启动。
- **cached_tokens 观测**：OpenAI 兼容 provider 提取 `usage.prompt_tokens_details.cached_tokens` 写入 ai_call_logs.token_usage_json（缺省不写字段），为 MiniMax 自动前缀缓存命中率提供数据基础。

### Fixed
- smoke_e2e / test_health / README 的业务表数断言链统一修正为 35（volumes 为唯一新增表；0014 系重建既有 reveal_policies 不增数）。

### Known Issues
- prompt 前缀缓存命中率待真实链路采样（cached_tokens 已可观测）；若命中率低需将 user 消息内动态字段后置。

## [3.4.0] - 2026-08-26

### Added（V3.4「多卷与规模」组织层）
- **卷管理**：迁移 0015（volumes 表 + chapters.volume_id）；VolumeService 与 REST 六端点（CRUD/seal/assign）；封存时冻结终态快照入卷归档；单项目同时仅一个 active 卷；sealed 卷拒绝挂章。
- **arc 按卷分组**：弧光视图章节元素携带 volume 归属，顶层新增 volumes 小节。

### 设计决策
- 未做按卷拆分快照存储——O-1 滚动窗口已实证上下文有界（63 章 trimmed 输入 31k 字符），存储级拆分的复杂度税不成立；150+ 章实测恶化时再按设计文档 §三升级快照分代。健康端点业务表数 35→36（volumes）。

## [3.3.0] - 2026-08-26

### Added（V3.3「知识权限补全」）
- **reveal_policies 数据模型**：迁移 0014——relationships/timeline_events/scenes 三表补 visibility+who_knows（I6 收口）；reveal_policies 表（target_kind 枚举/reveal_by_chapter/audience/status 机）+ CRUD API（GET/POST/PATCH/DELETE /api/projects/{pid}/reveal-policies）。
- **writer paged 装配可见性过滤**：HIDDEN 且存在 planned reader-audience 策略的实体从 writer 上下文整条移除（stats 记 hidden_filtered）；无策略时零行为变化；observer 保持作者全知视角（设计决策）。
- **arc 视图扩展**：reveal_policies 小节（planned/revealed/overdue 清单）。
- **模块轻量开关**：`NOVELOS_DISABLED_MODULES=simulation,arc` 按模块名过滤路由注册（默认空=零变化；仅过滤 HTTP 面，workflow 注册隔离为已知边界）。

### Changed
- 两条内置模型配置启用 `service_tier=priority`（MiniMax 官方优先准入，对策拥堵窗口大请求饿死；成本 1.5x）。

## [3.2.3] - 2026-08-26

### Fixed（V3.1.1 O-3）
- **observer 按腿输入裁剪**：腿 A 移除 events/hooks/debts（previous_state -65.5%）、腿 B 实体降级为标识摘要——双腿合计 payload 47.9k 字符，总 token ~120k→89k（-26%）。
- **近期 event_id 白名单**：payload.config 注入最近 30 个 event_id + prompt 强制避让，消除 observer 生成 id 与库中历史事件撞车导致的 UNIQUE constraint failed（ch063 实战首次尝试即 COMMITTED）。

### Known Issues
- provider 拥堵窗口下单腿仍可能 480s 超时（网络抖动同源），断点续跑可自愈；根治候选为流式调用或备用 provider 失败转移。

## [3.2.2] - 2026-08-26

### Added（V3.1.1 observer 减负专项 O-1/O-2）
- **事件摘要滚动窗口**：observer trimmed 输入的 events 摘要只保留最近 6 章触达 + 被 open hooks/debts 引用者，open hooks/debts 字段压缩；实测输入 92.6k→31.3k 字符（-66%）。
- **observer 双腿拆分**：`NOVELOS_OBSERVER_SPLIT` 开关（默认 on）——腿 A 实体状态（characters/relationships/world_changes）、腿 B 叙事对象（events/hooks/debts）各自独立调用与重试（按校验错误归类到出错腿），light capability 可配；run_agent 支持 capability_override。ch063 实战：单腿拥堵超时不再导致整次死锁。

## [3.2.1] - 2026-08-26

### Fixed
- **事件描述下沉 plot_events**（V3.1 P1-1.1 遗留）：新增迁移 0013 为 plot_events 加 description 列；write_through 写入 observer 的 `new_events[].description`；快照 DB 权威重建后事件描述不再丢失（漂移自愈测试同步更新语义）。

### Known Issues
- writer paged 模式的 LLM 输出质量 A/B 对比因 provider 长请求拥堵窗口暂缓，待稳定窗口补测（结构断言与体积量化已在 [3.2.0] 覆盖）。

## [3.2.0] - 2026-08-26

### Added（V3.2「写作现场完善与上下文分页」）
- **writer 上下文分页**：chapter-write 支持 `writer_context_mode=full|paged`（默认 paged，env `NOVELOS_WRITER_CONTEXT_MODE` 可全局回退 full）；paged 模式下实体集合按「最近触达全量 + 其余摘要」裁剪并注入裁剪统计。实测当前数据集缩减 5.4%（大项目收益更大，20 角色合成场景单键 -37%）。
- **叙事弧光视图**：`GET /api/projects/{pid}/arc` 聚合逐章 payoff/charge 标注、质量分、伏笔与债务台账，输出蓄力连击、兑现密度、逾期告警等弧线健康信号（含 51 条测试）。
- **CI 增强**：新增 pip-audit 依赖 CVE 扫描 job 与 e2e smoke job（真实起服务跑 scripts/smoke_e2e.py 全链路 + 状态同步巡检）。

### Changed
- chapter-write 默认上下文模式变更为 paged（原 full）；`writer_context_mode="full"` 或环境变量可回退旧行为。

### Known Issues
- paged 模式对 writer 输出质量的 LLM 实评未做（需对比 eval），当前仅结构断言与体积量化。

## [3.1.0] - 2026-08-26

### Added（V3.1「一致性治理与质量语义升级」）
- **快照漂移自愈**：commit 事务内 write_through 之后、逆路径清理之前，以 DB 为权威重建快照的 7 类实体集合（characters/locations/factions/world_rules/plot_events/hooks/narrative_debts）——历史漂移在下次 commit 自动收敛；分支路径不受影响。含漂移自愈回归测试。
- **LLM judge 双轨落库**：迁移 0012 为 quality_reports 新增 judge_json 列；`POST /api/chapters/{cid}/quality/judge` 持久化四维评审（pacing/style/logic/dialogue），GET quality 响应透出 judge 字段；m2_judge.py 支持 `--persist-api` 直连落库。judge 为旁路数据，不参与 overall 计算（七子分公式哈希不变）。
- **快照一致性巡检工具**：scripts/check_state_sync.py 只读对比 DB 实体表与 snapshot JSON 的 7 集合双向差集，退出码 0/1/2 可接 CI；已接入 smoke_e2e 第 10 步（每次冒烟自动验证零漂移）。

### Changed
- 快照 world_rules 等集合字段口径统一为 DB 权威语义（snapshot.events[].description 在首次重建后收敛为 NULL，属预期行为）。

### Known Issues
- new_events[].description 信息源未下沉 plot_events 表（DB 权威重建后该字段丢失），V3.1 P1-1.1 候选。

### Fixed
- 巡检脚本 world_rules id_field 错配（rule_id→world_rule_id）导致的假 DRIFT。

## [3.0.0] - 2026-08-26

### Added（V3.0「提速与减负」）
- **critic 采样模式**：`NOVELOS_CRITIC_MODE=off/sample/always`（默认 sample 每 5 章 1 次），请求 body `critic_mode` 可覆盖；跳过时工作流状态机不受影响。
- **light capability 分级路由**：critic/summarizer 映射到可配轻量模型，缺失自动回退 reasoning 链（零破坏，回退有日志）。
- **快照一致性巡检**：`scripts/check_state_sync.py` 只读对比 DB 实体表 vs snapshot JSON 七类实体集合，退出码 0/1/2 可接 CI；实测最新快照与 DB 对齐（早期版本漂移已随重写消除）。

### Changed（性能检修实测，单章 804s → 609s，-24%）
- **输出量瘦身**：observer `max_changes_per_array` 50→24+宁缺毋滥纪律；critic 限长 800 字；writer self_report 限 150 字；模型配置默认 thinking disabled。分环节实测：write 236-363s→32-136s、review 56-139s→29-37s。
- **超时策略**：模型配置 timeout 建议 1800s→480s（长等无恢复案例），驱动客户端 900s 快速失败重试。

### Fixed
- write_through 将 observer 自由文本 location 直塞 `plot_events.location_id` 外键导致 `FOREIGN KEY constraint failed`：加存在性守卫（无效置 NULL 不阻断）+ observer prompt 约束；含回归测试。

### Known Issues
- 单章耗时距 ≤300s 目标尚差（当前 609s）：commit 环节 observer 仍占 338-494s，结构性解法（quality 异步旁路/上下文分页/输出硬上限）排 V3.1 P1-3 与 V3.2 P2-1——见 docs/roadmap/v3-plan.md 实测表。

## [2.2.0] - 2026-08-25

### Added（M1~M4 里程碑：真实长跑验证与上限能力）
- **M1 真实模型长跑**：MiniMax-M3 连续生成 50+6 章，产出《一致性衰减观测报告》（docs/evaluation/m1-long-run-report.md）。核心结论：状态机零衰减实证（continuity 全程 99.4-99.7）；文风碎片化退化被定位并以 prompt 规则修复；payoff 闭环缺失被定位到 director 层。新增驱动脚本 scripts/m1_long_run.py（断点续跑/熔断/指标采集/单实例锁）与报告工具 scripts/m1_report.py。
- **M2 上限能力干预**：director 增 [payoff] 前置/字数纪律/叙事推进纪律/停滞强制升级/交互密度下限规则；writer 增反碎片化/字数下限/对话占比/payoff 可感知兑现规则。验证（docs/evaluation/m2-verification.md）：LLM judge 四维均分 34→69，对话维度 5→72，单章字数 849→1738-1930。新增 judge 探针 scripts/m2_judge.py（爽感/文风/逻辑/对话四维结构化评审）。
- **M3 量产管线硬化**：observer 输入快照分代裁剪（trimmed 模式，解 >110KB 快照超时）；validator 引用存在性校验（FK 失败前置为可自愈校验错误）；commit 管线接线两项能力——阻塞章 ch056 实战验收从 90 分钟不可解变为 253 秒一次通过。新增单实例文件锁（防双进程 SQLite 挂死）与按环节成本计量（observer 占 token 51.4%）。
- **M4 写作现场·续写助手**：`POST /api/projects/{pid}/chapters/{cid}/continue` 一键生成最多 3 个续写候选（temperature 差异化），`/continue/adopt` 采纳追加为新草稿版本（REVIEWED 自动降级 DRAFTED 待重审）；前端 ContinuePanel 三候选并排对比面板挂载章节详情页。
- quality 引擎新增 style 连续衰减监测（report-only，公式哈希不变）；golden 回归 eval 扩充至 3 case。

### Changed
- provider 请求体清理：params_json 中 base_url/api_key/api_key_env 不再泄漏进上游请求体。

### Known Issues / 路线登记（新增）
- LLM judge 目前是旁路探针，尚未正式接入 quality 评分语义（style 子分与 judge 口径不一致，见 m2-verification.md 遗留2）。
- 叙事主线惯性（量桩微循环）需故事弧光级管理工具，prompt 层只能缓解——归入 M4 后续写作现场迭代。

## [2.1.0] - 2026-08-24

### Added（番茄签约体检包）
- **新模块 packages/core/signing_check/**：面向番茄男频投稿的纯规则体检（无 LLM / 无新依赖 / 无 schema 变更）。检查项：第一章前 300 字冲突（ch1_conflict_300）、主角前 500 字出场（ch1_protagonist_500）、金手指前 1000 字亮相（ch2_golden_finger）、第三章小高潮/打脸（ch3_climax）、逐章章末钩子（chapter_hooks_chN）、单章字数区间 1200-2600 warn（chapter_length_chN，1500-2200 最佳）、高频副词堆叠检测 echo_words（>5 次/千字告警，对抗 AI 低质文的"复读词"问题）、签约窗口提示 signing_window（2万/5万/8万共 3 次机会）。
- **REST 端点**：`GET /api/projects/{project_id}/signing-check`，返回 items + summary 计数；项目不存在 404。见 packages/core/api/routers/signing_check.py。
- **番茄投稿包集成**：build_fanqie_package 导出末尾追加「===== 签约体检摘要 =====」段；摘要生成异常时跳过不阻断导出。
- 设计依据：2026 番茄平台 AI 低质文严打（黄金三章硬标准/签约窗口规则）与竞品调研（NovelCrafter Codex/Sudowrite Story Bible 的一致性治理已由 Story State 覆盖，本模块补齐平台规则体检缺口）。词典为模块级常量可扩展；启发式非保证，边界见 packages/core/signing_check/README.md。

### Known Issues / 路线登记（新增候选）
- signing_check 词典为子串匹配，存在误命中可能（如单字词「打」「血」）；后续可引入白名单或窗口规则校准。
- signing_check 暂无前端 UI 面板（API-first）；候选项：web 端体检报告面板。
- scripts 下 e2e/smoke 临时数据库建议改用系统临时目录，避免与主库同目录。

## [2.0.1] - 2026-08-24

### Fixed（全维度检修包）

- **lint 清零**：修复 260 处 ruff 错误（unused-import / 行超长 / import 排序等，涉及 packages / tests / scripts 共 23 个文件），`ruff check packages tests scripts` 恢复 All checks passed，CI 后端 job 恢复可过；story_state/service.py 的 facade re-export 统一加 `# noqa: F401` 防止自动修复误删。
- **CI 补前端 job**：`.github/workflows/ci.yml` 新增 web job（Node 20，npm ci → build → vitest），此前 195 个前端测试不在 CI 内。
- **版本号元数据对齐 2.0.0**：pyproject.toml / apps/web/package.json / packages/core/api/main.py `__version__` 三处由 0.1.0 同步为 2.0.0；test_health 与 test_spa_hosting 的版本断言改为引用 `__version__` 常量，后续升版只改一处。
- **文档一致性**：docs/data-model/data-model-v0.md 加现状注记（v0 的 28 张表 → 当前 34 张业务表 + chapter_fts，附增量迁移清单）；IMPLEMENTATION-PLAN §4.2 deviation #4（model-configs 明文返回）标记已关闭（router 层 `_mask_response` 已落地）；补 packages/core/workflow_registry/README.md；README 移除对停维护 apps/desktop/vite.config.ts 的消费性表述。
- **新增 ADR-0002**：「model provider API Key 在本地 SQLite 明文存储（MVP 决策）」，记录既有防线（出口掩码 / ai_call_logs 敏感字段黑名单 / 备份排除 model_configs）与未来升级路径。
- **工作区卫生**：清理 data/ 下 smoke_e2e_* / real_llm_e2e_* 等测试残留文件（主库 novelos.db 不受影响）。

## [2.0.0] - 2026-08-24

结构性升级（路线图：`docs/roadmap/v1.3-v2.x-plan.md`）。含内部架构破坏性调整，对外 API 契约保持兼容。

### Changed（架构）

- story_state god-object 拆分：`service.py` 2332→138 行 façade（`StoryStateService` 全委派 + 私有符号兼容 re-export），按职责拆为 `snapshots` / `deltas` / `write_through` / `commits` / `branches` / `queries` 六模块；调用方零改动、零行为变化（545 项回归全绿证明）。
- 分支读路径快照物化：迁移 `0009` 新增 `branch_snapshots` 表；创建分支物化基线、`promote` 后物化新基线；分支读取 = 最近物化快照 + 增量重放（回退全量重放），消除 O(N) 全量重放；增量计数有 spy 断言守护（增量 2 次 vs 全量 5 次）。
- 双写面统一：canon 写透与 domain CRUD 的实体写入收敛到 `story_state/write_helpers.py` 共享助手（`who_knows` 三态 / JSON 序列化 / 时间戳 / NULL 守卫两路逐字节一致）；`plot_events` / `timeline_events` 因派生索引语义差异保留双写（README 已声明口径：canon 以 observer delta 为权威）。
- 上下文装配缓存：`(project_id, state_version, chapter_no, role, 内容指纹)` 五元键进程内缓存（线程安全、256 上限）；失效三维——`state_version` 推进 + `plan_json` / `scene_plan` 内容指纹变化 + commit 后显式失效兜底。
- 端口收敛：唯一配置源 `Settings.api_port`（`NOVELOS_PORT` > `NOVELOS_API_PORT` > 默认 18081）；`main.py` / `vite.config` / smoke 脚本统一走配置。

### Added

- 设定条件触发动态注入（对标 Lorebook / Codex）：迁移 `0010` 给 `characters` / `locations` / `factions` 加 `aliases` + `inject_mode`（`auto` / `always` / `never`）；命中实体完整注入、未命中降级一行摘要、`never` 剔除；L0 世界规则常驻；`context-preview` 与前端面板带 `full` / `summary` / `suppressed` 徽标。
- FTS5 召回混合层：迁移 `0011` 建 `chapter_fts` 虚表（CJK bigram 索引化，零第三方依赖）；commit 成功后 upsert 索引（失败降级不阻断）；装配注入 `recalled_passages`（按章节计划关键词召回 top3 历史片段 × ≤300 字，跨长程呼应）；新模块 `packages/core/retrieval/`。

### Fixed

- 装配缓存脏命中（审查 P1）：`plan_json` 更新或 `scene_plan` 变化但 `state_version` 不变时返回陈旧上下文——键补内容指纹 + commit 后显式失效。
- FTS 查询含引号 token 静默降级；纯 CJK 长串不再作为无效整词 token 进入召回。

### Known Issues / 路线登记

- vitest 全量并发偶发单测顺序敏感（V1.5 起观察到 2 次，重跑即绿），待定位根因。
- `plot_events` / `timeline_events` 双写面未统一（派生索引语义差异，见 `story_state` README）。
- 物化按 N commit 间隔多次物化未启用（MVP 仅分叉点 / promote 两点）。
- 召回为 FTS5 关键词基线；向量 embedding 召回留待后续（需先选型论证）。

## [1.5.0] - 2026-08-24

架构债务与 UI 补缺包（路线图：`docs/roadmap/v1.3-v2.x-plan.md`）。

### Added

- What-if 分支 UI：项目总览页新增 `BranchesPanel`（分支列表 / 创建 / 分支状态查看 / promote 一键重放，409 冲突反馈）；纯前端落地（后端 `branches` / `state` 端点此前已齐备），补 `branchesApi` 封装与 `getBranchState`。
- workflow 注册表下沉 core：新增 `packages/core/workflow_registry/`（惰性 builder 注册中心），core 业务模块对 `packages.workflows` 零引用（AST + 字面值双重静态扫描测试守护）；`api/main.py` 以 `importlib` 装配触发注册；`packages/workflows` 保留兼容 façade。
- 前端「清除已存密钥」按钮：`AiSettingsPage` 编辑对话框显式清除入口（`clearKeyRequested` 机制，提交 `api_key=''` 触发后端清除；重新输入自动取消清除意图）。

### Changed

- router 越层 SQL 清零：`reference` / `workflows` / `model_configs` 三路由的 SQL 全部下沉——新增 `domain/reference/service.py`（`ReferenceService`）与 `core/model_router/configs.py`（`ModelConfigService`），`chapter` / `workflow_runtime` 各补一个查询函数；路由只留参数校验 + 脱敏 + 错误映射，API 契约零变化。
- `checkpoint_exclude` 推广到章节工作流（resume 影响已逐字段论证并测试守护）：`chapter-review` 剔除 `review_report` / `critic_*`；`chapter-commit` 剔除 `observer_input` / `observer_payload` / `delta` / `submit_result` / `snapshot_pre`；`pause_payload` 独立不受影响，checkpoint 写放大下降。

### Fixed

- （无用户可见修复；两项代码健壮性整理：`delete_canon_cascade` 连接关闭统一 finally、`runs.py` 函数定义位置规范）

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
