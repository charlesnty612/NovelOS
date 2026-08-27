import type { Dispatch, SetStateAction } from 'react';
import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import {
  capabilityBindingsApi,
  charactersApi,
  modelProfilesApi,
  projectsApi,
  workflowsApi,
} from '../api/endpoints';
import type {
  CapabilityBinding,
  ModelProfile,
  Project,
  ProjectInitBrief,
  ProjectInitPausePayload,
  ProjectInitPayload,
  ProjectInitResponse,
} from '../api/types';
import { ErrorBanner, InfoBanner } from './ErrorBanner';

interface Props {
  projectId: string;
  project: Project;
  /** 初始化成功后回调（父组件重载 project 等数据）。 */
  onDone: (resp: ProjectInitResponse) => void;
}

const DEFAULT_PLATFORM = '番茄·男频';
const DEFAULT_CHAPTER_SEED_COUNT = 10;
const CHAPTER_SEED_COUNT_MIN = 1;
const CHAPTER_SEED_COUNT_MAX = 100;
const DETAIL_TRUNCATE = 200;

// ---- 分步审阅向导配置 ------------------------------------------------------
// 4 关卡 → 中文名 / output_key（与后端 pause_payload.stage / revisions key 对齐）。
const STAGE_LABELS: Array<{
  stage: string;
  outputKey: string;
  label: string;
  capability: string;
}> = [
  { stage: 'premise', outputKey: 'premise_output', label: '题材定位', capability: 'premise_design' },
  { stage: 'world', outputKey: 'world_output', label: '世界观', capability: 'world_building' },
  { stage: 'character', outputKey: 'character_output', label: '核心角色', capability: 'character_design' },
  { stage: 'outline', outputKey: 'outline_output', label: '卷纲与章节种子', capability: 'volume_outline' },
];

const STAGES_TOTAL = STAGE_LABELS.length;

type PanelState =
  | 'idle'
  | 'busy'
  | 'review'
  | 'busy-resume'
  | 'done';

type PanelMode = 'idle-form' | 'review' | 'done';

/**
 * 解析表单中的 chapter_seed_count 字符串。
 * - 空 / NaN / < 1 / > 100 视为无效（返回 null）。
 * - 与后端 ProjectInitRequest.chapter_seed_count 的 Field(ge=1, le=100) 对齐，
 *   让前端校验在 Pydantic 422 之前先拦截。
 */
function parseSeedCount(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const n = Number.parseInt(trimmed, 10);
  if (!Number.isFinite(n)) return null;
  if (n < CHAPTER_SEED_COUNT_MIN || n > CHAPTER_SEED_COUNT_MAX) return null;
  return n;
}

/**
 * 安全地把任意值规整成 Record<string, unknown>（用于编辑态初始值）。
 * 后端 draft 是超集；非对象 → {}。
 */
function asRecord(v: unknown): Record<string, unknown> {
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    return v as Record<string, unknown>;
  }
  return {};
}

function asString(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback;
}

function asStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
}

/**
 * ProjectInitPanel —— 项目总览页「AI 初始化设定」面板（P1 project-init）。
 *
 * 状态机：
 *   idle（填写表单）
 *     └─ busy ─ POST /projects/init ─┬─ status=COMPLETED ─→ done
 *     │                              └─ status=PAUSED    ─→ review（4 关卡审阅）
 *     │                                                          └─ busy-resume ─┬─ PAUSED ─→ review（下一关）
 *     │                                                                             ├─ COMPLETED ─→ done
 *     │                                                                             └─ FAILED/Error ─→ review（带 ErrorBanner）
 *   done（沿用 InfoBanner + onDone）
 *
 * 表单默认勾选「分步审阅生成」→ 提交时 body.step_mode=true；不勾走老的一次性模式。
 * review 视图：premise 走结构化表单（title/genre/logline/positioning/selling_points/protagonist），
 * world / character / outline 走 JSON 编辑（outline 例外：volume.title 拆为单独输入框，
 * 其余仍走 JSON）。JSON 非法 → 提交按钮 disabled + 红字提示。
 * 「放弃本次初始化」仅本地 reset 回 idle，不调接口（后端 run 保持 PAUSED）。
 *
 * data-testid：
 *   - project-init-panel / -toggle / -form
 *   - title / genre / logline / platform / target-words / author-notes / chapter-seed-count
 *   - overwrite-confirm / -warning
 *   - submit / -busy / -progress
 *   - step-mode（新增：分步审阅 checkbox）
 *   - review-pane（新增：审阅视图根）
 *   - init-steps（新增：4 步进度条根）
 *   - init-steps-item-{premise|world|character|outline}（新增：步骤单元）
 *   - revision-premi-{stage}（新增：审阅视图当前阶段文案）
 *   - revision-logline / revision-positioning / revision-selling-points
 *     / revision-protagonist / revision-volume-title（premise 字段级）
 *   - revision-json-{premise|world|character|outline}（JSON 文本域 + outline 的非 title 字段）
 *   - revision-json-error-{stage}（JSON 非法提示）
 *   - revision-degraded-warning（_degraded 提示）
 *   - revision-submit / revision-submit-busy（放行按钮两种态）
 *   - init-abandon（放弃按钮）
 *   - done-result（终态展示——复用原 -result 语义）
 *   - stage-model-select（新增：每关顶部选择本关模型档案）
 *   - stage-model-status（切换后的「下一关起生效 ✓」/ 失败红字）
 */
export function ProjectInitPanel({ projectId, project, onDone }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [existingChars, setExistingChars] = useState<number | null>(null);
  const [probeErr, setProbeErr] = useState<string | null>(null);

  // 表单初值
  const [title, setTitle] = useState<string>(project.name ?? '');
  const [genre, setGenre] = useState<string>(project.genre ?? '');
  const [logline, setLogline] = useState<string>('');
  const [platform, setPlatform] = useState<string>(DEFAULT_PLATFORM);
  const [targetWords, setTargetWords] = useState<string>(
    project.target_words != null ? String(project.target_words) : '',
  );
  const [authorNotes, setAuthorNotes] = useState<string>('');
  const [chapterSeedCount, setChapterSeedCount] = useState<string>(
    String(DEFAULT_CHAPTER_SEED_COUNT),
  );
  const [overwriteConfirmed, setOverwriteConfirmed] = useState(false);
  const [stepMode, setStepMode] = useState<boolean>(true);

  // 工作流运行态
  const [panelState, setPanelState] = useState<PanelState>('idle');
  const [runId, setRunId] = useState<string | null>(null);
  const [pausePayload, setPausePayload] = useState<ProjectInitPausePayload | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [finalResp, setFinalResp] = useState<ProjectInitResponse | null>(null);

  // 环节绑定：进入 review 视图时按需拉一次；本地缓存供 stage-model-select 使用。
  const [bindings, setBindings] = useState<CapabilityBinding[]>([]);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [bindingsLoaded, setBindingsLoaded] = useState(false);

  const seedCount = parseSeedCount(chapterSeedCount);
  const needsConfirm =
    (existingChars ?? 0) > 0; // worldsApi 在 endpoints 中不存在，仅以角色数兜底
  const busyAny = panelState === 'busy' || panelState === 'busy-resume';
  const canSubmit =
    !busyAny &&
    logline.trim().length > 0 &&
    seedCount !== null &&
    (!needsConfirm || overwriteConfirmed);

  // 首次展开时探测既有数据（best-effort：失败不阻塞面板）。
  useEffect(() => {
    if (!expanded || existingChars !== null) return;
    setProbeErr(null);
    charactersApi
      .listByProject(projectId)
      .then((rows) => setExistingChars(rows.length))
      .catch((e: unknown) => {
        if (e instanceof Error) setProbeErr(e.message);
        setExistingChars(0);
      });
  }, [expanded, existingChars, projectId]);

  // ---------------- helpers ---------------------------------------------

  const reset = () => {
    setLogline('');
    setAuthorNotes('');
    setOverwriteConfirmed(false);
    setError(null);
    setFinalResp(null);
    setRunId(null);
    setPausePayload(null);
    setPanelState('idle');
  };

  /**
   * 进入 review 视图时按需拉一次 bindings + profile 清单。
   * - 失败 best-effort：不清空已有缓存；UI 仍可继续。
   * - 首次切换进入 review 即触发，之后无论阶段切换均复用本地缓存。
   */
  const ensureBindingsLoaded = () => {
    if (bindingsLoaded) return;
    setBindingsLoaded(true);
    Promise.all([
      capabilityBindingsApi.list().catch(() => [] as CapabilityBinding[]),
      modelProfilesApi.list().catch(() => [] as ModelProfile[]),
    ]).then(([bs, ps]) => {
      setBindings(bs);
      setProfiles(ps);
    });
  };

  const renderApiError = (e: unknown): string => {
    if (e instanceof ApiError) {
      const detail =
        e.detail.length > DETAIL_TRUNCATE
          ? `${e.detail.slice(0, DETAIL_TRUNCATE)}…`
          : e.detail;
      return `${e.status}: ${detail}`;
    }
    if (e instanceof Error) return e.message;
    return '初始化失败';
  };

  // ---------------- idle：提交 init -------------------------------------

  const handleInit = async () => {
    if (!canSubmit) return;
    setError(null);
    setFinalResp(null);
    setPausePayload(null);
    const trimmedTitle = title.trim();
    const trimmedGenre = genre.trim();
    const trimmedLogline = logline.trim();
    const trimmedPlatform = platform.trim() || DEFAULT_PLATFORM;
    const trimmedAuthorNotes = authorNotes.trim();
    const tw = targetWords.trim();

    const brief: ProjectInitBrief = {
      title: trimmedTitle,
      genre: trimmedGenre,
      logline: trimmedLogline,
      platform: trimmedPlatform,
    };
    if (tw) {
      const n = Number.parseInt(tw, 10);
      if (Number.isFinite(n) && n > 0) brief.target_words = n;
    }
    if (trimmedAuthorNotes) brief.author_notes = trimmedAuthorNotes;

    const payload: ProjectInitPayload = {
      brief,
      project_id: projectId,
      chapter_seed_count: seedCount ?? DEFAULT_CHAPTER_SEED_COUNT,
    };
    if (stepMode) payload.step_mode = true;

    setPanelState('busy');
    try {
      const resp = await projectsApi.init(payload);
      setRunId(resp.run_id);
      if (resp.status === 'PAUSED' && resp.pause_payload) {
        setPausePayload(resp.pause_payload);
        setPanelState('review');
        ensureBindingsLoaded();
      } else {
        // COMPLETED / FAILED（无 pause_payload）→ 一次性模式终态
        setFinalResp(resp);
        if (resp.status === 'COMPLETED') {
          setPanelState('done');
          onDone(resp);
        } else {
          // FAILED：保留在当前面板，暴露 ErrorBanner 后让用户决定下一步
          setPanelState('idle');
          setError(
            `run 终态异常：status=${resp.status}` +
              (resp.current_node ? `, current_node=${resp.current_node}` : ''),
          );
        }
      }
    } catch (e: unknown) {
      setPanelState('idle');
      setError(renderApiError(e));
    }
  };

  // ---------------- review：放行 resume ---------------------------------

  const handleResume = (parsed: Record<string, unknown>) => {
    if (!runId || !pausePayload) return;
    const stageMeta = STAGE_LABELS.find((s) => s.stage === pausePayload.stage);
    if (!stageMeta) return;
    setError(null);
    setPanelState('busy-resume');
    workflowsApi
      .resumeInit(runId, {
        human_input: { revisions: { [stageMeta.outputKey]: parsed } },
      })
      .then((resp) => {
        if (resp.status === 'PAUSED' && resp.pause_payload) {
          setPausePayload(resp.pause_payload);
          setPanelState('review');
          ensureBindingsLoaded();
        } else if (resp.status === 'COMPLETED') {
          setFinalResp(resp);
          setPausePayload(null);
          setPanelState('done');
          onDone(resp);
        } else {
          // 其他状态（含 FAILED）：保留在 review，由 ErrorBanner 展示
          setPanelState('review');
          setError(
            `run 终态异常：status=${resp.status}` +
              (resp.current_node ? `, current_node=${resp.current_node}` : ''),
          );
        }
      })
      .catch((e: unknown) => {
        setPanelState('review');
        setError(renderApiError(e));
      });
  };

  // ---------------- 视图分发 -------------------------------------------

  const viewMode: PanelMode =
    panelState === 'done'
      ? 'done'
      : panelState === 'review' || panelState === 'busy-resume'
        ? 'review'
        : 'idle-form';

  return (
    <div
      className="card"
      style={{ marginBottom: 16 }}
      data-testid="project-init-panel"
    >
      <div className="detail-pane__title">AI 初始化设定（P1 project-init）</div>

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
          {viewMode === 'idle-form' ? (
            <IdleForm
              needsConfirm={needsConfirm}
              existingChars={existingChars}
              probeErr={probeErr}
              title={title}
              setTitle={setTitle}
              genre={genre}
              setGenre={setGenre}
              platform={platform}
              setPlatform={setPlatform}
              targetWords={targetWords}
              setTargetWords={setTargetWords}
              chapterSeedCount={chapterSeedCount}
              setChapterSeedCount={setChapterSeedCount}
              logline={logline}
              setLogline={setLogline}
              authorNotes={authorNotes}
              setAuthorNotes={setAuthorNotes}
              overwriteConfirmed={overwriteConfirmed}
              setOverwriteConfirmed={setOverwriteConfirmed}
              stepMode={stepMode}
              setStepMode={setStepMode}
              busy={busyAny}
              error={error}
              canSubmit={canSubmit}
              seedCount={seedCount}
              onSubmit={() => void handleInit()}
              onCancel={() => {
                setExpanded(false);
                reset();
              }}
            />
          ) : viewMode === 'review' ? (
            <ReviewPane
              pausePayload={pausePayload!}
              busy={panelState === 'busy-resume'}
              error={error}
              bindings={bindings}
              profiles={profiles}
              onSubmit={handleResume}
              onAbandon={reset}
              onChangeStageModel={async (capability, profileId) => {
                if (profileId === '') {
                  await capabilityBindingsApi.unbind(capability);
                } else {
                  await capabilityBindingsApi.bind(capability, [profileId]);
                }
                // 本地缓存立即更新，避免再发 GET
                setBindings((prev) =>
                  prev.map((b) =>
                    b.capability === capability
                      ? {
                          ...b,
                          profile_ids: profileId === '' ? [] : [profileId],
                          profiles: profileId === ''
                            ? []
                            : profiles
                                .filter((p) => p.profile_id === profileId)
                                .map((p) => ({
                                  profile_id: p.profile_id,
                                  name: p.name,
                                  model: p.model,
                                })),
                        }
                      : b,
                  ),
                );
              }}
            />
          ) : (
            <DonePane finalResp={finalResp} />
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// 子组件：idle 表单
// ============================================================================
interface IdleFormProps {
  needsConfirm: boolean;
  existingChars: number | null;
  probeErr: string | null;
  title: string;
  setTitle: (v: string) => void;
  genre: string;
  setGenre: (v: string) => void;
  platform: string;
  setPlatform: (v: string) => void;
  targetWords: string;
  setTargetWords: (v: string) => void;
  chapterSeedCount: string;
  setChapterSeedCount: (v: string) => void;
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
  onSubmit: () => void;
  onCancel: () => void;
}

function IdleForm(props: IdleFormProps) {
  const {
    needsConfirm,
    existingChars,
    probeErr,
    title,
    setTitle,
    genre,
    setGenre,
    platform,
    setPlatform,
    targetWords,
    setTargetWords,
    chapterSeedCount,
    setChapterSeedCount,
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
    onSubmit,
    onCancel,
  } = props;

  return (
    <>
      {needsConfirm ? (
        <div data-testid="project-init-warning">
          <InfoBanner>
            检测到项目已有设定数据（角色 {existingChars ?? 0}），重新初始化将追加生成内容可能造成重复。
          </InfoBanner>
        </div>
      ) : null}
      {probeErr ? (
        <div className="muted small" style={{ marginBottom: 6 }}>
          探测既有数据失败（{probeErr}），已按"无既有数据"处理。
        </div>
      ) : null}

      <ErrorBanner>{error}</ErrorBanner>

      <div className="form-grid" style={{ marginTop: 4 }}>
        <Field label="标题">
          <input
            className="input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            data-testid="project-init-title"
            disabled={busy}
          />
        </Field>
        <Field label="题材">
          <input
            className="input"
            value={genre}
            onChange={(e) => setGenre(e.target.value)}
            data-testid="project-init-genre"
            disabled={busy}
          />
        </Field>
        <Field label="平台" hint="默认「番茄·男频」">
          <input
            className="input"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            data-testid="project-init-platform"
            disabled={busy}
          />
        </Field>
        <Field label="目标字数">
          <input
            className="input"
            inputMode="numeric"
            value={targetWords}
            onChange={(e) => setTargetWords(e.target.value)}
            data-testid="project-init-target-words"
            disabled={busy}
          />
        </Field>
        <Field label="章节种子数" hint="1-100，默认 10">
          <input
            className="input"
            inputMode="numeric"
            value={chapterSeedCount}
            onChange={(e) => setChapterSeedCount(e.target.value)}
            data-testid="project-init-chapter-seed-count"
            disabled={busy}
          />
        </Field>
      </div>

      <Field label="一句话简介（必填）" hint="驱动 premise_designer 节点">
        <textarea
          className="input"
          rows={3}
          value={logline}
          onChange={(e) => setLogline(e.target.value)}
          data-testid="project-init-logline"
          disabled={busy}
          style={{ width: '100%', resize: 'vertical' }}
        />
      </Field>

      <Field label="作者备注（可选）">
        <textarea
          className="input"
          rows={2}
          value={authorNotes}
          onChange={(e) => setAuthorNotes(e.target.value)}
          data-testid="project-init-author-notes"
          disabled={busy}
          style={{ width: '100%', resize: 'vertical' }}
        />
      </Field>

      <label
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          marginTop: 6,
        }}
        data-testid="project-init-step-mode"
      >
        <input
          type="checkbox"
          checked={stepMode}
          onChange={(e) => setStepMode(e.target.checked)}
          disabled={busy}
          data-testid="project-init-step-mode-checkbox"
        />
        <span className="small">
          分步审阅生成（推荐）：每完成一关暂停等待人工修订后再继续；关闭后将一次性生成全部设定。
        </span>
      </label>

      {needsConfirm ? (
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            marginTop: 6,
          }}
          data-testid="project-init-overwrite-confirm"
        >
          <input
            type="checkbox"
            checked={overwriteConfirmed}
            onChange={(e) => setOverwriteConfirmed(e.target.checked)}
            disabled={busy}
            data-testid="project-init-overwrite-checkbox"
          />
          <span className="small">
            我已知晓：初始化将以追加方式生成新内容，可能与既有数据重复。
          </span>
        </label>
      ) : null}

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
            正在生成设定（题材定位 → 世界观 → 角色 → 卷纲/章节种子），通常需要数分钟，请勿关闭页面。
          </span>
        ) : null}
      </div>
    </>
  );
}

// ============================================================================
// 子组件：review 视图（4 关卡审阅 + 放行 / 放弃）
// ============================================================================
interface ReviewPaneProps {
  pausePayload: ProjectInitPausePayload;
  busy: boolean;
  error: string | null;
  bindings: CapabilityBinding[];
  profiles: ModelProfile[];
  onSubmit: (parsed: Record<string, unknown>) => void;
  onAbandon: () => void;
  onChangeStageModel: (capability: string, profileId: string) => Promise<void>;
}

function ReviewPane({
  pausePayload,
  busy,
  error,
  bindings,
  profiles,
  onSubmit,
  onAbandon,
  onChangeStageModel,
}: ReviewPaneProps) {
  const stageMeta =
    STAGE_LABELS.find((s) => s.stage === pausePayload.stage) ?? STAGE_LABELS[0]!;
  const stepIndex = Math.min(
    Math.max(pausePayload.stage_index ?? 0, 0),
    STAGES_TOTAL - 1,
  );
  const stepNo = stepIndex + 1;
  const degraded = pausePayload.degraded === true;
  const draft = asRecord(pausePayload.draft);

  // 绑定状态：当前 capability 的 profile_id（取第一项；后端允许数组 → 多档选一个）
  const currentBinding = bindings.find((b) => b.capability === stageMeta.capability);
  const currentProfileId = currentBinding?.profile_ids[0] ?? '';
  const enabledProfiles = profiles.filter((p) => Number(p.enabled) === 1);
  const [modelMsg, setModelMsg] = useState<{
    kind: 'ok' | 'error';
    text: string;
  } | null>(null);

  const handleModelChange = async (profileId: string): Promise<void> => {
    setModelMsg(null);
    try {
      await onChangeStageModel(stageMeta.capability, profileId);
      setModelMsg({
        kind: 'ok',
        text: profileId === '' ? '下一关起生效 · 未绑定（默认配置）' : '下一关起生效 ✓',
      });
    } catch (e: unknown) {
      const msg = e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
      setModelMsg({ kind: 'error', text: `保存失败：${msg}` });
    }
  };

  // 用本地 state 维护编辑缓冲；提交时才回写到后端（避免每次按键打 API）。
  const [jsonText, setJsonText] = useState<string>(() =>
    JSON.stringify(draft, null, 2),
  );
  const [premise, setPremise] = useState(() => ({
    title: asString(draft.title, ''),
    genre: asString(draft.genre, ''),
    logline: asString(draft.logline, ''),
    positioning: asString(draft.positioning, ''),
    selling_points: asStringArray(draft.selling_points).join('\n'),
    protagonist: JSON.stringify(asRecord(draft.protagonist), null, 2),
  }));
  const [outlineTitle, setOutlineTitle] = useState<string>(() => {
    const v = asRecord(draft.volume);
    return asString(v.title, '');
  });
  // outline 非 title 部分的 JSON 缓冲
  const [outlineRestJson, setOutlineRestJson] = useState<string>(() => {
    const v = { ...asRecord(draft.volume) };
    delete v.title;
    const rest = { volume: v, chapter_seeds: draft.chapter_seeds ?? [] };
    return JSON.stringify(rest, null, 2);
  });
  // 当前实现走本地 validateAndCompose，未单独持久化 JSON 错误到 state；
// 后续如需受控错误映射可在此处加 useState。

  // ---- 校验：JSON 文本域合法性 + premise 必填 ---------------------------
  const validateAndCompose = (): {
    ok: boolean;
    parsed: Record<string, unknown> | null;
    errors: Record<string, string>;
  } => {
    const errors: Record<string, string> = {};

    let protagonistObj: Record<string, unknown> = {};
    try {
      const p = JSON.parse(premise.protagonist || '{}');
      if (p && typeof p === 'object' && !Array.isArray(p)) {
        protagonistObj = p as Record<string, unknown>;
      } else {
        errors.protagonist = '主角 JSON 必须是对象';
      }
    } catch {
      errors.protagonist = '主角 JSON 解析失败';
    }

    let composed: Record<string, unknown> = {};
    if (stageMeta.stage === 'premise') {
      composed = {
        title: premise.title,
        genre: premise.genre,
        logline: premise.logline,
        positioning: premise.positioning,
        selling_points: premise.selling_points
          .split('\n')
          .map((s) => s.trim())
          .filter((s) => s.length > 0),
        protagonist: protagonistObj,
      };
      // premise.logline 永远必填；其他字段允许空字符串透传
      if (premise.logline.trim().length === 0) {
        errors.premiseLogline = '一句话简介必填';
      }
    } else if (stageMeta.stage === 'outline') {
      // outline：volume.title 走输入框，其余走 JSON
      let rest: Record<string, unknown> = {};
      try {
        const r = JSON.parse(outlineRestJson || '{}');
        if (r && typeof r === 'object' && !Array.isArray(r)) {
          rest = r as Record<string, unknown>;
        } else {
          errors.outlineRest = '卷纲 JSON 必须是对象';
        }
      } catch {
        errors.outlineRest = '卷纲 JSON 解析失败';
      }
      const restVolume = asRecord(rest.volume);
      composed = {
        ...rest,
        volume: { ...restVolume, title: outlineTitle },
        chapter_seeds: rest.chapter_seeds ?? [],
      };
    } else {
      // world / character：整段 JSON
      try {
        const p = JSON.parse(jsonText || '{}');
        if (p && typeof p === 'object' && !Array.isArray(p)) {
          composed = p as Record<string, unknown>;
        } else {
          errors[stageMeta.stage] = '草稿 JSON 必须是对象';
        }
      } catch {
        errors[stageMeta.stage] = '草稿 JSON 解析失败';
      }
      // world.core_premise 必填（仅在 _degraded 时强校验）
      if (degraded && stageMeta.stage === 'world') {
        const cp = composed.core_premise;
        if (typeof cp !== 'string' || cp.trim().length === 0) {
          errors.worldCorePremise = 'core_premise 必填（AI 降级，请人工补全）';
        }
      }
    }

    return {
      ok: Object.keys(errors).length === 0,
      parsed: composed,
      errors,
    };
  };

  const { ok, parsed, errors } = validateAndCompose();
  const canSubmit = ok && !busy;

  const handleClickSubmit = () => {
    if (!ok || !parsed) return;
    onSubmit(parsed);
  };

  return (
    <div data-testid="review-pane">
      <ErrorBanner>{error}</ErrorBanner>

      <StepsBar currentIndex={stepIndex} />

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginTop: 6,
          flexWrap: 'wrap',
        }}
      >
        <span className="muted small">本关模型：</span>
        <select
          value={currentProfileId}
          onChange={(e) => void handleModelChange(e.target.value)}
          disabled={busy}
          data-testid="stage-model-select"
          style={{ minWidth: 240 }}
        >
          <option value="">默认配置（未绑定）</option>
          {enabledProfiles.map((p) => (
            <option key={p.profile_id} value={p.profile_id}>
              {p.name}（{p.model}）
            </option>
          ))}
        </select>
        {modelMsg ? (
          <span
            className="small"
            style={{
              color: modelMsg.kind === 'ok' ? 'seagreen' : 'crimson',
            }}
            data-testid="stage-model-status"
          >
            {modelMsg.text}
          </span>
        ) : null}
      </div>

      <div
        className="muted small"
        style={{ marginTop: 6 }}
        data-testid={`revision-premi-${stageMeta.stage}`}
      >
        第 {stepNo} / {STAGES_TOTAL} 步 · 人工审阅 · 当前关卡：{stageMeta.label}
      </div>

      {degraded ? (
        <div
          className="alert alert--warning"
          role="status"
          style={{ marginTop: 6 }}
          data-testid="revision-degraded-warning"
        >
          该环节 AI 生成降级，请人工补全后再放行。
        </div>
      ) : null}

      <div style={{ marginTop: 8 }}>
        {stageMeta.stage === 'premise' ? (
          <PremiseEditor
            premise={premise}
            setPremise={setPremise}
            errors={errors}
            busy={busy}
          />
        ) : stageMeta.stage === 'outline' ? (
          <OutlineEditor
            outlineTitle={outlineTitle}
            setOutlineTitle={setOutlineTitle}
            outlineRestJson={outlineRestJson}
            setOutlineRestJson={setOutlineRestJson}
            errors={errors}
            busy={busy}
          />
        ) : (
          <JsonField
            stage={stageMeta.stage}
            value={jsonText}
            onChange={setJsonText}
            error={null}
            busy={busy}
          />
        )}
      </div>

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
          onClick={handleClickSubmit}
          data-testid={busy ? 'revision-submit-busy' : 'revision-submit'}
        >
          {busy ? '提交修订中…' : '确认修订并继续'}
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={onAbandon}
          disabled={busy}
          data-testid="init-abandon"
        >
          放弃本次初始化
        </button>
        {!ok ? (
          <span className="muted small">
            请先修正高亮字段（必填或 JSON 非法）再放行。
          </span>
        ) : null}
      </div>

      <div className="muted small" style={{ marginTop: 6 }}>
        放弃本次初始化仅本地重置，后端 run 仍保持 PAUSED，不影响数据；后续可在项目总览页重新发起。
      </div>
    </div>
  );
}

// ============================================================================
// 子组件：步骤条
// ============================================================================
function StepsBar({ currentIndex }: { currentIndex: number }) {
  return (
    <div
      style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}
      data-testid="init-steps"
    >
      {STAGE_LABELS.map((s, i) => {
        const done = i < currentIndex;
        const active = i === currentIndex;
        return (
          <div
            key={s.stage}
            data-testid={`init-steps-item-${s.stage}`}
            style={{
              padding: '2px 8px',
              borderRadius: 4,
              border: '1px solid #888',
              opacity: done || active ? 1 : 0.45,
              fontSize: 12,
              background: active ? 'var(--color-accent, #eef)' : undefined,
            }}
          >
            {done ? '✓ ' : active ? '● ' : ''}
            {s.label}
          </div>
        );
      })}
    </div>
  );
}

// ============================================================================
// 子组件：premise 结构化编辑
// ============================================================================
interface PremiseDraft {
  title: string;
  genre: string;
  logline: string;
  positioning: string;
  selling_points: string;
  protagonist: string;
}

function PremiseEditor({
  premise,
  setPremise,
  errors,
  busy,
}: {
  premise: PremiseDraft;
  setPremise: Dispatch<SetStateAction<PremiseDraft>>;
  errors: Record<string, string>;
  busy: boolean;
}) {
  return (
    <>
      <Field label="标题">
        <input
          className="input"
          value={premise.title}
          onChange={(e) =>
            setPremise((prev) => ({ ...prev, title: e.target.value }))
          }
          disabled={busy}
          data-testid="revision-title"
        />
      </Field>
      <Field label="题材">
        <input
          className="input"
          value={premise.genre}
          onChange={(e) =>
            setPremise((prev) => ({ ...prev, genre: e.target.value }))
          }
          disabled={busy}
          data-testid="revision-genre"
        />
      </Field>
      <Field label="一句话简介（必填）">
        <textarea
          className="input"
          rows={2}
          value={premise.logline}
          onChange={(e) =>
            setPremise((prev) => ({ ...prev, logline: e.target.value }))
          }
          disabled={busy}
          data-testid="revision-logline"
          style={{ width: '100%', resize: 'vertical' }}
        />
        {errors.premiseLogline ? (
          <div className="small" style={{ color: 'crimson' }}>
            {errors.premiseLogline}
          </div>
        ) : null}
      </Field>
      <Field label="定位">
        <textarea
          className="input"
          rows={2}
          value={premise.positioning}
          onChange={(e) =>
            setPremise((prev) => ({ ...prev, positioning: e.target.value }))
          }
          disabled={busy}
          data-testid="revision-positioning"
          style={{ width: '100%', resize: 'vertical' }}
        />
      </Field>
      <Field label="卖点（每行一条）">
        <textarea
          className="input"
          rows={3}
          value={premise.selling_points}
          onChange={(e) =>
            setPremise((prev) => ({ ...prev, selling_points: e.target.value }))
          }
          disabled={busy}
          data-testid="revision-selling-points"
          style={{ width: '100%', resize: 'vertical' }}
        />
      </Field>
      <Field label="主角（JSON 对象）">
        <textarea
          className="input"
          rows={4}
          value={premise.protagonist}
          onChange={(e) =>
            setPremise((prev) => ({ ...prev, protagonist: e.target.value }))
          }
          disabled={busy}
          data-testid="revision-protagonist"
          style={{ width: '100%', resize: 'vertical', fontFamily: 'monospace' }}
        />
        {errors.protagonist ? (
          <div
            className="small"
            style={{ color: 'crimson' }}
            data-testid="revision-protagonist-error"
          >
            {errors.protagonist}
          </div>
        ) : null}
      </Field>
    </>
  );
}

// ============================================================================
// 子组件：outline 编辑（volume.title 拆出 + 其余 JSON）
// ============================================================================
function OutlineEditor({
  outlineTitle,
  setOutlineTitle,
  outlineRestJson,
  setOutlineRestJson,
  errors,
  busy,
}: {
  outlineTitle: string;
  setOutlineTitle: (v: string) => void;
  outlineRestJson: string;
  setOutlineRestJson: (v: string) => void;
  errors: Record<string, string>;
  busy: boolean;
}) {
  return (
    <>
      <Field label="卷标题">
        <input
          className="input"
          value={outlineTitle}
          onChange={(e) => setOutlineTitle(e.target.value)}
          disabled={busy}
          data-testid="revision-volume-title"
        />
      </Field>
      <Field label="卷纲其余字段（JSON 对象）">
        <textarea
          className="input"
          rows={10}
          value={outlineRestJson}
          onChange={(e) => setOutlineRestJson(e.target.value)}
          disabled={busy}
          data-testid="revision-json-outline"
          style={{ width: '100%', resize: 'vertical', fontFamily: 'monospace' }}
        />
        {errors.outlineRest ? (
          <div
            className="small"
            style={{ color: 'crimson' }}
            data-testid="revision-json-error-outline"
          >
            {errors.outlineRest}
          </div>
        ) : null}
      </Field>
    </>
  );
}

// ============================================================================
// 子组件：JSON 文本域（world / character 整段；outline 由专用组件接管）
// ============================================================================
function JsonField({
  stage,
  value,
  onChange,
  error,
  busy,
}: {
  stage: string;
  value: string;
  onChange: (v: string) => void;
  error: string | null;
  busy: boolean;
}) {
  return (
    <Field label={`${stage} 草稿（JSON 对象）`}>
      <textarea
        className="input"
        rows={12}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={busy}
        data-testid={`revision-json-${stage}`}
        style={{ width: '100%', resize: 'vertical', fontFamily: 'monospace' }}
      />
      {error ? (
        <div
          className="small"
          style={{ color: 'crimson' }}
          data-testid={`revision-json-error-${stage}`}
        >
          {error}
        </div>
      ) : null}
    </Field>
  );
}

// ============================================================================
// 子组件：done 视图（终态展示）
// ============================================================================
function DonePane({ finalResp }: { finalResp: ProjectInitResponse | null }) {
  return (
    <InfoBanner>
      <div data-testid="done-result">
        初始化完成 run_id={finalResp?.run_id ?? '-'}（status=
        {finalResp?.status ?? '-'}
        {finalResp?.current_node
          ? `，current_node=${finalResp.current_node}`
          : ''}
        ），请到 Story Bible 查看生成结果
        {finalResp?.project_id ? '（project_id=' + finalResp.project_id + '）' : ''}。
      </div>
    </InfoBanner>
  );
}

// ============================================================================
// 通用 Field
// ============================================================================
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginTop: 6 }}>
      <div className="muted small">
        {label}
        {hint ? <span className="muted small"> · {hint}</span> : null}
      </div>
      {children}
    </div>
  );
}