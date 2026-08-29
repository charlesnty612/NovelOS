-- =============================================================================
-- NovelOS Database Migration 0017: 关键表 UNIQUE 兜底（缺陷：缺数据库约束）
--   - relationships 表：无 UNIQUE(from,to,type) —— 重复 add delta 写两条读
--     一条脏数据（审计 docs/testing/audit-story-state-20260829.md §A2）。
--   - workflow_runs 表：原 409 防护是「查-插」非原子（TOCTOU），双 start
--     在窄窗口内可产生双 RUNNING 行（审计 docs/testing/audit-workflows-engine-20260829.md）。
--
-- 兜底策略：
--   1. relationships：先按 (project_id, from, to, type) 去重，保留最早
--      rowid（业务语义：先建立的关系统治当前态；后到同 key 的行视为重复
--      历史/重复 add，应被合并/拒绝）；再 CREATE UNIQUE INDEX 兜底后续
--      并发 add。
--   2. workflow_runs：用部分唯一索引（仅 RUNNING/PENDING 行）兜底 TOCTOU；
--      同 chapter 下只能有一条 RUNNING/PENDING 活跃行；终态行（COMPLETED/
--      FAILED/CANCELLED/PAUSED）可共存——PAUSED 不参与部分索引是因为
--      resume 会把自身重新置 RUNNING，需要让"自己"能落回。
--
-- 风格：参照 0002（drafts_unique.sql）的 IF NOT EXISTS 幂等 + _migrations
-- 表追踪（db.apply_migrations），第二次跑迁移不会被重放。
-- =============================================================================

-- 1) relationships 去重：合并策略注释
--    合并策略：按 (project_id, from_character_id, to_character_id, relation_type)
--    分组，保留 MIN(rowid) —— SQLite rowid 单调递增即代表 INSERT 顺序，
--    "最早"即"先建立的关系统治当前态"语义。state_json / last_state_version
--    不合并（保留最早行的快照；如果业务需要"以最新覆盖"语义，应在应用层
--    二次处理；本次兜底只解决"重复行"问题，不变更 state 语义）。
DELETE FROM relationships
WHERE rowid NOT IN (
    SELECT MIN(rowid) FROM relationships
    GROUP BY project_id, from_character_id, to_character_id, relation_type
);

-- 2) relationships 唯一索引：project + 边的两端 + 关系类型
--    不含 last_state_version（同一对角色同一关系类型只允许一条当前态；
--    历史走 state_deltas + 快照重建，见 state-delta-v0 设计）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_relationships_unique
    ON relationships(project_id, from_character_id, to_character_id, relation_type);

-- 3) workflow_runs 部分唯一索引：同 chapter 下仅允许一条 RUNNING/PENDING 行
--    - 排除 chapter_id IS NULL 的行（非章节任务，如 project-init）
--    - 不覆盖 PAUSED：resume 路径要把自身置回 RUNNING，需要让 "自己" 能
--      落回部分索引范围（详见 _mark_run_running 注释）；resume 入口的并
--      发防护由 API 层 _check_active_run_for_chapter + engine 层 IntegrityError
--      兜底（本次新增）
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_runs_active
    ON workflow_runs(chapter_id)
    WHERE status IN ('RUNNING','PENDING') AND chapter_id IS NOT NULL;

-- =============================================================================
-- 迁移结束 (0017)
-- =============================================================================
