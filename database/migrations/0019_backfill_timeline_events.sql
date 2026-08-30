-- =============================================================================
-- NovelOS Database Migration 0019: 回填 timeline_events 索引（V3.1 P1-1.1 B3）
--   背景：write_through new_events 此前未同步 timeline_events（修复见 B2），
--   导致生产库 plot_events 中已有 5 条 commit 事件（evt_ch_*）未在
--   timeline_events 留下索引行 → 右侧时间线断供。本迁移只回填存量缺口，
--   不动新事件（新增路径由 write_through B2 修复负责）。
--
-- 幂等策略：
--   - id 生成：``'tle_bf_' || substr(event_id, 5, 16)``（确定性规则）。
--     event_id 形如 ``evt_<12hex>``，从第 5 位起取 16 字符足够避免冲突
--     （生产 event_id 长度 16）；同一 event_id 多次回填得到同一 id，
--     重跑迁移靠 INSERT 前的 ``WHERE NOT EXISTS`` 守卫整体跳过。
--   - 守卫：``WHERE NOT EXISTS (SELECT 1 FROM timeline_events WHERE event_id = pe.event_id)``，
--     若该 event 已存在索引行（运维手工补 / 新路径已写），回填跳过。
--   - day_index：从 ``plot_events.time_json`` 解析 ``$.timeline_day``；
--     非 int（observer 历史上偶发非标输入）→ 跳过该行，不报错，避免迁移阻塞。
--   - description：复用 ``plot_events.description``（同一事件同源语义）。
--   - time_ref：从 ``plot_events.time_json`` 解析 ``$.in_story_date``（字符串原样存）。
--   - visibility/who_knows：复用 ``plot_events.visibility`` 与 ``who_knows``
--     （timeline_events DDL 有相同列；commit 路径 B2 已对齐该口径）。
--
-- 设计要点：
--   - 仅 INSERT 缺口行，不 UPDATE 已有行（避免覆盖运维手工修正）。
--   - 单 SQL 由 ``packages.core.db.apply_migrations`` 按文件粒度追踪，第二次
--     apply 因 ``_migrations`` 已记录直接跳过 → 整个迁移幂等；脚本内
--     ``WHERE NOT EXISTS`` 是双保险（防止手工清掉 _migrations 后重放导致重复）。
-- =============================================================================

INSERT INTO timeline_events (
    timeline_event_id, project_id, event_id, day_index,
    time_ref, description, visibility, who_knows
)
SELECT
    'tle_bf_' || substr(pe.event_id, 5, 16) AS timeline_event_id,
    pe.project_id,
    pe.event_id,
    CAST(json_extract(pe.time_json, '$.timeline_day') AS INTEGER) AS day_index,
    json_extract(pe.time_json, '$.in_story_date') AS time_ref,
    pe.description,
    pe.visibility,
    pe.who_knows
FROM plot_events AS pe
WHERE
    -- 仅回填有 timeline_day 的事件（无 day_index 的事件不算时间线事件）
    json_extract(pe.time_json, '$.timeline_day') IS NOT NULL
    -- 必须是整数（防御 observer 历史非标输入）
    AND typeof(json_extract(pe.time_json, '$.timeline_day')) = 'integer'
    AND CAST(json_extract(pe.time_json, '$.timeline_day') AS INTEGER) >= 0
    -- 幂等守卫：该 event 还没有索引行才插入
    AND NOT EXISTS (
        SELECT 1 FROM timeline_events te WHERE te.event_id = pe.event_id
    );

-- =============================================================================
-- 迁移结束 (0019)
-- =============================================================================
