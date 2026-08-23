-- =============================================================================
-- Sprint 10：branches.status 增加 ARCHIVED 枚举值（用于 Simulation 清理）
--   - 现状：0001_init.sql 定义 branches.status CHECK (status IN ('ACTIVE','MERGED','DISCARDED'))
--   - 需求：What-if Simulation 在创建临时分支、跑完假设 delta 后，
--     需要把分支标记为「已归档、不再接受写入」，但不丢失历史供 GET /simulations 重放。
--     新增 'ARCHIVED' 状态，复用 branches 表（无需新表 / 新列），与 'MERGED'/'DISCARDED'
--     并列；ARCHIVED 同样不可写入（_resolve_branch 校验 status == 'ACTIVE' 已覆盖）。
--   - SQLite 不支持直接 ALTER CHECK；用「12-step 重建表」标准手法：
--       1) 关闭 FK
--       2) 备份数据
--       3) DROP 原表
--       4) 重建（含新 CHECK）
--       5) 拷回数据
--       6) 重建索引
--       7) 重新启用 FK
--   - 幂等：用 _migrations 表保证只跑一次（Sprint 0 设计），不会重复破坏。
--   - 风格：与 0002/0003/0004 一致（DDL 头注释 + 迁移结束标志）。
-- =============================================================================

PRAGMA foreign_keys = OFF;

CREATE TABLE branches_new (
    branch_id           TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    name                TEXT NOT NULL,
    parent_branch_id    TEXT,
    base_state_version  INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE','MERGED','DISCARDED','ARCHIVED')),
    created_at          TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (parent_branch_id) REFERENCES branches_new(branch_id)
);

INSERT INTO branches_new
    (branch_id, project_id, name, parent_branch_id, base_state_version, status, created_at)
SELECT branch_id, project_id, name, parent_branch_id, base_state_version, status, created_at
FROM branches;

DROP TABLE branches;

ALTER TABLE branches_new RENAME TO branches;

CREATE INDEX IF NOT EXISTS idx_branches_project_id         ON branches(project_id);
CREATE INDEX IF NOT EXISTS idx_branches_parent_branch_id   ON branches(parent_branch_id);

PRAGMA foreign_keys = ON;

-- =============================================================================
-- 迁移结束 (0005)
-- =============================================================================
