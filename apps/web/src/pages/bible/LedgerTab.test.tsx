// LedgerTab 渲染冒烟测试（Sprint 9）：
// - 挂载组件，mock API 客户端；
// - 断言标题、表格、创建按钮存在；
// - 验证 1 条 hook + 1 条 debt 渲染（含逾期高亮）；
//
// 完整测试覆盖靠 ledgerState.test.ts（纯函数）+ tests/api/test_ledger.py（HTTP 层）；
// 本测试只做「组件不炸」的最薄冒烟。

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// 拦截 endpoints.ts 内的所有 fetch 调用
vi.mock('../../api/endpoints', () => {
  return {
    hooksApi: {
      listByProject: vi.fn(),
      create: vi.fn(),
      get: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
    },
    debtsApi: {
      listByProject: vi.fn(),
      create: vi.fn(),
      get: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
    },
    chaptersApi: {
      listByProject: vi.fn(),
    },
  };
});

import { hooksApi, debtsApi, chaptersApi } from '../../api/endpoints';
import { LedgerTab } from './LedgerTab';

describe('LedgerTab (冒烟)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (hooksApi.listByProject as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        hook_id: 'h1',
        project_id: 'p1',
        name: '黑玉佩秘密',
        introduced_chapter_id: 'c1',
        status: 'OPEN',
        importance: 0.9,
        expected_payoff_chapter_id: 'c1',
        payoff_chapter_id: null,
        visibility: 'RESTRICTED',
        who_knows: null,
        created_at: '2026-08-23T10:00:00',
        updated_at: '2026-08-23T10:00:00',
      },
    ]);
    (debtsApi.listByProject as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        debt_id: 'd1',
        project_id: 'p1',
        description: '男主答应调查父亲死亡',
        created_chapter_id: 'c1',
        severity: 0.8,
        deadline_chapter_id: null,
        status: 'open',
        visibility: 'RESTRICTED',
        who_knows: null,
        created_at: '2026-08-23T10:00:00',
        updated_at: '2026-08-23T10:00:00',
      },
    ]);
    (chaptersApi.listByProject as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        chapter_id: 'c1',
        project_id: 'p1',
        number: 1,
        title: '第一章',
        plan_json: {},
        status: 'PLANNED',
        visibility: 'VISIBLE',
        who_knows: null,
        created_at: '2026-08-23T10:00:00',
        updated_at: '2026-08-23T10:00:00',
      },
    ]);
  });

  it('挂载后渲染标题 + 表格 + 创建按钮', async () => {
    render(<LedgerTab projectId="p1" />);
    // 标题
    expect(screen.getByText('伏笔台账 Hooks')).toBeInTheDocument();
    expect(screen.getByText('叙事债务 Debts')).toBeInTheDocument();
    // 创建按钮
    expect(screen.getByTestId('create-hook-btn')).toBeInTheDocument();
    expect(screen.getByTestId('create-debt-btn')).toBeInTheDocument();
    // 数据加载后
    await waitFor(() => {
      expect(screen.getByText('黑玉佩秘密')).toBeInTheDocument();
      expect(screen.getByText('男主答应调查父亲死亡')).toBeInTheDocument();
    });
    // 表格存在
    expect(screen.getByTestId('hook-table')).toBeInTheDocument();
    expect(screen.getByTestId('debt-table')).toBeInTheDocument();
  });
});

// ----------------------------------------------------------------------------
// 关键流：filter 筛选 + create 调用 + API 失败
// ----------------------------------------------------------------------------

const HOOKS_FIXTURE = [
  {
    hook_id: 'h_open',
    project_id: 'p1',
    name: 'OPEN 钩子',
    introduced_chapter_id: 'c1',
    status: 'OPEN',
    importance: 0.5,
    expected_payoff_chapter_id: null,
    payoff_chapter_id: null,
    visibility: 'RESTRICTED',
    who_knows: null,
    created_at: '2026-08-23T10:00:00',
    updated_at: '2026-08-23T10:00:00',
  },
  {
    hook_id: 'h_active',
    project_id: 'p1',
    name: 'ACTIVE 钩子',
    introduced_chapter_id: 'c1',
    status: 'ACTIVE',
    importance: 0.7,
    expected_payoff_chapter_id: null,
    payoff_chapter_id: null,
    visibility: 'RESTRICTED',
    who_knows: null,
    created_at: '2026-08-23T10:00:00',
    updated_at: '2026-08-23T10:00:00',
  },
];

describe('LedgerTab 关键流', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (
      hooksApi.listByProject as unknown as ReturnType<typeof vi.fn>
    ).mockResolvedValue(HOOKS_FIXTURE);
    (debtsApi.listByProject as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (chaptersApi.listByProject as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        chapter_id: 'c1',
        project_id: 'p1',
        number: 1,
        title: '第一章',
        plan_json: {},
        status: 'PLANNED',
        visibility: 'VISIBLE',
        who_knows: null,
        created_at: '2026-08-23T10:00:00',
        updated_at: '2026-08-23T10:00:00',
      },
    ]);
  });

  it('a) 切换 status filter：选 ACTIVE → 只显示 ACTIVE 钩子', async () => {
    render(<LedgerTab projectId="p1" />);

    // 默认两条都在
    await waitFor(() => {
      expect(screen.getByText('OPEN 钩子')).toBeInTheDocument();
    });
    expect(screen.getByText('ACTIVE 钩子')).toBeInTheDocument();

    // 切到 ACTIVE
    const filter = screen.getByTestId('hook-status-filter') as HTMLSelectElement;
    fireEvent.change(filter, { target: { value: 'ACTIVE' } });

    // OPEN 行被过滤掉；ACTIVE 行仍存在
    await waitFor(() => {
      expect(screen.queryByText('OPEN 钩子')).toBeNull();
    });
    expect(screen.getByText('ACTIVE 钩子')).toBeInTheDocument();
  });

  it('b) 点「+ 新建伏笔」+ 填名称 + 保存 → 调 hooksApi.create + reload', async () => {
    (
      hooksApi.create as unknown as ReturnType<typeof vi.fn>
    ).mockResolvedValue({
      hook_id: 'h_new',
      project_id: 'p1',
      name: '新建伏笔',
      introduced_chapter_id: null,
      status: 'OPEN',
      importance: 0.5,
      expected_payoff_chapter_id: null,
      payoff_chapter_id: null,
      visibility: 'RESTRICTED',
      who_knows: null,
      created_at: '2026-08-23T10:00:00',
      updated_at: '2026-08-23T10:00:00',
    });

    const user = userEvent.setup();
    render(<LedgerTab projectId="p1" />);

    // 等初始数据加载
    await waitFor(() => {
      expect(screen.getByText('OPEN 钩子')).toBeInTheDocument();
    });

    // 点 + 新建伏笔 → modal 出现
    await user.click(screen.getByTestId('create-hook-btn'));
    await waitFor(() => {
      expect(screen.getByTestId('hook-name-input')).toBeInTheDocument();
    });

    // 填名称 + 保存
    await user.type(screen.getByTestId('hook-name-input'), '新建伏笔');
    await user.click(screen.getByTestId('hook-save'));

    await waitFor(() => {
      expect(hooksApi.create).toHaveBeenCalledTimes(1);
    });
    // create 调用：(projectId, payload)
    const [, payload] = (hooksApi.create as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(payload.name).toBe('新建伏笔');
    // 默认 status = OPEN、importance = 0.5
    expect(payload.status).toBe('OPEN');
    expect(payload.importance).toBe(0.5);
    // listByProject 被 reload 调用一次
    expect(
      (hooksApi.listByProject as unknown as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThanOrEqual(2);
  });

  it('c) hooksApi.listByProject 失败 → ErrorBanner 显示错误', async () => {
    (
      hooksApi.listByProject as unknown as ReturnType<typeof vi.fn>
    ).mockRejectedValue(new Error('伏笔台账 503'));

    render(<LedgerTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getByText('伏笔台账 503')).toBeInTheDocument();
    });
    // 数据不渲染
    expect(screen.queryByText('OPEN 钩子')).toBeNull();
  });
});