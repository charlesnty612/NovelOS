// 前后端契约类型（Sprint 5 一期对齐 packages/core/api 与 packages/domain）。
// 字段命名直接与后端 Pydantic 模型对齐。

export type ProjectStatus = 'ACTIVE' | 'PAUSED' | 'ARCHIVED';

export interface Project {
  project_id: string;
  name: string;
  premise: string | null;
  genre: string | null;
  target_words: number | null;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreatePayload {
  name: string;
  premise?: string | null;
  genre?: string | null;
  target_words?: number | null;
}

export interface ProjectUpdatePayload {
  name?: string | null;
  premise?: string | null;
  genre?: string | null;
  target_words?: number | null;
  status?: ProjectStatus;
}

export type CharacterRole =
  | 'protagonist'
  | 'antagonist'
  | 'supporting'
  | 'mentor'
  | 'love_interest'
  | 'narrator'
  | 'other';

export type VisibilityLevel = 'PUBLIC' | 'VISIBLE' | 'RESTRICTED' | 'HIDDEN';

export interface Character {
  character_id: string;
  project_id: string;
  name: string;
  role: CharacterRole;
  core_json: Record<string, unknown>;
  visibility: VisibilityLevel;
  who_knows: string[] | null;
  created_at: string;
  updated_at: string;
  latest_state_version: number;
  latest_state_json: Record<string, unknown>;
}

export interface CharacterCreatePayload {
  name: string;
  role?: CharacterRole | null;
  core_json?: Record<string, unknown> | null;
  visibility?: VisibilityLevel | null;
  who_knows?: string[] | null;
}

export interface CharacterUpdatePayload {
  name?: string | null;
  role?: CharacterRole | null;
  core_json?: Record<string, unknown> | null;
  visibility?: VisibilityLevel | null;
  who_knows?: string[] | null;
}

export interface CharacterState {
  character_id: string;
  state_version: number;
  state_json: Record<string, unknown>;
  visibility: VisibilityLevel;
  who_knows: string[] | null;
  created_at: string;
}

export interface WorldEntity {
  id: string;
  project_id: string;
  name: string;
  statement: string;
  data: Record<string, unknown>;
  visibility: VisibilityLevel;
  who_knows: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface WorldEntityPayload {
  name: string;
  statement?: string;
  data?: Record<string, unknown> | null;
  visibility?: VisibilityLevel | null;
  who_knows?: string[] | null;
}

export type EventType =
  | 'revelation'
  | 'conflict'
  | 'decision'
  | 'encounter'
  | 'transition'
  | 'other';

export interface PlotEvent {
  event_id: string;
  project_id: string;
  type: EventType;
  cause: Record<string, unknown> | null;
  effects: Record<string, unknown> | null;
  participants: unknown[];
  location_id: string | null;
  time: Record<string, unknown> | null;
  status: string;
  introduced_chapter_id: string | null;
  visibility: VisibilityLevel;
  who_knows: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface PlotEventCreatePayload {
  type: EventType;
  cause?: Record<string, unknown> | null;
  effects?: Record<string, unknown> | null;
  participants?: unknown[] | null;
  time?: Record<string, unknown> | null;
  status?: string;
}

export interface TimelineEvent {
  id: string;
  project_id: string;
  event_id: string;
  day_index: number;
  time_ref: string | null;
  description: string | null;
  created_at: string;
}

export interface Commit {
  commit_id: string;
  project_id: string;
  version: number;
  parent_version: number | null;
  message: string;
  author: string | null;
  created_at: string;
}

export interface HealthResponse {
  status: string;
  version?: string;
  tables?: Record<string, number>;
}

export interface SnapshotResponse {
  version?: number;
  state_version?: number;
  characters?: unknown;
  world?: unknown;
  hooks?: unknown[];
  debts?: unknown[];
  events?: unknown[];
  recent_events?: unknown[];
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Sprint 5 二期：chapters / drafts / workflow runs / model configs / agents
// 与 packages/core/api/routers + packages/core/workflow_runtime 对齐。
// ---------------------------------------------------------------------------

export type ChapterStatus =
  | 'PLANNED'
  | 'DRAFTED'
  | 'REVIEWED'
  | 'COMMITTED'
  | 'RELEASED';

export interface Chapter {
  chapter_id: string;
  project_id: string;
  number: number;
  title: string | null;
  plan_json: Record<string, unknown>;
  status: ChapterStatus;
  visibility: string;
  who_knows: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface ChapterCreatePayload {
  number: number;
  title?: string | null;
  plan_json?: Record<string, unknown> | null;
}

export interface ChapterUpdatePayload {
  title?: string | null;
  plan_json?: Record<string, unknown> | null;
  status?: ChapterStatus | null;
}

export interface Draft {
  draft_id: string;
  chapter_id: string;
  version: number;
  content: string;
  created_by: string;
  prompt_version: string | null;
  model_id: string | null;
  created_at: string;
}

export interface DraftCreatePayload {
  content: string;
}

// ---- workflow runs ---------------------------------------------------------

export type WorkflowRunStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'PAUSED'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED';

export type WorkflowNodeStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'COMPLETED'
  | 'FAILED'
  | 'SKIPPED';

export interface WorkflowNodeRun {
  node_run_id: string;
  run_id: string;
  node_id: string;
  agent_id: string | null;
  status: WorkflowNodeStatus;
  input_json: Record<string, unknown>;
  output_json: Record<string, unknown> | unknown[] | string | number | boolean | null;
  prompt_version: string | null;
  model_id: string | null;
  token_usage_json: Record<string, unknown> | null;
  latency_ms: number | null;
  error: string | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface WorkflowRun {
  run_id: string;
  workflow_id: string;
  chapter_id: string | null;
  status: WorkflowRunStatus;
  current_node: string | null;
  checkpoint_json: Record<string, unknown>;
  error: string | null;
  retry_count: number;
  started_at: string;
  ended_at: string | null;
  /**
   * 节点明细数组：单 run GET（`/runs/{id}`）返回；list（`/projects/{pid}/runs`）不返回。
   * UI 用 useApiCall 单独拉详情时填充。
   */
  nodes: WorkflowNodeRun[];
  // workflow_name 由 router 端通过 join 写入；后端 list_runs/get_run 暂未提供该字段，
  // 前端 UI 退化为显示 workflow_id；详见 workflow_runtime.runs.list_runs / get_run。
  workflow_name?: string;
}

// ---- workflow start payload / responses -----------------------------------

export interface WorkflowStartPayload {
  author_intent?: string | null;
  expected_role?: string | null;
  target_word_count?: number | null;
  mock_providers?: Record<string, string[]> | null;
}

export interface WorkflowStartResponse {
  run_id: string;
  status: WorkflowRunStatus;
  current_node: string | null;
  pause_payload?: PausePayload | null;
}

export interface ResumeRequestPayload {
  human_input: { approved: boolean } & Record<string, unknown>;
}

// ---- pause payloads --------------------------------------------------------
// chapter-review: author_review 节点 → {stage, message, review_report}
// chapter-commit: high_risk_approval 节点 → {stage, message, delta_id, changes}
export interface ChapterReviewPausePayload {
  stage: 'chapter-review';
  message: string;
  review_report: {
    chapter_id: string;
    word_count: number;
    target_word_count: number;
    within_range: boolean;
    deviation: number;
    forbidden_word_hits: string[];
    warnings: string[];
  };
}

export interface ChapterCommitPausePayload {
  stage: 'chapter-commit.high_risk_approval';
  message: string;
  delta_id: string | null;
  changes: {
    character_changes: unknown[];
    world_changes: unknown[];
  };
}

export type PausePayload = ChapterReviewPausePayload | ChapterCommitPausePayload | Record<string, unknown>;

// ---- model configs ---------------------------------------------------------

export type ModelCapability = 'reasoning' | 'creative_writing';

export type ModelProviderKind = 'openai_compatible' | 'mock';

export interface ModelConfig {
  config_id: string;
  capability: string;
  provider: string;
  model: string;
  /** DB 端存的是 JSON 字符串；前端拿到后 coerceJson 会把它解析成对象。 */
  params_json: Record<string, unknown>;
  enabled: 0 | 1;
}

export interface ModelConfigCreatePayload {
  capability: string;
  provider: string;
  model: string;
  params_json?: Record<string, unknown> | string | null;
  enabled?: 0 | 1 | boolean;
}

export interface ModelConfigUpdatePayload {
  capability?: string;
  provider?: string;
  model?: string;
  params_json?: Record<string, unknown> | string | null;
  enabled?: 0 | 1 | boolean;
}

export interface ModelConfigTestResult {
  config_id: string;
  latency_ms: number;
  preview: string;
  usage: Record<string, unknown> | null;
}

// ---- agents / prompts ------------------------------------------------------

export interface Agent {
  agent_id: string;
  name: string;
  role: string;
  config_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface PromptVersion {
  prompt_id: string;
  agent_id: string;
  version: string; // 形如 "v3"
  content: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface SyncPromptsResult {
  scanned: Array<[string, number]>;
  registered: Array<[string, number]>;
  updated: Array<[string, number]>;
  agents: string[];
}

// ---------------------------------------------------------------------------
// Sprint 6 下半：Quality 评估报告（packages.core.quality.service）
//   与 packages/core/api/routers/quality.py + quality_reports 表对齐。
//   - QualityReport.scores_json 含六子分 + _meta；
//   - QualityReport.issues_json 是 Issue[]（severity / category / rule_id 等）。
// ---------------------------------------------------------------------------

export type QualitySeverity = 'error' | 'warning' | 'info';

export type QualityCategory =
  | 'schema_validity'
  | 'timeline_consistency'
  | 'character_contradiction'
  | 'world_rule_contradiction'
  | 'knowledge_leakage'
  | 'compliance'
  | 'plot'
  | 'character'
  | 'continuity'
  | 'style'
  | 'pacing'
  | 'foreshadowing'
  | 'payoff';

export interface QualityIssue {
  severity: QualitySeverity;
  category: QualityCategory;
  location: string;
  rule_id: string;
  message: string;
  suggestion?: string | null;
  evidence_refs?: string[] | null;
  judge_trace?: Record<string, unknown> | null;
}

export interface QualityScoresMeta {
  scoring_version: string;
  llm_judge: string;
  evaluated_at: string;
  scoring_formula_hash: string;
}

export interface QualityScores {
  overall: number;
  plot: number;
  character: number;
  continuity: number;
  style: number;
  pacing: number;
  foreshadowing: number;
  _meta: QualityScoresMeta;
}

export interface QualityReport {
  report_id: string;
  project_id: string;
  chapter_id: string;
  commit_id: string | null;
  run_id: string | null;
  overall: number;
  scores_json: QualityScores;
  issues_json: QualityIssue[];
  created_at: string;
}
