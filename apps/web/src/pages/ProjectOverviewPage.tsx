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
import { StateHistoryPanel } from './StateHistoryPanel';
import { DeliveryPanel } from './DeliveryPanel';
import { useApiCall } from '../hooks/useApiCall';
import {
  backupApi,
  branchesApi,
  chaptersApi,
  commitsApi,
  healthApi,
  projectsApi,
  storyStateApi,
  styleSamplesApi,
} from '../api/endpoints';
import type {
  Branch,
  Chapter,
  Commit,
  HealthResponse,
  Project,
  SnapshotResponse,
  StyleSample,
} from '../api/types';
import { formatDateTime } from '../utils/format';
import { isDebtOverdue, isHookOverdue } from '../utils/ledgerState';
import { countOpenSnapshotItems, countSnapshot } from '../utils/snapshotCounts';

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
  // G9：「这本书现在什么状态」的第一问是「写到第几章」，此前概览页完全答不了
  // （只有「共 N 次提交」），作者必须跳去章节页自己数。
  const { data: chapters, error: chapterErr } = useApiCall<Chapter[]>(
    () => chaptersApi.listByProject(projectId),
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

      {/* G6：状态版本历史 + diff + 回滚（后端三端点早已存在，此前 UI 零入口）。 */}
      {commits ? (
        <StateHistoryPanel
          projectId={projectId}
          commits={commits}
          onChanged={() => reloadProject()}
        />
      ) : null}

      {/* G6：交付判定 + 签约自检（schema 早已生成，UI 此前不接）。 */}
      <DeliveryPanel projectId={projectId} />

      {/* V1.4 / Sprint 16：导出（整书 / 单章 / 番茄投稿包） */}
      <ExportPanel projectId={projectId} />

      <div className="form-grid">
        <div className="card">
          <div className="detail-pane__title">数据库状态</div>
          <ErrorBanner>{healthErr}</ErrorBanner>
          {health ? <HealthSummary health={health} /> : <Loading />}
        </div>

        <div className="card">
          <div className="detail-pane__title">这本书现在什么状态</div>
          <ErrorBanner>{snapErr}</ErrorBanner>
          <ErrorBanner>{chapterErr}</ErrorBanner>
          <SnapshotSummary
            snap={snapshot}
            commitsLength={commits?.length}
            chapters={chapters}
          />
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

/**
 * 「这本书现在什么状态」聚合视图（G9，2026-10-15）。
 *
 * 改动前这块只有四个 Stat，且其中两个**恒为 0**：
 *   - `countIn(snap.characters, 'characters')` —— `characters` 后端是 **list**，
 *     被 `endpoints.ts` 的旧归一化用 `normObj` 拍成了 `{}`；
 *   - `countArr(snap.events)` —— `events` 后端是 **dict**（按 event_id 键），
 *     被 `normArr` 拍成了 `[]`。
 * 叠加上「版本号取 `version` 还是 `state_version`」的猜测分支，整块面板没有一处
 * 读数是可信的，而旧夹具照抄了归一化后的形状（`characters: {}` / `events: []`），
 * 于是**没有任何测试能抓到它**。现在三个读数全部来自后端契约 + 单一口径
 * `utils/snapshotCounts.ts`，逾期判定复用 `utils/ledgerState`（与台账页同源）。
 */
function SnapshotSummary({
  snap,
  commitsLength,
  chapters,
}: {
  snap: SnapshotResponse | null;
  commitsLength: number | undefined;
  chapters: Chapter[] | null | undefined;
}) {
  if (!snap) return <Loading />;
  const counts = countSnapshot(snap);
  const open = countOpenSnapshotItems(snap);
  const chapterRefs = chapters ?? [];
  const maxNo = chapterRefs.reduce((m, c) => (c.number > m ? c.number : m), 0);
  const done = chapterRefs.filter(
    (c) => c.status === 'COMMITTED' || c.status === 'RELEASED',
  ).length;

  // 逾期判定复用台账页同一个函数（判据单点）。快照里的 hook/debt 行与人工台账行
  // 字段同名同形（后端 `_load_hooks` / `_load_debts` 写出的就是这些键）。
  const overdueHooks = (snap.hooks ?? []).filter((h) =>
    isHookOverdue(
      {
        status: typeof h['status'] === 'string' ? (h['status'] as string) : '',
        expected_payoff_chapter_id:
          typeof h['expected_payoff_chapter_id'] === 'string'
            ? (h['expected_payoff_chapter_id'] as string)
            : null,
      },
      chapterRefs,
    ),
  ).length;
  const overdueDebts = (snap.debts ?? []).filter((d) =>
    isDebtOverdue(
      {
        status: typeof d['status'] === 'string' ? (d['status'] as string) : '',
        deadline_chapter_id:
          typeof d['deadline_chapter_id'] === 'string'
            ? (d['deadline_chapter_id'] as string)
            : null,
      },
      chapterRefs,
    ),
  ).length;

  return (
    <>
      <InfoBanner>
        当前故事状态版本：v{snap.state_version}
        {commitsLength != null ? ` · 共 ${commitsLength} 次提交` : ''}
        {maxNo > 0 ? ` · 写到第 ${maxNo} 章（已定稿 ${done} 章）` : ' · 尚无章节'}
      </InfoBanner>

      <div className="detail-pane__title" style={{ marginTop: 10 }}>
        待办
      </div>
      <div className="form-grid">
        <Stat
          label="未回收伏笔"
          value={open.openHooks}
          hint={overdueHooks > 0 ? `其中 ${overdueHooks} 条已逾期` : '无逾期'}
          testId="stat-open-hooks"
        />
        <Stat
          label="未偿清债务"
          value={open.openDebts}
          hint={overdueDebts > 0 ? `其中 ${overdueDebts} 条已逾期` : '无逾期'}
          testId="stat-open-debts"
        />
        <Stat label="角色" value={counts.characters} hint="快照中的角色数" testId="stat-characters" />
        <Stat
          label="事件"
          value={`${counts.events} / ${counts.recentEvents}`}
          hint="累计 / 最近"
          testId="stat-events"
        />
      </div>

      {/* G8：同一本书上「伏笔数」有两个来源，此前并排摆着却没有任何说明。 */}
      <div className="muted small" data-testid="overview-dual-source-note">
        以上数字来自<b>状态快照</b>——由每次 commit 的 observer 自动推进。
        「设定 → 台账」页里的同名计数是<b>人工维护面</b>，两者<b>不自动同步</b>，
        数值不同是正常的（快照反映已提交章节的观察结果，台账反映你此刻的意图）。
      </div>
    </>
  );
}

function Stat({
  label,
  value,
  hint,
  testId,
}: {
  label: string;
  value: string | number;
  hint: string;
  testId: string;
}) {
  return (
    <div data-testid={testId}>
      <div className="muted small">{label}</div>
      <div className="stat__value">{value}</div>
      <div className="muted small">{hint}</div>
    </div>
  );
}
