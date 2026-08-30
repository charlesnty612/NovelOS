// 审批卡片：当 workflow run PAUSED 在 Human 节点时展示。
// 支持两种 stage：
//   - chapter-review  → 显示 review_report（字数偏离 / 禁用词 / warnings）
//                        + V1.3 critic_report（LLM 评审员建议；advisory only）
//   - chapter-commit.high_risk_approval → 显示 changes 计数 + 全量明细
//
// chapter-review 分支有四态决议：
//   - 批准        → onApprove(true)
//   - 驳回        → onApprove(false)              （run FAILED，章节保持 DRAFTED）
//   - 驳回并改稿  → onApprove(false, {revise:true, note})（run FAILED(rejected-for-revision)，
//                    章节保持 DRAFTED，note 落 plan_json.revision_note，可改稿后重跑 write/review）
//   - 按建议修改  → onApprove(false, {revise:true, note: <勾选的 critic suggestion / warnings / errors>})
//                    复用改稿回路，但 note 由审校报告自动收集（默认全选、用户可取消勾选）。
//
// 通过 props 注入决定 / 拒绝动作，由父组件负责调 resume API。

import { useMemo, useState } from 'react';
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

/** 一条「建议」的可视化条目 + 用于 note 拼接的纯文本。
 *  来源优先级：critic_report.issues[].suggestion（结构化 LLM 建议，最像"建议"）
 *  → review_report.warnings[]（字符串警告）
 *  → review_report.errors[].message（严重规则项提示） */
interface SuggestionEntry {
  /** 唯一 key，供 React 列表 + 勾选状态 */
  key: string;
  /** 显示用类别标签，例如「AI 审稿」「warnings」「严重」 */
  source: string;
  /** 选填：分类标签（仅 critic issue 有） */
  category?: CriticIssueCategory;
  /** 选填：严重度（仅 critic issue 有） */
  severity?: CriticIssueSeverity;
  /** 选填：问题引文（critic issue 有；warnings/errors 也可附加） */
  quote?: string;
  /** 真正写入 revision_note 的建议文本 */
  text: string;
}

/** 从 pausePayload 中抽取建议条目。
 *  字段命名以 types.ts ChapterReviewPausePayload + ApprovalCard ReviewReportShape 为准：
 *  - review_report.warnings: string[]
 *  - review_report.errors: {rule_id, severity, message}[]
 *  - critic_report.issues: {category, severity, quote, suggestion}[]   ← 主要建议源
 */
export function extractSuggestions(
  pausePayload: Record<string, unknown>,
): SuggestionEntry[] {
  const out: SuggestionEntry[] = [];
  const reviewReport = pausePayload['review_report'] as
    | ReviewReportShape
    | undefined;
  const criticReport = pausePayload['critic_report'] as
    | CriticReport
    | null
    | undefined;

  if (criticReport && Array.isArray(criticReport.issues)) {
    criticReport.issues.forEach((it: CriticIssue, i: number) => {
      const text = (it.suggestion ?? '').trim();
      if (!text) return;
      out.push({
        key: `critic-${i}`,
        source: 'AI 审稿',
        category: it.category,
        severity: it.severity,
        quote: it.quote,
        text,
      });
    });
  }

  if (reviewReport && Array.isArray(reviewReport.warnings)) {
    reviewReport.warnings.forEach((w, i) => {
      const text = String(w ?? '').trim();
      if (!text) return;
      out.push({
        key: `warn-${i}`,
        source: 'warnings',
        text,
      });
    });
  }

  if (reviewReport && Array.isArray(reviewReport.errors)) {
    reviewReport.errors.forEach((e, i) => {
      const text = String(e.message ?? '').trim();
      if (!text) return;
      out.push({
        key: `err-${i}`,
        source: '严重',
        severity: (e.severity as CriticIssueSeverity | undefined) ?? undefined,
        text,
      });
    });
  }

  return out;
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
  const [pendingRevise, setPendingRevise] = useState(false);
  const [pendingSuggestionKey, setPendingSuggestionKey] = useState<string | null>(null);
  const [reviseNote, setReviseNote] = useState('');

  const reviewReport = pausePayload['review_report'] as
    | ReviewReportShape
    | undefined;
  // V1.3：critic_report 仅作建议性参考；critic_status != 'ok' 时 critic_report 为 null，
  // UI 仅显示弱提示，不影响审批按钮可用性。
  const criticStatus = (pausePayload['critic_status'] as string | undefined) ?? 'skipped';
  const criticReport = pausePayload['critic_report'] as CriticReport | null | undefined;

  // chapter-review 分支：根据审校报告收集建议条目，默认全选。
  const suggestions = useMemo<SuggestionEntry[]>(
    () => (stage === 'chapter-review' ? extractSuggestions(pausePayload) : []),
    [stage, pausePayload],
  );
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(
    () => new Set(suggestions.map((s) => s.key)),
  );
  // 当建议列表变化（如 pausePayload 切换 run）时，重置勾选集合为全选。
  // 用 useMemo 派生集合的稳定 hash 来检测变化，避免漏更新。
  const suggestionsKey = suggestions.map((s) => s.key).join('|');
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useMemo(() => {
    setSelectedKeys(new Set(suggestions.map((s) => s.key)));
  }, [suggestionsKey]);

  const selectedNote = useMemo(() => {
    const picked = suggestions.filter((s) => selectedKeys.has(s.key));
    return picked.map((s) => s.text).join('\n');
  }, [suggestions, selectedKeys]);

  function toggleSuggestion(key: string) {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

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
          ? 'Observer 检测到高风险 / definition / world_kind=rule 变更，请人工审批。下方列出全部变更明细（高风险置顶），请逐条过目后再批准。'
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
        {stage === 'chapter-review' && suggestions.length > 0 ? (
          <button
            className="btn"
            disabled={submitting || selectedKeys.size === 0}
            data-testid="approval-apply-suggestions"
            onClick={async () => {
              setPendingRevise(true);
              try {
                await onApprove(false, {
                  revise: true,
                  // 勾选的建议 + 意见框补充内容合并下发（意见框对任何改稿动作都生效）
                  note:
                    [selectedNote, reviseNote.trim()]
                      .filter((s) => s)
                      .join('\n\n') || undefined,
                });
              } finally {
                setPendingRevise(false);
              }
            }}
          >
            {submitting && pendingRevise ? '提交中…' : '按建议修改'}
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

      {stage === 'chapter-review' && suggestions.length > 0 ? (
        <div
          style={{ marginTop: 8 }}
          data-testid="approval-suggestions"
          data-selected-count={selectedKeys.size}
        >
          <div className="muted small">
            审校建议（{suggestions.length}，已选 {selectedKeys.size}）— 勾选后点击「按建议修改」
          </div>
          <ul
            style={{
              listStyle: 'none',
              padding: 0,
              margin: '4px 0 0 0',
            }}
          >
            {suggestions.map((s) => {
              const checked = selectedKeys.has(s.key);
              return (
                <li
                  key={s.key}
                  data-testid="approval-suggestion-row"
                  data-suggestion-key={s.key}
                  style={{
                    display: 'flex',
                    gap: 6,
                    alignItems: 'flex-start',
                    borderLeft: '3px solid var(--color-border-strong)',
                    paddingLeft: 8,
                    marginTop: 4,
                  }}
                >
                  <input
                    type="checkbox"
                    data-testid="approval-suggestion-checkbox"
                    data-suggestion-key={s.key}
                    checked={checked}
                    disabled={submitting}
                    onChange={() => toggleSuggestion(s.key)}
                    style={{ marginTop: 3 }}
                  />
                  <div style={{ flex: 1 }}>
                    {/* 行内直接展示建议正文（+问题引文/分类/严重度），
                        让「应用此条」能一眼对上要改哪条；
                        note 拼接始终以程序数据 s.text 为准。 */}
                    <div
                      className="small muted"
                      style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}
                    >
                      <span className="badge">{s.source}</span>
                      {s.category ? (
                        <span className="badge">
                          {CRITIC_CATEGORY_LABEL[s.category] ?? s.category}
                        </span>
                      ) : null}
                      {s.severity ? (
                        <span className="badge">
                          {CRITIC_SEVERITY_LABEL[s.severity] ?? s.severity}
                        </span>
                      ) : null}
                      <div style={{ flex: 1 }} />
                      <button
                        type="button"
                        className="btn btn--sm"
                        disabled={submitting}
                        data-testid="approval-apply-suggestion"
                        data-suggestion-key={s.key}
                        onClick={async () => {
                          setPendingSuggestionKey(s.key);
                          try {
                            await onApprove(false, {
                              revise: true,
                              note: s.text || undefined,
                            });
                          } finally {
                            setPendingSuggestionKey(null);
                          }
                        }}
                      >
                        {submitting && pendingSuggestionKey === s.key
                          ? '应用修改中…'
                          : '应用此条'}
                      </button>
                    </div>
                    {s.quote ? (
                      <div
                        className="muted small"
                        data-testid="approval-suggestion-quote"
                        style={{ marginTop: 2 }}
                      >
                        位置：{s.quote}
                      </div>
                    ) : null}
                    <div
                      className="small"
                      data-testid="approval-suggestion-text"
                      style={{ marginTop: 2, whiteSpace: 'pre-wrap' }}
                    >
                      {s.text}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
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
  // 仅当 changes 存在时合并两条数组；HIGH 排最前，其余保持原相对顺序。
  const entries: ChangeEntry[] = useMemo(() => {
    if (!changes) return [];
    const merged: ChangeEntry[] = [];
    if (Array.isArray(changes.character_changes)) {
      changes.character_changes.forEach((raw, idx) => {
        merged.push(toChangeEntry('character_changes', raw, idx));
      });
    }
    if (Array.isArray(changes.world_changes)) {
      changes.world_changes.forEach((raw, idx) => {
        merged.push(toChangeEntry('world_changes', raw, idx));
      });
    }
    return merged.sort((a, b) => {
      const aHigh = a.risk_level === 'HIGH' ? 0 : 1;
      const bHigh = b.risk_level === 'HIGH' ? 0 : 1;
      return aHigh - bHigh;
    });
  }, [changes]);
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
      {entries.length > 0 ? (
        <ul
          data-testid="high-risk-change-list"
          style={{
            listStyle: 'none',
            padding: 0,
            margin: '8px 0 0 0',
          }}
        >
          {entries.map((e) => (
            <ChangeItemRow key={`${e.kind}-${e.change_id}-${e.idx}`} entry={e} />
          ))}
        </ul>
      ) : null}
      <div className="muted small" style={{ marginTop: 4 }}>
        涉及 character_changes / world_changes。批准会继续 COMMIT 流程，驳回则 workflow 进入 FAILED。
      </div>
    </div>
  );
}

// ---------- HighRiskSummary：变更明细渲染辅助 ----------

interface ChangeEntry {
  kind: 'character_changes' | 'world_changes';
  idx: number;
  change_id: string;
  op: string;
  target_id: string;
  field: string;
  before: unknown;
  after: unknown;
  confidence: unknown;
  evidence: { chapter_id?: unknown; scene_id?: unknown; excerpt?: unknown; span?: unknown } | undefined;
  notes: string | undefined;
  risk_level: string;
  visibility: unknown;
  who_knows: unknown;
  // 类别字段
  character_id?: string;
  facet?: string;
  world_id?: string;
  world_kind?: string;
}

function toChangeEntry(
  kind: 'character_changes' | 'world_changes',
  raw: unknown,
  idx: number,
): ChangeEntry {
  const o = (raw ?? {}) as Record<string, unknown>;
  const evidence = (o['evidence'] ?? undefined) as
    | { chapter_id?: unknown; scene_id?: unknown; excerpt?: unknown; span?: unknown }
    | undefined;
  return {
    kind,
    idx,
    change_id: strOrDash(o['change_id']),
    op: strOrDash(o['op']),
    target_id: strOrDash(o['target_id']),
    field: strOrDash(o['field']),
    before: o['before'],
    after: o['after'],
    confidence: o['confidence'],
    evidence,
    notes: typeof o['notes'] === 'string' ? (o['notes'] as string) : undefined,
    risk_level: typeof o['risk_level'] === 'string' ? (o['risk_level'] as string) : 'LOW',
    visibility: o['visibility'],
    who_knows: o['who_knows'],
    character_id: typeof o['character_id'] === 'string' ? (o['character_id'] as string) : undefined,
    facet: typeof o['facet'] === 'string' ? (o['facet'] as string) : undefined,
    world_id: typeof o['world_id'] === 'string' ? (o['world_id'] as string) : undefined,
    world_kind: typeof o['world_kind'] === 'string' ? (o['world_kind'] as string) : undefined,
  };
}

function strOrDash(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v);
  } catch {
    return '—';
  }
}

/** 把 before/after 渲染成展示字符串。null/undefined/'' → "新增：{after}"，否则 "{before} → {after}"。 */
function renderValueChange(before: unknown, after: unknown): { text: string; full: string } {
  const formatVal = (v: unknown): string => {
    if (v === null || v === undefined || v === '') return '—';
    if (Array.isArray(v)) return v.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join('；');
    if (typeof v === 'string') return v;
    if (typeof v === 'object') {
      try {
        return JSON.stringify(v);
      } catch {
        return String(v);
      }
    }
    return String(v);
  };
  const isEmpty = (v: unknown) => v === null || v === undefined || v === '';
  const full = isEmpty(before)
    ? `新增：${formatVal(after)}`
    : `${formatVal(before)} → ${formatVal(after)}`;
  const truncated = full.length > 200 ? full.slice(0, 200) + '…' : full;
  return { text: truncated, full };
}

function truncateWithTitle(s: string, max: number): { text: string; title: string } {
  if (s.length <= max) return { text: s, title: s };
  return { text: s.slice(0, max) + '…', title: s };
}

const RISK_BADGE_LABEL: Record<string, { label: string; color: string }> = {
  HIGH: { label: '高风险', color: 'var(--color-danger, #c62828)' },
  MEDIUM: { label: '中风险', color: 'var(--color-warn, #c97a16)' },
  LOW: { label: '低风险', color: 'var(--color-text-muted, #6b7280)' },
};

const OP_LABEL: Record<string, string> = {
  add: '新增',
  update: '更新',
  remove: '删除',
};

const WORLD_KIND_LABEL: Record<string, string> = {
  location: '地点',
  rule: '规则',
  faction: '势力',
  item: '物品',
};

function ChangeItemRow({ entry }: { entry: ChangeEntry }) {
  const riskInfo = RISK_BADGE_LABEL[entry.risk_level] ?? RISK_BADGE_LABEL.LOW!;
  const opLabel = OP_LABEL[entry.op] ?? entry.op;
  const typeLabel =
    entry.kind === 'character_changes'
      ? '人物'
      : WORLD_KIND_LABEL[entry.world_kind ?? ''] ?? entry.world_kind ?? '—';
  const objId = entry.kind === 'character_changes' ? entry.character_id : entry.world_id;
  const idTail = objId ? ` · ${objId}` : '';
  const valueRender = renderValueChange(entry.before, entry.after);
  const excerpt = entry.evidence?.excerpt;
  const excerptStr = typeof excerpt === 'string' ? excerpt : '';
  const excerptRender = excerptStr ? truncateWithTitle(excerptStr, 80) : null;
  const hasNotes = !!entry.notes;
  const hasExcerpt = !!excerptRender;
  return (
    <li
      data-testid="high-risk-change-item"
      data-risk={entry.risk_level}
      style={{
        borderLeft: '3px solid var(--color-border-strong)',
        paddingLeft: 8,
        marginTop: 6,
      }}
    >
      <div
        className="small"
        style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}
      >
        <span
          className="badge"
          data-testid="high-risk-change-risk"
          style={{ color: riskInfo.color, borderColor: riskInfo.color }}
        >
          {riskInfo.label}
        </span>
        <span className="badge" data-testid="high-risk-change-op">
          {opLabel}
        </span>
        <span className="badge" data-testid="high-risk-change-type">
          {typeLabel}
        </span>
        <span className="muted small" data-testid="high-risk-change-meta">
          {entry.field}
          {idTail}
        </span>
      </div>
      <div
        className="small"
        data-testid="high-risk-change-value"
        title={valueRender.full}
        style={{ marginTop: 2, whiteSpace: 'pre-wrap' }}
      >
        {valueRender.text}
      </div>
      {hasNotes || hasExcerpt ? (
        <div
          className="muted small"
          style={{ marginTop: 2 }}
          data-testid="high-risk-change-notes"
        >
          {hasNotes ? <span>备注：{entry.notes}</span> : null}
          {hasNotes && hasExcerpt ? <span>；</span> : null}
          {hasExcerpt ? (
            <span title={excerptRender!.title}>引文：{excerptRender!.text}</span>
          ) : null}
        </div>
      ) : null}
    </li>
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
