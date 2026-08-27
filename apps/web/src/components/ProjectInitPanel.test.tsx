import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ProjectInitPanel } from './ProjectInitPanel';
import type {
  CapabilityBinding,
  ModelProfile,
  Project,
  ProjectInitResponse,
} from '../api/types';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    projectsApi: {
      ...((actual.projectsApi as unknown) as object),
      init: vi.fn(),
    },
    charactersApi: {
      ...((actual.charactersApi as unknown) as object),
      listByProject: vi.fn(),
    },
    workflowsApi: {
      ...((actual.workflowsApi as unknown) as object),
      resumeInit: vi.fn(),
      resume: vi.fn(),
    },
    capabilityBindingsApi: {
      ...((actual.capabilityBindingsApi as unknown) as object),
      list: vi.fn(),
      bind: vi.fn(),
      unbind: vi.fn(),
    },
    modelProfilesApi: {
      ...((actual.modelProfilesApi as unknown) as object),
      list: vi.fn(),
    },
  };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const {
  projectsApi,
  charactersApi,
  workflowsApi,
  capabilityBindingsApi,
  modelProfilesApi,
} = await import('../api/endpoints');

const baseProject: Project = {
  project_id: 'prj_1',
  name: '九天神诀',
  premise: null,
  genre: '玄幻',
  target_words: 300000,
  status: 'ACTIVE',
  created_at: '2026-08-27T00:00:00Z',
  updated_at: '2026-08-27T00:00:00Z',
};

const premiseDraft = {
  title: '九天神诀',
  genre: '玄幻',
  logline: '少年得古籍，逆天改命',
  positioning: '无敌爽文',
  selling_points: ['节奏快', '升级爽'],
  protagonist: { name: '林动', age: 16 },
};

const worldDraft = {
  core_premise: '武道为尊，强者通神',
  rules: [{ id: 'r1', statement: '灵气可修炼' }],
  locations: [],
  factions: [],
};

const outlineDraft = {
  volume: { number: 1, title: '初入宗门', arc_summary: '少年成长' },
  chapter_seeds: [
    { number: 1, title: '觉醒', role: 'intro', one_sentence: 's', expected_word_count: 3000, key_beats: [] },
  ],
};

const characterDraft = {
  characters: [
    { name: '林动', role: 'protagonist', motivation: '复仇', goal: '登顶', conflict: '资源匮乏', relationships: [] },
  ],
};

// 引用以避免 TS6133（characterDraft 留作下一关 stage 复用，保留定义便于后续扩展）。
void characterDraft;

describe('ProjectInitPanel (P1 project-init)', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.init).mockReset();
    vi.mocked(charactersApi.listByProject).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(capabilityBindingsApi.list).mockReset();
    vi.mocked(capabilityBindingsApi.bind).mockReset();
    vi.mocked(capabilityBindingsApi.unbind).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    // 默认探测：空角色
    vi.mocked(charactersApi.listByProject).mockResolvedValue([]);
    // 默认 bindings / profiles：空返回，组件会按空态渲染（不抛错）
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue([] as CapabilityBinding[]);
    vi.mocked(modelProfilesApi.list).mockResolvedValue([] as ModelProfile[]);
    vi.mocked(capabilityBindingsApi.bind).mockImplementation(async (cap, ids) => ({
      capability: cap,
      label: cap,
      agents: [],
      profile_ids: ids,
      profiles: ids.map((id) => ({ profile_id: id, name: id, model: id })),
      legacy_available: false,
    }));
    vi.mocked(capabilityBindingsApi.unbind).mockResolvedValue(undefined as never);
  });

  it('提交按钮在 logline 为空时禁用', async () => {
    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    // 等待表单展开
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    const submit = screen.getByTestId('project-init-submit');
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    expect(submit).not.toBeDisabled();
  });

  it('提交时按预期参数调用 projectsApi.init 并传 projectId（默认 step_mode=true）', async () => {
    vi.mocked(projectsApi.init).mockResolvedValue({
      run_id: 'run_init_1',
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
    fireEvent.change(screen.getByTestId('project-init-genre'), {
      target: { value: '  玄幻  ' },
    });
    fireEvent.change(screen.getByTestId('project-init-target-words'), {
      target: { value: '300000' },
    });
    fireEvent.change(screen.getByTestId('project-init-chapter-seed-count'), {
      target: { value: '12' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(projectsApi.init).toHaveBeenCalledTimes(1);
    });
    const [payload] = vi.mocked(projectsApi.init).mock.calls[0]!;
    expect(payload.project_id).toBe('prj_1');
    // chapter_seed_count 走 payload 顶层（与后端 ProjectInitRequest.chapter_seed_count 对齐），
    // 不应出现在 brief 内。
    expect(payload.chapter_seed_count).toBe(12);
    expect(
      Object.prototype.hasOwnProperty.call(payload.brief, 'chapter_seed_count'),
    ).toBe(false);
    expect(payload.brief.logline).toBe('少年得古籍，逆天改命');
    expect(payload.brief.genre).toBe('玄幻'); // 已 trim
    expect(payload.brief.title).toBe('九天神诀');
    expect(payload.brief.platform).toBe('番茄·男频');
    expect(payload.brief.target_words).toBe(300000);
    // 新增：默认 step_mode=true（避免后端走老的一次性生成路径）
    expect(payload.step_mode).toBe(true);
  });

  it('已有角色时必须勾选确认才能提交', async () => {
    // 探测返回 2 个角色 → needsConfirm = true
    vi.mocked(charactersApi.listByProject).mockResolvedValue([
      { character_id: 'c1' } as never,
      { character_id: 'c2' } as never,
    ]);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    // 警告 banner 出现
    await waitFor(() => {
      expect(screen.getByTestId('project-init-warning')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    // 未勾选 → 提交仍禁用
    const submit = screen.getByTestId('project-init-submit');
    expect(submit).toBeDisabled();

    fireEvent.click(screen.getByTestId('project-init-overwrite-checkbox'));
    expect(submit).not.toBeDisabled();

    vi.mocked(projectsApi.init).mockResolvedValue({
      run_id: 'run_init_2',
      status: 'COMPLETED',
      current_node: 'persist_all',
      project_id: 'prj_1',
    } as ProjectInitResponse);
    fireEvent.click(submit);
    await waitFor(() => {
      expect(projectsApi.init).toHaveBeenCalledTimes(1);
    });
  });

  it('chapter_seed_count 填 150 时提交按钮 disabled', async () => {
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
    fireEvent.change(screen.getByTestId('project-init-chapter-seed-count'), {
      target: { value: '150' },
    });

    // 超过上限（100）→ canSubmit 为 false → 提交按钮 disabled
    expect(screen.getByTestId('project-init-submit')).toBeDisabled();
    expect(projectsApi.init).not.toHaveBeenCalled();

    // 回到合法范围后恢复
    fireEvent.change(screen.getByTestId('project-init-chapter-seed-count'), {
      target: { value: '12' },
    });
    expect(screen.getByTestId('project-init-submit')).not.toBeDisabled();
  });

  it('成功时展示 run_id 并回调 onDone', async () => {
    const onDone = vi.fn();
    vi.mocked(projectsApi.init).mockResolvedValue({
      run_id: 'run_init_3',
      status: 'COMPLETED',
      current_node: 'persist_all',
      project_id: 'prj_1',
    } as ProjectInitResponse);

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
      expect(screen.getByTestId('done-result')).toHaveTextContent(
        /run_id=run_init_3/,
      );
    });
    expect(onDone).toHaveBeenCalledTimes(1);
    const [resp] = onDone.mock.calls[0]!;
    expect(resp.run_id).toBe('run_init_3');
  });

  // ---------------- 新增：分步审阅测试 ----------------------------------

  it('默认 step-mode checkbox 勾选，且提交请求体含 step_mode:true', async () => {
    vi.mocked(projectsApi.init).mockResolvedValue({
      run_id: 'run_step_1',
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
    // (a) 默认勾选
    const cb = screen.getByTestId(
      'project-init-step-mode-checkbox',
    ) as HTMLInputElement;
    expect(cb.checked).toBe(true);

    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(projectsApi.init).toHaveBeenCalledTimes(1);
    });
    const [payload] = vi.mocked(projectsApi.init).mock.calls[0]!;
    expect(payload.step_mode).toBe(true);
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
    expect(payload2.human_input.revisions.premise_output).toBeDefined();
    expect(payload2.human_input.revisions.premise_output.logline).toBe('修改值');
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
    // world 是 JSON 文本域
    expect(screen.getByTestId('revision-json-world')).toBeInTheDocument();
  });

  it('JSON 非法禁用：把 world JSON 改成 "{bad" 后 revision-submit disabled 且有错误提示', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_step_5',
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
      expect(screen.getByTestId('revision-premi-world')).toBeInTheDocument();
    });

    // (e) 把 world JSON 改坏
    const jsonArea = screen.getByTestId('revision-json-world') as HTMLTextAreaElement;
    fireEvent.change(jsonArea, { target: { value: '{bad' } });
    expect(screen.getByTestId('revision-submit')).toBeDisabled();

    // 修好后恢复
    fireEvent.change(jsonArea, {
      target: { value: JSON.stringify(worldDraft) },
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
});