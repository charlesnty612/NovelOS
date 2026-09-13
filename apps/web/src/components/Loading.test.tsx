import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import LoadingDefault, { Loading, SkeletonRows } from './Loading';

describe('Loading', () => {
  it('默认渲染 spinner 与「加载中…」，并带 aria-live 状态区', () => {
    const { container } = render(<Loading />);
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(status).toHaveTextContent('加载中…');
    expect(container.querySelector('.spinner')).not.toBeNull();
  });

  it('支持自定义文案与追加 className', () => {
    render(<Loading text="正在加载项目总览…" className="app-sidebar__loading" />);
    const status = screen.getByRole('status');
    expect(status).toHaveClass('loading');
    expect(status).toHaveClass('app-sidebar__loading');
    expect(status).toHaveTextContent('正在加载项目总览…');
  });

  it('默认导出即 Loading', () => {
    expect(LoadingDefault).toBe(Loading);
  });
});

describe('SkeletonRows', () => {
  it('默认渲染 3 行骨架块，n 可控', () => {
    const { container: def } = render(<SkeletonRows />);
    expect(def.querySelectorAll('.skeleton--line')).toHaveLength(3);

    const { container: five } = render(<SkeletonRows n={5} />);
    expect(five.querySelectorAll('.skeleton--line')).toHaveLength(5);
  });
});
