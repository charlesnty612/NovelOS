import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ExportPanel } from './ExportPanel';
import { exportApi } from '../api/endpoints';

// ---------- fetch mock ----------
type FetchResp = {
  ok: boolean;
  status: number;
  headers: Headers;
  blob: () => Promise<Blob>;
  text: () => Promise<string>;
};

function makeResponse(opts: {
  ok?: boolean;
  status?: number;
  body?: string;
  contentType?: string;
  contentDisposition?: string;
}): FetchResp {
  const headers = new Headers();
  if (opts.contentType) headers.set('content-type', opts.contentType);
  if (opts.contentDisposition) headers.set('content-disposition', opts.contentDisposition);
  const ok = opts.ok ?? true;
  const status = opts.status ?? 200;
  const body = opts.body ?? '';
  const blob = async () => new Blob([body]);
  const text = async () => body;
  return { ok, status, headers, blob, text };
}

const fetchMock = vi.fn();
vi.stubGlobal('fetch', fetchMock);

// ---------- URL.createObjectURL / revokeObjectURL ----------
const origCreate = URL.createObjectURL;
const origRevoke = URL.revokeObjectURL;
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:fake');
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  URL.createObjectURL = origCreate;
  URL.revokeObjectURL = origRevoke;
});

// ---------- anchor click helper ----------
const anchorInstances: HTMLAnchorElement[] = [];
const origCreateElement = document.createElement.bind(document);
beforeEach(() => {
  anchorInstances.length = 0;
  document.createElement = ((tag: string) => {
    const el = origCreateElement(tag) as HTMLElement;
    if (tag === 'a') {
      const a = el as HTMLAnchorElement;
      // intercept .click() to record call instead of triggering real navigation
      const origClick = a.click.bind(a);
      a.click = () => {
        anchorInstances.push(a);
        origClick();
      };
    }
    return el;
  }) as typeof document.createElement;
});
afterEach(() => {
  document.createElement = origCreateElement;
});

// ---------- body appendChild spy ----------
const origAppend = document.body.appendChild.bind(document.body);
const appendSpy = vi.fn(<T extends Node>(n: T) => origAppend(n));
beforeEach(() => {
  appendSpy.mockClear();
  document.body.appendChild = appendSpy as typeof document.body.appendChild;
});
afterEach(() => {
  document.body.appendChild = origAppend;
});

describe('ExportPanel (V1.4 / Sprint 16)', () => {
  beforeEach(() => {
    fetchMock.mockReset();
  });

  it('exportApi.url composes expected query strings', () => {
    expect(exportApi.url('prj_1', { format: 'txt' })).toBe(
      '/api/projects/prj_1/export?format=txt',
    );
    expect(exportApi.url('prj_1', { format: 'docx', chapter_no: 3 })).toBe(
      '/api/projects/prj_1/export?format=docx&chapter_no=3',
    );
    // fanqie 强制忽略 chapter_no
    expect(exportApi.url('prj_1', { format: 'fanqie', chapter_no: 5 })).toBe(
      '/api/projects/prj_1/export?format=fanqie',
    );
  });

  it('renders three primary export buttons and chapter-no input', () => {
    render(<ExportPanel projectId="prj_1" />);
    expect(screen.getByTestId('export-panel')).toBeInTheDocument();
    expect(screen.getByTestId('export-book-docx')).toBeInTheDocument();
    expect(screen.getByTestId('export-book-txt')).toBeInTheDocument();
    expect(screen.getByTestId('export-fanqie')).toBeInTheDocument();
    expect(screen.getByTestId('export-chapter-no-input')).toBeInTheDocument();
    // 单章按钮初始禁用（chapterNo 为空）
    expect(screen.getByTestId('export-chapter-txt')).toBeDisabled();
    expect(screen.getByTestId('export-chapter-docx')).toBeDisabled();
  });

  it('clicking 整书 docx triggers fetch and downloads file with parsed filename', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({
        ok: true,
        status: 200,
        body: 'binary-docx',
        contentType:
          'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        contentDisposition:
          "attachment; filename=\"book.docx\"; filename*=UTF-8''fanqie-shu.docx",
      }),
    );

    render(<ExportPanel projectId="prj_1" />);
    fireEvent.click(screen.getByTestId('export-book-docx'));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/projects/prj_1/export?format=docx');
    expect(init.method).toBe('GET');
    expect(init.credentials).toBe('same-origin');

    await waitFor(() => {
      expect(anchorInstances.length).toBe(1);
    });
    const a = anchorInstances[0];
    expect(a.href).toBe('blob:fake');
    expect(a.download).toBe('fanqie-shu.docx');
  });

  it('clicking 番茄投稿包 uses /export?format=fanqie', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({
        ok: true,
        status: 200,
        body: 'fanqie-text',
        contentType: 'text/plain; charset=utf-8',
        contentDisposition:
          "attachment; filename*=UTF-8''fanqie-shu-tougaobao.txt",
      }),
    );

    render(<ExportPanel projectId="prj_2" />);
    fireEvent.click(screen.getByTestId('export-fanqie'));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe('/api/projects/prj_2/export?format=fanqie');
    await waitFor(() => {
      expect(anchorInstances[0].download).toBe('fanqie-shu-tougaobao.txt');
    });
  });

  it('enables 单章 buttons once chapter_no is a positive integer', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({
        ok: true,
        status: 200,
        body: 'single',
        contentType: 'text/plain; charset=utf-8',
        contentDisposition: 'attachment; filename="book-ch2.txt"',
      }),
    );

    render(<ExportPanel projectId="prj_3" />);
    const input = screen.getByTestId('export-chapter-no-input');
    fireEvent.change(input, { target: { value: '2' } });
    expect(screen.getByTestId('export-chapter-txt')).not.toBeDisabled();
    expect(screen.getByTestId('export-chapter-docx')).not.toBeDisabled();

    fireEvent.click(screen.getByTestId('export-chapter-txt'));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe('/api/projects/prj_3/export?format=txt&chapter_no=2');
  });

  it('shows 404 error banner when backend returns 404', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({
        ok: false,
        status: 404,
        body: JSON.stringify({ detail: 'project not found' }),
        contentType: 'application/json',
      }),
    );

    render(<ExportPanel projectId="prj_nope" />);
    fireEvent.click(screen.getByTestId('export-book-txt'));

    await waitFor(() => {
      expect(screen.getByText(/404:/)).toBeInTheDocument();
    });
    // 不应触发下载
    expect(anchorInstances.length).toBe(0);
  });

  it('shows 400 error banner when backend returns 400 with detail', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({
        ok: false,
        status: 400,
        body: JSON.stringify({
          detail: "unsupported format 'pdf' (allowed: txt, docx, fanqie)",
        }),
        contentType: 'application/json',
      }),
    );

    render(<ExportPanel projectId="prj_x" />);
    fireEvent.click(screen.getByTestId('export-book-txt'));

    await waitFor(() => {
      expect(screen.getByText(/400:/)).toBeInTheDocument();
    });
  });
});