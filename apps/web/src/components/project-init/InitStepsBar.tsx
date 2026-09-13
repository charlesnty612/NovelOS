// project-init 子组件：4 关卡步骤条（review 视图顶部）。
// V4.0 前端模块化批次从 ProjectInitPanel.tsx 原样搬出。
import { STAGE_LABELS } from './projectInitCore';

export function InitStepsBar({ currentIndex }: { currentIndex: number }) {
  return (
    <div
      style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}
      data-testid="init-steps"
    >
      {STAGE_LABELS.map((s, i) => {
        const done = i < currentIndex;
        const active = i === currentIndex;
        const borderColor = active
          ? 'var(--color-primary)'
          : done
            ? 'var(--color-success)'
            : 'var(--color-border)';
        return (
          <div
            key={s.stage}
            data-testid={`init-steps-item-${s.stage}`}
            style={{
              padding: '2px 8px',
              borderRadius: 'var(--radius-sm)',
              border: `1px solid ${borderColor}`,
              color: active ? 'var(--color-primary)' : undefined,
              fontWeight: active ? 600 : undefined,
              opacity: done || active ? 1 : 0.45,
              fontSize: 12,
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
