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
  timelineApi: { list: vi.fn(), remove: vi.fn() },
  chaptersApi: { listByProject: vi.fn() },
  // G6 新增两个页签；漏 mock 会落到模块未定义 → 组件一挂载就崩。
  volumesApi: {
    list: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    update: vi.fn(),
    seal: vi.fn(),
    assign: vi.fn(),
  },
  relationshipsApi: { list: vi.fn() },
  // G6 挂账批次（四）：弧光 / 剧透策略两个页签。同样漏 mock 会一挂载就崩。
  arcApi: { get: vi.fn() },
  revealPoliciesApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
  },
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
  arcApi,
  chaptersApi,
  charactersApi,
  eventsApi,
  genreApi,
  hooksApi,
  locationsApi,
  referenceApi,
  relationshipsApi,
  revealPoliciesApi,
  timelineApi,
  volumesApi,
  workflowsApi,
} from '../api/endpoints';
import { StoryBiblePage } from './StoryBiblePage';

/**
 * 弧光页签的最小可用载荷。
 *
 * ⚠️ 形状**照抄后端 `build_arc_view` 的返回**，不是照抄 UI 需要什么就造什么——
 * 见 AGENTS 坑区「夹具从被测代码的实际输出里抄形状 ⇒ 缺陷被写进断言」：
 * 少给一个键，组件里 `x.y.length` 就会在真实数据以外的路径上崩。
 * 这里给的是**全空**形态（最容易被写成「忘了给」的样子），故每个键都显式写出。
 */
const EMPTY_ARC_VIEW = {
  project_id: 'prj_1',
  generated_at: '2026-10-15T00:00:00Z',
  chapters: [],
  payoff: { total: 0, charge_streak: 0, max_charge_streak: 0, last_payoff_chapter: null },
  hooks: { open: 0, resolved: 0, overdue: 0 },
  debts: { open: 0, paid: 0 },
  reveal_policies: { planned: 0, revealed: 0, cancelled: 0, overdue: [] },
  volumes: [],
  alerts: [],
};

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
    asMock(volumesApi.list).mockResolvedValue([]);
    asMock(relationshipsApi.list).mockResolvedValue([]);
    asMock(arcApi.get).mockResolvedValue(EMPTY_ARC_VIEW);
    asMock(revealPoliciesApi.list).mockResolvedValue([]);
  });

  // G6：卷 / 人物关系页签进入 URL 契约。
  // 判别点是**页签真的挂载并发了请求**——只断言 testid 存在的话，
  // 把渲染分支整段删掉也照样绿（标签在，面板不在）。
  it('?tab=volumes 直接渲染卷页签（刷新 / 分享场景）', async () => {
    renderAt('/projects/prj_1/bible?tab=volumes');
    await waitFor(() => {
      expect(volumesApi.list).toHaveBeenCalledWith('prj_1');
    });
    expect(screen.getByTestId('tab-volumes')).toHaveAttribute('aria-selected', 'true');
  });

  it('?tab=relationships 直接渲染人物关系页签', async () => {
    renderAt('/projects/prj_1/bible?tab=relationships');
    await waitFor(() => {
      expect(relationshipsApi.list).toHaveBeenCalledWith('prj_1');
    });
    expect(screen.getByTestId('tab-relationships')).toHaveAttribute('aria-selected', 'true');
  });

  // G6 挂账批次（四）。判别点同上：页签真的挂载并发了请求，
  // 只断言 testid 存在的话，把渲染分支整段删掉照样绿。
  it('?tab=arc 直接渲染弧光页签', async () => {
    renderAt('/projects/prj_1/bible?tab=arc');
    await waitFor(() => {
      expect(arcApi.get).toHaveBeenCalledWith('prj_1');
    });
    expect(screen.getByTestId('tab-arc')).toHaveAttribute('aria-selected', 'true');
  });

  it('?tab=reveal 直接渲染剧透策略页签', async () => {
    renderAt('/projects/prj_1/bible?tab=reveal');
    await waitFor(() => {
      expect(revealPoliciesApi.list).toHaveBeenCalled();
    });
    expect(screen.getByTestId('tab-reveal')).toHaveAttribute('aria-selected', 'true');
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
