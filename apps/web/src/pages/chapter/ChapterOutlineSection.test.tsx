/**
 * ChapterOutlineSection（C-41）专属测试。
 *
 * 判别点：本组件**只**读写 `outline_json`（策展面），任何时候都不得带
 * `plan_json` 键——AGENTS「一列不得两用」；两者混用会让 planner 的生成面
 * 与作者手写的大纲互相覆盖。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChapterOutlineSection } from './ChapterOutlineSection';
import { chaptersApi } from '../../api/endpoints';
import type { Chapter } from '../../api/types';

vi.mock('../../api/endpoints', () => ({
  chaptersApi: {
    update: vi.fn(),
  },
}));

const baseChapter = (over: Partial<Chapter> = {}): Chapter =>
  ({
    chapter_id: 'ch_outline_001',
    project_id: 'prj_001',
    number: 3,
    title: '第三章',
    status: 'PLANNED',
    visibility: 'public',
    who_knows: null,
    plan_json: { chapter_goal: 'planner 生成的目标' },
    outline_json: { beats: ['开场冲突', '转折', '钩子'] },
    created_at: '2026-11-05T10:00:00+00:00',
    updated_at: '2026-11-05T10:00:00+00:00',
    ...over,
  }) as unknown as Chapter;

describe('ChapterOutlineSection（C-41）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(chaptersApi.update).mockResolvedValue(baseChapter());
  });

  it('只读态渲染 outline_json（不是 plan_json）', () => {
    render(<ChapterOutlineSection chapter={baseChapter()} onUpdated={vi.fn()} />);
    expect(screen.getByTestId('outline-panel')).toBeInTheDocument();
    const prose = screen.getByTestId('outline-prose');
    expect(prose).toHaveTextContent('开场冲突');
    expect(prose).not.toHaveTextContent('planner 生成的目标');
  });

  it('保存 ⇒ PATCH 只带 outline_json，绝不带 plan_json', async () => {
    const user = userEvent.setup();
    const onUpdated = vi.fn();
    render(
      <ChapterOutlineSection chapter={baseChapter()} onUpdated={onUpdated} />,
    );
    await user.click(screen.getByTestId('outline-edit'));
    const ta = screen.getByTestId('outline-textarea') as HTMLTextAreaElement;
    fireEvent.change(ta, {
      target: { value: '{"beats":["新开场"]}' },
    });
    await user.click(screen.getByTestId('outline-save'));

    await waitFor(() => expect(chaptersApi.update).toHaveBeenCalledTimes(1));
    const [cid, payload] = vi.mocked(chaptersApi.update).mock.calls[0];
    expect(cid).toBe('ch_outline_001');
    expect(payload).toEqual({ outline_json: { beats: ['新开场'] } });
    expect('plan_json' in (payload as object)).toBe(false);
    await waitFor(() => expect(onUpdated).toHaveBeenCalledTimes(1));
  });

  it('非法 JSON ⇒ 就地报错且不发请求', async () => {
    const user = userEvent.setup();
    render(<ChapterOutlineSection chapter={baseChapter()} onUpdated={vi.fn()} />);
    await user.click(screen.getByTestId('outline-edit'));
    const ta = screen.getByTestId('outline-textarea') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: '[1,2]' } });
    await user.click(screen.getByTestId('outline-save'));

    await waitFor(() =>
      expect(screen.getByText(/必须是 JSON 对象/)).toBeInTheDocument(),
    );
    expect(chaptersApi.update).not.toHaveBeenCalled();
  });

  it('清空 ⇒ 显式发 outline_json: null（后端写 NULL，不是 {}）', async () => {
    const user = userEvent.setup();
    render(<ChapterOutlineSection chapter={baseChapter()} onUpdated={vi.fn()} />);
    await user.click(screen.getByTestId('outline-edit'));
    await user.click(screen.getByTestId('outline-clear'));

    await waitFor(() => expect(chaptersApi.update).toHaveBeenCalledTimes(1));
    expect(vi.mocked(chaptersApi.update).mock.calls[0][1]).toEqual({
      outline_json: null,
    });
  });
});
