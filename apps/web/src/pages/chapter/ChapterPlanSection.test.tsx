/**
 * ChapterPlanSection（C-22 前端 UX 兜底）专属测试。
 *
 * 真正的保留合并事实源在后端 `ChapterService.update`；本组件只负责：用户在
 * textarea 里没写某些键时，不把**本地已知的**门禁键一起丢掉。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChapterPlanSection } from './ChapterPlanSection';
import { chaptersApi } from '../../api/endpoints';
import type { Chapter } from '../../api/types';

vi.mock('../../api/endpoints', () => ({
  chaptersApi: { update: vi.fn() },
}));

const chapter = (over: Partial<Chapter> = {}): Chapter =>
  ({
    chapter_id: 'ch_plan_001',
    project_id: 'prj_001',
    number: 1,
    title: '第一章',
    status: 'PLANNED',
    visibility: 'public',
    who_knows: null,
    plan_json: {
      chapter_goal: '旧目标',
      revision_note: '门禁建议',
      gate_blocked: { mode: 'enforce', rule_ids: ['R_A'] },
      suggested_hashes: ['h1'],
    },
    created_at: '2026-11-05T10:00:00+00:00',
    updated_at: '2026-11-05T10:00:00+00:00',
    ...over,
  }) as unknown as Chapter;

describe('ChapterPlanSection（C-22 UX 兜底）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(chaptersApi.update).mockResolvedValue(chapter());
  });

  it('文本里没写门禁键 ⇒ 保存时合回既有值', async () => {
    const user = userEvent.setup();
    render(<ChapterPlanSection chapter={chapter()} onUpdated={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: '编辑' }));
    fireEvent.change(screen.getByTestId('plan-textarea'), {
      target: { value: '{"chapter_goal":"新目标"}' },
    });
    await user.click(screen.getByTestId('plan-save'));

    await waitFor(() => expect(chaptersApi.update).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(chaptersApi.update).mock.calls[0][1]
      .plan_json as Record<string, unknown>;
    expect(payload.chapter_goal).toBe('新目标');
    expect(payload.revision_note).toBe('门禁建议');
    expect(payload.gate_blocked).toEqual({ mode: 'enforce', rule_ids: ['R_A'] });
    expect(payload.suggested_hashes).toEqual(['h1']);
  });

  it('文本里显式写了门禁键 ⇒ 以用户输入为准（不被既有值覆盖）', async () => {
    const user = userEvent.setup();
    render(<ChapterPlanSection chapter={chapter()} onUpdated={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: '编辑' }));
    fireEvent.change(screen.getByTestId('plan-textarea'), {
      target: { value: '{"revision_note":"我改的","chapter_goal":"x"}' },
    });
    await user.click(screen.getByTestId('plan-save'));

    await waitFor(() => expect(chaptersApi.update).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(chaptersApi.update).mock.calls[0][1]
      .plan_json as Record<string, unknown>;
    expect(payload.revision_note).toBe('我改的');
    // 没写的另外两个门禁键仍保留
    expect(payload.gate_blocked).toEqual({ mode: 'enforce', rule_ids: ['R_A'] });
    expect(payload.suggested_hashes).toEqual(['h1']);
  });
});
