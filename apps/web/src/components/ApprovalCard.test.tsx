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

  it('chapter-review 分支 InfoBanner 文案明确「驳回 → FAILED + 章节保持 DRAFTED」及「驳回并改稿」', () => {
    // Sprint 5 review F3：驳回语义文案必须明确 run 终止/章节保持/允许重新发起；revise 闭环。
    render(<ApprovalCard {...baseProps} />);
    expect(
      screen.getByText(
        /驳回则该 run 结束（FAILED）、章节保持 DRAFTED，可改稿后重新发起写正文\/审校/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/「驳回并改稿」会附上意见（落 plan_json\.revision_note）/),
    ).toBeInTheDocument();
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
    expect(screen.getByTestId('approval-revise')).toBeDisabled();
  });

  it('chapter-review 分支渲染第三个按钮「驳回并改稿」+ 改稿意见输入框', () => {
    render(<ApprovalCard {...baseProps} />);
    expect(
      screen.getByTestId('approval-revise'),
    ).toBeInTheDocument();
    expect(screen.getByText('驳回并改稿')).toBeInTheDocument();
    expect(screen.getByTestId('approval-revise-note')).toBeInTheDocument();
  });

  it('非 chapter-review 分支不渲染「驳回并改稿」按钮', () => {
    render(
      <ApprovalCard
        {...baseProps}
        stage="chapter-commit.high_risk_approval"
        message="高风险变更待审批"
        pausePayload={{ stage: 'chapter-commit.high_risk_approval', message: '高风险变更待审批' }}
      />,
    );
    expect(screen.queryByTestId('approval-revise')).not.toBeInTheDocument();
  });

  it('点击「驳回并改稿」触发 onApprove(false, {revise:true, note})', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalCard {...baseProps} onApprove={onApprove} />);
    fireEvent.change(screen.getByTestId('approval-revise-note'), {
      target: { value: '禁用词命中，请改写后重审' },
    });
    fireEvent.click(screen.getByTestId('approval-revise'));
    await waitFor(() =>
      expect(onApprove).toHaveBeenCalledWith(false, {
        revise: true,
        note: '禁用词命中，请改写后重审',
      }),
    );
  });

  it('点击「驳回并改稿」且意见为空 → note 为 undefined', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalCard {...baseProps} onApprove={onApprove} />);
    fireEvent.click(screen.getByTestId('approval-revise'));
    await waitFor(() =>
      expect(onApprove).toHaveBeenCalledWith(false, {
        revise: true,
        note: undefined,
      }),
    );
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
    // 该 warning 文本同时出现在 warnings 区块与审校建议列表行内
    expect(
      screen.getAllByText(/字数 1500 偏离 target 2200 达 -31\.8%/).length,
    ).toBeGreaterThanOrEqual(1);
    // 禁用词命中：仿佛、如同 — 用前缀匹配避免 strict mode 文本分割
    expect(screen.getByText(/禁用词命中：/)).toHaveTextContent(
      '禁用词命中：仿佛、如同',
    );
  });

  // -------- V1.3 critic_report（LLM 评审员，advisory only）--------

  it('critic_status=ok 且有 critic_report 时渲染总评 / 亮点 / 问题列表（含分类 + severity 徽标）', () => {
    render(
      <ApprovalCard
        {...baseProps}
        pausePayload={{
          ...baseProps.pausePayload,
          critic_status: 'ok',
          critic_report: {
            schema_version: 'critic-report.v1',
            prompt_version: 'critic:v1',
            chapter_id: 'ch_a',
            overall_comment: '节奏整体尚可，但末段伏笔推进不足。',
            strengths: ['女主情绪位移有锚点'],
            issues: [
              {
                category: 'foreshadowing',
                severity: 'high',
                quote: '「好」的时候，答得太轻',
                suggestion: '让男主主动提一句父亲遗物中的玉佩。',
              },
              {
                category: 'ai_flavor',
                severity: 'low',
                quote: '竹影斜斜地落在青石地砖上',
                suggestion: '删除或换成具体动作描写。',
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByTestId('critic-report')).toBeInTheDocument();
    expect(
      screen.getByText(/节奏整体尚可，但末段伏笔推进不足/),
    ).toBeInTheDocument();
    expect(screen.getByText(/女主情绪位移有锚点/)).toBeInTheDocument();
    // issue 数量徽标
    expect(screen.getByText(/问题（2）/)).toBeInTheDocument();
    // issue 行 + 引用 + 建议（引用/建议正文同时出现在 critic 卡片与
    // 审校建议列表行内，故用 getAllByText 允许多处匹配）
    expect(screen.getAllByTestId('critic-issue')).toHaveLength(2);
    expect(
      screen.getAllByText(/「好」的时候，答得太轻/).length,
    ).toBeGreaterThanOrEqual(1);
    expect(
      screen.getAllByText(/让男主主动提一句父亲遗物中的玉佩/).length,
    ).toBeGreaterThanOrEqual(1);
    // severity / category 徽标渲染了「高」「伏笔」「低」「AI 腔」
    expect(screen.getAllByText('高').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('伏笔').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('低').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('AI 腔').length).toBeGreaterThanOrEqual(1);
  });

  it('critic_status=failed 时显示「AI 审稿不可用」弱提示，不影响审批按钮', () => {
    render(
      <ApprovalCard
        {...baseProps}
        pausePayload={{
          ...baseProps.pausePayload,
          critic_status: 'failed',
          critic_report: null,
        }}
      />,
    );
    expect(screen.getByTestId('critic-report-degraded')).toBeInTheDocument();
    expect(
      screen.getByText(/AI 审稿不可用/),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('critic-report')).not.toBeInTheDocument();
    // 审批按钮仍可用
    expect(screen.getByTestId('approval-approve')).not.toBeDisabled();
    expect(screen.getByTestId('approval-reject')).not.toBeDisabled();
  });

  it('critic_status=skipped 或缺省时同样降级显示，不渲染 critic_report', () => {
    render(<ApprovalCard {...baseProps} />);
    expect(screen.getByTestId('critic-report-degraded')).toBeInTheDocument();
    expect(screen.queryByTestId('critic-report')).not.toBeInTheDocument();
  });

  it('critic_report.issues=[] 且 strengths=[] 时显示「未发现明显问题」', () => {
    render(
      <ApprovalCard
        {...baseProps}
        pausePayload={{
          ...baseProps.pausePayload,
          critic_status: 'ok',
          critic_report: {
            overall_comment: '本章表现稳定。',
            strengths: [],
            issues: [],
          },
        }}
      />,
    );
    expect(screen.getByText(/未发现明显问题/)).toBeInTheDocument();
  });

  it('commit.high_risk_approval 不渲染 critic_report 相关节点', () => {
    render(
      <ApprovalCard
        {...baseProps}
        stage="chapter-commit.high_risk_approval"
        message="高风险"
        pausePayload={{
          stage: 'chapter-commit.high_risk_approval',
          message: '高风险',
          critic_status: 'ok',
          critic_report: { overall_comment: 'x', strengths: [], issues: [] },
        }}
      />,
    );
    expect(screen.queryByTestId('critic-report')).not.toBeInTheDocument();
    expect(screen.queryByTestId('critic-report-degraded')).not.toBeInTheDocument();
  });

  // -------- 「按建议修改」按钮 --------
  // 来源主控任务：以实际 review_report / critic_report 数据结构为准。
  // 建议来源优先级：critic_report.issues[].suggestion → review_report.warnings[] → review_report.errors[].message

  /** 构造带 critic + warnings 的 pausePayload，触发按钮渲染 */
  function withSuggestions(extra?: Partial<{ warnings: string[]; errors: { rule_id: string; severity: string; message: string }[] }>) {
    return {
      ...baseProps.pausePayload,
      critic_status: 'ok',
      critic_report: {
        overall_comment: '总体可接受',
        strengths: [],
        issues: [
          {
            category: 'foreshadowing',
            severity: 'high',
            quote: '「好」的时候，答得太轻',
            suggestion: '让男主主动提一句父亲遗物中的玉佩。',
          },
          {
            category: 'ai_flavor',
            severity: 'low',
            quote: '竹影斜斜地落在青石地砖上',
            suggestion: '删除或换成具体动作描写。',
          },
        ],
      },
      review_report: {
        ...(baseProps.pausePayload['review_report'] as Record<string, unknown>),
        warnings: extra?.warnings ?? [],
        errors: extra?.errors ?? [],
      },
    };
  }

  it('chapter-review 含 critic_report.issues 时渲染「按建议修改」按钮与建议列表', () => {
    render(
      <ApprovalCard
        {...baseProps}
        pausePayload={withSuggestions() as Record<string, unknown>}
      />,
    );
    expect(screen.getByTestId('approval-apply-suggestions')).toBeInTheDocument();
    expect(screen.getByText('按建议修改')).toBeInTheDocument();
    // 两条 critic suggestion 都作为 checkbox 行渲染
    expect(screen.getAllByTestId('approval-suggestion-row')).toHaveLength(2);
    // 默认全选 → data-selected-count=2
    expect(screen.getByTestId('approval-suggestions')).toHaveAttribute(
      'data-selected-count',
      '2',
    );
  });

  it('点击「按建议修改」以 revise:true + note（含所有建议文本）调用 onApprove', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(
      <ApprovalCard
        {...baseProps}
        onApprove={onApprove}
        pausePayload={withSuggestions() as Record<string, unknown>}
      />,
    );
    fireEvent.click(screen.getByTestId('approval-apply-suggestions'));
    await waitFor(() =>
      expect(onApprove).toHaveBeenCalledWith(false, {
        revise: true,
        note: '让男主主动提一句父亲遗物中的玉佩。\n删除或换成具体动作描写。',
      }),
    );
  });

  it('「按建议修改」合并意见框补充内容：note = 勾选建议 + 意见框文本', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(
      <ApprovalCard
        {...baseProps}
        onApprove={onApprove}
        pausePayload={withSuggestions() as Record<string, unknown>}
      />,
    );
    fireEvent.change(screen.getByTestId('approval-revise-note'), {
      target: { value: '全书以人民币支付，银元仅作计价单位。' },
    });
    fireEvent.click(screen.getByTestId('approval-apply-suggestions'));
    await waitFor(() =>
      expect(onApprove).toHaveBeenCalledWith(false, {
        revise: true,
        note: '让男主主动提一句父亲遗物中的玉佩。\n删除或换成具体动作描写。\n\n全书以人民币支付，银元仅作计价单位。',
      }),
    );
  });

  it('取消勾选部分建议后，「按建议修改」只应用勾选的建议', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(
      <ApprovalCard
        {...baseProps}
        onApprove={onApprove}
        pausePayload={withSuggestions() as Record<string, unknown>}
      />,
    );
    const checkboxes = screen.getAllByTestId('approval-suggestion-checkbox');
    expect(checkboxes).toHaveLength(2);
    // 取消第二条
    fireEvent.click(checkboxes[1]!);
    // 选中计数 = 1
    expect(screen.getByTestId('approval-suggestions')).toHaveAttribute(
      'data-selected-count',
      '1',
    );
    fireEvent.click(screen.getByTestId('approval-apply-suggestions'));
    await waitFor(() =>
      expect(onApprove).toHaveBeenCalledWith(false, {
        revise: true,
        note: '让男主主动提一句父亲遗物中的玉佩。',
      }),
    );
  });

  it('submitting=true 时「按建议修改」按钮被禁用', () => {
    render(
      <ApprovalCard
        {...baseProps}
        submitting={true}
        pausePayload={withSuggestions() as Record<string, unknown>}
      />,
    );
    expect(screen.getByTestId('approval-apply-suggestions')).toBeDisabled();
  });

  // -------- 「应用此条」（逐条按建议修改） --------

  it('每条建议行有「应用此条」按钮', () => {
    render(
      <ApprovalCard
        {...baseProps}
        pausePayload={withSuggestions() as Record<string, unknown>}
      />,
    );
    expect(screen.getAllByTestId('approval-apply-suggestion')).toHaveLength(2);
  });

  it('点击某条「应用此条」→ 仅以该条建议文本调用 onApprove(revise:true)', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(
      <ApprovalCard
        {...baseProps}
        onApprove={onApprove}
        pausePayload={withSuggestions() as Record<string, unknown>}
      />,
    );
    const applyBtns = screen.getAllByTestId('approval-apply-suggestion');
    // 点第一条
    fireEvent.click(applyBtns[0]!);
    await waitFor(() =>
      expect(onApprove).toHaveBeenCalledWith(false, {
        revise: true,
        note: '让男主主动提一句父亲遗物中的玉佩。',
      }),
    );
  });

  it('submitting=true 时「应用此条」按钮被禁用', () => {
    render(
      <ApprovalCard
        {...baseProps}
        submitting={true}
        pausePayload={withSuggestions() as Record<string, unknown>}
      />,
    );
    const applyBtns = screen.getAllByTestId('approval-apply-suggestion');
    applyBtns.forEach((b) => expect(b).toBeDisabled());
  });
});
