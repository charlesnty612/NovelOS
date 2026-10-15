// 跨端看守 ②：Hook 五态机 / Debt 四态机的**合法后继表**（2026-10-15 批次（七））。
//
// 缺口来源
// --------
// `NovelOS-Artifacts: docs/analysis/ui-business-logic-assessment-2026-10-15.md` §三 第 2 行
// 标为**中风险**：`utils/ledgerState.ts` 的 `HOOK_ALLOWED_NEXT` / `DEBT_ALLOWED_NEXT`
// 是**手抄**自后端 `packages/domain/ledger/models.py` 的。后端加第六个状态、或改一条
// 合法迁移，前端会**静默失效**——作者在 UI 上看到的「下一步能选什么」是错的。
//
// 前端测试文件里原来写着一句「(与后端 packages/domain/ledger/models.py 对齐)」，
// 但那是**声明**，不是断言：测试里没有读那个文件。
//
// ⚠️ 基准来自后端源文件（解析出邻接表），不是把后端的表再抄一遍到测试里——
// 后者只是把同一个字面量抄到第三处，后端改了照样绿。
//
// 残余边界：本守卫**只看邻接表本身**。前端另外还复刻了后端「RESOLVED 且
// payoff_chapter_id 为 NULL 不阻断」那条宽松规则（见 `isHookBlockingDelivery`），
// 那条不在本文件覆盖范围内。

import { describe, expect, it } from 'vitest';

import { DEBT_ALLOWED_NEXT, HOOK_ALLOWED_NEXT } from '../ledgerState';
import { BACKEND, parseAllowedNextMap, readBackendSource } from './backendSource';

const ledgerSrc = readBackendSource(BACKEND.ledgerModels);
const backendHook = parseAllowedNextMap(ledgerSrc, 'HOOK_ALLOWED_NEXT');
const backendDebt = parseAllowedNextMap(ledgerSrc, 'DEBT_ALLOWED_NEXT');

/** 前端表 → `Record<string, string[]>`（排序后），便于与后端逐键比对。 */
function normalize(front: Record<string, readonly string[]>): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [k, v] of Object.entries(front)) out[k] = [...new Set(v)].sort();
  return out;
}

describe('跨端：Hook 状态机（后端 ledger/models.py ↔ 前端 ledgerState.ts）', () => {
  it('状态集合逐字相同（后端加/删状态 ⇒ 必红）', () => {
    expect(
      Object.keys(normalize(HOOK_ALLOWED_NEXT)).sort(),
      '后端 HOOK_ALLOWED_NEXT 的状态集与前端不一致 ⇒ UI 的「下一步可选」会漏掉或多出状态',
    ).toEqual(Object.keys(backendHook).sort());
  });

  it('每条合法迁移逐字相同', () => {
    const front = normalize(HOOK_ALLOWED_NEXT);
    for (const [state, nexts] of Object.entries(backendHook)) {
      expect(
        front[state],
        `状态 ${state} 的合法后继：后端 ${JSON.stringify(nexts)} vs 前端 ${JSON.stringify(front[state])}`,
      ).toEqual(nexts);
    }
  });

  it('自环（停留在原状态）在后端成立的，前端也必须允许', () => {
    // 这条单独列出的理由：自环最容易在「抄表」时漏，而后端每个状态都有自环
    const front = normalize(HOOK_ALLOWED_NEXT);
    for (const [state, nexts] of Object.entries(backendHook)) {
      if (nexts.includes(state)) {
        expect(front[state], `${state} 在后端允许自环，前端漏了`).toContain(state);
      }
    }
  });
});

describe('跨端：Debt 状态机（后端 ledger/models.py ↔ 前端 ledgerState.ts）', () => {
  it('状态集合逐字相同', () => {
    expect(Object.keys(normalize(DEBT_ALLOWED_NEXT)).sort()).toEqual(
      Object.keys(backendDebt).sort(),
    );
  });

  it('每条合法迁移逐字相同', () => {
    const front = normalize(DEBT_ALLOWED_NEXT);
    for (const [state, nexts] of Object.entries(backendDebt)) {
      expect(
        front[state],
        `状态 ${state} 的合法后继：后端 ${JSON.stringify(nexts)} vs 前端 ${JSON.stringify(front[state])}`,
      ).toEqual(nexts);
    }
  });

  it('终态（无出边除自身）在后端封死的，前端也不得开出别的边', () => {
    const front = normalize(DEBT_ALLOWED_NEXT);
    for (const [state, nexts] of Object.entries(backendDebt)) {
      const terminal = nexts.every((n) => n === state);
      if (terminal) {
        expect(front[state], `${state} 在后端是终态，前端却放行了其它后继`).toEqual(nexts);
      }
    }
  });
});

describe('跨端：解析器自身的健全性（防「两边都空 ⇒ 恒真」）', () => {
  it('解析出来的表非空，且键数合理', () => {
    // 若正则失效，parseAllowedNextMap 会抛错；这里再钉一次「确实有内容」，
    // 因为「两张空表相等」是这类守卫最容易落进去的恒真陷阱。
    expect(Object.keys(backendHook).length).toBeGreaterThanOrEqual(3);
    expect(Object.keys(backendDebt).length).toBeGreaterThanOrEqual(3);
  });
});
