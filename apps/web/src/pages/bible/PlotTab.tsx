import { useEffect, useState } from 'react';
import {
  chaptersApi,
  charactersApi,
  eventsApi,
  locationsApi,
  timelineApi,
} from '../../api/endpoints';
import type {
  Chapter,
  Character,
  EventType,
  PlotEvent,
  PlotEventCreatePayload,
  TimelineEvent,
  WorldEntity,
} from '../../api/types';
import { tryParseJsonObject } from '../../utils/format';
import { ErrorBanner } from '../../components/ErrorBanner';
import { EmptyState } from '../../components/EmptyState';

const TYPE_OPTIONS: { value: EventType; label: string }[] = [
  { value: 'revelation', label: '揭露 revelation' },
  { value: 'conflict', label: '冲突 conflict' },
  { value: 'decision', label: '决定 decision' },
  { value: 'encounter', label: '遭遇 encounter' },
  { value: 'transition', label: '转折 transition' },
  { value: 'other', label: '其他 other' },
];

const TYPE_LABEL: Record<EventType, string> = {
  revelation: '揭露',
  conflict: '冲突',
  decision: '决定',
  encounter: '遭遇',
  transition: '转折',
  other: '其他',
};

// 后端 plot_events.status DDL CHECK 仅允许 planned/recorded/resolved/abandoned
// （packages/domain/plot/models.py L22-27）。前端下拉必须与服务端枚举一致，
// 否则一旦选中非法值（过去是 ongoing）将直接 422。
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'planned', label: '计划中 planned' },
  { value: 'recorded', label: '已记录 recorded' },
  { value: 'resolved', label: '已解决 resolved' },
  { value: 'abandoned', label: '已废弃 abandoned' },
];

const STATUS_LABEL: Record<string, string> = {
  planned: '计划中',
  recorded: '已记录',
  resolved: '已解决',
  abandoned: '已废弃',
};

function describeTime(time: Record<string, unknown> | null): {
  primary: string;
  secondary: string | null;
} {
  if (!time || typeof time !== 'object') return { primary: '—', secondary: null };
  const td = (time as { timeline_day?: unknown }).timeline_day;
  const isd = (time as { in_story_date?: unknown }).in_story_date;
  const primary =
    typeof td === 'number' || typeof td === 'string'
      ? `Day ${td}`
      : '—';
  const secondary =
    typeof isd === 'string' && isd.trim() !== '' ? isd : null;
  return { primary, secondary };
}

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

interface PlotTabProps {
  projectId: string;
}

export function PlotTab({ projectId }: PlotTabProps) {
  const [events, setEvents] = useState<PlotEvent[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  // 名字映射：失败降级为显示原始 id 短串，不阻塞主列表。
  const [charNameById, setCharNameById] = useState<Record<string, string>>({});
  const [locNameById, setLocNameById] = useState<Record<string, string>>({});
  const [chapterNoById, setChapterNoById] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const reload = async () => {
    setLoading(true);
    setErr(null);
    try {
      const [es, tl, charsRes, locsRes, chsRes] = await Promise.allSettled([
        eventsApi.list(projectId),
        timelineApi.list(projectId),
        charactersApi.listByProject(projectId),
        locationsApi.list(projectId),
        chaptersApi.listByProject(projectId),
      ]);

      if (es.status === 'fulfilled') {
        setEvents(es.value);
      } else {
        setEvents([]);
        throw es.reason;
      }
      setTimeline(tl.status === 'fulfilled' ? tl.value : []);

      if (charsRes.status === 'fulfilled') {
        const m: Record<string, string> = {};
        for (const c of charsRes.value as Character[]) m[c.character_id] = c.name;
        setCharNameById(m);
      } else {
        setCharNameById({});
      }
      if (locsRes.status === 'fulfilled') {
        const m: Record<string, string> = {};
        for (const w of locsRes.value as WorldEntity[]) m[w.id] = w.name;
        setLocNameById(m);
      } else {
        setLocNameById({});
      }
      if (chsRes.status === 'fulfilled') {
        const m: Record<string, number> = {};
        for (const ch of chsRes.value as Chapter[]) m[ch.chapter_id] = ch.number;
        setChapterNoById(m);
      } else {
        setChapterNoById({});
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const handleDelete = async (id: string) => {
    if (!window.confirm('确认删除该事件？')) return;
    try {
      await eventsApi.delete(id);
      await reload();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '删除失败');
    }
  };

  return (
    <div>
      <div className="toolbar">
        <div className="muted small">
          剧情事件按故事内天数排列；章节提交（commit）落地的事件自动进入清单与时间线。
        </div>
        <div className="toolbar__spacer" />
        <button className="btn btn--primary" onClick={() => setCreating(true)}>
          + 新建事件
        </button>
      </div>

      <ErrorBanner>{err}</ErrorBanner>

      {loading ? (
        <div className="muted">加载中…</div>
      ) : null}

      <div className="layout-2col">
        <div>
          <div className="detail-pane__title">事件 Events</div>
          {!loading && events.length === 0 ? (
            <EmptyState
              title="还没有事件"
              hint="剧情事件用于标记重要节点。"
            />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>类型</th>
                  <th>描述</th>
                  <th>参与</th>
                  <th>状态</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => {
                  const t = describeTime(e.time);
                  const desc = (e.description ?? '').trim();
                  const isPlanned = e.status === 'planned';
                  return (
                    <tr
                      key={e.id}
                      data-testid="plot-event-row"
                      className={isPlanned ? 'muted' : undefined}
                    >
                      <td className="muted small">
                        <div>{t.primary}</div>
                        {t.secondary ? (
                          <div className="small">{t.secondary}</div>
                        ) : null}
                      </td>
                      <td>
                        <span
                          className="badge"
                          data-testid="plot-event-type"
                          data-type={e.type}
                        >
                          {TYPE_LABEL[e.type] ?? e.type}
                        </span>
                      </td>
                      <td data-testid="plot-event-desc">
                        {desc !== '' ? (
                          desc
                        ) : (
                          <span className="muted">（无描述）</span>
                        )}
                        {(e.location_id && locNameById[e.location_id]) ||
                        (e.introduced_chapter_id &&
                          chapterNoById[e.introduced_chapter_id] !== undefined) ? (
                          <div className="muted small" style={{ marginTop: 2 }}>
                            {e.location_id && locNameById[e.location_id]
                              ? `📍 ${locNameById[e.location_id]}`
                              : null}
                            {e.location_id &&
                            locNameById[e.location_id] &&
                            e.introduced_chapter_id &&
                            chapterNoById[e.introduced_chapter_id] !== undefined
                              ? ' · '
                              : null}
                            {e.introduced_chapter_id &&
                            chapterNoById[e.introduced_chapter_id] !== undefined
                              ? `来源「第 ${
                                  chapterNoById[e.introduced_chapter_id]
                                } 章」`
                              : null}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        {Array.isArray(e.participants) &&
                        e.participants.length > 0 ? (
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                            {e.participants.map((p, i) => {
                              const id =
                                typeof p === 'string' ? p : String(p ?? '');
                              const name = charNameById[id];
                              return (
                                <span
                                  key={`${id}-${i}`}
                                  className="badge"
                                  data-character-id={id}
                                >
                                  {name ?? shortId(id)}
                                </span>
                              );
                            })}
                          </div>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td>
                        <span
                          className="badge"
                          data-testid="plot-event-status"
                          data-status={e.status}
                        >
                          {STATUS_LABEL[e.status] ?? e.status}
                        </span>
                      </td>
                      <td className="right">
                        <button
                          className="btn btn--sm btn--danger"
                          onClick={() => void handleDelete(e.id)}
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <div>
          <div className="detail-pane__title">时间线 Timeline</div>
          {timeline.length === 0 ? (
            <div className="muted small">
              （暂无时间线条目；新建事件后服务端会自动同步一条）
            </div>
          ) : (
            <ol style={{ paddingLeft: 18, margin: 0 }}>
              {[...timeline]
                .sort((a, b) => a.day_index - b.day_index)
                .map((t) => {
                  const raw = t.description ?? '';
                  const truncated = raw.length > 80;
                  const shown = truncated ? truncate(raw, 80) : raw;
                  return (
                    <li key={t.id} style={{ marginBottom: 8 }}>
                      <div>
                        <strong>Day {t.day_index}</strong>
                        {t.time_ref ? (
                          <span className="muted small"> · {t.time_ref}</span>
                        ) : null}
                      </div>
                      <div
                        title={raw !== '' ? raw : undefined}
                      >
                        {shown !== '' ? shown : <span className="muted">（无描述）</span>}
                      </div>
                    </li>
                  );
                })}
            </ol>
          )}
        </div>
      </div>

      {creating ? (
        <EventFormModal
          onCancel={() => setCreating(false)}
          onSubmit={async (p) => {
            await eventsApi.create(projectId, p as PlotEventCreatePayload);
            setCreating(false);
            await reload();
          }}
        />
      ) : null}
    </div>
  );
}

interface EventFormModalProps {
  onCancel: () => void;
  onSubmit: (payload: PlotEventCreatePayload) => Promise<void>;
}

function EventFormModal({ onCancel, onSubmit }: EventFormModalProps) {
  const [type, setType] = useState<EventType>('revelation');
  const [description, setDescription] = useState('');
  const [participantsJson, setParticipantsJson] = useState('[]');
  // 默认含 timeline_day=1；后端 _validate_time 强制要求该字段，避免空 {} → 422。
  const [timeJson, setTimeJson] = useState('{"timeline_day": 1}');
  const [status, setStatus] = useState<string>('planned');
  const [jsonErr, setJsonErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setJsonErr(null);
    let participants: unknown[] = [];
    let time: Record<string, unknown> = {};
    try {
      const pp = JSON.parse(participantsJson.trim() || '[]');
      if (!Array.isArray(pp)) throw new Error('participants 必须是数组');
      // 兼容老格式（{character_id, role} 对象），统一抽取 id 字符串数组；
      // 后端契约：参与者是纯 id 字符串数组（详见 PlotTab 注释）。
      participants = pp.map((x: unknown) =>
        typeof x === 'string' ? x : (x as { character_id?: string })?.character_id ?? x,
      );
    } catch (e: unknown) {
      setJsonErr(`participants JSON 错误：${e instanceof Error ? e.message : 'parse error'}`);
      return;
    }
    const tp = tryParseJsonObject(timeJson);
    if (!tp.ok) {
      setJsonErr(`time JSON 错误：${tp.error}`);
      return;
    }
    time = tp.value;
    setSubmitting(true);
    try {
      // 后端 contract：cause 是 event_id 字符串数组（或 null）；
      // 描述/起因文本统一落进 description 字段，cause 不从 description 生成。
      // 当用户未在前置事件里挑 cause 时，传 null 让后端走默认（不影响必填校验）。
      await onSubmit({
        type,
        cause: null,
        participants,
        time,
        status,
        description: description.trim() !== '' ? description.trim() : null,
      });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(15,20,35,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
      }}
      onClick={onCancel}
    >
      <form
        className="card"
        style={{ width: 560, maxWidth: '92vw', maxHeight: '90vh', overflowY: 'auto' }}
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <div className="section-title">新建事件</div>
        <ErrorBanner>{err}</ErrorBanner>
        <ErrorBanner>{jsonErr}</ErrorBanner>

        <div className="form-grid">
          <div className="form-row">
            <label>类型</label>
            <select
              value={type}
              onChange={(e) => setType(e.target.value as EventType)}
            >
              {TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div className="form-row">
            <label>状态</label>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              {STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="form-row">
          <label>描述 description</label>
          <textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="一句话或一段话描述这个事件"
          />
        </div>

        <div className="form-row">
          <label>
            participants <span className="muted small">角色 ID 数组（纯字符串数组）</span>
          </label>
          <textarea
            rows={3}
            value={participantsJson}
            onChange={(e) => setParticipantsJson(e.target.value)}
            style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}
            placeholder='["char_xxx", "char_yyy"]'
          />
        </div>

        <div className="form-row">
          <label>
            time_json <span className="muted small">JSON 对象，必须含 timeline_day（如 {'{ "timeline_day": 1 }'}）</span>
          </label>
          <textarea
            rows={3}
            value={timeJson}
            onChange={(e) => {
              setTimeJson(e.target.value);
              setJsonErr(null);
            }}
            style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}
            placeholder='{ "timeline_day": 1 }'
          />
        </div>

        <div
          style={{
            display: 'flex',
            gap: 8,
            marginTop: 12,
            justifyContent: 'flex-end',
          }}
        >
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button type="submit" className="btn btn--primary" disabled={submitting}>
            {submitting ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </div>
  );
}
