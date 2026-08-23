import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { chaptersApi, qualityApi, workflowsApi } from '../api/endpoints';
import type {
  Chapter,
  Draft,
  QualityReport,
  WorkflowRun,
  WorkflowStartResponse,
} from '../api/types';
import { QualityPanel } from '../components/QualityPanel';
import { ErrorBanner, InfoBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import {
  ChapterStatusBadge,
  WorkflowRunStatusBadge,
} from '../components/ChapterStatusBadge';
import { ApprovalCard } from '../components/ApprovalCard';
import { useApiCall } from '../hooks/useApiCall';
import { usePoll } from '../hooks/usePoll';
import {
  EXPECTED_STATUS,
  countHighRiskChanges,
  getButtonAvailability,
  isRunForChapter,
  pickActiveRun,
  pickLatestRun,
} from '../utils/chapterState';
import { formatDateTime, formatJson, tryParseJsonObject } from '../utils/format';
import { ApiError } from '../api/client';

export function ChapterDetailPage() {
  const { pid, cid } = useParams();
  const projectId = pid!;
  const chapterId = cid!;
  const navigate = useNavigate();

  // ---- chapter 基本信息 ----
  const chapterCall = useApiCall<Chapter>(
    () => chaptersApi.get(chapterId),
    [chapterId],
  );
  const chapter = chapterCall.data;

  // ---- drafts 列表（version DESC） ----
  const draftsCall = useApiCall<Draft[]>(
    () => chaptersApi.listDrafts(chapterId),
    [chapterId],
  );

  // ---- 最新 quality report（不带 NotFound 报错；404 容错为 null） ----
  const [qualityReport, setQualityReport] = useState<QualityReport | null>(null);
  const [qualityLoading, setQualityLoading] = useState(false);
  const [qualityError, setQualityError] = useState<string | null>(null);
  const reloadQuality = useCallback(async () => {
    setQualityLoading(true);
    setQualityError(null);
    try {
      const r = await qualityApi.latest(chapterId);
      setQualityReport(r);
    } catch (e: unknown) {
      // 404 → 该 chapter 尚无 report，不视作错误
      if (e instanceof ApiError && e.status === 404) {
        setQualityReport(null);
        setQualityError(null);
      } else {
        setQualityError(e instanceof Error ? e.message : '加载 quality 失败');
      }
    } finally {
      setQualityLoading(false);
    }
  }, [chapterId]);

  useEffect(() => {
    void reloadQuality();
  }, [reloadQuality]);

  // ---- workflow runs（按 started_at DESC） ----
  const runsCall = useApiCall<WorkflowRun[]>(
    () => workflowsApi.listByProject(projectId),
    [projectId],
  );
  const chapterRuns = useMemo(
    () =>
      (runsCall.data ?? [])
        .filter((r) => isRunForChapter(r, chapterId))
        .slice()
        .sort((a, b) =>
          a.started_at < b.started_at ? 1 : a.started_at > b.started_at ? -1 : 0,
        ),
    [runsCall.data, chapterId],
  );

  // ---- 选中 run（默认最新） ----
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  // runs 重新加载后：若没选中，则选最新一条；若有但已不存在则重置
  useEffect(() => {
    if (chapterRuns.length === 0) {
      setSelectedRunId(null);
      return;
    }
    if (!selectedRunId || !chapterRuns.find((r) => r.run_id === selectedRunId)) {
      setSelectedRunId(pickLatestRun(chapterRuns, chapterId)?.run_id ?? null);
    }
  }, [chapterRuns, selectedRunId, chapterId]);

  const selectedRunSummary =
    chapterRuns.find((r) => r.run_id === selectedRunId) ?? null;

  // ---- 轮询选中 run：RUNNING 时 2s 拉一次，PAUSED/终态停 ----
  const activeRun = pickActiveRun(chapterRuns, chapterId);
  const pollTargetId = activeRun ? activeRun.run_id : null;
  const poll = usePoll<WorkflowRun>({
    fn: () => workflowsApi.get(pollTargetId as string),
    intervalMs: 2000,
    enabled: !!pollTargetId,
    stopWhen: (latest) => {
      if (!latest) return true;
      return latest.status !== 'RUNNING' && latest.status !== 'PENDING';
    },
    stopOnError: true,
    onResult: () => {
      // 轮询到结果后同步刷新 chapter / drafts / runs
      void chapterCall.reload();
      void draftsCall.reload();
      void runsCall.reload();
    },
  });

  // 详情（节点时间线）只在选中 run 后再拉
  const detail = useApiCall<WorkflowRun>(
    () => workflowsApi.get(selectedRunId as string),
    [selectedRunId],
  );
  const detailRun = detail.data;
  const pausePayload =
    (detailRun?.checkpoint_json as Record<string, unknown> | undefined)?.['__pause_payload__'] as
      | Record<string, unknown>
      | undefined;

  // ---- 顶部工作流按钮 ----
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const handleStartWorkflow = useCallback(
    async (
      action: 'plan' | 'write' | 'review' | 'commit',
      payload?: { author_intent?: string; target_word_count?: number },
    ) => {
      setActionErr(null);
      setSubmitting(true);
      try {
        let resp: WorkflowStartResponse;
        if (action === 'plan') resp = await workflowsApi.startPlan(projectId, chapterId, payload);
        else if (action === 'write') resp = await workflowsApi.startWrite(projectId, chapterId, payload);
        else if (action === 'review') resp = await workflowsApi.startReview(projectId, chapterId, payload);
        else resp = await workflowsApi.startCommit(projectId, chapterId, payload);
        // 启动后立刻刷新 + 选中该 run
        setSelectedRunId(resp.run_id);
        await Promise.all([chapterCall.reload(), runsCall.reload(), draftsCall.reload()]);
      } catch (e: unknown) {
        setActionErr(e instanceof Error ? e.message : '启动失败');
      } finally {
        setSubmitting(false);
      }
    },
    [projectId, chapterId, chapterCall, runsCall, draftsCall],
  );

  const handleResume = useCallback(
    async (approved: boolean) => {
      if (!selectedRunSummary) return;
      setActionErr(null);
      setSubmitting(true);
      try {
        await workflowsApi.resume(selectedRunSummary.run_id, {
          human_input: { approved },
        });
        await Promise.all([chapterCall.reload(), runsCall.reload(), draftsCall.reload(), detail.reload()]);
      } catch (e: unknown) {
        setActionErr(e instanceof Error ? e.message : '审批失败');
        throw e;
      } finally {
        setSubmitting(false);
      }
    },
    [selectedRunSummary, chapterCall, runsCall, draftsCall, detail],
  );

  // 渲染
  return (
    <div>
      <div className="toolbar">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Link to={`/projects/${projectId}/chapters`} className="muted small">
            ← 返回章节列表
          </Link>
          {chapter ? (
            <>
              <span className="muted">/</span>
              <strong>
                第 {chapter.number} 章 · {chapter.title ?? '（未命名）'}
              </strong>
              <ChapterStatusBadge status={chapter.status} />
            </>
          ) : null}
        </div>
      </div>

      <ErrorBanner>{chapterCall.error || actionErr}</ErrorBanner>

      {chapter ? (
        <ChapterHeader
          chapter={chapter}
          activeRunStatus={activeRun?.status ?? null}
          submitting={submitting}
          onStart={handleStartWorkflow}
        />
      ) : chapterCall.loading ? (
        <div className="muted">加载章节中…</div>
      ) : null}

      {/* Workflow 面板（含 runs 列表 / 时间线 / 审批卡片） */}
      {chapter ? (
        <div className="panel-grid" style={{ marginTop: 16 }}>
          <WorkflowPanel
            runs={chapterRuns}
            selectedRunId={selectedRunId}
            onSelect={(id) => setSelectedRunId(id)}
            detailRun={detailRun}
            detailLoading={detail.loading}
            detailError={detail.error}
            pollError={poll.error}
            pausePayload={pausePayload}
            submitting={submitting}
            onApprove={handleResume}
          />
          <DraftsPanel
            chapterId={chapterId}
            chapterStatus={chapter.status}
            drafts={draftsCall.data ?? []}
            draftsLoading={draftsCall.loading}
            draftsError={draftsCall.error}
            onCreated={async () => {
              await draftsCall.reload();
            }}
          />
        </div>
      ) : null}

      {chapter ? (
        <div style={{ marginTop: 16 }}>
          <PlanPanel chapter={chapter} onUpdated={() => chapterCall.reload()} />
        </div>
      ) : null}

      {chapter ? (
        <div style={{ marginTop: 16 }}>
          <QualityPanel
            chapterId={chapterId}
            report={qualityReport}
            loading={qualityLoading}
            error={qualityError}
            onEvaluated={(rep) => {
              setQualityReport(rep);
              void chapterCall.reload();
            }}
          />
        </div>
      ) : null}

      {chapter ? (
        <div style={{ marginTop: 16 }}>
          <button
            className="btn"
            onClick={() => {
              if (window.confirm(`确认删除第 ${chapter.number} 章？`)) {
                void chaptersApi.delete(chapterId).then(() => {
                  navigate(`/projects/${projectId}/chapters`);
                });
              }
            }}
          >
            删除章节
          </button>
        </div>
      ) : null}
    </div>
  );
}

// =============================================================================
// 子组件
// =============================================================================

function ChapterHeader({
  chapter,
  activeRunStatus,
  submitting,
  onStart,
}: {
  chapter: Chapter;
  activeRunStatus: 'PENDING' | 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | null;
  submitting: boolean;
  onStart: (
    action: 'plan' | 'write' | 'review' | 'commit',
    payload?: { author_intent?: string; target_word_count?: number },
  ) => Promise<void>;
}) {
  const activeRunInfo =
    activeRunStatus === 'RUNNING' || activeRunStatus === 'PENDING'
      ? { status: activeRunStatus as 'RUNNING' | 'PENDING' }
      : null;

  const buttons: Array<{
    action: 'plan' | 'write' | 'review' | 'commit';
    title: string;
    hint: string;
  }> = [
    { action: 'plan', title: '生成计划', hint: 'Director 跑出 plan_json' },
    { action: 'write', title: '写正文', hint: 'Writer 生成 draft' },
    { action: 'review', title: '审校', hint: 'basic_checks + 作者审批' },
    { action: 'commit', title: '提交', hint: 'Observer delta → COMMIT' },
  ];

  return (
    <div className="panel" data-testid="chapter-header">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ fontSize: 18, fontWeight: 600 }}>
          第 {chapter.number} 章 · {chapter.title ?? '（未命名）'}
        </div>
        <ChapterStatusBadge status={chapter.status} />
        <div style={{ flex: 1 }} />
        <span className="muted small">ID：{chapter.chapter_id}</span>
      </div>

      <div className="panel__section">
        <div className="panel__section-title">工作流操作</div>
        <div className="workflow-actions">
          {buttons.map((b) => {
            const avail = getButtonAvailability(b.action, {
              chapterStatus: chapter.status,
              activeRun: activeRunInfo,
            });
            return (
              <button
                key={b.action}
                className="btn"
                disabled={!avail.enabled || submitting}
                title={avail.reason ?? undefined}
                data-testid={`wf-btn-${b.action}`}
                onClick={() => void onStart(b.action)}
              >
                <span className="btn__title">{b.title}</span>
                <span className="btn__hint">
                  需要状态：{EXPECTED_STATUS[b.action].join(' / ')}
                </span>
              </button>
            );
          })}
        </div>
        <div className="muted small" style={{ marginTop: 6 }}>
          状态机：仅当章节处于对应状态时可点击；同一时间只能有一个 RUNNING run。
        </div>
      </div>
    </div>
  );
}

// ---- PlanPanel ----
function PlanPanel({
  chapter,
  onUpdated,
}: {
  chapter: Chapter;
  onUpdated: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState<string>(() =>
    formatJson(chapter.plan_json ?? {}),
  );
  const [err, setErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // chapter 重新加载后同步 text
  useEffect(() => {
    if (!editing) setText(formatJson(chapter.plan_json ?? {}));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapter.chapter_id, chapter.plan_json, chapter.updated_at]);

  const handleSave = async () => {
    setErr(null);
    const parsed = tryParseJsonObject(text);
    if (!parsed.ok) {
      setErr(parsed.error);
      return;
    }
    setSubmitting(true);
    try {
      await chaptersApi.update(chapter.chapter_id, { plan_json: parsed.value });
      setEditing(false);
      onUpdated();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="panel" data-testid="plan-panel">
      <div className="panel__title">
        计划（plan_json）
        <div style={{ flex: 1 }} />
        {!editing ? (
          <button className="btn btn--sm" onClick={() => setEditing(true)}>
            编辑
          </button>
        ) : (
          <>
            <button
              className="btn btn--sm"
              onClick={() => {
                setEditing(false);
                setText(formatJson(chapter.plan_json ?? {}));
                setErr(null);
              }}
              disabled={submitting}
            >
              取消
            </button>
            <button
              className="btn btn--sm btn--primary"
              onClick={handleSave}
              disabled={submitting}
              data-testid="plan-save"
            >
              {submitting ? '保存中…' : '保存'}
            </button>
          </>
        )}
      </div>

      <ErrorBanner>{err}</ErrorBanner>

      {!editing ? (
        <pre className="json-block">
          {formatJson(chapter.plan_json) || '（空）'}
        </pre>
      ) : (
        <>
          <div className="muted small" style={{ marginBottom: 6 }}>
            保存前会用 JSON.parse 校验；非对象会拒绝。
          </div>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={18}
            style={{
              width: '100%',
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              padding: 8,
              borderRadius: 6,
              border: '1px solid var(--color-border-strong)',
            }}
            data-testid="plan-textarea"
          />
        </>
      )}
    </div>
  );
}

// ---- WorkflowPanel ----
function WorkflowPanel({
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
  onApprove: (approved: boolean) => Promise<void> | void;
}) {
  return (
    <div className="panel" data-testid="workflow-panel">
      <div className="panel__title">Workflow runs</div>

      <div className="panel__section">
        <div className="panel__section-title">本章相关 run（按 started_at DESC）</div>
        {runs.length === 0 ? (
          <EmptyState title="本章暂无 run" hint="点击上方按钮启动一个工作流。" />
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
                  {/* 后端 list_runs 不返回 workflow_name（仅 GET /runs/{id} 含 nodes 但也无 workflow_name）；
                      前端以短 ID 形式展示，鼠标悬浮看完整 run_id / workflow_id。 */}
                  run · {r.run_id.slice(0, 12)}…
                </span>
                <WorkflowRunStatusBadge status={r.status} />
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
        <div className="panel__section-title">节点时间线（选中 run 的 checkpoint_json）</div>
        <ErrorBanner>{detailError || pollError}</ErrorBanner>
        {selectedRunId ? (
          detailLoading && !detailRun ? (
            <div className="muted">加载中…</div>
          ) : detailRun ? (
            <RunTimeline run={detailRun} />
          ) : null
        ) : (
          <div className="muted small">从上方列表选择一个 run 查看节点时间线。</div>
        )}
      </div>

      {/* 审批卡片：PAUSED + 含 __pause_payload__ 时渲染 */}
      {detailRun && detailRun.status === 'PAUSED' && pausePayload ? (
        <div className="panel__section">
          <div className="panel__section-title">审批</div>
          <ApprovalCard
            runId={detailRun.run_id}
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
    </div>
  );
}

function RunTimeline({ run }: { run: WorkflowRun }) {
  if (!run.nodes || run.nodes.length === 0) {
    return (
      <div className="muted small">
        暂无节点明细（可能 run 刚启动或后端未返回 nodes）。
      </div>
    );
  }
  return (
    <ol style={{ paddingLeft: 18, margin: 0 }}>
      {run.nodes.map((n) => (
        <li key={n.node_run_id} style={{ marginBottom: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <strong>{n.node_id}</strong>
            <NodeStatusBadge status={n.status} />
            <span className="muted small">
              {formatDateTime(n.started_at)}
              {n.ended_at ? ` → ${formatDateTime(n.ended_at)}` : ''}
              {n.latency_ms != null ? ` · ${n.latency_ms}ms` : ''}
            </span>
          </div>
          {n.error ? (
            <ErrorBanner>{n.error}</ErrorBanner>
          ) : null}
          {n.output_json && typeof n.output_json === 'object' ? (
            <details style={{ marginTop: 4 }}>
              <summary className="muted small">output_json</summary>
              <pre className="json-block" style={{ marginTop: 4 }}>
                {formatJson(n.output_json)}
              </pre>
            </details>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function NodeStatusBadge({
  status,
}: {
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'SKIPPED';
}) {
  const cls =
    status === 'COMPLETED'
      ? 'badge badge--chapter-committed'
      : status === 'RUNNING'
      ? 'badge badge--chapter-running'
      : status === 'FAILED'
      ? 'badge badge--chapter-failed'
      : status === 'SKIPPED'
      ? 'badge badge--archived'
      : 'badge badge--chapter-planned';
  return <span className={cls}>{status}</span>;
}

// ---- DraftsPanel ----
function DraftsPanel({
  chapterId,
  chapterStatus,
  drafts,
  draftsLoading,
  draftsError,
  onCreated,
}: {
  chapterId: string;
  chapterStatus: Chapter['status'];
  drafts: Draft[];
  draftsLoading: boolean;
  draftsError: string | null;
  onCreated: () => Promise<void> | void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editorText, setEditorText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 默认选中最新一条
  useEffect(() => {
    if (drafts.length === 0) {
      setSelectedId(null);
      setEditingId(null);
      return;
    }
    if (!selectedId || !drafts.find((d) => d.draft_id === selectedId)) {
      setSelectedId(drafts[0].draft_id);
    }
  }, [drafts, selectedId]);

  const selected = drafts.find((d) => d.draft_id === selectedId) ?? null;

  const canCreateDraft =
    chapterStatus === 'DRAFTED' || chapterStatus === 'REVIEWED';

  const handleStartEdit = () => {
    if (!selected) return;
    setEditorText(selected.content);
    setEditingId(selected.draft_id);
    setErr(null);
  };

  const handleSave = async () => {
    if (!editorText.trim()) {
      setErr('草稿内容不能为空');
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      await chaptersApi.createDraft(chapterId, { content: editorText });
      setEditingId(null);
      setEditorText('');
      await onCreated();
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        setErr(`保存失败（${e.status}）：${e.detail}`);
      } else {
        setErr(e instanceof Error ? e.message : '保存失败');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="panel" data-testid="drafts-panel">
      <div className="panel__title">
        草稿（drafts）
        <div style={{ flex: 1 }} />
        {editingId ? (
          <>
            <button
              className="btn btn--sm"
              onClick={() => {
                setEditingId(null);
                setEditorText('');
                setErr(null);
              }}
              disabled={submitting}
            >
              取消
            </button>
            <button
              className="btn btn--sm btn--primary"
              onClick={handleSave}
              disabled={submitting}
              data-testid="draft-save"
            >
              {submitting ? '保存中…' : '保存为新版本'}
            </button>
          </>
        ) : (
          <button
            className="btn btn--sm btn--primary"
            disabled={!canCreateDraft}
            title={
              canCreateDraft
                ? '把当前选中版本载入编辑器，编辑后保存为新 draft 版本'
                : `草稿仅在 DRAFTED / REVIEWED 时可新增；当前 ${chapterStatus}`
            }
            onClick={handleStartEdit}
            data-testid="draft-edit-btn"
          >
            人工改稿
          </button>
        )}
      </div>

      <ErrorBanner>{err}</ErrorBanner>
      <ErrorBanner>{draftsError}</ErrorBanner>
      {!canCreateDraft ? (
        <InfoBanner>
          当前章节状态 {chapterStatus}：仅 DRAFTED / REVIEWED 允许新增 draft。
        </InfoBanner>
      ) : null}

      {draftsLoading ? (
        <div className="muted">加载中…</div>
      ) : drafts.length === 0 ? (
        <EmptyState title="还没有 draft" hint="运行 chapter-write 或人工改稿会生成版本。" />
      ) : (
        <>
          <div className="panel__section">
            <div className="panel__section-title">版本列表</div>
            <div className="kv-list">
              {drafts.map((d) => (
                <div
                  key={d.draft_id}
                  className={`kv-list__row ${d.draft_id === selectedId ? 'kv-list__row--active' : ''}`}
                  onClick={() => {
                    setSelectedId(d.draft_id);
                    setEditingId(null);
                  }}
                  data-testid={`draft-row-${d.draft_id}`}
                >
                  <span className="kv-list__title">v{d.version}</span>
                  <span className="muted small">{d.created_by}</span>
                  <span className="kv-list__meta">
                    {formatDateTime(d.created_at)}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="panel__section">
            <div className="panel__section-title">
              {editingId ? '编辑内容（保存后会创建新版本）' : '当前版本内容'}
            </div>
            {editingId ? (
              <textarea
                value={editorText}
                onChange={(e) => setEditorText(e.target.value)}
                rows={16}
                style={{
                  width: '100%',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                  padding: 8,
                  borderRadius: 6,
                  border: '1px solid var(--color-border-strong)',
                }}
                data-testid="draft-textarea"
              />
            ) : selected ? (
              <pre
                className="prose-block"
                data-testid="draft-content"
              >
                {selected.content}
              </pre>
            ) : (
              <div className="muted">从左侧选择一份 draft 查看。</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
