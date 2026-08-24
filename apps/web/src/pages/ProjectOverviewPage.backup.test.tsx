import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ProjectOverviewPage } from './ProjectOverviewPage';
import { ApiError } from '../api/client';
import {
  backupApi,
  commitsApi,
  healthApi,
  projectsApi,
  storyStateApi,
  styleSamplesApi,
} from '../api/endpoints';
import type {
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
    backupApi: { downloadBackup: vi.fn(), importBackup: vi.fn() },
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

const baseHealth: HealthResponse = { status: 'ok', version: '0.1.0', tables: { chapters: 1 } };
const baseSnapshot = {
  characters: {},
  world: {},
  hooks: [],
  debts: [],
  events: [],
  recent_events: [],
};
const baseCommits: Commit[] = [];
const baseStyles: StyleSample[] = [];

function renderPage(pid = 'prj_001') {
  return render(
    <MemoryRouter initialEntries={[`/projects/${pid}/overview`]}>
      <Routes>
        <Route path="projects/:pid/overview" element={<ProjectOverviewPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ProjectOverviewPage 备份按钮', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.get).mockReset();
    vi.mocked(healthApi.get).mockReset();
    vi.mocked(storyStateApi.getCurrent).mockReset();
    vi.mocked(commitsApi.list).mockReset();
    vi.mocked(styleSamplesApi.list).mockReset();
    vi.mocked(backupApi.downloadBackup).mockReset();
  });

  it('项目卡片渲染「下载备份」按钮', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    vi.mocked(healthApi.get).mockResolvedValue(baseHealth);
    vi.mocked(storyStateApi.getCurrent).mockResolvedValue(baseSnapshot);
    vi.mocked(commitsApi.list).mockResolvedValue(baseCommits);
    vi.mocked(styleSamplesApi.list).mockResolvedValue(baseStyles);

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('project-backup-btn')).toBeInTheDocument();
    });
    expect(screen.getByTestId('project-backup-btn')).toHaveTextContent(
      '下载备份',
    );
  });

  it('点击「下载备份」调用 backupApi.downloadBackup(projectId)', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    vi.mocked(healthApi.get).mockResolvedValue(baseHealth);
    vi.mocked(storyStateApi.getCurrent).mockResolvedValue(baseSnapshot);
    vi.mocked(commitsApi.list).mockResolvedValue(baseCommits);
    vi.mocked(styleSamplesApi.list).mockResolvedValue(baseStyles);
    vi.mocked(backupApi.downloadBackup).mockResolvedValue(undefined);

    renderPage();
    const btn = await screen.findByTestId('project-backup-btn');
    fireEvent.click(btn);

    await waitFor(() => {
      expect(backupApi.downloadBackup).toHaveBeenCalledWith('prj_001');
    });
  });

  it('后端 404 时展示错误信息，按钮恢复可点击', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    vi.mocked(healthApi.get).mockResolvedValue(baseHealth);
    vi.mocked(storyStateApi.getCurrent).mockResolvedValue(baseSnapshot);
    vi.mocked(commitsApi.list).mockResolvedValue(baseCommits);
    vi.mocked(styleSamplesApi.list).mockResolvedValue(baseStyles);
    vi.mocked(backupApi.downloadBackup).mockRejectedValue(
      new ApiError(404, "project 'prj_001' not found"),
    );

    renderPage();
    const btn = await screen.findByTestId('project-backup-btn');
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByText(/404/)).toBeInTheDocument();
    });
    expect(screen.getByTestId('project-backup-btn')).toBeEnabled();
  });
});