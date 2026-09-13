/** @vitest-environment jsdom */
// Modal 基座行为测试（V3.10「交互完整」批次）。
// 覆盖：标题/ARIA、Esc 关闭、点遮罩关闭、closable=false 防误关、初始焦点、
// Tab 焦点循环、body 滚动锁与还原、关闭后焦点归还触发元素、嵌套弹窗只关最上层。

import { createRef, useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Modal, getFocusableElements } from './Modal';

function renderModal(overrides: Partial<Parameters<typeof Modal>[0]> = {}) {
  const onClose = vi.fn();
  const utils = render(
    <Modal title="新建章节" onClose={onClose} {...overrides}>
      <form>
        <div className="form-row">
          <label>章号</label>
          <input data-testid="field" />
        </div>
        <button type="button" data-testid="cancel">
          取消
        </button>
        <button type="submit" data-testid="save">
          保存
        </button>
      </form>
    </Modal>,
  );
  return { onClose, ...utils };
}

describe('Modal 基座', () => {
  it('渲染标题与内容，并带上 aria-modal / aria-labelledby 语义', () => {
    renderModal({ testId: 'm1' });

    const dialog = screen.getByTestId('m1');
    expect(dialog).toHaveAttribute('role', 'dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');

    const labelledBy = dialog.getAttribute('aria-labelledby');
    expect(labelledBy).toBeTruthy();
    // 标题元素存在且 id 与 aria-labelledby 对应（读屏能播报弹窗名）
    const titleEl = document.getElementById(labelledBy as string);
    expect(titleEl).toHaveTextContent('新建章节');
    // 内容渲染在卡片内
    expect(screen.getByTestId('field')).toBeInTheDocument();
  });

  it('Esc 关闭：keydown(Escape) → onClose', () => {
    const { onClose } = renderModal();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('点遮罩关闭；点卡片内部不关闭', () => {
    const { onClose } = renderModal({ testId: 'm2' });

    fireEvent.click(screen.getByTestId('field'));
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('m2-backdrop'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closable=false：Esc 与点遮罩都不关闭（提交中防误关）', () => {
    const { onClose } = renderModal({ closable: false, testId: 'm3' });

    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('m3-backdrop'));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('初始焦点落在容器内第一个可交互元素上', async () => {
    renderModal();
    // useEffect + setTimeout(0) 一拍之后聚焦
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByTestId('field'));
    });
  });

  it('initialFocusRef 优先于「第一个可交互元素」', async () => {
    const ref = createRef<HTMLButtonElement>();
    render(
      <Modal title="危险操作" onClose={() => {}} initialFocusRef={ref}>
        <button type="button" data-testid="confirm" ref={ref}>
          确认
        </button>
      </Modal>,
    );
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByTestId('confirm'));
    });
  });

  it('Tab 焦点循环：末元素 Tab 回首元素，首元素 Shift+Tab 到末元素', async () => {
    renderModal();
    const field = screen.getByTestId('field');
    const save = screen.getByTestId('save');

    await waitFor(() => expect(document.activeElement).toBe(field));

    // 末尾 → Tab → 回到第一个
    save.focus();
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(document.activeElement).toBe(field);

    // 第一个 → Shift+Tab → 跳到最后一个
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(save);
  });

  it('焦点跑到弹窗外时，Tab 把它拉回弹窗内', async () => {
    renderModal();
    const field = screen.getByTestId('field');
    await waitFor(() => expect(document.activeElement).toBe(field));

    // 模拟焦点逃逸（如脚本把焦点移到背景页）：Tab 应把它拉回弹窗第一个可交互元素
    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.activeElement).toBe(document.body);

    fireEvent.keyDown(window, { key: 'Tab' });
    expect(document.activeElement).toBe(field);
  });

  it('打开时锁 body 滚动，卸载后还原', () => {
    const prev = document.body.style.overflow;
    const { unmount } = renderModal();
    expect(document.body.style.overflow).toBe('hidden');
    unmount();
    expect(document.body.style.overflow).toBe(prev);
  });

  it('关闭后焦点归还触发元素', async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button data-testid="trigger" onClick={() => setOpen(true)}>
            打开
          </button>
          {open ? (
            <Modal title="弹窗" onClose={() => setOpen(false)}>
              <button type="button" data-testid="cancel">
                取消
              </button>
            </Modal>
          ) : null}
        </div>
      );
    }
    render(<Harness />);

    const trigger = screen.getByTestId('trigger');
    trigger.focus();
    fireEvent.click(trigger);

    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByTestId('cancel'));
    });

    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => {
      expect(document.activeElement).toBe(trigger);
    });
  });

  it('嵌套弹窗：一次 Esc 只关最上层', () => {
    const outerClose = vi.fn();
    const innerClose = vi.fn();
    render(
      <div>
        <Modal title="外层" onClose={outerClose} testId="outer">
          <button type="button">外层按钮</button>
        </Modal>
        <Modal title="内层" onClose={innerClose} testId="inner">
          <button type="button">内层按钮</button>
        </Modal>
      </div>,
    );

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(innerClose).toHaveBeenCalledTimes(1);
    expect(outerClose).not.toHaveBeenCalled();
  });

  it('getFocusableElements：跳过 disabled / hidden / tabindex=-1 的元素', () => {
    const container = document.createElement('div');
    container.innerHTML = `
      <button id="a">a</button>
      <button id="b" disabled>b</button>
      <button id="c" hidden>c</button>
      <span id="d" tabindex="-1"></span>
      <input id="e" type="hidden" />
      <a id="f" href="#x">f</a>
    `;
    const ids = getFocusableElements(container).map((el) => el.id);
    expect(ids).toEqual(['a', 'f']);
  });
});
