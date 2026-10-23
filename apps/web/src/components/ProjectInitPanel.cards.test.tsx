// ProjectInitPanel —— 卡片编辑与回退用例。
//
// 拆分说明：原 `ProjectInitPanel.test.tsx` 单文件 2381 行 / 39 例，超出 AGENTS
// 「新增/改动文件不得超 1500 行」硬规则 ⇒ 拆成三份（提交流 / review 多关推进 /
// 卡片编辑与回退）。本份为**卡片编辑与回退**：角色 / 世界观 / 卷纲 / 章节种子卡片
// 的渲染与编辑，字段类型不符时回退 JSON 域，JSON 域非法时禁用放行，
// protagonist 嵌套展开（含未知键）。
//
// 断言**逐字搬运**，未改任何断言；共用夹具与共用 beforeEach 设定见
// `./__fixtures__/projectInitMocks.ts`。
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ProjectInitPanel } from './ProjectInitPanel';
import type { ProjectInitResponse } from '../api/types';
import {
  baseProject,
  characterDraft,
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
});
