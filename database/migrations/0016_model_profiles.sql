-- =============================================================================
-- NovelOS Database Migration 0016: 模型档案库 + 环节绑定（两层架构）
--   - model_profiles：模型档案库（与 capability 解耦；同一模型可被多环节复用）
--   - capability_bindings：环节 → 档案列表（顺序即 fallback 序）
--   - 不动 model_configs（仅读兼容期保留）；新代码优先走 model_profiles + bindings
-- =============================================================================
-- 目标（V3.7「模型档案 + 环节绑定」）：
--   1. 把 model_configs 表里 capability 绑死导致的「同模型给两环节用需复制两份」
--      痛点消解：
--      - model_profiles 只存「模型是什么」（name / provider / model / params_json / enabled）；
--      - capability_bindings 存「环节用谁」（capability → [profile_id, ...] 顺序即 fallback）；
--      - 两者通过 profile_id 关联；同一 profile 可被多个 capability 引用。
--   2. ModelRouter 新增 ``_candidates(capability)``：
--      - 优先查 bindings + profiles（命中即用，不再走 model_configs）；
--      - 无 binding 时回落 model_configs（保持旧路由兼容）；
--      - 返回行键名与 model_configs 完全一致（config_id=profile_id，capability=本 capability），
--        保证 runner 把 config_id 写 ai_call_logs 不需要 schema 改动。
--   3. 迁移存量数据：把每条 model_configs 行转一条 model_profiles 档案（name 取 model 字段）；
--      capability_bindings 表留空（不自动绑定——避免语义错配；前端/运维按需显式 PUT）。
--
-- 设计要点：
--   - profile_id TEXT PRIMARY KEY，约定前缀 ``mprof_``；迁移派生 ID =
--     ``'mprof_' || replace(config_id,'cfg_','')``，确保从 config_id 可追溯原记录。
--   - params_json / model / provider 与 model_configs 列口径一致（字符串原样）。
--   - capability_bindings.capability 主键直接用 capability 名（与
--     model_router.AGENT_CAPABILITY values + reasoning/creative_writing/light 七项一致）。
--   - profile_ids TEXT 存 JSON 数组（顺序即 fallback 序）；
--     service 层 ``json.loads`` 解析；不在 SQL 内做数组运算。
--   - 时间戳 datetime('now') 生成 UTC ISO-8601 字符串（与 model_configs.created_at 兼容）。
--
-- 幂等：CREATE TABLE 无 IF NOT EXISTS；
--       ``packages.core.db.apply_migrations`` 按文件粒度幂等记录
--       （与 0015 风格一致），第二次跑不会重放。
-- =============================================================================

-- 1) model_profiles 表
CREATE TABLE model_profiles (
    profile_id  TEXT PRIMARY KEY,                          -- mprof_<12hex>
    name        TEXT NOT NULL,                             -- 显示名，如 "MiniMax-M3"
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- 2) capability_bindings 表
CREATE TABLE capability_bindings (
    capability  TEXT PRIMARY KEY,                          -- 环节标识
    profile_ids TEXT NOT NULL,                             -- JSON 数组：[主档, 备选1, ...]
    updated_at  TEXT NOT NULL
);

-- 3) 索引
-- capability_bindings 仅按主键（capability）精确查找；不做额外索引。
-- model_profiles.profile_id 即主键；按 profile_id 命中/批量读取已走主键索引。
-- 留空索引列表（与 0001 / 0015 风格一致：仅在确有查询路径时建索引）。

-- 4) 存量迁移：把每条 model_configs 行转一条 model_profiles 档案
--    - profile_id：'mprof_' || replace(config_id,'cfg_','') —— 确定性、可追溯
--    - name      ：COALESCE(model, provider || '/' || model) —— 旧行 model 必有，非空
--    - params_json / provider / model / enabled：原样
--    - created_at / updated_at：datetime('now')（迁移执行时刻），同一文件一次性导入
INSERT INTO model_profiles (profile_id, name, provider, model, params_json, enabled, created_at, updated_at)
SELECT
    'mprof_' || replace(config_id, 'cfg_', ''),
    COALESCE(NULLIF(model, ''), provider || '/' || model),
    provider,
    model,
    params_json,
    enabled,
    datetime('now'),
    datetime('now')
FROM model_configs;

-- 5) capability_bindings 表留空（不自动绑定；前端/运维显式 PUT 写入）

-- =============================================================================
-- 迁移结束 (0016)
-- =============================================================================
