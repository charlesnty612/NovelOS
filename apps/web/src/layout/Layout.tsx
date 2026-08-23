import { Outlet, useLocation, useParams, Link, matchPath } from 'react-router-dom';
import { useApiCall } from '../hooks/useApiCall';
import { projectsApi } from '../api/endpoints';
import type { Project } from '../api/types';

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
  const { data: project } = useApiCall<Project>(
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

  const projectRootActive = !!matchPath(
    { path: '/projects/:pid/*', end: false },
    location.pathname,
  );

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
              {project?.name ?? '加载中…'}
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
            {!projectRootActive ? null : null}
          </div>
        ) : null}
        <div style={{ flex: 1 }} />
        <div
          className="app-sidebar__section app-sidebar__item--muted"
          style={{ fontSize: 11 }}
        >
          Sprint 5 · 一期
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
              <span style={{ marginRight: 12 }}>ID: {project.project_id}</span>
              <span>状态：{project.status}</span>
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
