/**
 * ChapterRunPanel 专属测试（补口：此前该组件**零专属测试**）。
 *
 * 组件 426 行，管「本章运行列表 + 节点时间线 + PAUSED 审批卡 + 深度二审分栏」。
 * 判别的是**它把 props 变成了什么**（渲染出哪些行、点了哪一行回传哪个 run_id、
 * 什么条件下才渲染审批卡）——审批卡能不能出现，直接依赖编排 hook 在 PAUSED 时
 * 重拉 detail（见 useChapterRunOrchestration.test.ts）。
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChapterRunPanel } from './ChapterRunPanel';
import type { WorkflowRun } from '../../api/types';

function makeRun(over: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    run_id: 'wfr_a',
    workflow_id: 'wf_opaque',
    workflow_name: 'chapter-write',
    chapter_id: 'ch_001',
    status: 'RUNNING',
    current_node: 'writer',
    checkpoint_json: null,
    nodes: [],
    error: null,
    retry_count: 0,
    started_at: '2026-08-24T10:00:00+00:00',
    ended_at: null,
    ...over,
  } as unknown as WorkflowRun;
}

type Props = Parameters<typeof ChapterRunPanel>[0];

function renderPanel(over: Partial<Props> = {}) {
  const props: Props = {
    runs: [],
    selectedRunId: null,
    onSelect: vi.fn(),
    detailRun: null,
    detailLoading: false,
    detailError: null,
    pollError: null,
    pausePayload: undefined,
    submitting: false,
    onApprove: vi.fn(),
    ...over,
  };
  const utils = render(<ChapterRunPanel {...props} />);
  return { props, ...utils };
}

describe('ChapterRunPanel', () => {
  it('无 run 时渲染空态；有 run 时每条一行', () => {
    const { unmount } = renderPanel();
    expect(screen.getByText('本章暂无运行记录')).toBeInTheDocument();
    unmount();

    renderPanel({
      runs: [makeRun({ run_id: 'wfr_a' }), makeRun({ run_id: 'wfr_b' })],
    });
    expect(screen.getByTestId('run-row-wfr_a')).toBeInTheDocument();
    expect(screen.getByTestId('run-row-wfr_b')).toBeInTheDocument();
  });

  it('点某一行 ⇒ onSelect 回传该行的 run_id', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderPanel({ runs: [makeRun({ run_id: 'wfr_a' }), makeRun({ run_id: 'wfr_b' })], onSelect });

    await user.click(screen.getByTestId('run-row-wfr_b'));
    expect(onSelect).toHaveBeenCalledWith('wfr_b');
  });

  it('选中的那一行带高亮标记', () => {
    renderPanel({ runs: [makeRun({ run_id: 'wfr_a' })], selectedRunId: 'wfr_a' });
    expect(screen.getByTestId('run-row-wfr_a').className).toContain('kv-list__row--active');
  });

  it('轮询错误显示在时间线区（detailError 优先）', () => {
    const { unmount } = renderPanel({ pollError: '轮询炸了' });
    expect(screen.getByRole('alert')).toHaveTextContent('轮询炸了');
    unmount();

    renderPanel({ pollError: '轮询炸了', detailError: '详情也炸了' });
    expect(screen.getByRole('alert')).toHaveTextContent('详情也炸了');
  });

  it('PAUSED + 有暂停载荷 ⇒ 渲染审批卡；无暂停载荷 ⇒ 不渲染', () => {
    const paused = makeRun({ run_id: 'wfr_p', status: 'PAUSED' });
    const { unmount } = renderPanel({
      detailRun: paused,
      pausePayload: { stage: 'chapter-review', message: '请人工决议' },
    });
    expect(screen.getByText('审批')).toBeInTheDocument();
    unmount();

    renderPanel({ detailRun: paused, pausePayload: undefined });
    expect(screen.queryByText('审批')).toBeNull();
  });

  it('非 PAUSED 的 run ⇒ 不渲染审批卡（闸门没停就没有审批）', () => {
    renderPanel({
      detailRun: makeRun({ status: 'RUNNING' }),
      pausePayload: { stage: 'chapter-review', message: '请人工决议' },
    });
    expect(screen.queryByText('审批')).toBeNull();
  });

  it('FAILED 的 run 把后端原文错误渲染出来（不只给一个红点）', () => {
    // run 级错误渲染在**节点时间线之内** ⇒ 必须有节点，否则组件走「暂无节点明细」空态。
    const failed = makeRun({
      run_id: 'wfr_f',
      status: 'FAILED',
      error: 'observer delta failed validation',
      nodes: [
        {
          node_run_id: 'wnr_1',
          run_id: 'wfr_f',
          node_id: 'observer',
          agent_id: 'observer',
          status: 'FAILED',
          input_json: {},
          output_json: null,
          prompt_version: null,
          model_id: null,
          token_usage_json: null,
          latency_ms: 12,
          error: 'observer delta failed validation',
          started_at: '2026-08-24T10:00:00+00:00',
          ended_at: '2026-08-24T10:00:12+00:00',
        },
      ],
    });
    renderPanel({ runs: [failed], detailRun: failed, selectedRunId: 'wfr_f' });
    expect(screen.getByTestId('run-level-error')).toHaveTextContent(
      'observer delta failed validation',
    );
  });

  it('暂停载荷含 deep_review_report ⇒ 渲染深度二审分栏与判定徽标', () => {
    renderPanel({
      detailRun: makeRun({ status: 'PAUSED' }),
      pausePayload: {
        stage: 'chapter-review',
        message: '请人工决议',
        deep_review_report: {
          verdict: 'revise',
          overall_comment: '转折偏弱',
          issues: [
            {
              id: 'd1',
              layer: 'beat',
              severity: 'medium',
              quote: '他忽然就走了',
              suggestion: '补一次铺垫',
            },
          ],
        },
      },
    });
    expect(screen.getByTestId('deep-review-report')).toBeInTheDocument();
    expect(screen.getByTestId('deep-review-verdict')).toHaveAttribute('data-verdict', 'revise');
    expect(screen.getByTestId('deep-review-issue')).toHaveAttribute('data-layer', 'beat');
  });

  it('暂停载荷不含 deep_review_report ⇒ 不渲染该分栏（坏形状也不炸页）', () => {
    renderPanel({
      detailRun: makeRun({ status: 'PAUSED' }),
      pausePayload: { stage: 'chapter-review', message: '请人工决议' },
    });
    expect(screen.queryByTestId('deep-review-report')).toBeNull();
  });
});
