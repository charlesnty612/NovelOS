-- =============================================================================
-- NovelOS Database Migration 0015: V3.4 多卷与规模（组织层）
--   - volumes 表：卷级组织层（active / sealed）
--   - chapters.volume_id：章节归属外键（可空，旧章节不强制归属）
--   - 不动 story_state 快照构建逻辑（明确不做按卷拆快照，O-1 滚动窗口已
--     实现上下文有界，本版只做组织管理层）
-- =============================================================================
-- 目标（V3.4 主项「多卷与规模」）：
--   1. 增加 volumes 表承载卷（volume）CRUD 与封存归档能力；
--      status 枚举 active / sealed；UNIQUE(project_id, number) 防重号。
--   2. chapters.volume_id TEXT REFERENCES volumes(volume_id) 可空
--      （旧章节不强制回填）；本章归属是组织层信息，不影响 chapter 的业务主键
--      （chapter_id 仍全局唯一，迁移不破坏既有引用）。
--   3. terminal_snapshot_json TEXT（封存时由 service 层把 story_states 最新
--      快照 JSON 写入；DDL 仅落列，不做自动触发；语义对齐 v3.3-v3.5 候选设计
--      文档 §三「快照分代」）。
--
-- 设计要点：
--   - volume_id TEXT PRIMARY KEY，约定前缀 ``vol_``（由 new_id("vol") 生成）。
--   - status CHECK 枚举 active/sealed（与 DDL 给死，service 层通过显式方法
--     seal() / update() 控制状态机；DDL 不预判状态机迁移约束）。
--   - terminal_snapshot_json TEXT 可空；sealed 时 service 层写入
--     ``json.dumps(snapshot, ensure_ascii=False)`` 序列化结果；active 时 NULL。
--   - chapter→volume FK：ON DELETE SET NULL（删 volume 时章节自动脱钩，
--     与业务约束「同一章只能属一卷」一致——删卷自然解绑）。
--   - 索引：project_id 单列索引 + (project_id, status) 复合索引（list /
--     找 active / seal 时按 status 过滤）+ chapters.volume_id 外键索引
--     （arc 装配按卷聚合）。
--
-- 幂等：ADD COLUMN 无 IF NOT EXISTS；CREATE TABLE 无 IF NOT EXISTS；
--      ``packages.core.db.apply_migrations`` 按文件粒度幂等记录
--      （与 0014 风格一致），第二次跑不会重放。
-- =============================================================================

-- 1) volumes 表
CREATE TABLE volumes (
    volume_id              TEXT PRIMARY KEY,                 -- vol_<ulid>
    project_id             TEXT NOT NULL REFERENCES projects(project_id),
    number                 INTEGER NOT NULL,
    title                  TEXT,
    status                 TEXT NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active','sealed')),
    terminal_snapshot_json TEXT,                             -- seal 时冻结 story_states 最新快照 JSON
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    UNIQUE(project_id, number)
);

-- 2) chapters.volume_id 外键列（可空，旧章节不强制回填）
ALTER TABLE chapters ADD COLUMN volume_id TEXT REFERENCES volumes(volume_id) ON DELETE SET NULL;

-- 3) 索引
CREATE INDEX idx_volumes_project_id     ON volumes(project_id);
CREATE INDEX idx_volumes_project_status ON volumes(project_id, status);
CREATE INDEX idx_chapters_volume_id     ON chapters(volume_id);

-- =============================================================================
-- 迁移结束 (0015)
-- =============================================================================