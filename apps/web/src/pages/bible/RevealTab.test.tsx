// 剧透策略面板。
//
// ⚠️ **本文件刻意 mock 在 client 层（而不是像兄弟页签那样 mock `api/endpoints`）**。
// 要判别的正是 `revealPoliciesApi.list` 把状态筛选发成**哪个查询参数**——
// 后端 docstring 写 `?status=`，代码用的是 `status_filter`。mock 掉 endpoints 就等于
// 把要验的那段代码一起 mock 掉了，测试会绿得毫无意义（与坑区「守卫自己被悄悄缩小了
// 执行域」同形：量具没量到目标，却报告成量过了）。保留 client 其余导出真实——
// endpoints 里的 `coerceJson` / `normalize*` 依赖它们。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { api } from '../../api/client';
import type { RequestOptions } from '../../api/client';
import type { RevealPolicy } from '../../api/types';
import { RevealTab } from './RevealTab';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  };
});

const getMock = api.get as unknown as ReturnType<typeof vi.fn>;
const postMock = api.post as unknown as ReturnType<typeof vi.fn>;
const patchMock = api.patch as unknown as ReturnType<typeof vi.fn>;
const delMock = api.delete as unknown as ReturnType<typeof vi.fn>;

/** 列表响应体；改哪个字段就在用例里显式覆盖——这些字段本身就是判别点。 */
let policies: RevealPolicy[] = [];
/** 候选（角色/伏笔/债务…列表）的统一响应。 */
let candidateRows: unknown[] = [];
/** 每次 GET 的入参留痕：断言「发到线上的路径与查询参数」。 */
let getCalls: Array<{ path: string; query?: Record<string, unknown> }> = [];

const pol = (over: Partial<RevealPolicy> & { policy_id: string }): RevealPolicy => ({
  project_id: 'p1',
  target_kind: 'hook',
  target_id: 'hook_1',
  reveal_by_chapter: 5,
  audience: 'reader',
  status: 'planned',
  revealed_chapter: null,
  notes: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  ...over,
});

/** 打开新建弹窗并等到表单挂载（候选 GET 是挂载后才发的）。 */
async function openCreateModal() {
  // 必须先等首屏加载完：加载中整页是 <Loading>，「新建」按钮根本不在 DOM 里。
  const open = await screen.findByTestId('reveal-create-open');
  await userEvent.click(open);
  return screen.findByTestId('reveal-create-modal');
}

describe('RevealTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    policies = [];
    candidateRows = [];
    getCalls = [];
    getMock.mockImplementation((path: string, query?: RequestOptions['query']) => {
      getCalls.push({ path, query: query as Record<string, unknown> | undefined });
      return Promise.resolve(path.includes('/reveal-policies') ? policies : candidateRows);
    });
    postMock.mockResolvedValue(pol({ policy_id: 'rp_new' }));
    patchMock.mockResolvedValue(pol({ policy_id: 'rp1' }));
    delMock.mockResolvedValue(undefined);
  });

  it('列表：目标种类 / target_id / 面向对象 / 状态 / 实际章号都上屏', async () => {
    policies = [
      pol({ policy_id: 'rp1', target_kind: 'character', target_id: 'c_a', audience: 'character:c_b' }),
      pol({ policy_id: 'rp2', target_kind: 'hook', target_id: 'hook_x', reveal_by_chapter: null, status: 'revealed', revealed_chapter: 7 }),
    ];
    render(<RevealTab projectId="p1" />);

    const row1 = await screen.findByTestId('reveal-row-rp1');
    expect(row1).toHaveTextContent('角色');
    expect(row1).toHaveTextContent('c_a');
    expect(screen.getByTestId('reveal-audience-rp1')).toHaveTextContent('character:c_b');

    const row2 = screen.getByTestId('reveal-row-rp2');
    expect(row2).toHaveTextContent('伏笔');
    expect(row2).toHaveTextContent('已揭开');
    expect(row2).toHaveTextContent('第 7 章');
    // reveal_by_chapter 为 null ⇒ 显示「未定」而不是空白单元格（空白会被读成「不需要揭」）
    expect(row2).toHaveTextContent('未定');
    expect(row1).toHaveTextContent('第 5 章前');
  });

  it('加载态：GET 未返回前显示 Loading 文案（不是空表）', () => {
    getMock.mockImplementation(() => new Promise(() => {}));
    render(<RevealTab projectId="p1" />);
    expect(screen.getByText('正在读取剧透策略…')).toBeInTheDocument();
  });

  it('空态：一条都没有 ⇒ 明确空态 + 新建入口', async () => {
    policies = [];
    render(<RevealTab projectId="p1" />);
    expect(await screen.findByText('还没有剧透策略')).toBeInTheDocument();
    expect(screen.getByTestId('reveal-create-open')).toBeInTheDocument();
  });

  it('筛选后为空 ⇒ 文案与「全空」不同（否则「筛选没生效」和「真没有」长得一样）', async () => {
    policies = [];
    render(<RevealTab projectId="p1" />);
    await screen.findByText('还没有剧透策略');
    await userEvent.selectOptions(screen.getByTestId('reveal-status-filter'), 'cancelled');
    expect(await screen.findByText('该状态下没有剧透策略')).toBeInTheDocument();
  });

  it('接口失败 ⇒ 错误可见，不静默成空表', async () => {
    getMock.mockRejectedValue(new Error('剧透策略拉取失败'));
    render(<RevealTab projectId="p1" />);
    expect(await screen.findByText(/剧透策略拉取失败/)).toBeInTheDocument();
  });

  // 参数名判别：后端 docstring 写 `status`，代码收 `status_filter`。反例一并钉住。
  it('状态筛选发的是 status_filter（且没有 status 这个键）', async () => {
    policies = [pol({ policy_id: 'rp1', status: 'revealed', revealed_chapter: 3 })];
    render(<RevealTab projectId="p1" />);
    await screen.findByTestId('reveal-table');

    await userEvent.selectOptions(screen.getByTestId('reveal-status-filter'), 'revealed');
    await waitFor(() => expect(getCalls.length).toBeGreaterThan(1));

    const last = getCalls[getCalls.length - 1];
    expect(last.path).toBe('/projects/p1/reveal-policies');
    expect(last.query).toEqual({ status_filter: 'revealed' });
    expect(last.query).not.toHaveProperty('status');
  });

  it('新建：提交 target_kind + target_id，成功后列表刷新且新行上屏', async () => {
    policies = [];
    postMock.mockImplementation(() => {
      const created = pol({ policy_id: 'rp_new', target_kind: 'debt', target_id: 'debt_1' });
      policies = [created];
      return Promise.resolve(created);
    });
    render(<RevealTab projectId="p1" />);
    await openCreateModal();

    await userEvent.selectOptions(screen.getByTestId('reveal-kind-select'), 'debt');
    await userEvent.type(screen.getByTestId('reveal-target-input'), 'debt_1');
    await userEvent.click(screen.getByTestId('reveal-create-submit'));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        '/projects/p1/reveal-policies',
        expect.objectContaining({ target_kind: 'debt', target_id: 'debt_1' }),
      ),
    );
    expect(await screen.findByTestId('reveal-row-rp_new')).toHaveTextContent('debt_1');
    // 刷新是重新拉列表，不是本地插一行
    expect(getCalls.filter((c) => c.path.includes('/reveal-policies')).length).toBe(2);
  });

  it('target_id 可从候选下拉选入（仍保留手输口子）', async () => {
    policies = [];
    candidateRows = [{ character_id: 'c_a', name: '甲' }];
    render(<RevealTab projectId="p1" />);
    await openCreateModal();

    const picker = await screen.findByTestId('reveal-target-picker');
    expect(within(picker).getByText(/甲/)).toBeInTheDocument();
    await userEvent.selectOptions(picker, 'c_a');
    expect(screen.getByTestId('reveal-target-input')).toHaveValue('c_a');
  });

  it('target_id 为空 ⇒ 禁用提交（不发空 id 的请求）', async () => {
    policies = [];
    render(<RevealTab projectId="p1" />);
    await openCreateModal();
    expect(await screen.findByTestId('reveal-create-submit')).toBeDisabled();
    expect(postMock).not.toHaveBeenCalled();
  });

  it('计划章号非法 ⇒ 禁用提交（而不是把 NaN/null 悄悄发出去让后端当「没填」）', async () => {
    policies = [];
    render(<RevealTab projectId="p1" />);
    await openCreateModal();

    await userEvent.type(screen.getByTestId('reveal-target-input'), 'c_a');
    await userEvent.type(screen.getByTestId('reveal-by-input'), '0');
    expect(await screen.findByTestId('reveal-by-error')).toBeInTheDocument();
    expect(screen.getByTestId('reveal-create-submit')).toBeDisabled();
    expect(postMock).not.toHaveBeenCalled();
  });

  it('编辑：只提交 status / revealed_chapter / notes，其余字段不进请求体', async () => {
    policies = [
      pol({ policy_id: 'rp1', target_kind: 'character', target_id: 'c_a', reveal_by_chapter: 5 }),
    ];
    render(<RevealTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('reveal-edit-rp1'));

    await userEvent.selectOptions(screen.getByTestId('reveal-edit-status'), 'revealed');
    await userEvent.type(screen.getByTestId('reveal-edit-revealed'), '9');
    await userEvent.type(screen.getByTestId('reveal-edit-notes'), '已交代');
    await userEvent.click(screen.getByTestId('reveal-edit-submit'));

    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    const [path, body] = patchMock.mock.calls[0];
    expect(path).toBe('/projects/p1/reveal-policies/rp1');
    expect(Object.keys(body).sort()).toEqual(['notes', 'revealed_chapter', 'status']);
    expect(body).toEqual({ status: 'revealed', revealed_chapter: 9, notes: '已交代' });
    expect(body).not.toHaveProperty('target_id');
    expect(body).not.toHaveProperty('audience');
    expect(body).not.toHaveProperty('reveal_by_chapter');
  });

  // 后端 service._validate_status：status='revealed' 却没有 revealed_chapter ⇒ 422。
  it('状态改「已揭开」而原行也没有章号 ⇒ 禁用保存并说明（正例：原行有值时放行）', async () => {
    policies = [pol({ policy_id: 'rp1', status: 'planned', revealed_chapter: null })];
    render(<RevealTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('reveal-edit-rp1'));

    await userEvent.selectOptions(screen.getByTestId('reveal-edit-status'), 'revealed');
    expect(await screen.findByTestId('reveal-edit-revealed-required')).toBeInTheDocument();
    expect(screen.getByTestId('reveal-edit-submit')).toBeDisabled();
    expect(patchMock).not.toHaveBeenCalled();
  });

  it('原行已有实际章号 ⇒ 改状态时不必重填（照后端「复用现有值」的口径）', async () => {
    policies = [pol({ policy_id: 'rp1', status: 'planned', revealed_chapter: 7 })];
    render(<RevealTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('reveal-edit-rp1'));

    await userEvent.selectOptions(screen.getByTestId('reveal-edit-status'), 'revealed');
    expect(screen.queryByTestId('reveal-edit-revealed-required')).not.toBeInTheDocument();
    expect(screen.getByTestId('reveal-edit-submit')).not.toBeDisabled();
  });

  // 后端 update 把 `revealed_chapter is None` 读作「不提供」⇒ 发 null 也改不掉。
  it('清空「实际揭于章号」不发 null（发了也只是「不提供」，弹窗里也写明了）', async () => {
    policies = [pol({ policy_id: 'rp1', status: 'revealed', revealed_chapter: 7 })];
    render(<RevealTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('reveal-edit-rp1'));

    expect(screen.getByTestId('reveal-edit-hint')).toHaveTextContent('不会');
    await userEvent.clear(screen.getByTestId('reveal-edit-revealed'));
    await userEvent.click(screen.getByTestId('reveal-edit-submit'));

    await waitFor(() => expect(patchMock).toHaveBeenCalled());
    expect(patchMock.mock.calls[0][1]).not.toHaveProperty('revealed_chapter');
  });

  it('删除：先二次确认，确认之前不发请求', async () => {
    policies = [pol({ policy_id: 'rp1' })];
    render(<RevealTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('reveal-delete-rp1'));

    const dialog = await screen.findByTestId('reveal-delete-confirm');
    expect(dialog).toHaveTextContent('无法恢复');
    expect(delMock).not.toHaveBeenCalled();
  });

  it('删除确认里点取消 ⇒ 关窗且不发请求', async () => {
    policies = [pol({ policy_id: 'rp1' })];
    render(<RevealTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('reveal-delete-rp1'));

    const dialog = await screen.findByTestId('reveal-delete-confirm');
    await userEvent.click(within(dialog).getByTestId('reveal-delete-confirm-cancel'));
    await waitFor(() =>
      expect(screen.queryByTestId('reveal-delete-confirm')).not.toBeInTheDocument(),
    );
    expect(delMock).not.toHaveBeenCalled();
  });

  it('删除确认 ⇒ 发 DELETE 并刷新列表', async () => {
    policies = [pol({ policy_id: 'rp1' })];
    delMock.mockImplementation(() => {
      policies = [];
      return Promise.resolve(undefined);
    });
    render(<RevealTab projectId="p1" />);
    await userEvent.click(await screen.findByTestId('reveal-delete-rp1'));

    const dialog = await screen.findByTestId('reveal-delete-confirm');
    await userEvent.click(within(dialog).getByTestId('reveal-delete-confirm-confirm'));

    await waitFor(() => expect(delMock).toHaveBeenCalledWith('/projects/p1/reveal-policies/rp1'));
    expect(await screen.findByText('还没有剧透策略')).toBeInTheDocument();
  });

  // 反向用例：诚实说明必须真的在屏上（否则作者无从知道自己踩的是哪条边界）。
  it('反向用例：两条诚实说明都渲染，且页面不作「已确认归属」的断言', async () => {
    policies = [pol({ policy_id: 'rp1' })];
    render(<RevealTab projectId="p1" />);
    await screen.findByTestId('reveal-table');

    expect(screen.getByTestId('reveal-honest-hooks')).toHaveTextContent('不是伏笔');
    expect(screen.getByTestId('reveal-honest-pid')).toHaveTextContent('忽略 URL 里的项目 id');
    expect(document.body.textContent).not.toMatch(/已确认/);
  });

  it('新建表单明说 target_id 是自由文本、目标被删后会静默变孤儿', async () => {
    policies = [];
    render(<RevealTab projectId="p1" />);
    await openCreateModal();
    const hint = await screen.findByTestId('reveal-hint-target');
    expect(hint).toHaveTextContent('没有外键');
    expect(hint).toHaveTextContent('孤儿');
  });
});
