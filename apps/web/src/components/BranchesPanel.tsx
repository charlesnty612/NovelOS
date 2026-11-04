import { useCallback, useState } from 'react';
import { ApiError } from '../api/client';
import {
  branchesApi,
  storyStateApi,
} from '../api/endpoints';
import type {
  Branch,
  SnapshotResponse,
} from '../api/types';
import { ErrorBanner, InfoBanner } from './ErrorBanner';
import { ConfirmDialog } from './ConfirmDialog';
import { EmptyState } from './EmptyState';
import { formatDateTime, formatJson } from '../utils/format';
import { countSnapshot } from '../utils/snapshotCounts';

interface Props {
  projectId: string;
  initialBranches: Branch[];
}

/** 后端 promote 的 HIGH 风险审批 409 载荷（`detail` 对象被 client 层序列化成字符串）。 */
interface PromoteApprovalRequired {
  message: string;
  highRiskChangeIds: string[];
}

/**
 * 从 `ApiError.detail` 里解析 `approval_required`。
 *
 * ⚠️ `client.ts` 对**对象型** detail 走 `JSON.stringify`（`formatValidationDetail`
 * 只处理数组）⇒ 这里拿到的是 JSON 串，不是对象。解析失败一律返回 null，
 * 由调用方按普通 409 展示（**不吞错误、不伪造审批出路**）。
 */
function parseApprovalRequired(detail: string): PromoteApprovalRequired | null {
  try {
    const parsed: unknown = JSON.parse(detail);
    if (!parsed || typeof parsed !== 'object') return null;
    const o = parsed as Record<string, unknown>;
    if (o['error'] !== 'approval_required') return null;
    const ids = Array.isArray(o['high_risk_change_ids'])
      ? o['high_risk_change_ids'].map((v) => String(v))
      : [];
    return {
      message: typeof o['message'] === 'string' ? o['message'] : '需要人工批准高风险变更',
      highRiskChangeIds: ids,
    };
  } catch {
    return null;
  }
}

/**
 * BranchesPanel —— 项目总览页「What-if 分支」管理面板（V1.5 / Sprint 17）。
 *
 * 功能：
 * - 列表：name / status / base_state_version / created_at；
 * - 创建：name 输入框；提交后自动 reload；
 * - 详情：展开行可懒加载分支视角 state（getBranchState）+ 与 main 快照基础计数对比；
 * - promote：ACTIVE 分支显示按钮；成功 → 显示新 commit/version；冲突 → ErrorBanner；
 *
 * data-testid（便于 vitest 断言）：
 *   - branches-panel / -empty
 *   - branches-item-{bid} / -name-{bid} / -toggle-{bid} / -promote-{bid}
 *   - branches-create-name / -create-submit
 *   - branches-state-panel-{bid}（展开后的快照区）
 *   - branches-promote-result-{bid}（成功反馈区）
 */
export function BranchesPanel({ projectId, initialBranches }: Props) {
  const [branches, setBranches] = useState<Branch[]>(initialBranches);
  const [mainSnap, setMainSnap] = useState<SnapshotResponse | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [branchSnap, setBranchSnap] = useState<SnapshotResponse | null>(null);
  const [branchSnapErr, setBranchSnapErr] = useState<string | null>(null);
  const [branchSnapLoading, setBranchSnapLoading] = useState(false);
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [promoteMsg, setPromoteMsg] = useState<Record<string, string>>({});
  const [promoteErr, setPromoteErr] = useState<Record<string, string>>({});
  // HIGH 风险审批出路（批 6 / 2026-11-05）：promote 预检失败不产生任何 main commit，
  // 后端以 409 `approval_required` 给出 high_risk_change_ids。UI 弹确认框，
  // 用户确认后把 `author_approval` 带同一 branch 重发。
  const [approvalPrompt, setApprovalPrompt] = useState<{
    branchId: string;
    branchName: string;
    message: string;
    highRiskChangeIds: string[];
  } | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const rows = await branchesApi.list(projectId);
      setBranches(rows);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载分支失败');
    }
  }, [projectId]);

  const ensureMainSnap = useCallback(async () => {
    if (mainSnap) return mainSnap;
    try {
      const snap = await storyStateApi.getCurrent(projectId);
      setMainSnap(snap);
      return snap;
    } catch (e: unknown) {
      // main 拿不到也不阻塞面板；面板内分支对照仅作 best-effort。
      return null;
    }
  }, [mainSnap, projectId]);

  const handleToggle = useCallback(
    async (bid: string) => {
      setPromoteMsg((m) => ({ ...m, [bid]: '' }));
      setPromoteErr((e) => ({ ...e, [bid]: '' }));
      if (expanded === bid) {
        setExpanded(null);
        setBranchSnap(null);
        setBranchSnapErr(null);
        return;
      }
      setExpanded(bid);
      setBranchSnap(null);
      setBranchSnapErr(null);
      setBranchSnapLoading(true);
      try {
        const [snap] = await Promise.all([
          storyStateApi.getBranchState(projectId, bid),
          ensureMainSnap(),
        ]);
        setBranchSnap(snap);
      } catch (e: unknown) {
        if (e instanceof ApiError) {
          setBranchSnapErr(`${e.status}: ${e.detail}`);
        } else if (e instanceof Error) {
          setBranchSnapErr(e.message);
        } else {
          setBranchSnapErr('加载分支 state 失败');
        }
      } finally {
        setBranchSnapLoading(false);
      }
    },
    [expanded, ensureMainSnap, projectId],
  );

  const handleCreate = useCallback(async () => {
    setError(null);
    const trimmed = name.trim();
    if (!trimmed) {
      setError('分支名不能为空');
      return;
    }
    if (trimmed === 'main') {
      setError('分支名不能为 "main"（保留名）');
      return;
    }
    setBusy(true);
    try {
      await branchesApi.create(projectId, { name: trimmed });
      setName('');
      await reload();
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        setError(`${e.status}: ${e.detail}`);
      } else if (e instanceof Error) {
        setError(e.message);
      } else {
        setError('创建分支失败');
      }
    } finally {
      setBusy(false);
    }
  }, [name, projectId, reload]);

  const handlePromote = useCallback(
    async (bid: string, branchName: string, opts?: { approved?: boolean }) => {
      setPromoteMsg((m) => ({ ...m, [bid]: '' }));
      setPromoteErr((e) => ({ ...e, [bid]: '' }));
      setBusy(true);
      try {
        const payload = opts?.approved
          ? {
              author_approval: {
                approved: true,
                // 审计留痕用（后端原样落 commits.author_approval_json）——
                // 必须能看出是 UI 上的人工确认，而不是 workflow 的 system:* 预置批准。
                approver: 'user:ui',
              },
            }
          : {};
        const result = await branchesApi.promote(projectId, bid, payload);
        setPromoteMsg((m) => ({
          ...m,
          [bid]: `已 promote「${branchName}」：重放 ${result.promoted_commits} 条 delta → main 当前 v${result.state_version}`,
        }));
        await reload();
      } catch (e: unknown) {
        if (e instanceof ApiError) {
          const approval = e.status === 409 ? parseApprovalRequired(e.detail) : null;
          if (approval && !opts?.approved) {
            // 预检拒绝 ⇒ 此时**没有** main commit 产生；确认后重发同一请求。
            setApprovalPrompt({ branchId: bid, branchName, ...approval });
          } else {
            // 409 分支已 MERGED/DISCARDED、乐观锁冲突、promote_conflict、
            // 或「已批准但后端仍拒」等：照旧展示，不伪造第二条出路。
            setPromoteErr((prev) => ({
              ...prev,
              [bid]: `${e.status}: ${e.detail}`,
            }));
          }
        } else if (e instanceof Error) {
          setPromoteErr((prev) => ({ ...prev, [bid]: e.message }));
        } else {
          setPromoteErr((prev) => ({ ...prev, [bid]: 'promote 失败' }));
        }
      } finally {
        setBusy(false);
      }
    },
    [projectId, reload],
  );

  return (
    <div
      className="card"
      style={{ marginBottom: 16 }}
      data-testid="branches-panel"
    >
      <div className="detail-pane__title">
        What-if 分支（V1.5 / Sprint 17）
      </div>
      <ErrorBanner>{error}</ErrorBanner>
      <div className="muted small" style={{ marginBottom: 8 }}>
        分支用于隔离 What-if 实验：基于当前 main 快照创建分支，在分支内提交
        delta，最终通过 promote 把变更按序重放到 main。simulation（sim-*）是
        一次性的临时分支，自动归档。
      </div>

      {branches.length === 0 ? (
        <div data-testid="branches-empty">
          <EmptyState
            title="该项目暂无分支"
            hint="在下方输入分支名，基于当前 main 快照创建第一个分支。"
          />
        </div>
      ) : (
        <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 12px 0' }}>
          {branches.filter((b) => b.name !== 'main').map((b) => (
            <li
              key={b.branch_id}
              className="card"
              style={{ marginBottom: 8, padding: 8 }}
              data-testid={`branches-item-${b.branch_id}`}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <strong data-testid={`branches-name-${b.branch_id}`}>
                  {b.name}
                </strong>
                <BranchStatusBadge status={b.status} />
                <span className="muted small">
                  基于 v{b.base_state_version} · {formatDateTime(b.created_at)}
                </span>
                <div style={{ flex: 1 }} />
                {b.status === 'ACTIVE' ? (
                  <button
                    type="button"
                    className="btn btn--sm btn--primary"
                    disabled={busy}
                    onClick={() => void handlePromote(b.branch_id, b.name)}
                    data-testid={`branches-promote-${b.branch_id}`}
                    title="把分支全部 commits 按序重放到 main"
                  >
                    promote
                  </button>
                ) : null}
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={() => void handleToggle(b.branch_id)}
                  data-testid={`branches-toggle-${b.branch_id}`}
                >
                  {expanded === b.branch_id ? '收起' : '查看 state'}
                </button>
              </div>

              {expanded === b.branch_id ? (
                <div
                  data-testid={`branches-state-panel-${b.branch_id}`}
                  style={{ marginTop: 8 }}
                >
                  <ErrorBanner>{branchSnapErr}</ErrorBanner>
                  {branchSnapLoading ? (
                    <div className="muted small">加载分支 state…</div>
                  ) : branchSnap ? (
                    <BranchStateView
                      branchSnap={branchSnap}
                      mainSnap={mainSnap}
                    />
                  ) : (
                    <div className="muted small">无数据</div>
                  )}
                </div>
              ) : null}

              {promoteMsg[b.branch_id] ? (
                <InfoBanner>
                  <div
                    data-testid={`branches-promote-result-${b.branch_id}`}
                  >
                    {promoteMsg[b.branch_id]}
                  </div>
                </InfoBanner>
              ) : null}
              {promoteErr[b.branch_id] ? (
                <ErrorBanner>
                  <div
                    data-testid={`branches-promote-error-${b.branch_id}`}
                  >
                    promote 失败：{promoteErr[b.branch_id]}
                  </div>
                </ErrorBanner>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
          marginTop: 8,
        }}
      >
        <input
          className="input"
          placeholder="新分支名（不可为 'main'）"
          value={name}
          onChange={(e) => setName(e.target.value)}
          data-testid="branches-create-name"
        />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button
            type="button"
            className="btn btn--sm btn--primary"
            disabled={busy || !name.trim() || name.trim() === 'main'}
            onClick={() => void handleCreate()}
            data-testid="branches-create-submit"
          >
            基于当前 main 创建分支
          </button>
          <span className="muted small">
            命名建议：feature-xxx / experiment-yyy
          </span>
        </div>
      </div>

      {/* HIGH 风险审批（批 6）：只有在收到 409 approval_required 时出现。
          确认后带 `author_approval` 重发同一 branch 的 promote——
          预检失败时后端**没有**产生任何 main commit，所以这是一次「批准后重试」，
          不是「回滚后重做」。 */}
      {approvalPrompt ? (
        <ConfirmDialog
          open={true}
          title="需要批准高风险变更"
          body={
            `promote「${approvalPrompt.branchName}」包含 ${approvalPrompt.highRiskChangeIds.length} 项高风险变更，` +
            `需人工批准后才会重放到 main。\n\n${approvalPrompt.message}\n\n` +
            `高风险变更：${approvalPrompt.highRiskChangeIds.join('、') || '（未提供 id）'}\n\n` +
            '批准后会以 `user:ui` 记入提交审计。'
          }
          confirmText="批准并 promote"
          testId="branches-approval-confirm"
          onCancel={() => setApprovalPrompt(null)}
          onConfirm={() => {
            const pending = approvalPrompt;
            setApprovalPrompt(null);
            if (pending) {
              void handlePromote(pending.branchId, pending.branchName, {
                approved: true,
              });
            }
          }}
        />
      ) : null}
    </div>
  );
}

const BRANCH_STATUS_LABEL: Record<Branch['status'], string> = {
  ACTIVE: '激活',
  MERGED: '已合并',
  DISCARDED: '已丢弃',
  ARCHIVED: '已归档',
};

// 分支状态徽标：复用全局 .badge--* 语义类（不再自造 inline pill）。
const BRANCH_STATUS_CLASS: Record<Branch['status'], string> = {
  ACTIVE: 'badge badge--active',
  MERGED: 'badge badge--ok',
  DISCARDED: 'badge badge--warn',
  ARCHIVED: 'badge badge--archived',
};

function BranchStatusBadge({ status }: { status: Branch['status'] }) {
  return (
    <span className={BRANCH_STATUS_CLASS[status] ?? 'badge'}>
      {BRANCH_STATUS_LABEL[status] ?? status}
    </span>
  );
}

function BranchStateView({
  branchSnap,
  mainSnap,
}: {
  branchSnap: SnapshotResponse;
  mainSnap: SnapshotResponse | null;
}) {
  // 口径单点 utils/snapshotCounts（2026-10-15）。改动前本组件自带一份
  // countArr/countChars，用 `countArr(snap.events)` 数 events——而后端 events 是
  // **dict**（按 event_id 键），于是 What-if 分支对比的「Events」与「差」两列
  // **永远显示 0**，分支之间的事件差异根本看不出来。
  const bc = countSnapshot(branchSnap);
  const mc = mainSnap ? countSnapshot(mainSnap) : null;
  const branchChars = bc.characters;
  const branchHooks = bc.hooks;
  const branchDebts = bc.debts;
  const branchEvents = bc.events;

  const mainChars = mc ? mc.characters : null;
  const mainHooks = mc ? mc.hooks : null;
  const mainDebts = mc ? mc.debts : null;
  const mainEvents = mc ? mc.events : null;

  const diff = (a: number | null, b: number) => {
    if (a === null) return '—';
    const d = b - a;
    return d === 0 ? '0' : d > 0 ? `+${d}` : `${d}`;
  };

  return (
    <div style={{ marginTop: 4 }}>
      <div className="muted small">
        state_version（分支） =
        {typeof branchSnap.state_version === 'number'
          ? branchSnap.state_version
          : '—'}
        {mainSnap
          ? ` · main = ${typeof mainSnap.state_version === 'number' ? mainSnap.state_version : '—'}`
          : ''}
      </div>
      <div className="form-grid" style={{ marginTop: 6 }}>
        <Stat
          label="Characters"
          value={branchChars}
          hint={
            mainChars !== null
              ? `main ${mainChars}（差 ${diff(mainChars, branchChars)}）`
              : 'main 未加载'
          }
        />
        <Stat
          label="Hooks"
          value={branchHooks}
          hint={
            mainHooks !== null
              ? `main ${mainHooks}（差 ${diff(mainHooks, branchHooks)}）`
              : 'main 未加载'
          }
        />
        <Stat
          label="Debts"
          value={branchDebts}
          hint={
            mainDebts !== null
              ? `main ${mainDebts}（差 ${diff(mainDebts, branchDebts)}）`
              : 'main 未加载'
          }
        />
        <Stat
          label="Events"
          value={branchEvents}
          hint={
            mainEvents !== null
              ? `main ${mainEvents}（差 ${diff(mainEvents, branchEvents)}）`
              : 'main 未加载'
          }
        />
      </div>
      <details style={{ marginTop: 8 }}>
        <summary className="muted small">原始 state JSON</summary>
        <pre
          className="muted small"
          style={{
            background: 'var(--color-bg)',
            padding: 8,
            borderRadius: 'var(--radius-sm)',
            overflow: 'auto',
            maxHeight: 240,
          }}
        >
          {formatJson(branchSnap)}
        </pre>
      </details>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint: string;
}) {
  return (
    <div>
      <div className="muted small">{label}</div>
      <div style={{ fontSize: 16, fontWeight: 600 }}>{value}</div>
      <div className="muted small">{hint}</div>
    </div>
  );
}
