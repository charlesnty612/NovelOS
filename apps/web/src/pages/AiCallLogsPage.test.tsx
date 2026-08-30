import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AiCallLogsPage } from './AiCallLogsPage';
import { aiCallLogsApi } from '../api/endpoints';
import type { AiCallLogDetail, AiCallLogSummary } from '../api/types';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    aiCallLogsApi: {
      list: vi.fn(),
      get: vi.fn(),
    },
  };
});

const baseLog: AiCallLogSummary = {
  call_id: 'aic_001',
  run_id: 'wfr_001',
  node_run_id: 'wfrn_001',
  agent_id: 'agt_writer',
  model_id: 'openai/gpt-4o',
  prompt_version: 'writer:v1',
  latency_ms: 420,
  retry_count: 0,
  error: null,
  token_usage: { prompt: 100, completion: 50, total: 150 },
  cost: 0.012,
  created_at: '2026-08-24T10:00:00+00:00',
};

const baseLogError: AiCallLogSummary = {
  ...baseLog,
  call_id: 'aic_002',
  created_at: '2026-08-24T11:00:00+00:00',
  error: 'output invalid after retry',
  latency_ms: 880,
};

const baseDetail: AiCallLogDetail = {
  ...baseLog,
  input_context_ids: ['ch_1', 'char_2', 'hk_1'],
  output: {
    schema_version: 'writer-output.v1',
    prose: '...',
    self_report: { word_count: 2200 },
  },
};

function renderPage() {
  return render(
    <MemoryRouter>
      <AiCallLogsPage />
    </MemoryRouter>,
  );
}

describe('AiCallLogsPage', () => {
  beforeEach(() => {
    vi.mocked(aiCallLogsApi.list).mockReset();
    vi.mocked(aiCallLogsApi.get).mockReset();
  });

  it('renders list rows for successful and failed logs', async () => {
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([baseLog, baseLogError]);
    // 默认选第一条 → 拉详情
    vi.mocked(aiCallLogsApi.get).mockResolvedValue(baseDetail);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('ai-call-logs-page')).toBeInTheDocument();
    });
    // 两条日志行
    expect(screen.getByTestId('ai-log-row-aic_001')).toBeInTheDocument();
    expect(screen.getByTestId('ai-log-row-aic_002')).toBeInTheDocument();
    // 状态徽章
    expect(screen.getByTestId('ai-log-status-aic_001')).toHaveTextContent('成功');
    expect(screen.getByTestId('ai-log-status-aic_002')).toHaveTextContent('失败');
  });

  it('shows empty state when no logs', async () => {
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([]);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/暂无 AI 调用日志/)).toBeInTheDocument();
    });
  });

  it('loads and renders detail on first render (auto-select first row)', async () => {
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([baseLog]);
    vi.mocked(aiCallLogsApi.get).mockResolvedValue(baseDetail);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('ai-log-detail-aic_001')).toBeInTheDocument();
    });
    // input_context_ids / output 区块都渲染
    expect(screen.getByTestId('ai-log-detail-input-context')).toBeInTheDocument();
    expect(screen.getByTestId('ai-log-detail-output')).toBeInTheDocument();
    // token_usage 文案
    expect(screen.getByText(/prompt=100 completion=50 total=150/)).toBeInTheDocument();
  });

  it('switches detail when clicking another row', async () => {
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([baseLog, baseLogError]);
    vi.mocked(aiCallLogsApi.get).mockImplementation(async (id: string) => {
      if (id === 'aic_002') return { ...baseDetail, call_id: 'aic_002' };
      return baseDetail;
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('ai-log-detail-aic_001')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('ai-log-row-aic_002'));

    await waitFor(() => {
      expect(screen.getByTestId('ai-log-detail-aic_002')).toBeInTheDocument();
    });
    expect(aiCallLogsApi.get).toHaveBeenCalledWith('aic_002');
  });

  it('status badge tri-state: warn prefix shows 告警 with neutral badge class', async () => {
    // 三态徽章：null → 成功 / 普通 error → 失败 / warn: 前缀 → 告警（中性黄/灰）
    const warnLog: AiCallLogSummary = {
      ...baseLog,
      call_id: 'aic_warn',
      created_at: '2026-08-24T12:00:00+00:00',
      error: 'warn: first attempt invalid: no JSON object braces found',
      retry_count: 1,
      latency_ms: 950,
    };
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([baseLog, baseLogError, warnLog]);
    vi.mocked(aiCallLogsApi.get).mockResolvedValue(baseDetail);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('ai-log-row-aic_warn')).toBeInTheDocument();
    });

    const okBadge = screen.getByTestId('ai-log-status-aic_001');
    const errBadge = screen.getByTestId('ai-log-status-aic_002');
    const warnBadge = screen.getByTestId('ai-log-status-aic_warn');

    // 文案三态
    expect(okBadge).toHaveTextContent('成功');
    expect(errBadge).toHaveTextContent('失败');
    expect(warnBadge).toHaveTextContent('告警');

    // 样式三态：成功 = committed；失败 = failed；warn = chapter-warn（中性）
    expect(okBadge.className).toContain('badge--chapter-committed');
    expect(okBadge.className).not.toContain('badge--chapter-failed');
    expect(okBadge.className).not.toContain('badge--chapter-warn');

    expect(errBadge.className).toContain('badge--chapter-failed');
    expect(errBadge.className).not.toContain('badge--chapter-warn');

    expect(warnBadge.className).toContain('badge--chapter-warn');
    expect(warnBadge.className).not.toContain('badge--chapter-failed');
    expect(warnBadge.className).not.toContain('badge--chapter-committed');
  });

  it('reload button triggers list refresh', async () => {
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([baseLog]);
    vi.mocked(aiCallLogsApi.get).mockResolvedValue(baseDetail);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('ai-call-logs-page')).toBeInTheDocument();
    });
    const callsBefore = vi.mocked(aiCallLogsApi.list).mock.calls.length;
    fireEvent.click(screen.getByTestId('ai-logs-reload'));
    await waitFor(() => {
      expect(vi.mocked(aiCallLogsApi.list).mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });
});