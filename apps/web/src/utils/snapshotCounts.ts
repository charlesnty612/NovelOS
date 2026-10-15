// Canonical State 快照的**计数口径单点**。
//
// 2026-10-15 建立。此前同一口径在两处各写一份，且**两处都把 events 数成 0**：
//   - `pages/ProjectOverviewPage.tsx` —— `countArr(snap.events)`，而 `events`
//     后端是 **dict**（按 event_id 键）⇒ 「事件·累计」恒 0。
//   - `components/BranchesPanel.tsx` —— 同款 `countArr` ⇒ What-if 分支对比面板的
//     「Events / 差」两列永远显示 0，**分支之间的事件差异根本显示不出来**。
// 机制上还有一层：`endpoints.ts` 的手写归一化把 `events` 用 `normArr` 拍成 `[]`、
// 把 `characters`（list）用 `normObj` 拍成 `{}`，两处缺陷都由此放大。归一化已删
// （后端 `snapshots.latest_snapshot_version` 本就 `_parse_required_json` 解析过）。
//
// **本模块的立身之据是后端契约，不是猜测**：
// `packages/core/story_state/models.py::SnapshotResponse`（该端点已声明
// `response_model`）写死了 characters/hooks/debts = list、world/events = dict。
// 任何时候后端改了形状，这里应当**跟着响**，而不是静默数出另一个数。

import type { SnapshotResponse } from '../api/types';

export interface SnapshotCounts {
  characters: number;
  hooks: number;
  debts: number;
  /** 剧情事件总数（dict 的键数） */
  events: number;
  /** 最近事件数（list 的长度） */
  recentEvents: number;
}

/** 列表长度；非数组一律 0。 */
function listLen(v: unknown): number {
  return Array.isArray(v) ? v.length : 0;
}

/** 字典键数；非对象一律 0。 */
function dictLen(v: unknown): number {
  if (v && typeof v === 'object' && !Array.isArray(v)) return Object.keys(v).length;
  return 0;
}

/**
 * 对一份快照取五个计数。
 *
 * **字段与形状的对应是契约，不是约定**——尤其 `events` 是 dict 而非 list
 * （`story_state/snapshot.py::_load_plot_events_dict`）。写成 `listLen(events)`
 * 会静默归零，不报错。
 */
export function countSnapshot(snap: SnapshotResponse | null | undefined): SnapshotCounts {
  if (!snap) {
    return { characters: 0, hooks: 0, debts: 0, events: 0, recentEvents: 0 };
  }
  return {
    characters: listLen(snap.characters),
    hooks: listLen(snap.hooks),
    debts: listLen(snap.debts),
    events: dictLen(snap.events),
    recentEvents: listLen(snap.recent_events),
  };
}

/**
 * 快照里「还没了结」的伏笔 / 债条数（用于概览页的待办口径）。
 *
 * 与快照的 status 字段无关——快照里的 hooks/debts 是**全量**（`build_initial_state`
 * 注释：「= 全部 hooks 行」），「未完结」需要按 status 过滤。status 缺失时
 * **按未完结计**（fail-open：宁可多提醒一次，也不让待办消失）。
 */
const SETTLED_HOOK_STATUS = new Set(['RESOLVED', 'ABANDONED']);
const SETTLED_DEBT_STATUS = new Set(['paid', 'forgiven']);

function statusOf(row: unknown): string | null {
  if (!row || typeof row !== 'object' || Array.isArray(row)) return null;
  const s = (row as Record<string, unknown>)['status'];
  return typeof s === 'string' ? s : null;
}

export function countOpenSnapshotItems(snap: SnapshotResponse | null | undefined): {
  openHooks: number;
  openDebts: number;
} {
  if (!snap) return { openHooks: 0, openDebts: 0 };
  const hooks = Array.isArray(snap.hooks) ? snap.hooks : [];
  const debts = Array.isArray(snap.debts) ? snap.debts : [];
  return {
    openHooks: hooks.filter((h) => {
      const s = statusOf(h);
      return s === null || !SETTLED_HOOK_STATUS.has(s);
    }).length,
    openDebts: debts.filter((d) => {
      const s = statusOf(d);
      return s === null || !SETTLED_DEBT_STATUS.has(s);
    }).length,
  };
}
