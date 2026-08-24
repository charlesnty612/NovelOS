-- =============================================================================
-- Sprint 12 修补：quality_reports 补 project_id 索引
--   - 现状：0003_quality_reports.sql 仅建 (chapter_id, created_at) 索引，
--     但 packages/core/quality/service.py:list_reports 按 project_id 查询并
--     ORDER BY created_at DESC → 全表扫描；项目下报告数大时性能退化。
--   - 修复：补建 (project_id) 单列索引；查询计划从 scan 转为 ref + sort。
--     不改表结构、不改 DDL；幂等（IF NOT EXISTS），重复执行安全。
--   - 风格：与 0003 一致（DDL 头注释 + 迁移结束标志）。
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_quality_reports_project_id
    ON quality_reports(project_id);

-- =============================================================================
-- 迁移结束 (0006)
-- =============================================================================