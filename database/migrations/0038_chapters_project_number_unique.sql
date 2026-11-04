-- =============================================================================
-- NovelOS Database Migration 0038: chapters(project_id, number) 唯一约束
--
-- 缺陷（C-28，批次三十六 实证）：
--   0001_init.sql:549 只建了**普通**索引 ``idx_chapters_project_number``，
--   同 project_id 同 number 的两行可以直接插入成功。实测（临时库）：
--     INSERT ... (project_id='prj_a', number=1) 两次 ⇒ 两行都在。
--   正常路径（project-init / outline-batch）走 ``max(number)+1`` 不会重号，
--   所以重号只有**并发**可达——而并发恰好是「起两个 outline-batch / 两批建章」
--   最容易发生的场景（0037 的项目级守卫只按 run 归属拦，不能替代数据约束）。
--
-- 修法：建 UNIQUE INDEX ``idx_chapters_project_number_unique``
--   **不 DROP 旧索引**（AGENTS 硬规则 6「只加不改」）：旧索引是 0001 的历史产物，
--   删它属于改动既有 DDL，且它对 project_id 单列查询仍有用；多一个索引的成本
--   对本机单用户工具可忽略。旧索引名保留，新索引名带 ``_unique`` 后缀以示区别。
--
-- ⚠️ **存量脏数据防御（必须在建索引之前）**：
--   若库内**已经**存在同 (project_id, number) 的多行，直接 CREATE UNIQUE INDEX
--   会失败（``UNIQUE constraint failed``），整条迁移链从此卡死。
--   处置口径（确定序、可复算）：
--     1. 分组键 = (project_id, number)；组内按 (created_at ASC, rowid ASC) 排序，
--        **首行保留原号**（"先建立的章占号"），其余行视为重号；
--     2. 重号行按 (原 number, created_at, rowid) 全局排序，依次改成
--        ``该项目的 max(number)+1, +2, ...``（保证不撞既有号）；
--     3. 只改 ``number`` 列，**不动** title / plan_json / 正文等任何内容列；
--        也不改 ``updated_at``（这是一次完整性元数据修复，不是作者内容变更；
--        留痕在 ``_migrations`` 与 CHANGELOG）。
--   这是**软件层完整性元数据**的修复，与三仓分离纪律不冲突（不接触正文）。
--
-- 幂等策略：
--   - 迁移记在 _migrations（文件粒度）⇒ 第二次跑整文件跳过；
--   - 脚本自身也可重跑：TEMP 表在连接关闭后消失，重跑时若已无重号，
--     TEMP 表为空、UPDATE 命中 0 行，CREATE UNIQUE INDEX IF NOT EXISTS 无副作用。
--
-- 不增表：业务表仍 42 / 含 _migrations 仍 43（只加索引 + 一次性数据修复）。
-- =============================================================================

-- 1) 找出「同 project_id + number」里的非首行，并算出它们的新号。
--    窗口函数需要 SQLite >= 3.25（本仓 Python 3.12 自带 3.37+）。
CREATE TEMP TABLE _chapters_dup_renumber AS
SELECT
    d.chapter_id AS chapter_id,
    m.max_no + ROW_NUMBER() OVER (
        PARTITION BY d.project_id
        ORDER BY d.number ASC, d.created_at ASC, d.rowid ASC
    ) AS new_number
FROM (
    SELECT
        chapter_id,
        project_id,
        number,
        created_at,
        rowid,
        ROW_NUMBER() OVER (
            PARTITION BY project_id, number
            ORDER BY created_at ASC, rowid ASC
        ) AS dup_rank
    FROM chapters
) AS d
JOIN (
    SELECT project_id, MAX(number) AS max_no
    FROM chapters
    GROUP BY project_id
) AS m ON m.project_id = d.project_id
WHERE d.dup_rank > 1;

-- 2) 重编号（只改 number 列）。
UPDATE chapters
SET number = (
    SELECT r.new_number
    FROM _chapters_dup_renumber AS r
    WHERE r.chapter_id = chapters.chapter_id
)
WHERE chapter_id IN (SELECT chapter_id FROM _chapters_dup_renumber);

DROP TABLE _chapters_dup_renumber;

-- 3) 唯一约束兜底（后续并发 INSERT 在 SQLite 层直接 IntegrityError）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_chapters_project_number_unique
    ON chapters(project_id, number);

-- =============================================================================
-- 迁移结束 (0038)
-- =============================================================================
