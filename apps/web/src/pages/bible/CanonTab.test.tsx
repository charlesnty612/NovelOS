// CanonTab 渲染冒烟测试（Sprint 11 下半）：
// - 挂载组件，mock referenceApi；
// - 断言标题、表单、列表渲染；
// - 选中 canon 后断言详情面板渲染（logline + report_md + canon_json）。

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../api/endpoints', () => {
  return {
    referenceApi: {
      deconstruct: vi.fn(),
      listCanons: vi.fn(),
      getCanon: vi.fn(),
      deleteCanon: vi.fn(),
    },
  };
});

import { referenceApi } from '../../api/endpoints';
import { CanonTab } from './CanonTab';

const SAMPLE_CANON_LIST = [
  {
    canon_id: 'can_001',
    project_id: 'p1',
    title: '测试参照书',
    reader_profile: 'male_fantasy',
    status: 'active',
    created_at: '2026-08-23T10:00:00',
    logline: '草根少年逆袭金手指',
    spine_count: 3,
    rhythm_chapter_count: 3,
  },
];

const SAMPLE_CANON_DETAIL = {
  canon_id: 'can_001',
  project_id: 'p1',
  title: '测试参照书',
  reader_profile: 'male_fantasy',
  status: 'active',
  created_at: '2026-08-23T10:00:00',
  canon_json: {
    logline: '草根少年逆袭金手指',
    spine: [{ chapter_index: 1 }, { chapter_index: 2 }, { chapter_index: 3 }],
    rhythm: {
      mini_climax_interval: { median: 3 },
      major_climax_interval: { median: 5 },
      chapter_end_hook_rate: 0.85,
    },
    style_params: {
      pov: 'third_limited',
      dialogue_ratio: 0.25,
      action_ratio: 0.45,
    },
    metadata: { source_book_title: '测试参照书' },
  },
  report_md:
    '# 整体节奏\n整体走钩子优先。\n\n## 第一卷\n- 开篇冲突\n- 章末留悬念',
  extracts: [
    { extract_id: 'ex_1', chapter_index: 1, extract_json: {}, created_at: '2026-08-23T10:00:00' },
  ],
};

describe('CanonTab (冒烟)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (referenceApi.listCanons as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      SAMPLE_CANON_LIST,
    );
    (referenceApi.getCanon as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      SAMPLE_CANON_DETAIL,
    );
  });

  it('挂载后渲染：拆书表单 + 列表 + 选中后的详情', async () => {
    render(<CanonTab projectId="p1" />);

    // 拆书表单
    expect(screen.getByTestId('deconstruct-form')).toBeInTheDocument();
    expect(screen.getByTestId('deconstruct-submit')).toBeInTheDocument();

    // 列表加载完后展示 1 行
    await waitFor(() => {
      expect(screen.getByTestId('canon-table')).toBeInTheDocument();
    });
    expect(screen.getByText('测试参照书')).toBeInTheDocument();

    // 选中第一行 → 加载详情
    const row = screen.getByTestId('canon-row');
    await userEvent.click(row);

    await waitFor(() => {
      expect(screen.getByTestId('canon-logline')).toBeInTheDocument();
    });
    expect(screen.getByTestId('canon-logline').textContent).toContain('草根少年');
    // report_md 渲染：标题与列表项
    expect(screen.getByText('整体节奏')).toBeInTheDocument();
    expect(screen.getByText('第一卷')).toBeInTheDocument();
    expect(screen.getByText('开篇冲突')).toBeInTheDocument();
    expect(screen.getByText('章末留悬念')).toBeInTheDocument();
    // canon_json 全文 JSON 块
    expect(screen.getByTestId('canon-json-block')).toBeInTheDocument();
  });

  it('空列表展示 EmptyState', async () => {
    (referenceApi.listCanons as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce([]);
    render(<CanonTab projectId="p1" />);
    await waitFor(() => {
      expect(screen.getByText('还没有参照系')).toBeInTheDocument();
    });
  });

  it('点击「开始拆书」触发 referenceApi.deconstruct', async () => {
    (referenceApi.deconstruct as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      run_id: 'wfr_001',
      status: 'COMPLETED',
      current_node: null,
      project_id: 'p1',
      book_title: '新书',
      canon_id: 'can_002',
    });
    render(<CanonTab projectId="p1" />);
    await userEvent.type(screen.getByTestId('deconstruct-book-title'), '新书');
    await userEvent.type(screen.getByTestId('deconstruct-text'), '第一章 ...');
    await userEvent.click(screen.getByTestId('deconstruct-submit'));
    await waitFor(() => {
      expect(referenceApi.deconstruct).toHaveBeenCalledWith('p1', {
        book_title: '新书',
        text: '第一章 ...',
        reader_profile: 'male_fantasy',
      });
    });
  });
});
