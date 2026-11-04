// SkillsPanel —— 技能（内部消费外部 skill）配置面板（C-45③，2026-11-05）。
//
// 挂在 AI 设置页的 `.panel-grid` 里（该页路由 /projects/:pid/ai ⇒ 天然有 pid）。
// 上半：全部技能（跨作品资源，`GET /skills`，含 bound_project_count）
// 下半：本项目已绑定（`GET /projects/{pid}/skills`，**有序** ord ASC）
//
// **绑定是覆盖式有序**（后端 `SkillService.bind` 先清后写，列表顺序即注入顺序）⇒
// 任何顺序调整 / 增删都必须以**完整有序 skill_ids 数组**一次提交 bind；
// 「只加一个」的写法会把其余成员静默解绑（这是本面板最关键的约束）。
//
// 删除仍被项目绑定的 skill ⇒ 后端 409；面板提示先解绑，不做静默级联。
// 创建 / 编辑走 Modal（payload JSON 文本域 + JSON 校验），不新开顶级页面。

import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { skillsApi } from '../api/endpoints';
import type { Skill, SkillSummary } from '../api/types';
import { ErrorBanner, InfoBanner } from './ErrorBanner';
import { ConfirmDialog } from './ConfirmDialog';
import { EmptyState } from './EmptyState';
import { Loading } from './Loading';
import { Modal } from './Modal';
import { formatJson, tryParseJsonObject } from '../utils/format';
import { formatApiError } from '../utils/formatApiError';

interface Props {
  projectId: string;
}

export function SkillsPanel({ projectId }: Props) {
  const [all, setAll] = useState<SkillSummary[]>([]);
  const [bound, setBound] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // undefined = 关闭；null = 新建；Skill = 编辑
  const [editing, setEditing] = useState<Skill | null | undefined>(undefined);
  const [pendingDelete, setPendingDelete] = useState<SkillSummary | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [allRows, boundRows] = await Promise.all([
        skillsApi.list(),
        skillsApi.listBound(projectId),
      ]);
      setAll(allRows);
      setBound(boundRows);
    } catch (e: unknown) {
      setErr(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** **覆盖式**提交完整有序数组（唯一合法的绑定 / 排序写路径）。 */
  const submitOrder = useCallback(
    async (orderedIds: string[], successMessage: string) => {
      setBusy(true);
      setErr(null);
      setNotice(null);
      try {
        const rows = await skillsApi.bind(projectId, { skill_ids: orderedIds });
        setBound(rows);
        setNotice(successMessage);
        // bound_project_count 变了 ⇒ 全部列表同步刷新。
        setAll(await skillsApi.list());
      } catch (e: unknown) {
        setErr(formatApiError(e));
      } finally {
        setBusy(false);
      }
    },
    [projectId],
  );

  const bindSkill = (skillId: string) =>
    void submitOrder(
      [...bound.map((s) => s.skill_id), skillId],
      '已绑定（绑定为覆盖式，提交的是完整有序列表）',
    );

  const unbindSkill = async (skillId: string) => {
    setBusy(true);
    setErr(null);
    setNotice(null);
    try {
      const rows = await skillsApi.unbind(projectId, { skill_id: skillId });
      setBound(rows);
      setNotice('已解绑');
      setAll(await skillsApi.list());
    } catch (e: unknown) {
      setErr(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const move = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    if (target < 0 || target >= bound.length) return;
    const ids = bound.map((s) => s.skill_id);
    const [item] = ids.splice(index, 1);
    ids.splice(target, 0, item);
    // 顺序调整 = 一次完整数组的覆盖式 bind，不是「swap 单条」。
    void submitOrder(ids, '顺序已更新');
  };

  const openEdit = async (skillId: string) => {
    setErr(null);
    try {
      const full = await skillsApi.get(skillId);
      setEditing(full);
    } catch (e: unknown) {
      setErr(formatApiError(e));
    }
  };

  const doDelete = async (skillId: string) => {
    setPendingDelete(null);
    setBusy(true);
    setErr(null);
    setNotice(null);
    try {
      await skillsApi.remove(skillId);
      setNotice('已删除技能');
      setAll(await skillsApi.list());
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) {
        setErr(`该技能仍被项目绑定，不能删除：请先在本项目「已绑定」里解绑（${e.detail}）`);
      } else {
        setErr(formatApiError(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const boundIds = new Set(bound.map((s) => s.skill_id));

  return (
    <div className="card" data-testid="skills-panel">
      <div className="detail-pane__title">
        技能 Skills
        <span className="cdp-spacer" />
        <button
          type="button"
          className="btn btn--sm btn--primary"
          onClick={() => setEditing(null)}
          data-testid="skill-create-btn"
        >
          + 新建技能
        </button>
      </div>
      <div className="muted small" style={{ marginBottom: 8 }}>
        技能是跨作品资源；绑定到本项目后按下面的顺序注入模型上下文（顺序即注入顺序）。
      </div>

      <ErrorBanner>{err}</ErrorBanner>
      {notice ? (
        <div className="alert alert--info" role="status" data-testid="skills-notice">
          {notice}
        </div>
      ) : null}

      {loading ? (
        <Loading text="加载技能…" />
      ) : (
        <>
          <div className="panel__section">
            <div className="panel__section-title">全部技能（{all.length}）</div>
            {all.length === 0 ? (
              <EmptyState
                title="还没有技能"
                hint="用「新建技能」录入一个（payload 需符合 skill.schema.json）。"
              />
            ) : (
              <ul
                style={{ listStyle: 'none', padding: 0, margin: 0 }}
                data-testid="skills-all-list"
              >
                {all.map((s) => (
                  <li
                    key={s.skill_id}
                    className="card"
                    style={{ padding: 8, marginBottom: 6 }}
                    data-testid={`skill-all-${s.skill_id}`}
                  >
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <strong data-testid={`skill-name-${s.skill_id}`}>{s.name}</strong>
                      <span className="badge">v{s.version}</span>
                      <span className="muted small">
                        被 {s.bound_project_count ?? 0} 个项目绑定
                      </span>
                      <div style={{ flex: 1 }} />
                      {boundIds.has(s.skill_id) ? (
                        <span className="muted small">本项目已绑定</span>
                      ) : (
                        <button
                          type="button"
                          className="btn btn--sm"
                          disabled={busy}
                          onClick={() => bindSkill(s.skill_id)}
                          data-testid={`skill-bind-${s.skill_id}`}
                        >
                          绑定到本项目
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn btn--sm"
                        disabled={busy}
                        onClick={() => void openEdit(s.skill_id)}
                        data-testid={`skill-edit-${s.skill_id}`}
                      >
                        编辑
                      </button>
                      <button
                        type="button"
                        className="btn btn--sm btn--danger"
                        disabled={busy}
                        onClick={() => setPendingDelete(s)}
                        data-testid={`skill-delete-${s.skill_id}`}
                      >
                        删除
                      </button>
                    </div>
                    {s.description ? (
                      <div className="muted small">{s.description}</div>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="panel__section">
            <div className="panel__section-title">
              本项目已绑定（{bound.length}，按注入顺序）
            </div>
            {bound.length === 0 ? (
              <div className="muted small" data-testid="skills-bound-empty">
                尚未绑定任何技能。
              </div>
            ) : (
              <InfoBanner>
                <span className="muted small">
                  调整顺序会以<b>完整有序数组</b>覆盖式提交（后端 bind 语义）。
                </span>
              </InfoBanner>
            )}
            <ol
              style={{ paddingLeft: 18, margin: 0 }}
              data-testid="skills-bound-list"
            >
              {bound.map((s, idx) => (
                <li
                  key={s.skill_id}
                  style={{ marginBottom: 6 }}
                  data-testid={`skill-bound-${s.skill_id}`}
                >
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span data-testid={`skill-bound-ord-${s.skill_id}`}>
                      {idx + 1}. {s.name}
                    </span>
                    <span className="badge">v{s.version}</span>
                    <div style={{ flex: 1 }} />
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={busy || idx === 0}
                      onClick={() => move(idx, -1)}
                      data-testid={`skill-up-${s.skill_id}`}
                      title="上移（提交完整有序数组）"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={busy || idx === bound.length - 1}
                      onClick={() => move(idx, 1)}
                      data-testid={`skill-down-${s.skill_id}`}
                      title="下移（提交完整有序数组）"
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={busy}
                      onClick={() => void unbindSkill(s.skill_id)}
                      data-testid={`skill-unbind-${s.skill_id}`}
                    >
                      解绑
                    </button>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </>
      )}

      {editing !== undefined ? (
        <SkillFormModal
          initial={editing ?? undefined}
          onCancel={() => setEditing(undefined)}
          onSaved={async () => {
            setEditing(undefined);
            setNotice('已保存技能');
            await reload();
          }}
        />
      ) : null}

      {pendingDelete ? (
        <ConfirmDialog
          open={true}
          title="删除技能"
          body={`确认删除技能「${pendingDelete.name}」？若它仍被任何项目绑定，后端会拒绝（409），需先解绑。`}
          confirmText="删除"
          danger
          testId="skill-delete-confirm"
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => void doDelete(pendingDelete.skill_id)}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 创建 / 编辑弹窗
// ---------------------------------------------------------------------------

function SkillFormModal({
  initial,
  onCancel,
  onSaved,
}: {
  initial?: Skill;
  onCancel: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const isEdit = initial != null;
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const initialPayloadText = formatJson(initial?.payload ?? {});
  const [payloadText, setPayloadText] = useState(initialPayloadText);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setErr('技能名不能为空');
      return;
    }
    const parsed = tryParseJsonObject(payloadText);
    if (!parsed.ok) {
      setErr(`payload：${parsed.error}`);
      return;
    }
    setSubmitting(true);
    try {
      if (isEdit) {
        // 仅在 payload 文本真的变化时才带上——PUT 提供 payload 会让 version 自增
        // （装配缓存键跟随）。只改名字不该无故升版。
        await skillsApi.update(initial!.skill_id, {
          name: trimmedName,
          description: description.trim() === '' ? null : description.trim(),
          ...(payloadText !== initialPayloadText ? { payload: parsed.value } : {}),
        });
      } else {
        await skillsApi.create({
          name: trimmedName,
          description: description.trim() === '' ? null : description.trim(),
          payload: parsed.value,
        });
      }
      await onSaved();
    } catch (e: unknown) {
      setErr(formatApiError(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={isEdit ? `编辑技能 ${initial!.name}` : '新建技能'}
      onClose={onCancel}
      width={640}
      closable={!submitting}
      testId="skill-form-modal"
    >
      <form onSubmit={handleSubmit}>
        <ErrorBanner>{err}</ErrorBanner>
        <div className="form-row">
          <label>名称 *</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            data-testid="skill-form-name"
          />
        </div>
        <div className="form-row">
          <label>描述</label>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            data-testid="skill-form-description"
          />
        </div>
        <div className="form-row">
          <label>payload（JSON 对象）</label>
          <p className="muted small" style={{ margin: '0 0 6px' }}>
            需符合 docs/state-model/schemas/skill.schema.json；非对象会被拒绝。
          </p>
          <textarea
            rows={12}
            value={payloadText}
            onChange={(e) => setPayloadText(e.target.value)}
            data-testid="skill-form-payload"
          />
        </div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button
            type="button"
            className="btn"
            onClick={onCancel}
            disabled={submitting}
          >
            取消
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={submitting}
            data-testid="skill-form-save"
          >
            {submitting ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
