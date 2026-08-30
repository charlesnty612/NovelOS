import { renderHook, act } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useApiCall } from './useApiCall';

describe('useApiCall', () => {
  it('首次加载：loading 从 true 转为 false，data 被填充', async () => {
    const fn = vi.fn().mockResolvedValue({ id: 1, name: 'first' });
    const { result } = renderHook(() => useApiCall<{ id: number; name: string }>(fn));

    // 首载 effect 已发起，loading 应为 true（首次渲染后立即置位）
    expect(result.current.loading).toBe(true);
    expect(result.current.data).toBeNull();
    expect(fn).toHaveBeenCalledTimes(1);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual({ id: 1, name: 'first' });
    expect(result.current.error).toBeNull();
  });

  it('已有数据后调用 reload：loading 保持 false，data 最终被刷新', async () => {
    // 关键：用「可控 promise」让我们能在 reload 触发后、resolve 之前断言中间态。
    let resolveSecond: (() => void) | null = null;
    let n = 0;
    const fn = vi.fn().mockImplementation(async () => {
      n += 1;
      if (n === 2) {
        return await new Promise<{ id: number; name: string }>((r) => {
          resolveSecond = () => r({ id: 2, name: 'v2' });
        });
      }
      return { id: n, name: `v${n}` };
    });
    const { result } = renderHook(() => useApiCall<{ id: number; name: string }>(fn));

    // 等首载完成
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual({ id: 1, name: 'v1' });

    // 触发 reload（模拟 usePoll 每 2s 触发 chapterCall.reload()）
    // reload 期间 fn 已再次调用但 promise 尚未 resolve —— 此时是真正的「reload 中」状态。
    act(() => {
      result.current.reload();
    });

    // reload 期间：fn 已被再次调用，但 loading 应保持 false（静默刷新）
    expect(fn.mock.calls.length).toBe(2);
    expect(result.current.loading).toBe(false);
    // 旧数据保留，不会被替换为 null
    expect(result.current.data).toEqual({ id: 1, name: 'v1' });

    // 释放第二个 promise，等数据更新
    await act(async () => {
      resolveSecond!();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual({ id: 2, name: 'v2' });
    expect(result.current.error).toBeNull();
  });

  it('首载抛错：error 被设置，loading 转 false', async () => {
    const fn = vi.fn().mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useApiCall<unknown>(fn));

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBe('boom');
    expect(result.current.data).toBeNull();
  });

  it('有数据后 reload 抛错：error 被设置，旧数据保留，loading 仍为 false', async () => {
    const fn = vi
      .fn()
      .mockResolvedValueOnce({ id: 1 })
      .mockRejectedValueOnce(new Error('reload-boom'));
    const { result } = renderHook(() => useApiCall<{ id: number }>(fn));

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.data).toEqual({ id: 1 });

    await act(async () => {
      result.current.reload();
      await Promise.resolve();
      await Promise.resolve();
    });

    // 静默刷新语义下：旧数据保留，不被吞掉；error 照常展示
    expect(result.current.data).toEqual({ id: 1 });
    expect(result.current.error).toBe('reload-boom');
    expect(result.current.loading).toBe(false);
  });

  it('ApiError 抛错时把 detail 作为 error 字段', async () => {
    const { ApiError } = await import('../api/client');
    const err = new ApiError(500, 'server-exploded');
    const fn = vi.fn().mockRejectedValue(err);
    const { result } = renderHook(() => useApiCall<unknown>(fn));

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.error).toBe('server-exploded');
    expect(result.current.loading).toBe(false);
  });
});