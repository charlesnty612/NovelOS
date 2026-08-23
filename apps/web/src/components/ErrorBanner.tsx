import type { ReactNode } from 'react';

export function ErrorBanner({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <div className="alert alert--error" role="alert">
      {children}
    </div>
  );
}

export function InfoBanner({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <div className="alert alert--info" role="status">
      {children}
    </div>
  );
}
