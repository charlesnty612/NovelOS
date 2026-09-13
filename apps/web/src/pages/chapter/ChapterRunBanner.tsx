// 运行中横幅（从 ChapterDetailPage 拆出，2026-09-13 前端批次 C 信息架构重构）。
//
// V1.5：仅当存在 RUNNING/PENDING run（或 submitting 过渡期）时渲染。
// 数据源：poll.data（GET /runs/{id}，含 nodes/current_node/workflow_name/started_at），
// 退化为 activeRun（list 行，不含 nodes）。
//
// 文案三段：
//  1. 标题  ：⏳ 正在执行：{动作中文名}（{workflow_name}）
//  2. 节点  ：节点名（第 X/Y 步）· 已运行 N 秒
//  3. 提示  ：正在生成…（约需 1-3 分钟，请勿关闭）
//
// 终态（COMPLETED/FAILED/CANCELLED/PAUSED）由调用方控制不再传入 detail；FAILED 由 ErrorBanner 承载。
// 「停止工作流」按钮只负责发起请求（onRequestCancel），二次确认由页面统一用 ConfirmDialog 承载。

import { useEffect, useState } from 'react';
import type { WorkflowRun } from '../../api/types';

const WORKFLOW_ACTION_LABEL: Record<string, string> = {
  'chapter-plan': '生成计划',
  'chapter-write': '写正文',
  'chapter-review': '审校',
  'chapter-commit': '提交',
};

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s === 0 ? `${m} 分` : `${m} 分 ${s} 秒`;
}

export function ChapterRunBanner({
  detail,
  cancelPending,
  onRequestCancel,
}: {
  detail: WorkflowRun | null;
  /** 取消请求进行中：按钮禁用并显示「停止中…」 */
  cancelPending: boolean;
  /** 点击「停止工作流」——由页面弹出 ConfirmDialog（不再内联二次确认） */
  onRequestCancel: () => void;
}) {
  // 本地每秒 +1，让「已运行 X 秒」看起来在跳；started_at 没拿到时退化为 0。
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  if (!detail) {
    // 提交中、详情尚未到达：用兜底文案
    return (
      <div
        className="alert alert--info"
        data-testid="workflow-running-banner"
        role="status"
      >
        <div className="cdp-banner__title">⏳ 正在执行：工作流启动中…</div>
        <div className="muted small cdp-banner__line">
          正在生成（请勿关闭页面）。
        </div>
      </div>
    );
  }

  const actionLabel =
    WORKFLOW_ACTION_LABEL[detail.workflow_id] ??
    WORKFLOW_ACTION_LABEL[detail.workflow_name ?? ''] ??
    detail.workflow_name ??
    detail.workflow_id;
  const workflowName = detail.workflow_name ?? detail.workflow_id;

  const nodes = Array.isArray(detail.nodes) ? detail.nodes : [];

  // V1.5 横幅节点选取修正：
  //   后端仅在节点完成后才更新 current_node，导致 N+1 节点已 RUNNING 时横幅仍显示 N。
  //   这里优先取 nodes 中首个 RUNNING 节点作为展示节点；无 RUNNING 时回退到 current_node。
  const runningIdx = nodes.findIndex((n) => n.status === 'RUNNING');
  const fallbackIdx = detail.current_node
    ? nodes.findIndex((n) => n.node_id === detail.current_node)
    : -1;
  const currentIdx = runningIdx >= 0 ? runningIdx : fallbackIdx;
  const hasNodeProgress = currentIdx >= 0 && nodes.length > 0;
  const currentNodeName = hasNodeProgress ? nodes[currentIdx].node_id : null;

  // 已运行时长：优先按展示节点 started_at 起算；无节点 / 节点无 started_at 时回退到 run.started_at
  let elapsedSec = 0;
  const elapsedSource =
    (hasNodeProgress && nodes[currentIdx].started_at) || detail.started_at;
  if (elapsedSource) {
    const start = Date.parse(elapsedSource);
    if (!Number.isNaN(start)) {
      elapsedSec = Math.max(0, Math.floor((Date.now() - start) / 1000));
    }
  }
  // tick 引用进来避免 lint 警告，也保证下次 render 时 elapsedSec 会重新算
  void tick;

  const hintText = (() => {
    if (detail.status === 'PENDING') return '正在排队启动（约需 1-3 分钟，请勿关闭）';
    if (detail.workflow_id === 'chapter-write')
      return '正在生成正文草稿（约需 1-3 分钟，请勿关闭）';
    if (detail.workflow_id === 'chapter-plan')
      return '正在生成章节计划（约需 1-3 分钟，请勿关闭）';
    if (detail.workflow_id === 'chapter-review')
      return '正在执行审校（约需 1-3 分钟，请勿关闭）';
    if (detail.workflow_id === 'chapter-commit')
      return '正在提交章节（约需 1-3 分钟，请勿关闭）';
    return '正在执行（约需 1-3 分钟，请勿关闭）';
  })();

  // 取消按钮可见性：仅在 detail.status === 'RUNNING' 时展示。
  // - PENDING：未真正开始跑（不要展示「停止」按钮，避免歧义）
  // - PAUSED / 终态（COMPLETED/FAILED/CANCELLED）：banner 在父组件已不展示，此处兜底不出按钮
  const showCancelBtn = detail.status === 'RUNNING';

  return (
    <div
      className="alert alert--info"
      data-testid="workflow-running-banner"
      role="status"
      data-workflow-id={detail.workflow_id}
      data-current-node={currentNodeName ?? ''}
      data-run-id={detail.run_id}
    >
      <div className="cdp-banner">
        <div className="cdp-banner__body">
          <div className="cdp-banner__title">
            ⏳ 正在执行：{actionLabel}（{workflowName}）
          </div>
          <div className="muted small cdp-banner__line">
            {hasNodeProgress && currentNodeName
              ? `节点：${currentNodeName}（第 ${currentIdx + 1}/${nodes.length} 步）· 已运行 ${formatElapsed(elapsedSec)}`
              : `执行中 · 已运行 ${formatElapsed(elapsedSec)}`}
          </div>
          <div className="muted small cdp-banner__line">{hintText}</div>
        </div>
        {showCancelBtn ? (
          <button
            type="button"
            className="btn btn--sm btn--danger cdp-banner__action"
            data-testid="wf-cancel-btn"
            onClick={onRequestCancel}
            disabled={cancelPending}
          >
            {cancelPending ? '停止中…' : '停止工作流'}
          </button>
        ) : null}
      </div>
    </div>
  );
}
