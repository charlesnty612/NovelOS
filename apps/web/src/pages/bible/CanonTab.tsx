// Story Bible 第五个 Tab：参照系（Sprint 11 下半）。
// 顶部「拆书」表单 + 中部 canon 列表 + 底部选中 canon 的详情抽屉（report_md 渲染 + canon_json 字段摘要）。
//
// 设计要点：
// - POST /projects/{pid}/deconstruct 是同步长任务（无 Human 节点），无需轮询；
//   完成后直接刷新列表。
// - 删除走 DELETE /canons/{id}（204）→ 刷新列表。
// - report_md 用本地轻量 Markdown 渲染（utils/format.ts parseReportMarkdown）：
//   仅识别 `# / ## / ###` 标题与 `- ` 列表项，不引第三方 markdown 库（避免 XSS 与体积膨胀）。
// - canon_json 仅展示关键字段（logline / spine 条数 / rhythm 中位数 / style_params 摘要），
//   全量 JSON 走 <pre> 折叠块，避免主面板被超长 canon 撑爆。

import { useEffect, useState } from 'react';
import { referenceApi } from '../../api/endpoints';
import type {
  CanonDetail,
  CanonSummary,
  DeconstructStartResponse,
  ReaderProfile,
} from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { ErrorBanner } from '../../components/ErrorBanner';
import { formatDateTime, parseReportMarkdown } from '../../utils/format';

interface CanonTabProps {
  projectId: string;
}

const READER_PROFILE_OPTIONS: { value: ReaderProfile; label: string }[] = [
  { value: 'male_fantasy', label: '男频·玄幻 male_fantasy' },
  { value: 'male_urban', label: '男频·都市 male_urban' },
  { value: 'male_system', label: '男频·系统 male_system' },
  { value: 'female_general', label: '女频·通用 female_general' },
  { value: 'general', label: '通用 general' },
];

// ---------------------------------------------------------------------------
// 面板组件
// ---------------------------------------------------------------------------

export function CanonTab({ projectId }: CanonTabProps) {
  const [list, setList] = useState<CanonSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [bookTitle, setBookTitle] = useState('');
  const [readerProfile, setReaderProfile] = useState<ReaderProfile>('male_fantasy');
  const [text, setText] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CanonDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailErr, setDetailErr] = useState<string | null>(null);

  const reloadList = async () => {
    setLoading(true);
    setErr(null);
    try {
      const rows = await referenceApi.listCanons(projectId);
      setList(rows);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '加载参照系列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reloadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const handleDeconstruct = async () => {
    if (!bookTitle.trim()) {
      setErr('请填写书名');
      return;
    }
    if (!text.trim()) {
      setErr('请粘贴参照书全文');
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      const resp: DeconstructStartResponse = await referenceApi.deconstruct(projectId, {
        book_title: bookTitle.trim(),
        text: text,
        reader_profile: readerProfile,
      });
      if (resp.status !== 'COMPLETED') {
        setErr(`拆书未完成（status=${resp.status}）${resp.error ? `：${resp.error}` : ''}`);
      } else {
        // 成功：清空表单，刷新列表，自动选中新 canon
        setBookTitle('');
        setText('');
        await reloadList();
        if (resp.canon_id) setSelectedId(resp.canon_id);
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '拆书失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (canonId: string) => {
    if (!window.confirm('确认删除该参照系？关联的章节 extracts 会一并删除。')) return;
    try {
      await referenceApi.deleteCanon(canonId);
      if (selectedId === canonId) {
        setSelectedId(null);
        setDetail(null);
      }
      await reloadList();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '删除失败');
    }
  };

  // 详情加载
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailErr(null);
    referenceApi
      .getCanon(selectedId)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setDetailErr(e instanceof Error ? e.message : '加载详情失败');
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  return (
    <div data-testid="canon-tab">
      <div className="toolbar">
        <div className="muted small">
          参照系 Reference Canon（PRD v1.2 D1）—— 借结构不借表达。仅消费抽象模式（节奏 / 伏笔 /
          势力拓扑 / 爽点分布 / 文风统计），不含原文。
        </div>
      </div>

      <ErrorBanner>{err}</ErrorBanner>

      {/* ----------------- 顶部：拆书表单 ----------------- */}
      <div className="card" style={{ marginBottom: 16 }} data-testid="deconstruct-form">
        <div className="detail-pane__title">拆书 deconstruct-book</div>
        <div className="form-row">
          <label>书名 *</label>
          <input
            value={bookTitle}
            onChange={(e) => setBookTitle(e.target.value)}
            disabled={submitting}
            placeholder="如：测试参照书"
            data-testid="deconstruct-book-title"
          />
        </div>
        <div className="form-row">
          <label>读者档</label>
          <select
            value={readerProfile}
            onChange={(e) => setReaderProfile(e.target.value as ReaderProfile)}
            disabled={submitting}
            data-testid="deconstruct-reader-profile"
          >
            {READER_PROFILE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="form-row">
          <label>参照书全文（txt / 纯文本） *</label>
          <textarea
            rows={6}
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={submitting}
            placeholder="粘贴整本参照书正文"
            data-testid="deconstruct-text"
          />
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button
            className="btn btn--primary"
            disabled={submitting}
            onClick={() => void handleDeconstruct()}
            data-testid="deconstruct-submit"
          >
            {submitting ? '拆书中…' : '开始拆书'}
          </button>
        </div>
      </div>

      {/* ----------------- 列表 + 详情 ----------------- */}
      <div className="layout-2col">
        <div>
          <div className="detail-pane__title">
            参照系列表（{list.length}）
          </div>
          {loading ? (
            <div className="muted">加载中…</div>
          ) : list.length === 0 ? (
            <EmptyState
              title="还没有参照系"
              hint="使用上方表单粘贴一本参照书进行拆书；拆书结果会自动落入本项目。"
            />
          ) : (
            <table className="table" data-testid="canon-table">
              <thead>
                <tr>
                  <th>书名</th>
                  <th>读者档</th>
                  <th>创建时间</th>
                  <th>梗概摘要</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {list.map((c) => (
                  <tr
                    key={c.canon_id}
                    onClick={() => setSelectedId(c.canon_id)}
                    style={{
                      cursor: 'pointer',
                      background: c.canon_id === selectedId ? '#f3f7ff' : undefined,
                    }}
                    data-testid="canon-row"
                  >
                    <td>{c.title}</td>
                    <td className="muted small">{c.reader_profile}</td>
                    <td className="muted small">{formatDateTime(c.created_at)}</td>
                    <td style={{ maxWidth: 280 }}>{c.logline || '—'}</td>
                    <td className="right">
                      <button
                        className="btn btn--sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedId(c.canon_id);
                        }}
                      >
                        详情
                      </button>
                      <button
                        className="btn btn--sm btn--danger"
                        style={{ marginLeft: 6 }}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDelete(c.canon_id);
                        }}
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <aside className="detail-pane" data-testid="canon-detail-pane">
          {!selectedId ? (
            <div className="muted">从左侧选择参照系查看详情。</div>
          ) : detailLoading ? (
            <div className="muted">详情加载中…</div>
          ) : detailErr ? (
            <ErrorBanner>{detailErr}</ErrorBanner>
          ) : detail ? (
            <CanonDetailView detail={detail} />
          ) : null}
        </aside>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 详情视图
// ---------------------------------------------------------------------------

function CanonDetailView({ detail }: { detail: CanonDetail }) {
  const cj = detail.canon_json ?? {};
  const logline =
    typeof cj['logline'] === 'string' ? (cj['logline'] as string) : '';
  const spine = Array.isArray(cj['spine']) ? (cj['spine'] as unknown[]) : [];
  const rhythm = (cj['rhythm'] ?? {}) as Record<string, unknown>;
  const styleParams = (cj['style_params'] ?? {}) as Record<string, unknown>;

  const reportBlocks = parseReportMarkdown(detail.report_md);

  // rhythm 摘要：mini/major climax 中位数 + chapter_end_hook_rate
  const rhythmSummary: string[] = [];
  const mini = rhythm['mini_climax_interval'] as Record<string, unknown> | undefined;
  const major = rhythm['major_climax_interval'] as Record<string, unknown> | undefined;
  if (mini && typeof mini['median'] === 'number') {
    rhythmSummary.push(`mini_climax 中位数 ${mini['median']}`);
  }
  if (major && typeof major['median'] === 'number') {
    rhythmSummary.push(`major_climax 中位数 ${major['median']}`);
  }
  if (typeof rhythm['chapter_end_hook_rate'] === 'number') {
    rhythmSummary.push(`章末钩子率 ${rhythm['chapter_end_hook_rate']}`);
  }

  const styleSummary: string[] = [];
  if (typeof styleParams['pov'] === 'string') {
    styleSummary.push(`pov=${styleParams['pov']}`);
  }
  if (typeof styleParams['dialogue_ratio'] === 'number') {
    styleSummary.push(`对白比例 ${styleParams['dialogue_ratio']}`);
  }
  if (typeof styleParams['action_ratio'] === 'number') {
    styleSummary.push(`动作比例 ${styleParams['action_ratio']}`);
  }

  return (
    <div>
      <div className="detail-pane__title">{detail.title}</div>
      <div className="muted small">
        ID：{detail.canon_id} · 读者档：{detail.reader_profile} · 创建：{formatDateTime(detail.created_at)}
      </div>
      <div className="spacer" />

      {logline ? (
        <div className="detail-pane__section">
          <div className="detail-pane__section-title">logline</div>
          <div data-testid="canon-logline">{logline}</div>
        </div>
      ) : null}

      <div className="form-grid">
        <Field label="spine 条数" value={String(spine.length)} />
        <Field label="rhythm 摘要" value={rhythmSummary.join('；') || '—'} />
        <Field label="style_params 摘要" value={styleSummary.join('；') || '—'} />
        <Field label="extracts" value={String(detail.extracts.length)} />
      </div>

      <div className="detail-pane__section">
        <div className="detail-pane__section-title">report_md（T4 人读报告）</div>
        <div data-testid="canon-report-md">
          {reportBlocks.length === 0 ? (
            <div className="muted">报告为空。</div>
          ) : (
            reportBlocks.map((b, idx) => {
              if (b.kind === 'heading') {
                if (b.level === 1) return <h2 key={idx}>{b.text}</h2>;
                if (b.level === 2) return <h3 key={idx}>{b.text}</h3>;
                return <h4 key={idx}>{b.text}</h4>;
              }
              if (b.kind === 'list-item') {
                return (
                  <ul key={idx} style={{ margin: '4px 0' }}>
                    <li>{b.text}</li>
                  </ul>
                );
              }
              return <p key={idx} style={{ margin: '6px 0' }}>{b.text}</p>;
            })
          )}
        </div>
      </div>

      <div className="detail-pane__section">
        <div className="detail-pane__section-title">canon_json 全文</div>
        <pre className="json-block" data-testid="canon-json-block">
          {JSON.stringify(cj, null, 2)}
        </pre>
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
