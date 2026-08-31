import { useCallback, useState } from 'react';
import { qualityApi } from '../api/endpoints';
import type {
  QualityIssue,
  QualityReport,
  ReferenceConsumption,
  RevisionGuidance,
} from '../api/types';
import { ApiError } from '../api/client';
import { ErrorBanner } from './ErrorBanner';
import { formatApiError } from '../utils/formatApiError';

type SubscoreKey =
  | 'plot'
  | 'character'
  | 'continuity'
  | 'style'
  | 'pacing'
  | 'foreshadowing'
  | 'ai_trace';

const SUBSCORE_KEYS: SubscoreKey[] = [
  'plot',
  'character',
  'continuity',
  'style',
  'pacing',
  'foreshadowing',
  'ai_trace',
];

const SUBSCORE_LABELS: Record<SubscoreKey, string> = {
  plot: '剧情',
  character: '角色',
  continuity: '连贯',
  style: '风格',
  pacing: '节奏',
  foreshadowing: '伏笔',
  ai_trace: 'AI 痕迹',
};

function overallClass(score: number): string {
  if (score < 70) return 'quality-overall quality-overall--low';
  if (score < 85) return 'quality-overall quality-overall--mid';
  return 'quality-overall quality-overall--high';
}

function severityClass(s: QualityIssue['severity']): string {
  if (s === 'error') return 'badge badge--chapter-failed';
  if (s === 'warning') return 'badge badge--chapter-running';
  return 'badge badge--chapter-planned';
}

function severityLabel(s: QualityIssue['severity']): string {
  if (s === 'error') return '阻断';
  if (s === 'warning') return '警告';
  return '提示';
}

interface Props {
  chapterId: string;
  report: QualityReport | null;
  loading: boolean;
  error: string | null;
  onEvaluated: (report: QualityReport) => void;
  /**
   * V1.4：quality_gate 节点的 checkpoint 暴露字段（chapter_detail 页面从选中 run 的
   * ``checkpoint_json['quality_gate']`` 读取后传入）。含 blocked / mode /
   * reference_consumption / revision_guidance。无选中 run 或无 quality_gate 节点时为 null。
   */
  qualityGateCheckpoint?: import('../api/types').QualityGateCheckpoint | null;
}

/**
 * QualityPanel —— 章节详情页「质量评估」面板（Sprint 6 下半 + V1.4）。
 *
 * - 顶部按钮「运行质量评估」触发 POST evaluate。
 * - 展示最新一份 QualityReport：overall 大数字（按 70/85 阈值着色）+ 六子分条形 +
 *   issues 列表（severity 着色徽章 + category + rule_id + message + suggestion）；
 *   category=='payoff' 的 issue 单独一组「爽感问题」。
 * - V1.4 扩展：
 *   - 「本章消费参照系」区块：列文件名 + 字符数（项目级 ``references/<pid>/*.txt``）。
 *   - 「改稿引导」区块（enforce 阻断时）：每条 dimension / score / threshold /
 *     rule_hint；维度='guardrails' 时直接展示阻断 rule 的可执行建议。
 * - 评分版本（_meta.scoring_version）作为小字脚注。
 *
 * 复杂度：O(issues.length) 渲染；issues 通常 < 几十条，无虚拟化需求。
 */
export function QualityPanel({
  chapterId,
  report,
  loading,
  error,
  onEvaluated,
  qualityGateCheckpoint,
}: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  const handleEvaluate = useCallback(async () => {
    setSubmitting(true);
    setSubmitErr(null);
    try {
      const rep = await qualityApi.evaluate(chapterId);
      onEvaluated(rep);
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        // 走统一 formatApiError 文案；前缀「评估失败」由本面板附加（语义化）。
        // 为保持口径与其它面板一致，去掉手动拼的「（status）：detail」。
        setSubmitErr(formatApiError(e));
      } else {
        setSubmitErr(e instanceof Error ? `评估失败：${e.message}` : '评估失败');
      }
    } finally {
      setSubmitting(false);
    }
  }, [chapterId, onEvaluated]);

  return (
    <div className="panel" data-testid="quality-panel">
      <div className="panel__title">
        质量评估
        <div style={{ flex: 1 }} />
        <button
          className="btn btn--sm btn--primary"
          disabled={submitting}
          onClick={() => void handleEvaluate()}
          data-testid="quality-evaluate-btn"
        >
          {submitting ? '评估中…' : '运行质量评估'}
        </button>
      </div>

      <ErrorBanner>{submitErr || error}</ErrorBanner>

      {loading ? (
        <div className="muted">加载评估中…</div>
      ) : !report ? (
        <div className="muted small">
          暂无评估报告。点击上方「运行质量评估」生成。
        </div>
      ) : (
        <QualityReportView
          report={report}
          referenceConsumption={
            qualityGateCheckpoint?.reference_consumption ??
            report.scores_json._meta.reference_consumption
          }
          revisionGuidance={
            qualityGateCheckpoint?.revision_guidance ??
            report.scores_json._meta.revision_guidance
          }
        />
      )}
    </div>
  );
}

function QualityReportView({
  report,
  referenceConsumption,
  revisionGuidance,
}: {
  report: QualityReport;
  /** V1.4：本章 quality 评估消费的参照文本清单（_meta.reference_consumption）。 */
  referenceConsumption: ReferenceConsumption | null | undefined;
  /** V1.4：enforce 改稿引导（checkpoint_json['quality_gate'].revision_guidance）。 */
  revisionGuidance: RevisionGuidance[] | null | undefined;
}) {
  const subscores = report.scores_json;
  const payoffIssues = report.issues_json.filter((i) => i.category === 'payoff');
  const otherIssues = report.issues_json.filter((i) => i.category !== 'payoff');

  return (
    <div data-testid="quality-report">
      {/* overall 大数字 + 阈值着色 */}
      <div className="quality-overall-row">
        <div className={overallClass(subscores.overall)} data-testid="quality-overall">
          {subscores.overall}
        </div>
        <div className="muted small">
          report_id: {report.report_id.slice(0, 12)}… · 生成于{' '}
          {new Date(report.created_at).toLocaleString()}
        </div>
      </div>

      {/* 六子分条形 */}
      <div className="quality-subscores" data-testid="quality-subscores">
        {SUBSCORE_KEYS.map((k) => {
          const v = subscores[k] ?? 0;
          return (
            <div key={k} className="quality-subscore" data-testid={`quality-sub-${k}`}>
              <div className="quality-subscore__label">{SUBSCORE_LABELS[k]}</div>
              <div className="quality-subscore__bar">
                <div
                  className="quality-subscore__fill"
                  style={{ width: `${Math.max(0, Math.min(100, v))}%` }}
                />
              </div>
              <div className="quality-subscore__value">{v}</div>
            </div>
          );
        })}
      </div>

      {/* Issues：分两组：爽感（payoff）单列 + 其它 */}
      {otherIssues.length > 0 ? (
        <div className="panel__section">
          <div className="panel__section-title">Issues（按严重度）</div>
          <ul className="quality-issues">
            {otherIssues.map((i, idx) => (
              <li key={`${i.rule_id}-${idx}`} className="quality-issue">
                <span className={severityClass(i.severity)} data-testid={`issue-sev-${i.severity}`}>
                  {severityLabel(i.severity)}
                </span>
                <span className="quality-issue__cat">{i.category}</span>
                <span className="quality-issue__rule">{i.rule_id}</span>
                <span className="quality-issue__msg">{i.message}</span>
                {i.suggestion ? (
                  <span className="quality-issue__suggest muted small">
                    💡 {i.suggestion}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {payoffIssues.length > 0 ? (
        <div
          className="panel__section"
          data-testid="quality-payoff-group"
        >
          <div className="panel__section-title">爽感问题（H-1 ~ H-5）</div>
          <ul className="quality-issues">
            {payoffIssues.map((i, idx) => (
              <li key={`payoff-${i.rule_id}-${idx}`} className="quality-issue">
                <span className={severityClass(i.severity)}>{severityLabel(i.severity)}</span>
                <span className="quality-issue__cat">{i.category}</span>
                <span className="quality-issue__rule">{i.rule_id}</span>
                <span className="quality-issue__msg">{i.message}</span>
                {i.suggestion ? (
                  <span className="quality-issue__suggest muted small">
                    💡 {i.suggestion}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="muted small" data-testid="quality-meta">
        scoring_version: {subscores._meta.scoring_version}
        {' · '}formula_hash: {subscores._meta.scoring_formula_hash.slice(0, 8)}
        {' · '}evaluated_at: {subscores._meta.evaluated_at}
      </div>

      {/* V1.4：参照系消费可观测。无消费时不渲染区块。 */}
      {referenceConsumption && referenceConsumption.files_count > 0 ? (
        <div
          className="panel__section"
          data-testid="reference-consumption"
        >
          <div className="panel__section-title">
            本章消费参照系（quality_gate）
          </div>
          <ul className="quality-issues">
            {referenceConsumption.files.map((f) => (
              <li key={f.name} className="quality-issue">
                <span className="quality-issue__rule">{f.name}</span>
                <span className="quality-issue__msg">
                  {f.chars.toLocaleString()} 字
                </span>
              </li>
            ))}
          </ul>
          <div className="muted small" style={{ marginTop: 4 }}>
            来源：项目 references 目录 · 共{' '}
            {referenceConsumption.files_count} 份 ·{' '}
            {referenceConsumption.total_chars.toLocaleString()} 字
          </div>
        </div>
      ) : null}

      {/* V1.4：enforce 改稿引导（enforce 阻断时由后端写入；report 模式可能为空）。
          仅在非空时渲染；guardrails 维度的 rule_hint 直接来自阻断 rule。 */}
      {revisionGuidance && revisionGuidance.length > 0 ? (
        <div
          className="panel__section"
          data-testid="revision-guidance"
        >
          <div className="panel__section-title">
            改稿引导（enforce 阻断时由后端生成）
          </div>
          <ul className="quality-issues">
            {revisionGuidance.map((g, idx) => (
              <li
                key={`${g.dimension}-${idx}`}
                className="quality-issue"
                data-testid={`revision-${g.dimension}`}
              >
                <span className="badge badge--chapter-failed">{g.dimension}</span>
                <span className="quality-issue__msg">
                  {g.dimension === 'guardrails'
                    ? `当前阻断 rule：${g.top_issues[0]?.rule_id ?? '-'}`
                    : `当前分 ${g.score} / 阈值 ${g.threshold}`}
                </span>
                <span className="quality-issue__suggest">
                  💡 {g.rule_hint}
                </span>
                {g.top_issues.length > 0 ? (
                  <ul
                    className="muted small"
                    style={{ marginTop: 4, paddingLeft: 16 }}
                  >
                    {g.top_issues.slice(0, 3).map((iss, iidx) => (
                      <li key={`${g.dimension}-${iidx}`}>
                        [{iss.severity}] {iss.rule_id} · {iss.message}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
