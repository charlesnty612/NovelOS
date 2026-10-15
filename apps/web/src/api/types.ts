// 前后端契约类型（Sprint 5 一期对齐 packages/core/api 与 packages/domain）。
// 字段命名直接与后端 Pydantic 模型对齐。

export type ProjectStatus = 'ACTIVE' | 'PAUSED' | 'ARCHIVED';

// V3.7：项目级字数带覆盖（projects.word_band_json 反序列化）。
// 三键全可选；后端 resolve_band_config 校验合法性，未知键忽略。
// 无覆盖项目（DB NULL）→ 后端响应里 word_band = undefined，UI 按空态处理。
export interface ProjectWordBand {
  low_ratio?: number;
  high_ratio?: number;
  floor?: number;
}

export interface Project {
  project_id: string;
  name: string;
  premise: string | null;
  genre: string | null;
  target_words: number | null;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
  /** V3.7：项目级字数带覆盖；undefined = 项目无覆盖（走后端默认 0.85/1.15/1200） */
  word_band?: ProjectWordBand | null;
  /**
   * 快通道（后端迁移 0031 projects.fast_mode）：写完直接提交，跳过人工审批门。
   * - null/undefined = 未设置（后端回落 env 与默认「关」）
   * - true = 开、false = 作者显式关
   *
   * 只影响闸门是否阻断/挂起，**不影响** observer 等状态更新
   * （后端 projects.fast_mode 语义，前端不得自行解释成「跳过状态更新」）。
   */
  fast_mode?: boolean | null;
  /**
   * 项目写作圣经（后端迁移 0029 projects.writing_bible）：**跨章节长期有效的作者硬性约束**
   * （题材铁律 / 禁写项 / 称谓与视角约定…）。
   *
   * 与运行期 `author_intent` 是**基线 + 增量拼接**关系，不是覆盖
   * （后端 `context_engine.builders_common.common.resolve_author_intent`）：
   * 本列是基线，单次运行时补的要求是增量，冲突时**以增量段为准**（拼接模板的 header 明写）。
   * writer / director 的装配缓存键各带 `bible_fp` 维度 ⇒ 改本列后下一次运行必取新装配，
   * 不存在脏命中。
   *
   * 前端曾**完全没有编辑入口**（仅 types.generated.ts 有该字段）——与 2026-09-16
   * `author_intent` 死参数事故同款形状：后端有、装配在用、作者够不着。
   */
  writing_bible?: string | null;
}

export interface ProjectCreatePayload {
  name: string;
  premise?: string | null;
  genre?: string | null;
  target_words?: number | null;
  /**
   * V3.7：项目级字数带覆盖。
   * - 字段未提供（undefined） → 后端不写入 word_band_json（与 PATCH 缺省行为对齐）
   * - 显式 null → 显式清除（落 DB NULL），与 PATCH 同口径
   * - 显式对象 → 后端 resolve_band_config 校验 + 序列化存 word_band_json
   */
  word_band?: ProjectWordBand | null;
  /** 快通道：undefined = 不写入；显式 true/false = 作者显式选择 */
  fast_mode?: boolean | null;
  /**
   * 项目写作圣经（迁移 0029）：
   * - 字段未提供（undefined） → 后端保留原值
   * - 显式 null 或空白串 → 清除圣经（落 DB NULL）
   * - 非空串 → 去首尾空白后原样存
   */
  writing_bible?: string | null;
}

export interface ProjectUpdatePayload {
  name?: string | null;
  premise?: string | null;
  genre?: string | null;
  target_words?: number | null;
  status?: ProjectStatus;
  /**
   * V3.7：项目级字数带覆盖。
   * - 字段未提供（undefined） → 后端保留原值
   * - 显式 null → 清除覆盖（落 DB NULL）
   * - 显式对象 → 后端校验 + 序列化存 word_band_json
   */
  word_band?: ProjectWordBand | null;
  /**
   * 快通道（后端迁移 0031）：
   * - 字段未提供（undefined） → 后端保留原值
   * - 显式 null → 清除覆盖（落 DB NULL，回到「未设置」）
   * - 显式 true/false → 作者显式开/关
   */
  fast_mode?: boolean | null;
  /**
   * 项目写作圣经（迁移 0029）：见 `Project.writing_bible`。
   * 字段未提供（undefined）→ 后端保留原值；显式 null / 空白串 → 清除。
   */
  writing_bible?: string | null;
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
  /** 因果引用：后端 plot_events.cause 是 event_id 字符串数组（packages/domain/plot/models.py L40-41）。 */
  cause: string[] | null;
  effects: string[] | null;
  participants: unknown[];
  location_id: string | null;
  time: Record<string, unknown> | null;
  status: string;
  introduced_chapter_id: string | null;
  /** 事件描述全文（后端 commit 写入；UI 主展示列）。老 init 空壳行可能为 null/空串。 */
  description?: string | null;
  visibility: VisibilityLevel;
  who_knows: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface PlotEventCreatePayload {
  type: EventType;
  /** 与 PlotEvent.cause 对齐：event_id 字符串数组或 null。旧 Record 形态与后端契约不符。 */
  cause?: string[] | null;
  effects?: string[] | null;
  participants?: unknown[] | null;
  time?: Record<string, unknown> | null;
  status?: string;
  /** 事件描述全文（与 PlotEvent.description 对齐；后端 commit 必落字段）。 */
  description?: string | null;
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
  /** 迁移表数：现行后端返回数字；旧版返回 {表名: 行数} map（两种形状都兼容）。 */
  tables?: number | Record<string, number>;
}

/**
 * `GET /projects/{pid}/state` 与 `GET /projects/{pid}/state/versions/{n}` 的响应。
 *
 * **契约来源**：`packages/core/story_state/models.py::SnapshotResponse`
 * （该端点已声明 `response_model`，形状由后端保证）。本文件是手写权威面，
 * `types.generated.ts` 是同源的 drift 对照面（由 `scripts/gen_frontend_types.py` 生成）。
 *
 * ⚠️ **2026-10-15 重写**。此前这里是欠定义类型：六个字段全 optional、一个
 * `[key: string]: unknown` 索引签名，并列声明了 `version?` 与 `state_version?`
 * 两个**互斥**的版本命名（后端只写 `state_version`，`version` 是这份类型自己
 * 臆造的别名）。它逼出的那层手写归一化把 `characters`（list）与 `events`
 * （dict）拍反，导致概览页两个统计项**恒为 0** 且无告警——详见
 * `endpoints.ts::storyStateApi` 的注释。
 *
 * **不再有 `[key: string]: unknown`**：故事状态是开放 JSON 文档，delta 可带额外
 * 集合键，后端 `SnapshotResponse` 因此声明 `extra="allow"`；但索引签名会让
 * 拼错字段名静默通过，故这里只列已知的六个，多余键需显式 `as Record<string, unknown>` 读。
 */
export interface SnapshotResponse {
  /** 快照版本号；无快照时后端返回 0。**这是唯一的版本字段名**。 */
  state_version: number;
  /** 角色**列表**（不是对象）——按 character_id ASC。 */
  characters?: Array<Record<string, unknown>>;
  /** 世界观：{locations: dict, factions: dict, world_rules: list} */
  world?: Record<string, unknown>;
  /** 伏笔**列表**——按 hook_id ASC。 */
  hooks?: Array<Record<string, unknown>>;
  /** 叙事债务**列表**——按 debt_id ASC。 */
  debts?: Array<Record<string, unknown>>;
  /** 最近事件（时间线视角，列表）。 */
  recent_events?: Array<unknown>;
  /** 剧情事件，**按 event_id 键的字典**（不是列表）。 */
  events?: Record<string, unknown>;
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
  /**
   * 所属卷（迁移 0015 加的可空外键列）。未挂卷时为 `null`。
   *
   * ⚠️ 后端 `Chapter` 模型**没有声明**这一列，是靠 `model_config = extra="allow"`
   * + `SELECT *` 透出来的（2026-10-15 实测确认：assign 前 null、assign 后 `vol_…`）。
   * 一旦有人给该模型换成严格 `response_model`，它会**静默消失**——
   * 回归看守：`tests/api/test_volume_membership_contract.py`。
   */
  volume_id?: string | null;
  created_at: string;
  updated_at: string;
  /** 最近一次 COMPLETED 状态 chapter-review run 的 ended_at；
   *  用于版本列表渲染「未审」角标。无 COMPLETED review 时为 null（此时视为「该章节
   *  从未审过，全部 draft 都不带「未审」标记，避免在用户首次走流水线时被噪声覆盖）。 */
  last_review_completed_at?: string | null;
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
   * 后端 list_runs 提供的可读标签（如「写正文 → 草稿 v6」「审校（审 v5）」
   * 「生成计划」）；前端 runs 列表行优先展示 label。无 label 时退化为 run_id 短形式。
   * 后端字段命名可能为 label / title / display_name；契约方为 label。
   */
  label?: string | null;
  /**
   * 节点明细数组：单 run GET（`/runs/{id}`）返回；list（`/projects/{pid}/runs`）不返回。
   * UI 用 useApiCall 单独拉详情时填充。
   */
  nodes: WorkflowNodeRun[];
  /**
   * 工作流**名**（`chapter-review` / `chapter-write` / …），**不是** `workflow_id`。
   *
   * 由 router 层 `list_runs_endpoint` 逐行附加（`api/routers/workflows/runs.py`），
   * **不是** `workflow_runtime.runs.list_runs` 的 SQL 产出——下层只给不透明的
   * `wf_<hex>`，前端无从反查名字。⚠️ 本行注释此前写的是「后端暂未提供」，
   * 与事实相反，且据此先验被推翻过一次（误判「前端认不出 chapter-review run」，
   * 一度以为要改后端）；**判据以 router 层为准**。
   *
   * 缺席时按「判不出」处理，别默认当成非 review：G5 的 `findUnreviewedDraft`
   * 拿不到它就不拦（方向性安全，见该函数注释）。
   */
  workflow_name?: string | null;
  // P1.1 恢复挂起的初始化：单 run GET 在 PAUSED 时携带的 pause_payload；
  // 非 PAUSED / list 行可能缺省。前端 ProjectInitPanel 用其直接恢复 review 视图。
  pause_payload?: ProjectInitPausePayload | null;
  // P1.2 审阅卡「本次实际使用」：PAUSED 时携带，按 agent 名聚合 ai_call_logs
  // 最新一次成功调用的 model_id；非 PAUSED 缺省。前端卡片在下拉旁渲染一行小字。
  stage_models?: Record<string, string> | null;
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
  /**
   * 审校目标草稿版本（仅 chapter-review 生效）：缺省 ⇒ 审最新版；
   * 指定版本号 ⇒ 审该版。后端对不存在的版本会报错,前端不做校验。
   */
  draft_version?: number | null;
  /**
   * 深度二审开关（仅 chapter-review 生效）：true ⇒ 在常规审校完成后由 Kimi
   * 跑一次「设定 / 节拍 / 行为链」三层清单二审，pause_payload 中会带
   * deep_review_report；缺省 / false ⇒ 不跑、报告字段不存在或为 null。
   * 后端对不存在的版本会报错,前端不做校验；约 +1 分钟。
   */
  deep_review?: boolean | null;
  /**
   * 快通道自动衔接（仅 chapter-write 生效）：true ⇒ 写作 run 到达 COMPLETED 后
   * 由服务端守护线程自动起 chapter-commit run，省掉「点写正文 → 等 → 点提交」。
   * **仅当项目快通道已开启时才生效**——项目 fast_mode 为 null/false 时后端忽略本字段，
   * 不会自动提交（免得绕过一个作者没同意的 DRAFTED 直提）。
   */
  auto_commit?: boolean | null;
  /**
   * 快通道单次运行覆盖：显式 true/false 压过 projects.fast_mode 与 env。
   * 前端一般不传（走项目级开关）；留出是为了「这一章想守门」的一次性例外。
   */
  fast_mode?: boolean | null;
  /**
   * confirm 档规则的**显式接受声明**（后端 P0-1，仅 chapter-commit 的 quality_gate
   * 节点读取）：`{"rule_ids": [...], "reason": "..."}`。
   *
   * 语义：`enforce` 模式下判为 `confirm` 的 issue（如 RULE_STYLE_REPETITION_TRIGRAM）
   * 不许静默通过；**只有 `rule_ids` 覆盖本次命中的全部 confirm rule_id 且 `reason`
   * 非空**才放行。`block` 档不受此字段影响——硬停不可覆盖，带了也照样阻断。
   *
   * 覆盖判定是**集合包含**（后端 `gate._resolve_gate_override`）；部分覆盖照样阻断。
   * 本字段后端从 2026-09-18 就存在，前端直到 2026-10-15 才有入口——此前作者在
   * enforce 模式下被 confirm 命中即无路可走，只能改配置或改代码。
   */
  gate_override?: { rule_ids: string[]; reason: string } | null;
}

/**
 * `plan_json.gate_blocked` 标记的形状（后端 `chapter_commit/gate._persist_gate_block`）。
 * 三态，**决定 UI 该给哪条出路**：
 * - `block`        —— 有硬伤，只能改稿，**不接受**
 * - `confirm`      —— 无硬伤，只是要作者点头 ⇒ 提供「接受并提交」
 * - `block+confirm`—— 硬伤与待确认项同时命中 ⇒ 先修硬伤；此时给「接受」是假出路
 *   （硬停不可覆盖，作者点了仍会被拦），故 UI 只展示改稿按钮。
 */
export interface GateBlocked {
  mode: string;
  gate: 'block' | 'confirm' | 'block+confirm' | string;
  rule_ids: string[];
  blocked_at: string;
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

// ---- deep review（Kimi 三层清单二审） ------------------------------------
// 仅在 startReview 时传 deep_review=true 时由后端生成并写入 pause_payload。
// verdict='pass' 表示三层均无高/中风险问题；'revise' 表示存在需要改稿的问题。
// 后端在未开启 deep_review 时该字段为 null 或缺省；前端须容忍 None / 缺省。
export type DeepReviewLayer = 'setting' | 'beat' | 'behavior';
export type DeepReviewSeverity = 'high' | 'medium' | 'low';
export type DeepReviewVerdict = 'pass' | 'revise';

export interface DeepReviewIssue {
  layer: DeepReviewLayer;
  severity: DeepReviewSeverity;
  quote: string;
  suggestion: string;
}

export interface DeepReviewReport {
  verdict: DeepReviewVerdict;
  overall_comment: string;
  issues: DeepReviewIssue[];
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
  /** 深度二审报告（仅在 startReview 时传 deep_review=true 时存在）；未开启或
   *  失败时为 null / 缺省。UI 须容忍 null/缺省/坏形状（按需降级）。 */
  deep_review_report?: DeepReviewReport | null;
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
  /**
   * 人工评分（后端 `_attach_judge` 从 `quality_reports.judge_json` 提到顶层）。
   * ⚠️ 该字段此前**不在本类型里**，而 `GET .../quality` 一直在返回它——
   *   又一处「消费方有数据源但类型没跟上」的欠定义。null = 尚未人工打分。
   * ⚠️ 打分**不进任何门禁**（`chapter_commit/gate.py` 零引用），见 `qualityApi.judge`。
   */
  judge?: QualityJudgeRecord | null;
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
  /**
   * 单次 run 级模型档案覆盖：本次初始化全部 4 个 AI 节点统一使用该 model_profiles
   * 档案；不影响全局 capability_bindings。后端会校验 profile 存在，不存在 → 400。
   * null / 缺省 → 走全局 capability_bindings。
   */
  model_profile_id?: string | null;
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
//   GET    /capability-bindings    —— 8 项固定清单（V3.9.3 起含 observer 状态提取）
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

/** V3.8 「拉取模型」端点（POST /model-profiles/available-models）载荷与响应。 */
export interface FetchAvailableModelsPayload {
  provider: string;
  base_url?: string;
  api_key?: string;
  profile_id?: string;
}

export interface FetchAvailableModelsResponse {
  models: string[];
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

// ---------------------------------------------------------------------------
// 题材库（genre_pack，P3b 前端面板）
//   对齐 packages/core/genre/model.py 与 packages/core/api/routers/genre.py。
//   手写类型（types.generated.ts 由 scripts/gen_frontend_types.py 统一 regen，
//   本批次不动生成文件）。
// ---------------------------------------------------------------------------

/** 爽点类型条目（payload.payoff_types[]）：规定性条目，含密度与疲劳管理参数。 */
export interface GenrePayoffType {
  type_id: string;
  name: string;
  /** 强度分级：S=卷级 / M=章级中型 / s=章级小型。 */
  strength?: 'S' | 'M' | 's' | null;
  /** 密度上限原文文本（如「每卷 2~3 次」）。 */
  density_cap?: string | null;
  min_interval_chapters?: number | null;
  description?: string | null;
  applicable?: string | null;
  fatigue_risk?: string | null;
  verify_hint?: string | null;
  mapped_tropes?: string[];
  source?: string | null;
  stale?: boolean | null;
}

/** payload 消费子集（5 段 + P2 追加段；后端 extra="allow"，未知键原样保留）。 */
export interface GenrePackPayload {
  schema_version?: string;
  payoff_types?: GenrePayoffType[];
  structure_templates?: Record<string, unknown> | null;
  pacing?: Record<string, unknown> | null;
  ratio_declarations?: Record<string, number> | null;
  style_constraints?: Record<string, unknown> | null;
  [key: string]: unknown;
}

/** GET /genre-packs 行（列表摘要，无 payload 全文）。 */
export interface GenrePackSummary {
  pack_id: string;
  name: string;
  genre_tag: string;
  version: number;
  source_path: string | null;
  created_at: string;
  updated_at: string;
  payoff_type_count: number;
  structure_model: string | null;
  chapter_words_target: number | null;
  bound_project_count: number;
}

/** 题材包全文（GET /genre-packs/{pack_id} / 绑定响应里的 pack）。 */
export interface GenrePack {
  pack_id: string;
  name: string;
  genre_tag: string;
  version: number;
  payload: GenrePackPayload;
  source_path: string | null;
  created_at: string;
  updated_at: string;
  bound_project_count: number;
}

/** 项目 → 题材包绑定（单 slot；未绑定 → bound=false, pack=null）。 */
export interface GenreBinding {
  project_id: string;
  pack_id: string | null;
  bound: boolean;
  pack: GenrePack | null;
}

/** 核销层 v1 issue（review_report.genre_check.issues[]；恒 warning，report-only）。 */
export interface GenreCheckIssue {
  rule_id: string;
  severity: string;
  category: string;
  message: string;
  location?: Record<string, unknown> | null;
  suggestion?: string | null;
  evidence_refs?: string[];
}

/** review_report 的 genre_check 段（bound=false 时整段缺席）。 */
export interface GenreCheck {
  bound: boolean;
  checked: boolean;
  pack_id?: string | null;
  pack_version?: number | null;
  issues: GenreCheckIssue[];
  issue_count?: number;
  rule_ids?: string[];
  ratio_check?: Record<string, unknown> | null;
  redline_check?: Record<string, unknown> | null;
  /** 核销 v2 弧级累计对账段（卷内 typed scene ≥20 时出弧级结论；缺席=样本未就绪）。 */
  arc_check?: Record<string, unknown> | null;
  skipped?: string[];
  error?: string | null;
}

// =====================================================================
// G6 批次：后端已有、UI 零入口的能力（2026-10-15）
// ---------------------------------------------------------------------
// 下面四组类型逐字照抄后端契约，**不写归一化层**（理由见 endpoints.ts
// `storyStateApi` 的同款注释：归一化曾把两个字段拍反并让统计项恒为 0）。
// 每条对应后端的一个 `response_model`，形状有 schema 兜底。

/** 卷状态（与 DDL CHECK 对齐，`domain/volume/models.py::VolumeStatus`）。 */
export type VolumeStatus = 'active' | 'sealed';

/** 卷列表项（`GET /projects/{pid}/volumes`；含 chapter_count 聚合）。 */
export interface VolumeListItem {
  volume_id: string;
  project_id: string;
  number: number;
  title: string | null;
  arc_summary: string | null;
  status: VolumeStatus;
  terminal_snapshot_json: Record<string, unknown> | null;
  chapter_count: number;
  created_at: string;
  updated_at: string;
}

/** 卷完整表示（`GET /projects/{pid}/volumes/{vid}` / POST / PATCH 响应）。 */
export interface Volume extends Omit<VolumeListItem, 'chapter_count'> {}

/** 创建卷请求体（POST /volumes）。number 必填 ≥1；status 不可指定。 */
export interface VolumeCreatePayload {
  number: number;
  title?: string | null;
  arc_summary?: string | null;
}

/** 卷部分更新请求体（PATCH /volumes/{vid}）。sealed→active 由后端拒（409）。 */
export interface VolumeUpdatePayload {
  title?: string | null;
  status?: VolumeStatus;
  arc_summary?: string | null;
}

/**
 * 人物关系表行（`GET /projects/{pid}/relationships`，**只读**）。
 *
 * ⚠️ 与 `Character.relationships`（角色 core JSON 里的自由字段，CharacterTab 可编辑）
 * **是两个东西**：本表是有方向的成对关系，由 State Delta 的
 * `relationship_changes` 驱动写入（S2），S1 只提供只读端点。
 * 两者同名不同物，UI 不得互相替代。
 */
export interface Relationship {
  id: string;
  project_id: string;
  from_character_id: string;
  to_character_id: string;
  relation_type: string;
  state: Record<string, unknown>;
  last_state_version: number;
}

/** 签约体检单条（`GET /projects/{pid}/signing-check` 的 items[]）。 */
export interface SigningCheckItem {
  key: string;
  level: string;
  detail: string;
  advice?: string;
}

/** 签约体检结果（`GET /projects/{pid}/signing-check`）。 */
export interface SigningCheckResult {
  project_id: string;
  project_name: string;
  total_chars: number;
  generated_at: string;
  items: SigningCheckItem[];
  summary: {
    pass_count: number;
    warn_count: number;
    fail_count: number;
    info_count: number;
  };
  word_band_source: string;
  word_band_rejected?: boolean;
  genre_opening?: Record<string, unknown> | null;
}

// ---- 交付判定（`GET /projects/{pid}/delivery-verdict`） ------------------
// 形状逐字对应后端 `core/delivery/models.py` 的 pydantic 家族；该组 schema
// 2026-10-15 之前**已在 types.generated.ts 里生成但 UI 不接**（评估 G6）。

/** 判定四档（与后端 `delivery/verdict.py` 一致；不是章状态机）。 */
export type DeliveryVerdict =
  | 'deliverable'
  | 'needs_work'
  | 'not_deliverable'
  | 'not_a_candidate';

/** 证据源可达性（诚实面：看不到的要说出来，而不是当作通过）。 */
export interface DeliveryEvidenceSource {
  key: string;
  reachable: boolean;
  detail: string;
}

/** 交付判定的单条降级理由。 */
export interface DeliveryReason {
  rule_id: string;
  source: 'quality_issue' | 'length' | 'signing_check' | 'chapter_status' | 'evidence';
  severity: string;
  status: string;
  /** 面向作者的中文说明。 */
  message: string;
  /** 证据摘录（引文片段或数字）。 */
  evidence?: string | null;
}

/** 字数测量（`quality.wordcount` 口径）。 */
export interface DeliveryLength {
  visible_chars: number;
  target_word_count: number;
  band_low: number;
  band_high: number;
  deviation_pct: number;
  status: string;
  tier: string;
}

/** 质量报告里的一条 issue（gate 由后端 `issue_gate` 权威重推）。 */
export interface DeliveryIssue {
  rule_id: string;
  severity: string;
  gate: string;
  message: string;
}

/** 该章最新质量报告的证据面。 */
export interface DeliveryQuality {
  report_id: string;
  overall: number;
  evaluated_at?: string | null;
  draft_version?: number | null;
  gate_summary?: Record<string, unknown> | null;
  accepted_override?: Record<string, unknown> | null;
  issues?: DeliveryIssue[];
}

/** 签约体检在本章的适用面。 */
export interface DeliverySigning {
  scope: string;
  note?: string;
  items?: SigningCheckItem[];
}

export interface DeliverySigningItem {
  key: string;
  level: string;
  detail: string;
  advice?: string;
}

/** 一章的交付判定行。 */
export interface DeliveryChapter {
  chapter_id: string;
  number: number;
  title: string;
  /** 章节状态（流程位置，非交付判定）。 */
  status: string;
  verdict: DeliveryVerdict;
  blocking_reason?: string | null;
  draft_version?: number | null;
  length?: DeliveryLength | null;
  quality?: DeliveryQuality | null;
  signing: DeliverySigning;
  reasons?: DeliveryReason[];
}

/** 项目级汇总（作者的问题是「这本书能不能交」）。 */
export interface DeliveryRollUp {
  chapter_count: number;
  written_chapter_count: number;
  deliverable_count: number;
  needs_work_count: number;
  not_deliverable_count: number;
  not_a_candidate_count: number;
  deliverable_chapter_ids: string[];
  needs_work_chapter_ids: string[];
  not_deliverable_chapter_ids: string[];
  not_a_candidate_chapter_ids: string[];
  /** 每章 id → 首个降级理由（可交付章不出现）。 */
  blocking_reason_by_chapter?: Record<string, unknown>;
}

export interface DeliveryVerdictResponse {
  project_id: string;
  project_name: string;
  generated_at: string;
  project_verdict: DeliveryVerdict;
  project_reasons?: DeliveryReason[];
  roll_up: DeliveryRollUp;
  evidence_sources?: DeliveryEvidenceSource[];
  signing_check_scope?: string;
  chapters?: DeliveryChapter[];
}

// ============================================================================
// 2026-10-15 批次（四）：G6 挂账 5 条的契约类型
// ============================================================================
//
// ⚠️ **以下 5 组类型全部手写**，因为对应 router 的 body/response 都是裸 `dict`
// （`reveal_policies.py` / `arc.py` / `simulation.py` / `quality.py` 的 judge 段），
// OpenAPI 里**没有** schema ⇒ `types.generated.ts` 永远不会生成它们。
//
// 派生来源逐组登记（按 G10 纪律：手写契约必须写明它是从哪儿读出来的，
// 否则就成了「不知道后端返回什么」的兜底）：
//   - RevealPolicy      ← `packages/domain/knowledge/models.py:45` 的 `RevealPolicy.to_dict()`
//   - ArcView           ← `packages/core/arc/service.py::build_arc_view` 返回 dict
//   - SimulationResult  ← `packages/core/simulation/service.py:125` 的 `SimulationResult.to_dict()`
//   - StateDiff         ← `packages/core/story_state/snapshots.py:121` 的 `diff_snapshots`
//   - QualityJudge*     ← `packages/core/quality/service.py:198` 的 `save_judge_score`
//
// **刻意不用 `[key: string]: unknown` 索引签名**（G10 的病根）：快照内的元素是
// 不透明 blob（按 character_id/hook_id 键），用 `Record<string, unknown>` 精确表达，
// 而顶层字段一律必填——后端确实每次都返回它们。
//
// 挂账：这 4 个 router 应补 Pydantic 模型并声明 `response_model`
// （`SnapshotResponse` 已是先例）。⚠️ 但 `response_model` 会**过滤**未声明字段——
// 补模型前必须先核对实际返回的键集，否则会静默丢字段
// （`tests/api/test_volume_membership_contract.py` 就是这个坑的看守）。

/** 剧透策略的状态（对齐 DDL CHECK，迁移 0014_knowledge_reveal.sql）。 */
export type RevealPolicyStatus = 'planned' | 'revealed' | 'cancelled';

/** 被策略指向的实体种类。 */
export type RevealTargetKind =
  | 'character'
  | 'location'
  | 'faction'
  | 'world_rule'
  | 'event'
  | 'hook'
  | 'debt'
  | 'relationship';

/**
 * 一条剧透策略：「这个秘密什么时候、对谁揭开」。
 *
 * ⚠️ 与「伏笔 hooks」**不是同一个东西**：hooks 是实体（可 CRUD，本仓有完整 UI），
 * 一条 `target_kind='hook'` 的策略只是**指向**某个 hook_id、约定何时/对谁揭露它。
 */
export interface RevealPolicy {
  policy_id: string;
  project_id: string;
  target_kind: RevealTargetKind;
  target_id: string;
  /** 计划在第几章前揭开；null = 未定。 */
  reveal_by_chapter: number | null;
  /** `reader`（读者）/ `character:<id>` / 逗号混写。 */
  audience: string;
  status: RevealPolicyStatus;
  revealed_chapter: number | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface RevealPolicyCreate {
  target_kind: RevealTargetKind;
  target_id: string;
  reveal_by_chapter?: number | null;
  audience?: string;
  status?: RevealPolicyStatus;
  revealed_chapter?: number | null;
  notes?: string | null;
}

/** ⚠️ 后端只认这三个键，其余字段静默忽略。 */
export interface RevealPolicyUpdate {
  status?: RevealPolicyStatus;
  revealed_chapter?: number | null;
  notes?: string | null;
}

// ---------------------------------------------------------------- 弧光 / arc

export interface ArcChapterRow {
  number: number;
  title: string;
  has_payoff_beat: boolean;
  charge_beat: boolean;
  overall: number | null;
  pacing: number | null;
  prose_chars: number | null;
  volume_id: string | null;
  volume_number: number | null;
  // ⚠️ 后端 `build_arc_view` 显式 pop 掉 chapter_id，故此处**没有**该字段——
  // 不要在 UI 里拿它当行键或跳转目标。
}

export interface ArcVolumeRow {
  volume_id: string;
  number: number;
  title: string | null;
  status: string;
  chapter_count: number;
}

export interface ArcOverdueReveal {
  policy_id: string;
  target_kind: string;
  target_id: string;
  reveal_by_chapter: number | null;
  audience: string;
}

/** 节奏/伏笔告警。`level=fail` 的两条是硬信号，`warn` 是体检项。 */
export interface ArcAlert {
  level: 'warn' | 'fail';
  code: string;
  message: string;
}

export interface ArcView {
  project_id: string;
  generated_at: string;
  chapters: ArcChapterRow[];
  payoff: {
    total: number;
    charge_streak: number;
    max_charge_streak: number;
    last_payoff_chapter: number | null;
  };
  hooks: { open: number; resolved: number; overdue: number };
  debts: { open: number; paid: number };
  reveal_policies: {
    planned: number;
    revealed: number;
    cancelled: number;
    overdue: ArcOverdueReveal[];
  };
  volumes: ArcVolumeRow[];
  alerts: ArcAlert[];
}

// ------------------------------------------------------------------ 推演

/** 列表 diff 的元素是不透明快照 blob（按各自 id 键）。 */
export interface SnapshotListDiff {
  added?: Record<string, unknown>[];
  removed?: Record<string, unknown>[];
  changed?: Array<{ id: string; changes?: Record<string, { before: unknown; after: unknown }> }>;
}

/** ⚠️ 所有键都可能在为空时**整个省略**（`diff_snapshots` 用「空则不写」）。
 * 读法必须是 `diff.hooks?.added?.length ?? 0`，不能假设存在。 */
export interface StateDiff {
  version_a?: number;
  version_b?: number;
  branch_id?: string | null;
  characters?: SnapshotListDiff;
  world?: Record<string, { before: unknown; after: unknown }>;
  hooks?: SnapshotListDiff;
  debts?: SnapshotListDiff;
  events_changed?: Record<string, unknown>;
  recent_events?: SnapshotListDiff;
}

export interface SimulationResult {
  simulation_id: string;
  branch_id: string;
  name: string;
  base_version: number;
  /** 已成功应用的 delta 条数（失败时 = 部分应用数）。 */
  applied: number;
  diff: StateDiff;
  issues: Array<{ index: number; errors: string[] }>;
  branch_status: string;
  created_at: string;
}

export interface SimulationSummary {
  simulation_id: string;
  branch_id: string;
  name: string;
  base_version: number;
  status: string;
  created_at: string;
}

// ------------------------------------------------------- 人工评分 / judge

/** ⚠️ 四个维度后端**必填**且必须是 0-100 的整数，否则 422。 */
export interface QualityJudgePayload {
  pacing: number;
  style: number;
  logic: number;
  dialogue: number;
  /** 单条决议 ≤50 字。 */
  verdict?: string;
  /** 最多 3 条，每条 ≤30 字。 */
  top_issues?: string[];
}

export interface QualityJudgeRecord {
  pacing: number;
  style: number;
  logic: number;
  dialogue: number;
  verdict?: string | null;
  top_issues?: string[] | null;
  [k: string]: unknown;
}

export interface QualityJudgeResponse {
  report_id: string;
  chapter_id: string;
  /** null = 该报告尚未被打分。 */
  judge: QualityJudgeRecord | null;
}
