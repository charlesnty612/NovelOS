-- =============================================================================
-- NovelOS Database Migration 0032: skills + project_skills（内部消费外部 skill）
-- =============================================================================
-- 背景（本仓**新资源类型**的第一次落地；设计已定，实现照做）：
--   NovelOS 要能**内部消费外部 skill**（外部可移植的「通用手艺」文档，如
--   「对白潜台词写法」「悬念铺垫清单」）。skill 是**跨作品资产**：与
--   ``genre_packs``（题材公约）同类，但**更轻**——它不规定题材配比、不带
--   核销口径，只带「怎么做」的手艺说明 + 可选参考材料 + 可选检查项。
--
-- 与既有资源的关系（三条硬边界）：
--   1. **独立资源、独立 schema 版本线**：``skills`` 自有表 + 自有 payload schema
--      （``docs/state-model/schemas/skill.schema.json``，版本线 ``^skill\.v1\.\d+\.\d+$``）；
--      **不复用** ``genre_packs.payload_json``、**不复用** ``projects.genre_pack_id`` 列
--      （「一列不得两用」——策展面与生成面必须分列）。
--   2. **项目级绑定用关联表而非 JSON 列**：``project_skills`` 是 (project_id, skill_id)
--      关联表 + ``ord`` 决定注入顺序。理由有二：① 一个项目可绑**多个** skill
--      （多 slot 有序），JSON 列做不到有序多值；② 避免「JSON 列里一条脏数据 ⇒
--      读侧退空值 ⇒ 缓存键形态与主路径不同形 ⇒ 脏命中」这一族缺陷（本仓坑区
--      「一行脏 JSON 让整个列表端点 500」与「漏键=脏命中」）。
--   3. **软件层只承载「运行面 payload」**：SKILL.md 全文与 references 全文**属内容仓**
--      （NovelOS-Content），软件仓不解析、不入库。``source_path`` 只是审计用字符串。
--
-- 表设计：
--   skills —— skill 主表；一条 = 一个 skill 的一个版本快照
--       skill_id      主键，形如 ``skl_<12hex>``（默认生成）或调用方显式指定
--       name          显示名
--       description   一句话描述（可空）
--       version       payload 版本号（整数）；PUT 改 payload 时自增，同时是装配缓存键
--                    的指纹维度（漏键=脏命中，见硬规则 2）
--       payload_json  运行面 payload 全文（JSON 字符串，schema 见 skill.schema.json）
--       source_path   内容仓来源路径（审计用，可空；软件层不解析该路径）
--       created_at / updated_at  ISO-8601（now_iso 口径）
--   project_skills —— 项目 ↔ skill 多 slot 有序绑定
--       project_id  FK → projects(project_id)  ON DELETE CASCADE
--       skill_id    FK → skills(skill_id)      ON DELETE CASCADE
--       ord         注入顺序（整数，小在前）
--
-- 外键与 SQLite 兼容性：
--   - 本迁移**只建表**，不改既有表、不回填数据 ⇒ 存量库升级后行为逐字不变。
--   - ON DELETE CASCADE：删项目 ⇒ 绑定行随之消失；删 skill ⇒ 绑定行随之消失
--     （DELETE 端点另有 409 前置拦截：仍被项目绑定时先解绑再删）。
--
-- 幂等策略：
--   - CREATE TABLE / CREATE INDEX 均不带 IF NOT EXISTS（由 packages.core.db.apply_migrations
--     按文件粒度追踪 _migrations，第二次跑整文件跳过）。
--   - 本迁移新增 2 张业务表：业务表 39 → 41（含 _migrations 物理共 42）。
-- =============================================================================

CREATE TABLE skills (
    skill_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT,
    version       INTEGER NOT NULL DEFAULT 1,
    payload_json  TEXT NOT NULL,
    source_path   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- 列表端点按显示名检索（GET /api/skills?name=…）
CREATE INDEX idx_skills_name ON skills(name);

-- 项目级绑定：多 slot 有序（ord 决定注入顺序）。
CREATE TABLE project_skills (
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    skill_id   TEXT NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE,
    ord        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, skill_id)
);

-- 反查：某个 skill 被哪些项目绑定（DELETE 前的 409 判定 + 列表端点的绑定数）
CREATE INDEX idx_project_skills_skill_id ON project_skills(skill_id);

-- =============================================================================
-- 迁移结束 (0032)
-- =============================================================================
