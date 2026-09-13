/** @vitest-environment jsdom */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AiCallLogsPage } from './AiCallLogsPage';
import { aiCallLogsApi, projectsApi } from '../api/endpoints';
import type { AiCallLogDetail, AiCallLogSummary, Project } from '../api/types';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    aiCallLogsApi: {
      list: vi.fn(),
      get: vi.fn(),
    },
    projectsApi: {
      list: vi.fn(),
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
  created_at: new Date().toISOString(),
};

const baseLogError: AiCallLogSummary = {
  ...baseLog,
  call_id: 'aic_002',
  created_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
  error: 'output invalid after retry',
  latency_ms: 880,
};

const oldLog: AiCallLogSummary = {
  ...baseLog,
  call_id: 'aic_old',
  created_at: '2026-08-24T11:00:00+00:00',
  error: null,
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

const projects: Project[] = [
  {
    project_id: 'prj_001',
    name: '测试项目',
    status: 'ACTIVE',
    created_at: '2026-08-01T00:00:00+00:00',
    updated_at: '2026-08-01T00:00:00+00:00',
  } as Project,
];

function renderPage(initialEntries: string[] = ['/ai-logs']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <AiCallLogsPage />
    </MemoryRouter>,
  );
}

describe('AiCallLogsPage', () => {
  beforeEach(() => {
    vi.mocked(aiCallLogsApi.list).mockReset();
    vi.mocked(aiCallLogsApi.get).mockReset();
    vi.mocked(projectsApi.list).mockReset();
    vi.mocked(projectsApi.list).mockResolvedValue(projects);
  });

  it('renders list rows for successful and failed logs', async () => {
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([baseLog, baseLogError]);
    vi.mocked(aiCallLogsApi.get).mockResolvedValue(baseDetail);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('ai-call-logs-page')).toBeInTheDocument();
    });
    expect(screen.getByTestId('ai-log-row-aic_001')).toBeInTheDocument();
    expect(screen.getByTestId('ai-log-row-aic_002')).toBeInTheDocument();
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
    expect(screen.getByTestId('ai-log-detail-input-context')).toBeInTheDocument();
    expect(screen.getByTestId('ai-log-detail-output')).toBeInTheDocument();
    // 人读化：Token 用量给出合计与入/出明细（不再是裸 prompt=/completion= 键值）
    expect(screen.getByTestId('ai-log-detail-tokens')).toHaveTextContent(
      '150（入 100 / 出 50）',
    );
  });

  it('选中其他行只拉该条详情，列表不再重拉；回点命中缓存', async () => {
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([baseLog, baseLogError]);
    vi.mocked(aiCallLogsApi.get).mockImplementation(async (id: string) => {
      if (id === 'aic_002') return { ...baseDetail, call_id: 'aic_002' };
      return baseDetail;
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('ai-log-detail-aic_001')).toBeInTheDocument();
    });
    const listCallsBefore = vi.mocked(aiCallLogsApi.list).mock.calls.length;

    fireEvent.click(screen.getByTestId('ai-log-row-aic_002'));

    await waitFor(() => {
      expect(screen.getByTestId('ai-log-detail-aic_002')).toBeInTheDocument();
    });
    expect(vi.mocked(aiCallLogsApi.get)).toHaveBeenCalledWith('aic_002');
    // 选中变化不得触发列表重拉（此前 reload 依赖 selectedId，点一行闪一次全表）
    expect(vi.mocked(aiCallLogsApi.list).mock.calls.length).toBe(listCallsBefore);

    // 回点第一条：详情走缓存，不再重复请求
    fireEvent.click(screen.getByTestId('ai-log-row-aic_001'));
    await waitFor(() => {
      expect(screen.getByTestId('ai-log-detail-aic_001')).toBeInTheDocument();
    });
    expect(vi.mocked(aiCallLogsApi.get)).toHaveBeenCalledTimes(2);
  });

  it('status badge tri-state: warn prefix shows 告警 with neutral badge class', async () => {
    const warnLog: AiCallLogSummary = {
      ...baseLog,
      call_id: 'aic_warn',
      created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
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

    expect(okBadge).toHaveTextContent('成功');
    expect(errBadge).toHaveTextContent('失败');
    expect(warnBadge).toHaveTextContent('告警');

    expect(okBadge.className).toContain('badge--chapter-committed');
    expect(okBadge.className).not.toContain('badge--chapter-failed');
    expect(okBadge.className).not.toContain('badge--chapter-warn');

    expect(errBadge.className).toContain('badge--chapter-failed');
    expect(errBadge.className).not.toContain('badge--chapter-warn');

    expect(warnBadge.className).toContain('badge--chapter-warn');
    expect(warnBadge.className).not.toContain('badge--chapter-failed');
    expect(warnBadge.className).not.toContain('badge--chapter-committed');
  });

  it('过滤条：结果=失败只留失败行；时间=今天隐藏旧日志', async () => {
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([baseLog, baseLogError, oldLog]);
    vi.mocked(aiCallLogsApi.get).mockResolvedValue(baseDetail);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('ai-log-row-aic_old')).toBeInTheDocument();
    });

    // 结果状态过滤（前端内存过滤）
    fireEvent.change(screen.getByTestId('ai-logs-filter-result'), {
      target: { value: 'fail' },
    });
    await waitFor(() => {
      expect(screen.queryByTestId('ai-log-row-aic_001')).toBeNull();
    });
    expect(screen.getByTestId('ai-log-row-aic_002')).toBeInTheDocument();
    expect(screen.queryByTestId('ai-log-row-aic_old')).toBeNull();

    // 时间过滤：今天 → 上月那条被排除（在「全部结果」下验证）
    fireEvent.change(screen.getByTestId('ai-logs-filter-result'), {
      target: { value: 'all' },
    });
    fireEvent.change(screen.getByTestId('ai-logs-filter-time'), {
      target: { value: 'today' },
    });
    await waitFor(() => {
      expect(screen.queryByTestId('ai-log-row-aic_old')).toBeNull();
    });
    expect(screen.getByTestId('ai-log-row-aic_001')).toBeInTheDocument();
  });

  it('过滤后无结果：空态提示可清空筛选，点击后恢复', async () => {
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([baseLog]);
    vi.mocked(aiCallLogsApi.get).mockResolvedValue(baseDetail);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('ai-log-row-aic_001')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId('ai-logs-filter-result'), {
      target: { value: 'fail' },
    });

    await waitFor(() => {
      expect(screen.getByText(/没有符合条件的日志/)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('清空筛选'));

    await waitFor(() => {
      expect(screen.getByTestId('ai-log-row-aic_001')).toBeInTheDocument();
    });
  });

  it('项目过滤走服务端参数（project_id）', async () => {
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([baseLog]);
    vi.mocked(aiCallLogsApi.get).mockResolvedValue(baseDetail);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('ai-log-row-aic_001')).toBeInTheDocument();
    });

    const filter = screen.getByTestId('ai-logs-filter-project');
    // 下拉选项来自 projectsApi.list
    await waitFor(() => {
      expect(within(filter).getByText('测试项目')).toBeInTheDocument();
    });
    fireEvent.change(filter, { target: { value: 'prj_001' } });

    await waitFor(() => {
      expect(vi.mocked(aiCallLogsApi.list)).toHaveBeenCalledWith({
        limit: 200,
        project_id: 'prj_001',
      });
    });
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

  it('带 ?project= 来源时返回链接回该项目的 AI 设置页', async () => {
    vi.mocked(aiCallLogsApi.list).mockResolvedValue([baseLog]);
    vi.mocked(aiCallLogsApi.get).mockResolvedValue(baseDetail);

    renderPage(['/ai-logs?project=prj_001']);

    await waitFor(() => {
      expect(screen.getByText(/返回项目 AI 设置/)).toBeInTheDocument();
    });
    // 来源项目同时作为项目筛选初值（服务端过滤）
    expect(vi.mocked(aiCallLogsApi.list)).toHaveBeenCalledWith({
      limit: 200,
      project_id: 'prj_001',
    });
  });
});
