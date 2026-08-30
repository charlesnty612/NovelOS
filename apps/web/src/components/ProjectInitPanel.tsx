import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import {
  capabilityBindingsApi,
  charactersApi,
  modelProfilesApi,
  projectsApi,
  workflowsApi,
} from '../api/endpoints';
import type {
  CapabilityBinding,
  ModelProfile,
  Project,
  ProjectInitBrief,
  ProjectInitPausePayload,
  ProjectInitPayload,
  ProjectInitResponse,
  WorkflowRun,
} from '../api/types';
import { ErrorBanner, InfoBanner } from './ErrorBanner';
import {
  CharacterEditor,
  FullJsonFallbackEditor,
  OutlineEditor,
  PremiseEditor,
  composeAndValidate,
  WorldEditor,
  type JsonValidityChange,
} from './ProjectInitEditors';

interface Props {
  projectId: string;
  project: Project;
  /** 初始化成功后回调（父组件重载 project 等数据）。 */
  onDone: (resp: ProjectInitResponse) => void;
}

const DEFAULT_PLATFORM = '番茄·男频';
const DEFAULT_CHAPTER_SEED_COUNT = 10;
const CHAPTER_SEED_COUNT_MIN = 1;
const CHAPTER_SEED_COUNT_MAX = 500;
const DEFAULT_CHAPTER_WORD_COUNT = 3000;
// 后端 resume 已异步化（POST /runs/{id}/resume 固定立即返回 status=RUNNING，
// 真实终态靠 GET /runs/{id} 轮询）。前端在 init / resume / regenerate 三个入口
// 都可能拿到 RUNNING，需要轮询到终态再走原有 PAUSED/COMPLETED 分支。
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 600000;
const POLL_TIMEOUT_MESSAGE =
  '生成仍在后台进行，已等待超时；请稍后刷新页面在运行记录中查看结果';

/**
 * 测试覆盖：单测中通过在 window 上挂 __novelosPollIntervalMs / __novelosPollTimeoutMs
 * 注入更短的间隔/超时（默认 1ms/1000ms），避免在 fake timers 或短超时下被卡住。
 * 仅在 NODE_ENV !== 'production' 时生效（避免被滥用为生产可调参数）。
 */
function getPollIntervalMs(): number {
  if (
    typeof process !== 'undefined' &&
    process.env.NODE_ENV !== 'production' &&
    typeof window !== 'undefined'
  ) {
    const v = (window as unknown as { __novelosPollIntervalMs?: number })
      .__novelosPollIntervalMs;
    if (typeof v === 'number' && v > 0) return v;
  }
  return POLL_INTERVAL_MS;
}
function getPollTimeoutMs(): number {
  if (
    typeof process !== 'undefined' &&
    process.env.NODE_ENV !== 'production' &&
    typeof window !== 'undefined'
  ) {
    const v = (window as unknown as { __novelosPollTimeoutMs?: number })
      .__novelosPollTimeoutMs;
    if (typeof v === 'number' && v > 0) return v;
  }
  return POLL_TIMEOUT_MS;
}

/**
 * 轮询结果 + 即时响应统一形态：包含 init 同步响应（ProjectInitResponse，无 stage_models）
 * 与 GET 轮询响应（WorkflowRun，含 stage_models）的并集。
 */
type FinalRun =
  | ProjectInitResponse
  | (WorkflowRun & { project_id?: string | null });

/**
 * 轮询 GET /runs/{runId} 直到 run 进入「终态」。
 * 终态：PAUSED（带 pause_payload）/ COMPLETED / FAILED / CANCELLED。
 * 仍 RUNNING / PENDING 时继续等待；超过 timeoutMs 抛 Error。
 *
 * 用于后端 resume / init 已异步化的场景：HTTP 响应只回 RUNNING，
 * UI 必须轮询拿到真实终态才能进入 review / done。
 */
async function pollRunUntilTerminal(
  runId: string,
  opts: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<WorkflowRun> {
  const intervalMs = opts.intervalMs ?? getPollIntervalMs();
  const timeoutMs = opts.timeoutMs ?? getPollTimeoutMs();
  const deadline = Date.now() + timeoutMs;
  while (true) {
    if (opts.signal?.aborted) {
      throw new Error('轮询已取消');
    }
    const cur = await workflowsApi.get(runId);
    if (opts.signal?.aborted) {
      throw new Error('轮询已取消');
    }
    const status = cur.status;
    if (status === 'PAUSED' && cur.pause_payload) return cur;
    if (status === 'COMPLETED' || status === 'FAILED' || status === 'CANCELLED') {
      return cur;
    }
    // RUNNING / PENDING：再等一轮
    if (Date.now() >= deadline) {
      throw new Error(POLL_TIMEOUT_MESSAGE);
    }
    await new Promise<void>((resolve, reject) => {
      const t = setTimeout(resolve, intervalMs);
      if (opts.signal) {
        const onAbort = () => {
          clearTimeout(t);
          reject(new Error('轮询已取消'));
        };
        opts.signal.addEventListener('abort', onAbort, { once: true });
      }
    });
  }
}
// 后端 ProjectInitRequest.chapter_seed_count 校验为 Field(ge=1, le=500)；
// 推导出的 chapter_seed_count 落在 [10, 500]，与后端推导 clamp 一致（100 万字 ÷ 3000 ≈ 333 章可真实提交）。
const DERIVED_SEED_COUNT_MIN = 10;
const DERIVED_SEED_COUNT_MAX = 500;
const DETAIL_TRUNCATE = 200;

// 挂起恢复：sessionStorage key 模板（按 projectId 区分；仅本会话生效）。
const DISMISSED_SUSPENDED_KEY = (projectId: string) =>
  `novelos:project-init:dismissed:${projectId}`;
// 哪一类 run 算可恢复的（与后端 workflow.name 对齐）。
const RESUMABLE_WORKFLOW = 'project-init';

// ---- 分步审阅向导配置 ------------------------------------------------------
// 4 关卡 → 中文名 / output_key（与后端 pause_payload.stage / revisions key 对齐）。
const STAGE_LABELS: Array<{
  stage: string;
  outputKey: string;
  label: string;
  capability: string;
}> = [
  { stage: 'premise', outputKey: 'premise_output', label: '题材定位', capability: 'premise_design' },
  { stage: 'world', outputKey: 'world_output', label: '世界观', capability: 'world_building' },
  { stage: 'character', outputKey: 'character_output', label: '核心角色', capability: 'character_design' },
  { stage: 'outline', outputKey: 'outline_output', label: '卷纲与章节种子', capability: 'volume_outline' },
];

const STAGES_TOTAL = STAGE_LABELS.length;

// init-status 三态：
// - 'loading'：尚未拉取
// - 'ok'：已拉到，按 stage 状态决定默认勾选
// - 'failed'：请求失败 → 默认全选（保守不跳过任何环节）
type InitStatusLoadState = 'loading' | 'ok' | 'failed';

// stage 勾选维度（与 STAGE_LABELS.stage 对齐）：
// - stage：环节 id
// - forced：保留字段恒为 false（向后兼容旧测试断言；语义上所有环节都允许
//          自由勾选/取消，不再锁定未 done 环节）
// - done：来自 init-status（用于展示「已有设定 / 未生成」徽标）
// - label / detail：来自 init-status 的展示字段
type StageSelection = {
  stage: string;
  forced: boolean;
  done: boolean;
  label: string;
  detail: string | null;
};

type PanelState =
  | 'idle'
  | 'busy'
  | 'review'
  | 'busy-resume'
  | 'done';

type PanelMode = 'idle-form' | 'review' | 'done';

/**
 * 解析表单中的 chapter_seed_count 字符串。
 * - 空 / NaN / < 1 / > 100 视为无效（返回 null）。
 * - 与后端 ProjectInitRequest.chapter_seed_count 的 Field(ge=1, le=100) 对齐，
 *   让前端校验在 Pydantic 422 之前先拦截。
 */
function parseSeedCount(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const n = Number.parseInt(trimmed, 10);
  if (!Number.isFinite(n)) return null;
  if (n < CHAPTER_SEED_COUNT_MIN || n > CHAPTER_SEED_COUNT_MAX) return null;
  return n;
}

/**
 * 由 (target_words, chapter_word_count) 推导章节种子数。
 * 公式与后端 _derive_chapter_seed_count 一致：round(target_words / chapter_word_count)，
 * clamp 到 [DERIVED_SEED_COUNT_MIN, DERIVED_SEED_COUNT_MAX]（前端版）。
 * - 任一输入为空/非正整数 → 返回 null（不重算，保持用户当前值）。
 * - 注意：后端允许范围是 [10, 500]，但前端 chapter_seed_count 走顶层字段（Field le=100），
 *   因此前端 clamp 到 [10, 100] 以确保提交不 422；推导出 >100 时由 UI 提示用户。
 */
function deriveSeedCount(
  targetWordsRaw: string,
  chapterWordCountRaw: string,
): number | null {
  const tw = Number.parseInt(targetWordsRaw.trim(), 10);
  const cw = Number.parseInt(chapterWordCountRaw.trim(), 10);
  if (!Number.isFinite(tw) || tw <= 0) return null;
  if (!Number.isFinite(cw) || cw <= 0) return null;
  const derived = Math.round(tw / cw);
  if (derived < DERIVED_SEED_COUNT_MIN) return DERIVED_SEED_COUNT_MIN;
  if (derived > DERIVED_SEED_COUNT_MAX) return DERIVED_SEED_COUNT_MAX;
  return derived;
}

/**
 * 安全地把任意值规整成 Record<string, unknown>（用于编辑态初始值）。
 * 后端 draft 是超集；非对象 → {}。
 */
function asRecord(v: unknown): Record<string, unknown> {
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    return v as Record<string, unknown>;
  }
  return {};
}

/**
 * ProjectInitPanel —— 项目总览页「AI 初始化设定」面板（P1 project-init）。
 *
 * 状态机：
 *   idle（填写表单）
 *     └─ busy ─ POST /projects/init ─┬─ status=COMPLETED ─→ done
 *     │                              └─ status=PAUSED    ─→ review（4 关卡审阅）
 *     │                                                          └─ status=PAUSED ─→ review（下一关）
 *     │                                                                             ├─ COMPLETED ─→ done
 *     │                                                                             └─ FAILED/Error ─→ review（带 ErrorBanner）
 *   done（沿用 InfoBanner + onDone）
 *
 * 表单默认勾选「分步审阅生成」→ 提交时 body.step_mode=true；不勾走老的一次性模式。
 * review 视图：4 关卡全部走结构化编辑（详见 ProjectInitEditors.tsx）。
 *  - 白名单字段：专属控件 + 中文 label（premise 6 字段 / world 4 字段 / character 1 字段数组 / outline 2 字段）
 *  - 未知键：按 string/number/其他类型 自动适配（string→textarea rows=3、number→数字、其它→JSON 域 rows=4）
 *  - 数组卡片：每张卡内白名单专属控件 + 未知键规则；卡右上「删除」/ 列表尾「+ 添加一条」
 *  - 嵌套 object（protagonist / volume）：展开一层键值对
 *  - 类型不符预期：字段整体回退 JSON 文本域 rows=14
 *  - 必填 / JSON 非法 → 提交按钮 disabled
 * 「放弃本次初始化」仅本地 reset 回 idle，不调接口（后端 run 保持 PAUSED）。
 *
 * data-testid（关键）：
 *   - project-init-panel / -toggle / -form
 *   - project-init-title / -genre / -logline / -platform / -target-words
 *     / -author-notes / -chapter-seed-count
 *   - project-init-step-mode / -submit / -busy / -cancel / -progress
 *   - project-init-warning / -overwrite-confirm
 *   - review-pane（审阅视图根）
 *   - init-steps / init-steps-item-{premise|world|character|outline}
 *   - revision-premi-{stage}（当前阶段文案「第 N / 4 步 · …」）
 *   - revision-{stage}-{field}（premise / world / character / outline 字段级控件）
 *   - revision-card-{stage}-{key}-{idx}（卡片）
 *   - revision-card-add-{stage}-{key} / revision-card-remove-{stage}-{key}-{idx}
 *   - revision-json-fallback-{stage}-{key}（类型不符预期的 JSON 兜底）
 *   - revision-degraded-warning（_degraded 提示）
 *   - revision-submit / revision-submit-busy（放行按钮两种态）
 *   - init-abandon（放弃按钮）
 *   - done-result（终态展示）
 *   - stage-model-select / -status（本关模型档案切换）
 *
 * 向后兼容（测试仍在用的旧 testid）：
 *   - revision-logline / -positioning / -selling-points / -protagonist / -title / -genre
 *   - revision-volume-title
 *   - revision-core-premise
 *   - revision-card-{world|character}-rules-0-…  / -characters-0-…
 *   - revision-card-add-{stage}-{key}
 */
export function ProjectInitPanel({ projectId, project, onDone }: Props) {
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

  // ---------------- 视图分发 -------------------------------------------

  const viewMode: PanelMode =
    panelState === 'done'
      ? 'done'
      : panelState === 'review' || panelState === 'busy-resume'
        ? 'review'
        : 'idle-form';

  return (
    <div
      className="card"
      style={{ marginBottom: 16 }}
      data-testid="project-init-panel"
    >
      <div className="detail-pane__title">AI 初始化设定（P1 project-init）</div>

      {!expanded ? (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginTop: 4,
          }}
        >
          <span className="muted small">
            根据题材 brief 自动生成前提 / 世界观 / 角色 / 卷纲与章节种子，并落库到当前项目。
          </span>
          <div style={{ flex: 1 }} />
          <button
            type="button"
            className="btn btn--sm btn--primary"
            onClick={() => setExpanded(true)}
            data-testid="project-init-toggle"
          >
            AI 初始化设定
          </button>
        </div>
      ) : (
        <div style={{ marginTop: 8 }} data-testid="project-init-form">
          {/* 挂起恢复横幅：表单之上；非 idle-form 时不渲染（已进入 review/done 视图无需重复）。 */}
          {viewMode === 'idle-form' && showSuspendedBanner ? (
            <SuspendedResumeBanner
              suspendedRun={suspendedRun!}
              busy={resuming}
              onResume={() => void handleResumeSuspended()}
              onDismiss={handleDismissSuspended}
            />
          ) : null}
          {viewMode === 'idle-form' ? (
            <IdleForm
              needsConfirm={needsConfirm}
              probeErr={probeErr}
              title={title}
              setTitle={setTitle}
              genre={genre}
              setGenre={setGenre}
              platform={platform}
              setPlatform={setPlatform}
              targetWords={targetWords}
              setTargetWords={handleTargetWordsChange}
              chapterWordCount={chapterWordCount}
              setChapterWordCount={handleChapterWordCountChange}
              chapterSeedCount={chapterSeedCount}
              setChapterSeedCount={handleChapterSeedCountChange}
              seedCountManual={seedCountManual}
              logline={logline}
              setLogline={setLogline}
              authorNotes={authorNotes}
              setAuthorNotes={setAuthorNotes}
              overwriteConfirmed={overwriteConfirmed}
              setOverwriteConfirmed={setOverwriteConfirmed}
              stepMode={stepMode}
              setStepMode={setStepMode}
              busy={busyAny}
              initModelProfileId={initModelProfileId}
              setInitModelProfileId={setInitModelProfileId}
              initProfiles={profiles}
              error={error}
              canSubmit={canSubmit}
              seedCount={seedCount}
              selectedStages={selectedStages}
              onToggleStage={handleToggleStage}
              stageMetaById={stageMetaById}
              onSubmit={() => void handleInit()}
              onCancel={() => {
                setExpanded(false);
                reset();
              }}
            />
          ) : viewMode === 'review' ? (
            <ReviewPane
              key={`${pausePayload!.stage}-${pausePayload!.stage_index}`}
              pausePayload={pausePayload!}
              busy={panelState === 'busy-resume'}
              error={error}
              bindings={bindings}
              profiles={profiles}
              stageModels={stageModels}
              onSubmit={handleResume}
              onRegenerate={handleRegenerate}
              onAbandon={reset}
              onChangeStageModel={async (capability, profileId) => {
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
              }}
            />
          ) : (
            <DonePane finalResp={finalResp} />
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// 子组件：挂起恢复横幅（仅在 idle-form 视图渲染）
// ============================================================================
interface SuspendedResumeBannerProps {
  suspendedRun: WorkflowRun;
  busy: boolean;
  onResume: () => void;
  onDismiss: () => void;
}
function SuspendedResumeBanner({
  suspendedRun,
  busy,
  onResume,
  onDismiss,
}: SuspendedResumeBannerProps) {
  // stage 中文 label 与「第 N / M 步」直接读 checkpoint_json（list 端点不保证带 pause_payload）
  const cp = suspendedRun.checkpoint_json ?? {};
  const stage = typeof cp.stage === 'string' ? cp.stage : '';
  const stageLabel = STAGE_LABELS.find((s) => s.stage === stage)?.label ?? stage;
  const stageIndex =
    typeof cp.stage_index === 'number' && cp.stage_index >= 0
      ? cp.stage_index
      : 0;
  const stagesTotal = STAGE_LABELS.length;

  return (
    <div
      className="alert alert--info"
      role="status"
      style={{ marginBottom: 10 }}
      data-testid="suspended-resume-banner"
    >
      <div style={{ marginBottom: 6 }}>
        检测到挂起的初始化（{stageLabel || '未知关卡'} · 第 {stageIndex + 1}/{stagesTotal} 步），
        是否继续上次的审阅？
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          type="button"
          className="btn btn--sm btn--primary"
          onClick={onResume}
          disabled={busy}
          data-testid="resume-suspended"
        >
          {busy ? '加载中…' : '继续审阅'}
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={onDismiss}
          disabled={busy}
          data-testid="dismiss-suspended"
        >
          忽略
        </button>
      </div>
    </div>
  );
}

// ============================================================================
// 子组件：idle 表单
// ============================================================================
interface IdleFormProps {
  needsConfirm: boolean;
  probeErr: string | null;
  title: string;
  setTitle: (v: string) => void;
  genre: string;
  setGenre: (v: string) => void;
  platform: string;
  setPlatform: (v: string) => void;
  targetWords: string;
  setTargetWords: (v: string) => void;
  chapterWordCount: string;
  setChapterWordCount: (v: string) => void;
  chapterSeedCount: string;
  setChapterSeedCount: (v: string) => void;
  /** 用户是否手动改过章节种子数（true 时改单章字数/目标字数不再自动重算）。 */
  seedCountManual: boolean;
  logline: string;
  setLogline: (v: string) => void;
  authorNotes: string;
  setAuthorNotes: (v: string) => void;
  overwriteConfirmed: boolean;
  setOverwriteConfirmed: (v: boolean) => void;
  stepMode: boolean;
  setStepMode: (v: boolean) => void;
  busy: boolean;
  error: string | null;
  canSubmit: boolean;
  seedCount: number | null;
  /** 本次初始化模型档案：'' = 走全局 capability_bindings。 */
  initModelProfileId: string;
  setInitModelProfileId: (v: string) => void;
  /** 已启用的 model_profiles 列表（驱动下拉 options）。 */
  initProfiles: ModelProfile[];
  /** 环节勾选：当前选中的 stage id 列表；onToggleStage 处理单关勾选切换。 */
  selectedStages: string[];
  onToggleStage: (stage: string, next: boolean) => void;
  /** 环节元数据：从 init-status 解析（或失败兜底），用于渲染徽标 / 强制勾选态。 */
  stageMetaById: Record<string, StageSelection>;
  onSubmit: () => void;
  onCancel: () => void;
}

function IdleForm(props: IdleFormProps) {
  const {
    needsConfirm,
    probeErr,
    title,
    setTitle,
    genre,
    setGenre,
    platform,
    setPlatform,
    targetWords,
    setTargetWords,
    chapterWordCount,
    setChapterWordCount,
    chapterSeedCount,
    setChapterSeedCount,
    seedCountManual,
    logline,
    setLogline,
    authorNotes,
    setAuthorNotes,
    overwriteConfirmed,
    setOverwriteConfirmed,
    stepMode,
    setStepMode,
    busy,
    error,
    canSubmit,
    initModelProfileId,
    setInitModelProfileId,
    initProfiles,
    selectedStages,
    onToggleStage,
    stageMetaById,
    onSubmit,
    onCancel,
  } = props;

  const regeneratingExisting = selectedStages
    .map((s) => stageMetaById[s])
    .filter((m) => m?.done)
    .map((m) => m!.label);
  const confirmText = regeneratingExisting.length
    ? `检测到已勾选重新生成已有设定的环节（${regeneratingExisting.join('、')}），将覆盖该环节既有内容，可能造成重复。`
    : null;

  return (
    <>
      {needsConfirm && confirmText ? (
        <div data-testid="project-init-warning">
          <InfoBanner>{confirmText}</InfoBanner>
        </div>
      ) : null}
      {probeErr ? (
        <div className="muted small" style={{ marginBottom: 6 }}>
          探测既有数据失败（{probeErr}），已按"无既有数据"处理。
        </div>
      ) : null}

      <ErrorBanner>{error}</ErrorBanner>

      <div className="form-grid" style={{ marginTop: 4 }}>
        <Field label="标题">
          <input
            className="input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            data-testid="project-init-title"
            disabled={busy}
          />
        </Field>
        <Field label="题材">
          <input
            className="input"
            value={genre}
            onChange={(e) => setGenre(e.target.value)}
            data-testid="project-init-genre"
            disabled={busy}
          />
        </Field>
        <Field label="平台" hint="默认「番茄·男频」">
          <input
            className="input"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            data-testid="project-init-platform"
            disabled={busy}
          />
        </Field>
        <Field label="目标字数">
          <input
            className="input"
            inputMode="numeric"
            value={targetWords}
            onChange={(e) => setTargetWords(e.target.value)}
            data-testid="project-init-target-words"
            disabled={busy}
          />
        </Field>
        <Field label="单章字数" hint="建议 2000-4000，默认 3000">
          <input
            className="input"
            inputMode="numeric"
            value={chapterWordCount}
            onChange={(e) => setChapterWordCount(e.target.value)}
            data-testid="project-init-chapter-word-count"
            disabled={busy}
          />
        </Field>
        <Field
          label="章节种子数"
          hint={
            seedCountManual
              ? '已手动设置（1-100，默认 10）'
              : '1-100，默认 10；按目标字数÷单章字数自动推导'
          }
        >
          <input
            className="input"
            inputMode="numeric"
            value={chapterSeedCount}
            onChange={(e) => setChapterSeedCount(e.target.value)}
            data-testid="project-init-chapter-seed-count"
            disabled={busy}
          />
        </Field>
      </div>

      <Field label="一句话简介（必填）" hint="驱动 premise_designer 节点">
        <textarea
          className="input"
          rows={3}
          value={logline}
          onChange={(e) => setLogline(e.target.value)}
          data-testid="project-init-logline"
          disabled={busy}
          style={{ width: '100%', resize: 'vertical' }}
        />
      </Field>

      <Field label="作者备注（可选）">
        <textarea
          className="input"
          rows={2}
          value={authorNotes}
          onChange={(e) => setAuthorNotes(e.target.value)}
          data-testid="project-init-author-notes"
          disabled={busy}
          style={{ width: '100%', resize: 'vertical' }}
        />
      </Field>

      <label
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          marginTop: 6,
        }}
        data-testid="project-init-step-mode"
      >
        <input
          type="checkbox"
          checked={stepMode}
          onChange={(e) => setStepMode(e.target.checked)}
          disabled={busy}
          data-testid="project-init-step-mode-checkbox"
        />
        <span className="small">
          分步审阅生成（推荐）：每完成一关暂停等待人工修订后再继续；关闭后将一次性生成全部设定。
        </span>
      </label>

      {/* 本次生成的环节：基于 init-status 的阶段状态徽标 + 多选。 */}
      <div
        data-testid="init-stages-section"
        style={{ marginTop: 8 }}
      >
        <div className="muted small" style={{ marginBottom: 4 }}>
          本次生成的环节
        </div>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}
        >
          {STAGE_LABELS.map((s) => {
            const meta = stageMetaById[s.stage];
            // meta 缺失（极端时序）：按"未 done 兜底"展示徽标；不再锁定勾选。
            const done = meta?.done ?? false;
            const label = meta?.label ?? s.label;
            const detail = meta?.detail ?? null;
            const checked = selectedStages.includes(s.stage);
            return (
              <label
                key={s.stage}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
                data-testid={`init-stage-row-${s.stage}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={busy}
                  onChange={(e) => onToggleStage(s.stage, e.target.checked)}
                  data-testid={`init-stage-${s.stage}`}
                />
                <span className="small" style={{ minWidth: 96 }}>
                  {label}
                </span>
                <span
                  className="small"
                  style={{
                    color: done ? 'seagreen' : 'crimson',
                    opacity: 0.8,
                  }}
                  data-testid={`init-stage-${s.stage}-badge`}
                >
                  {done ? '✅ 已有设定' : '⚠ 未完成'}
                </span>
                {detail ? (
                  <span className="muted small">· {detail}</span>
                ) : null}
              </label>
            );
          })}
        </div>
        <div className="muted small" style={{ marginTop: 4 }}>
          已完成的环节默认不勾选（沿用已有设定）；未完成的环节默认勾选（将生成），可按需取消，仅生成勾选的环节。
        </div>
      </div>

      {needsConfirm ? (
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            marginTop: 6,
          }}
          data-testid="project-init-overwrite-confirm"
        >
          <input
            type="checkbox"
            checked={overwriteConfirmed}
            onChange={(e) => setOverwriteConfirmed(e.target.checked)}
            disabled={busy}
            data-testid="project-init-overwrite-checkbox"
          />
          <span className="small">
            我已知晓：初始化将以追加方式生成新内容，可能与既有数据重复。
          </span>
        </label>
      ) : null}

      {/* 本次初始化模型档案覆盖：'' = 走全局 capability_bindings / model_configs
          （默认）；非空 = model_profiles.profile_id。本次 run 全部 4 个 AI 节点
          统一使用该档案调用 LLM；不影响其他 run 与全局 binding。 */}
      <Field label="本次初始化模型" hint="覆盖全局绑定；留空走环节绑定（默认）">
        <select
          className="input"
          value={initModelProfileId}
          onChange={(e) => setInitModelProfileId(e.target.value)}
          disabled={busy}
          data-testid="project-init-model-profile"
        >
          <option value="">默认绑定（全局）</option>
          {initProfiles
            .filter((p) => Number(p.enabled) === 1)
            .map((p) => (
              <option key={p.profile_id} value={p.profile_id}>
                {p.name}（{p.provider}/{p.model}）
              </option>
            ))}
        </select>
      </Field>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginTop: 10,
        }}
      >
        <button
          type="button"
          className="btn btn--sm btn--primary"
          disabled={!canSubmit}
          onClick={onSubmit}
          data-testid={busy ? 'project-init-busy' : 'project-init-submit'}
        >
          {busy ? '生成中…' : '开始 AI 初始化'}
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={onCancel}
          disabled={busy}
          data-testid="project-init-cancel"
        >
          收起
        </button>
        {busy ? (
          <span className="muted small" data-testid="project-init-progress">
            正在生成设定（{selectedStages.map((s) => stageMetaById[s]?.label ?? s).join(' → ')}），通常需要数分钟，请勿关闭页面。
          </span>
        ) : null}
      </div>
    </>
  );
}

// ============================================================================
// 子组件：review 视图（4 关卡审阅 + 放行 / 放弃）
// ============================================================================
interface ReviewPaneProps {
  pausePayload: ProjectInitPausePayload;
  busy: boolean;
  error: string | null;
  bindings: CapabilityBinding[];
  profiles: ModelProfile[];
  /** 本次 run 各 AI 节点实际调用的 model_id（按 agent 名聚合），来自 GET /runs/{id} PAUSED 时的 stage_models。null = 未拉到，不渲染。 */
  stageModels: Record<string, string> | null;
  onSubmit: (parsed: Record<string, unknown>) => void;
  onRegenerate: (note: string) => void;
  onAbandon: () => void;
  onChangeStageModel: (capability: string, profileId: string) => Promise<void>;
}

function ReviewPane({
  pausePayload,
  busy,
  error,
  bindings,
  profiles,
  stageModels,
  onSubmit,
  onRegenerate,
  onAbandon,
  onChangeStageModel,
}: ReviewPaneProps) {
  const stageMeta =
    STAGE_LABELS.find((s) => s.stage === pausePayload.stage) ?? STAGE_LABELS[0]!;
  const stepIndex = Math.min(
    Math.max(pausePayload.stage_index ?? 0, 0),
    STAGES_TOTAL - 1,
  );
  const stepNo = stepIndex + 1;
  const degraded = pausePayload.degraded === true;
  const initialDraft = asRecord(pausePayload.draft);

  // 绑定状态：当前 capability 的 profile_id（取第一项；后端允许数组 → 多档选一个）
  const currentBinding = bindings.find((b) => b.capability === stageMeta.capability);
  const currentProfileId = currentBinding?.profile_ids[0] ?? '';
  const enabledProfiles = profiles.filter((p) => Number(p.enabled) === 1);

  // 本次实际使用的模型：stage → agent 名（与 project-init pipeline 节点 id 对齐）。
  // 从 stage_models 中按当前 stage 取 model_id；model_id 含 '/' 时剥掉 provider 前缀
  // （如 `openai_compatible/k3-256k` → `k3-256k`），与下拉 option 文案（name（model））风格一致。
  const STAGE_TO_AGENT: Record<string, string> = {
    premise: 'premise_designer',
    world: 'world_builder',
    character: 'character_designer',
    outline: 'volume_outliner',
  };
  const usedAgentName = STAGE_TO_AGENT[stageMeta.stage];
  const usedModelId = usedAgentName ? stageModels?.[usedAgentName] : undefined;
  const usedModelShort = usedModelId
    ? usedModelId.includes('/')
      ? usedModelId.split('/').slice(1).join('/')
      : usedModelId
    : null;
  const [modelMsg, setModelMsg] = useState<{
    kind: 'ok' | 'error';
    text: string;
  } | null>(null);

  const handleModelChange = async (profileId: string): Promise<void> => {
    setModelMsg(null);
    try {
      await onChangeStageModel(stageMeta.capability, profileId);
      setModelMsg({
        kind: 'ok',
        text: profileId === '' ? '下一关起生效 · 未绑定（默认配置）' : '下一关起生效 ✓',
      });
    } catch (e: unknown) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setModelMsg({ kind: 'error', text: `保存失败：${msg}` });
    }
  };

  // 用本地 state 维护编辑缓冲；提交时才回写到后端（避免每次按键打 API）。
  // 数据完整性铁律：draft 必须原样保留所有未知键，editor 只更新它认识的键。
  const [draft, setDraft] = useState<Record<string, unknown>>(initialDraft);
  const [premiseDraft, setPremiseDraft] = useState<Record<string, unknown>>(() => {
    // premise state 与顶层 draft 复用同一份 dict，避免双源不一致。
    return { ...initialDraft };
  });
  // 「带意见重新生成」的意见输入缓冲（不随 stage 切换而持久化——换关卡时意见可丢）。
  const [regenerateNote, setRegenerateNote] = useState<string>('');

  // P1.2「带意见重新生成」或重新挂起后，pausePayload.draft 会被新对象替换：
  // 此时必须重置本地 draft / premiseDraft / 编辑缓冲，让 UI 反映 AI 重新生成的新草稿。
  // stage/stage_index 也可能变化（虽然 regenerate 同关卡，但 PAUSED→PAUSED 的 stage 可能不同）。
  useEffect(() => {
    const next = asRecord(pausePayload.draft);
    setDraft(next);
    setPremiseDraft({ ...next });
    setRegenerateNote('');
    setInvalidJsonDomains(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pausePayload]);

  // 非法 JSON 域注册表：JSON 域 parse 失败时上抛，禁用放行。
  const [invalidJsonDomains, setInvalidJsonDomains] = useState<Set<string>>(
    () => new Set(),
  );
  const handleJsonValidityChange = useCallback<JsonValidityChange>(
    (domainKey, ok) => {
      setInvalidJsonDomains((prev) => {
        const next = new Set(prev);
        if (ok) next.delete(domainKey);
        else next.add(domainKey);
        return next;
      });
    },
    [],
  );

  // ---- 校验：必填 + 任何 JSON 兜底域非法 → 禁用放行 ----------------------
  const { ok, parsed, errors } = useMemo(
    () => composeAndValidate(stageMeta.stage, draft),
    [stageMeta.stage, draft],
  );

  // world._degraded 强校验：core_premise 非空（沿用旧行为）
  const worldCorePremiseError = (() => {
    if (!degraded) return null;
    if (stageMeta.stage !== 'world') return null;
    const cp = draft.core_premise;
    if (typeof cp !== 'string' || cp.trim().length === 0) {
      return 'core_premise 必填（AI 降级，请人工补全）';
    }
    return null;
  })();
  const effectiveErrors = worldCorePremiseError
    ? { ...errors, core_premise: worldCorePremiseError }
    : errors;
  const hasInvalidJson = invalidJsonDomains.size > 0;
  const canSubmit = ok && !worldCorePremiseError && !hasInvalidJson && !busy;

  const handleClickSubmit = () => {
    if (!canSubmit || !parsed) return;
    onSubmit(parsed);
  };

  // 「带意见重新生成」：意见可空（纯重试场景），不阻塞。
  const handleClickRegenerate = () => {
    if (busy) return;
    onRegenerate(regenerateNote);
  };

  // premise 用专用的 PremiseEditor（共享 draft state）
  const premiseEditor = (
    <PremiseEditor
      premise={premiseDraft}
      setPremise={(updater) => {
        setPremiseDraft((prev) => {
          const next =
            typeof updater === 'function'
              ? (updater as (p: Record<string, unknown>) => Record<string, unknown>)(prev)
              : updater;
          setDraft(next);
          return next;
        });
      }}
      errors={effectiveErrors}
      busy={busy}
      onJsonValidityChange={handleJsonValidityChange}
    />
  );

  const stageEditor = (() => {
    switch (stageMeta.stage) {
      case 'premise':
        return premiseEditor;
      case 'world':
        return (
          <WorldEditor
            draft={draft}
            onChange={setDraft}
            errors={effectiveErrors}
            onJsonValidityChange={handleJsonValidityChange}
          />
        );
      case 'character':
        return (
          <CharacterEditor
            draft={draft}
            onChange={setDraft}
            errors={effectiveErrors}
            onJsonValidityChange={handleJsonValidityChange}
          />
        );
      case 'outline':
        return (
          <OutlineEditor
            draft={draft}
            onChange={setDraft}
            errors={effectiveErrors}
            onJsonValidityChange={handleJsonValidityChange}
          />
        );
      default:
        return (
          <FullJsonFallbackEditor
            stage={stageMeta.stage}
            draft={draft}
            onChange={setDraft}
            onJsonValidityChange={handleJsonValidityChange}
          />
        );
    }
  })();

  return (
    <div data-testid="review-pane">
      <ErrorBanner>{error}</ErrorBanner>

      <StepsBar currentIndex={stepIndex} />

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginTop: 6,
          flexWrap: 'wrap',
        }}
      >
        <span className="muted small">本关模型：</span>
        <select
          value={currentProfileId}
          onChange={(e) => void handleModelChange(e.target.value)}
          disabled={busy}
          data-testid="stage-model-select"
          style={{ minWidth: 240 }}
        >
          <option value="">默认配置（未绑定）</option>
          {enabledProfiles.map((p) => (
            <option key={p.profile_id} value={p.profile_id}>
              {p.name}（{p.model}）
            </option>
          ))}
        </select>
        {modelMsg ? (
          <span
            className="small"
            style={{
              color: modelMsg.kind === 'ok' ? 'seagreen' : 'crimson',
            }}
            data-testid="stage-model-status"
          >
            {modelMsg.text}
          </span>
        ) : null}
        {usedModelShort ? (
          <span
            className="muted small"
            data-testid="stage-used-model"
            title={usedModelId ?? undefined}
          >
            本次实际使用：{usedModelShort}
          </span>
        ) : null}
      </div>

      <div
        className="muted small"
        style={{ marginTop: 6 }}
        data-testid={`revision-premi-${stageMeta.stage}`}
      >
        第 {stepNo} / {STAGES_TOTAL} 步 · 人工审阅 · 当前关卡：{stageMeta.label}
      </div>

      {degraded ? (
        <div
          className="alert alert--warning"
          role="status"
          style={{ marginTop: 6 }}
          data-testid="revision-degraded-warning"
        >
          该环节 AI 生成降级，请人工补全后再放行。
        </div>
      ) : null}

      <div style={{ marginTop: 8 }}>{stageEditor}</div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginTop: 10,
        }}
      >
        <button
          type="button"
          className="btn btn--sm btn--primary"
          disabled={!canSubmit}
          onClick={handleClickSubmit}
          data-testid={busy ? 'revision-submit-busy' : 'revision-submit'}
        >
          {busy ? '提交修订中…' : '确认修订并继续'}
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={onAbandon}
          disabled={busy}
          data-testid="init-abandon"
        >
          放弃本次初始化
        </button>
        {!canSubmit ? (
          <span className="muted small">
            请先修正高亮字段（必填或 JSON 非法）再放行。
          </span>
        ) : null}
      </div>

      {/* P1.2：带意见重新生成入口 —— 丢弃当前 draft，让 AI 重跑本关 */}
      <div style={{ marginTop: 10 }} data-testid="regenerate-section">
        <textarea
          className="input"
          rows={2}
          value={regenerateNote}
          onChange={(e) => setRegenerateNote(e.target.value)}
          placeholder="在此输入希望调整的方向，AI 将按意见重新生成本关…"
          disabled={busy}
          data-testid="regenerate-note"
          style={{ width: '100%', resize: 'vertical' }}
        />
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginTop: 6,
          }}
        >
          <button
            type="button"
            className="btn btn--sm"
            onClick={handleClickRegenerate}
            disabled={busy}
            data-testid="regenerate-submit"
          >
            带意见重新生成
          </button>
          <span className="muted small">
            重新生成将丢弃本关当前内容，由 AI 重新生成；意见可空（纯重试）。
          </span>
        </div>
      </div>

      <div className="muted small" style={{ marginTop: 6 }}>
        放弃本次初始化仅本地重置，后端 run 仍保持 PAUSED，不影响数据；后续可在项目总览页重新发起。
      </div>
    </div>
  );
}

// ============================================================================
// 子组件：步骤条
// ============================================================================
function StepsBar({ currentIndex }: { currentIndex: number }) {
  return (
    <div
      style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}
      data-testid="init-steps"
    >
      {STAGE_LABELS.map((s, i) => {
        const done = i < currentIndex;
        const active = i === currentIndex;
        return (
          <div
            key={s.stage}
            data-testid={`init-steps-item-${s.stage}`}
            style={{
              padding: '2px 8px',
              borderRadius: 4,
              border: '1px solid #888',
              opacity: done || active ? 1 : 0.45,
              fontSize: 12,
              background: active ? 'var(--color-accent, #eef)' : undefined,
            }}
          >
            {done ? '✓ ' : active ? '● ' : ''}
            {s.label}
          </div>
        );
      })}
    </div>
  );
}

// ============================================================================
// 子组件：done 视图（终态展示）
// ============================================================================
function DonePane({ finalResp }: { finalResp: ProjectInitResponse | null }) {
  return (
    <InfoBanner>
      <div data-testid="done-result">
        初始化完成 run_id={finalResp?.run_id ?? '-'}（status=
        {finalResp?.status ?? '-'}
        {finalResp?.current_node
          ? `，current_node=${finalResp.current_node}`
          : ''}
        ），请到 Story Bible 查看生成结果
        {finalResp?.project_id ? '（project_id=' + finalResp.project_id + '）' : ''}。
      </div>
    </InfoBanner>
  );
}

// ============================================================================
// 通用 Field
// ============================================================================
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginTop: 6 }}>
      <div className="muted small">
        {label}
        {hint ? <span className="muted small"> · {hint}</span> : null}
      </div>
      {children}
    </div>
  );
}