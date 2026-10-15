// 状态版本历史面板（G6：commits + diff + rollback）。
//
// 判别重点在**高风险批准路径**：后端 `rollback_commit` 对 HIGH 风险变更抛
// `ApprovalRequiredError` → 409，且 detail 经 `client.ts::parseError` 被
// **JSON.stringify 成字符串**。本组用例必须钉住两件事：
//   1. 第一次失败**不自动重发**（不绕过批准）；
//   2. change id 清单要摆给作者看，且批准后才带 `author_approval` 重发。
// 把「第二次自动重发」实现出来的话，第 1 条立刻红。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiError } from '../api/client';
import { commitsApi, storyStateApi } from '../api/endpoints';
import type { Commit } from '../api/types';
import { StateHistoryPanel } from './StateHistoryPanel';

vi.mock('../api/endpoints', async () => {
  const actual = await vi.importActual<typeof import('../api/endpoints')>(
    '../api/endpoints',
  );
  return {
    ...actual,
    commitsApi: { ...actual.commitsApi, rollback: vi.fn() },
    storyStateApi: { ...actual.storyStateApi, diff: vi.fn() },
  };
});

const rollback = commitsApi.rollback as unknown as ReturnType<typeof vi.fn>;
const diff = storyStateApi.diff as unknown as ReturnType<typeof vi.fn>;

const commit = (version: number, parent: number | null): Commit => ({
  commit_id: `cm_${version}`,
  project_id: 'p1',
  version,
  parent_version: parent,
  message: `第 ${version} 次提交`,
  author: 'human',
  created_at: '2026-10-01T08:00:00Z',
});

describe('StateHistoryPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    rollback.mockResolvedValue({});
    diff.mockResolvedValue({ version_a: 1, version_b: 2 });
  });

  it('无 commit ⇒ 空态文案，不是空白卡片', () => {
    render(<StateHistoryPanel projectId="p1" commits={[]} onChanged={() => {}} />);
    expect(screen.getByTestId('state-history-empty')).toBeInTheDocument();
  });

  it('列出 commit 版本与说明', () => {
    render(
      <StateHistoryPanel
        projectId="p1"
        commits={[commit(2, 1), commit(1, null)]}
        onChanged={() => {}}
      />,
    );
    expect(screen.getAllByTestId('state-history-row')).toHaveLength(2);
    expect(screen.getByText('第 2 次提交')).toBeInTheDocument();
  });

  it('点「改了什么」⇒ 用 (parent, current) 两个版本调 diff', async () => {
    render(
      <StateHistoryPanel projectId="p1" commits={[commit(2, 1)]} onChanged={() => {}} />,
    );
    await userEvent.click(screen.getByTestId('state-diff-btn'));
    await waitFor(() => expect(diff).toHaveBeenCalledWith('p1', 1, 2));
  });

  it('首个 commit 没有上一版 ⇒ 说明而不是空白面板', async () => {
    render(
      <StateHistoryPanel projectId="p1" commits={[commit(1, null)]} onChanged={() => {}} />,
    );
    await userEvent.click(screen.getByTestId('state-diff-btn'));
    expect(await screen.findByText(/没有上一版可比/)).toBeInTheDocument();
    expect(diff).not.toHaveBeenCalled();
  });

  it('点「回滚」⇒ POST /commits/{id}/rollback，空批准体', async () => {
    const onChanged = vi.fn();
    render(
      <StateHistoryPanel projectId="p1" commits={[commit(2, 1)]} onChanged={onChanged} />,
    );
    await userEvent.click(screen.getByTestId('state-rollback-btn'));
    await waitFor(() => expect(rollback).toHaveBeenCalledWith('cm_2', {}));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  // ---- 高风险批准：反向用例成对 ----
  it('后端要求批准 ⇒ 摆出 change id 清单，且**不自动重发**', async () => {
    rollback.mockRejectedValue(
      new ApiError(
        409,
        JSON.stringify({
          error: 'approval_required',
          message: '含高风险变更',
          high_risk_change_ids: ['wr_world_rule_1', 'wr_world_rule_2'],
        }),
      ),
    );
    render(<StateHistoryPanel projectId="p1" commits={[commit(2, 1)]} onChanged={() => {}} />);
    await userEvent.click(screen.getByTestId('state-rollback-btn'));

    const panel = await screen.findByTestId('rollback-approval');
    expect(panel).toBeInTheDocument();
    const list = screen.getByTestId('rollback-approval-list');
    expect(list).toHaveTextContent('wr_world_rule_1');
    expect(list).toHaveTextContent('wr_world_rule_2');
    // 关键：清单出现时**仍然只发过一次**——自动重发等于绕过批准。
    expect(rollback).toHaveBeenCalledTimes(1);
  });

  it('点确认 ⇒ 带 author_approval（含 change id 清单）重发', async () => {
    rollback.mockRejectedValueOnce(
      new ApiError(
        409,
        JSON.stringify({ error: 'approval_required', high_risk_change_ids: ['wr_1'] }),
      ),
    );
    render(<StateHistoryPanel projectId="p1" commits={[commit(2, 1)]} onChanged={() => {}} />);
    await userEvent.click(screen.getByTestId('state-rollback-btn'));
    await screen.findByTestId('rollback-approval');
    await userEvent.click(screen.getByTestId('rollback-approve-btn'));

    await waitFor(() => expect(rollback).toHaveBeenCalledTimes(2));
    expect(rollback.mock.calls[1]).toEqual([
      'cm_2',
      expect.objectContaining({
        approved: true,
        approver: 'human',
        high_risk_change_ids: ['wr_1'],
      }),
    ]);
  });

  it('点取消 ⇒ 不再发请求，清单消失', async () => {
    rollback.mockRejectedValue(
      new ApiError(409, JSON.stringify({ error: 'approval_required', high_risk_change_ids: [] })),
    );
    render(<StateHistoryPanel projectId="p1" commits={[commit(2, 1)]} onChanged={() => {}} />);
    await userEvent.click(screen.getByTestId('state-rollback-btn'));
    await screen.findByTestId('rollback-approval');
    await userEvent.click(screen.getByTestId('rollback-approval-cancel'));

    await waitFor(() => expect(screen.queryByTestId('rollback-approval')).not.toBeInTheDocument());
    expect(rollback).toHaveBeenCalledTimes(1);
  });

  // 反向：普通 409（如乐观锁）**不得**被误判成「要批准」——
  // 那会把一句没有清单的失败包装成一次批准流程。
  it('非 approval_required 的 409 ⇒ 直接报错，不弹批准面板', async () => {
    rollback.mockRejectedValue(
      new ApiError(409, JSON.stringify({ error: 'optimistic_lock', message: '版本已变' })),
    );
    render(<StateHistoryPanel projectId="p1" commits={[commit(2, 1)]} onChanged={() => {}} />);
    await userEvent.click(screen.getByTestId('state-rollback-btn'));
    await screen.findByText(/optimistic_lock/);
    expect(screen.queryByTestId('rollback-approval')).not.toBeInTheDocument();
  });

  it('detail 不是 JSON 字符串 ⇒ 不得崩溃，按普通失败处理', async () => {
    rollback.mockRejectedValue(new ApiError(409, 'plain text failure'));
    render(<StateHistoryPanel projectId="p1" commits={[commit(2, 1)]} onChanged={() => {}} />);
    await userEvent.click(screen.getByTestId('state-rollback-btn'));
    await screen.findByText(/plain text failure/);
    expect(screen.queryByTestId('rollback-approval')).not.toBeInTheDocument();
  });
});
