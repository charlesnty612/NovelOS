import { useCallback, useRef, type KeyboardEvent } from 'react';
import { Navigate, useParams, useSearchParams } from 'react-router-dom';
import { CanonTab } from './bible/CanonTab';
import { CharacterTab } from './bible/CharacterTab';
import { WorldTab } from './bible/WorldTab';
import { PlotTab } from './bible/PlotTab';
import { LedgerTab } from './bible/LedgerTab';
import { GenreTab } from './bible/GenreTab';

// tab 清单即 URL 契约：?tab=<key>（characters / world / plot / ledger / canon / genre）。
const BIBLE_TABS = [
  { key: 'characters', label: '角色' },
  { key: 'world', label: '世界' },
  { key: 'plot', label: '剧情' },
  { key: 'ledger', label: '伏笔与债务' },
  { key: 'canon', label: '参照系' },
  { key: 'genre', label: '题材' },
] as const;

type BibleTab = (typeof BIBLE_TABS)[number]['key'];

const DEFAULT_TAB: BibleTab = 'characters';

function isBibleTab(value: string | null | undefined): value is BibleTab {
  return value != null && BIBLE_TABS.some((t) => t.key === value);
}

export function StoryBiblePage() {
  const params = useParams();
  const projectId = params['pid']!;
  const splat = params['*'] ?? '';
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const activeTab: BibleTab = isBibleTab(tabParam) ? tabParam : DEFAULT_TAB;
  const tabRefs = useRef<Array<HTMLDivElement | null>>([]);

  // tab 状态以 URL 为唯一事实源（刷新 / 后退 / 分享都还原到同一页签）。
  const selectTab = useCallback(
    (next: BibleTab) => {
      const nextParams = new URLSearchParams(searchParams);
      nextParams.set('tab', next);
      setSearchParams(nextParams);
    },
    [searchParams, setSearchParams],
  );

  // 路由声明是 :pid/bible/*；子路径不是合法页签 → 兜底默认页（保留合法 ?tab=）。
  if (splat !== '') {
    const nextTab = isBibleTab(splat)
      ? splat
      : isBibleTab(tabParam)
        ? tabParam
        : DEFAULT_TAB;
    return <Navigate to={`/projects/${projectId}/bible?tab=${nextTab}`} replace />;
  }

  const handleTabKeyDown = (
    e: KeyboardEvent<HTMLDivElement>,
    index: number,
  ) => {
    const last = BIBLE_TABS.length - 1;
    let nextIndex: number;
    if (e.key === 'ArrowRight') nextIndex = index === last ? 0 : index + 1;
    else if (e.key === 'ArrowLeft') nextIndex = index === 0 ? last : index - 1;
    else if (e.key === 'Home') nextIndex = 0;
    else if (e.key === 'End') nextIndex = last;
    else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      selectTab(BIBLE_TABS[index].key);
      return;
    } else return;
    e.preventDefault();
    selectTab(BIBLE_TABS[nextIndex].key);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <div>
      <h1 className="section-title">Story Bible</h1>
      <p className="section-subtitle">
        管理本项目的角色、世界设定与剧情事件。所有数据都按项目隔离。
      </p>

      <div className="tabs" role="tablist" aria-label="Story Bible 页签">
        {BIBLE_TABS.map((t, i) => (
          <div
            key={t.key}
            ref={(el) => {
              tabRefs.current[i] = el;
            }}
            role="tab"
            id={`bible-tab-${t.key}`}
            aria-selected={activeTab === t.key}
            aria-controls={`bible-panel-${t.key}`}
            tabIndex={activeTab === t.key ? 0 : -1}
            className={`tabs__tab ${activeTab === t.key ? 'tabs__tab--active' : ''}`}
            onClick={() => selectTab(t.key)}
            onKeyDown={(e) => handleTabKeyDown(e, i)}
            data-testid={`tab-${t.key}`}
          >
            {t.label}
          </div>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`bible-panel-${activeTab}`}
        aria-labelledby={`bible-tab-${activeTab}`}
      >
        {activeTab === 'characters' ? <CharacterTab projectId={projectId} /> : null}
        {activeTab === 'world' ? <WorldTab projectId={projectId} /> : null}
        {activeTab === 'plot' ? <PlotTab projectId={projectId} /> : null}
        {activeTab === 'ledger' ? <LedgerTab projectId={projectId} /> : null}
        {activeTab === 'canon' ? <CanonTab projectId={projectId} /> : null}
        {activeTab === 'genre' ? <GenreTab projectId={projectId} /> : null}
      </div>
    </div>
  );
}
