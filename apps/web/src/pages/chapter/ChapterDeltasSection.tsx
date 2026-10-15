// 「这一章改了什么」——G6 缺口补齐（2026-10-15）。
//
// 后端 `GET /projects/{pid}/chapters/{cid}/deltas` 一直返回 observer 在 commit 时
// 抽出的完整 state delta（角色状态推进 / 世界观 delta / 伏笔收放 / 债务变化 /
// 新事件），但**前端从未调用它**——作者唯一能看到的只有 commit 的 message 一句话。
// 评估报告称「本仓最强的资产（版本化状态）恰好是作者够不着的那部分」，这条就是其中一项。
//
// **为什么不直接渲染 delta 原文**：delta 是机器结构（17 个顶层键，多数是数组），
// 逐字倒出来作者读不动。这里先给一行人话摘要（各类变化各动了几条），再折叠原始
// JSON。摘要口径单点在此模块，`summarizeDelta` 同时被下方测试与未来可能的其它入口复用。
//
// **数据来源的边界**：deltas 是 append-only 历史里这一章的 delta 列表（按
// `created_at` ASC）；同一 delta 可能被后续 commit `supersedes`，此处**不去重、
// 不折叠**——如实呈现，折叠口径留给未来真正需要时再定（宁可多列一条，不悄悄少一条）。

import { useCallback, useEffect, useState } from 'react';
import { storyStateApi, type StateDeltaRow } from '../../api/endpoints';
import { ErrorBanner } from '../../components/ErrorBanner';
import { Loading } from '../../components/Loading';
import { ReadableJson } from '../../components/ReadableJson';

/** 复用 endpoints 的行类型，不在本模块另立一份（形状分叉即假象）。 */
export type DeltaRow = StateDeltaRow;

export interface DeltaSummary {
  characters: number;
  world: number;
  relationships: number;
  hooksOpened: number;
  hooksResolved: number;
  debts: number;
  events: number;
  total: number;
}

/** 各类变化各动了几条。数组缺失按 0 计（非数组按 0，不猜——宁可少报不误报）。 */
export function summarizeDelta(d: DeltaRow): DeltaSummary {
  const n = (v: unknown) => (Array.isArray(v) ? v.length : 0);
  const characters = n(d.character_changes);
  const world = n(d.world_changes);
  const relationships = n(d.relationship_changes);
  const hooksOpened = n(d.new_hooks);
  const hooksResolved = n(d.resolved_hooks);
  const debts = n(d.debt_changes);
  const events = n(d.new_events);
  return {
    characters,
    world,
    relationships,
    hooksOpened,
    hooksResolved,
    debts,
    events,
    total:
      characters +
      world +
      relationships +
      hooksOpened +
      hooksResolved +
      debts +
      events,
  };
}

const PARTS: Array<{ key: keyof DeltaSummary; label: string }> = [
  { key: 'characters', label: '角色' },
  { key: 'world', label: '世界观' },
  { key: 'relationships', label: '关系' },
  { key: 'hooksOpened', label: '新埋伏笔' },
  { key: 'hooksResolved', label: '回收伏笔' },
  { key: 'debts', label: '债务' },
  { key: 'events', label: '新事件' },
];

export function ChapterDeltasSection({
  projectId,
  chapterId,
}: {
  projectId: string;
  chapterId: string;
}) {
  const [rows, setRows] = useState<DeltaRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    setLoading(true);
    try {
      setRows(await storyStateApi.listDeltas(projectId, chapterId));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '加载状态变更失败');
    } finally {
      setLoading(false);
    }
  }, [projectId, chapterId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="card" data-testid="chapter-deltas-section">
      <div className="detail-pane__title">这一章改了什么（状态变更）</div>
      <div className="muted small">
        由 commit 时的 observer 从正文抽取，供后续章节的上下文使用。这里是历史记录，不是编辑器。
      </div>
      <ErrorBanner>{err}</ErrorBanner>
      {loading && rows === null ? <Loading /> : null}
      {rows !== null && rows.length === 0 ? (
        <div className="muted small" data-testid="chapter-deltas-empty">
          本章尚未产生状态变更（通常意味着还没提交过）。
        </div>
      ) : null}
      {rows !== null && rows.length > 0 ? (
        <div className="table-wrap">
          <table className="table" data-testid="chapter-deltas-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>状态版本</th>
                <th>摘要</th>
                <th>明细</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d, i) => {
                const s = summarizeDelta(d);
                return (
                  <tr key={d.delta_id ?? `${d.created_at ?? ''}-${i}`} data-testid="delta-row">
                    <td className="muted small">
                      {d.created_at ? new Date(d.created_at).toLocaleString() : '—'}
                    </td>
                    <td className="muted small">
                      {d.previous_state_version != null
                        ? `v${d.previous_state_version} →`
                        : '—'}
                    </td>
                    <td>
                      {s.total === 0 ? (
                        <span className="muted small">（无变化）</span>
                      ) : (
                        <span data-testid="delta-summary">
                          {PARTS.filter((p) => s[p.key] > 0)
                            .map((p) => `${p.label} ${s[p.key]}`)
                            .join(' · ')}
                        </span>
                      )}
                    </td>
                    <td>
                      <details>
                        <summary className="muted small">查看原文</summary>
                        <ReadableJson value={d} emptyText="（空）" testId={`delta-json-${i}`} />
                      </details>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
