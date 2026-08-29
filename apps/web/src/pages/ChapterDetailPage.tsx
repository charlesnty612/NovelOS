import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { chaptersApi, modelProfilesApi, qualityApi, workflowsApi } from '../api/endpoints';
import type {
  Chapter,
  Draft,
  ModelProfile,
  QualityGateCheckpoint,
  QualityReport,
  WorkflowRun,
  WorkflowStartPayload,
  WorkflowStartResponse,
} from '../api/types';
import { QualityPanel } from '../components/QualityPanel';
import { ContextPreviewPanel } from '../components/ContextPreviewPanel';
import { ContinuePanel } from '../components/ContinuePanel';
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
import { extractPausePayload } from '../utils/pausePayload';
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
    onResult: (latest) => {
      // 轮询到结果后同步刷新 chapter / drafts / runs
      void chapterCall.reload();
      void draftsCall.reload();
      void runsCall.reload();
      // 异步化后：run 停到 PAUSED 时，detail（checkpoint 详情，含 pause_payload）
      // 是在 RUNNING 阶段拉的、不含暂停载荷——必须重拉，审批卡才渲染得出来。
      // （detail 声明在本 hook 之后，闭包调用时机在渲染完成后，安全。）
      if (latest.status === 'PAUSED') void detail.reload();
    },
  });

  // 详情（节点时间线）只在选中 run 后再拉
  const detail = useApiCall<WorkflowRun>(
    () => workflowsApi.get(selectedRunId as string),
    [selectedRunId],
  );
  const detailRun = detail.data;
  const pausePayload = extractPausePayload(detailRun?.checkpoint_json) ?? undefined;

  // V1.4：从选中 run 的 checkpoint_json 中提取 quality_gate 节点暴露字段（参照系消费
  // + 改稿引导）。无 quality_gate 节点时为 null；QualityPanel 按空态处理。
  const qualityGateCheckpoint = useMemo<QualityGateCheckpoint | null>(() => {
    const ckpt = detailRun?.checkpoint_json;
    if (!ckpt || typeof ckpt !== 'object') return null;
    const qg = (ckpt as Record<string, unknown>)['quality_gate'];
    if (!qg || typeof qg !== 'object') return null;
    const obj = qg as Record<string, unknown>;
    return {
      blocked: Boolean(obj['blocked']),
      mode: (typeof obj['mode'] === 'string' ? (obj['mode'] as string) : 'report'),
      reference_consumption:
        (obj['reference_consumption'] as QualityGateCheckpoint['reference_consumption']) ??
        { source: 'project_refs_dir', files: [], total_chars: 0, files_count: 0 },
      revision_guidance:
        (Array.isArray(obj['revision_guidance'])
          ? (obj['revision_guidance'] as QualityGateCheckpoint['revision_guidance'])
          : []) ?? [],
    };
  }, [detailRun?.checkpoint_json]);

  // ---- 顶部工作流按钮 ----
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [reviseLooping, setReviseLooping] = useState(false);
  const handleStartWorkflow = useCallback(
    async (
      action: 'plan' | 'write' | 'review' | 'commit',
      payload?: {
        author_intent?: string;
        target_word_count?: number;
        /** 已选档案 id（profile_id）；非空时并入 model_overrides[capability] */
        model_profile_id?: string | null;
        /** 写作模式（仅 write 生效）：true ⇒ 全新重写。临时字段，在此处剥离后
         *  按 action==='write' 决定是否写入请求 payload 的 fresh_write 键。 */
        fresh_write?: boolean;
      },
    ) => {
      // 防呆：章节已有 plan_json 时，「生成计划」需二次确认（覆盖会丢字数规划）
      if (action === 'plan' && chapter) {
        const plan = chapter.plan_json;
        const hasPlan =
          plan != null &&
          typeof plan === 'object' &&
          !Array.isArray(plan) &&
          Object.keys(plan).length > 0;
        if (hasPlan) {
          const ok = window.confirm(
            '章节已有计划，重新生成将覆盖当前计划（含字数规划），确定继续？',
          );
          if (!ok) return;
        }
      }
      setActionErr(null);
      setSubmitting(true);
      try {
        // 按次模型档案选择：plan/write/review → 对应 capability；commit 由 Observer 兜底
        // 不消耗 LLM，不透传 model_overrides（保留旧语义）。
        const capabilityByAction: Record<
          'plan' | 'write' | 'review' | 'commit',
          string | null
        > = {
          plan: 'reasoning',
          write: 'creative_writing',
          review: 'light',
          commit: null,
        };
        const capability = capabilityByAction[action];
        const profileId = (payload?.model_profile_id ?? '').trim();
        // 从入参 payload 中剥离临时字段 model_profile_id / fresh_write，避免下发给后端；
        // author_intent / target_word_count 等业务字段透传给后端。fresh_write 在下方按
        // action==='write' 决定是否并入请求 payload（业务字段）。
        const {
          model_profile_id: _omit,
          fresh_write: rawFreshWrite,
          ...basePayload
        } = payload ?? {};
        void _omit;
        const hasOverride = !!capability && !!profileId;
        // 写作模式：仅 write 动作支持；其他动作剥离后丢弃。
        const wantsFreshWrite =
          action === 'write' && rawFreshWrite === true;
        const hasBusinessFields =
          Object.keys(basePayload).length > 0 || wantsFreshWrite;
        // 无业务字段且无 override 时透传 undefined，保持向后兼容（与旧契约一致）。
        const requestPayload: WorkflowStartPayload | undefined =
          !hasOverride && !hasBusinessFields
            ? undefined
            : hasOverride
            ? {
                ...basePayload,
                model_overrides: { [capability as string]: profileId },
                ...(wantsFreshWrite ? { fresh_write: true } : {}),
              }
            : wantsFreshWrite
            ? ({ ...basePayload, fresh_write: true } as WorkflowStartPayload)
            : (basePayload as WorkflowStartPayload);
        let resp: WorkflowStartResponse;
        if (action === 'plan') resp = await workflowsApi.startPlan(projectId, chapterId, requestPayload);
        else if (action === 'write') resp = await workflowsApi.startWrite(projectId, chapterId, requestPayload);
        else if (action === 'review') resp = await workflowsApi.startReview(projectId, chapterId, requestPayload);
        else resp = await workflowsApi.startCommit(projectId, chapterId, requestPayload);
        // 启动后立刻刷新 + 选中该 run
        setSelectedRunId(resp.run_id);
        await Promise.all([chapterCall.reload(), runsCall.reload(), draftsCall.reload()]);
      } catch (e: unknown) {
        setActionErr(e instanceof Error ? e.message : '启动失败');
      } finally {
        setSubmitting(false);
      }
    },
    [projectId, chapterId, chapter, chapterCall, runsCall, draftsCall],
  );

  const handleResume = useCallback(
    async (
      approved: boolean,
      opts?: { revise?: boolean; note?: string },
    ) => {
      if (!selectedRunSummary) return;
      setActionErr(null);
      if (!approved || opts?.revise) setReviseLooping(true);
      setSubmitting(true);
      try {
        // 三态：approve / reject / revise（revise 时 run 以 FAILED(rejected-for-revision) 收尾，
        // chapter 保持 DRAFTED，note 落 plan_json.revision_note，改稿后可重跑 write/review）
        const human_input: { approved: boolean; revise?: boolean; note?: string } = {
          approved,
        };
        if (opts?.revise) {
          human_input['revise'] = true;
          if (opts.note) human_input['note'] = opts.note;
        }
        await workflowsApi.resume(selectedRunSummary.run_id, { human_input });
        await Promise.all([chapterCall.reload(), runsCall.reload(), draftsCall.reload(), detail.reload()]);
      } catch (e: unknown) {
        setActionErr(e instanceof Error ? e.message : '审批失败');
        throw e;
      } finally {
        setSubmitting(false);
        setReviseLooping(false);
      }
    },
    [selectedRunSummary, chapterCall, runsCall, draftsCall, detail],
  );

  // V1.5 / 横幅：构造「正在运行」实时详情。优先用 poll 拉到的最新详情（含 nodes /
  // current_node / workflow_name / started_at），否则退化为 activeRun 的 list 行
  // （list 不返回 nodes，组件内做退化文案）。submitting=true 时允许显示（按钮已点
  // 下但 list 还没刷出 RUNNING 行的过渡窗口）。
  const runningDetail: WorkflowRun | null = poll.data ?? activeRun ?? null;
  const isRunningBannerVisible =
    !!activeRun ||
    (submitting && !poll.data) ||
    (poll.data != null &&
      (poll.data.status === 'RUNNING' || poll.data.status === 'PENDING'));

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

      {/* V1.5：运行中横幅。RUNNING/PENDING 时显示节点进度 + 已运行时长；
          终态自动消失（FAILED 由 ErrorBanner 承载）。submitting 过渡期也显示，避免「按下无反馈」。 */}
      {reviseLooping ? (
        <div
          className="alert alert--info"
          data-testid="revise-loop-banner"
          role="status"
          style={{ marginTop: 12 }}
        >
          <div style={{ fontWeight: 600 }}>⏳ 自动改稿回路进行中</div>
          <div className="muted small" style={{ marginTop: 4 }}>
            已驳回并自动重写正文 → 重新审校（通常需 1-3 分钟，请勿关闭页面）
          </div>
        </div>
      ) : chapter && isRunningBannerVisible ? (
        <div style={{ marginTop: 12 }}>
          <WorkflowRunningBanner detail={runningDetail} />
        </div>
      ) : null}

      {/* Workflow 面板（含 runs 列表 / 时间线 / 审批卡片）+ 续写助手 */}
      {chapter ? (
        <div style={{ marginTop: 16 }}>
          <div className="panel-grid">
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
          <div style={{ marginTop: 16 }}>
            <ContinuePanel projectId={projectId} chapterId={chapterId} />
          </div>
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
            qualityGateCheckpoint={qualityGateCheckpoint}
          />
        </div>
      ) : null}

      {chapter ? (
        <div style={{ marginTop: 16 }}>
          <ContextPreviewPanel chapterId={chapterId} />
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
    payload?: {
      author_intent?: string;
      target_word_count?: number;
      model_profile_id?: string | null;
      fresh_write?: boolean;
    },
  ) => Promise<void>;
}) {
  const activeRunInfo =
    activeRunStatus === 'RUNNING' || activeRunStatus === 'PENDING'
      ? { status: activeRunStatus as 'RUNNING' | 'PENDING' }
      : null;

  // 拉取已启用模型档案列表；失败静默降级为不显示下拉（不阻塞按钮）。
  const profilesCall = useApiCall<ModelProfile[]>(
    () => modelProfilesApi.list(),
    [],
  );
  const enabledProfiles = useMemo(
    () =>
      (profilesCall.data ?? []).filter((p) => {
        const v = p.enabled;
        return v === 1 || v === true;
      }),
    [profilesCall.data],
  );

  // 每个按钮独立保存选中的 profile_id（key=action）；空串=走环节绑定默认。
  const [selectedProfile, setSelectedProfile] = useState<
    Record<'plan' | 'write' | 'review' | 'commit', string>
  >({ plan: '', write: '', review: '', commit: '' });
  const setProfile = (action: 'plan' | 'write' | 'review' | 'commit', value: string) =>
    setSelectedProfile((prev) => ({ ...prev, [action]: value }));

  // 「写正文」动作专属：写作模式选择。''=按意见改稿（默认），'fresh'=全新重写。
  // 仅 write 卡片渲染该下拉；点击时读取最新值，避免 setState 异步竞态。
  const [writeMode, setWriteMode] = useState<'' | 'fresh'>('');

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
            const supportsModelPick = b.action !== 'commit';
            const showSelect =
              supportsModelPick && !profilesCall.error && enabledProfiles.length > 0;
            return (
              <div
                key={b.action}
                className="workflow-actions__card"
                style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
              >
                <button
                  className="btn"
                  disabled={!avail.enabled || submitting}
                  title={avail.reason ?? undefined}
                  data-testid={`wf-btn-${b.action}`}
                  onClick={() =>
                    void onStart(b.action, {
                      model_profile_id: supportsModelPick
                        ? selectedProfile[b.action] || null
                        : null,
                      fresh_write:
                        b.action === 'write' ? writeMode === 'fresh' : undefined,
                    })
                  }
                >
                  <span className="btn__title">{b.title}</span>
                  <span className="btn__hint">
                    需要状态：{EXPECTED_STATUS[b.action].join(' / ')}
                  </span>
                </button>
                {supportsModelPick && showSelect ? (
                  <select
                    className="workflow-actions__model-select"
                    data-testid={`wf-model-select-${b.action}`}
                    value={selectedProfile[b.action]}
                    onChange={(e) => setProfile(b.action, e.target.value)}
                    title="选择本次运行使用的模型档案；默认走环节绑定"
                    disabled={submitting}
                  >
                    <option value="">环节绑定（默认）</option>
                    {enabledProfiles.map((p) => (
                      <option key={p.profile_id} value={p.profile_id}>
                        {p.name}（{p.provider}/{p.model}）
                      </option>
                    ))}
                  </select>
                ) : null}
                {/* 「写正文」专属：写作模式（按意见改稿 / 全新重写）。小号 select
                    放在模型下拉下方；仅 write 卡片渲染；其他动作不显示。 */}
                {b.action === 'write' ? (
                  <select
                    className="workflow-actions__model-select"
                    data-testid="wf-write-mode"
                    value={writeMode}
                    onChange={(e) =>
                      setWriteMode(e.target.value === 'fresh' ? 'fresh' : '')
                    }
                    title="全新重写忽略旧稿与改稿意见，用于不同模型文风对比"
                    disabled={submitting}
                    style={{ fontSize: 12 }}
                  >
                    <option value="">按意见改稿（默认）</option>
                    <option value="fresh">全新重写</option>
                  </select>
                ) : null}
              </div>
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
  onApprove: (
    approved: boolean,
    opts?: { revise?: boolean; note?: string },
  ) => Promise<void> | void;
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
                <span
                  title={
                    (r.error ?? '').includes('rejected-for-revision')
                      ? '该轮审校被「按建议修改/驳回并改稿」主动驳回，系统已自动重跑写正文→审校；非失败。'
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
            <NodeStatusBadge status={n.status} error={n.error} />
            <span className="muted small">
              {formatDateTime(n.started_at)}
              {n.ended_at ? ` → ${formatDateTime(n.ended_at)}` : ''}
              {n.latency_ms != null ? ` · ${n.latency_ms}ms` : ''}
            </span>
          </div>
          {n.error ? (
            (n.error ?? '').includes('rejected-for-revision') ? (
              // 「按建议修改/驳回并改稿」主动驳回：改稿回路会自动重跑 write→review，
              // 非真失败，用中性 InfoBanner 而非红色 ErrorBanner。
              <InfoBanner>
                已按审校建议驳回本轮（rejected-for-revision）：系统正在自动改稿重跑
                写正文 → 审校，非失败。
              </InfoBanner>
            ) : (
              <ErrorBanner>{n.error}</ErrorBanner>
            )
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

// ---- WorkflowRunningBanner ----
// V1.5：仅当存在 RUNNING/PENDING run（或 submitting 过渡期）时渲染。
// 数据源：poll.data（GET /runs/{id}，含 nodes/current_node/workflow_name/started_at），
// 退化为 activeRun（list 行，不含 nodes）。
//
// 文案三段：
//  1. 标题  ：⏳ 正在执行：{动作中文名}（{workflow_name}）
//  2. 节点  ：节点名（第 X/Y 步）· 已运行 N 秒
//  3. 提示  ：正在生成…（约需 1-3 分钟，请勿关闭）
//
// 终态（COMPLETED/FAILED/CANCELLED/PAUSED）由调用方控制不再传入 detail；FAILED 由 ErrorBanner 承载。
const WORKFLOW_ACTION_LABEL: Record<string, string> = {
  'chapter-plan': '生成计划',
  'chapter-write': '写正文',
  'chapter-review': '审校',
  'chapter-commit': '提交',
};

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s === 0 ? `${m} 分` : `${m} 分 ${s} 秒`;
}

function WorkflowRunningBanner({ detail }: { detail: WorkflowRun | null }) {
  // 本地每秒 +1，让「已运行 X 秒」看起来在跳；started_at 没拿到时退化为 0。
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  if (!detail) {
    // 提交中、详情尚未到达：用兜底文案
    return (
      <div
        className="alert alert--info"
        data-testid="workflow-running-banner"
        role="status"
      >
        <div style={{ fontWeight: 600 }}>⏳ 正在执行：工作流启动中…</div>
        <div className="muted small" style={{ marginTop: 4 }}>
          正在生成（请勿关闭页面）。
        </div>
      </div>
    );
  }

  const actionLabel =
    WORKFLOW_ACTION_LABEL[detail.workflow_id] ??
    WORKFLOW_ACTION_LABEL[detail.workflow_name ?? ''] ??
    detail.workflow_name ??
    detail.workflow_id;
  const workflowName = detail.workflow_name ?? detail.workflow_id;

  const nodes = Array.isArray(detail.nodes) ? detail.nodes : [];
  const currentIdx = detail.current_node
    ? nodes.findIndex((n) => n.node_id === detail.current_node)
    : -1;
  const hasNodeProgress = currentIdx >= 0 && nodes.length > 0;
  const currentNodeName = hasNodeProgress ? nodes[currentIdx].node_id : null;

  // 计算已运行时长（秒）
  let elapsedSec = 0;
  if (detail.started_at) {
    const start = Date.parse(detail.started_at);
    if (!Number.isNaN(start)) {
      elapsedSec = Math.max(0, Math.floor((Date.now() - start) / 1000));
    }
  }
  // tick 引用进来避免 lint 警告，也保证下次 render 时 elapsedSec 会重新算
  void tick;

  const hintText = (() => {
    if (detail.status === 'PENDING') return '正在排队启动（约需 1-3 分钟，请勿关闭）';
    if (detail.workflow_id === 'chapter-write')
      return '正在生成正文草稿（约需 1-3 分钟，请勿关闭）';
    if (detail.workflow_id === 'chapter-plan')
      return '正在生成章节计划（约需 1-3 分钟，请勿关闭）';
    if (detail.workflow_id === 'chapter-review')
      return '正在执行审校（约需 1-3 分钟，请勿关闭）';
    if (detail.workflow_id === 'chapter-commit')
      return '正在提交章节（约需 1-3 分钟，请勿关闭）';
    return '正在执行（约需 1-3 分钟，请勿关闭）';
  })();

  return (
    <div
      className="alert alert--info"
      data-testid="workflow-running-banner"
      role="status"
      data-workflow-id={detail.workflow_id}
      data-current-node={detail.current_node ?? ''}
      data-run-id={detail.run_id}
    >
      <div style={{ fontWeight: 600 }}>
        ⏳ 正在执行：{actionLabel}（{workflowName}）
      </div>
      <div className="muted small" style={{ marginTop: 4 }}>
        {hasNodeProgress && currentNodeName
          ? `节点：${currentNodeName}（第 ${currentIdx + 1}/${nodes.length} 步）· 已运行 ${formatElapsed(elapsedSec)}`
          : `执行中 · 已运行 ${formatElapsed(elapsedSec)}`}
      </div>
      <div className="muted small" style={{ marginTop: 2 }}>
        {hintText}
      </div>
    </div>
  );
}

function NodeStatusBadge({
  status,
  error,
}: {
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'SKIPPED';
  /** 节点 error：FAILED 且为 rejected-for-revision 时渲染中性「已驳回·改稿」，
   *  避免与真失败的红色 FAILED 混淆（改稿回路的正常语义）。 */
  error?: string | null;
}) {
  const rejectedForRevision =
    status === 'FAILED' && (error ?? '').includes('rejected-for-revision');
  const cls = rejectedForRevision
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
  return (
    <span className={cls}>{rejectedForRevision ? '已驳回·改稿' : status}</span>
  );
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
    <div
      className="panel"
      data-testid="drafts-panel"
      style={{ display: 'flex', flexDirection: 'column' }}
    >
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

          <div
            className="panel__section"
            style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
          >
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
                data-layout-ver="8"
                style={{
                  // 与左侧审批卡（approval-card）视觉齐高：审批卡由审校报告内容撑高，
                  // 此处给一个匹配的固定高度，避免 flex 撑满整个 panel 导致过高
                  height: 1722,
                  maxHeight: 'none',
                  overflowY: 'auto',
                }}
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
