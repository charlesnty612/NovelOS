import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EmptyState } from './EmptyState';

describe('EmptyState', () => {
  it('渲染 title 与 hint，并展示 action 节点', () => {
    render(
      <EmptyState
        title="还没有项目"
        hint="点按钮新建"
        action={<button>新建</button>}
      />,
    );
    expect(screen.getByText('还没有项目')).toBeInTheDocument();
    expect(screen.getByText('点按钮新建')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建' })).toBeInTheDocument();
  });
});
