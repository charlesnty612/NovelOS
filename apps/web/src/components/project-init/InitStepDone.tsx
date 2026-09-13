// project-init 子组件：done 视图（终态展示）。
// V4.0 前端模块化批次从 ProjectInitPanel.tsx 原样搬出（原 DonePane）。
import type { ProjectInitResponse } from '../../api/types';
import { InfoBanner } from '../ErrorBanner';

// workflow run 状态 → 作者可读中文（不再直出英文枚举）。
const RUN_STATUS_LABEL: Record<string, string> = {
  PENDING: '排队中',
  RUNNING: '运行中',
  PAUSED: '已暂停',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
};

export function InitStepDone({
  finalResp,
}: {
  finalResp: ProjectInitResponse | null;
}) {
  const status = finalResp?.status ?? '';
  return (
    <InfoBanner>
      <div data-testid="done-result">
        初始化完成（run_id={finalResp?.run_id ?? '-'}，状态：
        {RUN_STATUS_LABEL[status] ?? status ?? '—'}）。请到 Story Bible
        查看生成结果。
      </div>
    </InfoBanner>
  );
}
