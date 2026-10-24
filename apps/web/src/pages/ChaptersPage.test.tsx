/** @vitest-environment jsdom */
// ChaptersPage 关键流测试：
// - 列表加载：listByProject 返回 → 渲染表格行
// - 点击「进入」按钮 → 调 navigate 跳详情
// - API 失败 → ErrorBanner 显示错误文本
// - 新建 modal：number 已存在 → 拒绝提交（不调 create）
// - 续规划章节（outline-batch）：开弹窗默认 10 / 请求体口径 / COMPLETED 刷新+提示 /
//   FAILED 提示 / 409 专项文案 / 章数 0 前端拦截

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ChaptersPage } from './ChaptersPage';
import { chaptersApi, modelProfilesApi, projectsApi, workflowsApi } from '../api/endpoints';
import { ApiError } from '../api/client';
import type { Chapter, WorkflowRun, WorkflowRunStatus } from '../api/types';

// ---- API mock ----
// 页面 import 的全部 api 对象都必须出现在工厂里（缺一个就是 undefined）。
vi.mock('../api/endpoints', () => ({
  chaptersApi: {
    listByProject: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    listDrafts: vi.fn(),
    createDraft: vi.fn(),
  },
  projectsApi: {
    outlineBatch: vi.fn(),
  },
  workflowsApi: {
    get: vi.fn(),
  },
  modelProfilesApi: {
    list: vi.fn(),
  },
}));

// ---- fixtures ----
const baseChapter = (overrides: Partial<Chapter> = {}): Chapter => ({
  chapter_id: 'ch_001',
  project_id: 'prj_001',
  number: 1,
  title: '第一章',
  status: 'PLANNED',
  visibility: 'public',
  who_knows: null,
  created_at: '2026-08-24T10:00:00+00:00',
  updated_at: '2026-08-24T10:00:00+00:00',
  plan_json: {},
  ...overrides,
});

// workflowsApi.get 的返回（normalizeRun 之后的手写 WorkflowRun 形状）。
const baseRun = (overrides: Partial<WorkflowRun> = {}): WorkflowRun => ({
  run_id: 'run_ob1',
  workflow_id: 'wf_ob1',
  chapter_id: null,
  status: 'RUNNING' as WorkflowRunStatus,
  current_node: null,
  checkpoint_json: {},
  error: null,
  retry_count: 0,
  started_at: '2026-10-24T10:00:00+00:00',
  ended_at: null,
  nodes: [],
  ...overrides,
});

function renderPage(initialEntries: string[] = ['/projects/prj_001/chapters']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route
          path="/projects/:pid/chapters"
          element={<ChaptersPage />}
        />
        <Route
          path="/projects/:pid/chapters/:cid"
          element={<div data-testid="chapter-detail-stub" />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ChaptersPage - 关键流', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(chaptersApi.listByProject).mockResolvedValue([]);
    vi.mocked(chaptersApi.create).mockResolvedValue(baseChapter({ chapter_id: 'ch_new', number: 2 }));
  });

  it('a) 列表加载：listByProject 返回 → 渲染表格行（章号/标题/状态/进入按钮）', async () => {
    vi.mocked(chaptersApi.listByProject).mockResolvedValue([
      baseChapter({ chapter_id: 'ch_001', number: 1, title: '楔子' }),
      baseChapter({
        chapter_id: 'ch_002',
        number: 2,
        title: null,
        status: 'DRAFTED',
        updated_at: '2026-08-25T12:00:00+00:00',
      }),
    ]);

    renderPage();

    // 等列表行到位
    await waitFor(() => {
      expect(screen.getByTestId('chapter-row-ch_001')).toBeInTheDocument();
    });

    // 章号 + 标题
    expect(screen.getByTestId('chapter-row-ch_001')).toHaveTextContent('第 1 章');
    expect(screen.getByTestId('chapter-row-ch_001')).toHaveTextContent('楔子');

    // ch_002 标题为 null 时显示「未命名」
    expect(screen.getByTestId('chapter-row-ch_002')).toHaveTextContent('未命名');

    // 每行有「进入」按钮
    expect(
      screen.getByTestId('chapter-row-ch_001').querySelector('button.btn--sm'),
    ).toHaveTextContent('进入');
  });

  it('b) 点击「进入」按钮 → navigate 跳转到章节详情路由', async () => {
    vi.mocked(chaptersApi.listByProject).mockResolvedValue([
      baseChapter({ chapter_id: 'ch_007', number: 7 }),
    ]);

    renderPage();

    const row = await waitFor(() => screen.getByTestId('chapter-row-ch_007'));
    const enterBtn = row.querySelector('button.btn--sm') as HTMLButtonElement;
    expect(enterBtn).toHaveTextContent('进入');
    fireEvent.click(enterBtn);

    // 详情页 stub 渲染 = 路由跳转成功
    await waitFor(() => {
      expect(screen.getByTestId('chapter-detail-stub')).toBeInTheDocument();
    });
    // URL 上 /projects/prj_001/chapters/ch_007 已被匹配（stub 命中）
    expect(screen.getByTestId('chapter-detail-stub')).toBeInTheDocument();
  });

  it('c) API 失败：listByProject 抛错 → 显示错误文本，不渲染表格', async () => {
    vi.mocked(chaptersApi.listByProject).mockRejectedValue(
      new Error('网络异常 502'),
    );

    renderPage();

    // 错误文本出现在 ErrorBanner 内
    await waitFor(() => {
      expect(screen.getByText('网络异常 502')).toBeInTheDocument();
    });
    // loading 收尾、表格不渲染
    expect(screen.queryByTestId('chapter-list')).toBeNull();
  });

  it('d) 新建 modal：提交已存在的章号 → 显示错误，不调 create', async () => {
    vi.mocked(chaptersApi.listByProject).mockResolvedValue([
      baseChapter({ chapter_id: 'ch_001', number: 1 }),
    ]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-row-ch_001')).toBeInTheDocument();
    });

    // 打开新建 modal（toolbar 上的「+ 新建章节」）
    fireEvent.click(screen.getByTestId('new-chapter-btn'));

    // suggestedNumber 默认 = 2（list 中最大章号 + 1）
    const numberInput = (await waitFor(() =>
      screen.getByTestId('chapter-number'),
    )) as HTMLInputElement;
    expect(numberInput.value).toBe('2');

    // 用户改成 1（已存在）
    fireEvent.change(numberInput, { target: { value: '1' } });
    // 点保存
    fireEvent.click(screen.getByTestId('chapter-save'));

    // 校验失败：显示「章号 1 已存在」+ 不调 create
    await waitFor(() => {
      expect(screen.getByText(/章号 1 已存在/)).toBeInTheDocument();
    });
    expect(vi.mocked(chaptersApi.create)).not.toHaveBeenCalled();
  });
});

// ---------- V3.10「交互完整」：弹窗键盘可达 + 表格行键盘入口 ----------
describe('ChaptersPage · 弹窗与键盘可达（V3.10）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(chaptersApi.listByProject).mockResolvedValue([baseChapter()]);
  });

  it('新建弹窗：初始焦点入第一个输入框；Esc 关闭', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('chapter-row-ch_001')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('new-chapter-btn'));
    const numberInput = await screen.findByTestId('chapter-number');

    // 初始焦点 = 弹窗内第一个可交互元素
    await waitFor(() => {
      expect(document.activeElement).toBe(numberInput);
    });

    // Esc 关闭（此前自写 modal 没有 Esc，键盘用户被困）
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => {
      expect(screen.queryByTestId('chapter-number')).toBeNull();
    });
  });

  it('表格行键盘入口：行可聚焦（tabIndex=0），Enter 进入章节详情', async () => {
    renderPage();
    const row = await waitFor(() => screen.getByTestId('chapter-row-ch_001'));

    row.focus();
    expect(document.activeElement).toBe(row);

    fireEvent.keyDown(row, { key: 'Enter' });
    await waitFor(() => {
      expect(screen.getByTestId('chapter-detail-stub')).toBeInTheDocument();
    });
  });
});

// ---------- 续规划章节（outline-batch） ----------
describe('ChaptersPage · 续规划章节（outline-batch）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(chaptersApi.listByProject).mockResolvedValue([baseChapter()]);
    vi.mocked(chaptersApi.create).mockResolvedValue(baseChapter());
    // 201 形状 = WorkflowStartAccepted 三键。
    vi.mocked(projectsApi.outlineBatch).mockResolvedValue({
      run_id: 'run_ob1',
      status: 'RUNNING',
      current_node: null,
    });
    // 轮询默认立即到终态（避免用例残留 2s interval）；需要中间态的用例自行覆盖。
    vi.mocked(workflowsApi.get).mockResolvedValue(
      baseRun({ status: 'COMPLETED', ended_at: '2026-10-24T10:01:00+00:00' }),
    );
    vi.mocked(modelProfilesApi.list).mockResolvedValue([]);
  });

  /** 打开页面等列表渲染完 → 点「续规划章节」开弹窗。 */
  async function openBatchModal() {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('chapter-row-ch_001')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('outline-batch-btn'));
    await waitFor(() => {
      expect(screen.getByTestId('outline-batch-modal')).toBeInTheDocument();
    });
  }

  it('a) 点按钮开弹窗：章数默认 10，未选档案下拉无 model_profile_id 提交', async () => {
    await openBatchModal();

    const countInput = screen.getByTestId('outline-batch-count') as HTMLInputElement;
    expect(countInput.value).toBe('10');

    // 轮询 get 挂成 pending：让「规划中…」态稳定可见（卸载时 usePoll cleanup 清 timer）。
    vi.mocked(workflowsApi.get).mockReturnValue(new Promise(() => {}));

    fireEvent.click(screen.getByTestId('outline-batch-submit'));

    await waitFor(() => {
      expect(projectsApi.outlineBatch).toHaveBeenCalledTimes(1);
    });
    // 请求体口径：{ chapter_count: 10 }，**不带** model_profile_id（不是传 null）。
    const body = vi.mocked(projectsApi.outlineBatch).mock.calls[0][1];
    expect(body).toEqual({ chapter_count: 10 });
    expect('model_profile_id' in body).toBe(false);
    // 启动成功后弹窗关闭，按钮进入「规划中…」禁用态。
    await waitFor(() => {
      expect(screen.queryByTestId('outline-batch-modal')).toBeNull();
    });
    const batchBtn = screen.getByTestId('outline-batch-btn') as HTMLButtonElement;
    expect(batchBtn).toHaveTextContent('规划中…');
    expect(batchBtn).toBeDisabled();
    // 新建按钮同时被禁用（防项目级并发 409）。
    expect(screen.getByTestId('new-chapter-btn')).toBeDisabled();
  });

  it('b) 轮询到 COMPLETED：刷新章节列表 + 顶部成功提示「已新增 10 章」', async () => {
    await openBatchModal();
    fireEvent.click(screen.getByTestId('outline-batch-submit'));

    // 终态后 handleBatchTerminal 触发 reload（render 时首调 + 终态后再调 = 2 次）
    await waitFor(() => {
      expect(chaptersApi.listByProject).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(screen.getByTestId('outline-batch-notice')).toHaveTextContent(
        '已新增 10 章',
      );
    });
    // 轮询停了：run 拉取恰好 1 次（首次 tick 即终态，不挂 interval）。
    expect(workflowsApi.get).toHaveBeenCalledTimes(1);
    // 按钮恢复可点。
    await waitFor(() => {
      expect(screen.getByTestId('outline-batch-btn')).toBeEnabled();
    });
  });

  it('c) 轮询到 FAILED：顶部错误提示带 run.error 摘要', async () => {
    vi.mocked(workflowsApi.get).mockResolvedValue(
      baseRun({
        status: 'FAILED',
        error: '规划 LLM 调用超时',
        ended_at: '2026-10-24T10:01:00+00:00',
      }),
    );

    await openBatchModal();
    fireEvent.click(screen.getByTestId('outline-batch-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('outline-batch-notice')).toHaveTextContent(
        '规划失败',
      );
      expect(screen.getByTestId('outline-batch-notice')).toHaveTextContent(
        '规划 LLM 调用超时',
      );
    });
    // 失败不刷新列表（reload 只在 COMPLETED 触发）。
    expect(chaptersApi.listByProject).toHaveBeenCalledTimes(1);
  });

  it('d) 启动 409：弹窗内显示「已有规划任务在进行中」，弹窗不关闭', async () => {
    vi.mocked(projectsApi.outlineBatch).mockRejectedValue(
      new ApiError(409, 'project prj_001 already has an active outline-batch run'),
    );

    await openBatchModal();
    fireEvent.click(screen.getByTestId('outline-batch-submit'));

    await waitFor(() => {
      expect(screen.getByText('已有规划任务在进行中，请等当前任务结束后再试')).toBeInTheDocument();
    });
    // 409 时弹窗仍开着（用户可改参重试或取消），不进入轮询。
    expect(screen.getByTestId('outline-batch-modal')).toBeInTheDocument();
    expect(workflowsApi.get).not.toHaveBeenCalled();
  });

  it('e) 启动 422/400/404：弹窗内展示后端 detail 原文', async () => {
    vi.mocked(projectsApi.outlineBatch).mockRejectedValue(
      new ApiError(400, 'model_profile_id mprof_x not found in model_profiles'),
    );

    await openBatchModal();
    fireEvent.click(screen.getByTestId('outline-batch-submit'));

    await waitFor(() => {
      expect(
        screen.getByText(/model_profile_id mprof_x not found in model_profiles/),
      ).toBeInTheDocument();
    });
  });

  it('f) 章数输 0：前端拦截，不发请求', async () => {
    await openBatchModal();

    const countInput = screen.getByTestId('outline-batch-count') as HTMLInputElement;
    // 注：input 带 min=1 时，输入 0 会被**原生约束校验**拦在 submit 事件之前
    // （jsdom 与真实浏览器同行为）⇒ 根本轮不到 React 的 handleSubmit。
    // 本用例移除 min，专测 React 层的兜底拦截（两层防线各有其位）。
    countInput.removeAttribute('min');
    fireEvent.change(countInput, { target: { value: '0' } });
    fireEvent.click(screen.getByTestId('outline-batch-submit'));

    await waitFor(() => {
      expect(screen.getByText('章数必须是 1~50 的整数')).toBeInTheDocument();
    });
    expect(projectsApi.outlineBatch).not.toHaveBeenCalled();
    expect(workflowsApi.get).not.toHaveBeenCalled();
  });
});