// 章节详情页的 run 编排 hook（2026-09-06 审查批次三从 ChapterDetailPage 抽出）。
//
// 职责：runs 列表加载与过滤排序、选中 run（默认最新）、RUNNING 轮询、
// 选中 run 详情（节点时间线 / pausePayload / quality_gate checkpoint 提取）。
// 页面只消费本 hook 的返回值；chapter / drafts 的刷新通过 onPollTick 回调交还页面。

import { useEffect, useMemo, useState } from 'react';
import { workflowsApi } from '../api/endpoints';
import type { QualityGateCheckpoint, WorkflowRun } from '../api/types';
import { useApiCall } from './useApiCall';
import { usePoll } from './usePoll';
import {
  isRunForChapter,
  pickActiveRun,
  pickLatestRun,
} from '../utils/chapterState';
import { extractPausePayload } from '../utils/pausePayload';

export interface UseChapterRunOrchestrationArgs {
  projectId: string;
  chapterId: string;
  /** 轮询每次拿到结果后触发（页面刷新 chapter / drafts 等外部数据） */
  onPollTick: () => void;
}

export interface ChapterRunOrchestration {
  chapterRuns: WorkflowRun[];
  selectedRunId: string | null;
  setSelectedRunId: (id: string | null) => void;
  selectedRunSummary: WorkflowRun | null;
  activeRun: WorkflowRun | null;
  poll: ReturnType<typeof usePoll<WorkflowRun>>;
  detail: ReturnType<typeof useApiCall<WorkflowRun>>;
  detailRun: WorkflowRun | null;
  pausePayload: Record<string, unknown> | undefined;
  qualityGateCheckpoint: QualityGateCheckpoint | null;
  /** 供页面 handler 在动作后刷新 runs 列表（原 runsCall.reload） */
  runsReload: () => void | Promise<void>;
  /** 供页面 handler 刷新选中 run 详情（原 detail.reload） */
  detailReload: () => void | Promise<void>;
}

export function useChapterRunOrchestration(
  args: UseChapterRunOrchestrationArgs,
): ChapterRunOrchestration {
  const { projectId, chapterId, onPollTick } = args;

  // ---- workflow runs（按 started_at DESC） ----
  const runsCall = useApiCall<WorkflowRun[]>(
    () => workflowsApi.listByProject(projectId),
    [projectId],
  );
  const chapterRuns = useMemo(
    () =>
      (runsCall.data ?? [])
        .filter((r) => isRunForChapter(r, chapterId))
        .slice()
        .sort((a, b) =>
          a.started_at < b.started_at ? 1 : a.started_at > b.started_at ? -1 : 0,
        ),
    [runsCall.data, chapterId],
  );

  // ---- 选中 run（默认最新） ----
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  // runs 重新加载后：若没选中，则选最新一条；若有但已不存在则重置
  useEffect(() => {
    if (chapterRuns.length === 0) {
      setSelectedRunId(null);
      return;
    }
    if (!selectedRunId || !chapterRuns.find((r) => r.run_id === selectedRunId)) {
      setSelectedRunId(pickLatestRun(chapterRuns, chapterId)?.run_id ?? null);
    }
  }, [chapterRuns, selectedRunId, chapterId]);

  const selectedRunSummary =
    chapterRuns.find((r) => r.run_id === selectedRunId) ?? null;

  // 详情（节点时间线）只在选中 run 后再拉
  // （声明在 poll 之前：poll.onResult 闭包经 usePoll 的 ref 转发，取到的
  // 永远是最新一次渲染的 detail.reload，与原页面内先 poll 后 detail 的写法等价。）
  const detail = useApiCall<WorkflowRun>(
    () => workflowsApi.get(selectedRunId as string),
    [selectedRunId],
  );

  // ---- 轮询选中 run：RUNNING 时 2s 拉一次，PAUSED/终态停 ----
  const activeRun = pickActiveRun(chapterRuns, chapterId);
  const pollTargetId = activeRun ? activeRun.run_id : null;
  const poll = usePoll<WorkflowRun>({
    fn: () => workflowsApi.get(pollTargetId as string),
    intervalMs: 2000,
    enabled: !!pollTargetId,
    stopWhen: (latest) => {
      if (!latest) return true;
      return latest.status !== 'RUNNING' && latest.status !== 'PENDING';
    },
    stopOnError: true,
    onResult: (latest) => {
      // 轮询到结果后同步刷新 chapter / drafts / runs
      onPollTick();
      void runsCall.reload();
      // 异步化后：run 停到 PAUSED 时，detail（checkpoint 详情，含 pause_payload）
      // 是在 RUNNING 阶段拉的、不含暂停载荷——必须重拉，审批卡才渲染得出来。
      if (latest.status === 'PAUSED') void detail.reload();
    },
  });

  const detailRun = detail.data;
  const pausePayload = extractPausePayload(detailRun?.checkpoint_json) ?? undefined;

  // V1.4：从选中 run 的 checkpoint_json 中提取 quality_gate 节点暴露字段（参照系消费
  // + 改稿引导）。无 quality_gate 节点时为 null；QualityPanel 按空态处理。
  const qualityGateCheckpoint = useMemo<QualityGateCheckpoint | null>(() => {
    const ckpt = detailRun?.checkpoint_json;
    if (!ckpt || typeof ckpt !== 'object') return null;
    const qg = (ckpt as Record<string, unknown>)['quality_gate'];
    if (!qg || typeof qg !== 'object') return null;
    const obj = qg as Record<string, unknown>;
    return {
      blocked: Boolean(obj['blocked']),
      mode: (typeof obj['mode'] === 'string' ? (obj['mode'] as string) : 'report'),
      reference_consumption:
        (obj['reference_consumption'] as QualityGateCheckpoint['reference_consumption']) ??
        { source: 'project_refs_dir', files: [], total_chars: 0, files_count: 0 },
      revision_guidance:
        (Array.isArray(obj['revision_guidance'])
          ? (obj['revision_guidance'] as QualityGateCheckpoint['revision_guidance'])
          : []) ?? [],
    };
  }, [detailRun?.checkpoint_json]);

  return {
    chapterRuns,
    selectedRunId,
    setSelectedRunId,
    selectedRunSummary,
    activeRun,
    poll,
    detail,
    detailRun,
    pausePayload,
    qualityGateCheckpoint,
    runsReload: runsCall.reload,
    detailReload: detail.reload,
  };
}
