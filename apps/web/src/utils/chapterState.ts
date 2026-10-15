// 章节状态机按钮可用性（纯函数，便于单测）。
// 对齐 packages/domain/chapter/models.py 中的 ALLOWED_NEXT。
//
// 状态机白名单（后端给死）：
//   PLANNED   -> {PLANNED, DRAFTED}
//   DRAFTED   -> {PLANNED, DRAFTED, REVIEWED}
//   REVIEWED  -> {PLANNED, REVIEWED, COMMITTED}
//   COMMITTED -> {PLANNED, COMMITTED, RELEASED}
//   RELEASED  -> {PLANNED, RELEASED}
//
// 前端只关心四个工作流按钮的可用性：
//   - plan   ：生成计划（director）。仅 PLANNED 状态可启；
//              后端 chapter-write 校验要求 plan_json 已落；启动 plan 任务本身可在 PLANNED/DRAFTED/... 都启，
//              但 UI 上只在 PLANNED 暴露，避免覆盖已有计划。
//   - write  ：写正文（writer）。仅 PLANNED 或 DRAFTED 可启（write 节点在非 PLANNED/DRAFTED 会抛错）；
//              DRAFTED 重跑允许追加新 draft 版本（支撑「驳回并改稿」后的人工改稿循环）。
//   - review ：审校（review）。仅 DRAFTED 可启（mark_reviewed 节点会校验）。
//   - commit ：提交（commit）。仅 REVIEWED 可启（commit 节点会校验）。
//
// 额外约束：「每次只能一个 run 活跃」。前端简单判断：该项目最近一次 run 若 status=RUNNING，
// 则 4 个按钮全部禁用；若 status=PAUSED 则允许继续（resume 用审批卡片走；启动按钮是否可点击取决于状态机）。
//
// 注：每条规则的依据都是后端 pipeline.py 的硬校验口径（与 router 路径无关），这里只是把它前置到 UI。

import type { ChapterStatus, WorkflowRunStatus } from '../api/types';

export type WorkflowAction = 'plan' | 'write' | 'review' | 'commit';

export type PipelineStepState = 'done' | 'current' | 'todo';

const STATUS_RANK: Record<ChapterStatus, number> = {
  PLANNED: 0,
  DRAFTED: 1,
  REVIEWED: 2,
  COMMITTED: 3,
  RELEASED: 4,
};

/**
 * 推导四步流水线（plan→write→review→commit）每步的展示状态。
 * done    = 步骤产物已存在（状态机已越过该步，或 plan_json 已落库）；
 * current = 第一个非 done 的步骤（用户下一步该做的）；
 * todo    = 尚未到达。全部 done 时不存在 current（全部返回 'done'）。
 * 注意：本函数只管展示态，按钮可否点击仍由 getButtonAvailability 决定。
 */
export function getPipelineStepStates(input: {
  chapterStatus: ChapterStatus;
  hasPlan: boolean;
}): Record<WorkflowAction, PipelineStepState> {
  const rank = STATUS_RANK[input.chapterStatus];
  const done: Record<WorkflowAction, boolean> = {
    plan: input.hasPlan || rank >= 1,
    write: rank >= 1,
    review: rank >= 2,
    commit: rank >= 3,
  };
  const order: WorkflowAction[] = ['plan', 'write', 'review', 'commit'];
  const result: Record<WorkflowAction, PipelineStepState> = {
    plan: 'todo',
    write: 'todo',
    review: 'todo',
    commit: 'todo',
  };
  let currentMarked = false;
  for (const step of order) {
    if (done[step]) {
      result[step] = 'done';
    } else if (!currentMarked) {
      result[step] = 'current';
      currentMarked = true;
    } else {
      result[step] = 'todo';
    }
  }
  return result;
}

export interface ActiveRunInfo {
  status: WorkflowRunStatus;
}

/** 提交前的时序检查所需的最小 run 形状（只取用得上的四个字段）。 */
export type ReviewRunLike = {
  chapter_id: string | null;
  status: WorkflowRunStatus;
  ended_at: string | null;
  /**
   * router 层 `list_runs_endpoint` 附加（`runs.py:282`）。
   * ⚠️ **不可用 `workflow_id` 代替**：那是不透明的 `wf_<hex>`（dev 库实证：
   * chapter-review = `wf_0a10d0d159c9`），前端无从反查名字。
   * 缺该字段的列表行一律**判不出是 review** ⇒ 退化成不拦（见下方函数说明）。
   */
  workflow_name?: string | null;
};

/** 提交前的时序检查所需的最小 draft 形状。 */
export type DraftLike = { version: number; created_at: string };

export interface UnreviewedDraft {
  /** 那份「审完之后又冒出来」的草稿版本号 */
  version: number;
  createdAt: string;
  reviewEndedAt: string;
}

/**
 * 找出「审完又改稿」的未审草稿；无则 null。
 *
 * **逐字对齐后端时序守卫**（`api/routers/workflows/control.py::_guard_commit_draft_freshness`
 * 与 `workflows/chapter_commit/commit.py` 内同款）：
 *   1. 取该 chapter 最近一次 COMPLETED 的 chapter-review run 的 `ended_at`
 *      （无 → 不拦，由既有 REVIEWED 状态机校验兜底）；
 *   2. 若存在 `created_at` 严格更晚的 draft → 拒收（防「审过 v5、改出 v6、commit 定稿 v6」）。
 *
 * **为什么前端要重算一遍**：守卫在后端，作者点「提交」只会拿到一句 409；
 * UI 不建模这条就等于让作者靠撞墙来发现规则。
 *
 * **时间比较用 `Date.parse` 而非字符串字典序**：后端 SQL 对同一批 `now_iso()`
 * 产物做文本比较（格式统一 ⇒ 字典序即时间序）。前端解析成毫秒，结论一致，
 * 且万一将来两侧格式分叉（带不带毫秒/偏移）也**不会误判为「有未审改动」**——
 * 那会把合法的提交按钮错误地禁用掉。解析失败（NaN）同样按「无未审改动」处理：
 * **方向性选择**——误报（多拦）会挡住本该能做的操作，漏报只是退回今天的
 * 行为（点提交吃一个 409），后者代价小得多。
 */
export function findUnreviewedDraft(input: {
  chapterId: string;
  runs: readonly ReviewRunLike[];
  drafts: readonly DraftLike[];
}): UnreviewedDraft | null {
  let reviewEnd: string | null = null;
  let reviewEndMs = Number.NEGATIVE_INFINITY;
  for (const run of input.runs) {
    if (run.chapter_id !== input.chapterId) continue;
    if (run.workflow_name !== 'chapter-review') continue;
    if (run.status !== 'COMPLETED') continue;
    if (!run.ended_at) continue;
    const ms = Date.parse(run.ended_at);
    if (Number.isNaN(ms)) continue;
    if (ms > reviewEndMs) {
      reviewEndMs = ms;
      reviewEnd = run.ended_at;
    }
  }
  if (reviewEnd === null) return null;

  let stale: DraftLike | null = null;
  let staleMs = Number.NEGATIVE_INFINITY;
  for (const draft of input.drafts) {
    const ms = Date.parse(draft.created_at);
    if (Number.isNaN(ms) || ms <= reviewEndMs) continue;
    if (ms > staleMs) {
      staleMs = ms;
      stale = draft;
    }
  }
  if (stale === null) return null;
  return { version: stale.version, createdAt: stale.created_at, reviewEndedAt: reviewEnd };
}

export interface ButtonAvailabilityInput {
  chapterStatus: ChapterStatus;
  activeRun: ActiveRunInfo | null;
  /**
   * 快通道是否开启（后端 projects.fast_mode，迁移 0031）。
   *
   * 开启时「提交」按钮在 DRAFTED 也可点——对应后端 commit 节点在快通道下
   * 放行 DRAFTED 直提。**关闭时行为与该开关引入前逐字相同**。
   *
   * 刻意**不改** EXPECTED_STATUS 常量：它是与后端 ALLOWED_NEXT 对齐的状态机
   * 面，与快通道无关；快通道是「旁路」，不是状态机的合法后继。
   */
  fastMode?: boolean;
  /**
   * 「审完又改稿」的未审草稿（由 :func:`findUnreviewedDraft` 算出）。
   * 非 null ⇒ 提交被禁，理由给出该去做什么。
   *
   * **必须排在快通道旁路之前**（见 :func:`getButtonAvailability`）：后端那道守卫
   * 在 router 层 `start_commit` 里、`_start_workflow`（快通道解析）**之前**执行，
   * 所以快通道同样会被它拦下。若 UI 把旁路放在前面，就会在一个必然吃 409 的
   * 组合上把按钮点亮——又是一次「UI 放行而 backend 400/409」。
   */
  unreviewedDraft?: UnreviewedDraft | null;
}

export interface ButtonAvailability {
  /** 是否可点击（同时通过状态机与活动 run 校验） */
  enabled: boolean;
  /** 不可点击时的原因（鼠标悬浮提示用） */
  reason: string | null;
}

const ACTION_LABEL: Record<WorkflowAction, string> = {
  plan: '生成计划',
  write: '写正文',
  review: '审校',
  commit: '提交',
};

/**
 * 四步流水线的固定次序（G4：禁用理由要说「怎么办」，就得知道「上一步是谁」）。
 * 与 `getPipelineStepStates` 的 order 同源，两处必须一致。
 */
const PIPELINE_ORDER: WorkflowAction[] = ['plan', 'write', 'review', 'commit'];

/**
 * 给被禁用的按钮指出**下一步该点哪个**（G4）。
 *
 * 做法：从目标 action 往前找第一个「其 EXPECTED_STATUS 覆盖当前状态」的上游步骤——
 * 那就是作者此刻该点的按钮。找不到就返回 null（**不编造**）。
 *
 * 为什么不能一句话说「先做上一步」：状态机不是单向的，几种组合根本没有上游可做
 * （例：`review` 在 REVIEWED 时，`write` 要 DRAFTED/PLANNED、`plan` 要 PLANNED，
 * 两者都不覆盖 REVIEWED ⇒ 无路可走，UI 就不该假装有）。
 *
 * 措辞刻意是「请先完成『X』」而非「点 X 即可提交」——前者是事实，后者可能撒谎
 * （有的组合做完 X 仍不到目标状态）。
 */
function upstreamHint(action: WorkflowAction, chapterStatus: ChapterStatus): string | null {
  const idx = PIPELINE_ORDER.indexOf(action);
  for (let j = idx - 1; j >= 0; j--) {
    const upstream = PIPELINE_ORDER[j];
    if (EXPECTED_STATUS[upstream].includes(chapterStatus)) {
      return `请先完成「${ACTION_LABEL[upstream]}」`;
    }
  }
  return null;
}

/**
 * 给定章节状态 + 是否有活跃 run，判断指定 workflow 按钮是否可点击。
 *
 * - activeRun.status ∈ {RUNNING, PENDING} → 全部禁用（等待当前 run 完成 / 即将运行）。
 * - activeRun.status === 'PAUSED'  → 允许继续：resume 走审批卡片（批准/驳回/驳回并改稿三态，
 *   revise 分支 run 以 FAILED(rejected-for-revision) 收尾、chapter 保持 DRAFTED）；按钮仍按状态机决定。
 * - 其它终态（COMPLETED/FAILED/CANCELLED）→ 不阻止。
 */
export function getButtonAvailability(
  action: WorkflowAction,
  input: ButtonAvailabilityInput,
): ButtonAvailability {
  const { chapterStatus, activeRun } = input;
  if (activeRun && (activeRun.status === 'RUNNING' || activeRun.status === 'PENDING')) {
    return {
      enabled: false,
      reason: '当前有进行中的工作流 run，请等待其完成',
    };
  }
  // G5 时序守卫：**排在快通道旁路之前**。后端 `_guard_commit_draft_freshness` 在
  // router 层 start_commit 里、早于快通道解析执行，所以「审过又改稿 + 快通道开着」
  // 这个组合照样 409。若把旁路放前面，就会在必然失败的组合上把按钮点亮。
  if (action === 'commit' && input.unreviewedDraft) {
    return {
      enabled: false,
      reason:
        `审校完成后又产生了草稿 v${input.unreviewedDraft.version}（未审改动），` +
        '需先重新审校再提交',
    };
  }
  // 快通道旁路：commit 在 DRAFTED 也可直达（对应后端 commit 节点的 fast 放行）。
  // 只放宽 commit，不放宽其它 action——旁路范围必须最小。
  if (action === 'commit' && input.fastMode === true && chapterStatus === 'DRAFTED') {
    return { enabled: true, reason: null };
  }
  const expected = EXPECTED_STATUS[action];
  if (!expected.includes(chapterStatus)) {
    const hint = upstreamHint(action, chapterStatus);
    return {
      enabled: false,
      reason:
        `${ACTION_LABEL[action]} 仅在状态为 ${expected.join(' / ')} 时可用；当前状态 ${chapterStatus}` +
        (hint ? `。${hint}。` : ''),
    };
  }
  return { enabled: true, reason: null };
}

/** 状态机允许的工作流入口状态集 */
export const EXPECTED_STATUS: Record<WorkflowAction, ChapterStatus[]> = {
  plan: ['PLANNED'],
  write: ['PLANNED', 'DRAFTED'],
  review: ['DRAFTED'],
  commit: ['REVIEWED'],
};

/** 状态机完整白名单（与后端 ALLOWED_NEXT 对齐），便于 UI 验证或调试展示 */
export const ALLOWED_NEXT: Record<ChapterStatus, ChapterStatus[]> = {
  PLANNED: ['PLANNED', 'DRAFTED'],
  DRAFTED: ['PLANNED', 'DRAFTED', 'REVIEWED'],
  REVIEWED: ['PLANNED', 'REVIEWED', 'COMMITTED'],
  COMMITTED: ['PLANNED', 'COMMITTED', 'RELEASED'],
  RELEASED: ['PLANNED', 'RELEASED'],
};

/**
 * 判断一个 workflow run 是否属于指定章节。
 *
 * 后端 list_runs 的 SQL 把 chapter_id = NULL 的行也带出（"非章节任务"），且 chapter_id 列存在；
 * 直接读 row.chapter_id 即可，不需要回退到 checkpoint_json。
 */
export function isRunForChapter(
  run: { chapter_id?: string | null; checkpoint_json?: Record<string, unknown> | null },
  chapterId: string,
): boolean {
  if (run.chapter_id && run.chapter_id === chapterId) return true;
  // 兜底：极少数旧数据里 chapter_id 列缺失，回退到 checkpoint_json.chapter_id
  const ckpt = run.checkpoint_json || {};
  const inner = (ckpt['chapter_id'] || ckpt['_chapter_id']) as unknown;
  return typeof inner === 'string' && inner === chapterId;
}

/**
 * 判断 run 是否处于活跃状态（应阻止新启动）。
 */
export function isActiveRun(run: { status: WorkflowRunStatus } | null | undefined): boolean {
  if (!run) return false;
  return run.status === 'RUNNING' || run.status === 'PENDING';
}

/**
 * 从 runs 列表里挑出该章节最新的一个活跃 run（按 started_at DESC）。
 * 若没有活跃 run 返回 null。
 */
export function pickActiveRun<
  T extends {
    started_at: string;
    chapter_id?: string | null;
    checkpoint_json?: Record<string, unknown> | null;
    status: WorkflowRunStatus;
  },
>(runs: T[], chapterId: string): T | null {
  const mine = runs.filter((r) => isRunForChapter(r, chapterId));
  mine.sort((a, b) => (a.started_at < b.started_at ? 1 : a.started_at > b.started_at ? -1 : 0));
  for (const r of mine) {
    if (isActiveRun(r)) return r;
  }
  return null;
}

/**
 * 从 runs 列表里挑出该章节最新的 run（不限制状态）。
 */
export function pickLatestRun<
  T extends {
    started_at: string;
    chapter_id?: string | null;
    checkpoint_json?: Record<string, unknown> | null;
  },
>(runs: T[], chapterId: string): T | null {
  const mine = runs.filter((r) => isRunForChapter(r, chapterId));
  mine.sort((a, b) => (a.started_at < b.started_at ? 1 : a.started_at > b.started_at ? -1 : 0));
  return mine[0] ?? null;
}

/**
 * run 是否「人工纯驳回」：error 精确等于 'rejected'（review author_review 节点产生）。
 *
 * 后端两态口径（packages/workflows/chapter_review/pipeline.py:594-599）：
 *   - author_review 纯驳回 → FAILED(error='rejected')
 *   - 驳回并改稿         → FAILED(error='rejected-for-revision')
 * 其它 FAILED 的 error（如 commit 校验失败 'observer delta failed validation...'）
 * 都包含子串 'rejected' 但不是真驳回，必须用精确匹配排除。
 */
export function isRejectedRun(error: string | null | undefined): boolean {
  if (error == null) return false;
  return error.trim() === 'rejected';
}

/**
 * run 是否「驳回并改稿」：error 以 'rejected-for-revision' 开头。
 *
 * 顺序陷阱：'rejected-for-revision' 包含子串 'rejected'，调用方必须
 * 先判 isRejectedForRevisionRun，再判 isRejectedRun，否则会被裸
 * 'rejected' 分支误吞。
 */
export function isRejectedForRevisionRun(error: string | null | undefined): boolean {
  if (error == null) return false;
  return error.trim().startsWith('rejected-for-revision');
}

/**
 * run 节点是否「用户主动取消」（协作式 cancel 命中 checkpoint 前探针后被标 FAILED + 此 error）。
 *
 * 后端语义（packages/core/workflow_runtime/engine.py）：发起 cancel_run 后，被取消的当前节点
 * 标 FAILED(error='cancelled by user')（复用真失败枚举，F3 取舍见 CHANGELOG）——前端必须用
 * 精确字符串区分：是用户主动停手、不是真失败。
 *
 * 顺序陷阱：必须先判 isCancelledByUserNode 再判真失败，否则会落红色 ErrorBanner 误导用户。
 */
export function isCancelledByUserNode(error: string | null | undefined): boolean {
  if (error == null) return false;
  return error.trim() === 'cancelled by user';
}

/**
 * 提取 pause payload 中的「high risk 变更条数」。
 * chapter-commit.high_risk_approval 的 payload 形如
 *   { stage, message, delta_id, changes: { character_changes, world_changes } }
 * UI 用它展示「待审批 N 项变更」。
 * 其它 stage 返回 0。
 */
export function countHighRiskChanges(pausePayload: unknown): number {
  if (!pausePayload || typeof pausePayload !== 'object') return 0;
  const pp = pausePayload as Record<string, unknown>;
  const changes = pp['changes'];
  if (!changes || typeof changes !== 'object') return 0;
  const c = changes as Record<string, unknown>;
  const cc = Array.isArray(c['character_changes']) ? c['character_changes'].length : 0;
  const wc = Array.isArray(c['world_changes']) ? c['world_changes'].length : 0;
  return cc + wc;
}
