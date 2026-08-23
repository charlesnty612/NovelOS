import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { CharacterTab } from './bible/CharacterTab';
import { WorldTab } from './bible/WorldTab';
import { PlotTab } from './bible/PlotTab';
import { LedgerTab } from './bible/LedgerTab';

type BibleTab = 'characters' | 'world' | 'plot' | 'ledger';

export function StoryBiblePage() {
  const { pid } = useParams();
  const projectId = pid!;
  const [tab, setTab] = useState<BibleTab>('characters');

  return (
    <div>
      <h1 className="section-title">Story Bible</h1>
      <p className="section-subtitle">
        管理本项目的角色、世界设定与剧情事件。所有数据都按项目隔离。
      </p>

      <div className="tabs">
        <div
          className={`tabs__tab ${tab === 'characters' ? 'tabs__tab--active' : ''}`}
          onClick={() => setTab('characters')}
          data-testid="tab-characters"
        >
          角色
        </div>
        <div
          className={`tabs__tab ${tab === 'world' ? 'tabs__tab--active' : ''}`}
          onClick={() => setTab('world')}
          data-testid="tab-world"
        >
          世界
        </div>
        <div
          className={`tabs__tab ${tab === 'plot' ? 'tabs__tab--active' : ''}`}
          onClick={() => setTab('plot')}
          data-testid="tab-plot"
        >
          剧情
        </div>
        <div
          className={`tabs__tab ${tab === 'ledger' ? 'tabs__tab--active' : ''}`}
          onClick={() => setTab('ledger')}
          data-testid="tab-ledger"
        >
          伏笔与债务
        </div>
      </div>

      {tab === 'characters' ? <CharacterTab projectId={projectId} /> : null}
      {tab === 'world' ? <WorldTab projectId={projectId} /> : null}
      {tab === 'plot' ? <PlotTab projectId={projectId} /> : null}
      {tab === 'ledger' ? <LedgerTab projectId={projectId} /> : null}
    </div>
  );
}
