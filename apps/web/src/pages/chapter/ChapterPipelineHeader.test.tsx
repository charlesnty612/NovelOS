/**
 * ChapterPipelineHeader 专属测试（补口：此前该组件**零专属测试**）。
 *
 * 组件 426 行，管着四步流水线按钮 + 三个开关（快通道 / 深度二审 / auto_commit）
 * + 标题 inline 改名。判别的是**它交给页面的载荷**（onStart 的第二参、
 * onFastModeChange 的实参）——那才是页面真正发出去的东西。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { ChapterPipelineHeader } from './ChapterPipelineHeader';
import { chaptersApi, modelProfilesApi } from '../../api/endpoints';
import type { Chapter, ModelProfile } from '../../api/types';

vi.mock('../../api/endpoints', () => ({
  chaptersApi: {
    update: vi.fn(),
  },
  modelProfilesApi: {
    list: vi.fn(),
  },
}));

const baseChapter = (over: Partial<Chapter> = {}): Chapter =>
  ({
    chapter_id: 'ch_001',
    project_id: 'prj_001',
    number: 1,
    title: '第一章',
    status: 'PLANNED',
    visibility: 'public',
    who_knows: null,
    created_at: '2026-08-24T10:00:00+00:00',
    updated_at: '2026-08-24T10:00:00+00:00',
    plan_json: {},
    ...over,
  }) as unknown as Chapter;

type Props = Parameters<typeof ChapterPipelineHeader>[0];

function renderHeader(over: Partial<Props> = {}) {
  const props: Props = {
    chapter: baseChapter(),
    activeRunStatus: null,
    submitting: false,
    selectedDraftVersion: 1,
    onStart: vi.fn(),
    deepReview: false,
    onDeepReviewChange: vi.fn(),
    onTitleSaved: vi.fn(),
    fastMode: false,
    onFastModeChange: vi.fn(),
    autoCommit: false,
    onAutoCommitChange: vi.fn(),
    unreviewedDraft: null,
    ...over,
  };
  const utils = render(<ChapterPipelineHeader {...props} />);
  return { props, ...utils };
}

/** 取 onStart 某次调用的第二参（业务载荷） */
function startPayload(onStart: ReturnType<typeof vi.fn>, index = 0): Record<string, unknown> {
  const call = onStart.mock.calls[index];
  return (call?.[1] ?? {}) as Record<string, unknown>;
}

/**
 * 受控外壳：两个开关都是**受控组件**（checked 由父组件持有），
 * 直接 render 组件时父组件不改 prop ⇒ 第二次点击拿到的仍是 true。
 * 这里用真实父组件语义（受控 state）驱动，并把回调实参记下来。
 */
function renderHarness(over: Partial<Props> = {}) {
  const onStart = vi.fn();
  const fastModeCalls: boolean[] = [];
  const autoCommitCalls: boolean[] = [];

  function Harness() {
    const [fastMode, setFastMode] = useState(over.fastMode ?? false);
    const [autoCommit, setAutoCommit] = useState(over.autoCommit ?? false);
    const [deepReview, setDeepReview] = useState(over.deepReview ?? false);
    return (
      <ChapterPipelineHeader
        {...(over as Props)}
        chapter={over.chapter ?? baseChapter()}
        activeRunStatus={over.activeRunStatus ?? null}
        submitting={over.submitting ?? false}
        selectedDraftVersion={over.selectedDraftVersion ?? 1}
        onStart={over.onStart ?? onStart}
        deepReview={deepReview}
        onDeepReviewChange={(v) => {
          setDeepReview(v);
          over.onDeepReviewChange?.(v);
        }}
        onTitleSaved={over.onTitleSaved ?? vi.fn()}
        fastMode={fastMode}
        fastModeLabel={over.fastModeLabel}
        fastModeError={over.fastModeError}
        onFastModeChange={(v) => {
          fastModeCalls.push(v);
          setFastMode(v);
          over.onFastModeChange?.(v);
        }}
        autoCommit={autoCommit}
        onAutoCommitChange={(v) => {
          autoCommitCalls.push(v);
          setAutoCommit(v);
          over.onAutoCommitChange?.(v);
        }}
        unreviewedDraft={over.unreviewedDraft ?? null}
      />
    );
  }

  const utils = render(<Harness />);
  return { onStart, fastModeCalls, autoCommitCalls, ...utils };
}

describe('ChapterPipelineHeader', () => {
  beforeEach(() => {
    vi.mocked(chaptersApi.update).mockReset();
    vi.mocked(modelProfilesApi.list).mockReset();
    // 默认无档案 ⇒ 不渲染模型下拉（不干扰其它断言）
    vi.mocked(modelProfilesApi.list).mockResolvedValue([] as ModelProfile[]);
  });

  // ---------------------------------------------------------------------------
  // 快通道开关
  // ---------------------------------------------------------------------------
  it('勾选/取消快通道 ⇒ onFastModeChange 分别收到 true / false', async () => {
    const user = userEvent.setup();
    const { fastModeCalls } = renderHarness();

    const box = screen.getByTestId('wf-fast-mode-checkbox') as HTMLInputElement;
    await user.click(box);
    expect(fastModeCalls).toEqual([true]);
    await user.click(box);
    expect(fastModeCalls).toEqual([true, false]);
  });

  it('快通道三态文案：未设置时显示「未设置」而不是「已关闭」', () => {
    renderHeader({ fastModeLabel: '未设置（当前生效：关）' });
    expect(screen.getByTestId('wf-fast-mode-state')).toHaveTextContent('未设置');
    expect(screen.getByTestId('wf-fast-mode-state')).not.toHaveTextContent('已关闭');
  });

  it('快通道三态文案：开 / 关分别显示「已开启」/「已关闭」', () => {
    const { unmount } = renderHeader({ fastModeLabel: '已开启' });
    expect(screen.getByTestId('wf-fast-mode-state')).toHaveTextContent('已开启');
    unmount();
    renderHeader({ fastModeLabel: '已关闭' });
    expect(screen.getByTestId('wf-fast-mode-state')).toHaveTextContent('已关闭');
  });

  it('快通道保存失败 ⇒ 错误就地显示在开关旁', () => {
    renderHeader({ fastModeError: '后端不可用' });
    expect(screen.getByTestId('wf-fast-mode-error')).toHaveTextContent('后端不可用');
  });

  // ---------------------------------------------------------------------------
  // auto_commit 载荷链（组件这一段：写正文时带不带 auto_commit）
  // ---------------------------------------------------------------------------
  it('快通道关 ⇒ 写正文的载荷不带 auto_commit', async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();
    renderHeader({ onStart, fastMode: false, autoCommit: true });

    await user.click(screen.getByTestId('wf-btn-write'));
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(onStart.mock.calls[0][0]).toBe('write');
    expect(startPayload(onStart).auto_commit).toBeUndefined();
  });

  it('快通道开且 auto_commit 勾 ⇒ 写正文的载荷带 auto_commit: true', async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();
    renderHeader({ onStart, fastMode: true, autoCommit: true });

    await user.click(screen.getByTestId('wf-btn-write'));
    expect(startPayload(onStart).auto_commit).toBe(true);
  });

  it('快通道关时 auto_commit 复选框禁用（不给「会自动提交」的假承诺）', () => {
    renderHeader({ fastMode: false, autoCommit: true });
    const box = screen.getByTestId('wf-auto-commit-checkbox') as HTMLInputElement;
    expect(box).toBeDisabled();
    expect(screen.getByTestId('wf-auto-commit-toggle')).toHaveAttribute('data-disabled', 'true');
  });

  it('auto_commit 复选框的勾选/取消 ⇒ onAutoCommitChange 收到对应布尔值', async () => {
    const user = userEvent.setup();
    const { autoCommitCalls } = renderHarness({ fastMode: true });

    const box = screen.getByTestId('wf-auto-commit-checkbox') as HTMLInputElement;
    await user.click(box);
    expect(autoCommitCalls).toEqual([true]);
    await user.click(box);
    expect(autoCommitCalls).toEqual([true, false]);
  });

  // ---------------------------------------------------------------------------
  // 深度二审
  // ---------------------------------------------------------------------------
  it('勾了深度二审 ⇒ 审校的载荷带 deep_review: true；未勾带 false', async () => {
    const user = userEvent.setup();
    // 审校按钮仅在 DRAFTED 可点（状态机 EXPECTED_STATUS）
    const chapter = baseChapter({ status: 'DRAFTED' });

    const onStart1 = vi.fn();
    const { unmount } = renderHeader({ onStart: onStart1, chapter, deepReview: false });
    await user.click(screen.getByTestId('wf-btn-review'));
    expect(onStart1).toHaveBeenCalledTimes(1);
    expect(startPayload(onStart1).deep_review).toBe(false);
    unmount();

    const onStart2 = vi.fn();
    renderHeader({ onStart: onStart2, chapter, deepReview: true });
    await user.click(screen.getByTestId('wf-btn-review'));
    expect(onStart2).toHaveBeenCalledTimes(1);
    expect(startPayload(onStart2).deep_review).toBe(true);
  });

  // ---------------------------------------------------------------------------
  // 标题 inline 改名
  // ---------------------------------------------------------------------------
  it('改名保存 ⇒ 调 chaptersApi.update 并回调 onTitleSaved', async () => {
    const user = userEvent.setup();
    vi.mocked(chaptersApi.update).mockResolvedValue(baseChapter({ title: '新标题' }));
    const onTitleSaved = vi.fn();
    renderHeader({ onTitleSaved });

    await user.click(screen.getByTestId('chapter-title-btn'));
    const input = screen.getByTestId('chapter-title-input') as HTMLInputElement;
    await user.clear(input);
    await user.type(input, '新标题');
    await user.click(screen.getByTestId('chapter-title-save'));

    await waitFor(() => expect(chaptersApi.update).toHaveBeenCalledTimes(1));
    expect(vi.mocked(chaptersApi.update).mock.calls[0][1]).toEqual({ title: '新标题' });
    await waitFor(() => expect(onTitleSaved).toHaveBeenCalledTimes(1));
  });
});
