-- =============================================================================
-- Sprint 14：章节摘要链（chapter-commit 落库 + Context Engine 装配）
--   - 现状：chapter-commit 提交后无摘要留存；Context Engine L1 只能通过
--     recent_prose 取上一章末尾 500 字，无法支持「最近 N 章递进式摘要」的
--     长篇一致性装配需求（与天命/Morpheus/Sudowrite 标配对齐）。
--   - 新增 chapter_summaries 表：每次 chapter-commit 成功后落一行
--     (project_id, chapter_id, chapter_no, summary ≤ 200 字, tail_text 300 字,
--      created_at)；查最近 5 章按 chapter_no 倒序拼接。
--   - 摘要失败降级：chapter-commit summarize 步骤异常时仍允许 commit 成功；
--     该章 chapter_summaries 行缺失由 context_engine 装配侧容错（无行 = 无摘要）。
--   - 幂等：CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS；与 0006 一致。
--   - 风格：DDL 头注释 + 迁移结束标志。
-- =============================================================================

CREATE TABLE IF NOT EXISTS chapter_summaries (
    summary_id     TEXT PRIMARY KEY,                          -- 例: sum_<ulid>
    project_id     TEXT NOT NULL,
    chapter_id     TEXT NOT NULL,                             -- -> chapters.chapter_id
    chapter_no     INTEGER NOT NULL,
    summary        TEXT NOT NULL,                             -- ≤ 200 字；超长由调用方截断
    tail_text      TEXT NOT NULL,                             -- 已提交正文最后 300 字（不调 LLM）
    created_at     TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id)
);

CREATE INDEX IF NOT EXISTS idx_chapter_summaries_project_no
    ON chapter_summaries(project_id, chapter_no);

-- =============================================================================
-- 迁移结束 (0007)
-- =============================================================================