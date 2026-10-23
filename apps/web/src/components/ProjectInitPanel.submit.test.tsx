// ProjectInitPanel —— 提交流用例。
//
// 拆分说明：原 `ProjectInitPanel.test.tsx` 单文件 2381 行 / 39 例，超出 AGENTS
// 「新增/改动文件不得超 1500 行」硬规则 ⇒ 拆成三份（提交流 / review 多关推进 /
// 卡片编辑与回退）。本份为**提交流**：表单填写 → `projectsApi.init` 提交 →
// 环节多选（init-status）→ 目标字数 / 单章字数 / 章节种子数联动。
//
// 断言**逐字搬运**，未改任何断言；共用夹具与共用 beforeEach 设定见
// `./__fixtures__/projectInitMocks.ts`。
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ProjectInitPanel } from './ProjectInitPanel';
import type { Project, ProjectInitResponse } from '../api/types';
import {
  baseProject,
  premiseDraft,
  setupProjectInitMocks,
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
});
