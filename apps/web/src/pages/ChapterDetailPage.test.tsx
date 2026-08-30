/** @vitest-environment jsdom */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ChapterDetailPage } from './ChapterDetailPage';
import { chaptersApi, modelProfilesApi, qualityApi, workflowsApi } from '../api/endpoints';
import type {
  Chapter,
  Draft,
  ModelProfile,
  WorkflowRun,
  WorkflowStartResponse,
} from '../api/types';

// ---- API mock ----
vi.mock('../api/endpoints', () => ({
  chaptersApi: {
    get: vi.fn(),
    listDrafts: vi.fn(),
    delete: vi.fn(),
    update: vi.fn(),
    createDraft: vi.fn(),
  },
  qualityApi: {
    latest: vi.fn(),
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
  },
  modelProfilesApi: {
    list: vi.fn(),
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

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });

    const planBtn = screen.getByTestId('wf-btn-plan');
    // 章节状态为 PLANNED 时 plan 按钮可用（依据 chapterState 状态机）
    expect(planBtn).not.toBeDisabled();

    fireEvent.click(planBtn);

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy.mock.calls[0][0]).toMatch(/章节已有计划/);
    expect(vi.mocked(workflowsApi.startPlan)).not.toHaveBeenCalled();

    confirmSpy.mockRestore();
  });

  it('章节已有 plan_json 时确认通过后会调 startPlan', async () => {
    const chWithPlan = baseChapter({
      plan_json: { chapter_goal: '旧目标', expected_word_count: 3000 },
    });
    vi.mocked(chaptersApi.get).mockResolvedValue(chWithPlan);
    vi.mocked(workflowsApi.startPlan).mockResolvedValue(planResponse('wfr_plan_002'));

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('wf-btn-plan'));

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startPlan)).toHaveBeenCalledTimes(1);
    });
    expect(vi.mocked(workflowsApi.startPlan)).toHaveBeenCalledWith(
      'prj_001',
      'ch_001',
      undefined,
    );

    confirmSpy.mockRestore();
  });

  it('章节无 plan_json 时不弹确认，直接调 startPlan', async () => {
    const chNoPlan = baseChapter({ plan_json: {} });
    vi.mocked(chaptersApi.get).mockResolvedValue(chNoPlan);
    vi.mocked(workflowsApi.startPlan).mockResolvedValue(planResponse('wfr_plan_003'));

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('chapter-header')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('wf-btn-plan'));

    await waitFor(() => {
      expect(vi.mocked(workflowsApi.startPlan)).toHaveBeenCalledTimes(1);
    });
    expect(confirmSpy).not.toHaveBeenCalled();

    confirmSpy.mockRestore();
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

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

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
    expect(confirmSpy).not.toHaveBeenCalled();

    confirmSpy.mockRestore();
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
    // 首轮 list 显示 RUNNING（横幅应显示），随后 list 变 COMPLETED（横幅应消失）
    let listCallCount = 0;
    vi.mocked(workflowsApi.listByProject).mockImplementation(async () => {
      listCallCount += 1;
      if (listCallCount >= 2) {
        return [
          {
            ...runningList[0],
            status: 'COMPLETED',
            ended_at: new Date().toISOString(),
          } as WorkflowRun,
        ];
      }
      return runningList;
    });
    // poll 第一轮返回 RUNNING详情，第二轮返回 COMPLETED详情
    let getCallCount = 0;
    vi.mocked(workflowsApi.get).mockImplementation(async () => {
      getCallCount += 1;
      if (getCallCount >= 2) {
        return buildRunningDetail({
          status: 'COMPLETED',
          ended_at: new Date().toISOString(),
        });
      }
      return buildRunningDetail();
    });

    renderPage();

    // 第一阶段：横幅应显示
    await waitFor(() => {
      expect(screen.getByTestId('workflow-running-banner')).toBeInTheDocument();
    });
    // 第二阶段：经过至少 2 轮 poll 后（2s 间隔），横幅应消失
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
    let listCallCount = 0;
    vi.mocked(workflowsApi.listByProject).mockImplementation(async () => {
      listCallCount += 1;
      // 第一次返回 RUNNING 行；之后切到 FAILED（让 activeRun 消失、横幅也消失）
      if (listCallCount === 1) return [runningRow];
      return [
        {
          ...runningRow,
          status: 'FAILED',
          ended_at: new Date().toISOString(),
          error: '节点失败',
        },
      ];
    });
    let getCallCount = 0;
    vi.mocked(workflowsApi.get).mockImplementation(async () => {
      getCallCount += 1;
      if (getCallCount === 1) return buildRunningDetail(); // RUNNING
      // 第二轮：FAILED（poll 命中 stopWhen 停止）
      return buildRunningDetail({
        status: 'FAILED',
        ended_at: new Date().toISOString(),
        error: '节点失败',
      });
    });

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
// 按次选择模型档案：plan/write/review 三个按钮从下拉选择 profile_id，
// 启动后请求 payload 带 model_overrides；commit 不显示下拉。
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