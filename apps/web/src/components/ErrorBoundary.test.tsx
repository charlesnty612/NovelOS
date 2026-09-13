import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ErrorBoundary } from './ErrorBoundary';

function Boom({ message }: { message: string }): never {
  throw new Error(message);
}

describe('ErrorBoundary', () => {
  let consoleErr: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleErr = vi.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    consoleErr.mockRestore();
  });

  it('子组件渲染异常时给出错误信息与恢复入口（不再白屏）', () => {
    render(
      <ErrorBoundary>
        <Boom message="scores_json 形状变化" />
      </ErrorBoundary>,
    );
    expect(screen.getByText('页面渲染出错')).toBeInTheDocument();
    expect(screen.getByText(/scores_json 形状变化/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新页面' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回首页' })).toBeInTheDocument();
    expect(consoleErr).toHaveBeenCalled();
  });

  it('无异常时原样渲染子节点', () => {
    render(
      <ErrorBoundary>
        <div>正常内容</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText('正常内容')).toBeInTheDocument();
    expect(screen.queryByText('页面渲染出错')).not.toBeInTheDocument();
  });
});
