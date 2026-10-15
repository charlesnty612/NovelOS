// 「这一章改了什么」面板（G6）。
//
// 判别点：**摘要必须真的从 delta 的数组长度算出来**，而不是渲染出一句
// 「（无变化）」。本仓刚吃过一次同款亏：概览页两个统计项恒为 0，而夹具照抄了
// 错误形状，于是任何测试都抓不到。这里夹具用后端 `state-delta.schema.json`
// 的真实顶层键，且断言**具体数字与文案**。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { storyStateApi } from '../../api/endpoints';
import { ChapterDeltasSection, summarizeDelta } from './ChapterDeltasSection';

vi.mock('../../api/endpoints', async () => {
  const actual = await vi.importActual<typeof import('../../api/endpoints')>(
    '../../api/endpoints',
  );
  return { ...actual, storyStateApi: { listDeltas: vi.fn() } };
});

const listDeltas = storyStateApi.listDeltas as unknown as ReturnType<typeof vi.fn>;

// 字段名逐字取自 docs/state-model/schemas/state-delta.schema.json 的 properties。
const delta = {
  delta_id: 'dl_1',
  chapter_id: 'ch_1',
  workflow_run_id: 'wf_x',
  previous_state_version: 4,
  created_by: 'system:observer',
  created_at: '2026-10-01T08:00:00Z',
  status: 'APPLIED',
  character_changes: [{ character_id: 'chr_1' }, { character_id: 'chr_2' }],
  world_changes: [{ world_rule_id: 'wr_1' }],
  relationship_changes: [],
  new_events: [{ event_id: 'e1' }],
  new_hooks: [{ hook_id: 'h1' }],
  resolved_hooks: [{ hook_id: 'h0' }, { hook_id: 'h9' }],
  debt_changes: [{ debt_id: 'd1' }],
};

describe('summarizeDelta（口径单点）', () => {
  it('按七类数组各自的长度求和', () => {
    expect(summarizeDelta(delta)).toEqual({
      characters: 2,
      world: 1,
      relationships: 0,
      hooksOpened: 1,
      hooksResolved: 2,
      debts: 1,
      events: 1,
      total: 8,
    });
  });

  it('字段缺失按 0 计（不抛错），全空 ⇒ total 0', () => {
    expect(summarizeDelta({ delta_id: 'x' })).toEqual({
      characters: 0,
      world: 0,
      relationships: 0,
      hooksOpened: 0,
      hooksResolved: 0,
      debts: 0,
      events: 0,
      total: 0,
    });
  });

  it('字段存在但不是数组 ⇒ 按 0 计（宁可少报不误报）', () => {
    expect(summarizeDelta({ character_changes: 'oops' } as never).characters).toBe(0);
  });
});

describe('ChapterDeltasSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('渲染摘要文案与数字（判别：能读出「角色 2 · 回收伏笔 2」）', async () => {
    listDeltas.mockResolvedValue([delta]);
    render(<ChapterDeltasSection projectId="p1" chapterId="ch_1" />);
    const sum = await screen.findByTestId('delta-summary');
    expect(sum).toHaveTextContent('角色 2');
    expect(sum).toHaveTextContent('世界观 1');
    expect(sum).toHaveTextContent('回收伏笔 2');
    expect(sum).toHaveTextContent('新埋伏笔 1');
    expect(sum).toHaveTextContent('债务 1');
    expect(sum).toHaveTextContent('新事件 1');
    // 关系为 0 ⇒ 不出现（不列 0 的项）
    expect(sum).not.toHaveTextContent('关系');
  });

  it('带 projectId + chapterId 调后端', async () => {
    listDeltas.mockResolvedValue([]);
    render(<ChapterDeltasSection projectId="p1" chapterId="ch_9" />);
    await waitFor(() => expect(listDeltas).toHaveBeenCalledWith('p1', 'ch_9'));
  });

  it('空列表 ⇒ 明确说「尚未产生状态变更」，而不是空白', async () => {
    listDeltas.mockResolvedValue([]);
    render(<ChapterDeltasSection projectId="p1" chapterId="ch_1" />);
    expect(await screen.findByTestId('chapter-deltas-empty')).toBeInTheDocument();
  });

  it('接口失败 ⇒ 错误可见，不静默成空表', async () => {
    listDeltas.mockRejectedValue(new Error('boom'));
    render(<ChapterDeltasSection projectId="p1" chapterId="ch_1" />);
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });

  it('一条 delta 无任何变化 ⇒ 显示「（无变化）」而不是摘要', async () => {
    listDeltas.mockResolvedValue([{ delta_id: 'dl_x', created_at: '2026-10-01T08:00:00Z' }]);
    render(<ChapterDeltasSection projectId="p1" chapterId="ch_1" />);
    expect(await screen.findByText('（无变化）')).toBeInTheDocument();
  });
});
