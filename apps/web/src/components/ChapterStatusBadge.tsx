// 章节状态徽标：与 ProjectStatusBadge 一致的视觉风格，但颜色按章节状态机分级。
//
// 颜色映射（任务书给死，参考 ALLOWED_NEXT）：
//   PLANNED   = 灰 / 中性（刚创建，待规划）
//   DRAFTED   = 蓝（已写正文）
//   REVIEWED  = 橙（审校中/待审）
//   COMMITTED = 绿（已落地，等待发布）
//   RELEASED  = 深绿（已发布）
//
// Reuses 现有 .badge / .badge--* 类（见 src/index.css），新增 .badge--chapter-* 类。

import type { ChapterStatus } from '../api/types';

const LABEL: Record<ChapterStatus, string> = {
  PLANNED: '计划中',
  DRAFTED: '已写',
  REVIEWED: '已审',
  COMMITTED: '已落地',
  RELEASED: '已发布',
};

const CLASS: Record<ChapterStatus, string> = {
  PLANNED: 'badge badge--chapter-planned',
  DRAFTED: 'badge badge--chapter-drafted',
  REVIEWED: 'badge badge--chapter-reviewed',
  COMMITTED: 'badge badge--chapter-committed',
  RELEASED: 'badge badge--chapter-released',
};

export function ChapterStatusBadge({ status }: { status: ChapterStatus }) {
  return <span className={CLASS[status]}>{LABEL[status]}</span>;
}

export function WorkflowRunStatusBadge({
  status,
}: {
  status:
    | 'PENDING'
    | 'RUNNING'
    | 'PAUSED'
    | 'COMPLETED'
    | 'FAILED'
    | 'CANCELLED';
}) {
  const cls =
    status === 'RUNNING'
      ? 'badge badge--chapter-running'
      : status === 'PAUSED'
      ? 'badge badge--chapter-paused'
      : status === 'COMPLETED'
      ? 'badge badge--chapter-committed'
      : status === 'FAILED' || status === 'CANCELLED'
      ? 'badge badge--chapter-failed'
      : 'badge badge--chapter-planned';
  const label: Record<typeof status, string> = {
    PENDING: '等待',
    RUNNING: '运行中',
    PAUSED: '已暂停',
    COMPLETED: '已完成',
    FAILED: '失败',
    CANCELLED: '已取消',
  };
  return <span className={cls}>{label[status]}</span>;
}
