// 通用轮询 hook：固定间隔调用 fn，直到 stopWhen 返回 true 或组件卸载。
// 用于 chapter workflow run 的轮询：RUNNING 时 2s 轮询，PAUSED / 终态停止。
//
// 设计要点：
// - 用 setInterval + 显式 clearInterval 卸载，避免 React StrictMode 双调用导致旧 timer 残留。
// - 暴露最新一次 fn 结果与错误；fn 抛错时停止轮询并把错误透出（UI 用 ErrorBanner 展示）。
// - 启动条件通过 enabled 控制：enabled=false 时不跑、不挂 timer。

import { useEffect, useRef, useState } from 'react';

export interface UsePollOptions<T> {
  /** 拉取函数 */
  fn: () => Promise<T>;
  /** 间隔 ms */
  intervalMs: number;
  /** 是否启用；false 时不跑 */
  enabled: boolean;
  /**
   * 拿到最新结果时判断是否停止轮询；返回 true 立即停止。
   * 典型用法：run.status 已变为 PAUSED / COMPLETED / FAILED 时停。
   */
  stopWhen?: (latest: T | null) => boolean;
  /**
   * 出错时是否停止；默认 true（一次失败就停，避免后台无限刷错误）。
   */
  stopOnError?: boolean;
  /**
   * 拉取结果回调；用于把每次结果往外推（常见：刷新 chapter + runs）。
   */
  onResult?: (latest: T) => void;
}

export interface UsePollResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** 当前是否正在轮询 */
  running: boolean;
}

export function usePoll<T>(opts: UsePollOptions<T>): UsePollResult<T> {
  const { fn, intervalMs, enabled, stopWhen, stopOnError = true, onResult } = opts;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  // 用 ref 持有 fn / 回调，避免它们变化时重启 timer 时丢失上下文。
  const fnRef = useRef(fn);
  const onResultRef = useRef(onResult);
  const stopWhenRef = useRef(stopWhen);
  fnRef.current = fn;
  onResultRef.current = onResult;
  stopWhenRef.current = stopWhen;

  // 用 in-flight 标记 + alive 标记避免竞态：StrictMode 下 effect 会执行两次。
  const aliveRef = useRef(true);
  const inFlightRef = useRef(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    // 先清理上一轮
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    if (!enabled) {
      setRunning(false);
      return;
    }

    let stopped = false;
    const tick = async () => {
      if (stopped || !aliveRef.current) return;
      // 页面隐藏（切到后台 tab / 最小化）时跳过本次 tick，减少无意义请求；恢复可见时下一 tick 自然触发。
      if (typeof document !== 'undefined' && document.hidden) return;
      if (inFlightRef.current) return; // 上一次还没回来
      inFlightRef.current = true;
      setLoading(true);
      try {
        const res = await fnRef.current();
        if (!aliveRef.current) return;
        setData(res);
        setError(null);
        setLoading(false);
        onResultRef.current?.(res);
        if (stopWhenRef.current && stopWhenRef.current(res)) {
          stopped = true;
          if (intervalRef.current !== null) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
          }
          setRunning(false);
        }
      } catch (e: unknown) {
        if (!aliveRef.current) return;
        setError(e instanceof Error ? e.message : '轮询出错');
        setLoading(false);
        if (stopOnError) {
          stopped = true;
          if (intervalRef.current !== null) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
          }
          setRunning(false);
        }
      } finally {
        inFlightRef.current = false;
      }
    };

    setRunning(true);
    // 立刻拉一次，再启 interval（与 useApiCall 风格一致：用户能看到立即反馈）
    void tick();
    intervalRef.current = setInterval(() => {
      void tick();
    }, intervalMs);

    return () => {
      stopped = true;
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [enabled, intervalMs, stopOnError]);

  return { data, loading, error, running };
}
