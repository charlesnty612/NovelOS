import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ProjectsListPage } from './ProjectsListPage';
import { backupApi, projectsApi } from '../api/endpoints';
import { ApiError } from '../api/client';
import type { BackupPackage, Project } from '../api/types';

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

const baseBackup: BackupPackage = {
  format: 'novelos-backup',
  version: 1,
  exported_at: '2026-08-24T10:00:00Z',
  exported_from_project_id: 'prj_src',
  metadata: {
    schema_migrations: ['0001_init.sql'],
    table_count_exported: 22,
    exported_table_names: ['characters'],
    exported_at_iso: '2026-08-24T10:00:00Z',
    api_keys_stripped: true,
    ai_call_logs_excluded: true,
    evaluations_excluded: true,
    workflow_runs_excluded: true,
    model_configs_excluded: true,
    reference_canons_excluded: true,
  },
  project: baseProject,
  tables: { characters: [] },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route index element={<ProjectsListPage />} />
        <Route path="projects/:pid/overview" element={<div data-testid="overview-stub" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ProjectsListPage 备份导入', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.list).mockReset();
    vi.mocked(backupApi.importBackup).mockReset();
    vi.mocked(backupApi.downloadBackup).mockReset();
  });

  it('渲染「导入备份」按钮，点击打开模态框', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([]);
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('import-backup-btn')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('import-backup-btn'));
    await waitFor(() => {
      expect(screen.getByTestId('import-backup-modal')).toBeInTheDocument();
    });
    expect(screen.getByTestId('import-backup-file')).toBeInTheDocument();
    expect(screen.getByTestId('import-backup-submit')).toBeInTheDocument();
  });

  it('未选文件时禁用「导入」按钮', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([]);
    renderPage();

    fireEvent.click(screen.getByTestId('import-backup-btn'));
    await waitFor(() => {
      expect(screen.getByTestId('import-backup-modal')).toBeInTheDocument();
    });
    const submit = screen.getByTestId('import-backup-submit');
    expect(submit).toBeDisabled();
  });

  it('选 json 文件 → 提交 → 调 backupApi.importBackup，跳转到新项目', async () => {
    vi.mocked(projectsApi.list)
      .mockResolvedValueOnce([baseProject])
      .mockResolvedValueOnce([baseProject, {
        ...baseProject,
        project_id: 'prj_new',
        name: '测试项目（导入）',
      }]);
    const newProj: Project = {
      ...baseProject,
      project_id: 'prj_new',
      name: '测试项目（导入）',
    };
    vi.mocked(backupApi.importBackup).mockResolvedValue(newProj);

    // 让 jsdom 的 File.text() 返回合法 JSON 字符串
    const origText = File.prototype.text;
    File.prototype.text = async function () {
      return JSON.stringify(baseBackup);
    };

    try {
      renderPage();

      fireEvent.click(screen.getByTestId('import-backup-btn'));
      await waitFor(() => {
        expect(screen.getByTestId('import-backup-modal')).toBeInTheDocument();
      });

      const fileInput = screen.getByTestId(
        'import-backup-file',
      ) as HTMLInputElement;
      const fakeFile = new File([JSON.stringify(baseBackup)], 'backup.json', {
        type: 'application/json',
      });
      fireEvent.change(fileInput, { target: { files: [fakeFile] } });

      const submit = screen.getByTestId('import-backup-submit');
      await waitFor(() => {
        expect(submit).not.toBeDisabled();
      });
      fireEvent.click(submit);

      await waitFor(() => {
        expect(backupApi.importBackup).toHaveBeenCalledTimes(1);
      });
      expect(backupApi.importBackup).toHaveBeenCalledWith(
        expect.objectContaining({ format: 'novelos-backup' }),
      );
    } finally {
      File.prototype.text = origText;
    }
  });

  it('后端 422 时展示错误信息，不跳转', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([baseProject]);
    vi.mocked(backupApi.importBackup).mockRejectedValue(
      new ApiError(422, 'unsupported backup format'),
    );
    const origText = File.prototype.text;
    File.prototype.text = async function () {
      return JSON.stringify(baseBackup);
    };

    try {
      renderPage();

      fireEvent.click(screen.getByTestId('import-backup-btn'));
      await waitFor(() => {
        expect(screen.getByTestId('import-backup-modal')).toBeInTheDocument();
      });

      const fileInput = screen.getByTestId(
        'import-backup-file',
      ) as HTMLInputElement;
      fireEvent.change(fileInput, {
        target: { files: [new File(['{}'], 'b.json', { type: 'application/json' })] },
      });

      fireEvent.click(screen.getByTestId('import-backup-submit'));

      await waitFor(() => {
        expect(screen.getByText(/422/)).toBeInTheDocument();
      });
      expect(screen.getByTestId('import-backup-modal')).toBeInTheDocument();
    } finally {
      File.prototype.text = origText;
    }
  });
});