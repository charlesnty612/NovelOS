import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ProjectOverviewPage } from './ProjectOverviewPage';
import {
  branchesApi,
  chaptersApi,
  commitsApi,
  deliveryApi,
  healthApi,
  projectsApi,
  signingCheckApi,
  storyStateApi,
  styleSamplesApi,
} from '../api/endpoints';
import type {
  Chapter,
  Commit,
  HealthResponse,
  Project,
  StyleSample,
} from '../api/types';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    projectsApi: { list: vi.fn(), create: vi.fn(), get: vi.fn(), update: vi.fn(), delete: vi.fn() },
    healthApi: { get: vi.fn() },
    storyStateApi: { getCurrent: vi.fn() },
    commitsApi: { list: vi.fn() },
    styleSamplesApi: { list: vi.fn() },
    branchesApi: { list: vi.fn() },
    chaptersApi: { listByProject: vi.fn() },
    // G6：DeliveryPanel 挂在总览页上。**必须显式 mock**——漏了会落到 `...actual`
    // 的真实 api.get，面板停在错误态，而总览页用例照样全绿（没人在那儿断言）。
    // 那就是「量具没量到目标，却报告成量过了」。
    deliveryApi: { verdict: vi.fn() },
    signingCheckApi: { run: vi.fn() },
  };
});

const baseProject: Project = {
  project_id: 'prj_001',
  name: '测试项目',
  premise: null,
  genre: '科幻',
  target_words: 100_000,
  status: 'ACTIVE',
  created_at: '2026-08-24T10:00:00Z',
  updated_at: '2026-08-24T10:00:00Z',
};
const baseHealth: HealthResponse = { status: 'ok', version: '3.9.0', tables: {} };
const baseSnapshot = {
  state_version: 7,
  // 形状照抄后端 `core/story_state/models.py::SnapshotResponse`；
  // 旧夹具的 `version: 7` + `characters: {}` + `events: []` 是**欠定义类型 + 旧归一化
  // 层的产物**（后端只写 state_version，且 characters 是 list、events 是 dict），
  // 照抄它等于把「角色/事件恒 0」钉成期望值。
  characters: [{ character_id: 'chr_1', name: '林昭' }],
  world: { locations: {}, factions: {}, world_rules: [] },
  hooks: [{ hook_id: 'hk_1', name: '三年之约', status: 'ACTIVE' }],
  debts: [{ debt_id: 'db_1', description: '欠下的人情', status: 'open' }],
  events: { evt_1: { event_id: 'evt_1' }, evt_2: { event_id: 'evt_2' } },
  recent_events: [{ event_id: 'evt_2' }],
};
const baseCommits: Commit[] = [];
const baseChapters: Chapter[] = [
  { chapter_id: 'ch_1', project_id: 'prj_001', number: 1, title: '一', plan_json: {}, status: 'COMMITTED', visibility: 'public', who_knows: null, created_at: '', updated_at: '' },
  { chapter_id: 'ch_2', project_id: 'prj_001', number: 2, title: '二', plan_json: {}, status: 'DRAFTED', visibility: 'public', who_knows: null, created_at: '', updated_at: '' },
];
const baseStyles: StyleSample[] = [];
const baseBranches: Awaited<ReturnType<typeof branchesApi.list>> = [];

function renderPage(pid = 'prj_001') {
  return render(
    <MemoryRouter initialEntries={[`/projects/${pid}/overview`]}>
      <Routes>
        <Route path="projects/:pid/overview" element={<ProjectOverviewPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ProjectOverviewPage 加载态与人话化文案', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.get).mockReset();
    vi.mocked(healthApi.get).mockReset();
    vi.mocked(storyStateApi.getCurrent).mockReset();
    vi.mocked(commitsApi.list).mockReset();
    vi.mocked(styleSamplesApi.list).mockReset();
    vi.mocked(branchesApi.list).mockReset();
    vi.mocked(chaptersApi.listByProject).mockReset();
    vi.mocked(deliveryApi.verdict).mockReset();
    vi.mocked(signingCheckApi.run).mockReset();

    vi.mocked(healthApi.get).mockResolvedValue(baseHealth);
    vi.mocked(storyStateApi.getCurrent).mockResolvedValue(baseSnapshot);
    vi.mocked(commitsApi.list).mockResolvedValue(baseCommits);
    vi.mocked(styleSamplesApi.list).mockResolvedValue(baseStyles);
    vi.mocked(branchesApi.list).mockResolvedValue(baseBranches);
    vi.mocked(chaptersApi.listByProject).mockResolvedValue(baseChapters);
    vi.mocked(deliveryApi.verdict).mockResolvedValue({
      project_id: 'prj_001',
      project_name: '测试项目',
      generated_at: '2026-10-15T08:00:00Z',
      project_verdict: 'not_a_candidate',
      roll_up: {
        chapter_count: 0,
        written_chapter_count: 0,
        deliverable_count: 0,
        needs_work_count: 0,
        not_deliverable_count: 0,
        not_a_candidate_count: 0,
        deliverable_chapter_ids: [],
        needs_work_chapter_ids: [],
        not_deliverable_chapter_ids: [],
        not_a_candidate_chapter_ids: [],
      },
      evidence_sources: [],
      chapters: [],
    });
    vi.mocked(signingCheckApi.run).mockResolvedValue({
      project_id: 'prj_001',
      project_name: '测试项目',
      total_chars: 0,
      generated_at: '2026-10-15T08:00:00Z',
      items: [],
      summary: { pass_count: 0, warn_count: 0, fail_count: 0, info_count: 0 },
      word_band_source: 'default',
    });
  });

  it('项目未返回时渲染骨架占位，而不是整页空白', () => {
    vi.mocked(projectsApi.get).mockReturnValue(new Promise<Project>(() => {}));
    renderPage();

    expect(screen.getByTestId('overview-loading')).toBeInTheDocument();
    expect(screen.getByText('正在加载项目总览…')).toBeInTheDocument();
  });

  it('数据库卡片按人话展示表数（后端 tables 为数量，不再渲染空表行数列表）', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    // 后端 /api/health 返回的 tables 是数量（int），与前端类型标注的 Record 形状不同
    vi.mocked(healthApi.get).mockResolvedValue({
      status: 'ok',
      version: '3.9.0',
      tables: 38,
    } as unknown as HealthResponse);

    renderPage();
    const healthCard = (await screen.findByText('数据库状态')).closest('.card')!;
    expect(await within(healthCard as HTMLElement).findByText(/数据库 · 38 张表/)).toBeInTheDocument();
    expect(within(healthCard as HTMLElement).getByText('正常')).toBeInTheDocument();
    expect(within(healthCard as HTMLElement).queryByText('表行数')).not.toBeInTheDocument();
  });

  it('聚合区展示状态版本、进度与中文指标名', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    renderPage();

    const card = (await screen.findByText('这本书现在什么状态')).closest('.card')!;
    const scope = within(card as HTMLElement);
    expect(await scope.findByText(/当前故事状态版本：v7/)).toBeInTheDocument();
    expect(scope.getByText('未回收伏笔')).toBeInTheDocument();
    expect(scope.getByText('未偿清债务')).toBeInTheDocument();
    expect(scope.getByText('角色')).toBeInTheDocument();
    expect(scope.getByText('事件')).toBeInTheDocument();
    expect(scope.queryByText('Characters')).not.toBeInTheDocument();
    expect(scope.queryByText(/state_version 来自/)).not.toBeInTheDocument();
  });

  // ---- G10 判别：断言**数值**，不是标签存在 ----
  //
  // 旧用例只断言「角色/伏笔/债务/事件 这些词存在」，从不断言读数，于是
  // 「角色恒 0」「事件·累计恒 0」两个 bug 可以全程绿灯——这是本仓坑区
  // 「断言了结果 ≠ 判别了机制」的教科书案例（夹具还照抄了错误形状，
  // 见文件头 baseSnapshot 注释）。
  it('角色数按后端 list 形状读出（改动前恒为 0）', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    renderPage();
    const cell = (await screen.findByTestId('stat-characters'));
    expect(within(cell).getByText('1')).toBeInTheDocument();
  });

  it('事件「累计/最近」按后端 dict 形状读出（改动前累计恒为 0）', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    renderPage();
    const cell = (await screen.findByTestId('stat-events'));
    expect(within(cell).getByText('2 / 1')).toBeInTheDocument();
  });

  it('「写到第几章」写在聚合横幅里（G9：此前概览页答不了这第一问）', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    renderPage();
    expect(
      await screen.findByText(/写到第 2 章（已定稿 1 章）/),
    ).toBeInTheDocument();
  });

  // ---- G8 判别：两个「伏笔数」来源必须被点名 ----
  it('明写「状态快照 vs 人工台账」双口径不自动同步', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    renderPage();
    const note = await screen.findByTestId('overview-dual-source-note');
    expect(note).toHaveTextContent('状态快照');
    expect(note).toHaveTextContent('不自动同步');
  });

  it('章节接口失败不吞：横幅可见，其余读数照常', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    vi.mocked(chaptersApi.listByProject).mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByText('boom')).toBeInTheDocument();
    expect(await screen.findByTestId('stat-characters')).toBeInTheDocument();
  });
});
