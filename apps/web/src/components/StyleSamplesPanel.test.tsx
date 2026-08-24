import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StyleSamplesPanel } from './StyleSamplesPanel';
import { ApiError } from '../api/client';
import type { StyleSample } from '../api/types';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    styleSamplesApi: {
      list: vi.fn(),
      create: vi.fn(),
      remove: vi.fn(),
    },
  };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { styleSamplesApi } = await import('../api/endpoints');

const baseSamples: StyleSample[] = [
  {
    sample_id: 'asty_a1',
    project_id: 'prj_1',
    title: '雨夜',
    content: '雨敲在瓦上，一夜未歇。',
    created_at: '2026-08-24T10:00:00Z',
    updated_at: '2026-08-24T10:00:00Z',
  },
];

describe('StyleSamplesPanel (Sprint 15 / V1.3)', () => {
  beforeEach(() => {
    vi.mocked(styleSamplesApi.list).mockReset();
    vi.mocked(styleSamplesApi.create).mockReset();
    vi.mocked(styleSamplesApi.remove).mockReset();
  });

  it('renders empty state when no samples', () => {
    render(
      <StyleSamplesPanel projectId="prj_1" initialSamples={[]} />,
    );
    expect(screen.getByTestId('style-samples-panel')).toBeInTheDocument();
    expect(screen.getByTestId('style-samples-empty')).toHaveTextContent(
      /该项目暂无文风样例/,
    );
  });

  it('renders list with title and delete buttons', () => {
    render(
      <StyleSamplesPanel projectId="prj_1" initialSamples={baseSamples} />,
    );
    expect(screen.getByTestId('style-samples-item')).toBeInTheDocument();
    expect(screen.getByTestId('style-samples-title-asty_a1')).toHaveTextContent('雨夜');
    expect(screen.getByTestId('style-samples-delete-asty_a1')).toBeInTheDocument();
  });

  it('disables submit when title or content empty', () => {
    render(
      <StyleSamplesPanel projectId="prj_1" initialSamples={[]} />,
    );
    const submit = screen.getByTestId('style-samples-add-submit');
    expect(submit).toBeDisabled();
  });

  it('calls create api on submit and reloads', async () => {
    vi.mocked(styleSamplesApi.create).mockResolvedValue({
      sample_id: 'asty_b2',
      project_id: 'prj_1',
      title: '晨光',
      content: '晨光初照。',
      created_at: '2026-08-24T11:00:00Z',
      updated_at: '2026-08-24T11:00:00Z',
    });
    vi.mocked(styleSamplesApi.list).mockResolvedValue([
      {
        sample_id: 'asty_b2',
        project_id: 'prj_1',
        title: '晨光',
        content: '晨光初照。',
        created_at: '2026-08-24T11:00:00Z',
        updated_at: '2026-08-24T11:00:00Z',
      },
    ]);

    render(
      <StyleSamplesPanel projectId="prj_1" initialSamples={[]} />,
    );
    fireEvent.change(screen.getByTestId('style-samples-add-title'), {
      target: { value: '晨光' },
    });
    fireEvent.change(screen.getByTestId('style-samples-add-content'), {
      target: { value: '晨光初照。' },
    });
    fireEvent.click(screen.getByTestId('style-samples-add-submit'));

    await waitFor(() => {
      expect(styleSamplesApi.create).toHaveBeenCalledWith('prj_1', {
        title: '晨光',
        content: '晨光初照。',
      });
    });
    await waitFor(() => {
      expect(styleSamplesApi.list).toHaveBeenCalledWith('prj_1');
    });
  });

  it('shows 422 error when create returns 422', async () => {
    vi.mocked(styleSamplesApi.create).mockRejectedValue(
      new ApiError(422, 'content too long: 5001 chars > limit 5000'),
    );
    render(
      <StyleSamplesPanel projectId="prj_1" initialSamples={[]} />,
    );
    fireEvent.change(screen.getByTestId('style-samples-add-title'), {
      target: { value: 't' },
    });
    fireEvent.change(screen.getByTestId('style-samples-add-content'), {
      target: { value: 'c' },
    });
    fireEvent.click(screen.getByTestId('style-samples-add-submit'));

    await waitFor(() => {
      expect(screen.getByText(/422:/)).toBeInTheDocument();
    });
  });

  it('ignores 404 on delete (idempotent)', async () => {
    vi.mocked(styleSamplesApi.remove).mockRejectedValue(
      new ApiError(404, 'not found'),
    );
    vi.mocked(styleSamplesApi.list).mockResolvedValue([]);
    render(
      <StyleSamplesPanel projectId="prj_1" initialSamples={baseSamples} />,
    );
    fireEvent.click(screen.getByTestId('style-samples-delete-asty_a1'));

    await waitFor(() => {
      expect(styleSamplesApi.remove).toHaveBeenCalledWith('prj_1', 'asty_a1');
    });
    await waitFor(() => {
      expect(styleSamplesApi.list).toHaveBeenCalledWith('prj_1');
    });
  });
});
