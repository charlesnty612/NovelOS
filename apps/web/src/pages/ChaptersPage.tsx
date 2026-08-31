import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { chaptersApi } from '../api/endpoints';
import type { Chapter, ChapterCreatePayload } from '../api/types';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { ChapterStatusBadge } from '../components/ChapterStatusBadge';
import { formatDateTime } from '../utils/format';

export function ChaptersPage() {
  const { pid } = useParams();
  const navigate = useNavigate();
  const projectId = pid!;
  const [list, setList] = useState<Chapter[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const reload = async () => {
    setLoading(true);
    setErr(null);
    try {
      const rows = await chaptersApi.listByProject(projectId);
      setList(rows);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  return (
    <div>
      <div className="toolbar">
        <h1 className="section-title" style={{ margin: 0 }}>
          章节
        </h1>
        <div className="toolbar__spacer" />
        <button
          className="btn btn--primary"
          data-testid="new-chapter-btn"
          onClick={() => setCreating(true)}
        >
          + 新建章节
        </button>
      </div>
      <p className="section-subtitle">
        按章节号管理章节大纲、草稿与 AI 工作流。点击进入章节详情以运行 plan / write / review /
        commit 流程。
      </p>

      <ErrorBanner>{err}</ErrorBanner>

      {loading ? (
        <div className="muted">加载中…</div>
      ) : list.length === 0 ? (
        <EmptyState
          title="还没有章节"
          hint="点击右上角「新建章节」，从 chapter 1 开始。"
          action={
            <button
              className="btn btn--primary"
              onClick={() => setCreating(true)}
            >
              + 新建章节
            </button>
          }
        />
      ) : (
        <div className="table-wrap">
        <table className="table" data-testid="chapter-list">
          <thead>
            <tr>
              <th style={{ width: 80 }}>章号</th>
              <th>标题</th>
              <th style={{ width: 120 }}>状态</th>
              <th style={{ width: 180 }}>最近更新</th>
              <th className="right" style={{ width: 100 }}>
                操作
              </th>
            </tr>
          </thead>
          <tbody>
            {list.map((c) => (
              <tr
                key={c.chapter_id}
                onClick={() =>
                  navigate(`/projects/${projectId}/chapters/${c.chapter_id}`)
                }
                style={{ cursor: 'pointer' }}
                data-testid={`chapter-row-${c.chapter_id}`}
              >
                <td>第 {c.number} 章</td>
                <td>{c.title ?? <span className="muted">（未命名）</span>}</td>
                <td>
                  <ChapterStatusBadge status={c.status} />
                </td>
                <td className="muted small">{formatDateTime(c.updated_at)}</td>
                <td className="right">
                  <button
                    className="btn btn--sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(
                        `/projects/${projectId}/chapters/${c.chapter_id}`,
                      );
                    }}
                  >
                    进入
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      {creating ? (
        <ChapterCreateModal
          existingNumbers={list.map((c) => c.number)}
          onCancel={() => setCreating(false)}
          onSubmit={async (payload) => {
            await chaptersApi.create(projectId, payload);
            setCreating(false);
            await reload();
          }}
        />
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------- modal
interface ChapterCreateModalProps {
  existingNumbers: number[];
  onCancel: () => void;
  onSubmit: (payload: ChapterCreatePayload) => Promise<void>;
}

function ChapterCreateModal({
  existingNumbers,
  onCancel,
  onSubmit,
}: ChapterCreateModalProps) {
  const suggestedNumber =
    existingNumbers.length === 0
      ? 1
      : Math.max(...existingNumbers) + 1;
  const [number, setNumber] = useState<string>(String(suggestedNumber));
  const [title, setTitle] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const n = Number(number);
    if (!Number.isInteger(n) || n < 1) {
      setErr('章号必须是 ≥1 的整数');
      return;
    }
    if (existingNumbers.includes(n)) {
      setErr(`章号 ${n} 已存在，请使用其他章号`);
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit({ number: n, title: title.trim() || null });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      style={modalBackdrop}
      onClick={onCancel}
    >
      <form
        className="card"
        style={{ width: 420, maxWidth: '90vw' }}
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <div className="section-title">新建章节</div>
        <ErrorBanner>{err}</ErrorBanner>

        <div className="form-grid">
          <div className="form-row">
            <label>章号 *</label>
            <input
              type="number"
              min={1}
              value={number}
              onChange={(e) => setNumber(e.target.value)}
              required
              data-testid="chapter-number"
            />
          </div>
          <div className="form-row">
            <label>标题</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="（可选）"
              data-testid="chapter-title"
            />
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={submitting}
            data-testid="chapter-save"
          >
            {submitting ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </div>
  );
}

const modalBackdrop: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(15,20,35,0.4)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 100,
};
