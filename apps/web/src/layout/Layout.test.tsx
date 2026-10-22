import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Layout } from './Layout';
import { ApiError } from '../api/client';
import { healthApi, projectsApi } from '../api/endpoints';
import type { Project } from '../api/types';

vi.mock('../api/endpoints', () => ({
  projectsApi: { get: vi.fn() },
  healthApi: { get: vi.fn() },
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
    vi.mocked(healthApi.get).mockReset();
    // ⚠️ 用一个**显眼的假版本号**：若界面还写着硬编码常量（如 `NovelOS v3.9.0`），
    // 断言必红 ⇒ 这条断言才**判别**「版本号来自后端」而不是「恰好等于当前版本」。
    vi.mocked(healthApi.get).mockResolvedValue({ status: 'ok', version: '9.9.9-probe' });
  });

  it('侧栏显示项目名与版本标识，顶栏显示弱化项目 ID 与中文状态', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    renderLayout();

    await waitFor(() => {
      expect(screen.getByText('测试项目')).toBeInTheDocument();
    });
    // 版本号来自 `/api/health`（后端读 pyproject = 真单源），不是前端硬编码
    expect(await screen.findByText('NovelOS v9.9.9-probe')).toBeInTheDocument();
    expect(screen.queryByText('NovelOS v3.9.0')).not.toBeInTheDocument();
    expect(screen.queryByText('Sprint 5 · 一期')).not.toBeInTheDocument();
    expect(screen.getByTitle('项目 ID')).toHaveTextContent('prj_001');
    expect(screen.getByText('进行中')).toBeInTheDocument();
  });

  it('取不到版本号时版本位留空——不显示可能过时的版本，也不与品牌名重复', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject);
    vi.mocked(healthApi.get).mockRejectedValue(new ApiError(500, 'health failed'));
    renderLayout();

    await waitFor(() => {
      expect(screen.getByText('测试项目')).toBeInTheDocument();
    });
    // 品牌名 'NovelOS' 仍只有一处（降级文案若也写 'NovelOS' 会重复）
    await waitFor(() => {
      expect(screen.getAllByText('NovelOS')).toHaveLength(1);
    });
    expect(screen.queryByText(/NovelOS v/)).not.toBeInTheDocument();
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
