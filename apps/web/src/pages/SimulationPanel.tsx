// 状态推演（What-if Simulation）面板。
//
// 后端 `POST /projects/{pid}/simulate` 一直在算，但 UI 零入口。难处在**入参**：
// 它要的不是「几个改动」，而是**一整套完整的 Delta 对象**（10 元信息字段 + 7 变更
// 数组，每条 change 还带 change_id/op/target_id/confidence/evidence/risk_level）——
// 作者手写不出来，写错一个必填字段就是 422。故本面板走「**基于已有 delta 推演**」：
// 挑一章的既有 delta → 深拷贝 → 作者只改业务字段 → 跑，看 diff。
//
// ── 为什么是「白名单」而不是「黑名单」地剔除多余键 ──
// `GET /chapters/{cid}/deltas` 的行比 schema 多一个 `status`（后端 `queries.list_deltas`
// 显式 `d["status"] = r["status"]` 补的），而 `state-delta.schema.json` 是
// `additionalProperties: false` ⇒ 原样回传必然 422。黑名单只挡 `status`，挡住的是
// 「已知的坏键」，白名单挡住的是「一切 schema 不认的键」，后者才是能兜住未来变更的那一种。
//
// ── 三处「派生而非照抄」的字段（各自有硬理由，不是洁癖）──
//   delta_id  : `state_deltas.delta_id` 是 PRIMARY KEY。沿用原 id ⇒ 与原 delta 撞行，
//               INSERT 直接 IntegrityError（且原行会被静默顶掉风险）。必须重新生成。
//   supersedes: 该列有 `FOREIGN KEY → state_deltas(delta_id)`，且 `submit_delta` 会把被
//               指向的行 UPDATE 成 `superseded`。原样继承 = 用一次推演把**真实历史里
//               别的 delta 标成已废弃**。派生件不是重试，语义上也不该继承 ⇒ 置 null。
//   created_by: 如实记成 `simulation:ui`，好让「这条 delta 是人改出来的」在 DB 里可查。
//
// ── 不编辑的数组（relationship_changes / new_events / new_hooks）──
// 这三个的元素必填字段要么作者无从判断（evidence 要正文摘录）、要么改错了会造出
// 「凭空多一个事件/新埋伏笔」这种语义事故。本批只放开作者改得动、改得懂的四个；
// 其余只读展示并写明理由——宁可少做，不要做一个作者看不懂的半成品。

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  chaptersApi,
  simulationApi,
  storyStateApi,
  type StateDeltaRow,
} from '../api/endpoints';
import type {
  Chapter,
  SimulationResult,
  SnapshotListDiff,
  StateDiff,
} from '../api/types';
import { ErrorBanner } from '../components/ErrorBanner';
import { Loading } from '../components/Loading';
import { ReadableJson } from '../components/ReadableJson';

/** schema 的 7 个变更数组（顺序即 schema properties 顺序，便于与原文对照）。 */
export const SEVEN_ARRAYS = [
  'character_changes',
  'world_changes',
  'relationship_changes',
  'new_events',
  'resolved_hooks',
  'new_hooks',
  'debt_changes',
] as const;

type SevenArrayKey = (typeof SEVEN_ARRAYS)[number];

type Row = Record<string, unknown>;

export interface SimulationPanelProps {
  projectId: string;
}

/** 深拷贝。走 JSON 往返而非 structuredClone：传输层本来就是 JSON，`undefined` 键
 *  在真实链路上也会被丢掉，在这里保持同样口径（结构化克隆会把它们留着，制造
 *  「本地看着有、线上没有」的偏差）。 */
export function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

/** 生成新 delta_id。用 crypto.randomUUID；老环境无该 API 时退回时间戳+随机数。 */
export function newDeltaId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === 'function') return `dlt_sim_${c.randomUUID()}`;
  return `dlt_sim_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

/** 默认推演名带紧凑时间戳：后端分支名是「同名即 409」，不带时间戳等于让作者
 * 每次重跑都撞上一次自己。口径对齐后端 `_now_compact_ts()`（`sim-YYYYMMDDTHHMMSSZ`）。 */
export function defaultSimName(): string {
  const d = new Date();
  const p = (n: number, w = 2) => String(n).padStart(w, '0');
  const stamp =
    `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}` +
    `T${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}Z`;
  return `sim-${stamp}`;
}

/**
 * 从既有 delta 派生一条可提交的推演 delta。
 *
 * 导出是因为它是本面板**唯一**决定「机器字段怎么继承」的地方，必须能被测试直接
 * 钉住（测试要断言基底对象不被污染、机器字段没被清空、id 换过）。
 */
export function deriveDelta(base: StateDeltaRow, chapterId: string): Row {
  const out: Row = {
    delta_id: newDeltaId(),
    delta_version: typeof base.delta_version === 'number' ? base.delta_version : 1,
    schema_version:
      typeof base.schema_version === 'string' ? base.schema_version : 'state-delta-v0',
    chapter_id: typeof base.chapter_id === 'string' && base.chapter_id ? base.chapter_id : chapterId,
    // 沿用原 delta 的 workflow_run_id：它是这次观察的真实来源，换成编造的 run id
    // 会让 commits 行指向一个不存在的 run，事后无从追「这个假设当初是在哪次写作里提的」。
    workflow_run_id: base.workflow_run_id ?? '',
    previous_state_version:
      typeof base.previous_state_version === 'number' ? base.previous_state_version : 1,
    created_by: 'simulation:ui',
    created_at: new Date().toISOString(),
    // 置 null 而非继承：见文件头「三处派生而非照抄」。
    supersedes: null,
    notes: base.notes ?? null,
  };
  for (const k of SEVEN_ARRAYS) {
    const v = base[k];
    out[k] = clone(Array.isArray(v) ? v : []);
  }
  return out;
}

/** 把 edit 输入框的文本还原成 JSON 值：空 → null；能 parse 就 parse（数字/对象/数组）；
 * parse 失败按**原样字符串**存（作者写「青萍镇」不是要写带引号的 JSON）。
 * schema 对 `after` 不限类型，故这个映射不会凭空造出违规值。 */
export function parseLooseValue(text: string): unknown {
  const t = text.trim();
  if (t === '') return null;
  try {
    return JSON.parse(t);
  } catch {
    return text;
  }
}

/** 展示用：把任意值转成输入框初始文本（与 parseLooseValue 互逆的**显示**侧）。 */
function toInputText(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  return JSON.stringify(v);
}

function asRows(v: unknown): Row[] {
  return Array.isArray(v) ? (v as Row[]) : [];
}

const HOOK_STATUS = ['OPEN', 'ACTIVE', 'ESCALATED', 'RESOLVED', 'ABANDONED'] as const;
const DEBT_STATUS = ['open', 'acknowledged', 'paid', 'forgiven'] as const;

// ── diff 计数 ────────────────────────────────────────────────────────────────

/** 单个 SnapshotListDiff 的变化条数。**所有键都可能整个省略**（后端「空则不写」），
 * 故一律 `?? 0`，绝不假设存在——这是 types.ts 里写死的读法纪律。 */
export function countListDiff(d: SnapshotListDiff | undefined): number {
  if (!d) return 0;
  return (d.added?.length ?? 0) + (d.removed?.length ?? 0) + (d.changed?.length ?? 0);
}

/** 整份 diff 的变化条数；0 ⇒ 空 diff（UI 显示「无变化」而不是空白卡片）。 */
export function countStateDiff(diff: StateDiff): number {
  return (
    countListDiff(diff.characters) +
    countListDiff(diff.hooks) +
    countListDiff(diff.debts) +
    countListDiff(diff.recent_events) +
    Object.keys(diff.world ?? {}).length +
    Object.keys(diff.events_changed ?? {}).length
  );
}

/** 从不透明 blob 里取个可读标识：优先 *_id / id / name，回退下标。 */
function blobId(b: unknown, idx: number): string {
  if (b && typeof b === 'object') {
    const o = b as Row;
    for (const k of ['character_id', 'hook_id', 'debt_id', 'event_id', 'id', 'name']) {
      if (typeof o[k] === 'string' && o[k]) return o[k] as string;
    }
  }
  return `#${idx}`;
}

function ListDiffBlock({
  title,
  diff,
  testId,
}: {
  title: string;
  diff: SnapshotListDiff | undefined;
  testId: string;
}) {
  const added = diff?.added ?? [];
  const removed = diff?.removed ?? [];
  const changed = diff?.changed ?? [];
  return (
    <div data-testid={testId}>
      <div className="detail-pane__title">
        {title}
        <span className="muted small">
          {' '}
          新增 {added.length} · 移除 {removed.length} · 变更 {changed.length}
        </span>
      </div>
      {added.length > 0 ? (
        <p className="small">
          <span className="badge badge--chapter-committed">新增</span>{' '}
          {added.map((b, i) => blobId(b, i)).join('、')}
        </p>
      ) : null}
      {removed.length > 0 ? (
        <p className="small">
          <span className="badge badge--chapter-failed">移除</span>{' '}
          {removed.map((b, i) => blobId(b, i)).join('、')}
        </p>
      ) : null}
      {changed.length > 0 ? (
        <ul className="small" data-testid={`${testId}-changed`}>
          {changed.map((c, i) => (
            <li key={c.id ?? i}>
              <strong>{c.id ?? `#${i}`}</strong>
              {Object.entries(c.changes ?? {}).map(([field, pair]) => (
                <span key={field} className="muted">
                  {'  '}
                  {field}：{JSON.stringify(pair?.before)} → {JSON.stringify(pair?.after)}
                </span>
              ))}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function SimulationPanel({ projectId }: SimulationPanelProps) {
  const [chapters, setChapters] = useState<Chapter[] | null>(null);
  /** 同上：读失败 ≠ 「这个项目一章都没有」。 */
  const [chaptersFailed, setChaptersFailed] = useState(false);
  const [chapterId, setChapterId] = useState<string>('');
  const [deltas, setDeltas] = useState<StateDeltaRow[] | null>(null);
  /** 读失败与「确实没有 delta」是**两件事**，必须分开记：把前者显示成后者
   *  就是本仓反复吃的「把『没看到问题』显示成通过」。 */
  const [deltasFailed, setDeltasFailed] = useState(false);
  const [baseDeltaId, setBaseDeltaId] = useState<string>('');
  const [draft, setDraft] = useState<Row | null>(null);

  const [simName, setSimName] = useState<string>(() => defaultSimName());
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [running, setRunning] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);

  // 章节列表只依赖项目，故不随选中章重拉。
  useEffect(() => {
    let alive = true;
    setLoadErr(null);
    setChaptersFailed(false);
    chaptersApi
      .listByProject(projectId)
      .then((rows) => {
        if (!alive) return;
        setChapters(rows);
        setChapterId((prev) => prev || rows[0]?.chapter_id || '');
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setChaptersFailed(true);
        setLoadErr(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, [projectId]);

  const loadDeltas = useCallback(async () => {
    if (!chapterId) {
      setDeltas([]);
      setDeltasFailed(false);
      return;
    }
    setLoadErr(null);
    setDeltas(null);
    setDeltasFailed(false);
    setBaseDeltaId('');
    setDraft(null);
    setResult(null);
    try {
      setDeltas(await storyStateApi.listDeltas(projectId, chapterId));
    } catch (e: unknown) {
      // 失败 ⇒ deltas 留 null（不置 []），并置 deltasFailed，避免空列表被当成
      // 「本章没有状态变更」——那会把一个后端故障说成一个关于这本书的事实。
      setDeltasFailed(true);
      setLoadErr(e instanceof Error ? e.message : String(e));
    }
  }, [projectId, chapterId]);

  useEffect(() => {
    void loadDeltas();
  }, [loadDeltas]);

  const baseDelta = useMemo(
    () => deltas?.find((d) => d.delta_id === baseDeltaId) ?? null,
    [deltas, baseDeltaId],
  );

  function useAsBase() {
    if (!baseDelta) return;
    setDraft(deriveDelta(baseDelta, chapterId));
    setResult(null);
    setRunErr(null);
    // 每次取新基底都给一个新默认名：分支名同名即 409，作者手工改名前不该被自己的
    // 上一次重跑绊倒。
    setSimName(defaultSimName());
  }

  const patchElement = (key: SevenArrayKey, index: number, field: string, value: unknown) => {
    setDraft((d) => {
      if (!d) return d;
      const arr = asRows(d[key]).slice();
      arr[index] = { ...arr[index], [field]: value };
      return { ...d, [key]: arr };
    });
  };

  async function run() {
    if (!draft) return;
    setRunning(true);
    setRunErr(null);
    setResult(null);
    try {
      setResult(await simulationApi.run(projectId, [draft], simName.trim() || undefined));
    } catch (e: unknown) {
      // 409 的两种含义（branch_name_conflict / simulation_commit_failed）都由后端
      // 抛出；此处不猜，只把原文透出——作者需要知道是「名字撞了」还是「提交失败」。
      setRunErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  const chaptersLoading = chapters === null && !chaptersFailed;

  return (
    <div className="card stack" data-testid="simulation-panel">
      <div className="detail-pane__title">状态推演（假设这次会怎样）</div>

      {/* ── 诚实说明：面板的第一屏，不是折叠里的脚注 ──
          后端 README 明确列了这些副作用；作者在做决定之前必须先看见它们。 */}
      <ul className="muted small" data-testid="simulation-honest-notes">
        <li data-testid="simulation-honest-1">
          推演<strong>不是只读</strong>：每次跑会留一条归档分支 + 每条 delta 一条
          <code> state_deltas</code> + 一条 <code>commits</code>（供重放）。main 快照与领域表全程不动。
        </li>
        <li data-testid="simulation-honest-2">
          <strong>HIGH 风险变更绕过审批门</strong>：后端 <code>_skip_approval=True</code>，
          留痕标记 <code>simulation_bypassed</code>——推演不会弹人工确认。
        </li>
        <li data-testid="simulation-honest-3">
          <strong>失败也留残骸</strong>（已应用的部分 + 归档分支）；<strong>同名重跑会 409</strong>，
          默认名已带时间戳；409 含义 = 分支名撞了 或 提交失败，改名重试即可。
        </li>
        <li data-testid="simulation-honest-4">
          本功能<strong>不调用 LLM、不生成正文</strong>，只做状态推演（不烧额度）。
        </li>
        <li data-testid="simulation-honest-5">
          推演会往真实 <code>state_deltas</code> 表插行；派生件的{' '}
          <code>supersedes</code> 已置空，否则会把原 delta 标记成「已废弃」。
        </li>
      </ul>

      <ErrorBanner>{loadErr}</ErrorBanner>
      {chaptersLoading ? <Loading text="正在读取章节…" /> : null}

      {!chaptersFailed && chapters !== null && chapters.length === 0 ? (
        <p className="muted small" data-testid="simulation-no-chapters">
          本项目还没有章节，先写一章并提交后再来推演。
        </p>
      ) : null}

      {chapters !== null && chapters.length > 0 ? (
        <div className="row" data-testid="simulation-chapter-picker">
          <label className="small" htmlFor="sim-chapter">
            章节
          </label>
          <select
            id="sim-chapter"
            className="input"
            data-testid="simulation-chapter-select"
            value={chapterId}
            onChange={(e) => setChapterId(e.target.value)}
          >
            {chapters.map((c) => (
              <option key={c.chapter_id} value={c.chapter_id}>
                第{c.number}章 {c.title ?? ''}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {deltas === null && !deltasFailed ? (
        <Loading text="正在读取该章的状态变更…" />
      ) : null}

      {!deltasFailed && deltas !== null && deltas.length === 0 ? (
        <p className="muted small" data-testid="simulation-no-deltas">
          该章没有状态变更记录（通常意味着它还没提交过），没有可作基底的 delta。
        </p>
      ) : null}

      {deltas !== null && deltas.length > 0 ? (
        <fieldset className="stack" data-testid="simulation-delta-picker">
          <legend className="detail-pane__title">选一条 delta 作基底</legend>
          {deltas.map((d, i) => (
            <label key={d.delta_id ?? `d${i}`} className="row small">
              <input
                type="radio"
                name="sim-base-delta"
                data-testid={`simulation-base-${d.delta_id}`}
                checked={baseDeltaId === d.delta_id}
                onChange={() => setBaseDeltaId(d.delta_id ?? '')}
              />
              <code>{d.delta_id ?? '(无 id)'}</code>
              <span className="muted">
                {d.created_at ? new Date(d.created_at).toLocaleString() : '—'} ·{' '}
                {d.status ?? '—'} · v{d.previous_state_version ?? '?'}→
              </span>
            </label>
          ))}
          <button
            type="button"
            className="btn btn--small"
            data-testid="simulation-use-as-base"
            disabled={!baseDelta}
            onClick={useAsBase}
          >
            以此为基底
          </button>
          <p className="muted small">
            选一条<strong>已经写进历史</strong>的 delta 当基底——推演要的是
            「在既有状态上再假设一次」，从零手写一条 delta 需要填 10 个机器字段，
            作者写不出来也不该写。
          </p>
        </fieldset>
      ) : null}

      {draft ? (
        <div className="stack" data-testid="simulation-editor">
          <div className="detail-pane__title">
            改业务字段（机器字段已从基底继承：change_id / op / target_id / confidence /
            evidence / risk_level）
          </div>
          <p className="muted small">
            新 delta_id：<code data-testid="simulation-draft-id">{String(draft.delta_id)}</code>
          </p>

          <EditBlock
            title="角色状态变化（character_changes）"
            testId="edit-character_changes"
            rows={asRows(draft.character_changes)}
            render={(row, i) => (
              <>
                <label className="small">
                  字段路径
                  <input
                    className="input"
                    data-testid={`edit-character-${i}-field`}
                    value={toInputText(row.field)}
                    onChange={(e) =>
                      patchElement('character_changes', i, 'field', e.target.value)
                    }
                  />
                </label>
                <label className="small">
                  改成（after）
                  <input
                    className="input"
                    data-testid={`edit-character-${i}-after`}
                    value={toInputText(row.after)}
                    onChange={(e) =>
                      patchElement('character_changes', i, 'after', parseLooseValue(e.target.value))
                    }
                  />
                </label>
              </>
            )}
          />

          <EditBlock
            title="世界观变化（world_changes）"
            testId="edit-world_changes"
            rows={asRows(draft.world_changes)}
            render={(row, i) => (
              <>
                <label className="small">
                  字段路径
                  <input
                    className="input"
                    data-testid={`edit-world-${i}-field`}
                    value={toInputText(row.field)}
                    onChange={(e) => patchElement('world_changes', i, 'field', e.target.value)}
                  />
                </label>
                <label className="small">
                  改成（after）
                  <input
                    className="input"
                    data-testid={`edit-world-${i}-after`}
                    value={toInputText(row.after)}
                    onChange={(e) =>
                      patchElement('world_changes', i, 'after', parseLooseValue(e.target.value))
                    }
                  />
                </label>
              </>
            )}
          />

          <EditBlock
            title="伏笔回收（resolved_hooks）"
            testId="edit-resolved_hooks"
            rows={asRows(draft.resolved_hooks)}
            render={(row, i) => (
              <>
                <label className="small">
                  回收后状态
                  <select
                    className="input"
                    data-testid={`edit-resolved-${i}-to_status`}
                    value={toInputText(row.to_status) || 'RESOLVED'}
                    onChange={(e) =>
                      patchElement('resolved_hooks', i, 'to_status', e.target.value)
                    }
                  >
                    {HOOK_STATUS.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="small">
                  回收于哪一章
                  <input
                    className="input"
                    data-testid={`edit-resolved-${i}-payoff_chapter_id`}
                    value={toInputText(row.payoff_chapter_id)}
                    onChange={(e) =>
                      patchElement('resolved_hooks', i, 'payoff_chapter_id', e.target.value || null)
                    }
                  />
                </label>
                <label className="small">
                  回收摘要
                  <input
                    className="input"
                    data-testid={`edit-resolved-${i}-payoff_summary`}
                    value={toInputText(row.payoff_summary)}
                    onChange={(e) =>
                      patchElement('resolved_hooks', i, 'payoff_summary', e.target.value)
                    }
                  />
                </label>
              </>
            )}
          />

          <EditBlock
            title="叙事债务（debt_changes）"
            testId="edit-debt_changes"
            rows={asRows(draft.debt_changes)}
            render={(row, i) => (
              <>
                <label className="small">
                  之后状态
                  <select
                    className="input"
                    data-testid={`edit-debt-${i}-status_after`}
                    value={toInputText(row.status_after) || 'open'}
                    onChange={(e) =>
                      patchElement('debt_changes', i, 'status_after', e.target.value)
                    }
                  >
                    {DEBT_STATUS.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="small">
                  逾期于哪一章
                  <input
                    className="input"
                    data-testid={`edit-debt-${i}-deadline_chapter_id`}
                    value={toInputText(row.deadline_chapter_id)}
                    onChange={(e) =>
                      patchElement('debt_changes', i, 'deadline_chapter_id', e.target.value || null)
                    }
                  />
                </label>
                <label className="small">
                  之后严重度（0~1）
                  <input
                    className="input"
                    type="number"
                    step="0.05"
                    min="0"
                    max="1"
                    data-testid={`edit-debt-${i}-severity_after`}
                    value={toInputText(row.severity_after)}
                    onChange={(e) =>
                      patchElement(
                        'debt_changes',
                        i,
                        'severity_after',
                        e.target.value === '' ? null : Number(e.target.value),
                      )
                    }
                  />
                </label>
              </>
            )}
          />

          {/* 只读区：写明「为什么不能改」，否则作者会以为这是没做完。 */}
          <div data-testid="simulation-readonly-arrays">
            <p className="muted small">
              以下三组<strong>只读</strong>：改它们要么需要正文摘录（evidence），
              要么会「凭空造出新事件/新埋伏笔」这种语义事故。本批只放开上面四组。
            </p>
            {(['relationship_changes', 'new_events', 'new_hooks'] as const).map((k) => (
              <div key={k} data-testid={`readonly-${k}`}>
                <div className="detail-pane__title">{k}</div>
                <ReadableJson value={draft[k]} emptyText="（无）" />
              </div>
            ))}
          </div>

          <div className="row" data-testid="simulation-run-row">
            <label className="small" htmlFor="sim-name">
              推演名
            </label>
            <input
              id="sim-name"
              className="input"
              data-testid="simulation-name-input"
              value={simName}
              onChange={(e) => setSimName(e.target.value)}
            />
            <button
              type="button"
              className="btn"
              data-testid="simulation-run"
              disabled={running}
              onClick={() => void run()}
            >
              {running ? '推演中…' : '跑推演'}
            </button>
          </div>
          <ErrorBanner>{runErr}</ErrorBanner>
        </div>
      ) : null}

      {result ? (
        <div className="stack" data-testid="simulation-result">
          <div className="detail-pane__title">推演结果</div>
          <p className="small" data-testid="simulation-result-meta">
            <span data-testid="simulation-applied">已应用 {result.applied} 条 delta</span> ·
            分支 <code>{result.branch_id}</code> · 基线 v{result.base_version} · 状态{' '}
            {result.branch_status}
          </p>

          {result.issues.length > 0 ? (
            <div data-testid="simulation-issues">
              <div className="detail-pane__title">校验报错（{result.issues.length}）</div>
              <ul>
                {result.issues.map((it) => (
                  <li key={it.index} data-testid={`simulation-issue-${it.index}`}>
                    第 {it.index + 1} 条：
                    {(it.errors ?? []).map((e, i) => (
                      <span key={i} className="badge badge--chapter-failed">
                        {e}
                      </span>
                    ))}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div data-testid="simulation-diff">
            {countStateDiff(result.diff) === 0 ? (
              <p className="muted small" data-testid="simulation-diff-empty">
                无变化：这次假设推演到 main 状态上没有改动任何东西。
              </p>
            ) : (
              <>
                <ListDiffBlock
                  title="角色"
                  diff={result.diff.characters}
                  testId="diff-characters"
                />
                <ListDiffBlock title="伏笔" diff={result.diff.hooks} testId="diff-hooks" />
                <ListDiffBlock title="债务" diff={result.diff.debts} testId="diff-debts" />
                <ListDiffBlock
                  title="最近事件"
                  diff={result.diff.recent_events}
                  testId="diff-recent_events"
                />
                <div data-testid="diff-world">
                  <div className="detail-pane__title">
                    世界观<span className="muted small"> {Object.keys(result.diff.world ?? {}).length} 项变更</span>
                  </div>
                  <ul className="small">
                    {Object.entries(result.diff.world ?? {}).map(([k, pair]) => (
                      <li key={k}>
                        {k}：{JSON.stringify(pair?.before)} → {JSON.stringify(pair?.after)}
                      </li>
                    ))}
                  </ul>
                </div>
              </>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** 可编辑数组的一行容器。空数组渲染「（无可改项）」而不是空白——否则作者分不清
 * 「没有这类变更」与「这里坏了」。 */
function EditBlock({
  title,
  rows,
  render,
  testId,
}: {
  title: string;
  rows: Row[];
  render: (row: Row, index: number) => ReactNode;
  testId: string;
}) {
  return (
    <fieldset className="stack" data-testid={testId}>
      <legend className="detail-pane__title">{title}</legend>
      {rows.length === 0 ? (
        <p className="muted small">（无可改项）</p>
      ) : (
        rows.map((row, i) => (
          <div key={i} className="stack row" data-testid={`${testId}-row-${i}`}>
            {render(row, i)}
          </div>
        ))
      )}
    </fieldset>
  );
}
