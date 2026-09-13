-- =============================================================================
-- NovelOS Database Migration 0024: quality_reports.draft_version（V3.9 批次 4.3）
--   - 目的：改稿重评后历史报告能对应到具体草稿版本（drafts.version）。
--     QualityReport 加可选字段 draft_version，落库时取当前最新 draft 的 version。
--   - 仅 ALTER TABLE 加列，不增表（总表 38 不变）；存量行保持 NULL
--     （语义=未记录/未知，读侧按可空处理）。
-- =============================================================================

ALTER TABLE quality_reports
    ADD COLUMN draft_version INTEGER;
