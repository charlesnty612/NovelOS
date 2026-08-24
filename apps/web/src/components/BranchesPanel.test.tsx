import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BranchesPanel } from './BranchesPanel';
import { ApiError } from '../api/client';
import type { Branch } from '../api/types';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    branchesApi: {
      list: vi.fn(),
      create: vi.fn(),
      promote: vi.fn(),
    },
    storyStateApi: {
      ...((actual.storyStateApi as unknown) as object),
      getCurrent: vi.fn(),
      getBranchState: vi.fn(),
    },
  };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { branchesApi, storyStateApi } = await import('../api/endpoints');

const baseBranches: Branch[] = [
  {
    branch_id: 'br_main',
    project_id: 'prj_1',
    name: 'main',
    parent_branch_id: null,
    base_state_version: 0,
    status: 'ACTIVE',
    created_at: '2026-08-24T09:00:00Z',
  },
  {
    branch_id: 'br_exp1',
    project_id: 'prj_1',
    name: 'experiment-red-king',
    parent_branch_id: 'br_main',
    base_state_version: 5,
    status: 'ACTIVE',
    created_at: '2026-08-24T10:00:00Z',
  },
  {
    branch_id: 'br_sim',
    project_id: 'prj_1',
    name: 'sim-20260824T110000Z',
    parent_branch_id: 'br_main',
    base_state_version: 5,
    status: 'ARCHIVED',
    created_at: '2026-08-24T11:00:00Z',
  },
];

describe('BranchesPanel (V1.5 / Sprint 17)', () => {
  beforeEach(() => {
    vi.mocked(branchesApi.list).mockReset();
    vi.mocked(branchesApi.create).mockReset();
    vi.mocked(branchesApi.promote).mockReset();
    vi.mocked(storyStateApi.getCurrent).mockReset();
    vi.mocked(storyStateApi.getBranchState).mockReset();
  });

  it('renders empty state when no branches', () => {
    render(<BranchesPanel projectId="prj_1" initialBranches={[]} />);
    expect(screen.getByTestId('branches-panel')).toBeInTheDocument();
    expect(screen.getByTestId('branches-empty')).toHaveTextContent(
      /该项目暂无分支/,
    );
  });

  it('renders all branches with name/status/promote button only for ACTIVE', () => {
    render(
      <BranchesPanel projectId="prj_1" initialBranches={baseBranches} />,
    );
    // main 分支不渲染（不可 promote / 不可创建同名的保留分支）
    expect(
      screen.queryByTestId('branches-item-br_main'),
    ).not.toBeInTheDocument();
    // 其余 2 个分支
    expect(
      screen.getByTestId('branches-item-br_exp1'),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId('branches-item-br_sim'),
    ).toBeInTheDocument();
    // 名称
    expect(
      screen.getByTestId('branches-name-br_exp1'),
    ).toHaveTextContent('experiment-red-king');
    // promote 按钮仅 ACTIVE 出现
    expect(
      screen.getByTestId('branches-promote-br_exp1'),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId('branches-promote-br_sim'),
    ).not.toBeInTheDocument();
  });

  it('disables create button when name empty or "main"', () => {
    render(<BranchesPanel projectId="prj_1" initialBranches={[]} />);
    const submit = screen.getByTestId('branches-create-submit');
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByTestId('branches-create-name'), {
      target: { value: 'main' },
    });
    // 'main' 仍然被禁用（保留名）
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByTestId('branches-create-name'), {
      target: { value: 'feature-x' },
    });
    expect(submit).not.toBeDisabled();
  });

  it('calls create api on submit and reloads', async () => {
    vi.mocked(branchesApi.create).mockResolvedValue({
      branch_id: 'br_new',
      project_id: 'prj_1',
      name: 'feature-x',
      parent_branch_id: 'br_main',
      base_state_version: 7,
      status: 'ACTIVE',
      created_at: '2026-08-24T12:00:00Z',
    });
    vi.mocked(branchesApi.list).mockResolvedValue([
      ...baseBranches,
      {
        branch_id: 'br_new',
        project_id: 'prj_1',
        name: 'feature-x',
        parent_branch_id: 'br_main',
        base_state_version: 7,
        status: 'ACTIVE',
        created_at: '2026-08-24T12:00:00Z',
      },
    ]);

    render(<BranchesPanel projectId="prj_1" initialBranches={[]} />);
    fireEvent.change(screen.getByTestId('branches-create-name'), {
      target: { value: 'feature-x' },
    });
    fireEvent.click(screen.getByTestId('branches-create-submit'));

    await waitFor(() => {
      expect(branchesApi.create).toHaveBeenCalledWith('prj_1', {
        name: 'feature-x',
      });
    });
    await waitFor(() => {
      expect(branchesApi.list).toHaveBeenCalledWith('prj_1');
    });
    // 新分支渲染
    expect(
      screen.getByTestId('branches-name-br_new'),
    ).toHaveTextContent('feature-x');
  });

  it('shows 409 error when create hits branch_name_conflict', async () => {
    vi.mocked(branchesApi.create).mockRejectedValue(
      new ApiError(409, JSON.stringify({ error: 'branch_name_conflict' })),
    );
    render(<BranchesPanel projectId="prj_1" initialBranches={[]} />);
    fireEvent.change(screen.getByTestId('branches-create-name'), {
      target: { value: 'experiment-red-king' },
    });
    fireEvent.click(screen.getByTestId('branches-create-submit'));

    await waitFor(() => {
      expect(screen.getByText(/409:/)).toBeInTheDocument();
    });
  });

  it('promote success path: shows result banner and reloads', async () => {
    vi.mocked(branchesApi.promote).mockResolvedValue({
      commit_id: 'cmt_p1',
      state_version: 8,
      delta_id: 'dl_p1',
      promoted_from: 'br_exp1',
      promoted_commits: 2,
      branch_id: 'br_main',
      replayed_delta_ids: ['dl_p1', 'dl_p2'],
    });
    vi.mocked(branchesApi.list).mockResolvedValue([
      baseBranches[0], // main（面板会过滤）
      { ...baseBranches[1], status: 'MERGED' },
      baseBranches[2],
    ]);

    render(
      <BranchesPanel projectId="prj_1" initialBranches={baseBranches} />,
    );
    fireEvent.click(screen.getByTestId('branches-promote-br_exp1'));

    await waitFor(() => {
      expect(branchesApi.promote).toHaveBeenCalledWith(
        'prj_1',
        'br_exp1',
        {},
      );
    });
    await waitFor(() => {
      expect(
        screen.getByTestId('branches-promote-result-br_exp1'),
      ).toHaveTextContent(/已 promote「experiment-red-king」：重放 2 条 delta/);
    });
  });

  it('promote 409 (branch_closed) shows error banner', async () => {
    vi.mocked(branchesApi.promote).mockRejectedValue(
      new ApiError(
        409,
        JSON.stringify({
          error: 'branch_closed',
          message: 'branch is MERGED',
          branch_id: 'br_exp1',
        }),
      ),
    );

    render(
      <BranchesPanel projectId="prj_1" initialBranches={baseBranches} />,
    );
    fireEvent.click(screen.getByTestId('branches-promote-br_exp1'));

    await waitFor(() => {
      expect(
        screen.getByTestId('branches-promote-error-br_exp1'),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId('branches-promote-error-br_exp1'),
    ).toHaveTextContent(/branch_closed/);
  });

  it('toggle: expand shows state panel; collapse hides it', async () => {
    vi.mocked(storyStateApi.getBranchState).mockResolvedValue({
      state_version: 6,
      characters: { characters: [] },
      world: {},
      hooks: [],
      debts: [],
      events: [],
      recent_events: [],
    } as never);
    vi.mocked(storyStateApi.getCurrent).mockResolvedValue({
      state_version: 5,
      characters: { characters: [] },
      world: {},
      hooks: [],
      debts: [],
      events: [],
      recent_events: [],
    } as never);

    render(
      <BranchesPanel projectId="prj_1" initialBranches={baseBranches} />,
    );
    fireEvent.click(screen.getByTestId('branches-toggle-br_exp1'));

    await waitFor(() => {
      expect(
        screen.getByTestId('branches-state-panel-br_exp1'),
      ).toBeInTheDocument();
    });
    expect(storyStateApi.getBranchState).toHaveBeenCalledWith(
      'prj_1',
      'br_exp1',
    );

    // 再次点击 → 收起
    fireEvent.click(screen.getByTestId('branches-toggle-br_exp1'));
    await waitFor(() => {
      expect(
        screen.queryByTestId('branches-state-panel-br_exp1'),
      ).not.toBeInTheDocument();
    });
  });
});