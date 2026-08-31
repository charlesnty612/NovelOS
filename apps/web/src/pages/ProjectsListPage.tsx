import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { ErrorBanner, InfoBanner } from '../components/ErrorBanner';
import { StatusBadge } from '../components/StatusBadge';
import { EmptyState } from '../components/EmptyState';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { useApiCall } from '../hooks/useApiCall';
import { backupApi, projectsApi } from '../api/endpoints';
import type {
  BackupPackage,
  Project,
  ProjectCreatePayload,
  ProjectStatus,
  ProjectWordBand,
} from '../api/types';
import { formatDateTime } from '../utils/format';

export function ProjectsListPage() {
  const navigate = useNavigate();
  const { data, loading, error, reload } = useApiCall<Project[]>(
    () => projectsApi.list(),
    [],
  );
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Project | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [importErr, setImportErr] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  // V3.22「交互反馈统一」：归档确认弹窗受控状态；保存成功短暂条幅。
  const [archiveTarget, setArchiveTarget] = useState<Project | null>(null);
  const [savedBanner, setSavedBanner] = useState<string | null>(null);

  return (
    <div>
      <div className="toolbar">
        <h1 className="section-title" style={{ margin: 0 }}>
          我的项目
        </h1>
        <div className="toolbar__spacer" />
        <button
          className="btn"
          onClick={() => setShowImport(true)}
          data-testid="import-backup-btn"
          title="从备份 JSON 包导入为新项目"
        >
          导入备份
        </button>
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
      {savedBanner ? (
        <div data-testid="projects-saved-banner">
          <InfoBanner>{savedBanner}</InfoBanner>
        </div>
      ) : null}

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
            setSavedBanner('已保存');
            void reload();
            window.setTimeout(() => setSavedBanner(null), 2500);
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
            setSavedBanner('已保存');
            void reload();
            window.setTimeout(() => setSavedBanner(null), 2500);
          }}
          extraActions={({ submitting }) =>
            editing.status !== 'ARCHIVED' ? (
              <button
                className="btn btn--danger"
                disabled={submitting}
                onClick={() => setArchiveTarget(editing)}
                data-testid="project-archive-btn"
              >
                归档
              </button>
            ) : null
          }
        />
      ) : null}

      {archiveTarget ? (
        <ConfirmDialog
          open={true}
          title="归档项目"
          body={`确认归档「${archiveTarget.name}」？归档后该项目不再出现在主列表中。`}
          confirmText="归档"
          danger
          testId="archive-confirm"
          onCancel={() => setArchiveTarget(null)}
          onConfirm={async () => {
            const target = archiveTarget;
            setArchiveTarget(null);
            await projectsApi.update(target.project_id, {
              status: 'ARCHIVED' as ProjectStatus,
            });
            setEditing(null);
            setSavedBanner('已归档');
            void reload();
            window.setTimeout(() => setSavedBanner(null), 2500);
          }}
        />
      ) : null}

      {showImport ? (
        <ImportBackupModal
          importing={importing}
          error={importErr}
          onCancel={() => {
            if (importing) return;
            setShowImport(false);
            setImportErr(null);
          }}
          onSubmit={async (file) => {
            setImporting(true);
            setImportErr(null);
            try {
              const text = await file.text();
              const parsed = JSON.parse(text) as BackupPackage;
              const newProject = await backupApi.importBackup(parsed);
              setShowImport(false);
              reload();
              // 导入成功后直接跳到新项目总览
              navigate(`/projects/${newProject.project_id}/overview`);
            } catch (e: unknown) {
              if (e instanceof ApiError) {
                setImportErr(`${e.status}: ${e.detail}`);
              } else if (e instanceof Error) {
                setImportErr(e.message);
              } else {
                setImportErr('导入失败');
              }
            } finally {
              setImporting(false);
            }
          }}
        />
      ) : null}
    </div>
  );
}

// ----------------------------------------------------------------- import modal
interface ImportBackupModalProps {
  importing: boolean;
  error: string | null;
  onCancel: () => void;
  onSubmit: (file: File) => Promise<void>;
}

function ImportBackupModal({
  importing,
  error,
  onCancel,
  onSubmit,
}: ImportBackupModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [localErr, setLocalErr] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLocalErr(null);
    if (!file) {
      setLocalErr('请选择备份 JSON 文件');
      return;
    }
    if (!file.name.endsWith('.json')) {
      setLocalErr('文件必须是 .json 后缀');
      return;
    }
    await onSubmit(file);
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
      onClick={() => {
        if (!importing) onCancel();
      }}
    >
      <form
        className="card"
        style={{ width: 480, maxWidth: '90vw' }}
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
        data-testid="import-backup-modal"
      >
        <div className="section-title">从备份导入项目</div>
        <p className="muted small" style={{ marginTop: 4 }}>
          选择一份此前导出的 NovelOS 备份 JSON（format = <code>novelos-backup</code>，
          version = 1）。导入会创建一份**新**项目，不影响原项目。
        </p>
        <ErrorBanner>{error ?? localErr}</ErrorBanner>
        <div className="form-row">
          <label>备份文件 *</label>
          <input
            type="file"
            accept="application/json,.json"
            disabled={importing}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            data-testid="import-backup-file"
          />
          {file ? (
            <div className="muted small" style={{ marginTop: 4 }}>
              已选择：{file.name}（{Math.round(file.size / 1024)} KB）
            </div>
          ) : null}
        </div>
        <div
          style={{
            display: 'flex',
            gap: 8,
            marginTop: 12,
            justifyContent: 'flex-end',
          }}
        >
          <button
            type="button"
            className="btn"
            onClick={onCancel}
            disabled={importing}
            data-testid="import-backup-cancel"
          >
            取消
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={importing || !file}
            data-testid="import-backup-submit"
          >
            {importing ? '导入中…' : '导入为新项目'}
          </button>
        </div>
      </form>
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
  // V3.7：字数带覆盖三个输入。空串 = 该键走默认；全空 = 提交时省略 word_band 键
  // （创建场景无覆盖；编辑场景保留原值）。编辑模式下显示「清除」按钮可显式传 null。
  // 初始值来自 initial.word_band；undefined/null（无覆盖）→ 留空（与「全空=默认」语义一致）。
  const initBand = initial?.word_band ?? undefined;
  const [bandLowRatio, setBandLowRatio] = useState<string>(
    initBand?.low_ratio != null ? String(initBand.low_ratio) : '',
  );
  const [bandHighRatio, setBandHighRatio] = useState<string>(
    initBand?.high_ratio != null ? String(initBand.high_ratio) : '',
  );
  const [bandFloor, setBandFloor] = useState<string>(
    initBand?.floor != null ? String(initBand.floor) : '',
  );
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
    // V3.7：word_band 三键解析（空串 → 该键缺省）
    const parseOpt = (s: string): number | null => {
      const v = s.trim();
      if (v === '') return null;
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    };
    const lr = parseOpt(bandLowRatio);
    const hr = parseOpt(bandHighRatio);
    const fl = parseOpt(bandFloor);
    if (
      (bandLowRatio.trim() !== '' && lr === null) ||
      (bandHighRatio.trim() !== '' && hr === null) ||
      (bandFloor.trim() !== '' && fl === null)
    ) {
      setErr('字数带覆盖必须是数字');
      return;
    }
    // V3.7：word_band payload 构建
    // - 三键全空 → 提交时省略 word_band 键（保留后端原值；创建场景无覆盖）
    // - 任一非空 → 提交 dict（后端走 resolve_band_config 校验）
    const allEmpty =
      bandLowRatio.trim() === '' && bandHighRatio.trim() === '' && bandFloor.trim() === '';
    const payload: Partial<ProjectCreatePayload> & {
      status?: ProjectStatus;
      word_band?: ProjectWordBand | null;
    } = {
      name: name.trim(),
      premise: premise.trim() === '' ? null : premise.trim(),
      genre: genre.trim() === '' ? null : genre.trim(),
      target_words: tw,
      status,
    };
    if (!allEmpty) {
      const band: ProjectWordBand = {};
      if (lr !== null) band.low_ratio = lr;
      if (hr !== null) band.high_ratio = hr;
      if (fl !== null) band.floor = fl;
      payload.word_band = band;
    }
    setSubmitting(true);
    try {
      await onSubmit(payload);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  // V3.7：编辑模式下「清除覆盖」——把三个输入清空并显式提交 null（落 DB NULL）。
  // 仅在 initial?.word_band 非空（即项目当前有覆盖）时显示该按钮。
  const handleClearBand = async () => {
    setErr(null);
    setBandLowRatio('');
    setBandHighRatio('');
    setBandFloor('');
    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim(),
        premise: premise.trim() === '' ? null : premise.trim(),
        genre: genre.trim() === '' ? null : genre.trim(),
        target_words: targetWords.trim() === '' ? null : Number(targetWords),
        status,
        word_band: null,
      });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  const hasBandOverride =
    initial?.word_band != null &&
    (initial.word_band.low_ratio != null ||
      initial.word_band.high_ratio != null ||
      initial.word_band.floor != null);

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

        {/* V3.7：字数带覆盖（可选）——留空=不覆盖；与后端 resolve_band_config 校验对齐。
            三个键全留空：提交时省略 word_band 键；任一非空：提交 dict。 */}
        <div className="form-row">
          <label>字数带覆盖（可选）</label>
          <p className="muted small" style={{ margin: '0 0 6px' }}>
            调整「偏离目标 ±15% 警告」与「下限 1200 字」的项目级覆盖；留空使用后端默认。
          </p>
          <div className="form-grid">
            <div className="form-row">
              <label>下带比例</label>
              <input
                type="number"
                step="0.01"
                min={0.01}
                max={1}
                value={bandLowRatio}
                placeholder="0.85"
                onChange={(e) => setBandLowRatio(e.target.value)}
                data-testid="project-band-low-ratio"
              />
            </div>
            <div className="form-row">
              <label>上带比例</label>
              <input
                type="number"
                step="0.01"
                min={1}
                value={bandHighRatio}
                placeholder="1.15"
                onChange={(e) => setBandHighRatio(e.target.value)}
                data-testid="project-band-high-ratio"
              />
            </div>
          </div>
          <div className="form-row" style={{ marginTop: 6 }}>
            <label>下限字数</label>
            <input
              type="number"
              min={0}
              step={50}
              value={bandFloor}
              placeholder="1200"
              onChange={(e) => setBandFloor(e.target.value)}
              data-testid="project-band-floor"
            />
          </div>
          {hasBandOverride ? (
            <button
              type="button"
              className="btn btn--sm"
              onClick={handleClearBand}
              disabled={submitting}
              data-testid="project-clear-band"
              style={{ marginTop: 6 }}
            >
              清除字数带覆盖
            </button>
          ) : null}
        </div>

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
