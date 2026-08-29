import { useEffect, useState } from 'react';
import { charactersApi } from '../../api/endpoints';
import type {
  Character,
  CharacterCreatePayload,
  CharacterRole,
  CharacterUpdatePayload,
} from '../../api/types';
import { formatDateTime, formatJson } from '../../utils/format';
import { ErrorBanner } from '../../components/ErrorBanner';
import { EmptyState } from '../../components/EmptyState';

const ROLE_OPTIONS: { value: CharacterRole; label: string }[] = [
  { value: 'protagonist', label: '主角 protagonist' },
  { value: 'antagonist', label: '反派 antagonist' },
  { value: 'supporting', label: '配角 supporting' },
  { value: 'mentor', label: '导师 mentor' },
  { value: 'love_interest', label: '恋爱对象 love_interest' },
  { value: 'narrator', label: '叙述者 narrator' },
  { value: 'other', label: '其他 other' },
];

interface CharacterTabProps {
  projectId: string;
}

export function CharacterTab({ projectId }: CharacterTabProps) {
  const [list, setList] = useState<Character[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Character | null>(null);
  const [creating, setCreating] = useState(false);

  const reload = async () => {
    setLoading(true);
    setErr(null);
    try {
      const rows = await charactersApi.listByProject(projectId);
      setList(rows);
      if (rows.length > 0 && !selectedId) setSelectedId(rows[0].character_id);
      if (selectedId && !rows.find((r) => r.character_id === selectedId)) {
        setSelectedId(rows[0]?.character_id ?? null);
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

  const selected = list.find((c) => c.character_id === selectedId) ?? null;

  const handleDelete = async (id: string) => {
    if (!window.confirm('确认删除该角色？相关 state 历史也会被级联删除。')) return;
    try {
      await charactersApi.delete(id);
      if (selectedId === id) setSelectedId(null);
      await reload();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '删除失败');
    }
  };

  return (
    <div>
      <div className="toolbar">
        <div className="muted small">
          角色定义侧：name / role / core_json（性格/价值观/恐惧/欲望/缺陷）
        </div>
        <div className="toolbar__spacer" />
        <button className="btn btn--primary" onClick={() => setCreating(true)}>
          + 新建角色
        </button>
      </div>

      <ErrorBanner>{err}</ErrorBanner>

      <div className="layout-2col">
        <div>
          {loading ? (
            <div className="muted">加载中…</div>
          ) : list.length === 0 ? (
            <EmptyState
              title="还没有角色"
              hint="点击右上角“新建角色”，开始建立人物档案。"
              action={
                <button
                  className="btn btn--primary"
                  onClick={() => setCreating(true)}
                >
                  + 新建角色
                </button>
              }
            />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>角色</th>
                  <th>可见性</th>
                  <th>最新 state v</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {list.map((c) => (
                  <tr
                    key={c.character_id}
                    onClick={() => setSelectedId(c.character_id)}
                    style={{
                      cursor: 'pointer',
                      background:
                        c.character_id === selectedId ? '#f3f7ff' : undefined,
                    }}
                  >
                    <td>{c.name}</td>
                    <td>{c.role}</td>
                    <td>{c.visibility}</td>
                    <td>v{c.latest_state_version}</td>
                    <td className="right">
                      <button
                        className="btn btn--sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditing(c);
                        }}
                      >
                        编辑
                      </button>
                      <button
                        className="btn btn--sm btn--danger"
                        style={{ marginLeft: 6 }}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDelete(c.character_id);
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

        <aside className="detail-pane">
          {selected ? (
            <CharacterDetail character={selected} />
          ) : (
            <div className="muted">从左侧选择一个角色查看详情。</div>
          )}
        </aside>
      </div>

      {creating ? (
        <CharacterFormModal
          title="新建角色"
          onCancel={() => setCreating(false)}
          onSubmit={async (p) => {
            await charactersApi.create(projectId, p as CharacterCreatePayload);
            setCreating(false);
            await reload();
          }}
        />
      ) : null}

      {editing ? (
        <CharacterFormModal
          title={`编辑角色：${editing.name}`}
          initial={editing}
          // 编辑时快照原始 core_json：保存时与表单字段合并，
          // 保留后端 / project_init 写入的非表单键（motivation/goal/conflict/relationship 等），
          // 避免提交体全量覆盖。
          baseCoreJson={editing.core_json ?? {}}
          onCancel={() => setEditing(null)}
          onSubmit={async (p) => {
            await charactersApi.update(
              editing.character_id,
              p as CharacterUpdatePayload,
            );
            setEditing(null);
            await reload();
          }}
        />
      ) : null}
    </div>
  );
}

function CharacterDetail({ character }: { character: Character }) {
  return (
    <div>
      <div className="detail-pane__title">{character.name}</div>
      <div className="muted small">
        ID：{character.character_id} · 创建：{formatDateTime(character.created_at)}
      </div>
      <div className="spacer" />
      <div className="form-grid">
        <Field label="角色类型" value={character.role} />
        <Field label="可见性" value={character.visibility} />
      </div>

      <div className="detail-pane__section">
        <div className="detail-pane__section-title">core_json（性格/价值观/恐惧/欲望/缺陷…）</div>
        <pre className="json-block" data-testid="character-core-json">
          {formatJson(character.core_json)}
        </pre>
      </div>

      <div className="detail-pane__section">
        <div className="detail-pane__section-title">
          最新 state（v{character.latest_state_version}）
        </div>
        <pre className="json-block">{formatJson(character.latest_state_json)}</pre>
      </div>

      {character.who_knows && character.who_knows.length > 0 ? (
        <div className="detail-pane__section">
          <div className="detail-pane__section-title">who_knows</div>
          <div>{character.who_knows.join('、')}</div>
        </div>
      ) : null}
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

// ------------------------------------------------------------------- form modal
interface CharacterFormModalProps {
  title: string;
  initial?: Character;
  /**
   * 编辑模式下作为基础 core_json；保存时与表单字段合并，
   * 保留后端/project_init 写入的非表单键，避免被全量覆盖而丢失。
   * 新建场景无需传入。
   */
  baseCoreJson?: Record<string, unknown>;
  onCancel: () => void;
  onSubmit: (
    payload: CharacterCreatePayload | CharacterUpdatePayload,
  ) => Promise<void>;
}

const CORE_FIELDS: Array<{
  key: 'personality' | 'values' | 'fears' | 'desires' | 'flaws';
  label: string;
  hint: string;
}> = [
  { key: 'personality', label: '性格 personality', hint: '一句话或关键词列表' },
  { key: 'values', label: '价值观 values', hint: 'TA 坚守/践行的原则' },
  { key: 'fears', label: '恐惧 fears', hint: 'TA 害怕/回避的东西' },
  { key: 'desires', label: '欲望 desires', hint: 'TA 想要追求的东西' },
  { key: 'flaws', label: '缺陷 flaws', hint: 'TA 的弱点/盲点' },
];

function CharacterFormModal({
  title,
  initial,
  baseCoreJson,
  onCancel,
  onSubmit,
}: CharacterFormModalProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [role, setRole] = useState<CharacterRole>(initial?.role ?? 'supporting');
  const [core, setCore] = useState<Record<string, string>>(() => {
    const base: Record<string, string> = {};
    for (const f of CORE_FIELDS) base[f.key] = '';
    if (initial) {
      const cj = initial.core_json ?? {};
      for (const f of CORE_FIELDS) {
        const v = cj[f.key];
        if (typeof v === 'string') base[f.key] = v;
        else if (v !== undefined && v !== null) base[f.key] = formatJson(v);
      }
    }
    return base;
  });
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!name.trim()) {
      setErr('角色名不能为空');
      return;
    }
    const formCore: Record<string, unknown> = {};
    for (const f of CORE_FIELDS) {
      const v = core[f.key].trim();
      if (v === '') continue;
      formCore[f.key] = v;
    }
    // 编辑场景：以 baseCoreJson（=打开编辑器时的全量 core_json）为基底，
    // 用本次表单字段覆盖对应键；非表单键（motivation/goal/conflict/relationship 等）保留原值。
    // 新建场景无 baseCoreJson，提交体仅为表单字段（保持原行为，避免凭空填入非表单键）。
    const core_json: Record<string, unknown> = baseCoreJson
      ? { ...baseCoreJson, ...formCore }
      : formCore;
    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim(),
        role,
        core_json: Object.keys(core_json).length > 0 ? core_json : undefined,
      } as CharacterCreatePayload | CharacterUpdatePayload);
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
      style={modalBackdrop}
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

        <div className="form-grid">
          <div className="form-row">
            <label>名称 *</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              data-testid="character-name"
            />
          </div>
          <div className="form-row">
            <label>角色</label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as CharacterRole)}
            >
              {ROLE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div
          style={{
            marginTop: 8,
            paddingTop: 8,
            borderTop: '1px solid var(--color-border)',
          }}
        >
          <div className="muted small" style={{ marginBottom: 6 }}>
            core_json 关键字段（留空表示不写；保存后会合并成一个 JSON 对象）
          </div>
          {CORE_FIELDS.map((f) => (
            <div className="form-row" key={f.key}>
              <label>
                {f.label} <span className="muted small">— {f.hint}</span>
              </label>
              <textarea
                rows={2}
                value={core[f.key]}
                onChange={(e) =>
                  setCore((c) => ({ ...c, [f.key]: e.target.value }))
                }
              />
            </div>
          ))}
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

const modalBackdrop: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(15,20,35,0.4)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 100,
};
