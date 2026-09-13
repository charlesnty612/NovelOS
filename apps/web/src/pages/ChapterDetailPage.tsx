// 章节详情页（章节生产闭环的主工作台）。
//
// 信息架构（2026-09-13 前端批次 C 重构；同日二次调整：运行记录下置为分段 tab）：
//   1. 顶部核心工作区：章节头 + 四步流水线控制（ChapterPipelineHeader）；
//   2. 横幅区：质量门禁阻断 / 自动改稿回路 / 运行中横幅（含「停止工作流」）；
//   3. 运行记录（ChapterRunPanel：run 列表 + 节点时间线 + 审批卡）下置为第二个分段 tab，
//      tab 标签挂状态徽标（RUNNING 转圈点 / PAUSED「待审批」/ FAILED 红点）；
//      不在该 tab 且存在待审批 / 运行中的 run 时，tabs 上方给一条 slim 提示条
//      （替代原「常驻面板」的防漏审批功能——PAUSED 审批仍需作者能一眼看到）；
//   4. 分区导航（分段 tabs，默认「草稿」，状态落 URL ?section=）：
//      草稿 / 运行记录 / 章节计划 / 质量 / 上下文 / 危险操作。
//      ——此前是 8 个面板一路竖排到底，删除按钮悬在页底且无任何视觉降级。
//      分区首次访问才挂载，访问过的分区保留挂载（仅 hidden），切走再回来不丢编辑态
//      （审批卡里的勾选 / 改稿意见同样保留）。
//
// 子组件在 pages/chapter/ 下：ChapterPipelineHeader / ChapterRunPanel / ChapterRunBanner /
// ChapterDraftsSection / ChapterPlanSection / ChapterDangerZone。

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { chaptersApi, qualityApi, workflowsApi } from '../api/endpoints';
import type {
  Chapter,
  Draft,
  QualityReport,
  WorkflowRun,
  WorkflowStartPayload,
  WorkflowStartResponse,
} from '../api/types';
import { QualityPanel } from '../components/QualityPanel';
import { ContextPreviewPanel } from '../components/ContextPreviewPanel';
import { ContinuePanel } from '../components/ContinuePanel';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ErrorBanner } from '../components/ErrorBanner';
import { ErrorBoundary } from '../components/ErrorBoundary';
import { Loading } from '../components/Loading';
import { ChapterPipelineHeader } from './chapter/ChapterPipelineHeader';
import type {
  WorkflowAction,
  WorkflowStartArgs,
} from './chapter/ChapterPipelineHeader';
import { ChapterRunPanel } from './chapter/ChapterRunPanel';
import { ChapterRunBanner } from './chapter/ChapterRunBanner';
import { ChapterDraftsSection } from './chapter/ChapterDraftsSection';
import { ChapterPlanSection } from './chapter/ChapterPlanSection';
import { ChapterDangerZone } from './chapter/ChapterDangerZone';
import { useApiCall } from '../hooks/useApiCall';
import { useChapterRunOrchestration } from '../hooks/useChapterRunOrchestration';
import { ApiError } from '../api/client';

// 分区导航（下部分区；顶部核心工作区不受影响）。
// 「运行记录」紧随「草稿」：它是本次下置的常驻面板本体（run 列表 + 审批卡），
// 位置靠前保证待审批时一屏可达。
const SECTIONS = [
  { key: 'drafts', label: '草稿' },
  { key: 'runs', label: '运行记录' },
  { key: 'plan', label: '章节计划' },
  { key: 'quality', label: '质量' },
  { key: 'context', label: '上下文' },
  { key: 'danger', label: '危险操作' },
] as const;

type SectionKey = (typeof SECTIONS)[number]['key'];

const DEFAULT_SECTION: SectionKey = 'drafts';

function isSectionKey(value: string | null): value is SectionKey {
  return SECTIONS.some((s) => s.key === value);
}

export function ChapterDetailPage() {
  const { pid, cid } = useParams();
  const projectId = pid!;
  const chapterId = cid!;
  const navigate = useNavigate();

  // ---- 分区导航（落 URL，刷新/后退/书签可复现） ----
  const [searchParams, setSearchParams] = useSearchParams();
  const rawSection = searchParams.get('section');
  const section: SectionKey = isSectionKey(rawSection) ? rawSection : DEFAULT_SECTION;
  const selectSection = useCallback(
    (key: SectionKey) => {
      const next = new URLSearchParams(searchParams);
      next.set('section', key);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  // 已挂载过的分区集合：懒加载 + 切换保留（避免丢编辑中的计划/草稿，也避免重复请求）
  const [mountedSections, setMountedSections] = useState<Set<SectionKey>>(
    () => new Set<SectionKey>([section]),
  );
  useEffect(() => {
    setMountedSections((prev) =>
      prev.has(section) ? prev : new Set(prev).add(section),
    );
  }, [section]);

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

  // ---- run 编排（runs 列表/选中/轮询/详情）抽离至 useChapterRunOrchestration ----
  const handlePollTick = useCallback(() => {
    void chapterCall.reload();
    void draftsCall.reload();
  }, [chapterCall, draftsCall]);
  const orch = useChapterRunOrchestration({ projectId, chapterId, onPollTick: handlePollTick });
  const {
    chapterRuns, selectedRunId, setSelectedRunId, selectedRunSummary,
    activeRun, poll, detail, detailRun, pausePayload, qualityGateCheckpoint,
    runsReload, detailReload,
  } = orch;

  // ---- 运行记录 tab 的状态信号（tab 徽标 + tabs 上方提示条）----
  // 数据源：chapterRuns（轮询 onResult 会 runsReload，PAUSED/RUNNING 自然刷新）。
  // 优先级 PAUSED > RUNNING/PENDING > 当前选中/latest：待审批最需要人（防漏审批），
  // 其次是正在跑；都无活跃 run 时看选中/最近一次结果（FAILED 出红点）。
  const pausedRun = useMemo(
    () => chapterRuns.find((r) => r.status === 'PAUSED') ?? null,
    [chapterRuns],
  );
  const indicatorRun = pausedRun ?? activeRun ?? selectedRunSummary ?? chapterRuns[0] ?? null;
  const isGenerating =
    indicatorRun?.status === 'RUNNING' || indicatorRun?.status === 'PENDING';
  // 提示条只在「不在运行记录 tab + 有可行动状态」时出现（已在该 tab 就没必要重复提示）
  const signalKind: 'paused' | 'running' | null =
    section === 'runs' ? null : pausedRun ? 'paused' : isGenerating ? 'running' : null;
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
  // - deleteConfirm / deleteError: 删除章节二次确认 + 失败反馈（此前失败静默无提示）。
  // - deleting: 删除中按钮 disabled 防连点。
  interface PendingAction {
    action: WorkflowAction;
    payload?: WorkflowStartArgs;
  }
  const [planReconfirm, setPlanReconfirm] = useState<PendingAction | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // 「停止工作流」二次确认（复用 ConfirmDialog，替代此前的内联确认态）
  const [cancelConfirm, setCancelConfirm] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  // 「审校」动作专属：深度二审开关（Kimi 三层清单：设定/节拍/行为链）。
  // 默认不勾；勾选时 review 请求体带 deep_review:true，pause_payload 会
  // 多一个 deep_review_report 分栏。开启后审校约 +1 分钟。
  const [deepReview, setDeepReview] = useState(false);
  const handleStartWorkflow = useCallback(
    async (action: WorkflowAction, payload?: WorkflowStartArgs, opts?: { confirmed?: boolean }) => {
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
          // 把待执行 action+payload 暂存到 planReconfirm，弹窗确认后重跑。
          setPlanReconfirm({ action, payload });
          return;
        }
      }
      setActionErr(null);
      setSubmitting(true);
      try {
        // 按次模型档案选择：plan/write/review → 对应 capability；commit 走 observer
        // 覆盖键（observer agent 是 LLM 调用，V3.9.3 拆为独立 capability；summarizer
        // 走 light 绑定，不受 commit 覆盖键影响）。
        const capabilityByAction: Record<WorkflowAction, string> = {
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
        const wantsFreshWrite = action === 'write' && rawFreshWrite === true;
        // 深度二审开关：仅 review 动作支持；其他动作剥离后丢弃。
        const wantsDeepReview = action === 'review' && rawDeepReview === true;
        const hasBusinessFields =
          Object.keys(basePayload).length > 0 || wantsFreshWrite || wantsDeepReview;
        // 无业务字段且无 override 时透传 undefined，保持向后兼容（与旧契约一致）。
        const requestPayload: WorkflowStartPayload | undefined =
          !hasOverride && !hasBusinessFields
            ? undefined
            : hasOverride
            ? {
                ...basePayload,
                model_overrides: { [capability]: profileId },
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
        } else resp = await workflowsApi.startCommit(projectId, chapterId, requestPayload);
        // 启动后立刻刷新 + 选中该 run
        setSelectedRunId(resp.run_id);
        await Promise.all([chapterCall.reload(), runsReload(), draftsCall.reload()]);
      } catch (e: unknown) {
        setActionErr(e instanceof Error ? e.message : '启动失败');
      } finally {
        setSubmitting(false);
      }
    },
    [
      projectId,
      chapterId,
      chapter,
      chapterCall,
      draftsCall,
      selectedDraftVersion,
      runsReload,
      setSelectedRunId,
    ],
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
        await Promise.all([chapterCall.reload(), runsReload(), draftsCall.reload(), detailReload()]);
      } catch (e: unknown) {
        setActionErr(e instanceof Error ? e.message : '审批失败');
        throw e;
      } finally {
        setSubmitting(false);
        setReviseLooping(false);
      }
    },
    [selectedRunSummary, chapterCall, runsReload, draftsCall, detailReload],
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
  // 取消前由 ConfirmDialog 二次确认；取消成功后端置 CANCELLED，轮询自然把横幅撤下；
  // 409（run 已不在 RUNNING）也视为「已结束」——刷新详情即可。错误用 message 兜底，UI 不炸。
  const handleCancelRun = useCallback(async () => {
    if (!runningDetail) return;
    setCancelling(true);
    try {
      await workflowsApi.cancelRun(runningDetail.run_id);
      // 成功：触发既有刷新链路，poll 也会停到非 RUNNING，banner 自然消失
      await Promise.all([chapterCall.reload(), runsReload(), detailReload()]);
    } catch (e: unknown) {
      // 409：run 已不在 RUNNING（终态/PAUSED）→ 视为已结束，刷新即可，不报错
      if (e instanceof ApiError && (e.status === 409 || e.status === 404)) {
        await Promise.all([chapterCall.reload(), runsReload(), detailReload()]);
        return;
      }
      // 其它错误：信息抛给 ErrorBanner（与既有 actionErr 共用通道）
      setActionErr(e instanceof Error ? e.message : '停止工作流失败');
    } finally {
      setCancelling(false);
    }
  }, [runningDetail, chapterCall, runsReload, detailReload]);

  // ---- V3.9 批次 4.1：quality_gate enforce 阻断 → 「按门禁建议改稿」 ----
  // 阻断标记由 chapter_commit 的 quality_gate 节点写入 plan_json.gate_blocked
  // （改稿建议同批写入 plan_json.revision_note）；有标记即渲染入口。门禁通过后
  // 后端清除标记，入口自动消失。触发后复用 write（revise）→ review 链路。
  const gateBlock = useMemo<{ ruleIds: string[] } | null>(() => {
    const plan = chapter?.plan_json;
    if (!plan || typeof plan !== 'object' || Array.isArray(plan)) return null;
    const raw = (plan as Record<string, unknown>)['gate_blocked'];
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const ids = (raw as Record<string, unknown>)['rule_ids'];
    return { ruleIds: Array.isArray(ids) ? ids.map((v) => String(v)) : [] };
  }, [chapter?.plan_json]);

  const [gateRevising, setGateRevising] = useState(false);
  const handleGateRevise = useCallback(async () => {
    setActionErr(null);
    setSubmitting(true);
    setGateRevising(true);
    try {
      const resp = await workflowsApi.gateRevise(projectId, chapterId);
      setSelectedRunId(resp.run_id);
      await Promise.all([chapterCall.reload(), runsReload(), draftsCall.reload()]);
    } catch (e: unknown) {
      setActionErr(e instanceof Error ? e.message : '按门禁建议改稿启动失败');
    } finally {
      setGateRevising(false);
      setSubmitting(false);
    }
  }, [projectId, chapterId, chapterCall, draftsCall, runsReload, setSelectedRunId]);

  // 渲染
  return (
    <div>
      <div className="toolbar">
        <Link to={`/projects/${projectId}/chapters`} className="muted small">
          ← 返回章节列表
        </Link>
      </div>

      <ErrorBanner>{chapterCall.error || actionErr}</ErrorBanner>

      {chapter ? (
        <ChapterPipelineHeader
          chapter={chapter}
          activeRunStatus={activeRun?.status ?? null}
          submitting={submitting}
          selectedDraftVersion={selectedDraftVersion}
          onStart={handleStartWorkflow}
          deepReview={deepReview}
          onDeepReviewChange={setDeepReview}
          onTitleSaved={() => chapterCall.reload()}
        />
      ) : chapterCall.loading ? (
        <Loading text="加载章节中…" />
      ) : null}

      {/* V3.9 4.1：quality_gate 阻断出路。gate_blocked 标记在门禁阻断时写入
          plan_json，改稿建议同批写进 plan_json.revision_note；点击后按建议改稿
          （write revise → 自动接力 review）。门禁通过后后端清除标记，入口消失。 */}
      {chapter && gateBlock ? (
        <div
          className="alert cdp-gate-banner"
          data-testid="gate-block-banner"
        >
          <div className="cdp-gate-banner__title">质量门禁阻断：本稿未通过提交校验</div>
          <div className="muted small cdp-gate-banner__line">
            命中规则：
            {gateBlock.ruleIds.length > 0 ? gateBlock.ruleIds.join('、') : '（见门禁报告）'}
            。门禁已把可执行改稿建议写入章节计划；点击右侧按钮将按建议改稿
            （写正文 revise → 自动重新审校），审校通过后再提交。
          </div>
          <div className="cdp-gate-banner__actions">
            <button
              className="btn btn--primary"
              data-testid="gate-revise-btn"
              disabled={submitting || !!activeRun}
              title={
                activeRun
                  ? '已有工作流在运行，请等待结束'
                  : '按门禁建议改稿并自动重审'
              }
              onClick={() => void handleGateRevise()}
            >
              {gateRevising ? '提交中…' : '按门禁建议改稿'}
            </button>
          </div>
        </div>
      ) : null}

      {/* V1.5：运行中横幅。RUNNING/PENDING 时显示节点进度 + 已运行时长；
          终态自动消失（FAILED 由 ErrorBanner 承载）。submitting 过渡期也显示，避免「按下无反馈」。 */}
      {reviseLooping ? (
        <div
          className="alert alert--info cdp-banner-gap"
          data-testid="revise-loop-banner"
          role="status"
        >
          <div className="cdp-banner__title">⏳ 自动改稿回路进行中</div>
          <div className="muted small cdp-banner__line">
            已驳回并自动重写正文 → 重新审校（通常需 1-3 分钟，请勿关闭页面）
          </div>
        </div>
      ) : chapter && isRunningBannerVisible ? (
        <div className="cdp-banner-gap">
          <ChapterRunBanner
            detail={runningDetail}
            cancelPending={cancelling}
            onRequestCancel={() => setCancelConfirm(true)}
          />
        </div>
      ) : null}

      {/* 下部分区：分段导航 + 分区内容（首次访问才挂载；访问过的分区保留挂载、仅隐藏）。
          运行记录（原顶部常驻面板）下置为「运行记录」tab：面板本体在对应 tabpanel 内，
          这里只保留状态信号——待审批 / 运行中且不在该 tab 时给一条 slim 提示条。 */}
      {chapter ? (
        <div className="cdp-block">
          {signalKind ? (
            <div
              className={
                'alert cdp-run-signal' +
                (signalKind === 'paused' ? ' alert--warning' : ' alert--info')
              }
              data-testid="cdp-run-signal"
              data-kind={signalKind}
              role="status"
            >
              <span>
                {signalKind === 'paused' ? '当前有待人工审批' : '正在生成'}
              </span>
              <button
                type="button"
                className="cdp-run-signal__action"
                data-testid="cdp-run-signal-action"
                onClick={() => selectSection('runs')}
              >
                查看
              </button>
            </div>
          ) : null}
          <div className="tabs cdp-tabs" role="tablist" aria-label="章节详情分区">
            {SECTIONS.map((s) => (
              <button
                key={s.key}
                type="button"
                role="tab"
                aria-selected={section === s.key}
                className={
                  'tabs__tab cdp-tab' + (section === s.key ? ' tabs__tab--active' : '')
                }
                data-testid={`cdp-tab-${s.key}`}
                onClick={() => selectSection(s.key)}
              >
                {s.label}
                {s.key === 'runs' ? <RunTabBadge run={indicatorRun} /> : null}
              </button>
            ))}
          </div>

          {/* 分区内容：首次访问才挂载（懒加载，未打开的分区不发请求）；
              访问过的分区保持挂载、仅用 hidden 隐藏——切走再回来不会丢编辑中的
              计划 JSON / 草稿正文，也不会有「重新加载」闪烁。 */}
          {mountedSections.has('drafts') ? (
            <div
              role="tabpanel"
              hidden={section !== 'drafts'}
              data-testid="cdp-section-drafts"
            >
              <ChapterDraftsSection
                chapterId={chapterId}
                chapterStatus={chapter.status}
                lastReviewCompletedAt={chapter.last_review_completed_at ?? null}
                drafts={drafts}
                draftsLoading={draftsCall.loading}
                draftsError={draftsCall.error}
                selectedDraftVersion={selectedDraftVersion}
                onSelectDraftVersion={setSelectedDraftVersion}
                onCreated={async () => {
                  await draftsCall.reload();
                }}
              />
              <div className="cdp-block">
                <ContinuePanel projectId={projectId} chapterId={chapterId} />
              </div>
            </div>
          ) : null}

          {/* 运行记录（原顶部常驻面板）：run 列表 + 节点时间线 + 审批卡（PAUSED 时的
              人工决议）。与其它分区同语义：首次访问才挂载、访问过保留挂载仅 hidden——
              审批卡里的勾选 / 改稿意见切走再回来不丢。 */}
          {mountedSections.has('runs') ? (
            <div
              role="tabpanel"
              hidden={section !== 'runs'}
              data-testid="cdp-section-runs"
            >
              <ChapterRunPanel
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
            </div>
          ) : null}

          {mountedSections.has('plan') ? (
            <div
              role="tabpanel"
              hidden={section !== 'plan'}
              data-testid="cdp-section-plan"
            >
              <ChapterPlanSection chapter={chapter} onUpdated={() => chapterCall.reload()} />
            </div>
          ) : null}

          {mountedSections.has('quality') ? (
            <div
              role="tabpanel"
              hidden={section !== 'quality'}
              data-testid="cdp-section-quality"
            >
              {/* 局部 ErrorBoundary：QualityPanel 会深解引用 report.scores_json._meta，
                  形状一变就抛异常；页面级兜底会白屏，局部包裹至少保住本章其它分区。 */}
              <ErrorBoundary>
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
              </ErrorBoundary>
            </div>
          ) : null}

          {mountedSections.has('context') ? (
            <div
              role="tabpanel"
              hidden={section !== 'context'}
              data-testid="cdp-section-context"
            >
              <ErrorBoundary>
                <ContextPreviewPanel chapterId={chapterId} />
              </ErrorBoundary>
            </div>
          ) : null}

          {mountedSections.has('danger') ? (
            <div
              role="tabpanel"
              hidden={section !== 'danger'}
              data-testid="cdp-section-danger"
            >
              <ChapterDangerZone
                chapterNumber={chapter.number}
                deleting={deleting}
                error={deleteError}
                onRequestDelete={() => {
                  setDeleteError(null);
                  setDeleteConfirm(true);
                }}
              />
            </div>
          ) : null}
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
            setDeleteError(null);
            try {
              await chaptersApi.delete(chapterId);
              navigate(`/projects/${projectId}/chapters`);
            } catch (e: unknown) {
              // 此前是 try/finally（无 catch）：删除失败弹窗已关、按钮恢复，
              // 界面毫无反馈。现在把错误留在「危险操作」区就地展示。
              setDeleteError(e instanceof Error ? e.message : '删除章节失败');
            } finally {
              setDeleting(false);
            }
          }}
        />
      ) : null}

      {cancelConfirm ? (
        <ConfirmDialog
          open={true}
          title="停止当前工作流"
          body="确认停止当前工作流？已完成的节点会保留，正在执行的节点结果将被丢弃。"
          confirmText="确认停止"
          cancelText="再想想"
          danger
          testId="wf-cancel"
          onCancel={() => setCancelConfirm(false)}
          onConfirm={() => {
            setCancelConfirm(false);
            void handleCancelRun();
          }}
        />
      ) : null}
    </div>
  );
}

// 「运行记录」tab 的状态徽标（由选中/最新的 run 状态驱动）：
//   RUNNING/PENDING → accent 色转圈小环（复用 .spinner）；
//   PAUSED          → warn 色「待审批」小徽标；
//   FAILED          → error 色圆点（tooltip「失败」）；
//   终态 / 无 run   → 不挂标记。
// 轮询（useChapterRunOrchestration）刷新 chapterRuns，徽标随之自然更新。
function RunTabBadge({ run }: { run: WorkflowRun | null }) {
  switch (run?.status) {
    case 'PAUSED':
      return (
        <span
          className="badge badge--warn cdp-tab__badge"
          data-testid="cdp-runs-badge"
          data-state="paused"
        >
          待审批
        </span>
      );
    case 'RUNNING':
    case 'PENDING':
      return (
        <span
          className="spinner cdp-run-spinner cdp-tab__badge"
          data-testid="cdp-runs-badge"
          data-state="running"
          title="正在生成"
          aria-label="正在生成"
        />
      );
    case 'FAILED':
      return (
        <span
          className="cdp-run-dot cdp-tab__badge"
          data-testid="cdp-runs-badge"
          data-state="failed"
          title="失败"
          aria-label="失败"
        />
      );
    default:
      return null;
  }
}
