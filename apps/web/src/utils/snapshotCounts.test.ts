import { describe, expect, it } from 'vitest';
import { countOpenSnapshotItems, countSnapshot } from './snapshotCounts';
import type { SnapshotResponse } from '../api/types';

/**
 * 快照计数口径的判别用例。
 *
 * **本仓坑区「量具默认是什么」的直接应用**：下面每个用例的夹具都照抄
 * 后端 `packages/core/story_state/models.py::SnapshotResponse` 的形状，
 * 而不是照抄前端旧归一化层的输出。最关键的一条是 `events` 是
 * **dict（按 event_id 键）**——写成数组就测不出任何东西。
 */

const real: SnapshotResponse = {
  state_version: 7,
  characters: [{ character_id: 'chr_1' }, { character_id: 'chr_2' }],
  world: { locations: {}, factions: {}, world_rules: [] },
  hooks: [{ hook_id: 'hk_1', status: 'ACTIVE' }],
  debts: [{ debt_id: 'db_1', status: 'open' }],
  events: { evt_1: {}, evt_2: {}, evt_3: {} },
  recent_events: [{ event_id: 'evt_3' }],
};

describe('countSnapshot（口径单点）', () => {
  it('按后端形状读出全部五个计数', () => {
    expect(countSnapshot(real)).toEqual({
      characters: 2,
      hooks: 1,
      debts: 1,
      events: 3, // ← dict 的键数；这是本模块存在的全部理由
      recentEvents: 1,
    });
  });

  // 这条就是「事件恒 0」那个 bug 的判别：events 若是 dict，长度得用键数。
  it('events 是 dict 时按键数算（用 listLen 会静默归零）', () => {
    const snap = { ...real, events: { a: {}, b: {} } };
    expect(countSnapshot(snap).events).toBe(2);
  });

  it('characters 是 list 时按长度算（用 dictLen 会静默归零）', () => {
    expect(countSnapshot({ ...real, characters: [{ a: 1 }, { b: 2 }, { c: 3 }] }).characters).toBe(3);
  });

  it('空快照与缺字段一律归零，不抛错', () => {
    expect(countSnapshot({ state_version: 0 })).toEqual({
      characters: 0,
      hooks: 0,
      debts: 0,
      events: 0,
      recentEvents: 0,
    });
  });

  it('null / undefined 快照返回全零', () => {
    expect(countSnapshot(null).characters).toBe(0);
    expect(countSnapshot(undefined).events).toBe(0);
  });

  /**
   * 形状守卫：本仓坑区「同一模块内口径必须唯一，分叉即假象」的形状级断言。
   * 万一有人把 countSnapshot 改回「先 normObj 再 normArr」那套，本用例必须红。
   */
  it('**不做**任何形状归一化：list 进 list 出、dict 进 dict 出', () => {
    const chars = [{ x: 1 }];
    const evts = { k: {} };
    const snap: SnapshotResponse = { state_version: 1, characters: chars, events: evts };
    expect(countSnapshot(snap).characters).toBe(chars.length);
    expect(countSnapshot(snap).events).toBe(Object.keys(evts).length);
  });
});

describe('countOpenSnapshotItems（待办口径）', () => {
  it('伏笔：RESOLVED / ABANDONED 不计未回收', () => {
    const snap: SnapshotResponse = {
      state_version: 1,
      hooks: [
        { hook_id: 'a', status: 'OPEN' },
        { hook_id: 'b', status: 'ACTIVE' },
        { hook_id: 'c', status: 'RESOLVED' },
        { hook_id: 'd', status: 'ABANDONED' },
      ],
    };
    expect(countOpenSnapshotItems(snap).openHooks).toBe(2);
  });

  it('债：paid / forgiven 不计未偿清', () => {
    const snap: SnapshotResponse = {
      state_version: 1,
      debts: [
        { debt_id: 'a', status: 'open' },
        { debt_id: 'b', status: 'acknowledged' },
        { debt_id: 'c', status: 'paid' },
        { debt_id: 'd', status: 'forgiven' },
      ],
    };
    expect(countOpenSnapshotItems(snap).openDebts).toBe(2);
  });

  it('status 缺失按未回收计（fail-open：宁可多提醒一次）', () => {
    const snap: SnapshotResponse = {
      state_version: 1,
      hooks: [{ hook_id: 'a' }],
      debts: [{ debt_id: 'b' }],
    };
    expect(countOpenSnapshotItems(snap)).toEqual({ openHooks: 1, openDebts: 1 });
  });

  it('null 快照返回全零', () => {
    expect(countOpenSnapshotItems(null)).toEqual({ openHooks: 0, openDebts: 0 });
  });
});
