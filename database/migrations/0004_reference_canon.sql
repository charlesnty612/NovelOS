-- =============================================================================
-- NovelOS Database Migration 0004: reference_canon (Sprint 11 上半)
--   - 拆书工作流 deconstruct-book 的产出持久化（PRD §8 必须重新设计清单 D1）
--   - 参考文档：
--       docs/reference-canon/reference-canon-v0.md §6.1（metadata 文件头）
--       docs/reference-canon/schemas/reference-canon.schema.json（schema 权威）
--   - 表设计：
--       reference_canons —— ReferenceCanon 主表；一条 = 一本书的参照系
--           canon_json   存 ReferenceCanon JSON 全文（含 metadata）
--           report_md    存 T4 渲染的 Markdown 报告（人读）
--           status       active / archived（不删除，只归档——PRD §102 Local Only）
--       canon_extracts —— T2 逐章 ChapterExtract 落库；保留证据链
--           UNIQUE(canon_id, chapter_index) 防止 T2 重跑时同章重复
-- =============================================================================

CREATE TABLE reference_canons (
    canon_id        TEXT PRIMARY KEY,                        -- 例: can_<ulid>
    project_id      TEXT NOT NULL,                            -- -> projects.project_id
    title           TEXT NOT NULL,                            -- 内部追踪名（§6.1 source_book_title）
    reader_profile  TEXT NOT NULL DEFAULT 'male_fantasy',    -- 读者档枚举
    canon_json      TEXT NOT NULL,                            -- ReferenceCanon 全文（含 metadata）
    report_md       TEXT NOT NULL DEFAULT '',                 -- T4 人读报告
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','archived')),
    created_at      TEXT NOT NULL,                            -- ISO-8601
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE INDEX idx_reference_canons_project_id   ON reference_canons(project_id);
CREATE INDEX idx_reference_canons_project_created
    ON reference_canons(project_id, created_at DESC);

CREATE TABLE canon_extracts (
    extract_id      TEXT PRIMARY KEY,                         -- 例: cex_<ulid>
    canon_id        TEXT NOT NULL,                            -- -> reference_canons.canon_id
    chapter_index   INTEGER NOT NULL,                         -- 章号
    extract_json    TEXT NOT NULL,                            -- T2 ChapterExtract JSON
    created_at      TEXT NOT NULL,
    UNIQUE (canon_id, chapter_index),
    FOREIGN KEY (canon_id) REFERENCES reference_canons(canon_id)
);

CREATE INDEX idx_canon_extracts_canon_id ON canon_extracts(canon_id);