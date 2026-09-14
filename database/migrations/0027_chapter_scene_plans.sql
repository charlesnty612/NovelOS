-- =============================================================================
-- NovelOS Database Migration 0027: chapter_scene_plans（章节场景计划落库面）
--
-- 背景（P1「规划合并」产品化，2026-09-14）：
--   - 改造前：scene_plan 只活在 chapter-write 的 checkpoint_json 里——由 chapter-write
--     自己的 scene_planner 节点在写作期现算，写完即弃，跨 run 不可复用、不可审计。
--   - 改造后：chapter-plan 的单 AI 节点 director_planner 一次调用产出
--     「导演计划 + scene_plan」双契约（prompt 见 docs/agents/prompts/director_planner-v1.md），
--     scene_plan 需要**同一 run 内**落库：chapter-write 命中即跳过自身 scene_planner
--     调用（省一次 LLM 往返），未命中保留既有单节点调用 + 机械映射降级路径。
--
-- 表语义：
--   - 1 章 1 面（chapter_id UNIQUE）：重跑「生成计划」即覆盖本行；同 run 内 scene_plan
--     缺席（合并调用只回了计划段）时由写入方**删除本行**，避免旧 scene_plan 与新
--     plan_json 不匹配（陈旧场景被下游 writer 消费）。
--   - payload_json：scene_plan 对象原文（schema_version / prompt_version / chapter_id /
--     scenes[] / notes_for_writer / deviations）。读取方只消费 scenes[]，其余字段留作审计。
--   - source / prompt_version / run_id：产出溯源（哪次 run、哪份 prompt 生成），
--     与 ai_call_logs.prompt_version / _reference_canon_consumed 同款「可溯源」口径。
--
-- 幂等策略：
--   - CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS（与 0006 / 0007 一致）；
--   - 纯新增表，不改既有表结构、不回填数据 → 存量库升级后本表为空，
--     chapter-write 读不到 → 回落既有 scene_planner 路径（零行为突变）。
--
-- 删除语义（chapter_id 外键 ON DELETE CASCADE）：
--   chapter_scene_plans 是**派生数据**（由 plan_json 与 prompt 产出，可重生成），
--   不承担「防误删作者内容」的职责——drafts / scenes 等作者产物仍是默认 FK
--   （删除时 IntegrityError → router 409，见 domain.chapter.service.delete docstring）。
--   故本表随章节删除级联清理：保持「计划过但未写过正文的章节仍可删除」的改造前行为，
--   不让场景落库面成为新的删除阻断源。
-- =============================================================================

CREATE TABLE IF NOT EXISTS chapter_scene_plans (
    scene_plan_id  TEXT PRIMARY KEY,                          -- 例: csp_<ulid>
    chapter_id     TEXT NOT NULL UNIQUE,                      -- 1 章 1 面：重规划即覆盖
    project_id     TEXT NOT NULL,                             -- -> projects.project_id
    run_id         TEXT,                                      -- 产出本行的 chapter-plan run（审计可空）
    source         TEXT NOT NULL,                             -- 生产者，当前恒为 'director_planner'
    prompt_version TEXT,                                      -- 例: director_planner:v1
    scene_count    INTEGER NOT NULL,                          -- scenes[] 条数（免解析即可读的规模信号）
    payload_json   TEXT NOT NULL,                             -- scene_plan 对象原文
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE INDEX IF NOT EXISTS idx_chapter_scene_plans_project
    ON chapter_scene_plans(project_id);

-- =============================================================================
-- 迁移结束 (0027)
-- =============================================================================
