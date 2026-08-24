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
    ai_trace: 86,
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

  it('renders seven subscores with bars', () => {
    render(
      <QualityPanel
        chapterId="ch_1"
        report={baseReport}
        loading={false}
        error={null}
        onEvaluated={() => {}}
      />,
    );
    expect(screen.getByTestId('quality-subscores').children.length).toBe(7);
    expect(screen.getByTestId('quality-sub-plot')).toBeInTheDocument();
    const plotFill = screen.getByTestId('quality-sub-plot').querySelector(
      '.quality-subscore__fill',
    ) as HTMLElement;
    expect(plotFill).toBeTruthy();
    expect(plotFill.style.width).toBe('90%'); // plot=90
  });

  it('renders ai_trace subscore with label "AI 痕迹"', () => {
    render(
      <QualityPanel
        chapterId="ch_1"
        report={baseReport}
        loading={false}
        error={null}
        onEvaluated={() => {}}
      />,
    );
    expect(screen.getByTestId('quality-sub-ai_trace')).toBeInTheDocument();
    expect(screen.getByTestId('quality-sub-ai_trace')).toHaveTextContent('AI 痕迹');
    const fill = screen.getByTestId('quality-sub-ai_trace').querySelector(
      '.quality-subscore__fill',
    ) as HTMLElement;
    expect(fill.style.width).toBe('86%'); // ai_trace=86
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

  // ---------- V1.4：参照系消费可观测 + enforce 改稿引导 ----------

  it('V1.4: 缺 reference_consumption / revision_guidance 时不渲染区块', () => {
    render(
      <QualityPanel
        chapterId="ch_1"
        report={baseReport}
        loading={false}
        error={null}
        onEvaluated={() => {}}
      />,
    );
    expect(screen.queryByTestId('reference-consumption')).toBeNull();
    expect(screen.queryByTestId('revision-guidance')).toBeNull();
  });

  it('V1.4: qualityGateCheckpoint 携带 reference_consumption.files>0 时渲染参照系区块', () => {
    render(
      <QualityPanel
        chapterId="ch_1"
        report={baseReport}
        loading={false}
        error={null}
        onEvaluated={() => {}}
        qualityGateCheckpoint={{
          blocked: false,
          mode: 'report',
          reference_consumption: {
            source: 'project_refs_dir',
            files_count: 2,
            total_chars: 1234,
            files: [
              { name: 'ref_a.txt', chars: 1200 },
              { name: 'ref_b.txt', chars: 34 },
            ],
          },
          revision_guidance: [],
        }}
      />,
    );
    const block = screen.getByTestId('reference-consumption');
    expect(block).toBeInTheDocument();
    expect(block).toHaveTextContent('ref_a.txt');
    expect(block).toHaveTextContent('ref_b.txt');
    expect(block).toHaveTextContent('1,200 字');
    expect(block).toHaveTextContent('共 2 份');
    expect(block).toHaveTextContent('1,234 字');
  });

  it('V1.4: reference_consumption.files_count=0 时不渲染区块', () => {
    render(
      <QualityPanel
        chapterId="ch_1"
        report={baseReport}
        loading={false}
        error={null}
        onEvaluated={() => {}}
        qualityGateCheckpoint={{
          blocked: false,
          mode: 'report',
          reference_consumption: {
            source: 'project_refs_dir',
            files_count: 0,
            total_chars: 0,
            files: [],
          },
          revision_guidance: [],
        }}
      />,
    );
    expect(screen.queryByTestId('reference-consumption')).toBeNull();
  });

  it('V1.4: qualityGateCheckpoint.revision_guidance 非空时渲染改稿引导（guardrails + 低分子分）', () => {
    render(
      <QualityPanel
        chapterId="ch_1"
        report={baseReport}
        loading={false}
        error={null}
        onEvaluated={() => {}}
        qualityGateCheckpoint={{
          blocked: true,
          mode: 'enforce',
          reference_consumption: {
            source: 'project_refs_dir',
            files_count: 0,
            total_chars: 0,
            files: [],
          },
          revision_guidance: [
            {
              dimension: 'guardrails',
              score: 0,
              threshold: 60,
              rule_hint: '不要让已死亡角色在本章发生 action/location/goal 等活跃状态变更',
              top_issues: [
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
              ],
            },
            {
              dimension: 'style',
              score: 55,
              threshold: 60,
              rule_hint: '替换高频套用词为角色专属表达',
              top_issues: [],
            },
          ],
        }}
      />,
    );
    const block = screen.getByTestId('revision-guidance');
    expect(block).toBeInTheDocument();
    expect(screen.getByTestId('revision-guardrails')).toBeInTheDocument();
    expect(screen.getByTestId('revision-style')).toBeInTheDocument();
    expect(block).toHaveTextContent('RULE_CHAR_DEAD_ACTIVE');
    expect(block).toHaveTextContent('当前分 55 / 阈值 60');
    expect(block).toHaveTextContent('替换高频套用词');
  });

  it('V1.4: qualityGateCheckpoint 优先于 report._meta.* 字段', () => {
    // 当两者都给时，以 checkpoint（更新值）为准；UI 直接展示。
    render(
      <QualityPanel
        chapterId="ch_1"
        report={baseReport}
        loading={false}
        error={null}
        onEvaluated={() => {}}
        qualityGateCheckpoint={{
          blocked: false,
          mode: 'report',
          reference_consumption: {
            source: 'project_refs_dir',
            files_count: 1,
            total_chars: 7,
            files: [{ name: 'from-checkpoint.txt', chars: 7 }],
          },
          revision_guidance: [],
        }}
      />,
    );
    expect(screen.getByTestId('reference-consumption')).toHaveTextContent(
      'from-checkpoint.txt',
    );
  });
});
