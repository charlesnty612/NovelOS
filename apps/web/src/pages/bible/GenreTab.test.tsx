// GenreTab 组件测试（题材库 P3b）：
// - 绑定展示：当前绑定卡片渲染名称 / 题材 / 版本 / 爽点型数量 + payoff_types 表格；
// - 未绑定空态：EmptyState + 爽点空段提示；
// - issues 列表：最近一次 chapter-review run 的 pause_payload.review_report.genre_check
//   → rule_id / message 渲染（warning 色系）；
// - 解绑调用：点击「解绑」调 genreApi.unbind(pid)，并回落到未绑定空态；
// - 绑定调用：下拉选择 → 点击「绑定」调 genreApi.bind(pid, pack_id)。

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../api/endpoints', () => {
  return {
    genreApi: {
      listPacks: vi.fn(),
      getPack: vi.fn(),
      getBinding: vi.fn(),
      bind: vi.fn(),
      unbind: vi.fn(),
    },
    workflowsApi: {
      listByProject: vi.fn(),
      get: vi.fn(),
    },
  };
});

import { genreApi, workflowsApi } from '../../api/endpoints';
import { GenreTab } from './GenreTab';

const PACK = {
  pack_id: 'genre-male-quicktrans-v1',
  name: '男主快穿',
  genre_tag: '快穿',
  version: 3,
  payload: {
    schema_version: 'genre-pack.v1.0.0',
    payoff_types: [
      {
        type_id: 'face_slap',
        name: '打脸',
        strength: 'S',
        density_cap: '每卷 2~3 次',
      },
      {
        type_id: 'reveal_cape',
        name: '马甲掉落',
        strength: 'M',
        density_cap: '每 5 章 ≤1 次',
      },
    ],
  },
  source_path: 'genres/male-quicktrans',
  created_at: '2026-09-13T10:00:00',
  updated_at: '2026-09-13T10:00:00',
  bound_project_count: 1,
};

const BOUND = {
  project_id: 'p1',
  pack_id: PACK.pack_id,
  bound: true,
  pack: PACK,
};

const UNBOUND = {
  project_id: 'p1',
  pack_id: null,
  bound: false,
  pack: null,
};

const PACK_SUMMARY = {
  pack_id: PACK.pack_id,
  name: PACK.name,
  genre_tag: PACK.genre_tag,
  version: PACK.version,
  source_path: PACK.source_path,
  created_at: PACK.created_at,
  updated_at: PACK.updated_at,
  payoff_type_count: 2,
  structure_model: '单元剧',
  chapter_words_target: 2500,
  bound_project_count: 1,
};

/** chapter-review run：pause_payload 走 checkpoint_json 的节点键（后端约定）。 */
const REVIEW_RUN = {
  run_id: 'wfr_review_001',
  workflow_id: 'chapter-review',
  chapter_id: 'ch_001',
  status: 'PAUSED',
  current_node: 'author_review',
  checkpoint_json: {
    author_review: {
      __pause_payload__: {
        stage: 'author_review',
        review_report: {
          genre_check: {
            bound: true,
            checked: true,
            pack_id: PACK.pack_id,
            pack_version: 3,
            issues: [
              {
                rule_id: 'GENRE-RATIO-DEVIATION',
                severity: 'warning',
                category: 'payoff',
                message: '配比总偏离 32%（action 声明 70%）',
                suggestion: '提高 action 场景字数占比',
              },
              {
                rule_id: 'GENRE-REDLINE-HIT',
                severity: 'warning',
                category: 'pacing',
                message: '命中节奏红线：连续 3 章无小爽点',
              },
            ],
            issue_count: 2,
            rule_ids: ['GENRE-RATIO-DEVIATION', 'GENRE-REDLINE-HIT'],
            skipped: [],
            error: null,
          },
        },
      },
    },
  },
  error: null,
  retry_count: 0,
  started_at: '2026-09-13T11:00:00',
  ended_at: null,
  nodes: [],
};

const m = <T,>(fn: T) => fn as unknown as ReturnType<typeof vi.fn>;

describe('GenreTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    m(genreApi.getBinding).mockResolvedValue(BOUND);
    m(genreApi.listPacks).mockResolvedValue([PACK_SUMMARY]);
    m(workflowsApi.listByProject).mockResolvedValue([]);
  });

  it('绑定态：展示名称 / 题材 / 版本 / 爽点型数量 + payoff_types 表格', async () => {
    render(<GenreTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getByTestId('genre-binding-info')).toBeInTheDocument();
    });
    expect(screen.getByTestId('genre-binding-name')).toHaveTextContent('男主快穿');
    expect(screen.getByTestId('genre-binding-tag')).toHaveTextContent('快穿');
    expect(screen.getByTestId('genre-binding-version')).toHaveTextContent('v3');
    expect(screen.getByTestId('genre-binding-payoff-count')).toHaveTextContent('2');

    const rows = screen.getAllByTestId('genre-payoff-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('face_slap');
    expect(rows[0]).toHaveTextContent('打脸');
    expect(rows[0]).toHaveTextContent('S');
    expect(rows[0]).toHaveTextContent('每卷 2~3 次');
    expect(rows[1]).toHaveTextContent('马甲掉落');
  });

  it('未绑定：空态提示 + 爽点空段', async () => {
    m(genreApi.getBinding).mockResolvedValue(UNBOUND);
    render(<GenreTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getByText('尚未绑定题材包')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('genre-binding-info')).toBeNull();
    expect(screen.getByTestId('genre-payoff-panel')).toHaveTextContent(
      '当前题材包未声明 payoff_types',
    );
  });

  it('genre_check：渲染最近一次审校的 issues（rule_id + message）', async () => {
    m(workflowsApi.listByProject).mockResolvedValue([REVIEW_RUN]);
    render(<GenreTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getAllByTestId('genre-check-issue')).toHaveLength(2);
    });
    const issues = screen.getAllByTestId('genre-check-issue');
    expect(issues[0]).toHaveTextContent('GENRE-RATIO-DEVIATION');
    expect(issues[0]).toHaveTextContent('配比总偏离 32%');
    expect(issues[0]).toHaveTextContent('提高 action 场景字数占比');
    expect(issues[1]).toHaveTextContent('GENRE-REDLINE-HIT');
    // warning 色系（--color-warn）
    expect(issues[0].getAttribute('style')).toContain('var(--color-warn)');
  });

  it('无 genre_check 段：空态提示（不报错）', async () => {
    m(workflowsApi.listByProject).mockResolvedValue([
      {
        ...REVIEW_RUN,
        checkpoint_json: {
          author_review: { __pause_payload__: { stage: 'author_review', review_report: {} } },
        },
      },
    ]);
    render(<GenreTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getByTestId('genre-check-empty')).toBeInTheDocument();
    });
    expect(screen.getByTestId('genre-check-empty')).toHaveTextContent(
      '没有产出 genre_check 段',
    );
    expect(screen.queryAllByTestId('genre-check-issue')).toHaveLength(0);
  });

  it('点击「解绑」调 genreApi.unbind 并回落到未绑定空态', async () => {
    m(genreApi.unbind).mockResolvedValue(UNBOUND);
    render(<GenreTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getByTestId('genre-unbind')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId('genre-unbind'));

    await waitFor(() => {
      expect(genreApi.unbind).toHaveBeenCalledWith('p1');
    });
    await waitFor(() => {
      expect(screen.getByText('尚未绑定题材包')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('genre-binding-info')).toBeNull();
  });

  it('下拉选择后点击「绑定」调 genreApi.bind(pid, pack_id)', async () => {
    m(genreApi.getBinding).mockResolvedValue(UNBOUND);
    m(genreApi.bind).mockResolvedValue(BOUND);
    render(<GenreTab projectId="p1" />);

    await waitFor(() => {
      expect(screen.getByTestId('genre-pack-select')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByTestId('genre-pack-select')).toHaveTextContent('男主快穿');
    });
    await userEvent.selectOptions(
      screen.getByTestId('genre-pack-select'),
      PACK.pack_id,
    );
    await userEvent.click(screen.getByTestId('genre-bind'));

    await waitFor(() => {
      expect(genreApi.bind).toHaveBeenCalledWith('p1', PACK.pack_id);
    });
    await waitFor(() => {
      expect(screen.getByTestId('genre-binding-info')).toBeInTheDocument();
    });
    expect(screen.getByTestId('genre-binding-version')).toHaveTextContent('v3');
  });
});
