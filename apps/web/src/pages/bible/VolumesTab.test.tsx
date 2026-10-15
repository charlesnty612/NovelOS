// 卷面板（评估 G6：卷与章节的隶属关系此前在 UI 上完全不存在）。
//
// 判别重点是**两条来自后端语义的硬约束**，它们都不是 UI 偏好：
//
// 1. 「一个项目同时只能有一个 active 卷」——已有 active 时必须给出说明，
//    而不是让作者点下去撞一个 409。
// 2. **seal 不可逆**：必须二次确认；且**不给解封按钮**（后端 `sealed → active`
//    反向跳变被拒 409，给一个永远失败的按钮比不给更坏）。
//
// 另外钉住「挂完章要能看见」：卷列表响应只给 `chapter_count` 聚合，章节归属
// 要从 `chapters[].volume_id` 反查——弄错数据源的话这一列会恒为「—」，
// 而那看起来完全合理、零告警。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { volumesApi, chaptersApi } from '../../api/endpoints';
import type { Chapter, VolumeListItem } from '../../api/types';
import { VolumesTab } from './VolumesTab';

vi.mock('../../api/endpoints', () => ({
  volumesApi: {
    list: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    update: vi.fn(),
    seal: vi.fn(),
    assign: vi.fn(),
  },
  chaptersApi: { listByProject: vi.fn() },
}));

const vList = volumesApi.list as unknown as ReturnType<typeof vi.fn>;
const vCreate = volumesApi.create as unknown as ReturnType<typeof vi.fn>;
const vSeal = volumesApi.seal as unknown as ReturnType<typeof vi.fn>;
const vAssign = volumesApi.assign as unknown as ReturnType<typeof vi.fn>;
const cList = chaptersApi.listByProject as unknown as ReturnType<typeof vi.fn>;

// volume_id 有默认值：beforeEach 里造「响应体」时不必每次重复写，
// 而要指定身份时显式覆盖（identity 是这些用例的主要判别点，不该被默认值掩盖）。
const vol = (over: Partial<VolumeListItem> = {}): VolumeListItem => ({
  volume_id: 'vol_default',
  project_id: 'p1',
  number: 1,
  title: '第一卷',
  arc_summary: null,
  status: 'active',
  terminal_snapshot_json: null,
  chapter_count: 0,
  created_at: '2026-10-01T08:00:00Z',
  updated_at: '2026-10-01T08:00:00Z',
  ...over,
});

const chap = (chapter_id: string, number: number, volume_id: string | null): Chapter => ({
  chapter_id,
  project_id: 'p1',
  number,
  title: `第${number}章`,
  plan_json: {},
  status: 'DRAFTED',
  visibility: 'PUBLIC',
  who_knows: null,
  volume_id,
  created_at: '2026-10-01T08:00:00Z',
  updated_at: '2026-10-01T08:00:00Z',
});

describe('VolumesTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vCreate.mockResolvedValue(vol({}));
    vSeal.mockResolvedValue(vol({ status: 'sealed' }));
    vAssign.mockResolvedValue(vol({}));
  });

  it('无卷 ⇒ 空态 + 「新建第一卷」入口，而不是空白', async () => {
    vList.mockResolvedValue([]);
    cList.mockResolvedValue([]);
    render(<VolumesTab projectId="p1" />);
    expect(await screen.findByText('还没有卷')).toBeInTheDocument();
    expect(screen.getByTestId('volume-create-open')).toBeInTheDocument();
  });

  // 硬约束 1
  it('已有 active 卷 ⇒ 说明「同时只能有一个」，而不是让作者撞 409', async () => {
    vList.mockResolvedValue([vol({ volume_id: 'vol_1', status: 'active' })]);
    cList.mockResolvedValue([]);
    render(<VolumesTab projectId="p1" />);
    expect(await screen.findByTestId('volume-active-hint')).toHaveTextContent(
      '同时只能有一个',
    );
  });

  it('全部已封卷 ⇒ 不出该提示（可以再开一卷）', async () => {
    vList.mockResolvedValue([vol({ volume_id: 'vol_1', status: 'sealed' })]);
    cList.mockResolvedValue([]);
    render(<VolumesTab projectId="p1" />);
    await screen.findByTestId('volume-table');
    expect(screen.queryByTestId('volume-active-hint')).not.toBeInTheDocument();
  });

  // 挂章后要能看见（数据源判别）
  it('「包含章节」从 chapters[].volume_id 反查，不是恒「—」', async () => {
    vList.mockResolvedValue([
      vol({ volume_id: 'vol_1', chapter_count: 2 }),
      vol({ volume_id: 'vol_2', number: 2, chapter_count: 0 }),
    ]);
    cList.mockResolvedValue([
      chap('c1', 1, 'vol_1'),
      chap('c3', 3, 'vol_1'),
      chap('c2', 2, null),
    ]);
    render(<VolumesTab projectId="p1" />);
    const cell = await screen.findByTestId('volume-members-vol_1');
    expect(cell).toHaveTextContent('第1章');
    expect(cell).toHaveTextContent('第3章');
    // 未挂卷的那一卷是真的没有成员，不得借用别的卷的数据
    expect(screen.getByTestId('volume-members-vol_2')).toHaveTextContent('—');
    expect(screen.getByTestId('volume-unassigned-note')).toHaveTextContent('1 章');
  });

  // 硬约束 2：封卷不可逆
  it('点「封卷」⇒ 先弹 danger 确认，此时未发请求', async () => {
    vList.mockResolvedValue([vol({ volume_id: 'vol_1', status: 'active' })]);
    cList.mockResolvedValue([]);
    render(<VolumesTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('volume-seal-vol_1'));

    const dialog = await screen.findByTestId('volume-seal-confirm');
    expect(within(dialog).getByText(/不可逆/)).toBeInTheDocument();
    expect(vSeal).not.toHaveBeenCalled();
  });

  it('封卷确认文案说明「不能解封」——作者必须知道自己反悔不了', async () => {
    vList.mockResolvedValue([vol({ volume_id: 'vol_1', status: 'active' })]);
    cList.mockResolvedValue([]);
    render(<VolumesTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('volume-seal-vol_1'));
    const dialog = await screen.findByTestId('volume-seal-confirm');
    expect(dialog).toHaveTextContent('不支持解封');
  });

  it('确认封卷 ⇒ 调 seal 端点并提示已冻结', async () => {
    vList.mockResolvedValue([vol({ volume_id: 'vol_1', status: 'active' })]);
    cList.mockResolvedValue([]);
    render(<VolumesTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('volume-seal-vol_1'));
    const dialog = await screen.findByTestId('volume-seal-confirm');
    await userEvent.click(within(dialog).getByTestId('volume-seal-confirm-confirm'));

    await waitFor(() => expect(vSeal).toHaveBeenCalledWith('p1', 'vol_1'));
    expect(await screen.findByText(/已封卷/)).toBeInTheDocument();
  });

  // 硬约束 2：已封卷的卷不给封卷/挂章入口
  it('已封卷 ⇒ 不出「封卷」按钮，「挂章」按钮禁用（不给永远 409 的按钮）', async () => {
    vList.mockResolvedValue([vol({ volume_id: 'vol_1', status: 'sealed' })]);
    cList.mockResolvedValue([]);
    render(<VolumesTab projectId="p1" />);
    await screen.findByTestId('volume-table');
    expect(screen.queryByTestId('volume-seal-vol_1')).not.toBeInTheDocument();
    expect(screen.getByTestId('volume-assign-vol_1')).toBeDisabled();
  });

  // 挂章
  it('挂章 ⇒ 提交所选 chapter_id，并提示挂到了第几章', async () => {
    vList.mockResolvedValue([vol({ volume_id: 'vol_1', status: 'active' })]);
    cList.mockResolvedValue([chap('c1', 1, null), chap('c2', 2, null)]);
    render(<VolumesTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('volume-assign-vol_1'));

    await userEvent.selectOptions(
      await screen.findByTestId('volume-assign-select'),
      'c2',
    );
    await userEvent.click(screen.getByTestId('volume-assign-submit'));

    await waitFor(() => expect(vAssign).toHaveBeenCalledWith('p1', 'vol_1', 'c2'));
    expect(await screen.findByText(/第 2 章已挂入第 1 卷/)).toBeInTheDocument();
  });

  it('未选章 ⇒ 提交按钮禁用（不发空 chapter_id 的请求）', async () => {
    vList.mockResolvedValue([vol({ volume_id: 'vol_1', status: 'active' })]);
    cList.mockResolvedValue([chap('c1', 1, null)]);
    render(<VolumesTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('volume-assign-vol_1'));
    expect(await screen.findByTestId('volume-assign-submit')).toBeDisabled();
  });

  // 新建
  it('新建卷 ⇒ 提交 number + title + arc_summary', async () => {
    vList.mockResolvedValue([]);
    cList.mockResolvedValue([]);
    render(<VolumesTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('volume-create-open'));

    const num = await screen.findByTestId('volume-number-input');
    await userEvent.clear(num);
    await userEvent.type(num, '3');
    await userEvent.type(screen.getByTestId('volume-title-input'), '第三卷');
    await userEvent.type(screen.getByTestId('volume-summary-input'), '卷纲');
    await userEvent.click(screen.getByTestId('volume-create-submit'));

    await waitFor(() =>
      expect(vCreate).toHaveBeenCalledWith('p1', {
        number: 3,
        title: '第三卷',
        arc_summary: '卷纲',
      }),
    );
  });

  it('标题/摘要留空 ⇒ 不提交这两个键（空串与缺省语义不同）', async () => {
    vList.mockResolvedValue([]);
    cList.mockResolvedValue([]);
    render(<VolumesTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('volume-create-open'));
    await userEvent.click(await screen.findByTestId('volume-create-submit'));

    await waitFor(() =>
      expect(vCreate).toHaveBeenCalledWith('p1', { number: 1 }),
    );
  });

  it('接口失败 ⇒ 错误可见，不静默成空表', async () => {
    vList.mockRejectedValue(new Error('卷列表拉取失败'));
    cList.mockResolvedValue([]);
    render(<VolumesTab projectId="p1" />);
    expect(await screen.findByText(/卷列表拉取失败/)).toBeInTheDocument();
  });
});
