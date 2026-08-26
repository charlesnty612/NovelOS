// 审批卡片：当 workflow run PAUSED 在 Human 节点时展示。
// 支持两种 stage：
//   - chapter-review  → 显示 review_report（字数偏离 / 禁用词 / warnings）
//                        + V1.3 critic_report（LLM 评审员建议；advisory only）
//   - chapter-commit.high_risk_approval → 显示 changes 待审批条数
//
// chapter-review 分支有三态决议（对齐 PRD §59/§87 的「人工修改后重审」闭环）：
//   - 批准        → onApprove(true)
//   - 驳回        → onApprove(false)              （run FAILED，章节保持 DRAFTED）
//   - 驳回并改稿  → onApprove(false, {revise:true, note})（run FAILED(rejected-for-revision)，
//                    章节保持 DRAFTED，note 落 plan_json.revision_note，可改稿后重跑 write/review）
//
// 通过 props 注入决定 / 拒绝动作，由父组件负责调 resume API。

import { useState } from 'react';
import { ErrorBanner, InfoBanner } from './ErrorBanner';
import type {
  CriticIssue,
  CriticIssueCategory,
  CriticIssueSeverity,
  CriticReport,
} from '../api/types';

export interface ApproveOptions {
  /** 驳回并改稿（仅 chapter-review 分支）：run 以 FAILED(rejected-for-revision) 收尾 */
  revise?: boolean;
  /** 改稿意见，落 plan_json.revision_note 供下次 write 参考 */
  note?: string;
}

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
  onApprove: (approved: boolean, opts?: ApproveOptions) => Promise<void> | void;
}

interface ReviewReportErrorItem {
  rule_id?: string;
  severity?: string;
  message?: string;
}

interface ReviewReportShape {
  word_count?: number;
  target_word_count?: number;
  within_range?: boolean;
  deviation?: number;
  deviation_pct?: number;
  word_band?: { low?: number; high?: number };
  forbidden_word_hits?: string[];
  warnings?: string[];
  /** V3.7：字数严重级条目（>±30% 外缘），报告型展示给作者，不阻断 run。 */
  errors?: ReviewReportErrorItem[];
}

const CRITIC_CATEGORY_LABEL: Record<CriticIssueCategory, string> = {
  pacing: '节奏',
  character: '人物',
  logic: '逻辑',
  foreshadowing: '伏笔',
  ai_flavor: 'AI 腔',
  other: '其他',
};

const CRITIC_SEVERITY_LABEL: Record<CriticIssueSeverity, string> = {
  high: '高',
  medium: '中',
  low: '低',
};

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
  const [pendingRevise, setPendingRevise] = useState(false);
  const [reviseNote, setReviseNote] = useState('');

  const reviewReport = pausePayload['review_report'] as
    | ReviewReportShape
    | undefined;
  // V1.3：critic_report 仅作建议性参考；critic_status != 'ok' 时 critic_report 为 null，
  // UI 仅显示弱提示，不影响审批按钮可用性。
  const criticStatus = (pausePayload['critic_status'] as string | undefined) ?? 'skipped';
  const criticReport = pausePayload['critic_report'] as CriticReport | null | undefined;

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

      {stage === 'chapter-review' ? (
        <CriticReportSummary
          status={criticStatus}
          report={criticReport ?? null}
        />
      ) : null}

      {stage === 'chapter-commit.high_risk_approval' ? (
        <HighRiskSummary
          count={highRiskChangeCount}
          pausePayload={pausePayload}
        />
      ) : null}

      <InfoBanner>
        {stage === 'chapter-review'
          ? '请作者审查后批准（继续推进到 REVIEWED）；驳回则该 run 结束（FAILED）、章节保持 DRAFTED，可改稿后重新发起写正文/审校；「驳回并改稿」会附上意见（落 plan_json.revision_note），同样保持 DRAFTED，改稿后重新发起写正文/审校即可。'
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
        {stage === 'chapter-review' ? (
          <button
            className="btn"
            disabled={submitting}
            data-testid="approval-revise"
            onClick={async () => {
              setPendingRevise(true);
              try {
                await onApprove(false, {
                  revise: true,
                  note: reviseNote.trim() || undefined,
                });
              } finally {
                setPendingRevise(false);
              }
            }}
          >
            {submitting && pendingRevise ? '提交中…' : '驳回并改稿'}
          </button>
        ) : null}
      </div>

      {stage === 'chapter-review' ? (
        <div style={{ marginTop: 8 }}>
          <label
            className="muted small"
            htmlFor="approval-revise-note"
          >
            改稿意见（可选，落 plan_json.revision_note 供下次写正文参考）
          </label>
          <textarea
            id="approval-revise-note"
            data-testid="approval-revise-note"
            value={reviseNote}
            onChange={(e) => setReviseNote(e.target.value)}
            rows={2}
            disabled={submitting}
            style={{
              width: '100%',
              marginTop: 4,
              padding: 6,
              borderRadius: 6,
              border: '1px solid var(--color-border-strong)',
              fontFamily: 'inherit',
              fontSize: 12,
            }}
          />
        </div>
      ) : null}
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
      {Array.isArray(report['errors']) &&
      (report['errors'] as ReviewReportErrorItem[]).length > 0 ? (
        <div
          style={{
            marginTop: 6,
            padding: '6px 8px',
            borderLeft: '3px solid var(--color-danger, #c62828)',
            background: '#fff5f5',
            borderRadius: 4,
          }}
          data-testid="review-report-errors"
        >
          <div
            className="small"
            style={{ color: 'var(--color-danger, #c62828)', fontWeight: 600 }}
          >
            严重（{(report['errors'] as ReviewReportErrorItem[]).length}）
          </div>
          <ul
            style={{
              margin: '4px 0 0 18px',
              padding: 0,
              color: 'var(--color-danger, #c62828)',
            }}
          >
            {(report['errors'] as ReviewReportErrorItem[]).map((e, i) => (
              <li key={`err-${i}`} data-rule-id={e.rule_id ?? ''}>
                {e.message ?? String(e)}
              </li>
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

// V1.3：LLM 评审员（advisory only）。
// - status='ok' 且 report 非空 → 渲染总评 / 亮点 / 问题（带分类 + severity 徽标）。
// - 其他（failed / skipped / 报告缺失）→ 显示弱提示「AI 审稿不可用」，不阻塞审批按钮。
function CriticReportSummary({
  status,
  report,
}: {
  status: string;
  report: CriticReport | null;
}) {
  if (status !== 'ok' || !report) {
    return (
      <div
        className="muted small"
        style={{ marginTop: 6 }}
        data-testid="critic-report-degraded"
      >
        AI 审稿不可用（不影响审批）
      </div>
    );
  }

  const issues = Array.isArray(report.issues) ? report.issues : [];
  const strengths = Array.isArray(report.strengths) ? report.strengths : [];

  return (
    <div
      style={{ marginTop: 8 }}
      data-testid="critic-report"
      data-issue-count={issues.length}
    >
      <div className="muted small">AI 审稿（建议性，仅供参考）</div>
      {report.overall_comment ? (
        <div style={{ marginTop: 4 }}>{report.overall_comment}</div>
      ) : null}
      {strengths.length > 0 ? (
        <div style={{ marginTop: 6 }}>
          <div className="muted small">亮点</div>
          <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
            {strengths.map((s, i) => (
              <li key={`s-${i}`}>{s}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {issues.length > 0 ? (
        <div style={{ marginTop: 6 }}>
          <div className="muted small">问题（{issues.length}）</div>
          <ul style={{ listStyle: 'none', padding: 0, margin: '4px 0 0 0' }}>
            {issues.map((it, i) => (
              <CriticIssueRow key={`i-${i}`} issue={it} />
            ))}
          </ul>
        </div>
      ) : (
        <div className="muted small" style={{ marginTop: 4 }}>
          未发现明显问题
        </div>
      )}
    </div>
  );
}

function CriticIssueRow({ issue }: { issue: CriticIssue }) {
  const category = (issue.category ?? 'other') as CriticIssueCategory;
  const severity = (issue.severity ?? 'low') as CriticIssueSeverity;
  return (
    <li
      data-testid="critic-issue"
      data-severity={severity}
      data-category={category}
      style={{
        borderLeft: '3px solid var(--color-border-strong)',
        paddingLeft: 8,
        marginTop: 4,
      }}
    >
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <span
          className="badge"
          data-testid="critic-issue-severity"
          title={`severity=${severity}`}
        >
          {CRITIC_SEVERITY_LABEL[severity] ?? severity}
        </span>
        <span
          className="badge"
          data-testid="critic-issue-category"
          title={`category=${category}`}
        >
          {CRITIC_CATEGORY_LABEL[category] ?? category}
        </span>
      </div>
      {issue.quote ? (
        <div
          className="muted small"
          data-testid="critic-issue-quote"
          style={{ marginTop: 2 }}
        >
          「{issue.quote}」
        </div>
      ) : null}
      {issue.suggestion ? (
        <div data-testid="critic-issue-suggestion" style={{ marginTop: 2 }}>
          {issue.suggestion}
        </div>
      ) : null}
    </li>
  );
}
