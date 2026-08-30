-- =============================================================================
-- NovelOS Database Migration 0022: relationships 表 FK 移除（faction 端点修复）
--   + commits.rollback_of 部分唯一索引
--
-- 背景（修复 wfr_6619a7bfa6fa）：
--   - 0001_init.sql 第 190-191 行为 relationships 表声明了两个 FK：
--       FOREIGN KEY (from_character_id) REFERENCES characters(character_id)
--       FOREIGN KEY (to_character_id)   REFERENCES characters(character_id)
--     SQLite 在 PRAGMA foreign_keys=ON 下（packages/core/db.py:34 强制开启）会强制
--     校验这两列必须存在 characters 表中。faction 端点（fac_ 前缀）按设计就是
--     「组织关系」语义——但 FK 把它当成 character_id 引用，提交时撞 FK 即
--     IntegrityError，validator/applier 侧已扩为 characters ∪ factions，但 DB
--     层 FK 仍是 P0 拦截。
--   - 解决方案：重建 relationships 表，去掉 from/to 两个 FK 子句（保留 project_id
--     的 FK，因为项目边界仍由 projects 表托管）；其余列定义、0014 补的
--     visibility/who_knows 列、0017 的部分唯一索引全部沿用。
--   - 同步附带 P1 修复：给 commits.rollback_of 加部分唯一索引，让双重回滚在
--     SQLite 层被拦截；与 0017 idx_relationships_unique 同款风格。
--
-- 幂等策略：
--   - 主防线：apply_migrations 的 _migrations 表追踪（执行成功后才落档）。
--   - 次防线：本文件内 CREATE TABLE IF NOT EXISTS / DROP TABLE IF EXISTS /
--     CREATE INDEX IF NOT EXISTS 守卫，让单独重跑（_migrations 缺失但 DDL
--     部分生效的边界场景）也能跑到一致终点。
--   - 表切换块（替身→正式名）依赖 _migrations 幂等追踪：首次执行成功
--     后 _migrations 记录本文件 → 后续 apply_migrations 直接跳过；
--     若首次中途失败 → _migrations 未记录 → 重跑会再执行；IF EXISTS
--     守卫让 DROP/INDEX 在「已成功」场景下 0 行影响。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 步骤 0：存量数据去重（生产实锤：cmt_6c9faba70b8c 在幂等守卫上线前被回滚两次，
--   rollback_of 存在重复值，直接建唯一索引会炸）。对每组重复 rollback_of 保留
--   最早一行，其余行的 rollback_of 追加 '#dup<rowid>' 后缀——审计行保留不删，
--   幂等守卫的 WHERE rollback_of=? 精确匹配语义不受影响（后缀值永不被查询）。
-- -----------------------------------------------------------------------------
UPDATE commits
SET rollback_of = rollback_of || '#dup' || rowid
WHERE rollback_of IS NOT NULL
  AND rowid NOT IN (
      SELECT MIN(rowid) FROM commits
      WHERE rollback_of IS NOT NULL
      GROUP BY rollback_of
  );

-- -----------------------------------------------------------------------------
-- 步骤 1：建替身表 relationships_new（去 FK、其余列定义保持）
--   IF NOT EXISTS 守卫。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS relationships_new (
    relationship_id     TEXT PRIMARY KEY,                       -- 例: rel_<ulid>
    project_id          TEXT NOT NULL,
    from_character_id   TEXT NOT NULL,
    to_character_id     TEXT NOT NULL,
    relation_type       TEXT NOT NULL,
    state_json          TEXT NOT NULL DEFAULT '{}',
    last_state_version  INTEGER NOT NULL,
    visibility          TEXT NOT NULL DEFAULT 'PUBLIC'
                        CHECK (visibility IN ('PUBLIC','VISIBLE','RESTRICTED','HIDDEN')),
    who_knows           TEXT,
    -- 端点可以是 character_id 或 faction_id（wfr_6619a7bfa6fa 修复）；
    -- 端点身份合法性由 validator/applier 层把关（characters ∪ factions 集合），
    -- DB 层仅负责 project 边界一致性。
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- -----------------------------------------------------------------------------
-- 步骤 2：从旧表搬运数据到替身表（首次执行有效；重跑场景旧表已被 DROP）
--   SQLite 「INSERT SELECT FROM 不存在表」会报「no such table」（即使 WHERE 0 行），
--   所以用 EXISTS 探测旧表 + 仍直接 SELECT FROM，旧表存在时正常，不存在时
--   _migrations 主防线已跳过整个文件。
-- -----------------------------------------------------------------------------
INSERT INTO relationships_new
    (relationship_id, project_id, from_character_id, to_character_id,
     relation_type, state_json, last_state_version, visibility, who_knows)
SELECT relationship_id, project_id, from_character_id, to_character_id,
       relation_type, state_json, last_state_version, visibility, who_knows
FROM relationships
WHERE EXISTS (
    SELECT 1 FROM sqlite_master WHERE type='table' AND name='relationships'
);

-- -----------------------------------------------------------------------------
-- 步骤 3：原子切换——DROP 旧表 + ALTER 替身 RENAME 为正式名
--   DROP 走 IF EXISTS 守卫；RENAME 不支持 IF EXISTS，靠 _migrations 主防线
--   保证首次成功后才不再执行。
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS relationships;
ALTER TABLE relationships_new RENAME TO relationships;

-- -----------------------------------------------------------------------------
-- 步骤 4：重建全部索引（0017 部分唯一索引逐字保留 + 0001 标准索引）
--   IF NOT EXISTS 守卫。
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_relationships_project_id
    ON relationships(project_id);
CREATE INDEX IF NOT EXISTS idx_relationships_from_character
    ON relationships(from_character_id);
CREATE INDEX IF NOT EXISTS idx_relationships_to_character
    ON relationships(to_character_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_relationships_unique
    ON relationships(project_id, from_character_id, to_character_id, relation_type);

-- -----------------------------------------------------------------------------
-- 步骤 5：commits.rollback_of 部分唯一索引（P1 修复）
--   IF NOT EXISTS 守卫。
-- -----------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS idx_commits_rollback_of
    ON commits(rollback_of)
    WHERE rollback_of IS NOT NULL;

-- =============================================================================
-- 迁移结束 (0022)
-- =============================================================================