/**
 * 路由表冒烟（补口：App.tsx 此前零测试）。
 *
 * 判别的是**路由表本身**：每条 path 必须落到正确的页面组件。
 * 叶子页面全部 mock 成带 testid 的占位——路由表冒烟不该被各页面的数据加载
 * 拖垮（也不该发真请求）；Layout 需要的那两个 API 单独 mock。
 *
 * 突变验证：改 App.tsx 里任意一条 path ⇒ 对应用例必红。
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { App } from './App';

vi.mock('./api/endpoints', () => ({
  healthApi: { get: vi.fn(async () => ({ status: 'ok', version: '0.0.0-test' })) },
  projectsApi: {
    get: vi.fn(async () => ({
      project_id: 'prj_001',
      name: '测试项目',
      premise: null,
      genre: null,
      target_words: null,
      status: 'ACTIVE',
      created_at: '',
      updated_at: '',
    })),
  },
}));

vi.mock('./pages/ProjectsListPage', () => ({
  ProjectsListPage: () => <div data-testid="page-projects-list" />,
}));
vi.mock('./pages/ProjectOverviewPage', () => ({
  ProjectOverviewPage: () => <div data-testid="page-project-overview" />,
}));
vi.mock('./pages/StoryBiblePage', () => ({
  StoryBiblePage: () => <div data-testid="page-story-bible" />,
}));
vi.mock('./pages/ChaptersPage', () => ({
  ChaptersPage: () => <div data-testid="page-chapters" />,
}));
vi.mock('./pages/ChapterDetailPage', () => ({
  ChapterDetailPage: () => <div data-testid="page-chapter-detail" />,
}));
vi.mock('./pages/AiSettingsPage', () => ({
  AiSettingsPage: () => <div data-testid="page-ai-settings" />,
}));
vi.mock('./pages/AiCallLogsPage', () => ({
  AiCallLogsPage: () => <div data-testid="page-ai-logs" />,
}));
vi.mock('./pages/NotFoundPage', () => ({
  NotFoundPage: () => <div data-testid="page-not-found" />,
}));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe('App 路由表', () => {
  it('/ 命中项目列表页（index route）', () => {
    renderAt('/');
    expect(screen.getByTestId('page-projects-list')).toBeInTheDocument();
  });

  it('/projects/:pid/overview 命中项目总览页', () => {
    renderAt('/projects/prj_001/overview');
    expect(screen.getByTestId('page-project-overview')).toBeInTheDocument();
  });

  it('/projects/:pid/bible/* splat 落到 Story Bible 页', () => {
    renderAt('/projects/prj_001/bible/characters');
    expect(screen.getByTestId('page-story-bible')).toBeInTheDocument();
  });

  it('/projects/:pid/chapters 命中章节列表页', () => {
    renderAt('/projects/prj_001/chapters');
    expect(screen.getByTestId('page-chapters')).toBeInTheDocument();
  });

  it('/projects/:pid/chapters/:cid 命中章节详情页', () => {
    renderAt('/projects/prj_001/chapters/ch_001');
    expect(screen.getByTestId('page-chapter-detail')).toBeInTheDocument();
  });

  it('/projects/:pid/ai 命中 AI 设置页', () => {
    renderAt('/projects/prj_001/ai');
    expect(screen.getByTestId('page-ai-settings')).toBeInTheDocument();
  });

  it('/ai-logs 命中调用日志页', () => {
    renderAt('/ai-logs');
    expect(screen.getByTestId('page-ai-logs')).toBeInTheDocument();
  });

  it('未知路径落到 NotFound 页', () => {
    renderAt('/no-such-page/at-all');
    expect(screen.getByTestId('page-not-found')).toBeInTheDocument();
  });

  it('路由表互不串味：概览路径不该渲染章节详情页', () => {
    renderAt('/projects/prj_001/overview');
    expect(screen.queryByTestId('page-chapter-detail')).toBeNull();
  });
});
