// ProjectInitPanel —— review 多关推进用例。
//
// 拆分说明：原 `ProjectInitPanel.test.tsx` 单文件 2381 行 / 39 例，超出 AGENTS
// 「新增/改动文件不得超 1500 行」硬规则 ⇒ 拆成三份（提交流 / review 多关推进 /
// 卡片编辑与回退）。本份为**review 多关推进**：PAUSED 进审阅视图 → 逐关放行 →
// stage-model 切换 → 恢复挂起的初始化 → 带意见重新生成 → 异步化后的 RUNNING 轮询。
//
// 断言**逐字搬运**，未改任何断言；共用夹具与共用 beforeEach 设定见
// `./__fixtures__/projectInitMocks.ts`。
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ProjectInitPanel } from './ProjectInitPanel';
import type {
  CapabilityBinding,
  ModelProfile,
  ProjectInitResponse,
} from '../api/types';
import {
  baseProject,
  outlineDraft,
  premiseDraft,
  setupProjectInitMocks,
  worldDraft,
} from './__fixtures__/projectInitMocks';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  const { buildProjectInitEndpointsMock } = await import(
    './__fixtures__/projectInitMocks'
  );
  return buildProjectInitEndpointsMock(actual);
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const {
  projectsApi,
  charactersApi,
  workflowsApi,
  capabilityBindingsApi,
  modelProfilesApi,
} = await import('../api/endpoints');

describe('ProjectInitPanel (P1 project-init)', () => {
  beforeEach(() => {
    setupProjectInitMocks({
      projectsApi,
      charactersApi,
      workflowsApi,
      capabilityBindingsApi,
      modelProfilesApi,
    });
  });

  it('PAUSED 进入审阅视图并渲染「第 1 / 4 步 · 题材定位」', async () => {
    vi.mocked(projectsApi.init).mockResolvedValue({
      run_id: 'run_step_2',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    // (b) 进入 review 视图，步骤条第 1 / 4 + 题材定位可见
    await waitFor(() => {
      expect(screen.getByTestId('review-pane')).toBeInTheDocument();
    });
    expect(screen.getByTestId('revision-premi-premise')).toHaveTextContent(
      /第 1 \/ 4 步.*题材定位/,
    );
    // 步骤条 4 项都在
    expect(screen.getByTestId('init-steps-item-premise')).toBeInTheDocument();
    expect(screen.getByTestId('init-steps-item-world')).toBeInTheDocument();
    expect(screen.getByTestId('init-steps-item-character')).toBeInTheDocument();
    expect(screen.getByTestId('init-steps-item-outline')).toBeInTheDocument();
  });

  it('放行：修改 logline 后点击 revision-submit 调用 workflowsApi.resumeInit 携带 revisions.premise_output', async () => {
    // 初次 init 直接进入 premise PAUSED
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_step_3',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);
    // 放行后给一个 mock reply（这里不会再走到 review，因为测试只断言本次 resume 调用）
    vi.mocked(workflowsApi.resumeInit).mockResolvedValue({
      run_id: 'run_step_3',
      status: 'COMPLETED',
      current_node: 'persist_all',
      project_id: 'prj_1',
    } as ProjectInitResponse);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('review-pane')).toBeInTheDocument();
    });

    // (c) 修改 logline 后提交
    fireEvent.change(screen.getByTestId('revision-logline'), {
      target: { value: '修改值' },
    });
    fireEvent.click(screen.getByTestId('revision-submit'));

    await waitFor(() => {
      expect(workflowsApi.resumeInit).toHaveBeenCalledTimes(1);
    });
    const [calledId, payload2] = vi.mocked(workflowsApi.resumeInit).mock.calls[0]!;
    expect(calledId).toBe('run_step_3');
    expect(payload2.human_input.revisions).toBeDefined();
    expect(payload2.human_input.revisions!.premise_output).toBeDefined();
    expect(payload2.human_input.revisions!.premise_output!.logline).toBe('修改值');
  });

  it('二次 PAUSED 推进到第 2 关（world）显示「第 2 / 4 步 · 世界观」', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_step_4',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);
    // 第一次放行：推进到 world（仍 PAUSED）
    vi.mocked(workflowsApi.resumeInit).mockResolvedValueOnce({
      run_id: 'run_step_4',
      status: 'PAUSED',
      current_node: 'world_builder',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'world',
        stage_index: 1,
        stages_total: 4,
        degraded: false,
        draft: worldDraft,
      },
    } as ProjectInitResponse);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('revision-premi-premise')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('revision-submit'));

    // (d) 进入 world 阶段
    await waitFor(() => {
      expect(screen.getByTestId('revision-premi-world')).toBeInTheDocument();
    });
    expect(screen.getByTestId('revision-premi-world')).toHaveTextContent(
      /第 2 \/ 4 步.*世界观/,
    );
    // world 改造后改为结构化控件：core_premise 是独立 textarea（testid = revision-world-core-premise）
    expect(screen.getByTestId('revision-world-core-premise')).toBeInTheDocument();
    // rules 至少 1 张卡片（来自 worldDraft.rules）
    expect(screen.getByTestId('revision-card-world-rules-0')).toBeInTheDocument();
  });

  it('JSON 兜底非法禁用：character 关 characters 类型不符 → JSON 域改坏后 revision-submit disabled', async () => {
    // 构造 character 关 draft，characters 故意不是数组 → 触发 JSON 兜底
    const badCharacterDraft = {
      characters: 'not-an-array',
    };
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_step_5',
      status: 'PAUSED',
      current_node: 'character_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'character',
        stage_index: 2,
        stages_total: 4,
        degraded: false,
        draft: badCharacterDraft,
      },
    } as ProjectInitResponse);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('revision-premi-character')).toBeInTheDocument();
    });

    // (e) characters 字段类型不符 → 渲染 JSON 兜底
    const jsonArea = screen.getByTestId(
      'revision-json-fallback-character-characters',
    ) as HTMLTextAreaElement;
    expect(jsonArea).toBeInTheDocument();
    fireEvent.change(jsonArea, { target: { value: '{bad' } });
    expect(screen.getByTestId('revision-submit')).toBeDisabled();

    // 修好为合法数组对象后恢复
    fireEvent.change(jsonArea, {
      target: { value: JSON.stringify([{ name: '林动' }]) },
    });
    expect(screen.getByTestId('revision-submit')).not.toBeDisabled();
    expect(workflowsApi.resumeInit).not.toHaveBeenCalled();
  });

  it('COMPLETED 到达 done：最后一关 resume 返回 COMPLETED → onDone 被调用且显示引导文案', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_step_6',
      status: 'PAUSED',
      current_node: 'volume_outliner',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'outline',
        stage_index: 3,
        stages_total: 4,
        degraded: false,
        draft: outlineDraft,
      },
    } as ProjectInitResponse);
    // 放行 → COMPLETED
    vi.mocked(workflowsApi.resumeInit).mockResolvedValueOnce({
      run_id: 'run_step_6',
      status: 'COMPLETED',
      current_node: 'persist_all',
      project_id: 'prj_1',
    } as ProjectInitResponse);

    const onDone = vi.fn();
    render(
      <ProjectInitPanel
        projectId="prj_1"
        project={baseProject}
        onDone={onDone}
      />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('revision-premi-outline')).toBeInTheDocument();
    });
    // (f) 放行 → COMPLETED → done
    fireEvent.click(screen.getByTestId('revision-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('done-result')).toBeInTheDocument();
    });
    expect(screen.getByTestId('done-result')).toHaveTextContent(
      /run_id=run_step_6/,
    );
    expect(screen.getByTestId('done-result')).toHaveTextContent(/Story Bible/);
    expect(onDone).toHaveBeenCalledTimes(1);
    const [resp] = onDone.mock.calls[0]!;
    expect(resp.run_id).toBe('run_step_6');
    expect(resp.status).toBe('COMPLETED');
  });

  // ---------------- 新增：review 视图 stage-model-select 联动 -----------------

  it('review 视图显示本关模型（当前绑定高亮），切换发 PUT 并显示「下一关起生效」', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_stage_model',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);

    vi.mocked(capabilityBindingsApi.list).mockResolvedValue([
      {
        capability: 'premise_design',
        label: '题材定位',
        agents: ['premise_designer'],
        profile_ids: ['mpf_default'],
        profiles: [{ profile_id: 'mpf_default', name: 'default-reasoning', model: 'gpt-4o-mini' }],
        legacy_available: false,
      },
      {
        capability: 'world_building',
        label: '世界观',
        agents: ['world_builder'],
        profile_ids: ['mpf_default'],
        profiles: [{ profile_id: 'mpf_default', name: 'default-reasoning', model: 'gpt-4o-mini' }],
        legacy_available: false,
      },
      {
        capability: 'character_design',
        label: '角色设计',
        agents: ['character_designer'],
        profile_ids: ['mpf_default'],
        profiles: [{ profile_id: 'mpf_default', name: 'default-reasoning', model: 'gpt-4o-mini' }],
        legacy_available: false,
      },
      {
        capability: 'volume_outline',
        label: '卷纲',
        agents: ['volume_outliner'],
        profile_ids: ['mpf_default'],
        profiles: [{ profile_id: 'mpf_default', name: 'default-reasoning', model: 'gpt-4o-mini' }],
        legacy_available: false,
      },
    ] as CapabilityBinding[]);
    vi.mocked(modelProfilesApi.list).mockResolvedValue([
      {
        profile_id: 'mpf_default',
        name: 'default-reasoning',
        provider: 'openai_compatible',
        model: 'gpt-4o-mini',
        params: { base_url: 'https://api.openai.com/v1' },
        enabled: 1,
        has_api_key: false,
      },
      {
        profile_id: 'mpf_alt',
        name: 'alt-creative',
        provider: 'openai_compatible',
        model: 'deepseek-chat',
        params: { base_url: 'https://api.deepseek.com/v1' },
        enabled: 1,
        has_api_key: false,
      },
    ] as ModelProfile[]);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('review-pane')).toBeInTheDocument();
    });

    // (a) review 视图渲染 stage-model-select，且当前绑定高亮 mpf_default
    const select = (await screen.findByTestId(
      'stage-model-select',
    )) as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    expect(select.value).toBe('mpf_default');

    // (b) 切换到 mpf_alt → 调 capabilityBindingsApi.bind 并显示成功提示
    fireEvent.change(select, { target: { value: 'mpf_alt' } });
    await waitFor(() => {
      expect(capabilityBindingsApi.bind).toHaveBeenCalledTimes(1);
    });
    const [cap, profileIds] = vi.mocked(capabilityBindingsApi.bind).mock.calls[0]!;
    expect(cap).toBe('premise_design');
    // bind(capability, profileIds) 第二个参数是数组；服务端 PUT body 由 api.put 包装为 {profile_ids: profileIds}
    expect(profileIds).toEqual(['mpf_alt']);

    const status = await screen.findByTestId('stage-model-status');
    expect(status).toHaveTextContent(/下一关起生效/);
  });

  // ---------------- P1.2 review 视图「本次实际使用」行 -----------------

  it('review 视图在 stage_models 含本关 agent 时渲染「本次实际使用：xxx」（短名剥前缀）', async () => {
    // POST init 直接 PAUSED 同步返回 + 紧跟 GET /runs/{id} 异步取 stage_models
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_stage_models',
      status: 'PAUSED',
      current_node: 'world_builder',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'world',
        stage_index: 1,
        stages_total: 4,
        degraded: false,
        draft: worldDraft,
      },
    } as ProjectInitResponse);
    vi.mocked(workflowsApi.get).mockResolvedValueOnce({
      run_id: 'run_stage_models',
      status: 'PAUSED',
      current_node: 'world_builder',
      pause_payload: {
        stage: 'world',
        stage_index: 1,
        stages_total: 4,
        degraded: false,
        draft: worldDraft,
      },
      workflow_name: 'project-init',
      nodes: [],
      stage_models: {
        world_builder: 'openai_compatible/k3-256k',
        premise_designer: 'mock/mock',
      },
    } as never);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('review-pane')).toBeInTheDocument();
    });

    // 等待 panel 内部 GET 异步拉到的 stage_models 触发渲染（best-effort）
    const used = await screen.findByTestId('stage-used-model');
    expect(used).toBeInTheDocument();
    // 短名：剥掉 provider 前缀
    expect(used).toHaveTextContent('本次实际使用：k3-256k');
    expect(used).toHaveTextContent(/本次实际使用/);
    // 不应再包含 provider 前缀
    expect(used.textContent ?? '').not.toContain('openai_compatible/');
    // title 属性挂全名，便于悬停查看
    expect(used.getAttribute('title')).toBe('openai_compatible/k3-256k');
  });

  it('stage_models 缺失时不渲染「本次实际使用」行（不影响下拉）', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_no_stage_models',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);
    // GET /runs/{id} 返回空 stage_models（PAUSED 但 ai_call_logs 暂未落）
    vi.mocked(workflowsApi.get).mockResolvedValueOnce({
      run_id: 'run_no_stage_models',
      status: 'PAUSED',
      current_node: 'premise_designer',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
      workflow_name: 'project-init',
      nodes: [],
      stage_models: {},
    } as never);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('review-pane')).toBeInTheDocument();
    });
    // 给异步 GET 一个轮询机会
    await waitFor(() => {
      expect(workflowsApi.get).toHaveBeenCalled();
    });
    // 等一会让 stage_models=null 落地后断言「本次实际使用」行缺失
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId('stage-used-model')).toBeNull();
    // 下拉本身不受影响
    expect(screen.getByTestId('stage-model-select')).toBeInTheDocument();
  });

  // ---------------- P1.1 恢复挂起的初始化 ------------------------------

  it('探测到 PAUSED project-init run → 顶部横幅显示「世界观 · 第 2/4 步」且「继续审阅」在', async () => {
    // 模拟 listByProject 返回 1 条 PAUSED 的 project-init run（world 关）
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([
      {
        run_id: 'wfr_127cb28d4468',
        workflow_id: 'wf_proj_init',
        workflow_name: 'project-init',
        chapter_id: null,
        status: 'PAUSED',
        current_node: 'world_builder',
        checkpoint_json: {
          stage: 'world',
          stage_index: 1,
          stages_total: 4,
          degraded: false,
          draft: worldDraft,
        },
        error: null,
        retry_count: 0,
        started_at: '2026-08-27T10:00:00Z',
        ended_at: null,
        nodes: [],
      },
    ]);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    // 展开面板触发挂起 run 探测
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });

    // 横幅出现，含「世界观 · 第 2/4 步」字样
    await waitFor(() => {
      expect(screen.getByTestId('suspended-resume-banner')).toBeInTheDocument();
    });
    expect(screen.getByTestId('suspended-resume-banner')).toHaveTextContent(
      /世界观/,
    );
    expect(screen.getByTestId('suspended-resume-banner')).toHaveTextContent(
      /第 2\/4 步/,
    );
    expect(screen.getByTestId('resume-suspended')).toBeInTheDocument();
    expect(screen.getByTestId('dismiss-suspended')).toBeInTheDocument();
  });

  it('点击「继续审阅」→ 进入 review 视图且读到 pause_payload.draft 字段', async () => {
    const suspendedRunId = 'wfr_127cb28d4468';
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([
      {
        run_id: suspendedRunId,
        workflow_id: 'wf_proj_init',
        workflow_name: 'project-init',
        chapter_id: null,
        status: 'PAUSED',
        current_node: 'world_builder',
        checkpoint_json: { stage: 'world', stage_index: 1, draft: worldDraft },
        error: null,
        retry_count: 0,
        started_at: '2026-08-27T10:00:00Z',
        ended_at: null,
        nodes: [],
      },
    ]);
    // GET /runs/{id} 返回带 pause_payload 的完整 run
    vi.mocked(workflowsApi.get).mockResolvedValue({
      run_id: suspendedRunId,
      workflow_id: 'wf_proj_init',
      workflow_name: 'project-init',
      chapter_id: null,
      status: 'PAUSED',
      current_node: 'world_builder',
      checkpoint_json: { stage: 'world', stage_index: 1, draft: worldDraft },
      pause_payload: {
        stage: 'world',
        stage_index: 1,
        stages_total: 4,
        degraded: false,
        draft: worldDraft,
      },
      error: null,
      retry_count: 0,
      started_at: '2026-08-27T10:00:00Z',
      ended_at: null,
      nodes: [],
    });

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('suspended-resume-banner')).toBeInTheDocument();
    });

    // 点击「继续审阅」
    fireEvent.click(screen.getByTestId('resume-suspended'));

    // (a) 调了 workflowsApi.get 且参数正确
    await waitFor(() => {
      expect(workflowsApi.get).toHaveBeenCalledWith(suspendedRunId);
    });

    // (b) 进入 review 视图
    await waitFor(() => {
      expect(screen.getByTestId('review-pane')).toBeInTheDocument();
    });
    // (c) pause_payload.draft 字段值被填入控件（world.core_premise 是 textarea）
    const corePremise = screen.getByTestId(
      'revision-world-core-premise',
    ) as HTMLTextAreaElement;
    expect(corePremise).toBeInTheDocument();
    expect(corePremise.value).toBe('武道为尊，强者通神');
  });

  it('点击「忽略」→ 横幅消失且 sessionStorage 写入 run_id', async () => {
    const suspendedRunId = 'wfr_127cb28d4468';
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([
      {
        run_id: suspendedRunId,
        workflow_id: 'wf_proj_init',
        workflow_name: 'project-init',
        chapter_id: null,
        status: 'PAUSED',
        current_node: 'world_builder',
        checkpoint_json: { stage: 'world', stage_index: 1, draft: worldDraft },
        error: null,
        retry_count: 0,
        started_at: '2026-08-27T10:00:00Z',
        ended_at: null,
        nodes: [],
      },
    ]);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('suspended-resume-banner')).toBeInTheDocument();
    });

    // 点击「忽略」
    fireEvent.click(screen.getByTestId('dismiss-suspended'));

    // (a) 横幅消失
    await waitFor(() => {
      expect(
        screen.queryByTestId('suspended-resume-banner'),
      ).not.toBeInTheDocument();
    });
    // (b) sessionStorage 写入 run_id（key 含 projectId）
    const raw = sessionStorage.getItem('novelos:project-init:dismissed:prj_1');
    expect(raw).not.toBeNull();
    const arr = JSON.parse(raw ?? '[]') as string[];
    expect(arr).toContain(suspendedRunId);
    // (c) 没有触发任何恢复动作
    expect(workflowsApi.get).not.toHaveBeenCalled();
    expect(workflowsApi.resumeInit).not.toHaveBeenCalled();
  });

  // ---------------- P1.2：带意见重新生成（regenerate）入口 --------------------

  it('a) 填意见点「带意见重新生成」→ resumeInit 收到 (runId, { human_input: { regenerate_note: "意见内容" }, regenerate: true })', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_regen_1',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);
    // 重新生成后给一个 mock：仍在 premise 阶段、给一份新 draft
    vi.mocked(workflowsApi.resumeInit).mockResolvedValueOnce({
      run_id: 'run_regen_1',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: {
          ...premiseDraft,
          logline: '按意见重生成的新简介',
        },
      },
    } as ProjectInitResponse);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('review-pane')).toBeInTheDocument();
    });

    // 意见区在 review 视图底部
    const noteArea = screen.getByTestId('regenerate-note') as HTMLTextAreaElement;
    expect(noteArea).toBeInTheDocument();
    expect(noteArea).not.toBeDisabled();

    fireEvent.change(noteArea, { target: { value: '意见内容' } });
    fireEvent.click(screen.getByTestId('regenerate-submit'));

    await waitFor(() => {
      expect(workflowsApi.resumeInit).toHaveBeenCalledTimes(1);
    });
    const [calledId, payload] = vi.mocked(workflowsApi.resumeInit).mock.calls[0]!;
    expect(calledId).toBe('run_regen_1');
    expect(payload.regenerate).toBe(true);
    expect(payload.human_input.regenerate_note).toBe('意见内容');
    // 不应同时携带 revisions（重新生成语义是丢弃当前编辑、走 AI 重跑本关）
    expect(payload.human_input.revisions).toBeUndefined();
  });

  it('b) 不填意见点「带意见重新生成」→ resumeInit 收到 (runId, { human_input: { regenerate_note: "" }, regenerate: true })', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_regen_2',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);
    vi.mocked(workflowsApi.resumeInit).mockResolvedValueOnce({
      run_id: 'run_regen_2',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('review-pane')).toBeInTheDocument();
    });

    // 不填意见，直接点
    fireEvent.click(screen.getByTestId('regenerate-submit'));

    await waitFor(() => {
      expect(workflowsApi.resumeInit).toHaveBeenCalledTimes(1);
    });
    const [calledId, payload] = vi.mocked(workflowsApi.resumeInit).mock.calls[0]!;
    expect(calledId).toBe('run_regen_2');
    expect(payload.regenerate).toBe(true);
    expect(payload.human_input.regenerate_note).toBe('');
  });

  it('c) 重生成响应 PAUSED（新 draft）→ 审阅视图更新显示新内容（logline 字段值变化）', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_regen_3',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);
    // 重新生成后返回新 logline 的 draft
    vi.mocked(workflowsApi.resumeInit).mockResolvedValueOnce({
      run_id: 'run_regen_3',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: {
          ...premiseDraft,
          logline: 'AI 重新生成的简介：废柴逆袭',
        },
      },
    } as ProjectInitResponse);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('review-pane')).toBeInTheDocument();
    });
    // 初始 draft 的 logline 是「少年得古籍，逆天改命」
    expect(
      (screen.getByTestId('revision-logline') as HTMLTextAreaElement).value,
    ).toBe('少年得古籍，逆天改命');

    // 触发重新生成（不带意见也可）
    fireEvent.click(screen.getByTestId('regenerate-submit'));

    await waitFor(() => {
      expect(workflowsApi.resumeInit).toHaveBeenCalledTimes(1);
    });
    // 等待 review 视图重新挂载、显示新 draft 的 logline
    await waitFor(() => {
      const ta = screen.getByTestId('revision-logline') as HTMLTextAreaElement;
      expect(ta.value).toBe('AI 重新生成的简介：废柴逆袭');
    });
  });

  it('d) busy 期间「带意见重新生成」按钮与意见输入框均 disabled', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_regen_4',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);
    // 故意让 resumeInit 不立刻 resolve，便于断言 busy 期
    let resolveResume!: (resp: ProjectInitResponse) => void;
    const resumePending = new Promise<ProjectInitResponse>((resolve) => {
      resolveResume = resolve;
    });
    vi.mocked(workflowsApi.resumeInit).mockReturnValueOnce(resumePending);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('review-pane')).toBeInTheDocument();
    });

    // 触发重新生成，进入 busy
    fireEvent.click(screen.getByTestId('regenerate-submit'));

    // busy 期：按钮与输入框均 disabled
    await waitFor(() => {
      expect(screen.getByTestId('regenerate-submit')).toBeDisabled();
    });
    expect(screen.getByTestId('regenerate-note')).toBeDisabled();
    // 放行按钮也应处于 disabled（因为 busy 态）
    expect(screen.getByTestId('revision-submit-busy')).toBeInTheDocument();

    // 释放 promise，避免泄漏
    resolveResume({
      run_id: 'run_regen_4',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);
  });

  // ---------------- Bug 修复：resume/init 异步化后的 RUNNING 轮询 ------------
  // 后端 POST /runs/{id}/resume 与 POST /projects/init 已异步化：HTTP 响应固定返回
  // status=RUNNING，真实终态靠 GET /runs/{id} 轮询。前端必须轮询拿到终态再走
  // 原 PAUSED/COMPLETED 分支，否则会误报「run 终态异常：status=RUNNING」。

  it('A) resume 首响应 RUNNING → 轮询到 PAUSED → 进入 review 视图（含 stage_models）', async () => {
    // init 同步 PAUSED 进入 review 视图（用既有路径）
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_async_resume_paused',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseDraft,
      },
    } as ProjectInitResponse);
    // 放行首响应：RUNNING（后端异步化契约）
    vi.mocked(workflowsApi.resumeInit).mockResolvedValueOnce({
      run_id: 'run_async_resume_paused',
      status: 'RUNNING',
      current_node: null,
      project_id: 'prj_1',
    } as ProjectInitResponse);
    // 后续 GET 轮询：先 RUNNING 一次，再 PAUSED（带 stage_models）
    vi.mocked(workflowsApi.get)
      .mockResolvedValueOnce({
        run_id: 'run_async_resume_paused',
        status: 'RUNNING',
        current_node: 'world_builder',
        pause_payload: null,
        workflow_name: 'project-init',
        nodes: [],
        stage_models: {},
      } as never)
      .mockResolvedValueOnce({
        run_id: 'run_async_resume_paused',
        status: 'PAUSED',
        current_node: 'world_builder',
        pause_payload: {
          stage: 'world',
          stage_index: 1,
          stages_total: 4,
          degraded: false,
          draft: worldDraft,
        },
        workflow_name: 'project-init',
        nodes: [],
        stage_models: {
          world_builder: 'openai_compatible/k3-256k',
          premise_designer: 'openai_compatible/mock',
        },
      } as never);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    // 进入 premise review
    await waitFor(() => {
      expect(screen.getByTestId('revision-premi-premise')).toBeInTheDocument();
    });
    // 触发 resume：首响应 RUNNING，前端必须轮询
    fireEvent.click(screen.getByTestId('revision-submit'));

    // (a) 轮询调用了 GET /runs/{id}（至少一次）
    await waitFor(() => {
      expect(workflowsApi.get).toHaveBeenCalled();
    });
    // (b) 轮询到了 world PAUSED → review 视图切到 world（第 2 / 4 步）
    await waitFor(() => {
      expect(screen.getByTestId('revision-premi-world')).toBeInTheDocument();
    });
    expect(screen.getByTestId('revision-premi-world')).toHaveTextContent(
      /第 2 \/ 4 步.*世界观/,
    );
    // (c) 无错误 banner（不再报「run 终态异常：status=RUNNING」）
    expect(screen.queryByText(/run 终态异常/)).toBeNull();
    // (d) stage_models 已落到 review 视图（短名剥前缀）
    const used = await screen.findByTestId('stage-used-model');
    expect(used).toHaveTextContent('本次实际使用：k3-256k');
    expect(used.textContent ?? '').not.toContain('openai_compatible/');
  });

  it('B) resume 首响应 RUNNING → 轮询到 COMPLETED → done 视图 + onDone 回调', async () => {
    // init 同步 PAUSED（最后一关 outline）→ review
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_async_resume_done',
      status: 'PAUSED',
      current_node: 'volume_outliner',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'outline',
        stage_index: 3,
        stages_total: 4,
        degraded: false,
        draft: outlineDraft,
      },
    } as ProjectInitResponse);
    // 放行首响应：RUNNING
    vi.mocked(workflowsApi.resumeInit).mockResolvedValueOnce({
      run_id: 'run_async_resume_done',
      status: 'RUNNING',
      current_node: null,
      project_id: 'prj_1',
    } as ProjectInitResponse);
    // GET 轮询：RUNNING → COMPLETED
    vi.mocked(workflowsApi.get)
      .mockResolvedValueOnce({
        run_id: 'run_async_resume_done',
        status: 'RUNNING',
        current_node: 'persist_all',
        pause_payload: null,
        workflow_name: 'project-init',
        nodes: [],
        stage_models: {},
      } as never)
      .mockResolvedValueOnce({
        run_id: 'run_async_resume_done',
        status: 'COMPLETED',
        current_node: 'persist_all',
        pause_payload: null,
        workflow_name: 'project-init',
        nodes: [],
        stage_models: {},
      } as never);

    const onDone = vi.fn();
    render(
      <ProjectInitPanel
        projectId="prj_1"
        project={baseProject}
        onDone={onDone}
      />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(screen.getByTestId('revision-premi-outline')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('revision-submit'));

    // 轮询到 COMPLETED → done
    await waitFor(() => {
      expect(screen.getByTestId('done-result')).toBeInTheDocument();
    });
    expect(screen.getByTestId('done-result')).toHaveTextContent(
      /run_id=run_async_resume_done/,
    );
    expect(screen.getByTestId('done-result')).toHaveTextContent(/Story Bible/);
    // 无错误 banner
    expect(screen.queryByText(/run 终态异常/)).toBeNull();
    // onDone 用 polled COMPLETED 响应回调
    expect(onDone).toHaveBeenCalledTimes(1);
    const [resp] = onDone.mock.calls[0]!;
    expect(resp.run_id).toBe('run_async_resume_done');
    expect(resp.status).toBe('COMPLETED');
  });

  it('C) init 异步化首响应 RUNNING → 轮询到 PAUSED → 进入 review 视图', async () => {
    // init 首响应：RUNNING（后端 init 也异步化路径）
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_async_init_paused',
      status: 'RUNNING',
      current_node: null,
      project_id: 'prj_1',
    } as ProjectInitResponse);
    // GET 轮询：RUNNING → PAUSED
    vi.mocked(workflowsApi.get)
      .mockResolvedValueOnce({
        run_id: 'run_async_init_paused',
        status: 'RUNNING',
        current_node: 'premise_designer',
        pause_payload: null,
        workflow_name: 'project-init',
        nodes: [],
        stage_models: {},
      } as never)
      .mockResolvedValueOnce({
        run_id: 'run_async_init_paused',
        status: 'PAUSED',
        current_node: 'premise_designer',
        pause_payload: {
          stage: 'premise',
          stage_index: 0,
          stages_total: 4,
          degraded: false,
          draft: premiseDraft,
        },
        workflow_name: 'project-init',
        nodes: [],
        stage_models: {
          premise_designer: 'openai_compatible/gpt-4o-mini',
        },
      } as never);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    // (a) 调了 GET /runs/{id}（轮询已启动）
    await waitFor(() => {
      expect(workflowsApi.get).toHaveBeenCalled();
    });
    // (b) 轮询到 PAUSED → 进入 review 视图（第 1 / 4 步 premise）
    await waitFor(() => {
      expect(screen.getByTestId('revision-premi-premise')).toBeInTheDocument();
    });
    expect(screen.getByTestId('revision-premi-premise')).toHaveTextContent(
      /第 1 \/ 4 步.*题材定位/,
    );
    // (c) 无错误 banner
    expect(screen.queryByText(/run 终态异常/)).toBeNull();
    // (d) stage_models 已传入 ReviewPane
    const used = await screen.findByTestId('stage-used-model');
    expect(used).toHaveTextContent('本次实际使用：gpt-4o-mini');
  });
});
