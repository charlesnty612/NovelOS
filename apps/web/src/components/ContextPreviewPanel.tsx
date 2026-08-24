import { useCallback, useEffect, useState } from 'react';
import { contextPreviewApi } from '../api/endpoints';
import type {
  ContextPreviewItem,
  ContextPreviewResponse,
} from '../api/types';
import { ApiError } from '../api/client';
import { ErrorBanner, InfoBanner } from './ErrorBanner';
import { EmptyState } from './EmptyState';

interface Props {
  chapterId: string;
}

const KIND_LABEL: Record<string, string> = {
  chapter: '章节',
  project: '项目',
  knowledge_permissions: '知识权限',
  constraints: '约束',
  character: '角色',
  location: '地点',
  faction: '势力',
  world_rule: '世界规则',
  plot_event: '情节事件',
  hook: '伏笔',
  debt: '叙事债务',
  reference_canon: '参照系',
  author_intent: '作者意图',
  story_state_snapshot: '故事状态快照',
  director_plan: 'Director 计划',
  scene_plan: '场景计划',
  recent_prose: '上一章末尾',
  // Sprint 15 / V1.3
  author_style_sample: '作者文风样例',
  style_constraints: '风格约束',
  draft_text: '本章草稿',
  director_plan_summary: '计划摘要',
  previous_state: '上一状态',
  // Sprint 14-A/B
  chapter_summary: '历史章节摘要',
  previous_chapter_tail: '上一章结尾原文',
  open_foreshadow: '开放伏笔',
  // V2.0 Wave B 任务二：条件触发动态注入（never 模式仅预览可见）
  suppressed_character: '角色(已剔除)',
  suppressed_location: '地点(已剔除)',
  suppressed_faction: '势力(已剔除)',
  // V2.0 Wave C 任务一：FTS5 召回片段（章节正文跨长程呼应）
  recalled_passage: '召回片段',
};

/**
 * ContextPreviewPanel —— 章节详情页"AI 上下文明细"面板（Sprint 13）。
 *
 * - 顶部调 `GET /chapters/{cid}/context-preview`（dry-run，不调 LLM，不写库）。
 * - 按 L0/L1/L2 三个层折叠展示：每层一个 panel__section，含 token 估算 + 条目数量 +
 *   `<details>` 折叠的条目列表。
 * - 404 容错为 null（参考 QualityPanel 的 try/catch 模式）。
 * - data-testid：context-preview-panel / -layer-L0 / -layer-L1 / -layer-L2 /
 *   -total / -budget，便于测试断言。
 */
export function ContextPreviewPanel({ chapterId }: Props) {
  const [preview, setPreview] = useState<ContextPreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await contextPreviewApi.preview(chapterId);
      setPreview(r);
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 404) {
        setPreview(null);
        setError(null);
      } else {
        setError(e instanceof Error ? e.message : '加载上下文预览失败');
      }
    } finally {
      setLoading(false);
    }
  }, [chapterId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="panel" data-testid="context-preview-panel">
      <div className="panel__title">
        AI 上下文明细（dry-run）
        <div style={{ flex: 1 }} />
        <button
          className="btn btn--sm"
          onClick={() => void reload()}
          disabled={loading}
          data-testid="context-preview-reload"
        >
          {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <ErrorBanner>{error}</ErrorBanner>

      {loading && !preview ? (
        <div className="muted">加载上下文中…</div>
      ) : !preview ? (
        <div className="muted small">
          暂无上下文预览（该章节尚无完整数据；dry-run 不触发 LLM 调用）。
        </div>
      ) : (
        <ContextPreviewView preview={preview} />
      )}
    </div>
  );
}

function ContextPreviewView({ preview }: { preview: ContextPreviewResponse }) {
  const totalItems = preview.layers.reduce((n, l) => n + l.items.length, 0);
  return (
    <div data-testid="context-preview-view">
      <div className="quality-overall-row" style={{ marginBottom: 8 }}>
        <InfoBanner>
          <span data-testid="context-preview-total">
            估算 token：{preview.total_tokens}
          </span>
          {' / '}
          <span data-testid="context-preview-budget">
            预算 {preview.token_budget}
          </span>
          {' · '}
          {preview.within_budget ? '在预算内' : '超出预算'}
          {' · 涉及 '}
          {preview.agents.join(' / ')}
        </InfoBanner>
      </div>

      {totalItems === 0 ? (
        <EmptyState
          title="该章节暂无已装配的上下文条目"
          hint="项目可能尚无状态数据。"
        />
      ) : (
        preview.layers.map((layer) => (
          <div
            key={layer.id}
            className="panel__section"
            data-testid={`context-preview-layer-${layer.id}`}
          >
            <div className="panel__section-title">
              <strong>{layer.id}</strong> · {layer.label}
              <span className="muted small" style={{ marginLeft: 8 }}>
                {layer.items.length} 条 · 估算 {layer.token_estimate} tokens
              </span>
            </div>
            {layer.items.length === 0 ? (
              <div className="muted small">（该层无条目）</div>
            ) : (
              <details>
                <summary className="muted small">
                  展开 {layer.items.length} 个条目
                </summary>
                <ul
                  className="kv-list"
                  style={{ marginTop: 6, paddingLeft: 16 }}
                  data-testid={`context-preview-items-${layer.id}`}
                >
                  {layer.items.map((it) => (
                    <PreviewItemRow
                      key={`${it.kind}-${it.id}`}
                      item={it}
                    />
                  ))}
                </ul>
              </details>
            )}
          </div>
        ))
      )}
    </div>
  );
}

function PreviewItemRow({ item }: { item: ContextPreviewItem }) {
  const label = KIND_LABEL[item.kind] ?? item.kind;
  // Sprint 14-B：开放伏笔的 overdue 标记优先展示在最显眼的位置（沿用 badge 视觉约定）。
  const isOverdue = (item as { overdue?: boolean }).overdue === true;
  // V2.0 Wave B 任务二：条件触发动态注入状态徽标（full / summary / suppressed）。
  const injection = item.injection ?? 'full';
  return (
    <li className="kv-list__row">
      <span className="kv-list__title" data-testid={`preview-item-${item.kind}`}>
        [{label}] {item.name}
      </span>
      <span className="muted small">{item.id}</span>
      {injection !== 'full' ? (
        <span
          className="badge badge--chapter-planned"
          data-testid={`preview-item-injection-${item.kind}-${injection}`}
          style={
            injection === 'suppressed'
              ? { background: '#888', color: '#fff' }
              : { background: '#f5a623', color: '#000' }
          }
          title={
            injection === 'summary'
              ? '本章未触发该实体，仅注入一行摘要'
              : '该实体已配置为不注入'
          }
        >
          {injection === 'summary' ? '摘要' : '已剔除'}
        </span>
      ) : null}
      {item.summary_line && injection === 'summary' ? (
        <span className="muted small" data-testid={`preview-item-summary-line-${item.kind}`}>
          {item.summary_line}
        </span>
      ) : null}
      {isOverdue ? (
        <span
          className="badge badge--chapter-planned"
          data-testid={`preview-item-overdue-${item.kind}`}
          style={{ background: '#c33', color: '#fff' }}
        >
          逾期
        </span>
      ) : null}
      {item.source ? (
        <span className="badge badge--chapter-planned">{item.source}</span>
      ) : null}
    </li>
  );
}