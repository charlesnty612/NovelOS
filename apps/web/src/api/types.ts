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

// --- V1.4 Sprint 16 / MVP：项目备份 / 恢复 -----------------------------------
// 对应 packages/core/backup/schema.py 顶层契约；前端仅使用部分字段做 UI 提示。
export interface BackupMetadata {
  schema_migrations: string[];
  table_count_exported: number;
  exported_table_names: string[];
  exported_at_iso: string;
  api_keys_stripped: boolean;
  ai_call_logs_excluded: boolean;
  evaluations_excluded: boolean;
  workflow_runs_excluded: boolean;
  model_configs_excluded: boolean;
  reference_canons_excluded: boolean;
}

export interface BackupPackage {
  format: 'novelos-backup';
  version: 1;
  exported_at: string;
  exported_from_project_id: string;
  metadata: BackupMetadata;
  project: Project;
  tables: Record<string, unknown[]>;
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
  /**
   * 三态决议（chapter-review Human 节点，PRD §59/§87 改稿重审闭环）：
   * - { approved: true }                    → 通过，chapter → REVIEWED
   * - { approved: false }                   → 拒绝，run FAILED，chapter 保持 DRAFTED
   * - { approved: false, revise: true, note? } → 驳回并改稿，run FAILED(rejected-for-revision)，
   *   chapter 保持 DRAFTED，note 落 plan_json.revision_note
   */
  human_input: { approved: boolean; revise?: boolean; note?: string };
}

// ---- pause payloads --------------------------------------------------------
// chapter-review: author_review 节点 → {stage, message, review_report, critic_report?}
// chapter-commit: high_risk_approval 节点 → {stage, message, delta_id, changes}
//
// V1.3 新增 critic_report：LLM 评审员生成的建议性结构化报告，**仅做参考**、不拦截。
// critic_status='ok' 时 critic_report 必有；'failed'/'skipped' 时为 null。
export type CriticIssueCategory =
  | 'pacing'
  | 'character'
  | 'logic'
  | 'foreshadowing'
  | 'ai_flavor'
  | 'other';

export type CriticIssueSeverity = 'high' | 'medium' | 'low';

export interface CriticIssue {
  category: CriticIssueCategory;
  severity: CriticIssueSeverity;
  quote: string;
  suggestion: string;
}

export interface CriticReport {
  schema_version?: string;
  prompt_version?: string;
  chapter_id?: string;
  overall_comment: string;
  strengths: string[];
  issues: CriticIssue[];
}

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
  /** V1.3：'ok' = 有 critic_report；'failed' = AI 评审不可用（不阻断）；'skipped' = 未跑（兼容老 run） */
  critic_status?: 'ok' | 'failed' | 'skipped' | string;
  /** V1.3：critic_status='ok' 时为结构化报告；其余为 null。UI 须容忍 null。 */
  critic_report?: CriticReport | null;
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
  /** DB 端存的是 JSON 字符串；前端拿到后 coerceJson 会把它解析成对象。
   *  P1-1：读路径出口由后端脱敏，``api_key`` 会被替换为 ``"***"``；不要回填到输入框 value。
   *  用 ``has_api_key`` 判断「已配置 / 未配置」。 */
  params_json: Record<string, unknown>;
  enabled: 0 | 1;
  /** 后端在 GET/POST/PATCH 响应顶层附的字段：true 表示 DB 中存有非空 api_key。 */
  has_api_key?: boolean;
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
  ok: boolean;
  latency_ms: number;
  detail?: string | null;
  status_code?: number | null;
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
// Sprint 15 / V1.3：作者文风样例（author_style_samples）
//   对齐 packages/core/api/routers/author_style_samples.py。
//   - sample_id：asty_<12hex>
//   - 单篇 content ≤ 5000 字；单项目 ≤ 10 篇
//   - 服务端默认按 created_at DESC 返回
// ---------------------------------------------------------------------------

export interface StyleSample {
  sample_id: string;
  project_id: string;
  title: string;
  content: string;
  created_at: string;
  updated_at: string;
}

export interface StyleSampleCreatePayload {
  title: string;
  content: string;
}

// ---------------------------------------------------------------------------
// Sprint 9：Hooks（伏笔台账）+ Narrative Debts（叙事债务）
// 与 packages/domain/ledger/models.py + routers/ledger.py 对齐。
// ---------------------------------------------------------------------------

export type HookStatus =
  | 'OPEN'
  | 'ACTIVE'
  | 'ESCALATED'
  | 'RESOLVED'
  | 'ABANDONED';

export type DebtStatus = 'open' | 'acknowledged' | 'paid' | 'forgiven';

export interface Hook {
  hook_id: string;
  project_id: string;
  name: string;
  introduced_chapter_id: string | null;
  status: HookStatus;
  importance: number;
  expected_payoff_chapter_id: string | null;
  payoff_chapter_id: string | null;
  visibility: VisibilityLevel;
  who_knows: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface HookCreatePayload {
  name: string;
  introduced_chapter_id?: string | null;
  expected_payoff_chapter_id?: string | null;
  payoff_chapter_id?: string | null;
  status?: HookStatus | null;
  importance?: number | null;
  visibility?: VisibilityLevel | null;
  who_knows?: string[] | null;
}

export interface HookUpdatePayload {
  name?: string | null;
  introduced_chapter_id?: string | null;
  expected_payoff_chapter_id?: string | null;
  payoff_chapter_id?: string | null;
  status?: HookStatus | null;
  importance?: number | null;
  visibility?: VisibilityLevel | null;
  who_knows?: string[] | null;
}

export interface Debt {
  debt_id: string;
  project_id: string;
  description: string;
  created_chapter_id: string | null;
  severity: number;
  deadline_chapter_id: string | null;
  status: DebtStatus;
  visibility: VisibilityLevel;
  who_knows: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface DebtCreatePayload {
  description: string;
  created_chapter_id?: string | null;
  deadline_chapter_id?: string | null;
  status?: DebtStatus | null;
  severity?: number | null;
  visibility?: VisibilityLevel | null;
  who_knows?: string[] | null;
}

export interface DebtUpdatePayload {
  description?: string | null;
  created_chapter_id?: string | null;
  deadline_chapter_id?: string | null;
  status?: DebtStatus | null;
  severity?: number | null;
  visibility?: VisibilityLevel | null;
  who_knows?: string[] | null;
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
  // V1.4：参照系消费清单（quality_gate 节点 + evaluate 端点都写入）。
  // 缺失时整字段 undefined，前端按空态处理（不渲染区块）。
  reference_consumption?: ReferenceConsumption;
  // V1.4：enforce 改稿引导（仅 enforce 模式阻断时写入；report 模式可能缺失）。
  // 默认从 checkpoint_json['quality_gate'].revision_guidance 取（QualityPanel.props
  // 的 qualityGateCheckpoint 优先）。
  revision_guidance?: RevisionGuidance[];
}

export interface QualityScores {
  overall: number;
  plot: number;
  character: number;
  continuity: number;
  style: number;
  pacing: number;
  foreshadowing: number;
  ai_trace: number;
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

// ---------------------------------------------------------------------------
// V1.4：参照系消费可观测（Sprint 16）。
//   对齐 packages/core/quality/service.capture_reference_consumption 与
//   packages/workflows/chapter_commit/pipeline._quality_gate_node。
//   - ``source``：固定 ``"project_refs_dir"``（项目级 ``<db 父目录>/references/<pid>/*.txt``）
//   - ``files``：[{ name, chars }]；空数组表示项目下没有参照书
//   - ``files_count / total_chars``：冗余汇总字段（便于前端不必 reduce）
// ---------------------------------------------------------------------------

export interface ReferenceFileEntry {
  name: string;
  chars: number;
}

export interface ReferenceConsumption {
  source: string;
  files: ReferenceFileEntry[];
  total_chars: number;
  files_count: number;
}

// ---------------------------------------------------------------------------
// V1.4：enforce 改稿引导（revision_guidance）。
//   对齐 packages/workflows/chapter_commit/pipeline._build_revision_guidance。
//   每条引导：维度 + 当前分 + 阈值 + top_issues + 可执行建议（rule_hint）。
//   dimension='guardrails' 时 score=0（不是低分子分），rule_hint 直接来自阻断 rule。
// ---------------------------------------------------------------------------

export type RevisionDimension =
  | 'plot'
  | 'character'
  | 'continuity'
  | 'style'
  | 'pacing'
  | 'foreshadowing'
  | 'ai_trace'
  | 'guardrails';

export interface RevisionGuidance {
  dimension: RevisionDimension;
  score: number;
  threshold: number;
  top_issues: QualityIssue[];
  rule_hint: string;
}

// ---------------------------------------------------------------------------
// V1.4：quality_gate 节点 checkpoint 暴露字段。
//   对齐 packages/workflows/chapter_commit/pipeline._quality_gate_node 返回 dict + 阻断时 ctx 顶层。
//   - blocked：是否 enforce 阻断（true ⇒ run FAILED）
//   - mode：'enforce' | 'report'
//   - reference_consumption：本节点消费的参照文本清单
//   - revision_guidance：enforce 阻断时给作者的结构化改稿建议（report 模式可能为空）
// ---------------------------------------------------------------------------

export interface QualityGateCheckpoint {
  blocked: boolean;
  mode: 'enforce' | 'report' | string;
  reference_consumption: ReferenceConsumption;
  revision_guidance: RevisionGuidance[];
}

// ---------------------------------------------------------------------------
// Sprint 11 下半：Reference Canon（参照系）
//   对齐 packages/core/api/routers/reference.py + reference_canons / canon_extracts 表。
//   - CanonSummary：list 端点返回的轻量摘要（GET /projects/{pid}/canons）。
//   - CanonDetail ：GET /canons/{canon_id} 全文 + report_md + extracts。
//   - DeconstructStartResponse：POST /projects/{pid}/deconstruct 同步返回（含 canon_id）。
//   - ExtractEntry：canon_extracts 行的解包结构。
// ---------------------------------------------------------------------------

export type ReaderProfile =
  | 'male_fantasy'
  | 'male_urban'
  | 'male_system'
  | 'female_general'
  | 'general';

export interface CanonSummary {
  canon_id: string;
  project_id: string;
  title: string;
  reader_profile: ReaderProfile | string;
  status: 'active' | 'archived';
  created_at: string;
  logline: string;
  spine_count: number;
  rhythm_chapter_count: number;
}

export interface ExtractEntry {
  extract_id: string;
  chapter_index: number;
  /** 后端在 GET /canons/{id} 返回时已 json.loads；前端不二次解析。 */
  extract_json: Record<string, unknown>;
  created_at: string;
}

export interface CanonDetail {
  canon_id: string;
  project_id: string;
  title: string;
  reader_profile: ReaderProfile | string;
  status: 'active' | 'archived';
  /** 与后端 Pydantic 对齐：GET /canons/{id} 返回 dict（非字符串）。 */
  canon_json: Record<string, unknown>;
  /** T4 渲染的 Markdown 报告（人读）。 */
  report_md: string;
  created_at: string;
  extracts: ExtractEntry[];
}

export interface DeconstructStartResponse {
  run_id: string;
  status: 'COMPLETED' | 'FAILED' | 'PAUSED' | 'RUNNING' | 'PENDING' | 'CANCELLED';
  current_node: string | null;
  project_id: string;
  book_title: string;
  canon_id?: string;
  extracts_count?: number;
  error?: string;
}

export interface DeconstructPayload {
  book_title: string;
  text: string;
  reader_profile?: ReaderProfile;
}

// ---------------------------------------------------------------------------
// Sprint 13：Context preview（dry-run，AI 上下文装配可见化）
//   对齐 packages/core/context_engine/preview.py + workflows 路由 context-preview 端点。
//   只读、不调 LLM、不写库；用于章节详情页"AI 本次读了什么"面板。
// ---------------------------------------------------------------------------

export interface ContextPreviewItem {
  kind: string;
  id: string;
  name: string;
  role?: string;
  source?: string;
  status?: string;
  importance?: number;
  severity?: number;
  type?: string;
  source_len?: number;
  excerpt_len?: number;
  beats?: number;
  spine_count?: number;
  payoff_count?: number;
}

export interface ContextPreviewLayer {
  id: string;
  label: string;
  token_estimate: number;
  items: ContextPreviewItem[];
  truncated: boolean;
}

export interface ContextPreviewResponse {
  chapter_id: string;
  project_id: string;
  agents: string[];
  layers: ContextPreviewLayer[];
  total_tokens: number;
  token_budget: number;
  within_budget: boolean;
}

// ---------------------------------------------------------------------------
// Sprint 13：AI 调用日志（ai_call_logs）查询契约
//   对齐 packages/core/api/routers/ai_call_logs.py。
//   列表只含摘要；详情含 input_context_ids（list）+ output（结构化）。
//   **不含任何 API key 字段**（后端 schema 与路由层双重防御）。
// ---------------------------------------------------------------------------

export interface AiCallLogTokenUsage {
  prompt: number;
  completion: number;
  total: number;
}

export interface AiCallLogSummary {
  call_id: string;
  run_id: string;
  node_run_id: string | null;
  agent_id: string | null;
  model_id: string | null;
  prompt_version: string | null;
  latency_ms: number | null;
  retry_count: number;
  error: string | null;
  token_usage: AiCallLogTokenUsage | null;
  cost: number | null;
  created_at: string;
}

export interface AiCallLogDetail extends AiCallLogSummary {
  /** 仅详情端点返回：从 input_context_ids_json 解析。原文不落 DB（runner.py L52-69）。 */
  input_context_ids: string[];
  /** 仅详情端点返回：output_json 解析后的对象/数组/字符串等。 */
  output: Record<string, unknown> | unknown[] | string | number | boolean | null;
}

// ---------------------------------------------------------------------------
// V1.4 / Sprint 16：导出（packages/core/api/routers/export.py）
//   GET /projects/{pid}/export?format={txt|docx|fanqie}[&chapter_no=...]
// ---------------------------------------------------------------------------

export type ExportFormat = 'txt' | 'docx' | 'fanqie';

export interface ExportQuery {
  format: ExportFormat;
  chapter_no?: number;
}
