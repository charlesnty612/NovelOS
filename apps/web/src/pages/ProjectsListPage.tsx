import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ErrorBanner } from '../components/ErrorBanner';
import { StatusBadge } from '../components/StatusBadge';
import { EmptyState } from '../components/EmptyState';
import { useApiCall } from '../hooks/useApiCall';
import { projectsApi } from '../api/endpoints';
import type { Project, ProjectCreatePayload, ProjectStatus } from '../api/types';
import { formatDateTime } from '../utils/format';

export function ProjectsListPage() {
  const navigate = useNavigate();
  const { data, loading, error, reload } = useApiCall<Project[]>(
    () => projectsApi.list(),
    [],
  );
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Project | null>(null);

  return (
    <div>
      <div className="toolbar">
        <h1 className="section-title" style={{ margin: 0 }}>
          我的项目
        </h1>
        <div className="toolbar__spacer" />
        <button
          className="btn btn--primary"
          onClick={() => setShowCreate(true)}
          data-testid="new-project-btn"
        >
          + 新建项目
        </button>
      </div>
      <p className="section-subtitle">
        在 NovelOS 中，每个小说都是一个独立项目。点击进入项目后可维护总览、Story Bible、章节与 AI 设置。
      </p>

      <ErrorBanner>{error}</ErrorBanner>

      {loading ? (
        <div className="muted">加载中…</div>
      ) : data && data.length > 0 ? (
        <div className="project-grid" data-testid="project-grid">
          {data.map((p) => (
            <div
              key={p.project_id}
              className="project-card"
              onClick={() => navigate(`/projects/${p.project_id}/overview`)}
              data-testid={`project-card-${p.project_id}`}
            >
              <div className="project-card__title">{p.name}</div>
              <div className="project-card__meta">
                {p.genre ? `类型：${p.genre}` : '类型：未设置'}
                {' · '}
                {p.target_words
                  ? `目标：${p.target_words.toLocaleString()} 字`
                  : '目标：未设置'}
              </div>
              <div className="project-card__meta">{p.premise ?? '（暂无简介）'}</div>
              <div className="project-card__meta small">
                更新于 {formatDateTime(p.updated_at)}
              </div>
              <div
                style={{
                  display: 'flex',
                  gap: 8,
                  alignItems: 'center',
                  marginTop: 6,
                }}
              >
                <StatusBadge status={p.status} />
                <div style={{ flex: 1 }} />
                {p.status !== 'ARCHIVED' ? (
                  <button
                    className="btn btn--sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      setEditing(p);
                    }}
                  >
                    编辑
                  </button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState
          title="还没有项目"
          hint="点击右上角“新建项目”，开始你的第一部小说。"
          action={
            <button
              className="btn btn--primary"
              onClick={() => setShowCreate(true)}
            >
              + 新建项目
            </button>
          }
        />
      )}

      {showCreate ? (
        <ProjectFormModal
          title="新建项目"
          onCancel={() => setShowCreate(false)}
          onSubmit={async (payload) => {
            await projectsApi.create(payload as ProjectCreatePayload);
            setShowCreate(false);
            reload();
          }}
        />
      ) : null}

      {editing ? (
        <ProjectFormModal
          title={`编辑项目：${editing.name}`}
          initial={editing}
          onCancel={() => setEditing(null)}
          onSubmit={async (payload) => {
            await projectsApi.update(editing.project_id, payload);
            setEditing(null);
            reload();
          }}
          extraActions={({ submitting }) =>
            editing.status !== 'ARCHIVED' ? (
              <button
                className="btn btn--danger"
                disabled={submitting}
                onClick={async () => {
                  if (
                    !window.confirm(
                      `确认归档「${editing.name}」？归档后该项目不再出现在主列表中。`,
                    )
                  )
                    return;
                  await projectsApi.update(editing.project_id, {
                    status: 'ARCHIVED' as ProjectStatus,
                  });
                  setEditing(null);
                  reload();
                }}
              >
                归档
              </button>
            ) : null
          }
        />
      ) : null}
    </div>
  );
}

// ----------------------------------------------------------------- form modal
interface ProjectFormModalProps {
  title: string;
  initial?: Project;
  onCancel: () => void;
  onSubmit: (payload: Partial<ProjectCreatePayload> & { status?: ProjectStatus }) => Promise<void>;
  extraActions?: (args: { submitting: boolean }) => React.ReactNode;
}

function ProjectFormModal({
  title,
  initial,
  onCancel,
  onSubmit,
  extraActions,
}: ProjectFormModalProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [premise, setPremise] = useState(initial?.premise ?? '');
  const [genre, setGenre] = useState(initial?.genre ?? '');
  const [targetWords, setTargetWords] = useState<string>(
    initial?.target_words != null ? String(initial.target_words) : '',
  );
  const [status, setStatus] = useState<ProjectStatus>(initial?.status ?? 'ACTIVE');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!name.trim()) {
      setErr('项目名不能为空');
      return;
    }
    const tw = targetWords.trim() === '' ? null : Number(targetWords);
    if (tw !== null && (Number.isNaN(tw) || tw < 0)) {
      setErr('目标字数必须是 ≥0 的整数');
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim(),
        premise: premise.trim() === '' ? null : premise.trim(),
        genre: genre.trim() === '' ? null : genre.trim(),
        target_words: tw,
        status,
      });
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
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(15,20,35,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
      }}
      onClick={onCancel}
    >
      <form
        className="card"
        style={{ width: 480, maxWidth: '90vw' }}
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <div className="section-title">{title}</div>
        <ErrorBanner>{err}</ErrorBanner>
        <div className="form-row">
          <label>名称 *</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            data-testid="project-name"
          />
        </div>
        <div className="form-row">
          <label>简介</label>
          <textarea
            rows={3}
            value={premise}
            onChange={(e) => setPremise(e.target.value)}
            placeholder="一句话描述这个故事的前提（premise）"
          />
        </div>
        <div className="form-grid">
          <div className="form-row">
            <label>类型</label>
            <input
              value={genre}
              onChange={(e) => setGenre(e.target.value)}
              placeholder="如：科幻 / 悬疑 / 言情"
            />
          </div>
          <div className="form-row">
            <label>目标字数</label>
            <input
              type="number"
              min={0}
              value={targetWords}
              onChange={(e) => setTargetWords(e.target.value)}
            />
          </div>
        </div>
        {initial ? (
          <div className="form-row">
            <label>状态</label>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as ProjectStatus)}
            >
              <option value="ACTIVE">进行中</option>
              <option value="PAUSED">已暂停</option>
              <option value="ARCHIVED">已归档</option>
            </select>
          </div>
        ) : null}

        <div
          style={{
            display: 'flex',
            gap: 8,
            marginTop: 12,
            justifyContent: 'flex-end',
          }}
        >
          {extraActions ? extraActions({ submitting }) : null}
          <div style={{ flex: 1 }} />
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={submitting}
            data-testid="project-save"
          >
            {submitting ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </div>
  );
}
