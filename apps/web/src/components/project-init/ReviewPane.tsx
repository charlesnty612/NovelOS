// project-init 子组件：review 视图（4 关卡审阅 + 放行 / 放弃 / 带意见重新生成）。
// V4.0 前端模块化批次从 ProjectInitPanel.tsx 原样搬出：步骤条与编辑器分发改为
// 子组件引用，其余逻辑、文案、testid 逐字不变。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError } from '../../api/client';
import type {
  CapabilityBinding,
  ModelProfile,
  ProjectInitPausePayload,
} from '../../api/types';
import { ErrorBanner } from '../ErrorBanner';
import { composeAndValidate } from '../ProjectInitEditors';
import type { JsonValidityChange } from '../ProjectInitEditors';
import { STAGE_LABELS, STAGES_TOTAL, asRecord } from './projectInitCore';
import { InitStageEditor } from './InitStageEditor';
import { InitStepsBar } from './InitStepsBar';

export interface ReviewPaneProps {
  pausePayload: ProjectInitPausePayload;
  busy: boolean;
  error: string | null;
  bindings: CapabilityBinding[];
  profiles: ModelProfile[];
  /** 本次 run 各 AI 节点实际调用的 model_id（按 agent 名聚合），来自 GET /runs/{id} PAUSED 时的 stage_models。null = 未拉到，不渲染。 */
  stageModels: Record<string, string> | null;
  onSubmit: (parsed: Record<string, unknown>) => void;
  onRegenerate: (note: string) => void;
  onAbandon: () => void;
  onChangeStageModel: (capability: string, profileId: string) => Promise<void>;
}

export function ReviewPane({
  pausePayload,
  busy,
  error,
  bindings,
  profiles,
  stageModels,
  onSubmit,
  onRegenerate,
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
  const initialDraft = asRecord(pausePayload.draft);

  // 绑定状态：当前 capability 的 profile_id（取第一项；后端允许数组 → 多档选一个）
  const currentBinding = bindings.find((b) => b.capability === stageMeta.capability);
  const currentProfileId = currentBinding?.profile_ids[0] ?? '';
  const enabledProfiles = profiles.filter((p) => Number(p.enabled) === 1);

  // 本次实际使用的模型：stage → agent 名（与 project-init pipeline 节点 id 对齐）。
  // 从 stage_models 中按当前 stage 取 model_id；model_id 含 '/' 时剥掉 provider 前缀
  // （如 `openai_compatible/k3-256k` → `k3-256k`），与下拉 option 文案（name（model））风格一致。
  const STAGE_TO_AGENT: Record<string, string> = {
    premise: 'premise_designer',
    world: 'world_builder',
    character: 'character_designer',
    outline: 'volume_outliner',
  };
  const usedAgentName = STAGE_TO_AGENT[stageMeta.stage];
  const usedModelId = usedAgentName ? stageModels?.[usedAgentName] : undefined;
  const usedModelShort = usedModelId
    ? usedModelId.includes('/')
      ? usedModelId.split('/').slice(1).join('/')
      : usedModelId
    : null;
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
  // 数据完整性铁律：draft 必须原样保留所有未知键，editor 只更新它认识的键。
  const [draft, setDraft] = useState<Record<string, unknown>>(initialDraft);
  const [premiseDraft, setPremiseDraft] = useState<Record<string, unknown>>(() => {
    // premise state 与顶层 draft 复用同一份 dict，避免双源不一致。
    return { ...initialDraft };
  });
  // 「带意见重新生成」的意见输入缓冲（不随 stage 切换而持久化——换关卡时意见可丢）。
  const [regenerateNote, setRegenerateNote] = useState<string>('');

  // P1.2「带意见重新生成」或重新挂起后，pausePayload.draft 会被新对象替换：
  // 此时必须重置本地 draft / premiseDraft / 编辑缓冲，让 UI 反映 AI 重新生成的新草稿。
  // stage/stage_index 也可能变化（虽然 regenerate 同关卡，但 PAUSED→PAUSED 的 stage 可能不同）。
  useEffect(() => {
    const next = asRecord(pausePayload.draft);
    setDraft(next);
    setPremiseDraft({ ...next });
    setRegenerateNote('');
    setInvalidJsonDomains(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pausePayload]);

  // 非法 JSON 域注册表：JSON 域 parse 失败时上抛，禁用放行。
  const [invalidJsonDomains, setInvalidJsonDomains] = useState<Set<string>>(
    () => new Set(),
  );
  const handleJsonValidityChange = useCallback<JsonValidityChange>(
    (domainKey, ok) => {
      setInvalidJsonDomains((prev) => {
        const next = new Set(prev);
        if (ok) next.delete(domainKey);
        else next.add(domainKey);
        return next;
      });
    },
    [],
  );

  // ---- 校验：必填 + 任何 JSON 兜底域非法 → 禁用放行 ----------------------
  const { ok, parsed, errors } = useMemo(
    () => composeAndValidate(stageMeta.stage, draft),
    [stageMeta.stage, draft],
  );

  // world._degraded 强校验：core_premise 非空（沿用旧行为）
  const worldCorePremiseError = (() => {
    if (!degraded) return null;
    if (stageMeta.stage !== 'world') return null;
    const cp = draft.core_premise;
    if (typeof cp !== 'string' || cp.trim().length === 0) {
      return 'core_premise 必填（AI 降级，请人工补全）';
    }
    return null;
  })();
  const effectiveErrors = worldCorePremiseError
    ? { ...errors, core_premise: worldCorePremiseError }
    : errors;
  const hasInvalidJson = invalidJsonDomains.size > 0;
  const canSubmit = ok && !worldCorePremiseError && !hasInvalidJson && !busy;

  const handleClickSubmit = () => {
    if (!canSubmit || !parsed) return;
    onSubmit(parsed);
  };

  // 「带意见重新生成」：意见可空（纯重试场景），不阻塞。
  const handleClickRegenerate = () => {
    if (busy) return;
    onRegenerate(regenerateNote);
  };

  return (
    <div data-testid="review-pane">
      <ErrorBanner>{error}</ErrorBanner>

      <InitStepsBar currentIndex={stepIndex} />

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
          className="input"
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
              color: modelMsg.kind === 'ok' ? 'var(--color-success)' : 'var(--color-error)',
            }}
            data-testid="stage-model-status"
          >
            {modelMsg.text}
          </span>
        ) : null}
        {usedModelShort ? (
          <span
            className="muted small"
            data-testid="stage-used-model"
            title={usedModelId ?? undefined}
          >
            本次实际使用：{usedModelShort}
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
        <InitStageEditor
          stage={stageMeta.stage}
          draft={draft}
          setDraft={setDraft}
          premiseDraft={premiseDraft}
          setPremiseDraft={setPremiseDraft}
          errors={effectiveErrors}
          busy={busy}
          onJsonValidityChange={handleJsonValidityChange}
        />
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
        {!canSubmit ? (
          <span className="muted small">
            请先修正高亮字段（必填或 JSON 非法）再放行。
          </span>
        ) : null}
      </div>

      {/* P1.2：带意见重新生成入口 —— 丢弃当前 draft，让 AI 重跑本关 */}
      <div style={{ marginTop: 10 }} data-testid="regenerate-section">
        <textarea
          className="input"
          rows={2}
          value={regenerateNote}
          onChange={(e) => setRegenerateNote(e.target.value)}
          placeholder="在此输入希望调整的方向，AI 将按意见重新生成本关…"
          disabled={busy}
          data-testid="regenerate-note"
          style={{ width: '100%', resize: 'vertical' }}
        />
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginTop: 6,
          }}
        >
          <button
            type="button"
            className="btn btn--sm"
            onClick={handleClickRegenerate}
            disabled={busy}
            data-testid="regenerate-submit"
          >
            带意见重新生成
          </button>
          <span className="muted small">
            重新生成将丢弃本关当前内容，由 AI 重新生成；意见可空（纯重试）。
          </span>
        </div>
      </div>

      <div className="muted small" style={{ marginTop: 6 }}>
        放弃本次初始化仅本地重置，后端本次运行仍保持已暂停状态，不影响数据；后续可在项目总览页重新发起。
      </div>
    </div>
  );
}
