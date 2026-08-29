import { useEffect, useState } from 'react';
import { eventsApi, timelineApi } from '../../api/endpoints';
import type {
  EventType,
  PlotEvent,
  PlotEventCreatePayload,
  TimelineEvent,
} from '../../api/types';
import { formatDateTime, tryParseJsonObject } from '../../utils/format';
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

interface PlotTabProps {
  projectId: string;
}

export function PlotTab({ projectId }: PlotTabProps) {
  const [events, setEvents] = useState<PlotEvent[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const reload = async () => {
    setLoading(true);
    setErr(null);
    try {
      const [es, tl] = await Promise.all([
        eventsApi.list(projectId),
        timelineApi.list(projectId),
      ]);
      setEvents(es);
      setTimeline(tl);
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
          事件清单 + 时间线（按 day_index 升序）。
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
                  <th>类型</th>
                  <th>状态</th>
                  <th>时间</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id}>
                    <td>{e.type}</td>
                    <td>{e.status}</td>
                    <td className="muted small">
                      {formatDateTime(
                        (e.time as { at?: string } | null)?.at ?? null,
                      )}
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
                ))}
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
                .map((t) => (
                  <li key={t.id} style={{ marginBottom: 8 }}>
                    <div>
                      <strong>Day {t.day_index}</strong>
                      {t.time_ref ? (
                        <span className="muted small"> · {t.time_ref}</span>
                      ) : null}
                    </div>
                    <div>{t.description ?? '（无描述）'}</div>
                  </li>
                ))}
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
  const [timeJson, setTimeJson] = useState('{}');
  const [status, setStatus] = useState('planned');
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
      participants = pp;
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
      await onSubmit({
        type,
        cause: description.trim() ? { summary: description.trim() } : {},
        participants,
        time,
        status,
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
            <input
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              placeholder="如 planned / ongoing / resolved"
            />
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
            participants <span className="muted small">JSON 数组；本期兜底用文本框</span>
          </label>
          <textarea
            rows={3}
            value={participantsJson}
            onChange={(e) => setParticipantsJson(e.target.value)}
            style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}
            placeholder='[{"character_id":"cha_xxx","role":"actor"}]'
          />
        </div>

        <div className="form-row">
          <label>
            time_json <span className="muted small">JSON 对象，如 {'{ "day_index": 1, "at": "..." }'}</span>
          </label>
          <textarea
            rows={3}
            value={timeJson}
            onChange={(e) => {
              setTimeJson(e.target.value);
              setJsonErr(null);
            }}
            style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}
            placeholder='{ "day_index": 1 }'
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
