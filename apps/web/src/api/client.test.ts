import { describe, expect, it, beforeEach, vi, afterEach } from 'vitest';
import { ApiError, api, coerceJson } from './client';

describe('coerceJson', () => {
  it('null / undefined 原样返回', () => {
    expect(coerceJson(null)).toBeNull();
    expect(coerceJson(undefined)).toBeUndefined();
  });

  it('已是对象则原样返回', () => {
    const obj = { a: 1 };
    expect(coerceJson(obj)).toBe(obj);
  });

  it('合法 JSON 字符串被解析为对象', () => {
    expect(coerceJson('{"a":1}')).toEqual({ a: 1 });
    expect(coerceJson('[1,2,3]')).toEqual([1, 2, 3]);
  });

  it('非法 JSON 字符串回退原值（不抛错）', () => {
    expect(coerceJson('{not json}')).toBe('{not json}');
    expect(coerceJson('plain text')).toBe('plain text');
  });

  it('空字符串与字面量 "null" 不抛错', () => {
    expect(coerceJson('')).toBe('');
    expect(coerceJson('null')).toBe('null');
  });
});

describe('api client', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('2xx + application/json 返回解析后的对象', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ) as unknown as typeof fetch;

    const r = await api.get<{ ok: boolean }>('/projects');
    expect(r).toEqual({ ok: true });
  });

  it('非 2xx 抛 ApiError，并把 response.detail 透出', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'project not found' }), {
        status: 404,
        headers: { 'content-type': 'application/json' },
      }),
    ) as unknown as typeof fetch;

    await expect(api.get('/projects/p1')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      detail: 'project not found',
    });
  });

  it('非 2xx 且 detail 是非字符串时转成字符串', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: { msg: 'x', code: 1 } }), {
        status: 422,
        headers: { 'content-type': 'application/json' },
      }),
    ) as unknown as typeof fetch;

    try {
      await api.get('/projects');
      throw new Error('should have thrown');
    } catch (e: unknown) {
      expect(e).toBeInstanceOf(ApiError);
      const ae = e as ApiError;
      expect(ae.status).toBe(422);
      expect(ae.detail).toContain('msg');
    }
  });

  it('网络层抛错时包装为 ApiError(0)', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch')) as unknown as typeof fetch;

    try {
      await api.get('/projects');
      throw new Error('should have thrown');
    } catch (e: unknown) {
      expect(e).toBeInstanceOf(ApiError);
      const ae = e as ApiError;
      expect(ae.status).toBe(0);
      expect(ae.detail).toContain('Failed to fetch');
    }
  });

  it('POST 会序列化 body 并发送 Content-Type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('{}', { status: 201, headers: { 'content-type': 'application/json' } }),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await api.post('/projects', { name: 'A' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/projects');
    expect(init.method).toBe('POST');
    expect(init.headers).toMatchObject({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body as string)).toEqual({ name: 'A' });
  });

  it('204 No Content 返回 undefined', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(null, { status: 204 }),
    ) as unknown as typeof fetch;

    const r = await api.delete<void>('/projects/p1');
    expect(r).toBeUndefined();
  });
});
