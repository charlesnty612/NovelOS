/* eslint-disable */
/**
 * 由 scripts/gen_frontend_types.py 自动生成——请勿手工编辑。
 * 数据源：apps/web/openapi.json（scripts/export_openapi.py 导出）。
 * 用途：后端 Pydantic schema 的机器可读对照面（契约检查 / diff 审查）；
 * 运行时类型仍以手写 api/types.ts 为权威。
 */

export interface AdoptRequest {
  /** 采纳请求体：纯文本追加到最新草稿尾部。 */
  content: string;
}

export interface AdoptResponse {
  /** 采纳响应。 */
  version: number;
  /** 采纳响应。 */
  status: string;
  /** 采纳响应。 */
  appended_chars: number;
}

export interface AgentListRowOut {
  /** ``GET /agents`` 的**一行**（:meth:`PromptRegistry.list_agents` 的 SELECT 6 列）。 */
  agent_id: string;
  /** ``GET /agents`` 的**一行**（:meth:`PromptRegistry.list_agents` 的 SELECT 6 列）。 */
  name: string;
  /** = config_json['capability']（见 PromptRegistry._upsert_agent） */
  role: string;
  /** JSON **字符串**（端点未解析），形如 '{"capability": "reasoning"}' */
  config_json: string;
  /** ``GET /agents`` 的**一行**（:meth:`PromptRegistry.list_agents` 的 SELECT 6 列）。 */
  created_at: string;
  /** 每次 sync 都会刷新（内容未变也刷）；created_at 则始终保留原值 */
  updated_at: string;
}

export interface AgentOutputErrorDetail {
  /** 固定 'agent_output_invalid' */
  error: string;
  /** ``AgentOutputError`` 映射出的 ``detail``（**无** ``status_code``）。 */
  message: string;
}

export interface AgentOutputErrorEnvelope {
  detail: AgentOutputErrorDetail;
}

export interface AgentPromptRowOut {
  /** ``GET /agents/{name}/prompts`` 的**一行**（:meth:`PromptRegistry.list_prompts` 的 7 列）。 */
  prompt_id: string;
  /** ``GET /agents/{name}/prompts`` 的**一行**（:meth:`PromptRegistry.list_prompts` 的 7 列）。 */
  agent_id: string;
  /** 形如 'v3'（不含 agent 前缀） */
  version: string;
  /** ``GET /agents/{name}/prompts`` 的**一行**（:meth:`PromptRegistry.list_prompts` 的 7 列）。 */
  content: string;
  /** DRAFT / ACTIVE / DEPRECATED（DB CHECK 约束） */
  status: string;
  /** ``GET /agents/{name}/prompts`` 的**一行**（:meth:`PromptRegistry.list_prompts` 的 7 列）。 */
  created_at: string;
  /** **内容变化或新增**时才刷新；内容逐字相同的重复 sync 完全跳过 UPDATE */
  updated_at: string;
}

export interface AgentPromptSyncResult {
  /** ``POST /agents/sync`` 的 200 响应。 */
  scanned: Array<Array<unknown>>;
  /** ``POST /agents/sync`` 的 200 响应。 */
  registered: Array<Array<unknown>>;
  /** content 发生变化的子集，可能是空数组 */
  updated: Array<Array<unknown>>;
  /** ``POST /agents/sync`` 的 200 响应。 */
  agents: Array<string>;
}

export interface AgentRunResult {
  /** ``POST /agents/{name}/run`` 的 201 响应。 */
  run_id: string;
  /** ``POST /agents/{name}/run`` 的 201 响应。 */
  agent: string;
  /** ``POST /agents/{name}/run`` 的 201 响应。 */
  output: Record<string, unknown>;
}

export interface AiCallLogDetail {
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  call_id: string;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  run_id: string;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  node_run_id?: string | null;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  agent_id?: string | null;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  model_id?: string | null;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  prompt_version?: string | null;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  latency_ms?: number | null;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  retry_count: number;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  error?: string | null;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  token_usage?: Record<string, unknown> | null;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  cost?: number | null;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  created_at: string;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  input_context_ids: Array<string>;
  /** ``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。 */
  output?: unknown;
}

export interface AiCallLogSummary {
  /** ``GET /ai-call-logs`` 的一行：:func:`_summary` 的 12 键摘要。 */
  call_id: string;
  /** ``GET /ai-call-logs`` 的一行：:func:`_summary` 的 12 键摘要。 */
  run_id: string;
  /** ``GET /ai-call-logs`` 的一行：:func:`_summary` 的 12 键摘要。 */
  node_run_id: string | null;
  /** ``GET /ai-call-logs`` 的一行：:func:`_summary` 的 12 键摘要。 */
  agent_id: string | null;
  /** 'provider/model' 字符串（mock 路径下是 'mock/mock'） */
  model_id: string | null;
  /** 形如 'writer:v3'（含 agent 前缀，与 prompts.version 不同形态） */
  prompt_version: string | null;
  /** ``GET /ai-call-logs`` 的一行：:func:`_summary` 的 12 键摘要。 */
  latency_ms: number | null;
  /** 只计「输出不合契约」的重试（0/1），不是引擎的 run 级重试 */
  retry_count: number;
  /** ``GET /ai-call-logs`` 的一行：:func:`_summary` 的 12 键摘要。 */
  error: string | null;
  /** ``GET /ai-call-logs`` 的一行：:func:`_summary` 的 12 键摘要。 */
  token_usage: Record<string, unknown> | null;
  /** ``GET /ai-call-logs`` 的一行：:func:`_summary` 的 12 键摘要。 */
  cost: number | null;
  /** ``GET /ai-call-logs`` 的一行：:func:`_summary` 的 12 键摘要。 */
  created_at: string;
}

export interface ArcAlert {
  /** 节奏 / 伏笔告警。``level='fail'`` 是硬信号（charge_streak_exceeded）， */
  level: "warn" | "fail";
  /** 节奏 / 伏笔告警。``level='fail'`` 是硬信号（charge_streak_exceeded）， */
  code: string;
  /** 节奏 / 伏笔告警。``level='fail'`` 是硬信号（charge_streak_exceeded）， */
  message: string;
}

export interface ArcChapterRow {
  /** 弧光视图里的一行章节。 */
  number: number;
  /** 弧光视图里的一行章节。 */
  title: string;
  /** 由 plan_json.key_beats[].purpose 里的 [payoff] 标记推断，非作者声明的结构 */
  has_payoff_beat: boolean;
  /** 由 plan_json.key_beats[].purpose 里的 [charge] 标记推断 */
  charge_beat: boolean;
  /** 弧光视图里的一行章节。 */
  overall: number | null;
  /** 弧光视图里的一行章节。 */
  pacing: number | null;
  /** 最新草稿的去空白字符数（visible_chars 口径）；无草稿为 null */
  prose_chars: number | null;
  /** 弧光视图里的一行章节。 */
  volume_id: string | null;
  /** 弧光视图里的一行章节。 */
  volume_number: number | null;
}

export interface ArcDebtCounts {
  open: number;
  paid: number;
}

export interface ArcHookCounts {
  open: number;
  resolved: number;
  /** 计划回收章号 < 当前最大章号的伏笔数 */
  overdue: number;
}

export interface ArcOverdueReveal {
  /** 逾期未揭的剧透策略（``reveal_policies.overdue[]`` 的行）。 */
  policy_id: string;
  /** 逾期未揭的剧透策略（``reveal_policies.overdue[]`` 的行）。 */
  target_kind: string;
  /** 逾期未揭的剧透策略（``reveal_policies.overdue[]`` 的行）。 */
  target_id: string;
  /** 逾期未揭的剧透策略（``reveal_policies.overdue[]`` 的行）。 */
  reveal_by_chapter: number | null;
  /** 逾期未揭的剧透策略（``reveal_policies.overdue[]`` 的行）。 */
  audience: string;
}

export interface ArcPayoff {
  /** 回收节奏：全书 payoff 次数与「连续蓄力未回收」的最长/当前连击。 */
  total: number;
  /** 尾部连续蓄力（charge）章数 */
  charge_streak: number;
  /** 回收节奏：全书 payoff 次数与「连续蓄力未回收」的最长/当前连击。 */
  max_charge_streak: number;
  /** 回收节奏：全书 payoff 次数与「连续蓄力未回收」的最长/当前连击。 */
  last_payoff_chapter: number | null;
}

export interface ArcRevealCounts {
  planned: number;
  revealed: number;
  cancelled: number;
  overdue: Array<ArcOverdueReveal>;
}

export interface ArcView {
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  project_id: string;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  generated_at: string;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  chapters: Array<ArcChapterRow>;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  payoff: ArcPayoff;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  hooks: ArcHookCounts;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  debts: ArcDebtCounts;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  reveal_policies: ArcRevealCounts;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  volumes: Array<ArcVolumeRow>;
  /** ``GET /projects/{pid}/arc`` 的响应。 */
  alerts: Array<ArcAlert>;
}

export interface ArcVolumeRow {
  volume_id: string;
  number: number;
  title: string | null;
  /** 卷状态；后端列是 TEXT，未知值原样透出 */
  status: string;
  chapter_count: number;
}

export interface AvailableModelsOut {
  /** ``POST /model-profiles/available-models`` 的 200 响应。 */
  models: Array<string>;
}

export interface BackupMetadata {
  /** 备份包 ``metadata`` 段：导出事实 + 安全排除标志。 */
  schema_migrations: Array<string>;
  /** 备份包 ``metadata`` 段：导出事实 + 安全排除标志。 */
  table_count_exported: number;
  /** 备份包 ``metadata`` 段：导出事实 + 安全排除标志。 */
  exported_table_names: Array<string>;
  /** 备份包 ``metadata`` 段：导出事实 + 安全排除标志。 */
  exported_at_iso: string;
  /** 备份包 ``metadata`` 段：导出事实 + 安全排除标志。 */
  api_keys_stripped: boolean;
  /** 备份包 ``metadata`` 段：导出事实 + 安全排除标志。 */
  ai_call_logs_excluded: boolean;
  /** 备份包 ``metadata`` 段：导出事实 + 安全排除标志。 */
  evaluations_excluded: boolean;
  /** 备份包 ``metadata`` 段：导出事实 + 安全排除标志。 */
  workflow_runs_excluded: boolean;
  /** 备份包 ``metadata`` 段：导出事实 + 安全排除标志。 */
  model_configs_excluded: boolean;
  /** 备份包 ``metadata`` 段：导出事实 + 安全排除标志。 */
  reference_canons_excluded: boolean;
}

export interface BackupPackage {
  /** 备份包顶层（``GET /projects/{project_id}/backup`` 的响应体）。 */
  format: string;
  /** 备份包顶层（``GET /projects/{project_id}/backup`` 的响应体）。 */
  version: number;
  /** 备份包顶层（``GET /projects/{project_id}/backup`` 的响应体）。 */
  exported_at: string;
  /** 备份包顶层（``GET /projects/{project_id}/backup`` 的响应体）。 */
  exported_from_project_id: string;
  /** 备份包顶层（``GET /projects/{project_id}/backup`` 的响应体）。 */
  metadata: BackupMetadata;
  /** 备份包顶层（``GET /projects/{project_id}/backup`` 的响应体）。 */
  project: Record<string, unknown>;
  /** 备份包顶层（``GET /projects/{project_id}/backup`` 的响应体）。 */
  tables: Record<string, unknown>;
}

export interface BindingProfileOut {
  /** ``CapabilityBindingOut.profiles[]`` 的一项。 */
  profile_id: string;
  /** ``CapabilityBindingOut.profiles[]`` 的一项。 */
  name: string;
  /** ``CapabilityBindingOut.profiles[]`` 的一项。 */
  model: string;
  /** ``CapabilityBindingOut.profiles[]`` 的一项。 */
  provider: string;
}

export interface Body_start_deconstruct_upload_api_projects__project_id__deconstruct_upload_post {
  /** 参照书文件（.txt / .epub） */
  file: string;
  /** 书名；留空则从 epub dc:title 兜底 */
  book_title?: string;
  /** 读者档 */
  reader_profile?: string;
}

export interface BranchCreatedResponse {
  /** ``POST /projects/{pid}/branches`` 的 201 响应（实测 7 键恒在）。 */
  branch_id: string;
  /** ``POST /projects/{pid}/branches`` 的 201 响应（实测 7 键恒在）。 */
  project_id: string;
  /** ``POST /projects/{pid}/branches`` 的 201 响应（实测 7 键恒在）。 */
  name: string;
  /** 父分支；建在 main 上时是 main 的 branch_id */
  parent_branch_id: string;
  /** ``POST /projects/{pid}/branches`` 的 201 响应（实测 7 键恒在）。 */
  base_state_version: number;
  /** 建成的瞬间恒为 ACTIVE */
  status: string;
  /** ``POST /projects/{pid}/branches`` 的 201 响应（实测 7 键恒在）。 */
  created_at: string;
}

export interface BranchPromoteResponse {
  /** 空分支时为 null（键恒在） */
  commit_id?: string | null;
  /** 空分支时为 null（键恒在） */
  delta_id?: string | null;
  /** ⚠️ 空分支时是 base_state_version，不是新版本 */
  state_version: number;
  /** ⚠️ 空分支=源分支；非空=main 分支 */
  branch_id?: string | null;
  /** ``POST /projects/{pid}/branches/{bid}/promote`` 的 201 响应——**同一端点两个形状**。 */
  promoted_commits: number;
  /** 本次在 main 上重放出的新 delta_id 列表 */
  replayed_delta_ids: Array<string>;
  /** 只有空分支形状才有（恒为 MERGED） */
  status?: string | null;
  /** 只有非空分支形状才有：源分支 id */
  promoted_from?: string | null;
  /** 只有非空分支形状才有 */
  snapshot_ref?: string | null;
}

export interface BranchSummary {
  /** ``GET /projects/{pid}/branches`` 的**行**（实测恒 7 键）。 */
  branch_id: string;
  /** ``GET /projects/{pid}/branches`` 的**行**（实测恒 7 键）。 */
  project_id: string;
  /** ``GET /projects/{pid}/branches`` 的**行**（实测恒 7 键）。 */
  name: string;
  /** ⚠️ main 分支恒为 null（根节点）；子分支指向父分支 */
  parent_branch_id: string | null;
  /** ``GET /projects/{pid}/branches`` 的**行**（实测恒 7 键）。 */
  base_state_version: number;
  /** ACTIVE / MERGED / DISCARDED；列表里恒含 main（恒 ACTIVE） */
  status: string;
  /** ``GET /projects/{pid}/branches`` 的**行**（实测恒 7 键）。 */
  created_at: string;
}

export interface CanonDetailOut {
  /** ``GET /canons/{canon_id}``：单条 canon 全文 + 其 extracts。 */
  canon_id: string;
  /** ``GET /canons/{canon_id}``：单条 canon 全文 + 其 extracts。 */
  project_id: string;
  /** ``GET /canons/{canon_id}``：单条 canon 全文 + 其 extracts。 */
  title: string;
  /** ``GET /canons/{canon_id}``：单条 canon 全文 + 其 extracts。 */
  reader_profile: string;
  /** ``GET /canons/{canon_id}``：单条 canon 全文 + 其 extracts。 */
  status: string;
  /** ``GET /canons/{canon_id}``：单条 canon 全文 + 其 extracts。 */
  canon_json?: Record<string, unknown> | null;
  /** ``GET /canons/{canon_id}``：单条 canon 全文 + 其 extracts。 */
  report_md: string;
  /** ``GET /canons/{canon_id}``：单条 canon 全文 + 其 extracts。 */
  created_at: string;
  /** ``GET /canons/{canon_id}``：单条 canon 全文 + 其 extracts。 */
  extracts?: Array<CanonExtractOut>;
}

export interface CanonExtractOut {
  /** ``GET /canons/{cid}`` 里 ``extracts[]`` 的一格。 */
  extract_id: string;
  /** ``GET /canons/{cid}`` 里 ``extracts[]`` 的一格。 */
  chapter_index: number;
  /** ``GET /canons/{cid}`` 里 ``extracts[]`` 的一格。 */
  extract_json?: unknown;
  /** ``GET /canons/{cid}`` 里 ``extracts[]`` 的一格。 */
  created_at: string;
}

export interface CanonSummaryOut {
  /** ``GET /projects/{pid}/canons`` 的**列表行**出参（2026-10-15 批次（六）· L4）。 */
  canon_id: string;
  /** ``GET /projects/{pid}/canons`` 的**列表行**出参（2026-10-15 批次（六）· L4）。 */
  project_id: string;
  /** ``GET /projects/{pid}/canons`` 的**列表行**出参（2026-10-15 批次（六）· L4）。 */
  title: string;
  /** ``GET /projects/{pid}/canons`` 的**列表行**出参（2026-10-15 批次（六）· L4）。 */
  reader_profile: string;
  /** ``GET /projects/{pid}/canons`` 的**列表行**出参（2026-10-15 批次（六）· L4）。 */
  status: string;
  /** ``GET /projects/{pid}/canons`` 的**列表行**出参（2026-10-15 批次（六）· L4）。 */
  created_at: string;
  /** ``GET /projects/{pid}/canons`` 的**列表行**出参（2026-10-15 批次（六）· L4）。 */
  logline?: string;
  /** ``GET /projects/{pid}/canons`` 的**列表行**出参（2026-10-15 批次（六）· L4）。 */
  spine_count?: number;
  /** ``GET /projects/{pid}/canons`` 的**列表行**出参（2026-10-15 批次（六）· L4）。 */
  rhythm_chapter_count?: number;
}

export interface CapabilityBindingDeleted {
  /** ``DELETE /capability-bindings/{capability}`` 的 200 响应。 */
  capability: string;
  /** ``DELETE /capability-bindings/{capability}`` 的 200 响应。 */
  deleted: boolean;
}

export interface CapabilityBindingOut {
  /** ``PUT /capability-bindings/{capability}`` 的 200 响应。 */
  capability: string;
  /** 环节中文名，来自 CAPABILITY_LABELS */
  label: string;
  /** 归属该环节的 agent 名（含 'writer(revise·改稿)' 这类带标注形态） */
  agents: Array<string>;
  /** 按 fallback 优先序；未绑定时为空数组 */
  profile_ids: Array<string>;
  /** 按 profile_ids 顺序解析；缺失项跳过 */
  profiles: Array<BindingProfileOut>;
  /** model_configs 中该 capability 有 enabled 行（迁移引导用） */
  legacy_available: boolean;
  /** 未绑定时为 null（不是省略） */
  updated_at: string | null;
}

export interface ChangedDiffItemResponse {
  /** 稳定键：character_id / hook_id / debt_id / … */
  id: string;
  /** ``_diff_lists`` 的 changed 元素：``{"id": <稳定键>, "changes": {…}}``（实测）。 */
  changes?: Record<string, unknown> | null;
}

export interface ChangedItem {
  /** ``_diff_lists`` 的 changed 元素：``{"id": <稳定键>, "changes": {…}}``。 */
  id: string;
  /** ``_diff_lists`` 的 changed 元素：``{"id": <稳定键>, "changes": {…}}``。 */
  changes?: Record<string, unknown> | null;
}

export interface Chapter {
  /** 章节完整表示，对应数据库行。 */
  chapter_id: string;
  /** 章节完整表示，对应数据库行。 */
  project_id: string;
  /** 章节完整表示，对应数据库行。 */
  number: number;
  /** 章节完整表示，对应数据库行。 */
  title: string | null;
  /** 章节完整表示，对应数据库行。 */
  plan_json: Record<string, unknown>;
  /** 章节完整表示，对应数据库行。 */
  outline_json?: Record<string, unknown> | null;
  /** 章节完整表示，对应数据库行。 */
  status: "PLANNED" | "DRAFTED" | "REVIEWED" | "COMMITTED" | "RELEASED";
  /** 章节完整表示，对应数据库行。 */
  visibility: string;
  /** 章节完整表示，对应数据库行。 */
  who_knows: Array<string> | null;
  /** 章节完整表示，对应数据库行。 */
  created_at: string;
  /** 章节完整表示，对应数据库行。 */
  updated_at: string;
}

export interface ChapterCreate {
  /** 创建章节请求体。 */
  number: number;
  /** 创建章节请求体。 */
  title?: string | null;
  /** 创建章节请求体。 */
  plan_json?: Record<string, unknown> | null;
}

export interface ChapterReopenRequest {
  /** 重开返修请求体（全部可选，可省略整个 body）。 */
  reason?: string | null;
}

export interface ChapterUpdate {
  /** 部分更新请求体——所有字段均可选。 */
  title?: string | null;
  /** 部分更新请求体——所有字段均可选。 */
  plan_json?: Record<string, unknown> | null;
  /** 部分更新请求体——所有字段均可选。 */
  outline_json?: Record<string, unknown> | null;
  /** 部分更新请求体——所有字段均可选。 */
  status?: "PLANNED" | "DRAFTED" | "REVIEWED" | "COMMITTED" | "RELEASED" | null;
}

export interface Character {
  /** 角色完整表示。 */
  character_id: string;
  /** 角色完整表示。 */
  project_id: string;
  /** 角色完整表示。 */
  name: string;
  /** 角色完整表示。 */
  role: "protagonist" | "antagonist" | "supporting" | "mentor" | "love_interest" | "narrator" | "other";
  /** 角色完整表示。 */
  core_json: Record<string, unknown>;
  /** 角色完整表示。 */
  visibility: "PUBLIC" | "VISIBLE" | "RESTRICTED" | "HIDDEN";
  /** 角色完整表示。 */
  who_knows: Array<string> | null;
  /** 角色完整表示。 */
  created_at: string;
  /** 角色完整表示。 */
  updated_at: string;
  /** 角色完整表示。 */
  latest_state_version: number;
  /** 角色完整表示。 */
  latest_state_json: Record<string, unknown>;
  /** 角色完整表示。 */
  aliases?: Array<string>;
  /** 角色完整表示。 */
  inject_mode?: "auto" | "always" | "never";
}

export interface CharacterCreate {
  /** 创建角色请求体。 */
  name: string;
  /** 创建角色请求体。 */
  role?: "protagonist" | "antagonist" | "supporting" | "mentor" | "love_interest" | "narrator" | "other" | null;
  /** 创建角色请求体。 */
  core_json?: Record<string, unknown> | null;
  /** 创建角色请求体。 */
  visibility?: "PUBLIC" | "VISIBLE" | "RESTRICTED" | "HIDDEN" | null;
  /** 创建角色请求体。 */
  who_knows?: Array<string> | null;
  /** 创建角色请求体。 */
  aliases?: Array<string> | null;
  /** 创建角色请求体。 */
  inject_mode?: "auto" | "always" | "never" | null;
}

export interface CharacterState {
  /** 角色状态快照行（character_states 表）。 */
  character_id: string;
  /** 角色状态快照行（character_states 表）。 */
  state_version: number;
  /** 角色状态快照行（character_states 表）。 */
  state_json: Record<string, unknown>;
  /** 角色状态快照行（character_states 表）。 */
  visibility: "PUBLIC" | "VISIBLE" | "RESTRICTED" | "HIDDEN";
  /** 角色状态快照行（character_states 表）。 */
  who_knows: Array<string> | null;
  /** 角色状态快照行（character_states 表）。 */
  created_at: string;
}

export interface CharacterUpdate {
  /** 部分更新请求体——所有字段均可选。 */
  name?: string | null;
  /** 部分更新请求体——所有字段均可选。 */
  role?: "protagonist" | "antagonist" | "supporting" | "mentor" | "love_interest" | "narrator" | "other" | null;
  /** 部分更新请求体——所有字段均可选。 */
  core_json?: Record<string, unknown> | null;
  /** 部分更新请求体——所有字段均可选。 */
  visibility?: "PUBLIC" | "VISIBLE" | "RESTRICTED" | "HIDDEN" | null;
  /** 部分更新请求体——所有字段均可选。 */
  who_knows?: Array<string> | null;
  /** 部分更新请求体——所有字段均可选。 */
  aliases?: Array<string> | null;
  /** 部分更新请求体——所有字段均可选。 */
  inject_mode?: "auto" | "always" | "never" | null;
}

export interface CommitListItem {
  /** ``GET /projects/{pid}/commits`` 的**行**（实测恒 13 键）。 */
  commit_id: string;
  /** ``GET /projects/{pid}/commits`` 的**行**（实测恒 13 键）。 */
  project_id: string;
  /** NOT NULL；main commit 与分支 commit 各指向自己的分支 */
  branch_id: string;
  /** ``GET /projects/{pid}/commits`` 的**行**（实测恒 13 键）。 */
  chapter_id: string;
  /** ``GET /projects/{pid}/commits`` 的**行**（实测恒 13 键）。 */
  previous_state_version: number;
  /** 本 commit 产生的版本；回滚 commit 同样占一个版本号 */
  resulting_state_version: number;
  /** ``GET /projects/{pid}/commits`` 的**行**（实测恒 13 键）。 */
  delta_id: string;
  /** NOT NULL 列；解析失败时为 null */
  validation_json: Record<string, unknown> | null;
  /** NOT NULL 列；解析失败时为 null */
  author_approval_json: Record<string, unknown> | null;
  /** ``GET /projects/{pid}/commits`` 的**行**（实测恒 13 键）。 */
  timestamp: string;
  /** ``GET /projects/{pid}/commits`` 的**行**（实测恒 13 键）。 */
  workflow_run_id: string;
  /** 只有回滚产生的 commit 非 null；其余恒为 null */
  rollback_of: string | null;
  /** 本次 commit 定稿的草稿版本号；存量行与未指定该参数的提交恒为 null */
  draft_version: number | null;
}

export interface CommitResponse {
  /** ``POST /projects/{pid}/commits`` 的 201 响应（实测 4 键恒在）。 */
  commit_id: string;
  /** ``POST /projects/{pid}/commits`` 的 201 响应（实测 4 键恒在）。 */
  delta_id: string;
  /** ``POST /projects/{pid}/commits`` 的 201 响应（实测 4 键恒在）。 */
  state_version: number;
  /** main commit 才有；分支 commit 恒为 null（键本身恒在） */
  snapshot_ref?: string | null;
}

export interface ConfigTestResult {
  /** ``POST /model-configs/{id}/test`` 的 200 响应。 */
  config_id: string;
  /** ``POST /model-configs/{id}/test`` 的 200 响应。 */
  ok: boolean;
  /** ``POST /model-configs/{id}/test`` 的 200 响应。 */
  latency_ms: number;
  /** ``POST /model-configs/{id}/test`` 的 200 响应。 */
  detail: string;
  /** ``POST /model-configs/{id}/test`` 的 200 响应。 */
  status_code: number | null;
}

export interface ConnectionPresetOut {
  /** ``GET /model-connections/presets`` 的元素：一档「连接预设」。 */
  preset_id: string;
  /** ``GET /model-connections/presets`` 的元素：一档「连接预设」。 */
  label: string;
  /** ``GET /model-connections/presets`` 的元素：一档「连接预设」。 */
  provider: string;
  /** ``GET /model-connections/presets`` 的元素：一档「连接预设」。 */
  base_url?: string | null;
  /** 协议地址池（2026-10-24）：协议 → 该网关此协议的 base_url；只有多协议网关才带（如 opencode_go），缺席 = 无覆盖，全部走 base_url */
  protocol_base_urls?: Record<string, unknown> | null;
  /** ``GET /model-connections/presets`` 的元素：一档「连接预设」。 */
  params?: Record<string, unknown>;
  /** ``GET /model-connections/presets`` 的元素：一档「连接预设」。 */
  note?: string | null;
}

export interface ConnectionTestResult {
  /** ``POST /model-connections/{id}/test`` 的 200 响应。 */
  connection_id: string;
  /** false 表示「端点不可达或健康不过」，仍是 200 */
  ok: boolean;
  /** ``POST /model-connections/{id}/test`` 的 200 响应。 */
  latency_ms: number;
  /** 恒为字符串（router 用 `or ''` 兜底，不会是 null） */
  detail: string;
  /** 上游 HTTP 码；网络异常时为 null */
  status_code: number | null;
  /** 探测路径：'models'（模型列表端点）/ 'chat'（退化探测） */
  probe?: string | null;
}

export interface ContextPreview {
  /** ``GET /chapters/{chapter_id}/context-preview`` 的 200 响应（实测恒 7 键）。 */
  chapter_id: string;
  /** ``GET /chapters/{chapter_id}/context-preview`` 的 200 响应（实测恒 7 键）。 */
  project_id: string;
  /** 被预览的 agent 名，实测恒 ['director', 'writer', 'observer'] */
  agents: Array<string>;
  /** 恒 3 层，顺序 L0→L1→L2 */
  layers: Array<ContextPreviewLayer>;
  /** ``GET /chapters/{chapter_id}/context-preview`` 的 200 响应（实测恒 7 键）。 */
  total_tokens: number;
  /** ``GET /chapters/{chapter_id}/context-preview`` 的 200 响应（实测恒 7 键）。 */
  token_budget: number;
  /** total_tokens <= token_budget */
  within_budget: boolean;
}

export interface ContextPreviewItem {
  /** 条目类型：chapter / project / character / hook / director_plan / … */
  kind: string;
  /** 实体 id；非实体条目用下划线常量（如 _snap / _intent / _kp） */
  id: string;
  /** 人类可读摘要 */
  name: string;
}

export interface ContextPreviewLayer {
  /** L0 / L1 / L2 */
  id: string;
  /** 该层装载内容的人类可读说明 */
  label: string;
  /** 该层的 token 估算 */
  token_estimate: number;
  /** 预览的单个分层（L0 / L1 / L2）。实测三层**逐字同形**、恒 5 键。 */
  items: Array<ContextPreviewItem>;
  /** 是否因超预算被截断；实测恒 false（截断尚未实现） */
  truncated: boolean;
}

export interface ContinueRequest {
  /** 续写请求体。 */
  num_variants?: number;
  /** 续写请求体。 */
  instruction?: string;
}

export interface ContinueResponse {
  /** 续写响应。 */
  variants: Array<ContinueVariant>;
  /** 续写响应。 */
  model: string;
  /** 续写响应。 */
  total_elapsed_ms: number;
}

export interface ContinueVariant {
  /** 单个候选 variant 的响应字段。 */
  index: number;
  /** 单个候选 variant 的响应字段。 */
  text: string | null;
  /** 单个候选 variant 的响应字段。 */
  tokens: number | null;
  /** 单个候选 variant 的响应字段。 */
  elapsed_ms: number;
  /** 单个候选 variant 的响应字段。 */
  error?: string | null;
}

export interface Debt {
  /** Debt 完整表示，对应数据库行。 */
  debt_id: string;
  /** Debt 完整表示，对应数据库行。 */
  project_id: string;
  /** Debt 完整表示，对应数据库行。 */
  description: string;
  /** Debt 完整表示，对应数据库行。 */
  created_chapter_id: string | null;
  /** Debt 完整表示，对应数据库行。 */
  severity: number;
  /** Debt 完整表示，对应数据库行。 */
  deadline_chapter_id: string | null;
  /** Debt 完整表示，对应数据库行。 */
  status: "open" | "acknowledged" | "paid" | "forgiven";
  /** Debt 完整表示，对应数据库行。 */
  visibility: string;
  /** Debt 完整表示，对应数据库行。 */
  who_knows: Array<string> | null;
  /** Debt 完整表示，对应数据库行。 */
  created_at: string;
  /** Debt 完整表示，对应数据库行。 */
  updated_at: string;
}

export interface DebtCreate {
  /** 创建 Debt 请求体。 */
  description: string;
  /** 创建 Debt 请求体。 */
  created_chapter_id?: string | null;
  /** 创建 Debt 请求体。 */
  deadline_chapter_id?: string | null;
  /** 创建 Debt 请求体。 */
  status?: "open" | "acknowledged" | "paid" | "forgiven" | null;
  /** 创建 Debt 请求体。 */
  severity?: number;
  /** 创建 Debt 请求体。 */
  visibility?: string | null;
  /** 创建 Debt 请求体。 */
  who_knows?: Array<string> | null;
}

export interface DebtUpdate {
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  description?: string | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  created_chapter_id?: string | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  deadline_chapter_id?: string | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  status?: "open" | "acknowledged" | "paid" | "forgiven" | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  severity?: number | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  visibility?: string | null;
  /** 部分更新 Debt 请求体——所有字段均可选。 */
  who_knows?: Array<string> | null;
}

export interface DeconstructStartResponse {
  /** ``POST .../deconstruct`` 与 ``POST .../deconstruct-upload`` 的出参（两端点同构）。 */
  run_id: string;
  /** ``POST .../deconstruct`` 与 ``POST .../deconstruct-upload`` 的出参（两端点同构）。 */
  status: string;
  /** ``POST .../deconstruct`` 与 ``POST .../deconstruct-upload`` 的出参（两端点同构）。 */
  current_node?: string | null;
  /** ``POST .../deconstruct`` 与 ``POST .../deconstruct-upload`` 的出参（两端点同构）。 */
  project_id: string;
  /** ``POST .../deconstruct`` 与 ``POST .../deconstruct-upload`` 的出参（两端点同构）。 */
  book_title: string;
  /** ``POST .../deconstruct`` 与 ``POST .../deconstruct-upload`` 的出参（两端点同构）。 */
  canon_id?: string | null;
  /** ``POST .../deconstruct`` 与 ``POST .../deconstruct-upload`` 的出参（两端点同构）。 */
  extracts_count?: number | null;
  /** ``POST .../deconstruct`` 与 ``POST .../deconstruct-upload`` 的出参（两端点同构）。 */
  error?: string | null;
}

export interface DeliveryChapter {
  /** 一章的交付判定行。 */
  chapter_id: string;
  /** 一章的交付判定行。 */
  number: number;
  /** 一章的交付判定行。 */
  title: string;
  /** 章节状态（流程位置，非交付判定） */
  status: string;
  /** 一章的交付判定行。 */
  verdict: "deliverable" | "needs_work" | "not_deliverable" | "not_a_candidate";
  /** 首个降级理由的中文说明；可交付时为 null */
  blocking_reason?: string | null;
  /** 一章的交付判定行。 */
  draft_version?: number | null;
  /** 一章的交付判定行。 */
  length?: DeliveryLength | null;
  /** 一章的交付判定行。 */
  quality?: DeliveryQuality | null;
  /** 一章的交付判定行。 */
  signing: DeliverySigning;
  /** 一章的交付判定行。 */
  reasons?: Array<DeliveryReason>;
}

export interface DeliveryEvidenceSource {
  /** 证据源可达性（诚实面：看不到的要说出来）。 */
  key: string;
  /** 证据源可达性（诚实面：看不到的要说出来）。 */
  reachable: boolean;
  /** 证据源可达性（诚实面：看不到的要说出来）。 */
  detail: string;
}

export interface DeliveryIssue {
  /** 质量报告里的一条 issue（gate 由 ``issue_gate`` 权威重推，不读落库字段值）。 */
  rule_id: string;
  /** 质量报告里的一条 issue（gate 由 ``issue_gate`` 权威重推，不读落库字段值）。 */
  severity: string;
  /** 后果档 auto / confirm / block */
  gate: string;
  /** 质量报告里的一条 issue（gate 由 ``issue_gate`` 权威重推，不读落库字段值）。 */
  message: string;
}

export interface DeliveryLength {
  /** 字数测量（``quality.wordcount`` 口径）。 */
  visible_chars: number;
  /** 字数测量（``quality.wordcount`` 口径）。 */
  target_word_count: number;
  /** 字数测量（``quality.wordcount`` 口径）。 */
  band_low: number;
  /** 字数测量（``quality.wordcount`` 口径）。 */
  band_high: number;
  /** 字数测量（``quality.wordcount`` 口径）。 */
  deviation_pct: number;
  /** in_band / under / over */
  status: string;
  /** in_band / warn / error（两级阈值同 review 侧） */
  tier: string;
}

export interface DeliveryQuality {
  /** 该章最新质量报告的证据面。 */
  report_id: string;
  /** 该章最新质量报告的证据面。 */
  overall: number;
  /** 该章最新质量报告的证据面。 */
  evaluated_at?: string | null;
  /** 本报告评估的草稿版本 */
  draft_version?: number | null;
  /** 报告 _meta.gate_summary（block / confirm 聚合摘要） */
  gate_summary?: Record<string, unknown> | null;
  /** 报告 _meta.gate_accepted_override（confirm 档接受留痕） */
  accepted_override?: Record<string, unknown> | null;
  /** 该章最新质量报告的证据面。 */
  issues?: Array<DeliveryIssue>;
}

export interface DeliveryReason {
  /** 规则 / 证据来源标识（前端按此分支） */
  rule_id: string;
  /** 证据出处 */
  source: "quality_issue" | "length" | "signing_check" | "chapter_status" | "evidence";
  /** 该证据自身的严重度（error / warning / info） */
  severity: string;
  /** 后果档（决定判定是否降级） */
  status: "block" | "confirm" | "accepted" | "warn" | "length_out_of_band" | "length_beyond_band_edge" | "signing_fail" | "not_committed" | "not_evaluated" | "out_of_scope" | "not_a_candidate" | "project_not_deliverable" | "project_needs_work";
  /** 面向作者的中文说明 */
  message: string;
  /** 证据摘录（引文片段或数字） */
  evidence?: string | null;
}

export interface DeliveryRollUp {
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  chapter_count: number;
  /** 非 PLANNED 的章节数 */
  written_chapter_count: number;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  deliverable_count: number;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  needs_work_count: number;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  not_deliverable_count: number;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  not_a_candidate_count: number;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  deliverable_chapter_ids: Array<string>;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  needs_work_chapter_ids: Array<string>;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  not_deliverable_chapter_ids: Array<string>;
  /** 项目级汇总（作者的问题是「这本书能不能交」）。 */
  not_a_candidate_chapter_ids: Array<string>;
  /** 每章 id → 首个降级理由（可交付章不出现） */
  blocking_reason_by_chapter?: Record<string, unknown>;
}

export interface DeliverySigning {
  /** in_scope / out_of_scope / unavailable */
  scope: string;
  /** 签约体检在本章的适用面。 */
  note?: string;
  /** 签约体检在本章的适用面。 */
  items?: Array<DeliverySigningItem>;
}

export interface DeliverySigningItem {
  /** 签约体检的单项（仅第 1~3 章）。 */
  key: string;
  /** pass / warn / fail / info */
  level: string;
  /** 签约体检的单项（仅第 1~3 章）。 */
  detail: string;
  /** 签约体检的单项（仅第 1~3 章）。 */
  advice?: string;
}

export interface DeliveryVerdictResponse {
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  project_id: string;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  project_name: string;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  generated_at: string;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  project_verdict: "deliverable" | "needs_work" | "not_deliverable" | "not_a_candidate";
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  project_reasons?: Array<DeliveryReason>;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  roll_up: DeliveryRollUp;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  evidence_sources?: Array<DeliveryEvidenceSource>;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  signing_check_scope?: string;
  /** ``GET /api/projects/{project_id}/delivery-verdict`` 的响应体。 */
  chapters?: Array<DeliveryChapter>;
}

export interface Draft {
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  draft_id: string;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  chapter_id: string;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  version: number;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  content: string;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  created_by: string;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  prompt_version: string | null;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  model_id: string | null;
  /** 草稿完整表示，对应 ``drafts`` 表行。 */
  created_at: string;
}

export interface DraftCreate {
  /** 创建草稿请求体（人工改稿入口）。 */
  content: string;
}

export interface GateReviseRequest {
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  mock_providers?: Record<string, unknown> | null;
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  model_overrides?: Record<string, unknown> | null;
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  critic_mode?: string | null;
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  deep_review?: boolean | null;
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  target_word_count?: number | null;
  /** 「按门禁建议改稿」请求体（V3.9 批次 4.1）。 */
  author_intent?: string | null;
}

export interface GenreBindRequest {
  /** 绑定请求体：``{"pack_id": "..."}``。 */
  pack_id: string;
}

export interface GenreOpeningRule {
  /** 题材开篇规则的单条机检判定（``evaluate_genre_opening`` 的输出）。 */
  check_id: string;
  /** 题材开篇规则的单条机检判定（``evaluate_genre_opening`` 的输出）。 */
  chapter_no?: number | null;
  /** 题材开篇规则的单条机检判定（``evaluate_genre_opening`` 的输出）。 */
  description: string;
  /** 题材开篇规则的单条机检判定（``evaluate_genre_opening`` 的输出）。 */
  requirement: string;
  /** 题材开篇规则的单条机检判定（``evaluate_genre_opening`` 的输出）。 */
  status: string;
  /** 题材开篇规则的单条机检判定（``evaluate_genre_opening`` 的输出）。 */
  detail: string;
  /** 题材开篇规则的单条机检判定（``evaluate_genre_opening`` 的输出）。 */
  keywords_source?: string | null;
}

export interface GenreOpeningSection {
  /** 题材开篇检查段（恒 9 键）。 */
  pack_id: string;
  /** 题材开篇检查段（恒 9 键）。 */
  pack_name: string;
  /** 题材开篇检查段（恒 9 键）。 */
  rule_count: number;
  /** 题材开篇检查段（恒 9 键）。 */
  rules: Array<GenreOpeningRule>;
  /** 题材开篇检查段（恒 9 键）。 */
  pass_count: number;
  /** 题材开篇检查段（恒 9 键）。 */
  fail_count: number;
  /** 题材开篇检查段（恒 9 键）。 */
  unverifiable_count: number;
  /** 题材开篇检查段（恒 9 键）。 */
  info_count: number;
}

export interface GenrePack {
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  pack_id: string;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  name: string;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  genre_tag: string;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  version: number;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  payload: Record<string, unknown>;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  source_path: string | null;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  created_at: string;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  updated_at: string;
  /** 题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。 */
  bound_project_count?: number;
}

export interface GenrePackBinding {
  /** 项目 ↔ 题材包绑定（单 slot）的出参形状。 */
  project_id: string;
  /** 项目 ↔ 题材包绑定（单 slot）的出参形状。 */
  pack_id?: string | null;
  /** 项目 ↔ 题材包绑定（单 slot）的出参形状。 */
  bound: boolean;
  /** 项目 ↔ 题材包绑定（单 slot）的出参形状。 */
  pack?: GenrePack | null;
}

export interface GenrePackCreate {
  /** 创建题材包请求体（POST /projects/{pid}/genre-packs）。 */
  name: string;
  /** 创建题材包请求体（POST /projects/{pid}/genre-packs）。 */
  genre_tag: string;
  /** 创建题材包请求体（POST /projects/{pid}/genre-packs）。 */
  payload?: Record<string, unknown>;
  /** 显式 pack_id（内容仓 slug）；省略则生成 gp_<12hex>。 */
  pack_id?: string | null;
  /** 内容仓来源路径（审计用；软件层不解析）。 */
  source_path?: string | null;
}

export interface GenrePackSummary {
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  pack_id: string;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  name: string;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  genre_tag: string;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  version: number;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  source_path?: string | null;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  created_at: string;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  updated_at: string;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  payoff_type_count?: number;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  structure_model?: string | null;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  chapter_words_target?: number | null;
  /** 题材包摘要投影（列表端点 / 绑定响应 / 预览）。 */
  bound_project_count?: number;
}

export interface GenrePackUpdate {
  /** 更新题材包请求体（PUT /genre-packs/{pack_id}）；提供 payload 时 version 自增。 */
  name?: string | null;
  /** 更新题材包请求体（PUT /genre-packs/{pack_id}）；提供 payload 时 version 自增。 */
  genre_tag?: string | null;
  /** 更新题材包请求体（PUT /genre-packs/{pack_id}）；提供 payload 时 version 自增。 */
  payload?: Record<string, unknown> | null;
  /** 更新题材包请求体（PUT /genre-packs/{pack_id}）；提供 payload 时 version 自增。 */
  source_path?: string | null;
}

export interface HTTPValidationError {
  detail?: Array<ValidationError>;
}

export interface HealthResponse {
  /** 恒为 ``ok``（本端点只在应用已起来时才可达） */
  status: string;
  /** pyproject 单源版本，经 importlib 读取 */
  version: string;
  /** **业务表数**（已扣除 ``_migrations`` 虚表） */
  tables: number;
}

export interface Hook {
  /** Hook 完整表示，对应数据库行。 */
  hook_id: string;
  /** Hook 完整表示，对应数据库行。 */
  project_id: string;
  /** Hook 完整表示，对应数据库行。 */
  name: string;
  /** Hook 完整表示，对应数据库行。 */
  introduced_chapter_id: string | null;
  /** Hook 完整表示，对应数据库行。 */
  status: "OPEN" | "ACTIVE" | "ESCALATED" | "RESOLVED" | "ABANDONED";
  /** Hook 完整表示，对应数据库行。 */
  importance: number;
  /** Hook 完整表示，对应数据库行。 */
  expected_payoff_chapter_id: string | null;
  /** Hook 完整表示，对应数据库行。 */
  payoff_chapter_id: string | null;
  /** Hook 完整表示，对应数据库行。 */
  visibility: string;
  /** Hook 完整表示，对应数据库行。 */
  who_knows: Array<string> | null;
  /** Hook 完整表示，对应数据库行。 */
  created_at: string;
  /** Hook 完整表示，对应数据库行。 */
  updated_at: string;
}

export interface HookCreate {
  /** 创建 Hook 请求体。 */
  name: string;
  /** 创建 Hook 请求体。 */
  introduced_chapter_id?: string | null;
  /** 创建 Hook 请求体。 */
  expected_payoff_chapter_id?: string | null;
  /** 创建 Hook 请求体。 */
  payoff_chapter_id?: string | null;
  /** 创建 Hook 请求体。 */
  status?: "OPEN" | "ACTIVE" | "ESCALATED" | "RESOLVED" | "ABANDONED" | null;
  /** 创建 Hook 请求体。 */
  importance?: number;
  /** 创建 Hook 请求体。 */
  visibility?: string | null;
  /** 创建 Hook 请求体。 */
  who_knows?: Array<string> | null;
}

export interface HookUpdate {
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  name?: string | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  introduced_chapter_id?: string | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  expected_payoff_chapter_id?: string | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  payoff_chapter_id?: string | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  status?: "OPEN" | "ACTIVE" | "ESCALATED" | "RESOLVED" | "ABANDONED" | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  importance?: number | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  visibility?: string | null;
  /** 部分更新 Hook 请求体——所有字段均可选。 */
  who_knows?: Array<string> | null;
}

export interface ImportedProject {
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  project_id: string;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  name: string;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  status: string;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  created_at: string;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  updated_at: string;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  premise?: string | null;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  genre?: string | null;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  target_words?: number | null;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  foreshadow_overdue_chapters: number;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  word_band_json?: string | null;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  genre_pack_id?: string | null;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  writing_bible?: string | null;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  fast_mode?: number | null;
  /** ``POST /projects/import-backup`` 的响应：导入出的**新** projects 行。 */
  import_warnings?: Array<string> | null;
}

export interface ListDiff {
  /** 列表段的差异（``_diff_lists`` 的返回）。三个子键同样**空则省略**。 */
  added?: Array<Record<string, unknown>> | null;
  /** 列表段的差异（``_diff_lists`` 的返回）。三个子键同样**空则省略**。 */
  removed?: Array<Record<string, unknown>> | null;
  /** 列表段的差异（``_diff_lists`` 的返回）。三个子键同样**空则省略**。 */
  changed?: Array<ChangedItem> | null;
}

export interface ListDiffResponse {
  /** ``_diff_lists`` 的返回：``added`` / ``removed`` / ``changed``，三者都**空则省略**。 */
  added?: Array<Record<string, unknown>> | null;
  /** ``_diff_lists`` 的返回：``added`` / ``removed`` / ``changed``，三者都**空则省略**。 */
  removed?: Array<Record<string, unknown>> | null;
  /** ``_diff_lists`` 的返回：``added`` / ``removed`` / ``changed``，三者都**空则省略**。 */
  changed?: Array<ChangedDiffItemResponse> | null;
}

export interface ModelConfigOut {
  /** ``model_configs`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  config_id: string;
  /** ``model_configs`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  capability: string;
  /** ``model_configs`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  provider: string;
  /** ``model_configs`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  model: string;
  /** provider 调参解析后的对象；其中 api_key 恒被替换为 '***'，明文不出参 */
  params_json: Record<string, unknown>;
  /** 0/1 整数（非布尔）；0 的配置不可用于 /test */
  enabled: number;
  /** ``model_configs`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  has_api_key: boolean;
}

export interface ModelConnectionOut {
  /** ``model_connections`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  connection_id: string;
  /** ``model_connections`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  name: string;
  /** ``model_connections`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  provider: string;
  /** 连接级 base_url；anthropic / ollama 可空（用 provider 默认值） */
  base_url?: string | null;
  /** 协议地址池（2026-10-24）：协议 → 该网关此协议的 base_url；null/空 = 无覆盖，全部走 base_url。绑定态解析按**档案的协议**取址（provider 是模型级属性，连接不再选协议） */
  protocol_base_urls?: Record<string, unknown> | null;
  /** 连接级调参（api_key / headers / models_url）解析后的对象；其中 api_key 恒被替换为 '***'，headers 里的敏感头逐键脱敏 */
  params_json?: Record<string, unknown>;
  /** 0/1 整数（非布尔）；0 的连接不可用于 /test */
  enabled: number;
  /** ``model_connections`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  created_at: string;
  /** ``model_connections`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  updated_at: string;
  /** 是否已配置密钥；涵盖 params.api_key 与 secrets.json 两个来源 */
  has_api_key: boolean;
}

export interface ModelProfileOut {
  /** ``model_profiles`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  profile_id: string;
  /** ``model_profiles`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  name: string;
  /** ``model_profiles`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  provider: string;
  /** ``model_profiles`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  model: string;
  /** provider 调参（temperature / top_p / base_url / timeout_s …）解析后的对象；其中 api_key 恒被替换为 '***'，明文不出参 */
  params_json: Record<string, unknown>;
  /** 0/1 整数（非布尔）；0 的档案不可用于 /test */
  enabled: number;
  /** ``model_profiles`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  created_at: string;
  /** ``model_profiles`` 单行 + ``has_api_key``（``POST`` / ``GET`` / ``PATCH`` 共用）。 */
  updated_at: string;
  /** 是否已配置密钥；涵盖 params.api_key 与 secrets.json 两个来源 */
  has_api_key: boolean;
  /** 引用的连接 id（model_connections）；null = 该档案是「独立配置」（自带 base_url / api_key，存量档案的默认形态） */
  connection_id?: string | null;
}

export interface OutlineBatchRequest {
  /** outline-batch（批量续规划下一批章节）触发请求体。 */
  chapter_count?: number;
  /** outline-batch（批量续规划下一批章节）触发请求体。 */
  mock_providers?: Record<string, unknown> | null;
  /** outline-batch（批量续规划下一批章节）触发请求体。 */
  model_profile_id?: string | null;
}

export interface PlotEventOut {
  /** ``POST|GET|PATCH /api/events/{id}`` 的响应。 */
  id: string;
  /** ``POST|GET|PATCH /api/events/{id}`` 的响应。 */
  project_id: string;
  /** ``POST|GET|PATCH /api/events/{id}`` 的响应。 */
  type: string;
  /** 引用的上游 event_id 列表（已从 cause_json 解码） */
  cause: Array<string>;
  /** 影响的下游 event_id 列表（已从 effects_json 解码） */
  effects: Array<string>;
  /** 参与角色 character_id 列表（已从 participants_json 解码） */
  participants: Array<string>;
  /** ``POST|GET|PATCH /api/events/{id}`` 的响应。 */
  location_id: string | null;
  /** 已从 time_json 解码，形如 {timeline_day, in_story_date} */
  time: Record<string, unknown>;
  /** ``POST|GET|PATCH /api/events/{id}`` 的响应。 */
  status: string;
  /** ``POST|GET|PATCH /api/events/{id}`` 的响应。 */
  introduced_chapter_id: string | null;
  /** ``POST|GET|PATCH /api/events/{id}`` 的响应。 */
  visibility: string;
  /** 知情者 character_id 列表；NULL=沿用默认，[]=显式置空 */
  who_knows: Array<string> | null;
  /** 迁移 0013 的列；本字段曾因「模型里没有」而对 API 隐形 */
  description: string | null;
}

export interface ProfileTestResult {
  /** ``POST /model-profiles/{id}/test`` 的 200 响应。 */
  profile_id: string;
  /** false 表示「端点可达但鉴权/健康不过」，仍是 200 */
  ok: boolean;
  /** ``POST /model-profiles/{id}/test`` 的 200 响应。 */
  latency_ms: number;
  /** 恒为字符串（router 用 `or ''` 兜底，不会是 null） */
  detail: string;
  /** 上游 HTTP 码；网络异常时为 null */
  status_code: number | null;
  /** 仅 400 + 远端错误文本命中模型名标记时出现的中文提示；否则整个键不存在 */
  hint?: string | null;
  /** 连通性探测走的是哪条路：'models'（GET 模型列表端点，默认）/ 'chat'（模型列表端点 404/405/501 时退化的极小请求探测）。⚠️ 降级探测会**真的消耗 1 个 token**（上游收了一次请求）——它只发生在用户手工触发的 /test 路径上 */
  probe?: string | null;
}

export interface Project {
  /** 项目完整表示，对应数据库行。 */
  project_id: string;
  /** 项目完整表示，对应数据库行。 */
  name: string;
  /** 项目完整表示，对应数据库行。 */
  premise: string | null;
  /** 项目完整表示，对应数据库行。 */
  genre: string | null;
  /** 项目完整表示，对应数据库行。 */
  target_words: number | null;
  /** 项目完整表示，对应数据库行。 */
  status: "ACTIVE" | "PAUSED" | "ARCHIVED";
  /** 项目完整表示，对应数据库行。 */
  created_at: string;
  /** 项目完整表示，对应数据库行。 */
  updated_at: string;
  /** 项目完整表示，对应数据库行。 */
  foreshadow_overdue_chapters?: number | null;
  /** 字数带覆盖（low_ratio / high_ratio / floor）；None = 项目无覆盖，消费点走模块默认 0.85 / 1.15 / 1200。 */
  word_band?: Record<string, unknown> | null;
  /** 绑定的题材包 id（题材库单 slot）；None = 未绑定 / 极老库缺列。绑定与解绑走 POST /projects/{pid}/genre-pack/bind|unbind。 */
  genre_pack_id?: string | null;
  /** 项目写作圣经（迁移 0029 的可空列）；None = 无圣经 / 极老库缺列。写入走 POST /projects 与 PATCH /projects/{pid}；不被任何工作流覆盖。 */
  writing_bible?: string | null;
  /** 快通道开关（迁移 0031 的可空三态列）；None = 未设置 / 极老库缺列 → 解析时回落 env 与默认值（关）。读侧布尔化（NULL→False）。 */
  fast_mode?: boolean | null;
}

export interface ProjectCreate {
  /** 创建项目请求体。 */
  name: string;
  /** 创建项目请求体。 */
  premise?: string | null;
  /** 创建项目请求体。 */
  genre?: string | null;
  /** 创建项目请求体。 */
  target_words?: number | null;
  /** 伏笔 overdue 阈值（章节数）；省略时使用 DDL 默认 30。 */
  foreshadow_overdue_chapters?: number | null;
  /** 字数带覆盖（low_ratio / high_ratio / floor 三键可任选）；省略/None=无覆盖。校验在 router 层走 resolve_band_config。 */
  word_band?: Record<string, unknown> | null;
  /** 项目写作圣经：跨章节长期有效的作者硬性约束（题材铁律 / 禁写项 / 称谓与视角约定等）。省略/None=无圣经；空白串按无圣经处理（落 NULL）。装配时与运行期 author_intent 按「基线 + 增量」拼接，见模型模块 docstring。 */
  writing_bible?: string | null;
  /** 快通道：写完直接提交，跳过人工审批门（迁移 0031 的可空三态列）。True=开闸门旁路、False=显式关、省略/None=未设置（走默认关）。只影响闸门是否阻断/挂起，**不影响** observer 等状态更新。 */
  fast_mode?: boolean | null;
}

export interface ProjectEffectiveSettings {
  /** 快通道有效值（后端权威解析结果：项目列 > env > 默认关）。 */
  fast_mode_effective: boolean;
  /** 有效值来源：project=项目列有值 / env=项目列为 NULL 且回落环境变量 / off=项目列为 NULL 且 env 未设置（默认关）。 */
  fast_mode_source: "project" | "env" | "off";
}

export interface ProjectInitRequest {
  /** project-init 触发请求体。 */
  brief: Record<string, unknown>;
  /** project-init 触发请求体。 */
  project_id?: string | null;
  /** project-init 触发请求体。 */
  chapter_seed_count?: number | null;
  /** project-init 触发请求体。 */
  mock_providers?: Record<string, unknown> | null;
  /** project-init 触发请求体。 */
  step_mode?: boolean | null;
  /** project-init 触发请求体。 */
  selected_stages?: Array<string> | null;
  /** project-init 触发请求体。 */
  model_profile_id?: string | null;
}

export interface ProjectInitStage {
  /** premise / world / character / outline */
  stage: string;
  /** 人类可读名（题材定位 / 世界观 / 核心角色 / 卷纲与章节种子） */
  label: string;
  /** 由 DB 现有数据推导，不建新表 */
  done: boolean;
  /** 人类可读说明；未生成为「未生成」 */
  detail: string;
}

export interface ProjectInitStarted {
  /** ``POST /api/projects/init`` 的 201 响应。 */
  run_id: string;
  /** RUNNING / PAUSED / COMPLETED / FAILED；同步启动，非恒 RUNNING */
  status: string;
  /** ``POST /api/projects/init`` 的 201 响应。 */
  current_node: string | null;
  /** step_mode 暂停时 persist 节点未跑 ⇒ 可为 null */
  project_id: string | null;
  /** 仅 status=PAUSED 时出现；形状随 workflow 而变，不逐字段建模 */
  pause_payload?: Record<string, unknown> | null;
}

export interface ProjectInitStatus {
  /** 恒 4 行，顺序即 premise→world→character→outline */
  stages: Array<ProjectInitStage>;
  /** 四环节 done 的或；用于 UI 决定是否提示先跑 project-init */
  has_any_data: boolean;
}

export interface ProjectUpdate {
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  name?: string | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  premise?: string | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  genre?: string | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  target_words?: number | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  status?: "ACTIVE" | "PAUSED" | "ARCHIVED" | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  foreshadow_overdue_chapters?: number | null;
  /** 字数带覆盖（low_ratio / high_ratio / floor 三键可任选）；None=清除覆盖；省略=保留原值。校验在 router 层走 resolve_band_config。 */
  word_band?: Record<string, unknown> | null;
  /** 项目写作圣经。显式 null / 空白串 → 清除（落 NULL）；省略 → 保留原值。装配优先级见模型模块 docstring（基线 + 增量拼接）。 */
  writing_bible?: string | null;
  /** 快通道开关。true=开 / false=显式关 / 显式 null=清除覆盖（落 NULL，回到「未设置」）；省略=保留原值。 */
  fast_mode?: boolean | null;
}

export interface ProviderErrorDetail {
  /** 固定 'provider_error' */
  error: string;
  /** ``HTTPException(502, detail={...})`` 的 ``detail`` 部分。 */
  message: string;
  /** 上游 HTTP 码；provider 未带上来时为 null */
  status_code: number | null;
}

export interface ProviderErrorEnvelope {
  /** FastAPI 把 ``HTTPException(detail=...)`` 包成 ``{"detail": ...}``。 */
  detail: ProviderErrorDetail;
}

export interface QualityJudgeRecord {
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  pacing: number;
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  style: number;
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  logic: number;
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  dialogue: number;
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  verdict?: string | null;
  /** 落库到 ``quality_reports.judge_json`` 的一格人工评分。 */
  top_issues?: Array<string> | null;
}

export interface QualityJudgeResponse {
  /** ``POST /chapters/{cid}/quality/judge`` 的响应。 */
  report_id: string;
  /** ``POST /chapters/{cid}/quality/judge`` 的响应。 */
  chapter_id: string;
  /** ``POST /chapters/{cid}/quality/judge`` 的响应。 */
  judge: QualityJudgeRecord | null;
}

export interface QualityReportIssue {
  /** `issues_json[]` 的一格（**出参**形状；与内部 :class:`Issue` 字段同名同义）。 */
  severity: "error" | "warning" | "info";
  /** `issues_json[]` 的一格（**出参**形状；与内部 :class:`Issue` 字段同名同义）。 */
  category: "schema_validity" | "timeline_consistency" | "character_contradiction" | "world_rule_contradiction" | "knowledge_leakage" | "plot" | "character" | "continuity" | "style" | "pacing" | "foreshadowing" | "ai_trace" | "payoff" | "compliance";
  /** `issues_json[]` 的一格（**出参**形状；与内部 :class:`Issue` 字段同名同义）。 */
  location?: string;
  /** `issues_json[]` 的一格（**出参**形状；与内部 :class:`Issue` 字段同名同义）。 */
  rule_id: string;
  /** `issues_json[]` 的一格（**出参**形状；与内部 :class:`Issue` 字段同名同义）。 */
  message: string;
  /** `issues_json[]` 的一格（**出参**形状；与内部 :class:`Issue` 字段同名同义）。 */
  gate?: "auto" | "confirm" | "block";
  /** `issues_json[]` 的一格（**出参**形状；与内部 :class:`Issue` 字段同名同义）。 */
  suggestion?: string | null;
  /** `issues_json[]` 的一格（**出参**形状；与内部 :class:`Issue` 字段同名同义）。 */
  evidence_refs?: Array<string> | null;
  /** `issues_json[]` 的一格（**出参**形状；与内部 :class:`Issue` 字段同名同义）。 */
  judge_trace?: Record<string, unknown> | null;
}

export interface QualityReportResponse {
  /** ``GET /chapters/{cid}/quality`` 与 ``.../quality/evaluate`` 的出参。 */
  report_id: string;
  /** ``GET /chapters/{cid}/quality`` 与 ``.../quality/evaluate`` 的出参。 */
  project_id: string;
  /** ``GET /chapters/{cid}/quality`` 与 ``.../quality/evaluate`` 的出参。 */
  chapter_id: string;
  /** ``GET /chapters/{cid}/quality`` 与 ``.../quality/evaluate`` 的出参。 */
  commit_id?: string | null;
  /** ``GET /chapters/{cid}/quality`` 与 ``.../quality/evaluate`` 的出参。 */
  run_id?: string | null;
  /** ``GET /chapters/{cid}/quality`` 与 ``.../quality/evaluate`` 的出参。 */
  overall: number;
  /** ``GET /chapters/{cid}/quality`` 与 ``.../quality/evaluate`` 的出参。 */
  scores_json?: Record<string, unknown>;
  /** ``GET /chapters/{cid}/quality`` 与 ``.../quality/evaluate`` 的出参。 */
  issues_json?: Array<QualityReportIssue>;
  /** ``GET /chapters/{cid}/quality`` 与 ``.../quality/evaluate`` 的出参。 */
  draft_version?: number | null;
  /** ``GET /chapters/{cid}/quality`` 与 ``.../quality/evaluate`` 的出参。 */
  created_at: string;
  /** ``GET /chapters/{cid}/quality`` 与 ``.../quality/evaluate`` 的出参。 */
  judge: QualityJudgeRecord | null;
}

export interface RelationshipOut {
  /** ``GET /api/projects/{pid}/relationships`` 的响应行。 */
  id: string;
  /** ``GET /api/projects/{pid}/relationships`` 的响应行。 */
  project_id: string;
  /** ``GET /api/projects/{pid}/relationships`` 的响应行。 */
  from_character_id: string;
  /** ``GET /api/projects/{pid}/relationships`` 的响应行。 */
  to_character_id: string;
  /** ``GET /api/projects/{pid}/relationships`` 的响应行。 */
  relation_type: string;
  /** 已从 state_json 解码，形如 {intensity, since_chapter} */
  state: Record<string, unknown>;
  /** DDL NOT NULL；快照版本号 */
  last_state_version: number;
  /** 迁移 0014 伴随列，默认 'PUBLIC'（与 plot_events 的 'RESTRICTED' 不同） */
  visibility: string;
  /** 知情者 character_id 列表；NULL=沿用默认，[]=显式置空 */
  who_knows: Array<string> | null;
}

export interface ResumeRequest {
  human_input?: Record<string, unknown> | null;
  auto_revise_max?: number | null;
  mock_providers?: Record<string, unknown> | null;
  regenerate?: boolean | null;
  model_overrides?: Record<string, unknown> | null;
  author_intent?: string | null;
  target_word_count?: number | null;
}

export interface RevealPolicyCreateIn {
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  target_kind: "character" | "location" | "faction" | "world_rule" | "event" | "hook" | "debt" | "relationship";
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  target_id: string;
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  reveal_by_chapter?: number | null;
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  audience?: string;
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  status?: "planned" | "revealed" | "cancelled";
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  revealed_chapter?: number | null;
  /** ``POST /projects/{pid}/reveal-policies`` 的入参。 */
  notes?: string | null;
}

export interface RevealPolicyOut {
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  policy_id: string;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  project_id: string;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  target_kind: "character" | "location" | "faction" | "world_rule" | "event" | "hook" | "debt" | "relationship";
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  target_id: string;
  /** 计划在第几章前揭开；null = 未定 */
  reveal_by_chapter: number | null;
  /** `reader` / `character:<id>` / 逗号混写；DDL default 为 'reader' */
  audience: string;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  status: "planned" | "revealed" | "cancelled";
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  revealed_chapter: number | null;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  notes: string | null;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  created_at: string;
  /** ``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。 */
  updated_at: string;
}

export interface RevealPolicyUpdateIn {
  /** ``PATCH /projects/{pid}/reveal-policies/{policy_id}`` 的入参。 */
  status?: "planned" | "revealed" | "cancelled" | null;
  /** ``PATCH /projects/{pid}/reveal-policies/{policy_id}`` 的入参。 */
  revealed_chapter?: number | null;
  /** ``PATCH /projects/{pid}/reveal-policies/{policy_id}`` 的入参。 */
  notes?: string | null;
}

export interface RevisionNoteUpdate {
  /** 写入或清除改稿意见的请求体。 */
  note: string;
}

export interface RollbackResponse {
  /** ``POST /commits/{cid}/rollback`` 的 201 响应（实测 5 键 = commit + 1）。 */
  commit_id: string;
  /** ``POST /commits/{cid}/rollback`` 的 201 响应（实测 5 键 = commit + 1）。 */
  delta_id: string;
  /** ``POST /commits/{cid}/rollback`` 的 201 响应（实测 5 键 = commit + 1）。 */
  state_version: number;
  /** main commit 才有；分支 commit 恒为 null（键本身恒在） */
  snapshot_ref?: string | null;
  /** 被回滚的 commit_id（= 本次 commit 的来源） */
  rollback_of: string;
}

export interface RunCancelled {
  /** ``POST /runs/{run_id}/cancel`` 的 200 响应。 */
  run_id: string;
  /** 成功恒为 CANCELLED；409 冲突体走 FastAPI 的 {detail} */
  status: string;
}

export interface SigningCheckItem {
  /** 单条体检项（``signing_check.checks.CheckItem`` 的 dict 投影，恒 4 键）。 */
  key: string;
  /** 单条体检项（``signing_check.checks.CheckItem`` 的 dict 投影，恒 4 键）。 */
  level: string;
  /** 单条体检项（``signing_check.checks.CheckItem`` 的 dict 投影，恒 4 键）。 */
  detail: string;
  /** 单条体检项（``signing_check.checks.CheckItem`` 的 dict 投影，恒 4 键）。 */
  advice: string;
}

export interface SigningCheckResult {
  /** ``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。 */
  project_id: string;
  /** ``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。 */
  project_name: string;
  /** ``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。 */
  total_chars: number;
  /** ``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。 */
  generated_at: string;
  /** ``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。 */
  items: Array<SigningCheckItem>;
  /** ``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。 */
  summary: SigningCheckSummary;
  /** ``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。 */
  word_band_source: string;
  /** ``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。 */
  word_band_rejected?: boolean | null;
  /** ``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。 */
  genre_opening?: GenreOpeningSection | null;
  /** ``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。 */
  genre_opening_unavailable?: boolean | null;
  /** ``run_signing_check`` 的返回结构（恒 7 键 + 4 个条件键）。 */
  genre_opening_error?: string | null;
}

export interface SigningCheckSummary {
  /** 四档计数（``level`` 分桶；恒 4 键）。 */
  pass_count: number;
  /** 四档计数（``level`` 分桶；恒 4 键）。 */
  warn_count: number;
  /** 四档计数（``level`` 分桶；恒 4 键）。 */
  fail_count: number;
  /** 四档计数（``level`` 分桶；恒 4 键）。 */
  info_count: number;
}

export interface SimulationIssue {
  /** 被拒的 delta（``issues[index]``）。 */
  index: number;
  /** 被拒的 delta（``issues[index]``）。 */
  errors: Array<string>;
}

export interface SimulationResult {
  /** 与 branch_id 同值 */
  simulation_id: string;
  /** ``POST /simulate`` 与 ``GET /simulations/{id}`` 的响应。 */
  branch_id: string;
  /** ⚠️ **必须以 `sim-` 开头**，否则该次推演在 `GET /simulations` 与 `GET /simulations/{id}` 里**查不到**（两者都按 `name LIKE 'sim-%'` 过滤，实测自定义名会 404） */
  name: string;
  /** ``POST /simulate`` 与 ``GET /simulations/{id}`` 的响应。 */
  base_version: number;
  /** 已成功应用的 delta 条数；失败时是部分应用数 */
  applied: number;
  /** ``POST /simulate`` 与 ``GET /simulations/{id}`` 的响应。 */
  diff: StateDiff;
  /** ``POST /simulate`` 与 ``GET /simulations/{id}`` 的响应。 */
  issues: Array<SimulationIssue>;
  /** 恒为 ARCHIVED（推演结束即归档） */
  branch_status: string;
  /** ``POST /simulate`` 与 ``GET /simulations/{id}`` 的响应。 */
  created_at: string;
}

export interface SimulationSummary {
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  simulation_id: string;
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  branch_id: string;
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  name: string;
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  base_version: number;
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  status: string;
  /** ``GET /simulations`` 的一行（按 created_at DESC）。 */
  created_at: string;
}

export interface Skill {
  /** skill 完整表示（GET /api/skills/{skill_id}）。``payload`` 为解析后的 dict。 */
  skill_id: string;
  /** skill 完整表示（GET /api/skills/{skill_id}）。``payload`` 为解析后的 dict。 */
  name: string;
  /** skill 完整表示（GET /api/skills/{skill_id}）。``payload`` 为解析后的 dict。 */
  description?: string | null;
  /** skill 完整表示（GET /api/skills/{skill_id}）。``payload`` 为解析后的 dict。 */
  version: number;
  /** 运行面 payload（schema：docs/state-model/schemas/skill.schema.json）。⚠️ references 不是渐进披露——模型无 tool use，装配期全量注入。 */
  payload?: Record<string, unknown>;
  /** skill 完整表示（GET /api/skills/{skill_id}）。``payload`` 为解析后的 dict。 */
  source_path?: string | null;
  /** skill 完整表示（GET /api/skills/{skill_id}）。``payload`` 为解析后的 dict。 */
  created_at: string;
  /** skill 完整表示（GET /api/skills/{skill_id}）。``payload`` 为解析后的 dict。 */
  updated_at: string;
  /** skill 完整表示（GET /api/skills/{skill_id}）。``payload`` 为解析后的 dict。 */
  bound_project_count?: number;
}

export interface SkillBindRequest {
  /** 绑定请求体：``{"skill_ids": ["skl_a", "skl_b"]}``。 */
  skill_ids?: Array<string>;
}

export interface SkillCreate {
  /** 创建 skill 请求体（POST /api/skills）。 */
  name: string;
  /** 创建 skill 请求体（POST /api/skills）。 */
  description?: string | null;
  /** 创建 skill 请求体（POST /api/skills）。 */
  payload?: Record<string, unknown>;
  /** 显式 skill_id（内容仓 slug）；省略则生成 skl_<12hex>。 */
  skill_id?: string | null;
  /** 内容仓来源路径（审计用；软件层不解析）。 */
  source_path?: string | null;
}

export interface SkillSummary {
  /** skill 摘要投影（列表端点 / 绑定集合）。 */
  skill_id: string;
  /** skill 摘要投影（列表端点 / 绑定集合）。 */
  name: string;
  /** skill 摘要投影（列表端点 / 绑定集合）。 */
  description?: string | null;
  /** skill 摘要投影（列表端点 / 绑定集合）。 */
  version: number;
  /** skill 摘要投影（列表端点 / 绑定集合）。 */
  source_path?: string | null;
  /** skill 摘要投影（列表端点 / 绑定集合）。 */
  created_at: string;
  /** skill 摘要投影（列表端点 / 绑定集合）。 */
  updated_at: string;
  /** skill 摘要投影（列表端点 / 绑定集合）。 */
  applies_to?: Array<string>;
  /** skill 摘要投影（列表端点 / 绑定集合）。 */
  reference_count?: number;
  /** skill 摘要投影（列表端点 / 绑定集合）。 */
  check_count?: number;
  /** skill 摘要投影（列表端点 / 绑定集合）。 */
  bound_project_count?: number;
}

export interface SkillUnbindRequest {
  /** 解绑请求体：``{"skill_id": "skl_a"}``（幂等：未绑定也返回 200）。 */
  skill_id: string;
}

export interface SkillUpdate {
  /** 更新 skill 请求体（PUT /api/skills/{skill_id}）；提供 payload 时 version 自增。 */
  name?: string | null;
  /** 更新 skill 请求体（PUT /api/skills/{skill_id}）；提供 payload 时 version 自增。 */
  description?: string | null;
  /** 更新 skill 请求体（PUT /api/skills/{skill_id}）；提供 payload 时 version 自增。 */
  payload?: Record<string, unknown> | null;
  /** 更新 skill 请求体（PUT /api/skills/{skill_id}）；提供 payload 时 version 自增。 */
  source_path?: string | null;
}

export interface SnapshotResponse {
  /** 快照版本号；无快照时后端返回 0（genesis 未初始化前的形态） */
  state_version: number;
  /** 角色列表（按 character_id ASC） */
  characters?: Array<Record<string, unknown>>;
  /** 世界观：{locations: dict, factions: dict, world_rules: list} */
  world?: Record<string, unknown>;
  /** 伏笔列表（按 hook_id ASC） */
  hooks?: Array<Record<string, unknown>>;
  /** 叙事债务列表（按 debt_id ASC） */
  debts?: Array<Record<string, unknown>>;
  /** 最近事件（时间线视角） */
  recent_events?: Array<unknown>;
  /** 剧情事件，**按 event_id 键的字典**（不是列表） */
  events?: Record<string, unknown>;
}

export interface StartWorkflowRequest {
  author_intent?: string | null;
  expected_role?: string | null;
  target_word_count?: number | null;
  mock_providers?: Record<string, unknown> | null;
  quality_gate_mode?: string | null;
  critic_mode?: string | null;
  model_overrides?: Record<string, unknown> | null;
  fresh_write?: boolean | null;
  draft_version?: number | null;
  deep_review?: boolean | null;
  gate_override?: Record<string, unknown> | null;
  fast_mode?: boolean | null;
  auto_commit?: boolean | null;
}

export interface StateDeltaListItem {
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  delta_id: string;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  chapter_id: string;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  workflow_run_id: string;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  previous_state_version: number;
  /** schema 里 const=1；保留 int 而非 Literal 以免过紧 */
  delta_version: number;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  schema_version: string;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  created_by: string;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  created_at: string;
  /** 重试时指向被取代的 delta_id；普通 delta 为 null */
  supersedes: string | null;
  /** 恒为 null（restore_delta_from_row 写死） */
  notes: string | null;
  /** proposed / validated / applied / rejected / superseded */
  status: string;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  character_changes: Array<Record<string, unknown>>;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  world_changes: Array<Record<string, unknown>>;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  relationship_changes: Array<Record<string, unknown>>;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  new_events: Array<Record<string, unknown>>;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  resolved_hooks: Array<Record<string, unknown>>;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  new_hooks: Array<Record<string, unknown>>;
  /** ``GET /projects/{pid}/chapters/{cid}/deltas`` 的**行**（实测恒 18 键）。 */
  debt_changes: Array<Record<string, unknown>>;
}

export interface StateDiff {
  /** 两个快照的结构化差异。 */
  version_a: number;
  /** 两个快照的结构化差异。 */
  version_b: number;
  /** 两个快照的结构化差异。 */
  branch_id?: string | null;
  /** 两个快照的结构化差异。 */
  characters?: ListDiff | null;
  /** 两个快照的结构化差异。 */
  world?: Record<string, unknown> | null;
  /** 两个快照的结构化差异。 */
  hooks?: ListDiff | null;
  /** 两个快照的结构化差异。 */
  debts?: ListDiff | null;
  /** 两个快照的结构化差异。 */
  events_changed?: Record<string, unknown> | null;
  /** 两个快照的结构化差异。 */
  recent_events?: ListDiff | null;
}

export interface StateDiffResponse {
  /** ``GET /projects/{pid}/state/diff?a=&b=`` 的 200 响应。 */
  version_a: number;
  /** ``GET /projects/{pid}/state/diff?a=&b=`` 的 200 响应。 */
  version_b: number;
  /** 恒为 null——本端点不支持分支视角 */
  branch_id?: string | null;
  /** ``GET /projects/{pid}/state/diff?a=&b=`` 的 200 响应。 */
  characters?: ListDiffResponse | null;
  /** ``GET /projects/{pid}/state/diff?a=&b=`` 的 200 响应。 */
  world?: Record<string, unknown> | null;
  /** ``GET /projects/{pid}/state/diff?a=&b=`` 的 200 响应。 */
  hooks?: ListDiffResponse | null;
  /** ``GET /projects/{pid}/state/diff?a=&b=`` 的 200 响应。 */
  debts?: ListDiffResponse | null;
  /** ``GET /projects/{pid}/state/diff?a=&b=`` 的 200 响应。 */
  events_changed?: Record<string, unknown> | null;
  /** ``GET /projects/{pid}/state/diff?a=&b=`` 的 200 响应。 */
  recent_events?: ListDiffResponse | null;
}

export interface StyleSample {
  /** 文风样例行（``author_style_samples`` 表全列，**恒 6 键、无条件键**）。 */
  sample_id: string;
  /** 文风样例行（``author_style_samples`` 表全列，**恒 6 键、无条件键**）。 */
  project_id: string;
  /** 文风样例行（``author_style_samples`` 表全列，**恒 6 键、无条件键**）。 */
  title: string;
  /** 文风样例行（``author_style_samples`` 表全列，**恒 6 键、无条件键**）。 */
  content: string;
  /** 文风样例行（``author_style_samples`` 表全列，**恒 6 键、无条件键**）。 */
  created_at: string;
  /** 文风样例行（``author_style_samples`` 表全列，**恒 6 键、无条件键**）。 */
  updated_at: string;
}

export interface StyleSampleCreate {
  /** POST 请求体。 */
  title: string;
  /** POST 请求体。 */
  content: string;
}

export interface StyleSampleOut {
  /** ``POST /projects/{pid}/canons/{cid}/to-style-sample`` 的出参。 */
  sample_id: string;
  /** ``POST /projects/{pid}/canons/{cid}/to-style-sample`` 的出参。 */
  project_id: string;
  /** ``POST /projects/{pid}/canons/{cid}/to-style-sample`` 的出参。 */
  title: string;
  /** ``POST /projects/{pid}/canons/{cid}/to-style-sample`` 的出参。 */
  content: string;
  /** ``POST /projects/{pid}/canons/{cid}/to-style-sample`` 的出参。 */
  created_at: string;
  /** ``POST /projects/{pid}/canons/{cid}/to-style-sample`` 的出参。 */
  updated_at: string;
}

export interface SubmitDeltaResponse {
  /** ``POST /projects/{pid}/deltas`` 的 201 响应（实测 3 键恒在）。 */
  delta_id: string;
  /** ``POST /projects/{pid}/deltas`` 的 201 响应（实测 3 键恒在）。 */
  status: string;
  /** 恒在；无错时是 []（不是缺键） */
  errors: Array<string>;
}

export interface TimelineEventOut {
  /** ``POST /api/projects/{pid}/timeline`` 的响应（``GET .../timeline`` 同款）。 */
  id: string;
  /** ``POST /api/projects/{pid}/timeline`` 的响应（``GET .../timeline`` 同款）。 */
  project_id: string;
  /** ``POST /api/projects/{pid}/timeline`` 的响应（``GET .../timeline`` 同款）。 */
  event_id: string;
  /** 故事内第几天；DDL NOT NULL，恒在 */
  day_index: number;
  /** 可选 HH:MM 等 */
  time_ref: string | null;
  /** ``POST /api/projects/{pid}/timeline`` 的响应（``GET .../timeline`` 同款）。 */
  description: string | null;
  /** 迁移 0014 伴随列，默认 'PUBLIC'（与 plot_events 的 'RESTRICTED' 不同） */
  visibility: string;
  /** ``POST /api/projects/{pid}/timeline`` 的响应（``GET .../timeline`` 同款）。 */
  who_knows: Array<string> | null;
}

export interface ValidationError {
  loc: Array<string | number>;
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

export interface Volume {
  /** 卷完整表示，对应数据库行。 */
  volume_id: string;
  /** 卷完整表示，对应数据库行。 */
  project_id: string;
  /** 卷完整表示，对应数据库行。 */
  number: number;
  /** 卷完整表示，对应数据库行。 */
  title: string | null;
  /** 卷完整表示，对应数据库行。 */
  arc_summary: string | null;
  /** 卷完整表示，对应数据库行。 */
  status: "active" | "sealed";
  /** 卷完整表示，对应数据库行。 */
  terminal_snapshot_json: Record<string, unknown> | null;
  /** 卷完整表示，对应数据库行。 */
  created_at: string;
  /** 卷完整表示，对应数据库行。 */
  updated_at: string;
}

export interface VolumeAssignRequest {
  /** 挂章请求体。 */
  chapter_id: string;
}

export interface VolumeCreate {
  /** 创建卷请求体。 */
  number: number;
  /** 创建卷请求体。 */
  title?: string | null;
  /** 创建卷请求体。 */
  arc_summary?: string | null;
}

export interface VolumeListItem {
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  volume_id: string;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  project_id: string;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  number: number;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  title: string | null;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  arc_summary: string | null;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  status: "active" | "sealed";
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  terminal_snapshot_json: Record<string, unknown> | null;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  chapter_count: number;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  created_at: string;
  /** 卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。 */
  updated_at: string;
}

export interface VolumeUpdate {
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  title?: string | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  status?: "active" | "sealed" | null;
  /** 部分更新请求体——所有字段均可选，未提供则不修改。 */
  arc_summary?: string | null;
}

export interface WorkflowRunDetail {
  /** ``GET /runs/{run_id}`` 的 200 响应。 */
  run_id: string;
  /** workflows 表主键；人类可读名见 workflow_name */
  workflow_id: string;
  /** 反查 workflows.name；孤儿 run 为 null */
  workflow_name: string | null;
  /** project-init 等非章节任务的 run 为 null */
  chapter_id: string | null;
  /** PENDING / RUNNING / PAUSED / COMPLETED / FAILED / CANCELLED */
  status: string;
  /** ``GET /runs/{run_id}`` 的 200 响应。 */
  current_node: string | null;
  /** 暂停/恢复持久化点；键集随 workflow 演进，不建模 */
  checkpoint_json: Record<string, unknown>;
  /** 失败原因；成功 run 为 null */
  error: string | null;
  /** ``GET /runs/{run_id}`` 的 200 响应。 */
  retry_count: number;
  /** ``GET /runs/{run_id}`` 的 200 响应。 */
  started_at: string;
  /** 未到终态为 null */
  ended_at: string | null;
  /** 0026 新增的实例身份列；未设置时为 null */
  instance_id: string | null;
  /** 按 (started_at, node_run_id) 升序；无节点时为 [] */
  nodes: Array<WorkflowRunNode>;
  /** 仅 PAUSED 时出现；各 workflow 的挂起节点不同 ⇒ 形状不固定 */
  pause_payload?: Record<string, unknown> | null;
  /** 仅 PAUSED 时出现；{agent 名: 本次实际调用的 model_id}，无成功调用时为 {} */
  stage_models?: Record<string, unknown> | null;
}

export interface WorkflowRunNode {
  /** ``workflow_run_nodes`` 的一行（run 详情里的 ``nodes[]``）。 */
  node_run_id: string;
  /** ``workflow_run_nodes`` 的一行（run 详情里的 ``nodes[]``）。 */
  run_id: string;
  /** workflow 定义里的节点 id（不是 agent 名） */
  node_id: string;
  /** 纯逻辑节点无 agent ⇒ null */
  agent_id: string | null;
  /** PENDING / RUNNING / COMPLETED / FAILED / SKIPPED */
  status: string;
  /** 节点输入；DB 列 NOT NULL DEFAULT '{}'，故恒为对象 */
  input_json: Record<string, unknown>;
  /** 非结构化输出（SKIPPED / 尚未跑完）时为 null */
  output_json: Record<string, unknown> | null;
  /** 未调 AI 的节点为 null */
  prompt_version: string | null;
  /** 未调 AI 的节点为 null */
  model_id: string | null;
  /** {prompt, completion, total}；未调 AI 为 null */
  token_usage_json: Record<string, unknown> | null;
  /** ``workflow_run_nodes`` 的一行（run 详情里的 ``nodes[]``）。 */
  latency_ms: number | null;
  /** ``workflow_run_nodes`` 的一行（run 详情里的 ``nodes[]``）。 */
  error: string | null;
  /** PENDING（尚未开始）为 null */
  started_at: string | null;
  /** 未到终态为 null */
  ended_at: string | null;
}

export interface WorkflowRunSummary {
  /** ``GET /projects/{pid}/runs`` 的**行**（实测恒 13 键）。 */
  run_id: string;
  /** ``GET /projects/{pid}/runs`` 的**行**（实测恒 13 键）。 */
  workflow_id: string;
  /** JOIN workflows 反查；孤儿 run 为 null */
  workflow_name: string | null;
  /** 列表行专属：project-init 等非章节任务的 run 为 null（实测） */
  chapter_id: string | null;
  /** PENDING / RUNNING / PAUSED / COMPLETED / FAILED / CANCELLED */
  status: string;
  /** ``GET /projects/{pid}/runs`` 的**行**（实测恒 13 键）。 */
  current_node: string | null;
  /** 键集随 workflow 演进，不逐键建模 */
  checkpoint_json: Record<string, unknown>;
  /** 失败原因；成功 run 键在、值为 null */
  error: string | null;
  /** ``GET /projects/{pid}/runs`` 的**行**（实测恒 13 键）。 */
  retry_count: number;
  /** ``GET /projects/{pid}/runs`` 的**行**（实测恒 13 键）。 */
  started_at: string;
  /** 未到终态时键在、值为 null */
  ended_at: string | null;
  /** 0026 新增的实例身份列 */
  instance_id: string | null;
  /** 人类可读标签；workflow 名不在中文映射表里时为 null */
  label: string | null;
}

export interface WorkflowStartAccepted {
  /** 新建 run 的主键；真终态需轮询 GET /api/runs/{run_id} */
  run_id: string;
  /** 启动后恒为 RUNNING（同步化的 project-init 除外） */
  status: string;
  /** 恒为 null：异步启动返回时还没有节点在跑；给前端留位置 */
  current_node: string | null;
}

export interface WorldEntityOut {
  /** ``locations`` / ``factions`` / ``world_rules`` 三类实体的**出参**形态。 */
  id: string;
  /** ``locations`` / ``factions`` / ``world_rules`` 三类实体的**出参**形态。 */
  project_id: string;
  /** ``locations`` / ``factions`` / ``world_rules`` 三类实体的**出参**形态。 */
  name: string;
  /** 一句话陈述；未填时为空串（非 null） */
  statement: string;
  /** data_json 列的反序列化结果；未填时为 {}，也可能是数组 */
  data: Record<string, unknown> | Array<unknown>;
  /** 权限级别 */
  visibility: "PUBLIC" | "VISIBLE" | "RESTRICTED" | "HIDDEN";
  /** 可见者 ID 列表；null = 全员可见 */
  who_knows: Array<string> | null;
  /** 别名列表；恒为数组（world_rules 恒为 []，见模块注释） */
  aliases: Array<string>;
  /** 触发注入模式（world_rules 恒为 'auto'，见模块注释） */
  inject_mode: "auto" | "always" | "never";
  /** ``locations`` / ``factions`` / ``world_rules`` 三类实体的**出参**形态。 */
  created_at: string;
  /** ``locations`` / ``factions`` / ``world_rules`` 三类实体的**出参**形态。 */
  updated_at: string;
}
