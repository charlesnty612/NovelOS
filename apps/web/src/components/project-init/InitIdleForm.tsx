// project-init 子组件：idle 表单视图（校验提示 + 元信息步 + 环节勾选步 +
// 模型选择步 + 提交/收起动作）。V4.0 前端模块化批次从 ProjectInitPanel.tsx
// 原样搬出，文案、testid、按钮态不变。
import { ErrorBanner, InfoBanner } from '../ErrorBanner';
import type { ModelProfile } from '../../api/types';
import type { StageSelection } from './projectInitCore';
import { CheckboxRow } from './InitField';
import { InitStagePicker } from './InitStagePicker';
import { InitStepMeta } from './InitStepMeta';
import { InitStepModel } from './InitStepModel';

export interface InitIdleFormProps {
  needsConfirm: boolean;
  probeErr: string | null;
  title: string;
  setTitle: (v: string) => void;
  genre: string;
  setGenre: (v: string) => void;
  platform: string;
  setPlatform: (v: string) => void;
  targetWords: string;
  setTargetWords: (v: string) => void;
  chapterWordCount: string;
  setChapterWordCount: (v: string) => void;
  chapterSeedCount: string;
  setChapterSeedCount: (v: string) => void;
  /** 用户是否手动改过章节种子数（true 时改单章字数/目标字数不再自动重算）。 */
  seedCountManual: boolean;
  logline: string;
  setLogline: (v: string) => void;
  authorNotes: string;
  setAuthorNotes: (v: string) => void;
  overwriteConfirmed: boolean;
  setOverwriteConfirmed: (v: boolean) => void;
  stepMode: boolean;
  setStepMode: (v: boolean) => void;
  busy: boolean;
  error: string | null;
  canSubmit: boolean;
  seedCount: number | null;
  /** 本次初始化模型档案：'' = 走全局 capability_bindings。 */
  initModelProfileId: string;
  setInitModelProfileId: (v: string) => void;
  /** 已启用的 model_profiles 列表（驱动下拉 options）。 */
  initProfiles: ModelProfile[];
  /** 环节勾选：当前选中的 stage id 列表；onToggleStage 处理单关勾选切换。 */
  selectedStages: string[];
  onToggleStage: (stage: string, next: boolean) => void;
  /** 环节元数据：从 init-status 解析（或失败兜底），用于渲染徽标 / 强制勾选态。 */
  stageMetaById: Record<string, StageSelection>;
  onSubmit: () => void;
  onCancel: () => void;
}

export function InitIdleForm(props: InitIdleFormProps) {
  const {
    needsConfirm,
    probeErr,
    title,
    setTitle,
    genre,
    setGenre,
    platform,
    setPlatform,
    targetWords,
    setTargetWords,
    chapterWordCount,
    setChapterWordCount,
    chapterSeedCount,
    setChapterSeedCount,
    seedCountManual,
    logline,
    setLogline,
    authorNotes,
    setAuthorNotes,
    overwriteConfirmed,
    setOverwriteConfirmed,
    stepMode,
    setStepMode,
    busy,
    error,
    canSubmit,
    initModelProfileId,
    setInitModelProfileId,
    initProfiles,
    selectedStages,
    onToggleStage,
    stageMetaById,
    onSubmit,
    onCancel,
  } = props;

  const regeneratingExisting = selectedStages
    .map((s) => stageMetaById[s])
    .filter((m) => m?.done)
    .map((m) => m!.label);
  const confirmText = regeneratingExisting.length
    ? `检测到已勾选重新生成已有设定的环节（${regeneratingExisting.join('、')}），将覆盖该环节既有内容，可能造成重复。`
    : null;

  return (
    <>
      {needsConfirm && confirmText ? (
        <div data-testid="project-init-warning">
          <InfoBanner>{confirmText}</InfoBanner>
        </div>
      ) : null}
      {probeErr ? (
        <div className="muted small" style={{ marginBottom: 6 }}>
          探测既有数据失败（{probeErr}），已按"无既有数据"处理。
        </div>
      ) : null}

      <ErrorBanner>{error}</ErrorBanner>

      <InitStepMeta
        title={title}
        setTitle={setTitle}
        genre={genre}
        setGenre={setGenre}
        platform={platform}
        setPlatform={setPlatform}
        targetWords={targetWords}
        setTargetWords={setTargetWords}
        chapterWordCount={chapterWordCount}
        setChapterWordCount={setChapterWordCount}
        chapterSeedCount={chapterSeedCount}
        setChapterSeedCount={setChapterSeedCount}
        seedCountManual={seedCountManual}
        logline={logline}
        setLogline={setLogline}
        authorNotes={authorNotes}
        setAuthorNotes={setAuthorNotes}
        busy={busy}
      />

      <CheckboxRow
        testid="project-init-step-mode"
        checkboxTestid="project-init-step-mode-checkbox"
        checked={stepMode}
        onChange={(e) => setStepMode(e.target.checked)}
        disabled={busy}
      >
        分步审阅生成（推荐）：每完成一关暂停等待人工修订后再继续；关闭后将一次性生成全部设定。
      </CheckboxRow>

      {/* 本次生成的环节：基于 init-status 的阶段状态徽标 + 多选。 */}
      <InitStagePicker
        selectedStages={selectedStages}
        onToggleStage={onToggleStage}
        stageMetaById={stageMetaById}
        busy={busy}
      />

      {needsConfirm ? (
        <CheckboxRow
          testid="project-init-overwrite-confirm"
          checkboxTestid="project-init-overwrite-checkbox"
          checked={overwriteConfirmed}
          onChange={(e) => setOverwriteConfirmed(e.target.checked)}
          disabled={busy}
        >
          我已知晓：初始化将以追加方式生成新内容，可能与既有数据重复。
        </CheckboxRow>
      ) : null}

      {/* 本次初始化模型档案覆盖：'' = 走全局 capability_bindings / model_configs
          （默认）；非空 = model_profiles.profile_id。本次 run 全部 4 个 AI 节点
          统一使用该档案调用 LLM；不影响其他 run 与全局 binding。 */}
      <InitStepModel
        initModelProfileId={initModelProfileId}
        setInitModelProfileId={setInitModelProfileId}
        initProfiles={initProfiles}
        busy={busy}
      />

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginTop: 10,
        }}
      >
        <button
          type="button"
          className="btn btn--sm btn--primary"
          disabled={!canSubmit}
          onClick={onSubmit}
          data-testid={busy ? 'project-init-busy' : 'project-init-submit'}
        >
          {busy ? '生成中…' : '开始 AI 初始化'}
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={onCancel}
          disabled={busy}
          data-testid="project-init-cancel"
        >
          收起
        </button>
        {busy ? (
          <span className="muted small" data-testid="project-init-progress">
            正在生成设定（{selectedStages.map((s) => stageMetaById[s]?.label ?? s).join(' → ')}），通常需要数分钟，请勿关闭页面。
          </span>
        ) : null}
      </div>
    </>
  );
}
