// 交付判定 + 签约自检面板（评估 G6：schema 早已生成，UI 不接）。
//
// 后端 `GET /projects/{pid}/delivery-verdict` 与 `GET /projects/{pid}/signing-check`
// 一直在算、一直在返回，`types.generated.ts` 里连 schema 都生成了，但 UI 零入口——
// 而「这本书能不能签约」恰是本产品的主要出口，作者却只能去翻后端日志。
//
// 三条口径纪律（都不是 UI 偏好，是这份数据的性质）：
//
// 1. **判定是派生视图，每次 GET 现算、不落库。** 所以面板不缓存、不显示「判定于
//    某时」，重新点刷新就重算一次。跨会话复用一个旧数字就是在撒谎。
// 2. **证据不可达要说出来。** `evidence_sources[].reachable=false` 时（例如某类
//    证据面没采到），面板**显式列出**不可达的源，而不是把「没看到问题」显示成通过。
//    这是本仓签名体检自己的口径（`run_signing_check` 的诚实面），UI 不得反过来
//    把它压成绿灯。
// 3. **四档判定 ≠ 章节状态机。** `deliverable / needs_work / not_deliverable /
//    not_a_candidate` 是交付结论；章节的 `status` 是流程位置。两者分开渲染，
//    不混在一列里——本仓刚吃过「同一份报告里两个最新」的亏。

import { useCallback, useEffect, useState } from 'react';
import { deliveryApi, signingCheckApi } from '../api/endpoints';
import type {
  DeliveryVerdict,
  DeliveryVerdictResponse,
  SigningCheckResult,
} from '../api/types';
import { ErrorBanner } from '../components/ErrorBanner';
import { Loading } from '../components/Loading';

interface DeliveryPanelProps {
  projectId: string;
}

const VERDICT_LABEL: Record<DeliveryVerdict, string> = {
  deliverable: '可交付',
  needs_work: '需再改',
  not_deliverable: '不可交付',
  not_a_candidate: '不构成候选',
};

const VERDICT_CLASS: Record<DeliveryVerdict, string> = {
  deliverable: 'badge badge--chapter-committed',
  needs_work: 'badge badge--chapter-reviewed',
  not_deliverable: 'badge badge--chapter-failed',
  not_a_candidate: 'badge badge--chapter-planned',
};

const LEVEL_CLASS: Record<string, string> = {
  pass: 'badge badge--chapter-committed',
  warn: 'badge badge--chapter-reviewed',
  fail: 'badge badge--chapter-failed',
  info: 'badge badge--chapter-planned',
};

export function DeliveryPanel({ projectId }: DeliveryPanelProps) {
  const [verdict, setVerdict] = useState<DeliveryVerdictResponse | null>(null);
  const [signing, setSigning] = useState<SigningCheckResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // 两个都是纯只读 GET；signing-check 是 delivery-verdict 的证据源之一，
      // 并行拉，省一次往返。
      const [v, s] = await Promise.all([
        deliveryApi.verdict(projectId),
        signingCheckApi.run(projectId),
      ]);
      setVerdict(v);
      setSigning(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <Loading text="正在判定交付与签约自检…" />;

  const unreachable = (verdict?.evidence_sources ?? []).filter((s) => !s.reachable);

  return (
    <div className="card stack" data-testid="delivery-panel">
      <div className="row row--between">
        <div className="detail-pane__title">能不能交付</div>
        <button type="button" className="btn btn--small" onClick={() => void load()}>
          重新判定
        </button>
      </div>

      <ErrorBanner>{error}</ErrorBanner>

      {verdict ? (
        <>
          <div className="row" data-testid="delivery-verdict">
            <span className={VERDICT_CLASS[verdict.project_verdict]}>
              {VERDICT_LABEL[verdict.project_verdict]}
            </span>
            <span className="muted small">
              {verdict.roll_up.written_chapter_count}/{verdict.roll_up.chapter_count} 章已写 ·
              可交付 {verdict.roll_up.deliverable_count} 章
            </span>
          </div>

          {/* 口径纪律 2：证据不可达必须显式说出来。 */}
          {unreachable.length > 0 ? (
            <p className="muted small" data-testid="delivery-unreachable">
              证据源不可达：
              {unreachable.map((s) => `${s.key}（${s.detail}）`).join('；')}
            </p>
          ) : null}

          {(verdict.project_reasons ?? []).length > 0 ? (
            <ul className="muted small" data-testid="delivery-project-reasons">
              {verdict.project_reasons!.map((r, i) => (
                <li key={`${r.rule_id}-${i}`}>
                  {r.message}
                  {r.evidence ? `（证据：${r.evidence}）` : ''}
                </li>
              ))}
            </ul>
          ) : null}

          {(verdict.chapters ?? []).length > 0 ? (
            <details>
              <summary>逐章判定（{verdict.chapters!.length}）</summary>
              <table className="table" data-testid="delivery-chapter-table">
                <thead>
                  <tr>
                    <th>章</th>
                    <th>标题</th>
                    <th>交付判定</th>
                    <th>首个降级理由</th>
                  </tr>
                </thead>
                <tbody>
                  {verdict.chapters!.map((c) => (
                    <tr key={c.chapter_id} data-testid={`delivery-chapter-${c.chapter_id}`}>
                      <td>第{c.number}章</td>
                      <td>{c.title}</td>
                      <td>
                        <span className={VERDICT_CLASS[c.verdict]}>
                          {VERDICT_LABEL[c.verdict]}
                        </span>
                      </td>
                      <td className="muted small">{c.blocking_reason ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          ) : null}
        </>
      ) : null}

      {signing ? (
        <div data-testid="signing-check">
          <div className="detail-pane__title">
            签约自检（通过 {signing.summary.pass_count} · 警告{' '}
            {signing.summary.warn_count} · 不通过 {signing.summary.fail_count}）
          </div>
          {signing.items.map((it) => (
            <p key={it.key} className="small">
              <span className={LEVEL_CLASS[it.level] ?? 'badge'}>{it.level}</span> {it.detail}
              {it.advice ? <span className="muted"> — {it.advice}</span> : null}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
