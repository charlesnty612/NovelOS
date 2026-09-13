// 草稿版本区（从 ChapterDetailPage 拆出，2026-09-13 前端批次 C 信息架构重构）。
//
// 内容：版本列表（选中受控于页面）+ 当前版本正文 / 人工改稿编辑器。
// 布局：版本列表窄列 + 正文宽列；正文容器用 min-height 自适应，不再用固定像素高度。

import { useState } from 'react';
import { chaptersApi } from '../../api/endpoints';
import type { Chapter, Draft } from '../../api/types';
import { ErrorBanner, InfoBanner } from '../../components/ErrorBanner';
import { EmptyState } from '../../components/EmptyState';
import { ProseText } from '../../components/ProseText';
import { formatDateTime } from '../../utils/format';
import { ApiError } from '../../api/client';

const CHAPTER_STATUS_LABEL: Record<string, string> = {
  PLANNED: '计划中',
  DRAFTED: '草稿态',
  REVIEWED: '已审待改',
  COMMITTED: '已定稿',
};

export function ChapterDraftsSection({
  chapterId,
  chapterStatus,
  lastReviewCompletedAt,
  drafts,
  draftsLoading,
  draftsError,
  selectedDraftVersion,
  onSelectDraftVersion,
  onCreated,
}: {
  chapterId: string;
  chapterStatus: Chapter['status'];
  /** 最近一次 COMPLETED 状态 chapter-review run 的 ended_at（ISO 字符串）；
   *  null 表示该章节从未审过。用于在版本列表行渲染「未审」角标。 */
  lastReviewCompletedAt: string | null;
  drafts: Draft[];
  draftsLoading: boolean;
  draftsError: string | null;
  /** 页面持有的当前选中版本号（受控）。null = drafts 为空或尚未回落。 */
  selectedDraftVersion: number | null;
  /** 版本被点选时通知页面；保存新版本后页面会刷新此值,本组件无须本地同步。 */
  onSelectDraftVersion: (version: number) => void;
  onCreated: () => Promise<void> | void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editorText, setEditorText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 受控：选中态由页面持有。本组件只读 drafts 派生当前 draft 对象。
  const selected = drafts.find((d) => d.version === selectedDraftVersion) ?? null;

  const canCreateDraft = chapterStatus === 'DRAFTED' || chapterStatus === 'REVIEWED';

  const handleStartEdit = () => {
    if (!selected) return;
    setEditorText(selected.content);
    setEditingId(selected.draft_id);
    setErr(null);
  };

  const handleSave = async () => {
    if (!editorText.trim()) {
      setErr('草稿内容不能为空');
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      await chaptersApi.createDraft(chapterId, { content: editorText });
      setEditingId(null);
      setEditorText('');
      await onCreated();
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        setErr(`保存失败（${e.status}）：${e.detail}`);
      } else {
        setErr(e instanceof Error ? e.message : '保存失败');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="panel" data-testid="drafts-panel">
      <div className="panel__title">
        草稿版本
        <span className="cdp-spacer" />
        {editingId ? (
          <>
            <button
              className="btn btn--sm"
              onClick={() => {
                setEditingId(null);
                setEditorText('');
                setErr(null);
              }}
              disabled={submitting}
            >
              取消
            </button>
            <button
              className="btn btn--sm btn--primary"
              onClick={handleSave}
              disabled={submitting}
              data-testid="draft-save"
            >
              {submitting ? '保存中…' : '保存为新版本'}
            </button>
          </>
        ) : (
          <button
            className="btn btn--sm btn--primary"
            disabled={!canCreateDraft}
            title={
              canCreateDraft
                ? '把当前选中版本载入编辑器，编辑后保存为新 draft 版本'
                : `草稿仅在 DRAFTED / REVIEWED 时可新增；当前 ${chapterStatus}`
            }
            onClick={handleStartEdit}
            data-testid="draft-edit-btn"
          >
            人工改稿
          </button>
        )}
      </div>

      <ErrorBanner>{err}</ErrorBanner>
      <ErrorBanner>{draftsError}</ErrorBanner>
      {!canCreateDraft ? (
        <InfoBanner>
          当前章节状态「{CHAPTER_STATUS_LABEL[chapterStatus] ?? chapterStatus}
          」：仅在「{CHAPTER_STATUS_LABEL.DRAFTED}」或「{CHAPTER_STATUS_LABEL.REVIEWED}
          」时可新增草稿版本。
        </InfoBanner>
      ) : null}

      {draftsLoading ? (
        <div className="muted">加载中…</div>
      ) : drafts.length === 0 ? (
        <EmptyState title="还没有草稿" hint="运行「写正文」或人工改稿会生成版本。" />
      ) : (
        <div className="cdp-drafts">
          <div className="panel__section cdp-drafts__list">
            <div className="panel__section-title">版本列表</div>
            <div className="kv-list">
              {drafts.map((d) => (
                <div
                  key={d.draft_id}
                  className={`kv-list__row ${d.version === selectedDraftVersion ? 'kv-list__row--active' : ''}`}
                  onClick={() => {
                    onSelectDraftVersion(d.version);
                    setEditingId(null);
                  }}
                  data-testid={`draft-row-${d.draft_id}`}
                >
                  <span className="kv-list__title">v{d.version}</span>
                  {/* 模型徽标：model_id 存在且非 'mock/mock' 时展示（mock 默认无意义）。
                      完整 model_id 保留在 title，便于调试；徽标本身只显示短形式。 */}
                  {d.model_id && d.model_id !== 'mock/mock' ? (
                    <span
                      className="badge"
                      title={d.model_id}
                      data-testid={`draft-model-${d.draft_id}`}
                    >
                      {d.model_id}
                    </span>
                  ) : null}
                  {/* 「未审」徽标：该 draft 严格晚于最近一次 COMPLETED review 的
                      ended_at 才算「审校后又改稿 / 续写」。ISO 字符串可字典序比较。
                      lastReviewCompletedAt 为 null（该章节从未审过）则不显示，
                      避免在用户首次走流水线时被噪声覆盖。 */}
                  {lastReviewCompletedAt && d.created_at > lastReviewCompletedAt ? (
                    <span
                      className="badge badge--unreviewed"
                      title={`未审：该版本生成于 ${formatDateTime(lastReviewCompletedAt)} 那次审校之后`}
                      data-testid={`draft-unreviewed-${d.draft_id}`}
                    >
                      未审
                    </span>
                  ) : null}
                  <span className="muted small">{d.created_by}</span>
                  <span className="kv-list__meta">{formatDateTime(d.created_at)}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="panel__section cdp-drafts__content">
            <div className="panel__section-title">
              {editingId ? '编辑内容（保存后会创建新版本）' : '当前版本内容'}
            </div>
            {editingId ? (
              <textarea
                className="cdp-draft-textarea"
                value={editorText}
                onChange={(e) => setEditorText(e.target.value)}
                rows={16}
                data-testid="draft-textarea"
              />
            ) : selected ? (
              <div className="prose-block cdp-draft-content" data-testid="draft-content">
                <ProseText text={selected.content} />
              </div>
            ) : (
              <div className="muted">从左侧选择一份草稿查看。</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
