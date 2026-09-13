import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Layout } from './Layout';
import { ApiError } from '../api/client';
import { projectsApi } from '../api/endpoints';
import type { Project } from '../api/types';

vi.mock('../api/endpoints', () => ({
  projectsApi: { get: vi.fn() },
}));

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

function renderLayout(pid = 'prj_001') {
  return render(
    <MemoryRouter initialEntries={[`/projects/${pid}/overview`]}>
      <Routes>
        <Route path="projects/:pid/overview" element={<Layout />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Layout 外壳', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.get).mockReset();
  });

  it('侧栏显示项目名与版本标识，顶栏显示弱化项目 ID 与中文状态', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    renderLayout();

    await waitFor(() => {
      expect(screen.getByText('测试项目')).toBeInTheDocument();
    });
    expect(screen.getByText('NovelOS v3.9.0')).toBeInTheDocument();
    expect(screen.queryByText('Sprint 5 · 一期')).not.toBeInTheDocument();
    expect(screen.getByTitle('项目 ID')).toHaveTextContent('prj_001');
    expect(screen.getByText('进行中')).toBeInTheDocument();
  });

  it('项目信息加载失败时给出重试入口，点击后恢复', async () => {
    vi.mocked(projectsApi.get)
      .mockRejectedValueOnce(new ApiError(404, 'project not found'))
      .mockResolvedValue(baseProject);
    renderLayout();

    const retry = await screen.findByTestId('sidebar-project-retry');
    expect(retry).toHaveTextContent('项目信息加载失败，点击重试');

    fireEvent.click(retry);
    await waitFor(() => {
      expect(screen.getByText('测试项目')).toBeInTheDocument();
    });
    expect(projectsApi.get).toHaveBeenCalledTimes(2);
  });

  it('项目信息加载中显示统一 Loading，而非裸「加载中…」', () => {
    vi.mocked(projectsApi.get).mockReturnValue(new Promise<Project>(() => {}));
    renderLayout();

    expect(screen.getByRole('status')).toHaveTextContent('加载中…');
  });
});
