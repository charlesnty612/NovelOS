import { useParams } from 'react-router-dom';
import { ErrorBanner, InfoBanner } from '../components/ErrorBanner';
import { StatusBadge } from '../components/StatusBadge';
import { useApiCall } from '../hooks/useApiCall';
import {
  commitsApi,
  healthApi,
  projectsApi,
  storyStateApi,
} from '../api/endpoints';
import type {
  Commit,
  HealthResponse,
  Project,
  SnapshotResponse,
} from '../api/types';
import { formatDateTime } from '../utils/format';

export function ProjectOverviewPage() {
  const { pid } = useParams();
  const projectId = pid!;

  const { data: project, error: projectErr } = useApiCall<Project>(
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

  return (
    <div>
      <h1 className="section-title">项目总览</h1>
      <p className="section-subtitle">
        当前项目的基本信息、后端健康状态，以及 Story State 快照摘要。
      </p>

      <ErrorBanner>{projectErr}</ErrorBanner>

      {project ? (
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ fontSize: 18, fontWeight: 600 }}>{project.name}</div>
            <StatusBadge status={project.status} />
            <div style={{ flex: 1 }} />
            <span className="muted small">ID：{project.project_id}</span>
          </div>
          <div className="spacer" />
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
      ) : null}

      <div className="form-grid">
        <div className="card">
          <div className="detail-pane__title">后端健康</div>
          <ErrorBanner>{healthErr}</ErrorBanner>
          {health ? (
            <>
              <div>
                状态：<strong>{health.status}</strong>
              </div>
              {health.version ? <div>版本：{health.version}</div> : null}
              {health.tables ? (
                <div style={{ marginTop: 8 }}>
                  <div className="muted small">表行数</div>
                  <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
                    {Object.entries(health.tables).map(([k, v]) => (
                      <li key={k}>
                        <span className="muted">{k}</span>：{v}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          ) : (
            <div className="muted">加载中…</div>
          )}
        </div>

        <div className="card">
          <div className="detail-pane__title">State 快照摘要</div>
          <ErrorBanner>{snapErr}</ErrorBanner>
          <SnapshotSummary snap={snapshot} commitsLength={commits?.length} />
          <ErrorBanner>{commitErr}</ErrorBanner>
          {commits ? (
            <div className="muted small" style={{ marginTop: 4 }}>
              共 {commits.length} 条 commit
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

function SnapshotSummary({
  snap,
  commitsLength,
}: {
  snap: SnapshotResponse | null;
  commitsLength: number | undefined;
}) {
  if (!snap) return <div className="muted">加载中…</div>;
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
          ? `state_version 来自 /projects/{pid}/state 字段。`
          : commitsLength != null
          ? `快照接口未返回 version 字段，按 commits 长度推断：当前共 ${commitsLength} 次提交。`
          : '快照接口未返回 version 字段，且 commits 未加载。'}
      </InfoBanner>
      <div className="form-grid" style={{ marginTop: 8 }}>
        <Stat
          label="Characters"
          value={countIn(snap.characters, 'characters')}
          hint="快照中角色数"
        />
        <Stat
          label="Hooks"
          value={countArr(snap.hooks)}
          hint="未完结的伏笔"
        />
        <Stat
          label="Debts"
          value={countArr(snap.debts)}
          hint="叙事债务"
        />
        <Stat
          label="Events（累计/最近）"
          value={`${countArr(snap.events)} / ${countArr(snap.recent_events)}`}
          hint="事件总数 / 最近事件数"
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
      <div style={{ fontSize: 18, fontWeight: 600 }}>{value}</div>
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
