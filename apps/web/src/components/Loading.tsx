/**
 * 统一加载态：
 * - ``Loading``：16px spinner + 文案（默认「加载中…」），带 aria-live 状态区。
 * - ``SkeletonRows``：n 行灰底脉冲骨架块（列表/卡片占位）。
 * 两者均不依赖任何状态，纯展示；颜色与节奏由 index.css 的 --color-* 令牌决定。
 */
export function Loading({
  text = '加载中…',
  className,
}: {
  text?: string;
  className?: string;
}) {
  return (
    <div
      className={className ? `loading ${className}` : 'loading'}
      role="status"
      aria-live="polite"
    >
      <span className="spinner" aria-hidden="true" />
      <span>{text}</span>
    </div>
  );
}

export function SkeletonRows({
  n = 3,
  className,
}: {
  n?: number;
  className?: string;
}) {
  const rows = Math.max(0, Math.floor(n));
  return (
    <div
      className={className ? `skeleton-rows ${className}` : 'skeleton-rows'}
      role="status"
      aria-live="polite"
      aria-label="加载中"
    >
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton skeleton--line" aria-hidden="true" />
      ))}
    </div>
  );
}

export default Loading;
