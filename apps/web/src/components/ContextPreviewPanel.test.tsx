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
      items: [],
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
      expect(screen.getByText(/该项目暂无上下文数据/)).toBeInTheDocument();
    });
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
});