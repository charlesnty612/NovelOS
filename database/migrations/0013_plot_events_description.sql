-- =============================================================================
-- NovelOS Database Migration 0013: plot_events.description
--   （V3.1 P1-1.1 遗留项：observer 输出的 new_events[].description 下沉）
-- =============================================================================
-- 目标：把 observer 在 ``new_events[]`` 给出的 ``description`` 字段下沉到
--   ``plot_events`` 表，使「DB 权威快照重建」后事件描述不再丢失。
--
-- 背景：
-- - 既有 ``plot_events`` 表没有 description 列，``write_through`` 也不写它
--   → snapshot.events[eid].description 在 ``rebuild_snapshot_collections_from_db``
--   路径下必为 None（即便是 observer 本次 delta 给出的描述也会丢失）。
-- - 此次新增可空 TEXT 列，缺省 NULL（与历史行的「无描述」语义对齐；observer
--   本次未填 description 时写穿亦为 NULL）。
--
-- 设计要点：
-- - 不强制 NOT NULL：可空列表达「该事件未给出描述」与「本次 observer 未填」
--   的二态语义，与 Schema ``new_events.description: string|null`` 对齐。
-- - 不增索引：description 暂不参与查询过滤/排序，按需后置。
-- - 不破坏现有数据：ADD COLUMN + DEFAULT NULL；旧行 description=NULL，重建
--   后 ``snapshot.events[eid].description`` 即为 None（行为与 0012 同）。
-- - 列位置：SQLite ADD COLUMN 仅支持追加，新列落在表尾，不影响既有字段顺序。
-- - 幂等：ADD COLUMN 无 IF NOT EXISTS，由 ``packages.core.db.apply_migrations``
--   按文件粒度幂等记录（与 0010/0012 风格一致）。
-- =============================================================================

ALTER TABLE plot_events
    ADD COLUMN description TEXT;

-- =============================================================================
-- 迁移结束 (0013)
-- =============================================================================