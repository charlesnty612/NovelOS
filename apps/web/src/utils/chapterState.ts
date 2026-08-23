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
//   - write  ：写正文（writer）。仅 PLANNED 或 DRAFTED 可启（write 节点在非 PLANNED/DRAFTED 会抛错）。
//   - review ：审校（review）。仅 DRAFTED 可启（mark_reviewed 节点会校验）。
//   - commit ：提交（commit）。仅 REVIEWED 可启（commit 节点会校验）。
//
// 额外约束：「每次只能一个 run 活跃」。前端简单判断：该项目最近一次 run 若 status=RUNNING，
// 则 4 个按钮全部禁用；若 status=PAUSED 则允许继续（resume 用审批卡片走；启动按钮是否可点击取决于状态机）。
//
// 注：每条规则的依据都是后端 pipeline.py 的硬校验口径（与 router 路径无关），这里只是把它前置到 UI。

import type { ChapterStatus, WorkflowRunStatus } from '../api/types';

export type WorkflowAction = 'plan' | 'write' | 'review' | 'commit';

export interface ActiveRunInfo {
  status: WorkflowRunStatus;
}

export interface ButtonAvailabilityInput {
  chapterStatus: ChapterStatus;
  activeRun: ActiveRunInfo | null;
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
 * 给定章节状态 + 是否有活跃 run，判断指定 workflow 按钮是否可点击。
 *
 * - activeRun.status ∈ {RUNNING, PENDING} → 全部禁用（等待当前 run 完成 / 即将运行）。
 * - activeRun.status === 'PAUSED'  → 允许继续：resume 走审批卡片；按钮仍按状态机决定。
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
  const expected = EXPECTED_STATUS[action];
  if (!expected.includes(chapterStatus)) {
    return {
      enabled: false,
      reason: `${ACTION_LABEL[action]} 仅在状态为 ${expected.join(' / ')} 时可用；当前状态 ${chapterStatus}`,
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
