import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { usePoll } from './usePoll';

// 注：本测试用真实定时器。原因：usePoll 同时使用 setInterval + Promise 微任务；
// vitest 的 useFakeTimers + RTL 的 waitFor 会互相死锁。本组件逻辑允许 ±几十毫秒误差。
describe('usePoll', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  // 工具：等到 fn 至少被调用 N 次（最多等 maxMs）
  async function waitForCalls(fn: ReturnType<typeof vi.fn>, n: number, maxMs = 1000) {
    const t0 = Date.now();
    while (fn.mock.calls.length < n && Date.now() - t0 < maxMs) {
      await new Promise((r) => setTimeout(r, 10));
    }
    return fn.mock.calls.length;
  }

  it('enabled=false 时不调用 fn', async () => {
    const fn = vi.fn().mockResolvedValue({ status: 'RUNNING' });
    renderHook(() =>
      usePoll({
        fn,
        intervalMs: 50,
        enabled: false,
      }),
    );
    await new Promise((r) => setTimeout(r, 200));
    expect(fn).not.toHaveBeenCalled();
  });

  it('enabled=true 时立即拉一次 + 按 interval 持续拉取', async () => {
    const fn = vi.fn().mockResolvedValue({ status: 'RUNNING' });
    renderHook(() =>
      usePoll({
        fn,
        intervalMs: 50,
        enabled: true,
        stopWhen: (r) => (r as { status: string }).status !== 'RUNNING',
      }),
    );

    // 等到 fn 至少被调用 4 次（初始 + 3 次 interval @50ms，给 500ms 缓冲）
    const calls = await waitForCalls(fn, 4, 800);
    expect(calls).toBeGreaterThanOrEqual(4);
  });

  it('stopWhen 返回 true 后停止轮询', async () => {
    let n = 0;
    const fn = vi.fn().mockImplementation(async () => {
      n += 1;
      return { status: n < 2 ? 'RUNNING' : 'PAUSED' };
    });
    const stopWhen = (r: unknown) =>
      ((r as { status: string }).status) !== 'RUNNING';

    renderHook(() =>
      usePoll({ fn, intervalMs: 50, enabled: true, stopWhen }),
    );

    await waitForCalls(fn, 2, 500);
    const callsAfterStop = fn.mock.calls.length;
    expect(callsAfterStop).toBeGreaterThanOrEqual(2);

    // 等待 ~300ms，期间不应再调用
    await new Promise((r) => setTimeout(r, 300));
    expect(fn.mock.calls.length).toBe(callsAfterStop);
  });

  it('fn 抛错默认停止轮询并把错误写到 error', async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValue({ status: 'RUNNING' });

    const { result } = renderHook(() =>
      usePoll({
        fn,
        intervalMs: 50,
        enabled: true,
        stopWhen: (r) => ((r as { status: string })?.status) !== 'RUNNING',
      }),
    );

    // 等 error 被设置
    const t0 = Date.now();
    while (result.current.error !== 'boom' && Date.now() - t0 < 500) {
      await new Promise((r) => setTimeout(r, 10));
    }
    expect(result.current.error).toBe('boom');
    expect(result.current.running).toBe(false);

    const callsAfterError = fn.mock.calls.length;
    await new Promise((r) => setTimeout(r, 300));
    expect(fn.mock.calls.length).toBe(callsAfterError);
  });

  it('stopOnError=false 时遇到错误不停止', async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValue({ status: 'RUNNING' });

    renderHook(() =>
      usePoll({
        fn,
        intervalMs: 50,
        enabled: true,
        stopOnError: false,
      }),
    );

    // 初始调用 + interval 调用（不停止）
    await waitForCalls(fn, 3, 500);
    expect(fn.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it('组件卸载后停止轮询', async () => {
    const fn = vi.fn().mockResolvedValue({ status: 'RUNNING' });
    const { unmount } = renderHook(() =>
      usePoll({
        fn,
        intervalMs: 50,
        enabled: true,
        stopWhen: () => false,
      }),
    );
    await waitForCalls(fn, 2, 500);
    const callsBeforeUnmount = fn.mock.calls.length;
    expect(callsBeforeUnmount).toBeGreaterThanOrEqual(2);

    unmount();
    await new Promise((r) => setTimeout(r, 300));
    expect(fn.mock.calls.length).toBe(callsBeforeUnmount);
  });

  it('onResult 回调在每次成功结果时被调用', async () => {
    const onResult = vi.fn();
    const fn = vi.fn().mockResolvedValue({ status: 'RUNNING' });
    renderHook(() =>
      usePoll({
        fn,
        intervalMs: 50,
        enabled: true,
        onResult,
        stopWhen: () => false,
      }),
    );
    await waitForCalls(fn, 4, 500);
    // onResult 至少被调 4 次
    const t0 = Date.now();
    while (onResult.mock.calls.length < 4 && Date.now() - t0 < 300) {
      await new Promise((r) => setTimeout(r, 10));
    }
    expect(onResult.mock.calls.length).toBeGreaterThanOrEqual(4);
  });

  it('stopWhen 在初次返回值上即为 true 时，不应继续 interval', async () => {
    const fn = vi.fn().mockResolvedValue({ status: 'COMPLETED' });
    const { result } = renderHook(() =>
      usePoll({
        fn,
        intervalMs: 50,
        enabled: true,
        stopWhen: (r) => ((r as { status: string }).status) === 'COMPLETED',
      }),
    );

    await waitForCalls(fn, 1, 300);
    // 等到 running 被异步置为 false（stopWhen 命中后下一 microtask）
    const t0 = Date.now();
    while (result.current.running !== false && Date.now() - t0 < 500) {
      await new Promise((r) => setTimeout(r, 10));
    }
    const callsAfter = fn.mock.calls.length;
    expect(result.current.running).toBe(false);

    await new Promise((r) => setTimeout(r, 200));
    expect(fn.mock.calls.length).toBe(callsAfter);
  });
});

// 静音 act 警告：在异步回调中 setState 会触发；用 act 包一层避免 React 18 警告
// （不强制：上面所有测试已用 setTimeout 推进，足够释放微任务）
