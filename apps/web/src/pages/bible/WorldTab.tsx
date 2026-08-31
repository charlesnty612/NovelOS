import { useEffect, useState } from 'react';
import {
  factionsApi,
  locationsApi,
  worldRulesApi,
} from '../../api/endpoints';
import type { WorldEntity, WorldEntityPayload } from '../../api/types';
import { formatDateTime, formatJson, tryParseJsonObject } from '../../utils/format';
import { ErrorBanner } from '../../components/ErrorBanner';
import { EmptyState } from '../../components/EmptyState';
import { ReadableJson, RawJsonDetails } from '../../components/ReadableJson';
import { ConfirmDialog } from '../../components/ConfirmDialog';

type Kind = 'locations' | 'factions' | 'world-rules';

interface WorldTabProps {
  projectId: string;
}

const KIND_META: Record<Kind, { title: string; singular: string; client: {
  list: (pid: string) => Promise<WorldEntity[]>;
  create: (pid: string, p: WorldEntityPayload) => Promise<WorldEntity>;
  update: (id: string, p: WorldEntityPayload) => Promise<WorldEntity>;
  delete: (id: string) => Promise<void>;
} }> = {
  locations: {
    title: '地点 Locations',
    singular: '地点',
    client: locationsApi,
  },
  factions: {
    title: '势力 Factions',
    singular: '势力',
    client: factionsApi,
  },
  'world-rules': {
    title: '世界规则 World Rules',
    singular: '规则',
    client: worldRulesApi,
  },
};

export function WorldTab({ projectId }: WorldTabProps) {
  const [kind, setKind] = useState<Kind>('locations');
  const meta = KIND_META[kind];

  const [list, setList] = useState<WorldEntity[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<WorldEntity | null>(null);
  const [creating, setCreating] = useState(false);
  // V3.22「交互反馈统一」：删除世界观实体前 ConfirmDialog 二次确认。
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  const reload = async (k: Kind = kind) => {
    setLoading(true);
    setErr(null);
    try {
      setList(await KIND_META[k].client.list(projectId));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, kind]);

  const handleDelete = (id: string) => {
    setPendingDeleteId(id);
  };

  const doDelete = async (id: string) => {
    setPendingDeleteId(null);
    try {
      await meta.client.delete(id);
      await reload();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '删除失败');
    }
  };

  return (
    <div>
      <div className="toolbar">
        <div className="tabs" style={{ margin: 0, border: 'none' }}>
          {(Object.keys(KIND_META) as Kind[]).map((k) => (
            <div
              key={k}
              className={`tabs__tab ${k === kind ? 'tabs__tab--active' : ''}`}
              onClick={() => setKind(k)}
            >
              {KIND_META[k].title}
            </div>
          ))}
        </div>
        <div className="toolbar__spacer" />
        <button className="btn btn--primary" onClick={() => setCreating(true)}>
          + 新建{meta.singular}
        </button>
      </div>

      <ErrorBanner>{err}</ErrorBanner>

      {loading ? (
        <div className="muted">加载中…</div>
      ) : list.length === 0 ? (
        <EmptyState
          title={`还没有${meta.singular}`}
          hint={`点击右上角“新建${meta.singular}”，记录世界设定。`}
          action={
            <button
              className="btn btn--primary"
              onClick={() => setCreating(true)}
            >
              + 新建{meta.singular}
            </button>
          }
        />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>名称</th>
              <th>陈述</th>
              <th>可见性</th>
              <th>更新时间</th>
              <th className="right">操作</th>
            </tr>
          </thead>
          <tbody>
            {list.map((e) => (
              <tr key={e.id}>
                <td>{e.name}</td>
                <td className="muted" style={{ maxWidth: 360 }}>
                  {e.statement}
                </td>
                <td>{e.visibility}</td>
                <td className="muted small">{formatDateTime(e.updated_at)}</td>
                <td className="right">
                  <button
                    className="btn btn--sm"
                    onClick={() => setEditing(e)}
                  >
                    编辑
                  </button>
                  <button
                    className="btn btn--sm btn--danger"
                    style={{ marginLeft: 6 }}
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

      {list.length > 0 ? (
        <div style={{ marginTop: 16 }}>
          <div className="muted small" style={{ marginBottom: 6 }}>
            data_json 预览（第一条）
          </div>
          <div data-testid="world-data-preview">
            <ReadableJson value={list[0].data ?? {}} />
          </div>
          <RawJsonDetails
            value={list[0].data ?? {}}
            testId="world-data-preview-raw"
          />
        </div>
      ) : null}

      {creating ? (
        <EntityFormModal
          title={`新建${meta.singular}`}
          onCancel={() => setCreating(false)}
          onSubmit={async (p) => {
            await meta.client.create(projectId, p);
            setCreating(false);
            await reload();
          }}
        />
      ) : null}
      {editing ? (
        <EntityFormModal
          title={`编辑${meta.singular}：${editing.name}`}
          initial={editing}
          onCancel={() => setEditing(null)}
          onSubmit={async (p) => {
            await meta.client.update(editing.id, p);
            setEditing(null);
            await reload();
          }}
        />
      ) : null}

      {pendingDeleteId ? (
        <ConfirmDialog
          open={true}
          title={`删除${meta.singular}`}
          body={`确认删除该${meta.singular}？`}
          confirmText="删除"
          danger
          testId="world-entity-delete-confirm"
          onCancel={() => setPendingDeleteId(null)}
          onConfirm={() => void doDelete(pendingDeleteId)}
        />
      ) : null}
    </div>
  );
}

interface EntityFormModalProps {
  title: string;
  initial?: WorldEntity;
  onCancel: () => void;
  onSubmit: (payload: WorldEntityPayload) => Promise<void>;
}

function EntityFormModal({ title, initial, onCancel, onSubmit }: EntityFormModalProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [statement, setStatement] = useState(initial?.statement ?? '');
  const [dataText, setDataText] = useState(formatJson(initial?.data ?? {}));
  const [jsonErr, setJsonErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!name.trim()) {
      setErr('名称不能为空');
      return;
    }
    const parsed = tryParseJsonObject(dataText);
    if (!parsed.ok) {
      setJsonErr(parsed.error);
      return;
    }
    setJsonErr(null);
    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim(),
        statement: statement.trim(),
        data: parsed.value,
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
        <div className="section-title">{title}</div>
        <ErrorBanner>{err}</ErrorBanner>
        <ErrorBanner>{jsonErr}</ErrorBanner>

        <div className="form-row">
          <label>名称 *</label>
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="form-row">
          <label>陈述 statement</label>
          <textarea
            rows={3}
            value={statement}
            onChange={(e) => setStatement(e.target.value)}
            placeholder="一句话描述这个设定"
          />
        </div>
        <div className="form-row">
          <label>
            data_json <span className="muted small">（合法 JSON 对象；保存前会校验）</span>
          </label>
          <textarea
            rows={8}
            value={dataText}
            onChange={(e) => {
              setDataText(e.target.value);
              setJsonErr(null);
            }}
            style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}
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
