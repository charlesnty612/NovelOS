// PlotTab「事件清单 + 时间线」人读视图回归测试（Sprint 18 剧情页改造）。
//
// 覆盖：
//   1) 新建事件 payload 形状（cause 必为数组/null、time 含 timeline_day、status 默认 planned）。
//   2) 表格列重排：时间 / 类型 / 描述 / 参与 / 状态 / 操作。
//   3) 描述全文渲染、Day N 显示、类型/状态中文徽标、参与角色名解析、空描述「（无描述）」、
//      planned 行淡化、地点名「第 N 章」小字、时间列不再误显「—」。
//   4) 时间线按 day_index 升序 + 描述超 80 字截断。
//   5) API 失败 → ErrorBanner。

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../api/endpoints', () => {
  return {
    eventsApi: {
      list: vi.fn(),
      create: vi.fn(),
      delete: vi.fn(),
    },
    timelineApi: {
      list: vi.fn(),
    },
    charactersApi: {
      listByProject: vi.fn(),
    },
    locationsApi: {
      list: vi.fn(),
    },
    chaptersApi: {
      listByProject: vi.fn(),
    },
  };
});

import {
  chaptersApi,
  charactersApi,
  eventsApi,
  locationsApi,
  timelineApi,
} from '../../api/endpoints';
import { PlotTab } from './PlotTab';

// ---------------------------------------------------------------------------
// 基础空态：用于不关心列表的用例
// ---------------------------------------------------------------------------

const EMPTY_EVENT_BASE = {
  id: 'event_x',
  project_id: 'p1',
  type: 'revelation' as const,
  cause: null,
  effects: null,
  participants: [],
  location_id: null,
  time: { timeline_day: 1 },
  status: 'planned',
  introduced_chapter_id: null,
  description: null,
  visibility: 'RESTRICTED' as const,
  who_knows: null,
  created_at: '2026-08-29T00:00:00',
  updated_at: '2026-08-29T00:00:00',
};

const setupEmpty = () => {
  (eventsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  (timelineApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  (charactersApi.listByProject as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  (locationsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  (chaptersApi.listByProject as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  (eventsApi.create as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(EMPTY_EVENT_BASE);
};

// ---------------------------------------------------------------------------
// 表单 payload 形状（沿用旧测试，保证 cause 形态 + time.timeline_day 不回归）
// ---------------------------------------------------------------------------

describe('PlotTab 新建事件 payload 形状', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupEmpty();
  });

  it('提交时 cause 不是 dict；time 含 timeline_day；status 默认 planned', async () => {
    const user = userEvent.setup();
    render(<PlotTab projectId="p1" />);

    // 打开新建事件弹窗
    await user.click(screen.getByRole('button', { name: /\+ 新建事件/ }));

    // 填一段 description —— 旧 bug 会把它当 dict 塞进 cause
    await user.type(
      screen.getByPlaceholderText('一句话或一段话描述这个事件'),
      '男主在祠堂发现父亲的密信',
    );

    // 默认 timeJson='{"timeline_day":1}'，status 默认 planned
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(eventsApi.create).toHaveBeenCalledTimes(1);
    });

    const payload = (eventsApi.create as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0][1];

    // cause 必须是数组或 null，不能是 dict
    expect(payload.cause === null || Array.isArray(payload.cause)).toBe(true);
    if (Array.isArray(payload.cause)) {
      for (const item of payload.cause) {
        expect(typeof item).toBe('string');
      }
    }

    // time 必须含 timeline_day（数字或字符串数字）
    expect(payload.time).toBeTruthy();
    expect(payload.time).toHaveProperty('timeline_day');
    const td = (payload.time as Record<string, unknown>).timeline_day;
    expect(td === null || typeof td === 'number').toBe(true);

    // status 默认 planned
    expect(payload.status).toBe('planned');
    // description 透传（非空字符串）
    expect(payload.description).toBe('男主在祠堂发现父亲的密信');
  });

  it('状态选项不包含 ongoing；仅 planned / recorded / resolved / abandoned', async () => {
    const user = userEvent.setup();
    render(<PlotTab projectId="p1" />);
    await user.click(screen.getByRole('button', { name: /\+ 新建事件/ }));

    // 状态 select 的 options
    const options = Array.from(
      document.querySelectorAll('select option'),
    ).map((o) => o.textContent?.trim() ?? '');
    // 应该有 abandoned（后端枚举新成员）；不应该有 ongoing（DB CHECK 拒绝）
    expect(options.some((t) => t.includes('已废弃'))).toBe(true);
    expect(options.some((t) => t.includes('abandoned'))).toBe(true);
    expect(options.some((t) => t.includes('进行中'))).toBe(false);
    expect(options.some((t) => t.toLowerCase().includes('ongoing'))).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 列表 + 时间线：人读视图关键流
// ---------------------------------------------------------------------------

const CHAR_FIXTURE = [
  {
    character_id: 'char_001',
    project_id: 'p1',
    name: '林远',
    role: 'protagonist' as const,
    core_json: {},
    visibility: 'PUBLIC' as const,
    who_knows: null,
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
    latest_state_version: 1,
    latest_state_json: {},
  },
  {
    character_id: 'char_002',
    project_id: 'p1',
    name: '苏挽',
    role: 'love_interest' as const,
    core_json: {},
    visibility: 'PUBLIC' as const,
    who_knows: null,
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
    latest_state_version: 1,
    latest_state_json: {},
  },
];

const LOC_FIXTURE = [
  {
    id: 'loc_01',
    project_id: 'p1',
    name: '祠堂',
    statement: '',
    data: {},
    visibility: 'PUBLIC' as const,
    who_knows: null,
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
  },
];

const CHAPTER_FIXTURE = [
  {
    chapter_id: 'ch_01',
    project_id: 'p1',
    number: 1,
    title: '第一章',
    plan_json: {},
    status: 'COMMITTED' as const,
    visibility: 'PUBLIC',
    who_knows: null,
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
  },
];

// 全字段事件：description + participants(id 字符串数组) + location_id + introduced_chapter_id + time.timeline_day
const FULL_EVENTS = [
  {
    id: 'evt_001',
    project_id: 'p1',
    type: 'revelation' as const,
    cause: null,
    effects: null,
    participants: ['char_001', 'char_002'],
    location_id: 'loc_01',
    time: { timeline_day: 1, in_story_date: '永和三年春' },
    status: 'recorded',
    introduced_chapter_id: 'ch_01',
    description: '林远在祠堂发现父亲的密信，苏挽从旁协助破译。',
    visibility: 'RESTRICTED' as const,
    who_knows: null,
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
  },
  // planned + 空描述（init 残留空壳）
  {
    id: 'evt_002',
    project_id: 'p1',
    type: 'conflict' as const,
    cause: null,
    effects: null,
    participants: [],
    location_id: null,
    time: { timeline_day: 1 },
    status: 'planned',
    introduced_chapter_id: null,
    description: null,
    visibility: 'RESTRICTED' as const,
    who_knows: null,
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
  },
  // 查不到名字的角色：应显示 id 前 8 字符
  {
    id: 'evt_003',
    project_id: 'p1',
    type: 'decision' as const,
    cause: null,
    effects: null,
    participants: ['char_unknown_9999'],
    location_id: null,
    time: { timeline_day: 1 },
    status: 'abandoned',
    introduced_chapter_id: null,
    description: '关键抉择',
    visibility: 'RESTRICTED' as const,
    who_knows: null,
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
  },
];

const TIMELINE_FIXTURE_OUT_OF_ORDER = [
  {
    id: 'tl_5',
    project_id: 'p1',
    event_id: 'evt_003',
    day_index: 5,
    time_ref: '傍晚',
    description: '第五日·决战',
    created_at: '2026-08-20T10:00:00',
  },
  {
    id: 'tl_1',
    project_id: 'p1',
    event_id: 'evt_001',
    day_index: 1,
    time_ref: null,
    description: '第二日·秘信', // 故意与 day_index 不一致，验证 description 渲染
    created_at: '2026-08-20T10:00:00',
  },
];

describe('PlotTab 人读视图', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupEmpty();
    (eventsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(FULL_EVENTS);
    (timelineApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      TIMELINE_FIXTURE_OUT_OF_ORDER,
    );
    (charactersApi.listByProject as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      CHAR_FIXTURE,
    );
    (locationsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(LOC_FIXTURE);
    (chaptersApi.listByProject as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      CHAPTER_FIXTURE,
    );
  });

  it('a) 描述全文 + 中文徽标 + 角色名 + 地点/章节小字', async () => {
    render(<PlotTab projectId="p1" />);

    // 描述全文
    await waitFor(() => {
      expect(
        screen.getByText('林远在祠堂发现父亲的密信，苏挽从旁协助破译。'),
      ).toBeInTheDocument();
    });

    // 类型徽标：revelation → 揭露
    expect(screen.getByText('揭露')).toBeInTheDocument();
    // 状态徽标：recorded → 已记录；abandoned → 已废弃
    expect(screen.getByText('已记录')).toBeInTheDocument();
    expect(screen.getByText('已废弃')).toBeInTheDocument();
    // 中文状态：planned → 计划中
    expect(screen.getByText('计划中')).toBeInTheDocument();

    // 参与角色名解析（来自 charactersApi mock）
    expect(screen.getByText('林远')).toBeInTheDocument();
    expect(screen.getByText('苏挽')).toBeInTheDocument();
    // 查不到名字 → 显示 id 前 8 字符
    expect(screen.getByText('char_unk')).toBeInTheDocument();

    // 地点 + 第 N 章 小字（事件 evt_001 同时有 location_id 与 introduced_chapter_id）
    await waitFor(() => {
      expect(screen.getByText(/祠堂.*来源「第 1 章」|来源「第 1 章」.*祠堂/)).toBeInTheDocument();
    });
  });

  it('b) Day N 显示（不再误显 —）；空描述显「（无描述）」', async () => {
    render(<PlotTab projectId="p1" />);

    // 三个事件都是 Day 1；时间线另有 Day 1 / Day 5 —— 共多个 Day 1
    await waitFor(() => {
      expect(screen.getAllByText('Day 1').length).toBeGreaterThanOrEqual(3);
    });
    expect(screen.getByText('永和三年春')).toBeInTheDocument();

    // 空描述（evt_002）显「（无描述）」
    const descCells = screen.getAllByTestId('plot-event-desc');
    // 至少有一个「（无描述）」
    expect(
      descCells.some((c) => c.textContent?.includes('（无描述）') ?? false),
    ).toBe(true);

    // 时间列不再显「—」：确保表格行内第一格时间格都含「Day 1」
    const rows = screen.getAllByTestId('plot-event-row');
    for (const r of rows) {
      const firstCell = r.querySelector('td');
      expect(firstCell?.textContent).toMatch(/Day 1/);
    }
  });

  it('c) planned 行整体淡化（className 含 muted）', async () => {
    render(<PlotTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getAllByTestId('plot-event-row').length).toBe(3);
    });

    // 通过 data-testid 行 + 内含的 status badge 锁定对应行
    const plannedBadges = screen.getAllByTestId('plot-event-status');
    const plannedRow = plannedBadges
      .find((b) => b.getAttribute('data-status') === 'planned')
      ?.closest('tr');
    expect(plannedRow).toBeTruthy();
    expect(plannedRow?.className ?? '').toContain('muted');

    // recorded / abandoned 行不带 muted
    const recordedRow = plannedBadges
      .find((b) => b.getAttribute('data-status') === 'recorded')
      ?.closest('tr');
    expect(recordedRow?.className ?? '').not.toContain('muted');
  });

  it('d) 表格列头顺序：时间 / 类型 / 描述 / 参与 / 状态 / 操作', async () => {
    render(<PlotTab projectId="p1" />);
    await waitFor(() => {
      expect(screen.getAllByTestId('plot-event-row').length).toBeGreaterThan(0);
    });
    const ths = Array.from(document.querySelectorAll('table.table thead th')).map(
      (th) => th.textContent?.trim() ?? '',
    );
    expect(ths).toEqual(['时间', '类型', '描述', '参与', '状态', '操作']);
  });

  it('e) 时间线按 day_index 升序 + 描述渲染', async () => {
    render(<PlotTab projectId="p1" />);

    await waitFor(() => {
      expect(document.querySelector('ol')).toBeTruthy();
    });
    expect(screen.getByText('Day 5')).toBeInTheDocument();

    // 时间线按 day_index 升序：先 Day 1 后 Day 5
    const day5 = screen.getByText('Day 5');
    // 取时间线里的 Day 1（不在事件表格中的版本——通过时间线 ol 容器定位）
    const ol = document.querySelector('ol');
    expect(ol).toBeTruthy();
    const day1InTimeline = Array.from(ol?.querySelectorAll('strong') ?? []).find(
      (el) => el.textContent === 'Day 1',
    );
    expect(day1InTimeline).toBeTruthy();
    expect(
      (day1InTimeline as Element).compareDocumentPosition(day5) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // 时间线 description 渲染
    expect(ol?.textContent).toContain('第五日·决战');
    expect(ol?.textContent).toContain('第二日·秘信');
  });

  it('f) 时间线 description 超 80 字截断为 … 并把全文塞进 title', async () => {
    const longDesc = '字'.repeat(120);
    (timelineApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: 'tl_long',
        project_id: 'p1',
        event_id: 'evt_x',
        day_index: 1,
        time_ref: null,
        description: longDesc,
        created_at: '2026-08-20T10:00:00',
      },
    ]);

    render(<PlotTab projectId="p1" />);

    await waitFor(() => {
      // 截断后以「…」结尾：DOM 文本为 80 字 + …
      const olText = document.querySelector('ol')?.textContent ?? '';
      expect(olText).toContain('…');
      expect(olText).not.toContain(longDesc);
    });

    // 全文（120 字）通过 title 属性可访问
    const liWithTitle = document.querySelector('ol li [title]');
    expect(liWithTitle).toBeTruthy();
    expect(liWithTitle?.getAttribute('title')).toBe(longDesc);
  });

  it('g) 时间线空描述显「（无描述）」', async () => {
    (timelineApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: 'tl_empty',
        project_id: 'p1',
        event_id: 'evt_x',
        day_index: 1,
        time_ref: null,
        description: null,
        created_at: '2026-08-20T10:00:00',
      },
    ]);
    render(<PlotTab projectId="p1" />);
    await waitFor(() => {
      expect(document.querySelector('ol')?.textContent).toContain('（无描述）');
    });
  });

  it('h) API 失败：eventsApi.list 抛错 → ErrorBanner 显示错误', async () => {
    (eventsApi.list as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('事件服务 503'),
    );
    (timelineApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (charactersApi.listByProject as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (locationsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (chaptersApi.listByProject as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    render(<PlotTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getByText('事件服务 503')).toBeInTheDocument();
    });
    // 事件表格行不渲染
    expect(screen.queryByTestId('plot-event-row')).toBeNull();
  });

  it('i) 删除按钮：confirm + eventsApi.delete + reload', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    (
      eventsApi.delete as unknown as ReturnType<typeof vi.fn>
    ).mockResolvedValue(undefined);
    // 第二次 reload 后返回空
    (eventsApi.list as unknown as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(FULL_EVENTS)
      .mockResolvedValueOnce([]);

    const user = userEvent.setup();
    render(<PlotTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getAllByTestId('plot-event-row').length).toBe(3);
    });

    // 取表格行内的「删除」按钮
    const rowDel = document.querySelector('tr button.btn--danger') as HTMLButtonElement;
    expect(rowDel).toBeTruthy();
    await user.click(rowDel);

    await waitFor(() => {
      expect(eventsApi.delete).toHaveBeenCalledTimes(1);
    });
    expect(eventsApi.delete).toHaveBeenCalledWith('evt_001');
    confirmSpy.mockRestore();
  });
});
