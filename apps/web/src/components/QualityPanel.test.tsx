import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QualityPanel } from './QualityPanel';
import { qualityApi } from '../api/endpoints';
import type { QualityReport } from '../api/types';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    qualityApi: {
      latest: vi.fn(),
      evaluate: vi.fn(),
      listByProject: vi.fn(),
    },
  };
});

const baseReport: QualityReport = {
  report_id: 'qr_abcdef123456',
  project_id: 'prj_1',
  chapter_id: 'ch_1',
  commit_id: null,
  run_id: 'wfr_xxx',
  overall: 88,
  scores_json: {
    overall: 88,
    plot: 90,
    character: 85,
    continuity: 92,
    style: 80,
    pacing: 75,
    foreshadowing: 70,
    _meta: {
      scoring_version: 'quality-scoring-v0',
      llm_judge: 'deferred',
      evaluated_at: '2026-08-23T10:00:00Z',
      scoring_formula_hash: '0123456789abcdef',
    },
  },
  issues_json: [
    {
      severity: 'warning',
      category: 'style',
      location: 'ch_1',
      rule_id: 'RULE_STYLE_REPEATED_TRIGRAMS',
      message: '三字重复率偏高',
      suggestion: '把「然而」改成「不过」',
      evidence_refs: null,
      judge_trace: null,
    },
    {
      severity: 'error',
      category: 'character_contradiction',
      location: 'ch_1',
      rule_id: 'RULE_CHAR_DEAD_ACTIVE',
      message: '已死亡角色仍在活动',
      suggestion: null,
      evidence_refs: null,
      judge_trace: null,
    },
    {
      severity: 'warning',
      category: 'payoff',
      location: 'ch_1',
      rule_id: 'RULE_H1_NO_END_HOOK',
      message: '末段无钩子',
      suggestion: '加一句悬念',
      evidence_refs: null,
      judge_trace: null,
    },
  ],
  created_at: '2026-08-23T10:00:00Z',
};

describe('QualityPanel', () => {
  it('renders overall score and applies high color (>85)', () => {
    render(
      <QualityPanel
        chapterId="ch_1"
        report={baseReport}
        loading={false}
        error={null}
        onEvaluated={() => {}}
      />,
    );
    expect(screen.getByTestId('quality-overall')).toHaveTextContent('88');
    expect(screen.getByTestId('quality-overall').className).toMatch(
      /quality-overall--high/,
    );
    expect(screen.getByTestId('quality-meta')).toHaveTextContent(
      'scoring_version: quality-scoring-v0',
    );
  });

  it('overall < 70 uses low color; 70-85 uses mid color', () => {
    const low: QualityReport = {
      ...baseReport,
      overall: 65,
      scores_json: { ...baseReport.scores_json, overall: 65 },
    };
    const { rerender } = render(
      <QualityPanel
        chapterId="ch_1"
        report={low}
        loading={false}
        error={null}
        onEvaluated={() => {}}
      />,
    );
    expect(screen.getByTestId('quality-overall').className).toMatch(
      /quality-overall--low/,
    );

    const mid: QualityReport = {
      ...baseReport,
      overall: 78,
      scores_json: { ...baseReport.scores_json, overall: 78 },
    };
    rerender(
      <QualityPanel
        chapterId="ch_1"
        report={mid}
        loading={false}
        error={null}
        onEvaluated={() => {}}
      />,
    );
    expect(screen.getByTestId('quality-overall').className).toMatch(
      /quality-overall--mid/,
    );
  });

  it('renders six subscores with bars', () => {
    render(
      <QualityPanel
        chapterId="ch_1"
        report={baseReport}
        loading={false}
        error={null}
        onEvaluated={() => {}}
      />,
    );
    expect(screen.getByTestId('quality-subscores').children.length).toBe(6);
    expect(screen.getByTestId('quality-sub-plot')).toBeInTheDocument();
    const plotFill = screen.getByTestId('quality-sub-plot').querySelector(
      '.quality-subscore__fill',
    ) as HTMLElement;
    expect(plotFill).toBeTruthy();
    expect(plotFill.style.width).toBe('90%'); // plot=90
  });

  it('groups payoff issues under 爽感 section; others under Issues', () => {
    render(
      <QualityPanel
        chapterId="ch_1"
        report={baseReport}
        loading={false}
        error={null}
        onEvaluated={() => {}}
      />,
    );
    const payoffGroup = screen.getByTestId('quality-payoff-group');
    expect(payoffGroup).toBeInTheDocument();
    expect(payoffGroup).toHaveTextContent('RULE_H1_NO_END_HOOK');
    // non-payoff issues should NOT be inside payoff group
    expect(payoffGroup).not.toHaveTextContent('RULE_CHAR_DEAD_ACTIVE');
    // non-payoff issues exist somewhere on the page
    expect(
      screen.getAllByText(/RULE_CHAR_DEAD_ACTIVE|RULE_STYLE_REPEATED_TRIGRAMS/i).length,
    ).toBeGreaterThan(0);
  });

  it('shows empty state when report is null', () => {
    render(
      <QualityPanel
        chapterId="ch_1"
        report={null}
        loading={false}
        error={null}
        onEvaluated={() => {}}
      />,
    );
    expect(screen.getByText(/暂无评估报告/)).toBeInTheDocument();
  });

  it('evaluate button triggers API + onEvaluated', async () => {
    const onEvaluated = vi.fn();
    vi.mocked(qualityApi.evaluate).mockResolvedValue(baseReport);
    render(
      <QualityPanel
        chapterId="ch_1"
        report={null}
        loading={false}
        error={null}
        onEvaluated={onEvaluated}
      />,
    );
    fireEvent.click(screen.getByTestId('quality-evaluate-btn'));
    await waitFor(() => expect(qualityApi.evaluate).toHaveBeenCalledWith('ch_1'));
    await waitFor(() => expect(onEvaluated).toHaveBeenCalledWith(baseReport));
  });
});
