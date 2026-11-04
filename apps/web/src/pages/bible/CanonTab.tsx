// Story Bible 第五个 Tab：参照系（Sprint 11 下半）。
// 顶部「拆书」表单 + 中部 canon 列表 + 底部选中 canon 的详情抽屉（report_md 渲染 + canon_json 字段摘要）。
//
// 设计要点（批 7 / 2026-11-05 改）：
// - POST /projects/{pid}/deconstruct 与上传端点都是**异步受理**：201 返回
//   `WorkflowStartAccepted` 三键（run_id / status='RUNNING' / current_node=null）。
//   `status === 'RUNNING'` 是**成功受理**，不是错误；真终态须轮询 GET /runs/{id}。
//   本 Tab 受理后立刻清空表单，并在后台轮询到终态；COMPLETED 时刷新列表并自动选中
//   新增的 canon，FAILED/CANCELLED 时展示错误（带 run id）。
// - 删除走 DELETE /canons/{id}（204）→ 刷新列表。
// - report_md 用本地轻量 Markdown 渲染（utils/format.ts parseReportMarkdown）：
//   仅识别 `# / ## / ###` 标题与 `- ` 列表项，不引第三方 markdown 库（避免 XSS 与体积膨胀）。
// - canon_json 摘要走关键字段（logline / spine 条数 / rhythm 中位数 / style_params 摘要），
//   全文走 ReadableJson 人读视图 + 原始 JSON 折叠块，避免主面板被超长 canon 撑爆。

import { useEffect, useRef, useState } from 'react';
import { referenceApi, workflowsApi } from '../../api/endpoints';
import type {
  CanonDetail,
  CanonSummary,
  DeconstructPayload,
  ReaderProfile,
  WorkflowStartAccepted,
} from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { ErrorBanner, InfoBanner } from '../../components/ErrorBanner';
import { ReadableJson, RawJsonDetails } from '../../components/ReadableJson';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Loading } from '../../components/Loading';
import { formatApiError } from '../../utils/formatApiError';
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

const DECONSTRUCT_POLL_INTERVAL_MS = 2000;
const DECONSTRUCT_POLL_MAX_ATTEMPTS = 150;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

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
  // V3.x 文件上传：选了文件则走 uploadDeconstruct，与粘贴正文二选一。
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // 受理态（批 7）：非 null ⇒ 后台拆书中；展示 run_id 与最近一次轮询到的状态。
  const [accepted, setAccepted] = useState<{
    runId: string;
    status: string;
  } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // 轮询取消令牌：卸载 / 重新提交时自增，旧循环看到令牌变化即退出（防 setState 到已卸载组件）。
  const pollTokenRef = useRef(0);
  useEffect(
    () => () => {
      pollTokenRef.current += 1;
    },
    [],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CanonDetail | null>(null);
  // V3.22「交互反馈统一」：删除参照系前 ConfirmDialog 二次确认。
  const [pendingDeleteCanonId, setPendingDeleteCanonId] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailErr, setDetailErr] = useState<string | null>(null);

  const reloadList = async (): Promise<CanonSummary[]> => {
    setLoading(true);
    setErr(null);
    try {
      const rows = await referenceApi.listCanons(projectId);
      setList(rows);
      return rows;
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '加载参照系列表失败');
      return [];
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reloadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // 拆书是异步受理（批 7）：201 只表示「已受理」，真终态要轮询单 run 端点。
  // 150 次 × 2s ≈ 5 分钟；超时不报错成「失败」，只提示去运行记录看（防止把
  // 「还在跑」谎报成「失败了」）。
  const pollDeconstructRun = async (
    runId: string,
    token: number,
    beforeIds: Set<string>,
  ): Promise<void> => {
    for (let attempt = 0; attempt < DECONSTRUCT_POLL_MAX_ATTEMPTS; attempt++) {
      if (pollTokenRef.current !== token) return;
      try {
        const run = await workflowsApi.get(runId);
        if (pollTokenRef.current !== token) return;
        setAccepted((prev) =>
          prev && prev.runId === runId ? { ...prev, status: run.status } : prev,
        );
        if (run.status === 'COMPLETED') {
          setAccepted(null);
          const rows = await reloadList();
          if (pollTokenRef.current !== token) return;
          const added = rows.find((r) => !beforeIds.has(r.canon_id));
          if (added) setSelectedId(added.canon_id);
          setNotice(`拆书完成：${added ? `新增参照系「${added.title}」` : '可在列表查看结果'}`);
          return;
        }
        if (run.status === 'FAILED' || run.status === 'CANCELLED') {
          setAccepted(null);
          setErr(
            `拆书任务 ${runId} ${run.status === 'FAILED' ? '失败' : '已取消'}` +
              `${run.error ? `：${run.error}` : ''}`,
          );
          return;
        }
      } catch (e: unknown) {
        if (pollTokenRef.current !== token) return;
        // 网络/接口抖动只记一次，继续下一轮；连续失败由 maxAttempts 兜底。
        setAccepted((prev) =>
          prev && prev.runId === runId
            ? { ...prev, status: `轮询失败（${e instanceof Error ? e.message : '未知错误'}）` }
            : prev,
        );
      }
      await sleep(DECONSTRUCT_POLL_INTERVAL_MS);
    }
    if (pollTokenRef.current !== token) return;
    setNotice(`拆书仍在后台运行（run ${runId}）——请稍后到「运行记录」查看结果。`);
  };

  const handleDeconstruct = async () => {
    if (!bookTitle.trim()) {
      setErr('请填写书名');
      return;
    }
    // 文件 / 粘贴正文二选一：有文件走 uploadDeconstruct，无文件才要求粘贴正文。
    if (!uploadFile && !text.trim()) {
      setErr('请粘贴参照书全文，或选择 .txt / .epub 文件');
      return;
    }
    setSubmitting(true);
    setErr(null);
    setNotice(null);
    setAccepted(null);
    const beforeIds = new Set(list.map((c) => c.canon_id));
    try {
      let resp: WorkflowStartAccepted;
      if (uploadFile) {
        resp = await referenceApi.uploadDeconstruct(projectId, uploadFile, {
          book_title: bookTitle.trim(),
          reader_profile: readerProfile,
        });
      } else {
        const payload: DeconstructPayload = {
          book_title: bookTitle.trim(),
          text: text,
          reader_profile: readerProfile,
        };
        resp = await referenceApi.deconstruct(projectId, payload);
      }
      // ⚠️ 受理语义：status='RUNNING' 是**成功**（后端异步受理的恒定值），
      // 不是「拆书没完成」。只有 4xx 才会走到 catch。
      const runId = resp.run_id;
      setAccepted({ runId, status: resp.status });
      setNotice('已受理，后台拆书中…');
      // 表单立即清空：任务已受理，继续留着输入框会让作者误以为还能改这次任务。
      setBookTitle('');
      setText('');
      setUploadFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      const token = ++pollTokenRef.current;
      void pollDeconstructRun(runId, token, beforeIds);
    } catch (e: unknown) {
      setErr(formatApiError(e));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = (canonId: string) => {
    setPendingDeleteCanonId(canonId);
  };

  const doDelete = async (canonId: string) => {
    setPendingDeleteCanonId(null);
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
          势力拓扑 / 爽点分布 / 文风统计），不含原文。支持上传 .txt / .epub 文件代替粘贴正文。
        </div>
      </div>

      <ErrorBanner>{err}</ErrorBanner>

      {/* 批 7：拆书是异步受理。受理后展示 run_id + 轮询到的状态；
          RUNNING 绝不能渲染成错误（它正是成功受理的响应值）。 */}
      {accepted ? (
        <InfoBanner>
          <span data-testid="deconstruct-accepted">
            已受理拆书任务（run {accepted.runId}），后台拆书中…当前状态：{accepted.status}
          </span>
        </InfoBanner>
      ) : null}
      {notice ? (
        <div
          className="alert alert--info"
          role="status"
          aria-live="polite"
          data-testid="deconstruct-notice"
        >
          {notice}
        </div>
      ) : null}

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
            onChange={(e) => {
              setText(e.target.value);
              // 用户重新编辑正文 → 取消已选文件，避免文件与正文并存造成歧义。
              if (uploadFile) {
                setUploadFile(null);
                if (fileInputRef.current) fileInputRef.current.value = '';
              }
            }}
            disabled={submitting}
            placeholder={
              uploadFile
                ? '已选择文件，无需再粘贴正文（如需改为粘贴正文，请先点上方「清除」）'
                : '粘贴整本参照书正文'
            }
            data-testid="deconstruct-text"
          />
        </div>
        <div className="form-row">
          <label>或上传文件（.txt / .epub，≤ 20MB）</label>
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt,.epub"
            disabled={submitting}
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setUploadFile(f);
              // 选了文件 → 清空正文，避免两边同时存在造成歧义。
              if (f) setText('');
            }}
            data-testid="deconstruct-file"
          />
          {uploadFile ? (
            <div className="muted small" style={{ marginTop: 4 }} data-testid="deconstruct-file-info">
              已选：{uploadFile.name}（{(uploadFile.size / 1024).toFixed(1)} KB）
              {' · '}
              <button
                type="button"
                className="btn btn--sm"
                style={{ marginLeft: 4 }}
                disabled={submitting}
                onClick={() => {
                  setUploadFile(null);
                  if (fileInputRef.current) fileInputRef.current.value = '';
                }}
                data-testid="deconstruct-file-clear"
              >
                清除
              </button>
            </div>
          ) : null}
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
            <Loading />
          ) : list.length === 0 ? (
            <EmptyState
              title="还没有参照系"
              hint="使用上方表单粘贴一本参照书进行拆书；拆书结果会自动落入本项目。"
            />
          ) : (
            <div className="table-wrap">
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
                      className={
                        c.canon_id === selectedId
                          ? 'kv-list__row--active'
                          : undefined
                      }
                      style={{ cursor: 'pointer' }}
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
            </div>
          )}
        </div>

        <aside className="detail-pane" data-testid="canon-detail-pane">
          {!selectedId ? (
            <div className="muted">从左侧选择参照系查看详情。</div>
          ) : detailLoading ? (
            <Loading />
          ) : detailErr ? (
            <ErrorBanner>{detailErr}</ErrorBanner>
          ) : detail ? (
            <CanonDetailView detail={detail} />
          ) : null}
        </aside>
      </div>

      {pendingDeleteCanonId ? (
        <ConfirmDialog
          open={true}
          title="删除参照系"
          body="确认删除该参照系？关联的章节 extracts 会一并删除。"
          confirmText="删除"
          danger
          testId="canon-delete-confirm"
          onCancel={() => setPendingDeleteCanonId(null)}
          onConfirm={() => void doDelete(pendingDeleteCanonId)}
        />
      ) : null}
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
  // 文风样例面板同步：拆书 canon → author_style_samples 一次性按钮。
  // 失败在详情头部 ErrorBanner 单独呈现（不污染顶部 err）；成功展示轻反馈。
  const [writeInfo, setWriteInfo] = useState<string | null>(null);
  const [writeErr, setWriteErr] = useState<string | null>(null);
  const [writeBusy, setWriteBusy] = useState(false);
  // v0.1.2：主角人设（顶层 optional）；任一子字段非法类型 → 跳过该子字段
  const protagonist = (cj['protagonist'] ?? null) as
    | Record<string, unknown>
    | null;
  const protagonistIdentity =
    protagonist && typeof protagonist['identity'] === 'string'
      ? (protagonist['identity'] as string)
      : '';
  const protagonistCoreDrive =
    protagonist && typeof protagonist['core_drive'] === 'string'
      ? (protagonist['core_drive'] as string)
      : '';
  const protagonistTags: string[] = Array.isArray(protagonist?.['personality_tags'])
    ? (protagonist!['personality_tags'] as unknown[]).filter(
        (t): t is string => typeof t === 'string',
      )
    : [];
  const protagonistFoils: string[] = Array.isArray(protagonist?.['foil_techniques'])
    ? (protagonist!['foil_techniques'] as unknown[]).filter(
        (t): t is string => typeof t === 'string',
      )
    : [];
  const hasProtagonist =
    protagonist != null &&
    (protagonistIdentity !== '' ||
      protagonistCoreDrive !== '' ||
      protagonistTags.length > 0 ||
      protagonistFoils.length > 0);

  const reportBlocks = parseReportMarkdown(detail.report_md);

  const handleWriteToStyleSample = async (d: CanonDetail) => {
    setWriteInfo(null);
    setWriteErr(null);
    setWriteBusy(true);
    try {
      await referenceApi.writeToStyleSample(d.project_id, d.canon_id);
      setWriteInfo('已写入文风样例面板');
    } catch (e: unknown) {
      setWriteErr(formatApiError(e));
    } finally {
      setWriteBusy(false);
    }
  };

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

      <div style={{ marginTop: 8 }}>
        <button
          className="btn btn--sm btn--primary"
          disabled={writeBusy}
          onClick={() => void handleWriteToStyleSample(detail)}
          data-testid="canon-write-to-style-sample"
        >
          {writeBusy ? '写入中…' : '写入文风样例面板'}
        </button>
        {writeInfo ? (
          <span
            className="muted small"
            style={{ marginLeft: 8 }}
            data-testid="canon-write-to-style-sample-info"
          >
            {writeInfo}
          </span>
        ) : null}
        {writeErr ? (
          <span
            className="small"
            style={{ marginLeft: 8, color: 'var(--color-danger)' }}
            data-testid="canon-write-to-style-sample-err"
          >
            {writeErr}
          </span>
        ) : null}
      </div>

      {hasProtagonist ? (
        <div className="detail-pane__section" data-testid="canon-protagonist-block">
          <div className="detail-pane__section-title">主角人设（v0.1.2）</div>
          {protagonistIdentity ? (
            <div>
              <span className="muted small">一句话定位：</span>
              <span data-testid="canon-protagonist-identity">{protagonistIdentity}</span>
            </div>
          ) : null}
          {protagonistCoreDrive ? (
            <div>
              <span className="muted small">核心诉求：</span>
              <span data-testid="canon-protagonist-core-drive">{protagonistCoreDrive}</span>
            </div>
          ) : null}
          {protagonistTags.length > 0 ? (
            <div>
              <span className="muted small">性格标签：</span>
              <span data-testid="canon-protagonist-personality-tags">
                {protagonistTags.join('、')}
              </span>
            </div>
          ) : null}
          {protagonistFoils.length > 0 ? (
            <div data-testid="canon-protagonist-foil-techniques">
              <span className="muted small">配角衬托：</span>
              <ul style={{ margin: '4px 0 0 18px' }}>
                {protagonistFoils.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            </div>
          ) : null}
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
        <div data-testid="canon-json-block">
          <ReadableJson value={cj} />
        </div>
        <RawJsonDetails
          value={cj}
          testId="canon-json-raw"
          summary="查看原始 JSON"
        />
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
