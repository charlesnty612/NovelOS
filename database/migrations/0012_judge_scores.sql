-- =============================================================================
-- NovelOS Database Migration 0012: quality_reports.judge_json
--   （V3.1 P1-2：LLM judge 双轨并排落库）
-- =============================================================================
-- 目标：在 ``quality_reports`` 表新增 ``judge_json TEXT`` 列，把
--   ``scripts/m2_judge.py``（M2 章质量评审探针）的四维评分（pacing / style /
--   logic / dialogue 0-100）+ verdict + top_issues + score_avg + usage 等元数据
--   单独落库，与现有「七子分公式」双轨并存。
--
-- 设计要点：
-- - 双轨语义：judge_json 不参与 overall 计算，也不进入 ``scores_json``；
--   ``scoring_formula_hash`` 仅由 scores_json 派生（保持现有哈希稳定
--   V3.1 公式不变 → 哈希不变）。LLM judge 视为「旁路探针数据」。
-- - 不破坏现有数据：ADD COLUMN + DEFAULT NULL；旧行 judge_json = NULL，读端
--   表现为 ``"judge": null``，前端可见「未评审」/「评审中」二态。
-- - 不强制 NOT NULL：可表达"该 chapter 暂未跑过 M2 评审"的状态。
-- - 不增索引：judge_json 暂不参与查询过滤/排序，按需后置。
-- - 幂等：ADD COLUMN 无 IF NOT EXISTS，由 ``packages.core.db.apply_migrations``
--   按文件粒度幂等记录（与 0007/0010 风格一致）。
-- =============================================================================

ALTER TABLE quality_reports
    ADD COLUMN judge_json TEXT;

-- =============================================================================
-- 迁移结束 (0012)
-- =============================================================================
