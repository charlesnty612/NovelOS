-- =============================================================================
-- NovelOS Database Migration 0037: workflow_runs.project_id（项目级并发守卫的归属列）
--
-- 背景（C-1/C-2，2026-10-24）：
--   outline-batch 是项目级工作流（workflow_runs.chapter_id = NULL），其 API 层并发
--   守卫（routers/workflows/control.py::_start_outline_batch）原实现有两个缺陷：
--     ① 守卫查询无项目过滤——workflow_runs 没有可用的 project_id 列，跨项目也 409
--        （项目 A 有活跃 run 时项目 B 被误拦）；
--     ② 「查守卫 → 起 run」是两个连接上的两步（check-then-act），并发 N 个 POST
--        实测 N 个 201 + 重复章号（项目级 run 不受 0017 部分唯一索引
--        idx_workflow_runs_active 约束——该索引只覆盖 chapter_id 非空的行）。
--
-- 列语义：
--   - project_id TEXT 可空。写行时由引擎带出（engine._insert_run_row 新参
--     project_id）；NULL = 0037 前历史行中 checkpoint 里没有 project_id 的
--     （如 agent_runtime.runner.create_adhoc_run 的裸 run），或
--     project-init「新建项目」场景下起跑时项目还不存在。
--   - 守卫（outline-batch 项目级原子守卫，见 engine._insert_run_row 的
--     BEGIN IMMEDIATE 事务）按本列过滤 ⇒ detail 引用的 run_id 必属本项目。
--
-- 回填（与 0026/0031/0035 的「无回填」不同，本条**有**可靠回填依据）：
--   - workflow_runs.checkpoint_json 是 ctx 快照（顶层键 = initial_ctx 键），
--     chapter 级工作流 / outline-batch / project-init（挂载模式）的 ctx 顶层都有
--     project_id ⇒ ``json_extract(checkpoint_json, '$.project_id')`` 能把存量行
--     的归属逐位恢复出来，不是推断、是原文拷贝；
--   - checkpoint_json = '{}' 或无该键（adhoc 裸 run）⇒ json_extract 返回 NULL
--     ⇒ 保持 NULL（不造假）。
--
-- 幂等策略：
--   - ADD COLUMN 不带 IF NOT EXISTS（sqlite3 不支持）；由 packages.core.db.apply_migrations
--     按文件粒度追踪（_migrations 记录），第二次跑整文件跳过。
--   - 纯加列 + 一次性回填，**不增表**：业务表仍 42 / 含 _migrations 仍 43。
--   - 只加不改（AGENTS 硬规则 6）：不改动既有列，存量行为零突变。
-- =============================================================================

ALTER TABLE workflow_runs ADD COLUMN project_id TEXT;

-- 回填：checkpoint_json 顶层 project_id 原文拷贝（NULL 保持 NULL）。
UPDATE workflow_runs
SET project_id = json_extract(checkpoint_json, '$.project_id')
WHERE project_id IS NULL
  AND json_extract(checkpoint_json, '$.project_id') IS NOT NULL;

-- =============================================================================
-- 迁移结束 (0037)
-- =============================================================================
