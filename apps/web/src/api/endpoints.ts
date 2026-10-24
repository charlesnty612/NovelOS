// 各业务端点的薄封装。返回值已统一经 ApiError/coerceJson 处理。

import { api } from './client';
import { coerceJson } from './client';
import type {
  Agent,
  AiCallLogDetail,
  AiCallLogSummary,
  AiCallLogTokenUsage,
  BackupPackage,
  Branch,
  BranchCreatePayload,
  BranchPromoteResult,
  CapabilityBinding,
  CanonDetail,
  CanonSummary,
  Chapter,
  ChapterCreatePayload,
  ChapterUpdatePayload,
  Character,
  CharacterCreatePayload,
  CharacterState,
  CharacterUpdatePayload,
  Commit,
  ConnectionCreatePayload,
  ConnectionPresetOut,
  ConnectionTestResult,
  ConnectionUpdatePayload,
  ContextPreviewResponse,
  ContinueAdoptPayload,
  ContinueAdoptResponse,
  ContinueRequestPayload,
  ContinueResponse,
  Debt,
  DebtCreatePayload,
  DebtUpdatePayload,
  DeliveryVerdictResponse,
  DeconstructPayload,
  DeconstructStartResponse,
  Draft,
  DraftCreatePayload,
  FetchAvailableModelsPayload,
  FetchAvailableModelsResponse,
  GenreBinding,
  GenrePack,
  GenrePackSummary,
  HealthResponse,
  Hook,
  HookCreatePayload,
  HookUpdatePayload,
  InitStatusResponse,
  ModelConfig,
  ModelConfigCreatePayload,
  ModelConfigTestResult,
  ModelConfigUpdatePayload,
  ModelConnectionOut,
  ModelProfile,
  ModelProfileCreatePayload,
  ModelProfileTestResult,
  ModelProfileUpdatePayload,
  ArcView,
  OutlineBatchRequest,
  PlotEvent,
  PlotEventCreatePayload,
  Project,
  ProjectCreatePayload,
  ProjectInitPayload,
  ProjectInitResponse,
  ProjectInitResumeRequestPayload,
  ProjectUpdatePayload,
  PromptVersion,
  QualityReport,
  QualityJudgePayload,
  QualityJudgeResponse,
  ReaderProfile,
  Relationship,
  ResumeRequestPayload,
  RevealPolicy,
  RevealPolicyCreate,
  RevealPolicyStatus,
  RevealPolicyUpdate,
  SigningCheckResult,
  SimulationResult,
  SimulationSummary,
  SnapshotResponse,
  StyleSample,
  StyleSampleCreatePayload,
  SyncPromptsResult,
  TimelineEvent,
  Volume,
  VolumeCreatePayload,
  VolumeListItem,
  VolumeUpdatePayload,
  WorldEntity,
  WorldEntityPayload,
  WorkflowRun,
  WorkflowStartAccepted,
  WorkflowStartPayload,
  WorkflowStartResponse,
} from './types';

// ---------------------------------------------------------------- projects
export const projectsApi = {
  list: () => api.get<Project[]>('/projects'),
  create: (payload: ProjectCreatePayload) => api.post<Project>('/projects', payload),
  get: (id: string) => api.get<Project>(`/projects/${id}`),
  update: (id: string, payload: ProjectUpdatePayload) =>
    api.patch<Project>(`/projects/${id}`, payload),
  delete: (id: string) => api.delete<void>(`/projects/${id}`),
  // P1 project-init：触发 AI 初始化设定工作流（项目总览页挂载入口）。
  // 对齐 packages/core/api/routers/workflows.py POST /projects/init。
  // brief 字段在调用端已做 trim；project_id 在 payload 内携带（UI 入口仅在已有项目页打开）。
  init: (payload: ProjectInitPayload) =>
    api.post<ProjectInitResponse>('/projects/init', payload),
  // P1 project-init 增量：探测项目已有设定覆盖度，用于表单预勾选。
  // 对齐 packages/core/api/routers/projects.py GET /projects/{id}/init-status。
  initStatus: (projectId: string) =>
    api.get<InitStatusResponse>(`/projects/${projectId}/init-status`),
  // outline-batch（批量续规划下一批章节）：项目级异步工作流（装配 → LLM 规划 →
  // 落库建 PLANNED 章），201 返回 run 三键，真终态须轮询 GET /runs/{run_id}。
  // 对齐 packages/core/api/routers/workflows/control.py POST /projects/{id}/outline-batch。
  // 错误码：chapter_count 越界 422 / 项目不存在 404 / model_profile_id 无效 400 /
  // 项目级并发（已有 RUNNING 的 outline-batch run）409。
  outlineBatch: (projectId: string, payload: OutlineBatchRequest) =>
    api.post<WorkflowStartAccepted>(`/projects/${projectId}/outline-batch`, payload),
};

// ------------------------------------------------------------- style samples
// 对应 packages/core/api/routers/author_style_samples.py：
//   GET    /projects/{pid}/style-samples              列表（created_at DESC）
//   POST   /projects/{pid}/style-samples              新增（content ≤ 5000 字；项目 ≤ 10 篇）
//   DELETE /projects/{pid}/style-samples/{sample_id}  删除（204）
export const styleSamplesApi = {
  list: (pid: string) =>
    api.get<StyleSample[]>(`/projects/${pid}/style-samples`),
  create: (pid: string, payload: StyleSampleCreatePayload) =>
    api.post<StyleSample>(`/projects/${pid}/style-samples`, payload),
  remove: (pid: string, sampleId: string) =>
    api.delete<void>(`/projects/${pid}/style-samples/${sampleId}`),
};

// -------------------------------------------------------------- characters
export const charactersApi = {
  listByProject: (pid: string) =>
    api.get<Character[]>(`/projects/${pid}/characters`),
  create: (pid: string, payload: CharacterCreatePayload) =>
    api.post<Character>(`/projects/${pid}/characters`, payload),
  get: (id: string) => api.get<Character>(`/characters/${id}`),
  update: (id: string, payload: CharacterUpdatePayload) =>
    api.patch<Character>(`/characters/${id}`, payload),
  delete: (id: string) => api.delete<void>(`/characters/${id}`),
  listStates: (id: string) =>
    api.get<CharacterState[]>(`/characters/${id}/states`),
};

// -------------------------------------------------------------- world entities
// 后端对 location/faction/world_rule 用不同路径但同构结构；用辅助函数收敛。
function entityApi(resource: 'locations' | 'factions' | 'world-rules') {
  return {
    list: (pid: string) =>
      api
        .get<WorldEntity[]>(`/projects/${pid}/${resource}`)
        .then((rows) => rows.map(normalizeEntity)),
    create: (pid: string, payload: WorldEntityPayload) =>
      api
        .post<WorldEntity>(`/projects/${pid}/${resource}`, payload)
        .then(normalizeEntity),
    update: (id: string, payload: WorldEntityPayload) =>
      api.patch<WorldEntity>(`/${resource}/${id}`, payload).then(normalizeEntity),
    delete: (id: string) => api.delete<void>(`/${resource}/${id}`),
  };
}

function normalizeEntity(row: WorldEntity): WorldEntity {
  return {
    ...row,
    data: (coerceJson(row.data) as Record<string, unknown>) ?? {},
  };
}

export const locationsApi = entityApi('locations');
export const factionsApi = entityApi('factions');
export const worldRulesApi = entityApi('world-rules');

// -------------------------------------------------------------- plot events
export const eventsApi = {
  list: (pid: string) =>
    api
      .get<PlotEvent[]>(`/projects/${pid}/events`)
      .then((rows) => rows.map(normalizeEvent)),
  create: (pid: string, payload: PlotEventCreatePayload) =>
    api
      .post<PlotEvent>(`/projects/${pid}/events`, payload)
      .then(normalizeEvent),
  delete: (id: string) => api.delete<void>(`/events/${id}`),
};

function normalizeEvent(row: PlotEvent): PlotEvent {
  return {
    ...row,
    // 后端契约：cause/effects 是 event_id 字符串数组（plot/models.py L40-41）；
    // 若服务端把列表 JSON 序列化成字符串，统一经 coerceJson 还原成数组。
    cause: coerceJson(row.cause) as string[] | null,
    effects: coerceJson(row.effects) as string[] | null,
    time: coerceJson(row.time) as Record<string, unknown> | null,
  };
}

export const timelineApi = {
  list: (pid: string) => api.get<TimelineEvent[]>(`/projects/${pid}/timeline`),
  /**
   * 删时间线条目（`DELETE /timeline/{id}`）。
   *
   * 评估 G6：此前 PlotTab 的时间线**只读**——作者误建（重复日、临时占位）之后
   * 没有别的清理路径，只能改库。后端已有该端点，且返回 204。
   * 不可撤销：UI 侧不给「撤销」，改用二次确认（见 PlotTab）。
   */
  remove: (timelineId: string) => api.delete<void>(`/timeline/${timelineId}`),
};

// -------------------------------------------------------------- 人物关系表（只读）
/**
 * `relationships` **表**的有向成对关系（`GET /projects/{pid}/relationships`）。
 *
 * ⚠️ 与 `Character.relationships`（角色 core JSON 的自由字段，CharacterTab 可编辑）
 * 同名不同物：本表由 State Delta 的 `relationship_changes` 驱动写入，只读端点
 * 来自 Sprint 1。dev 库实测 54 行而后端此前零 UI 入口（评估 G6）。
 */
export const relationshipsApi = {
  list: (pid: string) => api.get<Relationship[]>(`/projects/${pid}/relationships`),
};

// -------------------------------------------------------------- 卷（G6）
/**
 * 卷（`volumes` 表）。**卷与章节的隶属关系此前在 UI 上完全不存在**——
 * 作者能建卷能封卷（后端 6 个端点全在），但看不到任何一章属于哪一卷。
 *
 * ⚠️ `seal` 是**不可逆**的终态动作：封存会把当时的故事状态快照冻结进
 * `terminal_snapshot_json`，且此后该卷拒绝挂章、`sealed → active` 反向跳变被后端
 * 拒（409）。UI 必须二次确认，且**不提供**「解封」按钮（后端也不支持）。
 */
export const volumesApi = {
  list: (pid: string) => api.get<VolumeListItem[]>(`/projects/${pid}/volumes`),
  create: (pid: string, payload: VolumeCreatePayload) =>
    api.post<Volume>(`/projects/${pid}/volumes`, payload),
  get: (pid: string, volumeId: string) =>
    api.get<Volume>(`/projects/${pid}/volumes/${volumeId}`),
  update: (pid: string, volumeId: string, payload: VolumeUpdatePayload) =>
    api.patch<Volume>(`/projects/${pid}/volumes/${volumeId}`, payload),
  seal: (pid: string, volumeId: string) =>
    api.post<Volume>(`/projects/${pid}/volumes/${volumeId}/seal`, {}),
  assign: (pid: string, volumeId: string, chapterId: string) =>
    api.post<Volume>(`/projects/${pid}/volumes/${volumeId}/assign`, { chapter_id: chapterId }),
};

// -------------------------------------------------------------- state / commits / health
export const storyStateApi = {
  /**
   * 取当前 Canonical State（main；传 branch_id 时取分支视角）。
   *
   * ⚠️ **不要在这里加归一化层**（2026-10-15 移除，理由是被它咬过）：
   * 这条端点**此前**有一层 `normArr` / `normObj` 手写归一化，把六个字段按猜测的
   * 形状拍一遍——而后端 `snapshots.latest_snapshot_version` 早已用
   * `_parse_required_json` 把 `story_states.snapshot_json`（TEXT 列）解析成
   * dict 再返回，**归一化是纯损失**。它真正的伤害是两个**恒为 0** 的统计项：
   *   - `characters` 后端是 **list**，被 `normObj` 拍成 `{}`
   *   - `events` 后端是 **dict**（按 event_id 键），被 `normArr` 拍成 `[]`
   * 概览页「角色」与「事件·累计」因此常年 0，且无任何告警。
   *
   * 归一化之所以存在，是因为 `types.ts` 的 `SnapshotResponse` 是欠定义类型（全
   * optional + `[key: string]: unknown` + 并列着后端从不返回的 `version`）。
   * 该类型已按后端 `core/story_state/models.py::SnapshotResponse` 契约重写，
   * **`response_model` 保证形状**，前端不需要再猜。
   */
  getCurrent: (pid: string, branchId?: string) =>
    api.get<SnapshotResponse>(`/projects/${pid}/state`, {
      branch_id: branchId,
    }),
  // V1.5 / Sprint 17：分支视角 state。形态与 getCurrent 同构（同一端点、同一
   // response_model）；404 时 BranchNotFound 抛 ApiError。
  getBranchState: (pid: string, branchId: string) =>
    api.get<SnapshotResponse>(`/projects/${pid}/state`, { branch_id: branchId }),

  /** 该章 observer 抽出的全部状态变更（append-only 历史，按 created_at ASC）。 */
  listDeltas: (pid: string, chapterId: string) =>
    api.get<StateDeltaRow[]>(`/projects/${pid}/chapters/${chapterId}/deltas`),

  /** 指定快照（与 getCurrent 同构，有 response_model）。 */
  getVersion: (pid: string, version: number) =>
    api.get<SnapshotResponse>(`/projects/${pid}/state/versions/${version}`),

  /** 两个 state_version 之间的结构化 diff（main 路径；分支视角不支持）。 */
  diff: (pid: string, a: number, b: number) =>
    api.get<StateDiffResponse>(`/projects/${pid}/state/diff`, { a, b }),
};

/**
 * `GET /projects/{pid}/chapters/{cid}/deltas` 的行形状。
 *
 * ⚠️ 该端点**没有 response_model**（2026-10-15 时），返回裸 dict；这里只声明
 * 章节侧面板真正读到的键，其余键由 `[k: string]: unknown` 兜住。与 SnapshotResponse
 * 不同——那份的形状是硬契约（写进了 `state_delta.schema.json` 并由后端 model 固定），
 * 这份的 17 个顶层键全部来自 schema，形状稳定，但**类型尚未由后端强制**，
 * 故保持宽松而不是假装严格。
 */
export interface StateDeltaRow {
  delta_id?: string;
  chapter_id?: string;
  workflow_run_id?: string | null;
  previous_state_version?: number;
  created_by?: string | null;
  created_at?: string;
  status?: string;
  notes?: string | null;
  character_changes?: unknown[];
  world_changes?: unknown[];
  relationship_changes?: unknown[];
  new_events?: unknown[];
  new_hooks?: unknown[];
  resolved_hooks?: unknown[];
  debt_changes?: unknown[];
  [k: string]: unknown;
}

/**
 * `GET /projects/{pid}/state/diff` 的返回结构（见
 * `packages/core/story_state/queries.py::diff_versions` docstring）。
 * 同样无 response_model，故声明为已知的形状 + 兜底索引签名。
 */
export interface StateDiffResponse {
  version_a?: number;
  version_b?: number;
  branch_id?: string | null;
  characters?: { changed?: unknown[]; added?: unknown[]; removed?: unknown[] };
  hooks?: { changed?: unknown[]; added?: unknown[]; removed?: unknown[] };
  debts?: { changed?: unknown[]; added?: unknown[]; removed?: unknown[] };
  events_changed?: Record<string, unknown>;
  [k: string]: unknown;
}

export const commitsApi = {
  list: (pid: string) => api.get<Commit[]>(`/projects/${pid}/commits`),
  /**
   * 回滚某个 commit（`POST /commits/{cid}/rollback`）。
   *
   * ⚠️ **高风险 delta 需要显式批准**：后端对 HIGH 风险的 observer 变更抛
   * `ApprovalRequiredError` → 409 `{"error": "approval_required",
   * "high_risk_change_ids": [...]}`。UI 必须在把 change id 摆给作者看之后才发
   * `author_approval`——「不许在不知道批准什么的情况下批准」（与质量门禁
   * `gate_override` 同一原则）。
   */
  rollback: (commitId: string, authorApproval?: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`/commits/${commitId}/rollback`, {
      author_approval: authorApproval ?? {},
    }),
};

export const healthApi = {
  get: () => api.get<HealthResponse>('/health'),
};

// -------------------------------------------------------------- chapters
// 列表按 number ASC（后端 ChapterService.list_by_project 给死）；
// drafts 列表按 version DESC（后端 ChapterService.list_drafts 给死）。
function normalizeChapter(row: Chapter): Chapter {
  return {
    ...row,
    plan_json: (coerceJson(row.plan_json) as Record<string, unknown>) ?? {},
    who_knows: row.who_knows ?? null,
  };
}

export const chaptersApi = {
  listByProject: (pid: string) =>
    api.get<Chapter[]>(`/projects/${pid}/chapters`).then((rows) =>
      rows.map(normalizeChapter),
    ),
  create: (pid: string, payload: ChapterCreatePayload) =>
    api
      .post<Chapter>(`/projects/${pid}/chapters`, payload)
      .then(normalizeChapter),
  get: (cid: string) => api.get<Chapter>(`/chapters/${cid}`).then(normalizeChapter),
  update: (cid: string, payload: ChapterUpdatePayload) =>
    api.patch<Chapter>(`/chapters/${cid}`, payload).then(normalizeChapter),
  delete: (cid: string) => api.delete<void>(`/chapters/${cid}`),

  listDrafts: (cid: string) => api.get<Draft[]>(`/chapters/${cid}/drafts`),
  createDraft: (cid: string, payload: DraftCreatePayload) =>
    api.post<Draft>(`/chapters/${cid}/drafts`, payload),

  // 重开返修（2026-09-26 批次）：仅 COMMITTED 章可调（后端 409 拦截其余状态）。
  // 语义：commits 历史不撤销（append-only），重开为 REVIEWED 后走既有评审/提交链
  // 产生新 commit。reason 可选，非空时后端附加写入 plan_json.revision_note。
  // 同章有 RUNNING/PENDING run 时后端 409（活动 run 守卫，与 workflow start 同款）。
  reopenChapter: (cid: string, payload: { reason?: string | null } = {}) =>
    api.post<Chapter>(`/chapters/${cid}/reopen`, payload).then(normalizeChapter),

  /**
   * 单独写改稿意见（`PATCH /chapters/{cid}/revision-note`，body `{note}`）。
   *
   * ⚠️⚠️ **它与 resume 的 `human_input.note` 写的是同一个槽**：
   *   `chapters.plan_json["revision_note"]`——`ChapterService.update_revision_note`
   *   与 `workflows/chapter_review/pipeline.py:1117` 都往这里写，
   *   **后写覆盖先写、无历史、无版本**。全仓有三个写入方（本端点 / resume / reopen）。
   *
   * ⇒ UI **不得**把它呈现成「作者笔记 / 意见箱」这类可累积的东西。
   *   正确呈现是「改稿指令」：给作者一个在**触发改稿之前**先写下指令的地方，
   *   并显示当前槽里已有的内容 + 标明它可能是上一次 resume 写进去的。
   *
   * ⚠️ 章节状态 ∉ {PLANNED, DRAFTED, REVIEWED} 时后端 409（已定稿章节锁死）。
   * ⚠️ 纯空白 note = **删键**（不是存空串）。
   */
  setRevisionNote: (cid: string, note: string) =>
    api.patch<Chapter>(`/chapters/${cid}/revision-note`, { note }).then(normalizeChapter),
};

// -------------------------------------------------------------- workflows
// workflow_runs 行字段：run_id / workflow_id / chapter_id / status /
// current_node / checkpoint_json / error / retry_count / started_at / ended_at
// 后端 list_runs 返回的是直接 SELECT workflow_runs.*（不含 nodes；detail 走 get_run）；
// 前端 list 时不强求 nodes。
function normalizeRun(row: WorkflowRun): WorkflowRun {
  return {
    ...row,
    checkpoint_json: (coerceJson(row.checkpoint_json) as Record<string, unknown>) ?? {},
    nodes: row.nodes ?? [],
  };
}

export const workflowsApi = {
  listByProject: (pid: string) =>
    api.get<WorkflowRun[]>(`/projects/${pid}/runs`).then((rows) =>
      rows.map(normalizeRun),
    ),
  get: (runId: string) =>
    api.get<WorkflowRun>(`/runs/${runId}`).then(normalizeRun),

  startPlan: (pid: string, cid: string, payload: WorkflowStartPayload = {}) =>
    api.post<WorkflowStartResponse>(
      `/projects/${pid}/chapters/${cid}/plan`,
      payload,
    ),
  startWrite: (pid: string, cid: string, payload: WorkflowStartPayload = {}) =>
    api.post<WorkflowStartResponse>(
      `/projects/${pid}/chapters/${cid}/write`,
      payload,
    ),
  startReview: (pid: string, cid: string, payload: WorkflowStartPayload = {}) =>
    api.post<WorkflowStartResponse>(
      `/projects/${pid}/chapters/${cid}/review`,
      payload,
    ),
  startCommit: (pid: string, cid: string, payload: WorkflowStartPayload = {}) =>
    api.post<WorkflowStartResponse>(
      `/projects/${pid}/chapters/${cid}/commit`,
      payload,
    ),
  // V3.9 批次 4.1：quality_gate enforce 阻断后的「按门禁建议改稿」。
  // 后端语义：复用 plan_json.revision_note（门禁阻断时写入）启动 write（revise 模式），
  // write 完成后自动接力 chapter-review；无 gate_blocked 标记 → 409。
  // mock_providers / model_overrides 为该入口的既有透传；target_word_count /
  // author_intent 于 2026-09-21（m6）补上——不传时后端从该章最近一次 run 的 ctx 继承，
  // 否则改稿轮会退回服务端默认字数/丢掉作者铁律。
  gateRevise: (
    pid: string,
    cid: string,
    payload: {
      mock_providers?: Record<string, string[]> | null;
      model_overrides?: Record<string, string> | null;
      target_word_count?: number | null;
      author_intent?: string | null;
    } = {},
  ) =>
    api.post<WorkflowStartResponse>(
      `/projects/${pid}/chapters/${cid}/gate-revise`,
      payload,
    ),
  resume: (runId: string, payload: ResumeRequestPayload) =>
    api.post<WorkflowStartResponse>(`/runs/${runId}/resume`, payload),
  // P1 project-init：分步审阅放行（POST /runs/{id}/resume，body 为 revisions）。
  // 与 chapter-review 走同一条路由，但 payload 形态不同——类型层面单独封装以避免误用。
  resumeInit: (runId: string, payload: ProjectInitResumeRequestPayload) =>
    api.post<ProjectInitResponse>(`/runs/${runId}/resume`, payload),
  // 协作式取消：POST /runs/{run_id}/cancel。
  // 200 → {run_id, status:'CANCELLED'}；run 非 RUNNING（终态/PAUSED）→ 409；
  // 不存在 → 404。引擎在探针命中 CANCELLED 后停止推进，无需前端轮询状态变更。
  // 成功/409 后统一 reload run 详情与列表让 UI 自然回落。
  cancelRun: (runId: string) =>
    api.post<{ run_id: string; status: 'CANCELLED' }>(`/runs/${runId}/cancel`, {}),
};

// -------------------------------------------------------------- model configs
// model_configs 行字段：config_id / capability / provider / model / params_json /
// enabled / has_api_key（Sprint 12 P1-1 后端响应顶层附）。params_json 在 DB 中存为
// JSON 字符串，统一在此解析为对象便于前端表单读写。注意：读路径下 params_json.api_key
// 已被后端替换为 "***"，不要回填到 <input value>。
function normalizeModelConfig(row: ModelConfig): ModelConfig {
  return {
    ...row,
    params_json: (coerceJson(row.params_json) as Record<string, unknown>) ?? {},
    has_api_key: row.has_api_key ?? false,
  };
}

export const modelConfigsApi = {
  list: (query?: { capability?: string; provider?: string }) =>
    api
      .get<ModelConfig[]>('/model-configs', query)
      .then((rows) => rows.map(normalizeModelConfig)),
  get: (id: string) =>
    api.get<ModelConfig>(`/model-configs/${id}`).then(normalizeModelConfig),
  create: (payload: ModelConfigCreatePayload) =>
    api.post<ModelConfig>('/model-configs', payload).then(normalizeModelConfig),
  update: (id: string, payload: ModelConfigUpdatePayload) =>
    api
      .patch<ModelConfig>(`/model-configs/${id}`, payload)
      .then(normalizeModelConfig),
  delete: (id: string) => api.delete<void>(`/model-configs/${id}`),
  test: (id: string) =>
    api.post<ModelConfigTestResult>(`/model-configs/${id}/test`, {}),
};

// -------------------------------------------------------------- model profiles
// model_profiles 行字段：profile_id / name / provider / model / params_json /
// enabled / has_api_key。后端读路径（_mask_response）返回 params_json（dict，含
// 脱敏后的 api_key="***"）；历史字段名 params 仅在测试夹具出现。
// 读路径下 params_json.api_key 已被脱敏为 "***"，不回填明文输入框。
// 导出仅为测试：回归用例锁死「后端 params_json 键 → 前端 params 字段」映射。
export function normalizeProfile(row: ModelProfile): ModelProfile {
  return {
    ...row,
    params:
      (coerceJson(
        (row as unknown as { params_json?: unknown }).params_json ??
          row.params,
      ) as Record<string, unknown>) ?? {},
    has_api_key: row.has_api_key ?? false,
  };
}

export const modelProfilesApi = {
  list: () =>
    api
      .get<ModelProfile[]>('/model-profiles')
      .then((rows) => rows.map(normalizeProfile)),
  create: (payload: ModelProfileCreatePayload) =>
    api
      .post<ModelProfile>('/model-profiles', payload)
      .then(normalizeProfile),
  update: (id: string, payload: ModelProfileUpdatePayload) =>
    api
      .patch<ModelProfile>(`/model-profiles/${id}`, payload)
      .then(normalizeProfile),
  remove: (id: string) => api.delete<void>(`/model-profiles/${id}`),
  test: (id: string) =>
    api.post<ModelProfileTestResult>(`/model-profiles/${id}/test`, {}),
  // V3.8「拉取模型」：后端代理调 provider 的模型列表接口。
  // 对应 packages/core/api/routers/model_profiles.py POST /model-profiles/available-models。
  // 入参可为空字符串——交由后端按 provider 走默认；apikey 不发（后端走 resolve_api_key）。
  // signal：15s 前端超时——后端进程若在响应中途死亡，fetch 可能永不落定（按钮永久
  // 「拉取中」事故），任何情况下都要让 promise settle。
  fetchAvailableModels: (payload: FetchAvailableModelsPayload) =>
    api.post<FetchAvailableModelsResponse>(
      '/model-profiles/available-models',
      payload,
      { signal: AbortSignal.timeout(15000) },
    ),
};

// -------------------------------------------------------------- model connections
// 2026-10-21 批次十五「连接层」：连接（provider / base_url / api_key / headers）与
// 档案（用什么模型 / 怎么调）拆成两层 ⇒ 同一厂商的多个档案挂同一条连接，
// 改一次密钥 / 地址 / 头，三条档案同时生效。
//
// ⚠️ 读路径下 `params_json.api_key` 恒为 "***"，`params_json.headers` 里的**敏感头**
// 逐键为 "***"（`x-opencode-session` 这类非凭据头**不掩**）⇒ 不回填到输入框。
//
// ⚠️ PATCH 的 `params` 语义：**缺键 = 保留原值**、显式 `null` = 删除该键；
// `headers` 是 dict ⇒ **逐键**语义（值 "***" = 该键不改）。
export function normalizeConnection(row: ModelConnectionOut): ModelConnectionOut {
  return {
    ...row,
    params_json: (coerceJson(row.params_json) as Record<string, unknown>) ?? {},
    has_api_key: row.has_api_key ?? false,
  };
}

export const connectionsApi = {
  /**
   * 连接预设清单（**一键填表**）。
   *
   * ⚠️ 权威清单在后端 `packages/core/model_router/presets.py`（**只此一处**）
   * ⇒ 前端只渲染、不硬编码任何 URL（避免同一组地址在前后端各写一份）。
   * 预设的 `params` 含 `headers` / `models_url`，**不含 api_key**。
   */
  presets: () => api.get<ConnectionPresetOut[]>('/model-connections/presets'),
  list: (query?: { include_enabled_only?: boolean }) =>
    api
      .get<ModelConnectionOut[]>('/model-connections', query)
      .then((rows) => rows.map(normalizeConnection)),
  get: (id: string) =>
    api.get<ModelConnectionOut>(`/model-connections/${id}`).then(normalizeConnection),
  create: (payload: ConnectionCreatePayload) =>
    api.post<ModelConnectionOut>('/model-connections', payload).then(normalizeConnection),
  update: (id: string, payload: ConnectionUpdatePayload) =>
    api
      .patch<ModelConnectionOut>(`/model-connections/${id}`, payload)
      .then(normalizeConnection),
  // 204。被任何档案引用 → 409（detail.referenced_by 列出引用它的档案）。
  remove: (id: string) => api.delete<void>(`/model-connections/${id}`),
  // 探测 `GET {models_url}`（缺省 `{base_url}/models`）。连接层没有 model 概念
  // ⇒ **不降级到 chat 探测**：端点 404/405/501 时如实报「该端点不提供模型列表」。
  test: (id: string) =>
    api.post<ConnectionTestResult>(`/model-connections/${id}/test`, {}),
};

// -------------------------------------------------------------- capability bindings
// GET 全量 8 项 capability → profile 映射（V3.9.3 起 observer 拆为独立环节）；
// PUT 单项绑定、DELETE 单项解绑。错误码：422 至少 1 个、档案存在 + enabled 才能保存（由后端校验）。
export const capabilityBindingsApi = {
  list: () => api.get<CapabilityBinding[]>('/capability-bindings'),
  bind: (capability: string, profileIds: string[]) =>
    api.put<CapabilityBinding>(`/capability-bindings/${capability}`, {
      profile_ids: profileIds,
    }),
  unbind: (capability: string) =>
    api.delete<void>(`/capability-bindings/${capability}`),
};

// -------------------------------------------------------------- agents
function normalizeAgent(row: Agent): Agent {
  return {
    ...row,
    config_json: (coerceJson(row.config_json) as Record<string, unknown>) ?? {},
  };
}

export const agentsApi = {
  list: () => api.get<Agent[]>('/agents').then((rows) => rows.map(normalizeAgent)),
  listPrompts: (name: string) =>
    api.get<PromptVersion[]>(`/agents/${name}/prompts`),
  sync: () => api.post<SyncPromptsResult>('/agents/sync', {}),
};

// -------------------------------------------------------------- quality
// 对应 packages/core/api/routers/quality.py：
//   GET /chapters/{cid}/quality           —— 最新一份；404 表示该 chapter 还没有 report
//   POST /chapters/{cid}/quality/evaluate —— 现场组装 ctx + 评估 + 落库（201）
//   GET /projects/{pid}/quality           —— 项目全部 quality_reports（created_at DESC）
export const qualityApi = {
  latest: (cid: string) => api.get<QualityReport>(`/chapters/${cid}/quality`),
  evaluate: (cid: string) =>
    api.post<QualityReport>(`/chapters/${cid}/quality/evaluate`, {}),
  listByProject: (pid: string) =>
    api.get<QualityReport[]>(`/projects/${pid}/quality`),

  /**
   * 人工评分（`POST /chapters/{cid}/quality/judge`，body 是裸 dict，无 schema）。
   *
   * ⚠️ **它只写 `quality_reports.judge_json` 一列，不写新行、不进任何门禁**：
   *   `workflows/chapter_commit/gate.py` 与 `core/quality/engine.py` 零引用 judge，
   *   它**不影响** `overall` / `scores_json` / `scoring_formula_hash`。
   *   ⇒ UI 必须如实写明「这是你的记录，不会解除门禁」，否则作者会以为打完分就能提交。
   * ⚠️ **必须先跑过 evaluate**：judge 只能挂在**已存在的最新报告行**上，
   *   没有报告行时后端 422（「先跑 evaluate 再落 judge」）。
   */
  judge: (cid: string, payload: QualityJudgePayload) =>
    api.post<QualityJudgeResponse>(`/chapters/${cid}/quality/judge`, payload),
};

// -------------------------------------------------------------- hooks / debts
// 对应 packages/core/api/routers/ledger.py：
//   POST/GET   /projects/{pid}/hooks           创建 / 列表（?status= 过滤）
//   GET/PATCH/DELETE /hooks/{id}               详情 / 更新 / 删除
//   POST/GET   /projects/{pid}/debts           创建 / 列表
//   GET/PATCH/DELETE /debts/{id}               详情 / 更新 / 删除
export const hooksApi = {
  listByProject: (pid: string, query?: { status?: string }) =>
    api.get<Hook[]>(`/projects/${pid}/hooks`, query),
  create: (pid: string, payload: HookCreatePayload) =>
    api.post<Hook>(`/projects/${pid}/hooks`, payload),
  get: (id: string) => api.get<Hook>(`/hooks/${id}`),
  update: (id: string, payload: HookUpdatePayload) =>
    api.patch<Hook>(`/hooks/${id}`, payload),
  delete: (id: string) => api.delete<void>(`/hooks/${id}`),
};

export const debtsApi = {
  listByProject: (pid: string, query?: { status?: string }) =>
    api.get<Debt[]>(`/projects/${pid}/debts`, query),
  create: (pid: string, payload: DebtCreatePayload) =>
    api.post<Debt>(`/projects/${pid}/debts`, payload),
  get: (id: string) => api.get<Debt>(`/debts/${id}`),
  update: (id: string, payload: DebtUpdatePayload) =>
    api.patch<Debt>(`/debts/${id}`, payload),
  delete: (id: string) => api.delete<void>(`/debts/${id}`),
};

// -------------------------------------------------------------- reference canon
// 对应 packages/core/api/routers/reference.py：
//   POST   /projects/{pid}/deconstruct         —— 启动拆书（同步执行到底，返回完整 status）
//   POST   /projects/{pid}/deconstruct-upload  —— 上传 .txt/.epub 启动拆书（multipart/form-data）
//   GET    /projects/{pid}/canons              —— 列表 active canon 摘要
//   GET    /canons/{canon_id}                  —— 全文 + report_md + extracts
//   DELETE /canons/{canon_id}                  —— 级联删除（204）
//   POST   /projects/{pid}/canons/{canon_id}/to-style-sample
//                                           —— 合成文风样例卡并落入 author_style_samples
//
// 设计要点：
// - deconstruct 是同步长任务（无 Human 节点），无需轮询；返回 canon_id 时直接刷新列表。
// - 失败 / FAILED：响应含 error 字段；前端展示 ErrorBanner。
// - to-style-sample：服务端按 style_params/techniques 合成风格卡；同标题二次写入 → 409。
// - uploadDeconstruct 走原生 fetch + FormData：client.ts 默认
//   Content-Type: application/json + JSON.stringify(body) 不兼容 multipart；
//   这里直接构造 FormData，浏览器自动设置 boundary，credentials 与 api.* 同源策略对齐。
export const referenceApi = {
  deconstruct: (pid: string, payload: DeconstructPayload) =>
    api.post<DeconstructStartResponse>(`/projects/${pid}/deconstruct`, payload),
  // 上传 .txt / .epub 文件启动拆书；服务端按扩展名分发：txt utf-8→gb18030、epub stdlib 解析。
  // bookTitle / readerProfile 可省略；epub 时服务端会从 dc:title 兜底。
  uploadDeconstruct: async (
    pid: string,
    file: File,
    options?: { book_title?: string; reader_profile?: ReaderProfile },
  ): Promise<DeconstructStartResponse> => {
    const fd = new FormData();
    fd.append('file', file);
    if (options?.book_title) fd.append('book_title', options.book_title);
    if (options?.reader_profile) fd.append('reader_profile', options.reader_profile);
    const { ApiError } = await import('./client');
    let resp: Response;
    try {
      // F9 修复：网络层失败（断网 / CORS / 服务端进程死亡）捕获后包装成
      // ``new ApiError(0, msg)``——与 client.request 的错误归一一致（client.ts
      // 在 fetch reject 时也走 ``new ApiError(0, msg)`` 路径）。**不**裸抛
      // TypeError，避免 UI 端 catch 路径需要额外分支处理。
      resp = await fetch(
        `/api/projects/${pid}/deconstruct-upload`,
        {
          method: 'POST',
          body: fd,
          credentials: 'same-origin',
        },
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'network error';
      throw new ApiError(0, msg);
    }
    // 与 client.request 的错误归一一致：非 2xx 抛 ApiError{status, detail}。
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      let detail = resp.statusText || `HTTP ${resp.status}`;
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
          const d = (parsed as { detail: unknown }).detail;
          detail = typeof d === 'string' ? d : JSON.stringify(d);
        }
      } catch {
        detail = text || detail;
      }
      throw new ApiError(resp.status, detail);
    }
    return (await resp.json()) as DeconstructStartResponse;
  },
  listCanons: (pid: string) =>
    api.get<CanonSummary[]>(`/projects/${pid}/canons`),
  getCanon: (canonId: string) => api.get<CanonDetail>(`/canons/${canonId}`),
  deleteCanon: (canonId: string) => api.delete<void>(`/canons/${canonId}`),
  // 文风样例合成：确定性格式化合成（不调 LLM），复用 author_style_samples 校验。
  writeToStyleSample: (pid: string, canonId: string) =>
    api.post<StyleSample>(
      `/projects/${pid}/canons/${canonId}/to-style-sample`,
      {},
    ),
};

// -------------------------------------------------------------- genre packs（题材库）
// 对应 packages/core/api/routers/genre.py：
//   GET    /genre-packs                        —— 列表摘要（?genre_tag= 精确过滤）
//   GET    /genre-packs/{pack_id}              —— 详情（payload 已解析为对象）
//   GET    /projects/{pid}/genre-pack          —— 项目当前绑定（含 pack 全文）
//   POST   /projects/{pid}/genre-pack/bind     —— 绑定（单 slot，覆盖式；404 = 项目/pack 不存在）
//   POST   /projects/{pid}/genre-pack/unbind   —— 解绑（幂等：未绑定也 200，bound=false）
// 设计要点：
// - 绑定是**项目单 slot**（与 canon 叠加参照双 slot 并存，互不覆盖）；
// - 绑定响应已是「绑定详情」，调用方拿到后无需二次 GET 即可刷新面板。
export const genreApi = {
  listPacks: (genreTag?: string) =>
    api.get<GenrePackSummary[]>('/genre-packs', { genre_tag: genreTag }),
  getPack: (packId: string) => api.get<GenrePack>(`/genre-packs/${packId}`),
  getBinding: (pid: string) =>
    api.get<GenreBinding>(`/projects/${pid}/genre-pack`),
  bind: (pid: string, packId: string) =>
    api.post<GenreBinding>(`/projects/${pid}/genre-pack/bind`, {
      pack_id: packId,
    }),
  // 解绑响应固定为 {project_id, pack_id: null, bound: false, pack: null}；
  // 后端只在项目不存在时 404。
  unbind: (pid: string) =>
    api.post<GenreBinding>(`/projects/${pid}/genre-pack/unbind`, {}),
};

// -------------------------------------------------------------- context preview
// 对应 packages/core/api/routers/workflows.py：
//   GET /chapters/{cid}/context-preview  —— dry-run，返回分层 token + items
//   只读、不调 LLM、不写 ai_call_logs。
export const contextPreviewApi = {
  preview: (cid: string) =>
    api.get<ContextPreviewResponse>(`/chapters/${cid}/context-preview`),
};

// -------------------------------------------------------------- AI call logs
// 对应 packages/core/api/routers/ai_call_logs.py：
//   GET  /ai-call-logs        —— 分页摘要（?project_id= &node= &limit= &offset=）
//   GET  /ai-call-logs/{id}   —— 单条详情（含 input_context_ids + output）
// 不暴露任何 api_key 字段（后端 schema + 路由白名单 + 前端类型三重防御）。
function normalizeAiLogSummary(row: AiCallLogSummary): AiCallLogSummary {
  return {
    ...row,
    token_usage: (coerceJson(row.token_usage) as AiCallLogTokenUsage | null) ?? null,
  };
}
function normalizeAiLogDetail(row: AiCallLogDetail): AiCallLogDetail {
  return {
    ...normalizeAiLogSummary(row),
    input_context_ids: Array.isArray(row.input_context_ids) ? row.input_context_ids : [],
    output:
      (coerceJson(row.output) as
        | Record<string, unknown>
        | unknown[]
        | string
        | number
        | boolean
        | null) ?? null,
  };
}
export const aiCallLogsApi = {
  list: (query?: { project_id?: string; node?: string; limit?: number; offset?: number }) =>
    api.get<AiCallLogSummary[]>('/ai-call-logs', query).then((rows) =>
      rows.map(normalizeAiLogSummary),
    ),
  get: (id: string) =>
    api.get<AiCallLogDetail>(`/ai-call-logs/${id}`).then(normalizeAiLogDetail),
};

// -------------------------------------------------------------- project backup
// 对应 packages/core/api/routers/backup.py（V1.4 Sprint 16 / MVP）：
//   GET  /projects/{pid}/backup      —— 下载 JSON 包（含 Content-Disposition attachment）
//   POST /projects/import-backup     —— 接收 JSON 包，导入为新项目（返回新 Project）
//
// 设计要点：
// - downloadBackup 直接用浏览器 fetch + Blob 触发下载；后端已经在响应头放好
//   filename，由 <a download> 兜底兼容。
// - importBackup 直接 POST JSON；服务端校验 format/version/表白名单；坏包 422。
export const backupApi = {
  /** 触发浏览器下载项目备份 JSON；后端响应头含 Content-Disposition。 */
  downloadBackup: async (projectId: string): Promise<void> => {
    const resp = await fetch(
      `/api/projects/${projectId}/backup`,
      { method: 'GET', credentials: 'same-origin' },
    );
    if (!resp.ok) {
      // 解析错误 detail 抛出 ApiError 与 api.* 风格一致
      const text = await resp.text().catch(() => '');
      let detail = resp.statusText || `HTTP ${resp.status}`;
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
          const d = (parsed as { detail: unknown }).detail;
          detail = typeof d === 'string' ? d : JSON.stringify(d);
        }
      } catch {
        detail = text || detail;
      }
      const { ApiError } = await import('./client');
      throw new ApiError(resp.status, detail);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `backup-${projectId}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // 下载触发后立即释放对象 URL（全站下载路径统一时机，见 ExportPanel）。
    URL.revokeObjectURL(url);
  },

  /** 导入备份包；返回新项目 dict。坏 format/version/表名 → 422 由后端转 ApiError。 */
  importBackup: (payload: BackupPackage): Promise<Project> =>
    api.post<Project>('/projects/import-backup', payload),
};

// -------------------------------------------------------------- export (V1.4 / Sprint 16)
// 对应 packages/core/api/routers/export.py：
//   GET /projects/{pid}/export?format={txt|docx|fanqie}[&chapter_no=...]
// 仅暴露类型契约；实际下载由 ExportPanel 直接 fetch + blob 触发。
export type ExportFormat = 'txt' | 'docx' | 'fanqie';

export interface ExportQuery {
  format: ExportFormat;
  chapter_no?: number;
}

export const exportApi = {
  /** 拼下载 URL（不发起请求；ExportPanel 用 fetch + blob 下载）。 */
  url(projectId: string, query: ExportQuery): string {
    const params = new URLSearchParams({ format: query.format });
    if (query.format !== 'fanqie' && query.chapter_no !== undefined) {
      params.set('chapter_no', String(query.chapter_no));
    }
    return `/api/projects/${projectId}/export?${params.toString()}`;
  },
};

// -------------------------------------------------------------- branches (V1.5 / Sprint 17)
// 对应 packages/core/api/routers/story_state.py：
//   GET    /projects/{pid}/branches                       —— 列分支（main 优先，其余 ASC）
//   POST   /projects/{pid}/branches                       —— 创建分支（{name, base_state_version?}）
//   POST   /projects/{pid}/branches/{bid}/promote         —— 分支 promote（{chapter_id?}）
//
// 错误码（直接走 ApiError 由 UI 处理）：
// - 422: name 为空 / base_state_version 非法
// - 409: branch_name_conflict / branch_closed / promote_conflict / optimistic_lock / approval_required
// - 404: project / branch 不存在
//
// 设计要点：
// - promote 走 POST body {chapter_id?: string}；缺省由后端用首个分支 commit 的 chapter_id 兜底。
// - 不在 endpoints 层封装"获取分支 state"——面板直接复用 storyStateApi.getBranchState。
export const branchesApi = {
  list: (pid: string) => api.get<Branch[]>(`/projects/${pid}/branches`),
  create: (pid: string, payload: BranchCreatePayload) =>
    api.post<Branch>(`/projects/${pid}/branches`, payload),
  promote: (
    pid: string,
    branchId: string,
    payload: { chapter_id?: string | null } = {},
  ) => api.post<BranchPromoteResult>(
    `/projects/${pid}/branches/${branchId}/promote`,
    payload,
  ),
};

// -------------------------------------------------------------- continuation
// 对应后端续写助手端点：
//   POST /projects/{pid}/chapters/{cid}/continue        —— 生成 N 个续写候选
//   POST /projects/{pid}/chapters/{cid}/continue/adopt  —— 采纳其一追加为新草稿
// 错误码统一走 ApiError：404 / 409 / 422 / 502；UI 在面板层用 ErrorBanner 呈现。
export const continuationApi = {
  generate: (pid: string, cid: string, payload: ContinueRequestPayload = {}) =>
    api.post<ContinueResponse>(
      `/projects/${pid}/chapters/${cid}/continue`,
      payload,
    ),
  adopt: (pid: string, cid: string, payload: ContinueAdoptPayload) =>
    api.post<ContinueAdoptResponse>(
      `/projects/${pid}/chapters/${cid}/continue/adopt`,
      payload,
    ),
};

// -------------------------------------------------------------- 交付判定 + 签约自检
/**
 * 交付判定与签约自检（评估 G6：schema 早已生成、UI 不接）。
 *
 * 两者**都是纯只读 GET**，且 signing-check 是 delivery-verdict 的证据源之一
 * （`roll_up` 与逐章 `signing` 都引它），因此放在一起、共用一次说明文案。
 *
 * ⚠️ 判定是**派生视图**：每次 GET 现算，不落库。UI 不得把它当成可编辑状态，
 * 也**不得**在本地缓存跨会话复用（重复测量会给出不同数字）。
 */
export const deliveryApi = {
  verdict: (pid: string) =>
    api.get<DeliveryVerdictResponse>(`/projects/${pid}/delivery-verdict`),
};

export const signingCheckApi = {
  run: (pid: string) => api.get<SigningCheckResult>(`/projects/${pid}/signing-check`),
};

// ============================================================================
// 2026-10-15 批次（四）：G6 挂账 5 条的端点封装
// ============================================================================

// ------------------------------------------------------------ 剧透策略
// 对应 `packages/core/api/routers/reveal_policies.py`（body 是裸 dict，无 schema）。
//
// ⚠️ **后端两处反直觉行为，UI 必须按它写，不能按「应该是」写**：
//   1. 列表的查询参数叫 **`status_filter`**，不是 `status`（router docstring 写的是
//      `?status=`，代码用的是 `status_filter`——**以代码为准**）。
//   2. `PATCH` / `DELETE` **完全忽略路径里的 `pid`**，只按 `policy_id` 单查
//      ⇒ A 项目的策略能经 B 项目的 URL 被改掉/删掉。本仓 UI 只在项目内操作，
//      但**不要在 UI 里写「已确认策略属于本项目」这类断言**——后端没这层保证。
//   3. 项目不存在时是 **422**（不是 404），router docstring 与实现不符。
export const revealPoliciesApi = {
  list: (pid: string, statusFilter?: RevealPolicyStatus) =>
    api.get<RevealPolicy[]>(
      `/projects/${pid}/reveal-policies`,
      statusFilter ? { status_filter: statusFilter } : undefined,
    ),
  create: (pid: string, payload: RevealPolicyCreate) =>
    api.post<RevealPolicy>(`/projects/${pid}/reveal-policies`, payload),
  update: (pid: string, policyId: string, payload: RevealPolicyUpdate) =>
    api.patch<RevealPolicy>(`/projects/${pid}/reveal-policies/${policyId}`, payload),
  remove: (pid: string, policyId: string) =>
    api.delete<void>(`/projects/${pid}/reveal-policies/${policyId}`),
};

// ---------------------------------------------------------------- 弧光
// 对应 `packages/core/api/routers/arc.py` 单个 GET。
// ⚠️ 无 `response_model` ⇒ 不在 OpenAPI 里，类型是手写的（派生来源见 types.ts 注释）。
// ⚠️ `chapters[]` 里**没有 chapter_id**（后端显式 pop 掉）——不要拿它当行键。
export const arcApi = {
  get: (pid: string) => api.get<ArcView>(`/projects/${pid}/arc`),
};

// ---------------------------------------------------------------- 推演
// 对应 `packages/core/api/routers/simulation.py`。
//
// ⚠️ **不是只读**：每次 simulate 会留一条 `branches` 归档行 + 每 delta 一条
// `state_deltas` + 每 delta 一条 `commits`（供重放）。main 快照与领域表全程不动
// （`skip_all=True`，见 core/simulation/README.md）。
// ⚠️ 失败也留残骸（已应用的部分 + 归档分支），**同名重跑会 409**。
// ⚠️ HIGH 风险变更**绕过审批门**（`_skip_approval=True`），留痕标记
//    `author_approval.status='simulation_bypassed'`。UI 必须如实告知作者。
export const simulationApi = {
  run: (pid: string, deltas: unknown[], name?: string) =>
    api.post<SimulationResult>(`/projects/${pid}/simulate`, {
      deltas,
      ...(name ? { name } : {}),
    }),
  list: (pid: string) => api.get<SimulationSummary[]>(`/projects/${pid}/simulations`),
  get: (pid: string, simulationId: string) =>
    api.get<SimulationResult>(`/projects/${pid}/simulations/${simulationId}`),
};
