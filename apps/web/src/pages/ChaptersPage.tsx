import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { chaptersApi } from '../api/endpoints';
import type { Chapter, ChapterCreatePayload } from '../api/types';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { ChapterStatusBadge } from '../components/ChapterStatusBadge';
import { Loading } from '../components/Loading';
import { Modal } from '../components/Modal';
import { formatDateTime } from '../utils/format';

export function ChaptersPage() {
  const { pid } = useParams();
  const navigate = useNavigate();
  const projectId = pid!;
  const [list, setList] = useState<Chapter[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

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
          className="btn btn--primary"
          data-testid="new-chapter-btn"
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

      {loading ? (
        <Loading />
      ) : list.length === 0 ? (
        <EmptyState
          title="还没有章节"
          hint="点击右上角「新建章节」，从第 1 章开始。"
          action={
            <button
              className="btn btn--primary"
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
    </div>
  );
}

// ------------------------------------------------------------------- modal
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
