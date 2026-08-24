import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ContextPreviewPanel } from './ContextPreviewPanel';
import { contextPreviewApi } from '../api/endpoints';
import type { ContextPreviewResponse } from '../api/types';
import { ApiError } from '../api/client';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    contextPreviewApi: {
      preview: vi.fn(),
    },
  };
});

const basePreview: ContextPreviewResponse = {
  chapter_id: 'ch_1',
  project_id: 'prj_1',
  agents: ['director', 'writer', 'observer'],
  layers: [
    {
      id: 'L0',
      label: '项目元数据',
      token_estimate: 80,
      items: [
        { kind: 'chapter', id: 'ch_1', name: '第 1 章' },
        { kind: 'project', id: 'prj_1', name: '测试项目' },
      ],
      truncated: false,
    },
    {
      id: 'L1',
      label: '业务摘要',
      token_estimate: 120,
      items: [
        { kind: 'character', id: 'char_1', name: '主角', role: 'protagonist' },
        { kind: 'hook', id: 'hk_1', name: '黑玉佩', status: 'OPEN', importance: 0.9 },
        { kind: 'debt', id: 'dbt_1', name: '欠债', severity: 0.7, status: 'open' },
      ],
      truncated: false,
    },
    {
      id: 'L2',
      label: '章节专属',
      token_estimate: 60,
      items: [
        { kind: 'author_style_sample', id: 'asty_a1', name: '雨夜散文', excerpt_len: 320 },
      ],
      truncated: false,
    },
  ],
  total_tokens: 260,
  token_budget: 8000,
  within_budget: true,
};

describe('ContextPreviewPanel', () => {
  beforeEach(() => {
    vi.mocked(contextPreviewApi.preview).mockReset();
  });

  it('renders three layers with token estimates and item counts', async () => {
    vi.mocked(contextPreviewApi.preview).mockResolvedValue(basePreview);
    render(<ContextPreviewPanel chapterId="ch_1" />);

    await waitFor(() => {
      expect(screen.getByTestId('context-preview-panel')).toBeInTheDocument();
    });
    expect(screen.getByTestId('context-preview-layer-L0')).toBeInTheDocument();
    expect(screen.getByTestId('context-preview-layer-L1')).toBeInTheDocument();
    expect(screen.getByTestId('context-preview-layer-L2')).toBeInTheDocument();

    // total / budget 渲染
    expect(screen.getByTestId('context-preview-total')).toHaveTextContent('260');
    expect(screen.getByTestId('context-preview-budget')).toHaveTextContent('8000');
  });

  it('shows empty state when all layers have no items', async () => {
    const empty: ContextPreviewResponse = {
      ...basePreview,
      layers: basePreview.layers.map((l) => ({ ...l, items: [] })),
      total_tokens: 30,
    };
    vi.mocked(contextPreviewApi.preview).mockResolvedValue(empty);
    render(<ContextPreviewPanel chapterId="ch_1" />);

    await waitFor(() => {
      expect(screen.getByText(/该章节暂无已装配的上下文条目/)).toBeInTheDocument();
    });
  });

  it('renders author_style_sample label in L2 items (Sprint 15 / V1.3)', async () => {
    vi.mocked(contextPreviewApi.preview).mockResolvedValue(basePreview);
    render(<ContextPreviewPanel chapterId="ch_1" />);

    await waitFor(() => {
      expect(screen.getByTestId('context-preview-layer-L2')).toBeInTheDocument();
    });
    // KIND_LABEL 新增 author_style_sample → "作者文风样例"
    expect(screen.getByText(/\[作者文风样例\]/)).toBeInTheDocument();
  });

  it('treats 404 as empty preview (no error banner)', async () => {
    // 404 → ApiError；面板应不显示错误
    vi.mocked(contextPreviewApi.preview).mockRejectedValue(
      new ApiError(404, 'chapter not found'),
    );
    render(<ContextPreviewPanel chapterId="ch_missing" />);

    await waitFor(() => {
      expect(screen.getByText(/暂无上下文预览/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/加载上下文预览失败/)).not.toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // V2.0 Wave B 任务二：条件触发动态注入（preview 标记展示）
  // - full：默认；无徽标
  // - summary：徽标「摘要」+ summary_line 文本
  // - suppressed：徽标「已剔除」+ kind=suppressed_*
  // ---------------------------------------------------------------------------
  it('renders full/summary/suppressed injection badges (V2.0 Wave B 任务二)', async () => {
    const previewWithInjection: ContextPreviewResponse = {
      ...basePreview,
      layers: [
        basePreview.layers[0],
        {
          ...basePreview.layers[1],
          items: [
            { kind: 'character', id: 'char_full', name: '林昭', role: 'protagonist', injection: 'full' },
            { kind: 'character', id: 'char_sum', name: '陈风', role: 'antagonist', injection: 'summary', summary_line: '陈风（antagonist）' },
            { kind: 'location', id: 'loc_full', name: '王城', injection: 'full' },
            { kind: 'location', id: 'loc_sum', name: '荒原', injection: 'summary', summary_line: '荒原 — 边境荒野' },
            { kind: 'suppressed_character', id: 'char_supp', name: '隐者', injection: 'suppressed' },
            { kind: 'suppressed_location', id: 'loc_supp', name: '禁地', injection: 'suppressed' },
            { kind: 'suppressed_faction', id: 'fac_supp', name: '刺客会', injection: 'suppressed' },
          ],
        },
        basePreview.layers[2],
      ],
    };
    vi.mocked(contextPreviewApi.preview).mockResolvedValue(previewWithInjection);
    render(<ContextPreviewPanel chapterId="ch_1" />);

    await waitFor(() => {
      expect(screen.getByTestId('context-preview-layer-L1')).toBeInTheDocument();
    });
    // full：默认无徽标（不应有 summary/suppressed 徽标）
    expect(
      screen.queryByTestId('preview-item-injection-character-suppressed'),
    ).not.toBeInTheDocument();
    // summary：徽标 + summary_line
    expect(
      screen.getByTestId('preview-item-injection-character-summary'),
    ).toHaveTextContent('摘要');
    expect(
      screen.getByTestId('preview-item-summary-line-character'),
    ).toHaveTextContent('陈风（antagonist）');
    // suppressed（角色 / 地点 / 势力 三类）
    expect(
      screen.getByTestId('preview-item-injection-suppressed_character-suppressed'),
    ).toHaveTextContent('已剔除');
    expect(
      screen.getByTestId('preview-item-injection-suppressed_location-suppressed'),
    ).toHaveTextContent('已剔除');
    expect(
      screen.getByTestId('preview-item-injection-suppressed_faction-suppressed'),
    ).toHaveTextContent('已剔除');
    // KIND_LABEL 新增项：suppressed_character 显示「角色(已剔除)」
    expect(screen.getByText(/\[角色\(已剔除\)\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[地点\(已剔除\)\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[势力\(已剔除\)\]/)).toBeInTheDocument();
  });
});