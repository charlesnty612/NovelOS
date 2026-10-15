// 弧光面板测试（评估 G6：`GET /projects/{pid}/arc` 一直在算，UI 零入口）。
//
// 判别重点与其它只读面板一致，但多了两条**反向用例**——它们守的是「诚实面」：
//
// 1. **没有 chapter_id ⇒ 不做跳转。** 后端 `build_arc_view` 显式 pop 掉该字段。
//    判别不只看「当前没渲染链接」（那可能只是碰巧），而是**故意喂一份带
//    chapter_id 的脏数据**，断言它既不产生链接、也不流进 DOM：组件对它的依赖
//    必须为零，否则后端哪天改回暴露它，UI 会静默长出指向不存在的 id 的死链。
// 2. **「没有告警」不得读成「节奏健康」。** 空告警时页面必须写出它的真实含义
//    （4 条规则 + 字符串标记推断 ⇒ 覆盖不了没打标记的章）。
//    缺了这句话，空态就是一张假体检单——正是本仓「算出来的问题没人能说不」的
//    同款形状在 UI 层的变体。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { arcApi } from '../../api/endpoints';
import type { ArcView } from '../../api/types';
import { ArcTab } from './ArcTab';

vi.mock('../../api/endpoints', () => ({
  arcApi: { get: vi.fn() },
}));

const aGet = arcApi.get as unknown as ReturnType<typeof vi.fn>;

const view = (over: Partial<ArcView> = {}): ArcView => ({
  project_id: 'p1',
  generated_at: '2026-10-15T08:00:00Z',
  chapters: [
    {
      number: 1,
      title: '开场',
      has_payoff_beat: true,
      charge_beat: false,
      overall: 88,
      pacing: 72,
      prose_chars: 2400,
      volume_id: 'v1',
      volume_number: 1,
    },
    {
      number: 2,
      title: '蓄力',
      has_payoff_beat: false,
      charge_beat: true,
      overall: 75,
      pacing: 40,
      prose_chars: 1800,
      volume_id: null,
      volume_number: null,
    },
  ],
  payoff: { total: 1, charge_streak: 1, max_charge_streak: 3, last_payoff_chapter: 1 },
  hooks: { open: 4, resolved: 2, overdue: 1 },
  debts: { open: 3, paid: 1 },
  reveal_policies: { planned: 2, revealed: 1, cancelled: 0, overdue: [] },
  volumes: [
    { volume_id: 'v1', number: 1, title: '第一卷', status: 'active', chapter_count: 1 },
    { volume_id: 'v2', number: 2, title: null, status: 'sealed', chapter_count: 0 },
  ],
  alerts: [
    { level: 'warn', code: 'low_pacing_chapter', message: '章节 pacing 低于 50：第 2 章' },
    { level: 'warn', code: 'foreshadow_overdue', message: '有 1 条伏笔已逾期未兑现' },
  ],
  ...over,
});

describe('ArcTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    aGet.mockResolvedValue(view());
  });

  it('加载态：请求未回来时显示 loading，不闪空屏', () => {
    aGet.mockReturnValue(new Promise(() => {}));
    render(<ArcTab projectId="p1" />);
    expect(screen.getByText('正在汇总弧光…')).toBeInTheDocument();
  });

  it('完整数据：章节行数与告警条数都按后端返回渲染', async () => {
    aGet.mockResolvedValue(
      view({
        alerts: [
          { level: 'warn', code: 'low_payoff_density', message: '最近 5 章 payoff 占比 20%' },
          { level: 'warn', code: 'low_pacing_chapter', message: 'pacing 低' },
          { level: 'warn', code: 'foreshadow_overdue', message: '逾期' },
        ],
      }),
    );
    render(<ArcTab projectId="p1" />);
    const table = await screen.findByTestId('arc-chapter-table');
    expect(table.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(screen.getByTestId('arc-alert-list').children).toHaveLength(3);
  });

  it('汇总数字：payoff 四项 + 伏笔/债务计数按后端口径显示', async () => {
    render(<ArcTab projectId="p1" />);
    expect(await screen.findByTestId('arc-stat-payoff')).toHaveTextContent('1');
    expect(screen.getByTestId('arc-stat-charge-streak')).toHaveTextContent('1');
    expect(screen.getByTestId('arc-stat-max-charge-streak')).toHaveTextContent('3');
    expect(screen.getByTestId('arc-stat-last-payoff')).toHaveTextContent('1');
    expect(screen.getByTestId('arc-stat-hooks')).toHaveTextContent('未闭环 4 · 已闭环 2');
    expect(screen.getByTestId('arc-stat-hooks')).toHaveTextContent('逾期 1');
    expect(screen.getByTestId('arc-stat-debts')).toHaveTextContent('未还 3 · 已还 1');
  });

  it('最近 payoff 为 null ⇒ 显示「尚无」，不把 null 渲染成 0 章', async () => {
    aGet.mockResolvedValue(
      view({ payoff: { total: 0, charge_streak: 0, max_charge_streak: 0, last_payoff_chapter: null } }),
    );
    render(<ArcTab projectId="p1" />);
    const stat = await screen.findByTestId('arc-stat-last-payoff');
    expect(stat).toHaveTextContent('尚无');
    expect(stat).not.toHaveTextContent('0 章');
  });

  // 空态三条：后端这三节都可能是空数组，白屏比说实话更坏。
  it('alerts 空 ⇒ 空态，并说清「空 ≠ 节奏健康」', async () => {
    aGet.mockResolvedValue(view({ alerts: [] }));
    render(<ArcTab projectId="p1" />);
    expect(await screen.findByText('没有触发告警')).toBeInTheDocument();
    expect(screen.queryByTestId('arc-alert-list')).not.toBeInTheDocument();
    // 判别点：空告警必须带着它的失效边界一起显示，否则会被读成体检通过。
    expect(screen.getByText(/不等于节奏健康/)).toBeInTheDocument();
  });

  it('chapters 空 ⇒ 空态，不渲染空表', async () => {
    aGet.mockResolvedValue(view({ chapters: [] }));
    render(<ArcTab projectId="p1" />);
    expect(await screen.findByText('还没有章节')).toBeInTheDocument();
    expect(screen.queryByTestId('arc-chapter-table')).not.toBeInTheDocument();
    // 告警节不受影响：空的是章节，不是整屏。
    expect(screen.getByTestId('arc-alert-list')).toBeInTheDocument();
  });

  it('volumes 空 ⇒ 空态，并说明未挂卷不影响其它列', async () => {
    aGet.mockResolvedValue(view({ volumes: [] }));
    render(<ArcTab projectId="p1" />);
    expect(await screen.findByText('还没有卷')).toBeInTheDocument();
    expect(screen.queryByTestId('arc-volume-table')).not.toBeInTheDocument();
  });

  it('warn 与 fail 视觉可分：标签不同、class 不同，且 fail 排在前面', async () => {
    aGet.mockResolvedValue(
      view({
        alerts: [
          { level: 'warn', code: 'low_pacing_chapter', message: 'pacing 低' },
          { level: 'fail', code: 'charge_streak_exceeded', message: '蓄力超过 3 章未兑现' },
        ],
      }),
    );
    render(<ArcTab projectId="p1" />);
    const fail = await screen.findByTestId('arc-alert-charge_streak_exceeded');
    const warn = screen.getByTestId('arc-alert-low_pacing_chapter');
    expect(fail).toHaveTextContent('硬信号');
    expect(warn).toHaveTextContent('提示');
    expect(fail.querySelector('.badge')?.className).not.toBe(warn.querySelector('.badge')?.className);
    // 硬信号不能沉在体检项下面。
    const list = screen.getByTestId('arc-alert-list');
    expect(list.children[0]).toBe(fail);
  });

  it('两条诚实说明都在页面上（不是「弧光不是实体」+ 悄悄省略推断来源）', async () => {
    render(<ArcTab projectId="p1" />);
    const entity = await screen.findByTestId('arc-info-entity');
    expect(entity).toHaveTextContent('不是一张表');
    expect(entity).toHaveTextContent('arcs');
    expect(entity).toHaveTextContent('现场汇总');
    const beat = screen.getByTestId('arc-info-beat');
    expect(beat).toHaveTextContent('[charge]');
    expect(beat).toHaveTextContent('[payoff]');
    expect(beat).toHaveTextContent('字符串标记');
  });

  // 反向用例：故意喂带 chapter_id 的脏数据。
  it('章节行不依赖 chapter_id：脏数据里的 id 既不产生跳转也不进 DOM', async () => {
    const dirty = {
      ...view(),
      chapters: [
        {
          number: 1,
          title: '开场',
          has_payoff_beat: true,
          charge_beat: false,
          overall: 88,
          pacing: 72,
          prose_chars: 2400,
          volume_id: null,
          volume_number: null,
          chapter_id: 'ch_leaked_id_should_never_reach_dom',
        },
      ],
    } as unknown as ArcView;
    aGet.mockResolvedValue(dirty);
    const { container } = render(<ArcTab projectId="p1" />);
    const table = await screen.findByTestId('arc-chapter-table');
    // 没有 id 就没有跳转目标：表里不允许出现任何链接或按钮。
    expect(table.querySelectorAll('a, button')).toHaveLength(0);
    // 行仍然按章号渲染（键不依赖 id）。
    expect(screen.getByTestId('arc-chapter-1')).toHaveTextContent('开场');
    // 最强判别：这个 id 一个字符都不许漏进 DOM。
    expect(container.innerHTML).not.toContain('ch_leaked_id_should_never_reach_dom');
  });

  it('章节页明说「没有 chapter_id 所以不跳转」，不是默默不跳', async () => {
    render(<ArcTab projectId="p1" />);
    const note = await screen.findByTestId('arc-no-chapter-id');
    expect(note).toHaveTextContent('没有 chapter_id');
    expect(note).toHaveTextContent('不做');
  });

  it('空值单元格显示占位符，不留空白（空白会被读成 0 分）', async () => {
    aGet.mockResolvedValue(
      view({
        chapters: [
          {
            number: 3,
            title: '',
            has_payoff_beat: false,
            charge_beat: false,
            overall: null,
            pacing: null,
            prose_chars: null,
            volume_id: null,
            volume_number: null,
          },
        ],
      }),
    );
    render(<ArcTab projectId="p1" />);
    const row = await screen.findByTestId('arc-chapter-3');
    expect(row).toHaveTextContent('未挂卷');
    expect(row.textContent?.trim()).not.toMatch(/\s{2,}/);
  });

  it('卷列表：卷号 / 标题 / 状态 / 章数，未知状态原样透出', async () => {
    aGet.mockResolvedValue(
      view({
        volumes: [
          { volume_id: 'v1', number: 1, title: '第一卷', status: 'active', chapter_count: 1 },
          { volume_id: 'v9', number: 9, title: null, status: 'drafting', chapter_count: 2 },
        ],
      }),
    );
    render(<ArcTab projectId="p1" />);
    const v1 = await screen.findByTestId('arc-volume-1');
    expect(v1).toHaveTextContent('第一卷');
    expect(v1).toHaveTextContent('进行中');
    const v9 = screen.getByTestId('arc-volume-9');
    expect(v9).toHaveTextContent('drafting');
    expect(v9).toHaveTextContent('2');
  });

  it('逾期剧透策略：有才显示，且目标 id 原样透出不猜名字', async () => {
    aGet.mockResolvedValue(
      view({
        reveal_policies: {
          planned: 1,
          revealed: 0,
          cancelled: 0,
          overdue: [
            {
              policy_id: 'rp1',
              target_kind: 'hook',
              target_id: 'hk_abc',
              reveal_by_chapter: 8,
              audience: 'reader',
            },
          ],
        },
      }),
    );
    render(<ArcTab projectId="p1" />);
    const row = await screen.findByTestId('arc-reveal-rp1');
    expect(row).toHaveTextContent('hk_abc');
    expect(row).toHaveTextContent('第8章前');
  });

  it('逾期剧透为空 ⇒ 整节不渲染', async () => {
    render(<ArcTab projectId="p1" />);
    await screen.findByTestId('arc-chapter-table');
    expect(screen.queryByTestId('arc-reveal-overdue-table')).not.toBeInTheDocument();
  });

  it('「重新汇总」⇒ 真的重发 GET（派生视图每次现算，不缓存）', async () => {
    render(<ArcTab projectId="p1" />);
    await screen.findByTestId('arc-chapter-table');
    await userEvent.click(screen.getByRole('button', { name: '重新汇总' }));
    await waitFor(() => expect(aGet).toHaveBeenCalledTimes(2));
    expect(aGet).toHaveBeenCalledWith('p1');
  });

  it('接口失败 ⇒ 错误可见，不静默成「没有告警」', async () => {
    aGet.mockRejectedValue(new Error('弧光汇总失败'));
    render(<ArcTab projectId="p1" />);
    expect(await screen.findByText(/弧光汇总失败/)).toBeInTheDocument();
    expect(screen.queryByTestId('arc-chapter-table')).not.toBeInTheDocument();
  });

  it('只读：面板里没有任何编辑入口（后端只有只读端点）', async () => {
    render(<ArcTab projectId="p1" />);
    const table = await screen.findByTestId('arc-chapter-table');
    expect(table.querySelectorAll('input, textarea, select')).toHaveLength(0);
    // 唯一的按钮是「重新汇总」。
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['重新汇总']);
  });
});
