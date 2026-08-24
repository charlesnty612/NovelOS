-- =============================================================================
-- NovelOS Database Migration 0009: Branch Snapshots (V2.0 Wave B)
-- =============================================================================
-- 目标：分支读路径快照物化——消除分支当前状态读取的 O(N) 全量 delta 重放。
--
-- 现状（迁移前）：
--   - branches 表（0001_init.sql line 476-487）记录分支元信息（base_state_version 等）；
--   - commits 表中 branch_id 字段标识分支归属；
--   - 分支当前状态由 ``branches.branch_current_state`` 推导：
--     1) 取 ``branches.base_state_version`` 处的 main story_states 快照作为 base；
--     2) 按 ``commits.branch_id`` 顺序（resulting_state_version ASC）重放本分支 delta，
--        复用 ``apply_delta``，每次读都是 O(N) 全量重放。
--   - 分支越长，读取越慢；分支上 N 个 commit → N 次 apply_delta。
--
-- 新方案（迁移后）：
--   - 新建 ``branch_snapshots`` 表，每个分支最多存一行「最近一次物化的快照」
--     （branch_id UNIQUE）：
--     * branch_id：唯一键，对应 branches.branch_id（FK，main 也可物化）
--     * state_version：物化时分支内版本号（与 commits.resulting_state_version 同序）
--     * snapshot_json：全量 JSON 快照（与 story_states.snapshot_json 同结构）
--     * created_at：ISO-8601 时间戳
--     * UNIQUE(branch_id, state_version)：保留未来按 N commit 间隔多次物化的扩展空间
--       （MVP 不启用，仅在 promote 与 create_branch 时各物化一次）
--   - 物化时机：
--     * ``create_branch`` 创建成功后 → 物化 base_state_version 处的 main 快照到
--       新分支行（state_version=base_state_version）作为「初始空基线」。
--     * ``promote_branch`` 全部重放成功后 → main 分支物化新基线（state_version=
--       新 main latest version）。
--   - 读路径改造（``branches.branch_current_state``）：
--     1) 先查 ``branch_snapshots`` 取最近一次物化（按 state_version DESC LIMIT 1）；
--     2) 若存在：从物化点开始增量重放，仅跑「物化点之后」的分支 commits；
--     3) 若不存在（兼容旧分支）→ 兜底为全量重放（与旧行为一致）。
--   - 主线（branch_id=None / main）走 ``story_states`` 最新版本快照，行为零变化。
--
-- 设计要点：
--   - 新建表（不破坏 story_states 表结构），FK 引用 branches 与 projects；
--   - 幂等（CREATE TABLE / CREATE INDEX / CREATE TRIGGER 均带 IF NOT EXISTS）；
--   - 写权限收敛到 service 层（无 application_id / 无 created_by 字段——属于
--     推导缓存，与 commits 表同语义纯函数性）；
--   - 不含 branch_name 字段——branch_id 已唯一，反查 name 走 branches 表。
--
-- 兼容性：
--   - 旧分支无 branch_snapshots 行 → 读路径自动回退全量重放；
--   - 旧 API 行为零变化（main 路径未动）；
--   - 迁移 runner 按文件粒度幂等，重复 apply 不会重跑（参见 packages/core/db.py）。
-- =============================================================================

CREATE TABLE IF NOT EXISTS branch_snapshots (
    branch_id      TEXT NOT NULL,                              -- -> branches.branch_id（main 也可物化）
    state_version  INTEGER NOT NULL,                           -- 物化时分支内 version（与 commits.resulting_state_version 同序）
    snapshot_json  TEXT NOT NULL,                              -- 全量 Story State JSON（与 story_states.snapshot_json 同结构）
    created_at     TEXT NOT NULL,                              -- ISO-8601
    PRIMARY KEY (branch_id, state_version),
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
);

CREATE INDEX IF NOT EXISTS idx_branch_snapshots_branch_version
    ON branch_snapshots(branch_id, state_version DESC);

-- =============================================================================
-- 迁移结束 (0009)
-- =============================================================================