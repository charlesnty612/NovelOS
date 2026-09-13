// Modal —— 对话弹窗基座（V3.10「交互完整」批次引入）。
//
// 引入动机：站内 9 个自写 modal（项目页 2 / 章节页 1 / AI 设置页 1 / Story Bible 各 tab）
// 原先各自复制一份 backdrop + card，而且除 ConfirmDialog 外一律没有 Esc、初始焦点与
// 焦点循环——键盘用户打开弹窗后无法关闭、Tab 会跑到弹窗背后的页面。本文件把这几件事
// 收敛到一处：Esc 关闭、初始焦点、Tab 焦点循环、关闭后归还焦点、锁 body 滚动。
//
// 用法（Modal 由父组件条件渲染，挂载即打开）：
//   {open ? (
//     <Modal title="新建章节" onClose={() => setOpen(false)} width={420} testId="x">
//       <form onSubmit={handleSubmit}>…<button type="submit">保存</button></form>
//     </Modal>
//   ) : null}
//
// 设计取舍（有意为之，勿当缺漏）：
// - 不渲染右上角「×」：它会在 DOM 顺序上抢走「初始焦点 = 第一个可交互元素」，
//   也会与各表单自带的「取消」按钮重复；Esc / 点遮罩 / 取消按钮三条关闭路径已足够。
// - 不提供 footer 插槽：动作按钮必须留在各页 <form> 内部，原生 submit 才不会被拆散。
// - 关闭后焦点归还打开前的元素（restoreFocus），避免焦点掉回 <body>。
//
// 可测性：jsdom 没有布局引擎（offsetParent 恒为 null、getClientRects 恒为空），
// 因此「可交互元素」只用属性判定（disabled / hidden / aria-hidden），不做可见性测量。

import {
  useEffect,
  useId,
  useRef,
  type CSSProperties,
  type ReactNode,
  type MutableRefObject,
} from 'react';

// Tab 焦点循环的候选集合。'[tabindex]:not([tabindex="-1"])' 覆盖自定义可聚焦元素；
// 容器自身用 tabIndex={-1}（可编程聚焦但不进 Tab 序列），因此不会被选中。
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function isInert(el: HTMLElement): boolean {
  if (el.hasAttribute('disabled') || el.hasAttribute('hidden')) return true;
  if (el.getAttribute('aria-hidden') === 'true') return true;
  if ((el as HTMLInputElement).type === 'hidden') return true;
  return el.closest('[hidden]') !== null;
}

/** 容器内按 DOM 顺序排列的可交互元素（供焦点循环与初始焦点使用）。 */
export function getFocusableElements(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => !isInert(el),
  );
}

// 打开中的弹窗栈：只让最上层响应 Esc / Tab 循环。
// 场景：项目页「编辑项目」弹窗内再弹「归档确认」——两个 modal 同时挂载时，
// 若各自监听 window，按一次 Esc 会把两层一起关掉。
const modalStack: symbol[] = [];

function pushModal(id: symbol): void {
  modalStack.push(id);
}

function popModal(id: symbol): void {
  const i = modalStack.indexOf(id);
  if (i >= 0) modalStack.splice(i, 1);
}

function isTopModal(id: symbol): boolean {
  return modalStack[modalStack.length - 1] === id;
}

export interface UseModalFocusOptions {
  /** false = 不接管（受控组件在 open=false 分支仍会调用 hook 时需要） */
  enabled?: boolean;
  /** Esc 关闭回调；缺省表示不响应 Esc */
  onClose?: () => void;
  /** Esc 是否可关闭，默认 true；提交中可传 false 防误关 */
  closeOnEsc?: boolean;
  /** 初始焦点目标；缺省 = 容器内第一个可交互元素，再缺省 = 容器自身 */
  initialFocusRef?: MutableRefObject<HTMLElement | null>;
  /** 卸载时把焦点还给打开前的元素，默认 true */
  restoreFocus?: boolean;
  /** 打开期间锁 body 滚动，默认 true */
  lockScroll?: boolean;
}

/**
 * 弹窗焦点管理：初始焦点 / Tab 循环 / Esc / 卸载归还焦点 / body 滚动锁。
 * 返回的 containerRef 必须挂在弹窗容器（role="dialog" 的那个元素）上。
 */
export function useModalFocus(options: UseModalFocusOptions = {}): {
  containerRef: MutableRefObject<HTMLDivElement | null>;
} {
  const {
    enabled = true,
    onClose,
    closeOnEsc = true,
    initialFocusRef,
    restoreFocus = true,
    lockScroll = true,
  } = options;
  const containerRef = useRef<HTMLDivElement>(null);
  // 打开前的焦点元素：关闭时归还。
  const restoreRef = useRef<HTMLElement | null>(null);
  const idRef = useRef<symbol | null>(null);
  if (idRef.current === null) idRef.current = Symbol('modal');

  // 入栈必须早于事件监听注册（effect 按声明顺序执行），否则首个键盘事件看不到自己。
  useEffect(() => {
    if (!enabled) return;
    const id = idRef.current as symbol;
    pushModal(id);
    return () => popModal(id);
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    restoreRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    return () => {
      const el = restoreRef.current;
      restoreRef.current = null;
      if (restoreFocus && el && document.contains(el)) el.focus();
    };
  }, [enabled, restoreFocus]);

  useEffect(() => {
    if (!enabled) return;
    // 等一拍再聚焦：让本次 render 的子树（含 autoFocus 之外的控件）先完成挂载。
    const timer = window.setTimeout(() => {
      const explicit = initialFocusRef?.current;
      if (explicit) {
        explicit.focus();
        return;
      }
      const container = containerRef.current;
      const first = getFocusableElements(container)[0];
      if (first) {
        first.focus();
        return;
      }
      // 弹窗内没有可交互元素时聚焦容器本身（tabIndex={-1}），Tab 才不会漏到背景页。
      container?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [enabled, initialFocusRef]);

  useEffect(() => {
    if (!enabled) return;
    const id = idRef.current as symbol;
    const handler = (e: KeyboardEvent) => {
      if (!isTopModal(id)) return;
      if (e.key === 'Escape') {
        if (!closeOnEsc || !onClose) return;
        // 捕获阶段拦下：避免同页面的其他 Esc 监听（如详情页的外层弹窗）一起触发。
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== 'Tab') return;
      const container = containerRef.current;
      if (!container) return;
      const items = getFocusableElements(container);
      if (items.length === 0) {
        e.preventDefault();
        container.focus();
        return;
      }
      const first = items[0] as HTMLElement;
      const last = items[items.length - 1] as HTMLElement;
      const active = document.activeElement;
      if (!(active instanceof HTMLElement) || !container.contains(active)) {
        // 焦点已经跑到弹窗外（如背景页）→ 拉回弹窗内。
        e.preventDefault();
        first.focus();
        return;
      }
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', handler, true);
    return () => window.removeEventListener('keydown', handler, true);
  }, [enabled, onClose, closeOnEsc]);

  useEffect(() => {
    if (!enabled || !lockScroll) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // 嵌套弹窗按 LIFO 卸载：内层还原成 'hidden'，外层再还原成最初值。
    return () => {
      document.body.style.overflow = prev;
    };
  }, [enabled, lockScroll]);

  return { containerRef };
}

export interface ModalProps {
  /** 标题（同时作为 aria-labelledby 的目标，必须非空） */
  title: string;
  /** 关闭回调（Esc / 点遮罩 / 子元素按钮调用） */
  onClose: () => void;
  /** 卡片宽度，默认 480；窄屏由 max-width: 92vw 兜底 */
  width?: number | string;
  /** 是否允许 Esc / 点遮罩关闭，默认 true；提交中可传 false */
  closable?: boolean;
  /** 可选 testid：挂在 role="dialog" 的卡片上（遮罩上是 `${testId}-backdrop`） */
  testId?: string;
  /** 初始焦点目标（如危险操作的弹窗应聚焦「取消」） */
  initialFocusRef?: MutableRefObject<HTMLElement | null>;
  children: ReactNode;
}

export function Modal({
  title,
  onClose,
  width = 480,
  closable = true,
  testId,
  initialFocusRef,
  children,
}: ModalProps) {
  const titleId = useId();
  const { containerRef } = useModalFocus({
    onClose,
    closeOnEsc: closable,
    initialFocusRef,
  });

  return (
    <div
      className="modal-backdrop"
      style={backdropStyle}
      onClick={() => {
        if (closable) onClose();
      }}
      data-testid={testId ? `${testId}-backdrop` : undefined}
    >
      <div
        ref={containerRef}
        className="card"
        role="dialog"
        aria-modal="true"
        // aria-labelledby 指向标题；aria-label 作为兜底（标题为空串时读屏仍能播报）。
        aria-labelledby={titleId}
        aria-label={title || undefined}
        tabIndex={-1}
        style={{ width, maxWidth: '92vw', ...dialogStyle }}
        // 卡片内的点击不冒泡到遮罩（否则点表单也会关窗）。
        onClick={(e) => e.stopPropagation()}
        data-testid={testId}
      >
        <div className="section-title" id={titleId} style={{ marginBottom: 12 }}>
          {title}
        </div>
        {children}
      </div>
    </div>
  );
}

// 与既有 9 个自写 modal 的视觉保持一致（同一个遮罩色 / 层级）。
const backdropStyle: CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(15,20,35,0.4)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 'var(--space-3)',
  zIndex: 100,
};

const dialogStyle: CSSProperties = {
  // 长表单（如模型档案）在窗口高度不足时就地滚动，而不是把卡片顶出可视区。
  maxHeight: '90vh',
  overflowY: 'auto',
};
