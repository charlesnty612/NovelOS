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
  /** 后端 plot_events 列表/详情接口返回字段为 id（非 event_id） */
  id: string;
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
  // P1.1 恢复挂起的初始化：单 run GET 在 PAUSED 时携带的 pause_payload；
  // 非 PAUSED / list 行可能缺省。前端 ProjectInitPanel 用其直接恢复 review 视图。
  pause_payload?: ProjectInitPausePayload | null;
}

// ---- workflow start payload / responses -----------------------------------

export interface WorkflowStartPayload {
  author_intent?: string | null;
  expected_role?: string | null;
  target_word_count?: number | null;
  mock_providers?: Record<string, string[]> | null;
  /**
   * 按次选择模型档案：key 为 capability（reasoning / creative_writing / light 等），
   * value 为 model_profiles.profile_id。未指定 capability 时后端走 capability_bindings 默认绑定。
   * 仅 plan/write/review 三个动作支持（commit 由 Observer 节点不消耗 LLM，不透传）。
   */
  model_overrides?: Record<string, string> | null;
  /**
   * 写作模式（仅 chapter-write 生效）：true ⇒ 全新重写，忽略旧稿与改稿意见，
   * 用于不同模型文风对比。缺省 / false ⇒ 按意见改稿（默认行为）。
   * 后端启动请求体可选布尔字段 fresh_write，缺省 false。
   */
  fresh_write?: boolean | null;
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
  /**
   * V2.0 Wave B 任务二：条件触发动态注入状态。
   * - 'full'      — 完整注入实体全字段（默认）
   * - 'summary'   — 仅一行摘要（name + role/statement），auto + 未命中降级
   * - 'suppressed'— never 模式不注入，仅 preview 列表可见
   */
  injection?: 'full' | 'summary' | 'suppressed';
  /** 当 injection='summary' 时附带的一行摘要文本（≤ 80 字） */
  summary_line?: string;
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

// ---------------------------------------------------------------------------
// V1.5 / Sprint 17：What-if 分支（branches 表 + story_state 分支路由）
//   对齐 packages/core/api/routers/story_state.py：
//   - GET    /projects/{pid}/branches                       列表（main 优先）
//   - POST   /projects/{pid}/branches                       创建（{name, base_state_version?}）
//   - POST   /projects/{pid}/branches/{bid}/promote         promote（{chapter_id?}）
//   - GET    /projects/{pid}/state?branch_id={bid}          分支视角当前 state
//
//   字段语义：
//   - status: 'ACTIVE' / 'MERGED' / 'DISCARDED' / 'ARCHIVED'
//     ARCHIVED 来自 simulation 临时分支（sim-*）的归档状态。
//   - base_state_version: 分支基于 main 的快照版本号。
//   - promote 返回：commit_id / state_version / delta_id / promoted_from /
//     promoted_commits / branch_id / replayed_delta_ids[]。
//   - 前端不直接调 diff_versions（不支持 branch_id，会 409）；分支视角差异在
//     面板内通过 getCurrentState(branch_id) 与 main 快照在 UI 层对比。
// ---------------------------------------------------------------------------

export type BranchStatus = 'ACTIVE' | 'MERGED' | 'DISCARDED' | 'ARCHIVED';

export interface Branch {
  branch_id: string;
  project_id: string;
  name: string;
  parent_branch_id: string | null;
  base_state_version: number;
  status: BranchStatus;
  created_at: string;
}

export interface BranchCreatePayload {
  name: string;
  base_state_version?: number | null;
}

export interface BranchPromoteResult {
  commit_id: string;
  state_version: number;
  delta_id: string;
  promoted_from: string;
  promoted_commits: number;
  branch_id: string;
  replayed_delta_ids: string[];
}

// ---------------------------------------------------------------------------
// 续写助手（continuation）
//   对齐后端契约：
//   POST /projects/{pid}/chapters/{cid}/continue        —— 生成 N 个续写候选
//   POST /projects/{pid}/chapters/{cid}/continue/adopt  —— 采纳其一追加为新草稿
//   错误码：404 项目/章节不存在；409 章节状态不可续写（detail 说明）；
//          422 参数非法；502 全部候选生成失败。统一走 ApiError 由 UI 展示。
// ---------------------------------------------------------------------------

export interface ContinueVariant {
  index: number;
  text: string;
  tokens: number | null;
  elapsed_ms: number;
}

export interface ContinueRequestPayload {
  num_variants?: number;
  instruction?: string;
}

export interface ContinueResponse {
  variants: ContinueVariant[];
  model: string;
  total_elapsed_ms: number;
}

export interface ContinueAdoptPayload {
  content: string;
}

export interface ContinueAdoptResponse {
  version: number;
  status: string;
  appended_chars: number;
}

// ---------------------------------------------------------------------------
// P1 project-init workflow：项目总览页「AI 初始化设定」入口契约。
//   对齐 packages/core/api/routers/workflows.py ProjectInitRequest / _start_project_init：
//   - POST /projects/init               body: {brief, project_id?, chapter_seed_count?}
//   - 响应: {run_id, status, current_node, project_id}
//   - 引擎同步阻塞到终态（COMPLETED / FAILED）；AI 节点失败按 chapter_review
//     critic 模式降级不阻断，仅 persist_all 失败才直接抛错。
// ---------------------------------------------------------------------------

export interface ProjectInitBrief {
  title?: string | null;
  genre?: string | null;
  logline: string;
  platform?: string | null;
  target_words?: number | null;
  author_notes?: string | null;
  chapter_seed_count?: number | null;
  /** 单章字数（字/章）；后端据此推导章节数 = round(target_words / chapter_word_count)。 */
  chapter_word_count?: number | null;
}

export interface ProjectInitPayload {
  brief: ProjectInitBrief;
  project_id?: string | null;
  chapter_seed_count?: number | null;
  /**
   * P1 project-init 增量：分步审阅生成。
   * - true：后端在 4 个关卡（premise / world / character / outline）每个完成时返回
   *   status=PAUSED + pause_payload，前端走审阅向导。
   * - 缺省 / false：维持旧的一次性生成模式，直接返回 COMPLETED。
   */
  step_mode?: boolean | null;
  /**
   * P1 project-init 增量：本次要生成的环节白名单。
   * - 缺省 / null / undefined：后端按"全选"处理（生成全部 4 关卡）。
   * - 指定数组：仅生成其中的环节；未列入的环节不生成、不暂停，复用项目已有设定。
   *   合法取值：'premise' | 'world' | 'character' | 'outline'（与 init-status 的 stage 对齐）。
   */
  selected_stages?: string[] | null;
}

// ---- P1 project-init：分步审阅 pause_payload 契约 --------------------------
// 后端 4 关卡逐个 PAUSED 时携带；前端据 stage 决定渲染哪种表单。
// - stage:           premise | world | character | outline
// - stage_index:     0-based 当前步骤下标（与 stages_total 配合显示「第 N / 4 步」）
// - stages_total:    固定 4
// - degraded:        true 表示该关卡 AI 生成降级（_degraded / error 已置入 draft）
// - draft:           该关卡的草稿字典；缺字段容错为空结构（前端按 stage 分别渲染）
export interface ProjectInitPausePayload {
  stage: 'premise' | 'world' | 'character' | 'outline' | string;
  stage_index: number;
  stages_total: number;
  degraded: boolean;
  draft: Record<string, unknown>;
}

export interface ProjectInitResponse {
  run_id: string;
  status: WorkflowRunStatus;
  current_node: string | null;
  project_id?: string | null;
  /** status=PAUSED 时携带；其余状态可缺省。 */
  pause_payload?: ProjectInitPausePayload | null;
}

// ---- P1 project-init：项目已有设定覆盖度探测 ------------------------------
// 对齐后端 GET /projects/{id}/init-status：表单预勾选用，让用户对已生成的环节选择"跳过/重新生成"。
// - stage：premise | world | character | outline（与 ProjectInitPausePayload.stage 同空间）。
// - label：中文环节名（后端给死，前端展示用）。
// - done：该环节是否已有设定（true ⇒ 用户可取消勾选 / false ⇒ 强制勾选）。
// - detail：可选，给 UI 展示的附加信息（如"3 角色 / 5 章种子"）。
export interface InitStageStatus {
  stage: 'premise' | 'world' | 'character' | 'outline' | string;
  label: string;
  done: boolean;
  detail?: string | null;
}

export interface InitStatusResponse {
  stages: InitStageStatus[];
  has_any_data: boolean;
}

// ---- P1 project-init：放行（resume）请求契约 --------------------------------
// POST /runs/{run_id}/resume —— 仅在 project-init 分步审阅模式下被调用。
// human_input.revisions 字典键为 output_key：premise_output / world_output /
// character_output / outline_output；值是该关卡修订后的完整 dict（前端整段替换）。
// 注：原 ResumeRequestPayload（chapter-review Human 节点）的 approved/revise 形态
// 不变；这里新增独立的 ProjectInitResumeRequestPayload 以避免两套语义在同一类型
// 上叠加可选字段导致的歧义。
export interface ProjectInitResumeRequestPayload {
  /**
   * 后端 POST /runs/{run_id}/resume 在 project-init 分步审阅模式下接受的扩展字段：
   * - human_input.revisions：放行（保留当前编辑走下一关）；regenerate=true 时可省
   * - human_input.regenerate_note：「带意见重新生成」时携带的 AI 引导意见；可空（纯重试）
   * - regenerate：true 时后端重跑当前挂起节点而非放行
   * 两者二选一：放行走 revisions；重生成走 regenerate_note + regenerate=true。
   */
  human_input: {
    revisions?: Record<string, Record<string, unknown>>;
    regenerate_note?: string;
  };
  regenerate?: boolean;
}

// ---------------------------------------------------------------------------
// 模型档案（model_profiles）+ 环节绑定（capability_bindings）。
// 对齐后端路由（开发中）：
//   GET    /model-profiles
//   POST   /model-profiles
//   PATCH  /model-profiles/{id}
//   DELETE /model-profiles/{id}    —— 409 + detail 列出 capability
//   POST   /model-profiles/{id}/test
//   GET    /capability-bindings    —— 7 项固定清单
//   PUT    /capability-bindings/{capability}
//   DELETE /capability-bindings/{capability}
// 档案与 capability 解耦：档案只描述「一组模型参数」，能力→档案的指派走
// capability_bindings。
// ---------------------------------------------------------------------------

export type CapabilityKey =
  | 'premise_design'
  | 'world_building'
  | 'character_design'
  | 'volume_outline'
  | 'creative_writing'
  | 'reasoning'
  | 'light';

export interface ModelProfile {
  profile_id: string;
  name: string;
  provider: string;
  model: string;
  /** 列表读路径下由后端脱敏：api_key 字段被替换为 "***"；不要回填到输入框。 */
  params: Record<string, unknown>;
  enabled: 0 | 1 | boolean;
  has_api_key?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface ModelProfileCreatePayload {
  name: string;
  provider: string;
  model: string;
  params: Record<string, unknown> | string | null;
  enabled?: 0 | 1 | boolean;
}

export interface ModelProfileUpdatePayload {
  name?: string;
  provider?: string;
  model?: string;
  params?: Record<string, unknown> | string | null;
  enabled?: 0 | 1 | boolean;
}

export interface ModelProfileTestResult {
  profile_id: string;
  ok: boolean;
  latency_ms: number;
  detail?: string | null;
  status_code?: number | null;
}

export interface CapabilityBindingProfile {
  profile_id: string;
  name: string;
  model: string;
}

export interface CapabilityBinding {
  capability: CapabilityKey | string;
  label: string;
  agents: string[];
  profile_ids: string[];
  profiles: CapabilityBindingProfile[];
  /** 后端提示：旧版 model_configs 链是否仍可兜底（解绑时附小字提示）。 */
  legacy_available: boolean;
}

export interface CapabilityBindPayload {
  profile_ids: string[];
}
