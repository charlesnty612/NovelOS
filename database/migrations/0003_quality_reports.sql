-- =============================================================================
-- NovelOS Database Migration 0003: quality_reports（Sprint 6 下半）
--   - Quality Engine 评估报告的持久化：与 chapter-commit pipeline 的 quality_gate
--     节点对齐；落地 issue 集合 + 六子分 + overall + _meta（spec §1.1/§1.3/§2.2）。
--   - 与 evaluations（PRD §38，DB 字段名固定）的差异：evaluations 是 Sprint 0
--     早期 PR 写定的「只存七子分 + guardrail_results」，schema 不兼容 Quality
--     v0 的 issues[] + _meta.scoring_version；本迁移新建独立表，evaluation 走
--     quality_reports（Service 用 ``packages.core.quality.service`` 入口）。
-- =============================================================================

CREATE TABLE quality_reports (
    report_id    TEXT PRIMARY KEY,                          -- 例: qr_<ulid>
    project_id   TEXT NOT NULL,
    chapter_id   TEXT NOT NULL,
    commit_id    TEXT,                                      -- 关联 commit；门禁场景为 NULL
    run_id       TEXT,                                      -- workflow_run_id
    overall      INTEGER NOT NULL,
    scores_json  TEXT NOT NULL,                             -- 六子分 + _meta
    issues_json  TEXT NOT NULL,                             -- Issue[]
    created_at   TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id)
);

CREATE INDEX idx_quality_reports_chapter ON quality_reports(chapter_id, created_at);
