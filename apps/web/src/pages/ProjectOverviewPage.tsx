import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { ErrorBanner, InfoBanner } from '../components/ErrorBanner';
import { Loading, SkeletonRows } from '../components/Loading';
import { ExportPanel } from '../components/ExportPanel';
import { ProjectInitPanel } from '../components/ProjectInitPanel';
import { StatusBadge } from '../components/StatusBadge';
import { StyleSamplesPanel } from '../components/StyleSamplesPanel';
import { BranchesPanel } from '../components/BranchesPanel';
import { useApiCall } from '../hooks/useApiCall';
import {
  backupApi,
  branchesApi,
  commitsApi,
  healthApi,
  projectsApi,
  storyStateApi,
  styleSamplesApi,
} from '../api/endpoints';
import type {
  Branch,
  Commit,
  HealthResponse,
  Project,
  SnapshotResponse,
  StyleSample,
} from '../api/types';
import { formatDateTime } from '../utils/format';

export function ProjectOverviewPage() {
  const { pid } = useParams();
  const projectId = pid!;
  const [backupErr, setBackupErr] = useState<string | null>(null);
  const [backingUp, setBackingUp] = useState(false);

  const { data: project, error: projectErr, reload: reloadProject } = useApiCall<Project>(
    () => projectsApi.get(projectId),
    [projectId],
  );
  const { data: health, error: healthErr } = useApiCall<HealthResponse>(
    () => healthApi.get(),
    [],
  );
  const { data: snapshot, error: snapErr } = useApiCall<SnapshotResponse>(
    () => storyStateApi.getCurrent(projectId),
    [projectId],
  );
  const { data: commits, error: commitErr } = useApiCall<Commit[]>(
    () => commitsApi.list(projectId),
    [projectId],
  );
  // Sprint 15 / V1.3：项目级文风样例（初始注入；面板内部自己 refetch）。
  const { data: styleSamples, error: styleErr } = useApiCall<StyleSample[]>(
    () => styleSamplesApi.list(projectId),
    [projectId],
  );
  // V1.5 / Sprint 17：What-if 分支（初始注入；面板内部自己 refetch）。
  const { data: branches, error: branchesErr } = useApiCall<Branch[]>(
    () => branchesApi.list(projectId),
    [projectId],
  );

  const handleBackup = async () => {
    setBackupErr(null);
    setBackingUp(true);
    try {
      await backupApi.downloadBackup(projectId);
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        setBackupErr(`${e.status}: ${e.detail}`);
      } else if (e instanceof Error) {
        setBackupErr(e.message);
      } else {
        setBackupErr('备份下载失败');
      }
    } finally {
      setBackingUp(false);
    }
  };

  if (!project) {
    return (
      <div>
        <h1 className="section-title">项目总览</h1>
        <p className="section-subtitle">
          当前项目的基本信息、数据库状态与故事状态快照。
        </p>
        <ErrorBanner>{projectErr}</ErrorBanner>
        {projectErr ? null : (
          <div className="card" data-testid="overview-loading">
            <Loading text="正在加载项目总览…" />
            <div className="spacer" />
            <SkeletonRows n={3} />
          </div>
        )}
      </div>
    );
  }

  return (
    <div>
      <h1 className="section-title">项目总览</h1>
      <p className="section-subtitle">
        当前项目的基本信息、数据库状态与故事状态快照。
      </p>

      <ErrorBanner>{projectErr}</ErrorBanner>
      <ErrorBanner>{backupErr}</ErrorBanner>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="toolbar">
          <div className="card-title">{project.name}</div>
          <StatusBadge status={project.status} />
          <div className="toolbar__spacer" />
          <button
            type="button"
            className="btn btn--sm"
            disabled={backingUp}
            onClick={() => void handleBackup()}
            data-testid="project-backup-btn"
            title="下载整项目 JSON 备份（含 38 张业务表；不含 API key）"
          >
            {backingUp ? '备份中…' : '下载备份'}
          </button>
          <span className="muted small">ID：{project.project_id}</span>
        </div>
        <div className="form-grid">
          <Field label="类型" value={project.genre ?? '未设置'} />
          <Field
            label="目标字数"
            value={
              project.target_words != null
                ? project.target_words.toLocaleString()
                : '未设置'
            }
          />
          <Field label="创建时间" value={formatDateTime(project.created_at)} />
          <Field label="最近更新" value={formatDateTime(project.updated_at)} />
        </div>
        {project.premise ? (
          <>
            <div className="spacer" />
            <div className="muted small">简介</div>
            <div>{project.premise}</div>
          </>
        ) : null}
      </div>

      {/* P1 project-init：项目总览页「AI 初始化设定」入口 */}
      <ProjectInitPanel
        projectId={projectId}
        project={project}
        onDone={() => reloadProject()}
      />

      {/* Sprint 15 / V1.3：项目级文风样例管理 */}
      <ErrorBanner>{styleErr}</ErrorBanner>
      <StyleSamplesPanel
        projectId={projectId}
        initialSamples={styleSamples ?? []}
      />

      {/* V1.5 / Sprint 17：What-if 分支（创建/查看/promote） */}
      <ErrorBanner>{branchesErr}</ErrorBanner>
      <BranchesPanel projectId={projectId} initialBranches={branches ?? []} />

      {/* V1.4 / Sprint 16：导出（整书 / 单章 / 番茄投稿包） */}
      <ExportPanel projectId={projectId} />

      <div className="form-grid">
        <div className="card">
          <div className="detail-pane__title">数据库状态</div>
          <ErrorBanner>{healthErr}</ErrorBanner>
          {health ? <HealthSummary health={health} /> : <Loading />}
        </div>

        <div className="card">
          <div className="detail-pane__title">故事状态快照</div>
          <ErrorBanner>{snapErr}</ErrorBanner>
          <SnapshotSummary snap={snapshot} commitsLength={commits?.length} />
          <ErrorBanner>{commitErr}</ErrorBanner>
          {commits ? (
            <div className="muted small" style={{ marginTop: 4 }}>
              共 {commits.length} 次提交
              {commits[0] ? `；最新 v${commits[0].version}` : ''}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="muted small">{label}</div>
      <div>{value}</div>
    </div>
  );
}

/** /api/health 的 tables 实际是「业务表数量」（int）；前端类型仍标注为
 *  Record<string, number>，故两种形状都兼容，避免把面板渲染成空列表。 */
function readTables(
  tables: HealthResponse['tables'],
): { count: number; rows: Record<string, number> | null } | null {
  const raw: unknown = tables;
  if (typeof raw === 'number') return { count: raw, rows: null };
  if (raw && typeof raw === 'object') {
    const rows = raw as Record<string, number>;
    return { count: Object.keys(rows).length, rows };
  }
  return null;
}

function HealthSummary({ health }: { health: HealthResponse }) {
  const tables = readTables(health.tables);
  return (
    <>
      <div data-testid="health-summary">
        数据库{tables ? ` · ${tables.count} 张表` : ''} ·{' '}
        <strong>{health.status === 'ok' ? '正常' : health.status}</strong>
      </div>
      {health.version ? (
        <div className="muted small">后端版本 {health.version}</div>
      ) : null}
      {tables?.rows ? (
        <details className="disclosure">
          <summary>查看各表行数</summary>
          <ul className="disclosure__list">
            {Object.entries(tables.rows).map(([name, rows]) => (
              <li key={name}>
                <span className="muted">{name}</span>：{rows}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </>
  );
}

function SnapshotSummary({
  snap,
  commitsLength,
}: {
  snap: SnapshotResponse | null;
  commitsLength: number | undefined;
}) {
  if (!snap) return <Loading />;
  const version =
    typeof snap.version === 'number'
      ? snap.version
      : typeof snap.state_version === 'number'
      ? snap.state_version
      : null;
  return (
    <>
      <InfoBanner>
        {version != null
          ? `当前故事状态版本：v${version}`
          : commitsLength != null
          ? `暂未取到状态版本，按提交次数推断：共 ${commitsLength} 次提交。`
          : '暂未取到状态版本。'}
      </InfoBanner>
      <div className="form-grid" style={{ marginTop: 8 }}>
        <Stat
          label="角色"
          value={countIn(snap.characters, 'characters')}
          hint="快照中的角色数"
        />
        <Stat label="伏笔" value={countArr(snap.hooks)} hint="未完结的伏笔" />
        <Stat label="债务" value={countArr(snap.debts)} hint="叙事债务" />
        <Stat
          label="事件"
          value={`${countArr(snap.events)} / ${countArr(snap.recent_events)}`}
          hint="累计 / 最近"
        />
      </div>
    </>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint: string;
}) {
  return (
    <div>
      <div className="muted small">{label}</div>
      <div className="stat__value">{value}</div>
      <div className="muted small">{hint}</div>
    </div>
  );
}

function countArr(v: unknown): number {
  return Array.isArray(v) ? v.length : 0;
}

function countIn(v: unknown, key: string): number {
  if (Array.isArray(v)) return v.length;
  if (v && typeof v === 'object') {
    const inner = (v as Record<string, unknown>)[key];
    if (Array.isArray(inner)) return inner.length;
    if (inner && typeof inner === 'object') return Object.keys(inner).length;
  }
  return 0;
}
