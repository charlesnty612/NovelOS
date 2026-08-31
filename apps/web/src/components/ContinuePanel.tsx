import { useState } from 'react';
import { continuationApi } from '../api/endpoints';
import type { ContinueVariant } from '../api/types';
import { ErrorBanner, InfoBanner } from './ErrorBanner';
import { ProseText } from './ProseText';
import { formatApiError } from '../utils/formatApiError';

interface Props {
  projectId: string;
  chapterId: string;
  disabled?: boolean;
}

type Phase = 'idle' | 'generating' | 'reviewing' | 'error';

interface ErrorState {
  // 原始异常对象，渲染时统一走 formatApiError 输出文案。
  original: unknown;
}

interface SuccessState {
  version: number;
  status: string;
  appended_chars: number;
}

/**
 * ContinuePanel —— 章节详情页「续写助手」面板。
 *
 * 功能：
 * - 一键调 `POST /continue` 生成 3 个续写候选；并排展示；
 * - 每张候选卡可调 `POST /continue/adopt` 追加为新草稿；
 * - 错误统一用 ErrorBanner 展示（409 detail 直显）。
 *
 * data-testid 清单（便于测试断言）：
 *   - continue-panel
 *   - continue-generate           生成 / 重试 按钮（共用）
 *   - continue-instruction        附加指令 textarea
 *   -continue-variants           候选外层容器
 *   - continue-variant-text-{i}   每个 variant 的正文 <pre>
 *   - continue-adopt-{i}          每个 variant 的采纳按钮
 *   - continue-regen              重新生成按钮
 *   - continue-success            成功 InfoBanner
 *   - continue-error              错误 ErrorBanner
 */
export function ContinuePanel({ projectId, chapterId, disabled = false }: Props) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [variants, setVariants] = useState<ContinueVariant[]>([]);
  const [instruction, setInstruction] = useState('');
  const [draftInstruction, setDraftInstruction] = useState('');
  const [adopting, setAdopting] = useState(false);
  const [error, setError] = useState<ErrorState | null>(null);
  const [success, setSuccess] = useState<SuccessState | null>(null);

  const handleGenerate = async () => {
    if (phase === 'generating' || adopting) return;
    setError(null);
    setSuccess(null);
    setPhase('generating');
    try {
      const trimmed = draftInstruction.trim();
      const payload: { num_variants?: number; instruction?: string } = {};
      if (trimmed) payload.instruction = trimmed;
      const resp = await continuationApi.generate(projectId, chapterId, payload);
      setVariants(resp.variants);
      setInstruction(draftInstruction);
      setPhase('reviewing');
    } catch (e: unknown) {
      setError({ original: e });
      setPhase('error');
    }
  };

  const handleRegen = () => {
    setPhase('idle');
    setError(null);
    setSuccess(null);
    setVariants([]);
  };

  const handleAdopt = async (variant: ContinueVariant) => {
    if (adopting || phase !== 'reviewing') return;
    setError(null);
    setAdopting(true);
    try {
      const resp = await continuationApi.adopt(projectId, chapterId, {
        content: variant.text,
      });
      setSuccess({
        version: resp.version,
        status: resp.status,
        appended_chars: resp.appended_chars,
      });
      // 成功后清空候选并回到 idle；保留 instruction 让用户可以基于同一指令再生成。
      setVariants([]);
      setPhase('idle');
      setDraftInstruction('');
    } catch (e: unknown) {
      setError({ original: e });
    } finally {
      setAdopting(false);
    }
  };

  const isGenerating = phase === 'generating';
  const showVariants = phase === 'reviewing' && variants.length > 0;
  const genDisabled = disabled || isGenerating || adopting;
  const genLabel = isGenerating
    ? '生成中…'
    : phase === 'error'
      ? '重试'
      : '生成 3 个续写候选';

  return (
    <div className="panel" data-testid="continue-panel">
      <div className="panel__title">
        续写助手
        <div style={{ flex: 1 }} />
        {showVariants ? (
          <button
            className="btn btn--sm"
            onClick={handleRegen}
            disabled={adopting}
            data-testid="continue-regen"
          >
            重新生成
          </button>
        ) : null}
        <button
          className="btn btn--sm btn--primary"
          onClick={() => void handleGenerate()}
          disabled={genDisabled}
          title={disabled ? '当前章节状态不可续写' : undefined}
          data-testid="continue-generate"
        >
          {genLabel}
        </button>
      </div>

      {error ? (
        <div data-testid="continue-error">
          <ErrorBanner>{formatApiError(error.original)}</ErrorBanner>
        </div>
      ) : null}

      {success ? (
        <div data-testid="continue-success">
          <InfoBanner>
            已追加为新草稿 v{success.version}，状态：{success.status}
          </InfoBanner>
          {success.status === 'DRAFTED' ? (
            <div className="muted small" style={{ marginTop: 6 }}>
              章节需重新评审
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="panel__section">
        <div className="panel__section-title">附加指令（多轮指导用，可选）</div>
        <textarea
          value={draftInstruction}
          onChange={(e) => setDraftInstruction(e.target.value)}
          rows={2}
          disabled={genDisabled}
          placeholder="例如：加入一段雨夜追逐，节奏紧凑，主角不要开口说话"
          style={{
            width: '100%',
            fontFamily: 'var(--font-mono)',
            fontSize: 12,
            padding: 8,
            borderRadius: 6,
            border: '1px solid var(--color-border-strong)',
            resize: 'vertical',
          }}
          data-testid="continue-instruction"
        />
      </div>

      {isGenerating ? (
        <div className="muted" data-testid="continue-generating">
          正在生成候选，请稍候…
        </div>
      ) : null}

      {showVariants ? (
        <VariantsGrid
          variants={variants}
          instruction={instruction}
          adopting={adopting}
          onAdopt={(v) => void handleAdopt(v)}
        />
      ) : null}
    </div>
  );
}

function VariantsGrid({
  variants,
  instruction,
  adopting,
  onAdopt,
}: {
  variants: ContinueVariant[];
  instruction: string;
  adopting: boolean;
  onAdopt: (v: ContinueVariant) => void;
}) {
  return (
    <div data-testid="continue-variants">
      {instruction.trim() ? (
        <InfoBanner>本次指令：{instruction.trim()}</InfoBanner>
      ) : null}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
          gap: 12,
          marginTop: 8,
        }}
      >
        {variants.map((v) => (
          <div
            key={v.index}
            className="panel__section"
            style={{ marginTop: 0 }}
            data-testid={`continue-variant-${v.index}`}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'baseline',
                gap: 8,
                flexWrap: 'wrap',
              }}
            >
              <strong>候选 #{v.index}</strong>
              <span className="muted small">{v.text.length} 字</span>
              <span className="muted small">{v.elapsed_ms} ms</span>
              {v.tokens != null ? (
                <span className="muted small">· {v.tokens} tokens</span>
              ) : null}
            </div>
            <div
              className="prose-block"
              style={{
                maxHeight: 240,
                fontSize: 13,
                padding: 8,
                marginTop: 6,
              }}
              data-testid={`continue-variant-text-${v.index}`}
            >
              <ProseText text={v.text} />
            </div>
            <div style={{ marginTop: 6, display: 'flex', justifyContent: 'flex-end' }}>
              <button
                className="btn btn--sm btn--primary"
                onClick={() => onAdopt(v)}
                disabled={adopting}
                data-testid={`continue-adopt-${v.index}`}
              >
                {adopting ? '采纳中…' : '采纳此版本'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}