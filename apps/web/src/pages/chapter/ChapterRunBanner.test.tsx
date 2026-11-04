/**
 * ChapterRunBanner 专属测试（C-43）。
 *
 * 判别点：横幅的专属提示必须按 **`workflow_name`** 判断。
 * `workflow_id` 是不透明的 `wf_<hex>`（每次启动都不同）⇒ 旧实现拿它比对
 * `'chapter-write'` 恒不命中，提示永远落到兜底文案（拆掉本修复必红）。
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChapterRunBanner } from './ChapterRunBanner';
import type { WorkflowRun } from '../../api/types';

function run(over: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    run_id: 'run_001',
    workflow_id: 'wf_0a10d0d159c9', // 不透明 id（绝不能用来判动作）
    workflow_name: null,
    chapter_id: 'ch_001',
    status: 'RUNNING',
    current_node: null,
    checkpoint_json: {},
    error: null,
    retry_count: 0,
    started_at: '2026-11-05T10:00:00+00:00',
    ended_at: null,
    nodes: [],
    ...over,
  } as WorkflowRun;
}

const noop = () => {};

describe('ChapterRunBanner（C-43：按 workflow_name 判提示）', () => {
  it('workflow_name=chapter-write ⇒ 显示写正文提示（不透明的 workflow_id 不影响判断）', () => {
    render(
      <ChapterRunBanner
        detail={run({ workflow_name: 'chapter-write' })}
        cancelPending={false}
        onRequestCancel={noop}
      />,
    );
    expect(screen.getByTestId('workflow-running-banner')).toHaveTextContent(
      '正在生成正文草稿',
    );
  });

  it('workflow_name=chapter-review ⇒ 显示审校提示', () => {
    render(
      <ChapterRunBanner
        detail={run({ workflow_name: 'chapter-review' })}
        cancelPending={false}
        onRequestCancel={noop}
      />,
    );
    expect(screen.getByTestId('workflow-running-banner')).toHaveTextContent(
      '正在执行审校',
    );
  });

  it('没有 workflow_name ⇒ 落到兜底文案，不瞎猜具体动作', () => {
    render(
      <ChapterRunBanner
        detail={run({ workflow_name: null })}
        cancelPending={false}
        onRequestCancel={noop}
      />,
    );
    expect(screen.getByTestId('workflow-running-banner')).toHaveTextContent(
      '正在执行（约需',
    );
  });

  it('PENDING（尚未真正开始）⇒ 排队提示优先于动作提示', () => {
    render(
      <ChapterRunBanner
        detail={run({ status: 'PENDING', workflow_name: 'chapter-write' })}
        cancelPending={false}
        onRequestCancel={noop}
      />,
    );
    expect(screen.getByTestId('workflow-running-banner')).toHaveTextContent(
      '正在排队启动',
    );
  });

  it('RUNNING 才显示停止按钮；PENDING 不显示（避免歧义）', () => {
    const { rerender } = render(
      <ChapterRunBanner
        detail={run({ status: 'RUNNING', workflow_name: 'chapter-write' })}
        cancelPending={false}
        onRequestCancel={noop}
      />,
    );
    expect(screen.getByTestId('wf-cancel-btn')).toBeInTheDocument();
    rerender(
      <ChapterRunBanner
        detail={run({ status: 'PENDING', workflow_name: 'chapter-write' })}
        cancelPending={false}
        onRequestCancel={noop}
      />,
    );
    expect(screen.queryByTestId('wf-cancel-btn')).not.toBeInTheDocument();
  });
});
