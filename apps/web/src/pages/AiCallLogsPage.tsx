// AI 调用日志（每次 LLM 调用的摘要与详情）。
//
// 2026-09-13 前端批次 C 重做：
//   - 选中与列表加载解耦：点行只拉该条详情（带本地缓存），不再整表重拉 / 闪烁；
//   - 主从布局：左列日志列表、右列详情，宽屏并列、窄屏堆叠；
//   - 详情人读化：中文标签 + 关键字段优先（结果 / 耗时 / Token / 模型 / 时间），
//     原始 JSON 与内部标识符收进折叠区；
//   - 过滤条：项目（走服务端 project_id）+ 结果状态 / 时间范围（服务端无对应参数，
//     前端在已加载页内内存过滤）；
//   - 空态 / 加载态用共享组件 EmptyState / Loading。
//
// 后端契约：GET /ai-call-logs（?project_id= &node= &limit= &offset=，默认 limit=50、
// 上限 200）、GET /ai-call-logs/{id}。响应不含任何 API key 字段。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { aiCallLogsApi, projectsApi } from '../api/endpoints';
import type { AiCallLogDetail, AiCallLogSummary, Project } from '../api/types';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { Loading } from '../components/Loading';
import { formatDateTime, formatJson } from '../utils/format';
import { ApiError } from '../api/client';

/** 单次拉取上限（后端 _MAX_LIMIT=200） */
const LIST_LIMIT = 200;
const DAY_MS = 24 * 60 * 60 * 1000;

type ResultFilter = 'all' | 'ok' | 'warn' | 'fail';
type TimeFilter = 'all' | 'today' | '7d';

function logStatus(r: Pick<AiCallLogSummary, 'error'>): 'ok' | 'warn' | 'fail' {
  if (!r.error) return 'ok';
  return r.error.startsWith('warn:') ? 'warn' : 'fail';
}

const STATUS_LABEL: Record<'ok' | 'warn' | 'fail', string> = {
  ok: '成功',
  warn: '告警',
  fail: '失败',
};

const STATUS_CLASS: Record<'ok' | 'warn' | 'fail', string> = {
  ok: 'badge badge--chapter-committed',
  warn: 'badge badge--chapter-warn',
  fail: 'badge badge--chapter-failed',
};

function formatLatency(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function formatTokens(r: AiCallLogSummary): string {
  const t = r.token_usage;
  if (!t) return '—';
  return `${t.total}（入 ${t.prompt} / 出 ${t.completion}）`;
}

export function AiCallLogsPage() {
  // 来源项目上下文：从项目 AI 设置跳来时带 ?project=<pid>，返回链接回该项目的 AI 设置页。
  const [searchParams] = useSearchParams();
  const projectFromUrl = searchParams.get('project') ?? '';

  const [rows, setRows] = useState<AiCallLogSummary[] | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  const [projects, setProjects] = useState<Project[]>([]);
  const [projectFilter, setProjectFilter] = useState(projectFromUrl);
  const [resultFilter, setResultFilter] = useState<ResultFilter>('all');
  const [timeFilter, setTimeFilter] = useState<TimeFilter>('all');

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AiCallLogDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  // 已看过的详情缓存：来回点选同一行不重复请求（列表重载后仍保留）。
  const detailCacheRef = useRef<Map<string, AiCallLogDetail>>(new Map());

  // ---- 列表加载（依赖：项目筛选 + 手动刷新；与选中解耦）----
  useEffect(() => {
    let alive = true;
    setListLoading(true);
    setListError(null);
    aiCallLogsApi
      .list({ limit: LIST_LIMIT, ...(projectFilter ? { project_id: projectFilter } : {}) })
      .then((data) => {
        if (!alive) return;
        setRows(data);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setListError(e instanceof Error ? e.message : '加载 AI 调用日志失败');
      })
      .finally(() => {
        if (alive) setListLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [projectFilter, reloadNonce]);

  // 项目下拉选项（失败静默：筛选条仍可用「全部项目」）
  useEffect(() => {
    let alive = true;
    projectsApi
      .list()
      .then((list) => {
        if (alive) setProjects(list);
      })
      .catch(() => {
        /* 忽略：无项目列表时只保留「全部项目」 */
      });
    return () => {
      alive = false;
    };
  }, []);

  // 结果状态 / 时间范围：后端无对应查询参数，在已加载页内内存过滤。
  const visibleRows = useMemo(() => {
    const list = rows ?? [];
    const now = Date.now();
    const startOfToday = new Date();
    startOfToday.setHours(0, 0, 0, 0);
    const threshold =
      timeFilter === 'today'
        ? startOfToday.getTime()
        : timeFilter === '7d'
        ? now - 7 * DAY_MS
        : null;
    return list.filter((r) => {
      if (resultFilter !== 'all' && logStatus(r) !== resultFilter) return false;
      if (threshold != null) {
        const t = Date.parse(r.created_at);
        if (!Number.isNaN(t) && t < threshold) return false;
      }
      return true;
    });
  }, [rows, resultFilter, timeFilter]);

  // 选中回落：列表（或过滤结果）变化后，保持选中项有效；无效则选第一条。
  useEffect(() => {
    if (visibleRows.length === 0) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !visibleRows.some((r) => r.call_id === selectedId)) {
      setSelectedId(visibleRows[0].call_id);
    }
  }, [visibleRows, selectedId]);

  // ---- 详情：仅按选中 id 拉单条（命中缓存则零请求）----
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailError(null);
      setDetailLoading(false);
      return;
    }
    const cached = detailCacheRef.current.get(selectedId);
    if (cached) {
      setDetail(cached);
      setDetailError(null);
      setDetailLoading(false);
      return;
    }
    let alive = true;
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    aiCallLogsApi
      .get(selectedId)
      .then((d) => {
        if (!alive) return;
        detailCacheRef.current.set(selectedId, d);
        setDetail(d);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) {
          setDetailError('该日志已被删除或不可访问');
        } else {
          setDetailError(e instanceof Error ? e.message : '加载详情失败');
        }
      })
      .finally(() => {
        if (alive) setDetailLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [selectedId]);

  const handleReload = useCallback(() => setReloadNonce((n) => n + 1), []);

  const hasFilters =
    projectFilter !== '' || resultFilter !== 'all' || timeFilter !== 'all';
  const resetFilters = () => {
    setProjectFilter('');
    setResultFilter('all');
    setTimeFilter('all');
  };

  const selectedRow = selectedId
    ? (rows ?? []).find((r) => r.call_id === selectedId) ?? null
    : null;

  return (
    <div data-testid="ai-call-logs-page">
      <div className="toolbar">
        <Link
          to={projectFromUrl ? `/projects/${projectFromUrl}/ai` : '/'}
          className="muted small"
        >
          {projectFromUrl ? '← 返回项目 AI 设置' : '← 返回项目列表'}
        </Link>
        <strong>AI 调用日志</strong>
        <span className="toolbar__spacer" />
        <button
          className="btn btn--sm"
          onClick={handleReload}
          disabled={listLoading}
          data-testid="ai-logs-reload"
        >
          {listLoading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <ErrorBanner>{listError}</ErrorBanner>

      <p className="muted small cdp-logs__intro">
        每次模型调用的结果、耗时与 Token 用量都记录在这里（不含任何 API Key）。
        选中左侧一条即可查看详情。
      </p>

      {/* 过滤条：项目走服务端过滤；结果状态 / 时间范围为前端内存过滤 */}
      <div className="cdp-filters" data-testid="ai-logs-filters">
        <label className="cdp-filter">
          <span className="cdp-filter__label">项目</span>
          <select
            className="input cdp-filter__control"
            value={projectFilter}
            data-testid="ai-logs-filter-project"
            onChange={(e) => setProjectFilter(e.target.value)}
          >
            <option value="">全部项目</option>
            {projects.map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="cdp-filter">
          <span className="cdp-filter__label">结果</span>
          <select
            className="input cdp-filter__control"
            value={resultFilter}
            data-testid="ai-logs-filter-result"
            onChange={(e) => setResultFilter(e.target.value as ResultFilter)}
          >
            <option value="all">全部</option>
            <option value="ok">成功</option>
            <option value="warn">告警</option>
            <option value="fail">失败</option>
          </select>
        </label>
        <label className="cdp-filter">
          <span className="cdp-filter__label">时间</span>
          <select
            className="input cdp-filter__control"
            value={timeFilter}
            data-testid="ai-logs-filter-time"
            onChange={(e) => setTimeFilter(e.target.value as TimeFilter)}
          >
            <option value="all">全部</option>
            <option value="today">今天</option>
            <option value="7d">最近 7 天</option>
          </select>
        </label>
        <span className="cdp-filters__spacer" />
        {rows ? (
          <span className="muted small" data-testid="ai-logs-count">
            显示 {visibleRows.length} / {rows.length} 条（最多最近 {LIST_LIMIT} 条）
          </span>
        ) : null}
      </div>

      {rows === null ? (
        listLoading ? (
          <Loading text="加载日志中…" />
        ) : null
      ) : visibleRows.length === 0 ? (
        hasFilters ? (
          <EmptyState
            title="没有符合条件的日志"
            hint="试试放宽结果状态或时间范围；也可以清空筛选。"
            action={
              <button className="btn btn--sm" onClick={resetFilters}>
                清空筛选
              </button>
            }
          />
        ) : (
          <EmptyState
            title="暂无 AI 调用日志"
            hint="运行一次工作流（生成计划 / 写正文 / 审校 / 提交）即可在此查看每次调用。"
          />
        )
      ) : (
        <div className="cdp-logs">
          <div className="panel" data-testid="ai-logs-list-panel">
            <div className="panel__title">最近调用</div>
            <div className="kv-list cdp-logs__list">
              {visibleRows.map((r) => {
                const st = logStatus(r);
                return (
                  <div
                    key={r.call_id}
                    className={`kv-list__row cdp-log-row ${r.call_id === selectedId ? 'kv-list__row--active' : ''}`}
                    onClick={() => setSelectedId(r.call_id)}
                    data-testid={`ai-log-row-${r.call_id}`}
                  >
                    <span className="kv-list__title">
                      {formatDateTime(r.created_at)}
                    </span>
                    <span className="muted small">{r.agent_id ?? '—'}</span>
                    <span className="muted small">{r.model_id ?? '—'}</span>
                    <span className="muted small">{formatLatency(r.latency_ms)}</span>
                    <span
                      className={STATUS_CLASS[st]}
                      data-testid={`ai-log-status-${r.call_id}`}
                    >
                      {STATUS_LABEL[st]}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="panel" data-testid="ai-logs-detail-panel">
            <div className="panel__title">调用详情</div>
            <ErrorBanner>{detailError}</ErrorBanner>
            {!selectedId ? (
              <div className="muted small">从左侧选择一条日志查看详情。</div>
            ) : detailLoading ? (
              <Loading text="加载详情中…" />
            ) : detail ? (
              <LogDetailView
                detail={detail}
                testId={`ai-log-detail-${detail.call_id}`}
                fallback={
                  selectedRow
                    ? {
                        created_at: selectedRow.created_at,
                        model_id: selectedRow.model_id,
                        latency_ms: selectedRow.latency_ms,
                      }
                    : undefined
                }
              />
            ) : (
              <div className="muted small">该日志无详情或已被删除。</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function LogDetailView({
  detail,
  testId,
  fallback,
}: {
  detail: AiCallLogDetail;
  testId: string;
  /** 列表行的摘要字段（详情缺字段时兜底展示） */
  fallback?: { created_at: string; model_id: string | null; latency_ms: number | null };
}) {
  const usage = detail.token_usage;
  const st = logStatus(detail);
  const createdAt = detail.created_at || fallback?.created_at || '';
  const modelId = detail.model_id ?? fallback?.model_id ?? null;
  const latencyMs = detail.latency_ms ?? fallback?.latency_ms ?? null;

  return (
    <div data-testid={testId}>
      <div className="cdp-log-detail__head">
        <span className={STATUS_CLASS[st]} data-testid="ai-log-detail-status">
          {STATUS_LABEL[st]}
        </span>
        <span className="cdp-log-detail__metric">
          <span className="muted small">耗时</span>
          <strong>{formatLatency(latencyMs)}</strong>
        </span>
        <span className="cdp-log-detail__metric">
          <span className="muted small">Token</span>
          <strong>{usage ? usage.total : '—'}</strong>
        </span>
        <span className="cdp-log-detail__metric">
          <span className="muted small">重试</span>
          <strong>{detail.retry_count}</strong>
        </span>
      </div>

      {detail.error ? (
        <div
          className={
            st === 'warn'
              ? 'alert cdp-log-detail__warn'
              : 'alert alert--error cdp-log-detail__error'
          }
          data-testid="ai-log-detail-error"
        >
          {st === 'warn' ? '告警：' : '失败：'}
          {detail.error}
        </div>
      ) : null}

      <div className="kv-list cdp-log-detail__facts">
        <div className="kv-list__row cdp-kv-row">
          <span className="cdp-kv-row__label">调用时间</span>
          <span className="muted small">{formatDateTime(createdAt)}</span>
        </div>
        <div className="kv-list__row cdp-kv-row">
          <span className="cdp-kv-row__label">模型</span>
          <span className="muted small">{modelId ?? '—'}</span>
        </div>
        <div className="kv-list__row cdp-kv-row">
          <span className="cdp-kv-row__label">Token 用量</span>
          <span className="muted small" data-testid="ai-log-detail-tokens">
            {formatTokens(detail)}
          </span>
        </div>
        <div className="kv-list__row cdp-kv-row">
          <span className="cdp-kv-row__label">智能体</span>
          <span className="muted small">{detail.agent_id ?? '—'}</span>
        </div>
        <div className="kv-list__row cdp-kv-row">
          <span className="cdp-kv-row__label">提示词版本</span>
          <span className="muted small">{detail.prompt_version ?? '—'}</span>
        </div>
        {detail.cost != null ? (
          <div className="kv-list__row cdp-kv-row">
            <span className="cdp-kv-row__label">费用</span>
            <span className="muted small">{detail.cost}</span>
          </div>
        ) : null}
      </div>

      <details className="cdp-log-detail__fold" data-testid="ai-log-detail-input-context">
        <summary className="cdp-log-detail__summary">
          输入上下文 ID（{detail.input_context_ids.length} 条）
        </summary>
        {detail.input_context_ids.length === 0 ? (
          <div className="muted small">（空）</div>
        ) : (
          <pre className="json-block">{formatJson(detail.input_context_ids)}</pre>
        )}
      </details>

      <details className="cdp-log-detail__fold" data-testid="ai-log-detail-output">
        <summary className="cdp-log-detail__summary">原始输出（JSON）</summary>
        {detail.output == null ? (
          <div className="muted small">（空）</div>
        ) : (
          <pre className="json-block">{formatJson(detail.output)}</pre>
        )}
      </details>

      <details className="cdp-log-detail__fold" data-testid="ai-log-detail-ids">
        <summary className="cdp-log-detail__summary">技术标识（排障用）</summary>
        <div className="kv-list cdp-log-detail__facts">
          <div className="kv-list__row cdp-kv-row">
            <span className="cdp-kv-row__label">调用 ID</span>
            <span className="muted small">{detail.call_id}</span>
          </div>
          <div className="kv-list__row cdp-kv-row">
            <span className="cdp-kv-row__label">运行 ID</span>
            <span className="muted small">{detail.run_id}</span>
          </div>
          <div className="kv-list__row cdp-kv-row">
            <span className="cdp-kv-row__label">节点运行 ID</span>
            <span className="muted small">{detail.node_run_id ?? '—'}</span>
          </div>
        </div>
      </details>
    </div>
  );
}
