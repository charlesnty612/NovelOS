// ConfirmDialog 组件单测（Sprint 22「交互反馈统一」批次）。
// 覆盖：
// - open=false 不渲染任何内容
// - open=true 渲染 title/body/testid 与两个按钮
// - 初始焦点在「取消」按钮上（danger 态下绝对不在确认键上）
// - 点击「确认」→ onConfirm 调用，「取消」→ onCancel 调用
// - Esc 关闭 → onCancel 调用
// - danger=true → 确认按钮带 btn--danger；danger=false → btn--primary
// - 遮罩 click → onCancel

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfirmDialog } from './ConfirmDialog';

describe('ConfirmDialog', () => {
  let onConfirm: ReturnType<typeof vi.fn>;
  let onCancel: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    onConfirm = vi.fn();
    onCancel = vi.fn();
  });

  const props = (overrides: Partial<Parameters<typeof ConfirmDialog>[0]> = {}) => ({
    open: true,
    title: '删除项目',
    body: '确认删除「测试」？',
    confirmText: '删除',
    cancelText: '取消',
    danger: true,
    onConfirm,
    onCancel,
    testId: 'cd',
    ...overrides,
  });

  it('open=false 时不渲染', () => {
    render(<ConfirmDialog {...props({ open: false })} />);
    expect(screen.queryByTestId('cd')).toBeNull();
  });

  it('open=true 时渲染 title/body/两个按钮（danger 态确认钮带 btn--danger）', () => {
    render(<ConfirmDialog {...props()} />);
    const dialog = screen.getByTestId('cd');
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute('role', 'dialog');
    expect(within(dialog).getByText('删除项目')).toBeInTheDocument();
    expect(within(dialog).getByText('确认删除「测试」？')).toBeInTheDocument();
    const confirmBtn = screen.getByTestId('cd-confirm');
    expect(confirmBtn).toHaveTextContent('删除');
    expect(confirmBtn.className).toContain('btn--danger');
    const cancelBtn = screen.getByTestId('cd-cancel');
    expect(cancelBtn).toHaveTextContent('取消');
  });

  it('danger=false 时确认钮走 btn--primary', () => {
    render(<ConfirmDialog {...props({ danger: false })} />);
    expect(screen.getByTestId('cd-confirm').className).toContain('btn--primary');
    expect(screen.getByTestId('cd-confirm').className).not.toContain('btn--danger');
  });

  it('初始焦点在「取消」按钮（danger 态下不在确认键上）', async () => {
    render(<ConfirmDialog {...props({ danger: true })} />);
    // useEffect 里有 setTimeout(0) 把焦点放到 cancelRef → 等一拍
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByTestId('cd-cancel'));
    });
  });

  it('点击「确认」调 onConfirm；点击「取消」调 onCancel', async () => {
    const user = userEvent.setup();
    render(<ConfirmDialog {...props()} />);

    await user.click(screen.getByTestId('cd-confirm'));
    expect(onConfirm).toHaveBeenCalledTimes(1);

    await user.click(screen.getByTestId('cd-cancel'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('Esc 关闭 → onCancel 调用', () => {
    render(<ConfirmDialog {...props()} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('open=false 后 Esc 不触发 onCancel', () => {
    const { rerender } = render(<ConfirmDialog {...props({ open: true })} />);
    rerender(<ConfirmDialog {...props({ open: false })} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('点遮罩关闭（点击 dialog 本身 → onCancel）', () => {
    render(<ConfirmDialog {...props()} />);
    fireEvent.click(screen.getByTestId('cd'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});