import { useEffect, useMemo, useState } from 'react';
import { chaptersApi, debtsApi, hooksApi } from '../../api/endpoints';
import type {
  Chapter,
  Debt,
  DebtCreatePayload,
  DebtStatus,
  DebtUpdatePayload,
  Hook,
  HookCreatePayload,
  HookStatus,
  HookUpdatePayload,
} from '../../api/types';
import { ErrorBanner } from '../../components/ErrorBanner';
import { EmptyState } from '../../components/EmptyState';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import {
  DEBT_STATUS_LABEL,
  HOOK_STATUS_LABEL,
  HOOK_STATUS_VALUES,
  getNextDebtStatuses,
  getNextHookStatuses,
  isHookOverdue,
  safeGetNextDebtStatuses,
  safeGetNextHookStatuses,
} from '../../utils/ledgerState';

interface LedgerTabProps {
  projectId: string;
}

// ---------------------------------------------------------------------------
// Status 徽标颜色：复用现有 badge 类，hook/debt 各自一套配色。
// ---------------------------------------------------------------------------

const HOOK_STATUS_CLASS: Record<HookStatus, string> = {
  OPEN: 'badge badge--chapter-planned',
  ACTIVE: 'badge badge--chapter-drafted',
  ESCALATED: 'badge badge--chapter-reviewed',
  RESOLVED: 'badge badge--chapter-committed',
  ABANDONED: 'badge badge--chapter-failed',
};

const DEBT_STATUS_CLASS: Record<DebtStatus, string> = {
  open: 'badge badge--chapter-planned',
  acknowledged: 'badge badge--chapter-reviewed',
  paid: 'badge badge--chapter-committed',
  forgiven: 'badge badge--chapter-failed',
};

// ---------------------------------------------------------------------------
// 主面板
// ---------------------------------------------------------------------------

export function LedgerTab({ projectId }: LedgerTabProps) {
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [debts, setDebts] = useState<Debt[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [hookFilter, setHookFilter] = useState<'all' | HookStatus>('all');
  const [editingHook, setEditingHook] = useState<Hook | null>(null);
  const [creatingHook, setCreatingHook] = useState(false);
  const [editingDebt, setEditingDebt] = useState<Debt | null>(null);
  const [creatingDebt, setCreatingDebt] = useState(false);
  // V3.22「交互反馈统一」：伏笔/债务删除前 ConfirmDialog 二次确认。
  const [pendingDeleteHookId, setPendingDeleteHookId] = useState<string | null>(null);
  const [pendingDeleteDebtId, setPendingDeleteDebtId] = useState<string | null>(null);

  const reload = async () => {
    setLoading(true);
    setErr(null);
    try {
      const [hs, ds, cs] = await Promise.all([
        hooksApi.listByProject(projectId),
        debtsApi.listByProject(projectId),
        chaptersApi.listByProject(projectId),
      ]);
      setHooks(hs);
      setDebts(ds);
      setChapters(cs);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // 章节 number 集合，供「逾期」判定 + 表单章节下拉
  const chapterOptions = useMemo(
    () =>
      [...chapters]
        .sort((a, b) => a.number - b.number)
        .map((c) => ({ id: c.chapter_id, label: `第 ${c.number} 章 ${c.title ? `· ${c.title}` : ''}` })),
    [chapters],
  );

  const chapterNumberById = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of chapters) m.set(c.chapter_id, c.number);
    return m;
  }, [chapters]);

  const visibleHooks =
    hookFilter === 'all' ? hooks : hooks.filter((h) => h.status === hookFilter);

  const handleDeleteHook = (id: string) => {
    setPendingDeleteHookId(id);
  };
  const handleDeleteDebt = (id: string) => {
    setPendingDeleteDebtId(id);
  };
  const doDeleteHook = async (id: string) => {
    setPendingDeleteHookId(null);
    try {
      await hooksApi.delete(id);
      await reload();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '删除失败');
    }
  };
  const doDeleteDebt = async (id: string) => {
    setPendingDeleteDebtId(null);
    try {
      await debtsApi.delete(id);
      await reload();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '删除失败');
    }
  };

  return (
    <div data-testid="ledger-tab">
      <div className="toolbar">
        <div className="muted small">
          伏笔台账（PRD §21，五态机）+ 叙事债务（PRD §22，四态机）。
          管理面（人工维护），不与 state 快照自动同步。
        </div>
      </div>

      <ErrorBanner>{err}</ErrorBanner>

      {loading ? <div className="muted">加载中…</div> : null}

      <div className="layout-2col">
        {/* ----------------- Hook 台账 ----------------- */}
        <div>
          <div className="detail-pane__title">伏笔台账 Hooks</div>

          <div
            className="toolbar"
            style={{ marginTop: 8, marginBottom: 8 }}
          >
            <select
              value={hookFilter}
              onChange={(e) => setHookFilter(e.target.value as 'all' | HookStatus)}
              data-testid="hook-status-filter"
            >
              <option value="all">全部状态</option>
              {HOOK_STATUS_VALUES.map((s) => (
                <option key={s} value={s}>
                  {HOOK_STATUS_LABEL[s]}
                </option>
              ))}
            </select>
            <div className="toolbar__spacer" />
            <button
              className="btn btn--primary btn--sm"
              onClick={() => setCreatingHook(true)}
              data-testid="create-hook-btn"
            >
              + 新建伏笔
            </button>
          </div>

          {!loading && visibleHooks.length === 0 ? (
            <EmptyState
              title={hookFilter === 'all' ? '还没有伏笔' : '该状态下没有伏笔'}
              hint="新建伏笔记录埋点 / 预期兑现 / 实际兑现章节。"
            />
          ) : (
            <table className="table" data-testid="hook-table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>状态</th>
                  <th>重要度</th>
                  <th>引入章节</th>
                  <th>预期兑现</th>
                  <th>实际兑现</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {visibleHooks.map((h) => {
                  const overdue = isHookOverdue(h, chapters);
                  return (
                    <tr
                      key={h.hook_id}
                      style={
                        overdue
                          ? { background: 'rgba(217, 69, 69, 0.08)' }
                          : undefined
                      }
                      data-testid="hook-row"
                    >
                      <td>
                        {h.name}
                        {overdue ? (
                          <span
                            className="badge badge--chapter-failed"
                            style={{ marginLeft: 8 }}
                            data-testid="hook-overdue-badge"
                          >
                            逾期
                          </span>
                        ) : null}
                      </td>
                      <td>
                        <span className={HOOK_STATUS_CLASS[h.status]}>
                          {HOOK_STATUS_LABEL[h.status]}
                        </span>
                      </td>
                      <td>{h.importance.toFixed(2)}</td>
                      <td className="muted small">
                        {chapterNumberById.get(h.introduced_chapter_id ?? '') !== undefined
                          ? `第 ${chapterNumberById.get(h.introduced_chapter_id!)} 章`
                          : '—'}
                      </td>
                      <td className="muted small">
                        {chapterNumberById.get(h.expected_payoff_chapter_id ?? '') !== undefined
                          ? `第 ${chapterNumberById.get(h.expected_payoff_chapter_id!)} 章`
                          : '—'}
                      </td>
                      <td className="muted small">
                        {chapterNumberById.get(h.payoff_chapter_id ?? '') !== undefined
                          ? `第 ${chapterNumberById.get(h.payoff_chapter_id!)} 章`
                          : '—'}
                      </td>
                      <td className="right">
                        <button
                          className="btn btn--sm"
                          onClick={() => setEditingHook(h)}
                        >
                          编辑
                        </button>
                        <button
                          className="btn btn--sm btn--danger"
                          style={{ marginLeft: 6 }}
                          onClick={() => void handleDeleteHook(h.hook_id)}
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* ----------------- Debt 面板 ----------------- */}
        <div>
          <div className="detail-pane__title">叙事债务 Debts</div>

          <div
            className="toolbar"
            style={{ marginTop: 8, marginBottom: 8 }}
          >
            <span className="muted small">共 {debts.length} 条</span>
            <div className="toolbar__spacer" />
            <button
              className="btn btn--primary btn--sm"
              onClick={() => setCreatingDebt(true)}
              data-testid="create-debt-btn"
            >
              + 新建债务
            </button>
          </div>

          {!loading && debts.length === 0 ? (
            <EmptyState
              title="还没有债务"
              hint="新建债务记录已承诺但尚未处理的问题。"
            />
          ) : (
            <table className="table" data-testid="debt-table">
              <thead>
                <tr>
                  <th>描述</th>
                  <th>严重度</th>
                  <th>状态</th>
                  <th>创建章节</th>
                  <th>截止章节</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {debts.map((d) => (
                  <tr key={d.debt_id}>
                    <td style={{ maxWidth: 360 }}>{d.description}</td>
                    <td>{d.severity.toFixed(2)}</td>
                    <td>
                      <span className={DEBT_STATUS_CLASS[d.status]}>
                        {DEBT_STATUS_LABEL[d.status]}
                      </span>
                    </td>
                    <td className="muted small">
                      {chapterNumberById.get(d.created_chapter_id ?? '') !== undefined
                        ? `第 ${chapterNumberById.get(d.created_chapter_id!)} 章`
                        : '—'}
                    </td>
                    <td className="muted small">
                      {chapterNumberById.get(d.deadline_chapter_id ?? '') !== undefined
                        ? `第 ${chapterNumberById.get(d.deadline_chapter_id!)} 章`
                        : '—'}
                    </td>
                    <td className="right">
                      <button
                        className="btn btn--sm"
                        onClick={() => setEditingDebt(d)}
                      >
                        编辑
                      </button>
                      <button
                        className="btn btn--sm btn--danger"
                        style={{ marginLeft: 6 }}
                        onClick={() => void handleDeleteDebt(d.debt_id)}
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {creatingHook ? (
        <HookFormModal
          title="新建伏笔"
          chapters={chapterOptions}
          onCancel={() => setCreatingHook(false)}
          onSubmit={async (p) => {
            await hooksApi.create(projectId, p as HookCreatePayload);
            setCreatingHook(false);
            await reload();
          }}
        />
      ) : null}
      {editingHook ? (
        <HookFormModal
          title={`编辑伏笔：${editingHook.name}`}
          chapters={chapterOptions}
          initial={editingHook}
          onCancel={() => setEditingHook(null)}
          onSubmit={async (p) => {
            await hooksApi.update(editingHook.hook_id, p);
            setEditingHook(null);
            await reload();
          }}
        />
      ) : null}
      {creatingDebt ? (
        <DebtFormModal
          title="新建债务"
          chapters={chapterOptions}
          onCancel={() => setCreatingDebt(false)}
          onSubmit={async (p) => {
            await debtsApi.create(projectId, p as DebtCreatePayload);
            setCreatingDebt(false);
            await reload();
          }}
        />
      ) : null}
      {editingDebt ? (
        <DebtFormModal
          title={`编辑债务：${editingDebt.description.slice(0, 24)}…`}
          chapters={chapterOptions}
          initial={editingDebt}
          onCancel={() => setEditingDebt(null)}
          onSubmit={async (p) => {
            await debtsApi.update(editingDebt.debt_id, p);
            setEditingDebt(null);
            await reload();
          }}
        />
      ) : null}

      {pendingDeleteHookId ? (
        <ConfirmDialog
          open={true}
          title="删除伏笔"
          body="确认删除该伏笔？"
          confirmText="删除"
          danger
          testId="hook-delete-confirm"
          onCancel={() => setPendingDeleteHookId(null)}
          onConfirm={() => void doDeleteHook(pendingDeleteHookId)}
        />
      ) : null}
      {pendingDeleteDebtId ? (
        <ConfirmDialog
          open={true}
          title="删除叙事债务"
          body="确认删除该债务？"
          confirmText="删除"
          danger
          testId="debt-delete-confirm"
          onCancel={() => setPendingDeleteDebtId(null)}
          onConfirm={() => void doDeleteDebt(pendingDeleteDebtId)}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Hook 表单 modal
// ---------------------------------------------------------------------------

interface HookFormProps {
  title: string;
  chapters: Array<{ id: string; label: string }>;
  initial?: Hook;
  onCancel: () => void;
  onSubmit: (payload: HookUpdatePayload) => Promise<void>;
}

const MODAL_STYLE_BACKDROP: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(15,20,35,0.4)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 100,
};

function HookFormModal({ title, chapters, initial, onCancel, onSubmit }: HookFormProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [importance, setImportance] = useState<number>(initial?.importance ?? 0.5);
  const [status, setStatus] = useState<HookStatus>(initial?.status ?? 'OPEN');
  const [introId, setIntroId] = useState<string>(initial?.introduced_chapter_id ?? '');
  const [expectedId, setExpectedId] = useState<string>(initial?.expected_payoff_chapter_id ?? '');
  const [payoffId, setPayoffId] = useState<string>(initial?.payoff_chapter_id ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 编辑模式：状态机的「合法后继」集合作为下拉选项
  const allowedNext = initial ? getNextHookStatuses(initial.status) : safeGetNextHookStatuses(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!name.trim()) {
      setErr('名称不能为空');
      return;
    }
    setSubmitting(true);
    try {
      const payload: HookCreatePayload | HookUpdatePayload = {
        name: name.trim(),
        importance,
        status,
        introduced_chapter_id: introId || null,
        expected_payoff_chapter_id: expectedId || null,
        payoff_chapter_id: payoffId || null,
      };
      await onSubmit(payload);
    } catch (e2: unknown) {
      setErr(e2 instanceof Error ? e2.message : '保存失败');
      setSubmitting(false);
    }
  };

  return (
    <div role="dialog" aria-modal="true" style={MODAL_STYLE_BACKDROP} onClick={onCancel}>
      <form
        className="card"
        style={{ width: 560, maxWidth: '92vw', maxHeight: '90vh', overflowY: 'auto' }}
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <div className="section-title">{title}</div>
        <ErrorBanner>{err}</ErrorBanner>

        <div className="form-row">
          <label>名称 *</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            data-testid="hook-name-input"
          />
        </div>

        <div className="form-row">
          <label>
            重要度 importance（0.0 - 1.0）: <strong>{importance.toFixed(2)}</strong>
          </label>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={importance}
            onChange={(e) => setImportance(parseFloat(e.target.value))}
            data-testid="hook-importance-input"
          />
        </div>

        <div className="form-row">
          <label>状态</label>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as HookStatus)}
            data-testid="hook-status-select"
          >
            {(initial ? allowedNext : HOOK_STATUS_VALUES).map((s) => (
              <option key={s} value={s}>
                {HOOK_STATUS_LABEL[s]} ({s})
              </option>
            ))}
          </select>
          {initial ? (
            <div className="muted small">
              当前状态 {HOOK_STATUS_LABEL[initial.status]}（{initial.status}）→ 仅显示合法迁移
            </div>
          ) : null}
        </div>

        <ChapterSelect label="引入章节" value={introId} onChange={setIntroId} chapters={chapters} testId="hook-intro" />
        <ChapterSelect label="预期兑现章节" value={expectedId} onChange={setExpectedId} chapters={chapters} testId="hook-expected" />
        <ChapterSelect label="实际兑现章节（可空）" value={payoffId} onChange={setPayoffId} chapters={chapters} testId="hook-payoff" />

        <div
          style={{
            display: 'flex',
            gap: 8,
            marginTop: 12,
            justifyContent: 'flex-end',
          }}
        >
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button type="submit" className="btn btn--primary" disabled={submitting} data-testid="hook-save">
            {submitting ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Debt 表单 modal
// ---------------------------------------------------------------------------

interface DebtFormProps {
  title: string;
  chapters: Array<{ id: string; label: string }>;
  initial?: Debt;
  onCancel: () => void;
  onSubmit: (payload: DebtUpdatePayload) => Promise<void>;
}

function DebtFormModal({ title, chapters, initial, onSubmit, onCancel }: DebtFormProps) {
  const [description, setDescription] = useState(initial?.description ?? '');
  const [severity, setSeverity] = useState<number>(initial?.severity ?? 0.5);
  const [status, setStatus] = useState<DebtStatus>(initial?.status ?? 'open');
  const [createdId, setCreatedId] = useState<string>(initial?.created_chapter_id ?? '');
  const [deadlineId, setDeadlineId] = useState<string>(initial?.deadline_chapter_id ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!description.trim()) {
      setErr('描述不能为空');
      return;
    }
    setSubmitting(true);
    try {
      const payload: DebtCreatePayload | DebtUpdatePayload = {
        description: description.trim(),
        severity,
        status,
        created_chapter_id: createdId || null,
        deadline_chapter_id: deadlineId || null,
      };
      await onSubmit(payload);
    } catch (e2: unknown) {
      setErr(e2 instanceof Error ? e2.message : '保存失败');
      setSubmitting(false);
    }
  };

  return (
    <div role="dialog" aria-modal="true" style={MODAL_STYLE_BACKDROP} onClick={onCancel}>
      <form
        className="card"
        style={{ width: 560, maxWidth: '92vw', maxHeight: '90vh', overflowY: 'auto' }}
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <div className="section-title">{title}</div>
        <ErrorBanner>{err}</ErrorBanner>

        <div className="form-row">
          <label>描述 *</label>
          <textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            required
            data-testid="debt-description-input"
          />
        </div>

        <div className="form-row">
          <label>
            严重度 severity（0.0 - 1.0）: <strong>{severity.toFixed(2)}</strong>
          </label>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={severity}
            onChange={(e) => setSeverity(parseFloat(e.target.value))}
            data-testid="debt-severity-input"
          />
        </div>

        <div className="form-row">
          <label>状态</label>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as DebtStatus)}
            data-testid="debt-status-select"
          >
            {(initial ? getNextDebtStatuses(initial.status) : safeGetNextDebtStatuses(null)).map((s) => (
              <option key={s} value={s}>
                {DEBT_STATUS_LABEL[s]} ({s})
              </option>
            ))}
          </select>
        </div>

        <ChapterSelect label="创建章节" value={createdId} onChange={setCreatedId} chapters={chapters} testId="debt-created" />
        <ChapterSelect label="截止章节" value={deadlineId} onChange={setDeadlineId} chapters={chapters} testId="debt-deadline" />

        <div
          style={{
            display: 'flex',
            gap: 8,
            marginTop: 12,
            justifyContent: 'flex-end',
          }}
        >
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button type="submit" className="btn btn--primary" disabled={submitting} data-testid="debt-save">
            {submitting ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chapter 下拉选择器（公共组件）
// ---------------------------------------------------------------------------

function ChapterSelect({
  label,
  value,
  onChange,
  chapters,
  testId,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  chapters: Array<{ id: string; label: string }>;
  testId?: string;
}) {
  return (
    <div className="form-row">
      <label>{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
      >
        <option value="">— 未指定 —</option>
        {chapters.map((c) => (
          <option key={c.id} value={c.id}>
            {c.label}
          </option>
        ))}
      </select>
    </div>
  );
}