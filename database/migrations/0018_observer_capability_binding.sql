-- =============================================================================
-- NovelOS Database Migration 0018: observer 独立 capability 绑定（V3.9.3）
--   背景：原 AGENT_CAPABILITY["observer"] = "reasoning"；chapter_commit 的
--   observer 双腿在 pipeline.py 里硬编码 ``capability_override="light"`` 走 light
--   链，导致 observer 实际跑 light 模型，但前端把 observer 展示在 reasoning 组
--   ——展示与实际失真（B1 类缺陷）。
--   修复：把 observer 拆为独立 capability（见
--   ``packages/core/model_router/router.py`` 的
--   ``AGENT_CAPABILITY["observer"] = "observer"`` 与 ``CAPABILITY_LABELS`` 新增
--   observer 条目），双腿显式 ``capability_override="observer"``。
--
-- 迁移职责（仅绑定，不动 capability 解码）：
--   - capability_bindings 表中若还没有 ``observer`` 行，则用 reasoning 行的
--     profile_ids 派生一条，线上行为与改动前一致（observer 一直跑 reasoning
--     的同一份绑定档案，或拆分前的 light 链——但拆分前依赖 capability_override
--     硬编码 light，迁移不动该语义；本迁移只补 observer 行的初始绑定）。
--   - 若 ``observer`` 行已存在（重复 apply / 手工预置），保持原值不动。
--   - reasoning 行不动。
--
-- 幂等：INSERT OR IGNORE（按 capability 主键去重），主键命中即跳过；
--       ``packages.core.db.apply_migrations`` 按文件粒度追踪（0016 风格），
--       第二次跑迁移不会被重放。
-- =============================================================================

INSERT OR IGNORE INTO capability_bindings (capability, profile_ids, updated_at)
SELECT 'observer', profile_ids, datetime('now')
FROM capability_bindings
WHERE capability = 'reasoning';

-- =============================================================================
-- 迁移结束 (0018)
-- =============================================================================
