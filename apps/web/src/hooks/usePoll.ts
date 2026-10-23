// 通用轮询 hook：固定间隔调用 fn，直到 stopWhen 返回 true 或组件卸载。
// 用于 chapter workflow run 的轮询：RUNNING 时 2s 轮询，PAUSED / 终态停止。
//
// 设计要点：
// - 用 setInterval + 显式 clearInterval 卸载，避免 React StrictMode 双调用导致旧 timer 残留。
// - 暴露最新一次 fn 结果与错误；fn 抛错时停止轮询并把错误透出（UI 用 ErrorBanner 展示）。
// - 启动条件通过 enabled 控制：enabled=false 时不跑、不挂 timer。
// - 目标身份通过 targetKey 声明（2026-10-23 修复）：目标换了 ⇒ 旧结果立即作废
//   （清 data + 立即重拉新目标 + 作废的那一轮不得写回 data）。
//
// 失败语义（2026-10-23 补口）：**出错 ⇒ 停止 + 把 error 交给调用方**；
// 恢复靠调用方显式调 `retry()`（沿用仓内 `useApiCall.reload()` 那种「手动 reload」
// 模式）。**刻意不做自动重试**——自动/无限重试会烧额度、也会把真实故障刷成噪音。

import { useCallback, useEffect, useRef, useState } from 'react';

export interface UsePollOptions<T> {
  /** 拉取函数 */
  fn: () => Promise<T>;
  /** 间隔 ms */
  intervalMs: number;
  /** 是否启用；false 时不跑 */
  enabled: boolean;
  /**
   * **被轮询对象的身份**（本项目传 run_id）。
   *
   * 语义：目标换了 ⇒ 旧结果立即作废。具体三条：
   *  1. 目标变了 ⇒ effect 重跑 ⇒ 旧 `data` 被清空（不再显示上一个目标的结果）；
   *  2. 目标变了 ⇒ **立即**重拉新目标（不等下一个 interval）；
   *  3. 上一轮还在 await 中的请求，resolve 后**不得**写进 `data`（竞态守卫）。
   *
   * ⚠️ **不传 targetKey ⇒ 退化成旧行为**：目标换了既不清数据也不立即重拉，
   *    页面会继续显示上一个目标的内容（横幅显示上一个 run 的动作名/节点/已运行时长）。
   *    传了就必须传**稳定且能区分目标**的值（run_id），别传对象/数组。
   */
  targetKey?: string;
  /**
   * 拿到最新结果时判断是否停止轮询；返回 true 立即停止。
   * 典型用法：run.status 已变为 PAUSED / COMPLETED / FAILED 时停。
   */
  stopWhen?: (latest: T | null) => boolean;
  /**
   * 出错时是否停止；默认 true（一次失败就停，避免后台无限刷错误）。
   *
   * 停摆后**不是**死局：调用方拿到 error 后可显式调 `retry()` 恢复轮询。
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
  /**
   * 手动重启轮询（出错停摆后的恢复入口）。
   *
   * 语义：清空 error 并**立即**再拉一次（不等下一个 interval）。
   * 由调用方决定何时调——本 hook 不做自动重试。
   */
  retry: () => void;
}

export function usePoll<T>(opts: UsePollOptions<T>): UsePollResult<T> {
  const { fn, intervalMs, enabled, stopWhen, stopOnError = true, onResult, targetKey } = opts;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  // 重试用：retry() 递增它 ⇒ 下面那个 effect 重跑 ⇒ 立刻再拉一次 + 重启 interval。
  const [nonce, setNonce] = useState(0);

  // 用 ref 持有 fn / 回调，避免它们变化时重启 timer 时丢失上下文。
  const fnRef = useRef(fn);
  const onResultRef = useRef(onResult);
  const stopWhenRef = useRef(stopWhen);
  fnRef.current = fn;
  onResultRef.current = onResult;
  stopWhenRef.current = stopWhen;

  // 用 in-flight 标记 + alive 标记避免竞态：StrictMode 下 effect 会执行两次。
  const aliveRef = useRef(true);
  // 在途占位用 **token** 而不是 boolean：
  // 旧轮次的 finally 只能清自己的 token，**不能**把新一轮的占位清掉
  // （否则会出现两个并发请求同时往 data 里写）。
  const inFlightRef = useRef<symbol | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // 上一次的目标身份：只有「目标**真的**换了」才清空 data。
  // ⚠️ 判据不能用 nonce —— retry() 也会让 effect 重跑，但那时目标没变，
  //    清数据会把用户正在看的内容抹掉。
  const prevTargetKeyRef = useRef<string | undefined>(undefined);

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

  // 手动重试入口：清错误 + 递增 nonce（下面的 effect 依赖它 ⇒ 重跑 ⇒ 立即再拉一次）。
  const retry = useCallback(() => {
    setError(null);
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    // ---- 目标身份变了？旧结果必须立刻作废 ----
    // 判据用 targetKey（被轮询对象的身份），**不用 nonce**：retry() 也会让本 effect
    // 重跑，但那时目标没变，清数据会把用户正在看的内容抹掉。
    if (prevTargetKeyRef.current !== targetKey) {
      prevTargetKeyRef.current = targetKey;
      // 旧目标的数据不得继续显示：否则页面横幅会显示上一个 run 的
      // 动作名 / 节点 / 已运行时长（新 run 可能显示「已运行 12 分」）。
      setData(null);
      // ⚠️ **错误也要一起清**（对抗审查 B · F-7，2026-10-23）：
      // 旧目标的报错文案会一直挂到新目标首次成功为止（setError(null) 只在
      // 成功路径与 retry() 里）⇒ 与「旧数据残留」同形状，只是维度是 error。
      setError(null);
    }
    // 解除上一轮的在途占位：新的一轮必须能**立即**拉一次，而不是
    // 「等上一个回来」再等一个 interval。
    // ⚠️ 这条依赖下面的竞态守卫（await 之后复查 stopped）——
    //    否则旧轮的响应会和新轮的响应一起写 data。
    inFlightRef.current = null;

    // 先清理上一轮
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    if (!enabled) {
      setRunning(false);
      // 目标没了（比如 run 已结束）⇒ 不该还挂着「加载中」。
      setLoading(false);
      return;
    }

    let stopped = false;
    const tick = async () => {
      if (stopped || !aliveRef.current) return;
      // 页面隐藏（切到后台 tab / 最小化）时跳过本次 tick，减少无意义请求；恢复可见时下一 tick 自然触发。
      if (typeof document !== 'undefined' && document.hidden) return;
      if (inFlightRef.current !== null) return; // 上一次还没回来
      const token = Symbol('usePoll.inflight');
      inFlightRef.current = token;
      setLoading(true);
      try {
        const res = await fnRef.current();
        // ⚠️ 竞态守卫：await 之后**必须**复查「这一轮是否已作废」。
        // 只查 aliveRef（组件还活着）不够：目标切换 ⇒ 上一轮 effect 的 cleanup
        // 把 stopped 置 true，但上一轮的 tick 可能还在 await 中 —— 它 resolve 后
        // 若继续 setData，就会把**旧目标**的结果写进来。
        if (!aliveRef.current || stopped) return;
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
        // 同口径：这一轮已作废（目标已切换）⇒ 旧目标的错误不该显示，
        // 也不该把新一轮的 running 置 false。
        if (stopped) return;
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
        // 只清自己的 token：旧轮的 finally 不得动新一轮的占位。
        if (inFlightRef.current === token) inFlightRef.current = null;
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
  }, [enabled, intervalMs, stopOnError, nonce, targetKey]);

  return { data, loading, error, running, retry };
}
