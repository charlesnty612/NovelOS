// 交付判定 + 签约自检面板（评估 G6：schema 早已生成，UI 不接）。
//
// 判别重点是**诚实面**：后端 `evidence_sources[].reachable=false` 表示某类证据面
// 没采到。把这种「没看到」显示成绿灯（本仓签名体检自己的口径要求显式说出来）
// 会让作者拿一个不完整的结论去做签约决策——这比没有面板更坏。
//
// 反向用例：reachable 全 true 时**不得**显示不可达提示（否则警告会被训练成噪声，
// 正是本仓「正常词与真套话分层」的同款教训）。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { deliveryApi, signingCheckApi } from '../api/endpoints';
import type { DeliveryVerdictResponse, SigningCheckResult } from '../api/types';
import { DeliveryPanel } from './DeliveryPanel';

vi.mock('../api/endpoints', () => ({
  deliveryApi: { verdict: vi.fn() },
  signingCheckApi: { run: vi.fn() },
}));

const dVerdict = deliveryApi.verdict as unknown as ReturnType<typeof vi.fn>;
const sRun = signingCheckApi.run as unknown as ReturnType<typeof vi.fn>;

const verdict = (over: Partial<DeliveryVerdictResponse> = {}): DeliveryVerdictResponse => ({
  project_id: 'p1',
  project_name: '测试书',
  generated_at: '2026-10-15T08:00:00Z',
  project_verdict: 'needs_work',
  roll_up: {
    chapter_count: 3,
    written_chapter_count: 2,
    deliverable_count: 1,
    needs_work_count: 1,
    not_deliverable_count: 0,
    not_a_candidate_count: 0,
    deliverable_chapter_ids: ['c1'],
    needs_work_chapter_ids: ['c2'],
    not_deliverable_chapter_ids: [],
    not_a_candidate_chapter_ids: [],
  },
  evidence_sources: [{ key: 'quality_report', reachable: true, detail: '已采到' }],
  chapters: [],
  ...over,
});

const signing = (over: Partial<SigningCheckResult> = {}): SigningCheckResult => ({
  project_id: 'p1',
  project_name: '测试书',
  total_chars: 6000,
  generated_at: '2026-10-15T08:00:00Z',
  items: [{ key: 'opening', level: 'warn', detail: '开篇钩子偏弱', advice: '前三句给冲突' }],
  summary: { pass_count: 3, warn_count: 1, fail_count: 0, info_count: 0 },
  word_band_source: 'default',
  ...over,
});

describe('DeliveryPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    dVerdict.mockResolvedValue(verdict());
    sRun.mockResolvedValue(signing());
  });

  it('渲染项目级判定与写/总章数', async () => {
    render(<DeliveryPanel projectId="p1" />);
    const v = await screen.findByTestId('delivery-verdict');
    expect(v).toHaveTextContent('需再改');
    expect(v).toHaveTextContent('2/3 章已写');
  });

  it('四档判定各有中文标签，不把结论压成一个「合格/不合格」', async () => {
    dVerdict.mockResolvedValue(verdict({ project_verdict: 'not_a_candidate' }));
    render(<DeliveryPanel projectId="p1" />);
    expect(await screen.findByTestId('delivery-verdict')).toHaveTextContent('不构成候选');
  });

  it('证据源不可达 ⇒ 显式列出（不得显示成通过）', async () => {
    dVerdict.mockResolvedValue(
      verdict({
        evidence_sources: [
          { key: 'quality_report', reachable: true, detail: '已采到' },
          { key: 'signing_check', reachable: false, detail: '未配置签约规则' },
        ],
      }),
    );
    render(<DeliveryPanel projectId="p1" />);
    const note = await screen.findByTestId('delivery-unreachable');
    expect(note).toHaveTextContent('signing_check');
    expect(note).toHaveTextContent('未配置签约规则');
    // 可达的那个不得混进不可达清单
    expect(note).not.toHaveTextContent('quality_report');
  });

  it('全部可达 ⇒ 不显示不可达提示（否则警告会变成噪声）', async () => {
    render(<DeliveryPanel projectId="p1" />);
    await screen.findByTestId('delivery-verdict');
    expect(screen.queryByTestId('delivery-unreachable')).not.toBeInTheDocument();
  });

  it('项目级理由逐条列出，并带证据摘录', async () => {
    dVerdict.mockResolvedValue(
      verdict({
        project_reasons: [
          {
            rule_id: 'r1',
            source: 'length',
            severity: 'warning',
            status: 'length_out_of_band',
            message: '第 2 章字数欠带',
            evidence: '1853 < 2125',
          },
        ],
      }),
    );
    render(<DeliveryPanel projectId="p1" />);
    const list = await screen.findByTestId('delivery-project-reasons');
    expect(list).toHaveTextContent('第 2 章字数欠带');
    expect(list).toHaveTextContent('1853 < 2125');
  });

  it('逐章判定：章号 / 标题 / 判定 / 首个降级理由', async () => {
    dVerdict.mockResolvedValue(
      verdict({
        chapters: [
          {
            chapter_id: 'c2',
            number: 2,
            title: '欠带章',
            status: 'COMMITTED',
            verdict: 'needs_work',
            blocking_reason: '字数欠带',
            signing: { scope: 'out_of_scope' },
          },
        ],
      }),
    );
    render(<DeliveryPanel projectId="p1" />);
    const row = await screen.findByTestId('delivery-chapter-c2');
    expect(row).toHaveTextContent('第2章');
    expect(row).toHaveTextContent('欠带章');
    expect(row).toHaveTextContent('字数欠带');
  });

  it('可交付章的降级理由显示「—」而不是空白', async () => {
    dVerdict.mockResolvedValue(
      verdict({
        chapters: [
          {
            chapter_id: 'c1',
            number: 1,
            title: '好章',
            status: 'COMMITTED',
            verdict: 'deliverable',
            blocking_reason: null,
            signing: { scope: 'in_scope' },
          },
        ],
      }),
    );
    render(<DeliveryPanel projectId="p1" />);
    expect(await screen.findByTestId('delivery-chapter-c1')).toHaveTextContent('—');
  });

  it('签约自检：摘要计数 + 逐条 level/detail/advice', async () => {
    render(<DeliveryPanel projectId="p1" />);
    const box = await screen.findByTestId('signing-check');
    expect(box).toHaveTextContent('通过 3');
    expect(box).toHaveTextContent('警告 1');
    expect(box).toHaveTextContent('开篇钩子偏弱');
    expect(box).toHaveTextContent('前三句给冲突');
  });

  it('「重新判定」⇒ 真的重发两个 GET（判定是现算的，不缓存）', async () => {
    render(<DeliveryPanel projectId="p1" />);
    await screen.findByTestId('delivery-verdict');
    await userEvent.click(screen.getByRole('button', { name: '重新判定' }));
    await waitFor(() => expect(dVerdict).toHaveBeenCalledTimes(2));
    expect(sRun).toHaveBeenCalledTimes(2);
  });

  it('接口失败 ⇒ 错误可见，不静默成「无问题」', async () => {
    dVerdict.mockRejectedValue(new Error('判定服务异常'));
    render(<DeliveryPanel projectId="p1" />);
    expect(await screen.findByText(/判定服务异常/)).toBeInTheDocument();
    expect(screen.queryByTestId('delivery-verdict')).not.toBeInTheDocument();
  });
});
