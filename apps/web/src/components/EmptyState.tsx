import type { ReactNode } from 'react';

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div
      style={{
        textAlign: 'center',
        padding: '40px 16px',
        color: 'var(--color-text-muted)',
      }}
    >
      <div style={{ fontSize: 15, marginBottom: 6 }}>{title}</div>
      {hint ? <div className="small">{hint}</div> : null}
      {action ? <div style={{ marginTop: 16 }}>{action}</div> : null}
    </div>
  );
}
