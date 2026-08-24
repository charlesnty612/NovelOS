// 各业务端点的薄封装。返回值已统一经 ApiError/coerceJson 处理。

import { api } from './client';
import { coerceJson } from './client';
import type {
  Agent,
  AiCallLogDetail,
  AiCallLogSummary,
  AiCallLogTokenUsage,
  BackupPackage,
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
  ContextPreviewResponse,
  Debt,
  DebtCreatePayload,
  DebtUpdatePayload,
  DeconstructPayload,
  DeconstructStartResponse,
  Draft,
  DraftCreatePayload,
  HealthResponse,
  Hook,
  HookCreatePayload,
  HookUpdatePayload,
  ModelConfig,
  ModelConfigCreatePayload,
  ModelConfigTestResult,
  ModelConfigUpdatePayload,
  PlotEvent,
  PlotEventCreatePayload,
  Project,
  ProjectCreatePayload,
  ProjectUpdatePayload,
  PromptVersion,
  QualityReport,
  ResumeRequestPayload,
  SnapshotResponse,
  StyleSample,
  StyleSampleCreatePayload,
  SyncPromptsResult,
  TimelineEvent,
  WorldEntity,
  WorldEntityPayload,
  WorkflowRun,
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
    cause: coerceJson(row.cause) as Record<string, unknown> | null,
    effects: coerceJson(row.effects) as Record<string, unknown> | null,
    time: coerceJson(row.time) as Record<string, unknown> | null,
  };
}

export const timelineApi = {
  list: (pid: string) => api.get<TimelineEvent[]>(`/projects/${pid}/timeline`),
};

// -------------------------------------------------------------- state / commits / health
export const storyStateApi = {
  getCurrent: (pid: string) =>
    api.get<SnapshotResponse>(`/projects/${pid}/state`).then((snap) => {
      const normArr = (v: unknown): unknown[] => {
        const x = coerceJson(v);
        return Array.isArray(x) ? x : [];
      };
      const normObj = (v: unknown): Record<string, unknown> => {
        const x = coerceJson(v);
        if (x && typeof x === 'object' && !Array.isArray(x)) {
          return x as Record<string, unknown>;
        }
        return {};
      };
      return {
        ...snap,
        characters: normObj(snap.characters),
        world: normObj(snap.world),
        hooks: normArr(snap.hooks),
        debts: normArr(snap.debts),
        events: normArr(snap.events),
        recent_events: normArr(snap.recent_events),
      };
    }),
};

export const commitsApi = {
  list: (pid: string) => api.get<Commit[]>(`/projects/${pid}/commits`),
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
  resume: (runId: string, payload: ResumeRequestPayload) =>
    api.post<WorkflowStartResponse>(`/runs/${runId}/resume`, payload),
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
//   POST   /projects/{pid}/deconstruct    —— 启动拆书（同步执行到底，返回完整 status）
//   GET    /projects/{pid}/canons         —— 列表 active canon 摘要
//   GET    /canons/{canon_id}             —— 全文 + report_md + extracts
//   DELETE /canons/{canon_id}             —— 级联删除（204）
//
// 设计要点：
// - deconstruct 是同步长任务（无 Human 节点），无需轮询；返回 canon_id 时直接刷新列表。
// - 失败 / FAILED：响应含 error 字段；前端展示 ErrorBanner。
export const referenceApi = {
  deconstruct: (pid: string, payload: DeconstructPayload) =>
    api.post<DeconstructStartResponse>(`/projects/${pid}/deconstruct`, payload),
  listCanons: (pid: string) =>
    api.get<CanonSummary[]>(`/projects/${pid}/canons`),
  getCanon: (canonId: string) => api.get<CanonDetail>(`/canons/${canonId}`),
  deleteCanon: (canonId: string) => api.delete<void>(`/canons/${canonId}`),
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
