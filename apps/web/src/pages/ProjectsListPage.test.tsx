// ProjectsListPage 关键流测试：
// - 列表加载：projectsApi.list → 渲染项目卡（含状态徽标 + 编辑按钮）
// - 创建项目：name 必填 + 提交 → 调 projectsApi.create + reload
// - 创建项目失败：projectsApi.create 抛错 → ErrorBanner 显示错误
// - 编辑 → 归档：确认 → 调 projectsApi.update(status: ARCHIVED)

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ProjectsListPage } from './ProjectsListPage';
import { ApiError } from '../api/client';
import { projectsApi } from '../api/endpoints';
import type { Project } from '../api/types';

// ---- API mock ----
vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    projectsApi: {
      list: vi.fn(),
      create: vi.fn(),
      get: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
    },
    backupApi: {
      downloadBackup: vi.fn(),
      importBackup: vi.fn(),
    },
  };
});

// ---- fixtures ----
const baseProject = (overrides: Partial<Project> = {}): Project => ({
  project_id: 'prj_001',
  name: '测试项目',
  premise: '一个测试用项目',
  genre: '科幻',
  target_words: 100_000,
  status: 'ACTIVE',
  created_at: '2026-08-24T10:00:00+00:00',
  updated_at: '2026-08-24T10:00:00+00:00',
  ...overrides,
});

function renderPage(initialEntries: string[] = ['/']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route index element={<ProjectsListPage />} />
        <Route
          path="projects/:pid/overview"
          element={<div data-testid="overview-stub" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ProjectsListPage - 关键流', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(projectsApi.list).mockResolvedValue([]);
    vi.mocked(projectsApi.create).mockResolvedValue(baseProject({ project_id: 'prj_new' }));
    vi.mocked(projectsApi.update).mockResolvedValue(baseProject());
  });

  it('a) 列表加载：渲染项目卡 + 状态徽标 + 编辑按钮', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([
      baseProject({ project_id: 'prj_001', name: '三体前传', status: 'ACTIVE' }),
      baseProject({ project_id: 'prj_002', name: '旧档', status: 'ARCHIVED' }),
    ]);

    renderPage();

    // 项目卡渲染
    await waitFor(() => {
      expect(screen.getByTestId('project-card-prj_001')).toBeInTheDocument();
    });
    expect(screen.getByTestId('project-card-prj_002')).toBeInTheDocument();

    // 名称渲染
    expect(screen.getByText('三体前传')).toBeInTheDocument();
    expect(screen.getByText('旧档')).toBeInTheDocument();

    // ACTIVE 项目渲染「编辑」按钮；ARCHIVED 不渲染
    const activeCard = screen.getByTestId('project-card-prj_001');
    expect(activeCard.querySelector('button.btn--sm')).toHaveTextContent('编辑');
    // ARCHIVED 卡内不显示「编辑」按钮（业务规则：归档项目不能编辑）
    const archivedCard = screen.getByTestId('project-card-prj_002');
    expect(archivedCard.querySelector('button.btn--sm')).toBeNull();
  });

  it('b) 创建项目：填名称 + 点保存 → 调 projectsApi.create + 关闭 modal', async () => {
    vi.mocked(projectsApi.list)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([baseProject({ project_id: 'prj_new', name: '新书' })]);

    const user = userEvent.setup();
    renderPage();

    // 点 + 新建项目
    await user.click(screen.getByTestId('new-project-btn'));

    // modal 出现 + name 输入框
    const nameInput = await waitFor(() => screen.getByTestId('project-name'));
    await user.type(nameInput, '新书');
    await user.click(screen.getByTestId('project-save'));

    await waitFor(() => {
      expect(projectsApi.create).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(projectsApi.create).mock.calls[0][0] as unknown as Record<string, unknown>;
    expect(payload.name).toBe('新书');
    // 默认 status = ACTIVE（ProjectsListPage 表单初始 status=ACTIVE）
    expect(payload.status).toBe('ACTIVE');

    // modal 关闭 + 新项目渲染到卡片网格
    await waitFor(() => {
      expect(screen.queryByTestId('project-name')).toBeNull();
    });
    await waitFor(() => {
      expect(screen.getByTestId('project-card-prj_new')).toBeInTheDocument();
    });
  });

  it('c) 创建项目失败：projectsApi.create 抛 ApiError → ErrorBanner 显示错误，modal 不关闭', async () => {
    vi.mocked(projectsApi.create).mockRejectedValue(
      new ApiError(409, '项目名已存在'),
    );

    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByTestId('new-project-btn'));
    await user.type(await screen.findByTestId('project-name'), '冲突名');
    await user.click(screen.getByTestId('project-save'));

    // ErrorBanner 显示后端 detail（useApiCall 把 ApiError.detail 作为 error string）
    await waitFor(() => {
      expect(screen.getByText(/项目名已存在/)).toBeInTheDocument();
    });
    // modal 仍存在（未关闭）
    expect(screen.getByTestId('project-name')).toBeInTheDocument();
  });

  it('d) 编辑 → 归档：点编辑 → 点「归档」+ 确认 → 调 projectsApi.update(status=ARCHIVED)', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([
      baseProject({ project_id: 'prj_001', name: '即将归档', status: 'ACTIVE' }),
    ]);

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    const user = userEvent.setup();
    renderPage();

    // 点项目卡上的「编辑」
    await waitFor(() => {
      expect(screen.getByTestId('project-card-prj_001')).toBeInTheDocument();
    });
    const editBtn = screen
      .getByTestId('project-card-prj_001')
      .querySelector('button.btn--sm') as HTMLButtonElement;
    expect(editBtn).toHaveTextContent('编辑');
    fireEvent.click(editBtn);

    // 编辑 modal 出现 + 「归档」按钮（extraActions）
    const archiveBtn = await waitFor(() =>
      screen.getByRole('button', { name: '归档' }),
    );
    await user.click(archiveBtn);

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(projectsApi.update).toHaveBeenCalled();
    });
    // 至少有一次 update 调用载荷含 status: ARCHIVED（业务语义）
    const archiveCall = vi.mocked(projectsApi.update).mock.calls.find(
      ([_id, p]) =>
        (p as Record<string, unknown>).status === 'ARCHIVED',
    );
    expect(archiveCall).toBeDefined();
    expect(archiveCall![0]).toBe('prj_001');

    confirmSpy.mockRestore();
  });
});