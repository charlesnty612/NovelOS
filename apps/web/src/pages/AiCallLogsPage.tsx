import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { aiCallLogsApi } from '../api/endpoints';
import type { AiCallLogDetail, AiCallLogSummary } from '../api/types';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { formatDateTime, formatJson } from '../utils/format';
import { ApiError } from '../api/client';

/**
 * AiCallLogsPage —— AI 调用日志查看器（Sprint 13）。
 *
 * - 顶部按钮「刷新」触发 list 重新加载。
 * - 列表：每行展示时间 / agent / model / latency / 状态徽章（按 error 着色）。
 * - 点击行展开详情：
 *   - 顶部 summary 行（同列表行）
 *   - <details>「input_context_ids」→ 列表
 *   - <details>「output_json」→ pre.json-block
 *
 * 入口：在 AiSettingsPage 顶部加链接跳转（见 AiSettingsPage.tsx）。
 * 路由：`/ai-logs`（全局页面，不挂在 projects 下；详见 App.tsx）。
 */
export function AiCallLogsPage() {
  const [logs, setLogs] = useState<AiCallLogSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AiCallLogDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await aiCallLogsApi.list();
      setLogs(rows);
      // 若当前选中的行已不存在，重置
      if (selectedId && !rows.find((r) => r.call_id === selectedId)) {
        setSelectedId(null);
        setDetail(null);
      } else if (!selectedId && rows.length > 0) {
        setSelectedId(rows[0].call_id);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载 AI 调用日志失败');
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // 选中变化时拉详情
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    (async () => {
      try {
        const d = await aiCallLogsApi.get(selectedId);
        setDetail(d);
      } catch (e: unknown) {
        if (e instanceof ApiError && e.status === 404) {
          setDetail(null);
          setDetailError('该日志已被删除或不可访问');
        } else {
          setDetailError(e instanceof Error ? e.message : '加载详情失败');
        }
      } finally {
        setDetailLoading(false);
      }
    })();
  }, [selectedId]);

  return (
    <div data-testid="ai-call-logs-page">
      <div className="toolbar">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Link to="/" className="muted small">
            ← 返回项目列表
          </Link>
          <strong>AI 调用日志</strong>
        </div>
        <div style={{ flex: 1 }} />
        <button
          className="btn btn--sm"
          onClick={() => void reload()}
          disabled={loading}
          data-testid="ai-logs-reload"
        >
          {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <ErrorBanner>{error}</ErrorBanner>

      <p className="muted small">
        每次 LLM 调用的摘要与详情（不含 API key）；详情可展开 prompt/response。
      </p>

      {!logs ? (
        loading ? (
          <div className="muted">加载中…</div>
        ) : null
      ) : logs.length === 0 ? (
        <EmptyState
          title="暂无 AI 调用日志"
          hint="运行一次 workflow 即可在此查看。"
        />
      ) : (
        <div className="panel" data-testid="ai-logs-list-panel">
          <div className="panel__title">最近调用</div>
          <div className="kv-list">
            {logs.map((r) => (
              <div
                key={r.call_id}
                className={`kv-list__row ${r.call_id === selectedId ? 'kv-list__row--active' : ''}`}
                onClick={() => setSelectedId(r.call_id)}
                data-testid={`ai-log-row-${r.call_id}`}
              >
                <span className="kv-list__title">
                  {formatDateTime(r.created_at)}
                </span>
                <span className="muted small">{r.agent_id ?? '-'}</span>
                <span className="muted small">{r.model_id ?? '-'}</span>
                <span className="muted small">
                  {r.latency_ms != null ? `${r.latency_ms}ms` : '-'}
                </span>
                <span
                  className={
                    r.error
                      ? 'badge badge--chapter-failed'
                      : 'badge badge--chapter-committed'
                  }
                  data-testid={`ai-log-status-${r.call_id}`}
                >
                  {r.error ? '失败' : '成功'}
                </span>
                <span className="kv-list__meta">
                  {r.call_id.slice(0, 12)}…
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {selectedId ? (
        <div
          className="panel"
          style={{ marginTop: 16 }}
          data-testid={`ai-log-detail-${selectedId}`}
        >
          <div className="panel__title">详情</div>
          <ErrorBanner>{detailError}</ErrorBanner>
          {detailLoading && !detail ? (
            <div className="muted">加载详情中…</div>
          ) : detail ? (
            <LogDetailView detail={detail} />
          ) : (
            <div className="muted small">该日志无详情或已被删除。</div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function LogDetailView({ detail }: { detail: AiCallLogDetail }) {
  const usage = detail.token_usage;
  return (
    <div data-testid="ai-log-detail-view">
      <div className="kv-list">
        <div className="kv-list__row">
          <span className="kv-list__title">call_id</span>
          <span className="muted small">{detail.call_id}</span>
        </div>
        <div className="kv-list__row">
          <span className="kv-list__title">run_id</span>
          <span className="muted small">{detail.run_id}</span>
        </div>
        <div className="kv-list__row">
          <span className="kv-list__title">node_run_id</span>
          <span className="muted small">{detail.node_run_id ?? '-'}</span>
        </div>
        <div className="kv-list__row">
          <span className="kv-list__title">agent_id</span>
          <span className="muted small">{detail.agent_id ?? '-'}</span>
        </div>
        <div className="kv-list__row">
          <span className="kv-list__title">model_id</span>
          <span className="muted small">{detail.model_id ?? '-'}</span>
        </div>
        <div className="kv-list__row">
          <span className="kv-list__title">prompt_version</span>
          <span className="muted small">{detail.prompt_version ?? '-'}</span>
        </div>
        <div className="kv-list__row">
          <span className="kv-list__title">latency</span>
          <span className="muted small">
            {detail.latency_ms != null ? `${detail.latency_ms}ms` : '-'}
          </span>
        </div>
        <div className="kv-list__row">
          <span className="kv-list__title">token_usage</span>
          <span className="muted small">
            {usage
              ? `prompt=${usage.prompt} completion=${usage.completion} total=${usage.total}`
              : '-'}
          </span>
        </div>
        <div className="kv-list__row">
          <span className="kv-list__title">retry_count</span>
          <span className="muted small">{detail.retry_count}</span>
        </div>
        <div className="kv-list__row">
          <span className="kv-list__title">error</span>
          <span className="muted small">{detail.error ?? '-'}</span>
        </div>
      </div>

      <div
        className="panel__section"
        data-testid="ai-log-detail-input-context"
      >
        <div className="panel__section-title">
          input_context_ids（仅 *_id 键值，原文不落 DB）
        </div>
        {detail.input_context_ids.length === 0 ? (
          <div className="muted small">（空）</div>
        ) : (
          <pre className="json-block">
            {formatJson(detail.input_context_ids)}
          </pre>
        )}
      </div>

      <div
        className="panel__section"
        data-testid="ai-log-detail-output"
      >
        <div className="panel__section-title">output_json</div>
        {detail.output == null ? (
          <div className="muted small">（空）</div>
        ) : (
          <pre className="json-block">{formatJson(detail.output)}</pre>
        )}
      </div>
    </div>
  );
}