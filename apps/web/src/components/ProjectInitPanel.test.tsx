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
      initStatus: vi.fn(),
    },
    charactersApi: {
      ...((actual.charactersApi as unknown) as object),
      listByProject: vi.fn(),
    },
    workflowsApi: {
      ...((actual.workflowsApi as unknown) as object),
      resumeInit: vi.fn(),
      resume: vi.fn(),
      listByProject: vi.fn(),
      get: vi.fn(),
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
    {
      name: '林动',
      role: 'protagonist',
      core_json: {
        motivation: '复仇',
        goal: '登顶',
        conflict: '资源匮乏',
        distinctive_trait: '沉稳',
        relationships: [],
      },
    },
  ],
};

// 引用以避免 TS6133（characterDraft 留作下一关 stage 复用，保留定义便于后续扩展）。
void characterDraft;

describe('ProjectInitPanel (P1 project-init)', () => {
  beforeEach(() => {
    vi.mocked(projectsApi.init).mockReset();
    vi.mocked(projectsApi.initStatus).mockReset();
    vi.mocked(charactersApi.listByProject).mockReset();
    vi.mocked(workflowsApi.resumeInit).mockReset();
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(capabilityBindingsApi.list).mockReset();
    vi.mocked(capabilityBindingsApi.bind).mockReset();
    vi.mocked(capabilityBindingsApi.unbind).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    // 默认探测：空角色
    vi.mocked(charactersApi.listByProject).mockResolvedValue([]);
    // 默认探测：无挂起 run（具体用例按需覆盖）
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([]);
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
    // 默认 init-status：失败（默认 fallback 行为：全部默认勾选且不强制禁用），
    // 具体 init-status 相关用例按需覆盖。
    vi.mocked(projectsApi.initStatus).mockRejectedValue(new Error('init-status not mocked'));
    // 清理 sessionStorage（避免跨用例残留）
    sessionStorage.clear();
    // 单测中把 poll 间隔压到 1ms，避免 5s 默认超时下被 2000ms 间隔卡住。
    (window as unknown as { __novelosPollIntervalMs?: number }).__novelosPollIntervalMs = 1;
    (window as unknown as { __novelosPollTimeoutMs?: number }).__novelosPollTimeoutMs = 5000;
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

  it('项目已有 premise 时回填一句话简介', async () => {
    const projectWithPremise: Project = {
      ...baseProject,
      premise: '定位：无敌爽文 / 卖点：节奏快、升级爽',
    };
    render(
      <ProjectInitPanel
        projectId="prj_1"
        project={projectWithPremise}
        onDone={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    const logline = screen.getByTestId('project-init-logline') as HTMLTextAreaElement;
    expect(logline.value).toBe('定位：无敌爽文 / 卖点：节奏快、升级爽');
    // 有值即可提交（复用，不用手抄）
    expect(screen.getByTestId('project-init-submit')).not.toBeDisabled();
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

  it('勾选重新生成已有设定的环节时必须勾选确认才能提交', async () => {
    // init-status：题材定位 done（已有设定）、outline 未 done
    vi.mocked(projectsApi.initStatus).mockResolvedValue({
      stages: [
        { stage: 'premise', label: '题材定位', done: true, detail: '已有 premise 文本' },
        { stage: 'world', label: '世界观', done: true, detail: '已有设定' },
        { stage: 'character', label: '核心角色', done: true, detail: '已有角色' },
        { stage: 'outline', label: '卷纲与章节种子', done: false, detail: '未生成' },
      ],
      has_any_data: true,
    } as never);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });
    // outline 强制勾选；默认仅勾 outline（题材/世界观/角色默认不勾）
    expect(screen.getByTestId('init-stage-outline')).toBeChecked();
    expect(screen.getByTestId('init-stage-premise')).not.toBeChecked();
    // 无警告（只补未完成环节）
    expect(screen.queryByTestId('project-init-warning')).toBeNull();

    // 手动勾上「题材定位」（重新生成已有设定）→ 警告出现
    fireEvent.click(screen.getByTestId('init-stage-premise'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-warning')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    // 未勾选确认 → 提交仍禁用
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
      target: { value: '600' },
    });

    // 超过上限（500）→ canSubmit 为 false → 提交按钮 disabled
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

  it('角色卡片渲染 core_json 内字段：值可见、修改后放行透传到 core_json 且保留其他键', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_char_1',
      status: 'PAUSED',
      current_node: 'character_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'character',
        stage_index: 2,
        stages_total: 4,
        degraded: false,
        draft: {
          characters: [
            {
              name: '林动',
              role: 'protagonist',
              core_json: {
                motivation: '复仇',
                goal: '登顶',
                conflict: '资源匮乏',
                distinctive_trait: '沉稳',
                relationships: [],
              },
              extra_top_key: '保留我',
            },
          ],
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

    // 进入角色关卡审阅，卡片 #1 渲染
    await waitFor(() => {
      expect(screen.getByTestId('revision-card-character-characters-0')).toBeInTheDocument();
    });

    // core_json 内字段可见且有值（此前的 bug：从顶层读空）
    const motivation = screen.getByTestId(
      'revision-character-characters-0-core_json-motivation',
    ) as HTMLTextAreaElement;
    expect(motivation.value).toBe('复仇');
    expect(screen.getByTestId('revision-character-characters-0-core_json-goal').textContent).toContain('登顶');

    // 修改动机后放行 → resume 收到的 revisions 里 core_json.motivation 已更新，且 extra_top_key 保留
    vi.mocked(workflowsApi.resumeInit).mockResolvedValueOnce({
      run_id: 'run_char_1',
      status: 'COMPLETED',
      current_node: 'persist_all',
      project_id: 'prj_1',
    } as ProjectInitResponse);
    fireEvent.change(motivation, { target: { value: '为父复仇' } });
    fireEvent.click(screen.getByTestId('revision-submit'));

    await waitFor(() => {
      expect(workflowsApi.resumeInit).toHaveBeenCalledTimes(1);
    });
    const [, resumeBody] = vi.mocked(workflowsApi.resumeInit).mock.calls[0];
    const revisions = (resumeBody.human_input as { revisions: Record<string, unknown> }).revisions;
    const chars = (revisions.character_output as { characters: Array<Record<string, unknown>> }).characters;
    const c0 = chars[0];
    expect((c0.core_json as Record<string, unknown>).motivation).toBe('为父复仇');
    expect((c0.core_json as Record<string, unknown>).goal).toBe('登顶');
    expect(c0.extra_top_key).toBe('保留我');
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

  // ---------------- 新增：结构化编辑器 4 项验证 --------------------------

  it('world 关渲染为卡片：rules 至少 1 卡、name/statement 控件存在', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_world_card',
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

    // (a) core_premise 是独立 textarea（不再是 JSON 域）
    const corePremise = screen.getByTestId('revision-world-core-premise') as HTMLTextAreaElement;
    expect(corePremise).toBeInTheDocument();
    expect(corePremise.value).toBe('武道为尊，强者通神');

    // (b) rules 渲染为卡片：testid = revision-card-world-rules-0
    expect(screen.getByTestId('revision-card-world-rules-0')).toBeInTheDocument();
    // 卡片内 name / statement 控件
    expect(screen.getByTestId('revision-world-rules-0-name')).toBeInTheDocument();
    const stmt = screen.getByTestId('revision-world-rules-0-statement') as HTMLTextAreaElement;
    expect(stmt).toBeInTheDocument();
    expect(stmt.value).toBe('灵气可修炼');
  });

  it('修改 outline 某章 one_sentence 后放行 → revisions.outline_output.chapter_seeds 已更新且未知键保留', async () => {
    // mock draft 故意埋一个未知键 _draft_meta，验证透传
    const outlineDraftWithExtra = {
      volume: { number: 1, title: '初入宗门', arc_summary: '少年成长' },
      chapter_seeds: [
        { number: 1, title: '觉醒', role: 'intro', one_sentence: '原始值', expected_word_count: 3000, key_beats: [] },
      ],
      _draft_meta: { source: 'unit-test', version: 7 },
    };

    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_outline_edit',
      status: 'PAUSED',
      current_node: 'volume_outliner',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'outline',
        stage_index: 3,
        stages_total: 4,
        degraded: false,
        draft: outlineDraftWithExtra,
      },
    } as ProjectInitResponse);
    vi.mocked(workflowsApi.resumeInit).mockResolvedValueOnce({
      run_id: 'run_outline_edit',
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
      expect(screen.getByTestId('revision-premi-outline')).toBeInTheDocument();
    });

    // 修改第 1 章 one_sentence
    const oneSentence = screen.getByTestId(
      'revision-outline-chapter_seeds-0-one_sentence',
    ) as HTMLTextAreaElement;
    expect(oneSentence.value).toBe('原始值');
    fireEvent.change(oneSentence, { target: { value: '修改后的一句话' } });

    // 放行
    fireEvent.click(screen.getByTestId('revision-submit'));

    await waitFor(() => {
      expect(workflowsApi.resumeInit).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(workflowsApi.resumeInit).mock.calls[0]!;
    const outlineOutput = payload.human_input.revisions!.outline_output as Record<string, unknown>;
    // chapter_seeds[0].one_sentence 已更新
    const seeds = outlineOutput.chapter_seeds as Array<Record<string, unknown>>;
    expect(seeds[0]!.one_sentence).toBe('修改后的一句话');
    // volume.title 原样保留
    const volume = outlineOutput.volume as Record<string, unknown>;
    expect(volume.title).toBe('初入宗门');
    // 未知键 _draft_meta 整段透传
    expect(outlineOutput._draft_meta).toEqual({ source: 'unit-test', version: 7 });
  });

  it('characters 卡片「+ 添加一条」产生新空卡且放行后数组长度+1', async () => {
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_char_add',
      status: 'PAUSED',
      current_node: 'character_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'character',
        stage_index: 2,
        stages_total: 4,
        degraded: false,
        draft: characterDraft,
      },
    } as ProjectInitResponse);
    vi.mocked(workflowsApi.resumeInit).mockResolvedValueOnce({
      run_id: 'run_char_add',
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
      expect(screen.getByTestId('revision-premi-character')).toBeInTheDocument();
    });

    // 初始 1 张卡
    expect(screen.getByTestId('revision-card-character-characters-0')).toBeInTheDocument();
    expect(screen.queryByTestId('revision-card-character-characters-1')).toBeNull();

    // 点 + 添加一条
    fireEvent.click(screen.getByTestId('revision-card-add-character-characters'));

    await waitFor(() => {
      expect(screen.getByTestId('revision-card-character-characters-1')).toBeInTheDocument();
    });

    // 新卡字段控件存在且值为空
    const newName = screen.getByTestId('revision-character-characters-1-name') as HTMLInputElement;
    expect(newName.value).toBe('');
    const newRole = screen.getByTestId('revision-character-characters-1-role') as HTMLInputElement;
    expect(newRole.value).toBe('');

    // 给新卡填一个姓名后放行
    fireEvent.change(newName, { target: { value: '萧炎' } });
    fireEvent.click(screen.getByTestId('revision-submit'));

    await waitFor(() => {
      expect(workflowsApi.resumeInit).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(workflowsApi.resumeInit).mock.calls[0]!;
    const charOutput = payload.human_input.revisions!.character_output as Record<string, unknown>;
    const chars = charOutput.characters as Array<Record<string, unknown>>;
    expect(chars.length).toBe(2);
    // 第 2 个是新增的「萧炎」
    expect(chars[1]!.name).toBe('萧炎');
    // 第 1 个原值保留
    expect(chars[0]!.name).toBe('林动');
    // core_json 内字段（动机/目标/冲突/辨识特征/relationships）透传保留
    const c0Core = chars[0]!.core_json as Record<string, unknown>;
    expect(c0Core.motivation).toBe('复仇');
    expect(c0Core.goal).toBe('登顶');
    expect(c0Core.conflict).toBe('资源匮乏');
    expect(c0Core.distinctive_trait).toBe('沉稳');
    expect(c0Core.relationships).toEqual([]);
  });

  it('字段类型不符时回退 JSON 域可编辑提交', async () => {
    // world.rules 类型不符（不是数组），会触发 rules 字段 JSON 兜底
    const badDraft = {
      core_premise: '武道为尊',
      rules: 'not-an-array',
    };
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_fallback',
      status: 'PAUSED',
      current_node: 'world_builder',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'world',
        stage_index: 1,
        stages_total: 4,
        degraded: false,
        draft: badDraft,
      },
    } as ProjectInitResponse);
    vi.mocked(workflowsApi.resumeInit).mockResolvedValueOnce({
      run_id: 'run_fallback',
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
      expect(screen.getByTestId('revision-premi-world')).toBeInTheDocument();
    });

    // rules 字段类型不符 → JSON 兜底域渲染（testid = revision-json-fallback-world-rules）
    const fallback = screen.getByTestId(
      'revision-json-fallback-world-rules',
    ) as HTMLTextAreaElement;
    expect(fallback).toBeInTheDocument();
    expect(fallback.value).toBe(JSON.stringify('not-an-array', null, 2));

    // 把兜底域改成合法数组对象并放行
    fireEvent.change(fallback, {
      target: { value: JSON.stringify([{ name: '修复后规则', statement: 's' }]) },
    });
    expect(screen.getByTestId('revision-submit')).not.toBeDisabled();
    fireEvent.click(screen.getByTestId('revision-submit'));

    await waitFor(() => {
      expect(workflowsApi.resumeInit).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(workflowsApi.resumeInit).mock.calls[0]!;
    const worldOutput = payload.human_input.revisions!.world_output as Record<string, unknown>;
    expect(worldOutput.core_premise).toBe('武道为尊');
    expect(worldOutput.rules).toEqual([{ name: '修复后规则', statement: 's' }]);
  });

  // ---------------- P2 必修：JSON 域非法注册表（world.rules 改坏禁用放行） ----------

  it('JSON 域非法禁用放行：world.rules 初始合法数组 → 改成 "{bad" → 放行 disabled；改回合法数组 → 恢复', async () => {
    // mock draft 让 rules 是合法数组（不触发 composeAndValidate 类型错），
    // 但额外塞一个未知键（array of object 类型的未知键）便于测试 JSON 域非法注册表。
    // 这里用一个 unknown-key 域作为目标：让 _extra_meta 是 object 类型 → 走未知键 JSON 域
    // → 改坏 → 注册表上报非法 → 禁用放行。
    const worldDraftWithExtra = {
      core_premise: '武道为尊',
      rules: [{ id: 'r1', statement: '灵气可修炼' }],
      locations: [],
      factions: [],
      _extra_meta: { source: 'unit-test', nested: { x: 1 } }, // object → 未知键 JSON 域
    };
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_json_invalid',
      status: 'PAUSED',
      current_node: 'world_builder',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'world',
        stage_index: 1,
        stages_total: 4,
        degraded: false,
        draft: worldDraftWithExtra,
      },
    } as ProjectInitResponse);
    vi.mocked(workflowsApi.resumeInit).mockResolvedValue({
      run_id: 'run_json_invalid',
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
      expect(screen.getByTestId('revision-premi-world')).toBeInTheDocument();
    });

    // (a) 未知键 _extra_meta（object）渲染为 JSON 域（testid = revision-unknown-world-_extra_meta）
    const jsonArea = screen.getByTestId(
      'revision-unknown-world-_extra_meta',
    ) as HTMLTextAreaElement;
    expect(jsonArea).toBeInTheDocument();
    // 此时初始合法 → 放行启用
    expect(screen.getByTestId('revision-submit')).not.toBeDisabled();
    expect(workflowsApi.resumeInit).not.toHaveBeenCalled();

    // (b) 把 JSON 域改坏为 "{bad" → 放行 disabled + 红字提示
    fireEvent.change(jsonArea, { target: { value: '{bad' } });
    expect(screen.getByTestId('revision-submit')).toBeDisabled();
    // 红字错误提示
    expect(jsonArea.parentElement?.textContent ?? '').toContain(
      'JSON 格式错误，修正后才能放行',
    );
    // 关键：改坏时 JSON 域不会静默提交旧值（draft._extra_meta 仍是合法原值）
    expect(workflowsApi.resumeInit).not.toHaveBeenCalled();

    // (c) 改回合法 → 放行恢复
    fireEvent.change(jsonArea, {
      target: { value: JSON.stringify({ source: 'unit-test', nested: { x: 1 } }) },
    });
    expect(screen.getByTestId('revision-submit')).not.toBeDisabled();

    // (d) 真放行 → resumeInit 被调用，且 _extra_meta 新值被提交
    fireEvent.click(screen.getByTestId('revision-submit'));
    await waitFor(() => {
      expect(workflowsApi.resumeInit).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(workflowsApi.resumeInit).mock.calls[0]!;
    const worldOutput = payload.human_input.revisions!.world_output as Record<string, unknown>;
    // _extra_meta 必须是合法对象（不是 "{bad"），验证 onJsonValidityChange 阻断提交
    expect(worldOutput._extra_meta).toEqual({ source: 'unit-test', nested: { x: 1 } });
  });

  // ---------------- P3.1 必修：protagonist 嵌套展开含 AI 开放键 -----------------

  it('protagonist 嵌套展开含未知键（core_desire）可见可改；放行后透传', async () => {
    const premiseWithExtra = {
      title: '九天神诀',
      genre: '玄幻',
      logline: '少年得古籍，逆天改命',
      positioning: '无敌爽文',
      selling_points: ['节奏快', '升级爽'],
      // AI 实际可能产出的开放键：core_desire / core_conflict / distinctive_trait
      protagonist: {
        name: '林动',
        age: 16,
        core_desire: '复仇',
        core_conflict: '资源匮乏',
        distinctive_trait: '左手藏锋',
      },
    };
    vi.mocked(projectsApi.init).mockResolvedValueOnce({
      run_id: 'run_protagonist_extra',
      status: 'PAUSED',
      current_node: 'premise_designer',
      project_id: 'prj_1',
      pause_payload: {
        stage: 'premise',
        stage_index: 0,
        stages_total: 4,
        degraded: false,
        draft: premiseWithExtra,
      },
    } as ProjectInitResponse);
    vi.mocked(workflowsApi.resumeInit).mockResolvedValue({
      run_id: 'run_protagonist_extra',
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

    // (a) 容器 testid 仍是 revision-protagonist
    expect(screen.getByTestId('revision-protagonist')).toBeInTheDocument();

    // (b) 白名单键可见（不带 stage 前缀）
    expect(screen.getByTestId('revision-protagonist-name')).toBeInTheDocument();
    const nameField = screen.getByTestId(
      'revision-protagonist-name',
    ) as HTMLInputElement;
    expect(nameField.value).toBe('林动');

    // (c) 未知键（AI 开放键）作为未知键字段渲染，testid = revision-protagonist-unknown-{key}
    const coreDesire = screen.getByTestId(
      'revision-protagonist-unknown-core_desire',
    ) as HTMLTextAreaElement;
    expect(coreDesire).toBeInTheDocument();
    expect(coreDesire.value).toBe('复仇');

    // (d) 修改 core_desire 后放行
    fireEvent.change(coreDesire, { target: { value: '守护妹妹' } });
    fireEvent.click(screen.getByTestId('revision-submit'));

    await waitFor(() => {
      expect(workflowsApi.resumeInit).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(workflowsApi.resumeInit).mock.calls[0]!;
    const premiseOutput = payload.human_input.revisions!
      .premise_output as Record<string, unknown>;
    const protagonist = premiseOutput.protagonist as Record<string, unknown>;
    // 修改值生效
    expect(protagonist.core_desire).toBe('守护妹妹');
    // 其他开放键原样透传
    expect(protagonist.core_conflict).toBe('资源匮乏');
    expect(protagonist.distinctive_trait).toBe('左手藏锋');
    // 白名单键也透传
    expect(protagonist.name).toBe('林动');
    expect(protagonist.age).toBe(16);
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

  // ---------------- P1.3 必修：环节状态 + 环节多选 --------------------------

  // helper：构造一份 init-status 响应（含全部 4 关卡）
  const buildInitStatusResponse = (
    overrides: Array<Partial<{ stage: string; label: string; done: boolean; detail: string | null }>>,
  ) => {
    const labels: Record<string, string> = {
      premise: '题材定位',
      world: '世界观',
      character: '核心角色',
      outline: '卷纲与章节种子',
    };
    const stages = ['premise', 'world', 'character', 'outline'].map((stage) => {
      const o = overrides.find((x) => x.stage === stage) ?? {};
      return {
        stage,
        label: o.label ?? labels[stage]!,
        done: o.done ?? false,
        detail: o.detail ?? null,
      };
    });
    return {
      stages,
      has_any_data: stages.some((s) => s.done),
    };
  };

  it('a) init-status 返回 outline 未 done、其余 done → outline 默认勾选可手动取消，premise/world/character 默认不勾选', async () => {
    vi.mocked(projectsApi.initStatus).mockResolvedValue(
      buildInitStatusResponse([
        { stage: 'premise', done: true },
        { stage: 'world', done: true },
        { stage: 'character', done: true },
        { stage: 'outline', done: false, detail: '卷纲未生成' },
      ]),
    );
    vi.mocked(projectsApi.init).mockResolvedValue({
      run_id: 'run_stages_a',
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

    // 等 init-status 解析完成
    await waitFor(() => {
      expect(screen.getByTestId('init-stage-outline')).toBeInTheDocument();
    });

    // (1) done 的环节默认不勾选
    const premiseCb = screen.getByTestId('init-stage-premise') as HTMLInputElement;
    const worldCb = screen.getByTestId('init-stage-world') as HTMLInputElement;
    const characterCb = screen.getByTestId('init-stage-character') as HTMLInputElement;
    const outlineCb = screen.getByTestId('init-stage-outline') as HTMLInputElement;

    expect(premiseCb.checked).toBe(false);
    expect(worldCb.checked).toBe(false);
    expect(characterCb.checked).toBe(false);
    // (2) outline 未 done → 默认勾选；不再强制 disabled（用户可按需取消）
    expect(outlineCb.checked).toBe(true);
    expect(outlineCb.disabled).toBe(false);

    // (3) 徽标：done → 灰绿，未 done → 灰红
    expect(screen.getByTestId('init-stage-premise-badge')).toHaveTextContent(/已有设定/);
    expect(screen.getByTestId('init-stage-outline-badge')).toHaveTextContent(/未完成/);

    // (4) 提交后 selected_stages = ['outline']
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));
    await waitFor(() => {
      expect(projectsApi.init).toHaveBeenCalledTimes(1);
    });
    const [payload] = vi.mocked(projectsApi.init).mock.calls[0]!;
    expect(payload.selected_stages).toEqual(['outline']);

    // (5) 用户可取消 outline（不强制）：勾选态变为 false
    fireEvent.click(outlineCb);
    expect(outlineCb.checked).toBe(false);
  });

  it('b) 手动勾上 premise 后提交 → selected_stages 含 premise（且含 outline）', async () => {
    vi.mocked(projectsApi.initStatus).mockResolvedValue(
      buildInitStatusResponse([
        { stage: 'premise', done: true },
        { stage: 'world', done: true },
        { stage: 'character', done: true },
        { stage: 'outline', done: false },
      ]),
    );
    vi.mocked(projectsApi.init).mockResolvedValue({
      run_id: 'run_stages_b',
      status: 'COMPLETED',
      current_node: 'persist_all',
      project_id: 'prj_1',
    } as ProjectInitResponse);

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('init-stage-premise')).toBeInTheDocument();
    });

    // 手动勾上 premise
    const premiseCb = screen.getByTestId('init-stage-premise') as HTMLInputElement;
    expect(premiseCb.checked).toBe(false);
    fireEvent.click(premiseCb);
    expect(premiseCb.checked).toBe(true);

    // 提交（勾选已有设定的环节 → 需勾选确认）
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    await waitFor(() => {
      expect(screen.getByTestId('project-init-warning')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('project-init-overwrite-checkbox'));
    fireEvent.click(screen.getByTestId('project-init-submit'));
    await waitFor(() => {
      expect(projectsApi.init).toHaveBeenCalledTimes(1);
    });
    const [payload] = vi.mocked(projectsApi.init).mock.calls[0]!;
    expect(payload.selected_stages).toEqual(
      expect.arrayContaining(['premise', 'outline']),
    );
    expect(payload.selected_stages).toHaveLength(2);
  });

  it('c) 未 done 环节默认勾选且可自由取消', async () => {
    vi.mocked(projectsApi.initStatus).mockResolvedValue(
      buildInitStatusResponse([
        { stage: 'premise', done: false },
        { stage: 'world', done: true },
        { stage: 'character', done: true },
        { stage: 'outline', done: false },
      ]),
    );

    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('init-stage-premise')).toBeInTheDocument();
    });

    // premise & outline 默认勾选、不再强制 disabled
    const premiseCb = screen.getByTestId('init-stage-premise') as HTMLInputElement;
    const outlineCb = screen.getByTestId('init-stage-outline') as HTMLInputElement;
    expect(premiseCb.checked).toBe(true);
    expect(premiseCb.disabled).toBe(false);
    expect(outlineCb.checked).toBe(true);
    expect(outlineCb.disabled).toBe(false);

    // 用户可点击取消未 done 环节（state 跟随翻转）
    fireEvent.click(premiseCb);
    expect(premiseCb.checked).toBe(false);
    fireEvent.click(outlineCb);
    expect(outlineCb.checked).toBe(false);

    // world 已 done → 默认不勾选且可手动切换
    const worldCb = screen.getByTestId('init-stage-world') as HTMLInputElement;
    expect(worldCb.checked).toBe(false);
    fireEvent.click(worldCb);
    expect(worldCb.checked).toBe(true);
    fireEvent.click(worldCb);
    expect(worldCb.checked).toBe(false);
  });

  it('d) init-status 请求失败 → 全部默认勾选、表单可用', async () => {
    // beforeEach 已设默认 mockRejectedValue；显式再次设置以确保意图清晰
    vi.mocked(projectsApi.initStatus).mockRejectedValue(new Error('mock failure'));
    vi.mocked(projectsApi.init).mockResolvedValue({
      run_id: 'run_stages_d',
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

    // 等 init-status 解析完成（失败兜底）
    await waitFor(() => {
      expect(screen.getByTestId('init-stage-premise')).toBeInTheDocument();
    });

    // 4 个 stage 全部默认勾选（不强制 disabled——失败态下不做强制）
    const stages = ['premise', 'world', 'character', 'outline'];
    for (const s of stages) {
      const cb = screen.getByTestId(`init-stage-${s}`) as HTMLInputElement;
      expect(cb.checked).toBe(true);
    }

    // 表单可用：填 logline 后能提交
    fireEvent.change(screen.getByTestId('project-init-logline'), {
      target: { value: '少年得古籍，逆天改命' },
    });
    expect(screen.getByTestId('project-init-submit')).not.toBeDisabled();
    fireEvent.click(screen.getByTestId('project-init-submit'));
    await waitFor(() => {
      expect(projectsApi.init).toHaveBeenCalledTimes(1);
    });
    const [payload] = vi.mocked(projectsApi.init).mock.calls[0]!;
    expect(payload.selected_stages).toEqual(
      expect.arrayContaining(['premise', 'world', 'character', 'outline']),
    );
    expect(payload.selected_stages).toHaveLength(4);
  });

  // ---------------- P1 project-init：单章字数（chapter_word_count）联动 ----

  it('a) 填目标字数 1000000 + 单章字数默认 3000 → 章节种子数自动重算为 333', async () => {
    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });

    // 初始：单章字数默认 3000，章节种子数默认 10
    const chapterWordCount = screen.getByTestId(
      'project-init-chapter-word-count',
    ) as HTMLInputElement;
    expect(chapterWordCount.value).toBe('3000');
    const chapterSeedCount = screen.getByTestId(
      'project-init-chapter-seed-count',
    ) as HTMLInputElement;
    expect(chapterSeedCount.value).toBe('10');

    // 改目标字数为 1000000 → 自动重算章节种子数（round(1000000/3000)=333，clamp 到 [10,500]）
    fireEvent.change(screen.getByTestId('project-init-target-words'), {
      target: { value: '1000000' },
    });
    expect(chapterSeedCount.value).toBe('333');
  });

  it('b) 手动改章节种子数为 15 → 再改单章字数 → 章节种子数保持 15（不被重算）', async () => {
    render(
      <ProjectInitPanel projectId="prj_1" project={baseProject} onDone={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId('project-init-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('project-init-form')).toBeInTheDocument();
    });

    const chapterSeedCount = screen.getByTestId(
      'project-init-chapter-seed-count',
    ) as HTMLInputElement;
    // 手动改 → 标记 manual=true
    fireEvent.change(chapterSeedCount, { target: { value: '15' } });
    expect(chapterSeedCount.value).toBe('15');

    // 改单章字数（默认 3000 → 2500）→ 章节种子数应保持 15
    const chapterWordCount = screen.getByTestId(
      'project-init-chapter-word-count',
    ) as HTMLInputElement;
    fireEvent.change(chapterWordCount, { target: { value: '2500' } });
    expect(chapterSeedCount.value).toBe('15');

    // 改目标字数（baseProject=300000 → 500000）也不应重算
    fireEvent.change(screen.getByTestId('project-init-target-words'), {
      target: { value: '500000' },
    });
    expect(chapterSeedCount.value).toBe('15');
  });

  it('c) 提交时 payload.brief 含 chapter_word_count（默认 3000）', async () => {
    vi.mocked(projectsApi.init).mockResolvedValue({
      run_id: 'run_cwc_1',
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
    fireEvent.change(screen.getByTestId('project-init-target-words'), {
      target: { value: '300000' },
    });
    // 显式设单章字数（覆盖默认 3000 为 3500，验证字段透传）
    fireEvent.change(screen.getByTestId('project-init-chapter-word-count'), {
      target: { value: '3500' },
    });
    fireEvent.click(screen.getByTestId('project-init-submit'));

    await waitFor(() => {
      expect(projectsApi.init).toHaveBeenCalledTimes(1);
    });
    const [payload] = vi.mocked(projectsApi.init).mock.calls[0]!;
    expect(payload.brief.chapter_word_count).toBe(3500);
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