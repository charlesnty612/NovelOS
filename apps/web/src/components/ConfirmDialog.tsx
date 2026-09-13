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
// V3.10「交互完整」：Esc / 初始焦点 / 焦点循环 / 关闭归还焦点 / 锁 body 滚动
// 统一交给 components/Modal.tsx 的 useModalFocus，避免与 Modal 各写一份。
//
// 设计要点：
// - 遮罩 click 也走 onCancel（与既有 modal 一致）
// - DOM 结构保持不变：role="dialog" 与 testId 落在遮罩元素上（既有调用方与测试
//   依赖该契约），useModalFocus 的容器同样是该遮罩，故 Tab 焦点循环覆盖卡片内全部控件
// - 通过 portal-free 渲染（与既有 modal 保持一致），zIndex: 100（由 Modal.tsx 的样式口径）

import { useRef } from 'react';
import { useModalFocus } from './Modal';

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
  const cancelRef = useRef<HTMLButtonElement>(null);
  // 初始焦点固定在「取消」：危险态下绝不停在确认键上（避免误按回车触发不可逆操作）。
  const { containerRef } = useModalFocus({
    enabled: open,
    onClose: onCancel,
    initialFocusRef: cancelRef,
  });

  if (!open) return null;

  return (
    <div
      ref={containerRef}
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
