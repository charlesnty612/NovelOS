import { useCallback, useState } from 'react';
import { ApiError } from '../api/client';
import { styleSamplesApi } from '../api/endpoints';
import type { StyleSample } from '../api/types';
import { ErrorBanner } from './ErrorBanner';

const MAX_TITLE_LEN = 200;
// 与后端 _MAX_CONTENT_CHARS = 5000 对齐（前端先做一次提示性校验，
// 实际 422 由后端兜底；避免超长请求浪费带宽）。
const MAX_CONTENT_LEN = 5000;

interface Props {
  projectId: string;
  initialSamples: StyleSample[];
}

/**
 * StyleSamplesPanel —— 项目总览页「文风样例」管理面板（Sprint 15 / V1.3）。
 *
 * - 列表（标题 + 截断预览 + 创建时间 + 删除按钮）；
 * - 新增：title + content（textarea，超 5000 字禁用保存）；
 * - 删除走 DELETE；404 静默忽略（幂等）。
 * - data-testid：style-samples-panel / -item / -title-{sid} / -add-title /
 *   -add-content / -add-submit / -delete-{sid}，便于 vitest 断言。
 */
export function StyleSamplesPanel({ projectId, initialSamples }: Props) {
  const [samples, setSamples] = useState<StyleSample[]>(initialSamples);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const rows = await styleSamplesApi.list(projectId);
      setSamples(rows);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载文风样例失败');
    }
  }, [projectId]);

  const handleAdd = useCallback(async () => {
    setError(null);
    const t = title.trim();
    const c = content.trim();
    if (!t || !c) {
      setError('标题与正文均不能为空');
      return;
    }
    if (t.length > MAX_TITLE_LEN) {
      setError(`标题不能超过 ${MAX_TITLE_LEN} 字`);
      return;
    }
    if (c.length > MAX_CONTENT_LEN) {
      setError(`正文不能超过 ${MAX_CONTENT_LEN} 字`);
      return;
    }
    setBusy(true);
    try {
      await styleSamplesApi.create(projectId, { title: t, content: c });
      setTitle('');
      setContent('');
      await reload();
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        setError(`${e.status}: ${e.detail}`);
      } else {
        setError(e instanceof Error ? e.message : '新增文风样例失败');
      }
    } finally {
      setBusy(false);
    }
  }, [title, content, projectId, reload]);

  const handleDelete = useCallback(
    async (sid: string) => {
      setError(null);
      setBusy(true);
      try {
        await styleSamplesApi.remove(projectId, sid);
      } catch (e: unknown) {
        // 404 当作已删除：幂等
        if (!(e instanceof ApiError) || e.status !== 404) {
          setError(e instanceof Error ? e.message : '删除文风样例失败');
        }
      } finally {
        setBusy(false);
        await reload();
      }
    },
    [projectId, reload],
  );

  return (
    <div
      className="card"
      style={{ marginBottom: 16 }}
      data-testid="style-samples-panel"
    >
      <div className="detail-pane__title">文风样例（Sprint 15 / V1.3）</div>
      <ErrorBanner>{error}</ErrorBanner>
      <div className="muted small" style={{ marginBottom: 8 }}>
        注入 writer 上下文，模仿其句式、用词与节奏（不模仿内容）。
        单篇 ≤ {MAX_CONTENT_LEN} 字；单项目 ≤ 10 篇。
      </div>

      {samples.length === 0 ? (
        <div className="muted small" data-testid="style-samples-empty">
          该项目暂无文风样例。
        </div>
      ) : (
        <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 12px 0' }}>
          {samples.map((s) => (
            <li
              key={s.sample_id}
              className="card"
              style={{ marginBottom: 8, padding: 8 }}
              data-testid="style-samples-item"
            >
              <div
                style={{ display: 'flex', alignItems: 'center', gap: 8 }}
              >
                <strong data-testid={`style-samples-title-${s.sample_id}`}>
                  {s.title}
                </strong>
                <span className="muted small">{s.created_at}</span>
                <div style={{ flex: 1 }} />
                <button
                  className="btn btn--sm"
                  disabled={busy}
                  onClick={() => void handleDelete(s.sample_id)}
                  data-testid={`style-samples-delete-${s.sample_id}`}
                >
                  删除
                </button>
              </div>
              <div
                className="muted small"
                style={{
                  marginTop: 4,
                  maxHeight: 60,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {s.content.slice(0, 200)}
                {s.content.length > 200 ? '…' : ''}
              </div>
            </li>
          ))}
        </ul>
      )}

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
          marginTop: 8,
        }}
      >
        <input
          className="input"
          placeholder="标题"
          value={title}
          maxLength={MAX_TITLE_LEN}
          onChange={(e) => setTitle(e.target.value)}
          data-testid="style-samples-add-title"
        />
        <textarea
          className="input"
          placeholder="正文（散文样例；≤ 5000 字）"
          value={content}
          maxLength={MAX_CONTENT_LEN}
          onChange={(e) => setContent(e.target.value)}
          rows={4}
          data-testid="style-samples-add-content"
        />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button
            className="btn btn--sm btn--primary"
            disabled={
              busy ||
              !title.trim() ||
              !content.trim() ||
              content.length > MAX_CONTENT_LEN
            }
            onClick={() => void handleAdd()}
            data-testid="style-samples-add-submit"
          >
            新增文风样例
          </button>
          <span className="muted small">
            {content.length} / {MAX_CONTENT_LEN}
          </span>
        </div>
      </div>
    </div>
  );
}
