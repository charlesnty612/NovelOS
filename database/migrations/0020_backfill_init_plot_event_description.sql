-- =============================================================================
-- NovelOS Database Migration 0020: 回填 project_init 空壳 plot_event 描述
--   背景：V3.4 init 阶段把卷纲摘要（arc_summary）登记为 type='other' status='planned'
--   的 plot_event。但 create_event 当时不支持 description 参数，arc_summary 被
--   静默丢弃 → 生产库留下一条无描述空壳（event_15c1b360ded3，project=
--   prj_ab772cd586aa，description=NULL，对应 timeline_events 索引行也 NULL）。
--
--   本迁移做三件事：
--   1) volumes 表新增 arc_summary TEXT 列（可空），承接卷纲摘要到 DB。
--      该列是 init 链路上的持久化收口（修复前 arc_summary 只活在 AI 输出 JSON
--      里、pipeline 落地即丢）；persist_all 后续会把 arc_summary 写入该列。
--   2) 回填 volumes.arc_summary：扫 workflow_runs.checkpoint_json 中含
--      volume.arc_summary 的 run（按 chapter_id → chapters.project_id 关联
--      反查 project），取对应卷的 arc_summary 写入 volumes 表对应 number 卷。
--      workflow_runs 不一定每个 project 都保留旧 outline checkpoint（取决于
--      checkpoint 行为），best-effort；取不到的项目保留 arc_summary=NULL。
--   3) 回填 plot_events.description / timeline_events.description：
--      对 type='other' AND status='planned' AND (description IS NULL OR
--      trim(description)='') AND introduced_chapter_id IS NULL 的空壳事件，
--      用其 project 第一卷（number 最小）的 volumes.arc_summary 回填；
--      arc_summary 为空/不存在的行保留 NULL（不强行填占位）。
--
--   同步 UPDATE timeline_events 对应行（event_id 匹配且 description IS NULL）。
--
-- 幂等策略：
--   - ADD COLUMN 不带 IF NOT EXISTS：sqlite3 ALTER TABLE 不支持该语法，
--     由 packages.core.db.apply_migrations 按文件粒度追踪（_migrations 记录）
--     第二次跑直接跳过。
--   - UPDATE 的 WHERE 条件天然幂等：description 非空行不再满足 WHERE，
--     重跑 UPDATE 影响 0 行。
--   - 从 workflow_runs 回填 volumes.arc_summary 用 json_extract + 关联子查询
--     单 SQL 完成；条件按 (project_id, volume number) 取首个命中值，
--     重跑结果不变。
-- =============================================================================

-- 1) volumes 表新增 arc_summary 列（V3.4 / V3.10 init 持久化收口）
ALTER TABLE volumes ADD COLUMN arc_summary TEXT;

-- 2) 从 workflow_runs.checkpoint_json 反向回填 volumes.arc_summary
--    关联路径：workflow_runs.chapter_id → chapters.project_id（同 project 任
--    一 chapter 关联到一条含 arc_summary 的 outline 节点 checkpoint 即取其值）。
--    checkpoint_json 形如 {"volume": {"number": 1, "title": "...", "arc_summary": "..."}, ...}
--    取首个非空 arc_summary 写入对应 number 的 volume（覆盖同 (project, number)
--    已有非 NULL 值——init 链路上的修复后版本会用相同 arc_summary 写入，幂等）。
UPDATE volumes
SET arc_summary = (
    SELECT json_extract(wr.checkpoint_json, '$.volume.arc_summary')
    FROM workflow_runs AS wr
    JOIN chapters AS ch ON ch.chapter_id = wr.chapter_id
    WHERE ch.project_id = volumes.project_id
      AND json_extract(wr.checkpoint_json, '$.volume.arc_summary') IS NOT NULL
      AND trim(json_extract(wr.checkpoint_json, '$.volume.arc_summary')) != ''
      AND json_extract(wr.checkpoint_json, '$.volume.number') = volumes.number
    ORDER BY wr.started_at DESC
    LIMIT 1
)
WHERE EXISTS (
    SELECT 1
    FROM workflow_runs AS wr
    JOIN chapters AS ch ON ch.chapter_id = wr.chapter_id
    WHERE ch.project_id = volumes.project_id
      AND json_extract(wr.checkpoint_json, '$.volume.arc_summary') IS NOT NULL
      AND trim(json_extract(wr.checkpoint_json, '$.volume.arc_summary')) != ''
      AND json_extract(wr.checkpoint_json, '$.volume.number') = volumes.number
);

-- 3) 回填 plot_events.description：用其 project 第一卷（number 最小）的
--    volumes.arc_summary 补 description。仅作用于空壳（type='other' AND
--    status='planned' AND description 空 AND introduced_chapter_id IS NULL
--    → 真正由 init 持久化阶段落的占位事件）。
UPDATE plot_events
SET description = (
    SELECT v.arc_summary
    FROM volumes AS v
    WHERE v.project_id = plot_events.project_id
      AND v.arc_summary IS NOT NULL
      AND trim(v.arc_summary) != ''
    ORDER BY v.number ASC
    LIMIT 1
)
WHERE type = 'other'
  AND status = 'planned'
  AND (description IS NULL OR trim(description) = '')
  AND introduced_chapter_id IS NULL
  AND EXISTS (
      SELECT 1
      FROM volumes AS v
      WHERE v.project_id = plot_events.project_id
        AND v.arc_summary IS NOT NULL
        AND trim(v.arc_summary) != ''
  );

-- 4) 同步 UPDATE timeline_events 对应行（event_id 匹配且 description IS NULL）。
--    timeline_events 由 create_event 同事务自动落，0019 已为 commit 路径回填
--    索引；本步只补 init 空壳对应的索引行。
UPDATE timeline_events
SET description = (
    SELECT v.arc_summary
    FROM plot_events AS pe
    JOIN volumes AS v
      ON v.project_id = pe.project_id
     AND v.arc_summary IS NOT NULL
     AND trim(v.arc_summary) != ''
    WHERE pe.event_id = timeline_events.event_id
    ORDER BY v.number ASC
    LIMIT 1
)
WHERE description IS NULL
  AND EXISTS (
      SELECT 1
      FROM plot_events AS pe
      JOIN volumes AS v
        ON v.project_id = pe.project_id
       AND v.arc_summary IS NOT NULL
       AND trim(v.arc_summary) != ''
      WHERE pe.event_id = timeline_events.event_id
  );

-- =============================================================================
-- 迁移结束 (0020)
-- =============================================================================