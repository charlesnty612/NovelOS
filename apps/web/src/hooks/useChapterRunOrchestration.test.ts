/**
 * useChapterRunOrchestration 专属测试（M-21 补口）。
 *
 * 此前本 hook（148 行，管 runs 过滤/选中、2s 轮询、PAUSED 时重拉 detail、
 * quality_gate checkpoint 提取）**零专属测试** ⇒ 审批卡能不能渲染、门禁报告
 * 能不能显示，全靠父页集成测试间接兜。本文件把它从整页集成里剥出来独立测。
 *
 * 三组判别：
 *   1. runs 过滤 → 默认选最新 → 选中切换；
 *   2. 轮询到 PAUSED 时 detail 被重拉（审批卡靠它渲染）——**删掉那行必红**；
 *   3. qualityGateCheckpoint 提取（有/无、mode 缺省、revision_guidance 降级）。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useChapterRunOrchestration } from './useChapterRunOrchestration';
import { workflowsApi } from '../api/endpoints';
import type { WorkflowRun } from '../api/types';

vi.mock('../api/endpoints', () => ({
  workflowsApi: {
    listByProject: vi.fn(),
    get: vi.fn(),
  },
}));

function makeRun(over: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    run_id: 'wfr_a',
    workflow_id: 'wf_opaque',
    workflow_name: 'chapter-write',
    chapter_id: 'ch_001',
    status: 'RUNNING',
    current_node: 'writer',
    checkpoint_json: null,
    nodes: [],
    error: null,
    retry_count: 0,
    started_at: '2026-10-01T00:00:00+00:00',
    ended_at: null,
    ...over,
  } as unknown as WorkflowRun;
}

/** 轮询挂掉的 run（无 active run ⇒ poll 不启动，测试不必等 2s） */
const TERMINAL_RUN = makeRun({ status: 'COMPLETED', ended_at: '2026-10-01T00:05:00+00:00' });

describe('useChapterRunOrchestration', () => {
  beforeEach(() => {
    vi.mocked(workflowsApi.listByProject).mockReset();
    vi.mocked(workflowsApi.get).mockReset();
    vi.mocked(workflowsApi.listByProject).mockResolvedValue([]);
    vi.mocked(workflowsApi.get).mockResolvedValue(TERMINAL_RUN);
  });

  function renderHookFor(chapterId = 'ch_001') {
    return renderHook(() =>
      useChapterRunOrchestration({
        projectId: 'prj_001',
        chapterId,
        onPollTick: () => {},
      }),
    );
  }

  // ---------------------------------------------------------------------------
  // 1. runs 过滤 / 默认选中 / 选中切换
  // ---------------------------------------------------------------------------
  describe('runs 列表过滤与选中', () => {
    it('只保留本章的 run，并按 started_at DESC 排序；默认选中最新一条', async () => {
      vi.mocked(workflowsApi.listByProject).mockResolvedValue([
        makeRun({ run_id: 'wfr_old', chapter_id: 'ch_001', started_at: '2026-10-01T00:00:00+00:00' }),
        makeRun({ run_id: 'wfr_new', chapter_id: 'ch_001', started_at: '2026-10-02T00:00:00+00:00' }),
        makeRun({ run_id: 'wfr_other', chapter_id: 'ch_002', started_at: '2026-10-03T00:00:00+00:00' }),
      ]);

      const { result } = renderHookFor('ch_001');

      await waitFor(() => expect(result.current.chapterRuns).toHaveLength(2));
      // 排序：started_at DESC ⇒ 新的在前
      expect(result.current.chapterRuns.map((r) => r.run_id)).toEqual(['wfr_new', 'wfr_old']);
      // 默认选中最新一条
      await waitFor(() => expect(result.current.selectedRunId).toBe('wfr_new'));
      expect(result.current.selectedRunSummary?.run_id).toBe('wfr_new');
    });

    it('setSelectedRunId 能切换选中项，selectedRunSummary 跟着换', async () => {
      vi.mocked(workflowsApi.listByProject).mockResolvedValue([
        makeRun({ run_id: 'wfr_old', chapter_id: 'ch_001', started_at: '2026-10-01T00:00:00+00:00' }),
        makeRun({ run_id: 'wfr_new', chapter_id: 'ch_001', started_at: '2026-10-02T00:00:00+00:00' }),
      ]);

      const { result } = renderHookFor('ch_001');
      await waitFor(() => expect(result.current.selectedRunId).toBe('wfr_new'));

      act(() => {
        result.current.setSelectedRunId('wfr_old');
      });

      expect(result.current.selectedRunId).toBe('wfr_old');
      expect(result.current.selectedRunSummary?.run_id).toBe('wfr_old');
    });
  });

  // ---------------------------------------------------------------------------
  // 2. PAUSED ⇒ detail 重拉（审批卡的生命线）
  // ---------------------------------------------------------------------------
  describe('轮询到 PAUSED 时的 detail 重拉', () => {
    it('还没选中 run 时**不请求** detail（不出现 GET /runs/null）', async () => {
      // T7：此前 useApiCall 无条件 fn()，挂载即用 `null as string` 去拉
      // ⇒ 一次注定 404 的请求，且那个 404 会落进 detailError 显示成误导性错误。
      vi.mocked(workflowsApi.listByProject).mockResolvedValue([]);
      const { result } = renderHookFor('ch_001');

      await waitFor(() => expect(result.current.chapterRuns).toHaveLength(0));
      expect(vi.mocked(workflowsApi.get).mock.calls.length).toBe(0); // 一次都没请求
      expect(result.current.detailRun).toBeNull(); // 没有选中 ⇒ 没有详情
    });

    it('轮询拿到 PAUSED 后 detail 被重拉，pausePayload 随之出现（RUNNING 阶段那份没有）', async () => {
      const running = makeRun({
        run_id: 'wfr_p',
        chapter_id: 'ch_001',
        status: 'RUNNING',
        checkpoint_json: {},
      });
      const paused = makeRun({
        run_id: 'wfr_p',
        chapter_id: 'ch_001',
        status: 'PAUSED',
        current_node: 'author_review',
        checkpoint_json: {
          author_review: {
            __pause_payload__: { stage: 'chapter-review', message: '请人工决议' },
          },
        },
      });

      vi.mocked(workflowsApi.listByProject).mockResolvedValue([running]);
      // 前两次取数（轮询首 tick + detail 首拉）都给 RUNNING 版；之后给 PAUSED 版。
      // ⇒ 只有「PAUSED 后重拉了 detail」才会让 pausePayload 出现。
      let realCalls = 0;
      vi.mocked(workflowsApi.get).mockImplementation(async (id: string) => {
        if (!id) return running; // 理论上不该再出现空 id（T7 已修）
        realCalls += 1;
        return realCalls <= 2 ? running : paused;
      });

      const { result } = renderHookFor('ch_001');

      // 选中 run 之后：detail 拉到 RUNNING 版 ⇒ 没有暂停载荷
      await waitFor(() => expect(result.current.detailRun?.status).toBe('RUNNING'));
      expect(result.current.pausePayload).toBeUndefined();

      // 轮询（2s 一次）拿到 PAUSED ⇒ 触发 detail.reload() ⇒ 暂停载荷出现
      await waitFor(() => expect(result.current.pausePayload).toBeDefined(), { timeout: 8000 });
      expect(result.current.pausePayload?.['stage']).toBe('chapter-review');
      expect(result.current.detailRun?.status).toBe('PAUSED');
    }, 20000);
  });

  // ---------------------------------------------------------------------------
  // 3. qualityGateCheckpoint 提取
  // ---------------------------------------------------------------------------
  describe('qualityGateCheckpoint 提取', () => {
    async function renderWithCheckpoint(ckpt: Record<string, unknown> | undefined) {
      vi.mocked(workflowsApi.listByProject).mockResolvedValue([TERMINAL_RUN]);
      vi.mocked(workflowsApi.get).mockResolvedValue(
        makeRun({ status: 'COMPLETED', checkpoint_json: ckpt }),
      );
      const rendered = renderHookFor('ch_001');
      await waitFor(() => expect(rendered.result.current.detailRun).not.toBeNull());
      return rendered;
    }

    it('checkpoint_json.quality_gate 有值时按字段提取', async () => {
      const { result } = await renderWithCheckpoint({
        quality_gate: {
          blocked: true,
          mode: 'enforce',
          reference_consumption: {
            source: 'project_refs_dir',
            files: ['ref-a.md'],
            total_chars: 42,
            files_count: 1,
          },
          revision_guidance: [
            {
              dimension: 'structure',
              score: 3,
              threshold: 4,
              top_issues: [],
              rule_hint: '加强转折',
            },
          ],
        },
      });

      const qg = result.current.qualityGateCheckpoint;
      expect(qg).not.toBeNull();
      expect(qg?.blocked).toBe(true);
      expect(qg?.mode).toBe('enforce');
      expect(qg?.reference_consumption.files).toEqual(['ref-a.md']);
      expect(qg?.revision_guidance).toHaveLength(1);
      expect(qg?.revision_guidance[0]?.rule_hint).toBe('加强转折');
    });

    it('没有 quality_gate 节点时为 null（QualityPanel 走空态）', async () => {
      const { result } = await renderWithCheckpoint({ author_review: { foo: 1 } });
      expect(result.current.qualityGateCheckpoint).toBeNull();
    });

    it('mode 缺省时降级为 report', async () => {
      const { result } = await renderWithCheckpoint({ quality_gate: { blocked: false } });
      expect(result.current.qualityGateCheckpoint?.mode).toBe('report');
    });

    it('revision_guidance 非数组时降级为空数组（不炸页）', async () => {
      const { result } = await renderWithCheckpoint({
        quality_gate: { blocked: false, mode: 'report', revision_guidance: '不是数组' },
      });
      expect(result.current.qualityGateCheckpoint?.revision_guidance).toEqual([]);
    });
  });
});
