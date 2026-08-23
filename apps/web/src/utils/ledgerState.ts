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
    status: HookStatus;
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