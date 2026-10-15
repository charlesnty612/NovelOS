// D1（2026-10-15）：`RELEASED` 终态的 UI 接线。
//
// **为什么这组用例必须存在**：`RELEASED` 写进状态机 Literal、CHECK 约束、
// 徽标配色、`delivery`/`signing_check` 的读取集里，却**没有任何一处写入它**——
// 徽标写「已发布」、后端当它是「已过提交门」，而实际值恒不存在（dev 库实测
// `chapters.status` 分布：COMMITTED 88 / DRAFTED 9 / PLANNED 80 / **RELEASED 0**）。
// 后端 `PATCH /chapters/{id}` 一直支持 `COMMITTED → RELEASED`，缺的只是入口。
//
// 判别点：`chaptersApi.update` 真的被调到了，且带**正确的目标状态**。
// 只断言「按钮在」抓不到——按钮摆在那儿不点，与没有功能同形。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { chaptersApi } from '../../api/endpoints';
import type { Chapter } from '../../api/types';
import { ChapterDeliveryActions } from './ChapterDeliveryActions';

vi.mock('../../api/endpoints', async () => {
  const actual = await vi.importActual<typeof import('../../api/endpoints')>(
    '../../api/endpoints',
  );
  return { ...actual, chaptersApi: { ...actual.chaptersApi, update: vi.fn() } };
});

const chapter = (status: Chapter['status']): Chapter => ({
  chapter_id: 'ch_1',
  project_id: 'prj_1',
  number: 3,
  title: '第三章',
  plan_json: {},
  status,
  visibility: 'VISIBLE',
  who_knows: null,
  created_at: '2026-10-01T00:00:00',
  updated_at: '2026-10-01T00:00:00',
});

const update = chaptersApi.update as unknown as ReturnType<typeof vi.fn>;

describe('ChapterDeliveryActions（D1：RELEASED 接线）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    update.mockResolvedValue(chapter('RELEASED'));
  });

  it('非 COMMITTED / RELEASED 状态整块不渲染（不给无关章加噪声按钮）', () => {
    for (const s of ['PLANNED', 'DRAFTED', 'REVIEWED'] as const) {
      const { unmount } = render(
        <ChapterDeliveryActions chapter={chapter(s)} busy={false} onChanged={() => {}} />,
      );
      expect(screen.queryByTestId('delivery-section')).not.toBeInTheDocument();
      unmount();
    }
  });

  it('COMMITTED ⇒ 渲染「标记已发布」', () => {
    render(<ChapterDeliveryActions chapter={chapter('COMMITTED')} busy={false} onChanged={() => {}} />);
    expect(screen.getByTestId('release-btn')).toBeInTheDocument();
    expect(screen.queryByTestId('unrelease-btn')).not.toBeInTheDocument();
  });

  it('RELEASED ⇒ 渲染「撤销发布标记」——单向前进会把误点变成永久错标', () => {
    render(<ChapterDeliveryActions chapter={chapter('RELEASED')} busy={false} onChanged={() => {}} />);
    expect(screen.getByTestId('unrelease-btn')).toBeInTheDocument();
    expect(screen.queryByTestId('release-btn')).not.toBeInTheDocument();
  });

  it('点「标记已发布」⇒ PATCH 带 status=RELEASED，并回调重拉', async () => {
    const onChanged = vi.fn();
    render(
      <ChapterDeliveryActions chapter={chapter('COMMITTED')} busy={false} onChanged={onChanged} />,
    );
    await userEvent.click(screen.getByTestId('release-btn'));
    await waitFor(() => {
      expect(update).toHaveBeenCalledWith('ch_1', { status: 'RELEASED' });
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('点「撤销」⇒ PATCH 带 status=PLANNED（ALLOWED_NEXT 允许的唯一退路）', async () => {
    update.mockResolvedValue(chapter('PLANNED'));
    render(<ChapterDeliveryActions chapter={chapter('RELEASED')} busy={false} onChanged={() => {}} />);
    await userEvent.click(screen.getByTestId('unrelease-btn'));
    await waitFor(() => {
      expect(update).toHaveBeenCalledWith('ch_1', { status: 'PLANNED' });
    });
  });

  it('busy（有 run 在跑 / 正在提交）时禁用，不与流水线抢同一章', () => {
    render(<ChapterDeliveryActions chapter={chapter('COMMITTED')} busy onChanged={() => {}} />);
    expect(screen.getByTestId('release-btn')).toBeDisabled();
  });

  // 反向用例：把「按钮真的发请求」这一步摘掉，本组 6 条里至少这 2 条必须红。
  // 少这条断言时，「按钮渲染正常但从不发请求」可以全程绿灯。
  it('PATCH 失败 ⇒ 错误可见，按钮恢复可点（不吞失败）', async () => {
    update.mockRejectedValue(new Error('409 illegal transition'));
    render(<ChapterDeliveryActions chapter={chapter('COMMITTED')} busy={false} onChanged={() => {}} />);
    await userEvent.click(screen.getByTestId('release-btn'));
    expect(await screen.findByText(/409 illegal transition/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('release-btn')).not.toBeDisabled());
  });
});
