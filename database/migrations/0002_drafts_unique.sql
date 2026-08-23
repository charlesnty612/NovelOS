-- =============================================================================
-- Sprint 5 review F2：drafts 增加 (chapter_id, version) 唯一索引
--   - 原因：CREATE TABLE drafts 时未声明 UNIQUE；ChapterService.create_draft 当前依赖
--     ``COALESCE(MAX(version),0)+1`` 计算 version，并发场景或手工 INSERT 仍可能产生
--     重复 (chapter_id, version)。加唯一索引兜底，触发即 sqlite3.IntegrityError，
--     由 ChapterService 转 DraftVersionConflict，router 转 409。
--   - 风格：用 ``IF NOT EXISTS`` 幂等，与现有 _migrations 表 + apply_migrations 协作；
--     重跑 runner 已通过 ``_migrations`` 跳过。
-- =============================================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_drafts_chapter_version
    ON drafts(chapter_id, version);
