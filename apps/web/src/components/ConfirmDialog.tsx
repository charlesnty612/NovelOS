// ConfirmDialog —— 通用确认对话框（Sprint 22「交互反馈统一」批次引入）。
//
// 替代 window.confirm 的受控组件：
// - 受控（open 由父组件持有）
// - 复用既有 modal 视觉：背景遮罩 + card（参照 ProjectsListPage 的 ProjectFormModal）
// - Esc 关闭 → onCancel
// - 默认初始焦点在「取消」按钮（危险操作不放在确认键上）
// - 危险操作（danger=true）→ 确认按钮加 btn--danger 配色（var(--color-danger)）
// - 支持自定义确认 / 取消文案
//
// 设计要点：
// - 遮罩 click 也走 onCancel（与既有 modal 一致）
// - 焦点管理：useEffect 把 cancelRef.current.focus() 放在每次 open=true 切换时；
//   在 danger 模式下额外确保焦点不在 confirm 上
// - 通过 portal-free 渲染（与既有 modal 保持一致），zIndex: 100

import { useEffect, useRef } from 'react';

export interface ConfirmDialogProps {
  open: boolean;
  /** 标题（显示在对话框顶部） */
  title: string;
  /** 正文（说明文字） */
  body: string;
  /** 确认按钮文案，默认「确认」 */
  confirmText?: string;
  /** 取消按钮文案，默认「取消」 */
  cancelText?: string;
  /** 危险态：true 时确认按钮用 btn--danger 配色 */
  danger?: boolean;
  /** 点击确认回调 */
  onConfirm: () => void;
  /** 点击取消 / Esc / 点遮罩 回调 */
  onCancel: () => void;
  /** 可选 testid（用于测试 / 区分场景） */
  testId?: string;
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmText = '确认',
  cancelText = '取消',
  danger = false,
  onConfirm,
  onCancel,
  testId,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement | null>(null);

  // 每次 open 切换到 true：默认焦点放在「取消」上。
  // 危险态下确保焦点绝对不在「确认」上（避免误按回车触发删除等不可逆操作）。
  useEffect(() => {
    if (!open) return;
    const id = window.setTimeout(() => {
      cancelRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(id);
  }, [open]);

  // Esc 关闭：仅在 open 时挂载监听
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCancel();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={testId ? `${testId}-title` : undefined}
      // 无 testId 时用 aria-label 兜底，避免读屏播空对话框
      aria-label={testId ? undefined : title}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(15,20,35,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
      }}
      onClick={onCancel}
      data-testid={testId}
    >
      <div
        className="card"
        role="document"
        style={{ width: 420, maxWidth: '90vw' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          id={testId ? `${testId}-title` : undefined}
          className="section-title"
          style={{ marginBottom: 8 }}
        >
          {title}
        </div>
        <p className="muted small" style={{ margin: '0 0 12px', lineHeight: 1.5 }}>
          {body}
        </p>
        <div
          style={{
            display: 'flex',
            gap: 8,
            justifyContent: 'flex-end',
            marginTop: 12,
          }}
        >
          <button
            ref={cancelRef}
            type="button"
            className="btn"
            onClick={onCancel}
            data-testid={testId ? `${testId}-cancel` : 'confirm-dialog-cancel'}
          >
            {cancelText}
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={danger ? 'btn btn--danger' : 'btn btn--primary'}
            onClick={onConfirm}
            data-testid={testId ? `${testId}-confirm` : 'confirm-dialog-confirm'}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}