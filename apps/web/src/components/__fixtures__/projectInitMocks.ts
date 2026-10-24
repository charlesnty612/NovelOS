// ProjectInitPanel 测试的**共用夹具 + 共用 mock 设定**。
//
// 由来：2026-10-23 前端测试瘦身批次（task-8）。原 `ProjectInitPanel.test.tsx`
// 单文件 2381 行 / 39 例，拆成三份（submit / review / cards）后，三份都要用同一套
// 「端点 mock 形状 + 基础项目夹具 + 各关卡 draft 夹具 + beforeEach 设定」。
// 放这里 = 只写一份，三份 import。
//
// ⚠️ 为什么 `vi.mock('../api/endpoints', ...)` **不在**本文件里：
//   vitest 的 `vi.mock` 按**调用它的那个测试文件的模块图**生效。写在被 import 的
//   模块里不会拦截调用方的 `../api/endpoints` ⇒ 三份测试文件各自保留自己的
//   `vi.mock` 工厂（工厂内部通过 `await import()` 调用本文件的 builder，
//   故**内容逐字相同**、只有一处定义）。
//
// ⚠️ 本文件**不**在运行期 import `../api/endpoints`（只用 `typeof import(...)`
//   取类型）—— 否则 `vi.mock` 工厂里 `await import(本文件)` 会与被 mock 的模块
//   形成环。测试文件把已 mock 的端点对象**传进来**（`setupProjectInitMocks`）。

import { vi } from 'vitest';
import type {
  CapabilityBinding,
  ModelProfile,
  Project,
} from '../../api/types';

/** 真实端点模块的类型（纯类型用法 ⇒ 无运行期 import） */
type Endpoints = typeof import('../../api/endpoints');

// ---------------------------------------------------------------------------
// 端点 mock 形状（供三份测试文件的 vi.mock 工厂调用）
// ---------------------------------------------------------------------------

/**
 * 造 `../api/endpoints` 的 mock 模块。
 *
 * 与原 `ProjectInitPanel.test.tsx` 里的工厂**逐字等价**（同样的 spread + 同样的
 * 覆写集合）：只把 `projectsApi.init/initStatus`、`charactersApi.listByProject`、
 * `workflowsApi.resumeInit/resume/listByProject/get`、
 * `capabilityBindingsApi.list/bind/unbind`、`modelProfilesApi.list` 换成 `vi.fn()`，
 * 其余端点沿用真实实现。
 */
export function buildProjectInitEndpointsMock(actual: Endpoints): Endpoints {
  return {
    ...actual,
    projectsApi: {
      ...((actual.projectsApi as unknown) as object),
      init: vi.fn(),
      initStatus: vi.fn(),
    },
    charactersApi: {
      ...((actual.charactersApi as unknown) as object),
      listByProject: vi.fn(),
    },
    workflowsApi: {
      ...((actual.workflowsApi as unknown) as object),
      resumeInit: vi.fn(),
      resume: vi.fn(),
      listByProject: vi.fn(),
      get: vi.fn(),
    },
    capabilityBindingsApi: {
      ...((actual.capabilityBindingsApi as unknown) as object),
      list: vi.fn(),
      bind: vi.fn(),
      unbind: vi.fn(),
    },
    modelProfilesApi: {
      ...((actual.modelProfilesApi as unknown) as object),
      list: vi.fn(),
    },
  } as unknown as Endpoints;
}

// ---------------------------------------------------------------------------
// 基础项目夹具
// ---------------------------------------------------------------------------

export const baseProject: Project = {
  project_id: 'prj_1',
  name: '九天神诀',
  premise: null,
  genre: '玄幻',
  target_words: 300000,
  status: 'ACTIVE',
  created_at: '2026-08-27T00:00:00Z',
  updated_at: '2026-08-27T00:00:00Z',
};

// ---------------------------------------------------------------------------
// 各关卡 draft 夹具（review 视图 pause_payload.draft 用）
// ---------------------------------------------------------------------------

export const premiseDraft = {
  title: '九天神诀',
  genre: '玄幻',
  logline: '少年得古籍，逆天改命',
  positioning: '无敌爽文',
  selling_points: ['节奏快', '升级爽'],
  protagonist: { name: '林动', age: 16 },
};

export const worldDraft = {
  core_premise: '武道为尊，强者通神',
  rules: [{ id: 'r1', statement: '灵气可修炼' }],
  locations: [],
  factions: [],
};

export const outlineDraft = {
  volume: { number: 1, title: '初入宗门', arc_summary: '少年成长' },
  chapter_seeds: [
    { number: 1, title: '觉醒', role: 'intro', one_sentence: 's', expected_word_count: 3000, key_beats: [] },
  ],
};

export const characterDraft = {
  characters: [
    {
      name: '林动',
      role: 'protagonist',
      core_json: {
        motivation: '复仇',
        goal: '登顶',
        conflict: '资源匮乏',
        distinctive_trait: '沉稳',
        relationships: [],
      },
    },
  ],
};

// ---------------------------------------------------------------------------
// 共用 beforeEach 设定
// ---------------------------------------------------------------------------

/** 三份测试文件传入的端点集合（各自 vi.mock 后 import 到的同一批 mock 实例） */
export interface ProjectInitMockApis {
  projectsApi: Endpoints['projectsApi'];
  charactersApi: Endpoints['charactersApi'];
  workflowsApi: Endpoints['workflowsApi'];
  capabilityBindingsApi: Endpoints['capabilityBindingsApi'];
  modelProfilesApi: Endpoints['modelProfilesApi'];
}

/**
 * 三份测试文件共用的「每例前重置 + 默认值」设定。
 *
 * 逐字等价于原 `ProjectInitPanel.test.tsx` 的 beforeEach 块：同样的重置集合、
 * 同样的默认值、同样清 sessionStorage、同样把轮询间隔压到 1ms。
 */
export function setupProjectInitMocks(api: ProjectInitMockApis): void {
  const {
    projectsApi,
    charactersApi,
    workflowsApi,
    capabilityBindingsApi,
    modelProfilesApi,
  } = api;
  vi.mocked(projectsApi.init).mockReset();
  vi.mocked(projectsApi.initStatus).mockReset();
  vi.mocked(charactersApi.listByProject).mockReset();
  vi.mocked(workflowsApi.resumeInit).mockReset();
  vi.mocked(workflowsApi.listByProject).mockReset();
  vi.mocked(workflowsApi.get).mockReset();
  vi.mocked(capabilityBindingsApi.list).mockReset();
  vi.mocked(capabilityBindingsApi.bind).mockReset();
  vi.mocked(capabilityBindingsApi.unbind).mockReset();
  vi.mocked(modelProfilesApi.list).mockReset();
  // 默认探测：空角色
  vi.mocked(charactersApi.listByProject).mockResolvedValue([]);
  // 默认探测：无挂起 run（具体用例按需覆盖）
  vi.mocked(workflowsApi.listByProject).mockResolvedValue([]);
  // 默认 bindings / profiles：空返回，组件会按空态渲染（不抛错）
  vi.mocked(capabilityBindingsApi.list).mockResolvedValue([] as CapabilityBinding[]);
  vi.mocked(modelProfilesApi.list).mockResolvedValue([] as ModelProfile[]);
  vi.mocked(capabilityBindingsApi.bind).mockImplementation(async (cap, ids) => ({
    capability: cap,
    label: cap,
    agents: [],
    profile_ids: ids,
    profiles: ids.map((id) => ({ profile_id: id, name: id, model: id })),
    legacy_available: false,
    thinking_override: null,
  }));
  vi.mocked(capabilityBindingsApi.unbind).mockResolvedValue(undefined as never);
  // 默认 init-status：失败（默认 fallback 行为：全部默认勾选且不强制禁用），
  // 具体 init-status 相关用例按需覆盖。
  vi.mocked(projectsApi.initStatus).mockRejectedValue(new Error('init-status not mocked'));
  // 清理 sessionStorage（避免跨用例残留）
  sessionStorage.clear();
  // 单测中把 poll 间隔压到 1ms，避免 5s 默认超时下被 2000ms 间隔卡住。
  (window as unknown as { __novelosPollIntervalMs?: number }).__novelosPollIntervalMs = 1;
  (window as unknown as { __novelosPollTimeoutMs?: number }).__novelosPollTimeoutMs = 5000;
}
