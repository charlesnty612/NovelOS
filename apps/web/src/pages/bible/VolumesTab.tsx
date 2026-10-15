// 卷面板（评估 G6：卷与章节的隶属关系在 UI 上完全不存在）。
//
// 后端 `volumes.py` 六条端点全在（含 seal / assign），dev 库实测有卷数据，
// 但作者看不到任何一章属于哪一卷——这是「后端有能力、作者够不着」的最典型形态。
//
// 本面板的三条硬约束（都来自后端语义，不是 UI 偏好）：
//
// 1. **一个项目同时只能有一个 active 卷**（`VolumeService.create` 在已有 active 时
//    抛 409）。所以「新建卷」在已有 active 卷时必须给出说明而不是让作者撞 409。
// 2. **seal 不可逆**：封存把当时的故事状态快照冻结进 `terminal_snapshot_json`，
//    此后该卷拒挂章、`sealed → active` 反向跳变被拒（409）。后端**没有**解封端点，
//    所以 UI 也不给「解封」按钮——给一个永远 409 的按钮比不给更坏。
// 3. **挂章后要能看见**：章节的 `volume_id` 不在本面板的列表响应里（列表只给
//    `chapter_count` 聚合），所以每次 load 都另拉一次章节列表，按 chapter_id 反查
//    出该卷实际包含的章号——否则作者挂完章仍不知道挂到哪儿了。

import { useCallback, useEffect, useMemo, useState } from 'react';
import { chaptersApi, volumesApi } from '../../api/endpoints';
import type {
  Chapter,
  VolumeCreatePayload,
  VolumeListItem,
  VolumeStatus,
  VolumeUpdatePayload,
} from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { ErrorBanner, InfoBanner } from '../../components/ErrorBanner';
import { Loading } from '../../components/Loading';
import { Modal } from '../../components/Modal';
import { ConfirmDialog } from '../../components/ConfirmDialog';

interface VolumesTabProps {
  projectId: string;
}

const STATUS_LABEL: Record<VolumeStatus, string> = {
  active: '进行中',
  sealed: '已封卷',
};

const STATUS_CLASS: Record<VolumeStatus, string> = {
  active: 'badge badge--chapter-drafted',
  sealed: 'badge badge--chapter-committed',
};

/** 从 chapter 上取卷归属。取不到就当未归属——宁可显示「未挂卷」也不谎报有归属。 */
function chapterVolumeId(chapter: Chapter): string | null {
  return chapter.volume_id ?? null;
}

export function VolumesTab({ projectId }: VolumesTabProps) {
  const [volumes, setVolumes] = useState<VolumeListItem[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // 新建卷表单
  const [createOpen, setCreateOpen] = useState(false);
  const [numberInput, setNumberInput] = useState('1');
  const [titleInput, setTitleInput] = useState('');
  const [summaryInput, setSummaryInput] = useState('');
  const [busy, setBusy] = useState(false);

  // 封卷确认（不可逆 ⇒ 二次确认，且初始焦点给「取消」）
  const [sealTarget, setSealTarget] = useState<VolumeListItem | null>(null);
  // 挂章
  const [assignTarget, setAssignTarget] = useState<VolumeListItem | null>(null);
  const [assignChapterId, setAssignChapterId] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [vs, cs] = await Promise.all([
        volumesApi.list(projectId),
        chaptersApi.listByProject(projectId),
      ]);
      setVolumes(vs);
      setChapters(cs);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  /** chapter_id → 章号，用于「这一卷包含哪几章」。 */
  const chapterNoById = useMemo(() => {
    const m = new Map<string, number>();
    chapters.forEach((c) => m.set(c.chapter_id, c.number));
    return m;
  }, [chapters]);

  const membersByVolume = useMemo(() => {
    const m = new Map<string, string[]>();
    chapters.forEach((c) => {
      const vid = chapterVolumeId(c);
      if (!vid) return;
      const arr = m.get(vid) ?? [];
      arr.push(c.chapter_id);
      m.set(vid, arr);
    });
    return m;
  }, [chapters]);

  const unassigned = useMemo(
    () => chapters.filter((c) => !chapterVolumeId(c)),
    [chapters],
  );

  // 硬约束 1：已有 active 卷时新建必 409，先在 UI 说明。
  const hasActive = volumes.some((v) => v.status === 'active');

  async function handleCreate() {
    setBusy(true);
    setError(null);
    try {
      const payload: VolumeCreatePayload = { number: Number(numberInput) };
      if (titleInput.trim()) payload.title = titleInput.trim();
      if (summaryInput.trim()) payload.arc_summary = summaryInput.trim();
      await volumesApi.create(projectId, payload);
      setCreateOpen(false);
      setNumberInput('1');
      setTitleInput('');
      setSummaryInput('');
      setNotice('卷已创建。');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleSeal() {
    if (!sealTarget) return;
    setBusy(true);
    setError(null);
    try {
      await volumesApi.seal(projectId, sealTarget.volume_id);
      setNotice(`第 ${sealTarget.number} 卷已封卷（终态快照已冻结，不可解封）。`);
      setSealTarget(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSealTarget(null);
    } finally {
      setBusy(false);
    }
  }

  async function handleAssign() {
    if (!assignTarget || !assignChapterId) return;
    setBusy(true);
    setError(null);
    try {
      await volumesApi.assign(projectId, assignTarget.volume_id, assignChapterId);
      const no = chapterNoById.get(assignChapterId) ?? assignChapterId;
      setNotice(`第 ${no} 章已挂入第 ${assignTarget.number} 卷。`);
      setAssignTarget(null);
      setAssignChapterId('');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdateTitle(v: VolumeListItem, title: string) {
    setError(null);
    try {
      const payload: VolumeUpdatePayload = { title };
      await volumesApi.update(projectId, v.volume_id, payload);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (loading) return <Loading text="正在读取卷…" />;

  return (
    <div className="stack">
      <ErrorBanner>{error}</ErrorBanner>
      <InfoBanner>{notice}</InfoBanner>

      <div className="row row--between">
        <h3>卷（{volumes.length}）</h3>
        <button
          type="button"
          className="btn"
          data-testid="volume-create-open"
          onClick={() => setCreateOpen(true)}
        >
          新建卷
        </button>
      </div>

      {hasActive ? (
        <p className="muted small" data-testid="volume-active-hint">
          一个项目同时只能有一个「进行中」的卷——封存当前卷后才能开下一卷。
        </p>
      ) : null}

      {volumes.length === 0 ? (
        <EmptyState
          title="还没有卷"
          hint="卷是章节之上的组织层：一本书可以分多卷，卷可以封存（冻结当时的故事状态）。"
          action={
            <button type="button" className="btn" onClick={() => setCreateOpen(true)}>
              新建第一卷
            </button>
          }
        />
      ) : (
        <table className="table" data-testid="volume-table">
          <thead>
            <tr>
              <th>卷号</th>
              <th>标题</th>
              <th>状态</th>
              <th>章节数</th>
              <th>包含章节</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {volumes.map((v) => {
              const members = membersByVolume.get(v.volume_id) ?? [];
              const nos = members
                .map((cid) => chapterNoById.get(cid))
                .filter((n): n is number => typeof n === 'number')
                .sort((a, b) => a - b);
              return (
                <tr key={v.volume_id} data-testid={`volume-row-${v.volume_id}`}>
                  <td>{v.number}</td>
                  <td>
                    <input
                      className="input"
                      aria-label={`第 ${v.number} 卷标题`}
                      defaultValue={v.title ?? ''}
                      onBlur={(e) => {
                        const next = e.target.value;
                        if (next !== (v.title ?? '')) void handleUpdateTitle(v, next);
                      }}
                    />
                    {v.arc_summary ? (
                      <div className="muted small">{v.arc_summary}</div>
                    ) : null}
                  </td>
                  <td>
                    <span className={STATUS_CLASS[v.status]}>{STATUS_LABEL[v.status]}</span>
                  </td>
                  <td data-testid={`volume-count-${v.volume_id}`}>{v.chapter_count}</td>
                  <td data-testid={`volume-members-${v.volume_id}`}>
                    {nos.length ? nos.map((n) => `第${n}章`).join('、') : '—'}
                  </td>
                  <td className="row">
                    <button
                      type="button"
                      className="btn btn--small"
                      data-testid={`volume-assign-${v.volume_id}`}
                      disabled={v.status === 'sealed'}
                      onClick={() => setAssignTarget(v)}
                    >
                      挂章
                    </button>
                    {v.status === 'active' ? (
                      <button
                        type="button"
                        className="btn btn--small btn--danger"
                        data-testid={`volume-seal-${v.volume_id}`}
                        onClick={() => setSealTarget(v)}
                      >
                        封卷
                      </button>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {unassigned.length > 0 ? (
        <p className="muted small" data-testid="volume-unassigned-note">
          尚未挂卷的章节：{unassigned.length} 章
        </p>
      ) : null}

      {/* 新建卷。⚠️ Modal 没有 open 入参（挂载即显示），必须条件挂载。 */}
      {createOpen ? (
      <Modal
        title="新建卷"
        onClose={() => setCreateOpen(false)}
        testId="volume-create-modal"
        closable={!busy}
      >
        <div className="stack">
          <label className="field">
            <span>卷号（≥1，同项目唯一）</span>
            <input
              className="input"
              data-testid="volume-number-input"
              value={numberInput}
              onChange={(e) => setNumberInput(e.target.value)}
            />
          </label>
          <label className="field">
            <span>标题（可空）</span>
            <input
              className="input"
              data-testid="volume-title-input"
              value={titleInput}
              onChange={(e) => setTitleInput(e.target.value)}
            />
          </label>
          <label className="field">
            <span>卷纲摘要（可空）</span>
            <textarea
              className="input"
              data-testid="volume-summary-input"
              value={summaryInput}
              onChange={(e) => setSummaryInput(e.target.value)}
            />
          </label>
          <div className="row">
            <button
              type="button"
              className="btn"
              data-testid="volume-create-submit"
              disabled={busy}
              onClick={() => void handleCreate()}
            >
              创建
            </button>
            <button type="button" className="btn btn--ghost" onClick={() => setCreateOpen(false)}>
              取消
            </button>
          </div>
        </div>
      </Modal>
      ) : null}

      {/* 封卷二次确认（不可逆 ⇒ danger + 初始焦点给取消） */}
      <ConfirmDialog
        open={sealTarget !== null}
        title="封卷（不可逆）"
        body={
          sealTarget
            ? `封存第 ${sealTarget.number} 卷会把当前故事状态快照冻结进 terminal_snapshot_json，此后该卷不能再挂章，且后端不支持解封。确定封卷？`
            : ''
        }
        confirmText="封卷"
        danger
        testId="volume-seal-confirm"
        onConfirm={() => void handleSeal()}
        onCancel={() => setSealTarget(null)}
      />

      {/* 挂章 */}
      {assignTarget ? (
      <Modal
        title={`把章节挂入第 ${assignTarget.number} 卷`}
        onClose={() => setAssignTarget(null)}
        testId="volume-assign-modal"
        closable={!busy}
      >
        <div className="stack">
          <label className="field">
            <span>章节</span>
            <select
              className="input"
              data-testid="volume-assign-select"
              value={assignChapterId}
              onChange={(e) => setAssignChapterId(e.target.value)}
            >
              <option value="">（选一章）</option>
              {chapters.map((c) => (
                <option key={c.chapter_id} value={c.chapter_id}>
                  第{c.number}章 {c.title}
                </option>
              ))}
            </select>
          </label>
          <div className="row">
            <button
              type="button"
              className="btn"
              data-testid="volume-assign-submit"
              disabled={busy || !assignChapterId}
              onClick={() => void handleAssign()}
            >
              挂入
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => setAssignTarget(null)}
            >
              取消
            </button>
          </div>
        </div>
      </Modal>
      ) : null}
    </div>
  );
}
