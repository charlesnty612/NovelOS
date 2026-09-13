import type { Project, ProjectInitResponse } from '../api/types';
import { InitIdleForm } from './project-init/InitIdleForm';
import { InitStepDone } from './project-init/InitStepDone';
import { ReviewPane } from './project-init/ReviewPane';
import { SuspendedResumeBanner } from './project-init/SuspendedResumeBanner';
import { useProjectInitOrchestration } from './project-init/useProjectInitOrchestration';

interface Props {
  projectId: string;
  project: Project;
  /** 初始化成功后回调（父组件重载 project 等数据）。 */
  onDone: (resp: ProjectInitResponse) => void;
}

/**
 * ProjectInitPanel —— 项目总览页「AI 初始化设定」面板（P1 project-init）。
 *
 * V4.0 前端模块化：本文件只保留「组装 + 子组件编排」。状态机 / 轮询 / PAUSED
 * resume / 数据流在 ./project-init/useProjectInitOrchestration.ts；视图拆为
 * ./project-init/ 下的子组件（InitIdleForm / ReviewPane / InitStepDone /
 * SuspendedResumeBanner / InitStepsBar / InitStageEditor / InitStepMeta /
 * InitStepModel / InitStagePicker），常量与纯函数在 projectInitCore.ts。
 *
 * 状态机（与拆分前一致）：
 *   idle（填写表单）
 *     └─ busy ─ POST /projects/init ─┬─ status=COMPLETED ─→ done
 *     │                              └─ status=PAUSED    ─→ review（4 关卡审阅）
 *     │                                                          └─ status=PAUSED ─→ review（下一关）
 *     │                                                                             ├─ COMPLETED ─→ done
 *     │                                                                             └─ FAILED/Error ─→ review（带 ErrorBanner）
 *   done（沿用 InfoBanner + onDone）
 *
 * 表单默认勾选「分步审阅生成」→ 提交时 body.step_mode=true；不勾走老的一次性模式。
 * review 视图：4 关卡全部走结构化编辑（详见 ProjectInitEditors.tsx）。
 *  - 白名单字段：专属控件 + 中文 label（premise 6 字段 / world 4 字段 / character 1 字段数组 / outline 2 字段）
 *  - 未知键：按 string/number/其他类型 自动适配（string→textarea rows=3、number→数字、其它→JSON 域 rows=4）
 *  - 数组卡片：每张卡内白名单专属控件 + 未知键规则；卡右上「删除」/ 列表尾「+ 添加一条」
 *  - 嵌套 object（protagonist / volume）：展开一层键值对
 *  - 类型不符预期：字段整体回退 JSON 文本域 rows=14
 *  - 必填 / JSON 非法 → 提交按钮 disabled
 * 「放弃本次初始化」仅本地 reset 回 idle，不调接口（后端 run 保持 PAUSED）。
 *
 * data-testid（关键）：
 *   - project-init-panel / -toggle / -form
 *   - project-init-title / -genre / -logline / -platform / -target-words
 *     / -author-notes / -chapter-seed-count
 *   - project-init-step-mode / -submit / -busy / -cancel / -progress
 *   - project-init-warning / -overwrite-confirm
 *   - review-pane（审阅视图根）
 *   - init-steps / init-steps-item-{premise|world|character|outline}
 *   - revision-premi-{stage}（当前阶段文案「第 N / 4 步 · …」）
 *   - revision-{stage}-{field}（premise / world / character / outline 字段级控件）
 *   - revision-card-{stage}-{key}-{idx}（卡片）
 *   - revision-card-add-{stage}-{key} / revision-card-remove-{stage}-{key}-{idx}
 *   - revision-json-fallback-{stage}-{key}（类型不符预期的 JSON 兜底）
 *   - revision-degraded-warning（_degraded 提示）
 *   - revision-submit / revision-submit-busy（放行按钮两种态）
 *   - init-abandon（放弃按钮）
 *   - done-result（终态展示）
 *   - stage-model-select / -status（本关模型档案切换）
 *
 * 向后兼容（测试仍在用的旧 testid）：
 *   - revision-logline / -positioning / -selling-points / -protagonist / -title / -genre
 *   - revision-volume-title
 *   - revision-core-premise
 *   - revision-card-{world|character}-rules-0-…  / -characters-0-…
 *   - revision-card-add-{stage}-{key}
 */
export function ProjectInitPanel({ projectId, project, onDone }: Props) {
  const {
    expanded,
    setExpanded,
    viewMode,
    probeErr,
    title,
    setTitle,
    genre,
    setGenre,
    logline,
    setLogline,
    platform,
    setPlatform,
    targetWords,
    authorNotes,
    setAuthorNotes,
    chapterWordCount,
    chapterSeedCount,
    seedCountManual,
    overwriteConfirmed,
    setOverwriteConfirmed,
    stepMode,
    setStepMode,
    initModelProfileId,
    setInitModelProfileId,
    handleTargetWordsChange,
    handleChapterWordCountChange,
    handleChapterSeedCountChange,
    seedCount,
    needsConfirm,
    busyAny,
    canSubmit,
    selectedStages,
    stageMetaById,
    handleToggleStage,
    busyResume,
    pausePayload,
    stageModels,
    error,
    finalResp,
    suspendedRun,
    resuming,
    showSuspendedBanner,
    bindings,
    profiles,
    handleChangeStageModel,
    handleInit,
    handleResume,
    handleRegenerate,
    handleResumeSuspended,
    handleDismissSuspended,
    reset,
  } = useProjectInitOrchestration({ projectId, project, onDone });

  return (
    <div
      className="card"
      style={{ marginBottom: 16 }}
      data-testid="project-init-panel"
    >
      <div className="detail-pane__title">AI 初始化设定</div>

      {!expanded ? (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginTop: 4,
          }}
        >
          <span className="muted small">
            根据题材 brief 自动生成前提 / 世界观 / 角色 / 卷纲与章节种子，并落库到当前项目。
          </span>
          <div style={{ flex: 1 }} />
          <button
            type="button"
            className="btn btn--sm btn--primary"
            onClick={() => setExpanded(true)}
            data-testid="project-init-toggle"
          >
            AI 初始化设定
          </button>
        </div>
      ) : (
        <div style={{ marginTop: 8 }} data-testid="project-init-form">
          {/* 挂起恢复横幅：表单之上；非 idle-form 时不渲染（已进入 review/done 视图无需重复）。 */}
          {viewMode === 'idle-form' && showSuspendedBanner ? (
            <SuspendedResumeBanner
              suspendedRun={suspendedRun!}
              busy={resuming}
              onResume={() => void handleResumeSuspended()}
              onDismiss={handleDismissSuspended}
            />
          ) : null}
          {viewMode === 'idle-form' ? (
            <InitIdleForm
              needsConfirm={needsConfirm}
              probeErr={probeErr}
              title={title}
              setTitle={setTitle}
              genre={genre}
              setGenre={setGenre}
              platform={platform}
              setPlatform={setPlatform}
              targetWords={targetWords}
              setTargetWords={handleTargetWordsChange}
              chapterWordCount={chapterWordCount}
              setChapterWordCount={handleChapterWordCountChange}
              chapterSeedCount={chapterSeedCount}
              setChapterSeedCount={handleChapterSeedCountChange}
              seedCountManual={seedCountManual}
              logline={logline}
              setLogline={setLogline}
              authorNotes={authorNotes}
              setAuthorNotes={setAuthorNotes}
              overwriteConfirmed={overwriteConfirmed}
              setOverwriteConfirmed={setOverwriteConfirmed}
              stepMode={stepMode}
              setStepMode={setStepMode}
              busy={busyAny}
              initModelProfileId={initModelProfileId}
              setInitModelProfileId={setInitModelProfileId}
              initProfiles={profiles}
              error={error}
              canSubmit={canSubmit}
              seedCount={seedCount}
              selectedStages={selectedStages}
              onToggleStage={handleToggleStage}
              stageMetaById={stageMetaById}
              onSubmit={() => void handleInit()}
              onCancel={() => {
                setExpanded(false);
                reset();
              }}
            />
          ) : viewMode === 'review' ? (
            <ReviewPane
              key={`${pausePayload!.stage}-${pausePayload!.stage_index}`}
              pausePayload={pausePayload!}
              busy={busyResume}
              error={error}
              bindings={bindings}
              profiles={profiles}
              stageModels={stageModels}
              onSubmit={handleResume}
              onRegenerate={handleRegenerate}
              onAbandon={reset}
              onChangeStageModel={handleChangeStageModel}
            />
          ) : (
            <InitStepDone finalResp={finalResp} />
          )}
        </div>
      )}
    </div>
  );
}
