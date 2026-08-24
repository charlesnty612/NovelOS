-- =============================================================================
-- Sprint 15 (V1.3)：
--   1. author_style_samples：项目级「个人文风样例」库（每条 1 篇散文）
--      - writer context 注入新源：取该项目最近 ≤2 篇，每篇截断 ≤1000 字，
--        引导 writer 模仿句式 / 用词 / 节奏（非内容）。
--      - CRUD：GET /api/projects/{pid}/style-samples（列表）、POST（新增）、
--        DELETE /api/projects/{pid}/style-samples/{sid}（删除）。
--      - 校验：单篇 content ≤ 5000 字（超限 → 422），单项目 ≤ 10 篇（超限 → 422）。
--   2. projects.foreshadow_overdue_chapters：项目级 overdue 阈值，默认 30。
--      - 读取时 fallback 至该常量（与原 _FORESHADOW_OVERDUE_CHAPTERS=30 对齐）。
--      - 不破坏性：仅 ADD COLUMN 兼容旧行（DEFAULT 30）。
--   3. 与 0007 一致：幂等（CREATE TABLE / CREATE INDEX 均带 IF NOT EXISTS），
--      ADD COLUMN 无 IF NOT EXISTS；迁移 runner 按文件粒度幂等记录，
--      重复 apply_migrations 不会重跑本文件（参见 packages/core/db.py）。
-- =============================================================================

CREATE TABLE IF NOT EXISTS author_style_samples (
    sample_id     TEXT PRIMARY KEY,                         -- 例: asty_<ulid>
    project_id    TEXT NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,                            -- ≤ 5000 字（API 层校验）
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE INDEX IF NOT EXISTS idx_author_style_samples_project_created
    ON author_style_samples(project_id, created_at DESC);

ALTER TABLE projects
    ADD COLUMN foreshadow_overdue_chapters INTEGER NOT NULL DEFAULT 30
    CHECK (foreshadow_overdue_chapters > 0);

-- =============================================================================
-- 迁移结束 (0008)
-- =============================================================================
