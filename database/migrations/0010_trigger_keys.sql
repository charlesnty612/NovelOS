-- =============================================================================
-- NovelOS Database Migration 0010: Conditional Trigger Keys (V2.0 Wave B · 任务二)
-- =============================================================================
-- 目标：为 canon 实体表（characters / locations / factions）加「触发键」机制，
--   让 Context Engine 不再全量注入实体，而是按"本章相关才注入"：
--   - scan chapter plan / beats / 前章尾段 → 命中实体 name 或 aliases → 触发
--   - inject_mode 三态：auto（默认）/ always（常驻）/ never（不注入仅摘要）
--   - 未命中 auto 实体 → 降级为一行摘要（name + 一句话描述）；never 完全不注入
-- 语义对标 NovelAI Lorebook 关键词触发 + NovelCrafter Codex 4 态。
--
-- 设计要点：
--   - 不破坏现有数据：所有列 ADD COLUMN 兼容旧行（DEFAULT '[]' / 'auto'）。
--   - 不动 L0 的 world_rules：世界规则按 PRD 是硬设定保持常驻，仅实体类做条件化。
--   - aliases 用 TEXT 存 JSON 数组字符串（与 who_knows 一致口径）。
--   - inject_mode CHECK 限制三值（auto/always/never），非法值 → SQLite 写入拒绝。
--   - 迁移幂等：与 0007/0008 一致，ADD COLUMN 无 IF NOT EXISTS；
--     由 packages.core.db.apply_migrations 按文件粒度幂等记录。
-- =============================================================================

-- -----------------------------------------------------------------------
-- characters：加 aliases + inject_mode
-- -----------------------------------------------------------------------
ALTER TABLE characters
    ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]';

ALTER TABLE characters
    ADD COLUMN inject_mode TEXT NOT NULL DEFAULT 'auto'
    CHECK (inject_mode IN ('auto', 'always', 'never'));

-- -----------------------------------------------------------------------
-- locations：加 aliases + inject_mode
-- -----------------------------------------------------------------------
ALTER TABLE locations
    ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]';

ALTER TABLE locations
    ADD COLUMN inject_mode TEXT NOT NULL DEFAULT 'auto'
    CHECK (inject_mode IN ('auto', 'always', 'never'));

-- -----------------------------------------------------------------------
-- factions：加 aliases + inject_mode
-- -----------------------------------------------------------------------
ALTER TABLE factions
    ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]';

ALTER TABLE factions
    ADD COLUMN inject_mode TEXT NOT NULL DEFAULT 'auto'
    CHECK (inject_mode IN ('auto', 'always', 'never'));

-- 索引：触发键扫描时按 project 过滤；不建覆盖索引（实体量小，scan 可接受）
-- 注意：暂不建索引，等真实数据量上来再评估（与 0008 风格一致）。

-- =============================================================================
-- 迁移结束 (0010)
-- =============================================================================