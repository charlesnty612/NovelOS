// Hook / Debt 状态机纯函数（与后端 packages/domain/ledger/models.py 对齐）。
//
// 状态机白名单（后端给死）：
//   Hook 五态机（PRD §21）：
//     OPEN       → {OPEN, ACTIVE, ABANDONED}
//     ACTIVE     → {ACTIVE, ESCALATED, RESOLVED, ABANDONED}
//     ESCALATED  → {ESCALATED, RESOLVED, ABANDONED}
//     RESOLVED   → {RESOLVED, ABANDONED}    # 兜底：已 RESOLVED 后仅可放弃（业务罕见但合规）
//     ABANDONED  → {ABANDONED}              # 终态
//
//   Debt 四态机（PRD §22 + v1.1 扩展）：
//     open         → {open, acknowledged, paid, forgiven}
//     acknowledged → {acknowledged, paid, forgiven}
//     paid         → {paid}                 # 终态
//     forgiven     → {forgiven}             # 终态
//
// 前端用本模块的 getNextStatuses(from) 把「合法后继」过滤出来给 UI 的下拉框；
// 「当前状态」也可作为合法选项（即幂等保留），方便用户重复提交同一状态。
//
// RESOLVED 时若 payoff_chapter_id 仍为 NULL：后端不阻断、不 warning，直接允许。
// 前端表单允许用户事后补录兑现章节。

import type { DebtStatus, HookStatus } from '../api/types';

export const HOOK_STATUS_VALUES: readonly HookStatus[] = [
  'OPEN',
  'ACTIVE',
  'ESCALATED',
  'RESOLVED',
  'ABANDONED',
];

export const DEBT_STATUS_VALUES: readonly DebtStatus[] = [
  'open',
  'acknowledged',
  'paid',
  'forgiven',
];

export const HOOK_ALLOWED_NEXT: Record<HookStatus, readonly HookStatus[]> = {
  OPEN: ['OPEN', 'ACTIVE', 'ABANDONED'],
  ACTIVE: ['ACTIVE', 'ESCALATED', 'RESOLVED', 'ABANDONED'],
  ESCALATED: ['ESCALATED', 'RESOLVED', 'ABANDONED'],
  RESOLVED: ['RESOLVED', 'ABANDONED'],
  ABANDONED: ['ABANDONED'],
};

export const DEBT_ALLOWED_NEXT: Record<DebtStatus, readonly DebtStatus[]> = {
  open: ['open', 'acknowledged', 'paid', 'forgiven'],
  acknowledged: ['acknowledged', 'paid', 'forgiven'],
  paid: ['paid'],
  forgiven: ['forgiven'],
};

export function getNextHookStatuses(from: HookStatus): readonly HookStatus[] {
  return HOOK_ALLOWED_NEXT[from] ?? [];
}

export function getNextDebtStatuses(from: DebtStatus): readonly DebtStatus[] {
  return DEBT_ALLOWED_NEXT[from] ?? [];
}

/**
 * 判断 hook 的 status 迁移是否合法（不含状态机白名单检查之外的字段校验）。
 */
export function isHookTransitionAllowed(
  from: HookStatus,
  to: HookStatus,
): boolean {
  return HOOK_ALLOWED_NEXT[from]?.includes(to) ?? false;
}

/**
 * 判断 debt 的 status 迁移是否合法。
 */
export function isDebtTransitionAllowed(
  from: DebtStatus,
  to: DebtStatus,
): boolean {
  return DEBT_ALLOWED_NEXT[from]?.includes(to) ?? false;
}

/** 兜底：未知 status 返回「全部允许」（前端不阻断）。 */
export function safeGetNextHookStatuses(from: unknown): readonly HookStatus[] {
  if (typeof from === 'string' && (HOOK_STATUS_VALUES as readonly string[]).includes(from)) {
    return getNextHookStatuses(from as HookStatus);
  }
  return HOOK_STATUS_VALUES;
}

export function safeGetNextDebtStatuses(from: unknown): readonly DebtStatus[] {
  if (typeof from === 'string' && (DEBT_STATUS_VALUES as readonly string[]).includes(from)) {
    return getNextDebtStatuses(from as DebtStatus);
  }
  return DEBT_STATUS_VALUES;
}

// ---------------------------------------------------------------------------
// UI 展示用
// ---------------------------------------------------------------------------

export const HOOK_STATUS_LABEL: Record<HookStatus, string> = {
  OPEN: '已埋',
  ACTIVE: '激活',
  ESCALATED: '升级',
  RESOLVED: '已兑现',
  ABANDONED: '已放弃',
};

export const DEBT_STATUS_LABEL: Record<DebtStatus, string> = {
  open: '未认领',
  acknowledged: '已认领',
  paid: '已偿清',
  forgiven: '已豁免',
};

/**
 * 判定 hook 是否「逾期」：expected_payoff_chapter 对应 chapter number < 当前最大 chapter number
 * 且状态非 RESOLVED / ABANDONED。
 *
 * 纯函数：把 chapters 列表映射成 number 集合，O(N) 单次遍历。
 * 当 expected_payoff_chapter_id 为空 / 对应 chapter 不存在时，按「未约定」处理：不视为逾期。
 */
export function isHookOverdue(
  hook: {
    /** 放宽到 `string`：本函数只与终态比对，不需要整个 Literal。
     *  代价是**未知 status 会被当成「非终态」**（fail-open，多提醒一次）——
     *  这个方向是安全的：漏报无感，误报只是多染红一行。 */
    status: string;
    expected_payoff_chapter_id: string | null;
  },
  chapters: Array<{ chapter_id: string; number: number }>,
): boolean {
  if (hook.status === 'RESOLVED' || hook.status === 'ABANDONED') return false;
  const targetId = hook.expected_payoff_chapter_id;
  if (!targetId) return false;
  const target = chapters.find((c) => c.chapter_id === targetId);
  if (!target) return false;
  if (chapters.length === 0) return false;
  const maxNumber = chapters.reduce((m, c) => (c.number > m ? c.number : m), 0);
  return target.number < maxNumber;
}

/**
 * 判定 debt 是否「逾期」（2026-10-15 补齐，与 `isHookOverdue` 逐条对称）。
 *
 * **为什么必须有**：伏笔与债在业务上是同一件事的两面（「到期未了结」），但改动前
 * 只有伏笔有逾期判定（表格行染红 + 徽标），债的 `deadline_chapter_id` 只被当
 * 普通列渲染成「第 N 章」文本——作者在同一张表里看到两种待遇，且「债逾期了」
 * 这件事**没有任何地方会告诉他**。不对称的理由评估文档里也没写出来，故按对称补齐。
 *
 * 判据与伏笔同构：截止章号 < 当前最大章号，且状态非终态。
 * - 伏笔终态：`RESOLVED` / `ABANDONED`
 * - 债终态：`paid`（已偿清）/ `forgiven`（已豁免）
 *
 * `deadline_chapter_id` 为空 / 对应章不存在 / 一章没写时一律**不判逾期**
 * （同 `isHookOverdue` 的「未约定」口径）。若判据将来后端也做了一份，
 * **两侧必须同时改**——这是一条会被抄的规则。
 *
 * 同一判据也适用于**状态快照**里的 debt 行：`story_state.snapshot._load_debts`
 * 写出的正是 `{debt_id, status, deadline_chapter_id, …}`，字段同名同形，
 * 故概览页的待办面板可直接复用本函数，无需第二份实现。
 */
export function isDebtOverdue(
  debt: {
    /** 同 isHookOverdue：只与终态比对，放宽到 string，未知值按非终态处理。 */
    status: string;
    deadline_chapter_id: string | null;
  },
  chapters: Array<{ chapter_id: string; number: number }>,
): boolean {
  if (debt.status === 'paid' || debt.status === 'forgiven') return false;
  const targetId = debt.deadline_chapter_id;
  if (!targetId) return false;
  const target = chapters.find((c) => c.chapter_id === targetId);
  if (!target) return false;
  if (chapters.length === 0) return false;
  const maxNumber = chapters.reduce((m, c) => (c.number > m ? c.number : m), 0);
  return target.number < maxNumber;
}