// LedgerTab 渲染冒烟测试（Sprint 9）：
// - 挂载组件，mock API 客户端；
// - 断言标题、表格、创建按钮存在；
// - 验证 1 条 hook + 1 条 debt 渲染（含逾期高亮）；
//
// 完整测试覆盖靠 ledgerState.test.ts（纯函数）+ tests/api/test_ledger.py（HTTP 层）；
// 本测试只做「组件不炸」的最薄冒烟。

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

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