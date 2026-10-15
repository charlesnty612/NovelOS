// 人工评分面板。
//
// 判别点按「作者会在这里被误导」排序：
// 1) **诚实说明必须常驻**——它在无报告 / 未打分 / 已打分三态都在，且不是折叠内容；
// 2) **无报告 ⇒ 表单真的 disabled**（不是只显示一句提示）；
// 3) **越界由前端夹、非整数由前端拒**——两者后端行为不同（越界静默夹取、非整数 422），
//    夹取结果还要回显到输入框，否则作者以为填的等于存下的；
// 4) 空值不发：后端对 verdict / top_issues 零校验，空串会被原样存进去。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { qualityApi } from '../../api/endpoints';
import { ApiError } from '../../api/client';
import type { QualityJudgePayload, QualityJudgeRecord, QualityReport } from '../../api/types';
import { ChapterJudgeSection, charCount, checkScore } from './ChapterJudgeSection';

vi.mock('../../api/endpoints', async () => {
  const actual = await vi.importActual<typeof import('../../api/endpoints')>(
    '../../api/endpoints',
  );
  return { ...actual, qualityApi: { latest: vi.fn(), judge: vi.fn() } };
});

const latest = qualityApi.latest as unknown as ReturnType<typeof vi.fn>;
const judge = qualityApi.judge as unknown as ReturnType<typeof vi.fn>;

type ScoreInputsFixture = { pacing: string; style: string; logic: string; dialogue: string };

function makeReport(j: QualityJudgeRecord | null = null): QualityReport {
  return {
    report_id: 'qr_1',
    project_id: 'p1',
    chapter_id: 'ch_1',
    commit_id: null,
    run_id: null,
    overall: 82,
    scores_json: {
      overall: 82,
      plot: 80,
      character: 83,
      continuity: 81,
      style: 84,
      pacing: 80,
      foreshadowing: 79,
      ai_trace: 88,
      _meta: {
        scoring_version: 'v1',
        llm_judge: 'none',
        evaluated_at: '2026-10-01T08:00:00Z',
        scoring_formula_hash: 'deadbeef',
      },
    },
    issues_json: [],
    created_at: '2026-10-01T08:00:00Z',
    judge: j,
  };
}

/** 四维 + 结论 + 一条问题全部填好（个别用例再单独覆盖某个字段）。 */
async function fillScores(
  user: ReturnType<typeof userEvent.setup>,
  v: ScoreInputsFixture = { pacing: '80', style: '75', logic: '88', dialogue: '70' },
) {
  for (const k of ['pacing', 'style', 'logic', 'dialogue'] as const) {
    await user.clear(screen.getByTestId(`judge-score-${k}`));
    await user.type(screen.getByTestId(`judge-score-${k}`), v[k]);
  }
}

function submitted(): QualityJudgePayload {
  return judge.mock.calls[0][1] as QualityJudgePayload;
}

describe('checkScore / charCount（校验口径单点）', () => {
  it('越界 → 夹进 0-100 且标 clamped；非整数 → 拒；空 → 拒', () => {
    expect(checkScore('150')).toMatchObject({ ok: true, value: 100, clamped: true });
    expect(checkScore('-5')).toMatchObject({ ok: true, value: 0, clamped: true });
    expect(checkScore('7.5')).toMatchObject({ ok: false, value: null });
    expect(checkScore('')).toMatchObject({ ok: false });
    expect(checkScore('七')).toMatchObject({ ok: false });
    expect(checkScore(' 88 ')).toMatchObject({ ok: true, value: 88, clamped: false });
  });

  it('charCount 按码点数算（emoji 不算两个字）', () => {
    expect(charCount('好好好')).toBe(3);
    expect(charCount('好🙂')).toBe(2);
  });
});

describe('ChapterJudgeSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('无报告 ⇒ 显示「先跑自动评审」且表单真的 disabled', async () => {
    latest.mockRejectedValue(new ApiError(404, 'has no quality report yet'));
    render(<ChapterJudgeSection chapterId="ch_1" />);

    expect(await screen.findByTestId('judge-no-report')).toHaveTextContent(
      '还没有评审报告',
    );
    // 判别点：不是只显示一句提示——四个输入框与提交键都在 DOM 上，且真的不可用。
    await waitFor(() => {
      expect(screen.getByTestId('judge-score-pacing')).toBeDisabled();
    });
    for (const k of ['pacing', 'style', 'logic', 'dialogue']) {
      expect(screen.getByTestId(`judge-score-${k}`)).toBeDisabled();
    }
    expect(screen.getByTestId('judge-submit')).toBeDisabled();
    expect(judge).not.toHaveBeenCalled();
  });

  it('四维渲染：四个输入都带名字、值来自已存分', async () => {
    latest.mockResolvedValue(
      makeReport({ pacing: 70, style: 80, logic: 90, dialogue: 60 }),
    );
    render(<ChapterJudgeSection chapterId="ch_1" />);

    await waitFor(() => {
      expect(screen.getByTestId('judge-score-pacing')).toHaveValue('70');
    });
    for (const k of ['pacing', 'style', 'logic', 'dialogue']) {
      expect(screen.getByTestId(`judge-score-${k}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId('judge-score-style')).toHaveValue('80');
    expect(screen.getByTestId('judge-score-logic')).toHaveValue('90');
    expect(screen.getByTestId('judge-score-dialogue')).toHaveValue('60');
    expect(screen.getByTestId('judge-saved')).toHaveTextContent('节奏 70');
  });

  it('提交成功 ⇒ 展示服务端回读的那一份分（含夹取结果）', async () => {
    latest.mockResolvedValue(makeReport(null));
    judge.mockResolvedValue({
      report_id: 'qr_1',
      chapter_id: 'ch_1',
      judge: { pacing: 80, style: 75, logic: 88, dialogue: 70, verdict: '收尾再紧一点' },
    });
    const user = userEvent.setup();
    render(<ChapterJudgeSection chapterId="ch_1" />);

    await waitFor(() => expect(screen.getByTestId('judge-submit')).toBeEnabled());
    await fillScores(user);
    await user.type(screen.getByTestId('judge-verdict'), '收尾再紧一点');
    await user.click(screen.getByTestId('judge-submit'));

    expect(await screen.findByTestId('judge-saved')).toHaveTextContent('节奏 80');
    expect(screen.getByTestId('judge-saved')).toHaveTextContent('收尾再紧一点');
    expect(judge).toHaveBeenCalledWith('ch_1', {
      pacing: 80,
      style: 75,
      logic: 88,
      dialogue: 70,
      verdict: '收尾再紧一点',
    });
  });

  it('越界 ⇒ 前端夹到 0-100 后再发，且输入框回显夹后的值', async () => {
    latest.mockResolvedValue(makeReport(null));
    judge.mockResolvedValue({
      report_id: 'qr_1',
      chapter_id: 'ch_1',
      judge: { pacing: 100, style: 0, logic: 50, dialogue: 50 },
    });
    const user = userEvent.setup();
    render(<ChapterJudgeSection chapterId="ch_1" />);

    await waitFor(() => expect(screen.getByTestId('judge-submit')).toBeEnabled());
    await fillScores(user, { pacing: '150', style: '-5', logic: '50', dialogue: '50' });
    await user.click(screen.getByTestId('judge-submit'));

    await waitFor(() => expect(judge).toHaveBeenCalled());
    expect(submitted().pacing).toBe(100);
    expect(submitted().style).toBe(0);
    // 回显：作者看见的必须是存下去的那个数，不是自己敲的。
    expect(screen.getByTestId('judge-score-pacing')).toHaveValue('100');
    expect(screen.getByTestId('judge-score-style')).toHaveValue('0');
  });

  it('非整数 ⇒ 前端拦住，不发请求（后端会 422，白跑一趟）', async () => {
    latest.mockResolvedValue(makeReport(null));
    const user = userEvent.setup();
    render(<ChapterJudgeSection chapterId="ch_1" />);

    await waitFor(() => expect(screen.getByTestId('judge-submit')).toBeEnabled());
    await fillScores(user, { pacing: '80', style: '7.5', logic: '88', dialogue: '70' });
    await user.click(screen.getByTestId('judge-submit'));

    expect(await screen.findByTestId('judge-err-style')).toHaveTextContent('只能是整数');
    expect(judge).not.toHaveBeenCalled();
  });

  it('verdict 超 50 字 ⇒ 拦下并说清超了多少', async () => {
    latest.mockResolvedValue(makeReport(null));
    const user = userEvent.setup();
    render(<ChapterJudgeSection chapterId="ch_1" />);

    await waitFor(() => expect(screen.getByTestId('judge-submit')).toBeEnabled());
    await fillScores(user);
    await user.type(screen.getByTestId('judge-verdict'), '一'.repeat(51));
    await user.click(screen.getByTestId('judge-submit'));

    expect(await screen.findByRole('alert')).toHaveTextContent('最多 50 字');
    expect(judge).not.toHaveBeenCalled();
  });

  it('每条问题超 30 字 ⇒ 拦下', async () => {
    latest.mockResolvedValue(makeReport(null));
    const user = userEvent.setup();
    render(<ChapterJudgeSection chapterId="ch_1" />);

    await waitFor(() => expect(screen.getByTestId('judge-submit')).toBeEnabled());
    await fillScores(user);
    await user.type(screen.getByTestId('judge-issue-0'), '长'.repeat(31));
    await user.click(screen.getByTestId('judge-submit'));

    expect(await screen.findByRole('alert')).toHaveTextContent('每条问题最多 30 字');
    expect(judge).not.toHaveBeenCalled();
  });

  it('结论与问题都留空 ⇒ 这两个键根本不发出去', async () => {
    latest.mockResolvedValue(makeReport(null));
    judge.mockResolvedValue({
      report_id: 'qr_1',
      chapter_id: 'ch_1',
      judge: { pacing: 80, style: 75, logic: 88, dialogue: 70 },
    });
    const user = userEvent.setup();
    render(<ChapterJudgeSection chapterId="ch_1" />);

    await waitFor(() => expect(screen.getByTestId('judge-submit')).toBeEnabled());
    await fillScores(user);
    await user.click(screen.getByTestId('judge-submit'));

    await waitFor(() => expect(judge).toHaveBeenCalled());
    expect(submitted()).not.toHaveProperty('verdict');
    expect(submitted()).not.toHaveProperty('top_issues');
  });

  it('加载失败 ⇒ 错误可见，且**仍然不给表单**（不把作者送进必失败的 422）', async () => {
    latest.mockRejectedValue(new ApiError(500, 'db exploded'));
    render(<ChapterJudgeSection chapterId="ch_1" />);

    expect(await screen.findByText(/db exploded/)).toBeInTheDocument();
    expect(screen.queryByTestId('judge-score-pacing')).not.toBeInTheDocument();
    expect(screen.queryByTestId('judge-submit')).not.toBeInTheDocument();
    // 「没报告」那套说辞不该冒充错误态
    expect(screen.queryByTestId('judge-no-report')).not.toBeInTheDocument();
  });

  // 反向用例：诚实说明不是「有内容时才出现」的一行小字。三个态都得在，且必须
  // 明说「不影响提交 / 不能放行拦截」——这是作者唯一会误读的地方。
  it('诚实说明：无报告 / 未打分 / 已打分 三态都渲染，且写明不解锁门禁', async () => {
    latest.mockRejectedValue(new ApiError(404, 'nope'));
    const { unmount } = render(<ChapterJudgeSection chapterId="ch_1" />);
    let note = await screen.findByTestId('judge-honest-note');
    expect(note).toHaveTextContent('提交审核根本不看这一格');
    expect(note).toHaveTextContent('不会放行任何一条拦截');
    expect(note).toHaveTextContent('覆盖上一次');
    unmount();

    latest.mockResolvedValue(makeReport(null));
    render(<ChapterJudgeSection chapterId="ch_1" />);
    note = await screen.findByTestId('judge-honest-note');
    expect(note).toHaveTextContent('不会放行任何一条拦截');
    expect(screen.getByTestId('judge-unsaved')).toBeInTheDocument();
  });

  it('诚实说明：已打分后依然在（不因为"已经有了"就撤掉）', async () => {
    latest.mockResolvedValue(makeReport({ pacing: 1, style: 2, logic: 3, dialogue: 4 }));
    render(<ChapterJudgeSection chapterId="ch_1" />);

    const note = await screen.findByTestId('judge-honest-note');
    expect(note).toBeInTheDocument();
    expect(note).toHaveTextContent('不会放行任何一条拦截');
  });

  it('onJudged 回调拿到服务端回读的那份（父页面据此刷新自己的报告副本）', async () => {
    const stored: QualityJudgeRecord = { pacing: 80, style: 75, logic: 88, dialogue: 70 };
    latest.mockResolvedValue(makeReport(null));
    judge.mockResolvedValue({ report_id: 'qr_1', chapter_id: 'ch_1', judge: stored });
    const onJudged = vi.fn();
    const user = userEvent.setup();
    render(<ChapterJudgeSection chapterId="ch_1" onJudged={onJudged} />);

    await waitFor(() => expect(screen.getByTestId('judge-submit')).toBeEnabled());
    await fillScores(user);
    await user.click(screen.getByTestId('judge-submit'));

    await waitFor(() => expect(onJudged).toHaveBeenCalledWith(stored));
  });
});
