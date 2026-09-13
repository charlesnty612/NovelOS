import { Component, type ErrorInfo, type ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * 渲染错误兜底：任一子组件抛异常时替换整片白屏，给出错误信息与恢复入口。
 * 仅捕获渲染/生命周期异常（事件回调里的错误仍需调用方自行 catch）。
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ErrorBoundary] 页面渲染出错', error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="card error-boundary" role="alert">
        <div className="error-boundary__title">页面渲染出错</div>
        <div className="alert alert--error error-boundary__message">
          {error.message || String(error)}
        </div>
        <div className="error-boundary__actions">
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => window.location.reload()}
          >
            刷新页面
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => window.location.assign('/')}
          >
            返回首页
          </button>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
