import type { ProjectStatus } from '../api/types';

const LABEL: Record<ProjectStatus, string> = {
  ACTIVE: '进行中',
  PAUSED: '已暂停',
  ARCHIVED: '已归档',
};

export function StatusBadge({ status }: { status: ProjectStatus }) {
  const cls =
    status === 'ACTIVE'
      ? 'badge badge--active'
      : status === 'PAUSED'
      ? 'badge badge--paused'
      : 'badge badge--archived';
  return <span className={cls}>{LABEL[status]}</span>;
}
