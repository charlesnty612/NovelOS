/**
 * SkillsPanel（C-45③）专属测试。
 *
 * 最关键的判别点：**绑定是覆盖式有序**——任何增删 / 排序都必须提交
 * 「完整有序 skill_ids 数组」。旧式「只加一个」会把其余成员静默解绑。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SkillsPanel } from './SkillsPanel';
import { ApiError } from '../api/client';
import { skillsApi } from '../api/endpoints';
import type { Skill, SkillSummary } from '../api/types';

vi.mock('../api/endpoints', () => ({
  skillsApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    listBound: vi.fn(),
    bind: vi.fn(),
    unbind: vi.fn(),
  },
}));

const summary = (id: string, over: Partial<SkillSummary> = {}): SkillSummary =>
  ({
    skill_id: id,
    name: `技能-${id}`,
    description: `描述-${id}`,
    version: 1,
    created_at: '2026-11-05T10:00:00+00:00',
    updated_at: '2026-11-05T10:00:00+00:00',
    bound_project_count: 0,
    ...over,
  }) as SkillSummary;

const fullSkill = (id: string, over: Partial<Skill> = {}): Skill =>
  ({
    skill_id: id,
    name: `技能-${id}`,
    description: `描述-${id}`,
    version: 1,
    payload: { instructions: 'do things' },
    created_at: '2026-11-05T10:00:00+00:00',
    updated_at: '2026-11-05T10:00:00+00:00',
    bound_project_count: 1,
    ...over,
  }) as Skill;

const ALL = [summary('skl_a'), summary('skl_b'), summary('skl_c')];
const BOUND = [fullSkill('skl_b'), fullSkill('skl_a')];

describe('SkillsPanel（C-45③）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(skillsApi.list).mockResolvedValue(ALL);
    vi.mocked(skillsApi.listBound).mockResolvedValue(BOUND);
  });

  it('渲染全部技能与本项目有序绑定（ord 按后端顺序）', async () => {
    render(<SkillsPanel projectId="prj_1" />);
    expect(await screen.findByTestId('skills-all-list')).toBeInTheDocument();
    expect(screen.getByTestId('skill-all-skl_c')).toBeInTheDocument();
    expect(screen.getByTestId('skill-bound-ord-skl_b')).toHaveTextContent('1. 技能-skl_b');
    expect(screen.getByTestId('skill-bound-ord-skl_a')).toHaveTextContent('2. 技能-skl_a');
  });

  it('绑定未绑定的技能 ⇒ bind 提交**完整有序**数组（不是只发新增那个）', async () => {
    vi.mocked(skillsApi.bind).mockResolvedValue([
      ...BOUND,
      fullSkill('skl_c'),
    ]);
    render(<SkillsPanel projectId="prj_1" />);
    fireEvent.click(await screen.findByTestId('skill-bind-skl_c'));

    await waitFor(() => expect(skillsApi.bind).toHaveBeenCalledTimes(1));
    expect(vi.mocked(skillsApi.bind).mock.calls[0]).toEqual([
      'prj_1',
      { skill_ids: ['skl_b', 'skl_a', 'skl_c'] },
    ]);
  });

  it('下移 ⇒ bind 提交重排后的完整数组（不是 swap 单条）', async () => {
    vi.mocked(skillsApi.bind).mockResolvedValue([
      fullSkill('skl_a'),
      fullSkill('skl_b'),
    ]);
    render(<SkillsPanel projectId="prj_1" />);
    fireEvent.click(await screen.findByTestId('skill-down-skl_b'));

    await waitFor(() => expect(skillsApi.bind).toHaveBeenCalledTimes(1));
    expect(vi.mocked(skillsApi.bind).mock.calls[0][1]).toEqual({
      skill_ids: ['skl_a', 'skl_b'],
    });
  });

  it('删除被绑定的技能遇 409 ⇒ 明确提示先解绑，不静默', async () => {
    vi.mocked(skillsApi.remove).mockRejectedValue(
      new ApiError(409, "skill 'skl_b' is bound to 1 project(s); unbind before deleting"),
    );
    render(<SkillsPanel projectId="prj_1" />);
    fireEvent.click(await screen.findByTestId('skill-delete-skl_b'));
    fireEvent.click(await screen.findByTestId('skill-delete-confirm-confirm'));

    await waitFor(() =>
      expect(screen.getByText(/仍被项目绑定，不能删除/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/请先在本项目「已绑定」里解绑/)).toBeInTheDocument();
  });

  it('新建技能：payload 文本域校验 JSON 对象并创建', async () => {
    const user = userEvent.setup();
    vi.mocked(skillsApi.create).mockResolvedValue(fullSkill('skl_new'));
    render(<SkillsPanel projectId="prj_1" />);
    await user.click(await screen.findByTestId('skill-create-btn'));
    await user.type(screen.getByTestId('skill-form-name'), '新技能');
    const payload = screen.getByTestId('skill-form-payload') as HTMLTextAreaElement;
    fireEvent.change(payload, { target: { value: '{"instructions":"x"}' } });
    await user.click(screen.getByTestId('skill-form-save'));

    await waitFor(() => expect(skillsApi.create).toHaveBeenCalledTimes(1));
    expect(vi.mocked(skillsApi.create).mock.calls[0][0]).toMatchObject({
      name: '新技能',
      payload: { instructions: 'x' },
    });
  });

  it('新建技能：非法 JSON ⇒ 就地报错且不创建', async () => {
    const user = userEvent.setup();
    render(<SkillsPanel projectId="prj_1" />);
    await user.click(await screen.findByTestId('skill-create-btn'));
    await user.type(screen.getByTestId('skill-form-name'), '坏技能');
    const payload = screen.getByTestId('skill-form-payload') as HTMLTextAreaElement;
    fireEvent.change(payload, { target: { value: '[1,2]' } });
    await user.click(screen.getByTestId('skill-form-save'));

    await waitFor(() =>
      expect(screen.getByText(/必须是 JSON 对象/)).toBeInTheDocument(),
    );
    expect(skillsApi.create).not.toHaveBeenCalled();
  });
});
