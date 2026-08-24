# 更新日志（CHANGELOG）

> **留痕规矩（自 V1.0 起强制）**：此后所有迭代——功能、修复、迁移、行为变更——合并前必须在本文件追加条目。
> 格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)：版本号 + 日期 + 分类小节
> （Added 新增 / Changed 变更 / Fixed 修复 / Removed 移除 / Migration 迁移 / Known Issues 已知问题）。
> 版本号语义化：破坏性变更升 major，新功能升 minor，修复升 patch。

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
  model-configs 列表 api_key 脱敏；checkpoint_exclude 推广到章节工作流（需先论证 resume 影响）；
  workflow 注册表下沉 core；router 越层 SQL（reference/workflows/model_configs）抽 service。
- **V2.x**：story_state/service.py（2300+ 行）按职责拆分；分支读路径 O(N²) 重放的快照物化；
  story_state 写透与 domain CRUD 的双写面统一；context engine 装配缓存；端口变量收敛。
- 不计划做：packages/agents 空骨架强填充（角色逻辑当前内联于各 pipeline，已在 README 标注）。
