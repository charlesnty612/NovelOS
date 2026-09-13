// project-init 子组件：挂起恢复横幅（仅在 idle-form 视图渲染）。
// V4.0 前端模块化批次从 ProjectInitPanel.tsx 原样搬出。
import type { WorkflowRun } from '../../api/types';
import { STAGE_LABELS } from './projectInitCore';

export interface SuspendedResumeBannerProps {
  suspendedRun: WorkflowRun;
  busy: boolean;
  onResume: () => void;
  onDismiss: () => void;
}

export function SuspendedResumeBanner({
  suspendedRun,
  busy,
  onResume,
  onDismiss,
}: SuspendedResumeBannerProps) {
  // stage 中文 label 与「第 N / M 步」直接读 checkpoint_json（list 端点不保证带 pause_payload）
  const cp = suspendedRun.checkpoint_json ?? {};
  const stage = typeof cp.stage === 'string' ? cp.stage : '';
  const stageLabel = STAGE_LABELS.find((s) => s.stage === stage)?.label ?? stage;
  const stageIndex =
    typeof cp.stage_index === 'number' && cp.stage_index >= 0
      ? cp.stage_index
      : 0;
  const stagesTotal = STAGE_LABELS.length;

  return (
    <div
      className="alert alert--info"
      role="status"
      style={{ marginBottom: 10 }}
      data-testid="suspended-resume-banner"
    >
      <div style={{ marginBottom: 6 }}>
        检测到挂起的初始化（{stageLabel || '未知关卡'} · 第 {stageIndex + 1}/{stagesTotal} 步），
        是否继续上次的审阅？
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          type="button"
          className="btn btn--sm btn--primary"
          onClick={onResume}
          disabled={busy}
          data-testid="resume-suspended"
        >
          {busy ? '加载中…' : '继续审阅'}
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={onDismiss}
          disabled={busy}
          data-testid="dismiss-suspended"
        >
          忽略
        </button>
      </div>
    </div>
  );
}
