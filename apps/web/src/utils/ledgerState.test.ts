import { describe, expect, it } from 'vitest';
import {
  DEBT_ALLOWED_NEXT,
  DEBT_STATUS_LABEL,
  DEBT_STATUS_VALUES,
  HOOK_ALLOWED_NEXT,
  HOOK_STATUS_LABEL,
  HOOK_STATUS_VALUES,
  getNextDebtStatuses,
  getNextHookStatuses,
  isDebtTransitionAllowed,
  isHookOverdue,
  isHookTransitionAllowed,
  safeGetNextDebtStatuses,
  safeGetNextHookStatuses,
} from './ledgerState';

describe('HOOK_ALLOWED_NEXT (与后端 packages/domain/ledger/models.py 对齐)', () => {
  it('覆盖所有 HOOK_STATUS_VALUES', () => {
    expect(Object.keys(HOOK_ALLOWED_NEXT).sort()).toEqual(
      [...HOOK_STATUS_VALUES].sort(),
    );
  });
  it('OPEN 允许 OPEN/ACTIVE/ABANDONED', () => {
    expect([...HOOK_ALLOWED_NEXT.OPEN].sort()).toEqual([
      'ABANDONED',
      'ACTIVE',
      'OPEN',
    ]);
  });
  it('ACTIVE 允许 ACTIVE/ESCALATED/RESOLVED/ABANDONED', () => {
    expect([...HOOK_ALLOWED_NEXT.ACTIVE].sort()).toEqual([
      'ABANDONED',
      'ACTIVE',
      'ESCALATED',
      'RESOLVED',
    ]);
  });
  it('ESCALATED 允许 ESCALATED/RESOLVED/ABANDONED', () => {
    expect([...HOOK_ALLOWED_NEXT.ESCALATED].sort()).toEqual([
      'ABANDONED',
      'ESCALATED',
      'RESOLVED',
    ]);
  });
  it('RESOLVED 允许 RESOLVED/ABANDONED', () => {
    expect([...HOOK_ALLOWED_NEXT.RESOLVED].sort()).toEqual([
      'ABANDONED',
      'RESOLVED',
    ]);
  });
  it('ABANDONED 终态，仅自身', () => {
    expect([...HOOK_ALLOWED_NEXT.ABANDONED]).toEqual(['ABANDONED']);
  });
});

describe('DEBT_ALLOWED_NEXT (与后端对齐)', () => {
  it('覆盖所有 DEBT_STATUS_VALUES', () => {
    expect(Object.keys(DEBT_ALLOWED_NEXT).sort()).toEqual(
      [...DEBT_STATUS_VALUES].sort(),
    );
  });
  it('open 允许 open/acknowledged/paid/forgiven', () => {
    expect([...DEBT_ALLOWED_NEXT.open].sort()).toEqual([
      'acknowledged',
      'forgiven',
      'open',
      'paid',
    ]);
  });
  it('acknowledged 允许 acknowledged/paid/forgiven', () => {
    expect([...DEBT_ALLOWED_NEXT.acknowledged].sort()).toEqual([
      'acknowledged',
      'forgiven',
      'paid',
    ]);
  });
  it('paid 终态，仅自身', () => {
    expect([...DEBT_ALLOWED_NEXT.paid]).toEqual(['paid']);
  });
  it('forgiven 终态，仅自身', () => {
    expect([...DEBT_ALLOWED_NEXT.forgiven]).toEqual(['forgiven']);
  });
});

describe('getNextHookStatuses', () => {
  it('OPEN 后继集合非空且全部为大写', () => {
    const next = getNextHookStatuses('OPEN');
    expect(next.length).toBeGreaterThan(0);
    for (const n of next) expect(typeof n).toBe('string');
  });
  it('OPEN→ACTIVE 在白名单', () => {
    expect(getNextHookStatuses('OPEN')).toContain('ACTIVE');
  });
  it('OPEN→RESOLVED 不在白名单', () => {
    expect(getNextHookStatuses('OPEN')).not.toContain('RESOLVED');
  });
  it('RESOLVED→ABANDONED 在白名单（兜底）', () => {
    expect(getNextHookStatuses('RESOLVED')).toContain('ABANDONED');
  });
  it('ABANDONED 仅自身', () => {
    expect(getNextHookStatuses('ABANDONED')).toEqual(['ABANDONED']);
  });
});

describe('getNextDebtStatuses', () => {
  it('open→acknowledged 合法', () => {
    expect(getNextDebtStatuses('open')).toContain('acknowledged');
  });
  it('paid 终态仅自身', () => {
    expect(getNextDebtStatuses('paid')).toEqual(['paid']);
  });
  it('forgiven 终态仅自身', () => {
    expect(getNextDebtStatuses('forgiven')).toEqual(['forgiven']);
  });
});

describe('isHookTransitionAllowed', () => {
  it('OPEN→ACTIVE 合法', () => {
    expect(isHookTransitionAllowed('OPEN', 'ACTIVE')).toBe(true);
  });
  it('OPEN→RESOLVED 非法', () => {
    expect(isHookTransitionAllowed('OPEN', 'RESOLVED')).toBe(false);
  });
  it('ACTIVE→OPEN 倒退非法', () => {
    expect(isHookTransitionAllowed('ACTIVE', 'OPEN')).toBe(false);
  });
  it('ACTIVE→ESCALATED 合法', () => {
    expect(isHookTransitionAllowed('ACTIVE', 'ESCALATED')).toBe(true);
  });
});

describe('isDebtTransitionAllowed', () => {
  it('open→paid 合法', () => {
    expect(isDebtTransitionAllowed('open', 'paid')).toBe(true);
  });
  it('paid→open 非法（终态）', () => {
    expect(isDebtTransitionAllowed('paid', 'open')).toBe(false);
  });
  it('forgiven→acknowledged 非法（终态）', () => {
    expect(isDebtTransitionAllowed('forgiven', 'acknowledged')).toBe(false);
  });
  it('acknowledged→paid 合法', () => {
    expect(isDebtTransitionAllowed('acknowledged', 'paid')).toBe(true);
  });
});

describe('safeGetNextHookStatuses / safeGetNextDebtStatuses', () => {
  it('未知 status → 返回全部允许', () => {
    const all = safeGetNextHookStatuses('GIBBERISH');
    expect(all.length).toBe(HOOK_STATUS_VALUES.length);
  });
  it('合法 status → 与 getNextHookStatuses 一致', () => {
    expect(safeGetNextHookStatuses('OPEN')).toEqual(getNextHookStatuses('OPEN'));
  });
  it('非字符串 → 返回全部允许', () => {
    expect(safeGetNextDebtStatuses(null).length).toBe(DEBT_STATUS_VALUES.length);
    expect(safeGetNextDebtStatuses(42).length).toBe(DEBT_STATUS_VALUES.length);
  });
});

describe('HOOK_STATUS_LABEL / DEBT_STATUS_LABEL', () => {
  it('Hook 五态有中文文案', () => {
    expect(Object.keys(HOOK_STATUS_LABEL).sort()).toEqual(
      [...HOOK_STATUS_VALUES].sort(),
    );
  });
  it('Debt 四态有中文文案', () => {
    expect(Object.keys(DEBT_STATUS_LABEL).sort()).toEqual(
      [...DEBT_STATUS_VALUES].sort(),
    );
  });
  it('所有 label 非空字符串', () => {
    for (const v of HOOK_STATUS_VALUES) expect(HOOK_STATUS_LABEL[v].length).toBeGreaterThan(0);
    for (const v of DEBT_STATUS_VALUES) expect(DEBT_STATUS_LABEL[v].length).toBeGreaterThan(0);
  });
});

describe('isHookOverdue', () => {
  const chapters = [
    { chapter_id: 'c1', number: 1 },
    { chapter_id: 'c5', number: 5 },
    { chapter_id: 'c10', number: 10 },
    { chapter_id: 'c20', number: 20 },
  ];

  it('RESOLVED 不算逾期', () => {
    expect(
      isHookOverdue(
        { status: 'RESOLVED', expected_payoff_chapter_id: 'c5' },
        chapters,
      ),
    ).toBe(false);
  });
  it('ABANDONED 不算逾期', () => {
    expect(
      isHookOverdue(
        { status: 'ABANDONED', expected_payoff_chapter_id: 'c5' },
        chapters,
      ),
    ).toBe(false);
  });
  it('expected_payoff 章节已过（number < max） → 逾期', () => {
    expect(
      isHookOverdue(
        { status: 'OPEN', expected_payoff_chapter_id: 'c5' },
        chapters,
      ),
    ).toBe(true);
  });
  it('expected_payoff 章节未到（number = max） → 不逾期', () => {
    expect(
      isHookOverdue(
        { status: 'OPEN', expected_payoff_chapter_id: 'c20' },
        chapters,
      ),
    ).toBe(false);
  });
  it('expected_payoff 章节不存在 → 不逾期（视为未约定）', () => {
    expect(
      isHookOverdue(
        { status: 'OPEN', expected_payoff_chapter_id: 'c_xxx' },
        chapters,
      ),
    ).toBe(false);
  });
  it('expected_payoff 为 null → 不逾期', () => {
    expect(
      isHookOverdue(
        { status: 'OPEN', expected_payoff_chapter_id: null },
        chapters,
      ),
    ).toBe(false);
  });
  it('chapters 为空 → 不逾期（无 chapter 可比对）', () => {
    expect(
      isHookOverdue(
        { status: 'OPEN', expected_payoff_chapter_id: 'c1' },
        [],
      ),
    ).toBe(false);
  });
});