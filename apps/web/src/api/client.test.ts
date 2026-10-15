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

  // 2026-10-15 批次（五）：FastAPI 的 pydantic 校验错误（detail 是数组）
  // 此前被 `JSON.stringify` 压成一坨符号，作者看不到「哪个字段错了」。
  it('detail 是 pydantic 校验错误数组时压成「字段: 说明」而不是 JSON', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [
            {
              type: 'enum',
              loc: ['body', 'target_kind'],
              msg: "Input should be 'character' or 'location'",
            },
          ],
        }),
        { status: 422, headers: { 'content-type': 'application/json' } },
      ),
    ) as unknown as typeof fetch;

    try {
      await api.get('/projects');
      throw new Error('should have thrown');
    } catch (e: unknown) {
      const ae = e as ApiError;
      expect(ae.status).toBe(422);
      // 判别点：字段名必须在，且不得是原始 JSON（`[` / `{"type"` 都不该出现）
      expect(ae.detail).toBe("target_kind: Input should be 'character' or 'location'");
      expect(ae.detail).not.toContain('"type"');
      expect(ae.detail).not.toContain('[');
    }
  });

  it('多条校验错误用「；」连接，且丢掉 loc 首段（body/query 对作者无意义）', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [
            { loc: ['body', 'target_kind'], msg: '枚举不合法' },
            { loc: ['body', 'reveal_by_chapter'], msg: '必须 ≥1' },
            { loc: ['body', 'notes', 0], msg: '子路径也要展开' },
          ],
        }),
        { status: 422, headers: { 'content-type': 'application/json' } },
      ),
    ) as unknown as typeof fetch;

    try {
      await api.get('/projects');
      throw new Error('should have thrown');
    } catch (e: unknown) {
      expect((e as ApiError).detail).toBe(
        'target_kind: 枚举不合法；reveal_by_chapter: 必须 ≥1；notes.0: 子路径也要展开',
      );
    }
  });

  it('detail 是数组但不是校验错误形态 ⇒ 仍回落 JSON.stringify（不吞信息）', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: ['纯文本数组', 2] }), {
        status: 422,
        headers: { 'content-type': 'application/json' },
      }),
    ) as unknown as typeof fetch;

    try {
      await api.get('/projects');
      throw new Error('should have thrown');
    } catch (e: unknown) {
      const d = (e as ApiError).detail;
      expect(d).toContain('纯文本数组');
      expect(d.startsWith('[')).toBe(true);
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
