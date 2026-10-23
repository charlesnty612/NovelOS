/** @vitest-environment jsdom */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ChapterDetailPage } from './ChapterDetailPage';
import {
  chaptersApi,
  modelProfilesApi,
  projectsApi,
  qualityApi,
  workflowsApi,
} from '../api/endpoints';
import { fetchProjectEffectiveSettings } from '../api/projectSettings';
import { ApiError } from '../api/client';
import type {
  Chapter,
  Draft,
  ModelProfile,
  Project,
  WorkflowRun,
  WorkflowStartResponse,
} from '../api/types';

// ---- API mock ----
vi.mock('../api/projectSettings', () => ({
  // 「已解析有效值」端点（2026-10-23 返工新增）：每个用例自己设有效值。
  fetchProjectEffectiveSettings: vi.fn(async () => ({
    fast_mode_effective: false,
    fast_mode_source: 'off' as 'project' | 'env' | 'off',
  })),
}));

vi.mock('../api/endpoints', () => ({
  chaptersApi: {
    get: vi.fn(),
    listDrafts: vi.fn(),
    delete: vi.fn(),
    update: vi.fn(),
    createDraft: vi.fn(),
    reopenChapter: vi.fn(),
    setRevisionNote: vi.fn(),
  },
  // 快通道（2026-10-14）：章节页要读项目级 fast_mode 并在开关变更时 PATCH。
  // 默认实现直接给「项目未开快通道」：本文件有 11 个 describe 各带一份 beforeEach，
  // 把默认值放工厂里才能保证每个 describe 都拿到可 await 的返回值
  // （放 beforeEach 只会覆盖其中一个 describe，其余会撞 undefined.then）。
  projectsApi: {
    get: vi.fn(async () => ({
      project_id: 'prj_test',
      name: 'test',
      premise: null,
      genre: null,
      target_words: null,
      status: 'ACTIVE',
      created_at: '',
      updated_at: '',
      fast_mode: null,
    })),
    update: vi.fn(async () => ({ project_id: 'prj_test' })),
  },
  qualityApi: {
    latest: vi.fn(),
    judge: vi.fn(),
  },
  workflowsApi: {
    listByProject: vi.fn(),
    get: vi.fn(),
    startPlan: vi.fn(),
    startWrite: vi.fn(),
    startReview: vi.fn(),
    startCommit: vi.fn(),
    resume: vi.fn(),
    resumeInit: vi.fn(),
    cancelRun: vi.fn(),
  },
  modelProfilesApi: {
    // 默认给可 await 的空列表：章节页 mount 即调 list，未给默认值的 describe
    // 会撞 `undefined.then`（本文件第 34 行那条注释记的就是这个坑）。
    // 既有 describe 都各自 mockReset 过，不受此默认值影响。
    list: vi.fn(async () => []),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    test: vi.fn(),
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

const planResponse = (runId = 'wfr_plan_001'): WorkflowStartResponse => ({
  run_id: runId,
  status: 'PENDING',
  current_node: null,
  pause_payload: null,
});

function renderPage(initialEntries: string[] = ['/projects/prj_001/chapters/ch_001']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route
          path="/projects/:pid/chapters/:cid"
          element={<ChapterDetailPage />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// 运行记录面板于 2026-09-13 下置为分段 tab（默认「草稿」，面板懒挂载）：
// 依赖面板内容（run 列表 / 节点时间线 / 审批卡）的用例先切到「运行记录」tab。
// 等待 tab 出现（chapter 加载完成）再点击，避免与首屏加载竞态。
async function openRunsTab() {
  fireEvent.click(await waitFor(() => screen.getByTestId('cdp-tab-runs')));
  await waitFor(() => {
    expect(screen.getByTestId('cdp-section-runs')).toBeVisible();
  });
}

// PAUSED 的 chapter-review run（pause_payload 含 critic_report 建议，
// 供运行记录 tab / 审批卡勾选态用例复用）。
function buildPausedReviewRun(overrides: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    run_id: 'wfr_paused_001',
    workflow_id: 'chapter-review',
    chapter_id: 'ch_001',
    status: 'PAUSED',
    current_node: 'author_review',
    checkpoint_json: {
      author_review: {
        __pause_payload__: {
          stage: 'chapter-review',
          message: '请人工决议',
          critic_status: 'ok',
          critic_report: {
            overall_comment: '整体尚可',
            strengths: [],
            issues: [
              {
                category: 'logic',
                severity: 'high',
                quote: '主角突然同意',
                suggestion: '补一个人物动机',
              },
            ],
          },
        },
      },
    },
    error: null,
    retry_count: 0,
    started_at: '2026-08-24T10:00:00+00:00',
    ended_at: null,
    nodes: [],
    workflow_name: 'chapter-review',
    ...overrides,
  } as unknown as WorkflowRun;
}

describe('ChapterDetailPage - 生成计划防呆', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.delete).mockReset();
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(chaptersApi.createDraft).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startPlan).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(workflowsApi.startReview).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.resume).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.cancelRun).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(modelProfilesApi.create).mockReset();
    vi.mocked(modelProfilesApi.update).mockReset();
    vi.mocked(modelProfilesApi.remove).mockReset();
    vi.mocked(modelProfilesApi.test).mockReset();

    // 默认空数据 / 404 容错，避免 useApiCall 进入 `.then` 之前的 undefined.then 错误
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    // 默认无档案：不渲染 select 下拉（不阻塞现有用例）
    vi.mocked(modelProfilesApi.list).mockResolvedValue([]);
    // 给未在具体 it() 中覆盖的 startXxx / get 一个默认成功响应（防止 reload 调用链断裂）
    vi.mocked(workflowsApi.startPlan).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });
    // detail = useApiCall(() => workflowsApi.get(selectedRunId ?? '')) —— mount 即触发
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
  });

  it('章节已有 plan_json 时点击「生成计划」会弹窗确认；取消则不调 startPlan', async () => {
    const chWithPlan = baseChapter({
      plan_json: {
        chapter_goal: '旧目标',
        expected_word_count: 3000,
      },
    });
    vi.mocked(chaptersApi.get).mockResolvedValue(chWithPlan);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });

    const planBtn = screen.getByTestId('wf-btn-plan');
    // 章节状态为 PLANNED 时 plan 按钮可用（依据 chapterState 状态机）
    expect(planBtn).not.toBeDisabled();

    fireEvent.click(planBtn);

    // V3.22「交互反馈统一」：用 ConfirmDialog（testid=plan-reconfirm）替代 window.confirm。
    const dialog = await waitFor(() =>
      screen.getByTestId('plan-reconfirm'),
    );
    expect(within(dialog).getByText(/章节已有计划/)).toBeInTheDocument();
    // 点「取消」不应调 startPlan
    const cancelBtn = within(dialog).getByTestId('plan-reconfirm-cancel');
    fireEvent.click(cancelBtn);

    await waitFor(() => {
      expect(screen.queryByTestId('plan-reconfirm')).not.toBeInTheDocument();
    });
    expect(vi.mocked(workflowsApi.startPlan)).not.toHaveBeenCalled();
  });

  it('章节已有 plan_json 时确认通过后会调 startPlan', async () => {
    const chWithPlan = baseChapter({
      plan_json: { chapter_goal: '旧目标', expected_word_count: 3000 },
    });
    vi.mocked(chaptersApi.get).mockResolvedValue(chWithPlan);
    vi.mocked(workflowsApi.startPlan).mockResolvedValue(planResponse('wfr_plan_002'));

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('wf-btn-plan'));

    // 点 ConfirmDialog 确认按钮 → 重跑 handleStartWorkflow
    const dialog = await waitFor(() =>
      screen.getByTestId('plan-reconfirm'),
    );
    fireEvent.click(within(dialog).getByTestId('plan-reconfirm-confirm'));

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startPlan)).toHaveBeenCalledTimes(1);
    });
    expect(vi.mocked(workflowsApi.startPlan)).toHaveBeenCalledWith(
      'prj_001',
      'ch_001',
      undefined,
    );
  });

  it('章节无 plan_json 时不弹确认，直接调 startPlan', async () => {
    const chNoPlan = baseChapter({ plan_json: {} });
    vi.mocked(chaptersApi.get).mockResolvedValue(chNoPlan);
    vi.mocked(workflowsApi.startPlan).mockResolvedValue(planResponse('wfr_plan_003'));

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('wf-btn-plan'));

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startPlan)).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByTestId('plan-reconfirm')).not.toBeInTheDocument();
  });

  it('非 plan 操作（write/review/commit）即使有 plan_json 也不弹确认', async () => {
    // 把章节推到 DRAFTED 让 write 按钮可用
    const chDrafted = baseChapter({
      status: 'DRAFTED',
      plan_json: { chapter_goal: 'x', expected_word_count: 3000 },
    });
    vi.mocked(chaptersApi.get).mockResolvedValue(chDrafted);
    vi.mocked(workflowsApi.startWrite).mockResolvedValue({
      ...planResponse('wfr_write_001'),
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });

    const writeBtn = screen.getByTestId('wf-btn-write');
    expect(writeBtn).not.toBeDisabled();

    fireEvent.click(writeBtn);

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startWrite)).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByTestId('plan-reconfirm')).not.toBeInTheDocument();
  });
});

// ---- helpers for running-banner tests ----
function buildNodeRun(
  nodeId: string,
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'SKIPPED',
  startedAt = '2026-08-24T10:00:00+00:00',
  endedAt: string | null = null,
) {
  return {
    node_run_id: `nrun_${nodeId}`,
    run_id: 'wfr_running_001',
    node_id: nodeId,
    agent_id: null,
    status,
    input_json: {},
    output_json: null,
    prompt_version: null,
    model_id: null,
    token_usage_json: null,
    latency_ms: null,
    error: null,
    started_at: startedAt,
    ended_at: endedAt,
  };
}

function buildRunningDetail(
  overrides: Partial<WorkflowRun> = {},
): WorkflowRun {
  return {
    run_id: 'wfr_running_001',
    workflow_id: 'chapter-write',
    chapter_id: 'ch_001',
    status: 'RUNNING',
    current_node: 'writer',
    checkpoint_json: {},
    error: null,
    retry_count: 0,
    started_at: new Date(Date.now() - 45_000).toISOString(),
    ended_at: null,
    nodes: [
      buildNodeRun('planner', 'COMPLETED'),
      buildNodeRun('writer', 'RUNNING'),
      buildNodeRun('reviewer', 'PENDING'),
      buildNodeRun('committer', 'PENDING'),
    ],
    workflow_name: 'chapter-write',
    pause_payload: null,
    ...overrides,
  };
}

describe('ChapterDetailPage - 工作流运行中横幅', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.delete).mockReset();
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(chaptersApi.createDraft).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startPlan).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(workflowsApi.startReview).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.resume).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.cancelRun).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(modelProfilesApi.create).mockReset();
    vi.mocked(modelProfilesApi.update).mockReset();
    vi.mocked(modelProfilesApi.remove).mockReset();
    vi.mocked(modelProfilesApi.test).mockReset();

    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([]);
    vi.mocked(workflowsApi.startPlan).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });
  });

  it('a) 有 RUNNING run 时横幅显示「写正文」+「第 X/Y 步」+「已运行」', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const runningList: WorkflowRun[] = [
      // list 行的 nodes 为空数组（list 不返回 nodes），由 poll 补详情
      {
        run_id: 'wfr_running_001',
        workflow_id: 'chapter-write',
        chapter_id: 'ch_001',
        status: 'RUNNING',
        current_node: 'writer',
        checkpoint_json: {},
        error: null,
        retry_count: 0,
        started_at: new Date(Date.now() - 45_000).toISOString(),
        ended_at: null,
        nodes: [],
        workflow_name: 'chapter-write',
      } as WorkflowRun,
    ];
    vi.mocked(workflowsApi.listByProject).mockResolvedValue(runningList);
    vi.mocked(workflowsApi.get).mockResolvedValue(buildRunningDetail());

    renderPage();

    const banner = await waitFor(() => screen.getByTestId('workflow-running-banner'));
    expect(banner).toBeInTheDocument();
    // 动作中文名 + workflow_name
    expect(banner.textContent).toMatch(/写正文/);
    expect(banner.textContent).toMatch(/chapter-write/);
    // 节点进度
    expect(banner.textContent).toMatch(/writer/);
    expect(banner.textContent).toMatch(/第\s*2\s*\/\s*4\s*步/);
    // 已运行
    expect(banner.textContent).toMatch(/已运行/);
  });

  // V1.5 横幅节点选取修正：当 current_node 仍指向已完成节点，但下一个节点已 RUNNING 时，
  //   横幅应显示真正在跑的节点（而不是 current_node 指向的已完成节点）。
  it('a1) current_node 滞后：实际 RUNNING 节点优先于 detail.current_node', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const runningList: WorkflowRun[] = [
      {
        run_id: 'wfr_running_005',
        workflow_id: 'chapter-write',
        chapter_id: 'ch_001',
        status: 'RUNNING',
        current_node: 'scene_planner', // 后端尚未更新,仍指上一个已完成节点
        checkpoint_json: {},
        error: null,
        retry_count: 0,
        started_at: new Date(Date.now() - 600_000).toISOString(),
        ended_at: null,
        nodes: [],
        workflow_name: 'chapter-write',
      } as WorkflowRun,
    ];
    vi.mocked(workflowsApi.listByProject).mockResolvedValue(runningList);
    // detail 含三个节点：scene_planner 已完成、writer 正在 RUNNING、reviewer PENDING
    // current_node 仍指 scene_planner（模拟后端只更新于节点完成）
    vi.mocked(workflowsApi.get).mockResolvedValue(
      buildRunningDetail({
        run_id: 'wfr_running_005',
        current_node: 'scene_planner',
        started_at: new Date(Date.now() - 600_000).toISOString(),
        nodes: [
          buildNodeRun('load_plan', 'COMPLETED', new Date(Date.now() - 590_000).toISOString()),
          buildNodeRun('scene_planner', 'COMPLETED', new Date(Date.now() - 540_000).toISOString()),
          buildNodeRun('writer', 'RUNNING', new Date(Date.now() - 5).toISOString()),
          buildNodeRun('reviewer', 'PENDING', '2026-08-24T10:00:00+00:00'),
        ],
      }),
    );

    renderPage();

    const banner = await waitFor(() => screen.getByTestId('workflow-running-banner'));
    // 横幅应展示实际 RUNNING 的 writer,而非 detail.current_node 指向的 scene_planner
    expect(banner.textContent).toMatch(/writer/);
    expect(banner.textContent).toMatch(/第\s*3\s*\/\s*4\s*步/);
    expect(banner.textContent).not.toMatch(/scene_planner（第/);
    // data-current-node 也应跟随真实展示节点,便于外部断言与监控
    expect(banner.getAttribute('data-current-node')).toBe('writer');
  });

  // V1.5 回归：nodes 中无 RUNNING 时（如节点状态尚未刷新 / 全 PENDING），仍按 detail.current_node 兜底展示。
  it('a2) nodes 中无 RUNNING 时回退到 detail.current_node', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([
      {
        run_id: 'wfr_running_006',
        workflow_id: 'chapter-write',
        chapter_id: 'ch_001',
        status: 'RUNNING',
        current_node: 'author_review', // 后端指向 author_review（但 nodes 都还没切到 RUNNING）
        checkpoint_json: {},
        error: null,
        retry_count: 0,
        started_at: new Date(Date.now() - 60_000).toISOString(),
        ended_at: null,
        nodes: [],
        workflow_name: 'chapter-write',
      } as WorkflowRun,
    ]);
    // 节点列表里没有 RUNNING 状态（全部 PENDING，模拟节点状态尚未刷新窗口）
    vi.mocked(workflowsApi.get).mockResolvedValue(
      buildRunningDetail({
        run_id: 'wfr_running_006',
        current_node: 'author_review',
        nodes: [
          buildNodeRun('planner', 'PENDING'),
          buildNodeRun('writer', 'PENDING'),
          buildNodeRun('author_review', 'PENDING'),
          buildNodeRun('committer', 'PENDING'),
        ],
      }),
    );

    renderPage();

    const banner = await waitFor(() => screen.getByTestId('workflow-running-banner'));
    // 没有任何 RUNNING 时,按 detail.current_node 兜底展示 author_review
    expect(banner.textContent).toMatch(/author_review/);
    expect(banner.getAttribute('data-current-node')).toBe('author_review');
  });

  // V1.5 回归：已运行时长应从展示节点的 started_at 起算,而非 run.started_at。
  //   本用例构造 run.started_at 早 5 分钟、节点 started_at 仅几秒前,
  //   断言 banner 「已运行」更接近节点口径（数十秒以内,而不是 5 分钟量级）。
  it('a3) 已运行时长按展示节点 started_at 起算（而非 run.started_at）', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([
      {
        run_id: 'wfr_running_007',
        workflow_id: 'chapter-write',
        chapter_id: 'ch_001',
        status: 'RUNNING',
        current_node: 'writer',
        checkpoint_json: {},
        error: null,
        retry_count: 0,
        started_at: new Date(Date.now() - 300_000).toISOString(), // run 启动 5 分钟前
        ended_at: null,
        nodes: [],
        workflow_name: 'chapter-write',
      } as WorkflowRun,
    ]);
    vi.mocked(workflowsApi.get).mockResolvedValue(
      buildRunningDetail({
        run_id: 'wfr_running_007',
        started_at: new Date(Date.now() - 300_000).toISOString(),
        nodes: [
          buildNodeRun('planner', 'COMPLETED', new Date(Date.now() - 290_000).toISOString()),
          // writer 节点 started_at 距现在几秒;已运行应是个位数秒,绝不该是 5 分钟
          buildNodeRun('writer', 'RUNNING', new Date(Date.now() - 5_000).toISOString()),
          buildNodeRun('reviewer', 'PENDING'),
          buildNodeRun('committer', 'PENDING'),
        ],
      }),
    );

    renderPage();

    const banner = await waitFor(() => screen.getByTestId('workflow-running-banner'));
    // 提取「已运行 X 秒」/「已运行 X 分」片段
    const text = banner.textContent ?? '';
    const m = text.match(/已运行\s*([0-9]+)\s*分(?:\s*([0-9]+)\s*秒)?/);
    if (m) {
      // 命中「X 分」格式：旧口径会跑到 5 分；新口径应为 0 分（节点才 5 秒）
      const minutes = parseInt(m[1], 10);
      expect(minutes).toBe(0);
    } else {
      // 命中「X 秒」格式：旧口径会是 ~300s；新口径应 ≤ 10s
      const m2 = text.match(/已运行\s*([0-9]+)\s*秒/);
      expect(m2).not.toBeNull();
      const seconds = parseInt(m2![1], 10);
      expect(seconds).toBeLessThan(30);
    }
  });

  it('b) run 终态（COMPLETED）时横幅消失', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    // 列表返回 RUNNING → poll 拿到 COMPLETED 后 activeRun 消失
    const runningList: WorkflowRun[] = [
      {
        run_id: 'wfr_running_002',
        workflow_id: 'chapter-write',
        chapter_id: 'ch_001',
        status: 'RUNNING',
        current_node: null,
        checkpoint_json: {},
        error: null,
        retry_count: 0,
        started_at: new Date().toISOString(),
        ended_at: null,
        nodes: [],
        workflow_name: 'chapter-write',
      } as WorkflowRun,
    ];
    // 首轮 list 显示 RUNNING（横幅应显示），随后 run 到达终态（横幅应消失）。
    //
    // ⚠️ 夹具必须**自洽**：列表与详情说的是**同一个 run** 的状态 ⇒ 用同一个
    // 时钟驱动。此前两条 mock 各用各的调用计数，靠「挂载时那次 GET /runs/null」
    // 把计数错开一格才碰巧对上 —— 那正是「把已知 bug 写进夹具」的形状
    // （T7 修掉 null-id 首拉后立刻暴露：poll 只拿到 RUNNING、列表却报终态，
    //  横幅永远不消失）。
    const runStartedAt = Date.now();
    const runFinished = () => Date.now() - runStartedAt > 1000;
    vi.mocked(workflowsApi.get).mockImplementation(async () =>
      runFinished()
        ? buildRunningDetail({
            status: 'COMPLETED',
            ended_at: new Date().toISOString(),
          })
        : buildRunningDetail(),
    );
    vi.mocked(workflowsApi.listByProject).mockImplementation(async () =>
      runFinished()
        ? [
            {
              ...runningList[0],
              status: 'COMPLETED',
              ended_at: new Date().toISOString(),
            } as WorkflowRun,
          ]
        : runningList,
    );

    renderPage();

    // 第一阶段：横幅应显示
    await waitFor(() => {
      expect(screen.getByTestId('workflow-running-banner')).toBeInTheDocument();
    });
    // 第二阶段：poll 拿到终态后（2s 间隔），横幅应消失
    await waitFor(
      () => {
        expect(screen.queryByTestId('workflow-running-banner')).toBeNull();
      },
      { timeout: 8000 },
    );
  });

  it('c) nodes 缺失时横幅仍显示（退化文案，不崩）', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([
      {
        run_id: 'wfr_running_003',
        workflow_id: 'chapter-write',
        chapter_id: 'ch_001',
        status: 'RUNNING',
        current_node: null,
        checkpoint_json: {},
        error: null,
        retry_count: 0,
        started_at: new Date().toISOString(),
        ended_at: null,
        nodes: [],
        workflow_name: 'chapter-write',
      } as WorkflowRun,
    ]);
    // poll 返回的详情不带 nodes（list 行退化路径）
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_running_003',
      workflow_id: 'chapter-write',
      chapter_id: 'ch_001',
      status: 'RUNNING',
      current_node: null,
      checkpoint_json: {},
      error: null,
      retry_count: 0,
      started_at: new Date().toISOString(),
      ended_at: null,
      nodes: [],
      workflow_name: 'chapter-write',
    } as WorkflowRun);

    renderPage();

    const banner = await waitFor(() => screen.getByTestId('workflow-running-banner'));
    expect(banner).toBeInTheDocument();
    // 不应抛错；显示「执行中」（无节点进度）+ 已运行
    expect(banner.textContent).toMatch(/执行中/);
    expect(banner.textContent).toMatch(/已运行/);
    // 不应包含「第 X/Y 步」
    expect(banner.textContent).not.toMatch(/第\s*\d+\s*\/\s*\d+\s*步/);
  });

  it('d) FAILED/PAUSED 时横幅消失（终态不显示）', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    // 初始 RUNNING 让横幅先出现
    const runningRow: WorkflowRun = {
      run_id: 'wfr_running_004',
      workflow_id: 'chapter-write',
      chapter_id: 'ch_001',
      status: 'RUNNING',
      current_node: null,
      checkpoint_json: {},
      error: null,
      retry_count: 0,
      started_at: new Date().toISOString(),
      ended_at: null,
      nodes: [],
      workflow_name: 'chapter-write',
    };
    // 夹具自洽（2026-10-23 返工）：列表与详情用**同一个时钟**驱动
    // （此前各用各的调用计数，靠挂载时那次 GET /runs/null 错开一格才碰巧对上）。
    const runStartedAt = Date.now();
    const runFinished = () => Date.now() - runStartedAt > 1000;
    vi.mocked(workflowsApi.listByProject).mockImplementation(async () =>
      runFinished()
        ? [
            {
              ...runningRow,
              status: 'FAILED',
              ended_at: new Date().toISOString(),
              error: '节点失败',
            },
          ]
        : [runningRow],
    );
    vi.mocked(workflowsApi.get).mockImplementation(async () =>
      runFinished()
        ? buildRunningDetail({
            status: 'FAILED',
            ended_at: new Date().toISOString(),
            error: '节点失败',
          })
        : buildRunningDetail(),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('workflow-running-banner')).toBeInTheDocument();
    });
    // 第二轮 poll 后变 FAILED：横幅应消失
    await waitFor(
      () => {
        expect(screen.queryByTestId('workflow-running-banner')).toBeNull();
      },
      { timeout: 8000 },
    );
  });

  // ---- 自动改稿回路过渡横幅（revise-loop-banner）----

  function mockReviewRunPending() {
    // 章节 DRAFTED + 一个 PAUSED 的 review run（审批卡片出现）
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const reviewRun: WorkflowRun = {
      run_id: 'wfr_review_001',
      workflow_id: 'chapter-review',
      chapter_id: 'ch_001',
      status: 'PAUSED',
      current_node: 'author_review',
      checkpoint_json: {},
      error: null,
      retry_count: 0,
      started_at: new Date(Date.now() - 60_000).toISOString(),
      ended_at: null,
      nodes: [],
      workflow_name: 'chapter-review',
    } as WorkflowRun;
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([reviewRun]);
    // detail（选中 run）需带 pause_payload 才会渲染审批卡片
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_review_001',
      workflow_id: 'chapter-review',
      chapter_id: 'ch_001',
      status: 'PAUSED',
      current_node: 'author_review',
      checkpoint_json: {
        author_review: {
          __pause_payload__: {
            stage: 'chapter-review',
            critic_status: 'completed',
          },
        },
      },
      error: null,
      retry_count: 0,
      started_at: new Date(Date.now() - 60_000).toISOString(),
      ended_at: null,
      nodes: [],
      workflow_name: 'chapter-review',
      pause_payload: {
        stage: 'chapter-review',
        critic_status: 'completed',
      },
    } as unknown as WorkflowRun);
  }

  it('e) 驳回（approved=false）→ 不触发自动回路,不显示 revise-loop-banner', async () => {
    mockReviewRunPending();
    // resume 是异步挂起的（模拟后端处理中）
    let resolveResume!: (v: WorkflowStartResponse) => void;
    vi.mocked(workflowsApi.resume).mockImplementation(
      () => new Promise((res) => { resolveResume = res as (v: WorkflowStartResponse) => void; }),
    );

    renderPage();
    // 运行记录面板已下置为分段 tab：先切到「运行记录」再断言审批卡
    await openRunsTab();

    // 审批卡片出现（PAUSED review run）
    const approveBtn = await waitFor(() => screen.getByTestId('approval-reject'));
    // 点击「驳回」→ handleResume(approved=false) → 不再启动 reviseLooping（纯驳回由后端 FAILED(rejected) 收尾）
    fireEvent.click(approveBtn);

    // 等候一轮 microtask 让 setState 落定
    await waitFor(() => {
      expect(vi.mocked(workflowsApi.resume)).toHaveBeenCalled();
    });
    // 纯驳回不显示回路横幅
    expect(screen.queryByTestId('revise-loop-banner')).toBeNull();

    // resolve 兜底清理,避免未完成的 promise 影响后续用例
    resolveResume!({ run_id: 'wfr_review_001', status: 'COMPLETED', current_node: null, pause_payload: null } as WorkflowStartResponse);
  });

  it('f) 批准（approved=true）→ 不显示 revise-loop-banner', async () => {
    mockReviewRunPending();
    vi.mocked(workflowsApi.resume).mockResolvedValue({ status: 'COMPLETED' } as never);

    renderPage();
    await openRunsTab();

    const approveBtn = await waitFor(() => screen.getByTestId('approval-approve'));
    fireEvent.click(approveBtn);

    // 批准不触发回路 → 无过渡横幅
    await waitFor(() => {
      expect(screen.queryByTestId('revise-loop-banner')).toBeNull();
    });
  });

  it('g) 驳回并改稿（revise=true）→ 显示 revise-loop-banner', async () => {
    mockReviewRunPending();
    let resolveResume!: (v: WorkflowStartResponse) => void;
    vi.mocked(workflowsApi.resume).mockImplementation(
      () => new Promise((res) => { resolveResume = res as (v: WorkflowStartResponse) => void; }),
    );

    renderPage();
    await openRunsTab();

    // 点击 ApprovalCard 上的「驳回并改稿」按钮(独立 testid,带 revise=true),
    // → handleResume(false, { revise: true }) → 触发自动改稿回路。
    const reviseBtn = await waitFor(() => screen.getByTestId('approval-revise'));
    fireEvent.click(reviseBtn);

    await waitFor(() => {
      expect(screen.getByTestId('revise-loop-banner')).toBeInTheDocument();
    });
    expect(screen.getByTestId('revise-loop-banner').textContent).toMatch(/自动改稿回路进行中/);
    resolveResume!({ run_id: 'wfr_review_001', status: 'COMPLETED', current_node: null, pause_payload: null } as WorkflowStartResponse);
    await waitFor(() => {
      expect(screen.queryByTestId('revise-loop-banner')).toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// 按次选择模型档案：plan/write/review/commit 四个按钮从下拉选择 profile_id，
// 启动后请求 payload 带 model_overrides（commit 走 observer 覆盖键，V3.9.4 起）。
// ---------------------------------------------------------------------------

function buildProfile(
  overrides: Partial<ModelProfile> = {},
): ModelProfile {
  return {
    profile_id: overrides.profile_id ?? 'mp_default',
    name: overrides.name ?? '默认档案',
    provider: overrides.provider ?? 'mock',
    model: overrides.model ?? 'mock-1',
    params: overrides.params ?? {},
    enabled: overrides.enabled ?? 1,
    has_api_key: overrides.has_api_key ?? true,
    created_at: overrides.created_at ?? '2026-08-24T10:00:00+00:00',
    updated_at: overrides.updated_at ?? '2026-08-24T10:00:00+00:00',
  };
}

describe('ChapterDetailPage - 按次模型档案选择', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.delete).mockReset();
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(chaptersApi.createDraft).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startPlan).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(workflowsApi.startReview).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.resume).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.cancelRun).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(modelProfilesApi.create).mockReset();
    vi.mocked(modelProfilesApi.update).mockReset();
    vi.mocked(modelProfilesApi.remove).mockReset();
    vi.mocked(modelProfilesApi.test).mockReset();

    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
    vi.mocked(workflowsApi.startPlan).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });
  });

  it('a) 选择档案后点「写正文」：payload 含 model_overrides={creative_writing: <id>}', async () => {
    const creativeId = 'mp_creative_42';
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    vi.mocked(modelProfilesApi.list).mockResolvedValue([
      buildProfile({ profile_id: creativeId, name: '创意写作-甲', provider: 'openai', model: 'gpt-x' }),
      buildProfile({ profile_id: 'mp_other', name: '其他' }),
    ]);
    vi.mocked(workflowsApi.startWrite).mockResolvedValue({
      run_id: 'wfr_write_override',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });
    const select = await waitFor(() =>
      screen.getByTestId('wf-model-select-write'),
    );
    fireEvent.change(select, { target: { value: creativeId } });

    const writeBtn = screen.getByTestId('wf-btn-write');
    expect(writeBtn).not.toBeDisabled();
    fireEvent.click(writeBtn);

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startWrite)).toHaveBeenCalledTimes(1);
    });
    const call = vi.mocked(workflowsApi.startWrite).mock.calls[0];
    expect(call[0]).toBe('prj_001');
    expect(call[1]).toBe('ch_001');
    expect(call[2]).toEqual({
      model_overrides: { creative_writing: creativeId },
      fresh_write: true, // 2026-08-30 起「全新重写」为默认写作模式
    });
    // 临时字段 model_profile_id 不得泄漏到下游 payload
    expect((call[2] as Record<string, unknown>)['model_profile_id']).toBeUndefined();
  });

  it('b) 不选择档案时点「写正文」：payload 不含 model_overrides 键', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    vi.mocked(modelProfilesApi.list).mockResolvedValue([
      buildProfile({ profile_id: 'mp_a', name: 'A' }),
    ]);
    vi.mocked(workflowsApi.startWrite).mockResolvedValue({
      run_id: 'wfr_write_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });
    // 即使有档案，select 默认 = ''，直接点按钮 = 不带 model_overrides
    await waitFor(() => {
      expect(screen.getByTestId('wf-model-select-write')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('wf-btn-write'));

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startWrite)).toHaveBeenCalledTimes(1);
    });
    const call = vi.mocked(workflowsApi.startWrite).mock.calls[0];
    // 未选档案 → model_overrides 不出现；默认写作模式「全新重写」带 fresh_write=true
    expect(call[2]).toEqual({ fresh_write: true });
  });

  it('c) 选「全新重写」点「写正文」：payload 含 fresh_write: true', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    vi.mocked(modelProfilesApi.list).mockResolvedValue([
      buildProfile({ profile_id: 'mp_a', name: 'A' }),
    ]);
    vi.mocked(workflowsApi.startWrite).mockResolvedValue({
      run_id: 'wfr_write_fresh',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });
    // write 专属写作模式下拉应渲染（仅 write 卡片，其他动作无）
    const modeSelect = await waitFor(() =>
      screen.getByTestId('wf-write-mode'),
    );
    // 切到「按意见改稿」：payload 不含 fresh_write（向后兼容路径）
    fireEvent.change(modeSelect, { target: { value: '' } });

    fireEvent.click(screen.getByTestId('wf-btn-write'));

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startWrite)).toHaveBeenCalledTimes(1);
    });
    const call = vi.mocked(workflowsApi.startWrite).mock.calls[0];
    expect(call[0]).toBe('prj_001');
    expect(call[1]).toBe('ch_001');
    // 未选 fresh + 无档案 → payload 为 undefined（保持向后兼容），fresh_write 不出现
    expect(call[2]).toBeUndefined();
    expect((call[2] as Record<string, unknown> | undefined)?.['fresh_write']).toBeUndefined();
  });

  it('d) 默认「全新重写」点「写正文」：payload 含 fresh_write=true', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    vi.mocked(modelProfilesApi.list).mockResolvedValue([
      buildProfile({ profile_id: 'mp_a', name: 'A' }),
    ]);
    vi.mocked(workflowsApi.startWrite).mockResolvedValue({
      run_id: 'wfr_write_default_mode',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });
    // 默认即为「全新重写」；不切换下拉，直接点按钮
    await waitFor(() => {
      expect(screen.getByTestId('wf-write-mode')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('wf-btn-write'));

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startWrite)).toHaveBeenCalledTimes(1);
    });
    const call = vi.mocked(workflowsApi.startWrite).mock.calls[0];
    // 默认 fresh 重写：payload 含 fresh_write=true（且无其他键泄漏）
    expect(call[2]).toEqual({ fresh_write: true });
  });

  it('e) 提交卡 V3.9.4：渲染模型下拉 + 选中后请求带 model_overrides.observer', async () => {
    // V3.9.4：observer 拆为独立 capability 后，commit 提交卡也应支持模型下拉；
    // 覆盖键 = observer（与 commit pipeline profile_id 透传口径一致）。
    const observerId = 'mp_observer_99';
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'REVIEWED' }));
    vi.mocked(modelProfilesApi.list).mockResolvedValue([
      buildProfile({ profile_id: observerId, name: '观察-甲', provider: 'openai', model: 'gpt-x' }),
      buildProfile({ profile_id: 'mp_other', name: '其他' }),
    ]);
    vi.mocked(workflowsApi.startCommit).mockResolvedValue({
      run_id: 'wfr_commit_override',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });
    // 提交卡应渲染模型下拉（data-testid 与其它 action 同形）
    const commitSelect = await waitFor(() =>
      screen.getByTestId('wf-model-select-commit'),
    );
    fireEvent.change(commitSelect, { target: { value: observerId } });

    const commitBtn = screen.getByTestId('wf-btn-commit');
    expect(commitBtn).not.toBeDisabled();
    fireEvent.click(commitBtn);

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startCommit)).toHaveBeenCalledTimes(1);
    });
    const call = vi.mocked(workflowsApi.startCommit).mock.calls[0];
    expect(call[0]).toBe('prj_001');
    expect(call[1]).toBe('ch_001');
    // 关键断言：commit 覆盖键是 observer（与 pipeline 一致），不是 reasoning
    expect(call[2]).toEqual({ model_overrides: { observer: observerId } });
    // 临时字段 model_profile_id 不得泄漏
    expect((call[2] as Record<string, unknown>)['model_profile_id']).toBeUndefined();
  });

  it('f) 提交卡不选择档案时：payload 不含 model_overrides 键', async () => {
    // 缺省零行为变更：未选档案时，commit 请求不应带 model_overrides 键。
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'REVIEWED' }));
    vi.mocked(modelProfilesApi.list).mockResolvedValue([
      buildProfile({ profile_id: 'mp_a', name: 'A' }),
    ]);
    vi.mocked(workflowsApi.startCommit).mockResolvedValue({
      run_id: 'wfr_commit_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });
    // 下拉存在但默认 = ''
    await waitFor(() => {
      expect(screen.getByTestId('wf-model-select-commit')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('wf-btn-commit'));

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startCommit)).toHaveBeenCalledTimes(1);
    });
    const call = vi.mocked(workflowsApi.startCommit).mock.calls[0];
    // 未选档案 → 不带 model_overrides；payload 为 undefined（保持向后兼容）
    expect(call[2]).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// 审校目标草稿版本：选中草稿后点审校 → payload 含 draft_version=<version>。
// ---------------------------------------------------------------------------

describe('ChapterDetailPage - 审校指定草稿版本', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.delete).mockReset();
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(chaptersApi.createDraft).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startPlan).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(workflowsApi.startReview).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.resume).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.cancelRun).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(modelProfilesApi.create).mockReset();
    vi.mocked(modelProfilesApi.update).mockReset();
    vi.mocked(modelProfilesApi.remove).mockReset();
    vi.mocked(modelProfilesApi.test).mockReset();

    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([]);
    // detail = useApiCall(() => workflowsApi.get(selectedRunId ?? '')) —— mount 即触发
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
    vi.mocked(workflowsApi.startReview).mockResolvedValue({
      run_id: 'wfr_review_v1',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });
  });

  it('点选 v1 草稿后点「审校」→ startReview 收到 payload 含 draft_version=1', async () => {
    // DRAFTED 状态让 review 按钮可用
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    // 列表返回两版:v2(最新)+v1;drafts 列表按 version DESC 排序
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([
      {
        draft_id: 'drf_v2',
        chapter_id: 'ch_001',
        version: 2,
        content: 'v2 内容',
        created_by: 'writer',
        prompt_version: null,
        model_id: null,
        created_at: '2026-08-24T11:00:00+00:00',
      },
      {
        draft_id: 'drf_v1',
        chapter_id: 'ch_001',
        version: 1,
        content: 'v1 内容',
        created_by: 'writer',
        prompt_version: null,
        model_id: null,
        created_at: '2026-08-24T10:00:00+00:00',
      },
    ] as Draft[]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('drafts-panel')).toBeInTheDocument();
    });
    // 等列表渲染完(v2 默认被自动选中,active row = drf_v2)
    await waitFor(() => {
      expect(screen.getByTestId('draft-row-drf_v1')).toBeInTheDocument();
    });

    // 1) 默认选中 v2 时,审校提示应显示「将审校：草稿 v2」
    expect(screen.getByTestId('wf-review-target-hint').textContent).toMatch(
      /将审校：.*v2/,
    );

    // 2) 点选 v1 行 → 受控选中态上抛,审校提示跟着切到 v1
    fireEvent.click(screen.getByTestId('draft-row-drf_v1'));
    expect(screen.getByTestId('wf-review-target-hint').textContent).toMatch(
      /将审校：.*v1/,
    );

    // 3) 点「审校」按钮 → payload 含 draft_version: 1
    const reviewBtn = await waitFor(() => screen.getByTestId('wf-btn-review'));
    expect(reviewBtn).not.toBeDisabled();
    fireEvent.click(reviewBtn);

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startReview)).toHaveBeenCalledTimes(1);
    });
    const call = vi.mocked(workflowsApi.startReview).mock.calls[0];
    expect(call[0]).toBe('prj_001');
    expect(call[1]).toBe('ch_001');
    expect(call[2]).toEqual({ draft_version: 1 });
  });
});

// ---------------------------------------------------------------------------
// 草稿版本列表「未审」角标：
// - chapter.last_review_completed_at 存在且 draft.created_at 严格晚于该时刻
//   → 行内出现「未审」徽标；
// - created_at ≤ last_review_completed_at → 不出现；
// - chapter.last_review_completed_at 为 null（该章节从未审过）→ 任何 draft
//   都不出现「未审」徽标（避免在用户首次走流水线时被噪声覆盖）。
// ---------------------------------------------------------------------------

describe('ChapterDetailPage - 草稿版本「未审」角标', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.delete).mockReset();
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(chaptersApi.createDraft).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startPlan).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(workflowsApi.startReview).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.resume).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.cancelRun).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(modelProfilesApi.create).mockReset();
    vi.mocked(modelProfilesApi.update).mockReset();
    vi.mocked(modelProfilesApi.remove).mockReset();
    vi.mocked(modelProfilesApi.test).mockReset();

    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([]);
    vi.mocked(workflowsApi.startPlan).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
  });

  it('last_review_completed_at 之后才生成的 draft 行出现「未审」徽标；之前的行不出现', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(
      baseChapter({
        status: 'DRAFTED',
        last_review_completed_at: '2026-08-24T10:30:00+00:00',
      }),
    );
    // v1 创建时间早于最近一次审校完成时间 → 审过；
    // v2 创建时间晚于最近一次审校完成时间 → 未审。
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([
      {
        draft_id: 'drf_v2',
        chapter_id: 'ch_001',
        version: 2,
        content: 'v2 内容',
        created_by: 'writer',
        prompt_version: null,
        model_id: null,
        created_at: '2026-08-24T11:00:00+00:00',
      },
      {
        draft_id: 'drf_v1',
        chapter_id: 'ch_001',
        version: 1,
        content: 'v1 内容',
        created_by: 'writer',
        prompt_version: null,
        model_id: null,
        created_at: '2026-08-24T10:00:00+00:00',
      },
    ] as Draft[]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('draft-row-drf_v2')).toBeInTheDocument();
    });

    // 1) v2 严格晚于 10:30 → 行内有「未审」徽标
    const v2Row = screen.getByTestId('draft-row-drf_v2');
    expect(within(v2Row).getByTestId('draft-unreviewed-drf_v2')).toHaveTextContent(
      '未审',
    );
    // 2) v1 早于 10:30 → 行内不出现「未审」徽标
    const v1Row = screen.getByTestId('draft-row-drf_v1');
    expect(within(v1Row).queryByTestId('draft-unreviewed-drf_v1')).toBeNull();
  });

  it('chapter.last_review_completed_at 为 null 时任何 draft 行都不出现「未审」徽标', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(
      baseChapter({
        status: 'DRAFTED',
        last_review_completed_at: null,
      }),
    );
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([
      {
        draft_id: 'drf_v2',
        chapter_id: 'ch_001',
        version: 2,
        content: 'v2 内容',
        created_by: 'writer',
        prompt_version: null,
        model_id: null,
        created_at: '2026-08-24T11:00:00+00:00',
      },
      {
        draft_id: 'drf_v1',
        chapter_id: 'ch_001',
        version: 1,
        content: 'v1 内容',
        created_by: 'writer',
        prompt_version: null,
        model_id: null,
        created_at: '2026-08-24T10:00:00+00:00',
      },
    ] as Draft[]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('draft-row-drf_v2')).toBeInTheDocument();
    });

    // 任何 draft 行都不应有「未审」徽标：避免在用户首次走流水线时被噪声覆盖。
    expect(
      screen.queryByTestId('draft-unreviewed-drf_v2'),
    ).toBeNull();
    expect(
      screen.queryByTestId('draft-unreviewed-drf_v1'),
    ).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 四步流水线展示态（plan→write→review→commit）：
// - done / current / todo 三态由 status + plan_json 共同推导；
// - current 步骤的按钮带 btn--primary；
// - 按钮副标题由「需要状态：…」改为「Director 生成章节计划」等人性化文案。
// ---------------------------------------------------------------------------

describe('ChapterDetailPage - 工作流四步流水线展示态', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.delete).mockReset();
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(chaptersApi.createDraft).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startPlan).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(workflowsApi.startReview).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.resume).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.cancelRun).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(modelProfilesApi.create).mockReset();
    vi.mocked(modelProfilesApi.update).mockReset();
    vi.mocked(modelProfilesApi.remove).mockReset();
    vi.mocked(modelProfilesApi.test).mockReset();

    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([]);
    vi.mocked(workflowsApi.startPlan).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
  });

  it('DRAFTED + 有 plan_json：plan/write=done, review=current(primary), commit=todo', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(
      baseChapter({
        status: 'DRAFTED',
        plan_json: { chapter_goal: '目标', expected_word_count: 3000 },
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('wf-pipeline')).toBeInTheDocument();
    });

    const planStep = screen.getByTestId('wf-step-plan');
    const writeStep = screen.getByTestId('wf-step-write');
    const reviewStep = screen.getByTestId('wf-step-review');
    const commitStep = screen.getByTestId('wf-step-commit');

    expect(planStep.getAttribute('data-state')).toBe('done');
    expect(writeStep.getAttribute('data-state')).toBe('done');
    expect(reviewStep.getAttribute('data-state')).toBe('current');
    expect(commitStep.getAttribute('data-state')).toBe('todo');

    // current 步骤的按钮带 btn--primary；其它步骤不带。
    const reviewBtn = screen.getByTestId('wf-btn-review');
    expect(reviewBtn.className).toMatch(/btn--primary/);
    expect(screen.getByTestId('wf-btn-plan').className).not.toMatch(/btn--primary/);
    expect(screen.getByTestId('wf-btn-write').className).not.toMatch(/btn--primary/);
    expect(screen.getByTestId('wf-btn-commit').className).not.toMatch(/btn--primary/);

    // done 步骤展示 ✓ 序号占位（dot 内文案）。
    expect(planStep.querySelector('.wf-step__dot')?.textContent).toBe('✓');
    expect(writeStep.querySelector('.wf-step__dot')?.textContent).toBe('✓');
    // current/todo 步骤展示 1-4 序号。
    expect(reviewStep.querySelector('.wf-step__dot')?.textContent).toBe('3');
    expect(commitStep.querySelector('.wf-step__dot')?.textContent).toBe('4');
  });

  it('PLANNED + 无 plan_json：plan=current(primary)，按钮副标题含「Director 生成章节计划」、不含「需要状态」', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(
      baseChapter({ status: 'PLANNED', plan_json: {} }),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('wf-pipeline')).toBeInTheDocument();
    });

    expect(screen.getByTestId('wf-step-plan').getAttribute('data-state')).toBe('current');
    expect(screen.getByTestId('wf-step-write').getAttribute('data-state')).toBe('todo');
    expect(screen.getByTestId('wf-step-review').getAttribute('data-state')).toBe('todo');
    expect(screen.getByTestId('wf-step-commit').getAttribute('data-state')).toBe('todo');

    const planBtn = screen.getByTestId('wf-btn-plan');
    expect(planBtn.className).toMatch(/btn--primary/);
    expect(planBtn.textContent).toMatch(/Director 生成章节计划/);
    expect(planBtn.textContent).not.toMatch(/需要状态/);
  });

  it('DRAFTED：todo 步骤按钮禁用且 title 给出状态机原因（disabled/tooltip 契约回归）', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(
      baseChapter({
        status: 'DRAFTED',
        plan_json: { chapter_goal: '目标', expected_word_count: 3000 },
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('wf-pipeline')).toBeInTheDocument();
    });

    // todo 步骤（commit）按钮禁用，title 透传 getButtonAvailability 的原因文案。
    const commitBtn = screen.getByTestId('wf-btn-commit');
    expect(commitBtn).toBeDisabled();
    expect(commitBtn.getAttribute('title')).toMatch(/REVIEWED/);

    // current 步骤（review）可点击、无禁用原因；done 的 write 在 DRAFTED 下状态机允许重跑。
    expect(screen.getByTestId('wf-btn-review')).toBeEnabled();
    expect(screen.getByTestId('wf-btn-write')).toBeEnabled();
    // done 的 plan 在 DRAFTED 下被状态机禁用（防覆盖已有计划）。
    expect(screen.getByTestId('wf-btn-plan')).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// FAILED run 错误渲染回归（commit 校验失败不应被误判为「已驳回」）
// ---------------------------------------------------------------------------

function buildFailedRun(error: string, runId = 'wfr_failed_001'): WorkflowRun {
  return {
    run_id: runId,
    workflow_id: 'chapter-commit',
    chapter_id: 'ch_001',
    status: 'FAILED',
    current_node: null,
    checkpoint_json: {},
    error,
    retry_count: 0,
    started_at: new Date().toISOString(),
    ended_at: new Date().toISOString(),
    nodes: [],
    workflow_name: 'chapter-commit',
    pause_payload: null,
  } as unknown as WorkflowRun;
}

describe('ChapterDetailPage - FAILED run 错误文案不误判为「已驳回」', () => {
  it("commit 校验失败 (error 含 'rejected' 子串) → 渲染「失败」，不出现「已驳回」", async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'COMMITTED' }));
    const failedRun = buildFailedRun(
      'observer delta rejected by validator: errors=[orphan ref to c1]',
    );
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([failedRun]);

    renderPage();
    await openRunsTab();

    // 列表渲染后：徽标为「失败」，不应出现「已驳回」/「已驳回·改稿」
    await waitFor(() => {
      expect(screen.getByText('失败')).toBeInTheDocument();
    });
    expect(screen.queryByText('已驳回')).toBeNull();
    expect(screen.queryByText('已驳回·改稿')).toBeNull();
  });

  it("commit 校验失败 (无 'rejected' 子串) → 渲染「失败」", async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'COMMITTED' }));
    const failedRun = buildFailedRun(
      'observer delta failed validation: errors=[x]',
      'wfr_failed_002',
    );
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([failedRun]);

    renderPage();
    await openRunsTab();

    await waitFor(() => {
      expect(screen.getByText('失败')).toBeInTheDocument();
    });
    expect(screen.queryByText('已驳回')).toBeNull();
  });

  it("纯驳回 (error='rejected') → 渲染「已驳回」", async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const failedRun = buildFailedRun('rejected', 'wfr_rejected_001');
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([failedRun]);

    renderPage();
    await openRunsTab();

    await waitFor(() => {
      expect(screen.getByText('已驳回')).toBeInTheDocument();
    });
    expect(screen.queryByText('已驳回·改稿')).toBeNull();
  });

  it("驳回并改稿 (error='rejected-for-revision') → 渲染「已驳回·改稿」", async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const failedRun = buildFailedRun(
      'rejected-for-revision',
      'wfr_rejected_for_revision_001',
    );
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([failedRun]);

    renderPage();
    await openRunsTab();

    await waitFor(() => {
      expect(screen.getByText('已驳回·改稿')).toBeInTheDocument();
    });
    expect(screen.queryByText('已驳回')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 协作式取消：节点被取消探针标 FAILED(error='cancelled by user')。
// 前端不能用红色 ErrorBanner / 红色 failed 徽标误导用户，需渲染中性徽标 + 中性 InfoBanner。
// ---------------------------------------------------------------------------

describe('ChapterDetailPage - 用户主动取消节点（非真失败）', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.delete).mockReset();
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(chaptersApi.createDraft).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startPlan).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(workflowsApi.startReview).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.resume).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.cancelRun).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(modelProfilesApi.create).mockReset();
    vi.mocked(modelProfilesApi.update).mockReset();
    vi.mocked(modelProfilesApi.remove).mockReset();
    vi.mocked(modelProfilesApi.test).mockReset();

    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([]);
    vi.mocked(workflowsApi.startPlan).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
  });

  // 构造一个被用户取消的 run：CANCELLED 状态 + nodes 含一个被探针标 FAILED 的节点。
  // 该节点 error === 'cancelled by user'（精确匹配），不应被误判为真失败。
  function buildCancelledRun(): WorkflowRun {
    return {
      run_id: 'wfr_user_cancelled_001',
      workflow_id: 'chapter-write',
      chapter_id: 'ch_001',
      status: 'CANCELLED',
      current_node: null,
      checkpoint_json: {},
      error: null,
      retry_count: 0,
      started_at: new Date(Date.now() - 60_000).toISOString(),
      ended_at: new Date().toISOString(),
      workflow_name: 'chapter-write',
      nodes: [
        {
          node_run_id: 'nrun_planner_cancelled',
          run_id: 'wfr_user_cancelled_001',
          node_id: 'planner',
          agent_id: null,
          status: 'COMPLETED',
          input_json: {},
          output_json: null,
          prompt_version: null,
          model_id: null,
          token_usage_json: null,
          latency_ms: null,
          error: null,
          started_at: new Date(Date.now() - 60_000).toISOString(),
          ended_at: new Date(Date.now() - 50_000).toISOString(),
        },
        {
          node_run_id: 'nrun_writer_cancelled',
          run_id: 'wfr_user_cancelled_001',
          node_id: 'writer',
          agent_id: null,
          status: 'FAILED',
          input_json: {},
          output_json: null,
          prompt_version: null,
          model_id: null,
          token_usage_json: null,
          latency_ms: null,
          // 引擎协作式取消在 checkpoint 前探针标 FAILED(error='cancelled by user')
          error: 'cancelled by user',
          started_at: new Date(Date.now() - 40_000).toISOString(),
          ended_at: new Date(Date.now() - 5_000).toISOString(),
        },
      ],
    } as unknown as WorkflowRun;
  }

  it('a) 节点行渲染「已取消」中性徽标，不出现红色徽标', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const cancelledRun = buildCancelledRun();
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([cancelledRun]);
    vi.mocked(workflowsApi.get).mockResolvedValue(cancelledRun);

    renderPage();
    await openRunsTab();

    // CANCELLED 属终态，时间线默认折叠；展开 <details> 后再断言节点行徽标。
    await waitFor(() => {
      expect(screen.getByTestId('run-timeline-details')).toBeInTheDocument();
    });
    const timelineDetails = screen.getByTestId('run-timeline-details') as HTMLDetailsElement;
    timelineDetails.open = true;

    // 节点徽标应显示「已取消」——文案精确锁定（仅校验 RunTimeline 节点行内的徽标，不动 runs 列表行）。
    // 节点时间线位于 <ol> 内,通过 ol 上下文拿「已取消」徽标才能精确锁定 NodeStatusBadge。
    await waitFor(() => {
      const nodeBadges = Array.from(
        document.querySelectorAll('ol li .badge'),
      ).filter((b) => (b.textContent ?? '').trim() === '已取消');
      expect(nodeBadges.length).toBeGreaterThan(0);
    });
    const nodeBadges = Array.from(
      document.querySelectorAll('ol li .badge'),
    ).filter((b) => (b.textContent ?? '').trim() === '已取消');
    const nodeClasses = nodeBadges.map((b) => b.className);
    // 节点行徽标应是中性 badge--chapter-rejected（不挂红色 failed 类）
    expect(
      nodeClasses.some((c) => /badge--chapter-rejected/.test(c)),
    ).toBe(true);
    expect(
      nodeClasses.some((c) => /badge--chapter-failed/.test(c)),
    ).toBe(false);
    // 已驳回相关文案不应出现（不是驳回语义）
    expect(screen.queryByText('已驳回')).toBeNull();
    expect(screen.queryByText('已驳回·改稿')).toBeNull();
  });

  it('b) 节点行不渲染红色 ErrorBanner「cancelled by user」（用中性 InfoBanner 或不渲染）', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const cancelledRun = buildCancelledRun();
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([cancelledRun]);
    vi.mocked(workflowsApi.get).mockResolvedValue(cancelledRun);

    renderPage();
    await openRunsTab();

    // CANCELLED 终态默认折叠；展开 <details> 后再断言节点内 InfoBanner / ErrorBanner 文案。
    await waitFor(() => {
      expect(screen.getByTestId('run-timeline-details')).toBeInTheDocument();
    });
    const timelineDetails = screen.getByTestId('run-timeline-details') as HTMLDetailsElement;
    timelineDetails.open = true;

    // 触发选中 + 详情后渲染节点时间线（细节 + info banner）
    await waitFor(() => {
      expect(screen.getByText('writer')).toBeInTheDocument();
    });
    // 整页 .alert--error 不应含 'cancelled by user' 文案
    const errorAlerts = Array.from(document.querySelectorAll('.alert--error'));
    const allErrorText = errorAlerts.map((e) => e.textContent ?? '').join(' | ');
    expect(allErrorText).not.toMatch(/cancelled by user/);
    // 中性 InfoBanner（alert--info）应含「已被用户主动取消」（节点 + 顶部运行中横幅外的「已被用户主动取消」文案）
    const infoAlerts = Array.from(document.querySelectorAll('.alert--info'));
    const allInfoText = infoAlerts.map((e) => e.textContent ?? '').join(' | ');
    expect(allInfoText).toMatch(/已被用户主动取消|cancelled by user/);
  });
});

// ---------------------------------------------------------------------------
// 节点时间线默认折叠 / 展开：
// - 终态（COMPLETED/FAILED/CANCELLED）默认折叠（避免长 run 把页面拉长）；
// - 进行中（RUNNING/PAUSED）默认展开（用户正在盯进度）。
// - summary 行展示「共 N 个节点 · 最新：<lastNode.node_id>」。
// 既有用户取消节点的断言若依赖 ol li 细节，需先展开 details 再断言。
// ---------------------------------------------------------------------------

describe('ChapterDetailPage - 节点时间线默认折叠 / 展开', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.delete).mockReset();
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(chaptersApi.createDraft).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startPlan).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(workflowsApi.startReview).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.resume).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.cancelRun).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(modelProfilesApi.create).mockReset();
    vi.mocked(modelProfilesApi.update).mockReset();
    vi.mocked(modelProfilesApi.remove).mockReset();
    vi.mocked(modelProfilesApi.test).mockReset();

    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([]);
    vi.mocked(workflowsApi.startPlan).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
  });

  // 构造一个 run：带两个节点、状态可由调用方传入（终态或进行中）。
  function buildRunWithNodes(status: WorkflowRun['status']): WorkflowRun {
    return {
      run_id: 'wfr_collapse_001',
      workflow_id: 'chapter-write',
      chapter_id: 'ch_001',
      status,
      current_node: status === 'RUNNING' ? 'writer' : null,
      checkpoint_json: {},
      error: null,
      retry_count: 0,
      started_at: new Date(Date.now() - 60_000).toISOString(),
      ended_at: status === 'RUNNING' || status === 'PENDING' || status === 'PAUSED' ? null : new Date().toISOString(),
      workflow_name: 'chapter-write',
      nodes: [
        {
          node_run_id: 'nrun_planner_a',
          run_id: 'wfr_collapse_001',
          node_id: 'planner',
          agent_id: null,
          status: 'COMPLETED',
          input_json: {},
          output_json: null,
          prompt_version: null,
          model_id: null,
          token_usage_json: null,
          latency_ms: 1234,
          error: null,
          started_at: new Date(Date.now() - 60_000).toISOString(),
          ended_at: new Date(Date.now() - 50_000).toISOString(),
        },
        {
          node_run_id: 'nrun_writer_a',
          run_id: 'wfr_collapse_001',
          node_id: 'writer',
          agent_id: null,
          status: status === 'RUNNING' ? 'RUNNING' : 'COMPLETED',
          input_json: {},
          output_json: null,
          prompt_version: null,
          model_id: null,
          token_usage_json: null,
          latency_ms: status === 'RUNNING' ? null : 4567,
          error: null,
          started_at: new Date(Date.now() - 40_000).toISOString(),
          ended_at: status === 'RUNNING' ? null : new Date(Date.now() - 5_000).toISOString(),
        },
      ],
    } as unknown as WorkflowRun;
  }

  it.each<WorkflowRun['status']>(['COMPLETED', 'FAILED', 'CANCELLED'])(
    '终态 %s：节点时间线默认折叠，details 无 open',
    async (status) => {
      vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
      const run = buildRunWithNodes(status);
      vi.mocked(workflowsApi.listByProject).mockResolvedValue([run]);
      vi.mocked(workflowsApi.get).mockResolvedValue(run);

      renderPage();
      await openRunsTab();

      // summary 行出现（折叠状态下也应可见）
      await waitFor(() => {
        expect(screen.getByTestId('run-timeline-summary')).toBeInTheDocument();
      });
      const details = screen.getByTestId('run-timeline-details') as HTMLDetailsElement;
      // 终态默认折叠：details.open 必须为 false。
      expect(details.open).toBe(false);
      // summary 应包含节点数与最后一个节点的 node_id
      expect(screen.getByTestId('run-timeline-summary').textContent).toMatch(/共\s*2\s*个节点/);
      expect(screen.getByTestId('run-timeline-summary').textContent).toMatch(/writer/);
    },
  );

  it.each<WorkflowRun['status']>(['RUNNING', 'PAUSED'])(
    '进行中 %s：节点时间线默认展开，details 有 open',
    async (status) => {
      vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
      const run = buildRunWithNodes(status);
      vi.mocked(workflowsApi.listByProject).mockResolvedValue([run]);
      vi.mocked(workflowsApi.get).mockResolvedValue(run);

      renderPage();
      await openRunsTab();

      await waitFor(() => {
        expect(screen.getByTestId('run-timeline-summary')).toBeInTheDocument();
      });
      const details = screen.getByTestId('run-timeline-details') as HTMLDetailsElement;
      // 进行中默认展开：details.open 必须为 true。
      expect(details.open).toBe(true);
      // 展开后 ol 内应可见节点 <li>
      await waitFor(() => {
        expect(document.querySelectorAll('ol li').length).toBeGreaterThan(0);
      });
    },
  );
});

// ---------------------------------------------------------------------------
// 深度二审（deep_review）：
// - 审校步骤勾选「深度二审（Kimi，+约1分钟）」 → startReview 请求体带 deep_review:true。
// - pause_payload 含 deep_review_report → 在审批卡片下方渲染「深度二审（三层清单）」分栏。
// - 未开启 / 字段缺省 → 不渲染；坏形状 → 降级提示，不炸。
// ---------------------------------------------------------------------------

describe('ChapterDetailPage - 深度二审 (deep_review)', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.delete).mockReset();
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(chaptersApi.createDraft).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startPlan).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(workflowsApi.startReview).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.resume).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.cancelRun).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(modelProfilesApi.create).mockReset();
    vi.mocked(modelProfilesApi.update).mockReset();
    vi.mocked(modelProfilesApi.remove).mockReset();
    vi.mocked(modelProfilesApi.test).mockReset();

    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([]);
    vi.mocked(workflowsApi.startReview).mockResolvedValue({
      run_id: 'wfr_review_deep',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });
    vi.mocked(workflowsApi.startPlan).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
  });

  it('a) 默认不勾深度二审：startReview 请求体不带 deep_review 键', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });
    const cb = screen.getByTestId('wf-review-deep-checkbox') as HTMLInputElement;
    expect(cb).toBeInTheDocument();
    expect(cb.checked).toBe(false);

    const reviewBtn = screen.getByTestId('wf-btn-review');
    expect(reviewBtn).not.toBeDisabled();
    fireEvent.click(reviewBtn);

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startReview)).toHaveBeenCalledTimes(1);
    });
    const call = vi.mocked(workflowsApi.startReview).mock.calls[0];
    const payload = call[2] as Record<string, unknown> | undefined;
    expect(payload).toBeDefined();
    expect(payload!['deep_review']).toBeUndefined();
  });

  it('b) 勾选深度二审后点审校：startReview 请求体带 deep_review: true', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });
    const cb = screen.getByTestId('wf-review-deep-checkbox') as HTMLInputElement;
    fireEvent.click(cb);
    expect(cb.checked).toBe(true);

    fireEvent.click(screen.getByTestId('wf-btn-review'));

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startReview)).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(workflowsApi.startReview).mock
      .calls[0][2] as Record<string, unknown>;
    expect(payload['deep_review']).toBe(true);
  });

  it('c) 取消勾选：请求体不再带 deep_review', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });
    const cb = screen.getByTestId('wf-review-deep-checkbox') as HTMLInputElement;
    fireEvent.click(cb);
    expect(cb.checked).toBe(true);
    fireEvent.click(cb);
    expect(cb.checked).toBe(false);

    fireEvent.click(screen.getByTestId('wf-btn-review'));
    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startReview)).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(workflowsApi.startReview).mock
      .calls[0][2] as Record<string, unknown>;
    expect(payload['deep_review']).toBeUndefined();
  });

  it('d) pause_payload.deep_review_report 存在时渲染分栏（verdict + issues）', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const pausedRun: WorkflowRun = {
      run_id: 'wfr_review_paused',
      workflow_id: 'chapter-review',
      chapter_id: 'ch_001',
      status: 'PAUSED',
      current_node: 'author_review',
      checkpoint_json: {
        author_review: {
          __pause_payload__: {
            stage: 'chapter-review',
            message: '请人工决议',
            deep_review_report: {
              verdict: 'revise',
              overall_comment: '行为链一致性需补强',
              issues: [
                {
                  layer: 'setting',
                  severity: 'high',
                  quote: '青木城所在大陆...',
                  suggestion: '对齐第 3 章设定',
                },
                {
                  layer: 'beat',
                  severity: 'medium',
                  quote: '本章末段对决',
                  suggestion: '把节拍拉长一拍',
                },
                {
                  layer: 'behavior',
                  severity: 'low',
                  quote: '主角面对挑衅的反应',
                  suggestion: '按人物卡修正',
                },
              ],
            },
          },
        },
      },
      error: null,
      retry_count: 0,
      started_at: new Date(Date.now() - 60_000).toISOString(),
      ended_at: null,
      nodes: [],
      workflow_name: 'chapter-review',
    } as WorkflowRun;
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([pausedRun]);
    vi.mocked(workflowsApi.get).mockResolvedValue(pausedRun);

    renderPage();
    await openRunsTab();

    const section = await waitFor(() =>
      screen.getByTestId('deep-review-report'),
    );
    expect(section).toBeInTheDocument();

    const verdict = screen.getByTestId('deep-review-verdict');
    expect(verdict.getAttribute('data-verdict')).toBe('revise');
    expect(verdict.textContent).toMatch(/建议改/);

    expect(screen.getByTestId('deep-review-overall').textContent).toMatch(
      /行为链一致性/,
    );

    const issues = screen.getByTestId('deep-review-issues');
    expect(issues.getAttribute('data-issue-count')).toBe('3');
    const issueRows = screen.getAllByTestId('deep-review-issue');
    expect(issueRows).toHaveLength(3);
    expect(issueRows[0].getAttribute('data-layer')).toBe('setting');
    expect(issueRows[0].getAttribute('data-severity')).toBe('high');
    expect(screen.getAllByTestId('deep-review-issue-quote').length).toBe(3);
    expect(screen.getAllByTestId('deep-review-issue-suggestion').length).toBe(3);
  });

  it('e) verdict=pass 时显示「通过」徽标', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const pausedRun: WorkflowRun = {
      run_id: 'wfr_review_pass',
      workflow_id: 'chapter-review',
      chapter_id: 'ch_001',
      status: 'PAUSED',
      current_node: 'author_review',
      checkpoint_json: {
        author_review: {
          __pause_payload__: {
            stage: 'chapter-review',
            message: '请人工决议',
            deep_review_report: {
              verdict: 'pass',
              overall_comment: '三层均无问题',
              issues: [],
            },
          },
        },
      },
      error: null,
      retry_count: 0,
      started_at: new Date().toISOString(),
      ended_at: null,
      nodes: [],
      workflow_name: 'chapter-review',
    } as WorkflowRun;
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([pausedRun]);
    vi.mocked(workflowsApi.get).mockResolvedValue(pausedRun);

    renderPage();
    await openRunsTab();

    const verdict = await waitFor(() =>
      screen.getByTestId('deep-review-verdict'),
    );
    expect(verdict.getAttribute('data-verdict')).toBe('pass');
    expect(verdict.textContent).toMatch(/通过/);
    expect(screen.queryByTestId('deep-review-issues')).toBeNull();
    expect(screen.getByText(/未发现明显问题/)).toBeInTheDocument();
  });

  it('f) pause_payload 不含 deep_review_report → 不渲染分栏', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const pausedRun: WorkflowRun = {
      run_id: 'wfr_review_no_deep',
      workflow_id: 'chapter-review',
      chapter_id: 'ch_001',
      status: 'PAUSED',
      current_node: 'author_review',
      checkpoint_json: {
        author_review: {
          __pause_payload__: {
            stage: 'chapter-review',
            message: '请人工决议',
          },
        },
      },
      error: null,
      retry_count: 0,
      started_at: new Date().toISOString(),
      ended_at: null,
      nodes: [],
      workflow_name: 'chapter-review',
    } as WorkflowRun;
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([pausedRun]);
    vi.mocked(workflowsApi.get).mockResolvedValue(pausedRun);

    renderPage();
    await openRunsTab();

    await waitFor(() => {
      expect(screen.getByTestId('approval-card')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('deep-review-report')).toBeNull();
    expect(screen.queryByTestId('deep-review-report-malformed')).toBeNull();
  });

  it('g) deep_review_report 坏形状（非对象） → 降级提示，不炸', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const pausedRun: WorkflowRun = {
      run_id: 'wfr_review_bad',
      workflow_id: 'chapter-review',
      chapter_id: 'ch_001',
      status: 'PAUSED',
      current_node: 'author_review',
      checkpoint_json: {
        author_review: {
          __pause_payload__: {
            stage: 'chapter-review',
            message: '请人工决议',
            deep_review_report: 'not-an-object',
          },
        },
      },
      error: null,
      retry_count: 0,
      started_at: new Date().toISOString(),
      ended_at: null,
      nodes: [],
      workflow_name: 'chapter-review',
    } as WorkflowRun;
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([pausedRun]);
    vi.mocked(workflowsApi.get).mockResolvedValue(pausedRun);

    renderPage();
    await openRunsTab();

    const malformed = await waitFor(() =>
      screen.getByTestId('deep-review-report-malformed'),
    );
    expect(malformed).toBeInTheDocument();
    expect(malformed.textContent).toMatch(/形状异常/);
  });
});

// ---------------------------------------------------------------------------
// 「停止当前工作流」按钮：banner 内嵌的协作式取消入口。
// - 仅 RUNNING 时可见/可用；PENDING / PAUSED / 终态不渲染。
// - 点击 → 内联确认态，文案「确认停止当前工作流？…」精确锁定；
//   确认 → POST /runs/{id}/cancel；409 → 静默刷新（视为已结束），其它错误由 ErrorBanner 展示。
// ---------------------------------------------------------------------------

describe('ChapterDetailPage - 停止当前工作流按钮', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.delete).mockReset();
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(chaptersApi.createDraft).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startPlan).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(workflowsApi.startReview).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.resume).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.cancelRun).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(modelProfilesApi.create).mockReset();
    vi.mocked(modelProfilesApi.update).mockReset();
    vi.mocked(modelProfilesApi.remove).mockReset();
    vi.mocked(modelProfilesApi.test).mockReset();

    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([]);
    vi.mocked(workflowsApi.startPlan).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
    });
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
  });

  it('a) 有 RUNNING run 时：banner 内显示「停止工作流」按钮', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const runningList: WorkflowRun[] = [
      {
        run_id: 'wfr_cancel_running',
        workflow_id: 'chapter-write',
        chapter_id: 'ch_001',
        status: 'RUNNING',
        current_node: 'writer',
        checkpoint_json: {},
        error: null,
        retry_count: 0,
        started_at: new Date(Date.now() - 30_000).toISOString(),
        ended_at: null,
        nodes: [],
        workflow_name: 'chapter-write',
      } as WorkflowRun,
    ];
    vi.mocked(workflowsApi.listByProject).mockResolvedValue(runningList);
    vi.mocked(workflowsApi.get).mockResolvedValue(
      buildRunningDetail({ run_id: 'wfr_cancel_running' }),
    );

    renderPage();

    const cancelBtn = await waitFor(() =>
      screen.getByTestId('wf-cancel-btn'),
    );
    expect(cancelBtn).toBeInTheDocument();
    expect(cancelBtn.textContent).toMatch(/停止工作流/);
  });

  it('b) 无 RUNNING run（仅终态 / 无 run / PAUSED）时：banner 不显示取消按钮', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    // 全部终态：列表里没有 RUNNING/PENDING 的行，banner 不应在文档中
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([
      {
        run_id: 'wfr_cancel_done',
        workflow_id: 'chapter-write',
        chapter_id: 'ch_001',
        status: 'COMPLETED',
        current_node: null,
        checkpoint_json: {},
        error: null,
        retry_count: 0,
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        nodes: [],
        workflow_name: 'chapter-write',
      } as WorkflowRun,
    ]);

    renderPage();

    // banner 自身不应渲染（activeRun 为空）
    expect(screen.queryByTestId('workflow-running-banner')).toBeNull();
    expect(screen.queryByTestId('wf-cancel-btn')).toBeNull();
  });

  it('c) 确认后调用 cancelRun，入参 = banner 展示的 run_id', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const targetRunId = 'wfr_cancel_confirm_target';
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([
      {
        run_id: targetRunId,
        workflow_id: 'chapter-write',
        chapter_id: 'ch_001',
        status: 'RUNNING',
        current_node: 'writer',
        checkpoint_json: {},
        error: null,
        retry_count: 0,
        started_at: new Date(Date.now() - 30_000).toISOString(),
        ended_at: null,
        nodes: [],
        workflow_name: 'chapter-write',
      } as WorkflowRun,
    ]);
    vi.mocked(workflowsApi.get).mockResolvedValue(
      buildRunningDetail({ run_id: targetRunId }),
    );
    vi.mocked(workflowsApi.cancelRun).mockResolvedValue({
      run_id: targetRunId,
      status: 'CANCELLED',
    });

    renderPage();

    // 1) 点「停止工作流」→ ConfirmDialog 二次确认出现，文案精确锁定
    const cancelBtn = await waitFor(() => screen.getByTestId('wf-cancel-btn'));
    fireEvent.click(cancelBtn);
    const confirm = await waitFor(() => screen.getByTestId('wf-cancel'));
    expect(confirm.textContent).toMatch(
      /确认停止当前工作流？已完成的节点会保留，正在执行的节点结果将被丢弃。/,
    );

    // 2) 点「确认停止」→ 调用 cancelRun(run_id)
    const confirmBtn = screen.getByTestId('wf-cancel-confirm');
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.cancelRun)).toHaveBeenCalledTimes(1);
    });
    expect(vi.mocked(workflowsApi.cancelRun)).toHaveBeenCalledWith(targetRunId);
  });

  it('d) cancelRun 返回 409：触发刷新但不报错炸屏（视为已结束）', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    const runId409 = 'wfr_cancel_409';
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([
      {
        run_id: runId409,
        workflow_id: 'chapter-write',
        chapter_id: 'ch_001',
        status: 'RUNNING',
        current_node: 'writer',
        checkpoint_json: {},
        error: null,
        retry_count: 0,
        started_at: new Date(Date.now() - 30_000).toISOString(),
        ended_at: null,
        nodes: [],
        workflow_name: 'chapter-write',
      } as WorkflowRun,
    ]);
    vi.mocked(workflowsApi.get).mockResolvedValue(
      buildRunningDetail({ run_id: runId409 }),
    );
    // cancelRun 模拟后端 409（run 已不在 RUNNING）；抛真实 ApiError 让父组件的
    //  instanceof 守卫命中「视为已结束、刷新即可」分支，不进 ErrorBanner 通道。
    vi.mocked(workflowsApi.cancelRun).mockImplementation(async () => {
      throw new ApiError(409, 'run not running');
    });

    renderPage();

    const cancelBtn = await waitFor(() => screen.getByTestId('wf-cancel-btn'));
    fireEvent.click(cancelBtn);
    const confirmBtn = await waitFor(() => screen.getByTestId('wf-cancel-confirm'));
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.cancelRun)).toHaveBeenCalledTimes(1);
    });
    // 触发刷新链路——listByProject / get 应被轮询/重拉多次（不严格次数，仅验证被调用）
    await waitFor(() => {
      expect(vi.mocked(workflowsApi.listByProject)).toHaveBeenCalled();
    });
    // 不应把 409 抛到 ErrorBanner 通道（页面不应出现 alert--error）
    // 顶部 ErrorBanner 在 page 中只展示 chapterCall.error / actionErr；409 不进 actionErr。
    const errorAlerts = document.querySelectorAll('.alert--error');
    // 过滤掉非本次取消导致的 ErrorBanner（chaptersApi.get 等也会走 ErrorBanner，但 mock 都是成功，无 error）
    expect(Array.from(errorAlerts).map((e) => e.textContent).join('|')).not.toMatch(
      /run not running|409/,
    );
  });
});

// ---------------------------------------------------------------------------
// 前端批次 C（2026-09-13）信息架构重构回归：
// - 下部分区 tabs（默认「草稿」，懒挂载；切走即卸载）
// - 运行记录下置为第二个分段 tab（懒挂载 / 状态徽标 / 待审批提示条 / ?section=runs 直开）
// - 章节标题 inline 改名（chaptersApi.update 的 title 字段）
// - 删除章节失败的就地反馈（此前 try/finally 无 catch，界面零反馈）
// - 「停止工作流」二次确认取消路径（ConfirmDialog 复用）
// ---------------------------------------------------------------------------

describe('ChapterDetailPage - 信息架构（分区 tabs / 标题改名 / 删除反馈）', () => {
  const draftV1: Draft = {
    draft_id: 'drf_v1',
    chapter_id: 'ch_001',
    version: 1,
    content: '正文第一版',
    created_by: 'writer',
    prompt_version: 'writer:v1',
    model_id: null,
    created_at: '2026-08-24T10:00:00+00:00',
  };

  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.delete).mockReset();
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(chaptersApi.createDraft).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startPlan).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(workflowsApi.startReview).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.resume).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.cancelRun).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(modelProfilesApi.create).mockReset();
    vi.mocked(modelProfilesApi.update).mockReset();
    vi.mocked(modelProfilesApi.remove).mockReset();
    vi.mocked(modelProfilesApi.test).mockReset();

    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([draftV1]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([]);
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
  });

  it('默认停在「草稿」分区；切到「章节计划」后草稿区隐藏、计划区渲染', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('drafts-panel')).toBeVisible();
    });
    // 未访问过的分区不挂载（懒加载：上下文面板不会在未打开时发请求）
    expect(screen.queryByTestId('plan-panel')).toBeNull();
    expect(screen.queryByTestId('chapter-danger-zone')).toBeNull();
    // 运行记录（原顶部常驻面板）已下置为分段 tab：默认同样不挂载
    expect(screen.queryByTestId('workflow-panel')).toBeNull();

    fireEvent.click(screen.getByTestId('cdp-tab-plan'));

    await waitFor(() => {
      expect(screen.getByTestId('plan-panel')).toBeVisible();
    });
    // 访问过的草稿区保留挂载（切走再回来不丢编辑态），但不再可见
    expect(screen.getByTestId('drafts-panel')).not.toBeVisible();
    expect(screen.getByTestId('cdp-tab-plan')).toHaveAttribute(
      'aria-selected',
      'true',
    );

    // 「危险操作」是独立分区，删除按钮不再悬在页底
    fireEvent.click(screen.getByTestId('cdp-tab-danger'));
    await waitFor(() => {
      expect(screen.getByTestId('chapter-danger-zone')).toBeVisible();
    });
    expect(screen.getByTestId('chapter-delete-btn')).toBeInTheDocument();
  });

  it('章节标题可 inline 改名：回车提交 chaptersApi.update(title)', async () => {
    vi.mocked(chaptersApi.update).mockResolvedValue(
      baseChapter({ status: 'DRAFTED', title: '改过的标题' }),
    );

    renderPage();

    const renameBtn = await waitFor(() => screen.getByTestId('chapter-title-btn'));
    fireEvent.click(renameBtn);

    const input = screen.getByTestId('chapter-title-input') as HTMLInputElement;
    // 预填当前标题，便于局部修改
    expect(input.value).toBe('第一章');
    fireEvent.change(input, { target: { value: '改过的标题' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      expect(vi.mocked(chaptersApi.update)).toHaveBeenCalledWith('ch_001', {
        title: '改过的标题',
      });
    });
    // 保存成功后回到只读态
    await waitFor(() => {
      expect(screen.queryByTestId('chapter-title-input')).toBeNull();
    });
  });

  it('标题改名取消（按钮）不发请求', async () => {
    renderPage();

    fireEvent.click(await waitFor(() => screen.getByTestId('chapter-title-btn')));
    fireEvent.change(screen.getByTestId('chapter-title-input'), {
      target: { value: '不想保存的标题' },
    });
    fireEvent.click(screen.getByTestId('chapter-title-cancel'));

    await waitFor(() => {
      expect(screen.queryByTestId('chapter-title-input')).toBeNull();
    });
    expect(vi.mocked(chaptersApi.update)).not.toHaveBeenCalled();
  });

  it('删除章节失败：错误在「危险操作」区就地展示，不再静默', async () => {
    vi.mocked(chaptersApi.delete).mockRejectedValue(new Error('数据库被占用'));

    renderPage();

    fireEvent.click(await waitFor(() => screen.getByTestId('cdp-tab-danger')));
    fireEvent.click(await waitFor(() => screen.getByTestId('chapter-delete-btn')));

    const confirmBtn = await waitFor(() =>
      screen.getByTestId('chapter-delete-confirm-confirm'),
    );
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(vi.mocked(chaptersApi.delete)).toHaveBeenCalledWith('ch_001');
    });
    await waitFor(() => {
      expect(screen.getByText(/数据库被占用/)).toBeInTheDocument();
    });
    // 失败后按钮恢复可用，允许重试
    const dangerZone = screen.getByTestId('chapter-danger-zone');
    const deleteBtn = within(dangerZone).getByTestId('chapter-delete-btn');
    expect(deleteBtn).not.toBeDisabled();
  });

  it('「停止工作流」确认框点「再想想」：不调用 cancelRun', async () => {
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([
      buildRunningDetail({ run_id: 'wfr_cancel_abort' }),
    ]);
    vi.mocked(workflowsApi.get).mockResolvedValue(
      buildRunningDetail({ run_id: 'wfr_cancel_abort' }),
    );

    renderPage();

    fireEvent.click(await waitFor(() => screen.getByTestId('wf-cancel-btn')));
    const dialog = await waitFor(() => screen.getByTestId('wf-cancel'));
    expect(dialog.textContent).toMatch(/已完成的节点会保留/);

    fireEvent.click(screen.getByTestId('wf-cancel-cancel'));

    await waitFor(() => {
      expect(screen.queryByTestId('wf-cancel')).toBeNull();
    });
    expect(vi.mocked(workflowsApi.cancelRun)).not.toHaveBeenCalled();
  });

  // ---- 运行记录下置为分段 tab（2026-09-13 二次调整）----

  it('运行记录为分段 tab：默认不挂载；切到该 tab 挂载；切走保留（审批卡勾选态不丢）', async () => {
    const pausedRun = buildPausedReviewRun();
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([pausedRun]);
    vi.mocked(workflowsApi.get).mockResolvedValue(pausedRun);

    renderPage();

    // 默认「草稿」：运行记录面板不在 DOM（与其它分区同语义的懒挂载）
    await waitFor(() => {
      expect(screen.getByTestId('drafts-panel')).toBeVisible();
    });
    expect(screen.queryByTestId('workflow-panel')).toBeNull();
    expect(screen.queryByTestId('cdp-section-runs')).toBeNull();

    // 切到「运行记录」：面板挂载（紧邻「草稿」的第二位）
    await openRunsTab();
    expect(screen.getByTestId('workflow-panel')).toBeVisible();

    // 取消一条审校建议的勾选（ApprovalCard 内部状态）
    const checkbox = screen.getAllByTestId(
      'approval-suggestion-checkbox',
    )[0] as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(false);

    // 切回「草稿」：面板保留挂载、仅 hidden（不重新挂载 → 状态不丢）
    fireEvent.click(screen.getByTestId('cdp-tab-drafts'));
    await waitFor(() => {
      expect(screen.getByTestId('drafts-panel')).toBeVisible();
    });
    expect(screen.getByTestId('workflow-panel')).not.toBeVisible();

    // 再切回「运行记录」：勾选态仍在
    fireEvent.click(screen.getByTestId('cdp-tab-runs'));
    await waitFor(() => {
      expect(screen.getByTestId('workflow-panel')).toBeVisible();
    });
    const checkboxAgain = screen.getAllByTestId(
      'approval-suggestion-checkbox',
    )[0] as HTMLInputElement;
    expect(checkboxAgain.checked).toBe(false);
  });

  it('运行记录 tab 徽标：PAUSED → warn「待审批」；不在该 tab 时提示条可一键切过去', async () => {
    const pausedRun = buildPausedReviewRun();
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([pausedRun]);
    vi.mocked(workflowsApi.get).mockResolvedValue(pausedRun);

    renderPage();

    // 徽标：warn 色「待审批」小徽标
    const badge = await waitFor(() => screen.getByTestId('cdp-runs-badge'));
    expect(badge).toHaveAttribute('data-state', 'paused');
    expect(badge.textContent).toBe('待审批');
    expect(badge.className).toMatch(/badge--warn/);

    // 提示条：不在运行记录 tab 时出现（warn 语义），点击「查看」切到该 tab
    const signal = screen.getByTestId('cdp-run-signal');
    expect(signal).toHaveAttribute('data-kind', 'paused');
    expect(signal.className).toMatch(/alert--warning/);
    expect(signal.textContent).toMatch(/当前有待人工审批/);

    fireEvent.click(screen.getByTestId('cdp-run-signal-action'));
    await waitFor(() => {
      expect(screen.getByTestId('cdp-tab-runs')).toHaveAttribute(
        'aria-selected',
        'true',
      );
    });
    // 切入后提示条消失；面板本体可见
    expect(screen.queryByTestId('cdp-run-signal')).toBeNull();
    expect(screen.getByTestId('workflow-panel')).toBeVisible();
  });

  it('运行记录 tab 徽标：RUNNING → 转圈点 + info「正在生成」提示条', async () => {
    const running = buildRunningDetail({ run_id: 'wfr_running_tab' });
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([running]);
    vi.mocked(workflowsApi.get).mockResolvedValue(running);

    renderPage();

    const badge = await waitFor(() => screen.getByTestId('cdp-runs-badge'));
    expect(badge).toHaveAttribute('data-state', 'running');
    // 转圈小环：复用 .spinner（accent 色 + 旋转动画）
    expect(badge.className).toMatch(/spinner/);

    const signal = screen.getByTestId('cdp-run-signal');
    expect(signal).toHaveAttribute('data-kind', 'running');
    expect(signal.className).toMatch(/alert--info/);
    expect(signal.textContent).toMatch(/正在生成/);

    fireEvent.click(screen.getByTestId('cdp-run-signal-action'));
    await waitFor(() => {
      expect(screen.queryByTestId('cdp-run-signal')).toBeNull();
    });
  });

  it('运行记录 tab 徽标：FAILED → 红点（tooltip「失败」），不显示提示条', async () => {
    const failed = buildFailedRun('commit 校验失败', 'wfr_failed_tab');
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([failed]);

    renderPage();

    const badge = await waitFor(() => screen.getByTestId('cdp-runs-badge'));
    expect(badge).toHaveAttribute('data-state', 'failed');
    expect(badge.getAttribute('title')).toBe('失败');
    // FAILED 不是「待审批 / 运行中」：不出提示条
    expect(screen.queryByTestId('cdp-run-signal')).toBeNull();
  });

  it('运行记录 tab 徽标：终态（COMPLETED）不挂标记，提示条也不出现', async () => {
    const done = buildRunningDetail({
      run_id: 'wfr_done_tab',
      status: 'COMPLETED',
      ended_at: '2026-08-24T10:10:00+00:00',
    });
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([done]);
    vi.mocked(workflowsApi.get).mockResolvedValue(done);

    renderPage();
    await openRunsTab();

    // 面板挂载且 run 行已渲染后再断言「无徽标」，避免加载窗口内的空断言
    await waitFor(() => {
      expect(screen.getByTestId('run-row-wfr_done_tab')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('cdp-runs-badge')).toBeNull();
    expect(screen.queryByTestId('cdp-run-signal')).toBeNull();
  });

  it('?section=runs 直开：运行记录 tab 直接挂载并选中，草稿区不挂载，提示条不重复', async () => {
    const pausedRun = buildPausedReviewRun();
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([pausedRun]);
    vi.mocked(workflowsApi.get).mockResolvedValue(pausedRun);

    renderPage(['/projects/prj_001/chapters/ch_001?section=runs']);

    await waitFor(() => {
      expect(screen.getByTestId('workflow-panel')).toBeVisible();
    });
    expect(screen.getByTestId('cdp-tab-runs')).toHaveAttribute(
      'aria-selected',
      'true',
    );
    // 草稿区从未访问 → 不挂载（懒加载语义对直开同样成立）
    expect(screen.queryByTestId('cdp-section-drafts')).toBeNull();
    // 已在该 tab：不重复提示
    expect(screen.queryByTestId('cdp-run-signal')).toBeNull();
    // 徽标仍可见（待审批状态不会被 tab 选择掩盖）
    expect(await waitFor(() => screen.getByTestId('cdp-runs-badge'))).toHaveAttribute(
      'data-state',
      'paused',
    );
  });
});

// =============================================================================
// 重开返修（2026-09-26 批次）：仅 COMMITTED 章渲染入口 → 内联确认 → 调 API → reload
// =============================================================================

describe('ChapterDetailPage - 重开返修', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.reopenChapter).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();

    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    // ChapterPipelineHeader mount 即拉档案列表：不 mock 会在 useApiCall 内 undefined.then
    vi.mocked(modelProfilesApi.list).mockResolvedValue([] as ModelProfile[]);
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
  });

  it('COMMITTED 章渲染「重开返修」按钮；PLANNED 章不渲染', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(
      baseChapter({ status: 'COMMITTED' }),
    );
    const { unmount } = renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('reopen-section')).toBeInTheDocument();
    });
    expect(screen.getByTestId('reopen-btn')).toBeInTheDocument();
    unmount();

    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'PLANNED' }));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('reopen-section')).toBeNull();
  });

  it('点击按钮进入内联确认；取消不调 API', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(
      baseChapter({ status: 'COMMITTED' }),
    );
    renderPage();
    fireEvent.click(await waitFor(() => screen.getByTestId('reopen-btn')));

    expect(await waitFor(() => screen.getByTestId('reopen-reason-input'))).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('reopen-cancel-btn'));
    await waitFor(() => {
      expect(screen.queryByTestId('reopen-reason-input')).toBeNull();
    });
    expect(vi.mocked(chaptersApi.reopenChapter)).not.toHaveBeenCalled();
  });

  it('输入原因后确认 → 调 reopenChapter(带 reason) → 成功后 reload 章数据', async () => {
    const committed = baseChapter({ status: 'COMMITTED' });
    vi.mocked(chaptersApi.get).mockResolvedValue(committed);
    vi.mocked(chaptersApi.reopenChapter).mockResolvedValue(
      baseChapter({ status: 'REVIEWED' }),
    );

    renderPage();
    fireEvent.click(await waitFor(() => screen.getByTestId('reopen-btn')));
    const input = await waitFor(() => screen.getByTestId('reopen-reason-input'));
    fireEvent.change(input, { target: { value: '打脸段落矛盾' } });
    fireEvent.click(screen.getByTestId('reopen-confirm-btn'));

    await waitFor(() => {
      expect(vi.mocked(chaptersApi.reopenChapter)).toHaveBeenCalledWith('ch_001', {
        reason: '打脸段落矛盾',
      });
    });
    // 成功后刷新链路：chaptersApi.get 被再次调用（至少 mount 1 次 + reload 1 次）
    await waitFor(() => {
      expect(vi.mocked(chaptersApi.get).mock.calls.length).toBeGreaterThanOrEqual(2);
    });
    // 确认态收起（章 reload 后仍 COMMITTED（mock 未变）→ 区块还在，但回到按钮态）
    await waitFor(() => {
      expect(screen.queryByTestId('reopen-reason-input')).toBeNull();
      expect(screen.getByTestId('reopen-btn')).toBeInTheDocument();
    });
  });

  it('API 失败 → 错误就地展示在重开区块内', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(
      baseChapter({ status: 'COMMITTED' }),
    );
    vi.mocked(chaptersApi.reopenChapter).mockRejectedValue(
      Object.assign(new Error('has an active workflow run'), { status: 409 }),
    );

    renderPage();
    fireEvent.click(await waitFor(() => screen.getByTestId('reopen-btn')));
    await waitFor(() => screen.getByTestId('reopen-reason-input'));
    fireEvent.click(screen.getByTestId('reopen-confirm-btn'));

    await waitFor(() => {
      const section = screen.getByTestId('reopen-section');
      expect(within(section).getByText(/active workflow run/)).toBeInTheDocument();
    });
  });
});

// ===========================================================================
// 2026-10-15 批次：G2 / G3 —— 两条「后端有能力、作者够不着」的 UI 缺口。
//
// 两条的共同形状（与 2026-09-16 author_intent 死参数事故同源）：
//   后端写了 / 装配在用 / UI 没有任何入口 ⇒ 作者以为设了其实没设。
// 每条配**反向用例**（AGENTS 坑区：「闸门断言必须配一个反向用例，
// 否则可能被『闸门被整体摘掉』蒙过去」）。
// ===========================================================================

describe('G2 — quality_gate confirm 档的显式接受出口', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startCommit).mockReset();

    // 页面首屏并发拉三处数据（useApiCall 直接 .then）：任一未设实现即 TypeError。
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'REVIEWED' }));
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([] as ModelProfile[]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    // workflowsApi.get 也会在挂载时被拉（URL 里的 selectedRunId）：
    // 只 mockReset 不给实现 ⇒ useApiCall 的 `fn().then` 直接 TypeError。
    vi.mocked(workflowsApi.get).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(workflowsApi.startCommit).mockResolvedValue(planResponse('wfr_commit_accept'));
  });

  // 后端 gate._persist_gate_block 的三种 gate 取值之一（gate=undefined 模拟历史标记）。
  const chapterWithGate = (gate: string | undefined, ruleIds: string[]) =>
    baseChapter({
      status: 'REVIEWED',
      plan_json: {
        gate_blocked: {
          mode: 'enforce',
          ...(gate === undefined ? {} : { gate }),
          rule_ids: ruleIds,
          blocked_at: '2026-10-15T10:00:00+00:00',
        },
      } as unknown as Chapter['plan_json'],
    });

  it('gate=confirm ⇒ 给出「显式接受并提交」，且接受请求带 gate_override', async () => {
    const ids = ['RULE_STYLE_REPETITION_TRIGRAM'];
    vi.mocked(chaptersApi.get).mockResolvedValue(chapterWithGate('confirm', ids));

    renderPage();
    const accept = await waitFor(() => screen.getByTestId('gate-accept-btn'));
    // confirm 档的出路是「二选一」，改稿按钮必须还在
    expect(screen.getByTestId('gate-revise-btn')).toBeInTheDocument();

    fireEvent.click(accept);
    await waitFor(() => screen.getByTestId('gate-accept-reason-input'));
    fireEvent.change(screen.getByTestId('gate-accept-reason-input'), {
      target: { value: '刻意复沓，是本卷的修辞' },
    });
    fireEvent.click(screen.getByTestId('gate-accept-confirm-btn'));

    await waitFor(() => expect(workflowsApi.startCommit).toHaveBeenCalled());
    // rule_ids 必须**原样**带上：后端按集合包含校验，前端做「补全/裁剪」
    // 会让作者以为覆盖成功却仍被拦。
    const call = vi.mocked(workflowsApi.startCommit).mock.calls[0];
    const payload = call[2];
    expect(payload?.gate_override).toEqual({
      rule_ids: ids,
      reason: '刻意复沓，是本卷的修辞',
    });
  });

  it('接受理由为空 ⇒ 不发请求，并在区块内说明为何必填', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(chapterWithGate('confirm', ['R_A']));

    renderPage();
    fireEvent.click(await waitFor(() => screen.getByTestId('gate-accept-btn')));
    await waitFor(() => screen.getByTestId('gate-accept-confirm-btn'));
    fireEvent.change(screen.getByTestId('gate-accept-reason-input'), {
      target: { value: '   ' },
    });
    fireEvent.click(screen.getByTestId('gate-accept-confirm-btn'));

    await waitFor(() =>
      expect(screen.getByText(/必须写明为何接受这些命中/)).toBeInTheDocument(),
    );
    expect(workflowsApi.startCommit).not.toHaveBeenCalled();
  });

  // ---- 反向用例：硬停不可覆盖，给「接受」按钮就是假出路 ----
  it.each([['block'], ['block+confirm']])(
    'gate=%s ⇒ 不提供接受按钮（反向：闸门被整体摘掉时本用例必红）',
    async (gate) => {
      vi.mocked(chaptersApi.get).mockResolvedValue(chapterWithGate(gate, ['R_A']));

      renderPage();
      await waitFor(() => screen.getByTestId('gate-revise-btn'));
      expect(screen.queryByTestId('gate-accept-btn')).toBeNull();
      expect(workflowsApi.startCommit).not.toHaveBeenCalled();
    },
  );

  it('gate=block+confirm ⇒ 说明「为什么现在不能接受」，而不是让作者以为 UI 又漏了', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(chapterWithGate('block+confirm', ['R_A']));

    renderPage();
    await waitFor(() => screen.getByTestId('gate-accept-blocked-note'));
    expect(screen.queryByTestId('gate-accept-btn')).toBeNull();
  });

  it('历史标记缺 gate 字段 ⇒ 按 block 处置（fail-closed，不擅自给逃生舱）', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(chapterWithGate(undefined, ['R_A']));

    renderPage();
    await waitFor(() => screen.getByTestId('gate-revise-btn'));
    expect(screen.queryByTestId('gate-accept-btn')).toBeNull();
  });

  it('接受失败 ⇒ 错误就地展示在门禁区块内，不静默', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(chapterWithGate('confirm', ['R_A']));
    vi.mocked(workflowsApi.startCommit).mockRejectedValue(
      Object.assign(new Error('gate_override.rule_ids 未覆盖：R_B'), { status: 400 }),
    );

    renderPage();
    fireEvent.click(await waitFor(() => screen.getByTestId('gate-accept-btn')));
    await waitFor(() => screen.getByTestId('gate-accept-confirm-btn'));
    fireEvent.change(screen.getByTestId('gate-accept-reason-input'), {
      target: { value: '有意为之' },
    });
    fireEvent.click(screen.getByTestId('gate-accept-confirm-btn'));

    await waitFor(() =>
      expect(screen.getByText(/gate_override\.rule_ids 未覆盖/)).toBeInTheDocument(),
    );
  });
});

describe('G3 — run 级 error 必须在 DOM 里可见', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();

    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'REVIEWED' }));
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([] as ModelProfile[]);
    vi.mocked(workflowsApi.get).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
  });

  // 门禁阻断的真实 error 形状：带 rule_id 清单与证据摘录（后端 gate.py 追加）。
  const GATE_ERROR =
    '质量门禁阻断：RULE_STYLE_REPETITION_TRIGRAM 连续 9 段逐字重复（占比 0.31）| ' +
    'evidence="他推开门，往城门去。"… | guidance=[{"dimension":"style"}]';

  const failedRun = (error: string | null, runId = 'wfr_failed_reason'): WorkflowRun =>
    buildRunningDetail({
      run_id: runId,
      status: 'FAILED',
      ended_at: '2026-08-24T10:10:00+00:00',
      error,
      nodes: [buildNodeRun('quality_gate', 'FAILED')],
    });

  it('真 FAILED ⇒ 渲染后端原文，且不受时间线默认收起影响', async () => {
    const run = failedRun(GATE_ERROR);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([run]);
    vi.mocked(workflowsApi.get).mockResolvedValue(run);

    renderPage();
    await openRunsTab();
    fireEvent.click(await waitFor(() => screen.getByTestId('run-row-' + run.run_id)));

    const banner = await waitFor(() => screen.getByTestId('run-level-error'));
    // 不截断：作者必须看见完整 rule_id 清单与证据摘录
    expect(banner).toHaveTextContent('RULE_STYLE_REPETITION_TRIGRAM');
    expect(banner).toHaveTextContent('evidence=');
    // 关键不变量：终态时间线默认收起，错误条必须在 details 之外
    expect(screen.getByTestId('run-timeline-details')).not.toHaveAttribute('open');
    expect(banner.closest('details')).toBeNull();
  });

  it('真 FAILED ⇒ run 行徽标悬停也给出原因（此前 title 是 undefined）', async () => {
    const run = failedRun(GATE_ERROR);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([run]);
    vi.mocked(workflowsApi.get).mockResolvedValue(run);

    renderPage();
    await openRunsTab();
    const row = await waitFor(() => screen.getByTestId('run-row-' + run.run_id));
    // title 落在包着徽标的**外层** span 上（内层是徽标本体，title 在它父节点）
    const badge = within(row).getByText('失败');
    expect(badge.parentElement?.getAttribute('title')).toContain('RULE_STYLE_REPETITION_TRIGRAM');
  });

  // ---- 反向用例：非真失败的 error 一律不得渲染成红色失败条 ----
  it.each([
    ['rejected（作者主动驳回，非失败）', 'rejected', 'wfr_benign_r'],
    ['rejected-for-revision（按建议改稿，非失败）', 'rejected-for-revision', 'wfr_benign_rfr'],
    ['cancelled by user（用户停手，非失败）', 'cancelled by user', 'wfr_benign_cancel'],
  ])('%s ⇒ 不渲染失败原因条', async (_label, err, runId) => {
    const run = failedRun(err, runId);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([run]);
    vi.mocked(workflowsApi.get).mockResolvedValue(run);

    renderPage();
    await openRunsTab();
    fireEvent.click(await waitFor(() => screen.getByTestId('run-row-' + run.run_id)));
    await waitFor(() => screen.getByTestId('run-timeline-details'));
    expect(screen.queryByTestId('run-level-error')).toBeNull();
  });

  it('COMPLETED run 即使带 error 也不渲染失败条（判据是 status 而非 error 字段非空）', async () => {
    const run = buildRunningDetail({
      run_id: 'wfr_completed_with_error',
      status: 'COMPLETED',
      ended_at: '2026-08-24T10:10:00+00:00',
      error: '一段陈旧的警告文本',
    });
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([run]);
    vi.mocked(workflowsApi.get).mockResolvedValue(run);

    renderPage();
    await openRunsTab();
    fireEvent.click(await waitFor(() => screen.getByTestId('run-row-' + run.run_id)));
    await waitFor(() => screen.getByTestId('run-timeline-details'));
    expect(screen.queryByTestId('run-level-error')).toBeNull();
  });
});

// ===========================================================================
// G5 接线用例（2026-10-15）：纯函数对了不等于页面接上了。
// `chapterState.test.ts` 验的是 findUnreviewedDraft / getButtonAvailability 的判定；
// 这里验的是**页面真的用了它**——把 findUnreviewedDraft 整体摘掉，本组必红。
// ===========================================================================

describe('G5 接线：页面把「审完又改稿」算进提交按钮', () => {
  const T1 = '2026-10-15T10:00:00+00:00'; // 审校完成
  const T2 = '2026-10-15T11:00:00+00:00'; // 审校之后的新草稿

  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'REVIEWED' }));
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([] as ModelProfile[]);
    vi.mocked(workflowsApi.get).mockImplementation(async () => {
      throw Object.assign(new Error('not found'), { status: 404 });
    });
    vi.mocked(workflowsApi.startCommit).mockReset();
    vi.mocked(workflowsApi.startCommit).mockResolvedValue(planResponse('wfr_commit'));
    vi.mocked(projectsApi.get).mockReset();
    vi.mocked(projectsApi.get).mockResolvedValue({
      project_id: 'prj_test',
      name: 'test',
      premise: null,
      genre: null,
      target_words: null,
      status: 'ACTIVE',
      created_at: '',
      updated_at: '',
      fast_mode: null,
    });
  });

  // review run 必须带 workflow_name —— 后端 list_runs_endpoint（runs.py:282）附加；
  // workflow_id 是不透明的 wf_<hex>，前端不能拿它当名字用。
  const reviewRun = (over: Partial<WorkflowRun> = {}): WorkflowRun =>
    buildRunningDetail({
      run_id: 'wfr_review_done',
      workflow_name: 'chapter-review',
      status: 'COMPLETED',
      ended_at: T1,
      started_at: T1,
      error: null,
      ...over,
    });

  const draftAt = (version: number, created_at: string): Draft => ({
    draft_id: `dr_${version}`,
    chapter_id: 'ch_001',
    version,
    content: 'x',
    created_by: 'test',
    prompt_version: null,
    model_id: null,
    created_at,
  });

  it('审校后又产生草稿 ⇒ 提交按钮禁用，标题给出「先重新审校」', async () => {
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([reviewRun()]);
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([
      draftAt(6, T2),
      draftAt(5, T1),
    ]);

    renderPage();

    const btn = await waitFor(() => screen.getByTestId('wf-btn-commit'));
    await waitFor(() => expect(btn).toBeDisabled());
    expect(btn.getAttribute('title')).toContain('未审改动');
    expect(btn.getAttribute('title')).toContain('v6');
    expect(btn.getAttribute('title')).toContain('重新审校');
  });

  it('反向用例：草稿都早于审校完成 ⇒ 提交按钮照常可点', async () => {
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([reviewRun()]);
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([draftAt(5, T1)]);

    renderPage();

    const btn = await waitFor(() => screen.getByTestId('wf-btn-commit'));
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  it('反向用例：run 缺 workflow_name ⇒ 不拦（判不出就放行，不得误禁）', async () => {
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([
      reviewRun({ workflow_name: undefined }),
    ]);
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([draftAt(6, T2)]);

    renderPage();

    const btn = await waitFor(() => screen.getByTestId('wf-btn-commit'));
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  it('反向用例：快通道开着也拦得住（守卫次序的端到端证据）', async () => {
    // 项目级 fast_mode = true + 章节 DRAFTED：没有未审改动时后端与 UI 都放行，
    // 有了未审改动则必须拦（后端守卫在 router 层、早于快通道解析）。
    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter({ status: 'DRAFTED' }));
    vi.mocked(projectsApi.get).mockResolvedValue({
      project_id: 'prj_test',
      name: 'test',
      premise: null,
      genre: null,
      target_words: null,
      status: 'ACTIVE',
      created_at: '',
      updated_at: '',
      fast_mode: true,
    });
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([reviewRun()]);
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([draftAt(6, T2)]);

    renderPage();

    const btn = await waitFor(() => screen.getByTestId('wf-btn-commit'));
    await waitFor(() => expect(btn).toBeDisabled());
    expect(btn.getAttribute('title')).toContain('未审改动');
  });
});

/**
 * G6 挂账批次（四）：**页面接线**判别。
 *
 * ⚠️ 为什么单独一个 describe 而不改那 11 个既有 beforeEach：本文件每个 describe
 * 各带一份完整 mock（历史原因），加两处要改 11 个地方、极易漏；独立块自带
 * mock，判别力等价而改动面小得多。
 *
 * 判别点是「组件真的挂上了并发了请求」——**只断言 testid 存在是不够的**：
 * 把页面里的挂载整行删掉，面板消失、其余 80 条用例仍全绿
 * （同 AGENTS 坑区「断言了结果 ≠ 判别了机制」）。
 */
describe('ChapterDetailPage - G6 挂账批次（四）接线', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(chaptersApi.setRevisionNote).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();

    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter());
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    // 人工评分组件自带 qualityApi.latest 预读；给它 404 走「还没评审」分支。
    // ⚠️ 必须抛**真** `ApiError`：组件判的是 `e instanceof ApiError && e.status === 404`，
    // 抛普通 Error 带 status 字段会落到「加载失败」分支，于是 no-report 提示不渲染——
    // 症状是「testid 找不到」，根因却在 mock 的类型上。
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw new ApiError(404, 'no quality report for chapter ch_001; run evaluate first');
    });
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'PENDING',
      current_node: null,
      pause_payload: null,
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun);
  });

  it('改稿指令面板挂上了：渲染并回显 plan_json.revision_note', async () => {
    vi.mocked(chaptersApi.get).mockResolvedValue(
      baseChapter({ plan_json: { revision_note: '把打脸段写短一点' } }),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });
    // 回显 ⇒ 读的是 chapter.plan_json，不是自己另存一份
    await waitFor(() => {
      expect(screen.getByDisplayValue('把打脸段写短一点')).toBeInTheDocument();
    });
  });

  it('人工评分面板挂上了：挂载即向 qualityApi.latest 取报告', async () => {
    renderPage();
    await waitFor(() => {
      expect(vi.mocked(qualityApi.latest)).toHaveBeenCalledWith('ch_001');
    });
    // 无报告 ⇒ 表单必须**真的 disabled**（不是藏起来、也不是能提交的空表单）：
    // 「打不了分」这件事要在 DOM 上看得见，否则作者只会以为这里坏了。
    // 用 testid 而非文案匹配：文案改字不该让接线判别用例红。
    expect(await screen.findByTestId('judge-no-report')).toBeInTheDocument();
    const pace = await screen.findByLabelText(/节奏/);
    expect(pace).toBeDisabled();
  });
});

// ===========================================================================
// M-20：快通道双写入口（章节页）+ auto_commit 载荷链。
//
// 补口事实：此前 `wf-fast-mode` 在前端测试中 **0 命中**、`auto_commit` 全前端
// **0 命中** ⇒ 同一个项目级开关有两条写路径（项目弹窗 / 章节页），只测了一条。
//
// **返工后的统一语义**（2026-10-23 批次三十二）：
//   - 判定与显示按**有效值**（后端 `GET /projects/{pid}/effective-settings` 给的
//     权威解析结果：项目列 > env > 默认关）——项目列为 NULL 且 env 开启时，
//     后端**实际在快通道**，前端显示「关」就是在撒谎。
//   - 写入一律**显式布尔**：勾选 ⇒ true、取消 ⇒ false（**显式关**，绝不发 null）。
//   - 「提交值 ≠ 当前值才下发」两个入口同款（项目弹窗同款实现）。
// ===========================================================================

describe('ChapterDetailPage - 快通道（双入口统一语义，M-20）', () => {
  const baseProject = (over: Partial<Project> = {}): Project => ({
    project_id: 'prj_001',
    name: '测试项目',
    premise: null,
    genre: null,
    target_words: null,
    status: 'ACTIVE',
    created_at: '',
    updated_at: '',
    fast_mode: null,
    ...over,
  });

  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.startWrite).mockReset();
    vi.mocked(projectsApi.get).mockReset();
    vi.mocked(projectsApi.update).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    vi.mocked(fetchProjectEffectiveSettings).mockReset();
    vi.mocked(fetchProjectEffectiveSettings).mockResolvedValue({
      fast_mode_effective: false,
      fast_mode_source: 'off',
    });

    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter());
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([] as WorkflowRun[]);
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: 'wfr_default',
      status: 'COMPLETED',
      checkpoint_json: null,
      nodes: [],
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: '2026-08-24T10:01:00+00:00',
    } as unknown as WorkflowRun);
    vi.mocked(workflowsApi.startWrite).mockResolvedValue(planResponse('wfr_write'));
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw new ApiError(404, 'no quality report');
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([] as ModelProfile[]);
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject());
    vi.mocked(projectsApi.update).mockResolvedValue(baseProject());
  });

  /** 等项目数据落地（状态文案只在项目值已知时渲染 ⇒ 它就是「已加载」的屏障） */
  async function waitProjectLoaded() {
    return screen.findByTestId('wf-fast-mode-state');
  }

  function fastModeBox(): HTMLInputElement {
    return screen.getByTestId('wf-fast-mode-checkbox') as HTMLInputElement;
  }

  it('勾选 ⇒ projectsApi.update 收到 {fast_mode: true}', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitProjectLoaded();

    await user.click(fastModeBox());
    await waitFor(() => expect(projectsApi.update).toHaveBeenCalledTimes(1));

    const payload = vi.mocked(projectsApi.update).mock.calls[0][1] as unknown as Record<string, unknown>;
    expect(payload.fast_mode).toBe(true);
  });

  it('项目已开启时取消勾选 ⇒ 收到 {fast_mode: false}（核心判别：不得静默空操作）', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject({ fast_mode: true }));
    vi.mocked(fetchProjectEffectiveSettings).mockResolvedValue({
      fast_mode_effective: true,
      fast_mode_source: 'project',
    });
    const user = userEvent.setup();
    renderPage();
    await waitProjectLoaded();

    expect(fastModeBox().checked).toBe(true); // 初值 = 有效值（项目列 = 开）
    await user.click(fastModeBox());
    await waitFor(() => expect(projectsApi.update).toHaveBeenCalledTimes(1));

    const payload = vi.mocked(projectsApi.update).mock.calls[0][1] as unknown as Record<string, unknown>;
    expect(payload.fast_mode).toBe(false); // 显式关，必须真的下发
  });

  it('项目未设置时：勾选 ⇒ true；再取消 ⇒ **显式关**（fast_mode: false，不发 null）', async () => {
    // 返工前的实现把「取消」写成 fast_mode: null ⇒ NULL 项目上**永远写不出显式关**
    // （能力回归）。Lead 裁决：用户点「关」= 显式关 ⇒ 发 false。
    const user = userEvent.setup();
    renderPage();
    await waitProjectLoaded();
    expect(fastModeBox().checked).toBe(false);

    await user.click(fastModeBox()); // 勾选
    await waitFor(() => expect(projectsApi.update).toHaveBeenCalledTimes(1));
    const first = vi.mocked(projectsApi.update).mock.calls[0][1] as unknown as Record<string, unknown>;
    expect(first.fast_mode).toBe(true);

    await user.click(fastModeBox()); // 取消 ⇒ 显式关
    await waitFor(() => expect(projectsApi.update).toHaveBeenCalledTimes(2));
    const second = vi.mocked(projectsApi.update).mock.calls[1][1] as unknown as Record<string, unknown>;
    expect(second.fast_mode).toBe(false); // 显式关，不是 null（null = 未设置）
  });

  it('PATCH 失败 ⇒ 复选框回滚 + 错误提示出现', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject({ fast_mode: true }));
    vi.mocked(fetchProjectEffectiveSettings).mockResolvedValue({
      fast_mode_effective: true,
      fast_mode_source: 'project',
    });
    vi.mocked(projectsApi.update).mockRejectedValue(new Error('后端不可用'));
    const user = userEvent.setup();
    renderPage();
    await waitProjectLoaded();

    await user.click(fastModeBox());
    const errBox = await screen.findByTestId('wf-fast-mode-error');
    expect(errBox).toHaveTextContent('后端不可用');
    // 回滚：复选框恢复勾选（不得停在错误的未勾选态）
    await waitFor(() => expect(fastModeBox().checked).toBe(true));
  });

  it('项目值为 null ⇒ 显示「未设置（当前生效：关）」而不是谎报「已关闭」', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject({ fast_mode: null }));
    renderPage();

    const state = await waitProjectLoaded();
    expect(state).toHaveTextContent('未设置');
    expect(state).toHaveTextContent('当前生效：关');
    expect(fastModeBox().checked).toBe(false);
  });

  it('项目列为 null 且 env 开启（有效值=开）⇒ 开关显示为勾选 + 文案说「当前生效：开」', async () => {
    // T3 核心：项目列 NULL 时后端会按 env 回落 ⇒ 后端**实际在快通道**（跳过审校闸门）。
    // 前端若显示成「关」，作者就无从得知闸门正在被跳过（不知情的闸门跳过）。
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject({ fast_mode: null }));
    vi.mocked(fetchProjectEffectiveSettings).mockResolvedValue({
      fast_mode_effective: true,
      fast_mode_source: 'env',
    });
    renderPage();

    const state = await waitProjectLoaded();
    expect(state).toHaveTextContent('未设置');
    expect(state).toHaveTextContent('当前生效：开');
    expect(fastModeBox().checked).toBe(true); // 勾选态 = 有效值
  });

  it('有效值端点失败 ⇒ 文案说「有效值未知」，不谎报「已关闭」', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject({ fast_mode: null }));
    vi.mocked(fetchProjectEffectiveSettings).mockRejectedValue(new Error('端点挂了'));
    renderPage();

    const state = await waitProjectLoaded();
    expect(state).toHaveTextContent('有效值未知');
    expect(state).not.toHaveTextContent('已关闭');
  });

  it('项目值为 true / false ⇒ 分别显示「已开启」/「已关闭」', async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(baseProject({ fast_mode: true }));
    vi.mocked(fetchProjectEffectiveSettings).mockResolvedValue({
      fast_mode_effective: true,
      fast_mode_source: 'project',
    });
    const { unmount } = renderPage();
    expect(await waitProjectLoaded()).toHaveTextContent('已开启');
    unmount();

    vi.mocked(projectsApi.get).mockResolvedValue(baseProject({ fast_mode: false }));
    vi.mocked(fetchProjectEffectiveSettings).mockResolvedValue({
      fast_mode_effective: false,
      fast_mode_source: 'project',
    });
    renderPage();
    expect(await waitProjectLoaded()).toHaveTextContent('已关闭');
  });

  it('auto_commit 载荷链：快通道关 ⇒ startWrite 载荷不含 auto_commit', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitProjectLoaded();

    const writeBtn = await waitFor(() => screen.getByTestId('wf-btn-write'));
    await user.click(writeBtn);
    await waitFor(() => expect(workflowsApi.startWrite).toHaveBeenCalledTimes(1));

    const payload = vi.mocked(workflowsApi.startWrite).mock.calls[0][2] as unknown as Record<string, unknown>;
    expect('auto_commit' in payload).toBe(false);
  });

  it('auto_commit 载荷链：快通道开过再关 ⇒ startWrite 载荷仍不含 auto_commit', async () => {
    // 快通道关着时后端会忽略 auto_commit，但发出去等于把「作者没同意的自动提交」
    // 写进请求（AGENTS §二 硬规则 10：两个开关是**串联**关系）。
    const user = userEvent.setup();
    renderPage();
    await waitProjectLoaded();

    await user.click(fastModeBox()); // 开
    await waitFor(() => expect(fastModeBox().checked).toBe(true));
    await user.click(fastModeBox()); // 关
    await waitFor(() => expect(fastModeBox().checked).toBe(false));

    await user.click(screen.getByTestId('wf-btn-write'));
    await waitFor(() => expect(workflowsApi.startWrite).toHaveBeenCalledTimes(1));

    const payload = vi.mocked(workflowsApi.startWrite).mock.calls[0][2] as unknown as Record<string, unknown>;
    expect('auto_commit' in payload).toBe(false);
  });

  it('auto_commit 载荷链：快通道开且 auto_commit 勾 ⇒ startWrite 载荷含 auto_commit: true', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitProjectLoaded();

    await user.click(fastModeBox()); // 开快通道
    // 返工后**不再自动勾上**「写完直接提交」——跳过审校与审批必须由作者显式勾选。
    const autoBox = screen.getByTestId('wf-auto-commit-checkbox') as HTMLInputElement;
    expect(autoBox.checked).toBe(false);

    await user.click(autoBox); // 作者显式勾选
    await waitFor(() => expect(autoBox.checked).toBe(true));

    await user.click(screen.getByTestId('wf-btn-write'));
    await waitFor(() => expect(workflowsApi.startWrite).toHaveBeenCalledTimes(1));

    const payload = vi.mocked(workflowsApi.startWrite).mock.calls[0][2] as unknown as Record<string, unknown>;
    expect(payload.auto_commit).toBe(true);
  });

  it('打开快通道**不会**自动勾上「写完直接提交」（跳过审批必须显式勾选）', async () => {
    // 返工前的行为：一个开关顺带打开了另一个「跳过审校与审批」的开关，且无任何提示。
    const user = userEvent.setup();
    renderPage();
    await waitProjectLoaded();

    await user.click(fastModeBox());
    await waitFor(() => expect(fastModeBox().checked).toBe(true));

    const autoBox = screen.getByTestId('wf-auto-commit-checkbox') as HTMLInputElement;
    expect(autoBox.checked).toBe(false);
  });

  it('关掉快通道 ⇒ 「写完直接提交」的勾选态一并清掉（不留「已勾选+禁用」的误导态）', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitProjectLoaded();

    await user.click(fastModeBox()); // 开
    const autoBox = screen.getByTestId('wf-auto-commit-checkbox') as HTMLInputElement;
    await user.click(autoBox); // 显式勾上
    await waitFor(() => expect(autoBox.checked).toBe(true));

    await user.click(fastModeBox()); // 关
    await waitFor(() => expect(fastModeBox().checked).toBe(false));
    await waitFor(() => expect(autoBox.checked).toBe(false));
  });
});

// ===========================================================================
// 轮询失败可恢复：usePoll 遇一次错误即永久停摆 ⇒ 页面必须把错误交出来 +
// 给一个手动重试入口（不自动重试：自动重试会烧额度）。
// ===========================================================================

describe('ChapterDetailPage - 轮询失败可恢复', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.get).mockReset();
    vi.mocked(chaptersApi.listDrafts).mockReset();
    vi.mocked(qualityApi.latest).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(projectsApi.get).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();

    vi.mocked(chaptersApi.get).mockResolvedValue(baseChapter());
    vi.mocked(chaptersApi.listDrafts).mockResolvedValue([] as Draft[]);
    vi.mocked(qualityApi.latest).mockImplementation(async () => {
      throw new ApiError(404, 'no quality report');
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([] as ModelProfile[]);
    vi.mocked(projectsApi.get).mockResolvedValue({
      project_id: 'prj_001',
      name: '测试项目',
      premise: null,
      genre: null,
      target_words: null,
      status: 'ACTIVE',
      created_at: '',
      updated_at: '',
      fast_mode: null,
    });
  });

  it('轮询出错时显示错误与「重试」入口；点重试后错误消失、轮询恢复', async () => {
    const runningRun = {
      run_id: 'wfr_running',
      workflow_id: 'wf_opaque',
      workflow_name: 'chapter-write',
      chapter_id: 'ch_001',
      status: 'RUNNING',
      current_node: 'writer',
      checkpoint_json: null,
      nodes: [],
      error: null,
      retry_count: 0,
      started_at: '2026-08-24T10:00:00+00:00',
      ended_at: null,
    } as unknown as WorkflowRun;
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([runningRun]);

    let shouldFail = true;
    vi.mocked(workflowsApi.get).mockImplementation(async (id: string) => {
      if (id === 'wfr_running' && shouldFail) throw new Error('轮询炸了');
      return runningRun;
    });

    const user = userEvent.setup();
    renderPage();

    const banner = await screen.findByTestId('poll-error-banner');
    expect(banner).toHaveTextContent('轮询炸了');

    shouldFail = false;
    await user.click(screen.getByTestId('poll-retry-btn'));
    await waitFor(() => expect(screen.queryByTestId('poll-error-banner')).toBeNull());
  });
});

