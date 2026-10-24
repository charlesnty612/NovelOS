import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  chaptersApi,
  modelProfilesApi,
  projectsApi,
  workflowsApi,
} from '../api/endpoints';
import { ApiError } from '../api/client';
import type {
  Chapter,
  ChapterCreatePayload,
  ModelProfile,
  OutlineBatchRequest,
  WorkflowRun,
} from '../api/types';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { ChapterStatusBadge } from '../components/ChapterStatusBadge';
import { Loading } from '../components/Loading';
import { Modal } from '../components/Modal';
import { usePoll } from '../hooks/usePoll';
import { formatDateTime } from '../utils/format';

export function ChaptersPage() {
  const { pid } = useParams();
  const navigate = useNavigate();
  const projectId = pid!;
  const [list, setList] = useState<Chapter[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // 续规划章节（outline-batch）：弹窗开关 / 轮询中的 run_id / 本次规划章数 /
  // 顶部提示（进行中、成功、失败共用一条横幅）。
  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [batchRunId, setBatchRunId] = useState<string | null>(null);
  const [batchPlannedCount, setBatchPlannedCount] = useState(0);
  const [batchNotice, setBatchNotice] = useState<string | null>(null);

  // reload 用 useCallback 绑定 projectId：effect 依赖 reload，无需 eslint-disable
  // 掩盖「缺依赖」（项目没有 ESLint，死注释只会误导后来者）。
  const reload = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const rows = await chaptersApi.listByProject(projectId);
      setList(rows);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // ------------------------------------------------------ 续规划章节（outline-batch）
  // 轮询复用仓内 usePoll（RUNNING 时 2s 一次，终态停止；targetKey = run_id 防
  // 两次起跑串台）。outline-batch 三节点无人工门，终态集合仍按 run 状态机全量
  // 对待：PAUSED / CANCELLED 虽然不该出现，停轮询并交中性提示，避免按钮永久禁用。
  const handleBatchTerminal = useCallback(
    (run: WorkflowRun) => {
      setBatchRunId(null);
      if (run.status === 'COMPLETED') {
        setBatchNotice(
          `已新增 ${batchPlannedCount} 章（状态为「已计划」），请在各章详情页先执行「生成计划」再写正文。`,
        );
        void reload();
      } else if (run.status === 'FAILED') {
        setBatchNotice(
          `规划失败：${run.error ?? '未知错误（可到运行记录查看详情）'}`,
        );
      } else {
        setBatchNotice(`规划任务已停止（状态：${run.status}）。`);
      }
    },
    [batchPlannedCount, reload],
  );

  // M-1（2026-10-24）：接住轮询的 error 通道。usePoll 出错即停（stopOnError 默认
  // true），若不消费 error，batchRunId 残留 ⇒ 「续规划章节」「+ 新建章节」两按钮
  // 永久禁用（一次网络抖动就死锁 UI）。
  // - error 非空 ⇒ 清 batchRunId（按钮恢复；轮询 enabled 随之关闭），并把错误
  //   **快照**存进 batchNotice 横幅——不能直接渲染 pollError：清 batchRunId 会
  //   让 targetKey 回到 undefined，usePoll 的「目标切换清 error」机制立即把
  //   error 清空 ⇒ 横幅闪烁即逝；batchNotice 是页面自有 state，不受其影响。
  // - effect 依赖只放 batchPollError：新起跑换 run_id 时 usePoll 内部会先把 error
  //   清回 null（targetKey 变化路径），本 effect 不会拿旧错误误清新 run。
  const { error: batchPollError } = usePoll<WorkflowRun>({
    fn: useCallback(() => workflowsApi.get(batchRunId!), [batchRunId]),
    intervalMs: 2000,
    enabled: batchRunId !== null,
    targetKey: batchRunId ?? undefined,
    stopWhen: (run) =>
      run !== null &&
      ['COMPLETED', 'FAILED', 'CANCELLED', 'PAUSED'].includes(run.status),
    onResult: (run) => {
      if (['COMPLETED', 'FAILED', 'CANCELLED', 'PAUSED'].includes(run.status)) {
        handleBatchTerminal(run);
      }
    },
  });

  useEffect(() => {
    if (batchPollError !== null) {
      setBatchRunId(null);
      setBatchNotice(
        `规划进度查询失败，已停止自动刷新：${batchPollError}` +
          `（可在运行记录查看该次规划结果，或重新发起。）`,
      );
    }
  }, [batchPollError]);

  const openChapter = (chapterId: string) => {
    navigate(`/projects/${projectId}/chapters/${chapterId}`);
  };

  return (
    <div>
      <div className="toolbar">
        <h1 className="section-title" style={{ margin: 0 }}>
          章节
        </h1>
        <div className="toolbar__spacer" />
        <button
          className="btn"
          data-testid="outline-batch-btn"
          // 起跑期间禁用：项目级并发下再点必然 409。
          disabled={batchRunId !== null || creating}
          onClick={() => setBatchModalOpen(true)}
        >
          {batchRunId !== null ? '规划中…' : '续规划章节'}
        </button>
        <button
          className="btn btn--primary"
          data-testid="new-chapter-btn"
          disabled={batchRunId !== null}
          onClick={() => setCreating(true)}
        >
          + 新建章节
        </button>
      </div>
      <p className="section-subtitle">
        按章节号管理章节大纲、草稿与 AI 工作流。进入章节详情可运行计划 / 写作 / 审校 /
        提交四步流程。
      </p>

      <ErrorBanner>{err}</ErrorBanner>

      {batchNotice !== null ? (
        <p
          className="section-subtitle"
          role="status"
          data-testid="outline-batch-notice"
        >
          {batchNotice}
        </p>
      ) : null}

      {loading ? (
        <Loading />
      ) : list.length === 0 ? (
        <EmptyState
          title="还没有章节"
          hint="点击右上角「新建章节」，从第 1 章开始。"
          action={
            <button
              className="btn btn--primary"
              disabled={batchRunId !== null}
              onClick={() => setCreating(true)}
            >
              + 新建章节
            </button>
          }
        />
      ) : (
        <div className="table-wrap">
        <table className="table" data-testid="chapter-list">
          <thead>
            <tr>
              {/* 列宽收编 CSS 类（.table__col--*）——不再逐格写 inline width */}
              <th className="table__col table__col--number">章号</th>
              <th>标题</th>
              <th className="table__col table__col--status">状态</th>
              <th className="table__col table__col--time">最近更新</th>
              <th className="right table__col table__col--actions">操作</th>
            </tr>
          </thead>
          <tbody>
            {list.map((c) => (
              <tr
                key={c.chapter_id}
                // 双入口：整行可点（鼠标便利）+ 「进入」按钮（语义/键盘入口）。
                // 行上只加 tabIndex 与 Enter/Space 处理，**不加 role**：<tr> 的隐式 row
                // 语义必须保留（role="button" 会让表格结构对读屏失效，axe 的
                // aria-required-children 也会报错）；键盘用户既能按 Tab 到行、按 Enter
                // 进入，也能用更明确的「进入」按钮。
                tabIndex={0}
                className="table__row--link"
                onClick={() => openChapter(c.chapter_id)}
                onKeyDown={(e) => {
                  // 子元素（「进入」按钮）自己处理键盘时不再重复触发
                  if (e.target !== e.currentTarget) return;
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    openChapter(c.chapter_id);
                  }
                }}
                style={{ cursor: 'pointer' }}
                data-testid={`chapter-row-${c.chapter_id}`}
              >
                <td>第 {c.number} 章</td>
                <td>{c.title ?? <span className="muted">（未命名）</span>}</td>
                <td>
                  <ChapterStatusBadge status={c.status} />
                </td>
                <td className="muted small">{formatDateTime(c.updated_at)}</td>
                <td className="right">
                  <button
                    className="btn btn--sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      openChapter(c.chapter_id);
                    }}
                  >
                    进入
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      {creating ? (
        <ChapterCreateModal
          existingNumbers={list.map((c) => c.number)}
          onCancel={() => setCreating(false)}
          onSubmit={async (payload) => {
            await chaptersApi.create(projectId, payload);
            setCreating(false);
            await reload();
          }}
        />
      ) : null}

      {batchModalOpen ? (
        <OutlineBatchModal
          onCancel={() => setBatchModalOpen(false)}
          onSubmit={async (payload) => {
            const run = await projectsApi.outlineBatch(projectId, payload);
            // 起跑成功：关弹窗、记下目标 run 与章数（成功提示用），轮询由
            // usePoll 的 enabled 接管。异常（409/422/400/404）留给弹窗内展示。
            setBatchPlannedCount(payload.chapter_count ?? 10);
            setBatchRunId(run.run_id);
            setBatchModalOpen(false);
            setBatchNotice(
              `正在规划 ${payload.chapter_count ?? 10} 章，完成后将出现在列表中…`,
            );
          }}
        />
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------- modal
interface OutlineBatchModalProps {
  onCancel: () => void;
  /** 启动成功（201）即 resolve；失败抛错由弹窗内展示（409 有专门文案）。 */
  onSubmit: (payload: OutlineBatchRequest) => Promise<void>;
}

// 续规划章节弹窗：章数（1~50，默认 10，前端同口径拦）+ 可选模型档案 +
// 说明文案。档案下拉为可选增强：拉取失败不阻塞提交（不传 = 跟随后端默认）。
function OutlineBatchModal({ onCancel, onSubmit }: OutlineBatchModalProps) {
  const [count, setCount] = useState('10');
  const [profileId, setProfileId] = useState('');
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    modelProfilesApi
      .list()
      .then((rows) => {
        if (alive) setProfiles(rows);
      })
      .catch(() => {
        // 档案可选：列表拉不到就不展示下拉项，用户仍可按默认提交。
      });
    return () => {
      alive = false;
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const n = Number(count);
    // 后端 422 同口径（1~50）；前端先拦，省一次必败往返。
    if (!Number.isInteger(n) || n < 1 || n > 50) {
      setErr('章数必须是 1~50 的整数');
      return;
    }
    setSubmitting(true);
    try {
      // 未选档案 = 跟随项目默认：请求体不带 model_profile_id（不是传 null）。
      const payload: OutlineBatchRequest = { chapter_count: n };
      if (profileId !== '') payload.model_profile_id = profileId;
      await onSubmit(payload);
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) {
        setErr('已有规划任务在进行中，请等当前任务结束后再试');
      } else {
        setErr(e instanceof Error ? e.message : '启动失败');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="续规划章节"
      onClose={onCancel}
      width={420}
      testId="outline-batch-modal"
      closable={!submitting}
    >
      <form onSubmit={handleSubmit}>
        <ErrorBanner>{err}</ErrorBanner>

        <p className="section-subtitle" style={{ marginTop: 0 }}>
          基于已写章节、未闭环伏笔与叙事债务，由 AI 规划下一批章节大纲（状态为「已计划」）。
          新章生成后为大纲状态——需在章节详情页先执行「生成计划」（AI 依据大纲产出本章详规），再写正文。
        </p>

        <div className="form-grid">
          <div className="form-row">
            <label>本次规划章数 *</label>
            <input
              type="number"
              min={1}
              max={50}
              value={count}
              onChange={(e) => setCount(e.target.value)}
              required
              data-testid="outline-batch-count"
            />
          </div>
          <div className="form-row">
            <label>模型档案（可选）</label>
            <select
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
              data-testid="outline-batch-profile"
            >
              <option value="">跟随项目默认</option>
              {profiles.map((p) => (
                <option key={p.profile_id} value={p.profile_id}>
                  {p.name}（{p.model}）
                </option>
              ))}
            </select>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={submitting}
            data-testid="outline-batch-submit"
          >
            {submitting ? '启动中…' : '开始规划'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

interface ChapterCreateModalProps {
  existingNumbers: number[];
  onCancel: () => void;
  onSubmit: (payload: ChapterCreatePayload) => Promise<void>;
}

function ChapterCreateModal({
  existingNumbers,
  onCancel,
  onSubmit,
}: ChapterCreateModalProps) {
  const suggestedNumber =
    existingNumbers.length === 0
      ? 1
      : Math.max(...existingNumbers) + 1;
  const [number, setNumber] = useState<string>(String(suggestedNumber));
  const [title, setTitle] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const n = Number(number);
    if (!Number.isInteger(n) || n < 1) {
      setErr('章号必须是 ≥1 的整数');
      return;
    }
    if (existingNumbers.includes(n)) {
      setErr(`章号 ${n} 已存在，请使用其他章号`);
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit({ number: n, title: title.trim() || null });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="新建章节"
      onClose={onCancel}
      width={420}
      testId="chapter-create-modal"
      // 提交中不允许 Esc / 点遮罩关闭（仍可用「取消」按钮退出）
      closable={!submitting}
    >
      <form onSubmit={handleSubmit}>
        <ErrorBanner>{err}</ErrorBanner>

        <div className="form-grid">
          <div className="form-row">
            <label>章号 *</label>
            <input
              type="number"
              min={1}
              value={number}
              onChange={(e) => setNumber(e.target.value)}
              required
              data-testid="chapter-number"
            />
          </div>
          <div className="form-row">
            <label>标题</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="（可选）"
              data-testid="chapter-title"
            />
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={submitting}
            data-testid="chapter-save"
          >
            {submitting ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
