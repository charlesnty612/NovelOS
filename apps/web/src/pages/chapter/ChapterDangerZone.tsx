// 危险操作区（从 ChapterDetailPage 页底拆出，2026-09-13 前端批次 C 信息架构重构）。
//
// 删除章节是不可撤销操作：视觉上降级（独立分区 + 警示描边），并且必须有二次确认。
// 删除失败的错误在区内就地展示（此前完全静默，界面无任何反馈）。

import { ErrorBanner } from '../../components/ErrorBanner';

export function ChapterDangerZone({
  chapterNumber,
  deleting,
  error,
  onRequestDelete,
}: {
  chapterNumber: number;
  deleting: boolean;
  /** 删除失败的错误文案（成功路径直接跳转，不落这里） */
  error: string | null;
  /** 打开二次确认弹窗（页面用 ConfirmDialog 承载） */
  onRequestDelete: () => void;
}) {
  return (
    <div className="panel cdp-danger" data-testid="chapter-danger-zone">
      <div className="panel__title">危险操作</div>
      <p className="muted small cdp-danger__desc">
        删除第 {chapterNumber} 章会连同它的草稿、运行记录一并移除，且不可撤销。
        如果只是想重写，建议改为生成新草稿。
      </p>
      <ErrorBanner>{error}</ErrorBanner>
      <button
        className="btn btn--danger"
        disabled={deleting}
        data-testid="chapter-delete-btn"
        onClick={onRequestDelete}
      >
        {deleting ? '删除中…' : '删除本章'}
      </button>
    </div>
  );
}
