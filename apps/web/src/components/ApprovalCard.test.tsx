import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApprovalCard } from './ApprovalCard';

describe('ApprovalCard', () => {
  const baseProps = {
    runId: 'r1',
    stage: 'chapter-review',
    message: '请作者审查',
    pausePayload: {
      stage: 'chapter-review',
      message: '请作者审查',
      review_report: {
        chapter_id: 'ch_a',
        word_count: 2000,
        target_word_count: 2200,
        within_range: true,
        deviation: -0.0909,
        forbidden_word_hits: [],
        warnings: [],
      },
    },
    highRiskChangeCount: 0,
    submitting: false,
    error: null,
    onApprove: vi.fn().mockResolvedValue(undefined),
  };

  it('渲染 stage 与 message', () => {
    render(<ApprovalCard {...baseProps} />);
    expect(screen.getByText(/stage:\s*chapter-review/)).toBeInTheDocument();
    expect(screen.getByText('请作者审查')).toBeInTheDocument();
    expect(screen.getByTestId('approval-card')).toBeInTheDocument();
  });

  it('review_report 模式下展示字数 / 目标 / 偏差', () => {
    render(<ApprovalCard {...baseProps} />);
    // review_report 中的字段被格式化展示
    expect(screen.getByText('2000')).toBeInTheDocument();
    expect(screen.getByText('2200')).toBeInTheDocument();
    expect(screen.getByText('-9.1%')).toBeInTheDocument();
  });

  it('点击「批准」触发 onApprove(true)', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalCard {...baseProps} onApprove={onApprove} />);
    fireEvent.click(screen.getByTestId('approval-approve'));
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith(true));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it('点击「驳回」触发 onApprove(false)', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalCard {...baseProps} onApprove={onApprove} />);
    fireEvent.click(screen.getByTestId('approval-reject'));
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith(false));
  });

  it('submitting=true 时禁用按钮', () => {
    render(<ApprovalCard {...baseProps} submitting={true} />);
    expect(screen.getByTestId('approval-approve')).toBeDisabled();
    expect(screen.getByTestId('approval-reject')).toBeDisabled();
  });

  it('commit.high_risk_approval 模式下展示变更条数', () => {
    render(
      <ApprovalCard
        {...baseProps}
        stage="chapter-commit.high_risk_approval"
        message="高风险变更待审批"
        pausePayload={{
          stage: 'chapter-commit.high_risk_approval',
          message: '高风险变更待审批',
          delta_id: 'd1',
          changes: {
            character_changes: [{ risk_level: 'HIGH' }, { risk_level: 'LOW' }],
            world_changes: [{ world_kind: 'rule' }],
          },
        }}
        highRiskChangeCount={3}
      />,
    );
    expect(screen.getByText('3 项')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument(); // character_changes 数
    expect(screen.getByText('1')).toBeInTheDocument(); // world_changes 数
  });

  it('review_report 含 warnings / forbidden_word_hits 时一并展示', () => {
    render(
      <ApprovalCard
        {...baseProps}
        pausePayload={{
          stage: 'chapter-review',
          message: '审查',
          review_report: {
            chapter_id: 'ch_a',
            word_count: 1500,
            target_word_count: 2200,
            within_range: false,
            deviation: -0.318,
            forbidden_word_hits: ['仿佛', '如同'],
            warnings: ['字数 1500 偏离 target 2200 达 -31.8%（阈值 ±15%）'],
          },
        }}
      />,
    );
    expect(
      screen.getByText(/字数 1500 偏离 target 2200 达 -31\.8%/),
    ).toBeInTheDocument();
    // 禁用词命中：仿佛、如同 — 用前缀匹配避免 strict mode 文本分割
    expect(screen.getByText(/禁用词命中：/)).toHaveTextContent(
      '禁用词命中：仿佛、如同',
    );
  });
});
