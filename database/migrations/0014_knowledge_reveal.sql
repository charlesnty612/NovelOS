-- =============================================================================
-- NovelOS Database Migration 0014: V3.3 知识权限补全
--   - relationships / timeline_events / scenes 三表补齐 visibility + who_knows
--   - reveal_policies 表按 v3.3-v3.5 候选设计文档 §二重建（与 0001 的旧版不兼容）
-- =============================================================================
-- 目标（V3.3 主项「知识权限补全」）：
--   1. 收尾 I6 缺口：relationships / timeline_events / scenes 三表补 visibility +
--      who_knows 字段，对齐 PRD §16 知识权限维度口径（与 characters/locations/
--      factions/world_rules/plot_events/hooks/narrative_debts/chapters 9 表同结构）。
--   2. reveal_policies 表按 v3.3-v3.5 设计文档 §二重建：
--      - target_type → target_kind（CHECK 枚举扩展到 8 种实体）
--      - from_chapter_id/until_chapter_id/policy → status+reveal_by_chapter+audience
--      - 新增 notes 字段（替代原 note）
--      - 索引 idx_rp_target 改为 (project_id, target_kind, target_id) 复合索引
--   3. 老 reveal_policies 行（v1.1 schema：policy 列 FORBIDDEN/HINT_OK/REVEALABLE
--      + from/until 章节窗口）整体 DROP 后重建；当前存量数据样本为空
--      （data/novelos.db 与 data/m1_run/novelos.db 均 0 行，详见 2026-08-26 实测），
--      故直接 DROP+CREATE 无数据损失风险。
--
-- 设计要点：
--   - ALTER TABLE 三表新增列均带 DEFAULT 'PUBLIC' / NULL，对齐 9 张已有表的
--     visibility 字段语义；旧行迁移后默认值生效。
--   - visibility 暂不加 CHECK 约束（避免与既有 9 张表口径差异——既有表也只在
--     characters/locations/factions/world_rules/hooks/chapters 上有 CHECK
--     三态/四态；relationships/timeline_events/scenes 三表新增时按 DDL 惯例
--     仅 DEFAULT，不加约束，容错更宽）。
--   - DROP reveal_policies 后删 idx_reveal_policies_project_id /
--     idx_reveal_policies_target；CREATE 新 idx_rp_target（设计文档命名）。
--   - target_kind CHECK 枚举按设计文档原文：
--     character/location/faction/world_rule/event/hook/debt/relationship
--     （覆盖 8 张可见表；fact 合并入 world_rule；chapter 不参与 policy 因为
--      chapter 是叙事载体本身而非可隐藏实体）。
--   - status CHECK planned/revealed/cancelled：status='revealed' 时 revealed_chapter
--     必须非空（应用层校验；DDL 仅加 CHECK 枚举，NOT NULL 由 service 层强约束）。
--
-- 幂等：ADD COLUMN 无 IF NOT EXISTS；DROP TABLE + CREATE 整体替换不可重入——
--      ``packages.core.db.apply_migrations`` 按文件粒度幂等记录（与 0004/0007
--      风格一致），第二次跑不会重放。
-- =============================================================================

-- 1) 三表补列
ALTER TABLE relationships   ADD COLUMN visibility TEXT NOT NULL DEFAULT 'PUBLIC';
ALTER TABLE relationships   ADD COLUMN who_knows  TEXT;
ALTER TABLE timeline_events ADD COLUMN visibility TEXT NOT NULL DEFAULT 'PUBLIC';
ALTER TABLE timeline_events ADD COLUMN who_knows  TEXT;
ALTER TABLE scenes          ADD COLUMN visibility TEXT NOT NULL DEFAULT 'PUBLIC';
ALTER TABLE scenes          ADD COLUMN who_knows  TEXT;

-- 2) reveal_policies 表重建（v3.3 schema 替代 0001 v1.1 schema）
DROP INDEX IF EXISTS idx_reveal_policies_target;
DROP INDEX IF EXISTS idx_reveal_policies_project_id;
DROP TABLE IF EXISTS reveal_policies;

CREATE TABLE reveal_policies (
    policy_id          TEXT PRIMARY KEY,                  -- rp_<ulid>
    project_id         TEXT NOT NULL REFERENCES projects(project_id),
    target_kind        TEXT NOT NULL
                       CHECK (target_kind IN (
                           'character','location','faction','world_rule',
                           'event','hook','debt','relationship'
                       )),
    target_id          TEXT NOT NULL,
    reveal_by_chapter  INTEGER,                            -- 到该章号必须揭示（NULL=无期限）
    audience           TEXT NOT NULL DEFAULT 'reader',    -- reader / character:<id> 逗号分隔
    status             TEXT NOT NULL DEFAULT 'planned'
                       CHECK (status IN ('planned','revealed','cancelled')),
    revealed_chapter   INTEGER,
    notes              TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX idx_rp_target ON reveal_policies(project_id, target_kind, target_id);

-- =============================================================================
-- 迁移结束 (0014)
-- =============================================================================
