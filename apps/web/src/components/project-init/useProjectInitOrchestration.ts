// project-init 面板的编排 hook（V4.0 前端模块化批次从 ProjectInitPanel.tsx 抽出）。
//
// 职责：展开态与表单初值、init-status 探测与环节勾选、init/resume/regenerate 的
// 异步 run 编排（RUNNING → 轮询终态）、挂起 run 的探测与恢复、review 视图的
// bindings/profiles 按需加载、错误文案渲染。
// 面板只消费本 hook 的返回值，本身退化为「视图组装」。
//
// 行为与拆分前逐字一致：状态机（idle → busy → review → busy-resume → done）、
// PAUSED 恢复、校验、文案、testid 全保留。

import { useEffect, useMemo, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { ApiError } from '../../api/client';
import {
  capabilityBindingsApi,
  charactersApi,
  modelProfilesApi,
  projectsApi,
  workflowsApi,
} from '../../api/endpoints';
import type {
  CapabilityBinding,
  ModelProfile,
  Project,
  ProjectInitBrief,
  ProjectInitPausePayload,
  ProjectInitPayload,
  ProjectInitResponse,
  WorkflowRun,
} from '../../api/types';
import {
  DEFAULT_CHAPTER_SEED_COUNT,
  DEFAULT_CHAPTER_WORD_COUNT,
  DEFAULT_PLATFORM,
  DETAIL_TRUNCATE,
  DISMISSED_SUSPENDED_KEY,
  RESUMABLE_WORKFLOW,
  STAGE_LABELS,
  deriveSeedCount,
  parseSeedCount,
  pollRunUntilTerminal,
} from './projectInitCore';
import type {
  FinalRun,
  InitStatusLoadState,
  PanelMode,
  PanelState,
  StageSelection,
} from './projectInitCore';

export interface UseProjectInitOrchestrationArgs {
  projectId: string;
  project: Project;
  /** 初始化成功后回调（父组件重载 project 等数据）。 */
  onDone: (resp: ProjectInitResponse) => void;
}

export interface ProjectInitOrchestration {
  // ---- 外壳 ----
  expanded: boolean;
  setExpanded: Dispatch<SetStateAction<boolean>>;
  viewMode: PanelMode;

  // ---- 表单 ----
  title: string;
  setTitle: Dispatch<SetStateAction<string>>;
  genre: string;
  setGenre: Dispatch<SetStateAction<string>>;
  logline: string;
  setLogline: Dispatch<SetStateAction<string>>;
  platform: string;
  setPlatform: Dispatch<SetStateAction<string>>;
  targetWords: string;
  authorNotes: string;
  setAuthorNotes: Dispatch<SetStateAction<string>>;
  chapterWordCount: string;
  chapterSeedCount: string;
  /** 用户是否手动改过章节种子数（true 时改单章字数/目标字数不再自动重算）。 */
  seedCountManual: boolean;
  overwriteConfirmed: boolean;
  setOverwriteConfirmed: Dispatch<SetStateAction<boolean>>;
  stepMode: boolean;
  setStepMode: Dispatch<SetStateAction<boolean>>;
  /** 本次初始化模型档案：'' = 走全局 capability_bindings。 */
  initModelProfileId: string;
  setInitModelProfileId: Dispatch<SetStateAction<string>>;
  handleTargetWordsChange: (v: string) => void;
  handleChapterWordCountChange: (v: string) => void;
  handleChapterSeedCountChange: (v: string) => void;

  // ---- 表单派生 ----
  seedCount: number | null;
  needsConfirm: boolean;
  busyAny: boolean;
  canSubmit: boolean;

  // ---- 探测 ----
  probeErr: string | null;

  // ---- 环节勾选 ----
  selectedStages: string[];
  stageMetaById: Record<string, StageSelection>;
  handleToggleStage: (stage: string, next: boolean) => void;

  // ---- run 运行态 ----
  panelState: PanelState;
  busyResume: boolean;
  pausePayload: ProjectInitPausePayload | null;
  /** 本次 run 各 AI 节点实际调用的 model_id（按 agent 名聚合）。null = 未拉到。 */
  stageModels: Record<string, string> | null;
  error: string | null;
  finalResp: ProjectInitResponse | null;

  // ---- 挂起恢复 ----
  suspendedRun: WorkflowRun | null;
  resuming: boolean;
  showSuspendedBanner: boolean;
  handleResumeSuspended: () => Promise<void>;
  handleDismissSuspended: () => void;

  // ---- review 视图数据源 ----
  bindings: CapabilityBinding[];
  profiles: ModelProfile[];
  handleChangeStageModel: (capability: string, profileId: string) => Promise<void>;

  // ---- 动作 ----
  handleInit: () => Promise<void>;
  handleResume: (parsed: Record<string, unknown>) => void;
  handleRegenerate: (note: string) => void;
  reset: () => void;
}

export function useProjectInitOrchestration(
  args: UseProjectInitOrchestrationArgs,
): ProjectInitOrchestration {
  const { projectId, project, onDone } = args;

  const [expanded, setExpanded] = useState(false);
  const [existingChars, setExistingChars] = useState<number | null>(null);
  const [probeErr, setProbeErr] = useState<string | null>(null);

  // 表单初值
  const [title, setTitle] = useState<string>(project.name ?? '');
  const [genre, setGenre] = useState<string>(project.genre ?? '');
  // 一句话简介：优先复用项目已存 premise 文本（未重新生成该环节时 logline 驱动下游，
  // 复用避免手抄；premise 是「定位+卖点」的浓缩）
  const [logline, setLogline] = useState<string>(project.premise ?? '');
  const [platform, setPlatform] = useState<string>(DEFAULT_PLATFORM);
  const [targetWords, setTargetWords] = useState<string>(
    project.target_words != null ? String(project.target_words) : '',
  );
  const [authorNotes, setAuthorNotes] = useState<string>('');
  const [chapterSeedCount, setChapterSeedCount] = useState<string>(
    String(DEFAULT_CHAPTER_SEED_COUNT),
  );
  // 单章字数（字/章，默认 3000）；修改后会按公式自动重算 chapter_seed_count，
  // 除非用户已经手动改过 chapter_seed_count（seedCountManual 标记）。
  const [chapterWordCount, setChapterWordCount] = useState<string>(
    String(DEFAULT_CHAPTER_WORD_COUNT),
  );
  const [seedCountManual, setSeedCountManual] = useState<boolean>(false);
  const [overwriteConfirmed, setOverwriteConfirmed] = useState(false);
  const [stepMode, setStepMode] = useState<boolean>(true);

  // 工作流运行态
  const [panelState, setPanelState] = useState<PanelState>('idle');
  const [runId, setRunId] = useState<string | null>(null);
  const [pausePayload, setPausePayload] = useState<ProjectInitPausePayload | null>(
    null,
  );
  // 本次 run 各 AI 节点实际调用的 model_id（按 agent 名聚合）；
  // 来源：GET /runs/{id} PAUSED 时携带的 stage_models。null 表示尚未拉到。
  const [stageModels, setStageModels] = useState<Record<string, string> | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [finalResp, setFinalResp] = useState<ProjectInitResponse | null>(null);

  // 环节绑定：进入 review 视图时按需拉一次；本地缓存供 stage-model-select 使用。
  const [bindings, setBindings] = useState<CapabilityBinding[]>([]);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [bindingsLoaded, setBindingsLoaded] = useState(false);

  // 本次初始化模型档案覆盖：'' = 默认（走全局 capability_bindings），
  // 其它值 = model_profiles.profile_id；后端会校验存在，不存在 → 400。
  // profiles 在 review 视图按需加载；idle-form 这里先有 state，渲染时若
  // profiles 未加载则只展示「默认绑定」option。
  const [initModelProfileId, setInitModelProfileId] = useState<string>('');

  // 组件挂载标记：异步轮询进行中若卸载组件，setState 会触发 React 警告。
  // 同时持有 AbortController 用于在卸载时立即取消轮询（pollRunUntilTerminal 内部使用）。
  const mountedRef = useRef<boolean>(true);
  const pollAbortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      pollAbortRef.current?.abort();
      pollAbortRef.current = null;
    };
  }, []);

  // 恢复挂起的初始化：面板展开时探测项目 runs；
  // 过滤 workflow_name==='project-init' && status==='PAUSED' 的最新一条。
  // - suspendedRun: 探测到的可恢复 run（null 表示无）
  // - resuming: 用户点击「继续审阅」后的加载态（期间禁用双按钮）
  // - dismissedKey: sessionStorage 中已忽略的 run_id 集合（本次会话不再提示）
  const [suspendedRun, setSuspendedRun] = useState<WorkflowRun | null>(null);
  const [resuming, setResuming] = useState(false);
  const [dismissedKey, setDismissedKey] = useState<Set<string>>(() => new Set());

  // 环节勾选状态：挂载时并行拉 init-status；用户可自由勾选/取消所有环节。
  // - initStatusLoad：'loading' / 'ok' / 'failed'——决定默认勾选策略
  // - selectedStages：本次提交要生成的环节 id 列表（始终为数组）
  // - stageMetaById：从 init-status 解析出的每关元数据（含 done / label / detail；
  //                  forced 字段恒为 false，仅保留以兼容旧测试断言）
  const [initStatusLoad, setInitStatusLoad] =
    useState<InitStatusLoadState>('loading');
  const [selectedStages, setSelectedStages] = useState<string[]>([]);
  const [stageMetaById, setStageMetaById] = useState<Record<string, StageSelection>>(
    () => ({}),
  );

  const seedCount = parseSeedCount(chapterSeedCount);

  /**
   * 单章字数 / 目标字数变化时自动重算 chapter_seed_count；
   * 用户已手动改过 chapter_seed_count（seedCountManual=true）则不重算。
   * 用 ref 风格的 setter：读取最新 seedCountManual 避免闭包陈旧。
   */
  const recomputeSeedCount = (nextTarget: string, nextChapter: string) => {
    if (seedCountManual) return;
    const derived = deriveSeedCount(nextTarget, nextChapter);
    if (derived !== null) setChapterSeedCount(String(derived));
  };
  const handleTargetWordsChange = (v: string) => {
    setTargetWords(v);
    recomputeSeedCount(v, chapterWordCount);
  };
  const handleChapterWordCountChange = (v: string) => {
    setChapterWordCount(v);
    recomputeSeedCount(targetWords, v);
  };
  const handleChapterSeedCountChange = (v: string) => {
    setSeedCountManual(true);
    setChapterSeedCount(v);
  };
  // 二次确认：仅当「本次要重新生成的环节」中包含「项目已有数据」的环节时才提示
  // （避免只补未完成环节时误报「可能造成重复」）。
  const needsConfirm = selectedStages.some((s) => stageMetaById[s]?.done);
  const busyAny = panelState === 'busy' || panelState === 'busy-resume';
  const canSubmit =
    !busyAny &&
    logline.trim().length > 0 &&
    seedCount !== null &&
    (!needsConfirm || overwriteConfirmed);

  // 首次展开时探测既有数据（best-effort：失败不阻塞面板）。
  useEffect(() => {
    if (!expanded || existingChars !== null) return;
    setProbeErr(null);
    charactersApi
      .listByProject(projectId)
      .then((rows) => setExistingChars(rows.length))
      .catch((e: unknown) => {
        if (e instanceof Error) setProbeErr(e.message);
        setExistingChars(0);
      });
  }, [expanded, existingChars, projectId]);

  // 首次展开时探测项目下是否存在 PAUSED 的 project-init run（best-effort；失败静默降级无横幅）。
  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    (async () => {
      try {
        const rows = await workflowsApi.listByProject(projectId);
        if (cancelled) return;
        const paused = rows
          .filter(
            (r: WorkflowRun) =>
              r.workflow_name === RESUMABLE_WORKFLOW && r.status === 'PAUSED',
          )
          // started_at DESC：后端 list_runs 已按 started_at DESC 排，取首条即最新。
          .slice(0, 1);
        setSuspendedRun(paused[0] ?? null);
      } catch {
        if (!cancelled) setSuspendedRun(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [expanded, projectId]);

  // 首次展开时并行拉 init-status；失败静默（不阻塞表单）。
  // - done 的环节：默认不勾选（沿用已有设定）；用户想重做可手动勾上。
  // - 未 done 的环节：默认勾选（保守）；用户可手动取消只跑部分环节。
  // - 请求失败：全部默认勾选（保守，不跳过任何环节）。
  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    // 重置为 loading 态（避免旧数据残留）
    setInitStatusLoad('loading');
    (async () => {
      try {
        const resp = await projectsApi.initStatus(projectId);
        if (cancelled) return;
        // 解析返回 stages → 构成本地结构
        const meta: Record<string, StageSelection> = {};
        const defaults: string[] = [];
        for (const s of resp.stages ?? []) {
          // 所有环节都允许自由勾选/取消；done 默认不勾、未 done 默认勾。
          meta[s.stage] = {
            stage: s.stage,
            forced: false,
            done: !!s.done,
            label: s.label ?? s.stage,
            detail: s.detail ?? null,
          };
          if (!s.done) defaults.push(s.stage);
        }
        setStageMetaById(meta);
        setSelectedStages(defaults);
        setInitStatusLoad('ok');
      } catch {
        // 失败：全部默认勾选（保守不跳过任何环节）
        if (cancelled) return;
        const meta: Record<string, StageSelection> = {};
        const defaults: string[] = [];
        for (const s of STAGE_LABELS) {
          meta[s.stage] = {
            stage: s.stage,
            forced: false,
            done: false,
            label: s.label,
            detail: null,
          };
          defaults.push(s.stage);
        }
        setStageMetaById(meta);
        setSelectedStages(defaults);
        setInitStatusLoad('failed');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [expanded, projectId]);

  // ---------------- helpers ---------------------------------------------

  // 环节勾选 toggle：所有环节都可自由勾选/取消（不再有强制锁定）。
  const handleToggleStage = (stage: string, next: boolean) => {
    setSelectedStages((prev) => {
      const has = prev.includes(stage);
      if (next && !has) return [...prev, stage];
      if (!next && has) return prev.filter((s) => s !== stage);
      return prev;
    });
  };

  const reset = () => {
    setLogline('');
    setAuthorNotes('');
    setOverwriteConfirmed(false);
    setError(null);
    setFinalResp(null);
    setRunId(null);
    setPausePayload(null);
    setPanelState('idle');
    // 重置环节勾选：恢复 init-status 默认值（不重置 initStatusLoad/meta，让 effect 在下次展开时刷新）
    if (initStatusLoad === 'failed') {
      setSelectedStages(STAGE_LABELS.map((s) => s.stage));
    } else {
      const def: string[] = [];
      for (const meta of Object.values(stageMetaById)) {
        // 默认：未 done → 勾选；done → 不勾选
        if (!meta.done) def.push(meta.stage);
      }
      setSelectedStages(def);
    }
  };

  // ---------------- 挂起恢复：继续审阅 / 忽略 ---------------------------

  /**
   * 继续审阅：拉取最新 pause_payload（list 行不保证带 pause_payload，所以走 GET /runs/{id}），
   * 进入 review 视图并复用既有渲染逻辑。
   * - 失败 → 留在 idle-form、显示 ErrorBanner；不调后端改 run 状态。
   */
  const handleResumeSuspended = async () => {
    if (!suspendedRun || resuming) return;
    setResuming(true);
    setError(null);
    try {
      const detail = await workflowsApi.get(suspendedRun.run_id);
      const pp = detail.pause_payload;
      if (!pp) {
        // 服务端已无 pause_payload（被并发 resume 或过期）：当作已不存在，隐藏横幅
        setSuspendedRun(null);
        return;
      }
      setRunId(detail.run_id);
      setPausePayload(pp);
      setStageModels(detail.stage_models ?? null);
      setPanelState('review');
      ensureBindingsLoaded();
      setSuspendedRun(null);
    } catch (e: unknown) {
      setError(renderApiError(e));
    } finally {
      setResuming(false);
    }
  };

  /**
   * 忽略：本次会话内不再提示（sessionStorage 记录 run_id）；
   * 不调后端（run 仍保持 PAUSED，后续可恢复）。
   */
  const handleDismissSuspended = () => {
    if (!suspendedRun) return;
    const key = DISMISSED_SUSPENDED_KEY(projectId);
    try {
      const raw = sessionStorage.getItem(key);
      const set = new Set<string>(raw ? (JSON.parse(raw) as string[]) : []);
      set.add(suspendedRun.run_id);
      sessionStorage.setItem(key, JSON.stringify(Array.from(set)));
      setDismissedKey(set);
    } catch {
      // sessionStorage 不可用时仅本地隐藏（不影响功能）
      setDismissedKey(new Set([suspendedRun.run_id]));
    }
    setSuspendedRun(null);
  };

  // 计算是否要展示横幅：满足「存在挂起 run 且本次会话未忽略」
  const showSuspendedBanner = useMemo(() => {
    if (!suspendedRun) return false;
    return !dismissedKey.has(suspendedRun.run_id);
  }, [suspendedRun, dismissedKey]);

  /**
   * 进入 review 视图时按需拉一次 bindings + profile 清单。
   * - 失败 best-effort：不清空已有缓存；UI 仍可继续。
   * - 首次切换进入 review 即触发，之后无论阶段切换均复用本地缓存。
   */
  const ensureBindingsLoaded = () => {
    if (bindingsLoaded) return;
    setBindingsLoaded(true);
    Promise.all([
      capabilityBindingsApi.list().catch(() => [] as CapabilityBinding[]),
      modelProfilesApi.list().catch(() => [] as ModelProfile[]),
    ]).then(([bs, ps]) => {
      setBindings(bs);
      setProfiles(ps);
    });
  };

  // 面板展开即预热 model profiles 列表（让 idle-form 的「本次初始化模型」下拉
  // 有内容；失败静默降级为只展示「默认绑定」option）。
  useEffect(() => {
    if (expanded) ensureBindingsLoaded();
  }, [expanded]);

  const renderApiError = (e: unknown): string => {
    if (e instanceof ApiError) {
      const detail =
        e.detail.length > DETAIL_TRUNCATE
          ? `${e.detail.slice(0, DETAIL_TRUNCATE)}…`
          : e.detail;
      return `${e.status}: ${detail}`;
    }
    if (e instanceof Error) return e.message;
    return '初始化失败';
  };

  // ---------------- idle：提交 init -------------------------------------

  const handleInit = async () => {
    if (!canSubmit) return;
    setError(null);
    setFinalResp(null);
    setPausePayload(null);
    const trimmedTitle = title.trim();
    const trimmedGenre = genre.trim();
    const trimmedLogline = logline.trim();
    const trimmedPlatform = platform.trim() || DEFAULT_PLATFORM;
    const trimmedAuthorNotes = authorNotes.trim();
    const tw = targetWords.trim();

    const brief: ProjectInitBrief = {
      title: trimmedTitle,
      genre: trimmedGenre,
      logline: trimmedLogline,
      platform: trimmedPlatform,
    };
    if (tw) {
      const n = Number.parseInt(tw, 10);
      if (Number.isFinite(n) && n > 0) brief.target_words = n;
    }
    if (trimmedAuthorNotes) brief.author_notes = trimmedAuthorNotes;
    // 单章字数：trim 后 parseInt，非法/空不发（后端默认 3000）。
    const cwc = chapterWordCount.trim();
    if (cwc) {
      const n = Number.parseInt(cwc, 10);
      if (Number.isFinite(n) && n > 0) brief.chapter_word_count = n;
    }

    const payload: ProjectInitPayload = {
      brief,
      project_id: projectId,
      chapter_seed_count: seedCount ?? DEFAULT_CHAPTER_SEED_COUNT,
    };
    if (stepMode) payload.step_mode = true;
    // 始终传勾选中的 stage 数组（不用省略逻辑）；后端按 selected_stages 过滤环节。
    payload.selected_stages = [...selectedStages];
    // 单次 run 级模型档案覆盖：'' = 走全局 capability_bindings（不传字段，
    // 后端按 None 处理）；非空 = model_profiles.profile_id。
    const trimmedProfile = initModelProfileId.trim();
    if (trimmedProfile) payload.model_profile_id = trimmedProfile;

    setPanelState('busy');
    const abort = new AbortController();
    pollAbortRef.current = abort;
    try {
      const resp = await projectsApi.init(payload);
      setRunId(resp.run_id);
      // 后端 init 也已异步化：HTTP 响应可能直接返回 RUNNING（不带 pause_payload），
      // 必须轮询 GET /runs/{id} 拿到真实终态后再走原分支。
      const polled =
        resp.status === 'RUNNING' || resp.status === 'PENDING'
          ? await pollRunUntilTerminal(resp.run_id, { signal: abort.signal })
          : null;
      if (!mountedRef.current) return;
      const final: FinalRun = polled ?? resp;
      if (final.status === 'PAUSED' && final.pause_payload) {
        setPausePayload(final.pause_payload);
        // polled 响应是 WorkflowRun（含 stage_models）；同步响应无此字段。
        // 若 init 直接同步 PAUSED，继续走原有 best-effort 异步 GET 拿 stage_models。
        setStageModels(polled ? polled.stage_models ?? null : null);
        setPanelState('review');
        ensureBindingsLoaded();
        if (!polled) {
          void (async () => {
            if (!mountedRef.current) return;
            try {
              const detail = await workflowsApi.get(resp.run_id);
              if (!mountedRef.current) return;
              setStageModels(detail.stage_models ?? null);
            } catch {
              // ignore: stage_models 缺省时卡片不渲染该行
            }
          })();
        }
      } else if (final.status === 'COMPLETED') {
        setFinalResp(final);
        setStageModels(null);
        setPanelState('done');
        onDone(final);
      } else {
        // FAILED / 其他：保留在当前面板，暴露 ErrorBanner 后让用户决定下一步
        setPanelState('idle');
        setError(
          `run 终态异常：status=${final.status}` +
            (final.current_node ? `, current_node=${final.current_node}` : ''),
        );
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setPanelState('idle');
      setError(renderApiError(e));
    } finally {
      if (pollAbortRef.current === abort) {
        pollAbortRef.current = null;
      }
    }
  };

  // ---------------- review：放行 resume ---------------------------------

  const handleResume = (parsed: Record<string, unknown>) => {
    if (!runId || !pausePayload) return;
    const stageMeta = STAGE_LABELS.find((s) => s.stage === pausePayload.stage);
    if (!stageMeta) return;
    setError(null);
    setPanelState('busy-resume');
    // 后端 resume 已异步化：HTTP 响应固定为 RUNNING，必须轮询 GET /runs/{id}
    // 拿到真实终态（PAUSED / COMPLETED / FAILED）后再走原分支。
    const abort = new AbortController();
    pollAbortRef.current = abort;
    workflowsApi
      .resumeInit(runId, {
        human_input: { revisions: { [stageMeta.outputKey]: parsed } },
      })
      .then(async (resp) => {
        const polled =
          resp.status === 'RUNNING' || resp.status === 'PENDING'
            ? await pollRunUntilTerminal(runId, { signal: abort.signal })
            : null;
        if (!mountedRef.current) return;
        const final: FinalRun = polled ?? resp;
        if (final.status === 'PAUSED' && final.pause_payload) {
          setPausePayload(final.pause_payload);
          // polled 响应是 WorkflowRun（含 stage_models）；同步响应无此字段。
          setStageModels(polled ? polled.stage_models ?? null : null);
          setPanelState('review');
          ensureBindingsLoaded();
        } else if (final.status === 'COMPLETED') {
          setFinalResp(final);
          setPausePayload(null);
          setStageModels(null);
          setPanelState('done');
          onDone(final);
        } else {
          // 其他状态（含 FAILED）：保留在 review，由 ErrorBanner 展示
          setPanelState('review');
          setError(
            `run 终态异常：status=${final.status}` +
              (final.current_node ? `, current_node=${final.current_node}` : ''),
          );
        }
      })
      .catch((e: unknown) => {
        if (!mountedRef.current) return;
        setPanelState('review');
        setError(renderApiError(e));
      })
      .finally(() => {
        if (pollAbortRef.current === abort) {
          pollAbortRef.current = null;
        }
      });
  };

  // ---------------- review：带意见重新生成（regenerate）----------------------
  /**
   * 行为：调 POST /runs/{id}/resume，body {regenerate: true, human_input: {regenerate_note}}。
   * 后端会丢弃当前 draft、重跑当前挂起节点，返回新 PAUSED（带新 draft）或 COMPLETED。
   * - PAUSED：用返回的 pause_payload 更新当前审阅视图；编辑器由 ReviewPane key 变化自然重挂。
   * - COMPLETED：与放行一致走 done。
   * - FAILED / Error：留在 review，ErrorBanner 展示。
   */
  const handleRegenerate = (note: string) => {
    if (!runId) return;
    setError(null);
    setPanelState('busy-resume');
    // 后端 resume 已异步化：与 handleResume 同处理流程（HTTP 响应固定 RUNNING → 轮询终态）。
    const abort = new AbortController();
    pollAbortRef.current = abort;
    workflowsApi
      .resumeInit(runId, {
        human_input: { regenerate_note: note },
        regenerate: true,
      })
      .then(async (resp) => {
        const polled =
          resp.status === 'RUNNING' || resp.status === 'PENDING'
            ? await pollRunUntilTerminal(runId, { signal: abort.signal })
            : null;
        if (!mountedRef.current) return;
        const final: FinalRun = polled ?? resp;
        if (final.status === 'PAUSED' && final.pause_payload) {
          setPausePayload(final.pause_payload);
          // polled 响应是 WorkflowRun（含 stage_models）；同步响应无此字段。
          setStageModels(polled ? polled.stage_models ?? null : null);
          setPanelState('review');
          ensureBindingsLoaded();
        } else if (final.status === 'COMPLETED') {
          setFinalResp(final);
          setPausePayload(null);
          setStageModels(null);
          setPanelState('done');
          onDone(final);
        } else {
          // 其他状态（含 FAILED）：保留在 review，由 ErrorBanner 展示
          setPanelState('review');
          setError(
            `run 终态异常：status=${final.status}` +
              (final.current_node ? `, current_node=${final.current_node}` : ''),
          );
        }
      })
      .catch((e: unknown) => {
        if (!mountedRef.current) return;
        setPanelState('review');
        setError(renderApiError(e));
      })
      .finally(() => {
        if (pollAbortRef.current === abort) {
          pollAbortRef.current = null;
        }
      });
  };

  // ---------------- review：本关模型档案切换（stage-model-select）---------

  const handleChangeStageModel = async (
    capability: string,
    profileId: string,
  ): Promise<void> => {
    if (profileId === '') {
      await capabilityBindingsApi.unbind(capability);
    } else {
      await capabilityBindingsApi.bind(capability, [profileId]);
    }
    // 本地缓存立即更新，避免再发 GET
    setBindings((prev) =>
      prev.map((b) =>
        b.capability === capability
          ? {
              ...b,
              profile_ids: profileId === '' ? [] : [profileId],
              profiles: profileId === ''
                ? []
                : profiles
                    .filter((p) => p.profile_id === profileId)
                    .map((p) => ({
                      profile_id: p.profile_id,
                      name: p.name,
                      model: p.model,
                    })),
            }
          : b,
      ),
    );
  };

  // ---------------- 视图分发 -------------------------------------------

  const viewMode: PanelMode =
    panelState === 'done'
      ? 'done'
      : panelState === 'review' || panelState === 'busy-resume'
        ? 'review'
        : 'idle-form';

  return {
    expanded,
    setExpanded,
    viewMode,
    title,
    setTitle,
    genre,
    setGenre,
    logline,
    setLogline,
    platform,
    setPlatform,
    targetWords,
    authorNotes,
    setAuthorNotes,
    chapterWordCount,
    chapterSeedCount,
    seedCountManual,
    overwriteConfirmed,
    setOverwriteConfirmed,
    stepMode,
    setStepMode,
    initModelProfileId,
    setInitModelProfileId,
    handleTargetWordsChange,
    handleChapterWordCountChange,
    handleChapterSeedCountChange,
    seedCount,
    needsConfirm,
    busyAny,
    canSubmit,
    probeErr,
    selectedStages,
    stageMetaById,
    handleToggleStage,
    panelState,
    busyResume: panelState === 'busy-resume',
    pausePayload,
    stageModels,
    error,
    finalResp,
    suspendedRun,
    resuming,
    showSuspendedBanner,
    handleResumeSuspended,
    handleDismissSuspended,
    bindings,
    profiles,
    handleChangeStageModel,
    handleInit,
    handleResume,
    handleRegenerate,
    reset,
  };
}
