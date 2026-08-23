import { useCallback, useEffect, useState } from 'react';
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
 */
export function useApiCall<T>(fn: () => Promise<T>, deps: unknown[] = []): UseApiCallResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fn()
      .then((res) => {
        if (alive) setData(res);
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
