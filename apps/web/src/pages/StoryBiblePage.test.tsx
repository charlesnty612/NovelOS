// StoryBiblePage：页签 URL 化（?tab=）与键盘可达的回归测试。
// - 直接带 ?tab= 打开 → 渲染对应页签（刷新 / 分享场景）；
// - /bible/* 非法子路径 → 兜底默认页并规范化 URL；
// - 点击 / 方向键 → 同步 URL 与选中态。
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

vi.mock('../api/endpoints', () => ({
  charactersApi: {
    listByProject: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
  locationsApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  factionsApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  worldRulesApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  eventsApi: { list: vi.fn(), create: vi.fn(), delete: vi.fn() },
  timelineApi: { list: vi.fn() },
  chaptersApi: { listByProject: vi.fn() },
  hooksApi: {
    listByProject: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
  debtsApi: {
    listByProject: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
  referenceApi: {
    listCanons: vi.fn(),
    deconstruct: vi.fn(),
    uploadDeconstruct: vi.fn(),
    getCanon: vi.fn(),
    deleteCanon: vi.fn(),
    writeToStyleSample: vi.fn(),
  },
  genreApi: {
    getBinding: vi.fn(),
    listPacks: vi.fn(),
    bind: vi.fn(),
    unbind: vi.fn(),
  },
  workflowsApi: { listByProject: vi.fn() },
}));

import {
  chaptersApi,
  charactersApi,
  eventsApi,
  genreApi,
  hooksApi,
  locationsApi,
  referenceApi,
  timelineApi,
  workflowsApi,
} from '../api/endpoints';
import { StoryBiblePage } from './StoryBiblePage';

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{`${loc.pathname}${loc.search}`}</div>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="projects/:pid/bible/*"
          element={
            <>
              <StoryBiblePage />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

describe('StoryBiblePage 页签 URL 化', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    asMock(charactersApi.listByProject).mockResolvedValue([]);
    asMock(locationsApi.list).mockResolvedValue([]);
    asMock(eventsApi.list).mockResolvedValue([]);
    asMock(timelineApi.list).mockResolvedValue([]);
    asMock(hooksApi.listByProject).mockResolvedValue([]);
    asMock(chaptersApi.listByProject).mockResolvedValue([]);
    asMock(referenceApi.listCanons).mockResolvedValue([]);
    asMock(genreApi.getBinding).mockResolvedValue(null);
    asMock(genreApi.listPacks).mockResolvedValue([]);
    asMock(workflowsApi.listByProject).mockResolvedValue([]);
  });

  it('?tab=world 直接渲染世界页签（刷新 / 分享场景）', async () => {
    renderAt('/projects/prj_1/bible?tab=world');
    await waitFor(() => {
      expect(locationsApi.list).toHaveBeenCalledWith('prj_1');
    });
    expect(screen.getByTestId('tab-world')).toHaveAttribute(
      'aria-selected',
      'true',
    );
    // 默认页签（角色）未被挂载 → 不会多发请求
    expect(charactersApi.listByProject).not.toHaveBeenCalled();
  });

  it('/bible/* 非法子路径兜底默认页并规范化 URL', async () => {
    renderAt('/projects/prj_1/bible/not-a-tab');
    await waitFor(() => {
      expect(screen.getByTestId('loc')).toHaveTextContent(
        '/projects/prj_1/bible?tab=characters',
      );
    });
    expect(screen.getByTestId('tab-characters')).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('/bible/<合法页签> 别名重定向到 ?tab=', async () => {
    renderAt('/projects/prj_1/bible/genre');
    await waitFor(() => {
      expect(screen.getByTestId('loc')).toHaveTextContent(
        '/projects/prj_1/bible?tab=genre',
      );
    });
  });

  it('点击页签写入 URL（后退/前进可用）', async () => {
    renderAt('/projects/prj_1/bible');
    fireEvent.click(screen.getByTestId('tab-plot'));
    await waitFor(() => {
      expect(screen.getByTestId('loc')).toHaveTextContent('?tab=plot');
    });
    await waitFor(() => {
      expect(eventsApi.list).toHaveBeenCalledWith('prj_1');
    });
  });

  it('方向键切换页签并移动焦点', async () => {
    renderAt('/projects/prj_1/bible');
    const first = screen.getByTestId('tab-characters');
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    await waitFor(() => {
      expect(screen.getByTestId('loc')).toHaveTextContent('?tab=world');
    });
    expect(document.activeElement).toBe(screen.getByTestId('tab-world'));

    fireEvent.keyDown(screen.getByTestId('tab-world'), { key: 'ArrowLeft' });
    await waitFor(() => {
      expect(screen.getByTestId('loc')).toHaveTextContent('?tab=characters');
    });
  });
});
