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
    <div className="empty">
      <div className="empty__title">{title}</div>
      {hint ? <div className="empty__hint">{hint}</div> : null}
      {action ? <div className="empty__action">{action}</div> : null}
    </div>
  );
}
