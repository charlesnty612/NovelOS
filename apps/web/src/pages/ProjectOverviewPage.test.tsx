import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ProjectOverviewPage } from './ProjectOverviewPage';
import {
  branchesApi,
  commitsApi,
  healthApi,
  projectsApi,
  storyStateApi,
  styleSamplesApi,
} from '../api/endpoints';
import type { Commit, HealthResponse, Project, StyleSample } from '../api/types';

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
  version: 7,
  characters: {},
  world: {},
  hooks: [],
  debts: [],
  events: [],
  recent_events: [],
};
const baseCommits: Commit[] = [];
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

    vi.mocked(healthApi.get).mockResolvedValue(baseHealth);
    vi.mocked(storyStateApi.getCurrent).mockResolvedValue(baseSnapshot);
    vi.mocked(commitsApi.list).mockResolvedValue(baseCommits);
    vi.mocked(styleSamplesApi.list).mockResolvedValue(baseStyles);
    vi.mocked(branchesApi.list).mockResolvedValue(baseBranches);
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

  it('状态快照区展示当前版本与中文指标名', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    renderPage();

    const snapCard = (await screen.findByText('故事状态快照')).closest('.card')!;
    const scope = within(snapCard as HTMLElement);
    expect(await scope.findByText('当前故事状态版本：v7')).toBeInTheDocument();
    expect(scope.getByText('角色')).toBeInTheDocument();
    expect(scope.getByText('伏笔')).toBeInTheDocument();
    expect(scope.getByText('债务')).toBeInTheDocument();
    expect(scope.getByText('事件')).toBeInTheDocument();
    expect(scope.queryByText('Characters')).not.toBeInTheDocument();
    expect(scope.queryByText(/state_version 来自/)).not.toBeInTheDocument();
  });
});
