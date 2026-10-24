-- =============================================================================
-- NovelOS Database Migration 0035: 连接层「协议地址池」
--   - model_connections 加可空列 protocol_base_urls TEXT
--
-- 目标（用户裁决 2026-10-24，协议下放模型档案）：
--   厂商（opencode/DeepSeek/MiniMax…）≠ 协议（openai_compatible/anthropic/ollama/mock）。
--   同一厂商网关下**不同模型声明不同协议**（实证：opencode Go 网关 GLM/Kimi/DeepSeek 走
--   openai/v1/chat/completions，MiniMax/Qwen/Claude 系走 anthropic/v1/messages，
--   认证头也分平面——OpenAI 平面 Bearer、Anthropic 平面 x-api-key）。
--   ⇒ 协议是**模型级属性**，由模型档案（model_profiles.provider）选择；
--     连接只是厂商网关（地址池 + key + headers），不再选协议、也不再向档案同步协议。
--
--   一条网关往往为每个协议提供不同路径（opencode Go：openai 走
--   /inference/go/openai/v1，anthropic 走 /inference/go/anthropic）⇒ 连接需要
--   一个「协议 → 该网关此协议 base_url」的覆盖表，绑定态解析时按**档案的协议**取址：
--       生效 base_url = protocol_base_urls[档案.provider] ?? 连接.base_url
--   （解析实现见 packages/core/model_router/effective.py::resolve_effective。）
--
-- 设计要点：
--   - **只加不改**：不动 provider / base_url 列（存量连接的「主协议 + 主地址」口径不变）；
--     protocol_base_urls 为 NULL ⇒ 无覆盖，绑定态全部走 base_url ⇒ 存量行零行为突变。
--   - **不回填**：存量连接此前没有协议池概念；opencode 用户如需同连接混跑双协议，
--     在连接编辑里补填即可（预设 opencode_go 已内置两协议地址，一键填表）。
--   - **JSON 覆盖表而非列族**：协议集合封闭（openai_compatible / anthropic / ollama /
--     mock，与 router 工厂四路 dispatch 一一对应），形状校验走写时 422
--     （packages/core/api/routers/connections.py），DB 层不设 CHECK（脏 JSON 读路径
--     按 parse_params 容错为 {} ⇒ 全部走 base_url，与「无覆盖」同语义，不炸）。
--
-- 迁移幂等：ADD COLUMN 无 IF NOT EXISTS —— 迁移 runner 按文件粒度幂等记录
--   （packages/core/db.py::apply_migrations），第二次跑不会重放本文件。
-- =============================================================================

ALTER TABLE model_connections
    ADD COLUMN protocol_base_urls TEXT;

-- =============================================================================
-- 迁移结束 (0035)
-- =============================================================================
