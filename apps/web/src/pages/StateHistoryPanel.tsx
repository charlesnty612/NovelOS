// 状态版本历史：commit 列表 + 「这一版改了什么」+ 回滚（G6 缺口补齐，2026-10-15）。
//
// 后端三件套早已存在、前端零入口：
//   - `GET  /projects/{pid}/commits`              —— 提交历史（`commitsApi.list` 早已接）
//   - `GET  /projects/{pid}/state/diff?a=&b=`     —— 两版本结构化 diff
//   - `POST /commits/{cid}/rollback`               —— 回滚
// 作者因而无法回答「这个状态是哪一章改的」「observer 写错了怎么反悔」。
//
// ⚠️ **回滚的高风险批准**：`rollback_commit` 对 HIGH 风险的 observer 变更抛
// `ApprovalRequiredError` → 409 `{"error":"approval_required",
// "high_risk_change_ids":[...]}`。本组件的纪律与质量门禁的 `gate_override` 同源：
// **先把要批准的东西摆出来，再让作者点**。绝不自动重试第二次——那等于绕过批准。
// 批准后连同 change id 一起回传，留痕在后端 `commits.author_approval_json`。

import { useCallback, useState } from 'react';
import { ApiError } from '../api/client';
import { commitsApi, storyStateApi, type StateDiffResponse } from '../api/endpoints';
import type { Commit } from '../api/types';
import { ErrorBanner } from '../components/ErrorBanner';
import { ReadableJson } from '../components/ReadableJson';

interface ApprovalDemand {
  commitId: string;
  version: number;
  highRiskChangeIds: string[];
}

/**
 * 判定后端是否在要求「高风险变更的显式批准」。
 *
 * ⚠️ **不能直接 `e.error === 'approval_required'`**：`ApiError.detail` 是
 * **字符串**——`client.ts::parseError` 把 FastAPI 的对象 detail 做了
 * `JSON.stringify` 才塞进去。所以必须先按 status 判、再解析字符串。
 * （写错了的形状在这里是静默的：解析失败会落到「普通失败」，作者看到一句
 *  没有清单的 409，正是「不许在不知道批准什么的情况下批准」要防的情形。）
 */
function readApprovalDemand(e: unknown): string[] | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(e.detail);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const p = parsed as { error?: unknown; high_risk_change_ids?: unknown };
  if (p.error !== 'approval_required') return null;
  return Array.isArray(p.high_risk_change_ids)
    ? p.high_risk_change_ids.filter((x): x is string => typeof x === 'string')
    : [];
}

export function StateHistoryPanel({
  projectId,
  commits,
  onChanged,
}: {
  projectId: string;
  commits: Commit[];
  onChanged: () => void | Promise<void>;
}) {
  const [diffFor, setDiffFor] = useState<number | null>(null);
  const [diff, setDiff] = useState<StateDiffResponse | null>(null);
  const [diffErr, setDiffErr] = useState<string | null>(null);
  const [demand, setDemand] = useState<ApprovalDemand | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const showDiff = useCallback(
    async (c: Commit) => {
      setDiffErr(null);
      setDiff(null);
      setDiffFor(c.version);
      // 与上一版比；首版没有上一版可比。
      if (c.parent_version == null) {
        setDiffErr('这是第一个 commit，没有上一版可比。');
        return;
      }
      try {
        setDiff(await storyStateApi.diff(projectId, c.parent_version, c.version));
      } catch (e: unknown) {
        setDiffErr(e instanceof Error ? e.message : '取 diff 失败');
      }
    },
    [projectId],
  );

  const doRollback = useCallback(
    async (commitId: string, approval?: Record<string, unknown>) => {
      setErr(null);
      setBusy(true);
      try {
        await commitsApi.rollback(commitId, approval);
        setDemand(null);
        await onChanged();
      } catch (e: unknown) {
        const ids = readApprovalDemand(e);
        if (ids !== null) {
          // 第一次就要求批准 ⇒ 把清单摆出来，等作者点头；**不自动重发**。
          const c = commits.find((x) => x.commit_id === commitId);
          setDemand({ commitId, version: c?.version ?? 0, highRiskChangeIds: ids });
        } else {
          setErr(e instanceof Error ? e.message : '回滚失败');
        }
      } finally {
        setBusy(false);
      }
    },
    [commits, onChanged],
  );

  if (commits.length === 0) {
    return (
      <div className="card" data-testid="state-history-panel">
        <div className="detail-pane__title">状态版本历史</div>
        <div className="muted small" data-testid="state-history-empty">
          还没有提交记录。
        </div>
      </div>
    );
  }

  return (
    <div className="card" data-testid="state-history-panel">
      <div className="detail-pane__title">状态版本历史</div>
      <div className="muted small">
        每次 commit 落一个状态快照。回滚会**新增一条反向 commit**，历史不丢。
      </div>
      <ErrorBanner>{err}</ErrorBanner>
      <div className="table-wrap">
        <table className="table" data-testid="state-history-table">
          <thead>
            <tr>
              <th>版本</th>
              <th>说明</th>
              <th>时间</th>
              <th className="right">操作</th>
            </tr>
          </thead>
          <tbody>
            {commits.map((c) => (
              <tr key={c.commit_id} data-testid="state-history-row">
                <td className="muted small">v{c.version}</td>
                <td>
                  {c.message || '—'}
                  {c.author ? <span className="muted small">（{c.author}）</span> : null}
                </td>
                <td className="muted small">{new Date(c.created_at).toLocaleString()}</td>
                <td className="right">
                  <button
                    className="btn btn--sm"
                    data-testid="state-diff-btn"
                    onClick={() => void showDiff(c)}
                  >
                    改了什么
                  </button>
                  <button
                    className="btn btn--sm"
                    style={{ marginLeft: 6 }}
                    data-testid="state-rollback-btn"
                    disabled={busy}
                    title="回滚该 commit 的状态变更（会新增一条反向 commit）"
                    // 首次点击**显式传空批准体**（而不是省略参数）：批准与否是这
                    // 次请求的语义组成部分，不该由函数默认值替调用方决定。
                    onClick={() => void doRollback(c.commit_id, {})}
                  >
                    回滚
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {diffFor != null ? (
        <details open data-testid="state-diff-panel">
          <summary className="muted small">v{diffFor} 的状态变化</summary>
          <ErrorBanner>{diffErr}</ErrorBanner>
          {diff ? <ReadableJson value={diff} emptyText="（无变化）" testId="state-diff-json" /> : null}
        </details>
      ) : null}

      {demand ? (
        <div className="alert alert--warn" data-testid="rollback-approval">
          <div className="cdp-banner__title">回滚 v{demand.version} 需要显式批准</div>
          <div className="muted small cdp-banner__line">
            该 commit 含<b>高风险状态变更</b>（世界规则类）。请确认下列条目确实要撤回：
          </div>
          <ul data-testid="rollback-approval-list">
            {demand.highRiskChangeIds.map((id) => (
              <li key={id}>
                <code>{id}</code>
              </li>
            ))}
          </ul>
          <div className="cdp-gate-banner__actions">
            <button
              className="btn btn--primary"
              data-testid="rollback-approve-btn"
              disabled={busy}
              onClick={() =>
                void doRollback(demand.commitId, {
                  approved: true,
                  approver: 'human',
                  high_risk_change_ids: demand.highRiskChangeIds,
                })
              }
            >
              {busy ? '回滚中…' : '确认回滚以上变更'}
            </button>
            <button
              className="btn"
              data-testid="rollback-approval-cancel"
              disabled={busy}
              onClick={() => setDemand(null)}
            >
              取消
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
