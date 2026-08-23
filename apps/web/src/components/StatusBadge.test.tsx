import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusBadge } from './StatusBadge';

describe('StatusBadge', () => {
  it('ACTIVE 显示进行中', () => {
    render(<StatusBadge status="ACTIVE" />);
    expect(screen.getByText('进行中')).toBeInTheDocument();
  });
  it('PAUSED 显示已暂停', () => {
    render(<StatusBadge status="PAUSED" />);
    expect(screen.getByText('已暂停')).toBeInTheDocument();
  });
  it('ARCHIVED 显示已归档', () => {
    render(<StatusBadge status="ARCHIVED" />);
    expect(screen.getByText('已归档')).toBeInTheDocument();
  });
});
