// 审批卡片：当 workflow run PAUSED 在 Human 节点时展示。
// 支持两种 stage：
//   - chapter-review  → 显示 review_report（字数偏离 / 禁用词 / warnings）
//   - chapter-commit.high_risk_approval → 显示 changes 待审批条数
//
// 通过 props 注入决定 / 拒绝动作，由父组件负责调 resume API。

import { useState } from 'react';
import { ErrorBanner, InfoBanner } from './ErrorBanner';

export interface ApprovalCardProps {
  runId: string;
  stage: string;
  message: string;
  pausePayload: Record<string, unknown>;
  /** 高风险变更条数（仅 commit.high_risk_approval 节点相关） */
  highRiskChangeCount: number;
  /** 是否正在提交 */
  submitting: boolean;
  /** 当前错误信息（resume 失败时显示） */
  error: string | null;
  /** 提交审批（approved=true/false）；由父组件负责调 /runs/{id}/resume */
  onApprove: (approved: boolean) => Promise<void> | void;
}

interface ReviewReportShape {
  word_count?: number;
  target_word_count?: number;
  within_range?: boolean;
  deviation?: number;
  forbidden_word_hits?: string[];
  warnings?: string[];
}

export function ApprovalCard(props: ApprovalCardProps) {
  const {
    stage,
    message,
    pausePayload,
    highRiskChangeCount,
    submitting,
    error,
    onApprove,
  } = props;
  const [pendingApprove, setPendingApprove] = useState<boolean | null>(null);

  const reviewReport = pausePayload['review_report'] as
    | ReviewReportShape
    | undefined;

  return (
    <div
      className="card"
      style={{
        borderLeft: '4px solid var(--color-warn)',
        background: '#fffdf6',
      }}
      data-testid="approval-card"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="badge badge--chapter-paused">PAUSED</span>
        <span className="muted small">stage: {stage}</span>
      </div>
      <div style={{ marginTop: 6, fontWeight: 600 }}>{message}</div>

      {stage === 'chapter-review' && reviewReport ? (
        <ReviewReportSummary report={reviewReport} />
      ) : null}

      {stage === 'chapter-commit.high_risk_approval' ? (
        <HighRiskSummary
          count={highRiskChangeCount}
          pausePayload={pausePayload}
        />
      ) : null}

      <InfoBanner>
        {stage === 'chapter-review'
          ? '请作者审查后批准（继续推进到 REVIEWED）或驳回则该 run 结束（FAILED），章节保持 DRAFTED，可改稿后重新发起写正文/审校。'
          : stage === 'chapter-commit.high_risk_approval'
          ? 'Observer 检测到高风险 / definition / world_kind=rule 变更，请人工审批。'
          : '请人工决议以恢复 workflow。'}
      </InfoBanner>

      <ErrorBanner>{error}</ErrorBanner>

      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <button
          className="btn btn--primary"
          disabled={submitting}
          data-testid="approval-approve"
          onClick={async () => {
            setPendingApprove(true);
            try {
              await onApprove(true);
            } finally {
              setPendingApprove(null);
            }
          }}
        >
          {submitting && pendingApprove === true ? '提交中…' : '批准'}
        </button>
        <button
          className="btn btn--danger"
          disabled={submitting}
          data-testid="approval-reject"
          onClick={async () => {
            setPendingApprove(false);
            try {
              await onApprove(false);
            } finally {
              setPendingApprove(null);
            }
          }}
        >
          {submitting && pendingApprove === false ? '提交中…' : '驳回'}
        </button>
      </div>
    </div>
  );
}

function ReviewReportSummary({
  report,
}: {
  report: ReviewReportShape;
}) {
  return (
    <div style={{ marginTop: 8 }}>
      <div className="form-grid">
        <Field label="字数" value={String(report['word_count'] ?? '—')} />
        <Field
          label="目标"
          value={String(report['target_word_count'] ?? '—')}
        />
        <Field
          label="是否达标"
          value={report['within_range'] ? '是' : '否'}
        />
        <Field
          label="偏差"
          value={
            typeof report['deviation'] === 'number'
              ? `${(Number(report['deviation']) * 100).toFixed(1)}%`
              : '—'
          }
        />
      </div>
      {Array.isArray(report['warnings']) &&
      (report['warnings'] as string[]).length > 0 ? (
        <div style={{ marginTop: 6 }}>
          <div className="muted small">warnings</div>
          <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
            {(report['warnings'] as string[]).map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {Array.isArray(report['forbidden_word_hits']) &&
      (report['forbidden_word_hits'] as string[]).length > 0 ? (
        <div className="muted small" style={{ marginTop: 4 }}>
          禁用词命中：
          {(report['forbidden_word_hits'] as string[]).join('、')}
        </div>
      ) : null}
    </div>
  );
}

function HighRiskSummary({
  count,
  pausePayload,
}: {
  count: number;
  pausePayload: Record<string, unknown>;
}) {
  const changes = pausePayload['changes'] as
    | { character_changes?: unknown[]; world_changes?: unknown[] }
    | undefined;
  return (
    <div style={{ marginTop: 8 }}>
      <div className="form-grid">
        <Field
          label="待审批变更条数"
          value={`${count} 项`}
        />
        <Field
          label="character_changes"
          value={String(changes?.character_changes?.length ?? 0)}
        />
        <Field
          label="world_changes"
          value={String(changes?.world_changes?.length ?? 0)}
        />
      </div>
      <div className="muted small" style={{ marginTop: 4 }}>
        涉及 character_changes / world_changes。批准会继续 COMMIT 流程，驳回则 workflow 进入 FAILED。
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="muted small">{label}</div>
      <div>{value}</div>
    </div>
  );
}
