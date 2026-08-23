// 各业务端点的薄封装。返回值已统一经 ApiError/coerceJson 处理。

import { api } from './client';
import { coerceJson } from './client';
import type {
  Agent,
  Chapter,
  ChapterCreatePayload,
  ChapterUpdatePayload,
  Character,
  CharacterCreatePayload,
  CharacterState,
  CharacterUpdatePayload,
  Commit,
  Debt,
  DebtCreatePayload,
  DebtUpdatePayload,
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
// enabled。params_json 在 DB 中存为 JSON 字符串，统一在此解析为对象便于前端表单读写。
function normalizeModelConfig(row: ModelConfig): ModelConfig {
  return {
    ...row,
    params_json: (coerceJson(row.params_json) as Record<string, unknown>) ?? {},
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
