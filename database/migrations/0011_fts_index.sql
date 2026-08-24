-- =============================================================================
-- NovelOS Database Migration 0011: FTS5 全文检索虚表（V2.0 Wave C · 任务一）
-- =============================================================================
-- 目标：对标社区"状态库定事实 + 检索召回供呼应"的混合方案，落地零依赖版。
--   - 对 chapters.content（已 commit 的章节正文）建 FTS5 虚表，让 Context Engine
--     装配时按当前章节计划文本关键词召回跨长程呼应片段。
--   - 使用 SQLite 原生 FTS5（python 标准库 sqlite3 模块默认编译），零依赖；
--     若本机 SQLite 不含 FTS5，运行时 ``packages.core.retrieval.search()``
--     会直接抛错（fail-fast），禁止 silent fallback 到 LIKE 扫描。
-- 设计要点：
--   - ``chapter_fts`` 内部 content 模式（不挂 external content）：存
--     ``(chapter_id TEXT UNIQUE, content TEXT)``。service 层
--     :func:`packages.core.retrieval.upsert_chapter` 负责写；
--     索引与 chapters 的一致性由 chapter_commit 钩子保证（commit 成功后
--     upsert；失败按 summarize 节点相同语义降级 log warning）。
--   - chapter_summaries 不入 FTS（摘要已是 context_engine 装配另一路输入；
--     召回目标是原文片段以让 writer/director 看到具体叙事语境）。
--   - FTS5 虚表 + 4 个内部表（chapter_fts_config/data/docsize/idx）均登记为
--     ``type='table'``；db.count_tables 与 health.tables 的"业务表"口径
--     由 packages.core.db 在 SELECT 加 ``AND name NOT LIKE 'chapter_fts%'``
--     排除，保持业务表数稳定（34 业务表 + _migrations = 35 总表）。
--   - 幂等：``CREATE VIRTUAL TABLE IF NOT EXISTS`` SQLite 支持；
--     service 层 upsert 用 ``INSERT OR REPLACE``。
-- =============================================================================

-- -----------------------------------------------------------------------
-- 1. FTS5 虚表（内部 content 模式，存 chapter_id + content）
--    chapter_id 作为 UNIQUE 列便于 service 层 UPSERT。
--    content 列存 bigram 化文本（中文按 2-gram + 空格分隔；英文按原 token），
--    让 FTS5 unicode61 按空格切词命中。snippet() 在该列返回 bigram 串，
--    前端展示时由 service 层根据 chapter_id 重新读 drafts.content 截断。
--    索引体积 ≈ 原文 1.5x（不含原 bigram 串按字符数计）；MVP 可接受。
-- -----------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS chapter_fts USING fts5(
    chapter_id UNINDEXED,
    content,
    tokenize='unicode61'
);

-- =============================================================================
-- 迁移结束 (0011)
-- =============================================================================
