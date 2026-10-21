-- =============================================================================
-- NovelOS Database Migration 0033: 模型连接层（连接与档案解耦）
--   - model_connections：连接表（连到哪里 / 怎么认证 / 带哪些头）
--   - model_profiles：加可空列 connection_id（档案引用连接）
--
-- 目标（用户裁决 2026-10-21）：
--   「同一厂商，想分配不同模型承担不同职责，需要要重新完全新建。」
--   ⇒ 现状 model_profiles 把「连接信息」（provider / base_url / api_key）与
--     「模型名」焊在同一条记录里：同一厂商想让 gpt-5.5 写正文、claude-opus 做推理、
--     便宜模型做评审，必须建 3 个档案并把 base_url / api_key 各重填 3 遍
--     —— 改一次 key 要改三处，漏一处就串味。
--   ⇒ 本迁移引入连接层：同一厂商的多个档案**共享一条连接**，建档案时只填模型名。
--
-- 职责划分（本仓口径，连接与档案各管各的）：
--   连接级（model_connections）——「连到哪里、怎么认证、带哪些头」：
--       provider / base_url / api_key / headers / models_url
--   档案级（model_profiles.params_json）——「用什么模型、怎么调」：
--       model / thinking / reasoning_effort / timeout_s / temperature / max_tokens / seed
--   ⚠️ timeout_s 归**档案**级而非连接级：不同模型响应速度不同，允许不同 timeout；
--     连接级 timeout 会在同厂商多模型时产生「哪个档案的值算数」的歧义。
--
-- 设计要点：
--   - **只加不改**：model_profiles 只 ADD COLUMN（可空）⇒ 存量档案 connection_id
--     为 NULL 时**运行时行为逐位不变**（仍走原 params_json 路径）。
--   - **不加外键**：连接被档案引用时的删除约束走应用层 409（照既有
--     `profile_in_use` 范式，见 packages/core/api/routers/model_profiles.py），
--     避免 SQLite「ALTER TABLE ADD COLUMN 带 REFERENCES」需重建表。
--
-- ⚠️⚠️ **本迁移刻意「不做自动回填」—— 先验判断被推翻，必须留痕**（2026-10-21）：
--   初版方案是「按 (provider, base_url, api_key) 三元组分组自动回填
--   connection_id」。**该方案是错的**，理由：
--   1. 生效 key 的权威解析链是 `secrets_store.resolve_profile_api_key`，5 级：
--        secrets[profile_id] → secrets[provider] → secrets[env_name]
--        → params_json.api_key → 环境变量
--      **最高两级读的是 secrets.json（一个文件），不在 DB 里** ⇒ SQL 迁移
--      根本看不见它们，只能看见 params_json 里的那个字段。
--   2. 本仓 secrets.json **现存的条目正是档案级**（实测：`mprof_f95e3c7b4127`）
--      ⇒ 存在「两个档案的 params_json.api_key 相同、但生效 key 不同」的真实情形
--      （档案级 secrets 覆盖档案 params 里的 key）。
--   3. 于是按 params 里的 key 分组会把这两个档案**合并进同一条连接** ⇒
--      档案 A 开始用档案 B 的 key ⇒ **凭据静默串用**（不报错，表现为某个档案突然 401
--      或计费到别人的号）。
--   ⇒ 结论：**回填必须按「生效 key」分组，而生效 key 只有运行期的解析函数算得出来**
--     （SQL 做不到，因为它要读文件）⇒ 回填改为**应用层的显式归组动作**：
--     分组键由 `resolve_profile_api_key` 在运行期算出，**先展示分组预览给用户确认**，
--     用户点头后才写 connection_id。
--     （实现见 packages/core/model_router/effective.py::group_profiles_by_connection
--      与 POST /model-profiles/group-by-connection。）
--
-- ⚠️ JSON 安全（AGENTS 坑区「一行脏 JSON 让整个读路径 500」的同族）：
--   本迁移**不解析**任何 params_json ⇒ 脏 JSON 行完全不受影响
--   （保持 connection_id = NULL ⇒ 走存量路径 ⇒ 零行为变化）。
--
-- 幂等：CREATE TABLE 带 IF NOT EXISTS（与 0007/0008 一致）；
--       ADD COLUMN 无 IF NOT EXISTS —— 迁移 runner 按文件粒度幂等记录
--       （packages/core/db.py::apply_migrations），第二次跑不会重放本文件。
-- =============================================================================

-- 1) model_connections 表
CREATE TABLE IF NOT EXISTS model_connections (
    connection_id TEXT PRIMARY KEY,                       -- mconn_<12hex>
    name         TEXT NOT NULL,                          -- 显示名，如 "opencode · Go"
    provider     TEXT NOT NULL,                          -- openai_compatible / anthropic / ollama / mock
    base_url     TEXT,                                   -- 连接级 base_url（anthropic / ollama 可空用默认）
    params_json  TEXT NOT NULL DEFAULT '{}',             -- api_key / headers / models_url
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- 2) model_profiles 加引用列（可空 ⇒ 存量兼容）
--    ⚠️ 无 REFERENCES：删除约束走应用层 409（见文件头「设计要点」）。
ALTER TABLE model_profiles
    ADD COLUMN connection_id TEXT;

-- 3) 索引：model_profiles.connection_id 有一条真实查询路径
--    ——「列出某连接下的档案」+「删除连接前的引用检查」。
--    （按 0016 迁移注释的口径：确有查询路径才建索引。）
CREATE INDEX IF NOT EXISTS idx_model_profiles_connection_id
    ON model_profiles(connection_id);

-- =============================================================================
-- 迁移结束 (0033)
-- =============================================================================
