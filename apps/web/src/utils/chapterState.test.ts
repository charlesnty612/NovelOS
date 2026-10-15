import { describe, expect, it } from 'vitest';
import {
  ALLOWED_NEXT,
  EXPECTED_STATUS,
  countHighRiskChanges,
  getButtonAvailability,
  getPipelineStepStates,
  isActiveRun,
  isRejectedForRevisionRun,
  isRejectedRun,
  isRunForChapter,
  pickActiveRun,
  pickLatestRun,
} from './chapterState';
import type { WorkflowRunStatus } from '../api/types';

describe('ALLOWED_NEXT (与后端 packages/domain/chapter/models.py 对齐)', () => {
  it('PLANNED 只能到 PLANNED / DRAFTED', () => {
    expect(ALLOWED_NEXT.PLANNED.slice().sort()).toEqual(['DRAFTED', 'PLANNED']);
  });
  it('DRAFTED 可到 PLANNED / DRAFTED / REVIEWED', () => {
    expect(ALLOWED_NEXT.DRAFTED.slice().sort()).toEqual([
      'DRAFTED',
      'PLANNED',
      'REVIEWED',
    ]);
  });
  it('REVIEWED 可到 PLANNED / REVIEWED / COMMITTED', () => {
    expect(ALLOWED_NEXT.REVIEWED.slice().sort()).toEqual([
      'COMMITTED',
      'PLANNED',
      'REVIEWED',
    ]);
  });
  it('COMMITTED 可到 PLANNED / COMMITTED / RELEASED', () => {
    expect(ALLOWED_NEXT.COMMITTED.slice().sort()).toEqual([
      'COMMITTED',
      'PLANNED',
      'RELEASED',
    ]);
  });
  it('RELEASED 可到 PLANNED / RELEASED', () => {
    expect(ALLOWED_NEXT.RELEASED.slice().sort()).toEqual(['PLANNED', 'RELEASED']);
  });
});

describe('EXPECTED_STATUS (按钮入口状态)', () => {
  it('plan 仅 PLANNED', () => {
    expect(EXPECTED_STATUS.plan).toEqual(['PLANNED']);
  });
  it('write 允许 PLANNED / DRAFTED', () => {
    expect(EXPECTED_STATUS.write.slice().sort()).toEqual(['DRAFTED', 'PLANNED']);
  });
  it('review 仅 DRAFTED', () => {
    expect(EXPECTED_STATUS.review).toEqual(['DRAFTED']);
  });
  it('commit 仅 REVIEWED', () => {
    expect(EXPECTED_STATUS.commit).toEqual(['REVIEWED']);
  });
});

describe('getButtonAvailability', () => {
  // 快通道（2026-10-14）：commit 在 DRAFTED 也可点，但**仅当 fastMode 为 true**。
  // 关着时必须与该开关引入前逐字相同——这条是回归看守。
  it('快通道关闭：DRAFTED 的 commit 仍禁用（与开关引入前一致）', () => {
    const r = getButtonAvailability('commit', {
      chapterStatus: 'DRAFTED',
      activeRun: null,
    });
    expect(r.enabled).toBe(false);
    expect(r.reason).toContain('仅在状态为 REVIEWED 时可用');
  });

  it('快通道关闭（显式 false）：DRAFTED 的 commit 同样禁用', () => {
    const r = getButtonAvailability('commit', {
      chapterStatus: 'DRAFTED',
      activeRun: null,
      fastMode: false,
    });
    expect(r.enabled).toBe(false);
  });

  it('快通道开启：DRAFTED 的 commit 启用', () => {
    expect(
      getButtonAvailability('commit', {
        chapterStatus: 'DRAFTED',
        activeRun: null,
        fastMode: true,
      }),
    ).toEqual({ enabled: true, reason: null });
  });

  it('快通道只放宽 commit：其余 action 的判定与关闭时逐字相同', () => {
    // 反例教材：曾把这条写成「review 在 DRAFTED 仍禁用」——错的，
    // 因为 EXPECTED_STATUS.review 本来就含 DRAFTED（写完草稿就该能审校）。
    // 真正的不变量是：**快通道开关对 plan/write/review 的判定零影响**。
    const statuses = ['PLANNED', 'DRAFTED', 'REVIEWED', 'COMMITTED'] as const;
    for (const s of statuses) {
      for (const action of ['plan', 'write', 'review'] as const) {
        const off = getButtonAvailability(action, {
          chapterStatus: s,
          activeRun: null,
        });
        const on = getButtonAvailability(action, {
          chapterStatus: s,
          activeRun: null,
          fastMode: true,
        });
        expect(on, `action=${action} status=${s}`).toEqual(off);
      }
    }
  });

  it('快通道不越过活跃 run 守卫：RUNNING 时一律禁用', () => {
    const r = getButtonAvailability('commit', {
      chapterStatus: 'DRAFTED',
      activeRun: { status: 'RUNNING' },
      fastMode: true,
    });
    expect(r.enabled).toBe(false);
    expect(r.reason).toContain('进行中的工作流 run');
  });

  // 审查批次 2026-10-15：此前旁路的**状态边界完全没有用例钉住**——把实现里的
  // `chapterStatus === 'DRAFTED'` 改成 `chapterStatus !== 'REVIEWED'`，
  // COMMITTED / PLANNED / RELEASED 会被一并放行，而全量 56 条前端用例照样全绿
  // （实测突变存活）。旁路最小性此前只对 *action* 有约束，对 *status* 没有。
  it('快通道旁路的状态边界：只有 DRAFTED 被放行，其余状态判定逐字不变', () => {
    // 不变量是「快通道对**非 DRAFTED** 状态零影响」，不是「其余状态都禁用」——
    // REVIEWED 本来就是 commit 的合法状态（EXPECTED_STATUS.commit = ['REVIEWED']），
    // 它 enabled=true 与快通道无关。
    // 判别：把实现里的 `=== 'DRAFTED'` 改成 `!== 'REVIEWED'`，COMMITTED /
    // PLANNED / RELEASED 会被一并放行 ⇒ 与关闭时的结果不等 ⇒ 本用例红。
    const statuses = ['PLANNED', 'DRAFTED', 'REVIEWED', 'COMMITTED', 'RELEASED'] as const;
    for (const s of statuses) {
      const on = getButtonAvailability('commit', {
        chapterStatus: s,
        activeRun: null,
        fastMode: true,
      });
      const off = getButtonAvailability('commit', {
        chapterStatus: s,
        activeRun: null,
      });
      if (s === 'DRAFTED') {
        expect(on.enabled, 'DRAFTED 应被快通道放行').toBe(true);
        expect(off.enabled, 'DRAFTED 关闭时不该放行').toBe(false);
      } else {
        expect(on, `状态 ${s}：快通道不该改变判定`).toEqual(off);
      }
    }
  });

  it('快通道旁路只在 DRAFTED 生效：REVIEWED 的行为与关闭时逐字相同', () => {
    // 与上一条互为对照：REVIEWED 是唯一「本来就该放行」的状态，快通道对它
    // 必须是无操作的——否则「放行 DRAFTED」就退化成了「放行一切非 REVIEWED」。
    const on = getButtonAvailability('commit', {
      chapterStatus: 'REVIEWED',
      activeRun: null,
      fastMode: true,
    });
    expect(on).toEqual(
      getButtonAvailability('commit', {
        chapterStatus: 'REVIEWED',
        activeRun: null,
      }),
    );
  });

  it('没有活跃 run、章节状态匹配 → 启用', () => {
    expect(
      getButtonAvailability('plan', {
        chapterStatus: 'PLANNED',
        activeRun: null,
      }),
    ).toEqual({ enabled: true, reason: null });
  });

  it('章节状态不匹配 → 禁用并给出原因', () => {
    const r = getButtonAvailability('plan', {
      chapterStatus: 'REVIEWED',
      activeRun: null,
    });
    expect(r.enabled).toBe(false);
    expect(r.reason).toContain('仅在状态为 PLANNED 时可用');
    expect(r.reason).toContain('当前状态 REVIEWED');
  });

  it('write 在 PLANNED 与 DRAFTED 均可用', () => {
    expect(
      getButtonAvailability('write', {
        chapterStatus: 'PLANNED',
        activeRun: null,
      }).enabled,
    ).toBe(true);
    expect(
      getButtonAvailability('write', {
        chapterStatus: 'DRAFTED',
        activeRun: null,
      }).enabled,
    ).toBe(true);
  });

  it('write 在 REVIEWED 不可用（pipeline 校验会拒）', () => {
    const r = getButtonAvailability('write', {
      chapterStatus: 'REVIEWED',
      activeRun: null,
    });
    expect(r.enabled).toBe(false);
    expect(r.reason).toContain('REVIEWED');
  });

  it('review 仅 DRAFTED 可用', () => {
    expect(
      getButtonAvailability('review', { chapterStatus: 'DRAFTED', activeRun: null })
        .enabled,
    ).toBe(true);
    expect(
      getButtonAvailability('review', {
        chapterStatus: 'PLANNED',
        activeRun: null,
      }).enabled,
    ).toBe(false);
  });

  it('commit 仅 REVIEWED 可用', () => {
    expect(
      getButtonAvailability('commit', {
        chapterStatus: 'REVIEWED',
        activeRun: null,
      }).enabled,
    ).toBe(true);
    expect(
      getButtonAvailability('commit', {
        chapterStatus: 'DRAFTED',
        activeRun: null,
      }).enabled,
    ).toBe(false);
  });

  it('RUNNING 活跃 run → 所有按钮禁用（即使状态匹配）', () => {
    const r = getButtonAvailability('plan', {
      chapterStatus: 'PLANNED',
      activeRun: { status: 'RUNNING' },
    });
    expect(r.enabled).toBe(false);
    expect(r.reason).toContain('进行中');
  });

  it('PENDING 活跃 run → 同样禁用（run 即将进入 RUNNING）', () => {
    const r = getButtonAvailability('write', {
      chapterStatus: 'DRAFTED',
      activeRun: { status: 'PENDING' },
    });
    expect(r.enabled).toBe(false);
  });

  it('PAUSED 活跃 run 不阻塞按钮（resume 走审批卡片）', () => {
    expect(
      getButtonAvailability('plan', {
        chapterStatus: 'PLANNED',
        activeRun: { status: 'PAUSED' },
      }).enabled,
    ).toBe(true);
  });

  it('COMPLETED 终态不阻塞', () => {
    expect(
      getButtonAvailability('review', {
        chapterStatus: 'DRAFTED',
        activeRun: { status: 'COMPLETED' },
      }).enabled,
    ).toBe(true);
  });

  it('FAILED 终态不阻塞', () => {
    expect(
      getButtonAvailability('commit', {
        chapterStatus: 'REVIEWED',
        activeRun: { status: 'FAILED' },
      }).enabled,
    ).toBe(true);
  });
});

describe('isRunForChapter', () => {
  it('chapter_id 匹配 → true', () => {
    expect(isRunForChapter({ chapter_id: 'ch_a' }, 'ch_a')).toBe(true);
  });
  it('chapter_id 不匹配 → false', () => {
    expect(isRunForChapter({ chapter_id: 'ch_b' }, 'ch_a')).toBe(false);
  });
  it('chapter_id 为 null 时回退 checkpoint_json', () => {
    expect(
      isRunForChapter(
        { chapter_id: null, checkpoint_json: { chapter_id: 'ch_a' } },
        'ch_a',
      ),
    ).toBe(true);
  });
  it('无任何 chapter 标识 → false', () => {
    expect(isRunForChapter({}, 'ch_a')).toBe(false);
  });
});

describe('isActiveRun', () => {
  it.each<WorkflowRunStatus>(['RUNNING', 'PENDING'])('%s 视为活跃', (s) => {
    expect(isActiveRun({ status: s })).toBe(true);
  });
  it.each<WorkflowRunStatus>(['PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED'])(
    '%s 不视为活跃',
    (s) => {
      expect(isActiveRun({ status: s })).toBe(false);
    },
  );
  it('null / undefined 不视为活跃', () => {
    expect(isActiveRun(null)).toBe(false);
    expect(isActiveRun(undefined)).toBe(false);
  });
});

describe('pickActiveRun / pickLatestRun', () => {
  const runs = [
    {
      run_id: 'r1',
      chapter_id: 'ch_a',
      checkpoint_json: {},
      status: 'COMPLETED' as WorkflowRunStatus,
      started_at: '2026-08-23T10:00:00',
    },
    {
      run_id: 'r2',
      chapter_id: 'ch_b',
      checkpoint_json: {},
      status: 'RUNNING' as WorkflowRunStatus,
      started_at: '2026-08-23T10:05:00',
    },
    {
      run_id: 'r3',
      chapter_id: 'ch_a',
      checkpoint_json: {},
      status: 'FAILED' as WorkflowRunStatus,
      started_at: '2026-08-23T10:10:00',
    },
    {
      run_id: 'r4',
      chapter_id: 'ch_a',
      checkpoint_json: {},
      status: 'PAUSED' as WorkflowRunStatus,
      started_at: '2026-08-23T10:15:00',
    },
  ];

  it('pickActiveRun 选出本章 RUNNING 的 run（若没有则返回 null）', () => {
    expect(pickActiveRun(runs, 'ch_b')?.run_id).toBe('r2');
    expect(pickActiveRun(runs, 'ch_a')).toBeNull();
  });

  it('pickLatestRun 选出本章最新一条 run（不限制状态）', () => {
    expect(pickLatestRun(runs, 'ch_a')?.run_id).toBe('r4');
    expect(pickLatestRun(runs, 'ch_b')?.run_id).toBe('r2');
    expect(pickLatestRun(runs, 'ch_unknown')).toBeNull();
  });
});

describe('countHighRiskChanges', () => {
  it('非对象返回 0', () => {
    expect(countHighRiskChanges(null)).toBe(0);
    expect(countHighRiskChanges(undefined)).toBe(0);
    expect(countHighRiskChanges(42)).toBe(0);
  });

  it('无 changes 字段 → 0', () => {
    expect(countHighRiskChanges({ stage: 'x' })).toBe(0);
  });

  it('只数 character_changes + world_changes 数组长度', () => {
    expect(
      countHighRiskChanges({
        stage: 'chapter-commit.high_risk_approval',
        changes: {
          character_changes: [{ risk_level: 'HIGH' }, { risk_level: 'LOW' }],
          world_changes: [{ world_kind: 'rule' }],
        },
      }),
    ).toBe(3);
  });

  it('changes 字段非对象 → 0', () => {
    expect(countHighRiskChanges({ changes: 'oops' })).toBe(0);
    expect(countHighRiskChanges({ changes: ['x'] })).toBe(0);
  });
});

describe('getPipelineStepStates', () => {
  it('PLANNED 且无 plan → plan=current，其余 todo', () => {
    expect(
      getPipelineStepStates({ chapterStatus: 'PLANNED', hasPlan: false }),
    ).toEqual({
      plan: 'current',
      write: 'todo',
      review: 'todo',
      commit: 'todo',
    });
  });

  it('PLANNED 且有 plan（hasPlan=true）→ plan=done，write=current', () => {
    expect(
      getPipelineStepStates({ chapterStatus: 'PLANNED', hasPlan: true }),
    ).toEqual({
      plan: 'done',
      write: 'current',
      review: 'todo',
      commit: 'todo',
    });
  });

  it('DRAFTED（有 plan）→ plan/write=done，review=current', () => {
    expect(
      getPipelineStepStates({ chapterStatus: 'DRAFTED', hasPlan: true }),
    ).toEqual({
      plan: 'done',
      write: 'done',
      review: 'current',
      commit: 'todo',
    });
  });

  it('DRAFTED 但无 plan → plan 也按 hasPlan=true 抬升（rank>=1）', () => {
    expect(
      getPipelineStepStates({ chapterStatus: 'DRAFTED', hasPlan: false }),
    ).toEqual({
      plan: 'done',
      write: 'done',
      review: 'current',
      commit: 'todo',
    });
  });

  it('REVIEWED → 仅 commit=current，其余 done', () => {
    expect(
      getPipelineStepStates({ chapterStatus: 'REVIEWED', hasPlan: true }),
    ).toEqual({
      plan: 'done',
      write: 'done',
      review: 'done',
      commit: 'current',
    });
  });

  it('COMMITTED → 全 done（无 current）', () => {
    expect(
      getPipelineStepStates({ chapterStatus: 'COMMITTED', hasPlan: true }),
    ).toEqual({
      plan: 'done',
      write: 'done',
      review: 'done',
      commit: 'done',
    });
  });

  it('RELEASED → 全 done', () => {
    expect(
      getPipelineStepStates({ chapterStatus: 'RELEASED', hasPlan: true }),
    ).toEqual({
      plan: 'done',
      write: 'done',
      review: 'done',
      commit: 'done',
    });
  });
});

describe('isRejectedRun / isRejectedForRevisionRun (后端两态严格匹配)', () => {
  it("'rejected' → isRejectedRun true / isRejectedForRevisionRun false", () => {
    expect(isRejectedRun('rejected')).toBe(true);
    expect(isRejectedForRevisionRun('rejected')).toBe(false);
  });

  it("'rejected-for-revision' → 反之 isRejectedRun false / isRejectedForRevisionRun true", () => {
    expect(isRejectedRun('rejected-for-revision')).toBe(false);
    expect(isRejectedForRevisionRun('rejected-for-revision')).toBe(true);
  });

  it("commit 校验失败 'observer delta rejected by validator: errors=[...]' → 两 helper 皆 false（防误判）", () => {
    const err = 'observer delta rejected by validator: errors=[orphan ref to c1]';
    expect(isRejectedRun(err)).toBe(false);
    expect(isRejectedForRevisionRun(err)).toBe(false);
  });

  it("'observer delta failed validation: errors=[...]' → 两 helper 皆 false", () => {
    const err = 'observer delta failed validation: errors=[something]';
    expect(isRejectedRun(err)).toBe(false);
    expect(isRejectedForRevisionRun(err)).toBe(false);
  });

  it('null / undefined / 空串 → 两 helper 皆 false', () => {
    expect(isRejectedRun(null)).toBe(false);
    expect(isRejectedRun(undefined)).toBe(false);
    expect(isRejectedRun('')).toBe(false);
    expect(isRejectedForRevisionRun(null)).toBe(false);
    expect(isRejectedForRevisionRun(undefined)).toBe(false);
    expect(isRejectedForRevisionRun('')).toBe(false);
  });

  it('前后带空白的 " rejected " 仍视为纯驳回（trim 后相等）', () => {
    expect(isRejectedRun('  rejected ')).toBe(true);
    expect(isRejectedForRevisionRun('  rejected ')).toBe(false);
    expect(isRejectedForRevisionRun('  rejected-for-revision ')).toBe(true);
    expect(isRejectedRun('  rejected-for-revision ')).toBe(false);
  });

  it('顺序陷阱：rejected-for-revision 必须先判（裸 rejected 不会误吞）', () => {
    // 模拟调用方"先 isRejectedForRevisionRun 再 isRejectedRun"的正确顺序
    const err = 'rejected-for-revision';
    const isRevFirst = isRejectedForRevisionRun(err) || isRejectedRun(err);
    expect(isRevFirst).toBe(true); // 第一支命中，正确分支为「驳回并改稿」

    // 反向顺序会得到错误结论（'rejected' 也是 false 的原因）—— 验证裸 rejected 严格匹配
    expect(isRejectedRun('rejected')).toBe(true);
    expect(isRejectedRun('rejected-for-revision')).toBe(false);
  });
});
