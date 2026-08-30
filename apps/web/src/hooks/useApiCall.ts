import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';

export interface UseApiCallResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * 通用「加载一段异步数据」hook。
 * - 自动忽略 StrictMode 下的双调用与组件卸载后的状态写入。
 * - 抛 ApiError 时把 detail 拿出来作为 UI 错误信息。
 * - 「静默刷新」：已有数据时调用 reload() 不再把内容区切回「加载中…」，
 *   避免轮询（典型 2s 一次）期间 UI 闪烁；首载仍正常显示 loading 骨架。
 *   error 路径不受静默语义影响，照常 setError。
 */
export function useApiCall<T>(fn: () => Promise<T>, deps: unknown[] = []): UseApiCallResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  // 标记是否已有一次成功的 setData。仅做静默刷新的判定依据，渲染无关。
  const hasDataRef = useRef(false);

  useEffect(() => {
    let alive = true;
    // 已有数据 → 静默刷新：保持 loading=false，避免轮询期间 UI 闪烁。
    // 无数据（首载或 reset 后）→ 正常置 loading=true 触发骨架屏。
    if (!hasDataRef.current) {
      setLoading(true);
    }
    setError(null);
    fn()
      .then((res) => {
        if (!alive) return;
        hasDataRef.current = true;
        setData(res);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        if (err instanceof ApiError) setError(err.detail);
        else if (err instanceof Error) setError(err.message);
        else setError('未知错误');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, loading, error, reload };
}