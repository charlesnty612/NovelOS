/**
 * NotFoundPage 冒烟（补口：此前零测试）。
 *
 * 判别两件事：404 文案真的渲染了；回首页的链接指向 "/"（不是空 href）。
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { NotFoundPage } from './NotFoundPage';

describe('NotFoundPage', () => {
  it('渲染 404 文案', () => {
    render(
      <MemoryRouter>
        <NotFoundPage />
      </MemoryRouter>,
    );
    expect(screen.getByText('页面不存在')).toBeInTheDocument();
  });

  it('提供回到项目列表的链接，指向 /', () => {
    render(
      <MemoryRouter>
        <NotFoundPage />
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: /回到项目列表/ });
    expect(link).toHaveAttribute('href', '/');
  });
});
