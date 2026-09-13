// project-init idle 表单「环节勾选步」：基于 init-status 的阶段状态徽标 + 多选
// （V4.0 前端模块化批次从 ProjectInitPanel.tsx 原样搬出，testid / 文案不变）。
import type { StageSelection } from './projectInitCore';
import { STAGE_LABELS } from './projectInitCore';

export interface InitStagePickerProps {
  /** 环节勾选：当前选中的 stage id 列表。 */
  selectedStages: string[];
  onToggleStage: (stage: string, next: boolean) => void;
  /** 环节元数据：从 init-status 解析（或失败兜底），用于渲染徽标。 */
  stageMetaById: Record<string, StageSelection>;
  busy: boolean;
}

export function InitStagePicker({
  selectedStages,
  onToggleStage,
  stageMetaById,
  busy,
}: InitStagePickerProps) {
  return (
    <div data-testid="init-stages-section" style={{ marginTop: 8 }}>
      <div className="muted small" style={{ marginBottom: 4 }}>
        本次生成的环节
      </div>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
        }}
      >
        {STAGE_LABELS.map((s) => {
          const meta = stageMetaById[s.stage];
          // meta 缺失（极端时序）：按"未 done 兜底"展示徽标；不再锁定勾选。
          const done = meta?.done ?? false;
          const label = meta?.label ?? s.label;
          const detail = meta?.detail ?? null;
          const checked = selectedStages.includes(s.stage);
          return (
            <label
              key={s.stage}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
              data-testid={`init-stage-row-${s.stage}`}
            >
              <input
                type="checkbox"
                checked={checked}
                disabled={busy}
                onChange={(e) => onToggleStage(s.stage, e.target.checked)}
                data-testid={`init-stage-${s.stage}`}
              />
              <span className="small" style={{ minWidth: 96 }}>
                {label}
              </span>
              <span
                className="small"
                style={{
                  color: done ? 'var(--color-success)' : 'var(--color-error)',
                  opacity: 0.8,
                }}
                data-testid={`init-stage-${s.stage}-badge`}
              >
                {done ? '✅ 已有设定' : '⚠ 未完成'}
              </span>
              {detail ? (
                <span className="muted small">· {detail}</span>
              ) : null}
            </label>
          );
        })}
      </div>
      <div className="muted small" style={{ marginTop: 4 }}>
        已完成的环节默认不勾选（沿用已有设定）；未完成的环节默认勾选（将生成），可按需取消，仅生成勾选的环节。
      </div>
    </div>
  );
}
