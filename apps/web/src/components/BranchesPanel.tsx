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
import { formatDateTime, formatJson } from '../utils/format';

interface Props {
  projectId: string;
  initialBranches: Branch[];
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
    async (bid: string, branchName: string) => {
      setPromoteMsg((m) => ({ ...m, [bid]: '' }));
      setPromoteErr((e) => ({ ...e, [bid]: '' }));
      setBusy(true);
      try {
        const result = await branchesApi.promote(projectId, bid, {});
        setPromoteMsg((m) => ({
          ...m,
          [bid]: `已 promote「${branchName}」：重放 ${result.promoted_commits} 条 delta → main 当前 v${result.state_version}`,
        }));
        await reload();
      } catch (e: unknown) {
        if (e instanceof ApiError) {
          // 409 分支已 MERGED/DISCARDED、乐观锁冲突、promote_conflict、approval_required 等
          setPromoteErr((prev) => ({
            ...prev,
            [bid]: `${e.status}: ${e.detail}`,
          }));
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
        <div className="muted small" data-testid="branches-empty">
          该项目暂无分支。
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
    </div>
  );
}

function BranchStatusBadge({ status }: { status: Branch['status'] }) {
  const palette: Record<Branch['status'], { bg: string; fg: string; label: string }> = {
    ACTIVE: { bg: '#1f6feb', fg: '#fff', label: 'ACTIVE' },
    MERGED: { bg: '#2da44e', fg: '#fff', label: 'MERGED' },
    DISCARDED: { bg: '#6e7681', fg: '#fff', label: 'DISCARDED' },
    ARCHIVED: { bg: '#8b5cf6', fg: '#fff', label: 'ARCHIVED' },
  };
  const it = palette[status] ?? palette.ACTIVE;
  return (
    <span
      style={{
        fontSize: 11,
        padding: '2px 8px',
        borderRadius: 10,
        background: it.bg,
        color: it.fg,
      }}
    >
      {it.label}
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
  const branchChars = countChars(branchSnap.characters);
  const branchHooks = countArr(branchSnap.hooks);
  const branchDebts = countArr(branchSnap.debts);
  const branchEvents = countArr(branchSnap.events);

  const mainChars = mainSnap ? countChars(mainSnap.characters) : null;
  const mainHooks = mainSnap ? countArr(mainSnap.hooks) : null;
  const mainDebts = mainSnap ? countArr(mainSnap.debts) : null;
  const mainEvents = mainSnap ? countArr(mainSnap.events) : null;

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
            background: '#f6f8fa',
            padding: 8,
            borderRadius: 4,
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

function countArr(v: unknown): number {
  return Array.isArray(v) ? v.length : 0;
}

function countChars(v: unknown): number {
  if (Array.isArray(v)) return v.length;
  if (v && typeof v === 'object') {
    const inner = (v as Record<string, unknown>).characters;
    if (Array.isArray(inner)) return inner.length;
    if (inner && typeof inner === 'object') return Object.keys(inner).length;
  }
  return 0;
}