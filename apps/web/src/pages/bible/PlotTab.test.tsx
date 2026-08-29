// PlotTab「新建事件」payload 形状测试（Sprint 11 bugfix）。
//
// 白盒审计发现：PlotTab.tsx 旧实现把 description 塞进 cause 字段
//（dict 形态），且 time 默认 {} → 后端 _validate_time 必 422。
// 修复后表单必须保证：
//   1) cause 是 event_id 字符串数组（或 null），绝不能是 {summary: ...} 这样的 dict
//   2) time 对象至少含 timeline_day
//
// 本测试以最小冒烟覆盖这两点，避免再次回归。

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
  };
});

import { eventsApi, timelineApi } from '../../api/endpoints';
import { PlotTab } from './PlotTab';

describe('PlotTab 新建事件 payload 形状', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (eventsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (timelineApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (eventsApi.create as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'event_x',
      project_id: 'p1',
      type: 'revelation',
      cause: null,
      effects: null,
      participants: [],
      location_id: null,
      time: { timeline_day: 1 },
      status: 'planned',
      introduced_chapter_id: null,
      visibility: 'RESTRICTED',
      who_knows: null,
      created_at: '2026-08-29T00:00:00',
      updated_at: '2026-08-29T00:00:00',
    });
  });

  it('提交时 cause 不是 dict；time 含 timeline_day', async () => {
    const user = userEvent.setup();
    render(<PlotTab projectId="p1" />);

    // 打开新建事件弹窗
    await user.click(screen.getByRole('button', { name: /\+ 新建事件/ }));

    // 填一段 description —— 旧 bug 会把它当 dict 塞进 cause
    await user.type(
      screen.getByPlaceholderText('一句话或一段话描述这个事件'),
      '男主在祠堂发现父亲的密信',
    );

    // 默认 timeJson='{}' 也必须被前端兜底，提交时含 timeline_day
    // 提交
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
  });
});

// ----------------------------------------------------------------------------
// 列表渲染 + 时间线排序 + 失败分支关键流
// ----------------------------------------------------------------------------

const EVENT_FIXTURE = [
  {
    id: 'evt_001',
    project_id: 'p1',
    type: 'revelation',
    cause: null,
    effects: null,
    participants: [],
    location_id: null,
    time: { at: '2026-08-21T10:00:00+00:00', timeline_day: 2 },
    status: 'planned',
    introduced_chapter_id: null,
    visibility: 'RESTRICTED',
    who_knows: null,
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
  },
  {
    id: 'evt_002',
    project_id: 'p1',
    type: 'conflict',
    cause: null,
    effects: null,
    participants: [],
    location_id: null,
    time: { at: '2026-08-22T10:00:00+00:00', timeline_day: 5 },
    status: 'ongoing',
    introduced_chapter_id: null,
    visibility: 'RESTRICTED',
    who_knows: null,
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
  },
];

const TIMELINE_FIXTURE_OUT_OF_ORDER = [
  {
    id: 'tl_5',
    project_id: 'p1',
    day_index: 5,
    time_ref: null,
    description: '第五日·决战',
    source_event_id: 'evt_002',
  },
  {
    id: 'tl_2',
    project_id: 'p1',
    day_index: 2,
    time_ref: '傍晚',
    description: '第二日·秘信',
    source_event_id: 'evt_001',
  },
];

describe('PlotTab 关键流', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('a) 列表 + 时间线渲染：事件行 + 时间线按 day_index 升序', async () => {
    (eventsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      EVENT_FIXTURE,
    );
    (
      timelineApi.list as unknown as ReturnType<typeof vi.fn>
    ).mockResolvedValue(TIMELINE_FIXTURE_OUT_OF_ORDER);

    render(<PlotTab projectId="p1" />);

    // 事件行渲染
    await waitFor(() => {
      expect(screen.getByText('revelation')).toBeInTheDocument();
    });
    expect(screen.getByText('conflict')).toBeInTheDocument();
    expect(screen.getByText('planned')).toBeInTheDocument();
    expect(screen.getByText('ongoing')).toBeInTheDocument();

    // 时间线渲染
    expect(screen.getByText('Day 2')).toBeInTheDocument();
    expect(screen.getByText('Day 5')).toBeInTheDocument();
    // 时间线按 day_index 升序：先 Day 2 后 Day 5
    const day2 = screen.getByText('Day 2');
    const day5 = screen.getByText('Day 5');
    expect(day2.compareDocumentPosition(day5) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('b) API 失败：eventsApi.list 抛错 → ErrorBanner 显示错误', async () => {
    (eventsApi.list as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('事件服务 503'),
    );
    (timelineApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    render(<PlotTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getByText('事件服务 503')).toBeInTheDocument();
    });
    // 事件不渲染
    expect(screen.queryByText('revelation')).toBeNull();
  });
});
