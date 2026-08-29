// WorldTab 关键流测试：
// - locations 默认 kind 列表加载：渲染名称/statement/可见性
// - 切换 kind=factions → 列表重新加载 + 调 factionsApi.list
// - API 失败 → ErrorBanner 显示错误

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { WorldTab } from './WorldTab';

vi.mock('../../api/endpoints', () => ({
  locationsApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
  factionsApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
  worldRulesApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}));

import { factionsApi, locationsApi, worldRulesApi } from '../../api/endpoints';

const LOC_FIXTURE = [
  {
    id: 'loc_001',
    project_id: 'p1',
    name: '青石镇',
    statement: '故事开始的偏僻小镇',
    visibility: 'PUBLIC',
    data: { climate: 'subtropical' },
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
  },
  {
    id: 'loc_002',
    project_id: 'p1',
    name: '北境雪山',
    statement: '埋藏上古秘辛的山脉',
    visibility: 'RESTRICTED',
    data: {},
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
  },
];

const FACT_FIXTURE = [
  {
    id: 'fac_001',
    project_id: 'p1',
    name: '玄甲宗',
    statement: '守护北方边陲的门派',
    visibility: 'PUBLIC',
    data: { alignment: 'lawful' },
    created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-20T10:00:00',
  },
];

describe('WorldTab - 关键流', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (locationsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      LOC_FIXTURE,
    );
    (factionsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      FACT_FIXTURE,
    );
    (worldRulesApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  });

  it('a) 默认 kind=locations：渲染地点列表 + data_json 预览（第一条）', async () => {
    render(<WorldTab projectId="p1" />);

    // 默认切到 locations kind
    expect(vi.mocked(locationsApi.list)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(factionsApi.list)).not.toHaveBeenCalled();

    // 数据渲染
    await waitFor(() => {
      expect(screen.getByText('青石镇')).toBeInTheDocument();
    });
    expect(screen.getByText('北境雪山')).toBeInTheDocument();
    expect(screen.getByText('故事开始的偏僻小镇')).toBeInTheDocument();
    // data_json 预览：climate: subtropical
    expect(screen.getByText(/subtropical/)).toBeInTheDocument();
  });

  it('b) 切到 factions：调 factionsApi.list + 渲染势力名', async () => {
    render(<WorldTab projectId="p1" />);

    // 默认 locations 先调一次
    await waitFor(() => {
      expect(screen.getByText('青石镇')).toBeInTheDocument();
    });

    // 点「势力」tab（toolbar 内嵌 .tabs，kind= factions）
    fireEvent.click(screen.getByText('势力 Factions'));

    await waitFor(() => {
      expect(vi.mocked(factionsApi.list)).toHaveBeenCalledTimes(1);
    });
    // 势力名渲染
    await waitFor(() => {
      expect(screen.getByText('玄甲宗')).toBeInTheDocument();
    });
    // 不再渲染地点
    expect(screen.queryByText('青石镇')).toBeNull();
  });

  it('c) API 失败：locationsApi.list 抛错 → ErrorBanner 显示错误，不渲染表格', async () => {
    (
      locationsApi.list as unknown as ReturnType<typeof vi.fn>
    ).mockRejectedValue(new Error('世界设定服务超时'));

    render(<WorldTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getByText('世界设定服务超时')).toBeInTheDocument();
    });
    // 任何地点都不应渲染
    expect(screen.queryByText('青石镇')).toBeNull();
  });
});