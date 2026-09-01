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
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ErrorBanner, InfoBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import {
  ChapterStatusBadge,
  WorkflowRunStatusBadge,
} from '../components/ChapterStatusBadge';
import { ApprovalCard } from '../components/ApprovalCard';
import { ProseText } from '../components/ProseText';
import { useApiCall } from '../hooks/useApiCall';
import { usePoll } from '../hooks/usePoll';
import {
  countHighRiskChanges,
  getButtonAvailability,
  getPipelineStepStates,
  isCancelledByUserNode,
  isRejectedForRevisionRun,
  isRejectedRun,
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

  // ---- 草稿选中版本（受控，提升至父组件，供审校按钮读取 payload）----
  // drafts 是 version DESC 排序（drafts[0] 即最新一版）；选中版本变化时同步刷新
  // ——见下方 selectedDraftVersion 的 sync effect。默认 null = drafts 加载完成后
  // 自动落到 drafts[0]（最新版），保持原视觉与行为。
  const [selectedDraftVersion, setSelectedDraftVersion] = useState<number | null>(null);
  const drafts = draftsCall.data ?? [];
  useEffect(() => {
    if (drafts.length === 0) {
      setSelectedDraftVersion(null);
      return;
    }
    if (selectedDraftVersion == null || !drafts.some((d) => d.version === selectedDraftVersion)) {
      setSelectedDraftVersion(drafts[0].version);
    }
  }, [drafts, selectedDraftVersion]);

  // ---- 顶部工作流按钮 ----
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [reviseLooping, setReviseLooping] = useState(false);
  // V3.22「交互反馈统一」：替换 window.confirm。
  // - planReconfirm: plan 二次确认（覆盖会丢字数规划）—— 用 PendingAction 持有原 action+payload，
  //   用户在 ConfirmDialog 点确认后再次调 handleStartWorkflow。
  // - deleteConfirm: 删除章节二次确认。
  // - deleting: 删除中按钮 disabled 防连点。
  interface PendingAction {
    action: 'plan' | 'write' | 'review' | 'commit';
    payload?: {
      author_intent?: string;
      target_word_count?: number;
      model_profile_id?: string | null;
      fresh_write?: boolean;
      deep_review?: boolean;
    };
  }
  const [planReconfirm, setPlanReconfirm] = useState<PendingAction | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // 「审校」动作专属：深度二审开关（Kimi 三层清单：设定/节拍/行为链）。
  // 默认不勾；勾选时 review 请求体带 deep_review:true，pause_payload 会
  // 多一个 deep_review_report 分栏。开启后审校约 +1 分钟。
  const [deepReview, setDeepReview] = useState(false);
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
        /** 深度二审开关（仅 review 生效）：true ⇒ review 请求体带 deep_review: true。
         *  临时字段,在此处剥离后按 action==='review' 决定是否并入请求 payload。 */
        deep_review?: boolean;
      },
      opts?: { confirmed?: boolean },
    ) => {
// 防呆：章节已有 plan_json 时，「生成计划」需二次确认（覆盖会丢字数规划）
        if (action === 'plan' && chapter && !opts?.confirmed) {
          const plan = chapter.plan_json;
          const hasPlan =
            plan != null &&
            typeof plan === 'object' &&
            !Array.isArray(plan) &&
            Object.keys(plan).length > 0;
          if (hasPlan) {
            // V3.22「交互反馈统一」：用 ConfirmDialog 替代 window.confirm。
            // 把待执行 action+payload 暂存到 planReconfirm，弹窗确认后由 useEffect 重跑。
            setPlanReconfirm({
              action,
              payload: payload as PendingAction['payload'],
            });
            return;
          }
        }
      setActionErr(null);
      setSubmitting(true);
      try {
        // 按次模型档案选择：plan/write/review → 对应 capability；commit 走 observer
        // 覆盖键（observer agent 是 LLM 调用，V3.9.3 拆为独立 capability；summarizer
        // 走 light 绑定，不受 commit 覆盖键影响）。
        const capabilityByAction: Record<
          'plan' | 'write' | 'review' | 'commit',
          string | null
        > = {
          plan: 'reasoning',
          write: 'creative_writing',
          review: 'light',
          commit: 'observer',
        };
        const capability = capabilityByAction[action];
        const profileId = (payload?.model_profile_id ?? '').trim();
        // 从入参 payload 中剥离临时字段 model_profile_id / fresh_write，避免下发给后端；
        // author_intent / target_word_count 等业务字段透传给后端。fresh_write 在下方按
        // action==='write' 决定是否并入请求 payload（业务字段）。
        const {
          model_profile_id: _omit,
          fresh_write: rawFreshWrite,
          deep_review: rawDeepReview,
          ...basePayload
        } = payload ?? {};
        void _omit;
        const hasOverride = !!capability && !!profileId;
        // 写作模式：仅 write 动作支持；其他动作剥离后丢弃。
        const wantsFreshWrite =
          action === 'write' && rawFreshWrite === true;
        // 深度二审开关：仅 review 动作支持；其他动作剥离后丢弃。
        const wantsDeepReview =
          action === 'review' && rawDeepReview === true;
        const hasBusinessFields =
          Object.keys(basePayload).length > 0 ||
          wantsFreshWrite ||
          wantsDeepReview;
        // 无业务字段且无 override 时透传 undefined，保持向后兼容（与旧契约一致）。
        const requestPayload: WorkflowStartPayload | undefined =
          !hasOverride && !hasBusinessFields
            ? undefined
            : hasOverride
            ? {
                ...basePayload,
                model_overrides: { [capability as string]: profileId },
                ...(wantsFreshWrite ? { fresh_write: true } : {}),
                ...(wantsDeepReview ? { deep_review: true } : {}),
              }
            : wantsFreshWrite
            ? ({
                ...basePayload,
                fresh_write: true,
                ...(wantsDeepReview ? { deep_review: true } : {}),
              } as WorkflowStartPayload)
            : wantsDeepReview
            ? ({ ...basePayload, deep_review: true } as WorkflowStartPayload)
            : (basePayload as WorkflowStartPayload);
        let resp: WorkflowStartResponse;
        if (action === 'plan') resp = await workflowsApi.startPlan(projectId, chapterId, requestPayload);
        else if (action === 'write') resp = await workflowsApi.startWrite(projectId, chapterId, requestPayload);
        else if (action === 'review') {
          // 审校目标版本：始终带 draft_version（默认 = 最新版,由 selectedDraftVersion
          // 父级状态保证;后端对不存在版本会报错,前端不校验）。
          const reviewPayload: WorkflowStartPayload = {
            ...(requestPayload ?? {}),
            draft_version: selectedDraftVersion,
          };
          resp = await workflowsApi.startReview(projectId, chapterId, reviewPayload);
        }
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
    [projectId, chapterId, chapter, chapterCall, runsCall, draftsCall, selectedDraftVersion],
  );

  const handleResume = useCallback(
    async (
      approved: boolean,
      opts?: { revise?: boolean; note?: string },
    ) => {
      if (!selectedRunSummary) return;
      setActionErr(null);
      // 仅当 opts?.revise（按建议修改/驳回并改稿）触发自动改稿回路并显示回路横幅。
      // 纯驳回（approved=false）由后端以 FAILED(error='rejected') 收尾,不再启动回路。
      if (opts?.revise) setReviseLooping(true);
      setSubmitting(true);
      try {
        // 三态：approve / reject / revise（revise 时 run 以 FAILED(rejected-for-revision) 收尾,
        // 纯驳回时 run 以 FAILED(error='rejected') 收尾,chapter 保持 DRAFTED。
        // note 落 plan_json.revision_note，改稿后可重跑 write/review）
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

  // 「停止当前工作流」按钮：banner 展示的 run 处于 RUNNING 时允许取消。
  // 取消成功后端置 CANCELLED，轮询自然把横幅撤下；409（run 已不在 RUNNING）也
  // 视为「已结束」——刷新详情即可。错误用 message 兜底，UI 不炸。
  const handleCancelRun = useCallback(async () => {
    if (!runningDetail) return;
    try {
      await workflowsApi.cancelRun(runningDetail.run_id);
      // 成功：触发既有刷新链路，poll 也会停到非 RUNNING，banner 自然消失
      await Promise.all([
        chapterCall.reload(),
        runsCall.reload(),
        detail.reload(),
      ]);
    } catch (e: unknown) {
      // 409：run 已不在 RUNNING（终态/PAUSED）→ 视为已结束，刷新即可，不报错
      if (e instanceof ApiError && e.status === 409) {
        await Promise.all([
          chapterCall.reload(),
          runsCall.reload(),
          detail.reload(),
        ]);
        return;
      }
      // 404：run 不存在 → 同样刷新兜底
      if (e instanceof ApiError && e.status === 404) {
        await Promise.all([
          chapterCall.reload(),
          runsCall.reload(),
          detail.reload(),
        ]);
        return;
      }
      // 其它错误：信息抛给 ErrorBanner（与既有 actionErr 共用通道）
      setActionErr(e instanceof Error ? e.message : '停止工作流失败');
    }
  }, [runningDetail, chapterCall, runsCall, detail]);

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
          selectedDraftVersion={selectedDraftVersion}
          onStart={handleStartWorkflow}
          deepReview={deepReview}
          onDeepReviewChange={setDeepReview}
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
          <WorkflowRunningBanner detail={runningDetail} onCancel={handleCancelRun} />
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
              lastReviewCompletedAt={chapter.last_review_completed_at ?? null}
              drafts={draftsCall.data ?? []}
              draftsLoading={draftsCall.loading}
              draftsError={draftsCall.error}
              selectedDraftVersion={selectedDraftVersion}
              onSelectDraftVersion={setSelectedDraftVersion}
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
            disabled={deleting}
            data-testid="chapter-delete-btn"
            onClick={() => setDeleteConfirm(true)}
          >
            {deleting ? '删除中…' : '删除章节'}
          </button>
        </div>
      ) : null}

      {planReconfirm ? (
        <ConfirmDialog
          open={true}
          title="覆盖章节计划"
          body="章节已有计划，重新生成将覆盖当前计划（含字数规划），确定继续？"
          confirmText="覆盖"
          danger
          testId="plan-reconfirm"
          onCancel={() => setPlanReconfirm(null)}
          onConfirm={() => {
            const next = planReconfirm;
            setPlanReconfirm(null);
            void handleStartWorkflow(next.action, next.payload, { confirmed: true });
          }}
        />
      ) : null}

      {deleteConfirm && chapter ? (
        <ConfirmDialog
          open={true}
          title="删除章节"
          body={`确认删除第 ${chapter.number} 章？此操作不可撤销。`}
          confirmText="删除"
          danger
          testId="chapter-delete-confirm"
          onCancel={() => {
            if (deleting) return;
            setDeleteConfirm(false);
          }}
          onConfirm={async () => {
            setDeleteConfirm(false);
            setDeleting(true);
            try {
              await chaptersApi.delete(chapterId);
              navigate(`/projects/${projectId}/chapters`);
            } finally {
              setDeleting(false);
            }
          }}
        />
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
  selectedDraftVersion,
  onStart,
  deepReview,
  onDeepReviewChange,
}: {
  chapter: Chapter;
  activeRunStatus: 'PENDING' | 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | null;
  submitting: boolean;
  /** 当前选中的 draft 版本号;用于「将审校:草稿 vN」动态提示。null = 尚无草稿。 */
  selectedDraftVersion: number | null;
  onStart: (
    action: 'plan' | 'write' | 'review' | 'commit',
    payload?: {
      author_intent?: string;
      target_word_count?: number;
      model_profile_id?: string | null;
      fresh_write?: boolean;
      deep_review?: boolean;
    },
  ) => Promise<void>;
  /** 「审校」步骤深度二审（Kimi 三层清单）开关 */
  deepReview: boolean;
  onDeepReviewChange: (v: boolean) => void;
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

  // 「写正文」动作专属：写作模式选择。'fresh'=全新重写（默认，2026-08-30 用户拍板：
  // 改稿应由「按建议修改/驳回并改稿」链路触发，手动写正文默认整章重写），''=按意见改稿。
  // 仅 write 卡片渲染该下拉；点击时读取最新值，避免 setState 异步竞态。
  const [writeMode, setWriteMode] = useState<'' | 'fresh'>('fresh');

  const buttons: Array<{
    action: 'plan' | 'write' | 'review' | 'commit';
    title: string;
    hint: string;
  }> = [
    { action: 'plan', title: '生成计划', hint: 'Director 生成章节计划' },
    { action: 'write', title: '写正文', hint: 'Writer 生成正文草稿' },
    { action: 'review', title: '审校', hint: 'basic_checks + 作者审批' },
    { action: 'commit', title: '提交', hint: 'Observer 提取状态并入库' },
  ];

  // 四步流水线展示态：状态机 + plan_json 是否已落库共同决定每步是 done/current/todo。
  const hasPlanForPipeline =
    chapter != null &&
    chapter.plan_json != null &&
    typeof chapter.plan_json === 'object' &&
    !Array.isArray(chapter.plan_json) &&
    Object.keys(chapter.plan_json).length > 0;
  const stepStates = getPipelineStepStates({
    chapterStatus: chapter.status,
    hasPlan: hasPlanForPipeline,
  });

  const stepStateLabel: Record<'done' | 'current' | 'todo', string> = {
    done: '已完成',
    current: '当前步骤',
    todo: '未到达',
  };
  const stepOrder: Array<'plan' | 'write' | 'review' | 'commit'> = [
    'plan',
    'write',
    'review',
    'commit',
  ];
  const buttonByAction = new Map(buttons.map((b) => [b.action, b] as const));

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
        <div className="wf-pipeline" data-testid="wf-pipeline">
          {stepOrder.map((action, idx) => {
            const b = buttonByAction.get(action)!;
            const state = stepStates[action];
            const avail = getButtonAvailability(b.action, {
              chapterStatus: chapter.status,
              activeRun: activeRunInfo,
            });
            const supportsModelPick = true; // V3.9.4：四 action 全支持模型下拉（commit 走 observer 覆盖键）
            const showSelect =
              supportsModelPick && !profilesCall.error && enabledProfiles.length > 0;
            const isCurrent = state === 'current';
            const dotLabel =
              state === 'done' ? '✓' : String(idx + 1);
            const stepNode = (
              <div
                key={b.action}
                className="wf-step"
                data-state={state}
                data-testid={`wf-step-${b.action}`}
              >
                <div className="wf-step__head">
                  <span
                    className="wf-step__dot"
                    data-state={state}
                    aria-hidden="true"
                  >
                    {dotLabel}
                  </span>
                  <span className="wf-step__name">{b.title}</span>
                  <span
                    className={
                      'wf-step__state' +
                      (isCurrent ? ' wf-step__state--current' : '')
                    }
                  >
                    {stepStateLabel[state]}
                  </span>
                </div>
                <button
                  className={'btn wf-step__btn' + (isCurrent ? ' btn--primary' : '')}
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
                      // 「审校」专属：勾选深度二审时透传给 handleStartWorkflow，
                      // 未勾选时透传 undefined（避免误带 false 触发旧契约歧义）。
                      deep_review:
                        b.action === 'review' ? deepReview : undefined,
                    })
                  }
                >
                  <span className="btn__title">{b.title}</span>
                  <span className="btn__hint">{b.hint}</span>
                </button>
                {supportsModelPick && showSelect ? (
                  <select
                    className="wf-step__select"
                    data-testid={`wf-model-select-${b.action}`}
                    value={selectedProfile[b.action]}
                    onChange={(e) => setProfile(b.action, e.target.value)}
                    title="选择本次运行使用的模型档案；默认走环节绑定"
                    disabled={submitting}
                  >
                    <option value="">模型：环节绑定（默认）</option>
                    {enabledProfiles.map((p) => (
                      <option key={p.profile_id} value={p.profile_id}>
                        {p.name}（{p.provider}/{p.model}）
                      </option>
                    ))}
                  </select>
                ) : null}
                {/* 「写正文」专属：写作模式（按意见改稿 / 全新重写）。小号 select
                    放在模型下拉下方；仅 write 步骤渲染；其他动作不显示。 */}
                {b.action === 'write' ? (
                  <select
                    className="wf-step__select"
                    data-testid="wf-write-mode"
                    value={writeMode}
                    onChange={(e) =>
                      setWriteMode(e.target.value === 'fresh' ? 'fresh' : '')
                    }
                    title="全新重写忽略旧稿与改稿意见，用于不同模型文风对比"
                    disabled={submitting}
                  >
                    <option value="fresh">模式：全新重写（默认）</option>
                    <option value="">模式：按意见改稿</option>
                  </select>
                ) : null}
                {/* 「审校」专属：动态提示将审哪版,让用户在点之前就知道。
                    草稿尚未加载时(selectedDraftVersion=null)显示「暂未选择」。
                    + 深度二审（Kimi 三层清单）开关：默认不勾，勾选时 review
                    请求体带 deep_review:true，pause_payload 多一段
                    deep_review_report 分栏。 */}
                {b.action === 'review' ? (
                  <>
                    <div
                      className="wf-step__extra muted small"
                      data-testid="wf-review-target-hint"
                    >
                      将审校：草稿 v{selectedDraftVersion ?? '暂未选择'}
                    </div>
                    <label
                      className="wf-step__extra muted small"
                      data-testid="wf-review-deep-toggle"
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                        cursor: submitting ? 'not-allowed' : 'pointer',
                      }}
                    >
                      <input
                        type="checkbox"
                        data-testid="wf-review-deep-checkbox"
                        checked={deepReview}
                        disabled={submitting}
                        onChange={(e) => onDeepReviewChange(e.target.checked)}
                        style={{ marginRight: 2 }}
                      />
                      深度二审（Kimi，+约1分钟）
                    </label>
                  </>
                ) : null}
              </div>
            );
            if (idx === stepOrder.length - 1) return stepNode;
            return [
              stepNode,
              <div
                key={`conn-${action}`}
                className="wf-step__connector"
                aria-hidden="true"
              />,
            ];
          })}
        </div>
        <div className="muted small" style={{ marginTop: 6 }}>
          步骤按顺序推进，同一时间只能运行一个工作流；灰色步骤需先完成前置步骤，已完成的步骤在状态允许时可重跑。
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
        <div className="prose-block" data-testid="plan-prose">
          <ProseText text={formatJson(chapter.plan_json) || '（空）'} />
        </div>
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
                  {/* 优先展示后端给的 label（如「写正文 → 草稿 v6」），缺省退化为 run_id 短形式；
                      完整 run_id 保留在行内 muted 小字，便于溯源。 */}
                  {r.label ? r.label : `run · ${r.run_id.slice(0, 12)}…`}
                  {r.label ? (
                    <span className="muted small" style={{ marginLeft: 6 }}>
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
  const verdictColor =
    verdict === 'pass'
      ? 'var(--color-success, #2e7d32)'
      : verdict === 'revise'
      ? 'var(--color-warn, #c97a16)'
      : 'var(--color-text-muted, #6b7280)';
  const issues = issuesRaw
    .map((it, i) => {
      if (!it || typeof it !== 'object') return null;
      const o = it as Record<string, unknown>;
      const layer =
        typeof o['layer'] === 'string' ? (o['layer'] as string) : '';
      const severity =
        typeof o['severity'] === 'string' ? (o['severity'] as string) : '';
      const quote = typeof o['quote'] === 'string' ? (o['quote'] as string) : '';
      const suggestion =
        typeof o['suggestion'] === 'string'
          ? (o['suggestion'] as string)
          : '';
      if (!layer && !severity && !quote && !suggestion) return null;
      return { idx: i, layer, severity, quote, suggestion };
    })
    .filter((x): x is NonNullable<typeof x> => x !== null);
  return (
    <div className="panel__section" data-testid="deep-review-report">
      <div className="panel__section-title">深度二审（三层清单）</div>
      <div
        className="small"
        style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}
      >
        <span
          className="badge"
          data-testid="deep-review-verdict"
          data-verdict={verdict || 'unknown'}
          style={{ color: verdictColor, borderColor: verdictColor }}
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
          data-testid="deep-review-issues"
          data-issue-count={issues.length}
          style={{ listStyle: 'none', padding: 0, margin: '8px 0 0 0' }}
        >
          {issues.map((it) => (
            <li
              key={`dri-${it.idx}`}
              data-testid="deep-review-issue"
              data-layer={it.layer}
              data-severity={it.severity}
              style={{
                borderLeft: '3px solid var(--color-border-strong)',
                paddingLeft: 8,
                marginTop: 4,
              }}
            >
              <div
                className="small"
                style={{
                  display: 'flex',
                  gap: 6,
                  alignItems: 'center',
                  flexWrap: 'wrap',
                }}
              >
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
                  className="muted small"
                  data-testid="deep-review-issue-quote"
                  style={{ marginTop: 2 }}
                >
                  「{it.quote}」
                </div>
              ) : null}
              {it.suggestion ? (
                <div
                  data-testid="deep-review-issue-suggestion"
                  style={{ marginTop: 2 }}
                >
                  {it.suggestion}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <div className="muted small" style={{ marginTop: 6 }}>
          未发现明显问题
        </div>
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
      <summary
        className="muted small"
        data-testid="run-timeline-summary"
        style={{ cursor: 'pointer', marginTop: 4 }}
      >
        共 {run.nodes.length} 个节点 · 最新：{lastNode.node_id}
      </summary>
      <ol style={{ paddingLeft: 18, margin: '4px 0 0 0' }}>
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
    </details>
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

function WorkflowRunningBanner({
  detail,
  onCancel,
}: {
  detail: WorkflowRun | null;
  /** 父组件传入的取消回调：POST /runs/{id}/cancel，由父组件统一处理 200/409/404/其它错误。 */
  onCancel: () => Promise<void> | void;
}) {
  // 本地每秒 +1，让「已运行 X 秒」看起来在跳；started_at 没拿到时退化为 0。
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  // 「停止当前工作流」按钮的本地状态：未确认 / 待确认 / 提交中。
  // 切换 run 后重置到 idle（避免上一轮的确认态遗留下来）。
  const [cancelPhase, setCancelPhase] = useState<'idle' | 'confirming' | 'submitting'>('idle');
  useEffect(() => {
    setCancelPhase('idle');
  }, [detail?.run_id]);

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

  // V1.5 横幅节点选取修正：
  //   后端仅在节点完成后才更新 current_node，导致 N+1 节点已 RUNNING 时横幅仍显示 N。
  //   这里优先取 nodes 中首个 RUNNING 节点作为展示节点；无 RUNNING 时回退到 current_node。
  const runningIdx = nodes.findIndex((n) => n.status === 'RUNNING');
  const fallbackIdx = detail.current_node
    ? nodes.findIndex((n) => n.node_id === detail.current_node)
    : -1;
  const currentIdx = runningIdx >= 0 ? runningIdx : fallbackIdx;
  const hasNodeProgress = currentIdx >= 0 && nodes.length > 0;
  const currentNodeName = hasNodeProgress ? nodes[currentIdx].node_id : null;

  // 已运行时长：优先按展示节点 started_at 起算；无节点 / 节点无 started_at 时回退到 run.started_at
  let elapsedSec = 0;
  const elapsedSource =
    (hasNodeProgress && nodes[currentIdx].started_at) || detail.started_at;
  if (elapsedSource) {
    const start = Date.parse(elapsedSource);
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

  // 取消按钮可见性：仅在 detail.status === 'RUNNING' 时展示。
  // - PENDING：未真正开始跑（不要展示「停止」按钮，避免歧义）
  // - PAUSED / 终态（COMPLETED/FAILED/CANCELLED）：banner 在父组件已不展示，此处兜底不出按钮
  const showCancelBtn = detail.status === 'RUNNING';
  // 二次确认中的「提交中」判定：用 isCancelSubmitting 抽象布尔，避免 TS 在
  // cancelPhase === 'confirming' 命中的三元里把 cancelPhase 缩窄为字面量 'confirming'
  // 导致后续 === 'submitting' 比较报 TS2367。
  const submittingForCancel = cancelPhase === 'submitting';

  const handleClickCancel = () => {
    if (submittingForCancel) return;
    setCancelPhase('confirming');
  };
  const handleConfirmCancel = async () => {
    if (submittingForCancel) return;
    setCancelPhase('submitting');
    try {
      await onCancel();
      // 父组件成功路径会刷详情/banner 自动消失；停留在 submitting 态
      // （无 run_id 变化则 effect 不触发重置），让用户在等待中看到「停止中…」。
    } catch {
      // 父组件自行处理错误（ErrorBanner / 静默刷新），本组件恢复 idle 允许重试
      setCancelPhase('idle');
    }
  };
  const handleAbortCancel = () => {
    if (submittingForCancel) return;
    setCancelPhase('idle');
  };

  return (
    <div
      className="alert alert--info"
      data-testid="workflow-running-banner"
      role="status"
      data-workflow-id={detail.workflow_id}
      data-current-node={currentNodeName ?? ''}
      data-run-id={detail.run_id}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 12,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
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
        {showCancelBtn ? (
          cancelPhase === 'confirming' ? (
            <div
              data-testid="wf-cancel-confirm"
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'flex-end',
                gap: 6,
                maxWidth: 360,
              }}
            >
              <div
                className="small"
                style={{ textAlign: 'right', lineHeight: 1.4 }}
              >
                确认停止当前工作流？已完成的节点会保留，正在执行的节点结果将被丢弃。
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  type="button"
                  className="btn btn--sm"
                  data-testid="wf-cancel-abort"
                  onClick={handleAbortCancel}
                  disabled={submittingForCancel}
                >
                  再想想
                </button>
                <button
                  type="button"
                  className="btn btn--sm btn--danger"
                  data-testid="wf-cancel-confirm-btn"
                  onClick={handleConfirmCancel}
                  disabled={submittingForCancel}
                >
                  {submittingForCancel ? '停止中…' : '确认停止'}
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              className="btn btn--sm btn--danger"
              data-testid="wf-cancel-btn"
              onClick={handleClickCancel}
              style={{ flexShrink: 0 }}
            >
              停止工作流
            </button>
          )
        ) : null}
      </div>
    </div>
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
  const cancelledByUser =
    status === 'FAILED' && isCancelledByUserNode(err);
  const rejectedForRevision =
    !cancelledByUser && status === 'FAILED' && isRejectedForRevisionRun(err);
  const rejectedOnly =
    !cancelledByUser &&
    !rejectedForRevision &&
    status === 'FAILED' &&
    isRejectedRun(err);
  const cls = cancelledByUser || rejectedForRevision || rejectedOnly
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

// ---- DraftsPanel ----
function DraftsPanel({
  chapterId,
  chapterStatus,
  lastReviewCompletedAt,
  drafts,
  draftsLoading,
  draftsError,
  selectedDraftVersion,
  onSelectDraftVersion,
  onCreated,
}: {
  chapterId: string;
  chapterStatus: Chapter['status'];
  /** 最近一次 COMPLETED 状态 chapter-review run 的 ended_at（ISO 字符串）；
   *  null 表示该章节从未审过。用于在版本列表行渲染「未审」角标。 */
  lastReviewCompletedAt: string | null;
  drafts: Draft[];
  draftsLoading: boolean;
  draftsError: string | null;
  /** 父组件持有的当前选中版本号（受控）。null = drafts 为空或尚未回落。 */
  selectedDraftVersion: number | null;
  /** 版本被点选时通知父组件；保存新版本后父组件会刷新此值,本组件无须本地同步。 */
  onSelectDraftVersion: (version: number) => void;
  onCreated: () => Promise<void> | void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editorText, setEditorText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 受控：选中态由父组件持有。本组件只读 drafts 派生当前 draft 对象。
  const selected = drafts.find((d) => d.version === selectedDraftVersion) ?? null;

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
                  className={`kv-list__row ${d.version === selectedDraftVersion ? 'kv-list__row--active' : ''}`}
                  onClick={() => {
                    onSelectDraftVersion(d.version);
                    setEditingId(null);
                  }}
                  data-testid={`draft-row-${d.draft_id}`}
                >
                  <span className="kv-list__title">v{d.version}</span>
                  {/* 模型徽标：model_id 存在且非 'mock/mock' 时展示（mock 默认无意义）。
                      完整 model_id 保留在 title，便于调试；徽标本身只显示短形式。 */}
                  {d.model_id && d.model_id !== 'mock/mock' ? (
                    <span
                      className="badge"
                      title={d.model_id}
                      data-testid={`draft-model-${d.draft_id}`}
                    >
                      {d.model_id}
                    </span>
                  ) : null}
                  {/* 「未审」徽标：该 draft 严格晚于最近一次 COMPLETED review 的
                      ended_at 才算「审校后又改稿 / 续写」。ISO 字符串可字典序比较。
                      lastReviewCompletedAt 为 null（该章节从未审过）则不显示，
                      避免在用户首次走流水线时被噪声覆盖。 */}
                  {lastReviewCompletedAt && d.created_at > lastReviewCompletedAt ? (
                    <span
                      className="badge badge--unreviewed"
                      title={`未审：该版本生成于 ${formatDateTime(lastReviewCompletedAt)} 那次审校之后`}
                      data-testid={`draft-unreviewed-${d.draft_id}`}
                    >
                      未审
                    </span>
                  ) : null}
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
              <div
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
                <ProseText text={selected.content} />
              </div>
            ) : (
              <div className="muted">从左侧选择一份 draft 查看。</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
