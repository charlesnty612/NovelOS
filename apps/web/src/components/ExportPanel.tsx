import { useMemo, useState } from 'react';
import { ApiError } from '../api/client';
import { exportApi } from '../api/endpoints';
import { ErrorBanner } from './ErrorBanner';
import { formatApiError } from '../utils/formatApiError';

type ExportFormat = 'txt' | 'docx' | 'fanqie';

interface ExportButton {
  format: ExportFormat;
  label: string;
  hint: string;
  testId: string;
  /** fanqie 强制忽略 chapterNo；txt/docx 整书场景下 chapterNo 不传 */
  buildUrl: (projectId: string, chapterNo?: number) => string;
}

const BUTTONS: ExportButton[] = [
  {
    format: 'docx',
    label: '整书 docx',
    hint: 'Word 可直接打开；含全部已提交章节',
    testId: 'export-book-docx',
    buildUrl: (pid) => exportApi.url(pid, { format: 'docx' }),
  },
  {
    format: 'txt',
    label: '整书 txt',
    hint: '纯文本；UTF-8 BOM 兼容 Excel / 番茄审核页',
    testId: 'export-book-txt',
    buildUrl: (pid) => exportApi.url(pid, { format: 'txt' }),
  },
  {
    format: 'fanqie',
    label: '番茄投稿包',
    hint: '前 ~1 万字正文 + 全书大纲（章节计划）',
    testId: 'export-fanqie',
    buildUrl: (pid) => exportApi.url(pid, { format: 'fanqie' }),
  },
];

interface ExportPanelProps {
  projectId: string;
}

/**
 * ExportPanel —— 项目总览页「导出」面板（V1.4 / Sprint 16）。
 *
 * - 三个下载按钮（整书 docx / 整书 txt / 番茄投稿包）；
 * - 单章导出另有一个 number 输入框 + 「下载单章」按钮；
 * - 触发下载：构造 ``/api/projects/{pid}/export?...`` URL，浏览器走
 *   ``Content-Disposition: attachment`` 自动弹出保存框。
 * - 失败（404 / 400 / 500）经 ErrorBanner 展示。
 */
export function ExportPanel({ projectId }: ExportPanelProps) {
  const [chapterNoInput, setChapterNoInput] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const chapterNo = useMemo(() => {
    if (!chapterNoInput.trim()) return undefined;
    const n = Number.parseInt(chapterNoInput.trim(), 10);
    return Number.isFinite(n) && n > 0 ? n : undefined;
  }, [chapterNoInput]);

  const triggerDownload = async (url: string) => {
    setError(null);
    setBusy(true);
    try {
      // 用 GET + blob 下载而非直接 <a href>：
      // - 可捕获 4xx/5xx 错误（<a href> 会直接走浏览器下载失败页）；
      // - 保持 Content-Disposition 文件名（response.headers）。
      // credentials: 'same-origin' 保证同源会话 cookie 一并携带（与 backupApi
      // 下载备份一致），避免鉴权 cookie 缺失导致后端 401。
      const resp = await fetch(url, {
        method: 'GET',
        credentials: 'same-origin',
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => '');
        let detail = text || resp.statusText || `HTTP ${resp.status}`;
        try {
          const parsed = JSON.parse(text);
          if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
            detail = String((parsed as { detail: unknown }).detail);
          }
        } catch {
          // 留 raw text
        }
        throw new ApiError(resp.status, detail);
      }
      const blob = await resp.blob();
      const disp = resp.headers.get('content-disposition') ?? '';
      const filename =
        parseFilename(disp) || `novelos-export-${Date.now()}.bin`;
      const objUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // 给浏览器一点时间触发下载，再 revoke
      setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        setError(formatApiError(e));
      } else {
        setError(e instanceof Error ? `操作失败：${e.message}` : '操作失败：导出失败');
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 16 }} data-testid="export-panel">
      <div className="detail-pane__title">导出（V1.4）</div>
      <div className="muted small" style={{ marginBottom: 8 }}>
        整书导出当前章节快照；番茄投稿包包含前 ~1 万字正文 + 全书大纲。
      </div>
      <ErrorBanner>{error}</ErrorBanner>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        {BUTTONS.map((b) => (
          <button
            key={b.format}
            type="button"
            className="btn btn--sm btn--primary"
            disabled={busy}
            onClick={() => void triggerDownload(b.buildUrl(projectId))}
            data-testid={b.testId}
            title={b.hint}
          >
            {b.label}
          </button>
        ))}
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginTop: 4,
        }}
      >
        <label className="muted small" htmlFor="export-chapter-no">
          单章 number
        </label>
        <input
          id="export-chapter-no"
          className="input"
          inputMode="numeric"
          placeholder="例 3"
          value={chapterNoInput}
          onChange={(e) => setChapterNoInput(e.target.value)}
          data-testid="export-chapter-no-input"
          style={{ width: 100 }}
        />
        <button
          type="button"
          className="btn btn--sm"
          disabled={busy || chapterNo === undefined}
          onClick={() =>
            void triggerDownload(
              exportApi.url(projectId, { format: 'txt', chapter_no: chapterNo })
            )
          }
          data-testid="export-chapter-txt"
        >
          下载单章 txt
        </button>
        <button
          type="button"
          className="btn btn--sm"
          disabled={busy || chapterNo === undefined}
          onClick={() =>
            void triggerDownload(
              exportApi.url(projectId, { format: 'docx', chapter_no: chapterNo })
            )
          }
          data-testid="export-chapter-docx"
        >
          下载单章 docx
        </button>
      </div>
      <div className="muted small" style={{ marginTop: 6 }}>
        输入章节 number（正整数）后启用单章导出按钮；不填则按整书导出。
      </div>
    </div>
  );
}

function parseFilename(contentDisposition: string): string | null {
  // 兼容两种写法：filename="x" / filename*=UTF-8''x
  const m1 = /filename\*=UTF-8''([^;]+)/i.exec(contentDisposition);
  if (m1 && m1[1]) {
    try {
      return decodeURIComponent(m1[1]);
    } catch {
      return m1[1];
    }
  }
  const m2 = /filename="?([^";]+)"?/i.exec(contentDisposition);
  return m2 && m2[1] ? m2[1] : null;
}

// 避免 lint 警告：exportApi 在测试侧以 mock 形式注入
void exportApi;