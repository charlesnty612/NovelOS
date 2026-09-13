// 运行记录面板（从 ChapterDetailPage 拆出，2026-09-13 前端批次 C 信息架构重构）。
//
// 内容：本章 run 列表（selected 高亮）、选中 run 的节点时间线、PAUSED 时的审批卡片
// （ApprovalCard）与深度二审报告分栏。

import { useEffect, useState } from 'react';
import type { WorkflowRun } from '../../api/types';
import { ApprovalCard } from '../../components/ApprovalCard';
import { ErrorBanner, InfoBanner } from '../../components/ErrorBanner';
import { EmptyState } from '../../components/EmptyState';
import { WorkflowRunStatusBadge } from '../../components/ChapterStatusBadge';
import { formatDateTime, formatJson } from '../../utils/format';
import {
  countHighRiskChanges,
  isCancelledByUserNode,
  isRejectedForRevisionRun,
  isRejectedRun,
} from '../../utils/chapterState';

export function ChapterRunPanel({
  runs,
  selectedRunId,
  onSelect,
  detailRun,
  detailLoading,
  detailError,
  pollError,
  pausePayload,
  submitting,
  onApprove,
}: {
  runs: WorkflowRun[];
  selectedRunId: string | null;
  onSelect: (id: string) => void;
  detailRun: WorkflowRun | null;
  detailLoading: boolean;
  detailError: string | null;
  pollError: string | null;
  pausePayload: Record<string, unknown> | undefined;
  submitting: boolean;
  onApprove: (
    approved: boolean,
    opts?: { revise?: boolean; note?: string },
  ) => Promise<void> | void;
}) {
  return (
    <div className="panel" data-testid="workflow-panel">
      <div className="panel__title">运行记录</div>

      <div className="panel__section">
        <div className="panel__section-title">本章运行（最新在前）</div>
        {runs.length === 0 ? (
          <EmptyState title="本章暂无运行记录" hint="点击上方按钮启动一个工作流。" />
        ) : (
          <div className="kv-list">
            {runs.map((r) => (
              <div
                key={r.run_id}
                className={`kv-list__row ${r.run_id === selectedRunId ? 'kv-list__row--active' : ''}`}
                onClick={() => onSelect(r.run_id)}
                data-testid={`run-row-${r.run_id}`}
              >
                <span
                  className="kv-list__title"
                  title={`run_id=${r.run_id} · workflow_id=${r.workflow_id}`}
                >
                  {/* 优先展示后端给的 label（如「写正文 → 草稿 v6」），缺省退化为 run_id 短形式；
                      完整 run_id 保留在行内 muted 小字，便于溯源。 */}
                  {r.label ? r.label : `run · ${r.run_id.slice(0, 12)}…`}
                  {r.label ? (
                    <span className="muted small cdp-run-id">
                      {r.run_id.slice(0, 8)}
                    </span>
                  ) : null}
                </span>
                <span
                  title={
                    isRejectedForRevisionRun(r.error)
                      ? '该轮审校被「按建议修改/驳回并改稿」主动驳回，系统已自动重跑写正文→审校；非失败。'
                      : isRejectedRun(r.error)
                      ? '作者已驳回本轮审校，章节保持 DRAFTED；非失败。'
                      : undefined
                  }
                >
                  <WorkflowRunStatusBadge status={r.status} error={r.error} />
                </span>
                <span className="kv-list__meta">
                  {formatDateTime(r.started_at)}
                  {r.ended_at ? ` → ${formatDateTime(r.ended_at)}` : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="panel__section">
        <div className="panel__section-title">节点时间线</div>
        <ErrorBanner>{detailError || pollError}</ErrorBanner>
        {selectedRunId ? (
          detailLoading && !detailRun ? (
            <div className="muted">加载中…</div>
          ) : detailRun ? (
            <RunTimeline run={detailRun} />
          ) : null
        ) : (
          <div className="muted small">从上方列表选择一次运行，即可查看它的节点时间线。</div>
        )}
      </div>

      {/* 审批卡片：PAUSED + 含 __pause_payload__ 时渲染 */}
      {detailRun && detailRun.status === 'PAUSED' && pausePayload ? (
        <div className="panel__section">
          <div className="panel__section-title">审批</div>
          <ApprovalCard
            stage={String(pausePayload['stage'] ?? '')}
            message={String(pausePayload['message'] ?? '请人工决议')}
            pausePayload={pausePayload}
            highRiskChangeCount={countHighRiskChanges(pausePayload)}
            submitting={submitting}
            error={null}
            onApprove={onApprove}
          />
        </div>
      ) : null}

      {/* 深度二审报告：仅在 chapter-review PAUSED 且 pause_payload 含
          deep_review_report（开启 deep_review 才有）时渲染。critic_report
          失败/缺省时不渲染；字段坏形状按需降级，不炸页。 */}
      {detailRun && detailRun.status === 'PAUSED' && pausePayload ? (
        <DeepReviewReportSection pausePayload={pausePayload} />
      ) : null}
    </div>
  );
}

// ---- DeepReviewReportSection ----
// 深度二审（Kimi 三层清单：设定 / 节拍 / 行为链）报告分栏。
// 数据来源：pause_payload['deep_review_report']。仅在字段存在且为合法对象时渲染；
// 未开启 deep_review / 字段缺失 / 坏形状（issues 不是数组等）一律降级为不渲染或
// 友好提示，不抛错、不炸页。verdict 与 severity 用既有的 badge 样式承载。
function DeepReviewReportSection({
  pausePayload,
}: {
  pausePayload: Record<string, unknown>;
}) {
  const raw = pausePayload['deep_review_report'];
  if (raw === null || raw === undefined) return null;
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    return (
      <div className="panel__section" data-testid="deep-review-report-malformed">
        <div className="panel__section-title">深度二审（三层清单）</div>
        <div className="muted small">报告形状异常，无法展示</div>
      </div>
    );
  }
  const report = raw as {
    verdict?: string;
    overall_comment?: string;
    issues?: unknown;
  };
  const verdict = typeof report.verdict === 'string' ? report.verdict : '';
  const overallComment =
    typeof report.overall_comment === 'string' ? report.overall_comment : '';
  const issuesRaw = Array.isArray(report.issues) ? report.issues : [];
  // verdict 徽标：pass=绿/通过；revise=橙/建议改；其他值降级为弱提示。
  const verdictLabel =
    verdict === 'pass'
      ? '通过'
      : verdict === 'revise'
      ? '建议改'
      : verdict
      ? verdict
      : '未知';
  const verdictClass =
    verdict === 'pass'
      ? 'cdp-verdict cdp-verdict--pass'
      : verdict === 'revise'
      ? 'cdp-verdict cdp-verdict--revise'
      : 'cdp-verdict';
  const issues = issuesRaw
    .map((it, i) => {
      if (!it || typeof it !== 'object') return null;
      const o = it as Record<string, unknown>;
      const layer = typeof o['layer'] === 'string' ? (o['layer'] as string) : '';
      const severity =
        typeof o['severity'] === 'string' ? (o['severity'] as string) : '';
      const quote = typeof o['quote'] === 'string' ? (o['quote'] as string) : '';
      const suggestion =
        typeof o['suggestion'] === 'string' ? (o['suggestion'] as string) : '';
      if (!layer && !severity && !quote && !suggestion) return null;
      return { idx: i, layer, severity, quote, suggestion };
    })
    .filter((x): x is NonNullable<typeof x> => x !== null);
  return (
    <div className="panel__section" data-testid="deep-review-report">
      <div className="panel__section-title">深度二审（三层清单）</div>
      <div className="cdp-verdict-row">
        <span
          className={'badge ' + verdictClass}
          data-testid="deep-review-verdict"
          data-verdict={verdict || 'unknown'}
        >
          {verdictLabel}
        </span>
        {overallComment ? (
          <span className="muted small" data-testid="deep-review-overall">
            {overallComment}
          </span>
        ) : null}
      </div>
      {issues.length > 0 ? (
        <ul
          className="cdp-dr-issues"
          data-testid="deep-review-issues"
          data-issue-count={issues.length}
        >
          {issues.map((it) => (
            <li
              key={`dri-${it.idx}`}
              className="cdp-dr-issue"
              data-testid="deep-review-issue"
              data-layer={it.layer}
              data-severity={it.severity}
            >
              <div className="cdp-dr-issue__head">
                <span
                  className="badge"
                  data-testid="deep-review-issue-layer"
                  title={`layer=${it.layer}`}
                >
                  {DEEP_REVIEW_LAYER_LABEL[it.layer] ?? it.layer ?? '未知层'}
                </span>
                <span
                  className="badge"
                  data-testid="deep-review-issue-severity"
                  title={`severity=${it.severity}`}
                >
                  {DEEP_REVIEW_SEVERITY_LABEL[it.severity] ?? it.severity ?? '—'}
                </span>
              </div>
              {it.quote ? (
                <div
                  className="muted small cdp-dr-issue__quote"
                  data-testid="deep-review-issue-quote"
                >
                  「{it.quote}」
                </div>
              ) : null}
              {it.suggestion ? (
                <div
                  className="cdp-dr-issue__suggestion"
                  data-testid="deep-review-issue-suggestion"
                >
                  {it.suggestion}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <div className="muted small cdp-hint">未发现明显问题</div>
      )}
    </div>
  );
}

const DEEP_REVIEW_LAYER_LABEL: Record<string, string> = {
  setting: '设定',
  beat: '节拍',
  behavior: '行为链',
};

const DEEP_REVIEW_SEVERITY_LABEL: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
};

function RunTimeline({ run }: { run: WorkflowRun }) {
  if (!run.nodes || run.nodes.length === 0) {
    return (
      <div className="muted small">
        暂无节点明细（可能 run 刚启动或后端未返回 nodes）。
      </div>
    );
  }
  // 进行中（RUNNING/PAUSED）默认展开，便于用户盯进度；
  // 终态（COMPLETED/FAILED/CANCELLED）默认收起，避免长 run 把页面无限拉长。
  // PENDING 既不是进行中也不是终态，默认收起。
  const defaultOpen = run.status === 'RUNNING' || run.status === 'PAUSED';
  // 摘要：取最后一个节点的信息作为最新状态一栏（节点名 + 状态徽标）。
  const lastNode = run.nodes[run.nodes.length - 1];
  // 仅「初始值受控」：useState 用 defaultOpen 初始化 + 切 run 时重置，
  // 之后用户手动展开/收起的操作不再被 usePoll 每 2s 重渲染打回。
  // details 仍保留 open 属性反映初始状态（DOM 行为兼容既有断言）。
  const [open, setOpen] = useState<boolean>(defaultOpen);
  useEffect(() => {
    setOpen(defaultOpen);
  }, [run.run_id, defaultOpen]);
  return (
    <details
      open={open}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
      data-testid="run-timeline-details"
    >
      <summary className="muted small cdp-timeline__summary" data-testid="run-timeline-summary">
        共 {run.nodes.length} 个节点 · 最新：{lastNode.node_id}
      </summary>
      <ol className="cdp-timeline">
        {run.nodes.map((n) => (
          <li key={n.node_run_id} className="cdp-timeline__node">
            <div className="cdp-timeline__head">
              <strong>{n.node_id}</strong>
              <NodeStatusBadge status={n.status} error={n.error} />
              <span className="muted small">
                {formatDateTime(n.started_at)}
                {n.ended_at ? ` → ${formatDateTime(n.ended_at)}` : ''}
                {n.latency_ms != null ? ` · ${n.latency_ms}ms` : ''}
              </span>
            </div>
            {n.error ? (
              isCancelledByUserNode(n.error) ? (
                // 用户协作式取消：当前节点被取消探针标 FAILED(error='cancelled by user')。
                // 非真失败——用户主动停手；用中性 InfoBanner 避免红色 ErrorBanner 误导为出错。
                <InfoBanner>
                  本节点已被用户主动取消（cancelled by user），非失败。
                </InfoBanner>
              ) : isRejectedForRevisionRun(n.error) ? (
                // 「按建议修改/驳回并改稿」主动驳回：改稿回路会自动重跑 write→review，
                // 非真失败，用中性 InfoBanner 而非红色 ErrorBanner。
                <InfoBanner>
                  已按审校建议驳回本轮（rejected-for-revision）：系统正在自动改稿重跑
                  写正文 → 审校，非失败。
                </InfoBanner>
              ) : isRejectedRun(n.error) ? (
                // 纯驳回：作者主动驳回本轮审校，后端以 FAILED(error='rejected') 收尾。
                // 章节保持 DRAFTED,可改稿后重新发起写正文/审校;非失败,用中性 InfoBanner。
                <InfoBanner>
                  作者已驳回本轮审校，章节保持 DRAFTED，可改稿后重新发起写正文/审校；非失败。
                </InfoBanner>
              ) : (
                <ErrorBanner>{n.error}</ErrorBanner>
              )
            ) : null}
            {n.output_json && typeof n.output_json === 'object' ? (
              <details className="cdp-timeline__output">
                <summary className="muted small">原始节点输出（JSON）</summary>
                <pre className="json-block">{formatJson(n.output_json)}</pre>
              </details>
            ) : null}
          </li>
        ))}
      </ol>
    </details>
  );
}

function NodeStatusBadge({
  status,
  error,
}: {
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'SKIPPED';
  /** 节点 error：FAILED 时区分多态——
   *   cancelled by user      = 用户协作式取消（中性徽标「已取消」，复用 badge--chapter-rejected 灰蓝系）
   *   rejected-for-revision  = 按建议修改/驳回并改稿（中性「已驳回·改稿」）
   *   rejected               = 纯驳回（中性「已驳回」）
   * 三态都用中性徽标，避免被误判为真失败（红色 FAILED）。
   * 判断顺序必须先 cancelled by user（精确匹配），再 rejected-for-revision（含子串 rejected），
   * 再 rejected（精确匹配），最后才落真失败红徽标。 */
  error?: string | null;
}) {
  const err = error ?? '';
  const cancelledByUser = status === 'FAILED' && isCancelledByUserNode(err);
  const rejectedForRevision =
    !cancelledByUser && status === 'FAILED' && isRejectedForRevisionRun(err);
  const rejectedOnly =
    !cancelledByUser && !rejectedForRevision && status === 'FAILED' && isRejectedRun(err);
  const cls =
    cancelledByUser || rejectedForRevision || rejectedOnly
      ? 'badge badge--chapter-rejected'
      : status === 'COMPLETED'
      ? 'badge badge--chapter-committed'
      : status === 'RUNNING'
      ? 'badge badge--chapter-running'
      : status === 'FAILED'
      ? 'badge badge--chapter-failed'
      : status === 'SKIPPED'
      ? 'badge badge--archived'
      : 'badge badge--chapter-planned';
  const label = cancelledByUser
    ? '已取消'
    : rejectedForRevision
    ? '已驳回·改稿'
    : rejectedOnly
    ? '已驳回'
    : status;
  return <span className={cls}>{label}</span>;
}
