-- =============================================================================
-- NovelOS Database Migration 0001: Initial Schema (28 tables)
-- =============================================================================
-- 目标范式：MVP Local-first Desktop SQLite (对齐 PRD §66)
-- 依赖规范：
--   - PRD §16-22 (领域模型)
--   - PRD §67  共 25 张 + v1.1 新增 3 张（workflow_run_nodes / ai_call_logs / reveal_policies）= 28 张
--   - PRD §75  (迁移纪律：只允许加列/加表，不允许破坏性变更)
--   - PRD §86  (Guardrail 五硬门槛)
--   - PRD §91  (Commit 字段)
--   - PRD §93  (AI 调用日志)
--   - docs/state-model/state-delta-v0.md §2.2 (Delta 元信息)
--   - docs/state-model/state-delta-v0.md §6.2 (state_version 严格 +1/commit，主会话拍板)
--   - docs/state-model/knowledge-permission-v0.md §1.2 (九张表权限字段落点)
--   - docs/state-model/schemas/state-delta.schema.json (字段权威名)
--   - docs/state-model/schemas/state-commit.schema.json (字段权威名)
-- 设计原则：
--   - TEXT 主键、人类可读前缀 (char_/event_/hook_/debt_/commit_/...)
--   - 可查询/需约束字段做独立列；领域子对象用 JSON TEXT 列 (Service 层序列化)
--   - PRAGMA: foreign_keys=ON, journal_mode=WAL
--   - 时间戳: ISO-8601 TEXT (与 state-delta/state-commit schema 的 date-time 对齐)
--   - visibility 四级枚举: PUBLIC / VISIBLE / RESTRICTED / HIDDEN (PRD §4 原则7)
-- =============================================================================

-- ============================================================
-- PRAGMA（主会话方案：头部即开启 FK 检查与 WAL）
-- ============================================================
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- =============================================================================
-- 1. projects (项目根)
-- =============================================================================
CREATE TABLE projects (
    project_id     TEXT PRIMARY KEY,                       -- 例: prj_<ulid>
    name           TEXT NOT NULL,
    premise        TEXT,
    genre          TEXT,
    target_words   INTEGER,
    status         TEXT NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('ACTIVE','PAUSED','ARCHIVED')),
    created_at     TEXT NOT NULL,                          -- ISO-8601
    updated_at     TEXT NOT NULL
);

-- =============================================================================
-- 2. characters (角色定义侧: PRD §16 §17 Definition)
--    权限字段: 统一 visibility + who_knows (主会话拍板, NULL=沿用默认)
--    字段级权限 (core/arc) 由 Service 层在 core_json/arc_json 内部做过滤
-- =============================================================================
CREATE TABLE characters (
    character_id   TEXT PRIMARY KEY,                       -- 例: char_<ulid>
    project_id     TEXT NOT NULL,
    name           TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'supporting'
                  CHECK (role IN ('protagonist','antagonist','supporting','mentor','love_interest','narrator','other')),
    core_json      TEXT NOT NULL DEFAULT '{}',             -- personality/values/fears/desires/flaws
    visibility     TEXT NOT NULL DEFAULT 'PUBLIC'
                  CHECK (visibility IN ('PUBLIC','VISIBLE','RESTRICTED','HIDDEN')),
    who_knows      TEXT,                                    -- NULL=沿用默认; JSON 字符串数组
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- =============================================================================
-- 3. character_states (角色状态侧: PRD §16 §17 State，快照追加不覆盖)
--    权限字段: 统一 visibility + who_knows (主会话拍板)
-- =============================================================================
CREATE TABLE character_states (
    character_id   TEXT NOT NULL,
    state_version  INTEGER NOT NULL,
    state_json     TEXT NOT NULL DEFAULT '{}',             -- location/goal/emotion/knowledge/beliefs/health/resources
    visibility     TEXT NOT NULL DEFAULT 'VISIBLE'
                  CHECK (visibility IN ('PUBLIC','VISIBLE','RESTRICTED','HIDDEN')),
    who_knows      TEXT,                                    -- NULL=沿用默认; JSON 字符串数组
    created_at     TEXT NOT NULL,
    PRIMARY KEY (character_id, state_version),
    FOREIGN KEY (character_id) REFERENCES characters(character_id)
);

-- =============================================================================
-- 4. locations (世界: 地点, PRD §18)
--    权限字段: 统一 visibility + who_knows (主会话拍板)
-- =============================================================================
CREATE TABLE locations (
    location_id   TEXT PRIMARY KEY,                       -- 例: loc_<ulid>
    project_id    TEXT NOT NULL,
    name          TEXT NOT NULL,
    statement     TEXT NOT NULL DEFAULT '',                -- 一句话陈述
    data_json     TEXT NOT NULL DEFAULT '{}',             -- 详细描述/层级/气候
    visibility    TEXT NOT NULL DEFAULT 'PUBLIC'
                  CHECK (visibility IN ('PUBLIC','VISIBLE','RESTRICTED','HIDDEN')),
    who_knows     TEXT,                                    -- NULL=沿用默认; JSON 字符串数组
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- =============================================================================
-- 5. factions (世界: 势力, PRD §18)
--    权限字段: 统一 visibility + who_knows (主会话拍板)
-- =============================================================================
CREATE TABLE factions (
    faction_id    TEXT PRIMARY KEY,                       -- 例: fac_<ulid>
    project_id    TEXT NOT NULL,
    name          TEXT NOT NULL,
    statement     TEXT NOT NULL DEFAULT '',
    data_json     TEXT NOT NULL DEFAULT '{}',
    visibility    TEXT NOT NULL DEFAULT 'VISIBLE'
                  CHECK (visibility IN ('PUBLIC','VISIBLE','RESTRICTED','HIDDEN')),
    who_knows     TEXT,                                    -- NULL=沿用默认; JSON 字符串数组
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- =============================================================================
-- 6. world_rules (世界: 规则/力量体系, PRD §18)
--    权限字段: 统一 visibility + who_knows (主会话拍板)
-- =============================================================================
CREATE TABLE world_rules (
    world_rule_id TEXT PRIMARY KEY,                       -- 例: wrule_<ulid>
    project_id    TEXT NOT NULL,
    name          TEXT NOT NULL,
    statement     TEXT NOT NULL DEFAULT '',                -- 规则陈述
    data_json     TEXT NOT NULL DEFAULT '{}',
    visibility    TEXT NOT NULL DEFAULT 'PUBLIC'
                  CHECK (visibility IN ('PUBLIC','VISIBLE','RESTRICTED','HIDDEN')),
    who_knows     TEXT,                                    -- NULL=沿用默认; JSON 字符串数组
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- =============================================================================
-- 7. plot_events (剧情事件, PRD §19 Plot Graph)
--    权限字段: 统一 visibility + who_knows (主会话拍板)
--    注意: introduced_chapter_id 引用 chapters.chapter_id, 但 chapters 在下方定义,
--          SQLite 启用 FK 时会按表名解析, 不依赖建表顺序
-- =============================================================================
CREATE TABLE plot_events (
    event_id                TEXT PRIMARY KEY,             -- 例: event_<ulid>
    project_id              TEXT NOT NULL,
    type                    TEXT NOT NULL
                            CHECK (type IN ('revelation','conflict','decision','encounter','transition','other')),
    cause_json              TEXT NOT NULL DEFAULT '[]',   -- [event_id...]
    effects_json            TEXT NOT NULL DEFAULT '[]',   -- [event_id...]
    participants_json       TEXT NOT NULL DEFAULT '[]',   -- [character_id...]
    location_id             TEXT,                         -- -> locations.location_id
    time_json               TEXT NOT NULL DEFAULT '{"timeline_day":1}',  -- {timeline_day, in_story_date}
    status                  TEXT NOT NULL DEFAULT 'planned'
                            CHECK (status IN ('planned','recorded','resolved','abandoned')),
    introduced_chapter_id   TEXT,                         -- -> chapters.chapter_id
    visibility              TEXT NOT NULL DEFAULT 'RESTRICTED'
                            CHECK (visibility IN ('PUBLIC','VISIBLE','RESTRICTED','HIDDEN')),
    who_knows               TEXT,                          -- NULL=沿用默认; JSON 字符串数组
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (location_id) REFERENCES locations(location_id),
    FOREIGN KEY (introduced_chapter_id) REFERENCES chapters(chapter_id)
);

-- =============================================================================
-- 8. timeline_events (时间线: 按 day_index 排序的事件索引, PRD §20)
-- =============================================================================
CREATE TABLE timeline_events (
    timeline_event_id   TEXT PRIMARY KEY,                 -- 例: tle_<ulid>
    project_id          TEXT NOT NULL,
    event_id            TEXT NOT NULL,                    -- -> plot_events.event_id
    day_index           INTEGER NOT NULL,                 -- 故事内第几天
    time_ref            TEXT,                             -- 可选 HH:MM 等
    description         TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (event_id) REFERENCES plot_events(event_id)
);

-- =============================================================================
-- 9. relationships (关系当前态, 历史由 delta+快照重建)
--    不加权限字段 (knowledge-permission §1.2 末段)
-- =============================================================================
CREATE TABLE relationships (
    relationship_id     TEXT PRIMARY KEY,                 -- 例: rel_<ulid>
    project_id          TEXT NOT NULL,
    from_character_id   TEXT NOT NULL,
    to_character_id     TEXT NOT NULL,
    relation_type       TEXT NOT NULL,                    -- ally / enemy / lover / family ...
    state_json          TEXT NOT NULL DEFAULT '{}',       -- intensity / since_chapter ...
    last_state_version  INTEGER NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (from_character_id) REFERENCES characters(character_id),
    FOREIGN KEY (to_character_id) REFERENCES characters(character_id)
);

-- =============================================================================
-- 10. hooks (伏笔台账, PRD §21 五态)
-- =============================================================================
CREATE TABLE hooks (
    hook_id                     TEXT PRIMARY KEY,          -- 例: hook_<ulid>
    project_id                  TEXT NOT NULL,
    name                        TEXT NOT NULL,
    introduced_chapter_id       TEXT,                      -- -> chapters.chapter_id
    status                      TEXT NOT NULL DEFAULT 'OPEN'
                                CHECK (status IN ('OPEN','ACTIVE','ESCALATED','RESOLVED','ABANDONED')),
    importance                  REAL NOT NULL DEFAULT 0.5
                                CHECK (importance >= 0.0 AND importance <= 1.0),
    expected_payoff_chapter_id  TEXT,                      -- -> chapters.chapter_id
    payoff_chapter_id           TEXT,                      -- -> chapters.chapter_id
    visibility                  TEXT NOT NULL DEFAULT 'RESTRICTED'
                                CHECK (visibility IN ('PUBLIC','VISIBLE','RESTRICTED','HIDDEN')),
    who_knows                   TEXT,                                    -- NULL=沿用默认/实体现状; '[]'=显式置空 (主会话拍板 2026-08-23, 对齐 kp §3.1)
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- =============================================================================
-- 11. narrative_debts (叙事债务, PRD §22 + knowledge-permission §1.2 扩展枚举)
-- =============================================================================
CREATE TABLE narrative_debts (
    debt_id                TEXT PRIMARY KEY,               -- 例: debt_<ulid>
    project_id             TEXT NOT NULL,
    description            TEXT NOT NULL,
    created_chapter_id     TEXT,                           -- -> chapters.chapter_id
    severity               REAL NOT NULL DEFAULT 0.5
                           CHECK (severity >= 0.0 AND severity <= 1.0),
    deadline_chapter_id    TEXT,                           -- -> chapters.chapter_id
    status                 TEXT NOT NULL DEFAULT 'open'
                           CHECK (status IN ('open','acknowledged','paid','forgiven')),
    visibility             TEXT NOT NULL DEFAULT 'RESTRICTED'
                           CHECK (visibility IN ('PUBLIC','VISIBLE','RESTRICTED','HIDDEN')),
    who_knows              TEXT,                                   -- NULL=沿用默认/实体现状; '[]'=显式置空 (主会话拍板 2026-08-23, 对齐 kp §3.1)
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- =============================================================================
-- 12. chapters (章节, PRD §44 Chapter Planner)
-- =============================================================================
CREATE TABLE chapters (
    chapter_id    TEXT PRIMARY KEY,                        -- 例: ch_<ulid>
    project_id    TEXT NOT NULL,
    number        INTEGER NOT NULL,                       -- 章号
    title         TEXT,
    plan_json     TEXT NOT NULL DEFAULT '{}',              -- chapter_goal/conflict/turning_point/character_change/scenes
    status        TEXT NOT NULL DEFAULT 'PLANNED'
                  CHECK (status IN ('PLANNED','DRAFTED','REVIEWED','COMMITTED','RELEASED')),
    visibility    TEXT NOT NULL DEFAULT 'VISIBLE'
                  CHECK (visibility IN ('PUBLIC','VISIBLE','RESTRICTED','HIDDEN')),
    who_knows     TEXT,                                              -- NULL=沿用默认/实体现状; '[]'=显式置空 (主会话拍板 2026-08-23, 对齐 kp §3.1)
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- =============================================================================
-- 13. scenes (场景, PRD §45 Scene Planner)
-- =============================================================================
CREATE TABLE scenes (
    scene_id      TEXT PRIMARY KEY,                        -- 例: sc_<ulid>
    chapter_id    TEXT NOT NULL,
    order_index   INTEGER NOT NULL,
    plan_json     TEXT NOT NULL DEFAULT '{}',              -- purpose/characters/location/conflict/turn/slots
    FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id)
);

-- =============================================================================
-- 14. drafts (草稿正文, PRD §116 结构化原则下的最终 prose 文本)
-- =============================================================================
CREATE TABLE drafts (
    draft_id        TEXT PRIMARY KEY,                     -- 例: dr_<ulid>
    chapter_id      TEXT NOT NULL,
    version         INTEGER NOT NULL,                     -- 同章多次草稿
    content         TEXT NOT NULL,                        -- 章节正文
    created_by      TEXT NOT NULL,                        -- agent:writer:v1 等
    prompt_version  TEXT,                              -- 例: writer:v7 (PRD §94)
    model_id        TEXT,                                 -- provider/model
    created_at      TEXT NOT NULL,
    FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id)
);

-- =============================================================================
-- 15. story_states (Canonical Story State 快照, PRD §65)
--    state_version 单调递增，每次 Commit 严格 +1 (state-delta-v0.md §6.2)
-- =============================================================================
CREATE TABLE story_states (
    project_id      TEXT NOT NULL,
    state_version   INTEGER NOT NULL,                      -- 单调递增，从 1 起
    snapshot_json   TEXT NOT NULL,                         -- 全量 Story State JSON
    commit_id       TEXT NOT NULL,                         -- 产生本快照的 commit
    created_at      TEXT NOT NULL,
    PRIMARY KEY (project_id, state_version),
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (commit_id) REFERENCES commits(commit_id)
);

-- =============================================================================
-- 16. state_deltas (Observer 输出的变更提案)
--    字段名与 state-delta.schema.json 顶层 properties 对齐
-- =============================================================================
CREATE TABLE state_deltas (
    delta_id                  TEXT PRIMARY KEY,            -- 例: dlt_<ulid>
    chapter_id                TEXT NOT NULL,
    workflow_run_id           TEXT NOT NULL,               -- -> workflow_runs.run_id
    previous_state_version    INTEGER NOT NULL,            -- 对齐 delta schema required
    delta_version             INTEGER NOT NULL DEFAULT 1,  -- 对齐 delta schema const=1
    schema_version            TEXT NOT NULL DEFAULT 'state-delta-v0',
    payload_json              TEXT NOT NULL,               -- 7 个 change 数组的完整 JSON
    status                    TEXT NOT NULL DEFAULT 'proposed'
                             CHECK (status IN ('proposed','validated','applied','rejected','superseded')),
    supersedes                TEXT,                        -- 重试指向旧 delta_id
    created_by                TEXT NOT NULL,
    created_at                TEXT NOT NULL,
    FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id),
    FOREIGN KEY (supersedes) REFERENCES state_deltas(delta_id)
);

-- =============================================================================
-- 17. commits (State Commit 不可变记录, 对齐 state-commit.schema.json)
-- =============================================================================
CREATE TABLE commits (
    commit_id                   TEXT PRIMARY KEY,          -- 例: cmt_<ulid>
    project_id                  TEXT NOT NULL,
    branch_id                   TEXT NOT NULL,             -- -> branches.branch_id
    chapter_id                  TEXT NOT NULL,            -- -> chapters.chapter_id
    previous_state_version      INTEGER NOT NULL,         -- 对齐 commit.previous_state.state_version
    resulting_state_version     INTEGER NOT NULL,         -- 对齐 commit.resulting_state_version (必填)
    delta_id                    TEXT NOT NULL,            -- -> state_deltas.delta_id (对齐 commit.delta)
    validation_json             TEXT NOT NULL,            -- 对齐 commit.validation (schema_valid/guardrail_results/...)
    author_approval_json        TEXT NOT NULL,            -- 对齐 commit.author_approval
    timestamp                   TEXT NOT NULL,            -- 对齐 commit.timestamp (ISO-8601)
    workflow_run_id             TEXT NOT NULL,            -- 对齐 commit.workflow_run.workflow_run_id
    rollback_of                 TEXT,                     -- 对齐 commit.rollback_of
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id),
    FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id),
    FOREIGN KEY (delta_id) REFERENCES state_deltas(delta_id)
);

-- =============================================================================
-- 18. agents (Agent 配置, PRD §28)
-- =============================================================================
CREATE TABLE agents (
    agent_id     TEXT PRIMARY KEY,                         -- 例: ag_<ulid>
    name         TEXT NOT NULL,                            -- 例: director
    role         TEXT NOT NULL,                            -- 例: reasoning
    config_json  TEXT NOT NULL DEFAULT '{}',               -- capability / forbidden / ...
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- =============================================================================
-- 19. prompts (Prompt 版本化, PRD §94)
-- =============================================================================
CREATE TABLE prompts (
    prompt_id    TEXT PRIMARY KEY,                         -- 例: prm_<ulid>
    agent_id     TEXT NOT NULL,                            -- -> agents.agent_id
    version      TEXT NOT NULL,                            -- 例: v7
    content      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'DRAFT'
                 CHECK (status IN ('DRAFT','ACTIVE','DEPRECATED')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);

-- =============================================================================
-- 20. workflows (Workflow 定义, PRD §40)
-- =============================================================================
CREATE TABLE workflows (
    workflow_id     TEXT PRIMARY KEY,                      -- 例: wf_<ulid>
    name            TEXT NOT NULL,
    version         TEXT NOT NULL,                         -- 例: v1
    definition_json TEXT NOT NULL,                         -- 节点/边/审批点
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- =============================================================================
-- 21. workflow_runs (Workflow 实例, PRD §61 §92)
-- =============================================================================
CREATE TABLE workflow_runs (
    run_id          TEXT PRIMARY KEY,                      -- 例: wfr_<ulid>
    workflow_id     TEXT NOT NULL,                         -- -> workflows.workflow_id
    chapter_id      TEXT,                                  -- -> chapters.chapter_id (NULL=非章节任务)
    status          TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','RUNNING','PAUSED','COMPLETED','FAILED','CANCELLED')),
    current_node    TEXT,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',            -- Pause/Resume 持久化点
    error           TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    FOREIGN KEY (workflow_id) REFERENCES workflows(workflow_id),
    FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id)
);

-- =============================================================================
-- 22. workflow_run_nodes (节点级执行记录, 支撑 PRD §8 Workflow Is Observable)
-- =============================================================================
CREATE TABLE workflow_run_nodes (
    node_run_id        TEXT PRIMARY KEY,                   -- 例: wfrn_<ulid>
    run_id             TEXT NOT NULL,                      -- -> workflow_runs.run_id
    node_id            TEXT NOT NULL,                      -- workflow 内部节点 ID
    agent_id           TEXT,                               -- -> agents.agent_id
    status             TEXT NOT NULL DEFAULT 'PENDING'
                      CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED','SKIPPED')),
    input_json         TEXT NOT NULL DEFAULT '{}',
    output_json        TEXT,
    prompt_version     TEXT,
    model_id           TEXT,
    token_usage_json   TEXT,                               -- {prompt, completion, total}
    latency_ms         INTEGER,
    error              TEXT,
    started_at         TEXT,
    ended_at           TEXT,
    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id),
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);

-- =============================================================================
-- 23. ai_call_logs (AI 调用日志, 落地 PRD §93)
-- =============================================================================
CREATE TABLE ai_call_logs (
    call_id                  TEXT PRIMARY KEY,             -- 例: aic_<ulid>
    run_id                   TEXT NOT NULL,                -- -> workflow_runs.run_id
    node_run_id              TEXT,                         -- -> workflow_run_nodes.node_run_id
    agent_id                 TEXT,                         -- -> agents.agent_id
    model_id                 TEXT,                         -- provider/model
    prompt_version           TEXT,                         -- 例: writer:v7 (PRD §94)
    input_context_ids_json   TEXT NOT NULL DEFAULT '[]',   -- [character_id/event_id/...] (PRD §93)
    output_json              TEXT,                         -- 结构化输出 (PRD §116)
    token_usage_json         TEXT,                         -- {prompt, completion, total}
    latency_ms               INTEGER,
    cost                     REAL,                         -- 折算费用
    error                    TEXT,
    retry_count              INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id),
    FOREIGN KEY (node_run_id) REFERENCES workflow_run_nodes(node_run_id),
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);

-- =============================================================================
-- 24. evaluations (Quality Engine 评分, PRD §37 §38)
-- =============================================================================
CREATE TABLE evaluations (
    evaluation_id              TEXT PRIMARY KEY,           -- 例: evl_<ulid>
    chapter_id                 TEXT NOT NULL,              -- -> chapters.chapter_id
    scores_json                TEXT NOT NULL,              -- {overall,plot,character,continuity,style,pacing,foreshadowing}
    guardrail_results_json     TEXT NOT NULL DEFAULT '[]', -- 对齐 commit.validation.guardrail_results (5 项)
    issues_json                TEXT NOT NULL DEFAULT '[]',
    scoring_version            TEXT NOT NULL,
    judge_model_versions_json  TEXT NOT NULL DEFAULT '[]',
    commit_id                  TEXT,                       -- 对应 commit (可选, 草稿期也可评)
    created_at                 TEXT NOT NULL,
    FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id),
    FOREIGN KEY (commit_id) REFERENCES commits(commit_id)
);

-- =============================================================================
-- 25. model_configs (Model Router 配置, PRD §51 §52)
-- =============================================================================
CREATE TABLE model_configs (
    config_id    TEXT PRIMARY KEY,                         -- 例: mcf_<ulid>
    capability   TEXT NOT NULL,                            -- reasoning / creative_writing / prose
    provider     TEXT NOT NULL,                            -- openai / anthropic / ollama ...
    model        TEXT NOT NULL,                            -- 例: gpt-4o / claude-3-5-sonnet
    params_json  TEXT NOT NULL DEFAULT '{}',               -- temperature / top_p / seed ...
    enabled      INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1))  -- 0/1 布尔
);

-- =============================================================================
-- 26. branches (Story Branch, PRD §48)
-- =============================================================================
CREATE TABLE branches (
    branch_id           TEXT PRIMARY KEY,                  -- 例: br_<ulid>
    project_id          TEXT NOT NULL,                     -- -> projects.project_id
    name                TEXT NOT NULL,                     -- main / branch-A
    parent_branch_id    TEXT,                              -- -> branches.branch_id (NULL=main)
    base_state_version  INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE','MERGED','DISCARDED')),
    created_at          TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (parent_branch_id) REFERENCES branches(branch_id)
);

-- =============================================================================
-- 28. reveal_policies (释放策略, PRD v1.1 新增)
--     释放策略的 Canonical 承载，Director/作者经 Intent 设置
--     Context Engine 构建 Writer 上下文时读取并注入
--     属于释放策略维度，不属于 visibility 字段落点（对齐 knowledge-permission-v0）
-- =============================================================================
CREATE TABLE reveal_policies (
    policy_id        TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES projects(project_id),
    target_type      TEXT NOT NULL CHECK (target_type IN ('character','plot_event','hook','world_rule','fact')),
    target_id        TEXT NOT NULL,
    from_chapter_id  TEXT,
    until_chapter_id TEXT,
    policy           TEXT NOT NULL CHECK (policy IN ('FORBIDDEN','HINT_OK','REVEALABLE')),
    note             TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- =============================================================================
-- 27. memories (Memory 架构, PRD §26: structured / semantic / narrative)
-- =============================================================================
CREATE TABLE memories (
    memory_id          TEXT PRIMARY KEY,                   -- 例: mem_<ulid>
    project_id         TEXT NOT NULL,
    kind               TEXT NOT NULL
                       CHECK (kind IN ('structured','semantic','narrative')),
    content            TEXT NOT NULL,
    embedding_ref      TEXT,                               -- vector DB 引用 (LanceDB/Chroma)
    source_ids_json    TEXT NOT NULL DEFAULT '[]',         -- [character_id/event_id/...]
    created_at         TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- =============================================================================
-- 索引 (只建主会话明确要求的)
-- =============================================================================
-- 所有外键列: SQLite 不会自动建索引，按主会话方案对关键外键补
CREATE INDEX idx_characters_project_id           ON characters(project_id);
CREATE INDEX idx_locations_project_id            ON locations(project_id);
CREATE INDEX idx_factions_project_id             ON factions(project_id);
CREATE INDEX idx_world_rules_project_id          ON world_rules(project_id);
CREATE INDEX idx_plot_events_project_id          ON plot_events(project_id);
CREATE INDEX idx_plot_events_location_id         ON plot_events(location_id);
CREATE INDEX idx_plot_events_introduced_chapter  ON plot_events(introduced_chapter_id);
CREATE INDEX idx_timeline_events_project_id      ON timeline_events(project_id);
CREATE INDEX idx_timeline_events_event_id        ON timeline_events(event_id);
CREATE INDEX idx_relationships_project_id        ON relationships(project_id);
CREATE INDEX idx_relationships_from_character    ON relationships(from_character_id);
CREATE INDEX idx_relationships_to_character      ON relationships(to_character_id);
CREATE INDEX idx_hooks_project_id                ON hooks(project_id);
CREATE INDEX idx_hooks_project_status            ON hooks(project_id, status);
CREATE INDEX idx_hooks_introduced_chapter        ON hooks(introduced_chapter_id);
CREATE INDEX idx_hooks_expected_payoff_chapter   ON hooks(expected_payoff_chapter_id);
CREATE INDEX idx_hooks_payoff_chapter            ON hooks(payoff_chapter_id);
CREATE INDEX idx_narrative_debts_project_id      ON narrative_debts(project_id);
CREATE INDEX idx_narrative_debts_project_status  ON narrative_debts(project_id, status);
CREATE INDEX idx_narrative_debts_created_chapter ON narrative_debts(created_chapter_id);
CREATE INDEX idx_narrative_debts_deadline_chapter ON narrative_debts(deadline_chapter_id);
CREATE INDEX idx_chapters_project_id             ON chapters(project_id);
CREATE INDEX idx_chapters_project_number         ON chapters(project_id, number);
CREATE INDEX idx_scenes_chapter_id               ON scenes(chapter_id);
CREATE INDEX idx_drafts_chapter_id               ON drafts(chapter_id);
CREATE INDEX idx_state_deltas_chapter_id         ON state_deltas(chapter_id);
CREATE INDEX idx_commits_project_branch_version  ON commits(project_id, branch_id, resulting_state_version);
CREATE INDEX idx_commits_chapter_id              ON commits(chapter_id);
CREATE INDEX idx_commits_delta_id                ON commits(delta_id);
CREATE INDEX idx_agents_name                     ON agents(name);
CREATE INDEX idx_prompts_agent_id                ON prompts(agent_id);
CREATE INDEX idx_workflow_runs_status            ON workflow_runs(status);
CREATE INDEX idx_workflow_runs_workflow_id       ON workflow_runs(workflow_id);
CREATE INDEX idx_workflow_runs_chapter_id       ON workflow_runs(chapter_id);
CREATE INDEX idx_workflow_run_nodes_run_id       ON workflow_run_nodes(run_id);
CREATE INDEX idx_workflow_run_nodes_agent_id     ON workflow_run_nodes(agent_id);
CREATE INDEX idx_ai_call_logs_run_id             ON ai_call_logs(run_id);
CREATE INDEX idx_ai_call_logs_node_run_id       ON ai_call_logs(node_run_id);
CREATE INDEX idx_ai_call_logs_agent_id           ON ai_call_logs(agent_id);
CREATE INDEX idx_evaluations_chapter_id          ON evaluations(chapter_id);
CREATE INDEX idx_evaluations_commit_id           ON evaluations(commit_id);
CREATE INDEX idx_branches_project_id             ON branches(project_id);
CREATE INDEX idx_branches_parent_branch_id       ON branches(parent_branch_id);
CREATE INDEX idx_memories_project_kind           ON memories(project_id, kind);
CREATE INDEX idx_reveal_policies_project_id      ON reveal_policies(project_id);
CREATE INDEX idx_reveal_policies_target          ON reveal_policies(target_type, target_id);

-- =============================================================================
-- PRAGMA 收尾: 启用 FK 检查 + WAL
-- 注: foreign_keys=ON 与 journal_mode=WAL 为持久连接设置; DDL 中执行仅对当前连接生效
-- 真实运行时应由 Service 层在每次连接时显式设置
-- =============================================================================
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- =============================================================================
-- 迁移结束 (0001)
-- =============================================================================