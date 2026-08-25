import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ContinuePanel } from './ContinuePanel';
import { ApiError } from '../api/client';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    continuationApi: {
      generate: vi.fn(),
      adopt: vi.fn(),
    },
  };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { continuationApi } = await import('../api/endpoints');

const sampleVariants = [
  { index: 1, text: '雨夜里，主角转身离去。', tokens: 18, elapsed_ms: 1200 },
  { index: 2, text: '钟声敲响十二下，巷子口的人影骤然消失。', tokens: 22, elapsed_ms: 1500 },
  { index: 3, text: '她没有回头，只是把手中的信塞进了口袋。', tokens: 21, elapsed_ms: 1300 },
];

describe('ContinuePanel', () => {
  beforeEach(() => {
    vi.mocked(continuationApi.generate).mockReset();
    vi.mocked(continuationApi.adopt).mockReset();
  });

  it('renders initial state with generate button and instruction textarea', () => {
    render(<ContinuePanel projectId="prj_1" chapterId="ch_1" />);

    expect(screen.getByTestId('continue-panel')).toBeInTheDocument();
    const btn = screen.getByTestId('continue-generate');
    expect(btn).toBeInTheDocument();
    expect(btn).not.toBeDisabled();
    expect(btn).toHaveTextContent(/生成 3 个续写候选/);
    expect(screen.getByTestId('continue-instruction')).toBeInTheDocument();
    // 默认无候选区 / 无成功条 / 无错误条
    expect(screen.queryByTestId('continue-variants')).not.toBeInTheDocument();
    expect(screen.queryByTestId('continue-success')).not.toBeInTheDocument();
    expect(screen.queryByTestId('continue-error')).not.toBeInTheDocument();
  });

  it('clicking generate calls api and renders 3 variant cards', async () => {
    vi.mocked(continuationApi.generate).mockResolvedValue({
      variants: sampleVariants,
      model: 'gpt-4o-mini',
      total_elapsed_ms: 4200,
    });

    render(<ContinuePanel projectId="prj_1" chapterId="ch_1" />);
    fireEvent.click(screen.getByTestId('continue-generate'));

    await waitFor(() => {
      expect(screen.getByTestId('continue-variants')).toBeInTheDocument();
    });

    // 3 张候选卡的正文与采纳按钮都在
    expect(screen.getByTestId('continue-variant-text-1')).toHaveTextContent(
      /雨夜里/,
    );
    expect(screen.getByTestId('continue-variant-text-2')).toHaveTextContent(
      /钟声/,
    );
    expect(screen.getByTestId('continue-variant-text-3')).toHaveTextContent(
      /没有回头/,
    );
    expect(screen.getByTestId('continue-adopt-1')).toBeInTheDocument();
    expect(screen.getByTestId('continue-adopt-2')).toBeInTheDocument();
    expect(screen.getByTestId('continue-adopt-3')).toBeInTheDocument();

    // instruction 为空 → payload 为 {}
    expect(continuationApi.generate).toHaveBeenCalledWith('prj_1', 'ch_1', {});
    expect(screen.getByTestId('continue-regen')).toBeInTheDocument();
  });

  it('forwards non-empty instruction as payload', async () => {
    vi.mocked(continuationApi.generate).mockResolvedValue({
      variants: sampleVariants,
      model: 'gpt-4o-mini',
      total_elapsed_ms: 4200,
    });

    render(<ContinuePanel projectId="prj_1" chapterId="ch_1" />);
    fireEvent.change(screen.getByTestId('continue-instruction'), {
      target: { value: '加入雨夜追逐，节奏紧凑' },
    });
    fireEvent.click(screen.getByTestId('continue-generate'));

    await waitFor(() => {
      expect(screen.getByTestId('continue-variants')).toBeInTheDocument();
    });
    expect(continuationApi.generate).toHaveBeenCalledWith('prj_1', 'ch_1', {
      instruction: '加入雨夜追逐，节奏紧凑',
    });
  });

  it('clicking adopt calls api and shows success banner with re-review hint', async () => {
    vi.mocked(continuationApi.generate).mockResolvedValue({
      variants: sampleVariants,
      model: 'gpt-4o-mini',
      total_elapsed_ms: 4200,
    });
    vi.mocked(continuationApi.adopt).mockResolvedValue({
      version: 4,
      status: 'DRAFTED',
      appended_chars: 19,
    });

    render(<ContinuePanel projectId="prj_1" chapterId="ch_1" />);
    fireEvent.click(screen.getByTestId('continue-generate'));

    await waitFor(() => {
      expect(screen.getByTestId('continue-adopt-2')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('continue-adopt-2'));

    await waitFor(() => {
      expect(screen.getByTestId('continue-success')).toBeInTheDocument();
    });
    const successBanner = screen.getByTestId('continue-success');
    expect(successBanner).toHaveTextContent(/v4/);
    expect(successBanner).toHaveTextContent(/DRAFTED/);
    // DRAFTED 时显示「章节需重新评审」
    expect(successBanner).toHaveTextContent(/章节需重新评审/);

    // adopt 被以 variant[1].text 提交
    expect(continuationApi.adopt).toHaveBeenCalledWith('prj_1', 'ch_1', {
      content: sampleVariants[1].text,
    });

    // 候选区应被清空
    await waitFor(() => {
      expect(screen.queryByTestId('continue-variants')).not.toBeInTheDocument();
    });
  });

  it('api 409 shows error detail with retry label on button', async () => {
    vi.mocked(continuationApi.generate).mockRejectedValue(
      new ApiError(409, 'chapter status PLANNED is not commitable'),
    );

    render(<ContinuePanel projectId="prj_1" chapterId="ch_1" />);
    fireEvent.click(screen.getByTestId('continue-generate'));

    await waitFor(() => {
      expect(screen.getByTestId('continue-error')).toBeInTheDocument();
    });
    const errBox = screen.getByTestId('continue-error');
    // detail 原文直显
    expect(errBox).toHaveTextContent(/chapter status PLANNED/);
    // 错误态下按钮文案应为「重试」
    const btn = screen.getByTestId('continue-generate');
    expect(btn).toHaveTextContent(/重试/);
  });

  it('loading state disables generate button and shows progress text', async () => {
    // 永不 resolve → 永久处于 generating
    vi.mocked(continuationApi.generate).mockImplementation(
      () => new Promise(() => {}),
    );

    render(<ContinuePanel projectId="prj_1" chapterId="ch_1" />);
    fireEvent.click(screen.getByTestId('continue-generate'));

    const btn = screen.getByTestId('continue-generate');
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent(/生成中/);
    expect(screen.getByTestId('continue-generating')).toHaveTextContent(
      /正在生成候选/,
    );
  });

  it('disabled prop disables generate and instruction', () => {
    render(<ContinuePanel projectId="prj_1" chapterId="ch_1" disabled />);
    expect(screen.getByTestId('continue-generate')).toBeDisabled();
    expect(screen.getByTestId('continue-instruction')).toBeDisabled();
  });
});