// Story Bible 第六个 Tab：题材（题材库 P3a 后端 / P3b 面板）。
//
// 结构：
// - 顶部「当前绑定」卡片：名称 / 题材 / 版本 / 爽点型数量 + 解绑按钮；未绑定 → EmptyState；
// - 「绑定题材包」卡片：GET /genre-packs 列表下拉 + 绑定（单 slot，覆盖式）；
// - 「爽点类型」卡片：绑定 payload.payoff_types 表格（type_id / name / strength / density_cap）；
// - 「题材核销」卡片：最近一次 chapter-review run 的 review_report.genre_check
//   → issues 列表（恒 warning，report-only），warning 色系。
//
// 设计要点：
// - 全部数据来自既有端点（本批次不改后端）：genreApi（packages/core/api/routers/genre.py）
//   + workflowsApi.listByProject（review_report 只经 run 的 checkpoint / pause_payload 暴露）。
// - genre_check 是**写后核销**产物、report-only 不阻断：拿不到就落空态提示，不报错、不阻塞面板。
// - 绑定 / 解绑成功后直接用响应里的绑定详情刷新（后端已回带 pack 全文），不二次拉取。
// - 类型手写在 api/types.ts（types.generated.ts 收尾统一 regen）。

import { useCallback, useEffect, useState } from 'react';
import { genreApi, workflowsApi } from '../../api/endpoints';
import type {
  GenreBinding,
  GenreCheck,
  GenrePack,
  GenrePackSummary,
  WorkflowRun,
} from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { ErrorBanner } from '../../components/ErrorBanner';
import { formatApiError } from '../../utils/formatApiError';
import { extractPausePayload } from '../../utils/pausePayload';

interface GenreTabProps {
  projectId: string;
}

const CHAPTER_REVIEW_WORKFLOW = 'chapter-review';

/** run 是否属于 chapter-review（list 端点同时给 workflow_id 与 workflow_name）。 */
function isChapterReviewRun(run: WorkflowRun): boolean {
  return (
    run.workflow_id === CHAPTER_REVIEW_WORKFLOW ||
    run.workflow_name === CHAPTER_REVIEW_WORKFLOW
  );
}

/** 从 pause_payload 里取 review_report.genre_check（形状非法 → null，不炸）。 */
function readGenreCheck(pausePayload: unknown): GenreCheck | null {
  if (!pausePayload || typeof pausePayload !== 'object') return null;
  const reviewReport = (pausePayload as Record<string, unknown>)['review_report'];
  if (!reviewReport || typeof reviewReport !== 'object') return null;
  const raw = (reviewReport as Record<string, unknown>)['genre_check'];
  if (!raw || typeof raw !== 'object') return null;
  const check = raw as Record<string, unknown>;
  if (!Array.isArray(check['issues'])) return null;
  return check as unknown as GenreCheck;
}

// ---------------------------------------------------------------------------
// 面板组件
// ---------------------------------------------------------------------------

export function GenreTab({ projectId }: GenreTabProps) {
  const [binding, setBinding] = useState<GenreBinding | null>(null);
  const [packs, setPacks] = useState<GenrePackSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selectedPackId, setSelectedPackId] = useState('');
  const [busy, setBusy] = useState(false);

  const [genreCheck, setGenreCheck] = useState<GenreCheck | null>(null);
  const [checkHint, setCheckHint] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [b, list] = await Promise.all([
        genreApi.getBinding(projectId),
        genreApi.listPacks(),
      ]);
      setBinding(b);
      setPacks(list);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '加载题材包失败');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // 题材核销：取最近一次 chapter-review run 的 review_report.genre_check。
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setCheckHint(null);
      try {
        const runs = await workflowsApi.listByProject(projectId);
        const reviewRuns = runs
          .filter(isChapterReviewRun)
          .slice()
          .sort((a, b) =>
            a.started_at < b.started_at ? 1 : a.started_at > b.started_at ? -1 : 0,
          );
        if (cancelled) return;
        for (const run of reviewRuns) {
          const check = readGenreCheck(extractPausePayload(run.checkpoint_json));
          if (check) {
            setGenreCheck(check);
            return;
          }
        }
        setGenreCheck(null);
        setCheckHint(
          reviewRuns.length === 0
            ? '本项目还没有 chapter-review 记录（审校完成后显示题材核销结果）。'
            : '最近一次审校没有产出 genre_check 段（未绑定题材包时该段缺席）。',
        );
      } catch (e: unknown) {
        if (cancelled) return;
        setGenreCheck(null);
        setCheckHint(
          e instanceof Error ? `题材核销记录加载失败：${e.message}` : '题材核销记录加载失败',
        );
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const handleBind = async () => {
    if (!selectedPackId) {
      setErr('请先选择题材包');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const next = await genreApi.bind(projectId, selectedPackId);
      setBinding(next);
      setSelectedPackId('');
    } catch (e: unknown) {
      setErr(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const handleUnbind = async () => {
    setBusy(true);
    setErr(null);
    try {
      const next = await genreApi.unbind(projectId);
      setBinding(next);
    } catch (e: unknown) {
      setErr(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const pack: GenrePack | null = binding?.pack ?? null;
  const payoffTypes = pack?.payload?.payoff_types ?? [];

  return (
    <div data-testid="genre-tab">
      <div className="toolbar">
        <div className="muted small">
          题材包 Genre Pack（题材库 P1a/P3a）—— 跨作品聚合的题材公约资产。项目绑定后，
          director / scene_planner / writer 各自注入声明段；写后由核销层对账配比与节奏红线
          （report-only，不阻断）。
        </div>
      </div>

      <ErrorBanner>{err}</ErrorBanner>

      {/* ----------------- 当前绑定 ----------------- */}
      <div className="card" style={{ marginBottom: 16 }} data-testid="genre-binding-card">
        <div className="detail-pane__title">当前绑定</div>
        {loading ? (
          <div className="muted">加载中…</div>
        ) : pack ? (
          <div data-testid="genre-binding-info">
            <div className="form-grid">
              <Field label="名称" value={pack.name} testId="genre-binding-name" />
              <Field label="题材" value={pack.genre_tag} testId="genre-binding-tag" />
              <Field label="版本" value={`v${pack.version}`} testId="genre-binding-version" />
              <Field
                label="爽点型数量"
                value={String(payoffTypes.length)}
                testId="genre-binding-payoff-count"
              />
            </div>
            <div className="muted small" style={{ marginTop: 8 }}>
              pack_id：{pack.pack_id}
              {pack.source_path ? ` · 来源：${pack.source_path}` : ''}
            </div>
            <div style={{ marginTop: 12 }}>
              <button
                className="btn btn--sm btn--danger"
                disabled={busy}
                onClick={() => void handleUnbind()}
                data-testid="genre-unbind"
              >
                解绑
              </button>
            </div>
          </div>
        ) : (
          <EmptyState
            title="尚未绑定题材包"
            hint="从下方「绑定题材包」选择后点绑定；绑定后各 consumer 自动注入题材段。"
          />
        )}
      </div>

      {/* ----------------- 绑定 / 解绑 ----------------- */}
      <div className="card" style={{ marginBottom: 16 }} data-testid="genre-bind-card">
        <div className="detail-pane__title">绑定题材包</div>
        <div className="form-row">
          <label>题材包</label>
          <select
            value={selectedPackId}
            disabled={busy}
            onChange={(e) => setSelectedPackId(e.target.value)}
            data-testid="genre-pack-select"
          >
            <option value="">（选择题材包）</option>
            {packs.map((p) => (
              <option key={p.pack_id} value={p.pack_id}>
                {`${p.name}（${p.genre_tag} v${p.version} · 爽点 ${p.payoff_type_count}）`}
              </option>
            ))}
          </select>
          <button
            className="btn btn--primary btn--sm"
            disabled={busy || !selectedPackId}
            onClick={() => void handleBind()}
            data-testid="genre-bind"
          >
            绑定
          </button>
        </div>
        <div className="muted small">
          绑定是项目单 slot（重复绑定=覆盖）；解绑后各 consumer 回到零注入。
        </div>
      </div>

      {/* ----------------- 爽点类型 ----------------- */}
      <div className="card" style={{ marginBottom: 16 }} data-testid="genre-payoff-panel">
        <div className="detail-pane__title">爽点类型（{payoffTypes.length}）</div>
        {payoffTypes.length === 0 ? (
          <div className="muted">当前题材包未声明 payoff_types。</div>
        ) : (
          <table className="table" data-testid="genre-payoff-table">
            <thead>
              <tr>
                <th>type_id</th>
                <th>名称</th>
                <th>强度</th>
                <th>密度上限</th>
              </tr>
            </thead>
            <tbody>
              {payoffTypes.map((t, idx) => (
                <tr key={t.type_id || idx} data-testid="genre-payoff-row">
                  <td className="muted small">{t.type_id}</td>
                  <td>{t.name}</td>
                  <td className="muted small">{t.strength || '—'}</td>
                  <td className="muted small">{t.density_cap || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ----------------- 题材核销（genre_check） ----------------- */}
      <div className="card" data-testid="genre-check-panel">
        <div className="detail-pane__title">
          题材核销 genre_check
          {genreCheck ? `（${genreCheck.issues.length}）` : ''}
        </div>
        {!genreCheck ? (
          <div className="muted" data-testid="genre-check-empty">
            {checkHint ?? '暂无核销记录。'}
          </div>
        ) : (
          <div>
            <div className="muted small" style={{ marginBottom: 8 }}>
              核销口径：配比偏差 + 节奏红线；issue 恒 warning，report-only 不阻断。
              {genreCheck.pack_id
                ? `（pack ${genreCheck.pack_id}@v${genreCheck.pack_version ?? '?'}）`
                : ''}
            </div>
            {genreCheck.error ? (
              <div className="small" style={{ color: 'var(--color-warn)' }}>
                核销读路径异常：{genreCheck.error}
              </div>
            ) : null}
            {genreCheck.issues.length === 0 ? (
              <div className="muted" data-testid="genre-check-clean">
                本章无题材核销问题。
              </div>
            ) : (
              <ul style={{ margin: '4px 0 0 18px' }} data-testid="genre-check-issues">
                {genreCheck.issues.map((issue, idx) => (
                  <li
                    key={`${issue.rule_id}-${idx}`}
                    style={{ color: 'var(--color-warn)', marginBottom: 4 }}
                    data-testid="genre-check-issue"
                  >
                    <span className="muted small">[{issue.rule_id}]</span>{' '}
                    <span>{issue.message}</span>
                    {issue.suggestion ? (
                      <div className="small muted">建议：{issue.suggestion}</div>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            {genreCheck.skipped && genreCheck.skipped.length > 0 ? (
              <div className="muted small" style={{ marginTop: 8 }}>
                跳过项：{genreCheck.skipped.join('、')}
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId?: string;
}) {
  return (
    <div>
      <div className="muted small">{label}</div>
      <div data-testid={testId}>{value}</div>
    </div>
  );
}
