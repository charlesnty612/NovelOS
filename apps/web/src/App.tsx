import { Routes, Route, Navigate } from 'react-router-dom';
import { Layout } from './layout/Layout';
import { ProjectsListPage } from './pages/ProjectsListPage';
import { ProjectOverviewPage } from './pages/ProjectOverviewPage';
import { StoryBiblePage } from './pages/StoryBiblePage';
import { ChaptersPage } from './pages/ChaptersPage';
import { ChapterDetailPage } from './pages/ChapterDetailPage';
import { AiSettingsPage } from './pages/AiSettingsPage';
import { AiCallLogsPage } from './pages/AiCallLogsPage';
import { NotFoundPage } from './pages/NotFoundPage';

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ProjectsListPage />} />
        <Route path="projects">
          <Route path=":pid/overview" element={<ProjectOverviewPage />} />
          <Route path=":pid/bible/*" element={<StoryBiblePage />} />
          <Route path=":pid/chapters" element={<ChaptersPage />} />
          <Route path=":pid/chapters/:cid" element={<ChapterDetailPage />} />
          <Route path=":pid/ai" element={<AiSettingsPage />} />
        </Route>
        <Route path="ai-logs" element={<AiCallLogsPage />} />
        <Route path="*" element={<NotFoundPage />} />
        <Route path="index" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
