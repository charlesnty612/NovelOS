// 章节头 + 四步流水线（从 ChapterDetailPage 拆出，2026-09-13 前端批次 C 信息架构重构）。
//
// 内容：章节标题（可 inline 改名，接 chaptersApi.update 的 title 字段）、状态徽标、
// 模型档案下拉（按次选择）、写作模式、深度二审开关，以及 plan/write/review/commit
// 四步按钮与状态机展示态。

import { useEffect, useMemo, useState } from 'react';
import { chaptersApi, modelProfilesApi } from '../../api/endpoints';
import type { Chapter, ModelProfile } from '../../api/types';
import { ChapterStatusBadge } from '../../components/ChapterStatusBadge';
import { ErrorBanner } from '../../components/ErrorBanner';
import { useApiCall } from '../../hooks/useApiCall';
import { getButtonAvailability, getPipelineStepStates } from '../../utils/chapterState';

export type WorkflowAction = 'plan' | 'write' | 'review' | 'commit';

export interface WorkflowStartArgs {
  author_intent?: string;
  target_word_count?: number;
  /** 已选档案 id（profile_id）；非空时并入 model_overrides[capability] */
  model_profile_id?: string | null;
  /** 写作模式（仅 write 生效）：true ⇒ 全新重写 */
  fresh_write?: boolean;
  /** 深度二审开关（仅 review 生效）：true ⇒ review 请求体带 deep_review: true */
  deep_review?: boolean;
}

export type StartWorkflowFn = (
  action: WorkflowAction,
  payload?: WorkflowStartArgs,
) => Promise<void>;

export function ChapterPipelineHeader({
  chapter,
  activeRunStatus,
  submitting,
  selectedDraftVersion,
  onStart,
  deepReview,
  onDeepReviewChange,
  onTitleSaved,
}: {
  chapter: Chapter;
  activeRunStatus: 'PENDING' | 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | null;
  submitting: boolean;
  /** 当前选中的 draft 版本号;用于「将审校:草稿 vN」动态提示。null = 尚无草稿。 */
  selectedDraftVersion: number | null;
  onStart: StartWorkflowFn;
  /** 「审校」步骤深度二审（Kimi 三层清单）开关 */
  deepReview: boolean;
  onDeepReviewChange: (v: boolean) => void;
  /** 标题改名成功后的回调（页面重拉 chapter） */
  onTitleSaved: () => void | Promise<void>;
}) {
  const activeRunInfo =
    activeRunStatus === 'RUNNING' || activeRunStatus === 'PENDING'
      ? { status: activeRunStatus as 'RUNNING' | 'PENDING' }
      : null;

  // 拉取已启用模型档案列表；失败静默降级为不显示下拉（不阻塞按钮）。
  const profilesCall = useApiCall<ModelProfile[]>(() => modelProfilesApi.list(), []);
  const enabledProfiles = useMemo(
    () =>
      (profilesCall.data ?? []).filter((p) => {
        const v = p.enabled;
        return v === 1 || v === true;
      }),
    [profilesCall.data],
  );

  // 每个按钮独立保存选中的 profile_id（key=action）；空串=走环节绑定默认。
  // V3.9.4：四 action 全支持模型下拉（commit 走 observer 覆盖键）。
  const [selectedProfile, setSelectedProfile] = useState<Record<WorkflowAction, string>>({
    plan: '',
    write: '',
    review: '',
    commit: '',
  });
  const setProfile = (action: WorkflowAction, value: string) =>
    setSelectedProfile((prev) => ({ ...prev, [action]: value }));

  // 「写正文」动作专属：写作模式选择。'fresh'=全新重写（默认，2026-08-30 用户拍板：
  // 改稿应由「按建议修改/驳回并改稿」链路触发，手动写正文默认整章重写），''=按意见改稿。
  // 仅 write 卡片渲染该下拉；点击时读取最新值，避免 setState 异步竞态。
  const [writeMode, setWriteMode] = useState<'' | 'fresh'>('fresh');

  // ---- 标题 inline 改名（2026-09-13 批次 C 功能补口）----
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(chapter.title ?? '');
  const [savingTitle, setSavingTitle] = useState(false);
  const [titleError, setTitleError] = useState<string | null>(null);

  useEffect(() => {
    if (!editingTitle) setTitleDraft(chapter.title ?? '');
  }, [chapter.chapter_id, chapter.title, editingTitle]);

  const startEditTitle = () => {
    setTitleDraft(chapter.title ?? '');
    setTitleError(null);
    setEditingTitle(true);
  };

  const saveTitle = async () => {
    if (savingTitle) return;
    const next = titleDraft.trim();
    if (next === (chapter.title ?? '')) {
      setEditingTitle(false);
      return;
    }
    setSavingTitle(true);
    setTitleError(null);
    try {
      await chaptersApi.update(chapter.chapter_id, { title: next === '' ? null : next });
      setEditingTitle(false);
      await onTitleSaved();
    } catch (e: unknown) {
      setTitleError(e instanceof Error ? e.message : '标题保存失败');
    } finally {
      setSavingTitle(false);
    }
  };

  const buttons: Array<{
    action: WorkflowAction;
    title: string;
    hint: string;
  }> = [
    { action: 'plan', title: '生成计划', hint: 'Director 生成章节计划' },
    { action: 'write', title: '写正文', hint: 'Writer 生成正文草稿' },
    { action: 'review', title: '审校', hint: '基础检查 + 作者审批' },
    { action: 'commit', title: '提交', hint: '提交定稿（自动提取状态变化）' },
  ];

  // 四步流水线展示态：状态机 + plan_json 是否已落库共同决定每步是 done/current/todo。
  const hasPlanForPipeline =
    chapter != null &&
    chapter.plan_json != null &&
    typeof chapter.plan_json === 'object' &&
    !Array.isArray(chapter.plan_json) &&
    Object.keys(chapter.plan_json).length > 0;
  const stepStates = getPipelineStepStates({
    chapterStatus: chapter.status,
    hasPlan: hasPlanForPipeline,
  });

  const stepStateLabel: Record<'done' | 'current' | 'todo', string> = {
    done: '已完成',
    current: '当前步骤',
    todo: '未到达',
  };
  const stepOrder: WorkflowAction[] = ['plan', 'write', 'review', 'commit'];
  const buttonByAction = new Map(buttons.map((b) => [b.action, b] as const));

  return (
    <div className="panel" data-testid="chapter-header">
      <div className="cdp-header__top">
        {editingTitle ? (
          <div className="cdp-title-edit">
            <input
              className="cdp-title-edit__input"
              value={titleDraft}
              autoFocus
              maxLength={200}
              placeholder="章节标题（留空 = 未命名）"
              data-testid="chapter-title-input"
              disabled={savingTitle}
              onChange={(e) => setTitleDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  void saveTitle();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  setEditingTitle(false);
                  setTitleError(null);
                }
              }}
            />
            <button
              className="btn btn--sm btn--primary"
              data-testid="chapter-title-save"
              disabled={savingTitle}
              onClick={() => void saveTitle()}
            >
              {savingTitle ? '保存中…' : '保存'}
            </button>
            <button
              className="btn btn--sm"
              data-testid="chapter-title-cancel"
              disabled={savingTitle}
              onClick={() => {
                setEditingTitle(false);
                setTitleError(null);
              }}
            >
              取消
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="cdp-header__title-btn"
            data-testid="chapter-title-btn"
            title="点击修改章节标题"
            onClick={startEditTitle}
          >
            第 {chapter.number} 章 · {chapter.title ?? '（未命名）'}
            <span className="cdp-header__title-pen" aria-hidden="true">
              ✎
            </span>
          </button>
        )}
        <ChapterStatusBadge status={chapter.status} />
        <span className="cdp-spacer" />
        <span className="muted small">ID：{chapter.chapter_id}</span>
      </div>

      <ErrorBanner>{titleError}</ErrorBanner>

      <div className="panel__section">
        <div className="panel__section-title">工作流操作</div>
        <div className="wf-pipeline" data-testid="wf-pipeline">
          {stepOrder.map((action, idx) => {
            const b = buttonByAction.get(action)!;
            const state = stepStates[action];
            const avail = getButtonAvailability(b.action, {
              chapterStatus: chapter.status,
              activeRun: activeRunInfo,
            });
            const showSelect =
              !profilesCall.error && enabledProfiles.length > 0;
            const isCurrent = state === 'current';
            const dotLabel = state === 'done' ? '✓' : String(idx + 1);
            const stepNode = (
              <div
                key={b.action}
                className="wf-step"
                data-state={state}
                data-testid={`wf-step-${b.action}`}
              >
                <div className="wf-step__head">
                  <span className="wf-step__dot" data-state={state} aria-hidden="true">
                    {dotLabel}
                  </span>
                  <span className="wf-step__name">{b.title}</span>
                  <span
                    className={
                      'wf-step__state' + (isCurrent ? ' wf-step__state--current' : '')
                    }
                  >
                    {stepStateLabel[state]}
                  </span>
                </div>
                <button
                  className={'btn wf-step__btn' + (isCurrent ? ' btn--primary' : '')}
                  disabled={!avail.enabled || submitting}
                  title={avail.reason ?? undefined}
                  data-testid={`wf-btn-${b.action}`}
                  onClick={() =>
                    void onStart(b.action, {
                      model_profile_id: selectedProfile[b.action] || null,
                      fresh_write: b.action === 'write' ? writeMode === 'fresh' : undefined,
                      // 「审校」专属：勾选深度二审时透传给页面，未勾选时透传 undefined
                      // （避免误带 false 触发旧契约歧义）。
                      deep_review: b.action === 'review' ? deepReview : undefined,
                    })
                  }
                >
                  <span className="btn__title">{b.title}</span>
                  <span className="btn__hint">{b.hint}</span>
                </button>
                {showSelect ? (
                  <select
                    className="wf-step__select"
                    data-testid={`wf-model-select-${b.action}`}
                    value={selectedProfile[b.action]}
                    onChange={(e) => setProfile(b.action, e.target.value)}
                    title="选择本次运行使用的模型档案；默认走环节绑定"
                    disabled={submitting}
                  >
                    <option value="">模型：环节绑定（默认）</option>
                    {enabledProfiles.map((p) => (
                      <option key={p.profile_id} value={p.profile_id}>
                        {p.name}（{p.provider}/{p.model}）
                      </option>
                    ))}
                  </select>
                ) : null}
                {/* 「写正文」专属：写作模式（按意见改稿 / 全新重写）。小号 select
                    放在模型下拉下方；仅 write 步骤渲染；其他动作不显示。 */}
                {b.action === 'write' ? (
                  <select
                    className="wf-step__select"
                    data-testid="wf-write-mode"
                    value={writeMode}
                    onChange={(e) => setWriteMode(e.target.value === 'fresh' ? 'fresh' : '')}
                    title="全新重写忽略旧稿与改稿意见，用于不同模型文风对比"
                    disabled={submitting}
                  >
                    <option value="fresh">模式：全新重写（默认）</option>
                    <option value="">模式：按意见改稿</option>
                  </select>
                ) : null}
                {/* 「审校」专属：动态提示将审哪版,让用户在点之前就知道。
                    草稿尚未加载时(selectedDraftVersion=null)显示「暂未选择」。
                    + 深度二审（Kimi 三层清单）开关：默认不勾，勾选时 review
                    请求体带 deep_review:true，pause_payload 多一段
                    deep_review_report 分栏。 */}
                {b.action === 'review' ? (
                  <>
                    <div
                      className="wf-step__extra muted small"
                      data-testid="wf-review-target-hint"
                    >
                      将审校：草稿 v{selectedDraftVersion ?? '暂未选择'}
                    </div>
                    <label
                      className="wf-step__extra muted small cdp-review-toggle"
                      data-testid="wf-review-deep-toggle"
                      data-disabled={submitting ? 'true' : 'false'}
                    >
                      <input
                        type="checkbox"
                        data-testid="wf-review-deep-checkbox"
                        checked={deepReview}
                        disabled={submitting}
                        onChange={(e) => onDeepReviewChange(e.target.checked)}
                      />
                      深度二审（Kimi，+约1分钟）
                    </label>
                  </>
                ) : null}
              </div>
            );
            if (idx === stepOrder.length - 1) return stepNode;
            return [
              stepNode,
              <div
                key={`conn-${action}`}
                className="wf-step__connector"
                aria-hidden="true"
              />,
            ];
          })}
        </div>
        <div className="muted small cdp-hint">
          步骤按顺序推进，同一时间只能运行一个工作流；灰色步骤需先完成前置步骤，已完成的步骤在状态允许时可重跑。
        </div>
      </div>
    </div>
  );
}
