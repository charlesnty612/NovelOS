// 剧透策略面板（「这个秘密什么时候、对谁揭开」的规则）。
//
// 后端 `reveal_policies`（DDL 0014 / router `reveal_policies.py`，body 是裸 dict
// ⇒ `types.ts` 里那组类型是手写的，见该文件 G10 批次的登记）。后端 CRUD 四条端点
// 全在，作者却只能看到钩子本身、记不下「该在第几章、对谁揭开」——G6 挂账的又一条
// 「后端有能力、作者够不着」。
//
// 本面板顶部两条诚实说明都来自**后端实现**，不是免责声明；页面与表单的写法都由它们
// 推出来（写在这里是为了让后来者知道哪些是「按语义写」而不是「按偏好写」）：
//
// 1. **与「伏笔 hooks」不是同一个东西**：hook 是实体（在「伏笔与债务」页签维护），
//    一条 `target_kind='hook'` 的策略只是**指向**某个 hook_id、约定何时/对谁揭露它。
//    两个都叫「伏笔」，混了会出现「改了伏笔没生效」的错觉。
// 2. **PATCH / DELETE 完全忽略 URL 里的项目 id**（router `reveal_policies.py:108/133`
//    把 pid 收了不用，service 只按 `policy_id` 单查单改）⇒ 本页**不写**「该策略属于
//    本项目」这类断言：列表给什么就操作什么，多余的保证会误导作者。
//
// 另外两条写进了新建表单，因为它们是**作者填错时后端不会替我们兜住**的地方：
//   - `target_id` 是自由文本、后端**无外键**（DDL 无 FK）：`service.create` 只在
//     **创建时** `SELECT 1 FROM <表> WHERE pk = ?` 校验存在性，且**不校验是否属于本项目**；
//     之后删掉目标实体，策略会静默变孤儿，列表照常显示旧 target_id，无任何告警。
//   - 编辑只暴露 status / revealed_chapter / notes：`RevealPolicyService.update`
//     的签名就只有这三个（`service.py:203`），多传的字段被静默忽略——UI 不给它们
//     可编辑的假象。
//
// 筛选刻意**交给后端**（`status_filter`，见 `endpoints.ts` 的反直觉注释 1）而不是
// 前端过滤：前端过滤会让「筛选没生效」与「这个状态真的一条都没有」长得一模一样。

import { useCallback, useEffect, useState } from 'react';
import {
  charactersApi,
  debtsApi,
  eventsApi,
  factionsApi,
  hooksApi,
  locationsApi,
  relationshipsApi,
  revealPoliciesApi,
  worldRulesApi,
} from '../../api/endpoints';
import type {
  RevealPolicy,
  RevealPolicyCreate,
  RevealPolicyStatus,
  RevealPolicyUpdate,
  RevealTargetKind,
} from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { ErrorBanner, InfoBanner } from '../../components/ErrorBanner';
import { Loading } from '../../components/Loading';
import { Modal } from '../../components/Modal';
import { ConfirmDialog } from '../../components/ConfirmDialog';

interface RevealTabProps {
  projectId: string;
}

const KIND_LABEL: Record<RevealTargetKind, string> = {
  character: '角色',
  location: '地点',
  faction: '势力',
  world_rule: '世界规则',
  event: '事件',
  hook: '伏笔',
  debt: '叙事债务',
  relationship: '人物关系',
};

const STATUS_LABEL: Record<RevealPolicyStatus, string> = {
  planned: '计划中',
  revealed: '已揭开',
  cancelled: '已取消',
};

const STATUS_CLASS: Record<RevealPolicyStatus, string> = {
  planned: 'badge badge--chapter-planned',
  revealed: 'badge badge--chapter-committed',
  cancelled: 'badge badge--archived',
};

interface Candidate {
  id: string;
  label: string;
}

/**
 * 8 种 `target_kind` 在本仓**都**有列表端点，所以每种都能给候选下拉；缺的种类
 * 给空下拉由作者手输 id，而不是编一串选项骗人。
 *
 * ⚠️ 主键字段名不统一，别按字段名猜：角色/伏笔/债务分别是 `character_id` /
 * `hook_id` / `debt_id`；地点·势力·世界规则·事件·关系的响应里叫 `id`
 * （`world/models.py:57`、`plot` 同款），而 service 路由是按 `locations.location_id`
 * 等列查的——**两者是同一个值**，因为该表主键就长那样。
 */
const CANDIDATE_SOURCES: Record<RevealTargetKind, (pid: string) => Promise<Candidate[]>> = {
  character: async (pid) =>
    (await charactersApi.listByProject(pid)).map((c) => ({ id: c.character_id, label: c.name })),
  location: async (pid) =>
    (await locationsApi.list(pid)).map((e) => ({ id: e.id, label: e.name })),
  faction: async (pid) =>
    (await factionsApi.list(pid)).map((e) => ({ id: e.id, label: e.name })),
  world_rule: async (pid) =>
    (await worldRulesApi.list(pid)).map((e) => ({ id: e.id, label: e.name })),
  event: async (pid) =>
    (await eventsApi.list(pid)).map((e) => ({ id: e.id, label: e.description?.trim() || e.id })),
  hook: async (pid) =>
    (await hooksApi.listByProject(pid)).map((h) => ({ id: h.hook_id, label: h.name })),
  debt: async (pid) =>
    (await debtsApi.listByProject(pid)).map((d) => ({ id: d.debt_id, label: d.description })),
  relationship: async (pid) =>
    (await relationshipsApi.list(pid)).map((r) => ({
      id: r.id,
      label: `${r.from_character_id} —${r.relation_type}→ ${r.to_character_id}`,
    })),
};

/** 章号：正整数（service `_validate_pos_int_or_none` 拒绝 0 / 负数 / 非数字）。 */
function chapterBad(raw: string): boolean {
  const v = raw.trim();
  return v !== '' && (!/^[0-9]+$/.test(v) || Number(v) < 1);
}

function planLabel(v: number | null): string {
  return v == null ? '未定' : `第 ${v} 章前`;
}

function actualLabel(v: number | null): string {
  return v == null ? '—' : `第 ${v} 章`;
}

export function RevealTab({ projectId }: RevealTabProps) {
  const [rows, setRows] = useState<RevealPolicy[]>([]);
  const [loading, setLoading] = useState(true);
  // 只有首次加载盖整页 Loading：切筛选时保留旧表，否则每点一次筛选就把整页换成转圈。
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<'' | RevealPolicyStatus>('');

  // 新建表单
  const [createOpen, setCreateOpen] = useState(false);
  const [kind, setKind] = useState<RevealTargetKind>('character');
  const [targetId, setTargetId] = useState('');
  const [revealBy, setRevealBy] = useState('');
  const [audience, setAudience] = useState('reader');
  const [notes, setNotes] = useState('');
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [candidatesFailed, setCandidatesFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  // 编辑（只三字段）
  const [editTarget, setEditTarget] = useState<RevealPolicy | null>(null);
  const [editStatus, setEditStatus] = useState<RevealPolicyStatus>('planned');
  const [editRevealed, setEditRevealed] = useState('');
  const [editNotes, setEditNotes] = useState('');

  // 删除二次确认
  const [deleteTarget, setDeleteTarget] = useState<RevealPolicy | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await revealPoliciesApi.list(projectId, statusFilter || undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setLoadedOnce(true);
    }
  }, [projectId, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  // 候选只在「新建弹窗开着 + 选了某种 kind」时拉：8 种里通常只用得上一种，
  // 全量拉既慢又会在项目没有对应实体时刷出一堆报错。
  useEffect(() => {
    if (!createOpen) return;
    let cancelled = false;
    setCandidatesFailed(false);
    void (async () => {
      try {
        const list = await CANDIDATE_SOURCES[kind](projectId);
        if (!cancelled) setCandidates(list);
      } catch {
        // 候选拉不到**不是**错误：target_id 本来就是自由文本，退回手输即可。
        if (!cancelled) {
          setCandidates([]);
          setCandidatesFailed(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [createOpen, kind, projectId]);

  const byBad = chapterBad(revealBy);
  const createBlocked = !targetId.trim() || byBad;

  // 编辑：status='revealed' 必须有 revealed_chapter（`service._validate_status`：
  // 显式传入优先，否则复用现有值）。这里照后端口径复算，不多拦也不少拦。
  const editByBad = chapterBad(editRevealed);
  const editRcEffective = editRevealed.trim()
    ? Number(editRevealed.trim())
    : (editTarget?.revealed_chapter ?? null);
  const editBlocked = editByBad || (editStatus === 'revealed' && editRcEffective == null);

  async function handleCreate() {
    if (createBlocked) return;
    setBusy(true);
    setError(null);
    try {
      const payload: RevealPolicyCreate = { target_kind: kind, target_id: targetId.trim() };
      if (revealBy.trim()) payload.reveal_by_chapter = Number(revealBy.trim());
      // audience 留空 = 用后端默认 'reader'；传空串会被 `_validate_audience` 判 422。
      if (audience.trim()) payload.audience = audience.trim();
      if (notes.trim()) payload.notes = notes.trim();
      await revealPoliciesApi.create(projectId, payload);
      setCreateOpen(false);
      setTargetId('');
      setRevealBy('');
      setNotes('');
      setNotice('剧透策略已创建。');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function openEdit(p: RevealPolicy) {
    setEditTarget(p);
    setEditStatus(p.status);
    setEditRevealed(p.revealed_chapter == null ? '' : String(p.revealed_chapter));
    setEditNotes(p.notes ?? '');
  }

  async function handleSaveEdit() {
    if (!editTarget || editBlocked) return;
    const payload: RevealPolicyUpdate = { status: editStatus, notes: editNotes };
    // ⚠️ 后端 `update` 把 `revealed_chapter is None` 读作「不提供」⇒ 清空输入框
    // **不会**清掉库里的值。这里刻意不发 null，并把这件事写在弹窗里。
    if (editRevealed.trim()) payload.revealed_chapter = Number(editRevealed.trim());
    setBusy(true);
    setError(null);
    try {
      await revealPoliciesApi.update(projectId, editTarget.policy_id, payload);
      setEditTarget(null);
      setNotice('剧透策略已更新。');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    const id = deleteTarget.policy_id;
    setBusy(true);
    setError(null);
    try {
      await revealPoliciesApi.remove(projectId, id);
      setDeleteTarget(null);
      setNotice('剧透策略已删除。');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDeleteTarget(null);
    } finally {
      setBusy(false);
    }
  }

  if (loading && !loadedOnce) return <Loading text="正在读取剧透策略…" />;

  return (
    <div className="stack">
      <ErrorBanner>{error}</ErrorBanner>
      <InfoBanner>{notice}</InfoBanner>

      <InfoBanner>
        <div data-testid="reveal-honest-hooks">
          剧透策略<b>不是伏笔</b>：伏笔（hook）是实体，在「伏笔与债务」页签维护；
          这里的一行只是<b>指向</b>某个实体的 id，记录「什么时候、对谁揭开」。本页改不了伏笔本身。
        </div>
        <div data-testid="reveal-honest-pid" style={{ marginTop: 6 }}>
          后端的编辑 / 删除<b>忽略 URL 里的项目 id</b>（只按 policy_id 定位），所以本页
          不写「该策略属于本项目」这类断言——它只是当前列表里的一行。
        </div>
      </InfoBanner>

      <div className="row row--between">
        <h3>剧透策略（{rows.length}）</h3>
        <div className="row">
          <select
            className="input"
            aria-label="按状态筛选"
            data-testid="reveal-status-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as '' | RevealPolicyStatus)}
          >
            <option value="">全部状态</option>
            <option value="planned">计划中</option>
            <option value="revealed">已揭开</option>
            <option value="cancelled">已取消</option>
          </select>
          <button
            type="button"
            className="btn"
            data-testid="reveal-create-open"
            onClick={() => setCreateOpen(true)}
          >
            新建策略
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title={statusFilter ? '该状态下没有剧透策略' : '还没有剧透策略'}
          hint={
            statusFilter
              ? '换一个状态，或看「全部状态」。'
              : '剧透策略记录「某个秘密计划在第几章前、对谁揭开」，由你手写，不由流程自动生成。'
          }
          action={
            <button type="button" className="btn" onClick={() => setCreateOpen(true)}>
              新建第一条
            </button>
          }
        />
      ) : (
        <table className="table" data-testid="reveal-table">
          <thead>
            <tr>
              <th>目标</th>
              <th>计划揭于</th>
              <th>面向对象</th>
              <th>状态</th>
              <th>实际揭于</th>
              <th>备注</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.policy_id} data-testid={`reveal-row-${p.policy_id}`}>
                <td data-testid={`reveal-target-${p.policy_id}`}>
                  <span className="badge">{KIND_LABEL[p.target_kind] ?? p.target_kind}</span>{' '}
                  <code>{p.target_id}</code>
                </td>
                <td>{planLabel(p.reveal_by_chapter)}</td>
                <td data-testid={`reveal-audience-${p.policy_id}`}>
                  {p.audience.trim() || '（未标注对象）'}
                </td>
                <td>
                  <span className={STATUS_CLASS[p.status]}>{STATUS_LABEL[p.status]}</span>
                </td>
                <td>{actualLabel(p.revealed_chapter)}</td>
                <td>{p.notes?.trim() || '—'}</td>
                <td className="row">
                  <button
                    type="button"
                    className="btn btn--small"
                    data-testid={`reveal-edit-${p.policy_id}`}
                    onClick={() => openEdit(p)}
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    className="btn btn--small btn--danger"
                    data-testid={`reveal-delete-${p.policy_id}`}
                    onClick={() => setDeleteTarget(p)}
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 新建。⚠️ Modal 没有 open 入参（挂载即显示），必须条件挂载。 */}
      {createOpen ? (
        <Modal
          title="新建剧透策略"
          onClose={() => setCreateOpen(false)}
          testId="reveal-create-modal"
          closable={!busy}
        >
          <div className="stack">
            <label className="field">
              <span>目标种类</span>
              <select
                className="input"
                data-testid="reveal-kind-select"
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value as RevealTargetKind);
                  // 换了种类就换候选集；旧 id 留着只会让人把 A 的 id 存成 B 的。
                  setTargetId('');
                }}
              >
                {(Object.keys(KIND_LABEL) as RevealTargetKind[]).map((k) => (
                  <option key={k} value={k}>
                    {KIND_LABEL[k]}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>目标 id（自由文本）</span>
              <input
                className="input"
                data-testid="reveal-target-input"
                value={targetId}
                onChange={(e) => setTargetId(e.target.value)}
              />
            </label>

            <label className="field">
              <span>从候选里选（可留空手输）</span>
              {/* key={kind}：换种类时下拉回到「手输」，不残留上一类的候选值。 */}
              <select
                key={kind}
                className="input"
                data-testid="reveal-target-picker"
                defaultValue=""
                onChange={(e) => setTargetId(e.target.value)}
              >
                <option value="">（手输 id）</option>
                {candidates.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label}（{c.id}）
                  </option>
                ))}
              </select>
            </label>

            {/* 候选拉不到就说「手输」，不静默成「这个种类没有候选」——后者是假陈述。 */}
            {candidatesFailed ? (
              <p className="muted small" data-testid="reveal-candidates-failed">
                该种类的候选列表拉取失败，请直接粘贴目标 id。
              </p>
            ) : null}

            <p className="muted small" data-testid="reveal-hint-target">
              target_id 填目标实体的 id。下拉只是方便，仍可直接粘贴：后端<b>没有外键</b>，
              只在新建时校验该 id 存在（且不校验是否属于本项目）——目标实体之后被删掉，
              这条策略会静默变成孤儿，列表不会告警。
            </p>

            <label className="field">
              <span>计划在第几章前揭开（可空 = 未定）</span>
              <input
                className="input"
                data-testid="reveal-by-input"
                value={revealBy}
                onChange={(e) => setRevealBy(e.target.value)}
              />
            </label>
            {byBad ? (
              <p className="muted small" data-testid="reveal-by-error">
                章号必须是正整数（≥1）。
              </p>
            ) : null}

            <label className="field">
              <span>面向对象（reader / character:&lt;id&gt;，留空 = reader）</span>
              <input
                className="input"
                data-testid="reveal-audience-input"
                value={audience}
                onChange={(e) => setAudience(e.target.value)}
              />
            </label>

            <label className="field">
              <span>备注（可空）</span>
              <textarea
                className="input"
                data-testid="reveal-notes-input"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </label>

            <div className="row">
              <button
                type="button"
                className="btn"
                data-testid="reveal-create-submit"
                disabled={busy || createBlocked}
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

      {/* 编辑：只暴露后端 update 真正认的三个字段。 */}
      {editTarget ? (
        <Modal
          title={`编辑剧透策略：${KIND_LABEL[editTarget.target_kind]} ${editTarget.target_id}`}
          onClose={() => setEditTarget(null)}
          testId="reveal-edit-modal"
          closable={!busy}
        >
          <div className="stack">
            <p className="muted small" data-testid="reveal-edit-hint">
              后端只认<b>状态 / 实际揭于章号 / 备注</b>三个字段；目标、计划章号、面向对象
              提交了也不生效（要改请删除重建）。清空「实际揭于章号」<b>不会</b>清掉库里已有的值
              ——后端把「未提供」与「清空」混同。
            </p>

            <label className="field">
              <span>状态</span>
              <select
                className="input"
                data-testid="reveal-edit-status"
                value={editStatus}
                onChange={(e) => setEditStatus(e.target.value as RevealPolicyStatus)}
              >
                <option value="planned">计划中</option>
                <option value="revealed">已揭开</option>
                <option value="cancelled">已取消</option>
              </select>
            </label>

            <label className="field">
              <span>实际揭于第几章（可空）</span>
              <input
                className="input"
                data-testid="reveal-edit-revealed"
                value={editRevealed}
                onChange={(e) => setEditRevealed(e.target.value)}
              />
            </label>
            {editStatus === 'revealed' && editRcEffective == null && !editByBad ? (
              <p className="muted small" data-testid="reveal-edit-revealed-required">
                状态改为「已揭开」时必须给出实际揭于章号，否则后端返回 422。
              </p>
            ) : null}

            <label className="field">
              <span>备注</span>
              <textarea
                className="input"
                data-testid="reveal-edit-notes"
                value={editNotes}
                onChange={(e) => setEditNotes(e.target.value)}
              />
            </label>

            <div className="row">
              <button
                type="button"
                className="btn"
                data-testid="reveal-edit-submit"
                disabled={busy || editBlocked}
                onClick={() => void handleSaveEdit()}
              >
                保存
              </button>
              <button type="button" className="btn btn--ghost" onClick={() => setEditTarget(null)}>
                取消
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除剧透策略"
        body={
          deleteTarget
            ? `删除「${KIND_LABEL[deleteTarget.target_kind]} ${deleteTarget.target_id}」的这条揭露约定？后端不保留删除记录，也无法恢复。`
            : ''
        }
        confirmText="删除"
        danger
        testId="reveal-delete-confirm"
        onConfirm={() => void handleDelete()}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
