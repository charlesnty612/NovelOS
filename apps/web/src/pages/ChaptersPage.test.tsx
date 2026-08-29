/** @vitest-environment jsdom */
// ChaptersPage 关键流测试：
// - 列表加载：listByProject 返回 → 渲染表格行
// - 点击「进入」按钮 → 调 navigate 跳详情
// - API 失败 → ErrorBanner 显示错误文本
// - 新建 modal：number 已存在 → 拒绝提交（不调 create）

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ChaptersPage } from './ChaptersPage';
import { chaptersApi } from '../api/endpoints';
import type { Chapter } from '../api/types';

// ---- API mock ----
vi.mock('../api/endpoints', () => ({
  chaptersApi: {
    listByProject: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    listDrafts: vi.fn(),
    createDraft: vi.fn(),
  },
}));

// ---- fixtures ----
const baseChapter = (overrides: Partial<Chapter> = {}): Chapter => ({
  chapter_id: 'ch_001',
  project_id: 'prj_001',
  number: 1,
  title: '第一章',
  status: 'PLANNED',
  visibility: 'public',
  who_knows: null,
  created_at: '2026-08-24T10:00:00+00:00',
  updated_at: '2026-08-24T10:00:00+00:00',
  plan_json: {},
  ...overrides,
});

function renderPage(initialEntries: string[] = ['/projects/prj_001/chapters']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route
          path="/projects/:pid/chapters"
          element={<ChaptersPage />}
        />
        <Route
          path="/projects/:pid/chapters/:cid"
          element={<div data-testid="chapter-detail-stub" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ChaptersPage - 关键流', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(chaptersApi.listByProject).mockResolvedValue([]);
    vi.mocked(chaptersApi.create).mockResolvedValue(baseChapter({ chapter_id: 'ch_new', number: 2 }));
  });

  it('a) 列表加载：listByProject 返回 → 渲染表格行（章号/标题/状态/进入按钮）', async () => {
    vi.mocked(chaptersApi.listByProject).mockResolvedValue([
      baseChapter({ chapter_id: 'ch_001', number: 1, title: '楔子' }),
      baseChapter({
        chapter_id: 'ch_002',
        number: 2,
        title: null,
        status: 'DRAFTED',
        updated_at: '2026-08-25T12:00:00+00:00',
      }),
    ]);

    renderPage();

    // 等列表行到位
    await waitFor(() => {
      expect(screen.getByTestId('chapter-row-ch_001')).toBeInTheDocument();
    });

    // 章号 + 标题
    expect(screen.getByTestId('chapter-row-ch_001')).toHaveTextContent('第 1 章');
    expect(screen.getByTestId('chapter-row-ch_001')).toHaveTextContent('楔子');

    // ch_002 标题为 null 时显示「未命名」
    expect(screen.getByTestId('chapter-row-ch_002')).toHaveTextContent('未命名');

    // 每行有「进入」按钮
    expect(
      screen.getByTestId('chapter-row-ch_001').querySelector('button.btn--sm'),
    ).toHaveTextContent('进入');
  });

  it('b) 点击「进入」按钮 → navigate 跳转到章节详情路由', async () => {
    vi.mocked(chaptersApi.listByProject).mockResolvedValue([
      baseChapter({ chapter_id: 'ch_007', number: 7 }),
    ]);

    renderPage();

    const row = await waitFor(() => screen.getByTestId('chapter-row-ch_007'));
    const enterBtn = row.querySelector('button.btn--sm') as HTMLButtonElement;
    expect(enterBtn).toHaveTextContent('进入');
    fireEvent.click(enterBtn);

    // 详情页 stub 渲染 = 路由跳转成功
    await waitFor(() => {
      expect(screen.getByTestId('chapter-detail-stub')).toBeInTheDocument();
    });
    // URL 上 /projects/prj_001/chapters/ch_007 已被匹配（stub 命中）
    expect(screen.getByTestId('chapter-detail-stub')).toBeInTheDocument();
  });

  it('c) API 失败：listByProject 抛错 → 显示错误文本，不渲染表格', async () => {
    vi.mocked(chaptersApi.listByProject).mockRejectedValue(
      new Error('网络异常 502'),
    );

    renderPage();

    // 错误文本出现在 ErrorBanner 内
    await waitFor(() => {
      expect(screen.getByText('网络异常 502')).toBeInTheDocument();
    });
    // loading 收尾、表格不渲染
    expect(screen.queryByTestId('chapter-list')).toBeNull();
  });

  it('d) 新建 modal：提交已存在的章号 → 显示错误，不调 create', async () => {
    vi.mocked(chaptersApi.listByProject).mockResolvedValue([
      baseChapter({ chapter_id: 'ch_001', number: 1 }),
    ]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-row-ch_001')).toBeInTheDocument();
    });

    // 打开新建 modal（toolbar 上的「+ 新建章节」）
    fireEvent.click(screen.getByTestId('new-chapter-btn'));

    // suggestedNumber 默认 = 2（list 中最大章号 + 1）
    const numberInput = (await waitFor(() =>
      screen.getByTestId('chapter-number'),
    )) as HTMLInputElement;
    expect(numberInput.value).toBe('2');

    // 用户改成 1（已存在）
    fireEvent.change(numberInput, { target: { value: '1' } });
    // 点保存
    fireEvent.click(screen.getByTestId('chapter-save'));

    // 校验失败：显示「章号 1 已存在」+ 不调 create
    await waitFor(() => {
      expect(screen.getByText(/章号 1 已存在/)).toBeInTheDocument();
    });
    expect(vi.mocked(chaptersApi.create)).not.toHaveBeenCalled();
  });
});