// 人物关系面板（评估 G6：54 行数据、零 UI 入口）。
//
// 判别重点：
// 1. **只读**——面板不得出现任何编辑入口。后端只有只读端点；在这里加输入框
//    会让「作者手写的角色档案关系」与「delta 驱动的真实关系」分叉，
//    且没有任何机制同步它们（本面板顶部横幅正是在讲这件事）。
// 2. **有方向**——渲染成「从 → 关系 → 到」，不能塌成无向的一对名字。
// 3. 角色名解析不到时**显式说「未知角色 <id>」**，不许渲染成空白单元格
//    （空白会被读成「没配角色」，而真相是「这条关系指向一个已删/未载入的角色」）。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { relationshipsApi, charactersApi } from '../../api/endpoints';
import type { Character, Relationship } from '../../api/types';
import { RelationshipsTab } from './RelationshipsTab';

vi.mock('../../api/endpoints', () => ({
  relationshipsApi: { list: vi.fn() },
  charactersApi: { listByProject: vi.fn() },
}));

const rList = relationshipsApi.list as unknown as ReturnType<typeof vi.fn>;
const cList = charactersApi.listByProject as unknown as ReturnType<typeof vi.fn>;

const rel = (over: Partial<Relationship> & { id: string }): Relationship => ({
  project_id: 'p1',
  from_character_id: 'c_a',
  to_character_id: 'c_b',
  relation_type: 'ally',
  state: {},
  last_state_version: 3,
  ...over,
});

const chr = (character_id: string, name: string): Character =>
  ({ character_id, project_id: 'p1', name }) as Character;

describe('RelationshipsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    cList.mockResolvedValue([chr('c_a', '甲'), chr('c_b', '乙'), chr('c_c', '丙')]);
  });

  it('无关系 ⇒ 明确空态，而不是空白表', async () => {
    rList.mockResolvedValue([]);
    render(<RelationshipsTab projectId="p1" />);
    expect(await screen.findByText('还没有人物关系')).toBeInTheDocument();
  });

  it('渲染成「从 → 关系 → 到」，且带方向', async () => {
    rList.mockResolvedValue([rel({ id: 'r1', from_character_id: 'c_a', to_character_id: 'c_b' })]);
    render(<RelationshipsTab projectId="p1" />);
    const row = await screen.findByTestId('relationship-row-r1');
    expect(row).toHaveTextContent('甲');
    expect(row).toHaveTextContent('乙');
    expect(row).toHaveTextContent('ally');
  });

  // 方向判别：同一对人反向是**另一条关系**，不能合并。
  it('反向关系是两条独立行，不合并成无向', async () => {
    rList.mockResolvedValue([
      rel({ id: 'r1', from_character_id: 'c_a', to_character_id: 'c_b', relation_type: 'ally' }),
      rel({ id: 'r2', from_character_id: 'c_b', to_character_id: 'c_a', relation_type: 'rival' }),
    ]);
    render(<RelationshipsTab projectId="p1" />);
    await screen.findByTestId('relationship-row-r1');
    expect(screen.getByTestId('relationship-row-r2')).toHaveTextContent('rival');
    expect(screen.getByTestId('relationship-type-r1')).toHaveTextContent('ally');
  });

  it('角色解析不到 ⇒ 显式写出未知 id，不渲染空白', async () => {
    rList.mockResolvedValue([rel({ id: 'r1', to_character_id: 'c_gone' })]);
    render(<RelationshipsTab projectId="p1" />);
    const row = await screen.findByTestId('relationship-row-r1');
    expect(row).toHaveTextContent('c_gone');
  });

  it('relation_type 为空串 ⇒ 说「未标注关系」而不是空白单元格', async () => {
    rList.mockResolvedValue([rel({ id: 'r1', relation_type: '' })]);
    render(<RelationshipsTab projectId="p1" />);
    expect(await screen.findByTestId('relationship-type-r1')).toHaveTextContent('未标注关系');
  });

  it('按角色筛选：只留与该角色相关的（无论它是从还是到）', async () => {
    rList.mockResolvedValue([
      rel({ id: 'r1', from_character_id: 'c_a', to_character_id: 'c_b' }),
      rel({ id: 'r2', from_character_id: 'c_c', to_character_id: 'c_b' }),
      rel({ id: 'r3', from_character_id: 'c_c', to_character_id: 'c_a' }),
    ]);
    render(<RelationshipsTab projectId="p1" />);
    await screen.findByTestId('relationship-row-r1');

    await userEvent.selectOptions(screen.getByTestId('relationship-focus'), 'c_a');
    await waitFor(() =>
      expect(screen.queryByTestId('relationship-row-r2')).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId('relationship-row-r1')).toBeInTheDocument();
    expect(screen.getByTestId('relationship-row-r3')).toBeInTheDocument();
  });

  // 只读判别
  it('只读：面板里没有任何编辑入口', async () => {
    rList.mockResolvedValue([rel({ id: 'r1' })]);
    const { container } = render(<RelationshipsTab projectId="p1" />);
    await screen.findByTestId('relationship-table');
    expect(container.querySelectorAll('input, textarea')).toHaveLength(0);
    expect(container.textContent).not.toMatch(/保存|删除|新建/);
  });

  it('接口失败 ⇒ 错误可见', async () => {
    rList.mockRejectedValue(new Error('关系拉取失败'));
    render(<RelationshipsTab projectId="p1" />);
    expect(await screen.findByText(/关系拉取失败/)).toBeInTheDocument();
  });
});
