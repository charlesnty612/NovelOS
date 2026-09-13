import { Outlet, useLocation, useParams, Link, matchPath } from 'react-router-dom';
import { useApiCall } from '../hooks/useApiCall';
import { projectsApi } from '../api/endpoints';
import { Loading } from '../components/Loading';
import { StatusBadge } from '../components/StatusBadge';
import type { Project } from '../api/types';

/** 侧栏底部版本标识；发版时随 pyproject / package.json 一同对齐（AGENTS 版本纪律）。 */
const APP_VERSION_LABEL = 'NovelOS v3.9.0';

interface NavItem {
  path: string;
  label: string;
  end?: boolean;
}

export function Layout() {
  const location = useLocation();
  const params = useParams();
  const pid = params.pid;

  // 当前项目信息（仅当进入项目内部时加载，顶部栏展示名称）
  const {
    data: project,
    error: projectErr,
    reload: reloadProject,
  } = useApiCall<Project>(
    async () => (pid ? projectsApi.get(pid) : Promise.reject(new Error('no project'))),
    [pid],
  );

  const projectNav: NavItem[] = pid
    ? [
        { path: `/projects/${pid}/overview`, label: '总览' },
        { path: `/projects/${pid}/bible`, label: 'Story Bible' },
        { path: `/projects/${pid}/chapters`, label: '章节' },
        { path: `/projects/${pid}/ai`, label: 'AI 设置' },
      ]
    : [];

  const inProject = !!pid;

  const isActive = (item: NavItem) =>
    item.end
      ? matchPath({ path: item.path, end: true }, location.pathname)
      : !!matchPath({ path: item.path + '/*', end: false }, location.pathname);

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-sidebar__brand">NovelOS</div>
        <div className="app-sidebar__section">
          <div className="app-sidebar__section-title">导航</div>
          <Link
            to="/"
            className={`app-sidebar__item ${!inProject ? 'app-sidebar__item--active' : ''}`}
          >
            项目列表
          </Link>
        </div>
        {inProject ? (
          <div className="app-sidebar__section">
            <div className="app-sidebar__section-title">
              {project ? (
                project.name
              ) : projectErr ? (
                <button
                  type="button"
                  className="app-sidebar__retry"
                  onClick={reloadProject}
                  title={projectErr}
                  data-testid="sidebar-project-retry"
                >
                  项目信息加载失败，点击重试
                </button>
              ) : (
                <Loading className="app-sidebar__loading" />
              )}
            </div>
            {projectNav.map((it) => (
              <Link
                key={it.path}
                to={it.path}
                className={`app-sidebar__item ${isActive(it) ? 'app-sidebar__item--active' : ''}`}
              >
                {it.label}
              </Link>
            ))}
          </div>
        ) : null}
        <div className="app-sidebar__spacer" />
        <div className="app-sidebar__section app-sidebar__item--muted app-sidebar__version">
          {APP_VERSION_LABEL}
        </div>
      </aside>

      <header className="app-topbar">
        <div className="app-topbar__title">
          {project ? `项目：${project.name}` : 'NovelOS'}
        </div>
        <div className="app-topbar__spacer" />
        <div className="app-topbar__meta">
          {project ? (
            <>
              <span className="app-topbar__id" title="项目 ID">
                {project.project_id}
              </span>
              <StatusBadge status={project.status} />
            </>
          ) : (
            <span>本地优先 · 单机</span>
          )}
        </div>
      </header>

      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
