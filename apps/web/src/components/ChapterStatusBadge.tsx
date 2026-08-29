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
  error,
}: {
  status:
    | 'PENDING'
    | 'RUNNING'
    | 'PAUSED'
    | 'COMPLETED'
    | 'FAILED'
    | 'CANCELLED';
  /** run 的 error 字段：FAILED 时用于区分「按建议驳回改稿」（rejected-for-revision）
   *  与真失败——前者是审校改稿回路的正常语义，渲染成中性徽标避免误导为红色失败。 */
  error?: string | null;
}) {
  // 后端「驳回」两态：
  //   rejected-for-revision = 按建议修改/驳回并改稿（会自动重跑 write→review）
  //   rejected              = 纯驳回（作者主动驳回，章节保持 DRAFTED）
  // 两者都用中性 badge--chapter-rejected 徽标，避免与真失败的红色 FAILED 混淆。
  // 判断顺序：rejected-for-revision 包含子串 rejected，必须先判。
  const err = error ?? '';
  const rejectedForRevision =
    status === 'FAILED' && err.includes('rejected-for-revision');
  const rejectedOnly =
    !rejectedForRevision && status === 'FAILED' && err.includes('rejected');
  const isRejected = rejectedForRevision || rejectedOnly;
  const cls = isRejected
    ? 'badge badge--chapter-rejected'
    : status === 'RUNNING'
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
  return (
    <span className={cls}>
      {rejectedForRevision ? '已驳回·改稿' : rejectedOnly ? '已驳回' : label[status]}
    </span>
  );
}
