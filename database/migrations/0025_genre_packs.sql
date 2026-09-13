-- =============================================================================
-- NovelOS Database Migration 0025: genre_packs（题材库 P1a，独立资源地基）
-- =============================================================================
-- 背景（用户专项需求 2026-09-13，评估与架构裁决见
--   docs/roadmap/题材库-评估与落地计划-2026-09-13.md §四）：
--   - 「题材库」= 跨作品聚合的题材公约资产（结构模板 / 爽点分类法 / 节奏与配比公约），
--     与 reference_canons（单部参照作品的描述性拆解产物）是**两种资源**：
--     前者规定性、项目级常驻；后者描述性、叠加参照。故不共用表 / 不共用 schema 版本线，
--     避免拆书链路与题材链路互相掣肘。
--   - 双 slot 并存：projects.genre_pack_id（题材包常驻）+ reference_canons 的 active
--     绑定（参照书叠加）。两者互不覆盖。
--   - 分层红线：题材正文（含审核禁忌清单）只进内容仓
--     （NovelOS-Content/genres/<题材>/）；软件仓只承载机制——本表存的是结构化 payload
--     （消费子集：爽点分类 / 结构模板 / 节奏 / 配比 / 文风约束），非题材正文。
--
-- 表设计：
--   genre_packs —— 题材包主表；一条 = 一个题材包的一个版本快照
--       pack_id      主键，形如 genre-male-quicktrans-v1（内容仓 slug）或 gp_<12hex>
--       name         题材包显示名（如「男主快穿」）
--       genre_tag    题材标签（检索维度；一个包一个主标签）
--       version      payload 版本号（整数）；PUT 更新时自增，同时是 context 装配缓存键
--                    的指纹维度（漏键=脏命中，见 V3.9 批次 1B 教训）
--       payload_json 结构化 payload 全文（JSON 字符串，schema 见
--                    docs/state-model/schemas/genre-pack.schema.json，版本线 v1.0.0）
--       source_path  内容仓来源路径（审计用，可空；软件仓不解析该路径）
--       created_at / updated_at  ISO-8601（now_iso 口径）
--   projects.genre_pack_id —— 项目级题材包绑定（单 slot；NULL = 未绑定）
--
-- 外键与 SQLite 兼容性：
--   - ``ALTER TABLE ... ADD COLUMN ... REFERENCES`` 要求新增列默认值为 NULL——
--     本列无 DEFAULT，SQLite 视为 NULL，满足约束（与 0015
--     ``chapters.volume_id TEXT REFERENCES volumes(volume_id) ON DELETE SET NULL`` 同款）。
--   - ON DELETE SET NULL：绑定时删除由 service / router 层前置拦截（409）；外键兜底
--     保证即使有旁路删除也不留悬空引用。
--
-- 幂等策略：
--   - CREATE TABLE 不带 IF NOT EXISTS、ADD COLUMN 也不带 IF NOT EXISTS（sqlite3 的
--     ALTER TABLE 不支持该语法）；由 packages.core.db.apply_migrations 按文件粒度追踪
--     （_migrations 记录），第二次跑整文件跳过。
--   - 本迁移新增 1 张表：业务表 37 → 38（含 _migrations 物理共 39）。
-- =============================================================================

CREATE TABLE genre_packs (
    pack_id      TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    genre_tag    TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL,
    source_path  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- 列表端点按题材标签过滤（GET /api/genre-packs?genre_tag=…）
CREATE INDEX idx_genre_packs_genre_tag ON genre_packs(genre_tag);

-- 项目级绑定：单 slot，NULL = 未绑定；解绑即置 NULL。
ALTER TABLE projects
    ADD COLUMN genre_pack_id TEXT REFERENCES genre_packs(pack_id) ON DELETE SET NULL;

-- 绑定反查（GET /projects/{pid}/genre-pack 与 DELETE 前的「有项目绑定则 409」）
CREATE INDEX idx_projects_genre_pack_id ON projects(genre_pack_id);

-- =============================================================================
-- 迁移结束 (0025)
-- =============================================================================
